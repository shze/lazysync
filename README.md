# LazySync

Syncs two folders lazily.

* Sync a `remote` folder that is mounted in locally but slow to access because it is limited by the network 
  connection, e.g. nfs, sshfs, davfs, and a `local` folder that is fast to access because it is on a local 
  file system.
* Sync lazily, i.e. only download data that is requested.

## Requirements

* enum34
* xdg
* jsonpickle

## How to run

```
python lazysync.py -r /remote/ -l /local/
```

## Status

* Syncing works for folders and files.
* Files are not directly deleted, but versioned and kept until manually deleted.
* Sync is automatically paused if paths are not yet mounted on start, or are unmounted during its run.
* Problems:
  * Relative symlinks `local` -> `remote` are not updated. (LazySync creates symlinks with absolute paths.)
* To Do:
  * Better logging levels and user adjustable logging.
  * Make sleep time user adjustable.
  * Syncing (user created) symlinks.
  * Non-lazy syncing.
  * Dry-run mode.
  * RSync-based copy and rate-limiting speed of copying.
  * Size limit for downloaded files.
  * Daemonization, definition of API for controling the daemon, implementation of a client.
  * Tests.

## Technical Information

### Syncing lazily

* Syncing lazily works by creating symlinks in the `local` folder that point to the corresponding file in 
  the `remote` folder. This avoids downloading files whose content is not needed (yet).
* If a file is read, it is downloaded, the symlink is replaced with a copy of the file contents. That a file is read is
  determined by an updated atime of the remote file. (This means reading a file can be faked by `touch`ing the file.)
* The local copy of the file is kept until a change to the `remote` file occurs, at which point the `local` copy is 
  replaced by a symlink.

### Syncing

* Inotify does [not emit events for remote filesystems](http://unix.stackexchange.com/questions/238956/), which make the 
  inotify approach unusable (no create/modify/delete events, but access events are emitted); this leaves scanning the 
  filesystem as the only option.
* Based on the differences of a filesystem scan compared to the tracking information stored from the last scan, the 
  following actions are implemented:
  
  * Change to files (dirs)
    location\event     create              mtime                 atime                   delete
    * `remote`         symlink (mkdir)     symlink (ignored)     download (ignored)      remove (rmdir)
    * `local`          upload (mkdir)      upload  (ignored)     ignored (ignored)       remove (rmdir)
  * Creation and deletion of files and dirs is assumed based on the previous tracking information
    * If path existed before (last scan): assume path was deleted
    * If path did not exist before: assume new file
  
* Box/webdav: When syncing files local to `remote`, the `remote` mtime will be what it was synced to based on the 
  `local` file until the webdav is unmounted; on unmount and remount, the `remote` mtime will be the upload time, which 
  will be newer than the `local` mtime, so lazysync will think `remote` was updated and update `local` (`ln`/`cp`).

### Data

* User data is stored in `$XDG_DATA_HOME/lazysync/<sync_hash>` based on the 
  [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html) and
  using [python xdg.BaseDirectory](http://pyxdg.readthedocs.io/en/latest/_modules/xdg/BaseDirectory.html).
  `<sync_hash>` is a hash calculated from the two sync paths.
* Deleted files are not directly deleted, but kept in `{remote,local}/.lazysync/<backup_hash>`. `<backup_hash>` is a 
  hash based on the original filename and the deletion date and time.