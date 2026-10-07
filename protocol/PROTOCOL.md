# TS4 Bridge wire protocol (v1)

Two processes:

- **ts4_bridge** (`.ts4script`, Python 3.7, inside `TS4_x64.exe`) listens on `127.0.0.1`.
- **ts4_mcp** (Python 3.12, the MCP server) connects as a client.

## Discovery

On load the mod writes `<TS4 user dir>\mod_data\ts4_bridge\bridge.json`:

```json
{"port": 47831, "token": "<32 hex chars>", "pid": 1234, "mod_version": "0.1.0", "protocol": 1}
```

`<TS4 user dir>` is `Documents\Electronic Arts\The Sims 4`. The MCP server reads this file to find
the port and token. Environment overrides: `TS4_BRIDGE_PORT`, `TS4_BRIDGE_TOKEN`, `TS4_USER_DIR`.

## Framing

Every message is `uint32 big-endian length` + UTF-8 JSON object. Max frame 16 MiB.

## Handshake

Client sends first:

```json
{"type": "auth", "token": "...", "client": "ts4_mcp/0.1.0"}
```

Server replies:

```json
{"type": "hello", "ok": true, "protocol": 1, "mod_version": "0.1.0",
 "game_version": "1.128.90.1030", "python": "3.7.0", "zone_id": 123, "zone_loaded": true}
```

or `{"type": "hello", "ok": false, "error": "bad token"}` and closes.

## Requests and responses

```json
{"type": "req", "id": "a1", "op": "sims.list", "args": {"household_only": true}}
{"type": "res", "id": "a1", "ok": true, "result": [...], "ms": 3.2}
{"type": "res", "id": "a1", "ok": false,
 "error": {"type": "KeyError", "message": "...", "traceback": "..."}}
```

- `op` names are `domain.verb`. `args` is always an object (may be empty).
- Ops execute **on the game thread** inside the `Zone.update` tick pump. The transport thread never
  touches game state.
- A request is answered exactly once. Ordering of responses is not guaranteed.
- Ops that take longer than one tick return a **job**: `{"job_id": "...", "status": "running"}`.
  Poll with `jobs.get` or wait for the `job.done` event.

## Events

The bridge pushes events as they happen and also buffers the last 2000 in a ring buffer:

```json
{"type": "event", "seq": 1041, "ts": 1760000000.1, "sim_ts": "Day 3 09:15",
 "name": "interaction.finished", "data": {"sim_id": 1, "affordance": "...", "outcome": "success"}}
```

`events.poll {"since": seq, "limit": 200}` returns `{"seq": latest, "events": [...]}` for catch-up.

Event names (initial set): `zone.loading`, `zone.loaded`, `save.done`, `dialog.shown`, `dialog.closed`,
`notification.shown`, `interaction.queued`, `interaction.started`, `interaction.finished`,
`sim.spawned`, `sim.died`, `sim.aged`, `sim.buff_added`, `sim.skill_level`, `household.funds`,
`job.done`, `bridge.error`.

## Core ops (always present)

| op | args | result |
|---|---|---|
| `bridge.ping` | | `{"pong": true, "tick": n}` |
| `bridge.info` | | versions, zone, uptime, op list |
| `bridge.ops` | | `[{"name", "doc", "args"}]` |
| `bridge.exec` | `{"code": str, "mode": "eval"\|"exec", "timeout_ms"?}` | `{"value": repr/JSON, "stdout": str}` |
| `bridge.cheat` | `{"command": str, "client_side": bool}` | `{"output": [lines]}` |
| `bridge.reload` | `{"modules"?: [str]}` | reloaded module names |
| `events.poll` | `{"since": int, "limit": int}` | see above |
| `jobs.get` | `{"job_id": str}` | job state |

Domain ops (`state.*`, `sims.*`, `interactions.*`, `time.*`, `household.*`, `world.*`, `objects.*`,
`build.*`, `cas.*`, `dialogs.*`, `persistence.*`) are documented in `bridge.ops` output and in the
server's tool docstrings. `tests/test_protocol_parity.py` asserts the server only calls ops the mod
declares.

## Versioning

`protocol` is bumped on incompatible framing/handshake changes. The server refuses to run when the
mod's `protocol` differs and tells the LLM to rebuild/redeploy the mod.
