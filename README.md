# LazySync

Syncs two folders lazily.

* Sync a 'remote' folder that is mounted in locally but slow to access because it is limited by the network 
  connection, e.g. nfs, sshfs, davfs, and a 'local' folder that is fast to access because it is on a local 
  file system.
* Sync lazily, i.e. only download data that is requested.

## Requirements

* enum34
* xdg
* jsonpickle

## How to run

## Status

* Syncing works for folders and files.
* Problems:
  * Relative symlinks local -> remote are not updated. (LazySync creates symlinks with absolute paths.)
* To Do:
  * Syncing (user created) symlinks.
  * Non-lazy syncing.
  * Dry-run mode.
  * Size limit for downloaded files.
  * Daemonization, definition of API for controling the daemin, implementation of a client.
  * Tests.

### Syncing lazily

* Syncing lazily works by creating symlinks in the 'local' folder that point to the corresponding file in 
  the 'remote' folder. This avoids downloading files whoses content is not needed (yet).
* If a file is read, it is downloaded, the symlink is replaced with a copy of the file contents. That a file is read is
  determined by an updated atime of the remote file. (This means reading a file can be faked by 'touch'ing the file.)
* The local copy of the file is kept until a change to the 'remote' file occurs, at which point the local copy is 
  replaced by a symlink.

## Technical Information

* Inotify does not emit events for remote filesystems, which make the inotify approach unusable.
* This leaves scanning the filesystem as the only option.
* Based on the differences of a filesystem scan compared to the tracking information stored from the last scan, the 
  following actions are implemented:
  
  1. Accessing (in both sync paths) existing files
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
