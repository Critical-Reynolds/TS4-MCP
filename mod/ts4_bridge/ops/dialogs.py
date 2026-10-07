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


def _row_sim_id(row):
    sid = getattr(row, 'sim_id', None)
    if sid:
        return int(sid)
    tag = getattr(row, 'tag', None)
    if isinstance(tag, int) and not isinstance(tag, bool) and tag > 1 << 40:
        try:
            if services.sim_info_manager().get(int(tag)) is not None:
                return int(tag)
        except Exception:
            pass
    return None


def _is_phone_call(dialog):
    if getattr(dialog, '_ts4_was_call', False):
        return True
    try:
        from ui.ui_dialog import PhoneRingType
        return dialog.get_phone_ring_type() != PhoneRingType.NO_RING
    except Exception:
        return False


def dialog_brief(dialog, include_responses=True):
    d = {'dialog_id': getattr(dialog, 'dialog_id', None), 'type': type(dialog).__name__}
    if _is_phone_call(dialog):
        d['phone_call'] = True
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
                for row in dialog.picker_rows[:80]:
                    rd = {'option_id': getattr(row, 'option_id', None), 'tag': L.tuning_name(getattr(row, 'tag', None)) if getattr(row, 'tag', None) is not None else None}
                    try:
                        rd['name'] = L.loc(row.name)
                    except Exception:
                        pass
                    try:
                        rd['enabled'] = bool(row.is_enable)
                    except Exception:
                        pass
                    sim_id = _row_sim_id(row)
                    if sim_id:
                        rd['sim_id'] = int(sim_id)
                        try:
                            rd['sim'] = L.sim_name(services.sim_info_manager().get(int(sim_id)))
                        except Exception:
                            pass
                    rows.append(rd)
                d['picker_rows'] = rows
                d['max_selectable'] = getattr(getattr(dialog, 'max_selectable', None), 'number_selectable', None)                     if not isinstance(getattr(dialog, 'max_selectable', None), int) else dialog.max_selectable
                d['how_to_answer'] = 'respond_dialog(dialog_id, picked=[option_id | sim_id | name, ...]) picks and confirms'
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


def agent_connected():
    try:
        from ts4_bridge import bootstrap
        return bootstrap.transport is not None and bootstrap.transport.connection_count() > 0
    except Exception:
        return False


_settings = globals().setdefault('_settings', {'calls_as_popups': True})


def install_ring_hook():
    """While the agent is connected, deliver incoming phone calls as their pop-up dialog instead of a
    ringing phone. The game has no message to stop a ringing phone on screen, so a call the agent answers
    in the background would keep ringing; a pop-up is closed by the normal dialog-close message."""
    from ui.ui_dialog_service import UiDialogService
    current = UiDialogService.dialog_show
    if getattr(current, '_ts4_ring_wrapped', False):
        return True
    from ui.ui_dialog import PhoneRingType

    def dialog_show(self, dialog, phone_ring_type, *args, **kwargs):
        if phone_ring_type != PhoneRingType.NO_RING and _settings.get('calls_as_popups') and agent_connected():
            try:
                dialog._ts4_was_call = True
                events.emit('phone.ringing', {'dialog_id': getattr(dialog, 'dialog_id', None),
                                              'ring_type': L.enum_name(phone_ring_type)},
                            sim_ts=g.sim_now_string())
            except Exception:
                pass
            phone_ring_type = PhoneRingType.NO_RING
        return current(self, dialog, phone_ring_type, *args, **kwargs)

    dialog_show._ts4_ring_wrapped = True
    dialog_show._ts4_bridge_wrapped = True
    UiDialogService.dialog_show = dialog_show
    log('phone calls are shown as pop-ups while the agent is connected')
    return True


def _ring_tick():
    try:
        if not globals().get('_hooked'):
            return
        if install_ring_hook():
            from ts4_bridge import hooks
            hooks._tick_callbacks.pop('ring_hook', None)
    except Exception as e:
        log('ring hook install failed: %r' % (e,))


try:
    from ts4_bridge import hooks as _hooks
    _hooks.register_tick('ring_hook', _ring_tick)
except Exception:
    pass


@op('dialogs.settings', doc='calls_as_popups: when true (default) incoming phone calls appear as their pop-up while '
                            'the agent is connected, so answering them closes them on screen.')
def dialogs_settings(calls_as_popups=None):
    if calls_as_popups is not None:
        _settings['calls_as_popups'] = bool(calls_as_popups)
    return dict(_settings)


@op('dialogs.list', doc='Open dialogs awaiting a player response, with their buttons/picker rows.')
def dialogs_list():
    return [dialog_brief(d) for d in active_dialogs().values()]


def _resolve_picks(dialog, picked):
    """Map agent picks (option ids, sim ids, sim names, row names) to the dialog's option ids."""
    rows = list(getattr(dialog, 'picker_rows', ()) or ())
    by_option = {getattr(r, 'option_id', None): r for r in rows}
    out, unknown = [], []
    for p in picked:
        match = None
        try:
            n = int(p)
            if n in by_option:
                match = n
            else:
                for r in rows:
                    if _row_sim_id(r) == n:
                        match = r.option_id
                        break
        except (TypeError, ValueError):
            text = str(p).strip().lower()
            for r in rows:
                names = []
                sid = _row_sim_id(r)
                if sid:
                    try:
                        names.append(L.sim_name(services.sim_info_manager().get(int(sid))).lower())
                    except Exception:
                        pass
                try:
                    tag = getattr(r, 'tag', None)
                    if tag is not None and not isinstance(tag, int):
                        names.append(L.tuning_name(tag).lower())
                except Exception:
                    pass
                if any(text == nm or nm.split(' ')[0] == text or text in nm for nm in names if nm):
                    match = r.option_id
                    break
        if match is None:
            unknown.append(p)
        elif match not in out:
            out.append(match)
    return out, unknown


@op('dialogs.respond', doc="Answer a dialog the way the player's UI does. response_id: a button id from "
                           "dialogs.list, or 'ok'/'cancel'/'close'. Pickers: picked=[option_id | sim_id | sim name] "
                           "selects rows and then confirms with OK (send response_id='cancel' with no picks to close). "
                           "text fills a text prompt before confirming.")
def dialogs_respond(dialog_id, response_id=None, picked=None, text=None):
    from ui.ui_dialog import ButtonType
    svc = services.ui_dialog_service()
    dialog = active_dialogs().get(int(dialog_id))
    if dialog is None:
        raise OpError('dialog %s is no longer open' % (dialog_id,), open=list(active_dialogs().keys()))
    mapping = {'ok': ButtonType.DIALOG_RESPONSE_OK, 'cancel': ButtonType.DIALOG_RESPONSE_CANCEL,
               'close': ButtonType.DIALOG_RESPONSE_CLOSED}
    out = {}
    if picked:
        options, unknown = _resolve_picks(dialog, picked)
        if unknown or not options:
            raise OpError('could not match picks %r to picker rows' % (unknown or picked,),
                          rows=dialog_brief(dialog).get('picker_rows'))
        # step 1 of the UI flow: ui.dialog.pick_result
        if not svc.dialog_pick_result(int(dialog_id), picked_results=options):
            raise OpError('the game rejected the picks %r' % (options,))
        out['picked_option_ids'] = options
        if response_id is None:
            response_id = 'ok'
    if text is not None and getattr(dialog, 'text_input_responses', None) is not None:
        names = list(getattr(dialog, 'text_inputs', None) or ())
        name = getattr(names[0], 'text_input_name', None) if names else None
        if name is None:
            try:
                name = next(iter(dialog.text_input_responses))
            except Exception:
                name = None
        if name is not None and not svc.dialog_text_input(int(dialog_id), name, str(text)):
            dialog.text_input_responses[name] = str(text)
        out['text'] = str(text)
    if response_id is None:
        response_id = 'ok'
    if isinstance(response_id, str) and response_id.lower() in mapping:
        rid = int(mapping[response_id.lower()])
    else:
        rid = int(response_id)
    # step 2 of the UI flow: ui.dialog.respond, which runs the result and closes the window on screen
    ok = svc.dialog_respond(int(dialog_id), rid, g.first_client())
    out.update({'ok': bool(ok), 'response_id': rid, 'still_open': int(dialog_id) in active_dialogs()})
    return out
