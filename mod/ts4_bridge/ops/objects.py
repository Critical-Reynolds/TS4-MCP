"""Object listing (buy-mode mutation ops come in Phase 5). Python 3.7."""
import services

from ts4_bridge.dispatch import op, OpError
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L


def _dist2(a, b):
    return (a.x - b.x) ** 2 + (a.z - b.z) ** 2


@op('objects.list', doc="Objects on the lot. query matches the tuning type name (e.g. 'fridge', 'bed', 'easel'). "
                        "near_sim_id/radius restricts to a circle around that sim. Sims are excluded unless include_sims.")
def objects_list(query=None, near_sim_id=None, radius=10.0, limit=80, include_sims=False, on_active_lot_only=True):
    g.require_zone()
    q = str(query).lower() if query else None
    center = None
    if near_sim_id is not None:
        _, sim = L.sim_instance(near_sim_id)
        center = sim.position
    out = []
    total = 0
    r2 = float(radius) ** 2
    for obj in services.object_manager().get_all():
        try:
            if getattr(obj, 'is_sim', False) and not include_sims:
                continue
            if q and q not in type(obj).__name__.lower():
                continue
            if on_active_lot_only:
                try:
                    if not obj.is_on_active_lot():
                        continue
                except Exception:
                    pass
            if center is not None and _dist2(obj.position, center) > r2:
                continue
            total += 1
            if len(out) < int(limit):
                d = L.object_brief(obj)
                if center is not None:
                    d['distance'] = round(_dist2(obj.position, center) ** 0.5, 2)
                out.append(d)
        except Exception:
            continue
    if center is not None:
        out.sort(key=lambda d: d.get('distance', 0))
    return {'count': len(out), 'total_matching': total, 'objects': out}


@op('objects.get', doc='Details of one object: type, definition, position, owner, value, states, inventory, slots.')
def objects_get(object_id):
    g.require_zone()
    obj = L.game_object(object_id)
    d = L.object_brief(obj)
    try:
        d['owner_household_id'] = obj.get_household_owner_id()
    except Exception:
        pass
    try:
        d['is_outside'] = bool(obj.is_outside)
    except Exception:
        pass
    try:
        d['parent_id'] = obj.parent.id if obj.parent is not None else None
    except Exception:
        pass
    try:
        sc = obj.state_component
        if sc is not None:
            d['states'] = {L.tuning_name(k): L.tuning_name(v) for k, v in sc._states.items()}
    except Exception:
        pass
    try:
        inv = obj.inventory_component
        if inv is not None:
            d['inventory'] = [L.object_brief(o) for o in list(inv)[:40]]
    except Exception:
        pass
    try:
        d['super_affordances'] = [L.tuning_name(a) for a in obj._super_affordances][:60]
    except Exception:
        pass
    return d
