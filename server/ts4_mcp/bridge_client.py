"""Async TCP client for the in-game ts4_bridge mod.

Framing: uint32 big-endian length + UTF-8 JSON. See protocol/PROTOCOL.md.
"""
from __future__ import annotations

import asyncio
import collections
import json
import struct
import sys
import time
import uuid
from typing import Any, Callable

from ts4_mcp import config

HEADER = struct.Struct(">I")
PROTOCOL_VERSION = 1
CLIENT_NAME = "ts4_mcp/0.1.0"


class BridgeError(Exception):
    """The game returned an error for an op (message is model-readable)."""

    def __init__(self, op: str, error: dict[str, Any]):
        self.op = op
        self.error = error or {}
        etype = self.error.get("type", "Error")
        msg = self.error.get("message", "")
        super().__init__(f"{op} failed: {etype}: {msg}")

    def detail(self) -> str:
        parts = [str(self)]
        tb = self.error.get("traceback")
        if tb:
            parts.append(tb[-2500:])
        extra = {k: v for k, v in self.error.items() if k not in ("type", "message", "traceback")}
        if extra:
            parts.append(json.dumps(extra, default=str)[:1500])
        return "\n".join(parts)


class NotConnected(Exception):
    pass


def _log(msg: str) -> None:
    sys.stderr.write(f"[ts4_mcp] {msg}\n")
    sys.stderr.flush()


class BridgeClient:
    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.hello: dict[str, Any] = {}
        self.endpoint: config.BridgeEndpoint | None = None
        self.events: collections.deque[dict] = collections.deque(maxlen=5000)
        self.last_seq = 0
        self._event_waiters: list[tuple[Callable[[dict], bool], asyncio.Future]] = []
        self.connected_at: float | None = None

    # ---- connection ------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def ensure_connected(self, timeout: float = 5.0) -> dict[str, Any]:
        if self.connected:
            return self.hello
        async with self._lock:
            if self.connected:
                return self.hello
            return await self._connect(timeout)

    async def _connect(self, timeout: float) -> dict[str, Any]:
        ep = config.read_endpoint()
        if ep is None:
            raise NotConnected(
                f"Bridge discovery file not found at {config.bridge_file()}. "
                "Is The Sims 4 running with the ts4_bridge mod installed and Script Mods enabled?"
            )
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ep.host, ep.port), timeout)
        except (OSError, asyncio.TimeoutError) as e:
            raise NotConnected(
                f"Cannot connect to the game bridge on {ep.host}:{ep.port} ({e}). "
                "The game may be closed, still loading, or the mod failed to start "
                "(check Documents/Electronic Arts/The Sims 4/mod_logs/ts4_bridge.log)."
            ) from e
        self._reader, self._writer, self.endpoint = reader, writer, ep
        await self._send({"type": "auth", "token": ep.token, "client": CLIENT_NAME})
        try:
            deadline = time.monotonic() + timeout
            while True:
                hello = await asyncio.wait_for(self._read_frame(), max(0.1, deadline - time.monotonic()))
                if hello and hello.get("type") == "event":
                    self._on_event(hello)  # broadcasts can race the hello; keep them
                    continue
                break
        except asyncio.TimeoutError as e:
            await self.close()
            raise NotConnected("Game accepted the connection but did not answer the handshake within "
                               f"{timeout}s; the game thread may be stalled or the pump not installed.") from e
        if not hello or hello.get("type") != "hello" or not hello.get("ok"):
            await self.close()
            health = (hello or {}).get("health")
            if health:
                raise NotConnected(
                    f"{(hello or {}).get('error')}. Pump health: {health}. The game's simulation thread is "
                    "not running the bridge; the game may be frozen, on a loading screen, or needs a restart."
                )
            raise NotConnected(f"Handshake rejected: {hello}")
        if hello.get("protocol") != PROTOCOL_VERSION:
            await self.close()
            raise NotConnected(
                f"Protocol mismatch: mod speaks v{hello.get('protocol')}, server expects v{PROTOCOL_VERSION}. "
                "Rebuild and redeploy the mod (python tools/deploy.py) and restart the game."
            )
        self.hello = hello
        self.connected_at = time.time()
        self._read_task = asyncio.create_task(self._read_loop())
        _log(f"connected to game bridge on port {ep.port}: {hello.get('game_version')} zone={hello.get('zone_id')}")
        return hello

    async def close(self) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        self._writer = None
        self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(NotConnected("connection closed"))
        self._pending.clear()

    # ---- framing ---------------------------------------------------------
    async def _send(self, obj: dict[str, Any]) -> None:
        if self._writer is None:
            raise NotConnected("not connected")
        payload = json.dumps(obj, default=str).encode("utf-8")
        self._writer.write(HEADER.pack(len(payload)) + payload)
        await self._writer.drain()

    async def _read_frame(self) -> dict[str, Any] | None:
        assert self._reader is not None
        head = await self._reader.readexactly(HEADER.size)
        (length,) = HEADER.unpack(head)
        body = await self._reader.readexactly(length)
        return json.loads(body.decode("utf-8"))

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await self._read_frame()
                if msg is None:
                    continue
                mtype = msg.get("type")
                if mtype == "res":
                    fut = self._pending.pop(msg.get("id"), None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                elif mtype == "event":
                    self._on_event(msg)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception as e:  # pragma: no cover
            _log(f"read loop error: {e!r}")
        finally:
            _log("disconnected from game bridge")
            await self.close()

    def _on_event(self, ev: dict[str, Any]) -> None:
        self.events.append(ev)
        self.last_seq = max(self.last_seq, int(ev.get("seq", 0)))
        still_waiting = []
        for pred, fut in self._event_waiters:
            if fut.done():
                continue
            try:
                hit = pred(ev)
            except Exception:
                hit = False
            if hit:
                fut.set_result(ev)
            else:
                still_waiting.append((pred, fut))
        self._event_waiters = still_waiting

    # ---- public API ------------------------------------------------------
    async def call(self, op: str, args: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
        """Call an op; returns the result or raises BridgeError / NotConnected."""
        await self.ensure_connected()
        req_id = uuid.uuid4().hex[:10]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        try:
            await self._send({"type": "req", "id": req_id, "op": op, "args": args or {}})
            res = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(
                f"{op} did not answer within {timeout}s. The game may be paused on a loading screen, "
                "frozen, or the op is long-running; check game_status."
            )
        if not res.get("ok"):
            raise BridgeError(op, res.get("error") or {})
        return res.get("result")

    async def wait_for_event(self, predicate: Callable[[dict], bool], timeout: float) -> dict | None:
        """Resolve with the first *future* event matching predicate, or None on timeout."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._event_waiters.append((predicate, fut))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None

    def recent_events(self, since_seq: int = 0, limit: int = 100) -> list[dict]:
        items = [e for e in self.events if int(e.get("seq", 0)) > since_seq]
        return items[-limit:]


_client: BridgeClient | None = None


def get_client() -> BridgeClient:
    global _client
    if _client is None:
        _client = BridgeClient()
    return _client
