#!/usr/bin/env python

from __future__ import print_function
from collections import deque # implements atomic append() and popleft() that do not require locking
import logging, argparse, os, sys, datetime, time, timeit, signal, stat, math, enum # enum is enum34

# global variables
sigint = False # variable to check for sigint
min_sleep = 1.9
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
  return {}

# parse the folders to sync from the command line arguments
def parse_command_line():
  logger.debug("parse_command_line()")
  parser = argparse.ArgumentParser(description = 'Syncs lazily a remote folder and a local folder')
  parser.add_argument('-r', '--remote', required = True)
  parser.add_argument('-l', '--local', required = True)
  args = parser.parse_args()
  return {
    'remote': os.path.abspath(args.remote), 
    'local': os.path.abspath(args.local)
  }

# given two dicts, merge them into a new dict as a shallow copy; see http://stackoverflow.com/questions/38987/
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

# TODO document: has no path
class syncfiledata:
  #
  def __init__(self, path):
    logger.debug("syncfiledata::__init__()")
    statinfo = os.lstat(path)
    # TODO add content hash
    self.is_dir = stat.S_ISDIR(statinfo.st_mode)
    self.is_file = stat.S_ISREG(statinfo.st_mode)
    self.is_link = stat.S_ISLNK(statinfo.st_mode)
    self.atime = statinfo.st_atime
    self.mtime = statinfo.st_mtime
    self.size = statinfo.st_size
    
  #
  def equal_without_atime(self, other):
    logger.debug("syncfiledata::equal_without_atime() equal=%s (is_dir=%s is_file=%s is_link=%s mtime=%s size=%s)", 
                 self.is_dir == other.is_dir and self.is_file == other.is_file and self.is_link == other.is_link and
                 math.floor(self.mtime) == math.floor(other.mtime) and self.size == other.size,
                 self.is_dir == other.is_dir, self.is_file == other.is_file, self.is_link == other.is_link, 
                 math.floor(self.mtime) == math.floor(other.mtime), self.size == other.size)
    return self.is_dir == other.is_dir and self.is_file == other.is_file and self.is_link == other.is_link and \
        math.floor(self.mtime) == math.floor(other.mtime) and self.size == other.size;
  
  #
  def __str__(self):
    return str(self.__dict__)
  
# TODO document: has no path
class syncfilepair:
  #
  def __init__(self, syncfiledata_remote, syncfiledata_local):
    logger.debug("syncfilepair::__init__()")
    self.syncfiledata_remote = syncfiledata_remote
    self.syncfiledata_local = syncfiledata_local

#
syncaction = enum.Enum('syncaction', 'cp_local cp_remote ln_remote rm_local rm_remote')

#
class synctask:
  #
  def __init__(self, relative_path, action):
    logger.debug("synctask::__init__()")
    self.relative_path = relative_path
    self.action = action
    
# lazily syncs two folders with the given config parameters
class lazysync:
  # initialize object
  def __init__(self, config):
    logger.debug("lazysync::__init__()")
    self.queue = deque() # queue of synctasks
    self.files = {} # dictionary of syncfilepair to keep track of atimes
    self.sleep_time = 0
    
  # 
  def find_changes(self):
    logger.debug("lazysync::find_changes()")
    remote_folder_set, remote_file_set = relative_walk(config['remote']) 
    local_folder_set, local_file_set = relative_walk(config['local'])
    
    folders_both = remote_folder_set & local_folder_set # set of folders in both sets: assume they're identical
    folders_remote_only = remote_folder_set - local_folder_set # folders only in remote_folder_set: need to be copied
    folders_local_only = local_folder_set - remote_folder_set # folders only in local_folder_set: need to be copied
    files_both = remote_file_set & local_file_set # set of files in both sets: need to check details (size, etc.)
    files_remote_only = remote_file_set - local_file_set # files only in remote_file_set: need to be copied/symlinked
    files_local_only = local_file_set - remote_file_set # files only in local_file_set: need to be copied
    
    # folders_both and files_both need to be compared against self.files, and, if different, added to self.queue
    for relative_path in folders_both | files_both:
      logger.debug("lazysync::find_changes() found relative_path in both: %s", relative_path)
      new_syncfiledata_remote = syncfiledata(os.path.join(config['remote'], relative_path))
      new_syncfiledata_local = syncfiledata(os.path.join(config['local'], relative_path))
      
      if(new_syncfiledata_remote.equal_without_atime(new_syncfiledata_local)):
        logger.debug("lazysync::find_changes() equal")
        self.files[relative_path] = syncfilepair(new_syncfiledata_remote, new_syncfiledata_local)
      else:
        if(new_syncfiledata_remote.mtime > new_syncfiledata_local.mtime):
          logger.debug("lazysync::find_changes() NOT equal; task: ln remote local")
          self.queue.append(synctask(relative_path, 'ln_remote'))
        else:
          logger.debug("lazysync::find_changes() NOT equal; task: cp local remote")
          self.queue.append(synctask(relative_path, 'cp_local'))
      
    # *_only has to be added to self.queue
    for relative_path in folders_remote_only | files_remote_only:
      logger.debug("lazysync::find_changes() found relative_path remote only: %s", relative_path)
      self.queue.append(synctask(relative_path, 'ln_remote'))
    for relative_path in folders_local_only | files_local_only:
      logger.debug("lazysync::find_changes() found relative_path local only: %s", relative_path)
      self.queue.append(synctask(relative_path, 'cp_local'))
      
  #
  def action_cp_local(self, relative_path):
    logger.info("lazysync::action_cp_local() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    # TODO clear potential remote file/symlink; handle folders, files, symlinks
    
  #
  def action_cp_remote(self, relative_path):
    logger.info("lazysync::action_cp_remote() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    # TODO clear potential local symlink; handle folders, files, symlinks
    
  #
  def action_ln_remote(self, relative_path):
    logger.info("lazysync::action_ln_remote() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    # TODO clear potential local file; handle folders, files, symlinks
    
  #
  def process_next_change(self):
    logger.debug("lazysync::process_next_change() queue.size=%s", len(self.queue))
    task = self.queue.popleft()
    if(task.action == syncaction.cp_local):
      self.action_cp_local(task.relative_path)
    elif(task.action == syncaction.cp_remote):
      self.action_cp_remote(task.relative_path)
    elif(task.action == syncaction.ln_remote):
      self.action_ln_remote(task.relative_path)
    elif(task.action == syncaction.rm_local):
      pass
    elif(task.action == syncaction.rm_remote):
      pass
    else:
      logger.debug("lazysync::process_next_change() UNKNOWN ACTION")
      
    # TODO save state
  
  # loop to detect sigint
  def loop(self):
    logger.debug("lazysync::loop()")
    global sigint
    while(not sigint):
      start_time = timeit.default_timer()
      
      if(not self.queue and self.sleep_time == 0): # check filesystem if queue is empty and waiting time is up
        self.find_changes()
      if(self.queue): # process any changes that are left
        self.process_next_change()
        
      duration = timeit.default_timer() -  start_time
      logger.debug("lazysync::loop() duration=%f", duration)
      self.sleep_time += duration

      if(not self.queue and self.sleep_time > 0): # sleep to avoid 100% cpu load; a small delay is tolerable to quit 
        logger.debug("lazysync::loop() sleep with sleep_time=%f", self.sleep_time)
        time.sleep(min_sleep) # sleep fixed time to pace polling if duration is very short
        self.sleep_time = max(0, self.sleep_time - min_sleep)
  
# main    
if __name__ == "__main__":
  logger.debug("__main__()")
  signal.signal(signal.SIGINT, sigint_handler)
  config = merge_two_dicts(get_config(), parse_command_line()) # cmd line second to overwrite default settings in config
  sync = lazysync(config)
  sync.loop()

  # TODO logger.debug("__main__() stat_float_times=%d", os.stat_float_times()) # set to true if not true
    
  #for this_syncfile in syncfile_list:
    #logger.debug("__main__ path='%s' r_atime=%s r_mtime=%f l_atime=%f l_mtime=%f", 
                 #this_syncfile.path,
                 #datetime.datetime.fromtimestamp(this_syncfile.remote_atime).strftime('%Y-%m-%d %H:%M:%S.%f'), # python datetime only supports microseconds, not nanoseconds, see: http://stackoverflow.com/questions/15649942 
                 ##this_syncfile.remote_atime, 
                 #this_syncfile.remote_mtime, this_syncfile.local_atime, this_syncfile.local_mtime)
