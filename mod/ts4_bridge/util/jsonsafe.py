"""Convert arbitrary game objects into JSON-serialisable data with size limits. Python 3.7."""
import enum as _stdenum  # the game replaces stdlib enum; it may lack Enum
_ENUM_BASE = getattr(_stdenum, "Enum", None) or getattr(_stdenum, "Int", None) or ()

_PRIMITIVES = (str, int, float, bool, type(None))


def jsonsafe(value, max_depth=6, max_items=200, max_chars=4000, _depth=0):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + '...<%d more>' % (len(value) - max_chars)
    if isinstance(value, bytes):
        return '<bytes %d>' % len(value)
    if _depth >= max_depth:
        return _repr(value, max_chars)
    kwargs = dict(max_depth=max_depth, max_items=max_items, max_chars=max_chars, _depth=_depth + 1)
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= max_items:
                out['...'] = '%d more items' % (len(value) - max_items)
                break
            out[_key(k)] = jsonsafe(v, **kwargs)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        seq = list(value)
        out = [jsonsafe(v, **kwargs) for v in seq[:max_items]]
        if len(seq) > max_items:
            out.append('...<%d more items>' % (len(seq) - max_items))
        return out
    # enum-ish (game enums subclass int but have a name)
    name = getattr(value, 'name', None)
    if (_ENUM_BASE and isinstance(value, _ENUM_BASE)) or (isinstance(name, str) and hasattr(type(value), '__members__')):
        return str(name)
    # Vector3 / Quaternion / Transform style objects
    for attrs in (('x', 'y', 'z', 'w'), ('x', 'y', 'z')):
        if all(hasattr(value, a) for a in attrs) and not hasattr(value, '__iter__'):
            try:
                return {a: float(getattr(value, a)) for a in attrs}
            except Exception:
                break
    if hasattr(value, '__iter__') and not hasattr(value, '__len__'):
        # generator or other iterable
        try:
            return jsonsafe(list(value), **kwargs)
        except Exception:
            pass
    return _repr(value, max_chars)


def _key(k):
    if isinstance(k, str):
        return k
    try:
        return str(k)
    except Exception:
        return repr(k)


def _repr(value, max_chars):
    try:
        r = repr(value)
    except Exception as e:
        r = '<repr failed: %r>' % (e,)
    if len(r) > max_chars:
        r = r[:max_chars] + '...'
    return r
