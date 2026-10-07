"""Small helpers over EA services. Only ever called on the game thread. Python 3.7."""
import services
import sims4.commands

from ts4_bridge.dispatch import OpError


def connection_id():
    """Session/connection id for commands and client cheats (0 if no client)."""
    try:
        client = services.client_manager().get_first_client()
    except Exception:
        client = None
    if client is None:
        return 0
    return client.id


def first_client():
    try:
        return services.client_manager().get_first_client()
    except Exception:
        return None


def zone_state():
    """Returns (zone_id, state_name, is_running)."""
    try:
        zone = services.current_zone()
    except Exception:
        zone = None
    if zone is None:
        return None, 'NO_ZONE', False
    state = getattr(zone, '_zone_state', None)
    state_name = getattr(state, 'name', str(state))
    # ZoneState.RUNNING means live/build mode on a loaded lot; is_zone_running() also demands the
    # client be fully connected, which is false in some edit/build situations, so key off the state.
    running = state_name == 'RUNNING'
    return zone.id, state_name, running


def require_zone():
    zone_id, state, running = zone_state()
    if zone_id is None or not running:
        raise OpError('no running zone (state=%s); load a lot first' % state, zone_state=state)
    return services.current_zone()


def sim_now_string():
    try:
        now = services.time_service().sim_now
        return 'Day %d %02d:%02d' % (int(now.day()), int(now.hour()), int(now.minute()))
    except Exception:
        return None


_testing_cheats_enabled = False


def ensure_testing_cheats():
    """Cheat-type console commands are silently ignored unless testingcheats is on."""
    global _testing_cheats_enabled
    if _testing_cheats_enabled:
        return
    try:
        sims4.commands.execute('testingcheats true', connection_id())
        _testing_cheats_enabled = True
    except Exception:
        pass


def run_cheat(command, client_side=False, enable_testing_cheats=True):
    """Execute a console command string. Output lines are captured where possible."""
    conn = connection_id()
    if enable_testing_cheats and not command.lower().startswith('testingcheats'):
        ensure_testing_cheats()
    captured = []
    original = sims4.commands.cheat_output

    def _capture(s, context):
        captured.append(str(s))
        try:
            original(s, context)
        except Exception:
            pass

    sims4.commands.cheat_output = _capture
    orig_output = sims4.commands.output
    sims4.commands.output = _capture
    try:
        if client_side:
            sims4.commands.client_cheat(command, conn)
        else:
            sims4.commands.execute(command, conn)
    finally:
        sims4.commands.cheat_output = original
        sims4.commands.output = orig_output
    return captured
