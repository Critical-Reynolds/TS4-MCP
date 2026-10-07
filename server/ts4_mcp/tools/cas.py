"""Create-a-Sim tools."""
from __future__ import annotations

from typing import Any

from ts4_mcp.server import DANGEROUS, MUTATING, READ_ONLY, _fmt, bridge_call, mcp
from ts4_mcp.tools.live import _localized


@mcp.tool(annotations=MUTATING)
async def create_sim(first_name: str, last_name: str, gender: str = "", age: str = "YOUNGADULT",
                     species: str = "HUMAN", traits: list[str] | None = None, household_id: int | None = None,
                     spawn: bool = True) -> str:
    """Create a new sim with generated looks and add them to the active household (or household_id).
    gender MALE|FEMALE (random if empty); age BABY|INFANT|TODDLER|CHILD|TEEN|YOUNGADULT|ADULT|ELDER;
    traits are tuning names from trait_options (e.g. trait_Ambitious, trait_Creative). Babies need a
    parent in the household. The sim spawns next to the active sim when spawn is true."""
    args: dict[str, Any] = {"first_name": first_name, "last_name": last_name, "age": age, "species": species,
                            "spawn": spawn}
    if gender:
        args["gender"] = gender
    if traits:
        args["traits"] = traits
    if household_id is not None:
        args["household_id"] = household_id
    return _fmt(await bridge_call("cas.create_sim", args, timeout=25.0))


@mcp.tool(annotations=READ_ONLY)
async def outfits(sim_id: str = "active") -> str:
    """Outfit categories a sim owns and which one is worn."""
    return _fmt(await bridge_call("cas.outfits", {"sim_id": sim_id}))


@mcp.tool(annotations=MUTATING)
async def set_outfit(category: str = "EVERYDAY", index: int = 0, sim_id: str = "active") -> str:
    """Change into an outfit category (EVERYDAY|FORMAL|ATHLETIC|SLEEP|PARTY|SWIMWEAR|HOTWEATHER|COLDWEATHER)."""
    return _fmt(await bridge_call("cas.set_outfit", {"category": category, "index": index, "sim_id": sim_id}))


@mcp.tool(annotations=DANGEROUS)
async def remove_sim(sim_id: str, confirm: bool = False) -> str:
    """Permanently delete a sim from the save. Irreversible; requires confirm=true and the player's consent."""
    return _fmt(await bridge_call("cas.remove_sim", {"sim_id": sim_id, "confirm": confirm}))
