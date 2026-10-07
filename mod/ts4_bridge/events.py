"""Event ring buffer. ``emit`` may be called from the game thread only. Python 3.7."""
import collections
import threading
import time

_RING = collections.deque(maxlen=2000)
_seq = 0
_lock = threading.Lock()
_sink = None  # callable(event_dict) installed by the pump to push to clients


def install_sink(fn):
    global _sink
    _sink = fn


def emit(name, data=None, sim_ts=None):
    global _seq
    with _lock:
        _seq += 1
        ev = {'type': 'event', 'seq': _seq, 'ts': time.time(), 'sim_ts': sim_ts,
              'name': name, 'data': data if data is not None else {}}
        _RING.append(ev)
    if _sink is not None:
        try:
            _sink(ev)
        except Exception:
            pass
    return ev['seq']


def latest_seq():
    return _seq


def poll(since=0, limit=200):
    with _lock:
        items = [e for e in _RING if e['seq'] > since]
    if limit and len(items) > limit:
        items = items[-limit:]
    return {'seq': _seq, 'events': items, 'dropped_before': (_RING[0]['seq'] - 1) if _RING else 0}
