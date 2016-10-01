#!/usr/bin/env python

from __future__ import print_function
from collections import deque, defaultdict
import logging, argparse, os, sys, datetime, time, timeit, signal, stat, math, shutil, hashlib, errno, filecmp
import jsonpickle, subprocess, ofnotify, enum # enum is enum34

# global variables
sigint = False # variable to check for sigint
min_sleep = 1.9 # seconds
app_identifier = "lazysync" # used for all paths
relative_backup_dir = '.%s' % (app_identifier) # to store old files for specific sync paths
data_file = 'data' # to store the information about the different backup files

#
def add_logging_level(logger, debug_level, debug_level_name):
  #
  def custom_debug(msg, *args, **kwargs):
    if logger.isEnabledFor(debug_level):
      logger._log(debug_level, msg, args, kwargs)
    
  logging.addLevelName(debug_level, debug_level_name.upper()) # add level name to numeric level
  setattr(logger, debug_level_name.lower(), custom_debug) # allow calling logger.<lowercase_level_name>(msg)
  setattr(logging, debug_level_name.upper(), debug_level) # allow setting level as logging.<uppercase_level_name>

# set up logging
logger = logging.getLogger(os.path.basename(sys.argv[0]))
add_logging_level(logger, logging.DEBUG - 1, 'TRACE')  # add level TRACE at value DEBUG - 1, i.e. just below DEBUG
logger.setLevel(logging.INFO)
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
  logger.trace("get_default_config()")
  return {
    'ignore' : [relative_backup_dir] # relative paths
  }

# parse the folders to sync from the command line arguments
def parse_command_line():
  logger.trace("parse_command_line()")
  parser = argparse.ArgumentParser(description = 'Syncs lazily a remote folder and a local folder')
  parser.add_argument('-r', '--remote', metavar = 'RM', required = True, help = 'Path where the remote data is located')
  parser.add_argument('-l', '--local', metavar = 'LC', required = True, help = 'Path where the local data is located')
  parser.add_argument('-L', '--lazy', choices = ['y', 'n'], default = 'n', 
                      help = 'Sync lazily (on access) or not (always download)')
  args = parser.parse_args()
  return {
    'remote': os.path.abspath(args.remote), 
    'local': os.path.abspath(args.local),
    'lazy': args.lazy == 'y'
  }

# merge two dicts; if key is in both and data is list or dict, merge; else overwrite default_dct with dct
def merge_two_dicts(dct, default_dct):
  logger.trace("merge_two_dicts()")
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
  logger.trace("relative_walk()")
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
  logger.trace("list_files() path='%s'", path)
  if(os.path.exists(path)):
    return [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))] 
  else:
    return []

#
def make_sure_path_exists(path):
  logger.trace("make_sure_path_exists()")
  try:
    os.makedirs(path)
  except OSError as e:
    if e.errno == errno.EEXIST and os.path.isdir(path):
      pass
    else:
      raise

#
def read_file_contents(path):
  logger.trace("read_file_contents()")
  f = open(path, 'r')
  contents = f.read()
  f.close()
  return contents

#
def write_file_contents(path, contents):
  logger.trace("write_file_contents()")
  f = open(path, 'w')
  f.write(contents)
  f.close()

#
class synctask:
  #
  def __init__(self, relative_path, action):
    logger.trace("synctask::__init__()")
    self.relative_path = relative_path
    self.action = action
    
# stores the metadata for one file; contains no path
class syncfiledata:
  #
  def __init__(self, path):
    logger.trace("syncfiledata::__init__()")
    statinfo = os.lstat(path)
    # TODO add content hash
    self.is_dir = stat.S_ISDIR(statinfo.st_mode)
    self.is_file = stat.S_ISREG(statinfo.st_mode)
    self.is_link = stat.S_ISLNK(statinfo.st_mode)
    self.atime = statinfo.st_atime
    self.mtime = statinfo.st_mtime
    self.size = statinfo.st_size
    
  # return if two syncfiledatas are equal without looking at the atime; don't look at mtime or size for dirs and links
  def equal_without_atime(self, other):
    logger.trace("syncfiledata::equal_without_atime() self={%s}", self)
    logger.trace("syncfiledata::equal_without_atime() other={%s}", other)
    
    equal_is_dir = self.is_dir == other.is_dir
    equal_is_file = self.is_file == other.is_file
    equal_is_link = self.is_link == other.is_link
    # dir mtime changes when contents change, i.e. on every file sync -> avoid having to sync dir mtime everytime by not 
    # comparing mtime for dirs
    equal_mtime = math.floor(self.mtime) == math.floor(other.mtime) or self.is_dir or self.is_link
    equal_size = self.size == other.size or self.is_dir or self.is_link
    all_equal = equal_is_dir and equal_is_file and equal_is_link and equal_mtime and equal_size
    
    logger.trace("syncfiledata::equal_without_atime() equal=%s (is_dir=%s, is_file=%s, is_link=%s, mtime=%s, size=%s)", 
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
    logger.trace("syncfilepair::__init__()")
    self.syncfiledata_remote = syncfiledata_remote
    self.syncfiledata_local = syncfiledata_local

#
class backupfiledata:
  #
  def __init__(self, path):
    logger.trace("backupfiledata::__init__()")
    self.path = path
    self.time = datetime.datetime.now()

# lazily syncs two folders with the given config parameters
class lazysync(ofnotify.event_processor):
  # initialize object
  def __init__(self, config):
    self.config = config
    logger.info("lazysync::__init__() Using %slazy mode.", '' if config['lazy'] else 'non-')
    self.queue = deque() # queue of synctasks
    self.files = {} # dictionary of path -> syncfilepair to keep track of atimes and deleted files
    self.remote_backup_files = defaultdict(list) # dict original_path -> [backupfiledata] to keep deleted files
    self.local_backup_files = defaultdict(list) # dict original_path -> [backupfiledata] to keep deleted files
    self.sleep_time = 0
    self.syncactions = enum.Enum('syncactions', 'cp_local cp_remote ln_remote rm_local rm_remote')
    self.syncaction_functions = {self.syncactions.cp_local: self.action_cp_local,
                                 self.syncactions.cp_remote: self.action_cp_remote,
                                 self.syncactions.ln_remote: self.action_ln_remote,
                                 self.syncactions.rm_local: self.action_rm_local,
                                 self.syncactions.rm_remote: self.action_rm_remote}

    self.load_data()
    
    self.notifier = ofnotify.threaded_notifier(self, [self.config['remote']])
    if self.config['lazy']:
      self.notifier.start()
      
  #
  def wait_for_paths_available(self, paths):
    logger.trace("lazysync::wait_for_paths_available() paths=%s", paths) # TODO make sure output is correctly formatted
    sleep = min_sleep
    while not sigint:
      all_path_available = True
      for path in paths:
        if not os.path.isdir(path):
          all_path_available = False
        
      if all_path_available:
        return 

      logger.info("lazysync::wait_for_paths_available() waiting for paths=%s to be accessible", paths)
      time.sleep(sleep)
      sleep += 0.4 * min_sleep
    
  #
  def first_time_setup(self):
    logger.debug("lazysync::first_time_setup()")
    self.wait_for_paths_available([self.config['remote'], self.config['local']])
    self.save_data()
    
  #
  def load_path_data(self, prefix):
    logger.debug("lazysync::load_path_data() prefix='%s'", prefix)
    backup_dir = os.path.join(prefix, relative_backup_dir)
    backup_data_file = os.path.join(backup_dir, data_file)
    self.wait_for_paths_available([backup_dir]) # make sure backup path is available
    if sigint: # exit here if ctrl-c was pressed while we were waiting and not try to read the files below
      sys.exit(0)
    if not os.path.isfile(backup_data_file):
      return defaultdict(list)

    logger.debug("lazysync::load_path_data() reading config from '%s'", backup_data_file)
    expected_backup_files = jsonpickle.decode(read_file_contents(backup_data_file))[0]
    existing_backup_files = list_files(backup_dir)
    logger.trace("lazysync::load_path_data() expected_backup_files=%s", expected_backup_files)
    logger.trace("lazysync::load_path_data() existing_backup_files=%s", existing_backup_files)
    # make sure expected_backup_files is consistent with existing_backup_files: figure out which expected_backup_files 
    # are existing (remove them from existing_backup_files) and which are not (add them to backup_files_to_be_deleted)
    backup_files_to_be_deleted = defaultdict(list)
    for original_path in expected_backup_files:
      for backup_file_data in expected_backup_files[original_path]:
        if os.path.isfile(backup_file_data.path): # check that backup file exists
          existing_backup_files.remove(backup_file_data.path) # remove it from existing_backup_files
          logger.info("lazysync::load_path_data() found backup file '%s' -> '%s' (%s)", original_path, 
                      backup_file_data.path, backup_file_data.time)
        else: # file does not exist
          backup_files_to_be_deleted[original_path].append(backup_file_data) # store to remove after iteration
    # remove expected_backup_files that have missing files      
    for original_path in backup_files_to_be_deleted:
      for backup_file_data in backup_files_to_be_deleted[original_path]:
        expected_backup_files[original_path].remove(backup_file_data)
        logger.info("lazysync::load_path_data() removing data for '%s' -> '%s' (%s), backup file is missing", 
                    original_path, backup_file_data.path, backup_file_data.time)
    # remove existing_backup_files that have no data in backup_files
    for file in existing_backup_files:
      if not file == backup_data_file: # do not delete the data file
        logger.info("lazysync::load_path_data() removing backup file '%s', backup data is missing", file)
        os.remove(file)
      
    return expected_backup_files
    
  #
  def load_data(self):
    logger.trace("lazysync::load_data()")
    local_backup_dir = os.path.join(self.config['local'], relative_backup_dir)
    if not os.path.isdir(local_backup_dir): # assume the local backup dir is always available
      self.first_time_setup()
      return 
    
    self.remote_backup_files = self.load_path_data(self.config['remote'])
    self.local_backup_files = self.load_path_data(self.config['local'])
    self.save_data() # save after making sure backup files are consistent
    
  #
  def save_path_data(self, prefix, backup_files_dict):
    logger.trace("lazysync::save_path_data()")
    backup_dir = os.path.join(prefix, relative_backup_dir)
    backup_data_file = os.path.join(backup_dir, data_file)
    make_sure_path_exists(backup_dir)
    logger.debug("lazysync::save_path_data() writing data to '%s' data=%s", backup_data_file, backup_files_dict)
    write_file_contents(backup_data_file, jsonpickle.encode([backup_files_dict]))
  
  #
  def save_data(self):
    logger.trace("lazysync::save_data() self.remote_backup_files=%s self.local_backup_files=%s", 
                 self.remote_backup_files, self.local_backup_files)
    self.save_path_data(self.config['remote'], self.remote_backup_files)
    self.save_path_data(self.config['local'], self.local_backup_files)
  
  #
  def filter_ignore(self, paths):
    logger.trace("lazysync::filter_ignore()")
    filtered = set()
    ignored = set()
    for p in paths:
      for i in self.config['ignore']:
        ignored.add(p) if p.startswith(i) else filtered.add(p)
    
    logger.debug("lazysync::filter_ignore() ignored=%s", ignored)
    return filtered

  #    
  def process_ofnotify_event(self, event):
    relative_path = os.path.relpath(event.path, self.config['remote'])
    path_local = os.path.join(self.config['local'], relative_path)
    path_remote = event.path
    logger.trace("lazysync::process_event() '%s' event=%s", relative_path, event.type)
    if(event.type == ofnotify.event_types.close and os.path.islink(path_local) 
       and os.path.realpath(path_local) == path_remote):
      logger.info("lazysync::process_event() '%s': symlinked remote has been accessed, downloading; task: cp remote local", 
                  relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.cp_remote))

  #
  def queue_change_for_remote(self, relative_path):
    logger.trace("lazysync::queue_change_for_remote() '%s'", relative_path)
    if(relative_path in self.files):
      logger.info("lazysync::find_changes() '%s': locally removed path; task: rm remote", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.rm_remote))
    else:
      logger.info("lazysync::find_changes() '%s': new remote path; task: ln remote local", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.ln_remote))
  
  #
  def queue_change_for_local(self, relative_path):
    logger.trace("lazysync::queue_change_for_local()")
    if(relative_path in self.files):
      logger.info("lazysync::find_changes() '%s': remotely removed path; task: rm local", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.rm_local))
    else:
      logger.info("lazysync::find_changes() '%s': new local path; task: cp local remote", relative_path)
      self.queue.append(synctask(relative_path, self.syncactions.cp_local))
  
  # 
  def find_changes(self):
    logger.trace("lazysync::find_changes()")
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
      logger.debug("lazysync::find_changes() '%s': found path in both", relative_path)
      path_remote = os.path.join(self.config['remote'], relative_path)
      path_local = os.path.join(self.config['local'], relative_path)
      new_syncfiledata_remote = syncfiledata(path_remote)
      new_syncfiledata_local = syncfiledata(path_local)
      
      if(os.path.islink(path_local) and os.path.realpath(path_local) == path_remote): # always to file, never to dir
        # if a symlink local -> remote is found in non-lazy mode, download the file
        if(not self.config['lazy']):
          logger.info("lazysync::find_changes() '%s': found symlink in non-lazy mode, downloading; task: cp remote local", 
                      relative_path)
          self.queue.append(synctask(relative_path, self.syncactions.cp_remote))
        else: # otherwise just update if it was not tracked before
          logger.debug("lazysync::find_changes() '%s': path_local is a symlink to path_remote, no changes needed", 
                       relative_path)
          if(relative_path not in self.files):
            self.files[relative_path] = syncfilepair(new_syncfiledata_remote, new_syncfiledata_local)
      elif(new_syncfiledata_remote.equal_without_atime(new_syncfiledata_local)):
        logger.debug("lazysync::find_changes() '%s': equal", relative_path)
        if(relative_path not in self.files):
          self.files[relative_path] = syncfilepair(new_syncfiledata_remote, new_syncfiledata_local)
      else:
        if(new_syncfiledata_remote.mtime > new_syncfiledata_local.mtime):
          logger.info("lazysync::find_changes() '%s': NOT equal; task: ln remote local", relative_path)
          self.queue.append(synctask(relative_path, self.syncactions.ln_remote))
        else:
          logger.info("lazysync::find_changes() '%s': NOT equal; task: cp local remote", relative_path)
          self.queue.append(synctask(relative_path, self.syncactions.cp_local))
      
    # *_only has to be added to self.queue, either to cp/ln if new, or to rm if old
    for relative_path in folders_remote_only: # for creating, do folders first, then files
      self.queue_change_for_remote(relative_path)
    for relative_path in files_remote_only:
      self.queue_change_for_remote(relative_path)
    for relative_path in folders_local_only:
      self.queue_change_for_local(relative_path)
    for relative_path in files_local_only:
      self.queue_change_for_local(relative_path)
      
  #
  def update_file_tracking(self, relative_path):
    logger.trace("lazysync::update_file_tracking() relative_path='%s'", relative_path)
    new_syncfiledata_remote = syncfiledata(os.path.join(self.config['remote'], relative_path))
    new_syncfiledata_local = syncfiledata(os.path.join(self.config['local'], relative_path))
    self.files[relative_path] = syncfilepair(new_syncfiledata_remote, new_syncfiledata_local)
    logger.trace("lazysync::update_file_tracking() remote=%s", new_syncfiledata_remote)
    logger.trace("lazysync::update_file_tracking() local=%s", new_syncfiledata_local)
    
  # 
  def get_last_backup_file_data(self, original_path):
    logger.trace("lazysync::get_last_backup_file_data() '%s'", original_path)
    backup_files = self.remote_backup_files if original_path.startswith(self.config['remote']) else self.local_backup_files
    last_backup_file_data = None
    for backup_file_data in backup_files[original_path]:
      if last_backup_file_data == None or backup_file_data.time > last_backup_file_data.time:
        last_backup_file_data = backup_file_data
    logger.debug("lazysync::get_last_backup_file_data() '%s' -> '%s' (%s)", original_path, last_backup_file_data.path, 
                 last_backup_file_data.time)
    return last_backup_file_data
    
  #
  def remove_backup_file(self, original_path, backup_file_data):
    logger.trace("lazysync::remove_backup_file() '%s' -> '%s' (%s)", original_path, backup_file_data.path, 
                 backup_file_data.time)
    if original_path.startswith(self.config['remote']): # remove from backup_file_data from dict, either remote or local
      self.remote_backup_files[original_path].remove(backup_file_data)
    else:
      self.local_backup_files[original_path].remove(backup_file_data)
    os.remove(backup_file_data.path) # remove backup file
    self.save_data() # save data

  #
  def action_cp_local(self, relative_path):
    logger.debug("lazysync::action_cp_local() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    if(os.path.islink(path_local)):
      link_target = os.path.realpath(path_local)
      if(os.path.realpath(path_local).startswith(self.config['local'])):
        link_target = os.path.join(self.config['remote'], os.path.relpath(link_target, self.config['local']))
      logger.info("lazysync::action_cp_local() relative_path is symlink, ln -s target='%s' remote='%s'", link_target, 
                  path_remote)
      if(os.path.lexists(path_remote)):
        self.action_rm_remote(relative_path)
      os.symlink(link_target, path_remote)
    elif(os.path.isdir(path_local)):
      logger.info("lazysync::action_cp_local() relative_path is dir, mkdir remote='%s'", path_remote)
      os.makedirs(path_remote)
      shutil.copystat(path_local, path_remote)
    else:
      logger.info("lazysync::action_cp_local() relative_path is file, cp local='%s' remote='%s'", path_local, 
                   path_remote)
      if os.path.lexists(path_remote): # remove an old file it it exists
        self.action_rm_remote(relative_path)
      shutil.copy2(path_local, path_remote) # copy new file
      last_backup_file_data = self.get_last_backup_file_data(path_remote) # get last backed up version
      if last_backup_file_data is not None and filecmp.cmp(path_remote, last_backup_file_data.path, shallow = False):
        logger.info("lazysync::action_cp_local() files remote='%s' and remote_backup='%s' are identical, not keeping remote_backup", 
                    path_remote, last_backup_file_data.path)
        self.remove_backup_file(path_remote, last_backup_file_data) # remove the previous version

    self.update_file_tracking(relative_path)

  #
  def action_cp_remote(self, relative_path):
    logger.debug("lazysync::action_cp_remote() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)

    if(os.path.islink(path_remote)):
      link_target = os.path.realpath(path_remote)
      if(os.path.realpath(path_remote).startswith(self.config['remote'])):
        link_target = os.path.join(self.config['local'], os.path.relpath(link_target, self.config['remote']))
      logger.info("lazysync::action_cp_remote() relative_path is symlink, ln -s target='%s' local='%s'", link_target, 
                  path_local)
      if(os.path.lexists(path_local)):
        self.action_rm_local(relative_path)
      os.symlink(link_target, path_local)
    elif(os.path.isdir(path_remote)):
      logger.info("lazysync::action_cp_remote() relative_path is dir, mkdir local='%s'", path_local)
      if(not os.path.lexists(path_local)): # only create the path is it does not exist (could be created with a subdir)
        os.makedirs(path_local)
      shutil.copystat(path_remote, path_local)
    else:
      logger.info("lazysync::action_cp_remote() relative_path is file, cp remote='%s' local='%s'", path_remote, 
                  path_local)
      if os.path.lexists(path_local): # remove an old file it it exists
        self.action_rm_local(relative_path)
      shutil.copy2(path_remote, path_local) # copy new file
      last_backup_file_data = self.get_last_backup_file_data(path_local) # get last backed up version
      if last_backup_file_data is not None and filecmp.cmp(path_local, last_backup_file_data.path, shallow = False):
        logger.info("lazysync::action_cp_remote() files local='%s' and local_backup='%s' are identical, not keeping local_backup", 
                    path_local, last_backup_file_data.path)
        self.remove_backup_file(path_local, last_backup_file_data) # remove the previous version
      
    self.update_file_tracking(relative_path)
    
  #
  def action_ln_remote(self, relative_path):
    logger.debug("lazysync::action_ln_remote() relative_path='%s'", relative_path)
    path_remote = os.path.join(self.config['remote'], relative_path)
    path_local = os.path.join(self.config['local'], relative_path)
    
    if(os.path.islink(path_remote) or os.path.isdir(path_remote)) or not self.config['lazy']:
      self.action_cp_remote(relative_path)
    else:
      logger.info("lazysync::action_ln_remote() relative_path is file, ln -s remote='%s' local='%s'", path_remote, 
                   path_local)
      if(os.path.lexists(path_local)):
        self.action_rm_local(relative_path)
      os.symlink(path_remote, path_local)
      self.update_file_tracking(relative_path)
      
  #
  def action_rm(self, prefix, relative_path):
    logger.debug("lazysync::action_rm() prefix='%s' relative_path='%s'", prefix, relative_path)
    original_path = os.path.join(prefix, relative_path)
    
    if(os.path.islink(original_path)):
      logger.info("lazysync::action_rm() rm symlink")
      os.remove(original_path) # symlinks are not backed up
      if relative_path in self.files: # check, b/c an old file can exist from a previous run, but no entry in self.files
        del self.files[relative_path]
    elif(os.path.isdir(original_path)):
      logger.debug("lazysync::action_rm() rm dir")
      # remove dir contents recursively
      for dirpath, dirnames, filenames in os.walk(original_path):
        relative_dirpath = os.path.relpath(dirpath, prefix)
        for dirname in dirnames:
          logger.info("lazysync::action_rm() recursively rm '%s'", os.path.join(relative_dirpath, dirname))
          self.action_rm(prefix, os.path.join(relative_dirpath, dirname))
        for filename in filenames: 
          logger.info("lazysync::action_rm() recursively rm '%s'", os.path.join(relative_dirpath, filename))
          self.action_rm(prefix, os.path.join(relative_dirpath, filename))
      # remove dir
      os.rmdir(original_path)
      if relative_path in self.files: # check, b/c an old file can exist from a previous run, but no entry in self.files
        del self.files[relative_path]
    elif(os.path.isfile(original_path)): # make sure file still exists and was not deleted recursively in a subdir
      hash_input = relative_path + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
      hashed_filename = hashlib.sha1(hash_input.encode()).hexdigest()
      backup_path = os.path.join(prefix, relative_backup_dir, hashed_filename)
      
      logger.info("lazysync::action_rm() rm file, back up in '%s' (hash_input='%s')", backup_path, hash_input)
      shutil.move(original_path, backup_path)

      if prefix == self.config['remote']:
        self.remote_backup_files[original_path].append(backupfiledata(backup_path))
      else:
        self.local_backup_files[original_path].append(backupfiledata(backup_path))
      self.save_data()
      
      if relative_path in self.files: # check, b/c an old file can exist from a previous run, but no entry in self.files
        del self.files[relative_path]
      
  #
  def action_rm_local(self, relative_path):
    logger.info("lazysync::action_rm_local() relative_path='%s'", relative_path)
    self.action_rm(self.config['local'], relative_path)
  
  #
  def action_rm_remote(self, relative_path):
    logger.info("lazysync::action_rm_remote() relative_path='%s'", relative_path)
    self.action_rm(self.config['remote'], relative_path)
    
  #
  def process_next_change(self):
    logger.debug("lazysync::process_next_change() queue.size=%s", len(self.queue))
    task = self.queue.popleft()
    if(task.action in self.syncaction_functions):
      self.syncaction_functions[task.action](task.relative_path)
    else:
      logger.debug("lazysync::process_next_change() no action for task '%s'", task.action)
  
  # loop to detect sigint
  def loop(self):
    logger.trace("lazysync::loop()")
    global sigint
    print_update_threshold = 15 // min_sleep
    update_count = print_update_threshold - 1
    remote_backup_dir = os.path.join(self.config['remote'], relative_backup_dir)
    local_backup_dir = os.path.join(self.config['local'], relative_backup_dir)
    while(not sigint):
      self.wait_for_paths_available([remote_backup_dir, local_backup_dir])
      
      start_time = timeit.default_timer()
      
      logger.trace("lazysync::loop() self.files.path=%s", self.files.keys())
      if(not self.queue and self.sleep_time == 0): # check filesystem if queue is empty and waiting time is up
        self.find_changes()
        update_count += 1
      if(self.queue): # process any changes that are left
        self.process_next_change()
        update_count = print_update_threshold
        
      duration = timeit.default_timer() -  start_time
      logger.debug("lazysync::loop() duration=%f", duration)
      self.sleep_time += duration

      if(not self.queue and self.sleep_time > 0): # sleep to avoid 100% cpu load; a small delay is tolerable to quit 
        level = logging.INFO if(duration > min_sleep or update_count % print_update_threshold == 0) else logging.DEBUG
        logger.log(level, "lazysync::loop() sleep with sleep_time=%f", self.sleep_time)
        time.sleep(min_sleep) # sleep fixed time to pace polling if duration is very short
        self.sleep_time = max(0, self.sleep_time - min_sleep)
        
    if self.config['lazy']:
      self.notifier.stop()
  
# main    
if __name__ == "__main__":
  logger.trace("__main__()")
  signal.signal(signal.SIGINT, sigint_handler)
  # not needed, b/c syncfiledata.equal_without_atime() only uses the int part b/c remote fs only report int values
  os.stat_float_times(True) 
  
  config = merge_two_dicts(parse_command_line(), get_default_config()) # cmd line first to overwrite default settings 
  sync = lazysync(config)
  sync.loop()
