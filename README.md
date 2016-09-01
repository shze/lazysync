# LazySync

Syncs two folders lazily.

* Sync a 'remote' folder that is mounted in locally but slow to access because it is limited by the network 
  connection, e.g. nfs, sshfs, davfs, and a 'local' folder that is fast to access because it is on a local 
  file system.
* Sync lazily, i.e. only download data that is requested.

## Requirements

* Pyinotify
* Enum34 for testing

## How to run

## Status

* Syncing only works for folders and files, including symlinks, when both remote and local path are on a 
  local filesystem.
  * Symlinks with a target within 'remote' or 'local' should be synced as symlinks with targets within 
    'local' or 'remote'; this also syncs dead symlinks.
  * Empty files are not downloaded in lazy mode, only symlinked.
* Known problems:
  * inotify does not emit events for remote filesystems, which make this approach unusable.
  * Relative symlinks local -> remote are not updated. (LazySync creates symlinks with absolute paths.)
  * More tests are needed. Currently untested: unmounting remote or local while LazySync is running.
* Not implemented:
  * Size limit for downloaded files.
  * Daemonization, definition of API for controling the daemin, implementation of a client.

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
