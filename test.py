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
  def __init__(self, location, name, type, size = None, link_dst = None):
    self.location = location
    self.name = name
    self.type = type
    self.size = size
    self.link_dst = link_dst
   
#
class synctest:
  #
  def __init__(self, name, cmdlineparams, init_paths, sync_paths, result_paths):
    logger.debug("synctest::__init__()")
    make_sure_dir_exists(os.path.join(name, "remote"))
    make_sure_dir_exists(os.path.join(name, "local"))
    
    self.process_paths(init_paths)
    cmdline = "python lazysync.py -r ./remote/ -l ./local/ " + cmdlineparams
    logger.debug("synctest::__init__() cmdline='%s'", cmdline)
    proc = subprocess.Popen(cmdline.split())
    time.sleep(.5)
    self.process_paths(sync_paths)
    time.sleep(.5)
    proc.terminate()
    
    # TODO cmp with result_paths
    
    shutil.rmtree(name)
  
  #
  def process_paths(self, paths):
    pass
  
# main    
if __name__ == "__main__":
  logger.debug("__main__()")
  test1 = synctest("test1", "", [], [], [])
