# LazySync

Syncs two folders lazily.

* Sync a `remote` folder that is mounted in locally but slow to access because it is limited by the network 
  connection, e.g. nfs, sshfs, davfs, and a `local` folder that is fast to access because it is on a local 
  file system.
* Sync lazily, i.e. only download data that is requested.

## Requirements

* enum34
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
  -r RM, --remote RM    Path where the remote data is located
  -l LC, --local LC     Path where the local data is located
  -L {y,n}, --lazy {y,n}
                        Sync lazily (on access) or not (always download)

```

## Status

* Syncing works for folders, files and symlinks.
* Files are not directly deleted, but versioned and kept until manually deleted. 
* Sync is automatically paused if paths are not yet mounted on start, or are unmounted during its run.
* Problems:
  * Relative symlinks `local` -> `remote` are not updated. (LazySync creates symlinks with absolute paths.)
  * A file access can be missed and the file won't be downloaded if the access/open time is too short to be detected.
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
  determined by checking the Linux kernel's list of open files.
* This assumes that the remote file is cached locally once it is downloaded and not downloaded again. This is true for 
  davfs2.
* A file that is opened and read can be missed if the open time is too short to be detected. If the file access is 
  missed, the file won't be downloaded.
* The local copy of the file is kept until a change to the `remote` file occurs, at which point the `local` copy is 
  replaced by a symlink.

### Syncing

* Inotify does [not emit events for remote filesystems](http://unix.stackexchange.com/questions/238956/), which make the 
  inotify approach unusable (no create/modify/delete events, but access events are emitted); this leaves scanning the 
  filesystem as the only option.
* Based on the differences of a filesystem scan compared to the tracking information stored from the last scan, the 
  following actions are implemented:
  
  * Change to files (dirs)
    location\event     create              mtime                                   atime                 delete
    * `remote`         symlink (mkdir)     lazy:symlink/non:download (ignored)     ignored (ignored)     remove (rmdir)
    * `local`          upload (mkdir)      upload  (ignored)                       ignored (ignored)     remove (rmdir)
  * Creation and deletion of files and dirs is assumed based on the previous tracking information
    * If path existed before (last scan): assume path was deleted
    * If path did not exist before: assume new file
  * In lazy mode, a symlink is replaced with a local copy (download), if the file was accessed (detected as open with 
    ofnotify).
  
* User symlinks are synced.
  * A user symlink is any symlink that is not a symlink `local` -> `remote`.
  * If a symlink's target is outside `remote` or `local`, they will appear as dead.
  
* Box/webdav
  * When syncing files local to `remote`, the `remote` mtime will be what it was synced to based on the 
    `local` file until the webdav is unmounted; on unmount and remount, the `remote` mtime will be the upload time, 
    which will be newer than the `local` mtime, so lazysync will think `remote` was updated and update `local` 
    (`ln`/`cp`).
  * When syncing files while LazySync is running, davfs2 will return two different atimes on upload (either when copying 
    to `remote` folder, or even when uploading though Box' webinterface), so that LazySync will conclude that the 
    `remote` file was accessed (through the symlink) and download it to `local`.

### Data

* Deleted files are not directly deleted, but kept in `{remote,local}/.lazysync/<backup_hash>`. `<backup_hash>` is a 
  hash based on the original filename and the deletion date and time. Information how each <backup_hash> relates back to
  the original filename is stored in `{remote,local}/.lazysync/data`

### Open file notify (ofnotify)

* Scan the list of open files for all processes in regular intervals to detect newly opened and closed files on the 
  local system only without any influences by other accesses to the remote files.
* This is achieved by scanning [/proc/<pid>/fd/<fd>](https://www.kernel.org/doc/Documentation/filesystems/proc.txt) by 
  using [psutil](https://pypi.python.org/pypi/psutil). By comparing with the previous scan, files that have been opened 
  or closed are detected and events are created accordingly.
* The time interval should correlate with the file size for a given filesystem and network connection. If the time to 
  read a file is longer than the time interval, this file should be detected and open and close events created (a file 
  that is read faster, will only be detected if the scan for open files happens between opening and closing of this 
  file.)
* On the first scan, all already open files will be treated like they were just opened, even if they have been open for
  a long time.

#### Example for ofnotify.notifier

```python
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

#### Example for ofnotify.threaded_notifier

```python
#!/usr/bin/env python

from __future__ import print_function
import ofnotify, time

class my_event_processor(ofnotify.event_processor):
  def process_event(self, event):
    print("process_event: path='%s' type=%s" % (event.path, event.type))

# main    
if __name__ == "__main__":
  n = ofnotify.threaded_notifier(my_event_processor(), ['/path/1/', '/path/2/'])
  n.start()
  
  while 1:
    try:
      time.sleep(2)
    except KeyboardInterrupt:
      n.stop()
      break
    except:
      n.stop()
      raise
```

### Code Formatting

[PEP 8](https://www.python.org/dev/peps/pep-0008/) is used. `autopep8 -a --ignore=E301 -` on Debian 8 to avoid 
[blank lines before a class docstring](https://github.com/hhatto/autopep8/issues/194).
