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

```
python ~/Code/lazysync/lazysync.py -h
usage: lazysync.py [-h] -r RMT -l LCL [-L {y,n}]

Syncs lazily a remote folder and a local folder

optional arguments:
  -h, --help            show this help message and exit
  -r RMT, --remote RMT  Path where the remote data is located
  -l LCL, --local LCL   Path where the local data is located
  -L {y,n}, --lazy {y,n}
                        Sync lazily (on access) or not (always download)

```

## Status

* Syncing works for folders and files.
* Files are not directly deleted, but versioned and kept until manually deleted.
* Sync is automatically paused if paths are not yet mounted on start, or are unmounted during its run.
* Problems:
  * Relative symlinks `local` -> `remote` are not updated. (LazySync creates symlinks with absolute paths.)
  * If `remote` is accessed by anybody or anything else, it will mistakenly be counted as own access and cause a file 
    download.
* To Do:
  * Better logging levels and user adjustable logging.
  * Make sleep time user adjustable.
  * Syncing (user created) symlinks.
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
  
* Box/webdav
  * When syncing files local to `remote`, the `remote` mtime will be what it was synced to based on the 
    `local` file until the webdav is unmounted; on unmount and remount, the `remote` mtime will be the upload time, 
    which will be newer than the `local` mtime, so lazysync will think `remote` was updated and update `local` 
    (`ln`/`cp`).
  * When syncing files while LazySync is running, davfs2 will return two different atimes on upload (either when copying 
    to `remote` folder, or even when uploading though Box' webinterface), so that LazySync will conclude that the 
    `remote` file was accessed (through the symlink) and download it to `local`.

### Data

* User data is stored in `$XDG_DATA_HOME/lazysync/<sync_hash>` based on the 
  [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html) and
  using [python xdg.BaseDirectory](http://pyxdg.readthedocs.io/en/latest/_modules/xdg/BaseDirectory.html).
  `<sync_hash>` is a hash calculated from the two sync paths.
* Deleted files are not directly deleted, but kept in `{remote,local}/.lazysync/<backup_hash>`. `<backup_hash>` is a 
  hash based on the original filename and the deletion date and time.

### Open file notify (ofnotify)

* Scan the list of open files for all processes in regular intervals to detect newly opened and closed files on the 
  local system only without any influences by other accesses to the remote files.
* This is achieved by scanning /proc/<pid>/fd/<fd>. By comparing with the previous scan, files that have been opened or
  closed are detected and events are created accordingly.
* The time interval should correlate with the file size for a given filesystem and network connection. If the time to 
  read a file is longer than the time interval, this file should be detected and open and close events created (a file 
  that is read faster, will only be detected if the scan for open files happens between opening and closing of this 
  file.)
* On the first scan, all already open files will be treated like they were just opened, even if they have been open for
  a long time.
* A minimal example to use the ofnotify.notifier follows:

```
#!/usr/bin/env python

from __future__ import print_function
import ofnotify

class my_event_processor(ofnotify.event_processor):
  def process_event(self, event):
    print("process_event: path='%s' type=%s" % (event.path, event.type))

if __name__ == "__main__":
  n = ofnotify.notifier(my_event_processor(), ['/path/1/', '/path/2/'])
  n.loop()
```
