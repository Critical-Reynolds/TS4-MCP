"""Decompile the installed game's scripts into reference/ with Motherlode (Python 3.7).

Usage: python tools/decompile.py [--game-dir PATH]
Requires .toolchain/py37 (embeddable 3.7.9 with ..\\Motherlode on its path) and .toolchain/Motherlode.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY37 = os.path.join(ROOT, ".toolchain", "py37", "python.exe")
MOTHERLODE = os.path.join(ROOT, ".toolchain", "Motherlode")
OUT = os.path.join(ROOT, "reference")
DEFAULT_GAME = r"C:\Program Files (x86)\Steam\steamapps\common\The Sims 4"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=os.environ.get("TS4_GAME_DIR", DEFAULT_GAME))
    args = ap.parse_args()
    if not os.path.isfile(PY37):
        sys.exit(f"missing {PY37}; extract python-3.7.9-embed-amd64.zip there")
    if not os.path.isdir(MOTHERLODE):
        subprocess.check_call(["git", "clone", "--depth", "1",
                               "https://github.com/BigBadBleuCheese/Motherlode", MOTHERLODE])
    pth = os.path.join(os.path.dirname(PY37), "python37._pth")
    with open(pth, "r+", encoding="utf-8") as f:
        text = f.read()
        if "..\\Motherlode" not in text:
            f.write("\n..\\Motherlode\n")
    subprocess.check_call([PY37, "-m", "motherlode", args.game_dir, "-o", OUT])


if __name__ == "__main__":
    main()
