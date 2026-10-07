"""Create-a-Sim from script: new sims, outfits, appearance basics. Python 3.7."""
import services
from sims.sim_info_types import Age, Gender, Species
from sims.sim_spawner import SimCreator, SimSpawner
from sims4.resources import Types

from ts4_bridge.dispatch import op, OpError
from ts4_bridge.log import log
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L


def _enum(cls, value, default):
    if value is None:
        return default
    if isinstance(value, str):
        v = getattr(cls, value.upper().replace(' ', ''), None)
        if v is None:
            # allow aliases
            aliases = {'YA': 'YOUNGADULT', 'YOUNG_ADULT': 'YOUNGADULT', 'MAN': 'MALE', 'WOMAN': 'FEMALE'}
            v = getattr(cls, aliases.get(value.upper(), ''), None)
        if v is None:
            raise OpError('unknown %s %r; options: %s' % (cls.__name__, value, [e.name for e in cls]))
        return v
    try:
        return cls(int(value))
    except Exception:
        raise OpError('bad %s %r' % (cls.__name__, value))


def _trait(name_or_id):
    mgr = services.get_instance_manager(Types.TRAIT)
    try:
        t = mgr.get(int(name_or_id))
        if t is not None:
            return t
    except Exception:
        pass
    for t in mgr.types.values():
        if L.tuning_name(t) == str(name_or_id):
            return t
    raise OpError('unknown trait %r' % (name_or_id,))


@op('cas.create_sim', doc="Create a sim with random (deterministic) genetics. gender: MALE|FEMALE; age: "
                          "BABY|INFANT|TODDLER|CHILD|TEEN|YOUNGADULT|ADULT|ELDER; species: HUMAN|DOG|CAT|FOX|HORSE. "
                          "traits: tuning names. Adds to household_id (default: active household) and spawns "
                          "them on the lot when spawn=true.")
def cas_create_sim(first_name, last_name, gender=None, age='YOUNGADULT', species='HUMAN', traits=None,
                   household_id=None, spawn=True, make_selectable=True):
    g.require_zone()
    hh = services.household_manager().get(int(household_id)) if household_id else services.active_household()
    if hh is None:
        raise OpError('no household')
    trait_objs = tuple(_trait(t) for t in (traits or []))
    creator = SimCreator(gender=_enum(Gender, gender, None) if gender else None,
                         age=_enum(Age, age, Age.YOUNGADULT),
                         species=_enum(Species, species, Species.HUMAN),
                         first_name=str(first_name), last_name=str(last_name), traits=trait_objs)
    result = SimSpawner.create_sim_infos((creator,), household=hh, generate_deterministic_sim=True,
                                         creation_source='ts4mcp')
    sim_infos = result[0] if isinstance(result, tuple) else result
    if not sim_infos:
        raise OpError('create_sim_infos returned nothing')
    info = sim_infos[0]
    out = {'sim_id': info.sim_id, 'name': L.sim_name(info), 'household_id': hh.id, 'size': int(hh.household_size)}
    client = g.first_client()
    if make_selectable and client is not None and hh is services.active_household():
        try:
            client.add_selectable_sim_info(info)
            out['selectable'] = True
        except Exception as e:
            out['selectable_error'] = repr(e)
    if spawn:
        try:
            active = services.active_sim_info()
            sim = active.get_sim_instance() if active is not None else None
            pos = sim.position if sim is not None else services.current_zone().lot.center
            SimSpawner.spawn_sim(info, sim_position=pos)
            out['spawned'] = True
        except Exception as e:
            out['spawn_error'] = repr(e)
            log('spawn_sim failed: %r' % (e,))
    return out


@op('cas.outfits', doc='List outfit categories/indices a sim has and the current one.')
def cas_outfits(sim_id='active'):
    g.require_zone()
    info = L.sim_info(sim_id)
    from sims.outfits.outfit_enums import OutfitCategory
    out = {'current': None, 'outfits': {}}
    try:
        cat, idx = info.get_current_outfit()
        out['current'] = {'category': L.enum_name(cat, OutfitCategory), 'index': int(idx)}
    except Exception:
        pass
    for cat in OutfitCategory:
        try:
            n = info.get_outfits_in_category(cat)
            if n:
                out['outfits'][cat.name] = len(n) if hasattr(n, '__len__') else n
        except Exception:
            continue
    return out


@op('cas.set_outfit', doc='Switch a sim to an outfit category (EVERYDAY|FORMAL|ATHLETIC|SLEEP|PARTY|SWIMWEAR|HOTWEATHER|COLDWEATHER) and index.')
def cas_set_outfit(category='EVERYDAY', index=0, sim_id='active'):
    g.require_zone()
    info = L.sim_info(sim_id)
    from sims.outfits.outfit_enums import OutfitCategory
    cat = _enum(OutfitCategory, category, OutfitCategory.EVERYDAY)
    info.set_current_outfit((cat, int(index)))
    sim = info.get_sim_instance()
    if sim is not None:
        try:
            sim.set_current_outfit((cat, int(index)))
        except Exception:
            pass
    return {'sim_id': info.sim_id, 'category': cat.name, 'index': int(index)}


@op('cas.remove_sim', doc='Remove a sim from the game permanently (destructive). Requires confirm=true.')
def cas_remove_sim(sim_id, confirm=False):
    g.require_zone()
    if not confirm:
        raise OpError('pass confirm=true to permanently delete a sim')
    info = L.sim_info(sim_id)
    name = L.sim_name(info)
    client = g.first_client()
    try:
        if client is not None:
            client.remove_selectable_sim_by_id(info.sim_id)
    except Exception:
        pass
    sim = info.get_sim_instance()
    if sim is not None:
        sim.destroy(source=sim, cause='ts4mcp remove_sim')
    hh = info.household
    info.remove_permanently(household=hh)
    return {'removed': name, 'sim_id': info.sim_id}
