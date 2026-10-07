"""Build and copy the mod into the game's Mods folder.

Usage: python tools/deploy.py [--no-build] [--user-dir PATH]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_mod  # noqa: E402

DEFAULT_USER_DIR = os.path.join(os.path.expanduser('~'), 'Documents', 'Electronic Arts', 'The Sims 4')


def user_dir(explicit: str | None) -> str:
    for c in (explicit, os.environ.get('TS4_USER_DIR'), DEFAULT_USER_DIR):
        if c and os.path.isdir(c):
            return c
    sys.exit('Sims 4 user folder not found; launch the game once or pass --user-dir')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-build', action='store_true')
    ap.add_argument('--user-dir', default=None)
    args = ap.parse_args()
    if not args.no_build:
        build_mod.build(build_mod.find_py37(None))
    udir = user_dir(args.user_dir)
    mods = os.path.join(udir, 'Mods')
    os.makedirs(mods, exist_ok=True)
    dest = os.path.join(mods, 'ts4_bridge.ts4script')
    shutil.copy2(build_mod.OUT, dest)
    print(f'deployed -> {dest}')
    opts = os.path.join(udir, 'Options.ini')
    try:
        with open(opts, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        if 'scriptmodsenabled = 1' not in text:
            print('WARNING: scriptmodsenabled is not 1 in Options.ini; enable Script Mods in game options')
        if 'modsdisabled = 1' in text:
            print('WARNING: modsdisabled = 1 in Options.ini; enable custom content and mods')
    except OSError:
        pass


if __name__ == '__main__':
    main()
