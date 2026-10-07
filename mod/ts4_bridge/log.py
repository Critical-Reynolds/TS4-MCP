"""Tiny thread-safe file logger (the game's own logging is awkward to read). Python 3.7."""
import os
import threading
import time

from ts4_bridge import paths

_lock = threading.Lock()
_MAX_BYTES = 2 * 1024 * 1024


def log(msg):
    line = '%s [%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), threading.current_thread().name, msg)
    with _lock:
        try:
            paths.ensure_dirs()
            try:
                if os.path.getsize(paths.LOG_FILE) > _MAX_BYTES:
                    os.replace(paths.LOG_FILE, paths.LOG_FILE + '.1')
            except OSError:
                pass
            with open(paths.LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line)
        except Exception:
            pass
