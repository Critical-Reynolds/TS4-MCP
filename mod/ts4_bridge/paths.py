"""Filesystem locations for the mod. Python 3.7 compatible."""
import os

MOD_NAME = 'ts4_bridge'


def _find_user_dir():
    # __file__ looks like ...\Mods\ts4_bridge.ts4script\ts4_bridge\paths.pyc
    here = os.path.abspath(__file__)
    probe = here
    for _ in range(8):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        if os.path.basename(parent).lower() == 'mods':
            return os.path.dirname(parent)
        probe = parent
    return os.path.join(os.path.expanduser('~'), 'Documents', 'Electronic Arts', 'The Sims 4')


USER_DIR = _find_user_dir()
MODS_DIR = os.path.join(USER_DIR, 'Mods')
MOD_LOGS_DIR = os.path.join(USER_DIR, 'mod_logs')
MOD_DATA_DIR = os.path.join(USER_DIR, 'mod_data', MOD_NAME)
LOG_FILE = os.path.join(MOD_LOGS_DIR, MOD_NAME + '.log')
BRIDGE_FILE = os.path.join(MOD_DATA_DIR, 'bridge.json')
GAME_VERSION_FILE = os.path.join(USER_DIR, 'GameVersion.txt')


def ensure_dirs():
    for d in (MOD_LOGS_DIR, MOD_DATA_DIR):
        try:
            os.makedirs(d)
        except OSError:
            pass


def game_version():
    try:
        with open(GAME_VERSION_FILE, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        # file begins with a few control bytes in some installs
        return ''.join(ch for ch in text if ch.isdigit() or ch == '.').strip('.') or text.strip()
    except OSError:
        return 'unknown'
