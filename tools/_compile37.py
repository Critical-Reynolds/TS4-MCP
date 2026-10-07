"""Compile a source tree to .pyc with the running interpreter (meant for Python 3.7).

Usage: python _compile37.py <src_pkg_dir> <out_pkg_dir>
Writes legacy-layout .pyc files (out/pkg/module.pyc) so zipimport inside the game finds them.
"""
import os
import py_compile
import sys


def main(src, out):
    count = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        rel = os.path.relpath(root, src)
        dest_dir = os.path.join(out, rel) if rel != '.' else out
        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)
        for name in files:
            if not name.endswith('.py'):
                continue
            src_file = os.path.join(root, name)
            dest_file = os.path.join(dest_dir, name[:-3] + '.pyc')
            py_compile.compile(src_file, cfile=dest_file, dfile=os.path.join('ts4_bridge', rel, name).replace('\\', '/'),
                               doraise=True, optimize=0)
            count += 1
    print('compiled %d files with Python %s' % (count, sys.version.split()[0]))


if __name__ == '__main__':
    if sys.version_info[:2] != (3, 7):
        sys.stderr.write('WARNING: expected Python 3.7, got %s\n' % sys.version.split()[0])
    main(sys.argv[1], sys.argv[2])
