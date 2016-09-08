#!/usr/bin/env python

from __future__ import print_function
from collections import deque # implements atomic append() and popleft() that do not require locking
import logging, argparse, os, sys, datetime, time, timeit

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

# has no path
class syncfiledata:
  #
  def __init__(self, remote_atime, remote_mtime, local_atime, local_mtime):
    logger.debug("syncfiledata::__init__()")
    self.remote_atime = remote_atime
    self.remote_mtime = remote_mtime
    self.local_atime = local_atime
    self.local_mtime = local_mtime

#
class synctask:
  #
  def __init__(self, path):
    logger.debug("synctask::__init__()")
    self.path = path
    
# lazily syncs two folders with the given config parameters
class lazysync:
  # initialize object
  def __init__(self, config):
    logger.debug("lazysync::__init__()")
    self.queue = deque() # queue of synctasks
    self.files = {} # dictionary of syncfiledata
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
    
    # folders_both and files_both need to be compared against data self.files
    # *_only has to be added to the self.queue
    
  #
  def process_next_change(self):
    logger.debug("lazysync::process_next_change()")
  
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
      self.sleep_time += max(duration, min_sleep)

      if(self.sleep_time > 0):
        logger.debug("lazysync::loop() sleep with sleep_time=%f", self.sleep_time)
        time.sleep(min_sleep) # avoid 100% cpu load and a small delay is tolerable to quit 
        self.sleep_time = min(0, self.sleep_time - min_sleep)
  
# main    
if __name__ == "__main__":
  logger.debug("__main__()")
  config = merge_two_dicts(get_config(), parse_command_line()) # cmd line second to overwrite default settings in config
  sync = lazysync(config)
  sync.loop()

  
  #logger.debug("__main__() stat_float_times=%d", os.stat_float_times()) # set to true if not true

  
  
  #syncfile_list = []
  
  #for path in folders_both:
    #remote_path = os.path.join(config['remote'], path)
    #local_path = os.path.join(config['local'], path)
    #logger.debug("__main__ lstat for remote_path='%s'", remote_path)
    #logger.debug("__main__ lstat for local_path='%s'", local_path)
    ##statinfo_remote = os.lstat(remote_path)
    ##statinfo_local = os.lstat(local_path)
    ##syncfile_list.append(syncfile(path, statinfo_remote.st_atime, statinfo_remote.st_mtime, statinfo_local.st_atime, statinfo_local.st_mtime))
    #syncfile_list.append(syncfile(path,
                                  #os.path.getatime(remote_path), os.path.getmtime(remote_path),
                                  #os.path.getatime(local_path), os.path.getmtime(local_path)))
  #for path in files_both:
    #remote_path = os.path.join(config['remote'], path)
    #local_path = os.path.join(config['local'], path)
    #logger.debug("__main__ lstat for remote_path='%s'", remote_path)
    #logger.debug("__main__ lstat for local_path='%s'", local_path)
    ##statinfo_remote = os.lstat(remote_path)
    ##statinfo_local = os.lstat(local_path)
    ##syncfile_list.append(syncfile(path, statinfo_remote.st_atime, statinfo_remote.st_mtime, statinfo_local.st_atime, statinfo_local.st_mtime))
    #syncfile_list.append(syncfile(path,
                                  #os.path.getatime(remote_path), os.path.getmtime(remote_path),
                                  #os.path.getatime(local_path), os.path.getmtime(local_path)))
  #for path in folders_remote_only:
    #remote_path = os.path.join(config['remote'], path)
    #logger.debug("__main__ lstat for remote_path='%s'", remote_path)
    ##statinfo_remote = os.lstat(remote_path)
    ##syncfile_list.append(syncfile(path, statinfo_remote.st_atime, statinfo_remote.st_mtime, 0, 0))
    #syncfile_list.append(syncfile(path, os.path.getatime(remote_path), os.path.getmtime(remote_path), 0, 0))
  #for path in files_remote_only:
    #remote_path = os.path.join(config['remote'], path)
    #logger.debug("__main__ lstat for remote_path='%s'", remote_path)
    ##statinfo_remote = os.lstat(remote_path)
    ##logger.debug("__main__ lstat for remote_path='%s' statinfo='%s'", remote_path, statinfo_remote)
    ##syncfile_list.append(syncfile(path, statinfo_remote.st_atime, statinfo_remote.st_mtime, 0, 0))
    #syncfile_list.append(syncfile(path, os.path.getatime(remote_path), os.path.getmtime(remote_path), 0, 0))
  #for path in folders_local_only:
    #local_path = os.path.join(config['local'], path)
    #logger.debug("__main__ lstat for local_path='%s'", local_path)
    ##statinfo_local = os.lstat(local_path)
    ##syncfile_list.append(syncfile(path, 0, 0, statinfo_local.st_atime, statinfo_local.st_mtime))
    #syncfile_list.append(syncfile(path, 0, 0, os.path.getatime(local_path), os.path.getmtime(local_path)))
  #for path in files_local_only:
    #local_path = os.path.join(config['local'], path)
    #logger.debug("__main__ lstat for local_path='%s'", local_path)
    ##statinfo_local = os.lstat(local_path)
    ##syncfile_list.append(syncfile(path, 0, 0, statinfo_local.st_atime, statinfo_local.st_mtime))
    #syncfile_list.append(syncfile(path, 0, 0, os.path.getatime(local_path), os.path.getmtime(local_path)))
    
  #for this_syncfile in syncfile_list:
    #logger.debug("__main__ path='%s' r_atime=%s r_mtime=%f l_atime=%f l_mtime=%f", 
                 #this_syncfile.path,
                 #datetime.datetime.fromtimestamp(this_syncfile.remote_atime).strftime('%Y-%m-%d %H:%M:%S.%f'), # python datetime only supports microseconds, not nanoseconds, see: http://stackoverflow.com/questions/15649942 
                 ##this_syncfile.remote_atime, 
                 #this_syncfile.remote_mtime, this_syncfile.local_atime, this_syncfile.local_mtime)
