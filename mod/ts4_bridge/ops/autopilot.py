"""Autopilot: keeps the active sim playing a money loop (needs, sleep, painting, selling, bills, routine
dialogs) on an in-game alarm so long stretches run without a tool call per sim hour. Python 3.7.

Everything here is ordinary play: interactions the player could click, buy-mode selling, paying bills.
No funds, skill or career cheats.
"""
import time
import traceback

import alarms
import clock
import services
from clock import ClockSpeedMode

from ts4_bridge.dispatch import op, OpError
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L

# State survives hot-reloads (bridge.reload re-executes this module into the same globals).
_state = globals().setdefault('_state', {
    'alarm': None, 'running': False,
    'cfg': {'activity': 'paint', 'recipe': 'recipe_Painting_Classics', 'interval': 10, 'fast': True,
            'auto_dialogs': True, 'pay_bills': True, 'sleep_from': 22, 'sleep_until': 6},
    'stats': {'ticks': 0, 'sold': 0, 'earned': 0, 'paintings_started': 0, 'bills_paid': 0, 'dialogs': 0,
              'errors': 0, 'last_error': None, 'started_funds': None, 'started_at': None},
    'log': [],
})

NEED_RULES = (  # (motive, threshold, label, substrings meaning "already handling it")
    ('Bladder', -30, 'toilet', ('toilet',)),
    ('Hunger', -20, 'eat', ('eat', 'cook', 'stove', 'fridge', 'snack')),
    ('Hygiene', -35, 'shower', ('shower', 'bath')),
)
FIXTURES = {  # label -> (tuning-class substrings, affordance tuning name)
    'easel': (('paintEasel',), None),
    'fridge': (('fridge',), 'fridge_CookAutonomously'),
    'bed': (('bedDouble', 'bedSingle', 'object_bed'), 'bed_sleep'),
    'toilet': (('toilet',), 'toilet-use-sitting'),
    'shower': (('Shower', 'shower'), 'shower_TakeShower'),
}


def _log(msg):
    try:
        now = str(services.time_service().sim_now)
    except Exception:
        now = '?'
    _state['log'].append('%s %s' % (now, msg))
    del _state['log'][:-40]


def _find(label):
    names, _ = FIXTURES[label]
    for o in services.object_manager().values():
        tn = type(o).__name__
        if getattr(o, 'is_sim', False) or getattr(o, 'parent', None) is not None and label != 'easel':
            continue
        if any(n in tn for n in names) and not tn.startswith('object_Canvas'):
            if label == 'fridge' and 'Mini' in tn:
                continue
            return o
    return None


def _motives(info):
    return {type(c).__name__[7:]: c.get_value() for c in info.commodity_tracker
            if type(c).__name__.startswith('motive_')}


def _active_names(sim):
    out = []
    for i in list(sim.si_state) + list(sim.queue):
        try:
            name = L.tuning_name(i.affordance)
        except Exception:
            continue
        if getattr(i, 'is_user_directed', False) or any(k in name.lower() for k in ('sleep', 'eat', 'cook', 'toilet', 'shower')):
            out.append(name)
    return out


def _push(name, target):
    from ts4_bridge.ops import interactions as I
    try:
        return bool(I.interactions_push(name, sim_id='active', target_id=str(target.id), insert='next').get('ok'))
    except Exception as e:
        _log('push %s failed: %s' % (name, str(e)[:120]))
        return False


MEAL_PREFERENCE = ('MacNCheese-Single', 'GrilledCheese', 'EggsToast-Single', 'Pancakes', 'Cereal')
EAT_COOLDOWN_MIN = 60


def _cook_from_picker(sim, fridge):
    """Cook a specific single-serving recipe through the fridge's recipe picker rows."""
    from ts4_bridge.ops import interactions as I
    ctx = I.make_context(sim, target=fridge)
    options = []
    for aop in I.all_aops(sim, fridge, ctx):
        rmap = aop.interaction_parameters.get('recipe_ingredients_map')
        if not rmap or not getattr(aop, '_ts4_result', None):
            continue
        for recipe in rmap.keys():
            options.append((L.tuning_name(recipe), recipe, aop))
    for want in MEAL_PREFERENCE:
        for name, recipe, aop in options:
            if want.lower() in name.lower():
                si = aop.interaction_factory(I.make_context(sim, target=fridge, insert='next')).interaction
                if si.on_choice_selected(recipe):
                    _log('cooking %s' % name)
                    return True
    return False


def _eat(sim, info):
    now_min = services.time_service().sim_now.absolute_minutes()
    if now_min - _state.get('last_eat', -10 ** 9) < EAT_COOLDOWN_MIN:
        return 'cooldown'
    fridge = _find('fridge')
    if fridge is None:
        return False
    _state['last_eat'] = now_min
    vegetarian = any(t.__name__ == 'trait_Vegetarian' for t in info.trait_tracker.equipped_traits)
    if _push('fridge_CookAutonomously_Vegetarian' if vegetarian else 'fridge_CookAutonomously', fridge):
        return True
    return _cook_from_picker(sim, fridge)


def _sell_finished():
    from ts4_bridge.ops import buy as B
    sold = 0
    for o in list(services.object_manager().values()):
        if not type(o).__name__.startswith('object_Canvas'):
            continue
        cp = o.get_crafting_process() if hasattr(o, 'get_crafting_process') else None
        if cp is None or not getattr(cp, 'is_complete', False) or not o.current_value:
            continue
        value = int(o.current_value)
        B.objects_sell(o.id)
        _state['stats']['sold'] += 1
        _state['stats']['earned'] += value
        sold += 1
        _log('sold painting for %d' % value)
    return sold


def _unfinished_canvas():
    for o in services.object_manager().values():
        if type(o).__name__.startswith('object_Canvas'):
            cp = o.get_crafting_process() if hasattr(o, 'get_crafting_process') else None
            if cp is not None and not getattr(cp, 'is_complete', False):
                return o
    return None


def _paint(sim):
    from ts4_bridge.ops import interactions as I
    canvas = _unfinished_canvas()
    if canvas is not None:  # resume instead of abandoning a half-finished canvas
        ctx = I.make_context(sim, target=canvas)
        for aop in I.all_aops(sim, canvas, ctx):
            name = L.tuning_name(aop.affordance)
            res = getattr(aop, '_ts4_result', None)
            if 'Paint' in name and res and getattr(aop.affordance, 'allow_user_directed', True):
                ok = bool(aop.test_and_execute(I.make_context(sim, target=canvas, insert='last')))
                _log('resume canvas via %s: %s' % (name, ok))
                return ok
    easel = _find('easel')
    if easel is None:
        return False
    ctx = I.make_context(sim, target=easel)
    aop = next((a for a in I.all_aops(sim, easel, ctx) if L.tuning_name(a.affordance).endswith('easel_StartCrafting')), None)
    if aop is None:
        return False
    recipes = {L.tuning_name(r): r for r in aop.interaction_parameters['recipe_ingredients_map'].keys()}
    recipe = recipes.get(_state['cfg']['recipe']) or recipes.get('recipe_Painting_Classics')
    si = aop.interaction_factory(I.make_context(sim, target=easel, insert='last')).interaction
    ok = bool(si.on_choice_selected(recipe))
    if ok:
        _state['stats']['paintings_started'] += 1
    return ok


def _pay_bills():
    hh = services.active_household()
    bm = getattr(hh, 'bills_manager', None)
    owed = getattr(bm, 'current_payment_owed', None) if bm else None
    if owed and hh.funds.money >= owed:
        bm.pay_bill()
        _state['stats']['bills_paid'] += 1
        _log('paid bills %d' % owed)


def _handle_dialogs():
    from ts4_bridge.ops import dialogs as D
    for dialog_id, dialog in list(D.active_dialogs().items()):
        brief = D.dialog_brief(dialog)
        responses = brief.get('responses') or []
        if getattr(dialog, 'is_picker', False) or brief.get('is_picker'):
            continue  # pickers need a real choice; leave them for the agent
        rid = responses[0]['response_id'] if responses else 'ok'
        try:
            D.dialogs_respond(dialog_id, response_id=rid)
            _state['stats']['dialogs'] += 1
            _log('dialog %r -> %s' % (str(brief.get('title'))[:50], rid))
        except Exception as e:
            _log('dialog %s respond failed: %r' % (dialog_id, e))


def _set_speed(fast_forward):
    gc = services.game_clock_service()
    want = ClockSpeedMode.SUPER_SPEED3 if fast_forward else ClockSpeedMode.SPEED3
    if gc.clock_speed != want and not gc.set_clock_speed(want) and fast_forward:
        gc.set_clock_speed(ClockSpeedMode.SPEED3)


def step():
    """One autopilot decision. Safe to call by hand."""
    _state['stats']['ticks'] += 1
    cfg = _state['cfg']
    if cfg['auto_dialogs']:
        _handle_dialogs()
    if cfg['pay_bills']:
        _pay_bills()
    info = services.active_sim_info()
    sim = info.get_sim_instance() if info else None
    if sim is None:  # at work / away
        if cfg['fast']:
            _set_speed(True)
        return {'act': 'away'}
    sold = _sell_finished()
    hour = services.time_service().sim_now.hour()
    m = _motives(info)
    busy = _active_names(sim)
    sleeping = any('sleep' in b.lower() for b in busy)
    act = None
    if not sleeping:
        for motive, threshold, label, handling in NEED_RULES:
            if m.get(motive, 100) < threshold and not any(h in b.lower() for b in busy for h in handling):
                if label == 'eat':
                    act = ('eat', _eat(sim, info))
                else:
                    target = _find(label)
                    act = (label, _push(FIXTURES[label][1], target) if target else False)
                break
        if act is None and (hour >= cfg['sleep_from'] or hour < cfg['sleep_until'] or m.get('Energy', 100) < -60):
            bed = _find('bed')
            act = ('sleep', _push('bed_sleep', bed) if bed else False)
        if act is None and not busy and cfg['activity'] == 'paint':
            act = ('paint', _paint(sim))
    if cfg['fast']:
        _set_speed(sleeping)
    if act and act[1] != 'cooldown':
        _log('%s -> %s' % act)
    return {'hour': hour, 'act': act, 'sold': sold, 'busy': busy[:4]}


def _on_tick():
    """Real-time hook: modal dialogs pause the sim clock, so sim-time alarms can't answer them."""
    try:
        if not (_state['running'] and _state['cfg']['auto_dialogs']):
            return
        now = time.monotonic()
        if now - _state.get('last_dialog_check', 0) < 1.0:
            return
        _state['last_dialog_check'] = now
        from ts4_bridge.ops import dialogs as D
        if D.active_dialogs():
            _handle_dialogs()
            if _state['cfg']['fast'] and not D.active_dialogs():
                _set_speed(False)  # the dialog paused the game; resume
    except Exception:
        _state['stats']['errors'] += 1
        _state['stats']['last_error'] = traceback.format_exc()[-800:]


try:  # re-registered on every reload so the hook always runs the current code
    from ts4_bridge import hooks as _hooks
    _hooks.register_tick('autopilot', _on_tick)
except Exception:
    pass


def _on_alarm(_handle):
    try:
        step()
    except Exception:
        _state['stats']['errors'] += 1
        _state['stats']['last_error'] = traceback.format_exc()[-800:]


def _stop_alarm():
    handle = _state.get('alarm')
    if handle is not None:
        try:
            alarms.cancel_alarm(handle)
        except Exception:
            pass
    _state['alarm'] = None


@op('autopilot.start', doc='Start the autopilot money loop (paint/sell/needs/sleep/bills/dialogs) on a repeating '
                           'in-game alarm. cfg overrides: activity, recipe, interval (sim minutes), fast, '
                           'auto_dialogs, pay_bills, sleep_from, sleep_until.')
def autopilot_start(**cfg):
    g.require_zone()
    unknown = set(cfg) - set(_state['cfg'])
    if unknown:
        raise OpError('unknown autopilot options: %s' % sorted(unknown), options=sorted(_state['cfg']))
    _state['cfg'].update(cfg)
    _stop_alarm()
    import ts4_bridge
    _state['alarm'] = alarms.add_alarm(ts4_bridge, clock.interval_in_sim_minutes(int(_state['cfg']['interval'])),
                                       _on_alarm, repeating=True)
    _state['running'] = True
    if _state['stats']['started_funds'] is None:
        _state['stats']['started_funds'] = int(services.active_household().funds.money)
        _state['stats']['started_at'] = str(services.time_service().sim_now)
    first = step()
    return {'running': True, 'cfg': dict(_state['cfg']), 'first_step': first}


@op('autopilot.stop', doc='Stop the autopilot and pause the game.')
def autopilot_stop(pause=True):
    _stop_alarm()
    _state['running'] = False
    if pause:
        services.game_clock_service().set_clock_speed(ClockSpeedMode.PAUSED)
    return {'running': False}


@op('autopilot.status', doc='Autopilot config, counters, recent log and current funds.')
def autopilot_status(log_lines=15):
    out = {'running': _state['running'] and _state['alarm'] is not None, 'cfg': dict(_state['cfg']),
           'stats': dict(_state['stats']), 'log': _state['log'][-int(log_lines):]}
    try:
        out['funds'] = int(services.active_household().funds.money)
        out['sim_now'] = str(services.time_service().sim_now)
    except Exception:
        pass
    return out
