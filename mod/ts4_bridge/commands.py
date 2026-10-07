"""In-game console commands for manual testing. Python 3.7."""
import sims4.commands


@sims4.commands.Command('ts4mcp.status', command_type=sims4.commands.CommandType.Live)
def _status(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        from ts4_bridge import bootstrap, pump
        p = pump.current()
        t = bootstrap.transport
        output('ts4_bridge %s protocol %d' % (bootstrap.MOD_VERSION, bootstrap.PROTOCOL_VERSION))
        output('listening on 127.0.0.1:%s, clients=%d' % (t.port if t else '?', t.connection_count() if t else 0))
        output('pump installed=%s ticks=%s stats=%s' % (p.installed if p else None, p.ticks if p else None, p.stats if p else None))
        output('bridge file: %s' % bootstrap.paths.BRIDGE_FILE)
    except Exception as e:
        output('ts4mcp.status failed: %r' % (e,))


@sims4.commands.Command('ts4mcp.reload', command_type=sims4.commands.CommandType.Live)
def _reload(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        from ts4_bridge.ops.core import reload
        output(str(reload()))
    except Exception as e:
        output('reload failed: %r' % (e,))
