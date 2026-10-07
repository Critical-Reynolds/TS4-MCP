"""Op modules. Importing this package registers every op. Python 3.7."""
from ts4_bridge.ops import core, state, sims, interactions, objects, dialogs, buy, household, cas  # noqa: F401

# Modules bridge.reload re-executes from source (order matters: helpers before ops).
ALL_MODULES = [
    'ts4_bridge.util.jsonsafe',
    'ts4_bridge.util.game',
    'ts4_bridge.util.lookup',
    'ts4_bridge.hooks',
    'ts4_bridge.ops.core',
    'ts4_bridge.ops.state',
    'ts4_bridge.ops.sims',
    'ts4_bridge.ops.interactions',
    'ts4_bridge.ops.objects',
    'ts4_bridge.ops.dialogs',
    'ts4_bridge.ops.buy',
    'ts4_bridge.ops.household',
    'ts4_bridge.ops.cas',
]
