"""Live-mode tools: observe, act, wait."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from ts4_mcp import localization as loc
from ts4_mcp.bridge_client import get_client
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from ts4_mcp.server import DANGEROUS, MUTATING, READ_ONLY, _fmt, bridge_call, mcp

NOTABLE_EVENTS = {
    "interaction.finished", "dialog.shown", "career.offers", "career.quit_choices", "phone.ringing", "sim.died", "sim.aged", "sim.born", "zone.loaded", "save.done",
    "career.workday_complete", "sim.skill_level", "situation.started", "household.changed", "sim.ready_to_age",
}


def _sims_index(snapshot: dict[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    hh = snapshot.get("household") or {}
    for m in hh.get("members") or []:
        if m.get("sim_id") and m.get("name"):
            out[m["sim_id"]] = m["name"]
    a = snapshot.get("active_sim") or {}
    if a.get("sim_id") and a.get("name"):
        out[a["sim_id"]] = a["name"]
    return out


async def _localized(op: str, args: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
    result = await bridge_call(op, args, timeout=timeout)
    try:
        hello = get_client().hello
        loc.load(hello.get("game_dir"))
    except Exception:
        pass
    return loc.resolve(result)


@mcp.tool(annotations=READ_ONLY)
async def look(include_buffs: bool = True, include_members: bool = True) -> str:
    """Compact snapshot of the game: lot, sim time and speed, active household and funds, the active sim's
    needs/mood/buffs/queue, selectable sims and any pending dialogs. Your main observation tool."""
    return _fmt(await _localized("state.snapshot", {"include_buffs": include_buffs, "include_members": include_members}))


@mcp.tool(annotations=READ_ONLY)
async def list_sims(scope: str = "household", name_filter: str = "", limit: int = 100, offset: int = 0) -> str:
    """List sims. scope: household (active household), lot (everyone currently on this lot), selectable,
    or all (every sim in the save; paginate with limit/offset)."""
    args: dict[str, Any] = {"scope": scope, "limit": limit, "offset": offset}
    if name_filter:
        args["name_filter"] = name_filter
    return _fmt(await _localized("sims.list", args))


@mcp.tool(annotations=READ_ONLY)
async def sim_details(sim_id: str = "active", sections: list[str] | None = None) -> str:
    """Detailed state of one sim (id or 'active'). sections subset of: identity, motives, mood, buffs,
    skills, careers, traits, relationships, aspiration, whims, queue, outfit, pregnancy."""
    args: dict[str, Any] = {"sim_id": sim_id}
    if sections:
        args["sections"] = sections
    return _fmt(await _localized("sims.details", args))


@mcp.tool(annotations=READ_ONLY)
async def list_interactions(sim_id: str = "active", target_id: str = "self", query: str = "",
                            only_runnable: bool = True, limit: int = 60) -> str:
    """Interactions a sim can do right now on a target: 'self', 'phone' (the cell phone menu: Find a Job,
    Quit Job, call/text sims, order delivery, invite over, travel...), an object id from list_objects, or
    another sim's id. Each entry has an affordance_id for do_interaction (pass the same target_id).
    query filters by name substring (e.g. 'paint', 'sleep', 'cook', 'career')."""
    args: dict[str, Any] = {"sim_id": sim_id, "target_id": target_id, "only_runnable": only_runnable, "limit": limit}
    if query:
        args["query"] = query
    return _fmt(await _localized("interactions.list", args, timeout=25.0))


@mcp.tool(annotations=MUTATING)
async def do_interaction(affordance_id: str, sim_id: str = "active", target_id: str = "self",
                         priority: str = "high", insert: str = "next") -> str:
    """Queue an interaction (affordance_id or tuning name from list_interactions) for a sim on a target
    ('self', 'phone', an object id or a sim id; use the target_id it was listed under). priority: low|high|critical. insert: next|first|last. Returns the enqueue result and queue."""
    return _fmt(await _localized("interactions.push", {"affordance_id": affordance_id, "sim_id": sim_id,
                                                        "target_id": target_id, "priority": priority,
                                                        "insert": insert}))


@mcp.tool(annotations=MUTATING)
async def cancel_interactions(sim_id: str = "active", interaction_id: int | str | None = None) -> str:
    """Cancel one interaction by id, or everything queued and running for the sim when interaction_id is omitted."""
    args: dict[str, Any] = {"sim_id": sim_id}
    if interaction_id is not None:
        args["interaction_id"] = int(interaction_id)
    return _fmt(await _localized("interactions.cancel", args))


@mcp.tool(annotations=READ_ONLY)
async def list_objects(query: str = "", near_sim_id: str = "", radius: float = 10.0, limit: int = 80,
                       include_sims: bool = False) -> str:
    """Objects on the current lot with ids, types, positions and values. query matches the type name
    ('fridge', 'bed', 'easel', 'computer', 'toilet'). near_sim_id with radius sorts by distance to that sim."""
    args: dict[str, Any] = {"limit": limit, "include_sims": include_sims, "radius": radius}
    if query:
        args["query"] = query
    if near_sim_id:
        args["near_sim_id"] = near_sim_id
    return _fmt(await _localized("objects.list", args))


@mcp.tool(annotations=READ_ONLY)
async def object_details(object_id: int | str) -> str:
    """Details of one object: definition, owner, states, inventory contents and its super affordances."""
    return _fmt(await _localized("objects.get", {"object_id": object_id}))


@mcp.tool(annotations=MUTATING)
async def set_active_sim(sim_id: str) -> str:
    """Switch control to another sim of the active household."""
    return _fmt(await bridge_call("sims.set_active", {"sim_id": sim_id}))


@mcp.tool(annotations=MUTATING)
async def set_speed(speed: str) -> str:
    """Set the game clock: paused | normal | fast | ultra | super (super = 'sims away' max speed)."""
    return _fmt(await bridge_call("time.set_speed", {"speed": speed}))


@mcp.tool(annotations=READ_ONLY)
async def pending_dialogs() -> str:
    """Dialogs currently waiting for a player answer, with button response_ids and picker rows."""
    return _fmt(await _localized("dialogs.list"))


@mcp.tool(annotations=MUTATING)
async def respond_dialog(dialog_id: int | str, response_id: str = "ok", picked: list[str] | None = None,
                         text: str = "") -> str:
    """Answer an open dialog the way the player would click it; the window closes on screen.
    response_id: a button's response_id, or ok|cancel|close. Phone calls: ok to accept, cancel to decline.
    Pickers (Invite Over, Chat With, Travel...): picked=["Martha"] or sim ids / option_ids from picker_rows;
    the picks are selected and confirmed with OK in one call. Text prompts: pass text."""
    args: dict[str, Any] = {"dialog_id": int(dialog_id), "response_id": response_id}
    if picked:
        args["picked"] = picked
    if text:
        args["text"] = text
    return _fmt(await bridge_call("dialogs.respond", args))


MAX_WAIT_SECONDS = int(os.environ.get("TS4_MAX_WAIT_SECONDS", "1800"))
DEFAULT_WAKE_NEEDS = {"Bladder": -40, "Hunger": -30, "Energy": -60, "Hygiene": -50, "Social": -60, "Fun": -60}
WAKE_TRIGGERS = ("idle", "needs", "sellable", "home", "left", "awake", "step", "mood")
NEGATIVE_MOODS = {"Mood_Angry", "Mood_Sad", "Mood_Tense", "Mood_Stressed", "Mood_Embarrassed",
                  "Mood_Uncomfortable", "Mood_Bored", "Mood_Dazed", "Mood_Scared"}


def _is_step(ev: dict[str, Any], active_id: Any) -> bool:
    """A user-directed interaction (including a queued social) of the active sim just finished."""
    data = ev.get("data") or {}
    return (ev.get("name") == "interaction.finished" and data.get("user_directed")
            and str(data.get("sim_id")) == str(active_id))


def _wake_reasons(prev: dict[str, Any], cur: dict[str, Any], wake_on: set[str]) -> list[str]:
    """Triggers fire on transitions between two wake.check polls, so a sim that is already idle (or already
    hungry) when the wait starts doesn't end it instantly."""
    pa, ca = prev.get("active") or {}, cur.get("active") or {}
    out = []
    if "home" in wake_on and ca.get("on_lot") and not pa.get("on_lot"):
        out.append("home")
    if "left" in wake_on and pa.get("on_lot") and not ca.get("on_lot"):
        out.append("left")
    if "awake" in wake_on and pa.get("sleeping") and ca.get("on_lot") and not ca.get("sleeping"):
        out.append("awake")
    if "idle" in wake_on and ca.get("idle") and pa.get("idle") is False:
        out.append("idle")
    if "needs" in wake_on:
        newly = set(ca.get("below") or []) - set(pa.get("below") or [])
        out.extend(f"need:{m}" for m in sorted(newly))
    if "sellable" in wake_on and len(cur.get("sellable") or []) > len(prev.get("sellable") or []):
        out.append("sellable")
    if "mood" in wake_on and ca.get("mood") in NEGATIVE_MOODS and pa.get("mood") != ca.get("mood"):
        out.append(f"mood:{ca.get('mood')}")
    return out


@mcp.tool(annotations=MUTATING)
async def wait(sim_minutes: int = 60, max_seconds: int = 20, speed: str = "ultra",
               until_events: list[str] | None = None, stop_on_dialog: bool = True,
               wake_on: list[str] | None = None, needs: dict[str, float] | None = None,
               ctx: Context | None = None) -> str:
    """Let sim time pass, then pause and return a fresh snapshot plus the events that happened.
    Runs the clock at `speed` (ultra | super | auto = super while the sim sleeps or is away, ultra otherwise)
    until `sim_minutes` elapse, `max_seconds` real seconds pass (up to 1800), a dialog appears, one of
    `until_events` fires (interaction.finished, career.workday_complete, sim.skill_level...), or a `wake_on`
    trigger fires: idle (stopped doing anything you directed), needs (a motive fell below `needs`
    thresholds, e.g. {"Hunger": -30}), sellable (a painting finished), home / left (arrived on / left the
    lot), awake (woke up), step (an interaction/social you queued finished; the reason carries its
    affordance and outcome), mood (the sim's mood turned negative: angry, sad, stressed, embarrassed...).
    Use long waits with wake_on so you only act when a decision is needed; for socials and anything
    you are steering closely, include "step" and "mood" instead of guessing a duration."""
    client = get_client()
    wake = set(wake_on or [])
    unknown = wake - set(WAKE_TRIGGERS)
    if unknown:
        raise ToolError(f"unknown wake_on {sorted(unknown)}; use {list(WAKE_TRIGGERS)}")
    thresholds = needs or (DEFAULT_WAKE_NEEDS if "needs" in wake else None)
    poll_args = {"needs": thresholds, "speed": "auto" if speed == "auto" else None}
    use_wake = bool(wake) or speed == "auto"
    before = await bridge_call("time.get")
    start_seq = client.last_seq
    await bridge_call("time.set_speed", {"speed": "ultra" if speed == "auto" else speed})
    prev = await bridge_call("wake.check", poll_args) if use_wake else None
    limit = max(1, min(int(max_seconds), MAX_WAIT_SECONDS))
    started = time.monotonic()
    deadline = started + limit
    last_progress = started
    reason = "timeout"
    fired: list[str] = []
    watch = set(until_events or []) | ({"dialog.shown"} if stop_on_dialog else set())
    active_id = ((prev or {}).get("active") or {}).get("sim_id")
    want_step = "step" in wake and active_id is not None
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ev = await client.wait_for_event(lambda e: e.get("name") in watch or (want_step and _is_step(e, active_id)),
                                             timeout=min(1.0, max(0.05, remaining)))
            if ev is not None:
                if ev.get("name") in watch:
                    reason = f"event:{ev.get('name')}"
                else:
                    d = ev.get("data") or {}
                    reason = f"wake:step:{d.get('affordance')}:{d.get('outcome')}"
                break
            if use_wake:
                cur = await bridge_call("wake.check", poll_args)
                now = cur["time"]
                fired = _wake_reasons(prev, cur, wake)
                prev = cur
                if fired:
                    reason = "wake:" + ",".join(fired)
                    break
            else:
                now = await bridge_call("time.get")
            elapsed = _sim_minutes_between(before, now)
            if elapsed >= int(sim_minutes):
                reason = "sim_minutes_elapsed"
                break
            if now.get("paused"):
                reason = ("paused_by_player_screen: " + now.get("pause_note", "")
                          if now.get("paused_by_player_screen") else "game_paused_externally")
                break
            if ctx is not None and time.monotonic() - last_progress >= 10:
                last_progress = time.monotonic()
                try:
                    await ctx.report_progress(elapsed, int(sim_minutes), f"{now.get('sim_now')} ({elapsed} sim min)")
                except Exception:
                    pass
    finally:
        try:
            await bridge_call("time.set_speed", {"speed": "paused"})
        except Exception:
            pass
    snap = await _localized("state.snapshot", {"include_buffs": False, "include_members": True})
    household_ids = set(_sims_index(snap).keys())
    events = []
    for e in client.recent_events(since_seq=start_seq, limit=200):
        name = e.get("name")
        data = e.get("data") or {}
        if name not in NOTABLE_EVENTS and name not in watch:
            continue
        # interaction chatter from NPCs is rarely useful; keep household sims and anything non-interaction
        if name.startswith("interaction.") and data.get("sim_id") not in household_ids:
            continue
        events.append({"seq": e.get("seq"), "sim_ts": e.get("sim_ts"), "name": name, "data": data})
    events = loc.resolve(events, _sims_index(snap))
    out: dict[str, Any] = {"stopped_because": reason, "sim_minutes_waited": _sim_minutes_between(before, snap.get("time", {})),
                           "real_seconds": round(time.monotonic() - started, 1), "events": events[-25:], "snapshot": snap}
    if use_wake and prev is not None:
        out["sellable"] = prev.get("sellable")
    return _fmt(out)


def _sim_minutes_between(a: dict[str, Any], b: dict[str, Any]) -> int:
    try:
        return int(b["abs_minutes"] - a["abs_minutes"])
    except Exception:
        try:
            ma = a["hour"] * 60 + a["minute"]
            mb = b["hour"] * 60 + b["minute"]
            d = mb - ma
            return d if d >= 0 else d + 24 * 60
        except Exception:
            return 0
