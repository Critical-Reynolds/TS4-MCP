"""Dialog capture and responses. Python 3.7.

The UI dialog service keeps every open dialog in ``_active_dialogs``. We hook ``dialog_show`` /
``dialog_respond`` / ``_dialog_cancel_internal`` to emit events, and expose list/respond ops.
"""
import services

from ts4_bridge import events
from ts4_bridge.dispatch import op, OpError
from ts4_bridge.log import log
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L

_hooked = globals().setdefault('_hooked', False)


def dialog_brief(dialog, include_responses=True):
    d = {'dialog_id': getattr(dialog, 'dialog_id', None), 'type': type(dialog).__name__}
    for attr in ('title', 'text'):
        try:
            val = getattr(dialog, attr, None)
            if val is None:
                continue
            # tunable factories need to be called with tokens the dialog resolves itself
            if hasattr(dialog, '_get_%s' % attr):
                d[attr] = L.loc(getattr(dialog, '_get_%s' % attr)())
            elif callable(val):
                try:
                    d[attr] = L.loc(val(*getattr(dialog, '_additional_tokens', ())))
                except Exception:
                    d[attr] = L.loc(val)
            else:
                d[attr] = L.loc(val)
        except Exception:
            continue
    try:
        owner = dialog.owner
        if owner is not None:
            d['owner_sim_id'] = owner.sim_id
            d['owner'] = owner.full_name
    except Exception:
        pass
    if include_responses:
        resp = []
        try:
            # adventure moments / preference prompts pass their buttons to show_dialog as
            # additional responses (ids are action indexes), so `responses` alone can be empty
            extra = tuple(getattr(dialog, '_additional_responses', None) or ())
            for r in tuple(dialog.responses or ()) + extra:
                rd = {'response_id': int(r.dialog_response_id)}
                try:
                    rd['text'] = L.loc(r.text) if not callable(r.text) else L.loc(r.text())
                except Exception:
                    pass
                try:
                    rd['ui_request'] = getattr(r.ui_request, 'name', None)
                except Exception:
                    pass
                resp.append(rd)
        except Exception:
            pass
        d['responses'] = resp
        d['is_picker'] = hasattr(dialog, 'picker_rows')
        if d['is_picker']:
            try:
                rows = []
                for row in dialog.picker_rows[:40]:
                    rd = {'option_id': getattr(row, 'option_id', None), 'tag': L.tuning_name(getattr(row, 'tag', None)) if getattr(row, 'tag', None) is not None else None}
                    try:
                        rd['name'] = L.loc(row.name)
                    except Exception:
                        pass
                    try:
                        rd['enabled'] = bool(row.is_enable)
                    except Exception:
                        pass
                    rows.append(rd)
                d['picker_rows'] = rows
            except Exception:
                pass
        d['text_input'] = bool(getattr(dialog, 'text_input_responses', None))
    return d


def active_dialogs():
    try:
        return dict(services.ui_dialog_service()._active_dialogs)
    except Exception:
        return {}


def pending_summary():
    out = []
    for did, dlg in active_dialogs().items():
        try:
            out.append(dialog_brief(dlg, include_responses=False))
        except Exception:
            out.append({'dialog_id': did, 'type': type(dlg).__name__})
    return out


def install_hooks():
    global _hooked
    if _hooked:
        return
    from ui.ui_dialog_service import UiDialogService
    if getattr(UiDialogService.dialog_show, '_ts4_bridge_wrapped', False):
        _hooked = True
        return
    orig_show = UiDialogService.dialog_show
    orig_respond = UiDialogService.dialog_respond
    orig_cancel = UiDialogService._dialog_cancel_internal

    def dialog_show(self, dialog, phone_ring_type, *args, **kwargs):
        result = orig_show(self, dialog, phone_ring_type, *args, **kwargs)
        try:
            if getattr(dialog, 'dialog_id', None) in self._active_dialogs:
                events.emit('dialog.shown', dialog_brief(dialog), sim_ts=g.sim_now_string())
            else:
                # notifications are not kept in _active_dialogs; still worth reporting
                events.emit('notification.shown', dialog_brief(dialog, include_responses=False),
                            sim_ts=g.sim_now_string())
        except Exception as e:
            log('dialog_show hook failed: %r' % (e,))
        return result

    def dialog_respond(self, dialog_id, response, *args, **kwargs):
        result = orig_respond(self, dialog_id, response, *args, **kwargs)
        try:
            events.emit('dialog.closed', {'dialog_id': dialog_id, 'response_id': int(response), 'ok': bool(result)},
                        sim_ts=g.sim_now_string())
        except Exception:
            pass
        return result

    def dialog_cancel_internal(self, dialog, *args, **kwargs):
        result = orig_cancel(self, dialog, *args, **kwargs)
        try:
            events.emit('dialog.closed', {'dialog_id': getattr(dialog, 'dialog_id', None), 'cancelled': True},
                        sim_ts=g.sim_now_string())
        except Exception:
            pass
        return result

    dialog_show._ts4_bridge_wrapped = True
    UiDialogService.dialog_show = dialog_show
    UiDialogService.dialog_respond = dialog_respond
    UiDialogService._dialog_cancel_internal = dialog_cancel_internal
    _hooked = True
    globals()['_hooked'] = True
    log('dialog hooks installed')


@op('dialogs.list', doc='Open dialogs awaiting a player response, with their buttons/picker rows.')
def dialogs_list():
    return [dialog_brief(d) for d in active_dialogs().values()]


@op('dialogs.respond', doc="Answer a dialog: response_id from dialogs.list, or 'ok'/'cancel'/'close'. "
                           "For pickers pass picked=[option_id,...] (or tags) instead.")
def dialogs_respond(dialog_id, response_id=None, picked=None, text=None):
    from ui.ui_dialog import ButtonType
    svc = services.ui_dialog_service()
    dialog = active_dialogs().get(int(dialog_id))
    if dialog is None:
        raise OpError('dialog %s is no longer open' % (dialog_id,), open=list(active_dialogs().keys()))
    if picked is not None:
        results = []
        for p in picked:
            try:
                results.append(int(p))
            except (TypeError, ValueError):
                results.append(p)
        ok = svc.dialog_pick_result(int(dialog_id), picked_results=results)
        return {'ok': bool(ok)}
    if text is not None and hasattr(dialog, 'text_input_responses'):
        try:
            # text inputs are keyed by name; fill the first one
            key = next(iter(dialog.text_input_responses))
            dialog.text_input_responses[key] = str(text)
        except Exception:
            pass
    if response_id is None:
        response_id = 'ok'
    mapping = {'ok': ButtonType.DIALOG_RESPONSE_OK, 'cancel': ButtonType.DIALOG_RESPONSE_CANCEL,
               'close': ButtonType.DIALOG_RESPONSE_CLOSED}
    if isinstance(response_id, str) and response_id.lower() in mapping:
        rid = int(mapping[response_id.lower()])
    else:
        rid = int(response_id)
    ok = svc.dialog_respond(int(dialog_id), rid, g.first_client())
    return {'ok': bool(ok), 'response_id': rid}
