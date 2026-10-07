"""Locate the game's user folder and the bridge discovery file."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_DIR = Path.home() / "Documents" / "Electronic Arts" / "The Sims 4"


def user_dir() -> Path:
    env = os.environ.get("TS4_USER_DIR")
    if env:
        return Path(env)
    one_drive = Path.home() / "OneDrive" / "Documents" / "Electronic Arts" / "The Sims 4"
    if not DEFAULT_USER_DIR.exists() and one_drive.exists():
        return one_drive
    return DEFAULT_USER_DIR


def bridge_file() -> Path:
    return user_dir() / "mod_data" / "ts4_bridge" / "bridge.json"


@dataclass
class BridgeEndpoint:
    host: str
    port: int
    token: str
    pid: int | None = None
    mod_version: str | None = None
    protocol: int | None = None


def read_endpoint() -> BridgeEndpoint | None:
    """Read bridge.json, honouring TS4_BRIDGE_PORT / TS4_BRIDGE_TOKEN overrides."""
    data: dict = {}
    path = bridge_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    port = os.environ.get("TS4_BRIDGE_PORT") or data.get("port")
    token = os.environ.get("TS4_BRIDGE_TOKEN") or data.get("token")
    if not port or not token:
        return None
    return BridgeEndpoint(
        host="127.0.0.1",
        port=int(port),
        token=str(token),
        pid=data.get("pid"),
        mod_version=data.get("mod_version"),
        protocol=data.get("protocol"),
    )


def journal_path() -> Path:
    env = os.environ.get("TS4_MCP_JOURNAL")
    if env:
        return Path(env)
    return user_dir() / "mod_data" / "ts4_bridge" / "journal.md"


def log_tail(name: str, lines: int = 40) -> str:
    """Last lines of a file in the user dir (lastException.txt, mod_logs/ts4_bridge.log)."""
    path = user_dir() / name
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
