# LazySync

Syncs two folders lazily.

* Sync a 'remote' folder that is mounted in locally but slow to access because it is limited by the network 
  connection, e.g. nfs, sshfs, davfs, and a 'local' folder that is fast to access because it is on a local 
  file system.
* Sync lazily, i.e. only download data that is requested.

## Requirements

* Python
* Pyinotify

## How to run

## Status

* Syncing should work for folders and files, including symlinks.
  * Symlinks with a target within 'remote' or 'local' should be synced as symlinks with targets within 
    'local' or 'remote'; this also syncs dead symlinks.
* Known problems:
  * Downloading does not work and is disabled for now; only symlinks are created.

## To Do

## Technical Information

### Syncing lazily

* Syncing lazily works by creating symlinks in the 'local' folder that point to the corresponding file in 
  the 'remote' folder. This avoids downloading files whoses content is not needed (yet).
* Symlinks that have a target are treated as special case and a remote/file <- remote/link will be synced 
  as remote/file <- local/file <- local/link and not as remote/file <- local/file and 
  remote/link <- local/link.
* If a file is read in the 'local' folder and therefore downloaded, the symlink is replaced with a copy of 
  the file contents until it is changed in the 'remote' folder.

### Design

* Use inotify (pyinotify) instead of scanning directory hierarchies every time. Based on events, the 
  following actions are implemented:
  
  1. Accessing existing files
     * remote: do nothing
     * local: download
  2. Change files (dirs)
     location\event     create              modify content     delete
     * remote           symlink (mkdir)     symlink            remove (rmdir)
     * local            upload (mkdir)      upload             remove (rmdir)

* Channel all inotify events through one queue and process them sequentially.
  * The deque from collections implements atomic append() and popleft() that can be used parallelized and 
    do not require locking.
  * Processing one event can cause other events, and processing them in parallel can have catastrophic 
    consequences. E.g. a modification of a remote file can mean that the local copy is now outdated as needs 
    to be replaced by a symlink, which creates a delete and a create event. If the delete event is 
    processed before the original event is completed, it can delete both the remote and local copies. 
    Therefore events must be processed sequentially.
