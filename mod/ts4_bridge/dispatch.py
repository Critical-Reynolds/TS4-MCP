"""Op registry for the TS4 bridge. Python 3.7 compatible.

Usage::

    from ts4_bridge.dispatch import op

    @op('sims.list', doc='List sims', args={'household_only': 'bool=false'})
    def sims_list(household_only=False):
        ...

Handlers run on the game thread and receive ``args`` as keyword arguments.
Raise ``OpError`` for user-facing failures (no traceback is attached).
"""
import inspect
import traceback

OPS = {}


class OpError(Exception):
    """A failure that is the caller's fault or an expected game-state condition."""

    def __init__(self, message, **extra):
        Exception.__init__(self, message)
        self.extra = extra


class OpSpec(object):
    __slots__ = ('name', 'func', 'doc', 'args', 'module')

    def __init__(self, name, func, doc, args):
        self.name = name
        self.func = func
        self.doc = doc
        self.args = args
        self.module = func.__module__

    def describe(self):
        return {'name': self.name, 'doc': self.doc, 'args': self.args, 'module': self.module}


def op(name, doc='', args=None):
    def deco(func):
        if args is None:
            spec_args = _args_from_signature(func)
        else:
            spec_args = args
        OPS[name] = OpSpec(name, func, doc or (func.__doc__ or '').strip(), spec_args)
        return func
    return deco


def _args_from_signature(func):
    out = {}
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return out
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            out[p.name] = 'required'
        else:
            out[p.name] = 'default=%r' % (p.default,)
    return out


def call(name, args):
    """Invoke an op. Returns (ok, payload) where payload is result or error dict."""
    spec = OPS.get(name)
    if spec is None:
        return False, {'type': 'UnknownOp', 'message': 'unknown op %r' % (name,),
                       'known': sorted(OPS.keys())}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return False, {'type': 'ProtocolError', 'message': 'args must be an object'}
    try:
        return True, spec.func(**args)
    except OpError as e:
        err = {'type': 'OpError', 'message': str(e)}
        err.update(e.extra)
        return False, err
    except TypeError as e:
        # Most likely bad kwargs; include the signature to help the caller.
        return False, {'type': 'TypeError', 'message': str(e), 'args': spec.args,
                       'traceback': traceback.format_exc()}
    except Exception as e:
        return False, {'type': type(e).__name__, 'message': str(e),
                       'traceback': traceback.format_exc()}


def describe_all():
    return [OPS[k].describe() for k in sorted(OPS.keys())]
