"""V0.9.42 ⬇ Export / ⬆ Import of your editing decisions (no photos, videos or voices).

One small JSON file with, for every project: each take's direction and the prompt H3 got, YOUR decision on it
(kept / not used / not decided), your note, what you asked to remember, your trims, which shots are left out and
the order, your people / places names, your settings, your films (Qwen's plan with your edits) and what the editor
learned from you. Only things YOU decided count as decisions; Qwen's checks are not carried over (they are Qwen's
opinion and can be run again).

Import only adds: a decision lands on a take only when that exact video (same sha256) is on this instance and the
take has no decision yet. Anything different is listed for you, never overwritten. Projects that are not on this
instance are kept aside and applied when you restore a backup of that project.
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path

SCHEMA = 'director-decisions-v0942'
MAX_BYTES = 50 * 1024 ** 2
PLAN_KEYS = ('mode', 'text', 'prompt', 'seed', 'frames', 'understood_as', 'assumptions', 'written_by', 'film', 'film_shot',
             'film_title', 'film_direction', 'retry_of', 'emphasis', 'enhanced', 'extend', 'settings_override', 'sampling',
             'coverage_role', 'storyboard', 'continued_from', 'frame_note', 'pose_note')
SLUG = re.compile(r'^[a-z0-9_]{1,40}$')
FILM_ID = re.compile(r'^[0-9a-f]{1,10}$')

class DecisionsError(ValueError): pass


def _undone(st):
    return {e['payload']['target'] for e in st['events'] if e['kind'] == 'UNDONE'}


def project_decisions(s):
    """Everything you decided in one project, as plain data."""
    st = s.state(); undone = _undone(st)
    names = {cid: c['name'] for cid, c in st['characters'].items()}
    places = {lid: l['name'] for lid, l in st['locations'].items()}
    trims = {}                                              # the trim that stands, with what the editor learned from it
    for e in st['events']:
        if e['kind'] == 'TRIM_SET' and e['seq'] not in undone:
            p = e['payload']; trims[(p['shot'], p['take'])] = {'in': p['in'], 'out': p['out'], 'learn': p.get('learn')}
    cuts = {}                                               # ✂ V0.9.68 split points and removed pieces that stand
    for e in st['events']:
        if e['kind'] == 'CUTS_SET' and e['seq'] not in undone:
            p = e['payload']; cuts[(p['shot'], p['take'])] = {'splits': list(p.get('splits') or []), 'removed': [list(r) for r in p.get('removed') or []]}
    shots = []
    for n in sorted(st['shots']):
        sh = st['shots'][n]
        if sh.get('source'): continue                       # your own original video: nothing was decided about it
        takes = {t['take']: t for t in sh['takes']}
        rows = []
        for p in sh['plans']:
            t = takes.get(p['take']) or {}
            dec = t.get('status') if t.get('status') in ('ACCEPTED', 'REJECTED') else None
            rows.append({'take': p['take'], **{k: p[k] for k in PLAN_KEYS if p.get(k) not in (None, '', [], {})},
                         'people': [names.get(c, c) for c in p.get('cast') or []], 'cast': list(p.get('cast') or []),
                         'place': places.get(p.get('location')) if p.get('location') else None,
                         'video': ({'file': t['file'], 'sha256': t['sha256']} if t.get('sha256') else None),
                         'decision': dec, 'note': t.get('note') if dec else None,
                         'remember': list(t.get('remember') or []),
                         'trim': trims.get((n, p['take'])), **({'cuts': cuts[(n, p['take'])]} if (n, p['take']) in cuts else {})})
        shots.append({'shot': n, 'left_out': n in st['excluded'], 'takes': rows})
    films = []
    d = s.root / 'films'
    if d.is_dir():
        for f in sorted(d.glob('*.json')):
            if f.name == 'last.json': continue
            try: films.append(json.loads(f.read_text()))
            except ValueError: pass
    last = s.last_film(); taste = None
    tf = s.root.parent / '_taste' / f'{s.slug}.json'
    if tf.is_file():
        try: taste = json.loads(tf.read_text())
        except ValueError: taste = None
    pr = st['project']
    return {'slug': s.root.name, 'ledger_slug': s.slug, 'title': pr['title'], 'tone': pr.get('tone'), 'settings': dict(st['settings']),
            'people': [{'id': cid, 'name': c['name'], 'pronoun': c.get('pronoun'), 'description': c.get('description'),
                        'voice': c.get('voice'), 'retired': bool(c.get('retired')), 'photos': len(c.get('refs') or [])}
                       for cid, c in st['characters'].items()],
            'places': [{'id': lid, 'name': l['name'], 'description': l.get('description'), 'retired': bool(l.get('retired'))}
                       for lid, l in st['locations'].items()],
            'order': st['order'], 'shots': shots, 'films': films, 'last_film': (last or {}).get('id'), 'taste': taste}


def export_all(studios, version=None):
    out = {'schema': SCHEMA, 'made': time.strftime('%Y-%m-%d %H:%M:%S'), 'director': version,
           'about': 'Your Director Studio editing decisions. No photos, videos or voices are in this file.', 'projects': []}
    for s in studios:
        try: out['projects'].append(project_decisions(s))
        except Exception as e: out.setdefault('could_not_export', []).append({'project': s.root.name, 'error': str(e)[:200]})
    return out


def counts(proj):
    rows = [r for sh in proj.get('shots', []) for r in sh.get('takes', [])]
    return {'takes': sum(1 for r in rows if r.get('video')), 'kept': sum(1 for r in rows if r.get('decision') == 'ACCEPTED'),
            'not_used': sum(1 for r in rows if r.get('decision') == 'REJECTED'), 'films': len(proj.get('films') or [])}


def autosave(s):
    """After each of your decisions: the project's decisions file next to the projects (survives ComfyUI restarts)."""
    d = s.root.parent / '_decisions'; d.mkdir(exist_ok=True)
    data = {'schema': SCHEMA, 'made': time.strftime('%Y-%m-%d %H:%M:%S'), 'projects': [project_decisions(s)]}
    n = s.root.name; tmp = d / f'{n}.json.partial'; tmp.write_text(json.dumps(data, ensure_ascii=False)); os.replace(tmp, d / f'{n}.json')


def read(raw):
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_BYTES: raise DecisionsError('DECISIONS_FILE_TOO_BIG')
        try: raw = raw.decode('utf-8')
        except UnicodeDecodeError: raise DecisionsError('NOT_A_DECISIONS_FILE')
    try: data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError: raise DecisionsError('NOT_A_DECISIONS_FILE: choose the .json that ⬇ Export decisions saved')
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('projects'), list):
        raise DecisionsError('NOT_A_DECISIONS_FILE: choose the .json that ⬇ Export decisions saved')
    for p in data['projects']:
        if not isinstance(p, dict) or not SLUG.match(str(p.get('slug') or '')) or not isinstance(p.get('shots', []), list):
            raise DecisionsError('DECISIONS_FILE_DAMAGED')
    return data


def _int(x):
    if isinstance(x, bool) or not isinstance(x, int): raise DecisionsError('DECISIONS_FILE_DAMAGED')
    return x


def merge(s, proj, *, apply=False, settings=False):
    """What importing `proj` into studio `s` does (apply=False: only says). Returns a report of plain sentences."""
    st = s.state(); undone = _undone(st)
    rep = {'project': s.root.name, 'title': st['project']['title'], 'added': [], 'same': 0, 'different': [], 'not_here': [], 'kept_yours': []}
    todo = []
    takes = {(n, t['take']): t for n, sh in st['shots'].items() for t in sh['takes']}
    clip_ev = {e['payload']['shot'] for e in st['events'] if e['kind'] in ('CLIP_EXCLUDED', 'CLIP_INCLUDED') and e['seq'] not in undone}
    for sh in proj.get('shots', []):
        n = _int(sh.get('shot'))
        for r in sh.get('takes', []):
            k = _int(r.get('take')); v = r.get('video') or {}
            dec = r.get('decision'); trim = r.get('trim'); rem = [str(x) for x in (r.get('remember') or []) if isinstance(x, str)]
            if dec not in (None, 'ACCEPTED', 'REJECTED'): raise DecisionsError('DECISIONS_FILE_DAMAGED')
            if not (dec or trim or rem or r.get('cuts')): continue          # nothing you decided about this take
            label = f'shot {n} take {k}'
            t = takes.get((n, k))
            if t is None or not v.get('sha256'):
                rep['not_here'].append(f"{label}: this take isn't on this instance (restore a backup that has it, then import again)"); continue
            if t.get('sha256') != v.get('sha256'):
                rep['different'].append(f'{label}: the video here is a different one, so its decision was not copied'); continue
            if dec:
                if t['status'] == dec: rep['same'] += 1
                elif t['status'] in ('ACCEPTED', 'REJECTED'):
                    rep['kept_yours'].append(f"{label}: here it is {'kept' if t['status'] == 'ACCEPTED' else 'not used'}, the file says "
                                             f"{'kept' if dec == 'ACCEPTED' else 'not used'}; this instance's decision stays")
                else:
                    note = (str(r.get('note') or '')[:300]) or None
                    todo.append(('TAKE_REVIEWED', {'shot': n, 'take': k, 'decision': dec, 'note': note, 'final_film_approval': False, 'imported': True}))
                    rep['added'].append(f"{label}: {'kept' if dec == 'ACCEPTED' else 'not used'}" + (f' ("{note}")' if note else ''))
            have = set(t.get('remember') or [])
            for x in rem:
                x = ' '.join(x.split())[:200]
                if x and x not in have:
                    import director_intent_v0739 as di
                    if di.FORBIDDEN_VISUAL_SIMPLE.search(x): continue
                    todo.append(('TAKE_REMEMBER', {'shot': n, 'take': k, 'text': x, 'imported': True})); have.add(x)
                    rep['added'].append(f'{label}: remember "{x}"')
            if isinstance(trim, dict):
                a, b = _int(trim.get('in')), _int(trim.get('out')); cur = st['trims'].get(f'{n}:{k}')
                frames = t.get('frames') or 10 ** 6
                if cur and (cur['in'], cur['out']) == (a, b): rep['same'] += 1
                elif cur: rep['kept_yours'].append(f'{label}: it is trimmed differently here; this instance\'s trim stays')
                elif 0 <= a < b <= frames and b - a >= 24:
                    learn = trim.get('learn') if isinstance(trim.get('learn'), dict) else None
                    todo.append(('TRIM_SET', {'shot': n, 'take': k, 'in': a, 'out': b, 'basis': 'HUMAN', 'learn': learn, 'imported': True}))
                    rep['added'].append(f'{label}: trim {a}–{b}')
            cuts = r.get('cuts')                                   # ✂ V0.9.68
            if isinstance(cuts, dict) and t:
                frames = t.get('frames') or 10 ** 6
                try:
                    sp = sorted({_int(x) for x in cuts.get('splits') or []}); rm = sorted({(_int(a), _int(b)) for a, b in cuts.get('removed') or []})
                except (TypeError, ValueError): sp = rm = None
                cur = st.get('cuts', {}).get(f'{n}:{k}')
                if sp is None or any(not 0 < x < frames for x in sp) or any(not 0 <= a < b <= frames for a, b in rm): pass
                elif cur and (cur['splits'], [tuple(x) for x in cur['removed']]) == (sp, rm): rep['same'] += 1
                elif cur: rep['kept_yours'].append(f'{label}: it is cut differently here; this instance\'s cuts stay')
                elif sp or rm:
                    todo.append(('CUTS_SET', {'shot': n, 'take': k, 'splits': sp, 'removed': [list(x) for x in rm], 'basis': 'HUMAN', 'imported': True}))
                    rep['added'].append(f'{label}: ✂ {len(rm)} piece(s) removed')
        if sh.get('left_out') and n in st['shots'] and n not in st['excluded'] and n not in clip_ev:
            todo.append(('CLIP_EXCLUDED', {'shot': n, 'imported': True})); rep['added'].append(f'shot {n}: left out of the movie')
    order = proj.get('order')
    if isinstance(order, list) and st['order'] is None and order and all(isinstance(x, int) and not isinstance(x, bool) for x in order) \
            and sorted(order) == sorted(st['shots']):
        todo.append(('ORDER_SET', {'order': order, 'imported': True})); rep['added'].append('the order of the shots')
    # films: any film this project doesn't have yet
    have_films = {f.stem for f in (s.root / 'films').glob('*.json')} if (s.root / 'films').is_dir() else set()
    new_films = []
    for f in proj.get('films') or []:
        fid = str((f or {}).get('id') or '')
        if not isinstance(f, dict) or not FILM_ID.match(fid) or not isinstance(f.get('shots'), list): continue
        if fid in have_films: rep['same'] += 1; continue
        new_films.append(f); rep['added'].append(f"film “{str(f.get('title') or fid)[:60]}” ({len(f['shots'])} shots)")
    # settings: only when you ask for them, and only the ones that differ
    diff = {}
    if isinstance(proj.get('settings'), dict):
        diff = {k: v for k, v in proj['settings'].items() if st['settings'].get(k) != v and k != 'loras'}
    rep['settings_differ'] = sorted(diff)
    if apply:
        for kind, payload in todo: s._append(kind, payload)
        if new_films:
            d = s.root / 'films'; d.mkdir(exist_ok=True)
            for f in new_films:
                text = json.dumps(f, indent=1, ensure_ascii=False); tmp = d / f"{f['id']}.json.partial"
                tmp.write_text(text); os.replace(tmp, d / f"{f['id']}.json")
            if not (d / 'last.json').is_file():
                f = next((x for x in new_films if x['id'] == proj.get('last_film')), new_films[-1])
                (d / 'last.json').write_text(json.dumps(f, indent=1, ensure_ascii=False))
        if settings and diff:
            done = []
            for k, v in diff.items():
                try: s.setting(**{k: v}); done.append(k)
                except Exception: rep.setdefault('settings_skipped', []).append(k)
            if done: rep['added'].append('settings: ' + ', '.join(sorted(done)))
        tf = s.root.parent / '_taste' / f'{s.slug}.json'
        if isinstance(proj.get('taste'), dict) and not tf.is_file():
            tf.parent.mkdir(exist_ok=True); tf.write_text(json.dumps(proj['taste'], indent=1))
        try: s._save_taste()                                           # what you kept is learned again
        except Exception: pass
        try: autosave(s)
        except Exception: pass
    rep['applied'] = bool(apply)
    return rep


def stash(projects_root, proj):
    """A project that is not on this instance: kept until you restore a backup of it."""
    d = Path(projects_root) / '_decisions_pending'; d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{proj['slug']}.json.partial"; tmp.write_text(json.dumps(proj, ensure_ascii=False)); os.replace(tmp, d / f"{proj['slug']}.json")


def pending(projects_root, folder, ledger_slug=None):
    """The decisions kept aside for this project: by its folder name, or by the name it was made with."""
    d = Path(projects_root) / '_decisions_pending'
    if not d.is_dir(): return None
    for f in [d / f'{folder}.json'] + sorted(d.glob('*.json')):
        if not f.is_file(): continue
        try: proj = json.loads(f.read_text())
        except ValueError: continue
        if f.stem == folder or (ledger_slug and proj.get('ledger_slug') == ledger_slug):
            return {**proj, '_pending_file': f.name}
    return None


def drop_pending(projects_root, proj):
    name = (proj or {}).get('_pending_file')
    if name and '/' not in name:
        f = Path(projects_root) / '_decisions_pending' / name
        if f.is_file(): f.unlink()


def sentence(rep):
    """One line for the page."""
    bits = [f"{len(rep['added'])} added"] + ([f"{rep['same']} already the same"] if rep['same'] else []) \
        + ([f"{len(rep['kept_yours'])} kept as they are here"] if rep['kept_yours'] else []) \
        + ([f"{len(rep['not_here'])} takes not on this instance"] if rep['not_here'] else []) \
        + ([f"{len(rep['different'])} different videos"] if rep['different'] else [])
    return f"{rep['title']}: " + ', '.join(bits)
