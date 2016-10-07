#!/usr/bin/env python

import daemon
import time


class lazysync:
    """Lazily syncs two folders with the given config parameters."""

    def __init__(self, config):
        pass

    def loop(self):
        while True:
            with open("/tmp/current_time.txt", "a") as f:
                f.write("The time is now " + time.ctime() + "\n")
            time.sleep(5)

if __name__ == "__main__":
    with daemon.DaemonContext():
        sync = lazysync([])
        sync.loop()
