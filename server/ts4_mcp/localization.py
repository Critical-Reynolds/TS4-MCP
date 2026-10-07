"""Resolve the game's localized string hashes to English text.

The in-game Python only sees string *hashes*; the text lives in STBL resources inside DBPF
packages (``Data/Client/Strings_ENG_US.package`` and the packs' ``Strings_ENG_US.package``).
This module parses those packages once per game version and caches the table as JSON.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path

STBL_TYPE = 0x220557DA
_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ts4_mcp"

_table: dict[int, str] | None = None
_game_dir: Path | None = None

_TOKEN_RE = re.compile(r"\{(\d+)\.([A-Za-z]+)\}")
_BRACED_RE = re.compile(r"\{[^{}]*\}")


def _log(msg: str) -> None:
    sys.stderr.write(f"[ts4_mcp:loc] {msg}\n")
    sys.stderr.flush()


def find_game_dir(hint: str | None = None) -> Path | None:
    cands = [hint, os.environ.get("TS4_GAME_DIR"),
             r"C:\Program Files (x86)\Steam\steamapps\common\The Sims 4",
             r"C:\Program Files\EA Games\The Sims 4",
             r"C:\Program Files (x86)\Origin Games\The Sims 4"]
    for c in cands:
        if c and (Path(c) / "Data" / "Client").is_dir():
            return Path(c)
    return None


# ------------------------------------------------------------------ DBPF / RefPack
def _refpack_decompress(data: bytes, expected: int) -> bytes:
    """EA RefPack (QFS) decompression."""
    pos = 0
    flags = data[0]
    pos = 2
    if flags & 0x80:
        pos += 1  # 4-byte sizes
    size_bytes = 4 if flags & 0x80 else 3
    pos += size_bytes
    if flags & 0x01:
        pos += size_bytes
    out = bytearray()
    n = len(data)
    while pos < n:
        b0 = data[pos]
        if b0 < 0x80:
            b1 = data[pos + 1]
            pos += 2
            lit = b0 & 0x03
            out += data[pos:pos + lit]
            pos += lit
            copy_len = ((b0 & 0x1C) >> 2) + 3
            offset = ((b0 & 0x60) << 3) + b1 + 1
            start = len(out) - offset
            for i in range(copy_len):
                out.append(out[start + i])
        elif b0 < 0xC0:
            b1, b2 = data[pos + 1], data[pos + 2]
            pos += 3
            lit = (b1 & 0xC0) >> 6
            out += data[pos:pos + lit]
            pos += lit
            copy_len = (b0 & 0x3F) + 4
            offset = ((b1 & 0x3F) << 8) + b2 + 1
            start = len(out) - offset
            for i in range(copy_len):
                out.append(out[start + i])
        elif b0 < 0xE0:
            b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
            pos += 4
            lit = b0 & 0x03
            out += data[pos:pos + lit]
            pos += lit
            copy_len = ((b0 & 0x0C) << 6) + b3 + 5
            offset = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1
            start = len(out) - offset
            for i in range(copy_len):
                out.append(out[start + i])
        elif b0 < 0xFC:
            lit = ((b0 & 0x1F) << 2) + 4
            pos += 1
            out += data[pos:pos + lit]
            pos += lit
        else:
            lit = b0 & 0x03
            pos += 1
            out += data[pos:pos + lit]
            pos += lit
            break
    return bytes(out)


def _iter_stbl_resources(path: Path):
    with path.open("rb") as f:
        head = f.read(96)
        if head[:4] != b"DBPF":
            return
        major, minor = struct.unpack_from("<II", head, 4)
        entry_count = struct.unpack_from("<I", head, 36)[0]
        index_size = struct.unpack_from("<I", head, 44)[0]
        index_pos = struct.unpack_from("<Q", head, 64)[0] if major >= 2 else struct.unpack_from("<I", head, 40)[0]
        f.seek(index_pos)
        index = f.read(index_size)
        off = 0
        (flags,) = struct.unpack_from("<I", index, off)
        off += 4
        const_type = const_group = const_inst_hi = None
        if flags & 1:
            (const_type,) = struct.unpack_from("<I", index, off); off += 4
        if flags & 2:
            (const_group,) = struct.unpack_from("<I", index, off); off += 4
        if flags & 4:
            (const_inst_hi,) = struct.unpack_from("<I", index, off); off += 4
        entries = []
        for _ in range(entry_count):
            if const_type is None:
                (rtype,) = struct.unpack_from("<I", index, off); off += 4
            else:
                rtype = const_type
            if const_group is None:
                (group,) = struct.unpack_from("<I", index, off); off += 4
            else:
                group = const_group
            if const_inst_hi is None:
                (inst_hi,) = struct.unpack_from("<I", index, off); off += 4
            else:
                inst_hi = const_inst_hi
            inst_lo, offset, size_raw, size_dec, comp, _committed = struct.unpack_from("<IIIIHH", index, off)
            off += 20
            size = size_raw & 0x7FFFFFFF
            if rtype == STBL_TYPE:
                entries.append((inst_hi, inst_lo, offset, size, size_dec, comp))
        for inst_hi, inst_lo, offset, size, size_dec, comp in entries:
            # language is the high byte of the instance: 0x00 = ENG_US
            if (inst_hi >> 24) != 0:
                continue
            f.seek(offset)
            raw = f.read(size)
            try:
                if comp == 0x5A42:
                    data = zlib.decompress(raw)
                elif comp == 0xFFFF:
                    data = _refpack_decompress(raw, size_dec)
                elif comp == 0:
                    data = raw
                else:
                    continue
            except Exception as e:
                _log(f"decompress failed in {path.name}: {e!r}")
                continue
            yield data


def _parse_stbl(data: bytes, into: dict[int, str]) -> int:
    if data[:4] != b"STBL":
        return 0
    version = struct.unpack_from("<H", data, 4)[0]
    off = 6
    off += 1  # compressed flag
    (num_entries,) = struct.unpack_from("<Q", data, off); off += 8
    off += 2  # reserved
    off += 4  # strings length
    count = 0
    for _ in range(num_entries):
        if off + 7 > len(data):
            break
        key, flags, length = struct.unpack_from("<IBH", data, off)
        off += 7
        text = data[off:off + length].decode("utf-8", errors="replace")
        off += length
        into[key] = text
        count += 1
    return count


def _build_table(game_dir: Path) -> dict[int, str]:
    table: dict[int, str] = {}
    packages = sorted(game_dir.rglob("Strings_ENG_US.package"))
    # also delta/client builds carry STBLs for newer strings
    packages += sorted(game_dir.glob("Delta/**/*.package")) if (game_dir / "Delta").exists() else []
    for pkg in packages:
        try:
            if pkg.stat().st_size < 96:  # empty placeholder packages are common in Delta/
                continue
            for blob in _iter_stbl_resources(pkg):
                _parse_stbl(blob, table)
        except Exception as e:
            _log(f"skip {pkg}: {e!r}")
    _log(f"string table built: {len(table)} entries from {len(packages)} packages")
    return table


def _version_key(game_dir: Path) -> str:
    ini = game_dir / "Game" / "Bin" / "Default.ini"
    try:
        m = re.search(r"gameversion\s*=\s*([\d.]+)", ini.read_text(errors="replace"))
        if m:
            return m.group(1)
    except OSError:
        pass
    return "unknown"


def load(game_dir_hint: str | None = None) -> dict[int, str]:
    global _table, _game_dir
    if _table is not None:
        return _table
    gd = find_game_dir(game_dir_hint)
    if gd is None:
        _log("game dir not found; names will stay as hashes")
        _table = {}
        return _table
    _game_dir = gd
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _CACHE_DIR / f"strings_{_version_key(gd)}.json"
    if cache.exists():
        try:
            _table = {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
            return _table
        except Exception:
            pass
    _table = _build_table(gd)
    try:
        cache.write_text(json.dumps(_table, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return _table


def text(hash_value: int | None, tokens: list | None = None, sims: dict[int, str] | None = None) -> str | None:
    """English text for a hash; token placeholders are filled when we know them, else simplified."""
    if hash_value is None:
        return None
    table = load()
    s = table.get(int(hash_value))
    if s is None:
        return None
    tokens = tokens or []

    def repl(m: re.Match) -> str:
        idx, kind = int(m.group(1)), m.group(2)
        tok = tokens[idx] if idx < len(tokens) else None
        if isinstance(tok, dict):
            if "sim_id" in tok:
                name = tok.get("name") or (sims or {}).get(tok["sim_id"])
                if name:
                    k = kind.lower()
                    if k.startswith("simfirst"):
                        return tok.get("first_name") or name.split(" ")[0]
                    if k.startswith("simlast"):
                        return name.split(" ")[-1]
                    return name
            if "text" in tok:
                return str(tok["text"])
            if "number" in tok:
                return str(tok["number"])
            if "hash" in tok:
                inner = text(tok.get("hash"), tok.get("tokens"), sims)
                if inner:
                    return inner
        return f"<{kind}>"

    s = _TOKEN_RE.sub(repl, s)
    # collapse gendered/plural variants like {M0.he}{F0.she} -> he/she
    s = _BRACED_RE.sub(lambda m: m.group(0).strip("{}").split(".")[-1], s)
    return s.strip()


def resolve(obj, sims: dict[int, str] | None = None):
    """Recursively replace {'hash': .., 'tokens': ..} dicts with {'text': .., 'hash': ..}."""
    if isinstance(obj, dict):
        if "hash" in obj and set(obj.keys()) <= {"hash", "tokens"}:
            t = text(obj.get("hash"), obj.get("tokens"), sims)
            return t if t is not None else f"<str {obj.get('hash')}>"
        return {k: resolve(v, sims) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v, sims) for v in obj]
    return obj
