"""Socket transport for the TS4 bridge.

Runs entirely on a daemon thread and touches only stdlib objects. It never calls
into game code. Requests are placed on ``inbox``; the game-thread pump
(``pump.py``) drains them and puts responses/events on ``outbox``.

Python 3.7 compatible.
"""
import json
import os
import queue
import select
import socket
import struct
import threading
import time

PROTOCOL_VERSION = 1
MAX_FRAME = 16 * 1024 * 1024
HEADER = struct.Struct('>I')


class Connection(object):
    """One authenticated-or-pending client socket."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.rbuf = b''
        self.wbuf = b''
        self.authed = False
        self.client_name = '?'
        self.connected_at = time.time()
        self.hello_pending_since = None
        sock.setblocking(False)

    def feed(self, data):
        self.rbuf += data

    def frames(self):
        """Yield complete decoded JSON frames from the read buffer."""
        while True:
            if len(self.rbuf) < HEADER.size:
                return
            (length,) = HEADER.unpack_from(self.rbuf, 0)
            if length > MAX_FRAME:
                raise ValueError('frame too large: %d' % length)
            end = HEADER.size + length
            if len(self.rbuf) < end:
                return
            payload = self.rbuf[HEADER.size:end]
            self.rbuf = self.rbuf[end:]
            yield json.loads(payload.decode('utf-8'))

    def queue_send(self, obj):
        payload = json.dumps(obj, default=_json_default).encode('utf-8')
        self.wbuf += HEADER.pack(len(payload)) + payload

    def flush(self):
        """Returns False when the socket is dead and the connection should be dropped."""
        if not self.wbuf:
            return True
        try:
            sent = self.sock.send(self.wbuf)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            return False
        self.wbuf = self.wbuf[sent:]
        return True

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _json_default(o):
    # Last-resort serialisation for game objects that leak into results.
    try:
        return repr(o)
    except Exception:
        return '<unserialisable>'


class Transport(object):
    """Listening TCP server on a background thread.

    ``inbox``  : queue of (conn_id, request_dict) for the game thread.
    ``outbox`` : queue of (conn_id or None, message_dict); None = broadcast.
    ``hello_provider`` : callable() -> dict, evaluated on the *game* thread via
    the pump (see ``pending_hellos``), because it reads game state.
    """

    HELLO_STALL_SECONDS = 8.0

    def __init__(self, token, host='127.0.0.1', port=0, log=None, health=None):
        self.token = token
        self.host = host
        self.port = port
        self.log = log or (lambda *a, **k: None)
        # callable() -> dict describing the game-thread pump (ticks, last tick time); read-only use
        self.health = health or (lambda: {})
        self.inbox = queue.Queue()
        self.outbox = queue.Queue()
        self.pending_hellos = queue.Queue()  # conn ids awaiting a hello payload
        self._conns = {}
        self._next_conn_id = 1
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._listener = None
        self.stats = {'accepted': 0, 'frames_in': 0, 'frames_out': 0, 'errors': 0}

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((self.host, self.port))
        self._listener.listen(4)
        self._listener.setblocking(False)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._run, name='ts4_bridge.transport')
        self._thread.daemon = True
        self._thread.start()
        self.log('transport listening on %s:%d' % (self.host, self.port))

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            for c in list(self._conns.values()):
                c.close()
            self._conns.clear()
        if self._listener is not None:
            self._listener.close()

    def connection_count(self):
        with self._lock:
            return len([c for c in self._conns.values() if c.authed])

    # ---- called by the game thread --------------------------------------
    def send(self, conn_id, obj):
        self.outbox.put((conn_id, obj))

    def broadcast(self, obj):
        self.outbox.put((None, obj))

    # ---- thread body -----------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # never let the thread die
                self.stats['errors'] += 1
                self.log('transport error: %r' % (e,))
                time.sleep(0.05)

    def _tick(self):
        self._drain_outbox()
        with self._lock:
            conns = list(self._conns.items())
        rlist = [self._listener] + [c.sock for _, c in conns]
        wlist = [c.sock for _, c in conns if c.wbuf]
        try:
            readable, writable, _ = select.select(rlist, wlist, [], 0.05)
        except (OSError, ValueError):
            self._prune_dead()
            return
        for s in readable:
            if s is self._listener:
                self._accept()
            else:
                self._read(s)
        for s in writable:
            for cid, c in conns:
                if c.sock is s:
                    if not c.flush():
                        self._drop(cid, 'send failed')
        self._check_stalled_hellos(conns)

    def _check_stalled_hellos(self, conns):
        """If the game thread never answers an auth (pump dead or game thread stalled), tell the
        client why instead of leaving it hanging."""
        now = time.time()
        for cid, c in conns:
            since = getattr(c, 'hello_pending_since', None)
            if since is None or now - since < self.HELLO_STALL_SECONDS:
                continue
            c.hello_pending_since = None
            try:
                health = self.health()
            except Exception:
                health = {}
            c.queue_send({'type': 'hello', 'ok': False,
                          'error': 'game thread is not servicing the bridge (pump stalled)',
                          'health': health})
            c.flush()
            self.log('hello stalled for client #%d; health=%r' % (cid, health))
            self._drop(cid, 'pump stalled')

    def _accept(self):
        try:
            sock, addr = self._listener.accept()
        except (BlockingIOError, InterruptedError):
            return
        if addr[0] != '127.0.0.1':
            sock.close()
            return
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn = Connection(sock, addr)
        with self._lock:
            cid = self._next_conn_id
            self._next_conn_id += 1
            self._conns[cid] = conn
        self.stats['accepted'] += 1
        self.log('client connected #%d from %s:%d' % (cid, addr[0], addr[1]))

    def _conn_for_sock(self, s):
        with self._lock:
            for cid, c in self._conns.items():
                if c.sock is s:
                    return cid, c
        return None, None

    def _read(self, s):
        cid, conn = self._conn_for_sock(s)
        if conn is None:
            return
        try:
            data = s.recv(65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b''
        if not data:
            self._drop(cid, 'closed by peer')
            return
        conn.feed(data)
        try:
            for frame in conn.frames():
                self.stats['frames_in'] += 1
                self._handle_frame(cid, conn, frame)
        except (ValueError, UnicodeDecodeError) as e:
            self._drop(cid, 'bad frame: %r' % (e,))

    def _handle_frame(self, cid, conn, frame):
        ftype = frame.get('type')
        if not conn.authed:
            if ftype == 'auth' and _const_eq(frame.get('token', ''), self.token):
                conn.authed = True
                conn.client_name = str(frame.get('client', '?'))[:64]
                conn.hello_pending_since = time.time()
                self.pending_hellos.put(cid)
                self.log('client #%d authenticated (%s)' % (cid, conn.client_name))
            else:
                conn.queue_send({'type': 'hello', 'ok': False, 'error': 'bad token'})
                conn.flush()
                self._drop(cid, 'auth failed')
            return
        if ftype == 'req':
            if not isinstance(frame.get('id'), str) or not isinstance(frame.get('op'), str):
                conn.queue_send({'type': 'res', 'id': frame.get('id'), 'ok': False,
                                 'error': {'type': 'ProtocolError', 'message': 'req needs string id and op'}})
                return
            self.inbox.put((cid, frame))
        elif ftype == 'ping':
            conn.queue_send({'type': 'pong', 'ts': time.time()})
        # unknown types are ignored

    def _drain_outbox(self):
        while True:
            try:
                cid, obj = self.outbox.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                if cid is None:
                    # broadcasts only after the hello went out, so the handshake stays first
                    targets = [c for c in self._conns.values() if c.authed and c.hello_pending_since is None]
                else:
                    c = self._conns.get(cid)
                    targets = [c] if c is not None else []
            for c in targets:
                if obj.get('type') == 'hello':
                    c.hello_pending_since = None
                try:
                    c.queue_send(obj)
                    self.stats['frames_out'] += 1
                except Exception as e:
                    self.log('serialise error: %r' % (e,))
                    c.queue_send({'type': obj.get('type', 'res'), 'id': obj.get('id'), 'ok': False,
                                  'error': {'type': 'SerializeError', 'message': repr(e)}})
                c.flush()

    def _drop(self, cid, why):
        with self._lock:
            c = self._conns.pop(cid, None)
        if c is not None:
            c.close()
            self.log('client #%d dropped: %s' % (cid, why))

    def _prune_dead(self):
        with self._lock:
            dead = [cid for cid, c in self._conns.items() if c.sock.fileno() < 0]
        for cid in dead:
            self._drop(cid, 'dead socket')


def _const_eq(a, b):
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a.encode('utf-8'), b.encode('utf-8')):
        r |= x ^ y
    return r == 0


def make_token():
    return os.urandom(16).hex()
