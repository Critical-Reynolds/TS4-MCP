"""Mod start-up: transport thread, tick pump, discovery file, console commands. Python 3.7."""
import json
import os
import sys
import time

from ts4_bridge import paths
from ts4_bridge.log import log
from ts4_bridge.transport import Transport, make_token, PROTOCOL_VERSION

MOD_VERSION = '0.1.0'
DEFAULT_PORT = 47831

transport = None
pump_instance = None


def _hello():
    from ts4_bridge.util import game as g
    zone_id, state, running = g.zone_state()
    return {
        'protocol': PROTOCOL_VERSION, 'mod_version': MOD_VERSION,
        'game_version': paths.game_version(), 'python': sys.version.split()[0],
        'zone_id': zone_id, 'zone_state': state, 'zone_loaded': running,
        'sim_now': g.sim_now_string(),
        'game_dir': _game_dir(),
    }


def _game_dir():
    """Install folder (…\\The Sims 4), derived from the process working directory (Game\\Bin)."""
    try:
        cwd = os.getcwd()
        probe = cwd
        for _ in range(4):
            if os.path.isdir(os.path.join(probe, 'Data', 'Client')):
                return probe
            probe = os.path.dirname(probe)
    except Exception:
        pass
    return None


def _write_bridge_file(port, token):
    paths.ensure_dirs()
    data = {'port': port, 'token': token, 'pid': os.getpid(), 'mod_version': MOD_VERSION,
            'protocol': PROTOCOL_VERSION, 'started': time.time()}
    tmp = paths.BRIDGE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, paths.BRIDGE_FILE)


def _pick_port():
    try:
        with open(paths.BRIDGE_FILE, 'r', encoding='utf-8') as f:
            old = json.load(f)
        port = int(old.get('port', DEFAULT_PORT))
        if 1024 < port < 65536:
            return port
    except Exception:
        pass
    return DEFAULT_PORT


def start():
    global transport, pump_instance
    paths.ensure_dirs()
    log('=' * 60)
    log('ts4_bridge %s starting; python %s; user_dir=%s' % (MOD_VERSION, sys.version.split()[0], paths.USER_DIR))

    from ts4_bridge import pump as pump_mod
    from ts4_bridge import ops  # noqa: F401  (registers ops)
    from ts4_bridge import commands  # noqa: F401  (registers console commands)

    token = make_token()

    def _health():
        p = pump_instance
        return p.health() if p is not None else {'installed': False}

    t = Transport(token=token, port=_pick_port(), log=log, health=_health)
    try:
        t.start()
    except OSError as e:
        log('port %d busy (%r); falling back to an ephemeral port' % (t.port, e))
        t = Transport(token=token, port=0, log=log, health=_health)
        t.start()
    transport = t

    p = pump_mod.Pump(t, _hello)
    pump_mod.install(p)
    pump_instance = p

    _write_bridge_file(t.port, token)
    log('ready: port=%d bridge_file=%s ops=%d' % (t.port, paths.BRIDGE_FILE, len(ops_count())))


def ops_count():
    from ts4_bridge import dispatch
    return dispatch.OPS
