#!/usr/bin/env python

from enum import Enum
import subprocess, os, shutil, time, logging, sys, errno

# set up logging
logger = logging.getLogger(os.path.basename(sys.argv[0]))
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(console_handler)

# http://stackoverflow.com/questions/273192
def make_sure_dir_exists(dir):
  try:
    os.makedirs(dir)
  except OSError as exception:
    if exception.errno != errno.EEXIST:
      raise
          
# enums for testing
path_type = Enum('path_type', 'file dir link delete')
path_location = Enum('path_location', 'remote local')

#
class synctest_path:
  #
  def __init__(self, location, name, type, size = None, link_src = None):
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
    full_path = os.path.join(self.location, self.name)
    if(path.type == path_type.file):
      blocksize = min(path.size, 1000000) # ~1MB
      count = path.size / blocksize; # writes exact size for <1MB file size, multiples of 1MB for larger files
      cmdline = "dd if=/dev/zero of=" + full_path + " bs=" + blocksize + " count=" + count
      logger.debug("synctest::process_path() cmdline='%s'", cmdline)
      subprocess.call(cmdline.split())
    elif(path.type == path_type.dir):
      os.path.makedirs(full_path)
    elif(path.type == path_type.link):
      os.symlink(self.link_src, full_path)
    elif(path.type == path_type.delete):
      if(os.path.lexists(full_path)):
        if(os.path.isdir(full_path)):
          os.removedirs(full_path)
        else:
          shutil.rmtree(full_path)
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
    # TODO
    return True
  
  # 
  def run(self):
    logger.debug("synctest::run()")
    make_sure_dir_exists(os.path.join(self.name, "remote"))
    make_sure_dir_exists(os.path.join(self.name, "local"))
    
    self.process_paths(self.init_paths)
    cmdline = "python lazysync.py -r ./remote/ -l ./local/ " + self.cmdlineparams
    logger.debug("synctest::run() cmdline='%s'", cmdline)
    proc = subprocess.Popen(cmdline.split())
    time.sleep(.5)
    self.process_paths(self.sync_paths)
    time.sleep(.5)
    proc.terminate()
    result = self.paths_correct()
    shutil.rmtree(self.name)
    
    return result
  
# main    
if __name__ == "__main__":
  logger.debug("__main__()")
  test1 = synctest("test1", "", [], [], [])
  test1.run()
  # TODO
