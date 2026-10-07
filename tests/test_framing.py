"""End-to-end test of the mod transport (stdlib only, runs on 3.12) against the async client."""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import threading
import time

import pytest

from ts4_bridge import dispatch, events
from ts4_bridge.transport import Transport

HEADER = struct.Struct(">I")


def _send(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj).encode()
    sock.sendall(HEADER.pack(len(payload)) + payload)


def _recv(sock: socket.socket) -> dict:
    head = b""
    while len(head) < 4:
        head += sock.recv(4 - len(head))
    (n,) = HEADER.unpack(head)
    body = b""
    while len(body) < n:
        body += sock.recv(n - len(body))
    return json.loads(body)


class FakePump(threading.Thread):
    """Stands in for the game thread: answers hellos and dispatches ops."""

    def __init__(self, transport: Transport):
        super().__init__(daemon=True)
        self.t = transport
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                cid = self.t.pending_hellos.get_nowait()
                self.t.send(cid, {"type": "hello", "ok": True, "protocol": 1})
            except Exception:
                pass
            try:
                cid, req = self.t.inbox.get(timeout=0.02)
            except Exception:
                continue
            ok, payload = dispatch.call(req["op"], req.get("args") or {})
            res = {"type": "res", "id": req["id"], "ok": ok}
            res["result" if ok else "error"] = payload
            self.t.send(cid, res)


@pytest.fixture
def server():
    dispatch.OPS.clear()

    @dispatch.op("test.echo")
    def echo(**kwargs):
        return kwargs

    @dispatch.op("test.boom")
    def boom():
        raise ValueError("kaboom")

    @dispatch.op("test.operror")
    def operror():
        raise dispatch.OpError("expected", code=7)

    t = Transport(token="secret", port=0)
    t.start()
    pump = FakePump(t)
    pump.start()
    yield t
    pump.stop.set()
    t.stop()


def test_auth_and_roundtrip(server: Transport):
    s = socket.create_connection(("127.0.0.1", server.port), timeout=3)
    _send(s, {"type": "auth", "token": "secret", "client": "test"})
    hello = _recv(s)
    assert hello["type"] == "hello" and hello["ok"] is True

    _send(s, {"type": "req", "id": "1", "op": "test.echo", "args": {"a": 1, "b": "x"}})
    res = _recv(s)
    assert res == {"type": "res", "id": "1", "ok": True, "result": {"a": 1, "b": "x"}}

    _send(s, {"type": "req", "id": "2", "op": "test.boom", "args": {}})
    res = _recv(s)
    assert res["ok"] is False and res["error"]["type"] == "ValueError" and "traceback" in res["error"]

    _send(s, {"type": "req", "id": "3", "op": "test.operror", "args": {}})
    res = _recv(s)
    assert res["error"] == {"type": "OpError", "message": "expected", "code": 7}

    _send(s, {"type": "req", "id": "4", "op": "nope", "args": {}})
    res = _recv(s)
    assert res["error"]["type"] == "UnknownOp"
    s.close()


def test_bad_token_rejected(server: Transport):
    s = socket.create_connection(("127.0.0.1", server.port), timeout=3)
    _send(s, {"type": "auth", "token": "wrong"})
    hello = _recv(s)
    assert hello["ok"] is False
    s.close()


def test_async_client_against_transport(server: Transport, monkeypatch):
    from ts4_mcp import bridge_client, config

    monkeypatch.setattr(config, "read_endpoint",
                        lambda: config.BridgeEndpoint("127.0.0.1", server.port, "secret"))

    async def go():
        c = bridge_client.BridgeClient()
        hello = await c.ensure_connected()
        assert hello["ok"]
        assert await c.call("test.echo", {"k": 2}) == {"k": 2}
        with pytest.raises(bridge_client.BridgeError):
            await c.call("test.boom")
        # broadcast an event and make sure the client sees it
        server.broadcast({"type": "event", "seq": 1, "name": "x", "data": {}})
        ev = await c.wait_for_event(lambda e: e["name"] == "x", timeout=2)
        assert ev is not None and ev["seq"] == 1
        await c.close()

    asyncio.run(go())


def test_events_ring():
    events._RING.clear()
    s1 = events.emit("a", {"v": 1})
    s2 = events.emit("b", {"v": 2})
    out = events.poll(since=s1)
    assert [e["seq"] for e in out["events"]] == [s2]
    assert out["seq"] == s2
