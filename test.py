#!/usr/bin/env python

from enum import Enum
import subprocess, os, shutil, time, logging, sys, errno

# set up logging
logger = logging.getLogger(os.path.basename(sys.argv[0]))
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(console_handler)

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

# enums for testing
path_type = Enum('path_type', 'file dir link delete')
path_location = Enum('path_location', 'remote local')

#
class synctest_path:
  #
  def __init__(self, location, name, type, size = 0, link_src = None):
    self.location = location
    self.name = name
    self.type = type
    self.size = size
    self.link_src = link_src
   
#
class synctest:
  #
  def __init__(self, name, cmdlineparams, init_paths, sync_paths, result_paths):
    logger.debug("synctest::__init__()")
    self.name = name
    self.cmdlineparams = cmdlineparams
    self.init_paths = init_paths
    self.sync_paths = sync_paths
    self.result_paths = result_paths
  
  #
  def process_path(self, path):
    logger.debug("synctest::process_path()")
    full_path = os.path.join(self.name, path.location.name, path.name)
    logger.debug("synctest::process_path() full_path=%s", full_path)
    if(path.type == path_type.file):
      # dd can write files of length 0 only as blocksize > 0 and count == 0
      blocksize = min(path.size, 1000000) if path.size > 0 else 1 # ~1MB
      count = path.size / blocksize; # writes exact size for <1MB file size, multiples of 1MB for larger files
      cmdline = "dd if=/dev/zero of=" + full_path + " bs=" + str(blocksize) + " count=" + str(count)
      logger.debug("synctest::process_path() cmdline='%s'", cmdline)
      FNULL = open(os.devnull, 'w')
      subprocess.call(cmdline.split(), stdout = FNULL, stderr = FNULL)
    elif(path.type == path_type.dir):
      os.makedirs(full_path)
    elif(path.type == path_type.link):
      os.symlink(self.link_src, full_path)
    elif(path.type == path_type.delete):
      if(os.path.lexists(full_path)):
        if(os.path.isdir(full_path)):
          shutil.rmtree(full_path)
        else:
          os.remove(full_path)
      else:
        logger.info()("synctest::process_path() Cannot delete path '%s', it does not exist!", full_path)
  
  #
  def process_paths(self, paths):
    logger.debug("synctest::process_paths()")
    for this_path in paths:
      self.process_path(this_path)
  
  #
  def paths_correct(self):
    logger.debug("synctest::paths_correct()")
    remote_folders, remote_files = relative_walk(os.path.join(self.name, "remote"))
    logger.debug("synctest::paths_correct() remote_folders: %s", remote_folders)
    logger.debug("synctest::paths_correct() remote_files: %s", remote_files)
    local_folders, local_files = relative_walk(os.path.join(self.name, "local"))
    logger.debug("synctest::paths_correct() local_folders: %s", local_folders)
    logger.debug("synctest::paths_correct() local_files: %s", local_files)
    
    for this_path in self.result_paths:
      full_path = os.path.join(self.name, this_path.location.name, this_path.name)
      logger.debug("synctest::paths_correct() checking correctness for path '%s', name '%s'", full_path, this_path.name)
      logger.debug("synctest::paths_correct() is_dir('%s') == %d", full_path, os.path.isdir(full_path))
      if(this_path.location == path_location.remote):
        if(os.path.isdir(full_path) and this_path.name in remote_folders):
          logger.debug("synctest::paths_correct() correctly found folder remote/%s", this_path.name)
          remote_folders.remove(this_path.name)
        elif(this_path.name in remote_files):
          if(this_path.type == path_type.link and os.path.islink(full_path)):
            logger.debug("synctest::paths_correct() correctly found link remote/%s", this_path.name)
            remote_files.remove(this_path.name)
          elif(this_path.type == path_type.file and not os.path.islink(full_path)):
            logger.debug("synctest::paths_correct() correctly found file remote/%s", this_path.name)
            remote_files.remove(this_path.name)
          else:
            logger.info("synctest::paths_correct() found file instead of link or link instead of file remote/%s", this_path.name)
            return False
        else:
          logger.info("synctest::paths_correct() did not find path remote/%s", this_path.name)
          return False
      elif(this_path.location == path_location.local):
        if(os.path.isdir(full_path) and this_path.name in local_folders):
          logger.debug("synctest::paths_correct() correctly found folder local/%s", this_path.name)
          local_folders.remove(this_path.name)
        elif(this_path.name in local_files):
          if(this_path.type == path_type.link and os.path.islink(full_path)):
            logger.debug("synctest::paths_correct() correctly found link local/%s", this_path.name)
            local_files.remove(this_path.name)
          elif(this_path.type == path_type.file and not os.path.islink(full_path)):
            logger.debug("synctest::paths_correct() correctly found file local/%s", this_path.name)
            local_files.remove(this_path.name)
          else:
            logger.info("synctest::paths_correct() found file instead of link or link instead of file remote/%s", this_path.name)
            return False
        else:
          logger.info("synctest::paths_correct() did not find path local/%s", this_path.name)
          return False

    if(len(remote_folders) > 0):
      paths_string = ", ".join(str(path) for path in remote_folders)
      logger.info("synctest::paths_correct() %d unexpected remote folders left: %s", len(remote_folders), paths_string)
      return False
    if(len(remote_files) > 0):
      paths_string = ", ".join(str(path) for path in remote_files)
      logger.info("synctest::paths_correct() %d unexpected remote files left: %s", len(remote_files), paths_string)
      return False
    if(len(local_folders) > 0):
      paths_string = ", ".join(str(path) for path in local_folders)
      logger.info("synctest::paths_correct() %d unexpected local folders left: %s", len(local_folders), paths_string)
      return False
    if(len(local_files) > 0):
      paths_string = ", ".join(str(path) for path in local_files)
      logger.info("synctest::paths_correct() %d unexpected local files left: %s", len(local_files), paths_string)
      return False
    
    return True
  
  # 
  def run(self):
    logger.debug("synctest::run() test=%s", self.name)
    if(os.path.exists(self.name)):
      shutil.rmtree(self.name)
    os.makedirs(os.path.join(self.name, "remote"))
    os.makedirs(os.path.join(self.name, "local"))
    
    self.process_paths(self.init_paths)
    cmdline = "python lazysync.py -r ./" + self.name + "/remote/ -l ./" + self.name + "/local/ " + self.cmdlineparams
    logger.debug("synctest::run() cmdline='%s'", cmdline)
    proc = subprocess.Popen(cmdline.split(), stdout = subprocess.PIPE, stderr = subprocess.PIPE)
    time.sleep(1)
    self.process_paths(self.sync_paths)
    time.sleep(2)
    proc.terminate()
    out, err = proc.communicate()
    
    if(self.paths_correct()):
      logger.info("synctest::run() test %s SUCCESSFUL", self.name)
      shutil.rmtree(self.name)
    else:
      logger.info("synctest::run() test %s FAILED", self.name)
      for line in out.splitlines():
        logger.info("synctest::run() lazysync stdout: %s", line)
      for line in err.splitlines():
        logger.info("synctest::run() lazysync stderr: %s", line)
  
# main    
if __name__ == "__main__":
  logger.debug("__main__()")
  
  remote_d = synctest_path(path_location.remote, "d", path_type.dir)
  remote_d_del = synctest_path(path_location.remote, "d", path_type.delete)
  remote_f = synctest_path(path_location.remote, "f", path_type.file)
  remote_f_del = synctest_path(path_location.remote, "f", path_type.delete)
  local_d = synctest_path(path_location.local, "d", path_type.dir)
  local_d_del = synctest_path(path_location.local, "d", path_type.delete)
  local_f = synctest_path(path_location.local, "f", path_type.file)
  local_f_del = synctest_path(path_location.local, "f", path_type.delete)
  local_l_to_f = synctest_path(path_location.local, "f", path_type.link)
  
  test_list = []
  
  # lazy mode tests
  test_list.append(synctest("10", "", [], [], [])) # empty
  
  test_list.append(synctest("20", "", [remote_d], [], [remote_d, local_d])) # initialize remote/dir
  test_list.append(synctest("21", "", [local_d], [], [remote_d, local_d])) # initialize local/dir
  test_list.append(synctest("22", "", [], [remote_d], [remote_d, local_d])) # create remote/dir
  test_list.append(synctest("23", "", [], [local_d], [remote_d, local_d])) # create local/dir
  test_list.append(synctest("24", "", [remote_d], [remote_d_del], [])) # delete remote/dir
  test_list.append(synctest("25", "", [remote_d], [local_d_del], [])) # delete local/dir
  test_list.append(synctest("26", "", [local_d], [local_d_del], [])) # delete local/dir
  test_list.append(synctest("27", "", [local_d], [remote_d_del], [])) # delete remote/dir
  
  test_list.append(synctest("30", "", [remote_f], [], [remote_f, local_l_to_f])) # initialize remote/file
  test_list.append(synctest("31", "", [local_f], [], [remote_f, local_f])) # initialize local/file
  test_list.append(synctest("32", "", [], [remote_f], [remote_f, local_l_to_f])) # create remote/file
  test_list.append(synctest("33", "", [], [local_f], [remote_f, local_f])) # create local/file
  test_list.append(synctest("34", "", [remote_f], [remote_f_del], [])) # delete remote/file
  test_list.append(synctest("35", "", [remote_f], [local_f_del], [])) # delete local/file
  test_list.append(synctest("36", "", [local_f], [local_f_del], [])) # delete local/file
  test_list.append(synctest("37", "", [local_f], [remote_f_del], [])) # delete remote/file
  
  # non-lazy mode tests
  
  # dry-run tests
  
  for this_test in test_list:
    this_test.run()
