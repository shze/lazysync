#!/usr/bin/env python

from __future__ import print_function
from collections import deque, defaultdict
import logging, argparse, os, sys, datetime, time, timeit, signal, stat, math, shutil, xdg.BaseDirectory, hashlib, errno
import jsonpickle, enum # enum is enum34

# global variables
sigint = False # variable to check for sigint
min_sleep = 1.9 # seconds
app_identifier = "lazysync" # used for all paths
backup_dir = '.%s' % (app_identifier) # to store old files for specific sync paths
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
def get_default_config():
  logger.debug("get_default_config()")
  return {
    'ignore' : [backup_dir] # relative paths
  }

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

#
def list_files(path):
  logger.debug("list_files() path='%s'", path)
  if(os.path.exists(path)):
    return [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))] 
  else:
    return []

#
def make_sure_path_exists(path):
  logger.debug("make_sure_path_exists()")
  try:
    os.makedirs(path)
  except OSError as e:
    if e.errno == errno.EEXIST and os.path.isdir(path):
      pass
    else:
      raise

#
def read_file_contents(path):
  logger.debug("read_file_contents()")
  f = open(path, 'r')
  contents = f.read()
  f.close()
  return contents

#
def write_file_contents(path, contents):
  logger.debug("write_file_contents()")
  f = open(path, 'w')
  f.write(contents)
  f.close()

#
class synctask:
  #
  def __init__(self, relative_path, action):
    logger.debug("synctask::__init__()")
    self.relative_path = relative_path
    self.action = action
    
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
class backupfiledata:
  #
  def __init__(self, path):
    self.path = path
    self.time = datetime.datetime.now()

# lazily syncs two folders with the given config parameters
class lazysync:
  # initialize object
  def __init__(self, config):
    logger.debug("lazysync::__init__()")
    self.config = config
    self.queue = deque() # queue of synctasks
    self.files = {} # dictionary of path -> syncfilepair to keep track of atimes and deleted files
    self.backup_files = defaultdict(list) # dictionary of original_path -> [backupfiledata] to keep deleted files
    self.sleep_time = 0
    self.syncactions = enum.Enum('syncactions', 'cp_local cp_remote ln_remote rm_local rm_remote')
    self.syncaction_functions = {self.syncactions.cp_local: self.action_cp_local,
                                 self.syncactions.cp_remote: self.action_cp_remote,
                                 self.syncactions.ln_remote: self.action_ln_remote,
                                 self.syncactions.rm_local: self.action_rm_local,
                                 self.syncactions.rm_remote: self.action_rm_remote}

    path_pair = self.config['remote'] + '?' + self.config['local']
    self.hashed_path_pair = hashlib.sha1(path_pair.encode()).hexdigest()
    self.load_data()
    
  # 
  def wait_for_sync_paths(self):
    logger.debug("lazysync::wait_for_sync_paths()")
    while(not sigint and not (os.path.isdir(self.config['remote']) and os.path.isdir(self.config['local']))):
      logger.debug("lazysync::wait_for_backup_paths() waiting for backup folders to be accessible")
      time.sleep(min_sleep * 2)
  
  #
  def wait_for_backup_paths(self):
    logger.debug("lazysync::wait_for_backup_paths()")
    remote_backup_dir = os.path.join(self.config['remote'], backup_dir)
    local_backup_dir = os.path.join(self.config['local'], backup_dir)
    while(not sigint and not (os.path.isdir(remote_backup_dir) and os.path.isdir(local_backup_dir))):
      logger.debug("lazysync::wait_for_backup_paths() waiting for backup folders to be accessible")
      time.sleep(min_sleep * 2)
    
  #
  def first_time_setup(self):
    logger.debug("lazysync::first_time_setup()")
    self.wait_for_sync_paths()
    self.save_data()
    
  #
  def load_data(self):
    logger.debug("lazysync::load_data()")
    data_paths = list(xdg.BaseDirectory.load_data_paths(app_identifier))
    if(not data_paths):
      self.first_time_setup()
      return 
      
    config_path = os.path.join(data_paths[0], self.hashed_path_pair)
    if(os.path.isfile(config_path)): # file exists, i.e. sync was setup before
      logger.debug("lazysync::load_data() reading config from '%s'", config_path)
      self.backup_files = jsonpickle.decode(read_file_contents(config_path))[0]
      
      # get existing_backup_files
      self.wait_for_backup_paths() # make sure backup paths are available
      remote_backup_dir = os.path.join(self.config['remote'], backup_dir)
      local_backup_dir = os.path.join(self.config['local'], backup_dir)
      existing_backup_files = list_files(remote_backup_dir) + list_files(local_backup_dir)
      logger.debug("lazysync::load_data() existing_backup_files=%s", existing_backup_files)
      # make sure backup_files is consistent with existing_backup_files
      backup_files_to_be_deleted = defaultdict(list)
      for original_path in self.backup_files:
        for backup_file in self.backup_files[original_path]:
          if os.path.isfile(backup_file.path): # check that file in backup_files exists
            existing_backup_files.remove(backup_file.path) # remove it from existing_backup_files
            logger.debug("lazysync::load_data() found backup file '%s' -> '%s' (%s)", original_path, backup_file.path, 
                         backup_file.time)
          else: # file does not exist
            backup_files_to_be_deleted[original_path].append(backup_file) # store to remove after iteration
      # remove backup_files that have missing files      
      for original_path in backup_files_to_be_deleted:
        for backup_file in backup_files_to_be_deleted[original_path]:
          self.backup_files[original_path].remove(backup_file)
          logger.debug("lazysync::load_data() removing data for '%s' -> '%s' (%s), backup file is missing", 
                       original_path, backup_file.path, backup_file.time)
      # remove existing_backup_files that have no data in backup_files
      for file in existing_backup_files:
        logger.debug("lazysync::load_data() removing backup file '%s', backup data is missing", file)
        os.remove(file)
  
  #
  def save_data(self):
    logger.debug("lazysync::save_data()")
    data_path = xdg.BaseDirectory.save_data_path(app_identifier) # makes sure dir exists
    make_sure_path_exists(os.path.join(self.config['remote'], backup_dir)) # remote backup dir
    make_sure_path_exists(os.path.join(self.config['local'], backup_dir)) # local backup dir

    config_path = os.path.join(data_path, self.hashed_path_pair)
    logger.debug("lazysync::save_data() writing config to '%s'", config_path)
    write_file_contents(config_path, jsonpickle.encode([self.backup_files]))
  
  #
  def filter_ignore(self, paths):
    logger.debug("lazysync::filter_ignore()")
    filtered = set()
    ignored = set()
    for p in paths:
      for i in self.config['ignore']:
        if p.startswith(i):
          ignored.add(p)
        else:
          filtered.add(p)
    
    logger.debug("lazysync::filter_ignore() ignored=%s", ignored)
    return filtered
    
  #
  def queue_remote_change(self, relative_path):
    logger.debug("lazysync::queue_remote_change()")
    if(relative_path in self.files):
      logger.debug("lazysync::find_changes() found locally removed relative_path: %s", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.rm_remote))
    else:
      logger.debug("lazysync::find_changes() found new remote relative_path: %s", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.ln_remote))
  
  #
  def queue_local_change(self, relative_path):
    logger.debug("lazysync::queue_local_change()")
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
    
    # create sets of folders and files in both sets or in one set only; filter out ignored paths
    folders_both = self.filter_ignore(remote_folder_set & local_folder_set) # check they are equal
    folders_remote_only = self.filter_ignore(remote_folder_set - local_folder_set) # need to be copied
    folders_local_only = self.filter_ignore(local_folder_set - remote_folder_set) # need to be copied
    files_both = self.filter_ignore(remote_file_set & local_file_set) # check they are equal
    files_remote_only = self.filter_ignore(remote_file_set - local_file_set) # need to be copied/symlinked
    files_local_only = self.filter_ignore(local_file_set - remote_file_set) # need to be copied
    
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
    
    if(os.path.islink(path_local)):
      pass # TODO clear potential existing link; handle local links
    elif(os.path.isdir(path_local)):
      logger.debug("lazysync::action_cp_local() relative_path is dir, mkdir remote='%s'", path_remote)
      os.makedirs(path_remote)
      shutil.copystat(path_local, path_remote)
    else:
      logger.debug("lazysync::action_cp_local() relative_path is file, cp local='%s' remote='%s'", path_local, 
                   path_remote)
      if(os.path.lexists(path_remote)):
        self.action_rm_remote(relative_path)
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
    
    if(os.path.islink(path_remote)):
      pass # TODO clear potential existing link; handle local links
    elif(os.path.isdir(path_remote)):
      logger.debug("lazysync::action_ln_remote() relative_path is dir, mkdir local='%s'", path_local)
      os.makedirs(path_local)
      shutil.copystat(path_remote, path_local)
    else:
      logger.debug("lazysync::action_ln_remote() relative_path is file, ln -s remote='%s' local='%s'", path_remote, 
                   path_local)
      if(os.path.lexists(path_local)):
        self.action_rm_local(relative_path)
      os.symlink(path_remote, path_local)
      
  #
  def action_rm(self, prefix, relative_path):
    logger.debug("lazysync::action_rm() prefix='%s' relative_path='%s'", prefix, relative_path)
    original_path = os.path.join(prefix, relative_path)
    
    if(os.path.islink(original_path)):
      logger.debug("lazysync::action_rm() rm symlink")
      os.remove(original_path) # symlinks are not backed up
    elif(os.path.isdir(original_path)):
      logger.debug("lazysync::action_rm() rm dir")
      # TODO for every path in dir action_rm(path)
    else:
      hashed_filename = hashlib.sha1(relative_path.encode()).hexdigest()
      backup_path = os.path.join(prefix, backup_dir, hashed_filename)
      
      logger.debug("lazysync::action_rm() rm file, back up in '%s'", backup_path)
      shutil.move(original_path, backup_path)
      self.backup_files[original_path].append(backupfiledata(backup_path))
      self.save_data()
      
  #
  def action_rm_local(self, relative_path):
    logger.debug("lazysync::action_rm_local() relative_path='%s'", relative_path)
    self.action_rm(self.config['local'], relative_path)
  
  #
  def action_rm_remote(self, relative_path):
    logger.debug("lazysync::action_rm_remote() relative_path='%s'", relative_path)
    self.action_rm(self.config['remote'], relative_path)
    
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
      self.wait_for_backup_paths()
      
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
  
  config = merge_two_dicts(parse_command_line(), get_default_config()) # cmd line first to overwrite default settings in config
  sync = lazysync(config)
  sync.loop()
