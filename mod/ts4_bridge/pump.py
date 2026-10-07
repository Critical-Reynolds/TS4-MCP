"""Game-thread pump: drains transport queues inside the server tick. Python 3.7.

Installed by wrapping ``game_services.on_tick`` which ``areaserver.c_api_server_tick``
calls on every server tick (including main menu and loading screens).
"""
import queue
import time
import traceback
import uuid

from ts4_bridge import dispatch, events
from ts4_bridge.log import log
from ts4_bridge.util.jsonsafe import jsonsafe

MAX_REQUESTS_PER_TICK = 16
TICK_BUDGET_SECONDS = 0.020


class Job(object):
    __slots__ = ('id', 'op', 'status', 'result', 'error', 'created', 'finished')

    def __init__(self, op):
        self.id = uuid.uuid4().hex[:12]
        self.op = op
        self.status = 'running'
        self.result = None
        self.error = None
        self.created = time.time()
        self.finished = None

    def describe(self):
        return {'job_id': self.id, 'op': self.op, 'status': self.status, 'result': self.result,
                'error': self.error, 'created': self.created, 'finished': self.finished}


class Pump(object):
    def __init__(self, transport, hello_provider):
        self.transport = transport
        self.hello_provider = hello_provider
        self.ticks = 0
        self.installed = False
        self.jobs = {}
        self.stats = {'requests': 0, 'errors': 0, 'slow_ops': 0, 'max_op_ms': 0.0}
        events.install_sink(self._push_event)

    # ---- events / jobs ---------------------------------------------------
    def _push_event(self, ev):
        self.transport.broadcast(ev)

    def new_job(self, op):
        job = Job(op)
        self.jobs[job.id] = job
        if len(self.jobs) > 500:
            for jid in sorted(self.jobs, key=lambda j: self.jobs[j].created)[:100]:
                self.jobs.pop(jid, None)
        return job

    def finish_job(self, job, result=None, error=None):
        job.status = 'done' if error is None else 'failed'
        job.result = jsonsafe(result)
        job.error = error
        job.finished = time.time()
        events.emit('job.done', job.describe())

    # ---- tick ------------------------------------------------------------
    def on_tick(self):
        self.ticks += 1
        self.last_tick_time = time.time()
        for stage in (self._maybe_late_init, self._drain_hellos, self._drain_requests, self._background_jobs):
            try:
                stage()
            except Exception:
                self.stats['errors'] += 1
                log('pump stage %s error:\n%s' % (getattr(stage, '__name__', stage), traceback.format_exc()))

    def _maybe_late_init(self):
        if self.ticks % 60 == 0:
            self._late_init()

    def health(self):
        return {'ticks': self.ticks, 'last_tick_time': getattr(self, 'last_tick_time', None),
                'age_s': round(time.time() - getattr(self, 'last_tick_time', time.time()), 2),
                'stats': dict(self.stats), 'installed': self.installed}

    def _late_init(self):
        """Things that need game services which do not exist at mod-load time."""
        try:
            from ts4_bridge import hooks
            hooks.ensure_installed_on_tick()
        except Exception:
            log('late init failed:\n' + traceback.format_exc())

    def _background_jobs(self):
        try:
            from ts4_bridge import hooks
            hooks.on_tick()
        except Exception:
            log('background job failed:\n' + traceback.format_exc())

    def _drain_hellos(self):
        while True:
            try:
                cid = self.transport.pending_hellos.get_nowait()
            except queue.Empty:
                return
            try:
                hello = self.hello_provider()
                hello.update({'type': 'hello', 'ok': True})
            except Exception as e:
                hello = {'type': 'hello', 'ok': True, 'warning': 'hello provider failed: %r' % (e,)}
            self.transport.send(cid, hello)

    def _drain_requests(self):
        start = time.perf_counter()
        handled = 0
        while handled < MAX_REQUESTS_PER_TICK:
            try:
                cid, req = self.transport.inbox.get_nowait()
            except queue.Empty:
                return
            handled += 1
            self._handle(cid, req)
            if time.perf_counter() - start > TICK_BUDGET_SECONDS:
                return

    def _handle(self, cid, req):
        self.stats['requests'] += 1
        op = req.get('op')
        args = req.get('args') or {}
        t0 = time.perf_counter()
        ok, payload = dispatch.call(op, args)
        ms = (time.perf_counter() - t0) * 1000.0
        if ms > self.stats['max_op_ms']:
            self.stats['max_op_ms'] = ms
        if ms > 250:
            self.stats['slow_ops'] += 1
            log('slow op %s took %.0f ms' % (op, ms))
        res = {'type': 'res', 'id': req.get('id'), 'ok': ok, 'ms': round(ms, 2)}
        if ok:
            try:
                res['result'] = jsonsafe(payload)
            except Exception as e:
                res['ok'] = False
                res['error'] = {'type': 'SerializeError', 'message': repr(e)}
        else:
            self.stats['errors'] += 1
            res['error'] = jsonsafe(payload)
            if payload.get('type') not in ('OpError', 'UnknownOp'):
                log('op %s failed: %s' % (op, payload.get('message')))
        self.transport.send(cid, res)


_pump = None


def install(pump):
    """Wrap game_services.on_tick so the pump runs every server tick."""
    global _pump
    _pump = pump
    import game_services
    original = game_services.on_tick
    if getattr(original, '_ts4_bridge_wrapped', False):
        # re-install after reload: swap the pump reference only
        pump.installed = True
        return

    def on_tick_wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        p = _pump
        if p is not None:
            p.on_tick()
        return result

    on_tick_wrapper._ts4_bridge_wrapped = True
    on_tick_wrapper._ts4_bridge_original = original
    game_services.on_tick = on_tick_wrapper
    pump.installed = True
    log('pump installed on game_services.on_tick')


def current():
    return _pump
