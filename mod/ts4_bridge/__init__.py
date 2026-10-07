"""TS4 Bridge: exposes The Sims 4's Python runtime to the ts4_mcp server over localhost.

The game imports every top-level package inside a .ts4script, so this module's
import is the mod's entry point. Everything is wrapped so that a failure here can
never take the game down with it.
"""
__version__ = '0.1.0'

def _inside_game():
    # game_services only exists inside TS4's interpreter; tests import this package on CPython 3.12.
    try:
        import game_services  # noqa: F401
        return True
    except ImportError:
        return False


try:
    if _inside_game():
        from ts4_bridge import bootstrap as _bootstrap
        _bootstrap.start()
except Exception:  # pragma: no cover - last line of defence
    import traceback
    try:
        from ts4_bridge.log import log
        log('FATAL during bootstrap:\n' + traceback.format_exc())
    except Exception:
        pass
