"""Buy mode: catalog index, placement tests, buy/move/sell objects, lot layout. Python 3.7.

Architecture (walls/floors/roofs) is client-side and not reachable from here; see lot.layout for
the read-only room/wall geometry the agent can plan against.
"""
import json
import os
import time

import services
import sims4.math
import sims4.resources
from sims4.math import Vector3, Transform, Location
import routing
import build_buy
from objects.system import create_object
from protocolbuffers import Consts_pb2

from ts4_bridge import paths
from ts4_bridge.dispatch import op, OpError
from ts4_bridge.log import log
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L

CATALOG_FILE = os.path.join(paths.MOD_DATA_DIR, 'catalog.json')
# State survives source hot-reloads (bridge.reload re-executes this module into the same globals).
_catalog_job = globals().setdefault('_catalog_job', {'status': 'idle', 'done': 0, 'total': 0, 'started': None,
                                                     'finished': None, 'error': None})
_catalog_keys = globals().setdefault('_catalog_keys', None)
_catalog_rows = globals().setdefault('_catalog_rows', None)
_catalog_cursor = globals().setdefault('_catalog_cursor', 0)
CATALOG_BATCH = 400


# ---------------------------------------------------------------- geometry helpers
def surface(level=0):
    return routing.SurfaceIdentifier(services.current_zone_id(), int(level), routing.SurfaceType.SURFACETYPE_WORLD)


def ground_height(x, z, level=0):
    try:
        return float(services.terrain_service.terrain_object().get_routing_surface_height_at(float(x), float(z), surface(level)))
    except Exception:
        try:
            import terrain
            return float(terrain.get_lot_level_height(float(x), float(z), int(level), services.current_zone_id(),
                                                      routing.SurfaceType.SURFACETYPE_WORLD))
        except Exception:
            return 0.0


def make_location(x, z, level=0, rotation_deg=0.0, y=None, height=None):
    """y is absolute; height is metres above the floor at x,z (ceiling/wall objects need one)."""
    if y is None:
        y = ground_height(x, z, level) + float(height or 0.0)
    position = Vector3(float(x), float(y), float(z))
    orientation = sims4.math.angle_to_yaw_quaternion(float(rotation_deg) * 3.141592653589793 / 180.0)
    return Location(Transform(position, orientation), surface(level))


def tag_names(tags):
    out = []
    try:
        import tag as tag_mod
        for t in tags:
            try:
                out.append(tag_mod.Tag(int(t)).name)
            except Exception:
                out.append(str(int(t)))
    except Exception:
        out = [str(t) for t in tags]
    return out


def definition_brief(definition):
    d = {'def_id': definition.id, 'price': int(definition.price)}
    try:
        d['name'] = {'hash': int(build_buy.get_object_catalog_name(definition.id))}
    except Exception:
        pass
    try:
        d['tuning'] = L.tuning_name(definition.cls)
    except Exception:
        pass
    try:
        d['tags'] = tag_names(definition.get_tags())[:30]
    except Exception:
        pass
    return d


# ---------------------------------------------------------------- catalog index (job)
@op('catalog.build_index', doc='Start (or restart) building the buy-catalog index (def_id, name hash, price, tags) '
                               'into mod_data/ts4_bridge/catalog.json. Runs in the background over many ticks. '
                               'Poll catalog.status.')
def catalog_build_index(force=False):
    global _catalog_keys, _catalog_rows, _catalog_cursor
    if _catalog_job['status'] == 'running' and not force:
        return dict(_catalog_job)
    if os.path.exists(CATALOG_FILE) and not force:
        return {'status': 'exists', 'file': CATALOG_FILE, 'size': os.path.getsize(CATALOG_FILE)}
    keys = list(sims4.resources.list(type=sims4.resources.Types.OBJECTDEFINITION))
    _catalog_keys = keys
    _catalog_rows = {}
    _catalog_cursor = 0
    _catalog_job.update({'status': 'running', 'done': 0, 'total': len(keys), 'started': time.time(),
                         'finished': None, 'error': None})
    return dict(_catalog_job)


def catalog_tick():
    """Called by the pump every tick while a build is running."""
    global _catalog_cursor
    if _catalog_job['status'] != 'running':
        return
    mgr = services.definition_manager()
    end = min(_catalog_cursor + CATALOG_BATCH, len(_catalog_keys))
    t0 = time.perf_counter()
    for key in _catalog_keys[_catalog_cursor:end]:
        def_id = key.instance
        try:
            name_hash = int(build_buy.get_object_catalog_name(def_id))
        except Exception:
            name_hash = 0
        if not name_hash:
            continue  # not a catalog object (spawners, debug objects...)
        row = {'n': name_hash}
        try:
            import sims4.common
            pack = build_buy.get_object_pack_by_key(key.type, key.group, key.instance)
            row['a'] = bool(sims4.common.is_available_pack(pack))
            row['k'] = int(pack)
        except Exception:
            pass
        try:
            row['t'] = [int(t) for t in build_buy.get_object_all_tags(def_id)][:40]
        except Exception:
            pass
        try:
            row['f'] = int(build_buy.get_object_buy_category_flags(def_id))
        except Exception:
            pass
        try:
            definition = mgr.get(def_id, pack_safe=True, get_fallback_definition_id=False)
            if definition is not None:
                row['p'] = int(definition.price)
                row['c'] = L.tuning_name(definition.cls)
        except Exception:
            pass
        _catalog_rows[str(def_id)] = row
        if time.perf_counter() - t0 > 0.012:
            end = _catalog_keys.index(key) + 1
            break
    _catalog_cursor = end
    _catalog_job['done'] = _catalog_cursor
    if _catalog_cursor >= len(_catalog_keys):
        try:
            names = {}
            try:
                import tag as tag_mod
                all_tags = set()
                for r in _catalog_rows.values():
                    all_tags.update(r.get('t', ()))
                for t in all_tags:
                    try:
                        names[str(t)] = tag_mod.Tag(t).name
                    except Exception:
                        pass
            except Exception:
                pass
            paths.ensure_dirs()
            tmp = CATALOG_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'game_version': paths.game_version(), 'built': time.time(),
                           'tags': names, 'objects': _catalog_rows}, f)
            os.replace(tmp, CATALOG_FILE)
            _catalog_job.update({'status': 'done', 'finished': time.time()})
            log('catalog index written: %d objects' % len(_catalog_rows))
        except Exception as e:
            _catalog_job.update({'status': 'failed', 'error': repr(e)})
            log('catalog index failed: %r' % (e,))


try:  # drive the index build from the reloadable tick hook
    from ts4_bridge import hooks as _hooks
    _hooks.register_tick('catalog', catalog_tick)
except Exception:
    pass


@op('catalog.status', doc='Progress of the catalog index build and the index file path.')
def catalog_status():
    d = dict(_catalog_job)
    d['file'] = CATALOG_FILE
    d['exists'] = os.path.exists(CATALOG_FILE)
    return d


@op('catalog.definition', doc='Details of one object definition (price, tags, tuning class).')
def catalog_definition(def_id):
    definition = services.definition_manager().get(int(def_id), pack_safe=True, get_fallback_definition_id=False)
    if definition is None:
        raise OpError('unknown definition %s' % def_id)
    d = definition_brief(definition)
    try:
        d['description'] = {'hash': int(build_buy.get_object_catalog_description(definition.id))}
    except Exception:
        pass
    try:
        d['super_affordances'] = [L.tuning_name(a) for a in definition.cls._super_affordances][:40]
    except Exception:
        pass
    return d


# ---------------------------------------------------------------- placement / buy / move / sell
def _errors(errors):
    out = []
    for e in errors or ():
        try:
            code, msg = e
            out.append({'code': int(code), 'message': str(msg)})
        except Exception:
            out.append(str(e))
    return out


def _test(obj, def_id, location):
    """Game legality test plus our own lot-bounds check (the native test ignores lot bounds)."""
    if obj is not None:
        result, errors = build_buy.test_location_for_object(obj, location=location)
    else:
        result, errors = build_buy.test_location_for_object(None, int(def_id), location, [])
    errors = _errors(errors)
    try:
        lot = services.current_zone().lot
        if not lot.is_position_on_lot(location.transform.translation):
            result = False
            errors.append({'code': -1, 'message': 'OutsideLotBounds'})
    except Exception:
        pass
    return bool(result), errors


CEILING_SCAN = [round(2.0 + 0.05 * i, 2) for i in range(91)]  # 2.0 .. 6.5 m above the floor
HINTS = {
    'MustBeOnCeiling': 'ceiling object: pass height (about the wall height, e.g. 3.0) or leave it unset to auto-find',
    'MustBeAgainstWall': 'wall object: put x or z on the wall line (wall_contours) and face it into the room; '
                         'windows sit on the wall centreline, wall decor on the interior face',
    'RequiresSlotForPlacement': 'slot-only object (counter sink, TV, lamp...): use buy_into_slot on a parent',
}


def _messages(errors):
    return {e.get('message') for e in errors if isinstance(e, dict)}


def _hints(errors):
    return [HINTS[m] for m in sorted(_messages(errors)) if m in HINTS]


def _test_auto(obj, def_id, x, z, level, rotation, y, height):
    """Test a placement; when no height was given and the game wants a ceiling, scan for the ceiling.
    Returns (location, ok, errors, auto_height)."""
    location = make_location(x, z, level, rotation, y, height)
    result, errors = _test(obj, def_id, location)
    if result or y is not None or height is not None or 'MustBeOnCeiling' not in _messages(errors):
        return location, result, errors, None
    floor = ground_height(x, z, level)
    best = None
    for h in CEILING_SCAN:  # keep the top of the valid band: that is flush with the ceiling
        loc = make_location(x, z, level, rotation, floor + h)
        ok, _ = _test(obj, def_id, loc)
        if ok:
            best = (loc, h)
        elif best is not None:
            break
    if best is None:
        return location, result, errors, None
    return best[0], True, [], best[1]


def _household(household_id=None):
    hh = services.household_manager().get(int(household_id)) if household_id else services.active_household()
    if hh is None:
        raise OpError('no active household')
    return hh


def _definition(def_id):
    definition = services.definition_manager().get(int(def_id), pack_safe=True, get_fallback_definition_id=False)
    if definition is None:
        raise OpError('unknown definition %s' % def_id)
    return definition


def _finish_purchase(obj, hh, price, free, place):
    """Run place(obj), take ownership and charge; destroy the object on any failure so nothing is orphaned."""
    try:
        place(obj)
        obj.set_household_owner_id(hh.id)
        try:
            obj.current_value = price
        except Exception:
            pass
        if not free and price and not hh.funds.try_remove(price, Consts_pb2.TELEMETRY_OBJECT_BUY):
            raise OpError('charging %d failed' % price)
    except Exception as e:
        try:
            obj.destroy(source=obj, cause='ts4mcp buy failed')
        except Exception:
            pass
        if isinstance(e, OpError):
            raise
        raise OpError('placing object failed: %r' % (e,))


@op('objects.test_placement', doc='Check whether def_id (or an existing object_id) can be placed at x,z (level, rotation). '
                                  'height = metres above the floor (auto-found for ceiling objects when omitted).')
def objects_test_placement(x, z, def_id=None, object_id=None, level=0, rotation=0.0, y=None, height=None):
    g.require_zone()
    if object_id is not None:
        obj, def_id = L.game_object(object_id), None
    elif def_id is not None:
        obj, def_id = None, int(def_id)
    else:
        raise OpError('need def_id or object_id')
    location, result, errors, auto = _test_auto(obj, def_id, x, z, level, rotation, y, height)
    out = {'ok': bool(result), 'errors': errors,
           'location': {'x': float(x), 'y': float(location.transform.translation.y), 'z': float(z),
                        'level': int(level), 'rotation': float(rotation)}}
    if auto is not None:
        out['auto_height'] = auto
    if _hints(errors):
        out['hints'] = _hints(errors)
    return out


@op('objects.buy', doc='Buy and place an object from the catalog: def_id at x,z (level, rotation degrees, '
                       'height above floor; ceiling objects auto-find the ceiling). Deducts the price from household '
                       'funds unless free=true. Fails if placement is illegal unless force=true (bb.moveobjects style).')
def objects_buy(def_id, x, z, level=0, rotation=0.0, y=None, height=None, force=False, free=False, household_id=None):
    g.require_zone()
    hh = _household(household_id)
    price = int(_definition(def_id).price)
    location, result, errors, auto = _test_auto(None, int(def_id), x, z, level, rotation, y, height)
    if not result and not force:
        raise OpError('placement is not legal here', errors=errors, hints=_hints(errors), price=price)
    if not free and hh.funds.money < price:
        raise OpError('cannot afford %d (funds %d)' % (price, hh.funds.money))
    obj = create_object(int(def_id))
    if obj is None:
        raise OpError('create_object failed for %s' % def_id)

    def place(o):
        o.location = location
    _finish_purchase(obj, hh, price, free, place)
    d = L.object_brief(obj)
    d['charged'] = 0 if free else price
    d['funds'] = int(hh.funds.money)
    d['placement_ok'] = bool(result)
    if auto is not None:
        d['auto_height'] = auto
    if errors:
        d['placement_errors'] = errors
    return d


@op('objects.move', doc='Move/rotate an existing object to x,z (level, rotation degrees, height above floor). '
                        'force skips the legality test.')
def objects_move(object_id, x, z, level=None, rotation=None, y=None, height=None, force=False):
    g.require_zone()
    obj = L.game_object(object_id)
    if level is None:
        level = L.level(obj) or 0
    if rotation is None:
        try:
            rotation = sims4.math.yaw_quaternion_to_angle(obj.orientation) * 180.0 / 3.141592653589793
        except Exception:
            rotation = 0.0
    location, result, errors, auto = _test_auto(obj, None, x, z, level, rotation, y, height)
    if not result and not force:
        raise OpError('placement is not legal there', errors=errors, hints=_hints(errors))
    obj.location = location
    d = L.object_brief(obj)
    d['placement_ok'] = bool(result)
    if auto is not None:
        d['auto_height'] = auto
    return d


# ---------------------------------------------------------------- slots (counter sinks, chairs, TVs, lamps...)
def _slot_types(slot):
    return [getattr(t, '__name__', str(t)) for t in slot.slot_types]


@op('objects.slots', doc='Runtime slots of an object (name, slot types, empty, child) - where buy_into_slot can put things.')
def objects_slots(object_id, empty_only=False, include_deco=True):
    g.require_zone()
    obj = L.game_object(object_id)
    out = []
    for s in obj.get_runtime_slots_gen():
        name = str(s.slot_name_or_hash)
        if not include_deco and name.startswith('_deco'):
            continue
        if empty_only and not s.empty:
            continue
        row = {'slot': name, 'types': _slot_types(s), 'empty': bool(s.empty)}
        if not s.empty:
            try:
                row['children'] = [c.id for c in s.children]
            except Exception:
                pass
        out.append(row)
    return {'object': L.object_brief(obj), 'count': len(out), 'slots': out[:120]}


@op('objects.buy_into_slot', doc='Buy def_id into a slot of parent_id (counter sink into a counter, chair at a table or '
                                 'desk, TV on a stand, computer on a desk, lamp on a nightstand). slot = slot name or '
                                 'substring to prefer (from objects.slots); otherwise the first empty slot that accepts it.')
def objects_buy_into_slot(parent_id, def_id, slot=None, free=False, household_id=None):
    g.require_zone()
    hh = _household(household_id)
    parent = L.game_object(parent_id)
    price = int(_definition(def_id).price)
    if not free and hh.funds.money < price:
        raise OpError('cannot afford %d (funds %d)' % (price, hh.funds.money))
    obj = create_object(int(def_id))
    if obj is None:
        raise OpError('create_object failed for %s' % def_id)
    try:
        want = set(obj.slot_type_set.slot_types) if obj.slot_type_set else set()
        candidates = [s for s in parent.get_runtime_slots_gen() if s.empty]
        if slot:
            candidates = [s for s in candidates if str(slot) in str(s.slot_name_or_hash)]
        # typed matches first; deco slots (lamps, plants) only accept by size, so they are tested too
        candidates.sort(key=lambda s: 0 if set(s.slot_types) & want else 1)
        target = next((s for s in candidates if s.is_valid_for_placement(obj=obj)), None)
    except Exception:
        obj.destroy(source=obj, cause='ts4mcp slot probe failed')
        raise
    if target is None:
        tried = [str(s.slot_name_or_hash) for s in candidates][:30]
        obj.destroy(source=obj, cause='ts4mcp no valid slot')
        raise OpError('no empty slot on %s accepts this object' % parent_id, tried=tried,
                      hints=['list slots with object_slots; some items (e.g. counter sinks) need a specific parent type'])
    _finish_purchase(obj, hh, price, free, target.add_child)
    d = L.object_brief(obj)
    d['slot'] = str(target.slot_name_or_hash)
    d['parent_id'] = parent.id
    d['charged'] = 0 if free else price
    d['funds'] = int(hh.funds.money)
    return d


@op('objects.sell', doc='Sell (destroy) an object and credit its current value to the owning/active household.')
def objects_sell(object_id, household_id=None):
    g.require_zone()
    obj = L.game_object(object_id)
    if getattr(obj, 'is_sim', False):
        raise OpError('refusing to sell a sim')
    hh = services.household_manager().get(int(household_id)) if household_id else services.active_household()
    value = 0
    try:
        value = int(obj.current_value)
    except Exception:
        pass
    brief = L.object_brief(obj)
    obj.destroy(source=obj, cause='ts4mcp sell')
    if hh is not None and value:
        hh.funds.add(value, Consts_pb2.TELEMETRY_OBJECT_SELL, count_as_earnings=False)
    return {'sold': brief, 'credited': value, 'funds': int(hh.funds.money) if hh else None}


@op('objects.to_inventory', doc="Move a lot object into the household inventory (like 'put in household inventory').")
def objects_to_inventory(object_id):
    g.require_zone()
    obj = L.game_object(object_id)
    ok = build_buy.move_object_to_household_inventory(obj)
    return {'ok': bool(ok)}


@op('lot.layout', doc='Read-only lot geometry: bounds, levels, room/block polygons, wall contours, pools, '
                      'terrain heights at the corners. Use it to plan where objects can go.')
def lot_layout(level=0, include_walls=True):
    zone = g.require_zone()
    lot = zone.lot
    out = {'zone_id': zone.id, 'size': [int(lot.size_x), int(lot.size_z)],
           'center': {'x': float(lot.center.x), 'z': float(lot.center.z)}}
    try:
        corners = [{'x': float(c.x), 'z': float(c.z), 'y': ground_height(c.x, c.z, 0)} for c in lot.corners]
        out['corners'] = corners
    except Exception:
        pass
    try:
        out['levels'] = {'lowest': int(build_buy.get_lowest_level_allowed(zone.id)),
                         'highest': int(build_buy.get_highest_level_allowed(zone.id))}
    except Exception:
        pass
    try:
        plex_id = 0
        blocks = build_buy.get_all_block_polygons(plex_id)
        rooms = []
        for block_id, value in dict(blocks).items():
            try:
                # values are (poly_data, block_level_index); poly_data is a sequence of polygons
                poly_data, block_level = value if isinstance(value, tuple) and len(value) == 2 else (value, None)
                polys = poly_data if hasattr(poly_data, '__iter__') else [poly_data]
                for poly in polys:
                    pts = [{'x': round(float(p.x), 2), 'z': round(float(p.z), 2)} for p in poly]
                    rooms.append({'block_id': int(block_id), 'level': block_level, 'polygon': pts})
            except Exception as e:
                rooms.append({'block_id': int(block_id), 'error': repr(e)})
        out['blocks'] = rooms[:60]
    except Exception as e:
        out['blocks_error'] = repr(e)
    if include_walls:
        try:
            # contours around a position on the given level
            contours = build_buy.get_wall_contours(float(lot.center.x), float(lot.center.z), surface(level), True)
            walls = []
            for c in list(contours or ())[:80]:
                try:
                    walls.append([{'x': round(float(p.x), 2), 'z': round(float(p.z), 2)} for p in c])
                except Exception:
                    walls.append(repr(c)[:200])
            out['wall_contours'] = walls
        except Exception as e:
            out['walls_error'] = repr(e)
    try:
        out['pools'] = [[{'x': round(float(p.x), 2), 'z': round(float(p.z), 2)} for p in poly]
                        for poly in list(build_buy.get_pool_edges())[:20]]
    except Exception:
        pass
    return out


@op('lot.probe', doc='Probe a point: ground height, outside?, natural ground?, has floor?, block/room id, routable?')
def lot_probe(x, z, level=0):
    g.require_zone()
    pos = Vector3(float(x), ground_height(x, z, level), float(z))
    out = {'x': float(x), 'z': float(z), 'y': float(pos.y), 'level': int(level)}
    for name, fn in (('outside', lambda: build_buy.is_location_outside(pos, int(level))),
                     ('natural_ground', lambda: build_buy.is_location_natural_ground(pos, int(level))),
                     ('has_floor', lambda: build_buy.has_floor_at_location(pos, int(level))),
                     ('block_id', lambda: build_buy.get_block_id(services.current_zone_id(), pos, int(level))),
                     ('routable', lambda: routing.test_point_placement_in_navmesh(surface(level), pos)),
                     ('on_lot', lambda: services.current_zone().lot.is_position_on_lot(pos))):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = 'err: %r' % (e,)
    return out
