"""Core ops: ping/info/exec/cheat/reload/events/jobs. Python 3.7."""
import ast
import contextlib
import importlib
import io
import sys
import time
import traceback

from ts4_bridge import dispatch, events, paths
from ts4_bridge.dispatch import op, OpError
from ts4_bridge.log import log
from ts4_bridge.util import game as g
from ts4_bridge.util.jsonsafe import jsonsafe

_START = time.time()

# Persistent namespace for bridge.exec so the caller can define helpers once.
EXEC_GLOBALS = {'__name__': '__ts4_bridge_exec__'}


def _prime_exec_globals():
    if EXEC_GLOBALS.get('_primed'):
        return
    code = (
        'import services, sims4, sims4.commands, sims4.resources, sims4.math\n'
        'from sims4.resources import Types\n'
        'import build_buy, objects, interactions, alarms, clock\n'
        'from objects.system import create_object\n'
        'from ts4_bridge.util import game as g\n'
        'from ts4_bridge.util.jsonsafe import jsonsafe\n'
        'from ts4_bridge import events\n'
    )
    try:
        exec(code, EXEC_GLOBALS)
    except Exception:
        log('priming exec globals failed:\n' + traceback.format_exc())
    EXEC_GLOBALS['_primed'] = True


@op('bridge.ping', doc='Liveness check.')
def ping():
    from ts4_bridge import pump
    p = pump.current()
    return {'pong': True, 'tick': p.ticks if p else None, 'time': time.time()}


@op('bridge.info', doc='Versions, zone state, connection and pump statistics.')
def info():
    from ts4_bridge import pump, bootstrap
    p = pump.current()
    zone_id, state, running = g.zone_state()
    return {
        'mod_version': bootstrap.MOD_VERSION,
        'protocol': bootstrap.PROTOCOL_VERSION,
        'game_version': paths.game_version(),
        'python': sys.version,
        'uptime_s': round(time.time() - _START, 1),
        'zone_id': zone_id, 'zone_state': state, 'zone_running': running,
        'sim_now': g.sim_now_string(),
        'connection_id': g.connection_id(),
        'pump': p.stats if p else None, 'ticks': p.ticks if p else None,
        'transport': bootstrap.transport.stats if bootstrap.transport else None,
        'clients': bootstrap.transport.connection_count() if bootstrap.transport else 0,
        'ops': len(dispatch.OPS),
        'events_seq': events.latest_seq(),
        'user_dir': paths.USER_DIR,
    }


@op('bridge.ops', doc='Describe every registered op.')
def ops():
    return dispatch.describe_all()


@op('bridge.exec', doc='Execute Python on the game thread. If the last statement is an '
                       'expression its value is returned (like a REPL). Namespace persists '
                       'between calls and has services/sims4/build_buy/create_object preloaded.')
def exec_(code, max_chars=4000, max_items=200, max_depth=6, reset=False):
    if reset:
        EXEC_GLOBALS.clear()
        EXEC_GLOBALS['__name__'] = '__ts4_bridge_exec__'
    _prime_exec_globals()
    if not isinstance(code, str):
        raise OpError('code must be a string')
    buf = io.StringIO()
    value = None
    has_value = False
    t0 = time.perf_counter()
    try:
        tree = ast.parse(code, filename='<bridge>', mode='exec')
    except SyntaxError as e:
        raise OpError('syntax error: %s (line %s)' % (e.msg, e.lineno), offset=e.offset, text=e.text)
    last_expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = ast.Expression(tree.body.pop().value)
        ast.fix_missing_locations(last_expr)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            if tree.body:
                exec(compile(tree, '<bridge>', 'exec'), EXEC_GLOBALS)
            if last_expr is not None:
                value = eval(compile(last_expr, '<bridge>', 'eval'), EXEC_GLOBALS)
                has_value = True
                EXEC_GLOBALS['_'] = value
    except Exception as e:
        return {'ok': False, 'error': {'type': type(e).__name__, 'message': str(e),
                                      'traceback': traceback.format_exc()},
                'stdout': buf.getvalue()[-max_chars:], 'ms': round((time.perf_counter() - t0) * 1000, 2)}
    out = {'ok': True, 'stdout': buf.getvalue()[-max_chars:],
           'ms': round((time.perf_counter() - t0) * 1000, 2)}
    if has_value:
        out['value'] = jsonsafe(value, max_depth=max_depth, max_items=max_items, max_chars=max_chars)
        out['value_type'] = type(value).__name__
    return out


@op('bridge.cheat', doc='Run a console/cheat command string. client_side=true for bb.*/cas.* '
                        'client cheats. Output lines are captured when the command prints.')
def cheat(command, client_side=False):
    if not isinstance(command, str) or not command.strip():
        raise OpError('command must be a non-empty string')
    lines = g.run_cheat(command.strip(), client_side=bool(client_side))
    return {'command': command, 'output': lines}


@op('bridge.commands', doc='List registered console commands (name, usage) via sims4.commands.describe.')
def commands(search=None):
    import sims4.commands
    try:
        described = sims4.commands.describe(search) or []
    except Exception as e:
        raise OpError('describe failed: %r' % (e,))
    return [jsonsafe(d) for d in described]


@op('bridge.reload', doc='Hot-reload bridge modules. With src_dir (the repo mod/ folder) the .py sources are '
                         'executed into the live module objects, bypassing the zip; without it, importlib.reload.')
def reload(modules=None, src_dir=None):
    import os
    from ts4_bridge import ops as ops_pkg
    names = modules
    if not names and src_dir:
        # take the module list from the *source* package so newly added modules are picked up
        init_path = os.path.join(src_dir, 'ts4_bridge', 'ops', '__init__.py')
        try:
            ns = {}
            with open(init_path, 'r', encoding='utf-8') as f:
                src = f.read()
            # only evaluate the ALL_MODULES assignment, not the imports
            start = src.index('ALL_MODULES')
            exec(src[start:], ns)
            names = ns.get('ALL_MODULES')
            ops_pkg.ALL_MODULES = names
        except Exception as e:
            log('could not read ALL_MODULES from source: %r' % (e,))
    names = names or ops_pkg.ALL_MODULES
    reloaded, failed = [], {}
    for name in names:
        try:
            mod = sys.modules.get(name)
            if src_dir:
                rel = name.replace('.', os.sep) + '.py'
                path = os.path.join(src_dir, rel)
                if not os.path.isfile(path):
                    raise OpError('source not found: %s' % path)
                with open(path, 'r', encoding='utf-8') as f:
                    source = f.read()
                if mod is None:
                    # brand-new module not present in the deployed zip: build it from source
                    import types
                    mod = types.ModuleType(name)
                    mod.__file__ = path
                    mod.__package__ = name.rpartition('.')[0]
                    sys.modules[name] = mod
                    parent = sys.modules.get(mod.__package__)
                    if parent is not None:
                        setattr(parent, name.rpartition('.')[2], mod)
                code = compile(source, path, 'exec')
                exec(code, mod.__dict__)
            elif mod is None:
                importlib.import_module(name)
            else:
                importlib.reload(mod)
            reloaded.append(name)
        except Exception as e:
            failed[name] = '%s: %s' % (type(e).__name__, e)
            log('reload %s failed:\n%s' % (name, traceback.format_exc()))
    # re-prime the exec namespace so it sees fresh helper modules
    EXEC_GLOBALS.pop('_primed', None)
    return {'reloaded': reloaded, 'failed': failed, 'ops': len(dispatch.OPS)}


@op('bridge.log', doc='Append a line to the mod log (debug aid).')
def log_line(message):
    log('[client] %s' % (message,))
    return True


@op('events.poll', doc='Events with seq > since (max limit).')
def events_poll(since=0, limit=200):
    return events.poll(int(since), int(limit))


@op('events.emit', doc='Emit a custom event (testing aid).')
def events_emit(name, data=None):
    return events.emit(str(name), data, sim_ts=g.sim_now_string())


@op('jobs.get', doc='State of a long-running job.')
def jobs_get(job_id):
    from ts4_bridge import pump
    p = pump.current()
    job = p.jobs.get(job_id) if p else None
    if job is None:
        raise OpError('unknown job %r' % (job_id,))
    return job.describe()


@op('jobs.list', doc='All known jobs (most recent first).')
def jobs_list(limit=50):
    from ts4_bridge import pump
    p = pump.current()
    jobs = sorted(p.jobs.values(), key=lambda j: j.created, reverse=True) if p else []
    return [j.describe() for j in jobs[:limit]]
