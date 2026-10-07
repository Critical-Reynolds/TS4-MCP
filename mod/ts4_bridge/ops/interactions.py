"""Interaction enumeration, pushing and cancelling. Python 3.7."""
import services
from interactions import priority as _prio
from interactions.aop import AffordanceObjectPair
from interactions.context import InteractionContext, QueueInsertStrategy

from ts4_bridge.dispatch import op, OpError
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L
from ts4_bridge.ops import state as S

PRIORITIES = {'low': _prio.Priority.Low, 'high': _prio.Priority.High, 'critical': _prio.Priority.Critical}


def make_pick(target):
    """A PickInfo like the one the client sends when the player clicks `target`."""
    try:
        from server.pick_info import PickInfo, PickType
    except Exception:
        return None
    try:
        if getattr(target, 'is_sim', False):
            pick_type = PickType.PICK_SIM
        elif getattr(target, 'is_terrain', False):
            pick_type = PickType.PICK_TERRAIN
        else:
            pick_type = PickType.PICK_OBJECT
        return PickInfo(pick_type=pick_type, target=target, location=target.position,
                        routing_surface=target.routing_surface)
    except Exception:
        return None


def make_context(sim, priority='high', source='pie_menu', insert='next', target=None):
    """Interaction context. For pie-menu style enumeration we build it exactly like the client does
    (with a PickInfo), which is what makes socials such as sim_Chat and template affordances appear."""
    pri = PRIORITIES.get(str(priority).lower(), _prio.Priority.High)
    strategy = QueueInsertStrategy.NEXT if insert == 'next' else (
        QueueInsertStrategy.FIRST if insert == 'first' else QueueInsertStrategy.LAST)
    pick = make_pick(target) if target is not None else None
    if source == 'pie_menu':
        client = g.first_client()
        if client is not None and pick is not None:
            try:
                ctx = client.create_interaction_context(sim, pick=pick)
                ctx.priority = pri
                return ctx
            except Exception:
                pass
        return InteractionContext(sim, InteractionContext.SOURCE_PIE_MENU, pri, insert_strategy=strategy, pick=pick)
    return InteractionContext(sim, InteractionContext.SOURCE_SCRIPT_WITH_USER_INTENT, pri,
                              insert_strategy=strategy, pick=pick)


PHONE_TARGETS = ('phone', 'cellphone', 'cell_phone')


def is_phone(target_id):
    return isinstance(target_id, str) and target_id.lower() in PHONE_TARGETS


def phone_context(sim, priority='high'):
    """Context the game uses for the phone menu (interactions.phone_choices): client context, no pick."""
    client = g.first_client()
    if client is None:
        raise OpError('no client; the phone needs the active player client')
    ctx = client.create_interaction_context(sim)
    ctx.priority = PRIORITIES.get(str(priority).lower(), _prio.Priority.High)
    return ctx


def all_aops(sim, target, context, phone=False):
    """Everything the pie menu would show for `sim` clicking `target`: super affordances, their
    sub-menu mixers (Friendly/Funny/Romance socials live here under sim_Chat), and mixers of the
    sim's currently running interactions. Mixer AOPs carry a precomputed test result in
    ``_ts4_result`` and their parent super in ``_ts4_super``."""
    seen = set()

    def _emit(aop, result=None, parent=None):
        # picker rows share one affordance and differ only in their parameters (e.g. one easel row per
        # group of painting styles), so the parameters are part of the identity
        params = tuple(sorted((k, id(v)) for k, v in (aop.interaction_parameters or {}).items()
                              if k not in ('affordance_list',)))
        key = (L.guid(aop.affordance), getattr(aop.target, 'id', None), L.guid(parent) if parent is not None else None,
               params)
        if key in seen:
            return None
        seen.add(key)
        if result is not None:
            aop._ts4_result = result
        if parent is not None:
            aop._ts4_super = parent
        return aop

    source = sim.potential_phone_interactions(context) if phone else target.potential_interactions(context)
    for aop in source:
        try:
            result = aop.test(context)
        except Exception as e:
            result = None
        out = _emit(aop, result)
        if out is not None:
            yield out
        # Like ChoiceMenu.add_potential_aops: only expand the sub-menu of supers that can run
        # (or at least have a tooltip), otherwise mixers get attributed to an invalid parent
        # (e.g. sim_Toddler_Talk for an adult target) and die on enqueue.
        if result is None or (not result and not getattr(result, 'tooltip', None)):
            continue
        try:
            subs = aop.affordance.potential_pie_menu_sub_interactions_gen(aop.target, context, None,
                                                                           **aop.interaction_parameters)
            for mixer_aop, mixer_result in subs:
                if not result:
                    mixer_result = result
                out = _emit(mixer_aop, mixer_result, aop.affordance)
                if out is not None:
                    yield out
        except Exception:
            continue
    if phone:
        return
    # mixers of running SIs whose potential targets include `target` (same as the pie menu)
    try:
        from autonomy import content_sets
        for si in list(sim.si_state):
            try:
                potential = si.get_potential_mixer_targets()
                if not any(t is target or (getattr(t, 'is_part', False) and t.part_owner is target) for t in potential):
                    continue
                cs = content_sets.generate_content_set(sim, si.super_affordance, si, context, potential_targets=(target,),
                                                       include_failed_aops_with_tooltip=True,
                                                       check_posture_compatibility=True,
                                                       aop_kwargs=si.aop.interaction_parameters)
                for _, aop, test_result in cs:
                    out = _emit(aop, test_result, si.affordance)
                    if out is not None:
                        yield out
            except Exception:
                continue
    except Exception:
        pass


def aop_brief(aop, context, test=True):
    aff = aop.affordance
    d = {'affordance_id': L.guid(aff), 'name': L.tuning_name(aff)}
    parent = getattr(aop, '_ts4_super', None)
    if parent is not None:
        d['mixer_of'] = L.tuning_name(parent)
    pre = getattr(aop, '_ts4_result', None)
    try:
        d['target_id'] = aop.target.id if aop.target is not None else None
        if aop.target is not None:
            d['target'] = (L.sim_name(aop.target.sim_info) if getattr(aop.target, 'is_sim', False)
                           else type(aop.target).__name__)
    except Exception:
        pass
    try:
        d['display_name'] = L.loc(aff.get_name(target=aop.target, context=context))
    except Exception:
        try:
            d['display_name'] = L.loc(aff.display_name)
        except Exception:
            pass
    try:
        d['is_social'] = bool(getattr(aff, 'is_social', False))
    except Exception:
        pass
    try:
        cat = getattr(aff, 'category', None)
        if cat is not None:
            d['category'] = L.tuning_name(cat)
    except Exception:
        pass
    if test:
        try:
            res = pre if pre is not None else aop.test(context)
            d['runnable'] = bool(res)
            if not res:
                reason = getattr(res, 'reason', None) or getattr(res, 'tooltip', None)
                if reason:
                    d['reason'] = str(reason)[:160]
        except Exception as e:
            d['runnable'] = False
            d['reason'] = 'test raised %r' % (e,)
    return d


@op('interactions.list', doc="Interactions sim_id can perform on target_id ('self', an object id or a sim id). "
                             "query filters by name substring. only_runnable drops ones whose tests fail.")
def interactions_list(sim_id='active', target_id='self', query=None, only_runnable=True, limit=60,
                      include_autonomous=False):
    g.require_zone()
    info, sim = L.sim_instance(sim_id)
    phone = is_phone(target_id)
    target = sim if phone else L.resolve_target(sim, target_id)
    context = phone_context(sim) if phone else make_context(sim, target=target)
    seen = set()
    out = []
    q = str(query).lower() if query else None
    total = 0
    for aop in all_aops(sim, target, context, phone=phone):
        aff = aop.affordance
        key = (L.guid(aff), getattr(aop.target, 'id', None), L.guid(getattr(aop, '_ts4_super', None)),
               tuple(sorted((k, id(v)) for k, v in (aop.interaction_parameters or {}).items() if k != 'affordance_list')))
        if key in seen:
            continue
        seen.add(key)
        if not include_autonomous and not getattr(aff, 'allow_user_directed', True):
            continue
        name = L.tuning_name(aff)
        if q and q not in name.lower():
            continue
        total += 1
        d = aop_brief(aop, context, test=True)
        if only_runnable and not d.get('runnable'):
            continue
        out.append(d)
        if len(out) >= int(limit):
            break
    return {'sim_id': info.sim_id, 'target_id': 'phone' if phone else getattr(target, 'id', None), 'count': len(out),
            'scanned': total, 'interactions': out}


@op('interactions.push', doc='Queue an interaction by affordance id (guid64) or tuning name on a target. '
                             'priority: low|high|critical. Returns the enqueue result and the new queue.')
def interactions_push(affordance_id, sim_id='active', target_id='self', priority='high', insert='next'):
    g.require_zone()
    info, sim = L.sim_instance(sim_id)
    aff = L.affordance_by_id(affordance_id)
    if is_phone(target_id):
        # exactly what selecting an item in the phone menu does: test_and_execute the phone AOP
        context = phone_context(sim, priority)
        candidates = [a for a in all_aops(sim, sim, context, phone=True) if a.affordance is aff]
        candidates.sort(key=lambda a: 0 if getattr(a, '_ts4_result', None) else 1)
        if not candidates:
            raise OpError('%s is not on the phone right now' % L.tuning_name(aff))
        res = candidates[0].test_and_execute(context)
        out = _enqueue_result(res, sim, aff)
        out['via'] = 'phone'
        return out
    target = L.resolve_target(sim, target_id)
    context = make_context(sim, priority=priority, source='pie_menu', insert=insert, target=target)
    is_super = getattr(aff, 'is_super', True)
    if callable(is_super):
        is_super = is_super()
    if not is_super:
        # mixer: resolve it the way the pie menu does (content sets of the available supers / running SIs);
        # prefer a candidate whose precomputed test passed.
        candidates = [aop for aop in all_aops(sim, target, context) if aop.affordance is aff]
        candidates.sort(key=lambda a: 0 if getattr(a, '_ts4_result', None) else 1)
        if not candidates:
            raise OpError('%s is a mixer that is not currently available on this target' % L.tuning_name(aff))
        res = candidates[0].test_and_execute(context)
        out = _enqueue_result(res, sim, aff)
        out['mixer_of'] = L.tuning_name(getattr(candidates[0], '_ts4_super', None))
        return out
    aop = AffordanceObjectPair(aff, target, aff, None)
    test = aop.test(context)
    if not test:
        raise OpError('interaction test failed: %s' % (str(getattr(test, 'reason', test))[:300],),
                      affordance=L.tuning_name(aff))
    res = aop.test_and_execute(context)
    return _enqueue_result(res, sim, aff)


def _enqueue_result(res, sim, aff):
    out = {'ok': bool(res), 'affordance': L.tuning_name(aff)}
    try:
        inter = res.interaction
        if inter is not None:
            out['interaction_id'] = inter.id
            from ts4_bridge.ops import jobs
            jobs.mark_agent_interaction(inter)
    except Exception:
        pass
    if not res:
        try:
            out['reason'] = str(res.test_result.reason)[:300] if res.test_result else str(res)[:300]
        except Exception:
            out['reason'] = str(res)[:300]
    out['queue'] = S.queue_brief(sim)
    return out


@op('interactions.queue', doc='Running and queued interactions for a sim.')
def interactions_queue(sim_id='active'):
    g.require_zone()
    info, sim = L.sim_instance(sim_id)
    return S.queue_brief(sim)


@op('interactions.cancel', doc='Cancel one interaction by id, or everything queued/running when interaction_id is null.')
def interactions_cancel(sim_id='active', interaction_id=None, reason='ts4mcp cancel'):
    from interactions.interaction_finisher import FinishingType
    g.require_zone()
    info, sim = L.sim_instance(sim_id)
    cancelled = []
    targets = []
    try:
        targets.extend(list(sim.queue))
    except Exception:
        pass
    try:
        targets.extend(list(sim.si_state))
    except Exception:
        pass
    for i in targets:
        if interaction_id is not None and getattr(i, 'id', None) != int(interaction_id):
            continue
        try:
            if i.cancel(FinishingType.USER_CANCEL, reason):
                cancelled.append(S.interaction_brief(i))
        except Exception:
            continue
    if interaction_id is not None and not cancelled:
        raise OpError('interaction %s not found on %s' % (interaction_id, info.full_name))
    return {'cancelled': cancelled, 'queue': S.queue_brief(sim)}
