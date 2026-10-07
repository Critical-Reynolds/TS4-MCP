"""Game event hooks -> bridge events. Python 3.7.

Uses the event manager (TestEvents) for most things and light injections for save/zone.
Installed from bootstrap once game services exist; safe to call repeatedly.
"""
import services

from ts4_bridge import events
from ts4_bridge.log import log
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L

# survive source hot-reloads: never re-register a second event handler
_installed = globals().setdefault('_installed', False)
_handler = globals().setdefault('_handler', None)

# Reloadable per-tick extension point (pump calls hooks.on_tick each tick when available).
_tick_callbacks = globals().setdefault('_tick_callbacks', {})


def register_tick(name, fn):
    _tick_callbacks[name] = fn


def on_tick():
    for name, fn in list(_tick_callbacks.items()):
        try:
            fn()
        except Exception as e:
            log('tick callback %s failed: %r' % (name, e))
            _tick_callbacks.pop(name, None)

WATCHED = ['InteractionComplete', 'InteractionStart', 'SkillLevelChange', 'BuffBeganEvent', 'BuffEndedEvent',
           'SimDeathTypeSet', 'AgedUp', 'ReadyToAge', 'SimoleonsEarned', 'LoadingScreenLifted', 'OffspringCreated',
           'MoodChange', 'WorkdayStart', 'WorkdayComplete', 'HouseholdChanged', 'SituationStarted', 'SituationEnded',
           'OnExitBuildBuy', 'SimTravel', 'ObjectAdd', 'ObjectDestroyed', 'ItemCrafted', 'CareerEvent',
           'WhimCompleted', 'RelationshipChanged', 'AddRelationshipBit', 'SpouseEvent', 'GenerationCreated',
           'UnlockEvent', 'BillsDelivered']

NOISY = {'ObjectAdd', 'ObjectDestroyed', 'MoodChange', 'BuffBeganEvent', 'BuffEndedEvent', 'RelationshipChanged'}


IDLE_AFFORDANCE_PREFIXES = ('stand', 'sim-stand', 'sim_stand', 'idle', 'posture_', 'generic_Stand')


class _Handler(object):
    def __init__(self):
        self._recent = {}

    def _duplicate(self, key):
        """The event manager can deliver the same event several times (once per registration key);
        drop repeats seen within the last 2 seconds."""
        import time as _t
        now = _t.time()
        last = self._recent.get(key)
        if len(self._recent) > 400:
            self._recent = {k: v for k, v in self._recent.items() if now - v < 2.0}
        self._recent[key] = now
        return last is not None and now - last < 2.0

    def handle_event(self, sim_info, event_type, resolver):
        try:
            name = getattr(event_type, 'name', str(event_type))
            data = {'sim_id': getattr(sim_info, 'sim_id', None),
                    'sim': L.sim_name(sim_info) if sim_info is not None else None,
                    'household_sim': bool(getattr(sim_info, 'is_selectable', False))}
            if name in ('InteractionComplete', 'InteractionStart'):
                inter = getattr(resolver, 'interaction', None)
                if inter is not None:
                    aff_name = L.tuning_name(getattr(inter, 'affordance', type(inter)))
                    if aff_name.lower().startswith(IDLE_AFFORDANCE_PREFIXES):
                        return
                    data['interaction_id'] = getattr(inter, 'id', None)
                    data['affordance'] = aff_name
                    try:
                        t = inter.target
                        data['target_id'] = t.id if t is not None else None
                    except Exception:
                        pass
                    if name == 'InteractionComplete':
                        try:
                            data['user_cancelled'] = bool(inter.user_canceled)
                        except Exception:
                            pass
                        try:
                            data['outcome'] = getattr(getattr(inter, 'outcome_result', None), 'name', None)
                        except Exception:
                            pass
            elif name == 'SkillLevelChange':
                data['skill'] = L.tuning_name(getattr(resolver, 'skill', None)) if hasattr(resolver, 'skill') else None
                data['level'] = getattr(resolver, 'new_level', None)
            elif name == 'SimoleonsEarned':
                data['amount'] = getattr(resolver, 'amount', None)
            elif name in ('BuffBeganEvent', 'BuffEndedEvent'):
                data['buff'] = L.tuning_name(getattr(resolver, 'buff', None)) if hasattr(resolver, 'buff') else None
            elif name == 'SimDeathTypeSet':
                try:
                    data['death_type'] = getattr(sim_info.death_tracker.death_type, 'name', None)
                except Exception:
                    pass
            elif name == 'AgedUp':
                data['age'] = getattr(getattr(sim_info, 'age', None), 'name', None)
            elif name in ('SituationStarted', 'SituationEnded'):
                sit = getattr(resolver, 'situation', None)
                data['situation'] = L.tuning_name(type(sit)) if sit is not None else None
            key = {'InteractionComplete': 'interaction.finished', 'InteractionStart': 'interaction.started',
                   'SkillLevelChange': 'sim.skill_level', 'BuffBeganEvent': 'sim.buff_added',
                   'BuffEndedEvent': 'sim.buff_removed', 'SimDeathTypeSet': 'sim.died', 'AgedUp': 'sim.aged',
                   'ReadyToAge': 'sim.ready_to_age', 'SimoleonsEarned': 'household.funds',
                   'LoadingScreenLifted': 'zone.loaded', 'OffspringCreated': 'sim.born',
                   'MoodChange': 'sim.mood', 'WorkdayStart': 'career.workday_start',
                   'WorkdayComplete': 'career.workday_complete', 'HouseholdChanged': 'household.changed',
                   'SituationStarted': 'situation.started', 'SituationEnded': 'situation.ended',
                   'OnExitBuildBuy': 'buildbuy.exited', 'SimTravel': 'sim.travel', 'ObjectAdd': 'object.added',
                   'ObjectDestroyed': 'object.destroyed', 'ItemCrafted': 'sim.crafted', 'CareerEvent': 'career.event',
                   'WhimCompleted': 'sim.whim_completed', 'RelationshipChanged': 'relationship.changed',
                   'AddRelationshipBit': 'relationship.bit_added', 'SpouseEvent': 'relationship.spouse',
                   'GenerationCreated': 'household.generation', 'UnlockEvent': 'unlock', 'BillsDelivered': 'household.bills'}
            dedupe_key = (name, data.get('sim_id'), data.get('interaction_id'), data.get('skill'),
                          data.get('buff'), data.get('amount'), data.get('situation'))
            if self._duplicate(dedupe_key):
                return
            events.emit(key.get(name, 'game.' + name), data, sim_ts=g.sim_now_string())
        except Exception as e:
            log('event handler failed for %r: %r' % (event_type, e))


def _register_events(mgr):
    """Register our handler with a (possibly new) event manager. The event manager is zone-scoped,
    so this runs again after every lot change."""
    from event_testing.test_events import TestEvent
    handler = globals().get('_handler') or _Handler()
    types = []
    for n in WATCHED:
        t = getattr(TestEvent, n, None)
        if t is not None:
            types.append(t)
    mgr.register(handler, types)
    globals()['_handler'] = handler
    globals()['_mgr_id'] = id(mgr)
    return len(types)


def install():
    global _installed, _handler
    if _installed or globals().get('_installed'):
        return True
    try:
        from event_testing.test_events import TestEvent
        mgr = services.get_event_manager()
    except Exception as e:
        log('event manager unavailable yet: %r' % (e,))
        return False
    if mgr is None:
        return False
    _handler = _Handler()
    globals()['_handler'] = _handler
    try:
        count = _register_events(mgr)
    except Exception as e:
        log('event registration failed: %r' % (e,))
        return False
    types = [None] * count
    _install_save_hooks()
    _install_zone_hooks()
    try:
        from ts4_bridge.ops import dialogs
        dialogs.install_hooks()
    except Exception as e:
        log('dialog hook install failed: %r' % (e,))
    _installed = True
    globals()['_installed'] = True
    globals()['_handler'] = _handler
    log('event hooks installed for %d TestEvents' % len(types))
    return True


def _install_zone_hooks():
    """Class-level patches on Zone survive lot changes (unlike the per-zone event manager)."""
    try:
        import zone as zone_mod
    except Exception as e:
        log('zone hooks unavailable: %r' % (e,))
        return
    Zone = zone_mod.Zone
    if getattr(Zone.on_loading_screen_animation_finished, '_ts4_bridge_wrapped', False):
        return
    orig_lifted = Zone.on_loading_screen_animation_finished
    orig_teardown = getattr(Zone, 'on_teardown', None)

    def on_loading_screen_animation_finished(self, *args, **kwargs):
        result = orig_lifted(self, *args, **kwargs)
        try:
            # make sure the event handler is on the new zone's event manager right away
            try:
                mgr = services.get_event_manager()
                if mgr is not None and id(mgr) != globals().get('_mgr_id'):
                    _register_events(mgr)
            except Exception:
                pass
            events.emit('zone.loaded', {'zone_id': self.id}, sim_ts=g.sim_now_string())
        except Exception as e:
            log('zone.loaded hook failed: %r' % (e,))
        return result

    on_loading_screen_animation_finished._ts4_bridge_wrapped = True
    Zone.on_loading_screen_animation_finished = on_loading_screen_animation_finished

    if orig_teardown is not None:
        def on_teardown(self, *args, **kwargs):
            try:
                events.emit('zone.unloading', {'zone_id': self.id}, sim_ts=g.sim_now_string())
            except Exception:
                pass
            return orig_teardown(self, *args, **kwargs)
        Zone.on_teardown = on_teardown
    log('zone hooks installed')


def _install_save_hooks():
    try:
        from services.persistence_service import PersistenceService
    except Exception as e:
        log('persistence hooks unavailable: %r' % (e,))
        return
    if getattr(PersistenceService.save_using, '_ts4_bridge_wrapped', False):
        return
    orig_save_using = PersistenceService.save_using
    orig_destroy = PersistenceService._destroy_save_timeline

    def save_using(self, save_generator, *args, **kwargs):
        try:
            events.emit('save.started', {}, sim_ts=g.sim_now_string())
        except Exception:
            pass
        return orig_save_using(self, save_generator, *args, **kwargs)

    def destroy_save_timeline(self, *args, **kwargs):
        result = orig_destroy(self, *args, **kwargs)
        try:
            events.emit('save.done', {}, sim_ts=g.sim_now_string())
        except Exception:
            pass
        return result

    save_using._ts4_bridge_wrapped = True
    PersistenceService.save_using = save_using
    PersistenceService._destroy_save_timeline = destroy_save_timeline
    log('save hooks installed')


def ensure_installed_on_tick():
    """Called from the pump periodically: installs hooks once services exist, and re-registers the
    event handler whenever the zone (and with it the event manager) has been replaced."""
    if not globals().get('_installed'):
        install()
        return
    _install_zone_hooks()  # idempotent; picks up hooks added by a source reload
    try:
        mgr = services.get_event_manager()
    except Exception:
        return
    if mgr is None or id(mgr) == globals().get('_mgr_id'):
        return
    try:
        count = _register_events(mgr)
        log('event hooks re-registered on new event manager (%d events)' % count)
    except Exception as e:
        log('event re-registration failed: %r' % (e,))
