"""Build mod/ts4_bridge into build/ts4_bridge.ts4script using the Python 3.7 toolchain.

Usage: python tools/build_mod.py [--py37 PATH]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'mod', 'ts4_bridge')
BUILD = os.path.join(ROOT, 'build')
PYC_DIR = os.path.join(BUILD, 'pyc', 'ts4_bridge')
OUT = os.path.join(BUILD, 'ts4_bridge.ts4script')
DEFAULT_PY37 = os.path.join(ROOT, '.toolchain', 'py37', 'python.exe')


def find_py37(explicit: str | None) -> str:
    candidates = [explicit, os.environ.get('TS4_PY37'), DEFAULT_PY37]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    for c in ('py -3.7', 'python3.7'):
        try:
            out = subprocess.run(c.split() + ['-c', 'import sys;print(sys.executable)'],
                                 capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    sys.exit('Python 3.7 not found. Put the embeddable 3.7.9 in .toolchain/py37 or set TS4_PY37.')


def build(py37: str) -> str:
    if os.path.isdir(os.path.join(BUILD, 'pyc')):
        shutil.rmtree(os.path.join(BUILD, 'pyc'))
    os.makedirs(PYC_DIR, exist_ok=True)
    compiler = os.path.join(ROOT, 'tools', '_compile37.py')
    res = subprocess.run([py37, compiler, SRC, PYC_DIR], capture_output=True, text=True)
    sys.stdout.write(res.stdout)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        sys.exit('compile failed')
    if os.path.exists(OUT):
        os.remove(OUT)
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(PYC_DIR):
            for name in sorted(files):
                full = os.path.join(root, name)
                arc = os.path.relpath(full, os.path.join(BUILD, 'pyc')).replace('\\', '/')
                zf.write(full, arc)
    size = os.path.getsize(OUT)
    print(f'wrote {OUT} ({size} bytes)')
    return OUT


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--py37', default=None)
    args = ap.parse_args()
    build(find_py37(args.py37))


if __name__ == '__main__':
    main()
