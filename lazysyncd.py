#!/usr/bin/env python

from __future__ import print_function
from collections import deque # implements atomic append() and popleft() that do not require locking
import logging, argparse, os, sys, datetime, time, timeit, signal, stat, math, shutil, enum # enum is enum34

# global variables
sigint = False # variable to check for sigint
min_sleep = 1.9 # seconds
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
  
#
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

# merge two dicts; if key is in both and data is list or dict, merge; else overwrite default_dct with dct
def merge_two_dicts(dct, default_dct):
  final_dct = {}
  for k in set(dct).union(default_dct): # for all keys
    # if k is in both dicts, and data is list or dict, merge
    if k in dct and k in default_dct and isinstance(dct[k], list) and isinstance(default_dct[k], list):
      final_dct[k] = dct[k] + default_dct[k]
    elif k in dct and k in default_dct and isinstance(dct[k], dict) and isinstance(default_dct[k], dict):
      final_dct[k] = merge_two_dicts(dct[k], default_dct[k])
    # if k is not in both, use dct first, and default_dct second
    elif k in dct:
      final_dct[k] = dct[k]
    else:
      final_dct[k] = default_dct[k]
  return final_dct

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

# stores the metadata for one file; contains no path
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
    
  # return if two syncfiledatas are equal without looking at the atime; don't look at mtime or size for directories
  def equal_without_atime(self, other):
    logger.debug("syncfiledata::equal_without_atime() self={%s}", self)
    logger.debug("syncfiledata::equal_without_atime() other={%s}", other)
    
    equal_is_dir = self.is_dir == other.is_dir
    equal_is_file = self.is_file == other.is_file
    equal_is_link = self.is_link == other.is_link
    # dir mtime changes when contents change, i.e. on every file sync -> avoid having to sync dir mtime everytime by not 
    # comparing mtime for dirs
    equal_mtime = math.floor(self.mtime) == math.floor(other.mtime) or self.is_dir
    equal_size = self.size == other.size or self.is_dir
    all_equal = equal_is_dir and equal_is_file and equal_is_link and equal_mtime and equal_size
    
    logger.debug("syncfiledata::equal_without_atime() equal=%s (is_dir=%s, is_file=%s, is_link=%s, mtime=%s, size=%s)", 
                 all_equal, equal_is_dir, equal_is_file, equal_is_link, equal_mtime, equal_size)
    return all_equal;
  
  #
  def __str__(self):
    return "is_dir=%s, is_file=%s, is_link=%s, atime=%f (%s), mtime=%f (%s), size=%s" \
        % (self.is_dir, self.is_file, self.is_link, 
           self.atime, datetime.datetime.fromtimestamp(self.atime).strftime('%Y-%m-%d %H:%M:%S.%f'), 
           self.mtime, datetime.datetime.fromtimestamp(self.mtime).strftime('%Y-%m-%d %H:%M:%S.%f'),
           self.size)
  
# stores the metadata for a pair of synced files; contains no path
class syncfilepair:
  #
  def __init__(self, syncfiledata_remote, syncfiledata_local):
    logger.debug("syncfilepair::__init__()")
    self.syncfiledata_remote = syncfiledata_remote
    self.syncfiledata_local = syncfiledata_local

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
    self.config = config
    self.queue = deque() # queue of synctasks
    self.files = {} # dictionary of syncfilepair to keep track of atimes
    self.sleep_time = 0
    self.syncactions = enum.Enum('syncactions', 'cp_local cp_remote ln_remote rm_local rm_remote')
    self.syncaction_functions = {self.syncactions.cp_local: self.action_cp_local,
                                 self.syncactions.cp_remote: self.action_cp_remote,
                                 self.syncactions.ln_remote: self.action_ln_remote,
                                 self.syncactions.rm_local: self.action_rm_local,
                                 self.syncactions.rm_remote: self.action_rm_remote}
    
  #
  def queue_remote_change(self, relative_path):
    if(relative_path in self.files):
      logger.debug("lazysync::find_changes() found locally removed relative_path: %s", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.rm_remote))
    else:
      logger.debug("lazysync::find_changes() found new remote relative_path: %s", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.ln_remote))
  
  #
  def queue_local_change(self, relative_path):
    if(relative_path in self.files):
      logger.debug("lazysync::find_changes() found remotely removed relative_path: %s", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.rm_local))
    else:
      logger.debug("lazysync::find_changes() found new local relative_path: %s", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.cp_local))
  
  # 
  def find_changes(self):
    logger.debug("lazysync::find_changes()")
    remote_folder_set, remote_file_set = relative_walk(self.config['remote']) 
    local_folder_set, local_file_set = relative_walk(self.config['local'])
    
    folders_both = remote_folder_set & local_folder_set # set of folders in both sets: assume they're identical
    folders_remote_only = remote_folder_set - local_folder_set # folders only in remote_folder_set: need to be copied
    folders_local_only = local_folder_set - remote_folder_set # folders only in local_folder_set: need to be copied
    files_both = remote_file_set & local_file_set # set of files in both sets: need to check details (size, etc.)
    files_remote_only = remote_file_set - local_file_set # files only in remote_file_set: need to be copied/symlinked
    files_local_only = local_file_set - remote_file_set # files only in local_file_set: need to be copied
    
    # folders_both and files_both need to be compared against self.files, and, if different, added to self.queue
    for relative_path in folders_both | files_both: # for existing folders and files ordering them is not needed
      logger.debug("lazysync::find_changes() found relative_path in both: %s", relative_path)
      path_remote = os.path.join(self.config['remote'], relative_path)
      path_local = os.path.join(self.config['local'], relative_path)
      new_syncfiledata_remote = syncfiledata(path_remote)
      new_syncfiledata_local = syncfiledata(path_local)
      
      if(os.path.islink(path_local) and os.path.realpath(path_local) == path_remote):
        logger.debug("lazysync::find_changes() path_local is a symlink to path_remote, no changes needed")
        continue
      if(new_syncfiledata_remote.equal_without_atime(new_syncfiledata_local)):
        logger.debug("lazysync::find_changes() equal")
        self.files[relative_path] = syncfilepair(new_syncfiledata_remote, new_syncfiledata_local)
      else:
        if(new_syncfiledata_remote.mtime > new_syncfiledata_local.mtime):
          logger.debug("lazysync::find_changes() NOT equal; task: ln remote local")
          self.queue.append(synctask(relative_path, self.syncactions.ln_remote))
        else:
          logger.debug("lazysync::find_changes() NOT equal; task: cp local remote")
          self.queue.append(synctask(relative_path, self.syncactions.cp_local))
      
    # *_only has to be added to self.queue, either to cp/ln if new, or to rm if old
    for relative_path in folders_remote_only: # for creating, do folders first, then files
      self.queue_remote_change(relative_path)
    for relative_path in files_remote_only:
      self.queue_remote_change(relative_path)
    for relative_path in folders_local_only:
      self.queue_local_change(relative_path)
    for relative_path in files_local_only:
      self.queue_local_change(relative_path)
      
  #
  def action_cp_local(self, relative_path):
    logger.debug("lazysync::action_cp_local() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    # TODO clear potential remote file/symlink; handle folders, files, symlinks
    if(os.path.islink(path_local)):
      pass
    elif(os.path.isdir(path_local)):
      logger.debug("lazysync::action_cp_local() relative_path is dir, mkdir remote='%s'", path_remote)
      os.makedirs(path_remote)
      shutil.copystat(path_local, path_remote)
    else:
      logger.debug("lazysync::action_cp_local() relative_path is file, cp local='%s' remote='%s'", path_local, 
                   path_remote)
      shutil.copy2(path_local, path_remote)

  #
  def action_cp_remote(self, relative_path):
    logger.debug("lazysync::action_cp_remote() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    # TODO clear potential local symlink; handle folders, files, symlinks
    
  #
  def action_ln_remote(self, relative_path):
    logger.debug("lazysync::action_ln_remote() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    # TODO clear potential local file; handle folders, files, symlinks
    if(os.path.islink(path_remote)):
      pass
    elif(os.path.isdir(path_remote)):
      logger.debug("lazysync::action_ln_remote() relative_path is dir, mkdir local='%s'", path_local)
      os.makedirs(path_local)
      shutil.copystat(path_remote, path_local)
    else:
      logger.debug("lazysync::action_ln_remote() relative_path is file, ln -s remote='%s' local='%s'", path_remote, 
                   path_local)
      os.symlink(path_remote, path_local)
    
  #
  def action_rm_local(self, relative_path):
    pass
  
  #
  def action_rm_remote(self, relative_path):
    pass
    
  #
  def process_next_change(self):
    logger.debug("lazysync::process_next_change() queue.size=%s", len(self.queue))
    task = self.queue.popleft()
    if(task.action in self.syncaction_functions):
      self.syncaction_functions[task.action](task.relative_path)
    else:
      logger.debug("lazysync::process_next_change() no action for task '%s'", task.action)
      
    # TODO save state
    #new_syncfiledata_remote = syncfiledata(os.path.join(self.config['remote'], task.relative_path))
    #new_syncfiledata_local = syncfiledata(os.path.join(self.config['local'], task.relative_path))
    #self.files[task.relative_path] = syncfilepair(new_syncfiledata_remote, new_syncfiledata_local)
  
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
  os.stat_float_times(True) # not needed, b/c syncfiledata.equal_without_atime() only uses the int part b/c remote fs only report int values
  
  config = merge_two_dicts(get_config(), parse_command_line()) # cmd line second to overwrite default settings in config
  sync = lazysync(config)
  sync.loop()
