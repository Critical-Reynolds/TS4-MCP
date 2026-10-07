"""Buy-mode tools: catalog search (server-side over the index), placement, buy/move/sell, lot geometry."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ts4_mcp import config
from ts4_mcp import localization as loc
from ts4_mcp.bridge_client import get_client
from ts4_mcp.server import DANGEROUS, MUTATING, READ_ONLY, _fmt, bridge_call, mcp
from ts4_mcp.tools.live import _localized

_catalog: dict[str, Any] | None = None
_catalog_mtime: float = 0.0


def catalog_file() -> Path:
    return config.user_dir() / "mod_data" / "ts4_bridge" / "catalog.json"


def _load_catalog() -> dict[str, Any] | None:
    global _catalog, _catalog_mtime
    path = catalog_file()
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _catalog is None or mtime != _catalog_mtime:
        data = json.loads(path.read_text(encoding="utf-8"))
        tag_names = data.get("tags", {})
        rows = []
        for def_id, row in data.get("objects", {}).items():
            name = loc.text(row.get("n")) or ""
            tags = [tag_names.get(str(t), str(t)) for t in row.get("t", [])]
            rows.append({"def_id": int(def_id), "name": name, "price": row.get("p"), "tuning": row.get("c"),
                         "tags": tags, "available": bool(row.get("a", True)),
                         "_search": (name + " " + " ".join(tags) + " " + (row.get("c") or "")).lower()})
        data["_rows"] = rows
        _catalog = data
        _catalog_mtime = mtime
    return _catalog


@mcp.tool(annotations=READ_ONLY)
async def catalog_search(query: str = "", tags: list[str] | None = None, max_price: int | None = None,
                         min_price: int | None = None, limit: int = 25, sort: str = "price") -> str:
    """Search the buy-mode catalog by English name, tag or tuning class (e.g. 'double bed', 'fridge',
    'Func_Toilet', 'easel'). Returns def_ids for buy_object with price and tags. The index is built once
    per game version by the in-game bridge (a few seconds); if missing, this starts it and asks you to retry.
    sort: price | name | -price."""
    cat = _load_catalog()
    if cat is None:
        status = await bridge_call("catalog.build_index", {})
        return _fmt({"catalog": "building", "status": status,
                     "hint": "Call catalog_search again in ~10-20 seconds."})
    terms = [t for t in re.split(r"\s+", query.lower().strip()) if t]
    want_tags = [t.lower() for t in (tags or [])]
    groups: dict[tuple, dict[str, Any]] = {}
    for row in cat["_rows"]:
        # only real, purchasable, available catalog entries
        if row["price"] is None or row["price"] <= 0 or not row["name"] or not row.get("available", True):
            continue
        if terms and not all(t in row["_search"] for t in terms):
            continue
        if want_tags and not all(any(wt in tg.lower() for tg in row["tags"]) for wt in want_tags):
            continue
        if max_price is not None and row["price"] > max_price:
            continue
        if min_price is not None and row["price"] < min_price:
            continue
        key = (row["name"], row["price"], row["tuning"])
        grp = groups.get(key)
        if grp is None:
            grp = {"def_id": row["def_id"], "name": row["name"], "price": row["price"], "tuning": row["tuning"],
                   "tags": [t for t in row["tags"] if t.startswith(("Func_", "BuyCat", "Style_", "Build_"))][:8],
                   "variants": []}
            groups[key] = grp
        else:
            grp["variants"].append(row["def_id"])
    out = list(groups.values())
    if sort == "name":
        out.sort(key=lambda r: r["name"])
    elif sort == "-price":
        out.sort(key=lambda r: -(r["price"] or 0))
    else:
        out.sort(key=lambda r: (r["price"] or 0))
    total = len(out)
    out = out[: max(1, min(int(limit), 100))]
    for r in out:
        if r["variants"]:
            r["variants"] = r["variants"][:12]
        else:
            r.pop("variants")
    return _fmt({"total": total, "shown": len(out), "objects": out,
                 "note": "variants are colour swatches of the same item", "game_version": cat.get("game_version")})


@mcp.tool(annotations=READ_ONLY)
async def catalog_status() -> str:
    """Progress of the in-game catalog index build."""
    return _fmt(await bridge_call("catalog.status"))


@mcp.tool(annotations=READ_ONLY)
async def object_definition(def_id: int) -> str:
    """Details of a catalog definition: name, description, price, tags and the interactions it offers."""
    return _fmt(await _localized("catalog.definition", {"def_id": def_id}))


@mcp.tool(annotations=READ_ONLY)
async def lot_layout(level: int = 0, include_walls: bool = True) -> str:
    """Read-only lot geometry: lot bounds/corners with ground heights, allowed levels, room block polygons,
    wall contours and pools. Plan furniture positions from this; architecture itself cannot be changed by script."""
    return _fmt(await bridge_call("lot.layout", {"level": level, "include_walls": include_walls}, timeout=25.0))


@mcp.tool(annotations=READ_ONLY)
async def lot_probe(x: float, z: float, level: int = 0) -> str:
    """Probe one point on the lot: ground height, outside/inside, natural ground, has floor, block id, routable, on lot."""
    return _fmt(await bridge_call("lot.probe", {"x": x, "z": z, "level": level}))


@mcp.tool(annotations=READ_ONLY)
async def test_placement(x: float, z: float, def_id: int | None = None, object_id: int | str | None = None,
                         level: int = 0, rotation: float = 0.0, height: float | None = None) -> str:
    """Check whether a catalog object (def_id) or an existing object (object_id) can legally sit at x,z
    with the given rotation in degrees. Returns the game's placement errors (plus hints) when not.
    height = metres above the floor; ceiling objects find the ceiling automatically when it is omitted."""
    args: dict[str, Any] = {"x": x, "z": z, "level": level, "rotation": rotation}
    if def_id is not None:
        args["def_id"] = def_id
    if object_id is not None:
        args["object_id"] = int(object_id)
    if height is not None:
        args["height"] = height
    return _fmt(await bridge_call("objects.test_placement", args))


@mcp.tool(annotations=MUTATING)
async def buy_object(def_id: int, x: float, z: float, level: int = 0, rotation: float = 0.0,
                     height: float | None = None, force: bool = False, free: bool = False) -> str:
    """Buy a catalog object and place it at x,z (level, rotation degrees), charging household funds.
    Fails with the game's placement errors when the spot is illegal unless force=true. Use test_placement
    and lot_layout to pick positions; x/z are world metres, one tile = 1 unit.
    Rotation: 0 faces +z, 90 +x, 180 -z, 270 -x (face wall-backed items away from their wall).
    height = metres above the floor for wall/ceiling objects; ceiling lights auto-find the ceiling.
    Slot-only items (counter sinks, chairs at a table, TVs, lamps on tables) go through buy_into_slot."""
    args: dict[str, Any] = {"def_id": def_id, "x": x, "z": z, "level": level, "rotation": rotation,
                            "force": force, "free": free}
    if height is not None:
        args["height"] = height
    return _fmt(await _localized("objects.buy", args))


@mcp.tool(annotations=MUTATING)
async def move_object(object_id: int | str, x: float, z: float, level: int | None = None,
                      rotation: float | None = None, height: float | None = None, force: bool = False) -> str:
    """Move and/or rotate an existing object (rotation in degrees; omit to keep current;
    height = metres above the floor for wall/ceiling objects)."""
    args: dict[str, Any] = {"object_id": int(object_id), "x": x, "z": z, "force": force}
    if level is not None:
        args["level"] = level
    if rotation is not None:
        args["rotation"] = rotation
    if height is not None:
        args["height"] = height
    return _fmt(await _localized("objects.move", args))


@mcp.tool(annotations=READ_ONLY)
async def object_slots(object_id: int | str, empty_only: bool = False, include_deco: bool = True) -> str:
    """Slots on an object (counter, table, desk, TV stand, nightstand...): slot name, slot types, whether
    empty and what sits there. Use a slot name with buy_into_slot."""
    return _fmt(await _localized("objects.slots", {"object_id": int(object_id), "empty_only": empty_only,
                                                    "include_deco": include_deco}))


@mcp.tool(annotations=MUTATING)
async def buy_into_slot(parent_id: int | str, def_id: int, slot: str = "", free: bool = False) -> str:
    """Buy a catalog object into a slot on an existing object, charging household funds: a counter sink
    into a counter, dining/desk chairs at a table or desk (_ctnm_chr_N), a TV on a TV stand, a computer on
    a desk, a lamp on a nightstand (_deco_lrg). slot is a slot name or substring to prefer (see
    object_slots); empty picks the first empty slot that accepts the object."""
    args: dict[str, Any] = {"parent_id": int(parent_id), "def_id": def_id, "free": free}
    if slot:
        args["slot"] = slot
    return _fmt(await _localized("objects.buy_into_slot", args))


@mcp.tool(annotations=MUTATING)
async def autopilot_start(activity: str = "paint", recipe: str = "", interval: int = 10, fast: bool = True,
                          auto_dialogs: bool = True, pay_bills: bool = True) -> str:
    """Run the active sim on an in-game money loop until stopped: keeps needs up (toilet, food, shower,
    sleep 22:00-06:00), paints at the easel (recipe tuning name, default Classics) and sells finished
    paintings, pays bills, answers routine dialogs with their first option, and keeps the clock running
    (super speed while asleep or at work). Then check in with autopilot_status instead of wait."""
    args: dict[str, Any] = {"activity": activity, "interval": interval, "fast": fast,
                            "auto_dialogs": auto_dialogs, "pay_bills": pay_bills}
    if recipe:
        args["recipe"] = recipe
    return _fmt(await _localized("autopilot.start", args))


@mcp.tool(annotations=READ_ONLY)
async def autopilot_status(log_lines: int = 15) -> str:
    """Autopilot counters (paintings sold, earnings, bills, dialogs, errors), recent decisions and funds."""
    return _fmt(await _localized("autopilot.status", {"log_lines": log_lines}))


@mcp.tool(annotations=MUTATING)
async def autopilot_stop(pause: bool = True) -> str:
    """Stop the autopilot (and pause the game unless pause=false)."""
    return _fmt(await _localized("autopilot.stop", {"pause": pause}))


@mcp.tool(annotations=DANGEROUS)
async def sell_object(object_id: int | str) -> str:
    """Sell (delete) an object and credit its current value to the household."""
    return _fmt(await _localized("objects.sell", {"object_id": int(object_id)}))
