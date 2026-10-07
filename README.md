# TS4-MCP

An MCP server that lets an LLM play **The Sims 4** through the game's own Python runtime. No screen
capture, no synthetic input: a script mod (`ts4_bridge`) inside the game exposes a localhost JSON API,
and `ts4_mcp` turns it into MCP tools for Claude Code / Claude Desktop.

```
Claude Code / Desktop ──stdio──▶ ts4_mcp (Python 3.12, mcp 2.x)
                                     │ TCP 127.0.0.1:47831, length-prefixed JSON, token auth
                                     ▼
                       ts4_bridge.ts4script (Python 3.7, inside TS4_x64.exe)
```

## What the agent can do
Observe everything (`look`, `sim_details`, `list_objects`, `lot_layout`), act through the real
interaction system (`list_interactions`, `do_interaction`), manage time (`wait`, `set_speed`), answer
dialogs, run households (careers, traits, relationships, aging, move-ins, travel, buying a house),
create sims, buy/place/move/sell any catalog object with the game's own placement rules, save, and
execute arbitrary Python on the game thread (`run_python`) for anything else.

What it cannot do (client-side only, not reachable from Python): draw walls/floors/roofs/pools/
terrain, use the main menu, load another save, or the visual Create-a-Sim editor. See
`docs/build-mode-spike.md`.

## Setup (Windows)
1. The Sims 4 installed and launched once. In Game Options → Other enable *Custom Content and Mods*
   and *Script Mods* (or set `scriptmodsenabled = 1` in `Documents\Electronic Arts\The Sims 4\Options.ini`).
2. Toolchain: Python 3.12+ and the Python 3.7.9 embeddable zip extracted to `.toolchain/py37`
   (the game embeds 3.7, mods must be compiled with it).
   ```
   python -m pip install uv
   python -m uv venv --python 3.12 .venv
   python -m uv pip install --python .venv\Scripts\python.exe -e .
   ```
3. Build and deploy the mod, then start the game and load a household:
   ```
   .venv\Scripts\python.exe tools\deploy.py
   ```
4. Register the server with Claude Code (user scope):
   ```
   claude mcp add --transport stdio --scope user ts4 -- <repo>\.venv\Scripts\python.exe -m ts4_mcp
   ```
   Claude Desktop: add the same command/args to `%APPDATA%\Claude\claude_desktop_config.json`.
5. In a Claude session: "Use the ts4 tools. Call game_status, then look."

## Development
- `reference/`: decompiled game scripts and native-module stubs (`tools/decompile.py` wraps Motherlode;
  gitignored). Read these before guessing an API.
- Hot reload: edit anything under `mod/ts4_bridge/ops`, `util` or `hooks.py`, then call the
  `reload_mod` tool; sources are executed into the live modules without restarting the game.
  Changes to `transport.py`, `pump.py`, `dispatch.py`, `bootstrap.py` need `tools/deploy.py` + a game restart.
- Logs: `tools/tail_logs.py` tails `mod_logs/ts4_bridge.log` and `lastException.txt`.
- Tests: `.venv\Scripts\python.exe -m pytest`.
- Protocol: `protocol/PROTOCOL.md`. Findings: `docs/api-findings.md`.
