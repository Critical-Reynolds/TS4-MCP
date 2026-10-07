"""Snapshot and time ops. Python 3.7."""
import services
from clock import ClockSpeedMode

from ts4_bridge.dispatch import op, OpError
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L

SPEED_NAMES = {'paused': ClockSpeedMode.PAUSED, 'pause': ClockSpeedMode.PAUSED, '0': ClockSpeedMode.PAUSED,
               'normal': ClockSpeedMode.NORMAL, '1': ClockSpeedMode.NORMAL,
               'fast': ClockSpeedMode.SPEED2, 'speed2': ClockSpeedMode.SPEED2, '2': ClockSpeedMode.SPEED2,
               'ultra': ClockSpeedMode.SPEED3, 'speed3': ClockSpeedMode.SPEED3, '3': ClockSpeedMode.SPEED3,
               'super': ClockSpeedMode.SUPER_SPEED3, 'super_speed3': ClockSpeedMode.SUPER_SPEED3}


def time_state():
    gc = services.game_clock_service()
    speed = gc.clock_speed
    out = {'sim_now': g.sim_now_string(), 'speed': getattr(speed, 'name', str(speed)),
           'paused': speed == ClockSpeedMode.PAUSED}
    try:
        now = services.time_service().sim_now
        out['day_of_week'] = int(now.day() % 7)
        out['hour'] = int(now.hour())
        out['minute'] = int(now.minute())
        out['abs_minutes'] = int(now.absolute_minutes())
        out['abs_days'] = int(now.absolute_days())
    except Exception:
        pass
    return out


def zone_state_brief():
    zone_id, state, running = g.zone_state()
    out = {'zone_id': zone_id, 'state': state, 'running': running}
    if zone_id is None:
        return out
    try:
        proto = services.get_persistence_service().get_zone_proto_buff(zone_id)
        if proto is not None:
            out['lot_name'] = proto.name
            out['world_id'] = proto.world_id
            out['owner_household_id'] = proto.household_id
    except Exception:
        pass
    try:
        venue = services.venue_service().get_venue_tuning(zone_id)
        if venue is not None:
            out['venue'] = L.tuning_name(venue)
            out['is_residential'] = bool(venue.is_residential)
    except Exception:
        pass
    try:
        zone = services.current_zone()
        lot = zone.lot
        out['lot_size'] = [int(lot.size_x), int(lot.size_z)]
        out['lot_center'] = {'x': round(float(lot.center.x), 2), 'z': round(float(lot.center.z), 2)}
    except Exception:
        pass
    try:
        hh = services.active_household()
        out['active_household_is_home'] = bool(hh is not None and hh.home_zone_id == zone_id)
    except Exception:
        pass
    return out


def household_brief(hh):
    if hh is None:
        return None
    out = {'household_id': hh.id, 'name': hh.name}
    try:
        out['funds'] = int(hh.funds.money)
    except Exception:
        pass
    try:
        out['home_zone_id'] = hh.home_zone_id
    except Exception:
        pass
    try:
        out['members'] = [L.sim_brief(si) for si in hh.sim_info_gen()]
    except Exception:
        out['members'] = []
    return out


def motives(info, visible_only=True):
    out = {}
    try:
        tracker = info.commodity_tracker
        for c in tracker.get_all_commodities():
            try:
                if visible_only and not c.is_visible:
                    continue
                name = L.tuning_name(c.stat_type)
                if visible_only and 'motive' not in name.lower():
                    continue
                short = name
                for prefix in ('commodity_Motive_', 'motive_', 'Motive_', 'commodity_'):
                    if short.startswith(prefix):
                        short = short[len(prefix):]
                        break
                out[short] = {
                    'value': round(float(c.get_value()), 1),
                    'min': float(c.min_value), 'max': float(c.max_value)}
            except Exception:
                continue
    except Exception as e:
        out['_error'] = repr(e)
    return out


def queue_brief(sim):
    out = {'running': [], 'queued': []}
    try:
        for si in sim.si_state:
            out['running'].append(interaction_brief(si))
    except Exception:
        pass
    try:
        for i in sim.queue:
            out['queued'].append(interaction_brief(i))
    except Exception:
        pass
    return out


def interaction_brief(i):
    d = {'id': getattr(i, 'id', None), 'name': L.tuning_name(getattr(i, 'affordance', type(i)))}
    try:
        t = i.target
        if t is not None:
            d['target_id'] = t.id
            d['target'] = type(t).__name__ if not getattr(t, 'is_sim', False) else L.sim_name(t.sim_info)
    except Exception:
        pass
    try:
        d['running'] = bool(i.running)
    except Exception:
        pass
    try:
        d['user_directed'] = bool(i.is_user_directed)
    except Exception:
        pass
    try:
        d['display_name'] = L.loc(i.get_name())
    except Exception:
        pass
    return d


def mood_brief(info):
    out = {}
    try:
        mood = info.get_mood()
        out['mood'] = L.tuning_name(mood)
        out['intensity'] = int(info.get_mood_intensity())
    except Exception:
        pass
    return out


def buffs_brief(info, limit=20):
    out = []
    try:
        for b in info.Buffs:
            bt = getattr(b, 'buff_type', type(b))
            d = {'name': L.tuning_name(bt), 'id': L.guid(bt)}
            try:
                d['mood'] = L.tuning_name(bt.mood_type) if getattr(bt, 'mood_type', None) else None
                d['weight'] = int(bt.mood_weight)
            except Exception:
                pass
            try:
                d['display_name'] = L.loc(bt.buff_name)
            except Exception:
                pass
            out.append(d)
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


@op('state.snapshot', doc='Compact overview: zone, time, household, active sim (needs, mood, queue), '
                          'selectable sims, pending dialogs.')
def snapshot(include_buffs=True, include_members=True):
    zone = zone_state_brief()
    out = {'zone': zone}
    if zone.get('zone_id') is None:
        out['note'] = 'No lot loaded (main menu or loading). Ask the player to load a household.'
        return out
    out['time'] = time_state()
    client = g.first_client()
    hh = services.active_household()
    out['household'] = household_brief(hh) if include_members else (
        {'household_id': hh.id, 'name': hh.name, 'funds': int(hh.funds.money)} if hh else None)
    info = services.active_sim_info()
    if info is not None:
        active = L.sim_brief(info)
        active.update(mood_brief(info))
        active['motives'] = motives(info)
        if include_buffs:
            active['buffs'] = buffs_brief(info)
        sim = info.get_sim_instance()
        if sim is not None:
            active['queue'] = queue_brief(sim)
        out['active_sim'] = active
    if client is not None:
        try:
            out['selectable_sim_ids'] = [si.sim_id for si in client.selectable_sims]
        except Exception:
            pass
    try:
        from ts4_bridge.ops import dialogs
        out['pending_dialogs'] = dialogs.pending_summary()
    except Exception:
        pass
    return out


@op('time.get', doc='Current sim time and speed.')
def time_get():
    g.require_zone()
    return time_state()


@op('time.set_speed', doc='Set clock speed: paused|normal|fast|ultra|super (or 0-3).')
def time_set_speed(speed):
    g.require_zone()
    mode = SPEED_NAMES.get(str(speed).lower())
    if mode is None:
        raise OpError('unknown speed %r; use paused|normal|fast|ultra|super' % (speed,))
    gc = services.game_clock_service()
    ok = gc.set_clock_speed(mode)
    return {'ok': bool(ok), **time_state()}


PASSIVE_PREFIXES = ('sim-stand', 'stand_passive', 'sim_stand')


def _user_directed(sim):
    out = []
    for i in list(sim.si_state) + list(sim.queue):
        try:
            name = L.tuning_name(i.affordance)
        except Exception:
            continue
        if getattr(i, 'is_user_directed', False) and not name.lower().startswith(PASSIVE_PREFIXES):
            out.append(name)
    return out


def _finished_crafts():
    """Finished paintings (and other completed canvases) waiting to be sold."""
    out = []
    for o in services.object_manager().values():
        if not type(o).__name__.startswith('object_Canvas'):
            continue
        cp = o.get_crafting_process() if hasattr(o, 'get_crafting_process') else None
        if cp is not None and getattr(cp, 'is_complete', False) and getattr(o, 'current_value', 0):
            out.append({'id': o.id, 'value': int(o.current_value)})
    return out


@op('wake.check', doc='One poll for wait(wake_on=...): time, active-sim facts (on lot, asleep, idle, motives '
                      'below thresholds) and sellable crafts. speed=auto keeps super speed while the sim sleeps '
                      'or is away and ultra otherwise (never overrides a pause).')
def wake_check(needs=None, speed=None):
    g.require_zone()
    out = {'time': time_state()}
    info = services.active_sim_info()
    sim = info.get_sim_instance() if info is not None else None
    active = {'sim_id': info.sim_id if info else None, 'on_lot': sim is not None}
    sleeping = False
    if sim is not None:
        directed = _user_directed(sim)
        running = []
        for i in list(sim.si_state):
            try:
                running.append(L.tuning_name(i.affordance))
            except Exception:
                pass
        sleeping = any('sleep' in n.lower() for n in running)
        active.update({'idle': not directed, 'directed': directed[:5], 'sleeping': sleeping,
                       'mood': mood_brief(info).get('mood')})
        mot = {k: v['value'] for k, v in motives(info).items() if isinstance(v, dict)}
        active['motives'] = {k: round(v, 1) for k, v in mot.items()}
        active['below'] = sorted(k for k, t in (needs or {}).items() if k in mot and mot[k] < float(t))
    out['active'] = active
    out['sellable'] = _finished_crafts()
    if speed and not out['time'].get('paused'):
        if str(speed).lower() == 'auto':
            mode = ClockSpeedMode.SUPER_SPEED3 if (sim is None or sleeping) else ClockSpeedMode.SPEED3
        else:
            mode = SPEED_NAMES.get(str(speed).lower())
        gc = services.game_clock_service()
        if mode is not None and gc.clock_speed != mode and not gc.set_clock_speed(mode):
            gc.set_clock_speed(ClockSpeedMode.SPEED3)  # super speed refused (someone awake/on lot)
    return out


@op('time.advance', doc='Jump the game clock forward (cheat-like; sims do not simulate the skipped time).')
def time_advance(hours=0, minutes=0):
    g.require_zone()
    services.game_clock_service().advance_game_time(hours=int(hours), minutes=int(minutes))
    return time_state()
