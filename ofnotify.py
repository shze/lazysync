#!/usr/bin/env python

from collections import deque # implements atomic append() and popleft() that do not require locking
import psutil, time, threading, enum # enum is enum34

#
event_types = enum.Enum('event_types', 'open close')
default_sleep_time = 0.7

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
  def __init__(self, event_processor, watch_paths, sleep_time = default_sleep_time):
    self.event_processor = event_processor
    self.watch_paths = watch_paths
    self.sleep_time = sleep_time
    self.queue = deque()
    self.tracked_files = set()

  #
  def _find_changes(self):
    # find all currently open files
    new_tracked_files = set()
    for pid in psutil.pids():
      try:
        current_pid = psutil.Process(pid) # inside try-except to catch psutil.NoSuchProcess
        for open_file in current_pid.open_files(): # catch psutil.AccessDenied
          for watch_path in self.watch_paths:
            if open_file.path.startswith(watch_path):
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
class threaded_notifier(threading.Thread, notifier):
  #
  def __init__(self, event_processor, watch_paths, sleep_time = default_sleep_time):
    threading.Thread.__init__(self) # initialize threading base class
    self._stop_event = threading.Event() # stop condition
    notifier.__init__(self, event_processor, watch_paths, sleep_time) # initialize notifier base class
  
  #
  def loop(self):
    while not self._stop_event.is_set():
      self._find_changes()
      while self.queue:
        self.event_processor.process_event(self.queue.popleft())
      time.sleep(self.sleep_time)
  
  #
  def stop(self):
    self._stop_event.set()
    threading.Thread.join(self)
  
  #
  def run(self):
    self.loop()
