"""Sim listing, details and active-sim switching. Python 3.7."""
import services

from ts4_bridge.dispatch import op, OpError
from ts4_bridge.util import game as g
from ts4_bridge.util import lookup as L
from ts4_bridge.ops import state as S

SECTIONS = ('identity', 'motives', 'mood', 'buffs', 'skills', 'careers', 'traits', 'relationships',
            'aspiration', 'whims', 'queue', 'outfit', 'pregnancy')


@op('sims.list', doc="scope: 'household' (active household), 'lot' (instanced on this lot), "
                     "'selectable', or 'all' (every sim in the save; use limit/offset).")
def sims_list(scope='household', limit=100, offset=0, name_filter=None):
    g.require_zone()
    infos = []
    if scope == 'household':
        hh = services.active_household()
        infos = list(hh.sim_info_gen()) if hh else []
    elif scope == 'lot':
        infos = [s.sim_info for s in services.sim_info_manager().instanced_sims_gen()]
    elif scope == 'selectable':
        client = g.first_client()
        infos = list(client.selectable_sims) if client else []
    elif scope == 'all':
        infos = list(services.sim_info_manager().get_all())
    else:
        raise OpError("scope must be household|lot|selectable|all")
    if name_filter:
        nf = str(name_filter).lower()
        infos = [i for i in infos if nf in (i.full_name or '').lower()]
    total = len(infos)
    infos = infos[int(offset):int(offset) + int(limit)]
    return {'total': total, 'sims': [L.sim_brief(i) for i in infos]}


def _skills(info, limit=40):
    out = []
    try:
        skills = list(info.all_skills())
        for s in skills:
            try:
                st = s.stat_type
                d = {'name': L.tuning_name(st), 'id': L.guid(st), 'level': int(s.get_user_value()),
                     'value': round(float(s.get_value()), 1)}
                try:
                    d['max_level'] = int(st.max_level)
                except Exception:
                    pass
                try:
                    d['display_name'] = L.loc(st.stat_name)
                except Exception:
                    pass
                out.append(d)
            except Exception:
                continue
    except Exception as e:
        return {'_error': repr(e)}
    out.sort(key=lambda d: -d['level'])
    return out[:limit]


def _careers(info):
    out = []
    try:
        for uid, career in info.career_tracker.careers.items():
            d = {'uid': uid, 'name': L.tuning_name(type(career)), 'level': int(career.level),
                 'user_level': int(career.user_level)}
            try:
                lvl = career.current_level_tuning
                d['title'] = L.loc(lvl.title)
                d['pay_per_hour'] = int(career.get_hourly_pay())
            except Exception:
                pass
            try:
                d['track'] = L.tuning_name(career.current_track_tuning)
            except Exception:
                pass
            try:
                d['at_work'] = bool(career.currently_at_work)
                d['is_work_time'] = bool(career.is_work_time)
            except Exception:
                pass
            try:
                nxt = career.get_next_work_time()
                if nxt and nxt[0] is not None:
                    t = nxt[0]
                    d['next_work'] = 'Day %d %02d:%02d' % (int(t.day()), int(t.hour()), int(t.minute()))
            except Exception:
                pass
            out.append(d)
    except Exception as e:
        return {'_error': repr(e)}
    return out


def _traits(info):
    out = []
    try:
        for t in info.trait_tracker.equipped_traits:
            d = {'name': L.tuning_name(t), 'id': L.guid(t)}
            try:
                d['display_name'] = L.loc(t.display_name)
                d['type'] = getattr(getattr(t, 'trait_type', None), 'name', None)
            except Exception:
                pass
            out.append(d)
    except Exception as e:
        return {'_error': repr(e)}
    return out


def _relationships(info, limit=15):
    out = []
    try:
        tracker = info.relationship_tracker
        for rel in tracker:
            try:
                other_id = rel.get_other_sim_id(info.sim_id) if hasattr(rel, 'get_other_sim_id') else getattr(rel, 'target_sim_id', None)
                if other_id is None:
                    continue
                other = services.sim_info_manager().get(other_id)
                d = {'sim_id': other_id, 'name': other.full_name if other else None}
                try:
                    d['friendship'] = round(float(tracker.get_relationship_score(other_id)), 1)
                except Exception:
                    pass
                try:
                    d['depth'] = round(float(tracker.get_relationship_depth(other_id)), 1)
                except Exception:
                    pass
                try:
                    bits = tracker.get_all_bits(other_id)
                    d['bits'] = [L.tuning_name(b) for b in bits][:12]
                except Exception:
                    pass
                out.append(d)
            except Exception:
                continue
    except Exception as e:
        return {'_error': repr(e)}
    out.sort(key=lambda d: -abs(d.get('friendship', 0)))
    return out[:limit]


def _aspiration(info):
    out = {}
    try:
        tracker = info.aspiration_tracker
        asp = getattr(info.primary_aspiration, '__name__', None) if hasattr(info, 'primary_aspiration') else None
        out['primary'] = asp
        try:
            out['display_name'] = L.loc(info.primary_aspiration.display_text)
        except Exception:
            pass
        try:
            out['satisfaction_points'] = int(info.get_whim_bucks()) if hasattr(info, 'get_whim_bucks') else None
        except Exception:
            pass
    except Exception as e:
        out['_error'] = repr(e)
    return out


def _whims(info):
    out = []
    try:
        tracker = info.whim_tracker
        for w in tracker.get_active_whims() if hasattr(tracker, 'get_active_whims') else []:
            try:
                out.append({'name': L.tuning_name(w.whim if hasattr(w, 'whim') else type(w))})
            except Exception:
                continue
    except Exception:
        pass
    return out


def _outfit(info):
    try:
        cat, idx = info.get_current_outfit()
        return {'category': getattr(cat, 'name', str(cat)), 'index': int(idx)}
    except Exception:
        return None


def _pregnancy(info):
    try:
        if not info.is_pregnant:
            return {'pregnant': False}
        pt = info.pregnancy_tracker
        return {'pregnant': True, 'progress': round(float(pt.get_pregnancy_progress()), 2) if hasattr(pt, 'get_pregnancy_progress') else None}
    except Exception:
        return None


@op('sims.details', doc='Detailed state for one sim. sections: ' + ','.join(SECTIONS) + ' (default: all but relationships/skills trimmed).')
def sims_details(sim_id='active', sections=None):
    g.require_zone()
    info = L.sim_info(sim_id)
    want = set(sections) if sections else set(SECTIONS)
    out = {}
    if 'identity' in want:
        out['identity'] = L.sim_brief(info)
    if 'motives' in want:
        out['motives'] = S.motives(info)
    if 'mood' in want:
        out['mood'] = S.mood_brief(info)
    if 'buffs' in want:
        out['buffs'] = S.buffs_brief(info)
    if 'skills' in want:
        out['skills'] = _skills(info)
    if 'careers' in want:
        out['careers'] = _careers(info)
    if 'traits' in want:
        out['traits'] = _traits(info)
    if 'relationships' in want:
        out['relationships'] = _relationships(info)
    if 'aspiration' in want:
        out['aspiration'] = _aspiration(info)
    if 'whims' in want:
        out['whims'] = _whims(info)
    if 'queue' in want:
        sim = info.get_sim_instance()
        out['queue'] = S.queue_brief(sim) if sim is not None else None
    if 'outfit' in want:
        out['outfit'] = _outfit(info)
    if 'pregnancy' in want:
        out['pregnancy'] = _pregnancy(info)
    return out


@op('sims.set_active', doc='Make a selectable sim the active (controlled) sim.')
def sims_set_active(sim_id):
    g.require_zone()
    info = L.sim_info(sim_id)
    client = g.first_client()
    if client is None:
        raise OpError('no client')
    if not info.is_selectable:
        raise OpError('%s is not in the active household / selectable' % info.full_name)
    ok = client.set_active_sim_by_id(info.sim_id)
    return {'ok': bool(ok), 'active_sim_id': client.active_sim_info.sim_id if client.active_sim_info else None}
