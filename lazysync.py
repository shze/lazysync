#!/usr/bin/env python

from __future__ import print_function
from collections import deque # implements atomic append() and popleft() that do not require locking
import signal, argparse, os, sys, pyinotify, time, shutil, logging, datetime, filecmp, stat

# global variable to check for sigint
sigint = False 
# set up logging
logger = logging.getLogger(os.path.basename(sys.argv[0]))
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(console_handler)

# catch sigint to interupt the inotify processing loop
def sigint_handler(signal, frame):
  logger.debug("sigint_handler()")
  global sigint
  sigint = True

# TODO allow human readable limits
def get_config():
  logger.debug("get_config()")
  return {
    'dry-run': False,
    'lazy': True,
    'local_storage_limit': 100000000 # ~100MB
  }

# parse the folders to sync from the command line arguments
def parse_command_line():
  logger.debug("parse_command_line()")
  parser = argparse.ArgumentParser(description = 'Syncs lazily a remote folder and a local folder')
  parser.add_argument('-r', '--remote', required = True)
  parser.add_argument('-l', '--local', required = True)
  parser.add_argument('-d', '--dryrun', action = 'store_true') # store_true means it's True if the flag is found
  parser.add_argument('-n', '--nolazy', action = 'store_true') # store_true means it's True if the flag is found
  args = parser.parse_args()
  return {
    'remote': os.path.abspath(args.remote), 
    'local': os.path.abspath(args.local),
    'dry-run': args.dryrun,
    'lazy': not args.nolazy
  }

# Given two dicts, merge them into a new dict as a shallow copy; see http://stackoverflow.com/questions/38987/
def merge_two_dicts(x, y):
  logger.debug("merge_two_dicts()")
  z = x.copy()
  z.update(y) # keys in y overwrite existing keys in x/z!
  return z

# walk all files and folders recursively starting at root_folder; all files and folders are relative to root_folder
def relative_walk(root_folder):
  logger.debug("relative_walk()")
  folders = set()
  files = set()
  for dirpath, dirnames, filenames in os.walk(root_folder):
    relative_dirpath = os.path.relpath(dirpath, root_folder)
    for dirname in dirnames:
      folders.add(os.path.normpath(os.path.join(relative_dirpath, dirname)))
    for filename in filenames: 
      files.add(os.path.normpath(os.path.join(relative_dirpath, filename)))
  return folders, files

#
def fast_file_cmp(file1, file2):
  s1 = filecmp._sig(os.stat(file1))
  s2 = filecmp._sig(os.stat(file2))
  if s1[0] != stat.S_IFREG or s2[0] != stat.S_IFREG:
    return False
  if s1 == s2:
    return True
  if s1[1] != s2[1]:
    return False
  return True # always shallow, see: stackoverflow.com/questions/23192359/

#
def files_identical(path1, path2):
  logger.debug("files_identical() path1='%s' path2='%s'", path1, path2)
  if(not os.path.exists(path1) or not os.path.exists(path2)):
    return False
  return fast_file_cmp(path1, path2)

#
def path_or_link_exists(path):
  logger.debug("path_or_link_exists() path='%s'", path)
  return os.path.lexists(path)

#
def local_is_symlink_to_remote(local_filename, remote_filename):
  logger.debug("local_is_symlink_to_remote() local_filename='%s' remote_filename='%s'", local_filename, remote_filename)
  return os.path.islink(local_filename) and os.readlink(local_filename) == remote_filename

#
class synctask:
  #
  def __init__(self, path, event_mask):
    logger.debug("synctask::__init__()")
    self.path = path
    self.event_mask = event_mask
    pass

# lazily syncs two folders with the given config parameters
class lazysync(pyinotify.ProcessEvent):
  # initialize object
  def __init__(self, config):
    logger.debug("lazysync::__init__()")
    self.config = config
    self.queue = deque()
    self.file_access = {}
    self.wm = pyinotify.WatchManager()
    self.notifier = pyinotify.ThreadedNotifier(self.wm, self)
    self.notifier.start()
    self.mask = pyinotify.IN_ACCESS | pyinotify.IN_DELETE | pyinotify.IN_CREATE | pyinotify.IN_CLOSE_WRITE
    
  # sync directionally from_dir -> to_dir
  def sync_one_way(self, from_dir, to_dir):
    logger.debug("lazysync::sync_one_way() from_dir='%s' to_dir='%s'", from_dir, to_dir)
    
    from_folder_set, from_file_set = relative_walk(from_dir) 
    to_folder_set, to_file_set = relative_walk(to_dir)
    folder_difference = from_folder_set - to_folder_set # folders only in from_folder_set: need to be copied
    folder_intersection = from_folder_set & to_folder_set # set of folders in both sets: assume they're identical
    file_difference = from_file_set - to_file_set # files only in from_file_set: need to be copied/symlinked
    file_intersection = from_file_set & to_file_set # set of files in both sets: need to check details (size, etc.)
        
    logger.debug("lazysync::sync_one_way() folders_diff.len=%s folder_intersect.len=%d file_diff.len=%d file_intersect.len=%d", 
                 len(folder_difference), len(folder_intersection), len(file_difference), len(file_intersection))
    return folder_difference, folder_intersection, file_difference, file_intersection
  
  # initialize local folder, then turn on inotify for it
  def initialize_local(self):
    logger.info("lazysync::initialize_local()")
    # sync remote -> local
    while True: # emulate do while loop
      folder_diff, folder_intersect, file_diff, file_intersect = self.sync_one_way(self.config['remote'], self.config['local']) 
      for relative_path in file_intersect:
        remote_path = os.path.join(self.config['remote'], relative_path)
        local_path = os.path.join(self.config['local'], relative_path)
        if(not files_identical(remote_path, local_path)):
          logger.warning("lazysync::initialize_local() file '%s' differs between local and remote! NOT SYNCING!", relative_path)
      for relative_path in folder_diff:
        self.queue.append(synctask(os.path.join(self.config['remote'], relative_path), pyinotify.IN_CREATE | pyinotify.IN_ISDIR))
      for relative_path in file_diff:
        self.queue.append(synctask(os.path.join(self.config['remote'], relative_path), pyinotify.IN_CREATE))
      self.process_queue()
      if(len(folder_diff) + len(file_diff) == 0 or self.config['dry-run']):
        break
    # turn on inotify on local
    self.watch_local = self.wm.add_watch(self.config['local'], self.mask, rec=True, auto_add=True)
  
  # initialize remote folder, then turn on inotify for it
  def initialize_remote(self):
    logger.info("lazysync::initialize_remote()")
    # sync local -> remote
    while True: # emulate do while loop
      folder_diff, folder_intersect, file_diff, file_intersect = self.sync_one_way(self.config['local'], self.config['remote'])
      for relative_path in file_intersect:
        remote_path = os.path.join(self.config['remote'], relative_path)
        local_path = os.path.join(self.config['local'], relative_path)
        if(not files_identical(remote_path, local_path)):
          logger.warning("lazysync::initialize_remote() file '%s' differs between local and remote! NOT SYNCING!", relative_path)
      for relative_path in folder_diff:
        self.queue.append(synctask(os.path.join(self.config['local'], relative_path), pyinotify.IN_CREATE | pyinotify.IN_ISDIR))
      for relative_path in file_diff:
        self.queue.append(synctask(os.path.join(self.config['local'], relative_path), pyinotify.IN_CREATE))
      self.process_queue()
      if(len(folder_diff) + len(file_diff) == 0 or self.config['dry-run']):
        break
    # turn on inotify on remote
    self.watch_remote = self.wm.add_watch(self.config['remote'], self.mask, rec=True, auto_add=True)
    
  # collect inotify events; redefined method from base class
  def process_default(self, event):
    logger.debug("lazysync::process_default() filename='%s' event='%s'", event.pathname, event.maskname)
    self.queue.append(synctask(event.pathname, event.mask))

  # file: download
  def action_remote_access_file(self, relative_path):
    logger.debug("lazysync::action_remote_access_file() relative_path='%s'", relative_path) 
    if(self.config['dry-run']):
      logger.warning("lazysync::action_remote_access_file() download '%s' (dry-run)", relative_path)
      return 
    local_filename = os.path.join(self.config['local'], relative_path)
    remote_filename = os.path.join(self.config['remote'], relative_path)
    if(local_is_symlink_to_remote(local_filename, remote_filename)):
      if(relative_path in self.file_access.keys()):
        # one file access event is created every ~160kB read, i.e. count * 160kB is roughly the read amount, at least
        # when read in a short time (repeatedly calling stat on a file or refreshing your file viewer might create access 
        # events, but only one; that's why we downweigh accesses here that are older than 10s
        seconds_since_last_acccess = time.time() - self.file_access[relative_path]['time']
        old_count_factor = max(0, 1 - seconds_since_last_acccess * 0.1) # becomes zero for accesses older than 10s
        self.file_access[relative_path]['count'] = 1 + old_count_factor * self.file_access[relative_path]['count']
        self.file_access[relative_path]['time'] = time.time()
      else:
        self.file_access[relative_path] = {'count': 1, 'time': time.time()} # unix time
        
      logger.debug("lazysync::action_remote_access_file() file=%s count=%f", relative_path, self.file_access[relative_path]['count'])
      
      # download if we're not in lazy mode, or if enough of a file was read; enough is semi-arbitrarily set to 45%, b/c:
      # * files with <=2 chunks of 160kB will always be downloaded, even on a single access event (small, should be fast)
      # * larger files will be downloaded after when they reach 45% of the size/160kB in access events
      if(not self.config['lazy'] or self.file_access[relative_path]['count'] * 160000 > os.path.getsize(remote_filename) * 0.45):
        logger.warning("lazysync::action_remote_access_file() download '%s'", relative_path)
        self.file_access.pop(relative_path) # remove key from dict
        os.remove(local_filename) # will trigger delete
        shutil.copy2(remote_filename, local_filename) # will trigger create and modify
        logger.warning("lazysync::action_remote_access_file() download '%s' finished", relative_path)
        # TODO update current local storage size
      
    else:
      # access to remote could be writing, but if it is we'll also see IN_CLOSE_WRITE for remote/file when it's done
      # so no need to warn here
      pass

  # dir: mkdir
  def action_local_create_dir(self, relative_path):
    logger.info("lazysync::action_local_create_dir() relative_path='%s'", relative_path)
    remote_path = os.path.join(self.config['remote'], relative_path)
    if(path_or_link_exists(remote_path)):
      logger.debug("lazysync::action_local_create_dir() remote path already exists, nothing to do.")
      return
    logger.warning("lazysync::action_local_create_dir() create dir '%s' %s", remote_path, "(dry-run)" if self.config['dry-run'] else "")
    if(self.config['dry-run']):
      return 
    os.makedirs(remote_path) # wills trigger create

  # dir: mkdir
  def action_remote_create_dir(self, relative_path):
    logger.info("lazysync::action_remote_create_dir() relative_path='%s'", relative_path)
    local_path = os.path.join(self.config['local'], relative_path)
    if(path_or_link_exists(local_path)):
      logger.debug("lazysync::action_remote_create_dir() local path already exists, nothing to do.")
      return 
    logger.warning("lazysync::action_remote_create_dir() create dir '%s' %s", local_path, "(dry-run)" if self.config['dry-run'] else "")
    if(self.config['dry-run']):
      return 
    os.makedirs(local_path) # will trigger create
    
  # file: upload 
  def action_local_create_modify_file(self, relative_path):
    logger.info("lazysync::action_local_create_modify_file() relative_path='%s'", relative_path)
    remote_path = os.path.join(self.config['remote'], relative_path)
    local_path = os.path.join(self.config['local'], relative_path)

    # symlinks with target within local
    if(os.path.islink(local_path) and os.path.realpath(local_path).startswith(self.config['local'])):
      logger.info("lazysync::action_local_create_modify_file() local path is a symlink within local, creating relative symlink in remote.")
      local_link_target = os.path.realpath(local_path)
      relative_link_target = os.path.relpath(local_link_target, self.config['local'])
      logger.warning("lazysync::action_local_create_modify_file() symlink '%s' -> '%s' %s", remote_path, relative_link_target, "(dry-run)" if self.config['dry-run'] else "")
      if(self.config['dry-run']):
        return 
      if(path_or_link_exists(remote_path)):
        os.remove(remote_path)
      os.symlink(relative_link_target, remote_path)
      
    # broken symlinks local -> target 
    elif(os.path.islink(local_path) and os.path.realpath(local_path).startswith(self.config['remote']) and not os.path.exists(local_path)):
      logger.warning("lazysync::action_local_create_modify_file() removing broken symlink '%s' -> '%s'", local_path, os.path.realpath(local_path))
      os.remove(local_path)
    
    # all other files and symlinks
    else:
      logger.info("lazysync::action_local_create_modify_file() local path is not a symlink within local, uploading local path.")
      if(files_identical(remote_path, local_path)):
        logger.info("lazysync::action_local_create_modify_file() remote and local files are identical, no need to modify local path.")
        return 
      logger.warning("lazysync::action_local_create_modify_file() upload '%s' %s", local_path, "(dry-run)" if self.config['dry-run'] else "")
      if(self.config['dry-run']):
        return 
      if(path_or_link_exists(remote_path)):
        os.remove(remote_path) # will trigger delete
      shutil.copy2(local_path, remote_path) # will trigger create and modify
      # TODO update current local storage size

  # file: symlink
  def action_remote_create_modify_file(self, relative_path):
    logger.info("lazysync::action_remote_create_modify_file() relative_path='%s'", relative_path)
    remote_path = os.path.join(self.config['remote'], relative_path)
    local_path = os.path.join(self.config['local'], relative_path)
    
    # symlinks with target within remote
    if(os.path.islink(remote_path) and os.path.realpath(remote_path).startswith(self.config['remote'])):
      logger.info("lazysync::action_remote_create_modify_file() remote path is a symlink within remote, creating relative symlink in local.")
      remote_link_target = os.path.realpath(remote_path)
      relative_link_target = os.path.relpath(remote_link_target, self.config['remote'])
      logger.warning("lazysync::action_remote_create_modify_file() symlink '%s' -> '%s' %s", local_path, relative_link_target, "(dry-run)" if self.config['dry-run'] else "")
      if(self.config['dry-run']):
        return 
      if(path_or_link_exists(local_path)):
        os.remove(local_path)
      os.symlink(relative_link_target, local_path)
    
    # all other files and symlinks
    else:
      logger.info("lazysync::action_remote_create_modify_file() remote path is not a symlink within remote, downloading/symlinking remote path.")
      # ignore if local file is symlink, b/c it automatically has the modifications
      if(local_is_symlink_to_remote(local_path, remote_path)):
        logger.info("lazysync::action_remote_create_modify_file() local path is symlink, no need to modify local path.")
        return 
      if(files_identical(remote_path, local_path)):
        logger.info("lazysync::action_remote_create_modify_file() remote and local files are identical, no need to modify local path.")
        return 
      logger.warning("lazysync::action_local_create_modify_file() symlink '%s' -> '%s' %s", local_path, remote_path, "(dry-run)" if self.config['dry-run'] else "")
      if(self.config['dry-run']):
        return 
      if(path_or_link_exists(local_path)):
        os.remove(local_path)
      os.symlink(remote_path, local_path)
      
      # if we're not in lazy mode, symlink quickly first, then create an access event to download the file
      if(not self.config['lazy']):
        logger.debug("lazysync::action_remote_create_modify_file() not in lazy mode, create IN_ACCESS event to download")
        self.queue.append(synctask(os.path.join(self.config['remote'], relative_path), pyinotify.IN_ACCESS)) 
      
  # dir: rmdir; file: rm
  def action_local_delete(self, relative_path):
    logger.info("lazysync::action_local_delete() relative_path='%s'", relative_path)
    remote_path = os.path.join(self.config['remote'], relative_path)
    local_path = os.path.join(self.config['local'], relative_path)
    # do not delete, if both exist b/c then the delete event is a result of another event/operation
    if(path_or_link_exists(remote_path) and path_or_link_exists(local_path)): 
      logger.debug("lazysync::action_local_delete() both remote and local paths exist, won't delete.")
      return
    # check in case the IN_DELETE for local/file is the result of action_remote_delete() deleting local/file
    if(not path_or_link_exists(remote_path)): 
      logger.debug("lazysync::action_local_delete() remote path does not exist, won't delete.")
      return
    logger.warning("lazysync::action_local_delete() delete '%s' %s", remote_path, "(dry-run)" if self.config['dry-run'] else "")
    if(self.config['dry-run']):
      return 
    if(os.path.isdir(remote_path)):
      os.rmdir(remote_path)
    else:
      os.remove(remote_path)
      # TODO update current local storage size

  # dir: rmdir; file: rm
  def action_remote_delete(self, relative_path):
    logger.info("lazysync::action_remote_delete() relative_path='%s'", relative_path)
    remote_path = os.path.join(self.config['remote'], relative_path)
    local_path = os.path.join(self.config['local'], relative_path)
    # do not delete, if both exist b/c then the delete event is a result of another event/operation
    if(path_or_link_exists(remote_path) and path_or_link_exists(local_path)): 
      logger.debug("lazysync::action_local_delete() both remote and local paths exist, won't delete.")
      return
    # check in case the IN_DELETE for remote/file is the result of action_local_delete() deleting remote/file
    if(not path_or_link_exists(local_path)):
      logger.debug("lazysync::action_remote_delete() local path does not exist, won't delete.")
      return
    logger.warning("lazysync::action_remote_delete() delete '%s' %s", local_path, "(dry-run)" if self.config['dry-run'] else "")
    if(self.config['dry-run']):
      return 
    if(os.path.isdir(local_path)):
      os.rmdir(local_path)
    else:
      os.remove(local_path)
      # TODO update current local storage size
    
  # 
  def process_event(self, synctask):
    logger.debug("lazysync::process_event() pathname='%s' mask='%s'", synctask.path, pyinotify.EventsCodes.maskname(synctask.event_mask))
    if(synctask.event_mask & pyinotify.IN_ACCESS):
      if(not synctask.event_mask & pyinotify.IN_ISDIR): # ignore accessing directories
        # look for remote/file access: if local/file symlinks to remote/file, no IN_ACCESS event is generated for local/file
        if(synctask.path.startswith(self.config['remote'])): 
          relative_filename = os.path.relpath(synctask.path, self.config['remote'])
          self.action_remote_access_file(relative_filename)
    elif(synctask.event_mask & pyinotify.IN_CREATE):
      if(synctask.event_mask & pyinotify.IN_ISDIR): 
        if(synctask.path.startswith(self.config['local'])):
          relative_path = os.path.relpath(synctask.path, self.config['local'])
          self.action_local_create_dir(relative_path)
        else:
          relative_path = os.path.relpath(synctask.path, self.config['remote'])
          self.action_remote_create_dir(relative_path)
      else: # files: symlinks will only trigger IN_CREATE, files will additionally trigger IN_CLOSE_WRITE
        if(synctask.path.startswith(self.config['local'])):
          relative_path = os.path.relpath(synctask.path, self.config['local'])
          self.action_local_create_modify_file(relative_path)
        else:
          relative_path = os.path.relpath(synctask.path, self.config['remote'])
          self.action_remote_create_modify_file(relative_path)
    elif(synctask.event_mask & pyinotify.IN_CLOSE_WRITE):
      if(not synctask.event_mask & pyinotify.IN_ISDIR): # ignore directories
        if(synctask.path.startswith(self.config['local'])): # local file modified
          relative_path = os.path.relpath(synctask.path, self.config['local'])
          self.action_local_create_modify_file(relative_path)
        else: # remote file modified
          relative_path = os.path.relpath(synctask.path, self.config['remote'])
          self.action_remote_create_modify_file(relative_path)
      pass
    elif(synctask.event_mask & pyinotify.IN_DELETE):
      if(synctask.path.startswith(self.config['local'])):
        relative_path = os.path.relpath(synctask.path, self.config['local'])
        self.action_local_delete(relative_path)
      else:
        relative_path = os.path.relpath(synctask.path, self.config['remote'])
        self.action_remote_delete(relative_path)
  
  #
  def process_queue(self):
    logger.info("lazysync::process_queue() len=%d", len(self.queue))
    while(self.queue and not sigint): # check sigint here too to avoid long processing times before exiting
      self.process_event(self.queue.popleft())

  # loop to detect sigint
  def loop(self):
    logger.debug("lazysync::loop()")
    global sigint
    while(not sigint):
      self.process_queue()
      time.sleep(1.5) # avoid 100% cpu load and a small delay is tolerable to quit 
    self.notifier.stop() # turn off inotify

# main    
if __name__ == "__main__":
  logger.debug("__main__()")
  signal.signal(signal.SIGINT, sigint_handler)
  config = merge_two_dicts(get_config(), parse_command_line()) # cmd line second to overwrite default settings in config
  if(config['dry-run']):
    logger.info("__main__() Dry run, NOT syncing any files!")

  # default queu size is 16384 (/proc/sys/fs/inotify/max_queued_events), but it cannot be changed w/o root access;
  # this leads to queue overflows when syncing large file of ~10GB, even when set to 100k
  #pyinotify.max_queued_events = 100000  
  sync = lazysync(config)
  sync.initialize_local()
  sync.initialize_remote()
  sync.loop()
