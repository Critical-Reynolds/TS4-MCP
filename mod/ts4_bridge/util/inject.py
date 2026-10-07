"""Scumbumbo-style function injection. Python 3.7 compatible."""
from functools import wraps


def inject(target_function, new_function):
    @wraps(target_function)
    def _inject(*args, **kwargs):
        return new_function(target_function, *args, **kwargs)
    return _inject


def inject_to(target_object, target_function_name):
    """Decorator: ``new(original, *args, **kwargs)`` replaces ``target_object.name``."""
    def _inject_to(new_function):
        target_function = getattr(target_object, target_function_name)
        setattr(target_object, target_function_name, inject(target_function, new_function))
        return new_function
    return _inject_to


def safe_inject_to(target_object, target_function_name, log=None):
    """Like inject_to but exceptions in the new function are swallowed and logged,
    and the original is always called."""
    def _inject_to(new_function):
        target_function = getattr(target_object, target_function_name)

        @wraps(target_function)
        def _wrapper(*args, **kwargs):
            result = target_function(*args, **kwargs)
            try:
                new_function(result, *args, **kwargs)
            except Exception as e:
                if log is not None:
                    import traceback
                    log('injection %s.%s failed: %r\n%s' % (
                        getattr(target_object, '__name__', target_object), target_function_name, e,
                        traceback.format_exc()))
            return result
        setattr(target_object, target_function_name, _wrapper)
        return new_function
    return _inject_to
