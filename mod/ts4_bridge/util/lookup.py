"""Lookups and compact descriptions of game entities. Game thread only. Python 3.7."""
import services
import sims4.resources

from ts4_bridge.dispatch import OpError
from ts4_bridge.util import game as g


def sim_info(sim_id):
    """SimInfo by id, or the active sim for None/'active'."""
    if sim_id in (None, 'active', 'me'):
        info = services.active_sim_info()
        if info is None:
            raise OpError('no active sim')
        return info
    try:
        sim_id = int(sim_id)
    except (TypeError, ValueError):
        raise OpError('sim_id must be an integer id or "active"')
    info = services.sim_info_manager().get(sim_id)
    if info is None:
        raise OpError('unknown sim id %d' % sim_id)
    return info


def sim_instance(sim_id):
    info = sim_info(sim_id)
    sim = info.get_sim_instance()
    if sim is None:
        raise OpError('%s (%d) is not instanced on this lot' % (info.full_name, info.sim_id), sim_id=info.sim_id)
    return info, sim


def game_object(obj_id):
    try:
        obj_id = int(obj_id)
    except (TypeError, ValueError):
        raise OpError('object id must be an integer')
    obj = services.object_manager().get(obj_id)
    if obj is None:
        # inventory objects live in the inventory manager
        try:
            obj = services.inventory_manager().get(obj_id)
        except Exception:
            obj = None
    if obj is None:
        raise OpError('unknown object id %d' % obj_id)
    return obj


def resolve_target(sim, target_id):
    """Target for an interaction: None/'self' -> the sim; otherwise an object or sim id."""
    if target_id in (None, 'self', 'me'):
        return sim
    try:
        tid = int(target_id)
    except (TypeError, ValueError):
        raise OpError('target_id must be an integer id or "self"')
    obj = services.object_manager().get(tid)
    if obj is not None:
        return obj
    info = services.sim_info_manager().get(tid)
    if info is not None:
        inst = info.get_sim_instance()
        if inst is None:
            raise OpError('target sim %d is not on this lot' % tid)
        return inst
    raise OpError('unknown target id %d' % tid)


def affordance_by_id(affordance_id):
    mgr = services.get_instance_manager(sims4.resources.Types.INTERACTION)
    aff = None
    try:
        aff = mgr.get(int(affordance_id))
    except Exception:
        aff = None
    if aff is None:
        # allow tuning names too
        for a in mgr.types.values():
            if getattr(a, '__name__', None) == str(affordance_id):
                aff = a
                break
    if aff is None:
        raise OpError('unknown interaction id/name %r' % (affordance_id,))
    return aff


def loc(factory_or_string):
    """Compact description of a localized string: its hash (resolved to text server-side)."""
    if factory_or_string is None:
        return None
    sid = getattr(factory_or_string, '_string_id', None)
    if sid is not None:
        return {'hash': int(sid)} if int(sid) else None
    h = getattr(factory_or_string, 'hash', None)
    if h is not None:
        if not int(h):
            return None
        try:
            tokens = []
            for t in getattr(factory_or_string, 'tokens', []):
                sim_id = int(getattr(t, 'sim_id', 0) or 0)
                if sim_id:
                    tok = {'sim_id': sim_id}
                    try:
                        info = services.sim_info_manager().get(sim_id)
                        if info is not None:
                            tok['name'] = sim_name(info)
                            tok['first_name'] = info.first_name or ''
                    except Exception:
                        pass
                    tokens.append(tok)
                elif t.HasField('text_string'):
                    tokens.append(loc(t.text_string))
                elif t.HasField('number'):
                    tokens.append({'number': t.number})
                elif t.HasField('raw_text'):
                    tokens.append({'text': t.raw_text})
                else:
                    tokens.append(None)
            return {'hash': int(h), 'tokens': tokens}
        except Exception:
            return {'hash': int(h)}
    return None


def pos(obj):
    try:
        p = obj.position
        return {'x': round(float(p.x), 2), 'y': round(float(p.y), 2), 'z': round(float(p.z), 2)}
    except Exception:
        return None


def level(obj):
    try:
        return int(obj.level)
    except Exception:
        try:
            return int(obj.routing_surface.secondary_id)
        except Exception:
            return None


def tuning_name(cls):
    return getattr(cls, '__name__', None) or repr(cls)


def enum_name(value, enum_cls=None):
    if value is None:
        return None
    name = getattr(value, 'name', None)
    if isinstance(name, str):
        return name
    if enum_cls is not None:
        try:
            return enum_cls(int(value)).name
        except Exception:
            pass
    s = str(value)
    return s.split('.')[-1] if '.' in s else s


def species_name(value):
    try:
        from sims.sim_info_types import Species
        return enum_name(value, Species)
    except Exception:
        return enum_name(value)


def sim_name(info):
    for attr in ('full_name',):
        try:
            v = getattr(info, attr)
            if v:
                return v
        except Exception:
            pass
    first = last = ''
    for src in (info, getattr(info, '_base', None)):
        if src is None:
            continue
        try:
            first = first or (src.first_name or '')
            last = last or (src.last_name or '')
        except Exception:
            pass
    name = ('%s %s' % (first, last)).strip()
    return name or ('Sim %d' % info.sim_id)


def guid(cls):
    return getattr(cls, 'guid64', None)


def object_brief(obj):
    d = {
        'id': obj.id,
        'type': type(obj).__name__,
        'def_id': getattr(getattr(obj, 'definition', None), 'id', None),
        'pos': pos(obj),
        'level': level(obj),
    }
    try:
        d['catalog_name'] = {'hash': int(obj.catalog_name)}
    except Exception:
        pass
    try:
        d['value'] = int(obj.current_value)
    except Exception:
        pass
    if getattr(obj, 'is_sim', False):
        d['is_sim'] = True
        try:
            d['name'] = obj.sim_info.full_name
        except Exception:
            pass
    return d


def sim_brief(info):
    inst = None
    try:
        inst = info.get_sim_instance()
    except Exception:
        pass
    d = {
        'sim_id': info.sim_id,
        'name': sim_name(info),
        'age': enum_name(getattr(info, 'age', None)),
        'gender': enum_name(getattr(info, 'gender', None)),
        'species': species_name(getattr(info, 'species', None)),
        'household_id': info.household_id,
        'is_selectable': bool(info.is_selectable),
        'is_npc': bool(info.is_npc),
        'instanced': inst is not None,
    }
    try:
        d['is_dead'] = bool(info.is_dead)
    except Exception:
        pass
    if inst is not None:
        d['pos'] = pos(inst)
        d['level'] = level(inst)
        try:
            d['on_active_lot'] = bool(inst.is_on_active_lot())
        except Exception:
            pass
    try:
        hh = info.household
        if hh is not None:
            d['household_name'] = hh.name
    except Exception:
        pass
    return d
