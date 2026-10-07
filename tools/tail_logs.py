"""Tail the game's lastException.txt and the bridge's own log.

Usage: python tools/tail_logs.py [--once]
"""
from __future__ import annotations

import argparse
import os
import time

USER_DIR = os.environ.get('TS4_USER_DIR') or os.path.join(
    os.path.expanduser('~'), 'Documents', 'Electronic Arts', 'The Sims 4')
FILES = {
    'EXC': os.path.join(USER_DIR, 'lastException.txt'),
    'UIEXC': os.path.join(USER_DIR, 'lastUIException.txt'),
    'LOG': os.path.join(USER_DIR, 'mod_logs', 'ts4_bridge.log'),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true', help='print current tails and exit')
    ap.add_argument('--lines', type=int, default=40)
    args = ap.parse_args()
    offsets: dict[str, int] = {}
    for tag, path in FILES.items():
        if os.path.exists(path):
            with open(path, 'rb') as f:
                data = f.read()
            tail = data.decode('utf-8', errors='replace').splitlines()[-args.lines:]
            for line in tail:
                print(f'[{tag}] {line}')
            offsets[tag] = len(data)
        else:
            offsets[tag] = 0
    if args.once:
        return
    print('--- tailing (Ctrl+C to stop) ---')
    while True:
        for tag, path in FILES.items():
            if not os.path.exists(path):
                continue
            size = os.path.getsize(path)
            if size < offsets[tag]:
                offsets[tag] = 0  # rotated/truncated
            if size > offsets[tag]:
                with open(path, 'rb') as f:
                    f.seek(offsets[tag])
                    chunk = f.read()
                offsets[tag] = size
                for line in chunk.decode('utf-8', errors='replace').splitlines():
                    print(f'[{tag}] {line}')
        time.sleep(0.5)


if __name__ == '__main__':
    main()
