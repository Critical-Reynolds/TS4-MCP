"""Finding and quitting jobs the way a player does. Python 3.7.

A player finds work through "Find a Job" on the phone or a computer (`phone_JoinCareer`,
`computer_JoinCareer`). That interaction makes the game build a career-selection panel
(`Career.get_join_career_pb`, limited to the day's handful of openings) and send it to the client.
The client answers with the `careers.select` command. We capture the panel when the game builds it
and answer it through the same command, so the agent only ever takes a job it was actually offered.
"""
import time

import services
from sims4.resources import Types

from ts4_bridge import events
from ts4_bridge.dispatch import op, OpError
from ts4_bridge.log import log
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L

# survives source hot-reloads
_offers = globals().setdefault('_offers', {})  # sim_id -> offer dict
_hooked = globals().setdefault('_hooked', False)
_element_hooked = globals().setdefault('_element_hooked', False)
# interaction ids the agent pushed; their career panels are captured but not sent to the screen
_agent_interactions = globals().setdefault('_agent_interactions', [])


def mark_agent_interaction(interaction):
    try:
        iid = int(interaction.id)
    except Exception:
        return
    _agent_interactions.append(iid)
    del _agent_interactions[:-200]


def _is_agent(interaction):
    try:
        return interaction is not None and int(interaction.id) in _agent_interactions
    except Exception:
        return False

JOIN, QUIT = 'join', 'quit'


def _reason_name(reason):
    try:
        from careers.career_ops import CareerOps
        if int(reason) == int(CareerOps.QUIT_CAREER):
            return QUIT
        if int(reason) == int(CareerOps.JOIN_CAREER):
            return JOIN
    except Exception:
        pass
    return str(reason)


def _choice_brief(sim_info, choice):
    career_cls = services.get_instance_manager(Types.CAREER).get(choice.uid)
    track = services.get_instance_manager(Types.CAREER_TRACK).get(choice.career_track)
    d = {'career_id': int(choice.uid), 'career': L.tuning_name(career_cls) if career_cls else None,
         'track_id': int(choice.career_track), 'track': L.tuning_name(track) if track else None,
         'level': int(choice.career_level), 'user_level': int(choice.career_level) + 1,
         'selectable': bool(choice.is_selectable), 'hourly_pay': int(choice.hourly_pay),
         'active_career': bool(choice.is_active)}
    try:
        d['name'] = L.loc(track.get_career_name(sim_info)) if track is not None else None
    except Exception:
        pass
    try:
        level_tuning = track.career_levels[int(choice.career_level)]
        d['title'] = L.loc(level_tuning.get_title(sim_info))
        sched = level_tuning.work_scheduler
        d['schedule'] = repr(sched)[:120] if sched is not None else None
    except Exception:
        pass
    try:
        if choice.HasField('company') and choice.company.hash:
            d['company'] = L.loc(choice.company)
    except Exception:
        pass
    try:
        if choice.HasField('not_selectable_tooltip'):
            d['why_not'] = L.loc(choice.not_selectable_tooltip)
    except Exception:
        pass
    try:
        if choice.conflicted_schedule:
            d['conflicts_with_current_job'] = True
    except Exception:
        pass
    try:
        cat = getattr(career_cls, 'career_category', None)
        d['category'] = L.enum_name(cat)
    except Exception:
        pass
    return d


def _capture(msg, source):
    try:
        sim_info = services.sim_info_manager().get(msg.sim_id)
        if sim_info is None:
            return
        reason = _reason_name(msg.reason)
        offer = {'sim_id': int(msg.sim_id), 'sim': L.sim_name(sim_info), 'kind': reason, 'source': source,
                 'shown_at': g.sim_now_string(), 'real_time': time.time(),
                 'current_shift': L.enum_name(getattr(msg, 'current_shift', None)),
                 'choices': [_choice_brief(sim_info, c) for c in msg.career_choices]}
        _offers[int(msg.sim_id)] = offer
        events.emit('career.offers' if reason == JOIN else 'career.quit_choices',
                    {'sim_id': offer['sim_id'], 'sim': offer['sim'], 'count': len(offer['choices'])},
                    sim_ts=offer['shown_at'])
    except Exception as e:
        log('career offer capture failed: %r' % (e,))


def install_hooks():
    """Wrap Career.get_join_career_pb / get_quit_career_pb (static methods) to record what the game
    offers. Idempotent; returns True once installed."""
    if globals().get('_hooked'):
        return _install_element_hook()
    try:
        from careers.career_tuning import Career
    except Exception as e:
        log('career hooks unavailable yet: %r' % (e,))
        return False
    for name, source in (('get_join_career_pb', 'join'), ('get_quit_career_pb', 'quit')):
        orig = Career.__dict__.get(name)
        func = getattr(orig, '__func__', orig)
        if func is None or getattr(func, '_ts4_bridge_wrapped', False):
            continue

        def make(func, source):
            def wrapper(*args, **kwargs):
                msg = func(*args, **kwargs)
                if msg is not None:
                    _capture(msg, source)
                return msg
            wrapper._ts4_bridge_wrapped = True
            return staticmethod(wrapper)

        setattr(Career, name, make(func, source))
    globals()['_hooked'] = True
    log('career offer hooks installed')
    return _install_element_hook()


def _install_element_hook():
    """When the agent runs Find a Job / Quit Job, build the career panel (captured by the hooks above)
    without sending it to the player's screen, where nothing would ever close it. Same logic as
    CareerSelectElement._do_behavior minus the Distributor.add_op."""
    if globals().get('_element_hooked'):
        return True
    try:
        from careers.career_tuning import Career, CareerSelectElement
        from careers import career_ops
    except Exception as e:
        log('career element hook unavailable: %r' % (e,))
        return False
    orig = CareerSelectElement._do_behavior
    if getattr(orig, '_ts4_bridge_wrapped', False):
        globals()['_element_hooked'] = True
        return True

    def _do_behavior(self, *args, **kwargs):
        if not _is_agent(getattr(self, 'interaction', None)):
            return orig(self, *args, **kwargs)
        try:
            participants = self.interaction.get_participants(self.subject)
            sim = next(iter(participants)) if participants else None
            sim_info = getattr(sim, 'sim_info', sim)
            if sim_info is None:
                return orig(self, *args, **kwargs)
            if self.career_op == career_ops.CareerOps.JOIN_CAREER:
                num = Career.NUM_CAREERS_PER_DAY
                if self.interaction.debug or self.interaction.cheat:
                    num = 0
                Career.get_join_career_pb(sim_info, num_careers_to_show=num,
                                          default_career_selection_data=self._get_default_selection_data())
                return None
            if self.career_op == career_ops.CareerOps.QUIT_CAREER and                     len(sim_info.career_tracker.get_quittable_careers()) != 1:
                Career.get_quit_career_pb(sim_info)
                return None
        except Exception as e:
            log('agent career element failed, falling back to the game UI: %r' % (e,))
        return orig(self, *args, **kwargs)

    _do_behavior._ts4_bridge_wrapped = True
    CareerSelectElement._do_behavior = _do_behavior
    globals()['_element_hooked'] = True
    log('career panel suppression for agent-driven job searches installed')
    return True


def _install_tick():
    if install_hooks():
        try:
            from ts4_bridge import hooks
            hooks._tick_callbacks.pop('job_hooks', None)
        except Exception:
            pass


try:
    from ts4_bridge import hooks as _hooks
    _hooks.register_tick('job_hooks', _install_tick)
except Exception:
    pass


def _offer_for(info):
    offer = _offers.get(info.sim_id)
    if offer is None:
        return None
    # refresh live data: the career may already have been joined or the day's list replaced
    return offer


@op('jobs.offers', doc='Job openings the game last showed this sim through Find a Job (phone/computer), '
                       'or quit choices after Quit Job. Empty until the sim has looked for work.')
def jobs_offers(sim_id='active'):
    info = L.sim_info(sim_id)
    offer = _offer_for(info)
    current = [{'career': L.tuning_name(type(c)), 'user_level': int(c.user_level)}
               for c in info.career_tracker.careers.values()]
    if offer is None:
        return {'sim_id': info.sim_id, 'offers': None, 'current_careers': current,
                'hint': 'Have the sim use Find a Job on their phone (list_interactions target_id "phone", '
                        'query "career") or on a computer, then call this again.'}
    out = dict(offer)
    out['current_careers'] = current
    return out


@op('jobs.accept', doc='Take one of the offered jobs (career_id from jobs.offers). shift: ALL_DAY|MORNING|'
                       'EVENING|NIGHT for part-time jobs. For quit choices this quits that career.')
def jobs_accept(career_id, sim_id='active', track_id=None, shift='ALL_DAY'):
    g.require_zone()
    info = L.sim_info(sim_id)
    offer = _offer_for(info)
    if offer is None:
        raise OpError('no job offers for %s; use Find a Job on the phone or a computer first' % L.sim_name(info))
    match = None
    for c in offer['choices']:
        if c['career_id'] == int(career_id) and (track_id is None or c['track_id'] == int(track_id)):
            match = c
            break
    if match is None:
        raise OpError('career %s was not among the offered jobs' % career_id,
                      offered=[(c['career_id'], c.get('name') or c['career']) for c in offer['choices']])
    if not match['selectable']:
        raise OpError('that job cannot be taken right now', why_not=match.get('why_not'))
    from careers.career_enums import CareerShiftType
    from careers.career_ops import CareerOps
    from server_commands.career_commands import select_career
    shift_type = getattr(CareerShiftType, str(shift).upper(), CareerShiftType.ALL_DAY)
    reason = CareerOps.QUIT_CAREER if offer['kind'] == QUIT else CareerOps.JOIN_CAREER
    before = set(info.career_tracker.careers.keys())
    # exactly what the client sends when the player clicks a job in the panel
    select_career(sim_id=info.sim_id, career_instance_id=match['career_id'], track_id=match['track_id'],
                  level=match['level'], company_name_hash=None, reason=reason,
                  schedule_shift_type=shift_type, _connection=g.connection_id())
    after = set(info.career_tracker.careers.keys())
    _offers.pop(info.sim_id, None)
    result = {'requested': offer['kind'], 'career': match.get('name') or match['career'],
              'title': match.get('title'), 'careers_now': [L.tuning_name(type(c)) for c in info.career_tracker.careers.values()]}
    if offer['kind'] == JOIN and match['career_id'] not in after:
        result['note'] = ('The game asked for confirmation (for example about leaving a current job). '
                          'Answer the dialog with pending_dialogs / respond_dialog.')
    elif offer['kind'] == QUIT and match['career_id'] in after:
        result['note'] = 'Quit is waiting on a confirmation dialog.'
    result['changed'] = before != after
    return result
