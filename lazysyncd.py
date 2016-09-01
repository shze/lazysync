#!/usr/bin/env python

from __future__ import print_function
import logging, argparse, os, sys

# set up logging
logger = logging.getLogger(os.path.basename(sys.argv[0]))
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(console_handler)

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

#
class syncfile:
  #
  def __init__(self, path, remote_atime, remote_mtime, local_atime, local_mtime):
    logger.debug("syncfile::__init__()")
    self.path = path
    self.remote_atime = remote_atime
    self.remote_mtime = remote_mtime
    self.local_atime = local_atime
    self.local_mtime = local_mtime

  
# main    
if __name__ == "__main__":
  logger.debug("__main__()")
  config = merge_two_dicts(get_config(), parse_command_line()) # cmd line second to overwrite default settings in config

  remote_folder_set, remote_file_set = relative_walk(config['remote']) 
  local_folder_set, local_file_set = relative_walk(config['local'])
  
  folders_both = remote_folder_set & local_folder_set # set of folders in both sets: assume they're identical
  folders_remote_only = remote_folder_set - local_folder_set # folders only in remote_folder_set: need to be copied
  folders_local_only = local_folder_set - remote_folder_set # folders only in local_folder_set: need to be copied
  files_both = remote_file_set & local_file_set # set of files in both sets: need to check details (size, etc.)
  files_remote_only = remote_file_set - local_file_set # files only in remote_file_set: need to be copied/symlinked
  files_local_only = local_file_set - remote_file_set # files only in local_file_set: need to be copied
  
  syncfile_list = []
  
  for path in folders_both:
    statinfo_remote = os.lstat(os.path.join(config['remote'], path))
    statinfo_local = os.lstat(os.path.join(config['local'], path))
    syncfile_list.append(syncfile(path, statinfo_remote.atime, statinfo_remote.mtime, statinfo_local.atime, statinfo_local.mtime))
    
  for this_syncfile in syncfile_list:
    logger.debug("__main__ path='%s' r_atime=%d r_mtime=%d", this_syncfile.path, this_syncfile.remote_atime, this_syncfile.remote_mtime)
