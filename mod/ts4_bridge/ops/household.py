"""Household, careers, skills, traits, relationships, aging, world/lots, travel, saving. Python 3.7."""
import services
import sims4.resources
from sims4.resources import Types

from ts4_bridge import events
from ts4_bridge.dispatch import op, OpError
from ts4_bridge.log import log
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L


def _instance(types, id_or_name):
    mgr = services.get_instance_manager(types)
    inst = None
    try:
        inst = mgr.get(int(id_or_name))
    except Exception:
        pass
    if inst is None:
        for t in mgr.types.values():
            if getattr(t, '__name__', None) == str(id_or_name):
                inst = t
                break
    if inst is None:
        raise OpError('unknown %s %r' % (types.name if hasattr(types, 'name') else types, id_or_name))
    return inst


# ---------------------------------------------------------------- funds / household
@op('household.funds', doc='Household funds; delta adds/removes simoleons (cheat-like).')
def household_funds(delta=0, household_id=None):
    g.require_zone()
    hh = services.household_manager().get(int(household_id)) if household_id else services.active_household()
    if hh is None:
        raise OpError('no household')
    if delta:
        from protocolbuffers import Consts_pb2
        if int(delta) > 0:
            hh.funds.add(int(delta), Consts_pb2.TELEMETRY_MONEY_CHEAT, count_as_earnings=False)
        else:
            hh.funds.try_remove(-int(delta), Consts_pb2.TELEMETRY_MONEY_CHEAT, require_full_amount=False)
    return {'household_id': hh.id, 'name': hh.name, 'funds': int(hh.funds.money)}


@op('household.list', doc='All households in the save with size, funds and home lot.')
def household_list(limit=100, player_only=False):
    out = []
    for hh in services.household_manager().get_all():
        try:
            if player_only and not hh.is_player_household:
                continue
            out.append({'household_id': hh.id, 'name': hh.name, 'size': int(hh.household_size),
                        'funds': int(hh.funds.money), 'home_zone_id': hh.home_zone_id,
                        'is_player': bool(hh.is_player_household),
                        'members': [L.sim_name(si) for si in hh.sim_info_gen()]})
        except Exception:
            continue
        if len(out) >= int(limit):
            break
    return out


@op('household.move_sim', doc='Move a sim into another household (to_household_id, or the active household).')
def household_move_sim(sim_id, to_household_id=None, make_selectable=True):
    g.require_zone()
    from sims.household_enums import HouseholdChangeOrigin
    info = L.sim_info(sim_id)
    target = services.household_manager().get(int(to_household_id)) if to_household_id else services.active_household()
    if target is None:
        raise OpError('target household not found')
    old = info.household
    if old is target:
        return {'ok': True, 'note': 'already in household'}
    if old is not None:
        old.remove_sim_info(info, destroy_if_empty_household=True)
    target.add_sim_info_to_household(info, reason=HouseholdChangeOrigin.UNKNOWN)
    client = g.first_client()
    if make_selectable and client is not None and target is services.active_household():
        try:
            client.add_selectable_sim_info(info)
        except Exception as e:
            log('add_selectable failed: %r' % (e,))
    return {'ok': True, 'sim_id': info.sim_id, 'household_id': target.id, 'size': int(target.household_size)}


@op('household.move_into_zone', doc='Move a household into a residential zone (buy/move house). furnished keeps lot furniture.')
def household_move_into_zone(zone_id, household_id=None, furnished=True):
    g.require_zone()
    hh = services.household_manager().get(int(household_id)) if household_id else services.active_household()
    if hh is None:
        raise OpError('no household')
    _require_known_zone(zone_id)
    venue = services.venue_service().get_venue_tuning(int(zone_id))
    if venue is None or not (venue.is_residential or getattr(venue, 'is_university_housing', False)):
        raise OpError('target zone is not residential')
    out = g.run_cheat('household.move_into_zone %d %d %s' % (int(zone_id), hh.id, 'true' if furnished else 'false'))
    return {'requested': True, 'output': out, 'note': 'The game processes the move asynchronously; '
                                                      'check look/world.lots afterwards.'}


# ---------------------------------------------------------------- careers / skills / traits / relationships
@op('career.list_available', doc='Career tuning names that can be joined (matching query).')
def career_list_available(query=None, limit=60):
    mgr = services.get_instance_manager(Types.CAREER)
    q = str(query).lower() if query else None
    out = []
    for c in mgr.types.values():
        name = L.tuning_name(c)
        if q and q not in name.lower():
            continue
        d = {'name': name, 'id': L.guid(c)}
        try:
            d['display_name'] = L.loc(c.start_track.career_name if hasattr(c, 'start_track') else None)
        except Exception:
            pass
        out.append(d)
        if len(out) >= int(limit):
            break
    return out


@op('career.action', doc="action: join|quit|promote|demote|add_pto|retire. career is a tuning name/id (needed for join).")
def career_action(action, sim_id='active', career=None, levels=1):
    g.require_zone()
    info = L.sim_info(sim_id)
    tracker = info.career_tracker
    def _current():
        return [{'name': L.tuning_name(type(x)), 'level': int(x.level), 'user_level': int(x.user_level)}
                for x in tracker.careers.values()]

    if action == 'join':
        if career is None:
            raise OpError('career required')
        c = _instance(Types.CAREER, career)
        if c.guid64 in tracker.careers:
            return {'ok': True, 'note': 'already in that career', 'careers': _current()}
        # same call the careers.add_career cheat makes, without needing testingcheats
        tracker.add_career(c(info))
        return {'ok': c.guid64 in tracker.careers, 'careers': _current()}
    if action == 'quit':
        if career:
            c = _instance(Types.CAREER, career)
            if hasattr(tracker, 'remove_career'):
                tracker.remove_career(c.guid64)
            else:
                tracker.quit_quittable_careers()
        else:
            tracker.quit_quittable_careers()
        return {'ok': True, 'careers': _current()}
    if action in ('promote', 'demote'):
        if not tracker.careers:
            raise OpError('sim has no career')
        target = None
        if career:
            c = _instance(Types.CAREER, career)
            target = tracker.careers.get(c.guid64)
        if target is None:
            target = list(tracker.careers.values())[0]
        for _ in range(int(levels)):
            if action == 'promote':
                target.promote()
            else:
                target.demote()
        return {'ok': True, 'career': L.tuning_name(type(target)), 'level': int(target.level), 'user_level': int(target.user_level)}
    if action == 'retire':
        for c in list(tracker.careers.values()):
            if hasattr(tracker, 'retire_career'):
                tracker.retire_career(c.guid64)
        return {'ok': True}
    raise OpError('unknown action %r' % action)


@op('skill.set', doc='Set a skill level (1-10) for a sim by skill tuning name/id (e.g. statistic_Skill_AdultMajor_Painting).')
def skill_set(skill, level, sim_id='active'):
    g.require_zone()
    info = L.sim_info(sim_id)
    s = _instance(Types.STATISTIC, skill)
    tracker = info.get_tracker(s)
    stat = tracker.get_statistic(s, add=True)
    if hasattr(stat, 'set_user_value'):
        stat.set_user_value(int(level))
    else:
        from statistics.skill import Skill
        value = s.convert_from_user_value(int(level)) if hasattr(s, 'convert_from_user_value') else int(level)
        tracker.set_value(s, value)
    return {'skill': L.tuning_name(s), 'level': int(stat.get_user_value())}


@op('skill.list_available', doc='Skill tuning names (matching query).')
def skill_list_available(query=None, limit=80):
    mgr = services.get_instance_manager(Types.STATISTIC)
    q = str(query).lower() if query else None
    out = []
    for s in mgr.types.values():
        name = L.tuning_name(s)
        if 'skill' not in name.lower():
            continue
        if q and q not in name.lower():
            continue
        d = {'name': name, 'id': L.guid(s)}
        try:
            d['display_name'] = L.loc(s.stat_name)
        except Exception:
            pass
        out.append(d)
        if len(out) >= int(limit):
            break
    return out


@op('motive.set', doc='Set a motive (Hunger, Energy, Bladder, Fun, Social, Hygiene) to a value in -100..100, or all of them.')
def motive_set(value, motive='all', sim_id='active'):
    g.require_zone()
    info = L.sim_info(sim_id)
    changed = {}
    for c in info.commodity_tracker.get_all_commodities():
        name = L.tuning_name(c.stat_type)
        if 'motive' not in name.lower():
            continue
        short = name.split('_')[-1]
        if motive != 'all' and short.lower() != str(motive).lower():
            continue
        c.set_value(float(value))
        changed[short] = round(float(c.get_value()), 1)
    if not changed:
        raise OpError('no motive matched %r' % motive)
    return changed


@op('trait.action', doc="action: add|remove. trait is a tuning name/id (e.g. trait_Ambitious).")
def trait_action(action, trait, sim_id='active'):
    g.require_zone()
    info = L.sim_info(sim_id)
    t = _instance(Types.TRAIT, trait)
    if action == 'add':
        ok = info.add_trait(t)
    elif action == 'remove':
        ok = info.remove_trait(t)
    else:
        raise OpError('action must be add|remove')
    return {'ok': bool(ok), 'traits': [L.tuning_name(x) for x in info.trait_tracker.equipped_traits]}


@op('trait.list_available', doc='Trait tuning names (matching query), with type.')
def trait_list_available(query=None, trait_type=None, limit=80):
    mgr = services.get_instance_manager(Types.TRAIT)
    q = str(query).lower() if query else None
    out = []
    for t in mgr.types.values():
        name = L.tuning_name(t)
        if q and q not in name.lower():
            continue
        ttype = getattr(getattr(t, 'trait_type', None), 'name', None)
        if trait_type and ttype != trait_type:
            continue
        d = {'name': name, 'id': L.guid(t), 'type': ttype}
        try:
            d['display_name'] = L.loc(t.display_name)
        except Exception:
            pass
        out.append(d)
        if len(out) >= int(limit):
            break
    return out


@op('relationship.modify', doc='Add friendship/romance score between two sims (delta -100..100). track: friendship|romance.')
def relationship_modify(sim_id, other_sim_id, delta, track='friendship'):
    g.require_zone()
    info = L.sim_info(sim_id)
    other = L.sim_info(other_sim_id)
    tracker = info.relationship_tracker
    track_type = None
    if track == 'romance':
        try:
            from relationships.relationship_track import RelationshipTrack
            track_type = RelationshipTrack.ROMANCE_TRACK
        except Exception:
            track_type = None
    if track_type is None:
        tracker.add_relationship_score(other.sim_id, float(delta))
        score = tracker.get_relationship_score(other.sim_id)
    else:
        tracker.add_relationship_score(other.sim_id, float(delta), track=track_type)
        score = tracker.get_relationship_score(other.sim_id, track=track_type)
    return {'sim_id': info.sim_id, 'other_sim_id': other.sim_id, 'track': track, 'score': round(float(score), 1)}


@op('sim.age_up', doc='Advance a sim to the next age stage (uses the aging system).')
def sim_age_up(sim_id='active'):
    g.require_zone()
    info = L.sim_info(sim_id)
    before = L.enum_name(info.age)
    try:
        info.advance_age()
    except Exception:
        g.run_cheat('sims.age_up %d' % info.sim_id)
    return {'sim_id': info.sim_id, 'before': before, 'after': L.enum_name(info.age)}


@op('sim.rename', doc='Set first/last name.')
def sim_rename(sim_id='active', first_name=None, last_name=None):
    g.require_zone()
    info = L.sim_info(sim_id)
    if first_name is not None:
        info.first_name = str(first_name)
    if last_name is not None:
        info.last_name = str(last_name)
    try:
        info.resend_sim_info()  # may not exist; fall back silently
    except Exception:
        pass
    return {'sim_id': info.sim_id, 'name': L.sim_name(info)}


# ---------------------------------------------------------------- world / travel / save
@op('world.lots', doc='Every lot (zone) in the save: name, world, venue, owner household, residential?.')
def world_lots(residential_only=False, unowned_only=False, world_id=None, limit=200):
    ps = services.get_persistence_service()
    vs = services.venue_service()
    out = []
    for proto in ps.zone_proto_buffs_gen():
        try:
            if world_id is not None and int(proto.world_id) != int(world_id):
                continue
            venue = None
            try:
                venue = vs.get_venue_tuning(proto.zone_id)
            except Exception:
                pass
            is_res = bool(venue is not None and venue.is_residential)
            if residential_only and not is_res:
                continue
            owner = int(proto.household_id) if proto.HasField('household_id') else 0
            if unowned_only and owner:
                continue
            d = {'zone_id': int(proto.zone_id), 'name': proto.name, 'world_id': int(proto.world_id),
                 'venue': L.tuning_name(venue) if venue is not None else None, 'residential': is_res,
                 'owner_household_id': owner}
            try:
                d['lot_description_id'] = int(proto.lot_description_id)
            except Exception:
                pass
            try:
                owner_hh = services.household_manager().get(owner) if owner else None
                d['owner'] = owner_hh.name if owner_hh else None
            except Exception:
                pass
            out.append(d)
        except Exception:
            continue
        if len(out) >= int(limit):
            break
    return {'count': len(out), 'current_zone_id': services.current_zone_id(), 'lots': out}


def _require_known_zone(zone_id):
    """A zone id missing from the save makes the client load forever, so refuse it up front."""
    if services.get_persistence_service().get_zone_proto_buff(int(zone_id)) is None:
        raise OpError('zone %s is not in this save (mistyped or rounded id? pass ids as strings; '
                      'see list_lots)' % (zone_id,))


@op('world.lot_value',doc='Furnished and unfurnished value of a lot (zone).')
def world_lot_value(zone_id):
    import build_buy
    try:
        return {'zone_id': int(zone_id), 'furnished': int(build_buy.get_lot_value(int(zone_id), True)),
                'unfurnished': int(build_buy.get_lot_value(int(zone_id), False))}
    except Exception as e:
        raise OpError('lot value unavailable: %r' % (e,))


@op('world.travel', doc='Travel the active household (or given sims) to another lot. Triggers a loading screen; '
                        'the connection survives, poll look until zone.loaded.')
def world_travel(zone_id, sim_ids=None):
    g.require_zone()
    _require_known_zone(zone_id)
    if int(zone_id) == services.current_zone_id():
        raise OpError('already on that lot')
    if sim_ids:
        infos = [L.sim_info(s) for s in sim_ids]
    else:
        infos = [services.active_sim_info()]
    lead = infos[0]
    for extra in infos[1:]:
        try:
            extra.inject_into_inactive_zone(int(zone_id))
        except Exception as e:
            log('inject_into_inactive_zone failed for %s: %r' % (extra.sim_id, e))
    events.emit('zone.loading', {'to_zone_id': int(zone_id)}, sim_ts=g.sim_now_string())
    lead.send_travel_switch_to_zone_op(zone_id=int(zone_id))
    return {'requested': True, 'zone_id': int(zone_id), 'sim_ids': [i.sim_id for i in infos]}


@op('persistence.save', doc='Save the game to the current slot (or a new slot). Fails while saving is locked.')
def persistence_save(new_slot=False, with_autosave=False):
    g.require_zone()
    ps = services.get_persistence_service()
    if ps.is_save_locked():
        raise OpError('saving is locked right now (loading, build mode or a blocking interaction)')
    cmd = 'persistence.save_to_new_slot' if new_slot else (
        'persistence.save_game_with_autosave' if with_autosave else 'persistence.save_game')
    out = g.run_cheat(cmd)
    return {'requested': True, 'command': cmd, 'output': out, 'note': 'save.done event fires when finished'}


@op('persistence.info', doc='Save slot info and whether saving is locked.')
def persistence_info():
    ps = services.get_persistence_service()
    out = {'save_locked': bool(ps.is_save_locked())}
    try:
        slot = ps.get_save_slot_proto_buff()
        out['slot_id'] = int(slot.slot_id)
        out['slot_name'] = slot.slot_name
    except Exception:
        pass
    return out
