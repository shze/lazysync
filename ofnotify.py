#!/usr/bin/env python

from collections import deque # implements atomic append() and popleft() that do not require locking
import psutil, time, enum # enum is enum34

#
event_types = enum.Enum('event_types', 'open close')

# 
class event:
  #
  def __init__(self, path, event_type):
    self.path = path
    self.type = event_type

#
class event_processor:
  #
  def process_event(self, event):
    pass

#
class notifier:
  #
  def __init__(self, event_processor, watch_paths, sleep_time = 0.7):
    self.event_processor = event_processor
    self.watch_paths = watch_paths
    self.sleep_time = sleep_time
    self.queue = deque()
    self.tracked_files = set()

  #
  def _find_changes(self):
    # find all currently open files and create open events for files that were not open before
    new_tracked_files = set()
    for pid in psutil.pids():
      try:
        current_pid = psutil.Process(pid) # inside try-except to catch psutil.NoSuchProcess
        for open_file in current_pid.open_files(): # catch psutil.AccessDenied
          watched = False # only track file in watched paths
          for watch_path in self.watch_paths:
            if open_file.path.startswith(watch_path):
              watched = True
              break
            
          if watched:
            new_tracked_files.add(open_file)
      except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
      except:
        raise
      
    # create open events for newly opened files
    opened_files = new_tracked_files - self.tracked_files
    for opened_file in opened_files:
      self.queue.append(event(open_file.path, event_types.open))
      
    # create close events for closed files
    closed_files = self.tracked_files - new_tracked_files
    for closed_file in closed_files:
      self.queue.append(event(closed_file.path, event_types.close))
    
    self.tracked_files = new_tracked_files # update
  
  #
  def loop(self):
    while 1:
      try:
        self._find_changes()
        while self.queue:
          self.event_processor.process_event(self.queue.popleft())
        time.sleep(self.sleep_time)
      except KeyboardInterrupt:
        break
  
#
class threaded_notifier:
  #
  def __init__(self):
    pass
