"""Household, careers, skills, traits, relationships, aging, world, travel and saving."""
from __future__ import annotations

import asyncio
from typing import Any

from ts4_mcp.bridge_client import get_client
from ts4_mcp.server import DANGEROUS, MUTATING, READ_ONLY, _fmt, bridge_call, mcp
from ts4_mcp.tools.live import _localized


@mcp.tool(annotations=MUTATING)
async def household_funds(delta: int = 0) -> str:
    """Read the active household's funds, or add/remove simoleons (cheat; prefer earning money in play
    unless the goal allows cheating)."""
    return _fmt(await bridge_call("household.funds", {"delta": delta}))


@mcp.tool(annotations=READ_ONLY)
async def list_households(player_only: bool = False, limit: int = 100) -> str:
    """All households in the save with members, funds and home lot."""
    return _fmt(await _localized("household.list", {"player_only": player_only, "limit": limit}))


@mcp.tool(annotations=MUTATING)
async def move_sim_to_household(sim_id: str, to_household_id: int | None = None) -> str:
    """Move a sim into another household (default: the active household, making them selectable).
    Used for move-ins, marriages, taking in roommates, splitting families."""
    args: dict[str, Any] = {"sim_id": sim_id}
    if to_household_id is not None:
        args["to_household_id"] = to_household_id
    return _fmt(await bridge_call("household.move_sim", args))


@mcp.tool(annotations=DANGEROUS)
async def move_household_to_lot(zone_id: int, furnished: bool = True) -> str:
    """Move the active household into another residential lot (buy/move house). The game handles the
    transaction asynchronously and will travel there; follow with look."""
    return _fmt(await bridge_call("household.move_into_zone", {"zone_id": zone_id, "furnished": furnished}))


@mcp.tool(annotations=READ_ONLY)
async def list_lots(residential_only: bool = False, unowned_only: bool = False, world_id: int | None = None,
                    limit: int = 200) -> str:
    """Every lot in the save with zone_id, name, world, venue type and owner. Use zone_id with travel or
    move_household_to_lot."""
    args: dict[str, Any] = {"residential_only": residential_only, "unowned_only": unowned_only, "limit": limit}
    if world_id is not None:
        args["world_id"] = world_id
    return _fmt(await _localized("world.lots", args, timeout=25.0))


@mcp.tool(annotations=READ_ONLY)
async def lot_value(zone_id: int) -> str:
    """Furnished and unfurnished value of a lot."""
    return _fmt(await bridge_call("world.lot_value", {"zone_id": zone_id}))


@mcp.tool(annotations=MUTATING)
async def travel(zone_id: int, sim_ids: list[str] | None = None, wait_seconds: int = 20) -> str:
    """Travel to another lot (the active sim, or the given sims together). Shows a loading screen; this
    tool waits up to wait_seconds for the new lot to load and returns a snapshot."""
    client = get_client()
    args: dict[str, Any] = {"zone_id": zone_id}
    if sim_ids:
        args["sim_ids"] = sim_ids
    res = await bridge_call("world.travel", args)
    import time
    deadline = time.monotonic() + max(1, min(wait_seconds, 25))
    loaded = False
    while time.monotonic() < deadline:
        ev = await client.wait_for_event(lambda e: e.get("name") == "zone.loaded",
                                         timeout=min(2.0, max(0.1, deadline - time.monotonic())))
        if ev is not None:
            loaded = True
            break
        try:  # polling fallback: the zone id changing and running is as good as the event
            info = await bridge_call("bridge.info", timeout=5.0)
            if info.get("zone_id") == zone_id and info.get("zone_running"):
                loaded = True
                break
        except Exception:
            pass
    out: dict[str, Any] = {"travel": res, "loaded": loaded}
    if loaded:
        await asyncio.sleep(1.0)
        try:
            out["snapshot"] = await _localized("state.snapshot", {"include_buffs": False})
        except Exception as e:  # zone may still be settling
            out["snapshot_error"] = str(e)
    else:
        out["hint"] = "Still loading; call look in a few seconds."
    return _fmt(out)


@mcp.tool(annotations=MUTATING)
async def save_game(new_slot: bool = False, wait_seconds: int = 15) -> str:
    """Save the game (current slot, or a new slot). Waits briefly for completion."""
    client = get_client()
    res = await bridge_call("persistence.save", {"new_slot": new_slot})
    ev = await client.wait_for_event(lambda e: e.get("name") == "save.done", timeout=max(1, min(wait_seconds, 25)))
    return _fmt({"save": res, "completed": ev is not None})


@mcp.tool(annotations=READ_ONLY)
async def career_options(query: str = "") -> str:
    """Career tuning names available to join (filter by substring, e.g. 'painter', 'tech', 'culinary')."""
    args: dict[str, Any] = {}
    if query:
        args["query"] = query
    return _fmt(await _localized("career.list_available", args))


@mcp.tool(annotations=MUTATING)
async def career(action: str, sim_id: str = "active", career_name: str = "", levels: int = 1) -> str:
    """Manage a sim's career. action: join (career_name required, from career_options) | quit | promote |
    demote | retire. Promote/demote are cheats; normally let the sim earn promotions by working."""
    args: dict[str, Any] = {"action": action, "sim_id": sim_id, "levels": levels}
    if career_name:
        args["career"] = career_name
    return _fmt(await _localized("career.action", args))


@mcp.tool(annotations=READ_ONLY)
async def skill_options(query: str = "") -> str:
    """Skill tuning names (filter by substring, e.g. 'painting', 'cooking')."""
    args: dict[str, Any] = {}
    if query:
        args["query"] = query
    return _fmt(await _localized("skill.list_available", args))


@mcp.tool(annotations=DANGEROUS)
async def set_skill(skill_name: str, level: int, sim_id: str = "active") -> str:
    """Cheat a skill to a level (1-10). Prefer letting sims learn by doing unless the goal allows cheats."""
    return _fmt(await bridge_call("skill.set", {"skill": skill_name, "level": level, "sim_id": sim_id}))


@mcp.tool(annotations=DANGEROUS)
async def set_motive(value: float, motive: str = "all", sim_id: str = "active") -> str:
    """Cheat a need to a value in -100..100 (motive: Hunger|Energy|Bladder|Fun|Social|Hygiene|all)."""
    return _fmt(await bridge_call("motive.set", {"value": value, "motive": motive, "sim_id": sim_id}))


@mcp.tool(annotations=READ_ONLY)
async def trait_options(query: str = "", trait_type: str = "") -> str:
    """Trait tuning names with type (PERSONALITY, ASPIRATION, HIDDEN...). Filter by substring."""
    args: dict[str, Any] = {}
    if query:
        args["query"] = query
    if trait_type:
        args["trait_type"] = trait_type
    return _fmt(await _localized("trait.list_available", args))


@mcp.tool(annotations=MUTATING)
async def set_trait(action: str, trait_name: str, sim_id: str = "active") -> str:
    """Add or remove a trait (action: add|remove) by tuning name, e.g. trait_Ambitious."""
    return _fmt(await bridge_call("trait.action", {"action": action, "trait": trait_name, "sim_id": sim_id}))


@mcp.tool(annotations=DANGEROUS)
async def modify_relationship(sim_id: str, other_sim_id: str, delta: float, track: str = "friendship") -> str:
    """Cheat relationship score between two sims (track: friendship|romance, delta -100..100)."""
    return _fmt(await bridge_call("relationship.modify", {"sim_id": sim_id, "other_sim_id": other_sim_id,
                                                          "delta": delta, "track": track}))


@mcp.tool(annotations=DANGEROUS)
async def age_up(sim_id: str = "active") -> str:
    """Advance a sim to the next life stage immediately."""
    return _fmt(await bridge_call("sim.age_up", {"sim_id": sim_id}))


@mcp.tool(annotations=MUTATING)
async def rename_sim(sim_id: str = "active", first_name: str = "", last_name: str = "") -> str:
    """Change a sim's first and/or last name."""
    args: dict[str, Any] = {"sim_id": sim_id}
    if first_name:
        args["first_name"] = first_name
    if last_name:
        args["last_name"] = last_name
    return _fmt(await bridge_call("sim.rename", args))
