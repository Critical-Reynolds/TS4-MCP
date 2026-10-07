"""MCP server definition and the core tool set."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ts4_mcp import config
from ts4_mcp.bridge_client import BridgeError, NotConnected, get_client

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")

mcp = MCPServer(
    name="ts4",
    title="The Sims 4",
    instructions=INSTRUCTIONS,
    version="0.1.0",
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DANGEROUS = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def _fmt(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, indent=1, default=str, ensure_ascii=False)


async def bridge_call(op: str, args: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
    """Call an op and convert bridge failures into model-readable exceptions.

    Must raise ToolError: the SDK replaces any other exception's text with a bare
    "Error executing tool <name>", hiding placement errors and tracebacks from the model."""
    try:
        return await get_client().call(op, args, timeout=timeout)
    except BridgeError as e:
        raise ToolError(e.detail()) from None
    except NotConnected as e:
        raise ToolError(f"Game not connected: {e}") from None
    except TimeoutError as e:
        raise ToolError(str(e)) from None


# --------------------------------------------------------------------------- core tools
@mcp.tool(annotations=READ_ONLY)
async def game_status() -> str:
    """Connection, game version, loaded lot, sim time, pump health and recent bridge events.
    Call this first in a session and whenever something seems off."""
    client = get_client()
    try:
        await client.ensure_connected()
    except NotConnected as e:
        tail = config.log_tail("mod_logs/ts4_bridge.log", 15)
        exc = config.log_tail("lastException.txt", 20)
        return _fmt({
            "connected": False,
            "problem": str(e),
            "bridge_file": str(config.bridge_file()),
            "mod_log_tail": tail,
            "last_exception_tail": exc,
            "hint": "Start The Sims 4 (Script Mods enabled) and load a household, then retry.",
        })
    info = await bridge_call("bridge.info")
    out = {"connected": True, **info, "recent_events": client.recent_events(limit=10)}
    return _fmt(out)


@mcp.tool(annotations=DANGEROUS)
async def run_python(code: str, max_chars: int = 4000, max_items: int = 200, reset_namespace: bool = False) -> str:
    """Execute Python on the game's simulation thread and return printed output plus the value of a
    trailing expression (REPL style). Preloaded: services, sims4, sims4.commands, Types, build_buy,
    objects, interactions, alarms, clock, create_object, jsonsafe, g (bridge helpers:
    g.first_client(), g.connection_id(), g.require_zone()). The namespace persists across calls.
    Keep calls short and never block, sleep or spawn threads. Example:
    `sim = services.active_sim_info(); (sim.first_name, sim.age, [c.stat_type.__name__ for c in sim.commodity_tracker])`"""
    result = await bridge_call("bridge.exec", {"code": code, "max_chars": max_chars, "max_items": max_items,
                                               "reset": reset_namespace}, timeout=25.0)
    if not result.get("ok", True):
        err = result.get("error", {})
        parts = [f"{err.get('type')}: {err.get('message')}"]
        if err.get("traceback"):
            parts.append(err["traceback"][-3000:])
        if result.get("stdout"):
            parts.append("stdout:\n" + result["stdout"])
        return "\n".join(parts)
    parts = []
    if result.get("stdout"):
        parts.append(result["stdout"].rstrip())
    if "value" in result:
        parts.append(f"=> ({result.get('value_type')}) " + _fmt(result["value"]))
    if not parts:
        parts.append("(no output)")
    return "\n".join(parts)


@mcp.tool(annotations=MUTATING)
async def cheat(command: str, client_side: bool = False) -> str:
    """Run a Sims 4 console command. Examples: 'sims.modify_funds 5000', 'careers.promote Painter',
    'stats.set_skill_level Major_Painting 10'. Use save_game (not a console command) to save. Set client_side=true for
    bb.* / cas.* client cheats such as 'bb.moveobjects'. Use list_commands to discover commands."""
    result = await bridge_call("bridge.cheat", {"command": command, "client_side": client_side})
    lines = result.get("output") or []
    return "\n".join(lines) if lines else f"ran: {command} (no output)"


@mcp.tool(annotations=READ_ONLY)
async def list_commands(search: str = "") -> str:
    """Search the game's registered console commands (name and usage)."""
    result = await bridge_call("bridge.commands", {"search": search or None})
    if not result:
        return "no commands matched" if search else "command registry unavailable"
    return _fmt(result)


@mcp.tool(annotations=READ_ONLY)
async def events_poll(since_seq: int = 0, limit: int = 100) -> str:
    """Game events (interaction outcomes, dialogs, deaths, zone loads...) with seq > since_seq.
    Events are also delivered to `wait`. Returns the latest seq to pass next time."""
    result = await bridge_call("events.poll", {"since": since_seq, "limit": limit})
    return _fmt(result)


@mcp.tool(annotations=READ_ONLY)
async def bridge_ops() -> str:
    """List every low-level op the in-game bridge exposes (used by run_python-free tooling)."""
    return _fmt(await bridge_call("bridge.ops"))


def mod_source_dir() -> Path | None:
    import os

    env = os.environ.get("TS4_MOD_SRC")
    cand = Path(env) if env else Path(__file__).resolve().parents[2] / "mod"
    return cand if (cand / "ts4_bridge").is_dir() else None


@mcp.tool(annotations=MUTATING)
async def reload_mod(from_source: bool = True) -> str:
    """Hot-reload the bridge's op/helper modules inside the running game (dev aid). With from_source the
    .py files in the repo's mod/ folder are executed into the live modules, so edits apply without
    rebuilding or restarting the game."""
    args: dict[str, Any] = {}
    src = mod_source_dir() if from_source else None
    if src is not None:
        args["src_dir"] = str(src)
    return _fmt(await bridge_call("bridge.reload", args, timeout=25.0))


@mcp.tool(annotations=READ_ONLY)
async def read_game_logs(lines: int = 40) -> str:
    """Tail the bridge log and the game's lastException.txt (script errors land there)."""
    return _fmt({
        "ts4_bridge.log": config.log_tail("mod_logs/ts4_bridge.log", lines),
        "lastException.txt": config.log_tail("lastException.txt", lines),
    })


# --------------------------------------------------------------------------- journal
@mcp.tool(annotations=MUTATING)
async def journal_append(text: str) -> str:
    """Append a dated note to your persistent play journal (goals, plan, discoveries, ids).
    Read it back via the ts4://journal resource or journal_read."""
    import time
    path = config.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {stamp}\n{text.rstrip()}\n")
    return f"journal updated ({path.stat().st_size} bytes)"


@mcp.tool(annotations=READ_ONLY)
async def journal_read(last_chars: int = 6000) -> str:
    """Read the tail of the play journal."""
    path = config.journal_path()
    if not path.exists():
        return "(journal is empty)"
    text = path.read_text(encoding="utf-8")
    return text[-last_chars:]


@mcp.resource("ts4://journal", name="Play journal", mime_type="text/markdown")
def journal_resource() -> str:
    path = config.journal_path()
    return path.read_text(encoding="utf-8") if path.exists() else "(journal is empty)"


def load_tool_modules() -> None:
    """Import domain tool modules so their @mcp.tool registrations run."""
    import importlib

    for name in ("ts4_mcp.tools.live", "ts4_mcp.tools.buy", "ts4_mcp.tools.life", "ts4_mcp.tools.cas"):
        importlib.import_module(name)


load_tool_modules()
