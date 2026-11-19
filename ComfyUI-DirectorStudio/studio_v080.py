#!/usr/bin/env python3
"""Director Studio V0.8 — any scene, simple prompts, references from photos OR videos,
automatic intelligent editing of every take into a movie timeline.

    s = Studio.create('/workspace/DirectorStudio/my_film', 'My Film', comfy_root='/workspace/ComfyUI')
    maya = s.add_character('Maya', pronoun='she', descriptors=['the woman', 'presenter'])
    s.add_media(maya, '/path/maya_phone_video.mp4', attest_original=True)   # Director picks the stills
    s.add_location('Hallway', '/path/hallway.jpg', attest_original=True)
    plan = s.plan('The woman walks towards the camera')      # -> ComfyUI workflow + API prompt
    s.generate(plan['shot'], plan['take'])                   # optional: queue on ComfyUI
    s.sync()        # finds the finished take, measures it, auto-edits it into the movie
    s.render()      # frame-accurate A/V render of the movie

Clean-Chain: only files YOU attest as original are references; files carrying ComfyUI
generation metadata, files in ComfyUI/output, or files matching any take/render are
refused. Generated takes are only ever edited, never fed back to H3 as visuals.
"""

from __future__ import annotations
from studio_common_v094 import *
from studio_common_v094 import _SHA_CACHE, _json_ok
from studio_edit_v094 import *
from studio_edit_v094 import _norm, _flow_at, _frame_hist, _hist_dist, _faces_near
from studio_prompts_v094 import *
from studio_prompts_v094 import _nums
from studio_graph_v094 import *
import studio_positions_v0945 as pm      # 📍 V0.9.49 (also imported where it was used before)
import studio_frame_v0969 as fr          # 📐 V0.9.69 the frame H3 starts on, and where everyone is in it
# (split in V0.9.4: shared basics, editing, prompts and the H3 graph live in their own files; every name is still here)

TOO_LONG = ('DIRECTION_TOO_LONG: over 3500 characters is more than one shot can show. Split it into a few shots, or use '
            '"Plan as a scene" / "Rewrite as a scene" to spread it over 2-4 shots')

MAX_PROMPT = 6800     # V0.9.31 (was 6000): H3 takes 7000 characters; 200 stay free for continuity refreshed just before sending
DEFAULT_STEPS = 25    # V0.9.35 (template 20): more sampling steps help motion quality (Comfy H3 notes); Settings can change it

def _fresh_rewrite_module():
    """V0.9.27: the rewrite module that matches this Studio. After an update without a full ComfyUI restart, Python can
    still hold the OLD studio_rewrite in memory ("build_request() got an unexpected keyword argument 'motion'"): it is
    then loaded again from disk; if the file on disk is old too, the update is incomplete and that is said plainly."""
    import importlib, inspect, studio_rewrite_v0913 as rw
    if 'start_known' not in inspect.signature(rw.build_request).parameters:      # V0.9.37 (was 'motion')
        rw = importlib.reload(rw)
        if 'start_known' not in inspect.signature(rw.build_request).parameters:
            raise StudioError('UPDATE_INCOMPLETE: studio_rewrite_v0913.py is from an older version. Install the update again, then restart ComfyUI')
    return rw

def pieces_of(lo, hi, splits, removed):
    """✂ V0.9.68 the clip [lo, hi) cut at the split points: -> (kept runs [[a, b], ...] with neighbouring kept pieces joined,
    every piece [{'a', 'b', 'removed'}] for the editor). A piece is removed when one of the removed ranges covers it."""
    cuts = sorted({int(x) for x in splits or [] if lo < int(x) < hi})
    edges = [lo] + cuts + [hi]
    rm = [(int(a), int(b)) for a, b in removed or []]
    view = [{'a': a, 'b': b, 'removed': any(ra <= a and b <= rb for ra, rb in rm)} for a, b in zip(edges, edges[1:])]
    runs = []
    for pc in view:
        if pc['removed']: continue
        if runs and runs[-1][1] == pc['a']: runs[-1][1] = pc['b']
        else: runs.append([pc['a'], pc['b']])
    return runs, view


def expand_pieces(items, clips=None):
    """✂ V0.9.68 one entry per kept piece, in order. items: the clips themselves, or render segments aligned with clips.
    A J/L audio start stays on the first piece and an audio end on the last; inside a clip the sound cuts with the picture."""
    out = []
    for it, c in zip(items, clips or items):
        runs = c.get('pieces') or [[it['in'], it['out']]]
        for k, (a, b) in enumerate(runs):
            x = dict(it); x['in'], x['out'] = int(a), int(b)
            if k > 0: x.pop('a_in', None)
            if k < len(runs) - 1: x.pop('a_out', None)
            out.append(x)
    return out


class Studio:
    def __init__(self, root, comfy_root, *, input_dir=None, output_dir=None):
        self.root = Path(root); self.comfy = Path(comfy_root)
        self.input_dir = Path(input_dir) if input_dir else self.comfy / 'input'
        self.output_dir = Path(output_dir) if output_dir else self.comfy / 'output'
        self.db = self.root / 'studio.sqlite3'
        if not self.db.is_file(): raise StudioError('NOT_A_STUDIO_PROJECT')
        st = self.state()
        self.slug = st['project']['slug']
        self.input_sub = f"director_studio/{self.slug}"

    # -- ledger -----------------------------------------------------------------
    @classmethod
    def create(cls, root, title, *, comfy_root, fmt=None, auto_edit=True, tone=None, input_dir=None, output_dir=None, extra_settings=None):
        root = Path(root)
        if root.exists(): raise StudioError('PROJECT_EXISTS')
        root.mkdir(parents=True)
        for d in ('uploads', 'plans', 'analysis', 'renders'): (root / d).mkdir()
        with closing(sqlite3.connect(root / 'studio.sqlite3')) as c:
            c.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY, kind TEXT, payload TEXT, prev TEXT, hash TEXT)')
            c.execute('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)'); c.execute("INSERT INTO meta VALUES('schema',?)", (SCHEMA,))
            c.commit()
        s = object.__new__(cls); s.root = root; s.comfy = Path(comfy_root); s.db = root / 'studio.sqlite3'
        s._append('PROJECT_CREATED', {'title': title, 'slug': slug(title), 'format': fmt or FMT,
                  'settings': {**(extra_settings or {}), 'auto_edit': auto_edit, 'pacing': 'natural', 'video_refs': 'auto', 'voice_refs': False,
                               'clean_refs': 'auto', 'match_color': True, 'learn': True, 'loras': [], 'qwen': 'manual'},
                  'tone': tone or 'Naturalistic, cinematic, believable human performance',
                  'rules': {'generated_media_as_h3_visual_reference': False, 'automatic_final_approval': False}}, ZERO)
        return cls(root, comfy_root, input_dir=input_dir, output_dir=output_dir)

    def _conn(self):
        c = sqlite3.connect(self.db, timeout=20, isolation_level=None); c.row_factory = sqlite3.Row
        return c

    def events(self):
        with closing(self._conn()) as c:
            if c.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] != SCHEMA: raise StudioError('SCHEMA')
            rows = c.execute('SELECT * FROM events ORDER BY seq').fetchall()
        prev = ZERO; out = []
        for i, r in enumerate(rows, 1):
            p = json.loads(r['payload'])
            if r['seq'] != i or r['prev'] != prev or r['hash'] != ehash(i, r['kind'], p, prev) or canonical(p) != r['payload']:
                raise StudioError('LEDGER_INTEGRITY_FAILED')
            out.append({'seq': i, 'kind': r['kind'], 'payload': p, 'hash': r['hash']}); prev = r['hash']
        return out

    def _append(self, kind, payload, expect_tail=None):
        with closing(self._conn()) as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                last = c.execute('SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
                seq, prev = (last['seq'] + 1, last['hash']) if last else (1, ZERO)
                if expect_tail is not None and prev != expect_tail: raise StudioError('STATE_CHANGED_RETRY')
                c.execute('INSERT INTO events VALUES (?,?,?,?,?)', (seq, kind, canonical(payload), prev, ehash(seq, kind, payload, prev)))
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK'); raise
        return seq

    # -- state replay -------------------------------------------------------------
    def state(self):
        ev = self.events()
        undone = {e['payload']['target'] for e in ev if e['kind'] == 'UNDONE'}
        st = {'project': None, 'characters': {}, 'locations': {}, 'refs': {}, 'shots': {}, 'settings': {},
              'trims': {}, 'cuts': {}, 'excluded': set(), 'order': None, 'events': ev, 'tail': ev[-1]['hash'] if ev else ZERO,
              'undoable': [], 'generated_sha': set(), 'submissions': [], 'memory': {}, 'chain': None, 'motions': {}, 'action_photos': {}}
        for e in ev:
            k, p = e['kind'], e['payload']
            if e['seq'] in undone: continue
            if k == 'PROJECT_CREATED':
                st['project'] = p
                st['settings'] = {'clean_refs': 'auto', 'match_color': True, 'learn': True, 'video_refs': 'auto',
                                  'voice_refs': False, 'loras': [], 'qwen': 'manual', 'ref_numbering': True, **p['settings']}
                if isinstance(st['settings'].get('video_refs'), bool): st['settings']['video_refs'] = 'auto' if st['settings']['video_refs'] else 'off'
            elif k == 'CHARACTER_ADDED': st['characters'][p['id']] = {**p, 'refs': []}
            elif k == 'LOCATION_ADDED': st['locations'][p['id']] = {**p, 'refs': []}
            elif k == 'REF_ADDED':
                st['refs'][p['id']] = p
                owner = st['characters'].get(p['owner']) or st['locations'].get(p['owner'])
                if owner is not None: owner['refs'].append(p['id'])
            elif k == 'REF_REMOVED':
                if p['id'] in st['refs']: st['refs'][p['id']] = {**st['refs'][p['id']], 'removed': True}
                owner = st['characters'].get(p['owner']) or st['locations'].get(p['owner'])
                if owner is not None and p['id'] in owner['refs']: owner['refs'].remove(p['id'])
            elif k in ('CHARACTER_UPDATED', 'LOCATION_UPDATED'):
                box = st['characters'] if k == 'CHARACTER_UPDATED' else st['locations']
                if p['id'] in box: box[p['id']].update({x: v for x, v in p.items() if x != 'id'})
            elif k in ('CHARACTER_RETIRED', 'CHARACTER_RESTORED', 'LOCATION_RETIRED', 'LOCATION_RESTORED'):
                box = st['characters'] if k.startswith('CHARACTER') else st['locations']
                if p['id'] in box: box[p['id']]['retired'] = k.endswith('RETIRED')
            elif k == 'SOURCE_ADDED':            # your own original video in the movie (e.g. the start of an extension)
                st['shots'][p['shot']] = {'shot': p['shot'], 'source': True, 'plans': [{
                    'shot': p['shot'], 'take': 1, 'mode': 'source', 'text': p.get('text') or 'Your original video', 'spec': {},
                    'cast': p.get('cast', []), 'location': p.get('location'), 'refs': [], 'videos': [], 'loras': [], 'prefix': p['file'][:-4],
                    'workflow_file': None, 'prompt': None, 'seed': None, 'frames': p['frames'], 'seq': e['seq'],
                    'understood_as': 'your original video (its ending leads into the next shot)', 'assumptions': [], 'missing_refs_for': []}],
                    'takes': [{'shot': p['shot'], 'take': 1, 'file': p['file'], 'sha256': p['sha256'], 'frames': p['frames'], 'fps': 24,
                               'audio': p.get('audio'), 'status': 'ACCEPTED', 'note': 'your original video', 'analysis': None,
                               'source': True, 'source_sha256': p['source_sha256'], 'provenance': 'USER_ORIGINAL_SOURCE'}]}
            elif k == 'ACTION_PHOTO_ADDED': st['action_photos'][p['id']] = dict(p)   # V0.9.22: 📷 a photo of the action you want
            elif k == 'ACTION_PHOTO_REMOVED':
                if p['id'] in st['action_photos']: st['action_photos'][p['id']]['removed'] = True
            elif k == 'MOTION_ADDED': st['motions'][p['id']] = dict(p)          # V0.9.21: a movement video of yours (🎭)
            elif k == 'MOTION_REMOVED':
                if p['id'] in st['motions']: st['motions'][p['id']]['removed'] = True
            elif k == 'CHARACTER_MAIN_SOURCE':
                if p['id'] in st['characters']: st['characters'][p['id']]['main_source'] = p.get('source_sha256')
            elif k == 'SHOT_PLANNED':
                sh = st['shots'].setdefault(p['shot'], {'shot': p['shot'], 'plans': [], 'takes': []})
                sh['plans'].append({**p, 'seq': e['seq']})
            elif k == 'TAKE_CAPTURED':
                sh = st['shots'].get(p['shot'])
                if sh is None or not any(q['take'] == p['take'] for q in sh['plans']):
                    st.setdefault('orphans', []).append(p)      # its plan was undone: the take is kept on disk, not used
                else:
                    sh['takes'].append({**p, 'status': 'CAPTURED', 'note': None, 'analysis': None})
                st['generated_sha'].add(p['sha256'])
            elif k == 'TAKE_RECAPTURED':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take']:
                        t.update(file=p['file'], sha256=p['sha256'], frames=p.get('frames'), fps=p.get('fps'), audio=p.get('audio'),
                                 analysis=None, analysis_error=None)
                st['generated_sha'].add(p['sha256'])
            elif k == 'TAKE_ANALYZED':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take']: t['analysis'] = p['summary']; t['analysis_file'] = p['file']; t['analysis_error'] = None
            elif k == 'TAKE_ANALYSIS_FAILED':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take']: t['analysis_error'] = p['error']
            elif k == 'TAKE_FILE_RESTORED':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take']: t['analysis_error'] = None
            elif k == 'TAKE_CHECKED':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take'] and t.get('sha256') == p.get('sha256'): t['check'] = {'points': p['points'], 'model': p.get('model')}
            elif k == 'TAKE_REMEMBER':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take']: t.setdefault('remember', []).append(p['text'])
            elif k == 'TAKE_REVIEWED':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take']: t['status'] = p['decision']; t['note'] = p.get('note'); t['reviewed_seq'] = e['seq']
            elif k == 'TRIM_SET':
                st['trims'][f"{p['shot']}:{p['take']}"] = {'in': p['in'], 'out': p['out']}
                if p.get('learn'): st.setdefault('prefs', []).append(p['learn'])
            elif k == 'CUTS_SET':                           # ✂ V0.9.68 split points and the pieces you removed (the take never changes)
                st['cuts'][f"{p['shot']}:{p['take']}"] = {'splits': list(p.get('splits') or []), 'removed': [list(r) for r in p.get('removed') or []]}
            elif k == 'LESSONS_CLEARED': st['lessons_cleared_at'] = e['seq']
            elif k == 'MEMORY_SHOT': st.setdefault('memory', {})[p['shot']] = p
            elif k == 'CLIP_EXCLUDED': st['excluded'].add(p['shot'])
            elif k == 'CLIP_INCLUDED': st['excluded'].discard(p['shot'])
            elif k == 'ORDER_SET': st['order'] = p['order']
            elif k == 'SETTING_SET': st['settings'].update(p)
            elif k == 'RENDERED': st['generated_sha'].add(p['sha256'])
            elif k == 'PLAN_REFRESHED':                  # an unsent plan rewritten with what the Director learned since
                for q in st['shots'].get(p['shot'], {'plans': []})['plans']:
                    if q['take'] == p['take']: q.update({k2: p[k2] for k2 in ('prompt', 'continuity', 'workflow_sha256', 'continuity_from') if k2 in p})
            elif k in ('CHAIN_REQUESTED', 'CHAIN_FINISHED', 'CHAIN_STOPPED'):
                st['chain'] = dict(p, retries={}, log=[]) if k == 'CHAIN_REQUESTED' else None
            elif k == 'CHAIN_RETRY' and st.get('chain'):
                box = st['chain'].setdefault('check_retries', {}) if p.get('kind') == 'check' else st['chain']['retries']
                box[str(p['shot'])] = box.get(str(p['shot']), 0) + 1
                st['chain']['log'].append(p)
            elif k == 'CHAIN_REVIEW' and st.get('chain'): st['chain']['review'] = bool(p.get('review'))   # V0.9.38: changed while it runs
            elif k == 'CHAIN_NOTE' and st.get('chain'): st['chain']['log'].append(p)       # V0.9.36 🎬: no take made sense; the best stands in
            elif k == 'FILM_CONTINUITY':                       # V0.9.37 🔗 Qwen rewrote (or couldn't rewrite) a film shot from the one before
                st.setdefault('continuity_done', {})[f"{p['shot']}:{p['take']}"] = p
                if st.get('chain') and p.get('new_take'):
                    for x in st['chain']['shots']:
                        if x[0] == p['shot'] and x[1] == p['take']: x[1] = p['new_take']
                if st.get('chain'): st['chain']['log'].append({'shot': p['shot'], **({'take': p['new_take']} if p.get('new_take') else {}), 'why': p['why']})
            elif k == 'GENERATED_FILE': st['generated_sha'].add(p['sha256'])   # V0.9.60 made from a take (a cut frame, a redrawn still): never an original
            elif k in ('REDRAW_SENT', 'REDRAW_GOT', 'REDRAW_LOOKED', 'REDRAW_DONE'):   # 🧪 V0.9.60 the built frame redrawn in the poses of the cut
                st.setdefault('redraws', {}).setdefault(f"{p['shot']}:{p['take']}", {}).update(
                    {**p, 'state': {'REDRAW_SENT': 'sent', 'REDRAW_GOT': 'got', 'REDRAW_LOOKED': 'looked', 'REDRAW_DONE': 'done'}[k]})
                if k == 'REDRAW_DONE' and p.get('new_take'):
                    for q in (st['shots'].get(p['shot']) or {'plans': []})['plans']:
                        if q['take'] == p['new_take']: q['built_for'] = p['take']
                if k == 'REDRAW_DONE' and st.get('chain'):
                    if p.get('new_take'):
                        for x in st['chain']['shots']:
                            if x[0] == p['shot'] and x[1] == p['take']: x[1] = p['new_take']
                    st['chain']['log'].append({'shot': p['shot'], **({'take': p['new_take']} if p.get('new_take') else {}), 'why': p['why']})
            elif k in ('FRAME_OK', 'FRAME_NO'):                 # V0.9.61 🔗 you looked at a built opening frame: use it, or don't
                no = st.setdefault('frame_no', set())
                for q in (st['shots'].get(p['shot']) or {'plans': []})['plans']:
                    if q['take'] in (p.get('built') or [p['take']]): q['frame_ok'] = (k == 'FRAME_OK')
                for t_ in (p.get('built') or [p['take']]):
                    if k == 'FRAME_NO': no.add((p['shot'], t_))
                    else: no.discard((p['shot'], t_))
                if k == 'FRAME_NO' and st.get('chain'):
                    for x in st['chain']['shots']:
                        if x[0] == p['shot'] and x[1] in (p.get('built') or []): x[1] = p['back_to']
                    st['chain']['log'].append({'shot': p['shot'], 'why': f"🔗 you chose not to use the built opening frame: take {p['back_to']} goes as planned"})
            elif k == 'CARRY_FRAME':                            # V0.9.60 🔗 a carry-on shot opens on a frame built from where the shot before ends
                if p.get('new_take'):
                    for q in (st['shots'].get(p['shot']) or {'plans': []})['plans']:
                        if q['take'] == p['new_take']: q['built_for'] = p['take']          # V0.9.61 the take it was built for
                if st.get('chain') and p.get('new_take'):
                    for x in st['chain']['shots']:
                        if x[0] == p['shot'] and x[1] == p['take']: x[1] = p['new_take']
                if st.get('chain'): st['chain']['log'].append({'shot': p['shot'], **({'take': p['new_take']} if p.get('new_take') else {}), 'why': p['why']})
            elif k == 'CHAIN_SKIPPED' and st.get('chain'):
                st['chain'].setdefault('skipped', []).append(p['shot']); st['chain']['log'].append(p)
            elif k == 'GENERATION_SUBMITTED': st['submissions'].append(p)
            elif k == 'QWEN_JOB_QUEUED':
                for t in st['shots'].get(p['shot'], {'takes': []})['takes']:
                    if t['take'] == p['take']: t['qwen_job'] = p['job_id']; t['qwen_at'] = p.get('at')
            if k in UNDOABLE: st['undoable'].append(e)
        planned = {(n, q['take']) for n, sh in st['shots'].items() for q in sh['plans']}
        st['submissions'] = [x for x in st['submissions'] if (x.get('shot'), x.get('take')) in planned]   # a discarded plan isn't waited for
        return st

    # -- characters, locations, references ---------------------------------------
    def add_character(self, name, *, pronoun='they', descriptors=(), description='', voice=''):
        if pronoun in ('auto', '', None):                 # from the name and description: 'Old man' -> he
            pronoun = guess_pronoun(name, ' '.join(descriptors or []), description) or 'they'
        if pronoun not in ('she', 'he', 'they'): raise StudioError('PRONOUN_SHE_HE_THEY')
        if not (name or '').strip(): raise StudioError('NAME_REQUIRED')
        name = ' '.join(name.split())[:60]
        st = self.state()
        if not re.search(r'\w', name): raise StudioError('NAME_NEEDS_LETTERS_OR_NUMBERS')
        cid = next((c['id'] for c in st['characters'].values() if same_name(c['name'], name)), None) or 'char_' + slug(name)
        if cid in st['characters'] and not same_name(st['characters'][cid]['name'], name):   # different name, same id ("Ann-Marie" / "Ann Marie")
            base = cid; n = 2
            while cid in st['characters'] and not same_name(st['characters'][cid]['name'], name): cid = f'{base}_{n}'; n += 1
        if cid in st['characters']:
            old = st['characters'][cid]
            if old.get('retired'):                       # the same person you removed earlier: bring them back
                self._append('CHARACTER_RESTORED', {'id': cid})
                if description or descriptors or voice or pronoun != old.get('pronoun'):
                    self.update_character(cid, pronoun=pronoun, descriptors=list(descriptors) or None, description=description or None, voice=voice or None)
                return cid
            raise StudioError(f"PERSON_EXISTS: you already have someone called {old['name']} (names are matched ignoring case and symbols); edit them with ✎ or pick another name")
        self._append('CHARACTER_ADDED', {'id': cid, 'name': name.strip()[:60], 'pronoun': pronoun,
                     'descriptors': [d.strip().lower()[:40] for d in descriptors if d.strip()][:8],
                     'description': description.strip()[:300], 'voice': (voice or '').strip()[:160]})
        return cid

    def add_location(self, name, path=None, *, description='', attest_original=False):
        if not (name or '').strip(): raise StudioError('NAME_REQUIRED')
        name = ' '.join((name or '').split())[:60]
        st = self.state()
        if not re.search(r'\w', name): raise StudioError('NAME_NEEDS_LETTERS_OR_NUMBERS')
        lid = next((L['id'] for L in st['locations'].values() if same_name(L['name'], name)), None) or 'loc_' + slug(name)
        if lid in st['locations'] and not same_name(st['locations'][lid]['name'], name):
            base = lid; n = 2
            while lid in st['locations'] and not same_name(st['locations'][lid]['name'], name): lid = f'{base}_{n}'; n += 1
        if lid in st['locations']:
            if st['locations'][lid].get('retired'):
                self._append('LOCATION_RESTORED', {'id': lid})
                if path: self.add_media(lid, path, attest_original=attest_original)
                return lid
            raise StudioError(f"PLACE_EXISTS: you already have a place called {st['locations'][lid]['name']}; add more photos/videos to it instead")
        self._append('LOCATION_ADDED', {'id': lid, 'name': name.strip()[:60], 'description': description.strip()[:300]})
        if path: self.add_media(lid, path, attest_original=attest_original)
        return lid

    def update_character(self, cid, *, name=None, pronoun=None, descriptors=None, description=None, voice=None):
        """Change who a character is: name, she/he/they, nicknames, look. Their id (and history) stays."""
        st = self.state()
        if cid not in st['characters']: raise StudioError('UNKNOWN_CHARACTER')
        ch = {}
        if name is not None:
            if not name.strip(): raise StudioError('NAME_REQUIRED')
            if any(c['name'].lower() == name.strip().lower() and i != cid for i, c in st['characters'].items()): raise StudioError('NAME_IN_USE')
            ch['name'] = name.strip()[:60]
        if pronoun is not None:
            if pronoun not in ('she', 'he', 'they'): raise StudioError('PRONOUN_SHE_HE_THEY')
            ch['pronoun'] = pronoun
        if descriptors is not None: ch['descriptors'] = [d.strip().lower()[:40] for d in descriptors if d.strip()][:8]
        if description is not None: ch['description'] = description.strip()[:500]
        if voice is not None: ch['voice'] = voice.strip()[:160]
        if not ch: raise StudioError('NOTHING_TO_CHANGE')
        self._append('CHARACTER_UPDATED', {'id': cid, **ch})

    def add_voice(self, cid, path, *, attest_permission=False):
        """A voice for a person, from YOUR audio or video file (any common format). Used as H3's voice-timbre reference
        (<Audio N>) whenever this person speaks, in every shot, until you change or remove it. Only the sound of the
        voice is used, never the words in the clip. Refused: generated takes (file, pixels or sound), ComfyUI output."""
        import studio_voice_v093 as voice, studio_cleanchain_v081 as cc
        st = self.state()
        if cid not in st['characters']: raise StudioError('UNKNOWN_CHARACTER')
        if not attest_permission: raise StudioError('CONFIRM_THIS_IS_YOUR_VOICE_OR_YOU_HAVE_PERMISSION_TO_USE_IT')
        p = Path(path)
        if not p.is_file() or p.is_symlink(): raise StudioError('MEDIA_NOT_FOUND')
        if p.suffix.lower() not in voice.AUDIO_EXT: raise StudioError('UNSUPPORTED_AUDIO_FILE_TYPE')
        try:
            if p.resolve().is_relative_to(self.output_dir.resolve()): raise StudioError('REFUSED_FILE_IS_IN_COMFYUI_OUTPUT_GENERATED_MEDIA')
        except FileNotFoundError: pass
        h = media.sha256(p)
        if h in st['generated_sha']: raise StudioError('REFUSED_FILE_IS_A_GENERATED_TAKE_OR_RENDER')
        marks = generation_markers(p)
        if marks: raise StudioError('REFUSED_FILE_CARRIES_GENERATION_METADATA ' + ';'.join(marks))
        try: has_video = bool(media.probe(p).get('video'))
        except ValueError: has_video = False
        if has_video: self._clean_chain_pixels(p, h)                  # a video's pictures must not come from a take either
        takes = cc.generated_takes(self.output_dir, extra=sorted((self.root / 'renders').glob('*.mp4')))
        hit = voice.generated_audio_match(p, takes)
        if hit: raise StudioError(f"REFUSED_SOUND_COMES_FROM_A_GENERATED_TAKE: this sound matches {hit['take']} (from {hit['at']} s, similarity "
                                  f"{hit['score']}). Clean-Chain: a voice comes only from your own recordings.")
        up = self.root / 'uploads' / f'{h[:16]}{p.suffix.lower()[:6]}'
        if not up.exists(): shutil.copyfile(p, up)
        dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True)
        try: rep = voice.extract_voice(up, dest / f'voice_{cid}_{h[:8]}.wav')
        except voice.VoiceError as e: raise StudioError(str(e))
        f = dest / rep['file']
        rid = f'voice_{h[:10]}'
        while rid in st['refs']: rid += 'r'
        self._append('REF_ADDED', {'id': rid, 'owner': cid, 'kind': 'voice', 'alt': False, 'variant': 'raw', 'file': f.name,
                     'sha256': media.sha256(f), 'input_name': f'{self.input_sub}/{f.name}', 'source_sha256': h, 'source_kind': 'audio',
                     'seconds': rep['seconds'], 'speech_seconds': rep['speech_seconds'], 'snr_db': rep['snr_db'], 'start': rep['start'],
                     'provenance': 'USER_ATTESTED_OWN_VOICE_OR_PERMISSION'})
        self._append('CHARACTER_UPDATED', {'id': cid, 'voice_ref': rid})
        self.last_warnings = list(rep['warnings'])
        return rid

    def set_voice(self, cid, rid):
        """Choose which of a person's voices is used (None = no voice reference; H3 makes one up from the description)."""
        st = self.state()
        if cid not in st['characters']: raise StudioError('UNKNOWN_CHARACTER')
        if rid is not None and (rid not in st['refs'] or st['refs'][rid].get('owner') != cid or st['refs'][rid]['kind'] != 'voice' or st['refs'][rid].get('removed')):
            raise StudioError('NOT_ONE_OF_THEIR_VOICES')
        self._append('CHARACTER_UPDATED', {'id': cid, 'voice_ref': rid})

    def _color_matched(self, st, refs, bg_from=None):
        """Copies of the chosen stills moved gently toward ONE anchor's light (the place plate, else the background person's
        own photo, else the first reference), so H3 isn't pulled between mixed phone cameras and lights. Stills from the
        same original as the anchor are left alone. Matched copies are new references derived from your originals."""
        R = st['refs']; still = lambda r: R[r]['kind'] in ('face', 'upper', 'full', 'location')
        anchor = next((r for r in refs if R[r]['kind'] == 'location'), None) or \
                 next((r for r in refs if R[r]['owner'] == bg_from and R[r]['kind'] in ('full', 'upper') and R[r].get('variant', 'raw') == 'raw'), None) or \
                 next((r for r in refs if still(r)), None)
        if anchor is None: return refs, 0
        dest = self.input_dir / self.input_sub; out = []; n = 0
        for r in refs:
            if r == anchor or not still(r) or R[r].get('source_sha256') == R[anchor].get('source_sha256'): out.append(r); continue
            mid = f"{r}_cm{R[anchor]['sha256'][:6]}"
            if mid not in R:
                src = self.input_dir / R[r]['input_name']; fn = Path(R[r]['input_name']).stem + f"_cm{R[anchor]['sha256'][:6]}.png"
                try: shift = media.match_still(src, self.input_dir / R[anchor]['input_name'], dest / fn)
                except Exception: out.append(r); continue
                self._append('REF_ADDED', {**{k: v for k, v in R[r].items() if k not in ('id', 'removed')}, 'id': mid, 'file': fn,
                             'sha256': media.sha256(dest / fn), 'input_name': f'{self.input_sub}/{fn}', 'matched_from': r, 'matched_to': anchor,
                             'shift': shift, 'provenance': (R[r].get('provenance') or 'USER_ATTESTED_ORIGINAL') + '_COLOUR_MATCHED'})
                st = self.state(); R = st['refs']
            out.append(mid); n += 1
        return out, n

    def choose_voices(self, st, cast, spec):
        """Voice references for the people who SPEAK in this shot (in the order they speak), at most 3."""
        if not st['settings'].get('voice_audio', True) or st['settings'].get('speech') == 'silent': return []
        speakers = []
        for l in (spec.get('dialogue') or [])[-3:]:
            sp = l.get('speaker') if l.get('speaker') in cast else (cast[0] if len(cast) == 1 else None)
            if sp and sp not in speakers: speakers.append(sp)
        out = []
        for c in speakers:
            r = (st['characters'].get(c) or {}).get('voice_ref')
            if r and r in st['refs'] and not st['refs'][r].get('removed') and st['refs'][r]['kind'] == 'voice': out.append(r)
        return out[:3]

    def update_location(self, lid, *, name=None, description=None):
        st = self.state()
        if lid not in st['locations']: raise StudioError('UNKNOWN_LOCATION')
        ch = {}
        if name is not None and name.strip(): ch['name'] = name.strip()[:60]
        if description is not None: ch['description'] = description.strip()[:300]
        if not ch: raise StudioError('NOTHING_TO_CHANGE')
        self._append('LOCATION_UPDATED', {'id': lid, **ch})

    def remove_ref(self, rid):
        """Stop using one reference (it stays in the history of shots that used it). Undo brings it back."""
        st = self.state(); r = st['refs'].get(rid)
        if not r or r.get('removed'): raise StudioError('UNKNOWN_REFERENCE')
        self._append('REF_REMOVED', {'id': rid, 'owner': r['owner'], 'reason': 'removed by you'})

    def retire(self, owner, on=True):
        """Take a character or place out of the project (or bring it back). Earlier shots keep it."""
        st = self.state()
        if owner in st['characters']: kind = 'CHARACTER'
        elif owner in st['locations']: kind = 'LOCATION'
        else: raise StudioError('UNKNOWN_OWNER')
        self._append(f"{kind}_{'RETIRED' if on else 'RESTORED'}", {'id': owner})

    # -- captions / on-screen text in uploads ---------------------------------------------
    def _text_notes(self, rep, place=False):
        if not rep.get('checked'):
            return [f"On-screen text was not checked ({rep['error']})."] if rep.get('error') else []
        out = []
        if rep.get('overlay_boxes'):
            out.append(f"Removed captions / on-screen text from {rep['frames_with_overlay_text']} frame(s) before making the references.")
        if rep.get('text_on_person_kept') and not place:
            out.append('Text on the person (e.g. a shirt print) was kept: it is part of how they look.')
        return out

    def _clean_clip(self, src, dest, rep, notes, start=None, auto=True):
        """73-frame (about 3 s) reference clip with burned-in captions filled in; skipped if a caption covers the face.
        V0.9.27: from the best 3 s of the video (face visible and moving), not the middle; `start` = your own pick."""
        chosen, info = ('you', {'why': 'picked by you'}) if start is not None else ('auto', {}) if auto else ('middle', {'why': 'the middle'})
        if start is None and auto:
            try: start, info = media.best_clip_start(src)
            except Exception as e: start, info = None, {'why': f'the middle (auto-pick failed: {type(e).__name__})'}
        import studio_text_v090 as tx
        cleanup = None
        if rep.get('checked') and rep.get('union') and rep.get('size'):
            H, W = rep['size']
            faces_b = [f['box'] if f else None for f in rep.get('faces', [])]
            flt, covers = tx.clip_filter(rep['union'], faces_b, W, H)
            if covers:
                notes.append('No reference clip was kept: captions/text cover the face in this video (the stills were cleaned).'); return None
            if flt: cleanup = {'w': W, 'h': H, 'filter': flt}
        clip = media.make_ref_clip(src, dest, cleanup=cleanup, start=start)
        try: dur = float(media.probe(src).get('duration') or 0)
        except Exception: dur = 0.0
        if start is None: start = max(0.0, (dur - media.REF_CLIP_SECONDS) / 2)
        return {**clip, 'start': round(float(start), 2), 'source_seconds': round(dur, 2), 'chosen': chosen, 'why': info.get('why')}

    # -- identity (face recognition, measurement only) --------------------------------
    def _refinfo(self):
        """⭐ V0.9.65 per-reference identity / angle / clothes measurements (cached by hash in analysis/)."""
        import studio_bestref_v0965 as br
        ri = getattr(self, '_refinfo_cache', None)
        if ri is None or ri.input_dir != Path(self.input_dir): ri = self._refinfo_cache = br.RefInfo(self.root / 'analysis', self.input_dir, media.load_image)
        return ri

    def _support_crops(self, st, refs, support):
        """⭐ V0.9.65 a supporting face (from an upload that is not the person's main reference) goes to H3 as a close crop of
        the face only, so its hair, makeup at the edges and clothes don't compete with the main reference. A crop of YOUR
        original (made once, kept next to it); nothing is generated. A face that can't be cropped is left out."""
        import studio_bestref_v0965 as br
        R = st['refs']; out = []; dropped = 0
        for r in refs:
            if r not in support: out.append(r); continue
            have = next((k for k, x in R.items() if x.get('support_of') == r and not x.get('removed')), None)
            if have and (self.input_dir / R[have]['input_name']).is_file(): out.append(have); continue
            crop = None
            try:
                px = self._refinfo().get(R[r]).get('px')
                if px: crop = br.identity_crop(media.load_image(self.input_dir / R[r]['input_name']), px)
            except Exception: crop = None
            if crop is None: dropped += 1; continue
            src = self.input_dir / R[r]['input_name']; dest = src.with_name(src.stem + '_features.png')
            if not __import__('cv2').imwrite(str(dest), crop): dropped += 1; continue
            nid = r + '_sup'
            while nid in R: nid += 's'
            self._append('REF_ADDED', {**{k: v for k, v in R[r].items() if k not in ('id', 'removed', 'seq')}, 'id': nid, 'file': dest.name,
                         'input_name': str(Path(R[r]['input_name']).with_name(dest.name)), 'sha256': media.sha256(dest),
                         'variant': 'support', 'alt': True, 'support_of': r,
                         'provenance': R[r].get('provenance', 'USER_ATTESTED_ORIGINAL') + '_FACE_CROP'})
            out.append(nid)
        return out, dropped

    def person_embedding(self, st, cid):
        """Identity of a character from their current reference stills (cached by the refs' hashes)."""
        import studio_identity_v090 as idn
        if not idn.available() or cid not in st['characters']: return None
        rs = [st['refs'][r] for r in st['characters'][cid]['refs'] if st['refs'][r]['kind'] in ('face', 'upper', 'full')
              and st['refs'][r].get('variant', 'raw') == 'raw']
        if not rs: return None
        key = hashlib.sha256(('robust1' + ''.join(sorted(r['sha256'] for r in rs))).encode()).hexdigest()[:16]   # ⭐ V0.9.65
        f = self.root / 'analysis' / f'identity_{cid}_{key}.npy'
        if f.is_file():
            try: return np.load(f)
            except Exception: pass
        ims = []
        for r in rs:
            try: ims.append(media.load_image(self.input_dir / r['input_name']))
            except Exception: pass
        e = None
        try:                                   # ⭐ V0.9.65 the ROBUST identity: stills that disagree with the rest are left out
            import studio_bestref_v0965 as br
            es = []
            for im in ims:
                fs = idn.embed(im)
                if len(fs) == 1: es.append(fs[0]['emb'])
            e, _ = br.robust_centroid(es)
        except Exception: e = None
        if e is None: e = idn.person_embedding(ims)
        if e is not None:
            tmp = f.with_name(f.stem + '.partial.npy'); np.save(tmp, e); os.replace(tmp, f)
        return e

    def _sides_at_cut(self, st, t, cast_ids, out):
        """V0.9.37 📍 who is left to right WHERE THE MOVIE CUTS this take: faces recognised (against their ORIGINAL photos) in
        5 frames over the last ~0.7 s before the cut; only a clear answer counts (at least 2 frames, 3 in 4 agreeing),
        else [] (not sure: no sides claimed). Measurement only; kept in analysis/ so it is read once per take and cut point."""
        return self._sides_in(st, self.take_path(t), t.get('sha256'), cast_ids, tuple(int(out) - 1 - 4 * k for k in range(5)))   # the last ~0.7 s

    def join_report(self, shots=None):
        """V0.9.37 📍 every join in the movie as it is cut now: who is left/right where shot A ends and where shot B starts
        (faces recognised in those frames), and what 🔍 said about B. -> [{'from', 'to', 'end', 'start', 'state', 'note'}],
        state 'ok' | 'swapped' | 'unknown' (couldn't be told) | 'one_person' (sides don't apply). Measurement only.
        shots: only joins INTO these shot numbers (e.g. one film)."""
        import studio_check_v0915 as ck
        st = self.state(); tl = self.timeline(st); out = []
        want = set(int(x) for x in shots) if shots else None
        tk = lambda c: next((x for x in st['shots'][c['shot']]['takes'] if x['take'] == c['take']), None)
        for a, b in zip(tl, tl[1:]):
            if want is not None and b['shot'] not in want: continue
            ta, tb = tk(a), tk(b)
            if not ta or not tb: continue
            pa = next((q for q in st['shots'][a['shot']]['plans'] if q['take'] == a['take']), {})
            pb = next((q for q in st['shots'][b['shot']]['plans'] if q['take'] == b['take']), {})
            ca = [c for c in pa.get('cast', []) if c in st['characters']]; cb = [c for c in pb.get('cast', []) if c in st['characters']]
            row = {'from': [a['shot'], a['take']], 'to': [b['shot'], b['take']], 'end': [], 'start': [], 'state': 'one_person', 'note': ''}
            common = [c for c in ca if c in cb]
            if pb.get('start_frame') and st['refs'].get(pb['start_frame']) and not pb.get('carry_frame'):   # V0.9.38 🎞: a new start on your picture, not a carry-over
                row['state'] = 'opens_on'; row['picture'] = st['refs'][pb['start_frame']]['file']
            elif pb.get('film_join') == 'skip':                                     # V0.9.44 ⏩ time has passed: sides start fresh
                row['state'] = 'time_skip'; row['later'] = pb.get('film_later') or ''
            elif len(common) >= 2:
                try:
                    row['end'] = self._sides_at_cut(st, ta, ca, int(a['out']))
                    row['start'] = self._sides_in(st, self.take_path(tb), tb.get('sha256'), cb, tuple(int(b['in']) + 4 * k for k in range(6)))
                except Exception: pass
                both = [n for n in row['end'] if n in row['start']]
                if len(both) < 2: row['state'] = 'unknown'
                else: row['state'] = 'ok' if [n for n in row['end'] if n in both] == [n for n in row['start'] if n in both] else 'swapped'
            chk = tb.get('check')
            if chk:
                sc = ck.score(chk); sense = ck.makes_sense(chk)
                miss = [x['point'] for x in chk.get('points', []) if x.get('seen') == 'no']
                row['note'] = (f'🔍 {round(100 * sc)}% of the actions seen' if sc is not None else '🔍 checked') + \
                              ('' if sense is not False else ' · does not make sense here') + (': not seen: ' + '; '.join(m[:60] for m in miss[:2]) if miss else '')
            out.append(row)
        return out

    def _sides_in(self, st, path, sha, cast_ids, frames):
        """Who is left to right in these frames of a video (recognised; [] when the frames disagree or can't tell)."""
        key = hashlib.sha256(f"{sha}|{','.join(map(str, frames))}|{'-'.join(sorted(cast_ids))}".encode()).hexdigest()[:20]
        f = self.root / 'analysis' / f'sides_{key}.json'
        if f.is_file():
            try: return json.loads(f.read_text())['left_to_right']
            except (ValueError, KeyError): pass
        import cv2
        votes = []; read = 0
        cap = cv2.VideoCapture(str(path))
        try:
            for i in frames:
                if i < 0: continue
                cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, im = cap.read()
                if not ok: continue
                read += 1
                r = self.people_in_picture(st, im, cast_ids) or {}
                if r.get('left_to_right'): votes.append(tuple(r['left_to_right']))
        finally: cap.release()
        best = max(set(votes), key=votes.count) if votes else None
        order = list(best) if best and votes.count(best) >= min(2, read) and votes.count(best) >= 0.75 * len(votes) else []
        try:
            f.parent.mkdir(parents=True, exist_ok=True); tmp = f.with_suffix('.partial')
            tmp.write_text(json.dumps({'left_to_right': order, 'frames_read': read, 'votes': [list(v) for v in votes]})); os.replace(tmp, f)
        except OSError: pass
        return order

    def _recognise_side(self, st, owner, path, is_video):
        """Multi-person upload of someone we already know: which side are they on? (pos, score) or None."""
        try:
            import studio_identity_v090 as idn
            if not idn.available(): return None
            e = self.person_embedding(st, owner)
            if e is None: return None
            pics = [im for _, im in media.sample_frames(path, n=8)] if is_video else [media.load_image(path)]
            votes = {}
            for im in pics:
                fs = idn.embed(im)
                if len(fs) < 2 or len(media.people_faces(im)) < 2: continue     # V0.9.26: one real person (+ a print / hand)
                pos, sc = idn.side_of(fs, e)
                if pos: votes.setdefault(pos, []).append(sc)
            if not votes: return None
            pos = max(votes, key=lambda k: len(votes[k]))
            if len(votes[pos]) < max(1, sum(len(v) for v in votes.values()) * 0.7): return None   # inconsistent: ask instead
            return pos, float(np.median(votes[pos]))
        except Exception:
            return None

    def _people_at(self, st, path, cast, frames):
        """📐 V0.9.69 where each person is in these frames of a video: {name: {'x', 'y'}} (the 📍 dots' terms), only for people
        recognised (their ORIGINAL photos) in at least 2 of the frames. Measurement only."""
        import cv2, studio_identity_v090 as idn
        if not idn.available(): return {}
        embs = {st['characters'][c]['name']: self.person_embedding(st, c) for c in cast if c in st['characters']}
        embs = {n: e for n, e in embs.items() if e is not None}
        if not embs: return {}
        seen = {n: [] for n in embs}; cap = cv2.VideoCapture(str(path))
        try:
            for i in frames:
                if i < 0: continue
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(i)); ok, im = cap.read()
                if not ok: continue
                for n, (sc, f, margin) in idn.assign(idn.embed(im), embs).items():
                    if sc >= idn.MATCH and margin >= idn.MARGIN: seen[n].append((f['cx'], f['cy'], f['h']))
        finally: cap.release()
        out = {}
        for n, v in seen.items():
            if len(v) < 2: continue
            x, y, h = (float(np.median([a[k] for a in v])) for k in range(3))
            out[n] = {'x': round(min(1.0, max(0.0, x)), 3), 'y': round(min(0.95, max(0.15, y + 6.5 * h)), 3)}
        return out

    def take_positions(self, st, p, f, a):
        """📐 V0.9.69 a take against the positions asked for the shot: where everyone really is right after the lead-in (or
        at the start) and at the end, against your 📍 dots. -> (problems in plain words, what was measured). A person who
        can't be recognised there (turned away, out of frame) is not judged."""
        spec = p.get('spec') or {}; pos = spec.get('positions') or {}
        if not pos and spec.get('start_sides') and st['settings'].get('position_check', True) and p.get('mode') != 'source':
            l, r = spec['start_sides']; lo = int(clean_range(a)[0])               # the sides the Director fixed: only a swap counts
            seen = self._people_at(st, f, [c for c in p.get('cast', []) if c in st['characters']], [lo + 4 * k for k in range(5)])
            rows = [{'who': l, 'x': 0.3, 'y': 0.7, 'posture': 'sitting'}, {'who': r, 'x': 0.7, 'y': 0.7, 'posture': 'sitting'}]
            return [x for x in fr.judge(rows, seen, 'start') if 'swapped' in x], {'start': seen}
        if not pos or not st['settings'].get('position_check', True) or p.get('mode') == 'source': return [], None
        lo, hi = (int(v) for v in clean_range(a)); lead = float(spec.get('lead_in') or 0)
        cast = [c for c in p.get('cast', []) if c in st['characters']]
        problems, got = [], {}
        if pos.get('start') and not p.get('extend') and (lead or not p.get('start_frame')):      # pinned with no lead-in: frame 0 is the picture
            t0 = max(lo, int(round(lead * 24)) + 6) if lead else lo
            got['start'] = self._people_at(st, f, cast, [t0 + 4 * k for k in range(5) if t0 + 4 * k < hi])
            problems += fr.judge(pos['start'], got['start'], 'start')
        if pos.get('end'):
            got['end'] = self._people_at(st, f, cast, [hi - 1 - 4 * k for k in range(5)])
            problems += fr.judge(pos['end'], got['end'], 'end')
        return problems, got

    def take_identity(self, st, plan, f, a):
        import studio_identity_v090 as idn
        if not idn.available() or not st['settings'].get('identity_check', True) or not plan.get('cast'): return None
        people = {}
        for c in plan['cast']:
            e = self.person_embedding(st, c)
            if e is not None: people[c] = e
        if not people: return None
        lo, hi = clean_range(a)
        ident = idn.take_identity(f, people, frames=(lo, hi))
        if len(people) >= 2 and (plan.get('spec') or {}).get('dialogue') and st['settings'].get('talking_check', True):
            import studio_talking_v0921 as tk                 # V0.9.21: who's talking (mouth movement against the sound)
            try: ident['talking'] = tk.measure(f, people, frames=(lo, hi))
            except Exception as e: ident['talking'] = {'error': type(e).__name__}
        return ident

    def _gate(self, path, attest):
        p = Path(path)
        if not attest: raise StudioError('CONFIRM_THIS_IS_YOUR_ORIGINAL_MEDIA')
        if not p.is_file() or p.is_symlink(): raise StudioError('MEDIA_NOT_FOUND')
        try:
            if p.resolve().is_relative_to(self.output_dir.resolve()):
                raise StudioError('REFUSED_FILE_IS_IN_COMFYUI_OUTPUT_GENERATED_MEDIA')
        except FileNotFoundError: pass
        h = media.sha256(p)
        if h in self.state()['generated_sha']: raise StudioError('REFUSED_FILE_IS_A_GENERATED_TAKE_OR_RENDER')
        marks = generation_markers(p)
        if marks: raise StudioError('REFUSED_FILE_CARRIES_GENERATION_METADATA ' + ';'.join(marks))
        self._clean_chain_pixels(p, h)
        return h

    def known_originals(self):
        """sha256 of every file that entered ANY project here as your attested original, and of every reference still or
        clip the Director cut from one. H3 copies its reference into the first frames of a take, so such a file looks
        exactly like the start of a take made from it; it is still yours, not generated. Generated files never get in."""
        good, bad = set(), set()
        for db in sorted(self.root.parent.glob('*/studio.sqlite3')):
            try:
                with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)) as c:
                    for kind, payload in c.execute("SELECT kind, payload FROM events WHERE kind IN ('REF_ADDED','SOURCE_ADDED','MOTION_ADDED','ACTION_PHOTO_ADDED','TAKE_CAPTURED','TAKE_RECAPTURED','RENDERED')"):
                        p = json.loads(payload)
                        if kind in ('REF_ADDED', 'SOURCE_ADDED', 'MOTION_ADDED', 'ACTION_PHOTO_ADDED'):
                            good.update(x for x in (p.get('sha256'), p.get('source_sha256')) if x)
                        else: bad.add(p.get('sha256'))
            except Exception: continue
        return good - bad

    def _clean_chain_pixels(self, p, h):
        """Refuse a picture/clip whose PIXELS come from a generated take (last frame, any frame, crop, re-encode)."""
        import studio_cleanchain_v081 as cc
        st = self.state()
        if h in {r.get('source_sha256') for r in st['refs'].values()}: return
        if h in self.known_originals(): return      # exactly a file you uploaded before, or a reference the Director made from it
        try:
            meta = media.probe(p)
        except Exception: meta = {}
        ext = p.suffix.lower()
        is_video = meta.get('video') and (meta.get('frames', 0) > 1 or meta.get('duration', 0) > 0.5) and ext not in ('.png', '.jpg', '.jpeg', '.webp', '.heic', '.heif')
        try:
            pics = [f for _, f in media.sample_frames(p, n=5)] if is_video else [media.load_image(p)]
        except Exception: return                                        # unreadable media is refused later by intake
        takes = cc.generated_takes(self.output_dir, extra=sorted((self.root / 'renders').glob('*.mp4')))
        try: hit, note = cc.check_pictures_isolated(pics, takes, self.root.parent / '_fingerprints')
        except RuntimeError:
            raise StudioError('CLEAN_CHAIN_CHECK_COULD_NOT_RUN: OpenCV crashed while checking this file against your generated takes, '
                              'so it was not added (an unchecked file is never used as a reference). Please send this message to Claude.')
        if note: self._cc_note = note
        if hit:
            early = hit['frame'] <= 3
            if early:                                  # is this file literally one of the references that take was made FROM?
                try:
                    tk = next((t for t in takes if Path(t).name == hit['take']), None)
                    prov = cg.embedded_provenance(tk) if tk else {}
                    names = [x for x in (prov.get('images') or []) + (prov.get('videos') or []) + (prov.get('anchors') or []) if isinstance(x, str) and '..' not in x]
                    if any((self.input_dir / x).is_file() and media.sha256(self.input_dir / x) == h for x in names): return
                except Exception: pass
            raise StudioError(f"REFUSED_PIXELS_COME_FROM_A_GENERATED_TAKE: this matches frame {hit['frame'] + 1} of {hit['frames']} in "
                              f"{hit['take']} (similarity {hit['score']}, {hit['method'].replace('_', ' ')}). Clean-Chain: generated frames "
                              'are never used as references. ' + (
                              'H3 copies its reference into the first frames of a take, so an original that take was made from looks the same: '
                              'upload the ORIGINAL file you first gave the Director (it is recognised exactly), not a copy or a frame saved from the take.'
                              if early else 'Use an original photo or video instead.'))

    def add_media(self, owner, path, *, attest_original=False, kind=None, who=None, replace=False):
        """Photo or video of a person or place (any phone format). People: the Director picks the best
        face / upper-body / full-body stills, makes CLEAN (isolated) versions, and keeps a 73-frame (about 3 s) clip.
        Places: clean plates (no people) from up to 3 different angles."""
        st = self.state()
        if owner not in st['characters'] and owner not in st['locations']: raise StudioError('UNKNOWN_OWNER')
        if kind and kind not in ('face', 'upper', 'full', 'lying', 'turn', 'sitting'): raise StudioError('KIND_FACE_UPPER_FULL')
        orient = kind if kind in ('lying', 'turn', 'sitting') else None         # V0.9.49 your answer to "lying down or sideways?"
        if orient: kind = 'full' if orient in ('lying', 'sitting') else None     # 🛏 / 🪑 you said lying down / sitting: a full-body photo in that pose
        src_sha = self._gate(path, attest_original)
        p = Path(path); ext = p.suffix.lower()[:6] or '.bin'
        up = self.root / 'uploads' / f'{src_sha[:16]}{ext}'
        if not up.exists(): shutil.copyfile(p, up)
        dest = self.input_dir / self.input_sub
        dest.mkdir(parents=True, exist_ok=True)
        try: meta = media.probe(up)
        except ValueError: meta = {}
        is_video = meta.get('video') and (meta.get('frames', 0) > 1 or meta.get('duration', 0) > 0.5) and ext not in ('.png', '.jpg', '.jpeg', '.webp', '.heic', '.heif')
        base = f"{owner}_{src_sha[:8]}"
        skip = getattr(self, 'skip_steps', set())      # steps the upload helper had to skip because they crashed here
        iso = st['settings'].get('clean_refs', 'auto') != 'never' and 'cutout' not in skip
        notext = st['settings'].get('ignore_text', True) and 'text' not in skip
        text_notes = []
        auto_who = None
        if owner in st['characters'] and who is None and replace is False and 'identity' not in skip:
            auto_who = self._recognise_side(st, owner, up, is_video)       # an existing person: find them by face
            if auto_who: who = auto_who[0]
        try:
            if owner in st['characters']:
                if who: base += f'_{who}'
                if is_video:
                    made = media.extract_person_refs(up, dest, base, isolate_refs=iso, who=who, ignore_text=notext,
                                                     kind=('full' if orient == 'lying' else kind), orient=orient)   # V0.9.50 a body-only video
                    rep = dict(media.TEXT_REPORT); text_notes = self._text_notes(rep)
                    if who in (None, 'only'):  # a clip showing several people would teach H3 the wrong identity
                        body_only = any(m.get('no_face') for m in made)          # V0.9.50 a body-only video: its clip too, marked so
                        clip = self._clean_clip(up, dest / f'{base}_clip.mp4', rep, text_notes, auto=not body_only)
                        if clip: made.append({'kind': 'ref_clip', 'file': Path(clip['path']).name, 'sha256': clip['sha256'], 'seconds': clip['seconds'],
                                              **{k: clip.get(k) for k in ('start', 'source_seconds', 'chosen', 'why')},
                                              **({'no_face': True, **({'pose': 'lying'} if any(m.get('pose') == 'lying' for m in made) else {})} if body_only else {})})
                else:
                    made = media.person_refs_from_image(up, dest, base, kind=kind, isolate_refs=iso, who=who, ignore_text=notext, orient=orient)
                    text_notes = self._text_notes(dict(media.TEXT_REPORT))
            else:
                made = media.location_plates(up, dest, base, k=3 if is_video else 1, ignore_text=notext)
                rep = dict(media.TEXT_REPORT); text_notes = self._text_notes(rep, place=True)
                if is_video:                                  # a place: the middle (the person auto-pick looks for a face)
                    clip = self._clean_clip(up, dest / f'{base}_clip.mp4', rep, text_notes, auto=False)
                    if clip: made.append({'kind': 'ref_clip', 'file': Path(clip['path']).name, 'sha256': clip['sha256'], 'seconds': clip['seconds']})
        except ValueError as e:
            raise StudioError(str(e))
        if replace and made:                          # new photos/video REPLACE this person's or place's earlier references
            for rid in list((st['characters'].get(owner) or st['locations'].get(owner))['refs']):
                self._append('REF_REMOVED', {'id': rid, 'owner': owner, 'reason': 'replaced by a new upload'})
            st = self.state()
        ids = []; existing = {(r['owner'], r['sha256']) for r in st['refs'].values() if not r.get('removed')}
        for i, m in enumerate(made):
            rid = f"ref_{src_sha[:10]}{'_' + who if who else ''}_{i}"
            if (owner, m['sha256']) in existing: continue          # already in use for this person/place
            while rid in st['refs']: rid += 'r'                      # a removed/replaced copy keeps its old id: give a new one
            m = {k: v for k, v in m.items() if k != 'path'}
            self._append('REF_ADDED', {'id': rid, 'owner': owner, **m, 'variant': m.get('variant', 'raw'),
                         'input_name': f"{self.input_sub}/{m['file']}", 'source_sha256': src_sha,
                         'source_kind': 'video' if is_video else 'image',
                         'provenance': 'USER_ATTESTED_ORIGINAL_NO_GENERATION_METADATA'})
            ids.append(rid)
        warnings = []
        for m in made:
            for pr in (m.get('quality') or {}).get('problems', []): warnings.append(pr)
            if m.get('kind') == 'location' and m.get('people_in_plate'):
                warnings.append(f"the place reference shows {m['people_in_plate']} person/people; they may appear in shots. "
                                'A short video of the empty place gives a clean plate.')
        warnings += text_notes
        if owner in st['characters'] and any(m.get('no_face') for m in made):          # V0.9.48 a body-only photo
            st2 = self.state(); C2 = st2['characters'][owner]
            has_face = any(st2['refs'][r]['kind'] in ('face', 'upper', 'full') and not st2['refs'][r].get('no_face') for r in C2['refs'] if r in st2['refs'])
            warnings.append(f"Body-only photo added for {C2['name']}: H3 takes the body, build and clothes from it, and the face from {C2['name']}'s "
                            + ('face photos.' if has_face else 'face photos, but there are none yet: add a photo or video where the face is clearly visible.'))
        if owner in st['characters'] and any(m.get('kind') == 'full' and m.get('feet_in') is False for m in made):   # V0.9.49
            nm = st['characters'][owner]['name']
            warnings.append(f"{nm}'s full-body reference has the feet out of the frame, so H3 has to guess {nm}'s legs and shoes. "
                            f"A photo or video showing {nm} head to toe, with some floor below the feet, gives H3 the lower body.")
        if auto_who: warnings.append(f"Several people are in this; {st['characters'][owner]['name']} was recognised on the {auto_who[0]} (face match {auto_who[1]:.2f}), so only that person was used.")
        if who and who != 'only' and is_video: warnings.append(f'No reference clip was kept from this video because it shows several people; the stills show only the person on the {who}.')
        if getattr(self, '_cc_note', None): warnings.append(self._cc_note); self._cc_note = None
        self.last_warnings = sorted(set(warnings))
        return ids

    def register_original(self, owner, input_name, kind, sha256, provenance):
        """Register an EXISTING original already in ComfyUI/input (used by the hallway importer).
        Verifies the hash; for person upper/full stills also makes the clean cut-out next to it."""
        st = self.state()
        f = self.input_dir / input_name
        if '..' in Path(input_name).parts or not f.is_file() or f.is_symlink(): raise StudioError('ORIGINAL_MISSING ' + input_name)
        if media.sha256(f) != sha256: raise StudioError('ORIGINAL_HASH_MISMATCH ' + input_name)
        if generation_markers(f): raise StudioError('ORIGINAL_CARRIES_GENERATION_METADATA ' + input_name)
        im = media.load_image(f)
        ids = []; base = f"{owner}_{sha256[:8]}"
        entry = {'kind': kind, 'alt': False, 'variant': 'raw', 'file': Path(input_name).name, 'sha256': sha256}
        if kind == 'location':
            entry.update(angle=0, people_in_plate=len(media.faces(im)), plate_signature=[round(float(v), 5) for v in media.small_hist(im).flatten()],
                         rgb_stats=media.rgb_stats([im]))
        else:
            fs = media.faces(im)
            entry['background_signature'] = media.background_signature(im, media.person_mask(im, fs[0] if len(fs) == 1 else None))
            if kind in ('upper', 'full'):
                clean, info = media.isolate(im, fs[0] if len(fs) == 1 else None)
                if clean is not None:
                    dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True)
                    cn = f'{base}_{kind}_clean.png'; cv_ok = __import__('cv2').imwrite(str(dest / cn), media.body_crop(clean, info))   # V0.9.49 head to toe
                    if cv_ok:
                        rid = f'ref_{sha256[:10]}_clean'
                        if rid not in st['refs']:
                            self._append('REF_ADDED', {'id': rid, 'owner': owner, 'kind': kind, 'alt': False, 'variant': 'clean', 'file': cn,
                                         'sha256': media.sha256(dest / cn), 'input_name': f'{self.input_sub}/{cn}', 'source_sha256': sha256,
                                         'source_kind': 'image', 'provenance': provenance + '_CLEAN_CUTOUT'})
                            ids.append(rid)
        rid = f'ref_{sha256[:10]}_orig'
        if rid not in st['refs']:
            self._append('REF_ADDED', {'id': rid, 'owner': owner, **entry, 'input_name': input_name, 'source_sha256': sha256,
                         'source_kind': 'image', 'provenance': provenance})
            ids.append(rid)
        return ids

    # -- reference choice -----------------------------------------------------------
    SAME_PLACE = 0.50     # measured: same hallway 0.29, different places 0.86-0.91

    def choose_refs(self, st, cast, location, focus=None, framing=None, prev_plan=None, bg_from=None, prefer=None, not_standing=(), hidden=()):
        """<= 6 clean image refs. Location plate first (same plate as the previous shot in the same place,
        a different angle when asked), then identity refs per person. For each person still, the RAW
        version is used only when it was filmed in this same place; otherwise the CLEAN isolated one."""
        refs = st['refs']; why = []
        cap = max(1, min(MAX_REFS, int(st['settings'].get('max_refs', MAX_REFS))))
        prefer = dict(prefer or {})
        for c in cast:
            if c in st['characters'] and st['characters'][c].get('main_source') and c not in prefer: prefer[c] = st['characters'][c]['main_source']
        def owned(owner):
            rs = [r for r in (st['characters'].get(owner) or st['locations'].get(owner) or {'refs': []})['refs'] if not refs[r].get('matched_from') and not refs[r].get('support_of')]
            want = prefer.get(owner)                       # the main reference video's stills first
            return sorted(rs, key=lambda r: 0 if want and refs[r].get('source_sha256') == want else 1) if want else rs
        plate = None
        if location:
            plates = sorted([r for r in owned(location) if refs[r]['kind'] == 'location'],     # your own photo before a made 🔒 plate
                            key=lambda r: (refs[r].get('variant') == 'frame_plate', refs[r].get('angle', 0)))
            prev_plate = next((r for r in (prev_plan or {}).get('refs', []) if refs.get(r, {}).get('kind') == 'location'), None)
            if plates:
                plate = plates[0]
                if prev_plate in plates and (prev_plan or {}).get('location') == location:
                    plate = prev_plate; why.append('same background plate as the previous shot (consistency)')
                    if framing in ('new_angle', 'reverse', 'profile') and len(plates) > 1:
                        sig = refs[prev_plate].get('plate_signature')
                        plate = max((p for p in plates if p != prev_plate),
                                    key=lambda p: media.hist_dist(refs[p]['plate_signature'], sig) if sig and refs[p].get('plate_signature') else 0)
                        why[-1] = 'different angle plate of the same place, as asked'
        chosen = [plate] if plate else []
        plate_sig = refs[plate].get('plate_signature') if plate else None
        # ⭐ V0.9.65 the best references from ALL of a person's uploads (faces: identity agreement, quality, angles, uploads;
        # bodies: one outfit, head to toe, the face matching). Off = the first upload's stills, as before.
        bp = {}; self.last_ref_sources = {}; self.last_support = set()
        if st['settings'].get('best_refs', True):
            import studio_bestref_v0965 as br
            nf = max(1, min(4, int(st['settings'].get('faces_per_person', 3))))
            for c in cast:
                if c not in st['characters']: continue
                try:
                    bp[c] = br.plan_person(refs, [r for r in owned(c) if r in refs], self._refinfo(), main_source=prefer.get(c),
                                           max_faces=nf, standing=c not in not_standing)
                    self.last_ref_sources[c] = bp[c]['outfit_sources']; self.last_support |= bp[c]['support']
                except Exception as e:
                    bp[c] = None; why.append(f"{st['characters'][c]['name']}: best-reference choice skipped ({type(e).__name__}); first upload's stills used")
        anchor = None
        if not location and bg_from:
            # background = the place behind this person in their OWN original photo/video
            mine = [r for r in owned(bg_from) if refs[r].get('variant', 'raw') == 'raw' and refs[r]['kind'] in ('full', 'upper')]
            look = set((bp.get(bg_from) or {}).get('outfit_sources') or [])        # ⭐ V0.9.65 the person in their main look
            if look and any(refs[r].get('source_sha256') in look for r in mine): mine = [r for r in mine if refs[r].get('source_sha256') in look]
            mine.sort(key=lambda r: (0 if refs[r]['kind'] == 'full' else 1, bool(refs[r].get('alt'))))
            if mine:
                anchor = mine[0]; chosen = [anchor]; plate_sig = refs[anchor].get('background_signature')
                C = st['characters'][bg_from]; their = {'she': 'her', 'he': 'his'}.get(C.get('pronoun'), 'their')
                why.append(f"background: the place behind {C['name']} in {their} own reference")
        mode = st['settings'].get('clean_refs', 'auto')
        decided = {}; body_cut = set(); posed = set()
        def fam_pick(owner, d, kind):
            raw = [d['raw']] if d.get('raw') else []; clean = [d['clean']] if d.get('clean') else []
            return decide(owner, kind, raw, clean)
        def pick(owner, kind, alt=False):
            body_first = lambda r: 0 if refs[r].get('no_face') else 1     # V0.9.48 a body-only photo you added is the body
            B = bp.get(owner)
            if B and kind == 'face':
                return B['faces'][1 if alt else 0] if len(B['faces']) > (1 if alt else 0) else None
            if B and kind in ('full', 'upper') and not any(refs[r]['kind'] == kind and refs[r].get('no_face') for r in owned(owner)):
                return fam_pick(owner, B[kind][0], kind) if (B[kind] and not alt) else None    # only the main look's body photos
            raw = sorted([r for r in owned(owner) if refs[r]['kind'] == kind and bool(refs[r].get('alt')) == alt and refs[r].get('variant', 'raw') == 'raw'], key=body_first)
            clean = sorted([r for r in owned(owner) if refs[r]['kind'] == kind and bool(refs[r].get('alt')) == alt and refs[r].get('variant') == 'clean'], key=body_first)
            return decide(owner, kind, raw, clean)
        def decide(owner, kind, raw, clean):
            if not raw and not clean: return None
            if owner == bg_from and anchor and raw: return raw[0]          # their background IS the scene
            if mode == 'never' or not clean: return raw[0] if raw else clean[0]
            if mode == 'always' or not raw: return clean[0]
            if location and kind in ('full', 'upper'):        # V0.9.45: an uncut full / upper-body photo is a whole scene of one person:
                body_cut.add(owner); return clean[0]          # H3 drifted to it and ended the shot on it (and the other person left)
            sig = refs[raw[0]].get('background_signature')
            if plate_sig is None or sig is None: return raw[0]
            same = media.hist_dist(sig, plate_sig) < self.SAME_PLACE
            decided[owner] = 'raw' if same else 'clean'
            return raw[0] if same else clean[0]
        people = sorted(cast, key=lambda c: 0 if c == focus else 1)
        for kind, alt in (('face', False), ('full', False), ('upper', False), ('face', True)):
            for c in people:
                if kind == 'full' and c in not_standing:                 # V0.9.48 📍 set sitting / lying: no standing full-body photo
                    fam = not_standing[c] if isinstance(not_standing, dict) else None
                    same = [r for r in owned(c) if refs[r]['kind'] == 'full' and fam == 'lying' and refs[r].get('pose') == 'lying'
                            and not refs[r].get('alt') and not (c in hidden and not refs[r].get('no_face'))]
                    same.sort(key=lambda r: 0 if refs[r].get('variant') == 'clean' else 1)
                    if same and len(chosen) < cap and same[0] not in chosen:      # V0.9.49 a photo of them lying down: the same pose, so it goes
                        chosen.append(same[0]); posed.add(c)
                    continue
                if c in hidden:                                            # 🙈 V0.9.48 face never shown: no face photo at all,
                    if kind == 'face': continue                            # and a body-only photo for the body when there is one
                    bodies = [r for r in owned(c) if refs[r]['kind'] == kind and refs[r].get('no_face') and not refs[r].get('alt')]
                    bodies.sort(key=lambda r: 0 if refs[r].get('variant') == 'clean' else 1)
                    if bodies:
                        if len(chosen) < cap and bodies[0] not in chosen: chosen.append(bodies[0])
                        continue
                r = pick(c, kind, alt)
                if r and len(chosen) < cap and r not in chosen: chosen.append(r)
        for c in people:
            name = st['characters'][c]['name']
            if c in posed: why.append(f'{name}: the full-body photo of {name} lying down (the same pose you set)')
            elif c in not_standing: why.append(f'{name}: no full-body photo this time (you set {name} not standing; it shows {name} standing)'
                                               + (f'. A photo of {name} lying down, head to toe, gives H3 the legs and clothes' if isinstance(not_standing, dict) and not_standing.get(c) == 'lying' else ''))
            if c in hidden:
                if any(refs[r].get('no_face') for r in chosen if refs[r]['owner'] == c):
                    why.append(f"🙈 {name}: face never shown in this shot, so no face photo; the body from {name}'s body-only photo")
                else:
                    why.append(f"🙈 {name}: face never shown in this shot, so no face photo; there is no body-only photo of {name}, so H3 still sees "
                               f"{name}'s face in a body photo: add one (a photo of the body without the face) for a sure result")
            if decided.get(c) == 'clean': why.append(f'{name}: isolated (clean) refs, filmed somewhere else than this place')
            elif c in body_cut: why.append(f'{name}: cut-out full and upper-body refs (no other background or pose for H3 to land on), original face photos')
            elif decided.get(c) == 'raw': why.append(f'{name}: original refs, filmed in this same place')
            elif mode == 'always' and any(refs[r].get('variant') == 'clean' for r in chosen if refs[r]['owner'] == c):
                why.append(f'{name}: isolated (clean) refs (setting: always)')
        if location and plate and len(chosen) < cap:
            others = [r for r in owned(location) if refs[r]['kind'] == 'location' and r != plate]
            if others: chosen.append(others[0]); why.append('a second angle of the place for background consistency')
        # ⭐ V0.9.65 room left: more of each person's best faces (other uploads / angles), then a second full-body photo in
        # the same clothes (another upload first), then a fourth face
        extra_n = {c: 0 for c in people}
        if bp:
            rounds = [('face', 2), ('full', 1), ('face', 3)]
            for kind, k in rounds:
                for c in people:
                    B = bp.get(c)
                    if not B or len(chosen) >= cap or c in hidden: continue
                    if kind == 'face' and len(B['faces']) > k and B['faces'][k] not in chosen:
                        chosen.append(B['faces'][k]); extra_n[c] += 1
                    elif kind == 'full' and c not in not_standing and len(B['full']) > 1:
                        lead = next((r for r in chosen if refs[r]['owner'] == c and refs[r]['kind'] == 'full'), None)
                        lead_src = refs[lead].get('source_sha256') if lead else None
                        alts = sorted(B['full'][1:], key=lambda d: 0 if d['source'] != lead_src else 1)
                        r = fam_pick(c, alts[0], 'full')
                        if r and r not in chosen: chosen.append(r); extra_n[c] += 1
        for c in people:
            B = bp.get(c)
            if not B: continue
            name = st['characters'][c]['name']
            used = [r for r in chosen if r in B['face_info']]
            if B['main'] and B['n_sources'] > 1:
                sup = [r for r in used if r in B['support']]
                why.append(f"⭐ {name}: main reference = " + ('the upload you ★ starred' if B['main_why'] == 'starred' else 'the upload that shows them best')
                           + f" (it sets {name}'s clothes, hair and makeup; every body photo and the first face come from it)"
                           + (f"; {len(sup)} face(s) from {len({B['face_info'][r]['src'] for r in sup})} other upload(s) support it as close crops, for the facial features only" if sup else ''))
            if used and B['n_face_cands'] > len(used):
                fi = [B['face_info'][r] for r in used]
                agree = ', '.join(f"{f['sim']:.2f}" for f in fi if f['sim'] is not None)
                angles = [a for a in ('left', 'front', 'right') if any(f['yaw'] == a for f in fi)]
                why.append(f"⭐ {name}: the best {len(used)} of {B['n_face_cands']} face stills" + (', angles ' + '/'.join(angles) if len(angles) > 1 else '')
                           + (f'; identity agreement {agree}' if agree else ''))
            for n in B['notes']: why.append(f'⭐ {name}: {n}')
            if extra_n[c]: why.append(f'⭐ {name}: {extra_n[c]} more of {name}\'s best photos in the spare reference slots')
        missing = [st['characters'][c]['name'] for c in cast if not any(refs[r]['owner'] == c for r in chosen)]
        if True:
            # keep each person's references together, in a fixed order, so the prompt can say "images 2-5 are Maya"
            # (always grouped, so the numbering setting changes only the prompt text: a fair A/B test)
            groups = [r for r in chosen if refs[r]['kind'] == 'location']
            order = ([bg_from] if anchor else []) + [c for c in people if c != (bg_from if anchor else None)]
            for c in order:
                mine = [r for r in chosen if refs[r]['owner'] == c]
                if anchor and c == bg_from: mine = [anchor] + [r for r in mine if r != anchor]
                groups += mine
            groups += [r for r in chosen if r not in groups]
            chosen = groups
        return chosen, missing, why

    def _capped_clip(self, st, rid):
        """A reference clip longer than 73 frames (clips made before V0.9.6 are 5 s) -> its 73-frame copy (made once,
        from the Director's own caption-cleaned clip; nothing generated is involved). Returns the id to use."""
        r = st['refs'][rid]
        if r.get('kind') != 'ref_clip' or float(r.get('seconds') or 0) <= media.REF_CLIP_SECONDS + 0.05: return rid
        have = next((k for k, x in st['refs'].items() if x.get('capped_from') == rid and not x.get('removed')), None)
        if have: return have
        src = self.input_dir / r['input_name']
        if not src.is_file(): return rid
        dest = src.with_name(src.stem + f'_c{media.REF_CLIP_FRAMES}.mp4')
        try: c = media.trim_ref_clip(src, dest)
        except Exception: return rid
        nid = rid + f'_c{media.REF_CLIP_FRAMES}'
        while nid in st['refs']: nid += 'c'
        self._append('REF_ADDED', {**{k: v for k, v in r.items() if k not in ('removed', 'audio', 'seq')}, 'id': nid, 'file': dest.name,
                     'input_name': str(Path(r['input_name']).with_name(dest.name)), 'sha256': c['sha256'], 'seconds': c['seconds'], 'capped_from': rid})
        return nid

    def choose_videos(self, st, cast, location, focus=None, lying=(), hidden=()):
        """Up to 3 ORIGINAL clips (73 frames, about 3 s, each): each person in the shot, then the place. V0.9.50: someone you
        set lying gets their clip of lying down (a body-only video) when there is one; someone whose face is hidden (🙈)
        gets their body-only clip, never a clip showing the face."""
        if st['settings'].get('video_refs', 'auto') == 'off': return []
        refs = st['refs']; out = []
        for c in sorted(cast, key=lambda c: 0 if c == focus else 1):
            want = st['characters'][c].get('main_source')
            clips = [r for r in st['characters'][c]['refs'] if refs[r]['kind'] == 'ref_clip' and not refs[r].get('capped_from')]   # never 'tail_clip' (only for extending)
            body = [r for r in clips if refs[r].get('no_face')]
            lie = [r for r in body if refs[r].get('pose') == 'lying']
            if c in lying and lie: clip = lie[0]
            elif c in hidden: clip = (body or [None])[0]
            else:
                outfit = (getattr(self, 'last_ref_sources', None) or {}).get(c) or []      # ⭐ V0.9.65 a clip in the same clothes as the stills
                if outfit:                                   # only the main reference's own clip: another upload's clothes never mix in
                    clip = next((r for r in clips if not refs[r].get('no_face') and refs[r].get('source_sha256') in outfit), None)
                else:
                    clip = (next((r for r in clips if want and refs[r].get('source_sha256') == want), None)
                            or next((r for r in clips if not refs[r].get('no_face')), None) or (clips[0] if clips else None))
            if clip: out.append(clip)
        if location:
            clip = next((r for r in st['locations'][location]['refs'] if refs[r]['kind'] == 'ref_clip' and not refs[r].get('capped_from')), None)
            if clip: out.append(clip)
        return out[:MAX_VIDEOS]

    def loras_for(self, st, cast):
        out = []
        for l in st['settings'].get('loras', []):
            if l.get('enabled', True) and (l.get('scope', 'all') == 'all' or l.get('scope') in cast):
                out.append(l)
        return out

    # -- planning -------------------------------------------------------------------
    def last_shot(self, st):
        return st['shots'][max(st['shots'])] if st['shots'] else None

    START_KINDS = ('full', 'upper', 'location', 'face')

    def start_frames(self, st=None):
        """The stills you can open a shot on: YOUR original photos and frames of your original videos (never a cut-out on
        grey, a colour-matched copy, or anything generated)."""
        st = st or self.state(); out = []
        owners = {**{c['id']: c['name'] for c in st['characters'].values() if not c.get('retired')},
                  **{l['id']: l['name'] for l in st['locations'].values() if not l.get('retired')}}
        for r in st['refs'].values():
            if r.get('removed') or r.get('kind') not in self.START_KINDS or r.get('variant', 'raw') != 'raw' or r.get('matched_from'): continue
            if not str(r.get('provenance', '')).startswith('USER_ATTESTED') or r.get('owner') not in owners: continue
            if r.get('sha256') in st['generated_sha']: continue
            out.append({'id': r['id'], 'owner': r['owner'], 'who': owners[r['owner']], 'kind': r['kind'], 'file': r['file'], 'input_name': r['input_name'],
                        'time': r.get('time'), 'from_video': r.get('source_kind') == 'video'})
        return out

    def plan(self, text, *, mode='auto', location=None, background=None, target_shot=None, _settings=None, _seed=None, _extra=None, extend=None, people=None, emotions=None,
             start_frame=None, end_frame=None, pose=None, duration=None, positions=None, hide_faces=None, reveal=None, compose_start=False):
        """duration (V0.9.36): seconds this shot runs in the movie (🎬 film plans), unless the text says its own.
        mode: auto | next | revise | retake. pose (V0.9.21): {'source': id from pose_sources(), 'strength', 'start'} to move
        like one of YOUR videos; None keeps the previous take's movement on a revise / retake; 'none' removes it. background: None (auto: keep the previous shot's choice) |
        a location id | 'people' (use the place behind the people in their own photos/videos)."""
        st = self.state()
        if _settings: st['settings'] = {**st['settings'], **_settings}
        chars = [c for c in st['characters'].values() if not c.get('retired')]
        places = {i: L for i, L in st['locations'].items() if not L.get('retired')}
        if not chars and not places: raise StudioError('ADD_A_CHARACTER_OR_LOCATION_FIRST')
        last = self.last_shot(st)
        if target_shot is not None:
            if target_shot not in st['shots']: raise StudioError('NO_SUCH_SHOT')
            last = st['shots'][target_shot]
            if mode not in ('revise', 'retake'): mode = 'retake' if last['takes'] else 'revise'
        prev_plan = last['plans'][-1] if last else None
        live = {c['id'] for c in chars}
        prev_cast = [c for c in prev_plan['cast'] if c in live] if prev_plan else None
        try:
            d = si.interpret(text, chars, previous_cast=prev_cast,
                             focus=(prev_plan or {}).get('spec', {}).get('subject'))
        except ValueError as e:
            if 'EMPTY_DIRECTION' in str(e): raise StudioError('WRITE_WHAT_HAPPENS: type what happens in this shot first (for example "Maya walks in and smiles")')
            if 'DIRECTION_TOO_LONG' in str(e): raise StudioError(TOO_LONG)
            raise
        if people:                                   # picked in the Who row: exactly these people, the first is the focus
            byname = {c['name']: c['id'] for c in chars}
            picked = list(dict.fromkeys(byname.get(p, p) for p in people))
            if any(p not in live for p in picked): raise StudioError('UNKNOWN_PERSON')
            extra = [c for c in d['cast'] if c not in picked and not d.get('replaced')]
            d['cast'] = picked + [c for c in extra if si.mentions(re.sub(r'"[^"]*"', ' ', text), [st['characters'][c]])[1]]
            if d['question'] and d['question'].startswith('Who do you mean'): d['question'] = None
            if not d.get('subject'): d['subject'] = picked[0]
        others_emo = {}
        if emotions:                                 # per-person emotions picked in the UI: {name or id: (emotion key, intensity)}
            import director_intent_v0739 as di_
            known = {k for k, *_ in di_.EMOTIONS}
            byname = {c['name']: c['id'] for c in chars}
            for who_, val in emotions.items():
                cid_ = byname.get(who_, who_)
                if cid_ not in live: raise StudioError('UNKNOWN_PERSON')
                emo_, inten_ = (val if isinstance(val, (list, tuple)) else (val, 'normal'))
                if emo_ not in known: raise StudioError('UNKNOWN_EMOTION')
                if inten_ not in ('subtle', 'normal', 'strong'): inten_ = 'normal'
                others_emo[cid_] = {'emotion': emo_, 'intensity': inten_}
            if people:
                for cid_ in list(others_emo):
                    if cid_ not in d['cast']: d['cast'].append(cid_)
        if (_extra or {}).get('film') and d['action'] not in ('DIRECT', 'RETAKE'):
            # V0.9.38 🎬: a film shot is always a shot. Its words ("she comes in earlier", "wait", "go outside", "the cut of
            # his jacket") used to be read as an editing or project command ("Loosen the cut by a few frames") and the shot stopped.
            d['action'] = 'DIRECT'; d['edit'] = None; d['reading'] = si.describe(d, chars)
        if d['question']: return {'action': 'QUESTION', 'question': d['question'], 'understood_as': d['reading']}
        if d['action'] not in ('DIRECT', 'RETAKE'):
            return {'action': 'NOT_A_SHOT', 'understood_as': d['reading'], 'interpretation': d['action']}
        if mode == 'auto':
            if d['action'] == 'RETAKE' and last: mode = 'retake'
            elif last and not last['takes']: mode = 'revise'
            else: mode = 'next'
        if mode in ('revise', 'retake') and not last: mode = 'next'
        used = set(st['shots']) | set(st.get('memory', {}))
        shot = (max(used) + 1 if used else 1) if mode == 'next' else last['shot']
        base_spec = {} if mode == 'next' else dict(prev_plan['spec'])
        spec = merge(base_spec, d)
        if duration is not None and not d.get('duration'):
            spec['duration'] = max(1.0, min(10.0, float(duration)))
        if emotions is not None:
            if not spec.get('subject') and others_emo: spec['subject'] = next(iter(others_emo))
            subj_ = spec.get('subject')
            if subj_ in others_emo:                  # the focus person's own pick is THE emotion of the shot
                spec['emotion'] = others_emo[subj_]['emotion']; spec['intensity'] = others_emo[subj_]['intensity']
            spec['emotions'] = {k: v for k, v in others_emo.items() if k != subj_}
        if mode == 'retake' or (mode == 'revise' and d['action'] == 'RETAKE'):
            spec['take_bump'] = spec.get('take_bump', 0) + 1
        named_loc = None
        low = ' ' + re.sub(r'"[^"]*"', ' ', text).lower() + ' '
        for lid, L in places.items():
            if re.search(r'(?<![a-z0-9])' + re.escape(L['name'].lower()) + r'(?![a-z0-9])', low): named_loc = lid
        if background not in (None, 'auto', 'people') and background not in places: raise StudioError('UNKNOWN_BACKGROUND')
        if location and location not in places: raise StudioError('UNKNOWN_LOCATION')
        prev_bg = (prev_plan or {}).get('background') or {}
        want_people = background == 'people' or (background in (None, 'auto') and not location and not named_loc and
                                                  (d.get('background_from_people') or (prev_bg.get('mode') == 'people' and not d.get('background_place'))))
        if background not in (None, 'auto', 'people'): location = background
        if want_people: loc = None
        else:
            loc = location or named_loc or ((prev_plan or {}).get('location') if (prev_plan or {}).get('location') in places else None) \
                  or next(iter(places), None)
        named = bool(si.mentions(re.sub(r'"[^"]*"', ' ', text), chars)[1]) if chars else False
        cast = prev_cast if (mode != 'next' and not named and not people) else d['cast']
        if d.get('replaced') or d.get('removed'):
            cast = d['cast']
            # earlier direction carried into this take must follow the new casting
            def words(cid): C = st['characters'][cid]; return [C['name']] + list(C.get('descriptors', []))
            acts = []
            for a in spec.get('actions', []):
                for old_c, new_c in d.get('replaced', {}).items():
                    for w in words(old_c): a = re.sub(r'(?i)(?<![\w])' + re.escape(w) + r'(?![\w])', st['characters'][new_c]['name'], a)
                if any(re.search(r'(?i)(?<![\w])' + re.escape(w) + r'(?![\w])', a) for c in d.get('removed', []) for w in words(c)): continue
                acts.append(a)
            spec['actions'] = acts
            spec['dialogue'] = [x for x in spec.get('dialogue', []) if x.get('speaker') not in d.get('removed', [])]
            spec['dialogue'] = [{**x, 'speaker': d['replaced'].get(x.get('speaker'), x.get('speaker'))} for x in spec['dialogue']] if d.get('replaced') else spec['dialogue']
            if spec.get('subject') in d.get('replaced', {}): spec['subject'] = d['replaced'][spec['subject']]
            if spec.get('subject') in d.get('removed', []): spec.pop('subject', None)
            if spec.get('framing_subject') in d.get('replaced', {}): spec['framing_subject'] = d['replaced'][spec['framing_subject']]
            if spec.get('framing_subject') in d.get('removed', []): spec.pop('framing_subject', None)
        bg_from = None
        if loc is None and cast:
            bg_from = next((c for c in ([spec.get('subject')] + list(cast)) if c in cast and any(
                st['refs'][r]['kind'] in ('full', 'upper') and st['refs'][r].get('variant', 'raw') == 'raw'
                for r in st['characters'][c]['refs'])), None)
        if len(cast) > MAX_PEOPLE:
            names = ', '.join(st['characters'][c]['name'] for c in cast)
            return {'action': 'QUESTION', 'understood_as': d['reading'],
                    'question': f'H3 handles at most two people in one shot, and this would have {len(cast)} ({names}). Which two should be in it?'}
        if extend is None and mode in ('revise', 'retake') and prev_plan and prev_plan.get('extend') and shot == prev_plan['shot']:
            extend = prev_plan['extend']                 # a retake of an extension still continues from YOUR original
        if extend and extend['owner'] in st['characters'] and extend['owner'] not in cast:
            cast = ([extend['owner']] + [c for c in cast if c != extend['owner']])[:MAX_PEOPLE]
        prefer = {extend['owner']: extend['source_sha256']} if extend else None
        # 📍 V0.9.45 where you set everyone (start, and optionally end); a retake / revise of the same shot keeps them
        if positions is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']: positions = (prev_plan.get('spec') or {}).get('positions')
        import studio_positions_v0945 as pm
        try: positions = pm.clean(positions, [st['characters'][c]['name'] for c in cast if c in st['characters']])
        except pm.PositionsError as e: raise StudioError(str(e))
        if positions: spec['positions'] = positions
        else: spec.pop('positions', None)
        if (_extra or {}).get('film_angle') and loc: spec['place_view'] = True      # 📷 V0.9.46 one angle = the place photo's viewpoint
        elif mode == 'next': spec.pop('place_view', None)
        if (_extra or {}).get('film_lock') and loc: spec['frame_lock'] = True       # 🔒 V0.9.47 ... and nobody leaves that frame
        elif mode == 'next': spec.pop('frame_lock', None)
        _pro = {st['characters'][c]['name']: st['characters'][c].get('pronoun') for c in cast if c in st['characters']}
        pos_words = ' '.join(x for x in pm.for_h3(positions, pronoun=lambda n: _pro.get(n) or 'they') if x) if positions else None
        pos_warn = pm.clashes(text, positions) if positions else []                  # V0.9.48 the shot's text vs your positions
        lead = (_extra or {}).get('lead_in')                                         # ⏱ V0.9.48 seconds to move into place first
        if lead is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']: lead = (prev_plan.get('spec') or {}).get('lead_in')

        _byname = {st['characters'][c]['name']: c for c in cast if c in st['characters']}
        # 🙈 V0.9.48 people whose face is never shown in this shot (a retake keeps it)
        if hide_faces is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']: hide_faces = (prev_plan.get('spec') or {}).get('hide_faces')
        hidden = {_byname[n] for n in (hide_faces or []) if n in _byname}
        if hidden: spec['hide_faces'] = sorted(st['characters'][c]['name'] for c in hidden)
        else: spec.pop('hide_faces', None)
        if reveal is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']: reveal = (prev_plan.get('spec') or {}).get('reveal')
        rv = [n for n in (reveal or []) if n in _byname and _byname[n] not in hidden]
        if rv: spec['reveal'] = rv
        else: spec.pop('reveal', None)
        not_standing = {_byname[r['who']]: pm.FAMILY.get(r['posture']) for r in (positions or {}).get('start') or []
                        if r['who'] in _byname and pm.FAMILY.get(r['posture']) not in ('standing',)}
        refs, missing, ref_why = self.choose_refs(st, cast, loc, focus=spec.get('subject'), framing=spec.get('framing'),
                                                  prev_plan=prev_plan, bg_from=bg_from, prefer=prefer, not_standing=not_standing, hidden=hidden)
        if getattr(self, 'last_support', None):           # ⭐ V0.9.65 supporting faces from other uploads -> close crops of the face
            refs, dropped = self._support_crops(st, refs, self.last_support)
            if dropped: ref_why.append(f'⭐ {dropped} supporting face(s) left out: the face could not be cropped close')
            st = self.state()
            if _settings: st['settings'] = {**st['settings'], **_settings}
        bg = {'mode': 'place', 'location': loc} if loc else ({'mode': 'people', 'from': bg_from} if bg_from else {'mode': 'none'})
        if loc is None and want_people and not bg_from:
            ref_why.append("no photo of the people shows a usable background (only face crops); add a place or a wider photo")
        if not refs: raise StudioError('NO_REFERENCES_YET_ADD_A_PHOTO_OR_VIDEO')
        if st['settings'].get('match_refs', False):
            refs, n_matched = self._color_matched(st, refs, bg_from)
            if n_matched: ref_why.append(f'{n_matched} reference(s) gently colour-matched to the scene anchor (white balance, tint, exposure; your originals are untouched)')
            st = self.state()                                  # includes the matched copies just added
            if _settings: st['settings'] = {**st['settings'], **_settings}
        videos = self.choose_videos(st, cast, loc, focus=spec.get('subject'), lying={c for c, f in not_standing.items() if f == 'lying'}, hidden=hidden)
        capped = [self._capped_clip(st, r) for r in videos]
        if capped != videos:                               # an older 5 s clip got its 73-frame copy: read it back
            videos = capped; st = self.state()
            if _settings: st['settings'] = {**st['settings'], **_settings}
        audios = self.choose_voices(st, cast, spec)
        anchors = []
        if extend:
            how = st['settings'].get('extend_with', 'anchor')
            anchor = self._anchor_for(st, extend['clip']) if how in ('anchor', 'anchor_clip') else None
            if anchor:
                st = self.state()
                if _settings: st['settings'] = {**st['settings'], **_settings}
                anchors = [anchor]
            extend = {k: v for k, v in extend.items() if k != 'anchored'}
            if anchors: extend['anchored'] = True
            # pinned: the take STARTS on the last ~0.9 s of your original (Add Guide, frame 0), then moves on; that second is cut
            # off in the movie. The clip (<Video 1>) is only sent too with "pin + clip" or when the guide can't be made.
            videos = [extend['clip']] if (not anchors or how == 'anchor_clip') else []
            if start_frame: raise StudioError('AN_EXTENSION_ALREADY_STARTS_ON_YOUR_VIDEO: no separate start frame')
            ref_why.insert(0, ('starts on the last second of your original video (pinned frames and sound) and carries on from there; '
                               'that second is cut off in the movie' + (', plus its last 3 s as <Video 1>' if videos else ''))
                              if anchors else 'continues your original video from its last frame ([video continuation], its last seconds as the only video reference)')
        anchor_at = {}
        if start_frame is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot'] and not extend and not compose_start \
                and prev_plan.get('frame_ok') is not False:                  # (V0.9.61 not a built frame you said no to)
            start_frame = prev_plan.get('start_frame')                       # a retake opens on the same picture
        built = None; spec.pop('stand_ins', None)
        if compose_start and not start_frame and not extend and (positions or {}).get('start'):     # 🧩 V0.9.55 the first frame from your dots
            cfd = ((_extra or {}).get('carry_frame') or {}).get('dots')        # 🔗 V0.9.60 measured where the shot before cuts (with face sizes)
            built = self.compose_first_frame(st, cast, loc, ({'start': cfd} if cfd else (spec.get('positions') or positions)), hidden=hidden)
            start_frame = built; st = self.state(); stand = list((getattr(self, '_stand_in_of', None) or {}).get(built) or [])
            if _settings: st['settings'] = {**st['settings'], **_settings}
            if lead is None: lead = 1.5                                      # the paste-up moment is cut off in the movie
            if stand:                                                        # V0.9.61 standing in the picture: H3 gets them into the pose
                lead = max(float(lead), 2.5); spec['stand_ins'] = stand
        if start_frame and not extend:
            ok_ids = {x['id'] for x in self.start_frames(st)}
            if start_frame not in ok_ids and not (start_frame == built or (st['refs'].get(start_frame) or {}).get('variant') == 'composed'
                                                  or ((st['refs'].get(start_frame) or {}).get('variant') == 'redrawn' and ((_extra or {}).get('carry_frame') or {}).get('redrawn') == start_frame)):
                raise StudioError('START_FRAME_MUST_BE_ONE_OF_YOUR_ORIGINAL_STILLS')
            sf_pic, sf_fit = start_frame, None
            if st['settings'].get('fit_pictures', True):                     # 📐 V0.9.69 the file H3 gets: your picture cropped to 9:16 around its people
                try: sf_pic, sf_fit = self.fit_picture(st, start_frame)
                except Exception: sf_pic, sf_fit = start_frame, None
                if sf_pic != start_frame:
                    st = self.state()
                    if _settings: st['settings'] = {**st['settings'], **_settings}
            anchors = [sf_pic]; anchor_at[sf_pic] = 0
            cf = (_extra or {}).get('carry_frame') or ((prev_plan or {}).get('carry_frame') if (mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']) else None)
            ref_why.insert(0, (f"🧪 opens where shot {cf['from'][0]} ends: the frame built from your own photos, redrawn by Qwen-Image-Edit in the poses "
                               f"everyone had when shot {cf['from'][0]} cuts (stick figures only from that shot, never its pixels); pinned as frame 0, the movie cuts in after the lead-in"
                               if (cf and st['refs'][start_frame].get('variant') == 'redrawn') else
                               f"🔗 opens where shot {cf['from'][0]} ends: a first frame built from your own photos, everyone placed where they were "
                               f"when shot {cf['from'][0]} cuts; pinned as frame 0, the movie cuts in after the lead-in" if (cf and st['refs'][start_frame].get('variant') == 'composed') else
                               f"🧩 opens on a first frame built from your positions (the room with each person's cut-out from your own photos, "
                               f"where you put them), pinned as frame 0; the movie cuts in after the lead-in" if st['refs'][start_frame].get('variant') == 'composed'
                               else f"opens exactly on your original still ({st['refs'][start_frame]['file']}), pinned as frame 0"))
            _fn = self._fit_note(st, start_frame, sf_fit, cast)
            if _fn: ref_why.insert(1, _fn)
        else: sf_pic = None
        if end_frame is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']:
            end_frame = prev_plan.get('end_frame')                           # a retake ends on the same picture
        if end_frame:                                    # position target: the take ENDS exactly on one of your original stills
            if end_frame not in {x['id'] for x in self.start_frames(st)}: raise StudioError('END_FRAME_MUST_BE_ONE_OF_YOUR_ORIGINAL_STILLS')
            if end_frame in anchors or end_frame == start_frame: raise StudioError('START_AND_END_FRAME_MUST_DIFFER')
            ef_pic, ef_fit = end_frame, None
            if st['settings'].get('fit_pictures', True):                     # 📐 V0.9.69
                try: ef_pic, ef_fit = self.fit_picture(st, end_frame)
                except Exception: ef_pic, ef_fit = end_frame, None
                if ef_pic != end_frame:
                    st = self.state()
                    if _settings: st['settings'] = {**st['settings'], **_settings}
            anchors = anchors + [ef_pic]; anchor_at[ef_pic] = -1
            ref_why.insert(0, f"ends exactly on your original still ({st['refs'][end_frame]['file']}): everyone in its positions by the last frame")
            _fn = self._fit_note(st, end_frame, ef_fit, cast, 'ending')
            if _fn: ref_why.insert(1, _fn)
        else: ef_pic = None
        if hidden and start_frame and not extend:        # 🙈 V0.9.48 the pinned picture must not show a face this shot hides
            shown = []
            try:                                         # only a face RECOGNISED as theirs counts (not "a face is there")
                import studio_identity_v090 as idn
                if idn.available():
                    embs = {c: self.person_embedding(st, c) for c in hidden}; embs = {c: e for c, e in embs.items() if e is not None}
                    got = idn.assign(idn.embed(media.load_image(self.input_dir / st['refs'][sf_pic or start_frame]['input_name'])), embs) if embs else {}
                    shown = [st['characters'][c]['name'] for c, x in got.items() if x[0] >= idn.MATCH and x[2] >= idn.MARGIN]
            except Exception: shown = []
            if shown: ref_why.insert(0, f"⚠ 🙈 {', '.join(shown)}'s face is visible in the picture this shot opens on, so it shows in the first "
                                        "frame: pick an opening picture where that face isn't visible for the hidden shots")
        # 📐 V0.9.69 where everyone IS in the pinned first frame: H3 is told who is who in it; your dots and the shot's text are
        # held against it (dots that agree take its exact spot; dots that differ get a lead-in to move there)
        spec.pop('picture_layout', None); spec.pop('end_layout', None)
        if sf_pic and not extend and st['settings'].get('fit_pictures', True):
            _names = [st['characters'][c]['name'] for c in cast if c in st['characters'] and c not in hidden]
            lay = self.picture_layout(st, sf_pic, _names); pic_rows = (lay or {}).get('rows') or []
            if pic_rows:
                spec['picture_layout'] = [{'who': r['who'], 'x': r['x'], 'y': r['y']} for r in pic_rows]
                ref_why.insert(1, '📐 read from the frame H3 starts on and told to H3: ' + '; '.join(f"{r['who']} {fr.where(r)}" for r in pic_rows))
                try:                                     # 🔗 a lead-in back to where the shot before ended: do they have to cross?
                    _story = self.story(st, before=shot); _cut = (_story[-1].get('at_cut') or {}) if _story else {}
                    _ltr = _cut.get('left_to_right') or []
                    _join = (_extra or {}).get('film_join') or ((prev_plan or {}).get('film_join') if mode in ('revise', 'retake') else None)
                    if lead and _join == 'carry' and len(_ltr) >= 2 and len(pic_rows) == 2 and {pic_rows[0]['who'], pic_rows[1]['who']} == {_ltr[0], _ltr[-1]} \
                            and pic_rows[0]['who'] != _ltr[0]:
                        d['assumptions'] = list(d['assumptions']) + [
                            f"CHECK: 📐 the opening picture has {pic_rows[0]['who']} on the left, but the shot before ends with {_ltr[0]} on the left: in the "
                            "lead-in they would have to CROSS each other, and H3 often swaps who is who instead. A picture with them on those sides (or "
                            "✂ a cut / ⏩ a time skip instead of 🔗 carrying on) is far more reliable."]
                except Exception: pass
                asked = (positions or {}).get('start')
                made = (st['refs'].get(start_frame) or {}).get('variant') in ('composed', 'redrawn')     # built from the dots: nothing to settle
                if asked and not made:
                    df = fr.differences(asked, pic_rows)
                    if not df['moves'] and not df['crossing']:
                        if not lead:
                            positions = {**positions, 'start': fr.snap(asked, pic_rows)}; spec['positions'] = positions
                            pos_words = ' '.join(x for x in pm.for_h3(positions, pronoun=lambda n: _pro.get(n) or 'they') if x)
                            ref_why.insert(1, '📐 your 📍 start dots agree with the opening picture: the prompt uses exactly where the picture has everyone')
                    else:
                        if not lead:
                            lead = 2.5 if df['crossing'] else 2.0
                            ref_why.insert(1, f"📐 your 📍 start dots differ from the opening picture, and frame 0 IS the picture: a {lead:g} s ⏱ lead-in was added "
                                              "so H3 moves everyone from the picture to your dots (set the lead-in yourself, or clear the dots to open as the picture shows)")
                        d['assumptions'] = list(d['assumptions']) + ['CHECK: 📐 ' + '; '.join(
                            f"{w}: the opening picture has {w} {fr.where(s_)}, your dots {fr.where(r_)}" for w, r_, s_ in df['moves'][:2] or
                            [(a_['who'], a_, next(x for x in pic_rows if x['who'] == a_['who'])) for a_ in asked if any(x['who'] == a_['who'] for x in pic_rows)][:2])
                            + '. H3 moves them there in the lead-in.'
                            + (' ⚠ They would have to CROSS each other to get there: H3 often swaps who is who instead. A picture with them on '
                               'those sides, or dots on the picture\'s sides, is far more reliable.' if df['crossing'] else '')]
                elif not asked:
                    cl = fr.text_side_clashes(text, pic_rows)
                    if cl: d['assumptions'] = list(d['assumptions']) + ['CHECK: 📐 ' + '; '.join(cl) + ': the shot opens as the picture shows. Change the '
                                                                       'text, pick another picture, or set 📍 dots (a ⏱ lead-in then moves everyone there).']
        if ef_pic and st['settings'].get('fit_pictures', True):
            lay = self.picture_layout(st, ef_pic, [st['characters'][c]['name'] for c in cast if c in st['characters'] and c not in hidden])
            if (lay or {}).get('rows'): spec['end_layout'] = [{'who': r['who'], 'x': r['x'], 'y': r['y']} for r in lay['rows']]
        if lead and start_frame and not extend:          # ⏱ V0.9.48 (after a retake has inherited its start picture)
            spec['lead_in'] = float(lead); ref_why.insert(1, f"⏱ the first {float(lead):g} s are for everyone to move from the picture into place; the movie cuts in after them")
            for w in (getattr(self, '_cut_off_of', None) or {}).get(start_frame) or []:   # V0.9.62
                ref_why.insert(2, f"🧩 {w}'s full-body photo stops above the feet, so the built frame fades out there; add one that shows {w} head to toe for a cleaner frame")
            for x in spec.get('stand_ins') or []:                                       # V0.9.61
                ref_why.insert(2, f"🧩 no photo of {x['who']} {str(x['posture']).replace('_', ' ')}: {x['who']} stands in the built frame and H3 brings them into that pose in those first seconds")
        else: spec.pop('lead_in', None)
        loras = self.loras_for(st, cast)
        if videos and not extend: ref_why.append(f'{len(videos)} original 3 s (73-frame) reference clip(s) for motion and identity')
        if loras: ref_why.append('LoRA: ' + ', '.join(f"{l['name']} @ {l['strength']}" for l in loras))
        join = (_extra or {}).get('film_join')
        if join is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']: join = prev_plan.get('film_join')
        continuity = self.continuity_text(st, shot, cast, opens_on=(bool(start_frame) and not extend) or bool((positions or {}).get('start')), join=join,
                                          lead=spec.get('lead_in'))
        # a scene-planner shot knows what happened in the beat just before it (as state, never as an action to perform)
        scene_ctx = (_extra or {}).get('scene_context')
        if scene_ctx is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot']: scene_ctx = prev_plan.get('scene_context')
        if scene_ctx: continuity = (continuity + ' ' + scene_ctx).strip()
        spec.pop('start_sides', None)
        if not positions and not start_frame and not end_frame and not extend:      # 📍 V0.9.69 nothing says who is on which side: fix it
            try: sides = self._fixed_sides(st, shot, cast, text, join)
            except Exception: sides = None
            if sides:
                spec['start_sides'] = sides
                ref_why.append(f"📍 the shot doesn't say who is on which side, so the Director fixes it: {sides[0]} on the left, {sides[1]} on the right, "
                               "the same in every take (say it in the shot or set 📍 dots to choose)")
        lessons = self.lessons(st, cast)
        length = h3_length(st['settings'].get('gen_length', 124)); length = length if length in GEN_LENGTHS else 124
        want = spec.get('duration')
        if isinstance(want, (int, float)) and spec.get('lead_in'): want_gen = want + float(spec['lead_in'])   # ⏱ V0.9.48 lead-in on top
        else: want_gen = want
        if isinstance(want, (int, float)) and want_gen * 24 + 14 > length:
            # you asked for more than the setting makes: this shot is generated longer (H3 lengths only; +~0.6 s for its settling start)
            need = int(want_gen * 24 + 14)
            length = next((L for L in LONG_LENGTHS if L >= need), LONG_LENGTHS[-1])
            ref_why.append(f'generated {length / 24:.1f} s long (instead of the usual {h3_length(st["settings"].get("gen_length", 124)) / 24:.1f} s) '
                           f'so the shot can run about {want:g} s' + (' (longer than 7.3 s is experimental: more GPU memory and time)' if length > 175 else ''))
        if audios: ref_why.append('voice: ' + ', '.join(f"{st['characters'][st['refs'][r]['owner']]['name']} (your recording, audio {i + 1})" for i, r in enumerate(audios)))
        if pose is None and mode in ('revise', 'retake') and prev_plan and shot == prev_plan['shot'] and prev_plan.get('pose'):
            pose = {k: prev_plan['pose'][k] for k in ('source', 'strength', 'start', 'who') if k in prev_plan['pose']}       # a new take keeps its movement
        pose_rec = self._prepare_pose(st, pose, length) if pose and pose != 'none' else None
        pose_note = ''
        if pose_rec:
            ref_why.append(f"🎭 moves like {pose_rec['label']} (strength {pose_rec['strength']:g}): stick figures read from your video steer the take")
            pose_rec, pose_note, why = self._pose_who(st, pose_rec, pose, cast, spec)
            if why: ref_why.append(why)
            if pose_rec['short_by'] > 0:
                d['assumptions'] = list(d['assumptions']) + [f"Your movement video is {pose_rec['short_by'] / 24:.1f} s shorter than the take: its last pose is held for the rest."]
        prompt = self._compose(st, spec, cast, loc, refs, continuity, lessons, videos, bg, length, loras, extend=extend, audios=audios,
                               start=bool(start_frame) and not extend, end=bool(end_frame))
        prompt = self._spec_notes(prompt, spec)                     # 📐 V0.9.69 who is who in the pinned picture; fixed sides
        frame_note = self.frame_people_note(st, cast, (sf_pic or start_frame) if (start_frame and not extend) else None, ef_pic or end_frame) if (start_frame or end_frame) else ''
        prompt = self._with_note(prompt, frame_note)                # V0.9.27: someone not in your start/end picture comes in / leaves
        prompt = self._with_note(prompt, pose_note)           # V0.9.30: who follows the movement, who moves on their own
        if pose_rec: prompt = self._no_lines(prompt)         # 🎭 the stick figures steer movement; they are never drawn into the picture
        if official_format(st): d['assumptions'] = list(d['assumptions']) + dialogue_budget(spec.get('dialogue', [])[-3:], length / FMT['fps'])
        no_look = [st['characters'][c]['name'] for c in cast if not (st['characters'][c].get('description') or '').strip()]
        if no_look:                                          # V0.9.23: the look words come from the card, the same in every shot
            d['assumptions'] = list(d['assumptions']) + [f"TIP: {' and '.join(no_look)} {'has' if len(no_look) == 1 else 'have'} no look on the card, so H3 only has the photos "
                                                         "for clothes and accessories. ✎ Edit → 📝 Describe from photos writes it once; every shot then uses the same words."]
        for c in cast:                                       # 'Old man' saved as she/her: every "she" in a direction becomes ambiguous
            C = st['characters'][c]; g = guess_pronoun(C['name'], ' '.join(C.get('descriptors') or []), C.get('description'))
            if g and C.get('pronoun') in ('she', 'he') and g != C['pronoun']:
                d['assumptions'] = list(d['assumptions']) + [f"CHECK: {C['name']} is set to {C['pronoun']}/{'her' if C['pronoun'] == 'she' else 'him'}, "
                                                             f"but the name says {g}. Fix it with ✎ on their card, or \"{C['pronoun']}\" in your directions may point at the wrong person."]
        enhanced = []
        if official_format(st) and st['settings'].get('enhance', True):
            import studio_enhance_v093 as en
            enhanced = en.enhance(' then '.join(action_without_lines(a) for a in spec.get('actions', [])[-3:]), framing=spec.get('framing'),
                                  move=spec.get('move'), place_known=bool(loc or bg.get('mode') == 'people'), lively=lively(st))['changes']
        if len(prompt) > MAX_PROMPT:
            raise StudioError(f'DIRECTION_TOO_LONG_FOR_ONE_SHOT: with your references this prompt would be {len(prompt)} characters and the Director sends at most {MAX_PROMPT} (H3 takes 7000). '
                              'Shorten it, or use "Plan as a scene" to spread it over several shots')
        if len(text or '') > 2900:                       # V0.9.34 (was 1700): above the 2800 kept for the shot
            d['assumptions'] = list(d['assumptions']) + ['Your direction is very long for one ~5 s shot; the end may have been shortened. '
                                                         'Check "Prompt sent to H3", or use "Plan as a scene" for several shots.']
        takes_before = sum(1 for p in (st['shots'].get(shot) or {'plans': []})['plans'])
        take = takes_before + 1
        seed = self._fresh_seed(st, {'p': st['project']['slug'], 'shot': shot, 'take': take, 'prompt': prompt})
        if _seed is not None: seed = int(_seed)
        prefix = f"Studio_{st['project']['slug']}_S{shot:02d}_T{take:02d}"
        vnames = [st['refs'][r]['input_name'] for r in videos]
        sampling = self._sampling(st)
        graph = build_graph([st['refs'][r]['input_name'] for r in refs], prompt, seed, prefix, video_names=vnames,
                            video_audio=bool(st['settings'].get('voice_refs')) and not audios, loras=[(l['name'], l['strength']) for l in loras], length=length,
                            audio_names=[st['refs'][r]['input_name'] for r in audios], anchor_names=self._anchor_names(st, {'anchors': anchors, 'anchor_at': anchor_at}),
                            pose=pose_rec, **sampling)
        shas = {st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in refs}
        cg.validate_director_graph(graph, shas, require_capture=False, length=length,
                                   videos={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in videos} if videos else None,
                                   loras={l['name']: l['strength'] for l in loras} if loras else None,
                                   audios={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in audios} or None,
                                   anchors={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in anchors} or None,
                                   pose={pose_rec['name']: pose_rec['sha256']} if pose_rec else None)
        wf_name = f'{prefix}.json'
        text_wf = json.dumps(graph, indent=2, ensure_ascii=False) + '\n'
        (self.root / 'plans' / wf_name).write_text(text_wf)
        plan = {'shot': shot, 'take': take, 'mode': mode, 'text': d['text'][:2000], 'spec': spec, 'cast': cast,
                'location': loc, 'background': bg, 'frames': length, 'continuity': continuity, 'continuity_from': self._starts_from(st, shot, start_frame if (start_frame and not extend) else None), 'extend': extend, 'refs': refs, 'videos': videos, 'audios': audios, 'anchors': anchors,
                'start_frame': (start_frame if (sf_pic or start_frame) in anchors and not extend else None), 'end_frame': end_frame or None, 'anchor_at': anchor_at, 'frame_note': frame_note or None, 'pose_note': pose_note or None, 'positions_words': pos_words, 'positions_warnings': pos_warn, 'loras': loras, 'missing_refs_for': missing, 'prompt': prompt, 'seed': seed,
                'prefix': prefix, 'workflow_file': wf_name, 'workflow_sha256': hashlib.sha256(text_wf.encode()).hexdigest(),
                'understood_as': d['reading'], 'assumptions': d['assumptions'], 'refused': d['refused'], 'ref_choice': ref_why,
                'lessons_used': lessons, 'enhanced': enhanced, **({'pose': pose_rec} if pose_rec else {}),
                'video_ref_clip_available': [r for c in cast for r in st['characters'][c]['refs'] if st['refs'][r]['kind'] == 'ref_clip'],
                'generation_authorized': False, **(_extra or {}), **({'scene_context': scene_ctx} if scene_ctx else {}),
                **({'film_join': join, **({'film_later': prev_plan['film_later']} if prev_plan.get('film_later') else {})}
                   if (join and 'film_join' not in (_extra or {}) and prev_plan and shot == prev_plan['shot']) else {}),   # V0.9.60 a new take keeps its 🔗 join
                **({'carry_frame': prev_plan['carry_frame']} if ('carry_frame' not in (_extra or {}) and prev_plan and shot == prev_plan['shot']
                    and prev_plan.get('carry_frame') and start_frame and start_frame == prev_plan.get('start_frame')) else {})}   # ... and its built frame
        plan['sampling'] = sampling
        if _settings: plan['settings_override'] = _settings
        if start_frame and not extend and (st['refs'].get(start_frame) or {}).get('variant') in ('composed', 'redrawn'):
            plan['start_pic'] = st['refs'][start_frame]['input_name']                 # V0.9.60 the page shows the frame H3 starts on
        elif sf_pic and sf_pic != start_frame and not extend:
            plan['start_pic'] = st['refs'][sf_pic]['input_name']                      # 📐 V0.9.69 ... also when it is your picture, fitted
        self._append('SHOT_PLANNED', plan, st['tail'])
        return {'action': 'PLANNED', **plan}

    @staticmethod
    def _sampling(st):
        """V0.9.35: steps and sampler for H3 (the research: 25 steps help motion; the official template uses res_multistep)."""
        return {'steps': int(st['settings'].get('steps', DEFAULT_STEPS)), 'sampler': st['settings'].get('sampler', 'euler')}

    # -- 🎬 make a film (V0.9.36): a brief + a total length -> Qwen plans and writes every shot ---------------------
    # -- 🔒 V0.9.47 stay in the opening frame -----------------------------------------------------------------------
    def frame_plate(self, sf):
        """Your opening picture with the people taken out -> a place ('Opening frame: …') whose one photo is exactly that view
        of the room. Every shot of a 🔒 film sees the room from it, with nobody in it for H3 to land on. Made from YOUR original
        by ordinary image processing (the person mask the cut-outs use, then filling in), never by a generative model. Returns
        the place id; the same opening picture gives the same place again."""
        import cv2, numpy as np
        st = self.state(); sf = self._start_frame_ok(st, sf)
        if not sf: raise StudioError('FILM_LOCK_NEEDS_AN_OPENING_PICTURE: pick the picture the film opens on (🎞) first')
        r = st['refs'][sf]
        for L in st['locations'].values():                      # made before from this picture: the same place
            if L.get('frame_of') == r['sha256'] and not L.get('retired'): return L['id']
        src = self.input_dir / r['input_name']
        if not src.is_file() or not same_file(src, r['sha256']): raise StudioError('START_PICTURE_FILE_MISSING: the opening picture is not in ComfyUI/input any more')
        im = media.load_image(src)
        m = media.person_mask(im)
        hard = (m > 0.35).astype(np.uint8) if m is not None else np.zeros(im.shape[:2], np.uint8)
        frac = float(hard.mean())
        if frac > 0.6: raise StudioError('OPENING_PICTURE_IS_MOSTLY_PEOPLE: too little of the room shows to keep its view; open on a wider picture')
        if frac > 0:
            k = max(9, int(0.03 * max(im.shape[:2])) | 1)
            hard = cv2.dilate(hard, np.ones((k, k), np.uint8))
            im = media.fill_people(im, hard)
        dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True)
        name = f"frameplate_{r['sha256'][:10]}.png"
        if not cv2.imwrite(str(dest / name), im): raise StudioError('COULD_NOT_WRITE_THE_FRAME_PLATE')
        h = media.sha256(dest / name)
        base = 'Opening frame: ' + Path(r['file']).stem[:36]
        lname, k2 = base, 2
        while any(same_name(L['name'], lname) for L in st['locations'].values()): lname = f'{base} ({k2})'; k2 += 1
        lid = 'loc_' + slug(lname)
        while lid in st['locations']: lid += '_x'
        self._append('LOCATION_ADDED', {'id': lid, 'name': lname[:60], 'frame_of': r['sha256'],
                                        'description': 'the room exactly as in your opening picture, seen from the same spot'})
        self._append('REF_ADDED', {'id': f'ref_{h[:10]}_frameplate', 'owner': lid, 'kind': 'location', 'alt': False, 'variant': 'frame_plate',
                                   'file': name, 'sha256': h, 'input_name': f'{self.input_sub}/{name}', 'source_sha256': r['sha256'],
                                   'source_kind': 'image', 'provenance': str(r.get('provenance') or 'USER_ATTESTED_ORIGINAL') + '_PEOPLE_REMOVED',
                                   'angle': 0, 'people_in_plate': 0, 'people_removed': round(frac, 3),
                                   'plate_signature': [round(float(x), 5) for x in media.small_hist(im).flatten()], 'rgb_stats': media.rgb_stats([im])})
        return lid

    def positions_guess(self, items):
        """📍 V0.9.49 for each {text, people, positions?}: where the text puts everyone (the setter starts from it), who the
        text leaves vague (📍?), and, when you set positions, where the text disagrees with them (✍)."""
        st = self.state(); names = {c['name'] for c in st['characters'].values()}
        out = []
        for it in (items or [])[:60]:
            text = str((it or {}).get('text') or '')[:4000]
            people = [n for n in ((it or {}).get('people') or []) if n in names][:2]
            g = pm.from_text(text, people)
            pic = self.dots_from_picture(st, (it or {}).get('picture'), people) if (it or {}).get('picture') else None
            if pic:                                                 # V0.9.57 📍 the picture you open on shows where they are
                for r in g['start']:
                    if r['who'] in pic['at']:
                        r.update(pic['at'][r['who']]); r['from'] = 'picture'
                        if r['who'] in g['vague']: g['vague'].remove(r['who'])
            clash = []
            if (it or {}).get('positions'):
                try: clash = pm.clashes(text, pm.clean(it['positions'], people))
                except pm.PositionsError as e: clash = [str(e)]
            out.append({'positions': {'start': g['start']} if g['start'] else None, 'known': g['known'], 'vague': g['vague'], 'clashes': clash,
                        **({'picture': pic} if pic else {})})
        return out

    # ---- 📐 V0.9.69 the picture a shot opens / ends on, fitted to H3's frame and read -----------------------------------
    def _fit_info(self, st, rid, im=None):
        """How one of your pictures fits H3's 9:16 frame (the crop around its people), cached by the picture's hash."""
        R = st['refs'][rid]
        f = self.root / 'analysis' / f"fit_{R['sha256'][:20]}_{fr.W}x{fr.H}_2.json"
        if f.is_file():
            try: return json.loads(f.read_text())
            except ValueError: pass
        if im is None: im = media.load_image(self.input_dir / R['input_name'])
        h, w = im.shape[:2]
        boxes = []
        try:                                             # the identity detector first (it finds small and soft faces the quick one misses)
            import studio_identity_v090 as idn
            if idn.available(): boxes = [x['px'][:4] for x in idn.embed(im) if x['h'] > 0.02]
        except Exception: boxes = []
        if not boxes:
            try: boxes = [x['box'] for x in media.faces(im) if x.get('box')]
            except Exception: boxes = []
        info = fr.fit_rect(w, h, boxes)
        info.update(of=[int(w), int(h)], faces=len(boxes), shape=fr.shape_words(w, h))
        try:
            f.parent.mkdir(parents=True, exist_ok=True); tmp = f.with_suffix('.partial'); tmp.write_text(json.dumps(info)); os.replace(tmp, f)
        except OSError: pass
        return info

    def fit_picture(self, st, rid):
        """📐 The file H3 is given for a pinned picture: your picture cropped to 9:16 around its people and sized to the frame
        (a crop of YOUR original, made once; nothing is drawn). A picture that already is 9:16 is used as it is.
        -> (reference id to pin, info). May add a reference: read the state again after it."""
        import cv2
        R = st['refs'].get(rid)
        if not R or R.get('variant') in ('framed',) or Path(R['input_name']).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'): return rid, None
        im = media.load_image(self.input_dir / R['input_name'])
        info = self._fit_info(st, rid, im)
        if info.get('as_is'): return rid, info
        key = hashlib.sha256(f"{R['sha256']}|{info['rect']}|{fr.W}x{fr.H}".encode()).hexdigest()[:12]
        fid = f'ref_fit_{key}'
        if fid in st['refs'] and (self.input_dir / st['refs'][fid]['input_name']).is_file(): return fid, info
        dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True); name = f'frame_{key}.png'
        if not cv2.imwrite(str(dest / name), fr.fit_image(im, info['rect'])): return rid, info
        self._append('REF_ADDED', {'id': fid, 'owner': None, 'kind': 'start_fit', 'alt': False, 'variant': 'framed', 'framed_of': rid, 'file': name,
                                   'sha256': media.sha256(dest / name), 'input_name': f'{self.input_sub}/{name}', 'source_sha256': R['sha256'],
                                   'source_kind': 'image', 'fit': {'rect': info['rect'], 'of': info['of']},
                                   'provenance': str(R.get('provenance') or 'USER_ATTESTED_ORIGINAL') + '_CROPPED_TO_THE_FRAME'})
        return fid, info

    def _pinned_file(self, st, rid):
        """The file a pinned picture goes to H3 as (your picture fitted to the frame, made if needed), for anything that must
        look at what H3 gets."""
        r = st['refs'][rid]
        if st['settings'].get('fit_pictures', True):
            try:
                fid, _ = self.fit_picture(st, rid)
                if fid != rid: return self.input_dir / self.input_sub / f"frame_{fid[len('ref_fit_'):]}.png"
            except Exception: pass
        return self.input_dir / r['input_name']

    def picture_layout(self, st, rid, names):
        """📐 Where each person of the shot is in a pinned picture (the file H3 gets): [{'who', 'x', 'y'}] left to right, in the
        📍 dots' terms (x across the frame, y = where their feet are, from the face's size). Only clear matches."""
        R = st['refs'].get(rid)
        if not R or not names: return None
        cache = getattr(self, '_layout_cache', None)
        if cache is None: cache = self._layout_cache = {}
        key = (R['sha256'], tuple(names))
        if key not in cache:
            try: got = self.dots_from_picture(st, media.load_image(self.input_dir / R['input_name']), list(names))
            except Exception: got = None
            cache[key] = None if not got else {'rows': sorted(({'who': n, **v} for n, v in got['at'].items()), key=lambda r: r['x']), 'missing': got['missing']}
        return cache[key]

    def _fit_note(self, st, rid, info, cast, what='opening'):
        """The plan card's line about a fitted picture (and who the crop leaves outside the frame)."""
        if not info or info.get('as_is'): return None
        w, h = info['of']; x0, y0, cw, ch = info['rect']
        kept = f"{100 * cw / w:.0f}% of its width" if cw < w else f"{100 * ch / h:.0f}% of its height"
        t = (f"📐 your {what} picture is {info['shape']} ({w}×{h}): H3's frame is 9:16, so the Director crops it around the people in it "
             f"({kept} kept) and pins that; your original is untouched")
        if info.get('outside'):
            t += (f". ⚠ {len(info['outside'])} of the {info['faces']} people in it don't fit in a 9:16 frame together: whoever is outside "
                  "comes into the frame during the shot. A picture with everyone closer together (or an upright one) opens the shot with everyone in it")
        if info.get('scale', 1) > 2.2: t += f". ⚠ it is small for the frame (enlarged {info['scale']:.1f}×): the first frame will be soft"
        return t

    ENTERS = re.compile(r'\b(?:enters?|entering|walks? in\b|walks? into|comes? in\b|comes? into|coming in|arrives?|appears?|steps? in\b|steps? into|'
                        r'joins?|returns?|comes? back|runs? in\b|runs? into|bursts? in|off[- ]?screen|into (?:the )?(?:frame|room|shot)|through the door)', re.I)
    def _fixed_sides(self, st, shot, cast, text, join):
        """📍 V0.9.69 a two-person shot whose words don't say who is on which side (and nothing else fixes it: no dots, no
        picture, no shot before it in this scene): the Director fixes the sides, so every take of this prompt puts them the same
        way. What the text does say is kept; otherwise the first-named person is on the left. -> [left name, right name] or None."""
        if not st['settings'].get('fix_sides', True) or len(cast) != 2 or any(c not in st['characters'] for c in cast): return None
        if self.ENTERS.search(re.sub(r'"[^"]*"', ' ', str(text or ''))): return None            # someone isn't there at the start
        story = self.story(st, before=shot); prev = story[-1] if story else None
        if prev and join != 'skip' and self._same_scene(st, prev, shot): return None         # the shot before fixes it (measured where it cuts)
        names = [st['characters'][c]['name'] for c in cast]
        g = pm.from_text(text, names)
        if all('side' in g['known'].get(n, []) for n in names): return None                  # the text says it itself
        rows = sorted(g['start'], key=lambda r: r['x'])
        return [rows[0]['who'], rows[1]['who']] if rows[0]['x'] < rows[1]['x'] else None

    def _spec_notes(self, prompt, spec):
        """📐 V0.9.69 what the plan measured or fixed about where people are, put into the prompt (every place it is composed)."""
        if spec.get('picture_layout'): prompt = self._bind_frame(prompt, spec['picture_layout'], 'first')
        if spec.get('end_layout'): prompt = self._bind_frame(prompt, spec['end_layout'], 'last')
        if spec.get('start_sides'):
            def label(n):
                m = re.search(r'(<Subject \d+>) \(' + re.escape(n) + r'\)', prompt)
                return f'{m.group(1)} ({n})' if m else n
            l, r = spec['start_sides']
            prompt = self._with_note(prompt, f'At the start, {label(l)} is on the left of the frame and {label(r)} is on the right of the frame.')
        return prompt

    @staticmethod
    def _bind_frame(prompt, rows, moment='first'):
        """📐 Tell H3 who is who in the pinned picture (both prompt formats), right where the prompt speaks of that picture."""
        def label(n):
            m = re.search(r'(<Subject \d+>) \(' + re.escape(n) + r'\)', prompt)
            return f'{m.group(1)} ({n})' if m else n
        b = fr.bind_sentence(rows, label, moment)
        if not b: return prompt
        marks = (('moves on from it without a cut. ', 'and moves on from it without a cut.') if moment == 'first' else
                 ('so the video moves naturally towards it. ', 'by then everyone is where that picture shows them.'))
        if marks[0] in prompt: return prompt.replace(marks[0], marks[0] + b + ' ', 1)
        if marks[1] in prompt: return prompt.replace(marks[1], marks[1] + ' ' + b, 1)
        return Studio._with_note(prompt, b)

    def dots_from_picture(self, st, picture, people):
        """📍 V0.9.57 where each person in the shot is in one of YOUR stills (the picture a shot opens on): faces found in it and
        matched to each person's ORIGINAL photos (face recognition, measurement only). -> {'at': {name: {'x', 'y'}}, 'missing':
        [names]} or None. x = the face's place across the frame; y = an estimate of where their feet are (the dot means the feet),
        from the face's height, capped near the bottom. Only a clear match counts; anyone else keeps the guess from the text."""
        import studio_identity_v090 as idn
        if not people or not idn.available(): return None
        if picture is not None and not isinstance(picture, np.ndarray):
            r = next((x for x in self.start_frames(st) if x['id'] == picture), None)
            if not r: return None
            try:
                im = media.load_image(self.input_dir / r['input_name'])
                if st['settings'].get('fit_pictures', True):           # 📐 V0.9.69 measured in the frame H3 gets, not the whole photo
                    info = self._fit_info(st, picture, im)
                    if not info.get('as_is'): im = fr.fit_image(im, info['rect'])
            except Exception: return None
        else: im = picture                                       # V0.9.60 an image (a frame the Director built)
        by_name = {c['name']: cid for cid, c in st['characters'].items() if not c.get('retired')}
        embs = {n: self.person_embedding(st, by_name[n]) for n in people if n in by_name}
        embs = {n: e for n, e in embs.items() if e is not None}
        if not embs: return None
        faces = [f for f in idn.embed(im) if f['h'] > 0.02]
        at = {}
        for n, (sc, f, margin) in idn.assign(faces, embs).items():
            if sc >= idn.MATCH and margin >= idn.MARGIN:
                at[n] = {'x': round(min(1.0, max(0.0, f['cx'])), 3), 'y': round(min(0.95, max(0.15, f['cy'] + 6.5 * f['h'])), 3)}
        return {'at': at, 'missing': [n for n in people if n not in at]}

    POSE_OF = {'lying_back': 'lying', 'lying_front': 'lying', 'lying_side': 'lying', 'sitting': 'sitting', 'kneeling': 'sitting', 'crouching': 'sitting'}
    def cut_dots(self, st, shot):
        """🔗 V0.9.60 where everyone is WHERE THE MOVIE CUTS shot `shot` (its take in the movie): faces in the last ~0.7 s matched to
        each person's ORIGINAL photos (measurement only). -> ({'who': {'x', 'y'}}, (shot, take)) with every recognised person, or
        ({}, None). x = across the frame, y = where their feet are, estimated from the face size (the dots' meaning)."""
        import cv2, studio_identity_v090 as idn
        if not idn.available(): return {}, None
        c = next((x for x in self.timeline(st) if x['shot'] == shot), None)
        if not c: return {}, None
        t = next((x for x in st['shots'][shot]['takes'] if x['take'] == c['take']), None)
        p = next((q for q in st['shots'][shot]['plans'] if q['take'] == c['take']), None)
        if not t or not p or not t.get('analysis'): return {}, None
        embs = {st['characters'][x]['name']: self.person_embedding(st, x) for x in p.get('cast', []) if x in st['characters']}
        embs = {n: e for n, e in embs.items() if e is not None}
        if not embs: return {}, None
        seen = {n: [] for n in embs}; cap = cv2.VideoCapture(str(self.take_path(t)))
        try:
            for i in (int(c['out']) - 1 - 4 * k for k in range(5)):
                if i < 0: continue
                cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, im = cap.read()
                if not ok: continue
                for n, (sc, f, margin) in idn.assign(idn.embed(im), embs).items():
                    if sc >= idn.MATCH and margin >= idn.MARGIN: seen[n].append((f['cx'], f['cy'], f['h'], self._yaw(f)))
        finally: cap.release()
        at = {}
        for n, v in seen.items():
            if len(v) < 2: continue                                # only a clear answer: seen in at least 2 of the 5 frames
            x, y, h, yaw = (float(np.median([a[k] for a in v])) for k in range(4))
            at[n] = {'x': round(min(1.0, max(0.0, x)), 3), 'y': round(min(0.95, max(0.15, y + 6.5 * h)), 3),
                     'fx': round(x, 4), 'fy': round(y, 4), 'fh': round(h, 4), 'yaw': round(yaw, 3)}   # their face there: size and which way it turns
        return at, (shot, c['take'])

    @staticmethod
    def _yaw(f):
        """V0.9.60 which way a face turns, from its landmarks: nose left or right of the middle of the eyes, in eye widths
        (about -0.5 .. 0.5; 0 = straight at the camera; + = the nose sits right of the eyes' middle, in the picture)."""
        try:
            px = f['px']; rx, lx, nx = px[4], px[6], px[8]
            return max(-1.0, min(1.0, (nx - (rx + lx) / 2) / max(1.0, abs(lx - rx))))
        except (KeyError, IndexError, TypeError): return 0.0

    def _photo_yaw(self, R, r):
        """Which way the face turns in one of YOUR photos (cached on the reference; None when it can't be told)."""
        import studio_identity_v090 as idn
        key = ('yaw', R[r]['sha256']); c = getattr(self, '_yaw_cache', None)
        if c is None: c = self._yaw_cache = {}
        if key not in c:
            try: fs = idn.embed(media.load_image(self.input_dir / R[r]['input_name'])) if idn.available() else []
            except Exception: fs = []
            c[key] = self._yaw(fs[0]) if len(fs) == 1 else None
        return c[key]

    def carry_frame(self, shot, take):
        """🔗 V0.9.60 a 🔗 carries-on film shot opens on a frame BUILT from where the shot before ends: the room and each person's
        cut-out from YOUR photos, placed where they were when the movie cuts the shot before (measured, cut_dots). H3 starts on
        it, so the shot reliably opens where the last one stopped; nothing generated goes to H3 (Clean-Chain). Only when it can
        be told: everyone in this shot recognised at the cut, a room photo, a photo of each person in that posture. Otherwise
        the shot goes as planned and the reason is noted. Setting 'carry_frame' (on). -> the new take, or None."""
        st = self.state()
        if not st['settings'].get('carry_frame', True): return None
        try: p = self.plan_record(st, shot, take)
        except StudioError: return None
        cf = p.get('carry_frame')
        pinned = self._pinned_picture(p)                                  # 🎞 V0.9.61 'every shot opens on the picture' gives way here
        if not p.get('film') or p.get('film_join') != 'carry' or (p.get('start_frame') and not cf and not pinned) or p.get('extend') or p.get('mode') in ('edited', 'source'): return None
        if not p.get('workflow_file'): return None       # 🔗 V0.9.61 starting dots planned ahead give way to where everyone really is (✕ Don't use it: back to them)
        if any(x['shot'] == shot and x['take'] == take for x in st['submissions']): return None
        story = self.story(st, before=shot)
        if not story or story[-1].get('take') is None: return None       # the shot before isn't back yet
        prev_n = story[-1]['shot']
        if cf and list(cf.get('from') or []) == [prev_n, story[-1]['take']]: return None   # built from the take in the movie now
        if cf or pinned: p = {**p, 'start_frame': None}                  # you kept another take of the shot before: build it again
        key = [prev_n, story[-1]['take']]                                  # tried once per take and per take of the shot before
        if any(e['kind'] == 'CARRY_FRAME' and e['payload'].get('shot') == shot and e['payload'].get('take') == take and e['payload'].get('prev') == key
               for e in st['events']): return None
        names = [st['characters'][c]['name'] for c in p.get('cast', []) if c in st['characters']]
        def skip(why):
            self._append('CARRY_FRAME', {'shot': shot, 'take': take, 'new_take': None, 'prev': key, 'why': why}); return None
        if not names: return None
        at, src = self.cut_dots(st, prev_n)
        miss = [n for n in names if n not in at]
        if miss: return skip(f"🔗 couldn't tell where {' and '.join(miss)} {'are' if len(miss) > 1 else 'is'} when shot {prev_n} cuts: "
                             "H3 makes this shot's first frame itself")
        prev_plan = next((q for q in st['shots'][prev_n]['plans'] if q['take'] == src[1]), {})
        ends = {r['who']: r for r in ((prev_plan.get('spec') or {}).get('positions') or {}).get('end') or []}
        guess = {r['who']: r for r in pm.from_text(p.get('film_direction') or p.get('text') or '', names)['start']}
        rows = [{'who': n, **at[n], 'posture': (ends.get(n) or guess.get(n) or {}).get('posture', 'standing'),
                 'facing': (ends.get(n) or guess.get(n) or {}).get('facing', '')} for n in names]
        spec = p.get('spec') or {}
        # V0.9.60 build it first and look at it: everyone recognisable in it, in the same left-to-right order as at the cut
        try:
            hid = {c for c in p.get('cast', []) if c in st['characters'] and st['characters'][c]['name'] in (spec.get('hide_faces') or [])}
            rid = self.compose_first_frame(st, p.get('cast', []), p.get('location'), {'start': rows}, hidden=hid)
            seen = (self.dots_from_picture(self.state(), media.load_image(self.input_dir / self.state()['refs'][rid]['input_name']), names) or {}).get('at') or {}
        except StudioError as e:
            return skip("🔗 no built first frame: " + (str(e).split(": ", 1)[-1] if ": " in str(e) else str(e))[:200])
        hidden_names = set(spec.get('hide_faces') or [])                # 🙈 a hidden face can't be recognised: not checked
        want = [r['who'] for r in sorted(rows, key=lambda r: r['x']) if r['who'] not in hidden_names]
        lost = [n for n in want if n not in seen]
        if lost: return skip(f"🔗 the built frame didn't come out right ({' and '.join(lost)} not recognisable in it): H3 makes this shot's first frame itself")
        if [n for n in sorted(seen, key=lambda n: seen[n]['x']) if n in want] != want:
            return skip("🔗 the built frame didn't come out right (people on the wrong sides): H3 makes this shot's first frame itself")
        extra = {k: p[k] for k in ('film', 'film_shot', 'film_title', 'film_framing', 'film_action_photo', 'film_direction', 'scene_context',
                                     'continued_from', 'written_by', 'film_angle', 'film_lock', 'film_camera', 'film_join', 'film_later') if p.get(k) is not None}
        try:
            q = self.plan(p['text'], mode='revise', target_shot=shot, people=names, background=p.get('location') or None,
                          duration=spec.get('duration'), pose='none', _seed=p.get('seed'),
                          positions={'start': rows, **({'end': (spec.get('positions') or {})['end']} if (spec.get('positions') or {}).get('end') else {})},   # V0.9.61 your end dots stay
                          hide_faces=spec.get('hide_faces') or [], compose_start=True,
                          _extra={**extra, 'carry_frame': {'from': list(src), 'dots': rows}})
        except StudioError as e:
            return skip("🔗 no built first frame: " + (str(e).split(": ", 1)[-1] if ": " in str(e) else str(e))[:200])
        if q.get('action') != 'PLANNED' or q.get('shot') != shot: return skip('🔗 no built first frame: the Director could not plan it')
        self._append('CARRY_FRAME', {'shot': shot, 'take': take, 'new_take': q['take'], 'from': list(src), 'prev': key,
                                     'why': f"🔗 opens where shot {prev_n} ends: take {q['take']} starts on a frame built from your photos "
                                            f"(take {take} stays on the card)"})
        return q['take']

    def _built_origin(self, st, shot, take):
        """A take that opens on a built (or redrawn) frame -> (the take it was built for, [the built takes on the way])."""
        seen, t = [], take
        while True:
            q = next((x for x in st['shots'].get(shot, {'plans': []})['plans'] if x['take'] == t), None)
            if not q or q.get('built_for') is None or t in seen: return t, seen
            seen.append(t); t = q['built_for']

    def _frame_needs_ok(self, shot, take):
        try: p = self.plan_record(self.state(), shot, take)
        except StudioError: return False
        return bool(p.get('carry_frame') and p.get('start_pic') and p.get('frame_ok') is None)

    def _pinned_picture(self, p):
        """🎞 V0.9.61 this carry-on shot opens on the film's picture only because '🎞 every shot opens on the picture' is ticked, or
        it is a 🎥 camera move in a 🔒 stay-in-the-opening-frame film (not a
        picture you picked for this one shot): where everyone really is when the shot before cuts matters more, so the built frame
        wins; ✕ Don't use it goes back to the picture."""
        if not p.get('start_frame') or p.get('carry_frame') or p.get('film_join') != 'carry': return False
        try: f = self.load_film(p.get('film'))
        except StudioError: return False
        if (self.state()['refs'].get(p['start_frame']) or {}).get('variant') == 'composed': return True   # 🧩 built ahead from planned dots
        first = ((f.get('shots') or [{}])[0] or {}).get('start_frame')
        return bool((f.get('pin_all') or (f.get('lock_frame') and p.get('film_camera'))) and first and p['start_frame'] == first)   # 🔒 a camera move starts on it too

    def opening_frame(self, shot):
        """🔗 V0.9.61 build the opening frame of a carry-on shot WITHOUT sending it, so you can look first (once the shot before
        is back). -> {'take', 'start_pic'} or {'take': None, 'why'}. Nothing is generated; ▶ Generate (or the in-order run) sends it."""
        st = self.state()
        if shot not in st['shots']: raise StudioError('NO_SUCH_SHOT')
        sent = {(x['shot'], x['take']) for x in st['submissions']}
        open_ = [q for q in st['shots'][shot]['plans'] if q.get('workflow_file') and (shot, q['take']) not in sent
                 and (shot, q['take']) not in st.get('frame_no', set())]
        if not open_: raise StudioError('NOTHING_TO_SHOW: every take of this shot has been sent; make a new take first')
        p = max(open_, key=lambda q: q['take'])
        if p.get('film_join') != 'carry': raise StudioError('NOT_A_CARRY_ON_SHOT: only a 🔗 carries-on film shot opens on a built frame')
        if not st['settings'].get('carry_frame', True): raise StudioError('BUILT_FRAMES_ARE_OFF: tick "🔗 Carry-on shots start exactly where the last one ended"')
        story = self.story(st, before=shot)
        if not story or story[-1].get('take') is None:
            raise StudioError(f'NOT_YET: the opening frame is built from where shot {story[-1]["shot"] if story else shot - 1} ends, so it has to be back first')
        nt = self.carry_frame(shot, p['take'])
        p2 = self.plan_record(self.state(), shot, nt or p['take'])
        if p2.get('carry_frame') and p2.get('start_pic'): return {'take': p2['take'], 'start_pic': p2['start_pic'], 'new': bool(nt)}
        why = next((e['payload'].get('why') for e in reversed(self.state()['events']) if e['kind'] == 'CARRY_FRAME' and e['payload'].get('shot') == shot
                    and e['payload'].get('take') == p['take'] and not e['payload'].get('new_take')), None)
        if not why and p.get('built_for') is None and any((shot, q['take']) in st.get('frame_no', set()) for q in st['shots'][shot]['plans']):
            why = '🔗 you chose not to use the built frame for this take; ✎ Edit or 🎲 a new take builds it again'
        if not why and p.get('start_frame') and not self._pinned_picture(p):
            why = '🔗 this shot opens on the picture you picked for it (🎞), so no frame is built; pick "carries on" instead of a picture to build one'
        return {'take': None, 'why': why or "🔗 no built frame for this take: H3 makes this shot's first frame itself"}

    def frame_choice(self, shot, take, use):
        """🔗 V0.9.61 you looked at a built opening frame: use it (True) or not (False: the take it was built for goes as planned)."""
        st = self.state(); p = self.plan_record(st, shot, take)
        if not p.get('carry_frame') or not p.get('start_pic'): raise StudioError('NOT_A_BUILT_FRAME')
        if any(x['shot'] == shot and x['take'] == take for x in st['submissions']): raise StudioError('ALREADY_SENT: this take has gone to ComfyUI')
        origin, built = self._built_origin(st, shot, take)
        self._append('FRAME_OK' if use else 'FRAME_NO', {'shot': shot, 'take': take, 'back_to': origin, 'built': built or [take]})
        return {'take': take if use else origin}

    def carry_redraw(self, shot, take, url, opener=None, now=None):
        """🧪 V0.9.60 experiment (setting 'carry_redraw', off): the built carry-on frame of this take, redrawn by Qwen-Image-Edit in
        YOUR ComfyUI so everyone takes the pose they had where the shot before cuts (studio_redraw_v0960). Sent once, picked up
        when ComfyUI saved it, then the shot is planned again on it (a new take, same seed). Anything that goes wrong (not ready,
        no picture back in time) leaves the shot on the built frame, and the run says why.
        -> 'waiting_for_redraw', the new take, or None (go on as it is)."""
        import time as _t, cv2, json as _j, studio_redraw_v0960 as rd
        from urllib.request import urlopen, Request
        st = self.state()
        if not st['settings'].get('carry_redraw', False): return None
        try: p = self.plan_record(st, shot, take)
        except StudioError: return None
        cf = p.get('carry_frame') or {}
        if not cf or cf.get('redrawn') or not p.get('start_frame') or (st['refs'].get(p['start_frame']) or {}).get('variant') != 'composed': return None
        if any(x['shot'] == shot and x['take'] == take for x in st['submissions']): return None
        rec = (st.get('redraws') or {}).get(f'{shot}:{take}')
        def done(why, new_take=None):
            self._append('REDRAW_DONE', {'shot': shot, 'take': take, 'new_take': new_take, 'why': why}); return new_take
        if rec and rec.get('state') == 'done': return None
        op = opener or urlopen
        if not rec:
            def fetch(node):
                try:
                    with op(url.rstrip('/') + '/object_info/' + node, timeout=15) as r: return _j.load(r).get(node)
                except Exception: return None
            ready = rd.readiness(fetch)
            if not ready['ready']:
                return done('🧪 no redraw (the shot goes on the built frame): ' + '; '.join(ready['problems'][:2] + [f'missing {x}' for x in ready['missing_files'][:3]])[:300])
            prev_n, prev_t = cf['from']
            c = next((x for x in self.timeline(st) if x['shot'] == prev_n and x['take'] == prev_t), None)
            t0 = next((x for x in st['shots'][prev_n]['takes'] if x['take'] == prev_t), None)
            if not c or not t0: return done(f'🧪 no redraw: shot {prev_n} take {prev_t} is not in the movie any more')
            cap = cv2.VideoCapture(str(self.take_path(t0)))
            try:
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(c['out']) - 1)); ok, im = cap.read()
            finally: cap.release()
            if not ok: return done(f'🧪 no redraw: could not read where shot {prev_n} cuts')
            sub = self.input_dir / self.input_sub / 'pose_only'; sub.mkdir(parents=True, exist_ok=True)
            tmp = sub / f'cut_{prev_n}_{prev_t}_{int(c["out"])}.png'
            if not cv2.imwrite(str(tmp), im): return done('🧪 no redraw: could not write the cut frame')
            h = media.sha256(tmp)                    # a frame of a TAKE: marked generated, so it can never be added as yours
            self._append('GENERATED_FILE', {'sha256': h, 'file': f'{self.input_sub}/pose_only/{tmp.name}', 'why': 'cut frame for stick figures only'})
            names = [r['who'] for r in sorted(cf.get('dots') or [], key=lambda r: r['x'])]
            prefix = f'Studio_{self.slug}_S{shot:02d}_T{take:02d}_redraw'
            graph = rd.api(st['refs'][p['start_frame']]['input_name'], f'{self.input_sub}/pose_only/{tmp.name}', rd.prompt_text(names),
                           seed=p.get('seed') or 0, prefix=prefix)
            body = _j.dumps({'prompt': graph, 'client_id': CLIENT_ID}).encode()
            try:
                with op(Request(url.rstrip('/') + '/prompt', data=body, headers={'Content-Type': 'application/json'}, method='POST'), timeout=30) as r:
                    resp = _j.load(r)
            except Exception as e: return done(f'🧪 no redraw: ComfyUI refused it ({str(e)[:160]})')
            if resp.get('node_errors'): return done('🧪 no redraw: ComfyUI rejected it: ' + _j.dumps(resp['node_errors'])[:200])
            self._append('REDRAW_SENT', {'shot': shot, 'take': take, 'prefix': prefix, 'prompt_id': resp.get('prompt_id'), 'at': round(now or _t.time())})
            return 'waiting_for_redraw'
        if rec.get('state') == 'sent':
            files = sorted(self.output_dir.glob(rec['prefix'] + '_*.png'))
            if not files:
                if (now or _t.time()) - float(rec.get('at') or 0) > rd.WAIT_S: return done('🧪 the redraw did not come back in time: the shot goes on the built frame')
                return 'waiting_for_redraw'
            f = files[-1]; im = media.load_image(f)
            dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(f.read_bytes()).hexdigest()[:12]; name = f'redrawn_{key}.png'
            if not cv2.imwrite(str(dest / name), im): return done('🧪 no redraw: could not copy the redrawn picture')
            h = media.sha256(dest / name); rid = f'ref_redrawn_{key}'
            self._append('GENERATED_FILE', {'sha256': h, 'file': f'{self.input_sub}/{name}', 'why': 'AI-redrawn first frame (experiment)'})
            if rid not in st['refs']:
                self._append('REF_ADDED', {'id': rid, 'owner': p.get('location'), 'kind': 'start_composite', 'alt': False, 'variant': 'redrawn', 'file': name,
                                           'sha256': h, 'input_name': f'{self.input_sub}/{name}', 'source_sha256': st['refs'][p['start_frame']]['sha256'],
                                           'source_kind': 'image', 'provenance': 'AI_REDRAWN_EXPERIMENT_FROM_YOUR_BUILT_FRAME_AND_STICK_FIGURES'})
            # measured first: everyone still recognisable as THEMSELVES (their original photos), on the same sides, about where they were
            names_lr = [r['who'] for r in sorted(cf.get('dots') or [], key=lambda r: r['x'])]
            hidden_names = set((p.get('spec') or {}).get('hide_faces') or [])
            want = [n for n in names_lr if n not in hidden_names]
            seen = (self.dots_from_picture(self.state(), im, want) or {}).get('at') or {}
            lost = [n for n in want if n not in seen]
            if lost: return done(f"🧪 the redraw changed {' and '.join(lost)} (not recognisable as {'them' if len(lost) > 1 else 'themselves'} any more): the shot goes on the built frame")
            if [n for n in sorted(seen, key=lambda n: seen[n]['x'])] != want: return done('🧪 the redraw moved people to the wrong sides: the shot goes on the built frame')
            dots = {r['who']: r for r in cf.get('dots') or []}
            moved = [n for n in want if abs(seen[n]['x'] - dots[n]['x']) > 0.12]
            if moved: return done(f"🧪 the redraw moved {' and '.join(moved)} away from where shot {cf['from'][0]} ended: the shot goes on the built frame")
            look = bool(os.environ.get('DIRECTOR_CHECK_FAKE'))
            if not look:
                try: look = bool(self.qwen_ready()['ready'])
                except Exception: look = False
            self._append('REDRAW_GOT', {'shot': shot, 'take': take, 'prefix': rec['prefix'], 'rid': rid, 'look': look, 'at': round(now or _t.time())})
            if look: return 'waiting_for_redraw_look'          # 👁 Qwen looks at it next (in the background, when the GPU is free)
            rec = {**rec, 'rid': rid, 'state': 'looked', 'ok': True}
        elif rec.get('state') == 'got':
            if (now or _t.time()) - float(rec.get('at') or 0) > rd.LOOK_WAIT_S: return done("🧪 Qwen couldn't look at the redraw in time: the shot goes on the built frame")
            return 'waiting_for_redraw_look'
        if not rec.get('ok'): return done('🧪 Qwen saw a problem in the redraw (' + str(rec.get('why') or 'see the take card')[:200] + '): the shot goes on the built frame')
        rid = rec['rid']; spec = p.get('spec') or {}
        names = [st['characters'][c]['name'] for c in p.get('cast', []) if c in st['characters']]
        extra = {k: p[k] for k in ('film', 'film_shot', 'film_title', 'film_framing', 'film_action_photo', 'film_direction', 'scene_context',
                                     'continued_from', 'written_by', 'film_angle', 'film_lock', 'film_camera', 'film_join', 'film_later') if p.get(k) is not None}
        try:
            q = self.plan(p['text'], mode='revise', target_shot=shot, people=names, background=p.get('location') or None, duration=spec.get('duration'),
                          pose='none', _seed=p.get('seed'), start_frame=rid, positions=spec.get('positions'), hide_faces=spec.get('hide_faces') or [],
                          _extra={**extra, 'carry_frame': {**cf, 'redrawn': rid}})
        except StudioError as e: return done('🧪 no redraw: ' + str(e)[:200])
        if q.get('action') != 'PLANNED' or q.get('shot') != shot: return done('🧪 no redraw: the Director could not plan it')
        return done(f"🧪 opens on the built frame redrawn in the poses where shot {cf['from'][0]} ends: take {q['take']} (take {take} on the built frame stays on the card)",
                    q['take'])

    LOOK_POINTS = ['The same people are in picture 2 as in picture 1: nobody added, nobody missing',
                   'Each person in picture 2 has the same face, hair and skin tone as in picture 1',
                   'Each person in picture 2 wears the same clothes and shoes as in picture 1',
                   'Picture 2 shows the same room with the same light as picture 1',
                   'Picture 2 looks like one real photo: hands, arms and legs look right, feet on the floor, nothing melted, doubled or floating']
    LOOK_PROMPT = ("Picture 1 was put together from the director's own photos. Picture 2 is the same picture redrawn so the people take new "
                   "poses. Compare them closely and say for each statement whether it is true.\nBe strict, a wrong \"yes\" is worse than a \"no\".\n"
                   "Statements:\n{statements}\nAnswer with JSON only: {{\"answers\": [{{\"n\": 1, \"seen\": \"yes|no|unclear\", \"evidence\": \"what you see\"}}, ...]}}")

    def redraw_look_job(self, frames_dir=None):
        """👁 V0.9.60 the redraw the in-order run waits on, for Qwen (your Qwen model) to look at -> {'shot', 'take', 'request'} or None."""
        st = self.state()
        for key, rec in (st.get('redraws') or {}).items():
            if rec.get('state') != 'got' or not rec.get('look'): continue
            shot, take = map(int, key.split(':'))
            try: p = self.plan_record(st, shot, take)
            except StudioError: continue
            import studio_qwenmodel_v0916 as qm
            mdl = qm.model_id(st['settings'].get('qwen_model'), 'DIRECTOR_CHECK_MODEL')
            frames = [['PICTURE 1 (put together from the original photos)', str(self.input_dir / st['refs'][p['start_frame']]['input_name'])],
                      ['PICTURE 2 (redrawn)', str(self.input_dir / st['refs'][rec['rid']]['input_name'])]]
            req = {'model_id': mdl, 'frames': frames, 'max_new_tokens': 700,
                   'prompt': self.LOOK_PROMPT.format(statements='\n'.join(f'{i + 1}. {x}' for i, x in enumerate(self.LOOK_POINTS)))}
            return {'key': key, 'shot': shot, 'take': take, 'request': req, 'model': mdl}
        return None

    def record_redraw_look(self, job, reply):
        """Qwen's answer on a redraw -> REDRAW_LOOKED (ok only when it said no 'no'; Qwen is a hint, the measurements came first)."""
        import studio_check_v0915 as ck
        pts = ck.read_reply(reply, self.LOOK_POINTS, need_frame=0)
        bad = [x for x in pts if x['seen'] == 'no']
        why = '; '.join((x['evidence'] or x['point'])[:90] for x in bad) if bad else ''
        self._append('REDRAW_LOOKED', {'shot': job['shot'], 'take': job['take'], 'ok': not bad, 'why': why,
                                       'points': [{k: x[k] for k in ('point', 'seen', 'evidence')} for x in pts]})
        return not bad

    def _cut_off(self, ref):
        """V0.9.62 this full-body photo stops above the person's feet (cached by the photo's hash)."""
        c = self.__dict__.setdefault('_cut_off_cache', {})
        if ref['sha256'] not in c:
            try:
                im = media.load_image(self.input_dir / ref['input_name']); fs = media.faces(im)
                c[ref['sha256']] = bool(media.cut_off_at_bottom(im, fs[0] if len(fs) == 1 else None))
            except Exception: c[ref['sha256']] = False
        return c[ref['sha256']]

    def compose_first_frame(self, st, cast, loc, positions, hidden=()):
        """🧩 V0.9.55 a first frame built from your positions: the room (your opening picture with the people taken out, or your
        place photo) and each person's cut-out from one of YOUR photos in that posture (standing / 🪑 sitting / 🛏 lying), set
        where you put their dot and sized for the distance. Only your originals go in (processed copies: Clean-Chain). Cached
        by what went in. -> the picture's reference id (variant 'composed'), usable only as a pinned first frame."""
        import cv2
        if not loc or loc not in st['locations']: raise StudioError('COMPOSE_NEEDS_A_ROOM: pick the place (or open the film on your picture with 🔒) so there is a room to put everyone in')
        R = st['refs']; L = st['locations'][loc]
        room = next((r for r in L['refs'] if r in R and R[r].get('variant') == 'frame_plate'), None) or \
               next((r for r in L['refs'] if r in R and R[r]['kind'] == 'location' and not R[r].get('removed') and not R[r].get('matched_from')), None)
        if not room: raise StudioError('COMPOSE_NEEDS_A_ROOM: this place has no photo yet')
        byname = {st['characters'][c]['name']: c for c in cast if c in st['characters']}
        picks, why = [], []
        stand_ins, cut_off = [], []                                          # V0.9.61 no photo in that posture: standing, H3 moves them
        for row in (positions or {}).get('start') or []:
            c = byname.get(row['who'])
            if not c: continue
            want = self.POSE_OF.get(row.get('posture'), 'upright')
            full = lambda w: [r for r in st['characters'][c]['refs'] if r in R and R[r]['kind'] == 'full' and R[r].get('variant', 'raw') == 'raw'
                              and not R[r].get('removed') and (R[r].get('pose') or 'upright') == w]
            cand = full(want)
            if not cand and want != 'upright' and full('upright'):
                stand_ins.append({'who': row['who'], 'posture': row.get('posture')})
                row = {**row, 'posture': 'standing'}; want = 'upright'; cand = full('upright')
            if c in hidden: cand = [r for r in cand if R[r].get('no_face')] or cand
            if cand and st['settings'].get('best_refs', True):              # ⭐ V0.9.67 the built frame shows them in their main look
                try:
                    import studio_bestref_v0965 as br
                    look = set(br.plan_person(R, [r for r in st['characters'][c]['refs'] if r in R], self._refinfo(),
                                              main_source=st['characters'][c].get('main_source'))['outfit_sources'])
                    cand = [r for r in cand if R[r].get('source_sha256') in look or R[r].get('no_face')] or cand
                except Exception: pass
            yaw = row.get('yaw')                                             # 🔗 V0.9.60 the photo turned the way they faced at the cut
            cand.sort(key=lambda r: (0 if R[r].get('feet_in', True) is not False else 1, 0 if c not in hidden or R[r].get('no_face') else 1,
                                     abs((self._photo_yaw(R, r) if yaw is not None else 0) or 0) if yaw is None else abs((self._photo_yaw(R, r) or 0) - yaw)))
            if not cand:
                word = {'lying': '🛏 lying down', 'sitting': '🪑 sitting', 'upright': 'standing, head to toe'}[want]
                said = str(row.get('posture') or 'standing').replace('_', ' ')          # V0.9.61 the posture as set (kneeling uses a 🪑 photo)
                asked = '' if said.split()[0] in word else f" for {row['who']} {said} (the Director builds that from a {word.split(',')[0]} photo)"
                raise StudioError(f"COMPOSE_NO_PHOTO_IN_THAT_POSTURE: there is no full-body photo of {row['who']} {word}{asked}. Add one "
                                  f"(Type: {word.split(',')[0]}) or set {row['who']} in a posture you have a photo of")
            whole = [r for r in cand if not self._cut_off(R[r])] if want == 'upright' else cand   # V0.9.62 a photo that shows their feet first
            if not whole: cut_off.append(row['who'])
            picks.append((row, (whole or cand)[0]))
        if not picks: raise StudioError('COMPOSE_NOBODY_PLACED')
        fit9 = bool(st['settings'].get('fit_pictures', True))               # 📐 V0.9.69 the room in H3's 9:16 frame, so the dots mean the same there
        key = hashlib.sha256(json.dumps([R[room]['sha256'], [[p[0].get(k) for k in ('who', 'x', 'y', 'posture', 'fx', 'fy', 'fh')] + [R[p[1]]['sha256']] for p in picks]]
                                        + (['9x16'] if fit9 else []), sort_keys=True).encode()).hexdigest()[:12]
        rid = f'ref_composed_{key}'
        if not hasattr(self, '_stand_in_of'): self._stand_in_of = {}
        self._stand_in_of[rid] = stand_ins                                     # (per picture: safe with two plans at once)
        if not hasattr(self, '_cut_off_of'): self._cut_off_of = {}
        self._cut_off_of[rid] = cut_off
        if rid in R and (self.input_dir / R[rid]['input_name']).is_file(): return rid
        room_im = media.load_image(self.input_dir / R[room]['input_name'])
        if R[room].get('people_in_plate') and R[room].get('variant') != 'frame_plate':   # V0.9.60 people in your place photo: taken out first
            import numpy as _np                                                      # (the person mask + filling in, as for 🔒; nothing generated)
            m = media.person_mask(room_im); hard = (m > 0.35).astype(_np.uint8) if m is not None else None
            if hard is not None and hard.any():
                if hard.mean() > 0.6: raise StudioError('COMPOSE_ROOM_IS_MOSTLY_PEOPLE: your photo of this place is mostly people; add a photo of the place itself')
                k = max(9, int(0.03 * max(room_im.shape[:2])) | 1)
                room_im = media.fill_people(room_im, cv2.dilate(hard, _np.ones((k, k), _np.uint8)))
        hz = R[room].get('horizon')
        if hz is None and R[room].get('source_sha256'):
            src = next((x for x in R.values() if x.get('sha256') == R[room]['source_sha256'] and x.get('variant', 'raw') == 'raw'), None)
            try: hz = media.standing_horizon(media.load_image(self.input_dir / src['input_name'])) if src else None
            except Exception: hz = None
        h0, w0 = room_im.shape[:2]
        if fit9 and abs((w0 / h0) / (fr.W / fr.H) - 1) > fr.TOL:
            # the room cropped to 9:16 before anyone is placed: the same crop as the picture it was made from (so the frame is
            # the opening picture's frame), else its middle. Without this the node cropped the finished frame and the dots shifted.
            rect = None
            srcid = next((k for k, x in R.items() if x.get('sha256') == R[room].get('source_sha256') and x.get('variant', 'raw') == 'raw'), None)
            try:
                fi = self._fit_info(st, srcid) if srcid else None
                if fi and fi.get('of') == [w0, h0]: rect = fi['rect']
            except Exception: rect = None
            if rect is None: rect = fr.fit_rect(w0, h0, [])['rect']
            x0, y0, cw, ch = rect; room_im = room_im[y0:y0 + ch, x0:x0 + cw]
            if hz is not None and ch != h0: hz = float(min(1.0, max(0.0, (hz * h0 - y0) / ch)))
        people = []
        for row, r in picks:
            im = media.load_image(self.input_dir / R[r]['input_name']); fs = media.faces(im)
            people.append({'who': row['who'], 'image': im, 'face': fs[0] if len(fs) == 1 else None, 'x': row['x'], 'y': row['y'], 'posture': row.get('posture', 'standing'),
                           **({k: row[k] for k in ('fx', 'fy', 'fh')} if row.get('fh') and len(fs) == 1 else {})})
        try: img = media.compose_start(room_im, people, horizon=hz)
        except ValueError as e: raise StudioError(str(e))
        dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True)
        name = f'composed_{key}.png'
        if not cv2.imwrite(str(dest / name), img): raise StudioError('COULD_NOT_WRITE_THE_BUILT_FRAME')
        self._append('REF_ADDED', {'id': rid, 'owner': loc, 'kind': 'start_composite', 'alt': False, 'variant': 'composed', 'file': name,
                                   'sha256': media.sha256(dest / name), 'input_name': f'{self.input_sub}/{name}',
                                   'source_sha256': R[room]['sha256'], 'sources': [R[room]['sha256']] + [R[r]['sha256'] for _, r in picks],
                                   'source_kind': 'image', 'provenance': 'USER_ATTESTED_ORIGINALS_COMPOSED_FROM_YOUR_POSITIONS', 'horizon': hz})
        return rid

    def picture_framing(self, sf, st=None):
        """📐 V0.9.47 how YOUR still is framed, measured (wide / medium / close-up / two-shot, how many people), cached by its hash."""
        st = st or self.state(); r = st['refs'].get(sf)
        if not r: raise StudioError('UNKNOWN_PICTURE')
        cache = getattr(self, '_framing_cache', None)
        if cache is None: cache = self._framing_cache = {}
        if r['sha256'] in cache: return cache[r['sha256']]
        src = self.input_dir / r['input_name']
        if not src.is_file(): raise StudioError('START_PICTURE_FILE_MISSING: the picture is not in ComfyUI/input any more')
        out = media.shot_framing(media.load_image(src))
        if r.get('kind') in ('full', 'location') and out['framing'] == 'medium' and out['people'] <= 1:     # V0.9.48 the photo's own type
            out = {**out, 'framing': 'wide', 'words': media.FRAMING_WORDS['wide'] + (f", {out['people']} person" if out['people'] else '')}
        cache[r['sha256']] = out
        return out

    def lock_angle(self, st, sf, angle=None):
        """The film's one framing: yours if you chose one, else MEASURED from the opening picture (a close-up keeps medium:
        one fixed angle for a whole film is wide, medium or two-shot)."""
        if angle and angle != 'match': return angle
        try: f = self.picture_framing(sf, st)['framing']
        except Exception: f = 'medium'
        return {'close_up': 'medium'}.get(f, f)

    def _start_frame_ok(self, st, sf):
        """V0.9.38 🎞: a picture a film shot may open on: one of YOUR original stills (start_frames), else StudioError."""
        if sf and sf not in {x['id'] for x in self.start_frames(st)}: raise StudioError('START_FRAME_MUST_BE_ONE_OF_YOUR_ORIGINAL_STILLS')
        return sf or None

    def _opening_jpg(self, st, sf, pictures_dir, label):
        """Your start picture as a small JPEG for Qwen ([label, path]), or None."""
        import studio_rewrite_v0913 as rw
        r = st['refs'].get(sf)
        if not r or pictures_dir is None: return None
        d = Path(pictures_dir) / f'open_{sf}'; d.mkdir(parents=True, exist_ok=True)
        out = rw.prepare_pictures([{'label': label, 'path': str(self._pinned_file(st, sf)), 'frame': None}], d)   # 📐 V0.9.69 the fitted frame
        out = [(l, j) for l, j in out if Path(j).is_file()]
        return [str(out[0][0]), str(out[0][1])] if out else None

    def film_request(self, brief, seconds, *, people=None, places=None, pictures_dir=None, opens_on=None, angle=None, lock_frame=False, lead_in=None, pin_all=False,
                     continue_from=None):
        """What the Qwen film worker is asked (see studio_film_v0936): the people and places you picked (default: all), and
        with pictures_dir your ORIGINAL photos of them (never a generated take). opens_on (V0.9.38 🎞): one of your original
        stills the film's first shot opens exactly on; Qwen sees it while planning and writing shot 1."""
        _fresh_rewrite_module()                                      # reloaded in place, so the film module sees it too
        import studio_film_v0936 as fm, studio_qwenmodel_v0916 as qm, studio_rewrite_v0913 as rw
        st = self.state()
        so_far = None; base = None
        if continue_from:                                            # 🎬 V0.9.50 the next part of a film you made: its settings carry on
            base = self.load_film(continue_from)
            if len(base.get('shots') or []) >= fm.MAX_SHOTS: raise StudioError(f'FILM_AT_MOST_{fm.MAX_SHOTS}_SHOTS: start a new film for what comes next')
            first = (base.get('shots') or [{}])[0] or {}
            opens_on = opens_on or (first.get('start_frame') if (base.get('pin_all') or base.get('lock_frame')) else None)
            angle = angle or base.get('angle'); lock_frame = lock_frame or bool(base.get('lock_frame'))
            pin_all = pin_all or bool(base.get('pin_all'))
            if lead_in is None: lead_in = base.get('lead_in')
            if not people: people = sorted({p for s in base['shots'] for p in (s.get('people') or [])}) or None
            so_far = fm.so_far_text(base, self.film_ending(st, base))
        chars = [c for c in st['characters'].values() if not c.get('retired')]
        byname = {c['name']: c for c in chars}; byid = {c['id']: c for c in chars}
        picked = [byname.get(p) or byid.get(p) for p in (people or [])]
        if any(c is None for c in picked): raise StudioError('UNKNOWN_PERSON')
        picked = picked or chars
        locs = [L for L in st['locations'].values() if not L.get('retired')]
        lbyname = {L['name']: L for L in locs}; lbyid = {L['id']: L for L in locs}
        lp = [lbyname.get(p) or lbyid.get(p) for p in (places or [])]
        if any(L is None for L in lp): raise StudioError('UNKNOWN_PLACE')
        lp = lp or locs
        cast = [{'name': c['name'], 'pronoun': c.get('pronoun')} for c in picked]
        pl = [{'name': L['name'], 'description': L.get('description') or ''} for L in lp]
        opens_on = self._start_frame_ok(st, opens_on)
        if angle == 'match' and not opens_on: raise StudioError('FILM_MATCH_NEEDS_AN_OPENING_PICTURE: pick the picture the film opens on (🎞) first')
        if lock_frame and not opens_on: raise StudioError('FILM_LOCK_NEEDS_AN_OPENING_PICTURE: pick the picture the film opens on (🎞) first')
        if pin_all and not opens_on: raise StudioError('FILM_PIN_ALL_NEEDS_AN_OPENING_PICTURE: pick the picture every shot opens on (🎞) first')
        if lock_frame or angle == 'match':                           # 🔒 / 📐 V0.9.47 the one angle measured from the opening picture
            angle = self.lock_angle(st, opens_on, angle)
        pics = []; opening = None
        if pictures_dir is not None:
            R = st['refs']; items = [it for it in self.rewrite_pictures(st, picked) if '(an ORIGINAL photo' in it['label'] and not it['label'].startswith('The place')]
            for L in lp[:3]:
                plate = next((r for r in L['refs'] if r in R and R[r]['kind'] == 'location' and not R[r].get('removed') and not R[r].get('matched_from')), None)
                if plate: items.append({'label': f"The place, {L['name']} (an ORIGINAL photo)", 'path': str(self.input_dir / R[plate]['input_name']), 'frame': None})
            pics = rw.prepare_pictures(items, pictures_dir)
            if opens_on:
                o = self._opening_jpg(st, opens_on, pictures_dir, fm.OPENING_LABEL)
                if o: pics = [tuple(o)] + list(pics)[:rw.MAX_PICTURES - 1]
                opening = self._opening_jpg(st, opens_on, Path(pictures_dir) / 'shot1', 'START PICTURE (an ORIGINAL photo): the new shot opens exactly on this picture')
                if not o: raise StudioError('START_PICTURE_FILE_MISSING: the picture this film opens on is not in ComfyUI/input any more; pick another or re-add it')
        model, thinking = qm.film_model(st['settings'])               # V0.9.39 🧠 Settings > Film writer
        lead = fm.lead_of({'lead_in': lead_in} if lead_in is not None else {})          # ⏱ V0.9.48
        try: angle = fm.angle_ok(angle); outline = fm.outline_request(brief, seconds, cast=cast, places=pl, pictures=pics, model=model, opening=bool(opens_on), angle=angle, lead_in=lead, so_far=so_far)
        except ValueError as e: raise StudioError(str(e))
        yours = self.notes_for_qwen(st)                                # V0.9.61 📝 how you judged earlier takes
        if yours: outline['user'] = outline['user'].rstrip() + '\n\n' + self.notes_text(yours)
        import studio_thinking_v0939 as th
        return {'model_id': model, 'think': th.budgets(thinking), 'outline': outline, 'cast': cast, 'names': [c['name'] for c in cast], 'place_names': [p['name'] for p in pl],
                'total': float(seconds), 'motion': st['settings'].get('motion', 'lively'), 'brief': ' '.join(str(brief).split())[:4000],
                'opens_on': opens_on, 'opening_picture': opening, 'angle': angle, 'lock_frame': bool(lock_frame), 'lead_in': lead,
                'pin_all': bool(pin_all and opens_on), **({'continue_from': base['id']} if base else {})}

    def film_from_reply(self, request, result):
        """Qwen's film (outline + every shot) -> the checked film you see and edit; saved as a draft (nothing is planned)."""
        import studio_film_v0936 as fm, director_intent_v0739 as di
        if result.get('outline_error') and not result.get('shot_replies'):
            raise StudioError('FILM_OUTLINE_UNUSABLE: ' + result['outline_error'] + ' (Qwen\'s plan could not be read; try again or shorten the brief)')
        try:
            outline = fm.check_outline(result.get('outline_reply') or '', names=request['names'], places=request['place_names'],
                                       total=request['total'], forbidden=di.FORBIDDEN_VISUAL_SIMPLE, angle=request.get('angle'))
            film = fm.assemble(outline, result.get('shot_replies') or [], names=request['names'], forbidden=di.FORBIDDEN_VISUAL_SIMPLE)
        except ValueError as e: raise StudioError(str(e) + ' (Qwen\'s film was not usable; try again)')
        film.update(id=hashlib.sha256(json.dumps([request.get('brief'), result.get('outline_reply')]).encode()).hexdigest()[:10],
                    brief=request.get('brief', ''), model=result.get('model_id') or request.get('model_id'), planned={}, angle=request.get('angle'),
                    lock_frame=bool(request.get('lock_frame')), lead_in=fm.lead_of(request),
                    note=f"Written by {str(result.get('model_id') or 'Qwen').split('/')[-1]}: a suggestion. Read and edit any shot, then plan them.")
        if result.get('thinking'):                                   # V0.9.39 🧠
            import studio_thinking_v0939 as th
            said, tw = th.note(result['thinking'])
            if said: film['note'] = film['note'].replace(': a suggestion.', f', which {said}: a suggestion.', 1)
            film['warnings'] = list(film.get('warnings') or []) + tw
            film['thinking'] = [dict(x) for x in result['thinking'] if isinstance(x, dict)][:60]
        film['warnings'] = list(film.get('warnings') or []) + fm.idle_warnings(film)     # V0.9.53 an idle first / last shot: said
        tb = '' if request.get('continue_from') else fm.thin_brief(request.get('brief'), request.get('total'))   # V0.9.54
        if tb: film['warnings'].insert(0, tb)
        for sh in film['shots']: sh.setdefault('start_frame', None)
        if request.get('opens_on') and film['shots']: film['shots'][0]['start_frame'] = request['opens_on']   # 🎞 V0.9.38
        if request.get('pin_all') and request.get('opens_on'):                       # 🎞 V0.9.48 every shot opens on the picture
            for sh in film['shots']: sh['start_frame'] = request['opens_on']
            film['pin_all'] = True
        fm.keep_notes(film, result.get('notes'))                     # V0.9.43 ✋ the notes you typed while Qwen worked
        if result.get('stopped'):                                    # V0.9.37: Qwen stopped part-way; what it wrote is kept
            done = len(result.get('shot_replies') or []); n = len(film['shots'])
            film['warnings'].insert(0, f"Qwen stopped after writing {done} of {n} shots: {result['stopped']}. The rest use the planned beat; "
                                       "press ✍ Qwen rewrites on them when ComfyUI is idle, or edit them yourself.")
        if request.get('continue_from'):                             # 🎬 V0.9.50 the new part goes after the film's shots
            film = self._continue_merge(self.load_film(request['continue_from']), film, request)
        self._save_film(film)
        return film

    @staticmethod
    def _film_sig(film, s):
        """✎ V0.9.50 what a film shot is planned FROM: its words and every setting of it (and the film's camera, lock and lead-in)."""
        keys = ('direction', 'framing', 'people', 'place', 'seconds', 'start_frame', 'positions', 'hide_face', 'join', 'later', 'camera')
        blob = json.dumps([{k: s.get(k) for k in keys}, film.get('angle'), bool(film.get('lock_frame')), film.get('lead_in')], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def _continue_merge(self, base, part, request):
        import studio_film_v0936 as fm
        k = len(base['shots']); new = []
        for i, s in enumerate(part['shots']):
            s = dict(s); s['n'] = k + i + 1; s['part'] = len(base.get('parts') or []) + 2 if base.get('parts') else 2
            if i == 0: s.setdefault('join', 'cut')
            new.append(s)
        merged = json.loads(json.dumps(base))
        if len(merged['shots']) + len(new) > fm.MAX_SHOTS: raise StudioError(f'FILM_AT_MOST_{fm.MAX_SHOTS}_SHOTS: ask for a shorter next part')
        parts = merged.get('parts') or [{'from': 1, 'brief': merged.get('brief', ''), 'title': merged.get('title')}]
        parts.append({'from': k + 1, 'brief': request.get('brief', ''), 'title': part.get('title')})
        merged['shots'] = merged['shots'] + new; merged['parts'] = parts
        merged['target'] = float(merged.get('target') or 0) + float(part.get('target') or request.get('total') or 0)
        merged['warnings'] = list(dict.fromkeys(list(base.get('warnings') or []) + list(part.get('warnings') or [])))
        merged['note'] = (f"Part {len(parts)} added after shot {k} ({len(new)} new shot{'s' if len(new) != 1 else ''}), written by "
                          f"{str(part.get('model') or 'Qwen').split('/')[-1]}: a suggestion. Read and edit them, then plan the remaining shots.")
        if part.get('thinking'): merged['thinking'] = part['thinking']
        if part.get('notes'): merged['notes'] = list(dict.fromkeys(list(base.get('notes') or []) + list(part['notes'])))
        fm.summarize(merged)
        return merged

    def film_rewrite_request(self, film, numbers, pictures_dir=None):
        """V0.9.37 ✍: Qwen writes shots `numbers` of the film again (the ones it didn't get to, or any you want redone).
        Shots already planned are left alone: they are shots in the project now; change those on their own card."""
        _fresh_rewrite_module()
        import studio_film_v0936 as fm, studio_qwenmodel_v0916 as qm
        if not isinstance(film, dict) or not film.get('shots'): raise StudioError('FILM_HAS_NO_SHOTS')
        planned = film.get('planned') or {}
        nums = [int(n) for n in numbers or [] if str(n).isdigit()]
        if any(str(n) in planned for n in nums): raise StudioError('FILM_SHOT_ALREADY_PLANNED: change a planned shot on its own card')
        st = self.state()
        chars = {c['name']: c for c in st['characters'].values() if not c.get('retired')}
        names = sorted({p for s in film['shots'] for p in (s.get('people') or []) if p in chars}) or list(chars)[:1]
        if not names: raise StudioError('FILM_NEEDS_A_PERSON: add at least one person first')
        cast = [{'name': n, 'pronoun': chars[n].get('pronoun')} for n in names]
        model, thinking = qm.film_model(st['settings'])               # V0.9.39 🧠 the film's writer rewrites its shots too
        opens = {}                                                   # 🎞 V0.9.38: a shot that opens on your picture: Qwen sees it
        for n in nums:
            sh = film['shots'][n - 1] if 1 <= n <= len(film['shots']) else {}
            sf = self._start_frame_ok(st, sh.get('start_frame'))
            if sf:
                o = self._opening_jpg(st, sf, Path(pictures_dir) / f'shot{n}', 'START PICTURE (an ORIGINAL photo): the new shot opens exactly on this picture') if pictures_dir is not None else None
                if pictures_dir is not None and not o: raise StudioError(f'START_PICTURE_FILE_MISSING: the picture shot {n} opens on is not in ComfyUI/input any more; pick another or re-add it')
                opens[n] = o or ['START PICTURE', '']
        try: nums, reqs = fm.rewrite_requests(film, nums, cast=cast, motion=st['settings'].get('motion', 'lively'), model=model, opens_on=opens)
        except ValueError as e: raise StudioError(str(e))
        import studio_thinking_v0939 as th
        return {'model_id': model, 'think': th.budgets(thinking), 'shots_only': reqs, 'numbers': nums, 'names': names}

    # -- ⏱ how long generating takes (V0.9.37): read from ComfyUI's own history of YOUR takes -------------------------
    def gen_timings(self, *, comfy_url=None, opener=None, max_fetch=25):
        """How long each of your takes took to generate on THIS ComfyUI: [{'frames', 'steps', 'seconds'}]. ComfyUI's history
        (start and finish time of each job) is read once per take and kept in timing.json, since ComfyUI forgets it on a
        restart. Only takes that came back are counted; a job that failed or was cancelled is skipped."""
        from urllib.request import urlopen
        op = opener or urlopen
        url = (comfy_url or getattr(self, 'comfy_url', None) or 'http://127.0.0.1:18188').rstrip('/')
        f = self.root / 'timing.json'
        try: cache = json.loads(f.read_text()) if f.is_file() else {}
        except ValueError: cache = {}
        st = self.state(); fetched = 0; changed = False
        captured = {(n, t['take']) for n, sh in st['shots'].items() for t in sh['takes']}
        for sub in st['submissions']:
            pid = sub.get('prompt_id')
            if not pid or pid in cache or (sub['shot'], sub['take']) not in captured or fetched >= max_fetch: continue
            fetched += 1
            try:
                with op(f'{url}/history/{pid}', timeout=10) as r: h = json.load(r).get(pid) or {}
            except Exception: continue
            st_ = h.get('status') or {}
            msgs = {m[0]: (m[1] or {}).get('timestamp') for m in st_.get('messages') or [] if isinstance(m, (list, tuple)) and len(m) == 2}
            t0, t1 = msgs.get('execution_start'), msgs.get('execution_success')
            if not st_.get('completed') or not t0 or not t1 or t1 <= t0: cache[pid] = None; changed = True; continue
            plan = next((p for p in st['shots'].get(sub['shot'], {'plans': []})['plans'] if p['take'] == sub['take']), {})
            cache[pid] = {'frames': int(plan.get('frames') or 124), 'steps': int((plan.get('sampling') or {}).get('steps') or DEFAULT_STEPS),
                          'seconds': round((t1 - t0) / 1000.0, 1)}
            changed = True
        if changed:
            tmp = f.with_suffix('.partial'); tmp.write_text(json.dumps(cache)); os.replace(tmp, f)
        return [v for v in cache.values() if v and v.get('seconds', 0) > 5]

    def _gen_frames(self, st, seconds):
        """The length H3 generates for a shot that runs `seconds` in the movie (the same rule as plan())."""
        length = h3_length(st['settings'].get('gen_length', 124)); length = length if length in GEN_LENGTHS else 124
        if isinstance(seconds, (int, float)) and seconds * 24 + 14 > length:
            need = int(seconds * 24 + 14); length = next((L for L in LONG_LENGTHS if L >= need), LONG_LENGTHS[-1])
        return length

    def film_estimate(self, film, *, comfy_url=None, opener=None):
        """⏱ About how long generating the film's shots takes: from how long YOUR takes took on this ComfyUI (the closest
        length you have made, scaled by frames and steps). With no takes timed yet it says so and gives no number."""
        st = self.state(); steps = self._sampling(st)['steps']
        shots = [s for s in (film or {}).get('shots') or []]
        frames = [self._gen_frames(st, float(s.get('seconds') or 5)) for s in shots]
        samples = self.gen_timings(comfy_url=comfy_url, opener=opener)
        out = {'shots': len(shots), 'frames': frames, 'samples': len(samples), 'steps': steps}
        if not samples or not frames: return {**out, 'seconds_per_take': None}
        total = 0.0
        for fr in frames:
            near = sorted(samples, key=lambda x: (abs(x['frames'] - fr), -x['seconds']))[:5]
            rates = sorted(x['seconds'] / (x['frames'] * x['steps']) for x in near)
            total += rates[len(rates) // 2] * fr * steps
        return {**out, 'seconds_per_take': round(total, 0), 'longest_sample': max(x['frames'] for x in samples)}

    def film_rewrite_apply(self, film, request, result):
        import studio_film_v0936 as fm, director_intent_v0739 as di
        film = json.loads(json.dumps(film))                          # a copy: the page's film is not changed in place
        fm.apply_rewrites(film, request['numbers'], result.get('shot_replies') or [], names=request['names'],
                          forbidden=di.FORBIDDEN_VISUAL_SIMPLE, stopped=result.get('stopped'))
        fm.keep_notes(film, result.get('notes'))                     # V0.9.43 ✋
        for n in request.get('numbers') or []:                        # 📍 V0.9.49 a rewrite that still disagrees with your positions says so
            if not (isinstance(n, int) and 1 <= n <= len(film['shots'])): continue
            s = film['shots'][n - 1]
            if not ((s.get('positions') or {}).get('start')): continue
            try: cl = pm.clashes(s.get('direction') or '', pm.clean(s['positions'], s.get('people') or []))
            except pm.PositionsError: cl = []
            ws = [w for w in (s.get('warnings') or []) if not w.startswith("📍 Qwen's rewrite still")]
            s['warnings'] = ws + [f"📍 Qwen's rewrite still disagrees with your positions: {c}" for c in cl]
        for x in result.get('thinking') or []:                       # V0.9.39 🧠 a shot whose thinking was cut short says so
            n = x.get('step') if isinstance(x, dict) else None
            if isinstance(n, int) and x.get('cut') and 1 <= n <= len(film['shots']):
                film['shots'][n - 1].setdefault('warnings', []).append('Qwen used all its thinking room on this shot and wrote it from what it had thought.')
        self._save_film(film)
        return film

    def _save_film(self, film):
        film['id'] = re.sub(r'[^0-9a-f]', '', str(film.get('id') or ''))[:10] or hashlib.sha256(json.dumps(film.get('shots'), default=str).encode()).hexdigest()[:10]
        d = self.root / 'films'; d.mkdir(exist_ok=True)
        text = json.dumps(film, indent=1, ensure_ascii=False)
        for name in (f"{film['id']}.json", 'last.json'):
            tmp = d / (name + '.partial'); tmp.write_text(text); os.replace(tmp, d / name)
        self._autosave_decisions()

    def films(self):
        """🎬 V0.9.50 your saved films, newest first: [{'id', 'title', 'shots', 'planned', 'kept', 'seconds', 'parts', 'opens_on', 'saved'}]."""
        d = self.root / 'films'; out = []
        if not d.is_dir(): return out
        st = self.state()
        for f in d.glob('*.json'):
            if f.name == 'last.json' or not re.fullmatch(r'[0-9a-f]{1,10}\.json', f.name): continue
            try: film = json.loads(f.read_text())
            except ValueError: continue
            planned = film.get('planned') or {}
            kept = sum(1 for v in planned.values() if isinstance(v, list) and v and st['shots'].get(int(v[0])) and
                       any(t['status'] == 'ACCEPTED' for t in st['shots'][int(v[0])]['takes']))
            out.append({'id': film.get('id') or f.stem, 'title': str(film.get('title') or 'Your film')[:80], 'shots': len(film.get('shots') or []),
                        'planned': len(planned), 'kept': kept, 'seconds': round(sum(float(s.get('seconds') or 0) for s in film.get('shots') or []), 1),
                        'parts': len(film.get('parts') or []) or 1, 'opens_on': ((film.get('shots') or [{}])[0] or {}).get('start_frame'),
                        'saved': int(f.stat().st_mtime)})
        return sorted(out, key=lambda x: -x['saved'])

    def load_film(self, fid):
        fid = re.sub(r'[^0-9a-f]', '', str(fid or ''))[:10]
        f = self.root / 'films' / f'{fid}.json'
        if not fid or not f.is_file(): raise StudioError('NO_SUCH_FILM')
        try: return json.loads(f.read_text())
        except ValueError: raise StudioError('FILM_FILE_DAMAGED')

    def film_ending(self, st, film):
        """🎬 V0.9.50 how the film ends now, in words (text only, never a frame): the end positions you set on its last shot, else
        who was where when the movie cuts that shot (faces recognised), else nothing (Qwen goes by the last shot's words)."""
        shots = film.get('shots') or []
        if not shots: return ''
        last = shots[-1]; bits = []
        pos = last.get('positions') or {}
        if pos.get('end') or pos.get('start'):
            try:
                c = pm.clean(pos, last.get('people') or [])
                a, b = pm.for_h3(c)
                bits.append((b or a).replace('By the end, ', 'At the end, ').replace('At the start, ', 'Where the last shot began: '))
            except pm.PositionsError: pass
        v = (film.get('planned') or {}).get(str(len(shots)))
        if not bits and isinstance(v, list) and v:
            try:
                for e in reversed(self.story(st, before=int(v[0]) + 1)):
                    if e.get('shot') == int(v[0]):
                        lr = ((e.get('at_cut') or {}).get('left_to_right')) or []
                        if len(lr) >= 2: bits.append(f"Where the movie cuts the last shot: {' on the left, '.join(lr[:1])} on the left and {lr[-1]} on the right.")
                        break
            except Exception: pass
        if not bits and last.get('after'): bits.append('From the film\'s plan, when the last shot ends: ' + last['after'].rstrip('.') + '.')   # V0.9.54
        return ' '.join(bits)

    def last_film(self):
        f = self.root / 'films' / 'last.json'
        try: return json.loads(f.read_text()) if f.is_file() else None
        except ValueError: return None

    def plan_film(self, film, *, start_at=1, count=None):
        """Plan the film's shots in order (from shot `start_at`), each as a new shot with its own people, place and length.
        Stops at the first shot that needs you (a question, or a direction the Director refuses) and says which one."""
        import studio_film_v0936 as fm
        try: shots = fm.clean_edited(film)
        except ValueError as e: raise StudioError(str(e))
        st = self.state()
        chars = {c['name']: c['id'] for c in st['characters'].values() if not c.get('retired')}
        locs = {L['name']: L['id'] for L in st['locations'].values() if not L.get('retired')}
        fid = re.sub(r'[^0-9a-f]', '', str(film.get('id') or ''))[:10] or hashlib.sha256(json.dumps(shots).encode()).hexdigest()[:10]
        planned = dict(film.get('planned') or {}); out = []; stopped = None
        # V0.9.49 a shot only counts as planned from THIS film when that plan still exists (not discarded), came from this film,
        # and was planned from the words the shot has now. A stale entry (a discarded plan, an earlier film's shots, words
        # changed since) made the film skip the shot ("already planned") while nothing for it could be sent.
        # V0.9.50 ✎ a planned shot you edited (its words or any of its settings) is planned again as a NEW TAKE of the same shot,
        # so it keeps its place in the movie; only a plan that is gone or came from another film makes a new shot
        replanned = []; edit = {}
        for k in list(planned):
            v = planned[k]; x = next((y for y in shots if str(y['n']) == str(k)), None)
            try: q = self.plan_record(st, int(v[0]), int(v[1]))
            except (StudioError, ValueError, TypeError, IndexError, KeyError): q = None
            if q is None or q.get('film') != fid:
                planned.pop(k); replanned.append(int(k)); continue
            if x is None: continue
            changed = (q.get('film_sig') != self._film_sig(film, x)) if q.get('film_sig') else ((q.get('film_direction') or '') != x['direction'][:2800])
            if changed: planned.pop(k); edit[int(k)] = int(v[0])
        reveals, kept = {}, set()                    # 👁 V0.9.48 the first shot that shows a face hidden in the shots before it
        for x in shots:
            hid = set(x.get('hide_face') or []); rv = [n for n in x['people'] if n in kept and n not in hid]
            if rv: reveals[x['n']] = rv
            kept = (kept - set(rv)) | hid
        if film.get('lock_frame'):                  # 🔒 V0.9.47 every shot inside the opening picture's frame: its view, one fixed angle
            sf0 = shots[0].get('start_frame') if shots else None
            try: lock_loc = self.frame_plate(sf0)
            except StudioError as e:                     # V0.9.50: a picture that is mostly people (or someone on a bed, which the
                if not str(e).startswith('OPENING_PICTURE_IS_MOSTLY_PEOPLE'): raise      # cut-out takes as part of them) has
                lock_loc = None                          # too little empty room to make the room picture from: the lock carries on
                note = ("🔒 The opening picture is mostly people (or people on furniture the cut-out counts as part of them), so no "     # without it
                        "empty-room picture could be made from it. Every shot still uses its one camera angle; turn on 🎞 every shot opens on "
                        "the picture to keep the exact frame, or open the film on a wider picture of the room.")
                film = {**film, 'warnings': [w for w in (film.get('warnings') or []) if not w.startswith('🔒 The opening picture is mostly')] + [note]}
            st = self.state()
            film = {**film, 'angle': self.lock_angle(st, sf0, film.get('angle'))}
            if lock_loc:
                lname = st['locations'][lock_loc]['name']; locs[lname] = lock_loc
                for x in shots: x['place'] = lname
            for x in shots: x['framing'] = film['angle']
        for s in shots:
            if s['n'] < int(start_at): continue
            if str(s['n']) in planned: continue                      # already planned from this film: never twice
            if count is not None and len(out) >= int(count): break
            bad = [p for p in s['people'] if p not in chars]
            if bad: stopped = {'n': s['n'], 'why': f"UNKNOWN_PERSON: {', '.join(bad)} is not in this project"}; break
            if s['place'] and s['place'] not in locs: stopped = {'n': s['n'], 'why': f"UNKNOWN_PLACE: {s['place']}"}; break
            try: sf = self._start_frame_ok(st, s.get('start_frame'))
            except StudioError as e: stopped = {'n': s['n'], 'why': str(e)}; break
            prev = next((x for x in shots if x['n'] == s['n'] - 1), None)
            ctx = None
            if prev:                    # V0.9.37: the story beat, not the written-out direction (its "by the end" positions were
                orig = film['shots'][prev['n'] - 1] if prev['n'] - 1 < len(film['shots']) else {}     # only meant, not measured)
                src = orig.get('beat') if (orig.get('beat') and not orig.get('edited')) else prev['direction']
                before = re.sub(r'\s*["\u201c][^"\u201d]*["\u201d]', '', src).strip(' ,.;')[:400]
                jn = s.get('join') or 'cut'
                ctx = (f"Just before this shot, in the film (already happened, not shown again): {before}. " +
                       ("This shot opens on the director's picture: where everyone starts comes from that picture." if sf else
                        f"Then time passes ({s.get('later') or 'a while later'}): this shot shows what is happening now." if jn == 'skip' else
                        "This shot cuts straight to its own action, already under way." if jn == 'cut' else
                        "Where everyone is now comes only from what was measured where the movie cuts the shot before, never from this."))
            again = edit.get(s['n'])                                    # ✎ V0.9.50 a new take of the shot it already is
            move = s.get('camera') if s.get('camera') in fm.CAMERA_MOVES else None      # 🎥 V0.9.50 this one shot moves the camera
            text = f"{fm.lead(s['framing'], s['people'])}. {s['direction']}"
            if move:                                                    # its own words may still say the camera stays put: that goes
                text = re.sub(r'[^.!?]*\bcamera\b[^.!?]*\b(?:static|still|tripod|never moves|does not move|doesn\'t move|locked)\b[^.!?]*[.!?]\s*', '', text, flags=re.I).strip()
                text += ' ' + fm.CAMERA_MOVES[move]
                if not sf and film.get('lock_frame') and shots and shots[0].get('start_frame'):     # it starts on the film's picture
                    try: sf = self._start_frame_ok(st, shots[0]['start_frame'])
                    except StudioError: pass
            elif film.get('angle') and fm.CAMERA_STILL not in text: text += ' ' + fm.CAMERA_STILL       # 📷 V0.9.41 one fixed angle
            ap = (film['shots'][s['n'] - 1] if s['n'] - 1 < len(film['shots']) else {}).get('action_photo')
            if ap and (ap not in st['action_photos'] or st['action_photos'][ap].get('removed')): ap = None   # 📷 one of YOUR original stills only
            try:
                p = self.plan(text, mode='retake' if again else 'next', target_shot=again, people=s['people'] or None, background=locs.get(s['place']) if s['place'] else None,
                              duration=s['seconds'], pose='none', start_frame=sf, positions=s.get('positions'), hide_faces=s.get('hide_face') or [],
                              compose_start=bool(film.get('compose_start') and not sf and ((s.get('positions') or {}).get('start'))
                                                 and not (prev and (s.get('join') or 'cut') == 'carry' and st['settings'].get('carry_frame', True))),   # 🧩 V0.9.55; 🔗 V0.9.61 a carry-on shot's frame is built when the shot before is back
                              reveal=reveals.get(s['n'], []),
                              _extra={'film': fid, 'film_shot': s['n'], 'film_title': str(film.get('title') or '')[:80], 'written_by': 'qwen_film',
                                      'film_sig': self._film_sig(film, s), **({'replanned': True} if again else {}),
                                      'film_direction': s['direction'][:2800], 'film_framing': s['framing'],
                                      **({'film_angle': film['angle']} if (film.get('angle') and not move) else {}),
                                      **({'film_lock': True} if (film.get('lock_frame') and not move) else {}),
                                      **({'film_camera': move} if move else {}),
                                      **({'lead_in': fm.lead_of(film)} if (sf and fm.lead_of(film)) else {}),
                                      **({'film_join': s.get('join') or 'cut', **({'film_later': s['later']} if s.get('later') else {})} if prev else {}),
                                      **({'film_action_photo': ap} if ap else {}),
                                      **({'scene_context': ctx} if ctx else {})})
            except StudioError as e:
                stopped = {'n': s['n'], 'why': str(e)}; break
            out.append({'n': s['n'], **{k: p.get(k) for k in ('action', 'shot', 'take', 'question', 'understood_as', 'frames')}})
            if p.get('action') != 'PLANNED':
                stopped = {'n': s['n'], 'why': p.get('question') or f"The Director read it as {p.get('interpretation') or 'not a shot'}: {p.get('understood_as')}"}; break
            planned[str(s['n'])] = [p['shot'], p['take']]
        if shots and shots[0].get('start_frame') and any(x.get('place') for x in shots[1:]):     # V0.9.46
            note = (f"Shot 1 opens on your picture, so its camera sees the room the way that photo does; the shots after it get the room "
                    f"from the place photo ({next(x['place'] for x in shots[1:] if x.get('place'))}). If the two show the room from different "
                    "spots, the angle changes after shot 1: use a place photo taken from the same spot as the opening picture, or no opening picture.")
            if note not in (film.get('warnings') or []): film = {**film, 'warnings': list(film.get('warnings') or []) + [note]}
        film = {**film, 'id': fid, 'shots': [{**orig, **{k: c[k] for k in ('direction', 'seconds', 'framing', 'people', 'place', 'auto', 'start_frame', 'join', 'later', 'positions', 'hide_face', 'camera') if k in c}}
                                             for orig, c in zip(film['shots'], shots)], 'planned': planned}
        if edit:
            done_ = sorted(n for n in edit if str(n) in planned)
            if done_:
                note = (f"✎ Shot {', '.join(map(str, done_))}: planned again from your changes, as a new take of the same shot (its place in the "
                        "movie stays; the earlier take is still on the shot's card).")
                film = {**film, 'warnings': [w for w in (film.get('warnings') or []) if not w.startswith('✎ Shot ')] + [note]}
        if replanned:
            note = (f"Shot {', '.join(map(str, sorted(replanned)))}: its earlier plan was discarded, came from another film, or its words "
                    "changed since, so it was planned again from the film as it is now.")
            film = {**film, 'warnings': [w for w in (film.get('warnings') or []) if not w.endswith('planned again from the film as it is now.')] + [note]}
        if 'target' in film: fm.summarize(film)
        self._save_film(film)
        shots_planned = [planned[str(s['n'])][0] for s in shots if str(s['n']) in planned]
        return {'film': film, 'plans': out, 'stopped': stopped, 'shots': shots_planned}

    # -- 📷 action photo for the rewriter (V0.9.22) --------------------------------------------
    def add_action_photo(self, path, *, name='', attest=False):
        """A photo of the action you want: ONLY one of YOUR original stills (attested, same gate as every original: never a
        generated take or a frame of one, never a file from ComfyUI/output). Only Qwen sees it, and only for the body
        (posture, limbs, hands, direction); it never reaches H3."""
        import cv2
        p = Path(path)
        h = self._gate(p, attest)
        im = cv2.imread(str(p))
        if im is None: raise StudioError('ACTION_PHOTO_MUST_BE_A_PHOTO (jpg, png or webp)')
        st = self.state(); aid = h[:16]
        if aid in st['action_photos'] and not st['action_photos'][aid].get('removed'): return aid
        d = self.root / 'action_photos'; d.mkdir(parents=True, exist_ok=True)
        f = d / (aid + '.jpg'); cv2.imwrite(str(f), im, [cv2.IMWRITE_JPEG_QUALITY, 92])
        name = ' '.join(str(name or p.stem).split())[:60] or 'action'
        self._append('ACTION_PHOTO_ADDED', {'id': aid, 'name': name, 'file': f.name, 'sha256': h, 'provenance': 'USER_ATTESTED_ORIGINAL'})
        return aid

    def _action_item(self, st, action):
        """'<photo id>' (your original still) or 'take:<shot>:<take>:<frame>' (a moment of one of your takes: generated, so
        ONLY for position and body action, never appearance; only Qwen's words, which you review, go further)."""
        import studio_rewrite_v0913 as rw
        if str(action).startswith('take:'):
            try: _, n, tk, fr = str(action).split(':'); n, tk, fr = int(n), int(tk), int(fr)
            except ValueError: raise StudioError('ACTION_FRAME_FORMAT')
            t = next((x for x in (st['shots'].get(n) or {'takes': []})['takes'] if x['take'] == tk), None)
            if not t: raise StudioError('NO_SUCH_TAKE')
            f = self.take_path(t)
            if not same_file(f, t['sha256']): raise StudioError('TAKE_FILE_MISSING_OR_CHANGED')
            if not 0 <= fr < int(t.get('frames') or 10 ** 6): raise StudioError('ACTION_FRAME_OUTSIDE_THE_TAKE')
            label = (rw.ACTION_LABEL if t.get('source') else rw.ACTION_LABEL_GENERATED)
            return {'label': label, 'path': str(f), 'frame': fr}
        a = (st.get('action_photos') or {}).get(action)
        if not a or a.get('removed'): raise StudioError('NO_SUCH_ACTION_PHOTO')
        return {'label': rw.ACTION_LABEL, 'path': str(self.root / 'action_photos' / a['file']), 'frame': None}

    def remove_action_photo(self, aid):
        st = self.state()
        if aid not in st['action_photos']: raise StudioError('NO_SUCH_ACTION_PHOTO')
        self._append('ACTION_PHOTO_REMOVED', {'id': aid})

    # -- 🎭 movement from YOUR video (V0.9.21) -----------------------------------------------
    def pose_sources(self, st=None):
        """Videos a shot can take its movement from: your uploaded original videos and the movement videos you added."""
        st = st or self.state(); out = []
        names = {**{c['id']: c['name'] for c in st['characters'].values()}, **{l['id']: l['name'] for l in st['locations'].values()}}
        for o in self.originals(st):
            if o['source_sha256'] in st['generated_sha']: continue
            out.append({'id': 'orig:' + o['source_sha256'][:16], 'label': f"{names.get(o['owner'], 'your')}'s original video", 'kind': 'original',
                        'path': str(self.root / 'uploads' / o['file']), 'sha256': o['source_sha256'], 'file': o['file']})
        for m in st['motions'].values():
            if m.get('removed') or m['sha256'] in st['generated_sha']: continue
            out.append({'id': 'mot:' + m['id'], 'label': f"movement video \"{m['name']}\"", 'kind': 'movement', 'path': str(self.root / 'motions' / m['file']),
                        'sha256': m['sha256'], 'file': m['file'], 'seconds': m.get('seconds')})
        return out

    def add_motion(self, path, *, name='', attest=False):
        """Add a movement video of yours (🎭): e.g. you acting out how someone should move. It must be YOUR original (same
        gate as every reference: never generated, never from ComfyUI/output). Only its stick figures ever reach H3."""
        import studio_pose_v0921 as ps
        h = self._gate(path, attest)
        st = self.state(); mid = h[:16]
        if mid in st['motions'] and not st['motions'][mid].get('removed'): return mid
        frames = ps.video_frames(path)
        if frames < 24: raise StudioError('MOVEMENT_VIDEO_TOO_SHORT: at least 1 second, please')
        d = self.root / 'motions'; d.mkdir(parents=True, exist_ok=True)
        f = d / (mid + (Path(path).suffix.lower() or '.mp4'))
        if not f.exists(): shutil.copyfile(path, f)
        name = ' '.join(str(name or Path(path).stem).split())[:60] or 'movement'
        self._append('MOTION_ADDED', {'id': mid, 'name': name, 'file': f.name, 'sha256': h, 'seconds': round(frames / 24, 2),
                                      'provenance': 'USER_ATTESTED_ORIGINAL'})
        return mid

    def remove_motion(self, mid):
        st = self.state()
        if mid not in st['motions']: raise StudioError('NO_SUCH_MOVEMENT_VIDEO')
        self._append('MOTION_REMOVED', {'id': mid})

    def _prepare_pose(self, st, pose, length):
        """Your chosen video -> the take's exact length and size in ComfyUI/input (what SDPose reads). Fail closed."""
        import studio_pose_v0921 as ps
        if not isinstance(pose, dict) or not pose.get('source'): raise StudioError('POSE_PICK_A_MOVEMENT_VIDEO')
        src = next((x for x in self.pose_sources(st) if x['id'] == pose['source']), None)
        if src is None: raise StudioError('POSE_ONLY_FROM_YOUR_OWN_VIDEOS: pick one of your original or movement videos')
        f = Path(src['path'])
        if not f.is_file() or media.sha256(f) != src['sha256']: raise StudioError('POSE_VIDEO_MISSING_OR_CHANGED: add it again')
        strength = round(float(pose.get('strength') or ps.DEFAULT_STRENGTH), 2)
        if not ps.STRENGTH[0] <= strength <= ps.STRENGTH[1]: raise StudioError('POSE_STRENGTH_0.2_TO_1.0')
        start = max(0.0, round(float(pose.get('start') or 0), 2))
        rel = f"director_studio/{st['project']['slug']}/pose_{src['sha256'][:10]}_{int(start * 100)}_{length}.mp4"
        dest = self.input_dir / rel
        if dest.is_file(): sha, real = media.sha256(dest), ps.video_frames(f) - int(round(start * 24))
        else:
            try: _, sha, real = ps.prepare_control(f, dest, frames=length, start=start, width=FMT['width'], height=FMT['height'])
            except RuntimeError as e: raise StudioError(str(e))
        if real <= 12: raise StudioError('POSE_START_IS_PAST_THE_END_OF_THE_VIDEO')
        return {'source': src['id'], 'label': src['label'], 'source_sha256': src['sha256'], 'name': rel, 'sha256': sha,
                'strength': strength, 'start': start, 'short_by': max(0, length - real)}

    _MOVERS_CACHE = {}
    def movers_in(self, path, sha=None):
        """V0.9.30: how many people your movement video shows (most faces seen in one of a few frames; None = can't tell,
        e.g. filmed from behind). Cached by the video's hash."""
        cache = Studio._MOVERS_CACHE
        key = sha or str(path)
        if key not in cache:
            try: cache[key] = max((len(media.people_faces(im)) for _, im in media.sample_frames(path, 6)), default=0) or None
            except Exception: cache[key] = None
        return cache[key]

    def _pose_who(self, st, pose_rec, pose, cast, spec):
        """V0.9.30: which person of the shot follows the movement video (your pick, else the shot's main person) and,
        when the video shows fewer people than the shot, a sentence that everyone else moves on their own.
        -> (pose_rec with 'who'/'movers', note for H3, line for the plan card)."""
        chars = st['characters']
        want = (pose or {}).get('who') if isinstance(pose, dict) else None
        byname = {chars[c]['name'].lower(): c for c in cast if c in chars}
        who = want if want in cast else byname.get(str(want or '').lower())
        if want and not who: raise StudioError(f'POSE_WHO_IS_NOT_IN_THIS_SHOT: {str(want)[:40]}')
        if not who and cast: who = spec.get('subject') if spec.get('subject') in cast else cast[0]
        src = next((x for x in self.pose_sources(st) if x['id'] == pose_rec['source']), None)
        movers = self.movers_in(src['path'], src.get('sha256')) if src else None
        pose_rec = {**pose_rec, 'who': who, 'movers': movers}
        if not who or len(cast) < 2 or (movers is not None and movers >= len(cast)): return pose_rec, '', None
        others = [chars[c]['name'] for c in cast if c != who and c in chars]
        one = len(others) == 1
        pr = chars[next(c for c in cast if c != who)].get('pronoun') if one else None
        own = {'she': 'her', 'he': 'his'}.get(pr, 'their')
        name = chars[who]['name']; oth = ' and '.join(others)
        note = (f"{name} follows the guiding movement. {oth} {'is' if one else 'are'} not part of it and "
                f"move{'s' if one else ''} on {own} own, as described.")
        return pose_rec, note, f"🎭 {name} follows the movement; {oth} move{'s' if one else ''} on {own} own" + \
               (f" (your video shows {movers} person)" if movers == 1 else " (couldn't count the people in your video)" if movers is None else '')

    def _pose_decl(self, p):
        pr = p.get('pose')
        return {pr['name']: pr['sha256']} if pr else None

    def _ensure_pose_file(self, st, p):
        """Before sending: the prepared movement clip is still there and unchanged (re-made from your video if not)."""
        pr = p.get('pose')
        if not pr: return
        f = self.input_dir / pr['name']
        if f.is_file() and media.sha256(f) == pr['sha256']: return
        f.unlink(missing_ok=True)
        again = self._prepare_pose(st, pr, int(p.get('frames', 124)))
        if again['sha256'] != pr['sha256']: raise StudioError('POSE_CLIP_CHANGED: plan the shot again (🎭 movement)')

    # -- extending your own original videos -------------------------------------------------
    def take_path(self, t):
        return (self.root / 'sources' / t['file']) if t.get('source') else (self.output_dir / t['file'])

    def originals(self, st=None):
        """Your uploaded ORIGINAL videos per person/place (the files you uploaded, kept in the project)."""
        st = st or self.state(); out = {}
        for r in st['refs'].values():
            if r.get('source_kind') != 'video' or r.get('removed'): continue
            o = out.setdefault((r['owner'], r['source_sha256']), {'owner': r['owner'], 'source_sha256': r['source_sha256'], 'refs': 0})
            o['refs'] += 1
        for o in out.values():
            ups = sorted((self.root / 'uploads').glob(o['source_sha256'][:16] + '.*'))
            o['file'] = ups[0].name if ups else None
            C = st['characters'].get(o['owner']); o['main'] = bool(C and C.get('main_source') == o['source_sha256'])
            o['in_movie'] = any(sh.get('source') and sh['takes'][0]['source_sha256'] == o['source_sha256'] for sh in st['shots'].values())
        return [o for o in out.values() if o['file']]

    def set_main_source(self, cid, source_sha256):
        """Make one of a character's original videos their main reference: its stills and clip come first."""
        st = self.state()
        if cid not in st['characters']: raise StudioError('UNKNOWN_CHARACTER')
        if source_sha256 and not any(o['owner'] == cid and o['source_sha256'] == source_sha256 for o in self.originals(st)) \
                and not any(r.get('owner') == cid and r.get('source_sha256') == source_sha256 and not r.get('removed')
                            and r.get('kind') in ('face', 'upper', 'full') for r in st['refs'].values()):     # ⭐ V0.9.65 a photo too
            raise StudioError('NOT_ONE_OF_THEIR_ORIGINAL_UPLOADS')
        self._append('CHARACTER_MAIN_SOURCE', {'id': cid, 'source_sha256': source_sha256 or None})

    def extend(self, source_sha256, text, *, background=None, people=None, emotions=None):
        """Continue one of YOUR original videos: its ending goes into the movie, and the next shot continues from it
        ([video continuation], the last 73 frames (about 3 s) of your video as <Video 1>). Generated takes can never be extended."""
        st = self.state()
        o = next((x for x in self.originals(st) if x['source_sha256'] == source_sha256), None)
        if o is None: raise StudioError('ONLY_YOUR_UPLOADED_ORIGINAL_VIDEOS_CAN_BE_EXTENDED')
        src = self.root / 'uploads' / o['file']
        if media.sha256(src) != source_sha256: raise StudioError('ORIGINAL_VIDEO_CHANGED')
        if source_sha256 in st['generated_sha']: raise StudioError('REFUSED_GENERATED_MEDIA')
        owner = o['owner']; dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True)
        tail = next((r for r in st['refs'].values() if r.get('kind') == 'tail_clip' and r.get('source_sha256') == source_sha256 and not r.get('removed')
                     and float(r.get('seconds') or 0) <= media.REF_CLIP_SECONDS + 0.05), None)          # an older 5 s ending is cut again at 73 frames
        if tail is None:
            meta = media.probe(src); dur = float(meta.get('duration') or 0)
            if dur < 1.0: raise StudioError('VIDEO_TOO_SHORT_TO_EXTEND')
            frames = media.sample_frames(src, 24)
            frames = [(t_, im) for t_, im in frames if t_ >= max(0.0, dur - media.REF_CLIP_SECONDS)] or frames
            media._clean_text(frames, st['settings'].get('ignore_text', True)); rep = dict(media.TEXT_REPORT); notes = []
            clip = self._clean_clip_at(src, dest / f'{owner}_{source_sha256[:8]}_tail{media.REF_CLIP_FRAMES}.mp4', rep, notes, start=max(0.0, dur - media.REF_CLIP_SECONDS))
            if not clip: raise StudioError('CAPTIONS_COVER_THE_FACE_AT_THE_END_OF_THIS_VIDEO_CANNOT_EXTEND_IT')
            rid = f'ref_{source_sha256[:10]}_tail'
            while rid in st['refs']: rid += 'r'
            self._append('REF_ADDED', {'id': rid, 'owner': owner, 'kind': 'tail_clip', 'alt': False, 'variant': 'raw', 'file': Path(clip['path']).name,
                         'sha256': clip['sha256'], 'seconds': clip['seconds'], 'input_name': f"{self.input_sub}/{Path(clip['path']).name}",
                         'source_sha256': source_sha256, 'source_kind': 'video', 'provenance': 'USER_ATTESTED_ORIGINAL_ENDING_FOR_CONTINUATION'})
            st = self.state(); tail = st['refs'][rid]
        shot_src = next((n for n, sh in st['shots'].items() if sh.get('source') and sh['takes'][0]['source_sha256'] == source_sha256), None)
        if shot_src is None:
            used = set(st['shots']) | set(st.get('memory', {})); shot_src = (max(used) + 1) if used else 1
            sd = self.root / 'sources'; sd.mkdir(exist_ok=True)
            f = sd / f"source_{source_sha256[:12]}.mp4"
            info = media.conform_source(src, f, seconds=16.0)
            self._append('SOURCE_ADDED', {'shot': shot_src, 'file': f.name, 'sha256': media.sha256(f), 'frames': info['frames'], 'audio': info['audio'],
                         'source_sha256': source_sha256, 'cast': [owner] if owner in st['characters'] else [],
                         'location': owner if owner in st['locations'] else None, 'text': 'Your original video'})
            self._edited = True
        p = self.plan(text, mode='next', background=background, people=people, emotions=emotions,
                      _extra={'extend_source': source_sha256}, extend={'clip': tail['id'], 'owner': owner, 'source_sha256': source_sha256})
        return p

    def _anchor_for(self, st, tail_id):
        """The last ANCHOR_FRAMES frames (about 0.9 s, with sound) of YOUR original video, cut from its (caption-cleaned)
        ending clip: pinned at frame 0 of an extension take so it starts exactly where your original stops. None if it
        can't be made (the take then continues from the clip as before)."""
        t = st['refs'].get(tail_id)
        if not t or t.get('kind') != 'tail_clip' or t.get('removed'): return None
        have = next((r for r in st['refs'].values() if r.get('kind') == 'anchor_clip' and r.get('from_tail') == tail_id and not r.get('removed')), None)
        if have and (self.input_dir / have['input_name']).is_file(): return have['id']
        src = self.input_dir / t['input_name']
        if not src.is_file() or media.sha256(src) != t['sha256']: return None
        dest = src.with_name(f'{src.stem}_end{media.ANCHOR_FRAMES}.mp4')
        try: c = media.trim_ref_clip(src, dest, frames=media.ANCHOR_FRAMES, at_end=True)
        except Exception: return None
        if have and have.get('sha256') == c['sha256']: return have['id']
        rid = f"{tail_id}_end"
        while rid in st['refs']: rid += 'e'
        self._append('REF_ADDED', {'id': rid, 'owner': t['owner'], 'kind': 'anchor_clip', 'alt': False, 'variant': 'raw', 'file': dest.name,
                     'sha256': c['sha256'], 'seconds': c['seconds'], 'input_name': str(Path(t['input_name']).with_name(dest.name)),
                     'source_sha256': t['source_sha256'], 'source_kind': 'video', 'from_tail': tail_id,
                     'provenance': 'USER_ATTESTED_ORIGINAL_ENDING_FOR_CONTINUATION'})
        return rid

    def _clip_info(self, r):
        """🎞 which seconds of which original a person's reference clip is (for the card)."""
        up = next((p.name for p in (self.root / 'uploads').glob(str(r.get('source_sha256', ''))[:16] + '.*') if p.is_file()), None) if r.get('source_sha256') else None
        return {'seconds': r.get('seconds'), 'start': r.get('start'), 'source_seconds': r.get('source_seconds'), 'chosen': r.get('chosen'),
                'why': r.get('why'), 'original': up}

    def set_clip_start(self, rid, start):
        """🎞 V0.9.27: YOUR choice of which 3 s of the original video the person's reference clip is: cut again from your
        ORIGINAL upload (captions filled in as before). Shots planned before keep the clip they had. Returns the new ref id."""
        st = self.state(); r = st['refs'].get(rid)
        if not r or r.get('kind') != 'ref_clip' or r.get('removed'): raise StudioError('NOT_A_REFERENCE_CLIP')
        if r['owner'] not in st['characters']: raise StudioError('ONLY_A_PERSONS_CLIP_CAN_BE_MOVED')
        ups = [p for p in (self.root / 'uploads').glob(r['source_sha256'][:16] + '.*') if p.is_file()]
        if not ups: raise StudioError('ORIGINAL_VIDEO_NOT_IN_THIS_PROJECT_ANY_MORE_ADD_IT_AGAIN')
        up = ups[0]; dur = float(media.probe(up).get('duration') or 0)
        if dur < media.REF_CLIP_SECONDS: raise StudioError('VIDEO_SHORTER_THAN_3_SECONDS')
        start = max(0.0, min(float(start), dur - media.REF_CLIP_SECONDS))
        frames = media.sample_frames(up, 48)
        media._clean_text(frames, st['settings'].get('ignore_text', True)); rep = dict(media.TEXT_REPORT)
        dest = self.input_dir / self.input_sub; dest.mkdir(parents=True, exist_ok=True)
        base = Path(r['input_name']).stem.split('_clip')[0]
        clip = self._clean_clip(up, dest / f'{base}_clip_s{int(start * 1000):06d}.mp4', rep, [], start=start)
        if not clip: raise StudioError('CAPTIONS_COVER_THE_FACE_IN_THOSE_3_SECONDS_PICK_OTHERS')
        if any(x.get('sha256') == clip['sha256'] and not x.get('removed') and x['owner'] == r['owner'] for x in st['refs'].values()): return rid
        nid = f"{rid.split('_s')[0]}_s{int(start * 1000):06d}"
        while nid in st['refs']: nid += 'r'
        self._append('REF_REMOVED', {'id': rid, 'owner': r['owner'], 'reason': 'clip moved to other seconds'})
        self._append('REF_ADDED', {**{k: v for k, v in r.items() if k not in ('removed', 'id', 'matched_from')}, 'id': nid,
                                   'file': Path(clip['path']).name, 'input_name': f"{self.input_sub}/{Path(clip['path']).name}",
                                   'sha256': clip['sha256'], 'seconds': clip['seconds'], 'start': clip['start'],
                                   'source_seconds': clip['source_seconds'], 'chosen': 'you', 'why': 'picked by you'})
        return nid

    def _clean_clip_at(self, src, dest, rep, notes, start):
        import studio_text_v090 as tx
        cleanup = None
        if rep.get('checked') and rep.get('union') and rep.get('size'):
            H, W = rep['size']
            flt, covers = tx.clip_filter(rep['union'], [f['box'] if f else None for f in rep.get('faces', [])], W, H)
            if covers: return None
            if flt: cleanup = {'w': W, 'h': H, 'filter': flt}
        return media.make_ref_clip(src, dest, cleanup=cleanup, start=start)

    # -- several takes, A/B tests, storyboard ---------------------------------------------
    @staticmethod
    def _fresh_seed(st, basis):
        """A seed no take of this project has ever had (V0.9.25). The ledger only grows (a discard or undo is a new
        event), so mixing in its length means a shot/take number freed by a discard (e.g. a scene planned again) never
        gets its old seed back; and every seed ever planned, discarded ones too, is skipped. Same seed + same prompt =
        the same video (ComfyUI even hands back its cached copy)."""
        used = {e['payload'].get('seed') for e in st['events'] if e['kind'] == 'SHOT_PLANNED'}
        n = len(st['events'])
        while True:
            seed = int(hashlib.sha256(canonical({**basis, 'n': n}).encode()).hexdigest()[:12], 16)
            if seed not in used: return seed
            n += 1

    def another_take(self, shot):
        """🎲 One more take of a shot (any shot, also one inside a scene): its newest plan, a new seed. Returns the take."""
        st = self.state(); sh = st['shots'].get(shot)
        if not sh: raise StudioError(f'NO_SUCH_SHOT_{shot}')
        base = next((q for q in reversed(sh['plans']) if q.get('workflow_file')), None)
        if not base: raise StudioError('IMPORTED_TAKE_HAS_NO_WORKFLOW_PLAN_A_RETAKE_OR_NEW_SHOT')
        return self.more_takes(shot, base['take'], 2)[0]

    def more_takes(self, shot, take, n):
        """n-1 more takes of the same plan with different seeds (same refs, prompt, LoRAs). Returns their take numbers."""
        if not 2 <= n <= 4: raise StudioError('TAKES_2_TO_4')
        out = []
        for k in range(n - 1):
            st = self.state(); p = self.plan_record(st, shot, take)
            if not p.get('workflow_file'): raise StudioError('IMPORTED_TAKE_HAS_NO_WORKFLOW_PLAN_A_RETAKE_OR_NEW_SHOT')
            t2 = len(st['shots'][shot]['plans']) + 1
            seed = self._fresh_seed(st, {'p': st['project']['slug'], 'shot': shot, 'take': t2, 'prompt': p['prompt'], 'variant_of': take})
            prefix = f"Studio_{st['project']['slug']}_S{shot:02d}_T{t2:02d}"
            g = build_graph([st['refs'][r]['input_name'] for r in p['refs']], p['prompt'], seed, prefix,
                            video_names=[st['refs'][r]['input_name'] for r in p.get('videos', [])],
                            video_audio=bool(p.get('videos')) and bool(st['settings'].get('voice_refs')) and not p.get('audios'),
                            audio_names=[st['refs'][r]['input_name'] for r in p.get('audios', [])], anchor_names=self._anchor_names(st, p), pose=p.get('pose'),
                            loras=[(l['name'], l['strength']) for l in p.get('loras', [])], length=int(p.get('frames', 124)), **(p.get('sampling') or {}))
            text_wf = json.dumps(g, indent=2, ensure_ascii=False) + '\n'
            (self.root / 'plans' / f'{prefix}.json').write_text(text_wf)
            q = {**{k2: v for k2, v in p.items() if k2 != 'seq'}, 'take': t2, 'mode': 'variant', 'variant_of': take, 'seed': seed, 'prefix': prefix,
                 'workflow_file': f'{prefix}.json', 'workflow_sha256': hashlib.sha256(text_wf.encode()).hexdigest()}
            self._append('SHOT_PLANNED', q); out.append(t2)
        return out

    AB_SETTINGS = {'fit_pictures': (True, False), 'fix_sides': (True, False), 'best_refs': (True, False), 'match_refs': (True, False), 'enhance': (True, False), 'ref_numbering': (True, False), 'max_refs': (9, 6), 'video_refs': ('auto', 'off'), 'clean_refs': ('auto', 'always'),
                   'gen_length': (124, 107), 'prompt_format': ('official', 'classic'), 'motion': ('lively', 'natural'), 'look': ('real_life', 'cinematic'), 'faithful': (True, False), 'loras': ('on', 'off'),
                   'acting': ('expressive', 'understated'), 'steps': (25, 20), 'sampler': ('euler', 'res_multistep')}

    def ab_test(self, shot, setting):
        """Two new takes of a shot that differ ONLY in one setting (same seed), for a side-by-side choice."""
        if setting not in self.AB_SETTINGS: raise StudioError('AB_SETTING_' + '_'.join(self.AB_SETTINGS))
        st = self.state()
        if shot not in st['shots']: raise StudioError('NO_SUCH_SHOT')
        base = st['shots'][shot]['plans'][-1]
        if base.get('mode') == 'imported': raise StudioError('PLAN_A_RETAKE_OF_THIS_SHOT_FIRST')
        seed = self._fresh_seed(st, {'ab': setting, 'shot': shot, 'n_plans': len(st['shots'][shot]['plans'])})   # A and B share it
        va, vb = self.AB_SETTINGS[setting]
        oa, ob = {setting: va}, {setting: vb}
        if setting == 'loras':                        # V0.9.27: your LoRAs (as set in Settings) vs none, same seed
            mine = st['settings'].get('loras') or []
            if not mine: raise StudioError('AB_LORAS_ADD_A_LORA_IN_SETTINGS_FIRST')
            oa, ob = {'loras': mine}, {'loras': []}
        ta = self.plan(base['text'], mode='retake', target_shot=shot, background=(base.get('background') or {}).get('location') or
                       ('people' if (base.get('background') or {}).get('mode') == 'people' else None), _settings=oa, _seed=seed)
        if ta.get('action') != 'PLANNED': raise StudioError('AB_COULD_NOT_PLAN: ' + str(ta.get('question') or ta.get('action')))
        tb = self.plan(base['text'], mode='retake', target_shot=shot, background=(base.get('background') or {}).get('location') or
                       ('people' if (base.get('background') or {}).get('mode') == 'people' else None), _settings=ob, _seed=seed)
        if ta['prompt'] == tb['prompt'] and ta['refs'] == tb['refs'] and ta.get('videos') == tb.get('videos') and ta.get('loras') == tb.get('loras') and ta.get('sampling') == tb.get('sampling'):
            for e in [e for e in self.state()['events'] if e['kind'] == 'SHOT_PLANNED'][-2:]:
                self._append('UNDONE', {'target': e['seq'], 'kind': 'SHOT_PLANNED'})
            raise StudioError(f'AB_{setting.upper()}_MAKES_NO_DIFFERENCE_FOR_THIS_SHOT')
        aid = f"ab_{shot}_{ta['take']}_{tb['take']}"
        self._append('AB_TEST', {'id': aid, 'shot': shot, 'setting': setting, 'seed': seed,
                                 'a': {'take': ta['take'], 'value': va}, 'b': {'take': tb['take'], 'value': vb},
                                 'same_prompt': ta['prompt'] == tb['prompt'], 'same_refs': ta['refs'] == tb['refs']})
        return {'id': aid, 'a': ta, 'b': tb}

    def ab_result(self, aid, winner):
        if winner not in ('a', 'b', 'same'): raise StudioError('WINNER_A_B_SAME')
        st = self.state()
        if not any(e['kind'] == 'AB_TEST' and e['payload']['id'] == aid for e in st['events']): raise StudioError('UNKNOWN_AB_TEST')
        self._append('AB_RESULT', {'id': aid, 'winner': winner})

    def ab_summary(self, st=None):
        st = st or self.state(); tests = {}; tally = {}
        for e in st['events']:
            if e['kind'] == 'AB_TEST': tests[e['payload']['id']] = {**e['payload'], 'winner': None}
            elif e['kind'] == 'AB_RESULT' and e['payload']['id'] in tests: tests[e['payload']['id']]['winner'] = e['payload']['winner']
        for t in tests.values():
            if not t['winner']: continue
            d = tally.setdefault(t['setting'], {str(t['a']['value']): 0, str(t['b']['value']): 0, 'same': 0})
            k = 'same' if t['winner'] == 'same' else str(t[t['winner']]['value']); d[k] = d.get(k, 0) + 1
        return {'tests': list(tests.values()), 'tally': tally}

    def storyboard(self, lines):
        """Plan a whole scene: one new shot per line, each aware of the shots planned before it."""
        lines = [l.strip() for l in lines if l and l.strip()][:20]
        if not lines: raise StudioError('WRITE_ONE_SHOT_PER_LINE')
        out = []
        for l in lines:
            p = self.plan(l, mode='next', _extra={'storyboard': True})
            out.append(p)
            if p.get('action') != 'PLANNED': break
        return out

    def cover(self, text, *, people=None, emotions=None, background=None, start_frame=None, end_frame=None, pose=None):
        """Coverage: one direction -> 2-4 consecutive shots that cut like a filmed scene (wide, the speaker's shot, the
        listener's reaction, a two-shot for action). Each is planned normally (same references, continuity, Clean-Chain)."""
        import studio_coverage_v098 as cv
        st = self.state(); chars = [c for c in st['characters'].values() if not c.get('retired')]
        names = {c['id']: c['name'] for c in chars}; byname = {c['name']: c['id'] for c in chars}
        picked = [byname.get(p, p) for p in (people or [])]
        if any(p not in names for p in picked): raise StudioError('UNKNOWN_PERSON')
        try: whole = si.interpret(text, chars, previous_cast=None)
        except ValueError as e: raise StudioError(TOO_LONG if 'TOO_LONG' in str(e) else 'WRITE_WHAT_HAPPENS: type what happens in this moment first')
        cast = picked or [c for c in whole.get('cast', []) if c in names] or list(names)[:2]
        focus = picked[0] if picked else (whole.get('subject') if whole.get('subject') in cast else (cast[0] if cast else None))
        def read(c):
            try: return si.interpret(c, chars, previous_cast=cast)
            except ValueError: return {}
        pron = {c['id']: c.get('pronoun') for c in chars}
        shots = cv.shot_texts(text, read, cast, names, focus=focus, pronouns=pron)
        if len(shots) < 2:
            raise StudioError('COVERAGE_NEEDS_TWO_BEATS: write the moment as two or more beats, e.g. '
                              '"Maya walks in, then tells Grandpa \"I got the job!\", then they hug"')
        return self._plan_scene(text, shots, names, byname, emotions=emotions, background=background, start_frame=start_frame, end_frame=end_frame, pose=pose)

    def _plan_scene(self, text, shots, names, byname, *, emotions=None, background=None, start_frame=None, end_frame=None, source='coverage', pose=None):
        st = self.state()
        emo = {byname.get(k, k): v for k, v in (emotions or {}).items()}
        gid = 'cov_' + hashlib.sha256(f"{text}|{len(st['events'])}".encode()).hexdigest()[:8]
        out = []; pose_at = float((pose or {}).get('start') or 0) if isinstance(pose, dict) else 0.0
        for i, sh in enumerate(shots):
            mine = {k: v for k, v in emo.items() if k in sh['people']} or None
            # 🎭 one movement video runs through the scene: each shot picks it up where the shot before left off
            my_pose = ({**pose, 'start': round(pose_at, 2)} if isinstance(pose, dict) and pose.get('source') else 'none')
            before = re.sub(r'\s*["\u201c][^"\u201d]*["\u201d]', '', sh.get('before') or '').strip(' ,.;')   # never an earlier line's words
            ctx = (f"Just before this shot, in the same moment (already happened, not shown again): {before}." if before else None)
            kw = dict(mode='next', people=[names[c] for c in sh['people'] if c in names] or None, emotions=mine, background=background,
                      _extra={'coverage': gid, 'coverage_role': sh['role'], 'coverage_beat': i + 1, 'coverage_of': text[:300],
                              'written_by': source, **({'scene_context': ctx} if ctx else {})},
                      start_frame=start_frame if i == 0 else None,                         # the scene opens on your picture
                      end_frame=end_frame if i == len(shots) - 1 else None)                # ... and its last shot ends on yours
            try: p = self.plan(sh['text'], pose=my_pose, **kw)
            except StudioError as e:
                if my_pose == 'none' or 'POSE_START_IS_PAST_THE_END' not in str(e): raise
                p = self.plan(sh['text'], pose='none', **kw); pose = None                 # the movement video has run out: the rest move freely
            if my_pose != 'none' and p.get('action') == 'PLANNED': pose_at += int(p.get('frames', 124)) / 24
            out.append(p)
            if p.get('action') != 'PLANNED': break
        return {'coverage': gid, 'shots': [{'role': s['role'], 'text': s['text'], 'before': s.get('before')} for s in shots], 'plans': out}

    # -- the prompt rewriter (Qwen): suggestions you keep, edit or discard -----------------------------------------
    def rewrite_pictures(self, st, picked, *, extend_source=None, start_frame=None, end_frame=None, action_photo=None, prev_end=True):
        """What Qwen gets to look at (labelled): your original photos of the people and the place, your start / end pictures,
        and the moment the new shot starts from: the end of YOUR original video (an extension) or the end of the previous shot
        in the movie (a generated take: for positions and actions only, never appearance)."""
        R = st['refs']; items = []
        if action_photo:                                   # 📷 shown first so it is never the one left out
            items.append(self._action_item(st, action_photo))
        def still(rid, label, pinned=False):
            r = R.get(rid)
            if r and not r.get('removed'):                  # 📐 V0.9.69 a pinned picture: Qwen sees the frame H3 gets (fitted), not the whole photo
                items.append({'label': label, 'path': str(self._pinned_file(st, rid) if pinned else self.input_dir / r['input_name']), 'frame': None})
        for c in picked:
            mine = [r for r in c['refs'] if r in R and not R[r].get('removed') and R[r].get('variant', 'raw') == 'raw' and not R[r].get('matched_from')]
            want = c.get('main_source')
            mine.sort(key=lambda r: (0 if want and R[r].get('source_sha256') == want else 1))
            body = next((r for r in mine if R[r]['kind'] in ('full', 'upper')), None); face = next((r for r in mine if R[r]['kind'] == 'face'), None)
            for r in (body, face):
                if r: still(r, f"{c['name']} (an ORIGINAL photo, only for telling who is who in the other pictures; never describe how {c['name']} looks)")
        L = next((l for l in st['locations'].values() if not l.get('retired')), None)
        if L:
            plate = next((r for r in L['refs'] if r in R and R[r]['kind'] == 'location' and not R[r].get('removed') and not R[r].get('matched_from')), None)
            if plate: still(plate, f"The place, {L['name']} (an ORIGINAL photo)")
        if start_frame: still(start_frame, 'START PICTURE (an ORIGINAL photo): the new shot opens exactly on this picture', pinned=True)
        if end_frame: still(end_frame, 'END PICTURE (an ORIGINAL photo): the new shot ends exactly on this picture', pinned=True)
        if extend_source:
            tail = next((r for r in R.values() if r.get('kind') == 'tail_clip' and r.get('source_sha256') == extend_source and not r.get('removed')), None)
            if tail: items.append({'label': "THE END OF THE DIRECTOR'S ORIGINAL VIDEO (reliable): the new shot begins exactly at this moment",
                                   'path': str(self.input_dir / tail['input_name']), 'frame': 'last'})
        elif not start_frame and prev_end:                 # V0.9.37: not when the shot right before isn't back (tl[-1] would be an older shot)
            try: tl = self.timeline(st)
            except Exception: tl = []
            if prev_end is not True and not isinstance(prev_end, bool): tl = [c for c in tl if c['shot'] == prev_end]   # that shot's cut, nothing else
            if tl:
                c = tl[-1]; t = next((x for x in st['shots'][c['shot']]['takes'] if x['take'] == c['take']), None)
                if t:
                    items.append({'label': ("THE END OF THE PREVIOUS SHOT, the director's ORIGINAL video (reliable): the new shot starts from this moment"
                                            if t.get('source') else 'END OF THE PREVIOUS SHOT (generated): ONLY for who is where, which way they face, '
                                            'what they hold and do at this moment; never describe how anyone looks from it'),
                                  'path': str(self.take_path(t)), 'frame': max(0, int(c['out']) - 1)})
        return items

    def describe_request(self, cid, pictures_dir):
        """📝 What Qwen looks at to write a person's look: up to 4 of their ORIGINAL photos (never a generated take)."""
        import studio_rewrite_v0913 as rw, studio_qwenmodel_v0916 as qm
        st = self.state(); C = st['characters'].get(cid)
        if not C: raise StudioError('UNKNOWN_CHARACTER')
        R = st['refs']
        mine = [r for r in C['refs'] if r in R and not R[r].get('removed') and R[r].get('variant', 'raw') == 'raw' and not R[r].get('matched_from')
                and R[r]['kind'] in ('full', 'upper', 'face') and R[r].get('sha256') not in st['generated_sha']]
        if not mine: raise StudioError('ADD_A_PHOTO_OF_THEM_FIRST')
        # ONE upload only (one outfit): their ★ main video, else the upload with the most stills, so two outfits never mix
        groups = {}
        for r in mine: groups.setdefault(R[r].get('source_sha256') or R[r].get('sha256'), []).append(r)
        want = C.get('main_source')
        look = []
        if st['settings'].get('best_refs', True):            # ⭐ V0.9.67 the same main look the shots use (it may span same-look uploads)
            try:
                import studio_bestref_v0965 as br
                srcs = br.plan_person(R, [r for r in C['refs'] if r in R], self._refinfo(), main_source=want)['outfit_sources']
                look = [r for s_ in srcs for r in groups.get(s_, [])]
            except Exception: look = []
        mine = look or groups.get(want) or max(groups.values(), key=lambda g: (len(g), max(R[r].get('seq', 0) if isinstance(R[r].get('seq'), int) else 0 for r in g)))
        mine.sort(key=lambda r: ('full', 'upper', 'face').index(R[r]['kind']))
        pics = rw.prepare_pictures([{'label': f"{C['name']} (an ORIGINAL photo)", 'path': str(self.input_dir / R[r]['input_name']), 'frame': None}
                                    for r in mine[:4]], pictures_dir)
        return rw.describe_request(C['name'], C.get('pronoun'), pics, model=qm.model_id(st['settings'].get('qwen_model'), 'DIRECTOR_REWRITE_MODEL'))

    def describe_look(self, cid, reply):
        """Qwen's look line -> a SUGGESTION for the card (not saved: you check, edit and press Save)."""
        import studio_rewrite_v0913 as rw, director_intent_v0739 as di
        st = self.state(); C = st['characters'].get(cid)
        if not C: raise StudioError('UNKNOWN_CHARACTER')
        try: look = rw.clean_look(reply, C['name'])
        except ValueError as e: raise StudioError(str(e) + ': Qwen gave no usable description; write the look yourself')
        if di.FORBIDDEN_VISUAL_SIMPLE.search(look): raise StudioError('DESCRIBE_REFUSED_CLEAN_CHAIN')
        return {'id': cid, 'description': look, 'current': C.get('description') or ''}

    def check_carry(self, st, shot=None):
        """V0.9.23: what 🔍 "Did it do what I asked?" found in the take that is in the movie for the previous shot:
        {'shot', 'take', 'done': [...], 'missed': [...]} or None (no checked take before this shot). shot (V0.9.37): that
        shot's take, not simply the last one in the movie."""
        try: tl = self.timeline(st)
        except Exception: return None
        if shot is not None: tl = [c for c in tl if c['shot'] == shot]
        if not tl: return None
        c = tl[-1]; t = next((x for x in st['shots'][c['shot']]['takes'] if x['take'] == c['take']), None)
        pts = ((t or {}).get('check') or {}).get('points') or []
        if not pts: return None
        done = [x['point'] for x in pts if x.get('seen') == 'yes']; missed = [x['point'] for x in pts if x.get('seen') == 'no']
        return {'shot': c['shot'], 'take': c['take'], 'done': done, 'missed': missed} if (done or missed) else None

    def people_in_picture(self, st, im, cids):
        """V0.9.27: who of `cids` is in this picture (an image array, or a path). Faces are counted (hands and prints are not
        people) and recognised against each person's ORIGINAL photos. -> {'present', 'absent', 'names', 'count', 'unsure',
        'nobody'} with names, or None when it can't be told. Measurement only."""
        try:
            if not isinstance(im, np.ndarray): im = media.load_image(im)
            names = [st['characters'][c]['name'] for c in cids if c in st['characters']]
            n = len(media.people_faces(im))
            if n == 0: return {'present': [], 'absent': names, 'names': names, 'count': 0, 'nobody': True}
            import studio_identity_v090 as idn
            embs = {c: self.person_embedding(st, c) for c in cids} if idn.available() else {}
            embs = {c: e for c, e in embs.items() if e is not None}
            got = idn.assign(idn.embed(im), embs) if embs else {}
            sure = {c: v for c, v in got.items() if v[0] >= idn.MATCH and v[2] >= idn.MARGIN}
            # V0.9.32: who is on which side, MEASURED (the old hint just said "the first name is on the left")
            order = [st['characters'][c]['name'] for c, v in sorted(sure.items(), key=lambda kv: kv[1][1]['cx'])]
            if n >= len(names):
                return {'present': names, 'absent': [], 'names': names, 'count': n,
                        **({'left_to_right': order} if len(order) == len(names) else {'sides_unsure': True})}
            if len(sure) == n:
                pres = [st['characters'][c]['name'] for c in cids if c in sure]
                return {'present': pres, 'absent': [x for x in names if x not in pres], 'names': names, 'count': n,
                        **({'left_to_right': order} if len(order) >= 2 else {})}
            return {'present': [], 'absent': [], 'names': names, 'count': n, 'unsure': True}
        except Exception:
            return None

    def _picture_of(self, item):
        """The full-size picture behind one rewrite picture item (a photo, or a frame of a video)."""
        import cv2
        if item.get('frame') is None: return media.load_image(item['path'])
        cap = cv2.VideoCapture(str(item['path'])); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        idx = max(0, n - 1) if item['frame'] == 'last' else max(0, min(int(item['frame']), n - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx); ok, im = cap.read(); cap.release()
        return im if ok else None

    def frame_people_note(self, st, cast, start_frame=None, end_frame=None):
        """V0.9.27: for H3's prompt: who of the shot is NOT in your start / end picture (they come in / leave during the shot)."""
        out = []
        for rid, first in ((start_frame, True), (end_frame, False)):
            r = st['refs'].get(rid) if rid else None
            if not r: continue
            seen = self.people_in_picture(st, str(self.input_dir / r['input_name']), cast)
            gone = (seen or {}).get('absent') or []
            if not gone: continue
            who = ' and '.join(gone); one = len(gone) == 1
            pr = next((c.get('pronoun') for c in st['characters'].values() if c['name'] == gone[0]), None) if one else None
            they, verb, have = ({'she': 'she', 'he': 'he'}.get(pr, 'they'), 'comes' if pr in ('she', 'he') else 'come',
                                'has' if pr in ('she', 'he') else 'have') if one else ('they', 'come', 'have')
            out.append(f"{who} {'is' if one else 'are'} not in the first frame: {they} {verb} into the frame during the shot, as the action describes."
                       if first else f"{who} {'is' if one else 'are'} not in the last frame: by the end {they} {have} left the frame.")
        return ' '.join(out)

    @staticmethod
    def _no_lines(prompt):
        """🎭 a take steered by stick figures: say they are never drawn into the picture (both prompt formats)."""
        prompt = prompt.replace('There is no on-screen text, no subtitles and no watermark.',
                                'There is no on-screen text, no subtitles and no watermark, and no stick figures, skeleton lines, dots or drawn lines anywhere in the picture.')
        return prompt.replace('no text, no extra people,', 'no text, no stick figures or drawn lines, no extra people,')

    @staticmethod
    def _with_note(prompt, note):
        """Put a sentence at the end of the shot description (official format) or before AUDIO (classic)."""
        if not note: return prompt
        if '\n\noverall_soundscape:' in prompt: return prompt.replace('\n\noverall_soundscape:', ' ' + note + '\n\noverall_soundscape:', 1)
        if ' AUDIO' in prompt: return prompt.replace(' AUDIO', ' ' + note + ' AUDIO', 1)
        return prompt + ' ' + note

    def rewrite_request(self, text, *, people=None, mode='shot', extend_source=None, start_frame=None, end_frame=None, pictures_dir=None, length=None,
                        action_photo=None, use_check=True, camera=None, framing=None, pose=None, for_shot=None, positions=None, hide_faces=None):
        """What Qwen is asked: your notes, the people (names, he/she, look), the place and what is already true now; and, with
        pictures_dir, the labelled pictures it looks at (see rewrite_pictures)."""
        rw = _fresh_rewrite_module()
        if not (text or '').strip(): raise StudioError('WRITE_WHAT_HAPPENS: type what happens in this moment first')
        if len(text) > 4000: raise StudioError('NOTES_TOO_LONG_FOR_QWEN: up to 4000 characters at a time; rewrite it in parts')
        st = self.state(); chars = [c for c in st['characters'].values() if not c.get('retired')]
        byname = {c['name']: c for c in chars}
        picked = [byname[p] for p in (people or []) if p in byname] or chars[:2]
        cast = [{'name': c['name'], 'pronoun': c.get('pronoun'), 'look': c.get('description') or ', '.join(c.get('descriptors') or [])} for c in picked]
        nxt = int(for_shot) if for_shot else ((max(st['shots']) + 1) if st['shots'] else 1)    # V0.9.37: 🔗 a planned shot of a film
        try: stand = self.continuity_text(st, nxt, [c['id'] for c in picked], opens_on=bool(start_frame))
        except Exception: stand = ''
        try: pf = None if start_frame else self.positions_from(st, nxt)   # V0.9.37 📍: never the end of a shot two back (V0.9.38: a start picture says it)
        except Exception: pf = None
        import studio_positions_v0945 as pm                  # 📍 V0.9.45 where you set everyone: known facts for Qwen
        try: pos = pm.clean(positions, [c['name'] for c in cast])
        except pm.PositionsError as e: raise StudioError(str(e))
        if pos and pos.get('start'): pf = None             # you said where they start: nothing is pending
        if pf and pf.get('pending') is not None: stand = (stand + ' ' + self.UNKNOWN_START.format(n=pf['pending'])).strip()
        if pos: stand = (stand + ' ' + pm.for_qwen(pos, pronoun=lambda n: next((c['pronoun'] for c in cast if c['name'] == n), 'they'))).strip()
        hid = [c['name'] for c in cast if c['name'] in (hide_faces or [])]                 # 🙈 V0.9.48
        if hid:
            import studio_film_v0936 as _fm
            stand = (stand + _fm.HIDE_STAND.format(who=' and '.join(hid), s='' if len(hid) > 1 else 's')).strip()
        place = next((L['name'] + (f" ({L['description']})" if L.get('description') else '') for L in st['locations'].values() if not L.get('retired')), None)
        secs = h3_length(st['settings'].get('gen_length', 124)) / 24
        # V0.9.21: the shot's own length (the ~6/~8/~10 s chips, or "about 8 seconds" in your notes) so Qwen fills it, not 5 s
        m = re.search(r'\b(?:about|around|roughly|~)?\s*(\d{1,2})\s*(?:s|sec|secs|seconds)\b(?:\s+long)?', text or '', re.I)
        want = str(length) if str(length or '') in ('6', '8', '10') else (m.group(1) if m and 6 <= int(m.group(1)) <= 10 else None)
        if want: secs = max(secs, float(want))
        pics = []; items = []
        if pictures_dir is not None:
            items = self.rewrite_pictures(st, picked, extend_source=extend_source, start_frame=start_frame, end_frame=end_frame, action_photo=action_photo,
                                          prev_end=(False if pf and pf.get('pending') is not None else (pf or {}).get('shot') or True))
            pics = rw.prepare_pictures(items, pictures_dir)
        by_label = {it['label']: it for it in items}
        def seen_in(label):                                # V0.9.27: who is really in that picture (never ask Qwen about someone absent)
            it = by_label.get(label)
            if not it: return None
            im = self._picture_of(it)
            return self.people_in_picture(st, im, [c['id'] for c in picked]) if im is not None else None
        if action_photo:
            self._action_item(st, action_photo)                # refuses an unknown photo / take frame in plain words
            text = text.rstrip() + ('\n' + rw.ACTION_ASK.format(who=' and '.join(c['name'] for c in cast) or 'the people'))
        import studio_qwenmodel_v0916 as qm
        req = rw.build_request(text, mode=mode, cast=cast, place=place, where_things_stand=stand or None, seconds=secs, pictures=pics,
                               model=qm.model_id(st['settings'].get('qwen_model'), 'DIRECTOR_REWRITE_MODEL'),
                               motion=st['settings'].get('motion', 'lively'), camera_chosen=bool(camera), framing=framing,
                               start_known=not (pf and pf.get('pending') is not None))
        # step 1 (V0.9.23): every picture that pins a moment is described ALONE first; the rewrite gets those words as fixed text
        names = [c['name'] for c in cast]; steps = []
        def find(prefix): return next(((l, j) for l, j in pics if l.startswith(prefix)), None)
        if action_photo:
            ap = find('ACTION PHOTO')
            if ap: steps.append(rw.pose_step(ap[0], ap[1], names))
            else: req['user'] = req['user'].replace('{POSE}', '(see the ACTION PHOTO)')
        start = find('START PICTURE')
        prev = None if start else find(('END OF THE PREVIOUS SHOT', 'THE END OF THE PREVIOUS SHOT', "THE END OF THE DIRECTOR'S ORIGINAL VIDEO"))
        asks = []
        seen_report = {}
        if start or prev:
            src = start or prev
            seen = seen_in(src[0]); seen_report['start' if start else 'previous'] = seen
            steps.append(rw.stand_step(src[0], src[1], names, start_picture=bool(start), seen=seen))
            asks.append(rw.STAND_ASK.format(src='the START PICTURE the shot opens on' if start else
                                            ("the end of the director's original video" if src[0].startswith("THE END OF THE DIRECTOR'S") else 'the end of the previous shot')))
            a = rw.absent_ask(seen, 'start' if start else 'previous')
            if a: asks.append(a)
        end = find('END PICTURE')
        if end:
            seen = seen_in(end[0]); seen_report['end'] = seen
            steps.append(rw.end_step(end[0], end[1], names, seen=seen)); asks.append(rw.END_ASK.format())
            a = rw.absent_ask(seen, 'end')
            if a: asks.append(a)
        if seen_report: req['in_pictures'] = {k: v for k, v in seen_report.items() if v}
        if isinstance(pose, dict) and pose.get('source') and len(picked) >= 2:   # V0.9.30: 🎭 who follows the movement video
            ids = [c['id'] for c in picked]; byn = {c['name'].lower(): c['id'] for c in picked}
            who = pose.get('who') if pose.get('who') in ids else byn.get(str(pose.get('who') or '').lower(), ids[0])
            src = next((x for x in self.pose_sources(st) if x['id'] == pose['source']), None)
            movers = self.movers_in(src['path'], src.get('sha256')) if src else None
            if movers is None or movers < len(ids):
                nm = st['characters'][who]['name']; oth = ' and '.join(c['name'] for c in picked if c['id'] != who)
                asks.append(f"MOVEMENT GUIDE: {nm}'s body movement is steered by the director's movement video: describe what {nm} does "
                            f"in broad strokes that fit it. {oth} {'is' if len(ids) == 2 else 'are'} NOT part of that movement: "
                            f"{oth} move{'s' if len(ids) == 2 else ''} on their own; write {oth}'s own actions in full.")
                req['pose_who'] = {'follows': nm, 'free': oth}
        carry = self.check_carry(st, shot=(pf or {}).get('shot') if pf and pf.get('pending') is None else None) if use_check and not extend_source else None
        if pf and pf.get('pending') is not None: carry = None                 # 🔍 of a shot two back is not "the previous shot"
        if carry:                                          # 🔍 what really happened in the previous shot (Qwen checked the take)
            if carry['done']: asks.append('ALREADY DONE in the previous shot (seen on screen; do NOT do it again): ' + '; '.join(carry['done']) + '.')
            if carry['missed']: asks.append("NOT DONE in the previous shot (it didn't happen on screen): " + '; '.join(carry['missed']) +
                                            ". Only if the director's notes for this shot want it, it can happen now; otherwise leave it out.")
            req['carry'] = carry
        yours = self.notes_for_qwen(st)                    # V0.9.61 📝 how you judged earlier takes, in your own words
        if yours: asks.append(self.notes_text(yours)); req['your_notes'] = yours
        if asks: req['user'] = req['user'].replace("\nDIRECTOR'S NOTES:", '\n' + '\n'.join(asks) + "\n\nDIRECTOR'S NOTES:", 1)
        if steps: req['pre_steps'] = steps
        return req, [c['name'] for c in picked]

    NOTES_HEAD = ("HOW THIS DIRECTOR JUDGES TAKES (their own notes on takes they kept or rejected earlier; let them shape how you write, "
                  "but the director's notes for THIS shot come first):")
    def notes_for_qwen(self, st, limit=5):
        """📝 V0.9.61 your notes on takes you kept or rejected (newest first): Qwen learns what you want from your own words.
        Only takes with a note (a bare Keep/Reject doesn't say why). Off with Settings > learn."""
        if not st['settings'].get('learn', True): return []
        out = []
        for n, sh in st['shots'].items():
            for t in sh['takes']:
                note = ' '.join(str(t.get('note') or '').split())
                if not note or t['status'] not in ('ACCEPTED', 'REJECTED'): continue
                q = next((x for x in sh['plans'] if x['take'] == t['take']), {})
                out.append((t.get('reviewed_seq') or 0, t['status'] == 'ACCEPTED', ' '.join(str(q.get('text') or '').split())[:140], note[:200]))
        out.sort(key=lambda x: -x[0])
        return [{'kept': k, 'shot': txt, 'note': nt} for _, k, txt, nt in out[:limit]]

    def notes_text(self, notes):
        return self.NOTES_HEAD + ''.join(f'\n- {"Kept" if x["kept"] else "Rejected"} a take of "{x["shot"]}": "{x["note"]}"' for x in notes)

    def rewrite(self, text, *, people=None, mode='shot', reply=None, qwen_python=None, steps=None, **pictures):
        """Qwen's suggestion, checked (names, Clean-Chain wording, your lines). Nothing is planned or sent."""
        import studio_rewrite_v0913 as rw, director_intent_v0739 as di
        if reply is None:
            import tempfile as _tf
            with _tf.TemporaryDirectory() as d:
                req, names = self.rewrite_request(text, people=people, mode=mode, pictures_dir=d, **pictures)
                qp = qwen_python or (getattr(self, 'qwen_paths', None) or {}).get('qwen_python') or qwen.DEFAULTS['qwen_python']
                try: reply, steps = rw.run_with_steps(req, qwen_python=qp)
                except RuntimeError as e: raise StudioError(str(e))
        else: req, names = self.rewrite_request(text, people=people, mode=mode, action_photo=pictures.get('action_photo'),
                                                use_check=pictures.get('use_check', True), extend_source=pictures.get('extend_source'))
        try:
            res = rw.check(reply, mode=mode, original=text, names=names, forbidden=di.FORBIDDEN_VISUAL_SIMPLE)
            steps = steps or {}
            for k in ('POSE', 'END'):
                m = di.FORBIDDEN_VISUAL_SIMPLE.search(steps.get(k) or '')
                if m: raise ValueError('REWRITE_REFUSED_CLEAN_CHAIN: a picture description said "' + m.group(0).strip()[:60] + '"')
            if steps.get('POSE') and pictures.get('action_photo'): res = rw.ensure_pose(res, steps['POSE'], text, mode)
            if steps.get('END') and pictures.get('end_frame'): res = rw.ensure_pose(res, steps['END'], text, mode, where='end', field='end')
            if steps.get('STAND') and not di.FORBIDDEN_VISUAL_SIMPLE.search(steps['STAND']): res['stand'] = steps['STAND'].strip()
        except ValueError as e: raise StudioError(str(e) + ' (Qwen\'s suggestion was not usable; your own wording still works)')
        res['mode'] = mode; res['model'] = req.get('model_id')
        if req.get('carry'): res['carry'] = req['carry']
        res['note'] = f"Written by {str(req.get('model_id') or 'Qwen').split('/')[-1]} (a suggestion, not checked against your footage): keep, edit or discard it."
        return res

    def rewrite_clashes(self, res, positions, people=None):
        """📍 V0.9.49 where a one-shot rewrite disagrees with the positions you set (posture or side of the frame), plain words."""
        if not positions or not isinstance(res, dict) or not res.get('direction'): return []
        st = self.state(); names = [c['name'] for c in st['characters'].values() if not c.get('retired')]
        cast = [n for n in (people or []) if n in names] or names
        try: pos = pm.clean(positions, cast)
        except pm.PositionsError: return []
        return pm.clashes(res['direction'], pos)

    def plan_written_scene(self, text, shots, *, emotions=None, background=None, start_frame=None, end_frame=None, pose=None):
        """A scene whose shots YOU kept (from the rewriter, as written or edited): [{'direction', 'framing', 'people'}]."""
        import studio_rewrite_v0913 as rw
        st = self.state(); chars = [c for c in st['characters'].values() if not c.get('retired')]
        names = {c['id']: c['name'] for c in chars}; byname = {c['name']: c['id'] for c in chars}
        if not isinstance(shots, list) or not 1 <= len(shots) <= 4: raise StudioError('SCENE_1_TO_4_SHOTS')
        clean = []
        for s in shots:
            fr = str(s.get('framing') or 'medium')
            if fr not in rw.FRAMINGS: fr = 'medium'
            d = ' '.join(str(s.get('direction') or '').split())
            if len(d) < 5: raise StudioError('WRITE_WHAT_HAPPENS: every shot needs a direction')
            ppl = [p for p in (s.get('people') or []) if p in byname]
            if len(ppl) != len(s.get('people') or []): raise StudioError('UNKNOWN_PERSON')
            clean.append({'framing': fr, 'people': ppl, 'direction': d})
        plan = rw.scene_shots({'shots': clean})
        for k, sh in enumerate(plan):
            sh['people'] = [byname[p] for p in sh['people']] or [c['id'] for c in chars[:2]]
            sh['before'] = clean[k - 1]['direction'] if k else None
        return self._plan_scene(text or ' '.join(s['direction'] for s in clean), plan, names, byname, emotions=emotions, background=background,
                                start_frame=start_frame, end_frame=end_frame, source='you_from_qwen', pose=pose)

    def angles(self, shot, take):
        """Coverage: the same shot (same direction and lines) from two more angles: a wide two-shot and a close-up on the
        speaker (or the person in focus). Each is a new take; they are ranked like any takes and you keep one."""
        st = self.state(); p = self.plan_record(st, shot, take)
        if not p.get('workflow_file'): raise StudioError('PLAN_A_RETAKE_FIRST')
        who = next((l.get('speaker') for l in reversed(p['spec'].get('dialogue', [])) if l.get('speaker')), None) or p['spec'].get('subject') or (p['cast'] or [None])[0]
        name = st['characters'][who]['name'] if who in st['characters'] else None
        current = p['spec'].get('framing')
        wants = [w for w in (('wider', 'wide shot of both of them'), ('close_up', f'close-up on {name}' if name else None),
                             ('over_shoulder', f'over the shoulder on {name}' if name and len(p['cast']) > 1 else None)) if w[1] and w[0] != current][:2]
        bgm = p.get('background') or {}; out = [(shot, take)]
        for _, words in wants:
            q = self.plan(f"{p['text']}. {words}", mode='retake', target_shot=shot,
                          background=bgm.get('location') or ('people' if bgm.get('mode') == 'people' else None))
            if q.get('action') == 'PLANNED': out.append((q['shot'], q['take']))
        return out

    def retry_plan(self, shot, take, how):
        """After ComfyUI failed a take: 'same' = send the same plan again; 'fewer_refs' = a new take with at most 6 images;
        'no_clips' = a new take with still images only (both use less GPU memory). Returns (shot, take) to generate."""
        st = self.state(); p = self.plan_record(st, shot, take)
        if how == 'same':     # V0.9.25: once a take went to ComfyUI, "again" is a NEW take with a NEW seed (same plan otherwise):
                              # the same seed gives the same video back (ComfyUI may just return its cached copy)
            sent = any(x['shot'] == shot and x['take'] == take for x in st['submissions'])
            if not sent or not p.get('workflow_file'): return shot, take
            return shot, self.more_takes(shot, take, 2)[0]
        over = {'fewer_refs': {'max_refs': 6}, 'no_clips': {'video_refs': 'off'}}.get(how)
        if over is None: raise StudioError('RETRY_SAME_FEWER_REFS_NO_CLIPS')
        if p.get('extend') and how == 'no_clips':
            if not p.get('anchors'): raise StudioError('AN_EXTENSION_NEEDS_ITS_VIDEO_CLIP_TRY_FEWER_REFERENCES')
            over = {**over, 'extend_with': 'anchor'}          # keeps the pinned original ending, drops only the clip
        bgm = p.get('background') or {}
        q = self.plan(p['text'], mode='retake', target_shot=shot, _settings=over,
                      background=bgm.get('location') or ('people' if bgm.get('mode') == 'people' else None))
        if q.get('action') != 'PLANNED': raise StudioError('RETRY_COULD_NOT_PLAN')
        return q['shot'], q['take']

    def edit_prompt(self, shot, take, prompt):
        """Your own wording for a planned take: saved as a NEW take of the same shot (same references, clips, LoRAs,
        length and seed). Clean-Chain still applies: the references can't change and last-frame requests are refused."""
        import director_intent_v0739 as di
        st = self.state(); p = self.plan_record(st, shot, take)
        if not p.get('workflow_file'): raise StudioError('PLAN_A_RETAKE_FIRST')
        prompt = (prompt or '').strip()
        if not prompt: raise StudioError('PROMPT_EMPTY')
        if len(prompt) < 40: raise StudioError('PROMPT_TOO_SHORT: H3 needs at least 40 characters; describe the shot in a full sentence or two')
        if len(prompt) > MAX_PROMPT: raise StudioError(f'PROMPT_OVER_{MAX_PROMPT}_CHARACTERS: H3 takes 7000; the Director keeps a little room for continuity it adds before sending')
        bad = di.FORBIDDEN_VISUAL_SIMPLE.search(prompt)
        if bad: raise StudioError('CLEAN_CHAIN_REFUSED: ' + bad.group(0).strip()[:80] + ' (generated frames are never references)')
        t2 = len(st['shots'][shot]['plans']) + 1
        prefix = f"Studio_{st['project']['slug']}_S{shot:02d}_T{t2:02d}"
        g = build_graph([st['refs'][r]['input_name'] for r in p['refs']], prompt, p['seed'], prefix,
                        video_names=[st['refs'][r]['input_name'] for r in p.get('videos', [])],
                        video_audio=bool(p.get('videos')) and bool(st['settings'].get('voice_refs')) and not p.get('audios'),
                            audio_names=[st['refs'][r]['input_name'] for r in p.get('audios', [])], anchor_names=self._anchor_names(st, p), pose=p.get('pose'),
                        loras=[(l['name'], l['strength']) for l in p.get('loras', [])], length=int(p.get('frames', 124)), **(p.get('sampling') or {}))
        shas = {st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p['refs']}
        try:
            cg.validate_director_graph(g, shas, require_capture=False, length=int(p.get('frames', 124)),
                                       videos={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('videos', [])} or None,
                                       loras={l['name']: l['strength'] for l in p.get('loras', [])} or None,
                                       audios={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('audios', [])} or None,
                                   anchors={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('anchors', [])} or None, pose=self._pose_decl(p))
        except cg.GraphError as e: raise StudioError('EDITED_PROMPT_REFUSED: ' + str(e))
        text_wf = json.dumps(g, indent=2, ensure_ascii=False) + '\n'
        (self.root / 'plans' / f'{prefix}.json').write_text(text_wf)
        q = {**{k: v for k, v in p.items() if k != 'seq'}, 'take': t2, 'mode': 'edited', 'edited_from': take, 'prompt': prompt, 'prefix': prefix,
             'workflow_file': f'{prefix}.json', 'workflow_sha256': hashlib.sha256(text_wf.encode()).hexdigest()}
        self._append('SHOT_PLANNED', q)
        return {'action': 'PLANNED', **q}

    def plan_script(self, script, *, preview=False):
        """A dialogue script -> one naturally speakable shot per line (long lines split), planned as a storyboard."""
        import studio_script_v091 as sc
        st = self.state(); chars = [c for c in st['characters'].values() if not c.get('retired')]
        secs = h3_length(st['settings'].get('gen_length', 124)) / FMT['fps']
        budget = max(4, int((secs - 1.0) * WORDS_PER_SECOND))
        shots, problems = sc.parse(script, chars, budget)
        if problems: raise StudioError('SCRIPT: ' + ' | '.join(problems[:3]))
        if not shots: raise StudioError('SCRIPT_EMPTY: write lines like  Maya: How was it?')
        texts = sc.directions(shots, chars)
        if preview: return {'shots': len(texts), 'directions': texts, 'words_per_shot': budget}
        return {'shots': len(texts), 'directions': texts, 'words_per_shot': budget, 'plans': self.storyboard(texts)}

    def unsent_plans(self, st=None):
        """Latest plan of every shot that has no take yet and was never sent to ComfyUI."""
        st = st or self.state(); sent = {(x['shot'], x['take']) for x in st['submissions']}; out = []
        for n, sh in sorted(st['shots'].items()):
            if not sh['plans']: continue
            p = sh['plans'][-1]
            if sh['takes'] and not (p.get('replanned') and p['take'] > max(t['take'] for t in sh['takes'])): continue   # ✎ V0.9.50 re-planned
            if (n, p['take']) not in sent and p.get('workflow_file'): out.append((n, p['take']))
        return out

    def open_plan(self, st=None):
        """The newest plan whose take hasn't arrived yet (what the plan card shows), so a closed page can come back to it."""
        st = st or self.state()
        for e in reversed(st['events']):
            if e['kind'] != 'SHOT_PLANNED' or e['seq'] in {x['payload']['target'] for x in st['events'] if x['kind'] == 'UNDONE'}: continue
            p = e['payload']
            if any(t['take'] == p['take'] for t in st['shots'].get(p['shot'], {'takes': []})['takes']): return None
            sent = any(x['shot'] == p['shot'] and x['take'] == p['take'] for x in st['submissions'])
            return {'action': 'PLANNED', **p, 'sent': sent}
        return None

    _RANK_CACHE = {}
    def rank_model(self, st):
        """What your Keep/Reject decisions say you prefer (studio_rank_v0911): learned weights, how many comparisons
        they rest on, how much they count yet, and your habits in words. Starts from other projects' habits."""
        import studio_rank_v0911 as rk
        key = (str(self.root), st.get('tail'), bool(st['settings'].get('learn', True)))
        if key in Studio._RANK_CACHE: return Studio._RANK_CACHE[key]
        prior = dict(rk.PRIOR); n_prior = 0
        tdir = self.root.parent / '_taste'
        if tdir.is_dir():
            acc = {k: 0.0 for k in rk.NAMES}; tot = 0
            for f in tdir.glob('*.json'):
                if f.stem == self.slug: continue
                try: j = json.loads(f.read_text())
                except ValueError: continue
                if not j.get('rank_pairs') or not isinstance(j.get('rank_w'), dict): continue
                for k in rk.NAMES: acc[k] += float(j['rank_w'].get(k, rk.PRIOR[k])) * j['rank_pairs']
                tot += j['rank_pairs']
            if tot: prior = {k: (acc[k] / tot) * 0.7 + rk.PRIOR[k] * 0.3 for k in rk.NAMES}; n_prior = tot
        decisions = []
        if st['settings'].get('learn', True):
            for n, sh in st['shots'].items():
                kept = [t for t in sh['takes'] if t['status'] == 'ACCEPTED' and t.get('analysis')]
                if not kept: continue
                k = sorted(kept, key=lambda t: (t.get('reviewed_seq') or 0, t['take']))[-1]
                others = [t for t in sh['takes'] if t is not k and t.get('analysis') and t['status'] != 'ACCEPTED']
                if not others: continue
                try: decisions.append((k.get('reviewed_seq') or 0, n, self.take_features(st, n, k), [(self.take_features(st, n, o), o['status']) for o in others]))
                except Exception: continue
        decisions = [d[1:] for d in sorted(decisions, key=lambda d: d[0])]            # oldest first: recent choices weigh more
        pairs = rk.pairs_from(decisions)
        w = rk.learn(pairs, prior)
        ev = rk.evidence(pairs) + n_prior
        agree = rk.agreement(decisions, prior) if decisions else None
        m = {'w': w, 'prior': prior, 'pairs': len(pairs), 'decisions': len(decisions), 'pairs_other_projects': n_prior, 'trust': rk.trust(ev),
             'habits': rk.habits(w) if rk.trust(ev) > 0 else [], 'min_pairs': rk.MIN_PAIRS,
             'agreement': {'agreed': agree[0], 'out_of': agree[1]} if agree else None}
        Studio._RANK_CACHE = {kk: v for kk, v in Studio._RANK_CACHE.items() if kk[0] != str(self.root)}; Studio._RANK_CACHE[key] = m
        return m

    _LOOK_BY_SHA = {}
    # -- did it do what I asked? (Qwen looks; a hint, never an approval) ---------------------------------------------
    def pending_checks(self, st=None, limit=12):
        """Measured, unrejected takes made from a plan with actions and not checked yet: [(shot, take, points, people)]."""
        import studio_check_v0915 as ck
        st = st or self.state(); out = []
        if not st['settings'].get('direction_check', True) or st['settings'].get('qwen', 'manual') == 'off': return out
        names = {c['id']: c['name'] for c in st['characters'].values()}
        for n, sh in sorted(st['shots'].items(), reverse=True):                 # newest shots first
            for t in sh['takes']:
                if t.get('source') or t['status'] == 'REJECTED' or not t.get('analysis') or t.get('check') or not t.get('sha256'): continue
                p = next((q for q in sh['plans'] if q['take'] == t['take']), None)
                if not p or not (p.get('spec') or {}).get('actions'): continue
                pts = ck.points(p['spec']['actions'], names)
                if pts: out.append((n, t['take'], pts, [names[c] for c in p.get('cast', []) if c in names]))
                if len(out) >= limit: return out
        return out

    def check_requests(self, todo, frames_dir):
        """Frames of each take (small JPEGs in frames_dir) and Qwen's question: [{'key', 'request', 'points', 'sha256'}]."""
        import studio_check_v0915 as ck
        st = self.state(); jobs = []
        for n, tk, pts, people in todo:
            t = next((x for x in st['shots'][n]['takes'] if x['take'] == tk), None)
            if not t: continue
            f = self.take_path(t)
            if not same_file(f, t['sha256']): continue
            d = Path(frames_dir) / f'{n}_{tk}'; d.mkdir(parents=True, exist_ok=True)
            p = next((q for q in st['shots'][n]['plans'] if q['take'] == tk), None) or {}
            fr = ck.frames_of(f, d, start=float((p.get('spec') or {}).get('lead_in') or 0))     # ⏱ V0.9.48 not the lead-in
            import studio_qwenmodel_v0916 as qm
            mdl = qm.model_id(st['settings'].get('qwen_model'), 'DIRECTOR_CHECK_MODEL')
            before = None; sense = []
            if p.get('film') and st['settings'].get('film_check', True):     # 🎬 V0.9.36: does it make sense in the film?
                before = None if (p.get('start_frame') and not p.get('carry_frame')) else self._film_before(st, p, d)   # V0.9.38 🎞: a new start on your picture: nothing to carry on from
                loc = st['locations'].get((p.get('background') or {}).get('location') or p.get('location') or '')
                sense = ck.film_points(people, place=(loc or {}).get('name'), has_before=bool(before), join=p.get('film_join'),
                                       hidden=(p.get('spec') or {}).get('hide_faces') or ())
            if len(fr) >= 3: jobs.append({'key': f'{n}:{tk}', 'request': ck.request(fr, pts + sense, people, model=mdl, before=before),
                                          'points': pts + sense, 'sense_from': len(pts) if sense else None, 'sha256': t['sha256'], 'model': mdl,
                                          'times': [round(float(x), 2) for x, _ in fr]})
        return jobs

    def _film_before(self, st, p, out_dir):
        """🎬 The last moment of the film's previous shot as it is in your movie (its take's frame, as a small JPEG for Qwen's
        check only; never a reference for H3), or None."""
        import studio_rewrite_v0913 as rw
        want = int(p.get('film_shot') or 0) - 1
        if want < 1: return None
        prev = next((n for n, sh in st['shots'].items() for q in sh['plans'] if q.get('film') == p['film'] and q.get('film_shot') == want), None)
        if prev is None: return None
        try: tl = self.timeline(st)
        except Exception: return None
        c = next((x for x in tl if x['shot'] == prev), None)
        t = c and next((x for x in st['shots'][prev]['takes'] if x['take'] == c['take']), None)
        if not t: return None
        bd = Path(out_dir) / 'before'; bd.mkdir(parents=True, exist_ok=True)
        got = rw.prepare_pictures([{'label': 'before', 'path': str(self.take_path(t)), 'frame': max(0, int(c['out']) - 1)}], bd)
        return str(got[0][1]) if got else None

    def record_checks(self, jobs, replies, model=None):
        """Qwen's answers -> TAKE_CHECKED (only for the exact file it looked at)."""
        import studio_check_v0915 as ck
        done = 0
        for j in jobs:
            if j['key'] not in replies: continue
            n, tk = (int(x) for x in j['key'].split(':'))
            pts = ck.read_reply(replies[j['key']], j['points'], times=j.get('times'), need_frame=j.get('sense_from'))   # 🔍 V0.9.55 a yes needs a frame
            if j.get('sense_from') is not None:
                for i, x in enumerate(pts): x['sense'] = i >= j['sense_from']        # 🎬 the film points, judged apart from your actions
            self._append('TAKE_CHECKED', {'shot': n, 'take': tk, 'sha256': j['sha256'], 'points': pts,
                                          'model': model or j.get('model') or ck.MODEL_ID, 'unverified': True}); done += 1
        return done

    def take_features(self, st, shot, t):
        """The measured features the ranking learns from: the take's measurements, whether its shot has lines (so talk is
        judged against the shot) and its look against the shot before (only when already measured; else neutral)."""
        import studio_rank_v0911 as rk
        p = next((q for q in st['shots'][shot]['plans'] if q['take'] == t['take']), None) or {}
        dia = bool((p.get('spec') or {}).get('dialogue')) if p.get('spec') is not None else None
        if dia is False and st['settings'].get('speech', SPEECH_DEFAULT) == 'natural': dia = None     # natural talk allowed: neutral
        return rk.features(self._analysis(t), t['analysis'], dialogue=dia, look=Studio._LOOK_BY_SHA.get(t.get('sha256')))

    def rank_score(self, st, t, shot=None):
        """Built-in measured score, plus (once you've made enough decisions) what you usually keep."""
        import studio_rank_v0911 as rk
        base = take_score(t['analysis'])[0]
        import studio_check_v0915 as ck
        asked = ck.score(t.get('check'))
        if asked is not None: base += 0.3 * (asked - 0.5)          # did what you asked (Qwen's look: a nudge, never decisive)
        if ck.makes_sense(t.get('check')) is False: base -= 0.4      # 🎬 an extra person / wrong place / a jump: the in-order run won't build on it
        m = self.rank_model(st)
        if not m['trust']: return base
        try:
            shot = shot if shot is not None else next(n for n, sh in st['shots'].items() if any(x is t or (x['take'] == t['take'] and x.get('sha256') == t.get('sha256')) for x in sh['takes']))
            f = self.take_features(st, shot, t)
        except Exception: return base
        return base + m['trust'] * (rk.score(f, m['w']) - rk.score(f, rk.PRIOR))

    def rank_takes(self, st, shot):
        """Ranking of a shot's unrejected takes (best first): measured, and shaped by what you usually keep once there
        are enough of your decisions. Never an approval: until you keep one, the top take only stands in."""
        import studio_rank_v0911 as rk
        m = self.rank_model(st); res = []
        for t in st['shots'][shot]['takes']:
            if t['status'] == 'REJECTED' or not t.get('analysis') or t.get('analysis_error') in ('TAKE_FILE_MISSING', 'TAKE_FILE_CHANGED'): continue
            res.append((self.rank_score(st, t, shot), take_score(t['analysis'])[1], t))
        res.sort(key=lambda x: -x[0])
        out = [{'take': t['take'], 'score': round(sc, 3), 'why': why} for sc, why, t in res]
        if m['trust'] and len(res) >= 2:
            try:
                fb, fo = self.take_features(st, shot, res[0][2]), self.take_features(st, shot, res[1][2])
                learned = rk.explain(fb, fo, m['w'])
                if learned: out[0]['learned_why'] = [f'{x} (you usually keep that)' for x in learned]
            except Exception: pass
        return out

    def story(self, st, before=None, tl=None):
        """What the Director remembers of the film so far, in movie order: every earlier shot that made it into
        the film (or was imported as history), with your direction, your notes and what was MEASURED at its end.
        Text only: generated frames are never used as visual references (Clean-Chain)."""
        order = [n for n in (st['order'] or sorted(st['shots'])) if n in st['shots']]
        order += [n for n in sorted(st['shots']) if n not in order]
        for m in sorted(st.get('memory', {})):                         # history-only shots sit at their number
            if m in st['shots']: continue
            k = next((i for i, n in enumerate(order) if n > m), len(order)); order.insert(k, m)
        if before is not None:
            order = order[:order.index(before)] if before in order else [n for n in order if n < before]
        out = []
        try: in_movie = {c['shot']: c for c in (tl if tl is not None else self.timeline(st))}
        except Exception: in_movie = {}
        # a planned shot that was never generated only counts when it comes after the last shot you actually generated
        # (it's part of what you're planning now); older leftovers that the film has moved past are not remembered
        filmed = [i for i, n in enumerate(order) if n in st['shots'] and st['shots'][n]['takes']]
        last_filmed = filmed[-1] if filmed else -1
        for i_, n in enumerate(order):
            if n in st['excluded']: continue
            if n in st['shots'] and not st['shots'][n]['takes'] and i_ < last_filmed: continue
            if n in st['shots']:
                sh = st['shots'][n]
                clip = in_movie.get(n)
                t = (next((t for t in sh['takes'] if clip and t['take'] == clip['take']), None) or
                     next((t for t in sorted(sh['takes'], key=lambda t: t.get('reviewed_seq') or 0, reverse=True) if t['status'] == 'ACCEPTED'), None) or
                     next((t for t in reversed(sh['takes']) if t['status'] != 'REJECTED'), None))
                if t is None and not sh['takes'] and sh['plans']:       # planned (storyboard) but not filmed yet
                    p = sh['plans'][-1]
                    out.append({'shot': n, 'take': None, 'text': p['text'], 'note': None, 'accepted': False, 'planned': True,
                                'cast': [st['characters'][c]['name'] for c in p.get('cast', []) if c in st['characters']],
                                'location': (st['locations'].get(p.get('location')) or {}).get('name'), 'end_people': None})
                    continue
                if t is None: continue                                  # every take rejected
                p = next((q for q in sh['plans'] if q['take'] == t['take']), sh['plans'][-1])
                es = (t.get('analysis') or {}).get('end_state') or {}
                at_cut = None
                if t.get('analysis'):
                    try:
                        out_f = (clip or {}).get('out') or es.get('frames', [0, t['analysis']['frames']])[-1] + 1
                        cast_ids = [c for c in p.get('cast', []) if c in st['characters']]
                        at_cut = end_at_cut(self._analysis(t), out_f, (t['analysis'] or {}).get('identity'),
                                            {c: st['characters'][c]['name'] for c in cast_ids}, cast_ids,
                                            sides=None)
                    except Exception: at_cut = None
                out.append({'shot': n, 'take': t['take'], 'text': p['text'], 'note': t.get('note'), 'source': bool(t.get('source')),
                            'cast': [st['characters'][c]['name'] for c in p.get('cast', []) if c in st['characters']],
                            'cast_ids': list(p.get('cast', [])), 'location_id': p.get('location'), 'background': p.get('background'),
                            'location': (st['locations'].get(p.get('location')) or {}).get('name'),
                            'end_people': es.get('face_count_mode'), 'accepted': t['status'] == 'ACCEPTED', 'at_cut': at_cut,
                            'remember': list(t.get('remember') or []), '_cut': (t, out_f, cast_ids) if at_cut else None})
            else:
                m = st['memory'][n]
                out.append({'shot': n, 'take': None, 'text': m['text'], 'note': m.get('note'), 'cast': m.get('cast', []),
                            'location': m.get('location'), 'end_people': None, 'accepted': True, 'history_only': True})
        if out and out[-1].get('_cut'):                    # V0.9.37 📍 the shot the next one continues from: sides AT its cut
            t, out_f, cids = out[-1]['_cut']
            if len(cids) >= 2:
                try:
                    sides = self._sides_at_cut(st, t, cids, out_f)
                    if sides: out[-1]['at_cut'] = {**out[-1]['at_cut'], 'left_to_right': sides, 'sides': 'RECOGNISED_AT_THE_CUT'}
                except Exception: pass
        for e in out: e.pop('_cut', None)
        return out

    STORY_CHARS = 900

    def continuity_text(self, st, shot, cast, opens_on=False, join=None, lead=None):
        """Text-only continuity for H3: STATE that carries over, never earlier ACTIONS. H3 can't tell "this happened
        before" from "do this now", so an earlier "she fixes her hair" in the prompt gets acted out again. What goes in:
        the measured state where the movie cuts (who is there, where, which way they move), your notes on the previous
        take and the things you pinned with Remember. The Director keeps the whole story itself (story(), the plan card,
        its checks) and never hands it to H3. Never a frame, never a clip."""
        story = self.story(st, before=shot)
        if not story: return ''
        prev = story[-1]
        parts = []
        here = {st['characters'][c]['name'] for c in cast if c in st['characters']}
        carried = [x for x in lasting_state([(e['text'], list(e.get('cast') or [])) for e in story], {n: n for n in here}) if x.split(' is ')[0] in here]
        if carried:                                        # lasting changes only, as state: "Maya is wearing the red hat"
            parts.append('Already true at the start of this shot (from earlier shots; not an action to perform): ' + '; '.join(carried) + '.')
        if prev.get('note') and not prev.get('source'):          # (your own video's label is not a note)
            parts.append(f"The filmmaker noted about the previous shot: {unquote(action_without_lines(prev['note'])[:200]).rstrip('.')}.")
        for r in (prev.get('remember') or [])[-3:]: parts.append(f"Keep in mind from the previous shot: {unquote(r[:160]).rstrip('.')}.")
        names = {c: st['characters'][c]['name'] for c in cast if c in st['characters']}
        same_scene = self._same_scene(st, prev, shot)
        cut = prev.get('at_cut')
        if opens_on and lead and join == 'carry' and cut and same_scene and len(cut.get('left_to_right') or []) >= 2:
            # ⏱ V0.9.48 🔗 opens on your picture with a lead-in: the lead-in takes everyone to where the shot before ended
            parts.append(f"By the end of the first {float(lead):g} seconds everyone is where the previous shot ended: "
                         f"{cut['left_to_right'][0]} on the left and {cut['left_to_right'][-1]} on the right.")
        elif opens_on or join == 'skip': pass              # 🎞 it opens on YOUR picture / ⏩ time has passed: people start where this shot says
        elif join == 'cut':                                # V0.9.44 ✂ a cut: only which side of the screen everyone is on carries over
            if cut and same_scene and len(cut.get('left_to_right') or []) >= 2:
                parts.append(f"Everyone keeps the same side of the screen as in the shot before: {cut['left_to_right'][0]} on the left and "
                             f"{cut['left_to_right'][-1]} on the right.")
        elif cut and same_scene:
            parts.append(continuity_sentence(cut, names))
        elif prev.get('end_people') and len(cast) >= 2 and prev['end_people'] >= 2:
            parts.append('Keep the same screen positions as the previous shot (left/right), so screen direction is preserved.')
        return ' '.join(parts)

    UNKNOWN_START = ("The previous shot (shot {n}) is not back from generating yet, so where everyone is at the very start of "
                     "this shot is NOT known yet: do not say where anyone starts, stands or faces at the beginning; describe what "
                     "happens and where people end up. The Director adds the starting positions from how shot {n} really ends, "
                     "just before this shot is sent.")

    def positions_from(self, st, shot):
        """V0.9.37 📍 Where this shot's starting positions come from: {'shot', 'take', 'measured'} = measured where the movie
        cuts that take; or {'pending': n} = the shot right before it (shot n) isn't back yet, or a newer take of it is still
        generating, so they are NOT known yet (nothing older is used: positions of a shot two back would be wrong). None =
        the first shot (nothing before it)."""
        story = self.story(st, before=shot)
        if not story: return None
        prev = story[-1]; n = prev.get('shot')
        if prev.get('take') is None and prev.get('planned'): return {'pending': n}
        if n in st['shots']:
            sh = st['shots'][n]; have = {t['take'] for t in sh['takes']}
            sent = {x['take'] for x in st['submissions'] if x['shot'] == n}
            newest = max((p['take'] for p in sh['plans']), default=None)
            if newest is not None and newest in sent and newest not in have and newest > (prev.get('take') or 0):
                return {'pending': n, 'newer_take': newest, 'shot': n, 'take': prev.get('take')}
        if prev.get('take') is None: return None
        m = bool(prev.get('at_cut')); return {'shot': n, 'take': prev['take'], 'measured': m, 'measured_at_cut': m}

    def _starts_from(self, st, shot, start_frame=None):
        """📍 for the plan card: {'opens_on': file} when the shot opens on your picture (V0.9.38), else positions_from()."""
        if start_frame and start_frame in st['refs']: return {'opens_on': st['refs'][start_frame]['file']}
        return self.positions_from(st, shot)

    def _same_scene(self, st, prev, shot):
        """Is the shot being planned in the same place as the previous one (so positions and movement carry over)?"""
        last = (st['shots'].get(shot) or {}).get('plans') or []
        if not last: return True                      # a new shot: assume the scene continues unless a new place is named
        p = last[-1]; a, b = prev.get('background') or {}, p.get('background') or {}
        if a.get('mode') == 'place' and b.get('mode') == 'place': return a.get('location') == b.get('location')
        return True

    def refresh_continuity(self, shot, take):
        """Rewrite an UNSENT plan's continuity with what is known now (how the previous shot actually ended, measured
        where the movie cuts, plus your notes). Same direction, references and seed; only the text changes.
        Returns True when the prompt changed."""
        st = self.state(); p = self.plan_record(st, shot, take)
        if any(x['shot'] == shot and x['take'] == take for x in st['submissions']) or any(t['take'] == take for t in st['shots'][shot]['takes']):
            raise StudioError('ALREADY_SENT_CANNOT_CHANGE')
        if not p.get('workflow_file') or p.get('mode') in ('edited', 'source') or 'continuity' not in p: return False
        new = self.continuity_text(st, shot, p['cast'], opens_on=bool(p.get('start_frame')) or bool(((p.get('spec') or {}).get('positions') or {}).get('start')), join=p.get('film_join'),
                                   lead=(p.get('spec') or {}).get('lead_in'))
        if p.get('scene_context'): new = (new + ' ' + p['scene_context']).strip()
        if new == p.get('continuity', ''): return False
        prompt = self._compose(st, p['spec'], p['cast'], p.get('location'), p['refs'], new, p.get('lessons_used'), p.get('videos', []),
                               p.get('background'), int(p.get('frames', 124)), p.get('loras', []), extend=p.get('extend'), audios=p.get('audios', []),
                               start=bool(p.get('start_frame')), end=bool(p.get('end_frame')))
        prompt = self._with_note(self._with_note(self._spec_notes(prompt, p['spec']), p.get('frame_note')), p.get('pose_note'))
        if p.get('pose'): prompt = self._no_lines(prompt)      # V0.9.30: it was lost when continuity was refreshed
        if len(prompt) > 7000: return False
        length = int(p.get('frames', 124))
        g = build_graph([st['refs'][r]['input_name'] for r in p['refs']], prompt, p['seed'], p['prefix'],
                        video_names=[st['refs'][r]['input_name'] for r in p.get('videos', [])],
                        video_audio=bool(p.get('videos')) and bool(st['settings'].get('voice_refs')) and not p.get('audios'),
                            audio_names=[st['refs'][r]['input_name'] for r in p.get('audios', [])], anchor_names=self._anchor_names(st, p), pose=p.get('pose'),
                        loras=[(l['name'], l['strength']) for l in p.get('loras', [])], length=length, **(p.get('sampling') or {}))
        shas = {st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p['refs']}
        cg.validate_director_graph(g, shas, require_capture=False, length=length,
                                   videos={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('videos', [])} or None,
                                   loras={l['name']: l['strength'] for l in p.get('loras', [])} or None,
                                   audios={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('audios', [])} or None,
                                   anchors={st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('anchors', [])} or None, pose=self._pose_decl(p))
        text_wf = json.dumps(g, indent=2, ensure_ascii=False) + '\n'
        (self.root / 'plans' / p['workflow_file']).write_text(text_wf)
        self._append('PLAN_REFRESHED', {'shot': shot, 'take': take, 'prompt': prompt, 'continuity': new,
                     'workflow_sha256': hashlib.sha256(text_wf.encode()).hexdigest(),
                     'continuity_from': self._starts_from(st, shot, p.get('start_frame'))}, st['tail'])
        return True

    def remember(self, shot, take, text):
        """Something you want the next shots to keep (e.g. a Qwen observation you checked, "she is holding the cup").
        Your words, so they are carried as your direction, never as measured truth."""
        st = self.state(); self._take(st, shot, take)
        text = ' '.join(str(text or '').split())[:200]
        if not text: raise StudioError('NOTHING_TO_REMEMBER')
        import director_intent_v0739 as di
        if di.FORBIDDEN_VISUAL_SIMPLE.search(text): raise StudioError('CLEAN_CHAIN_REFUSED')
        self._append('TAKE_REMEMBER', {'shot': shot, 'take': take, 'text': text})

    # -- storyboard in order: each shot continues from how the previous one actually ended --------------------
    OVERNIGHT_RETRIES = 1          # a shot whose takes all failed is tried once more with a new seed, then the night goes on
    OVERNIGHT_TIMEOUT = 45 * 60    # a take that never arrives (ComfyUI error, crash) counts as failed after this long

    def start_chain(self, only=None, preview=False, review=False, takes=1, overnight=False):
        """Generate planned shots in order. only = the shot numbers you chose (e.g. one scene); otherwise every planned
        shot that was never sent. preview=True lists them without sending anything."""
        st = self.state(); todo = [[n, t] for n, t in self.unsent_plans(st) if only is None or n in set(int(x) for x in only)]
        if preview: return todo
        if not todo: raise StudioError('NOTHING_PLANNED_TO_GENERATE')
        takes = int(takes or 1)
        if not 1 <= takes <= 4: raise StudioError('TAKES_PER_SHOT_1_TO_4')
        if overnight: review = False                   # nobody is there to ✓ Keep: the best-ranked take stands in, you choose in the morning
        self._append('CHAIN_REQUESTED', {'shots': todo, 'authorized_by': 'EXPLICIT_USER_ACTION', 'review': bool(review),
                                         'takes': takes, 'overnight': bool(overnight), 'started_at': int(time.time())})
        return todo

    def set_chain_review(self, review):
        """V0.9.38: tick or untick "Wait for my ✓ Keep after each shot" on the RUNNING in-order run (no restart). An
        overnight run never waits (nobody is there to keep a take). -> the setting now, or None (no run)."""
        ch = self.state().get('chain')
        if not ch: return None
        review = bool(review) and not ch.get('overnight')
        if bool(ch.get('review')) != review: self._append('CHAIN_REVIEW', {'review': review, 'authorized_by': 'EXPLICIT_USER_ACTION'})
        return review

    def stop_chain(self):
        if self.state().get('chain'): self._append('CHAIN_STOPPED', {})

    def chain_step(self, url, opener=None):
        """Send the next shot of an in-order storyboard once the shot before it has arrived and been measured.
        With review on ("wait for my OK"), the next shot only goes once YOU kept a take of the one before, and it continues
        from that take; a rejected or undecided take keeps it waiting (make a New take for another try).
        Returns 'sent', 'waiting', 'waiting_for_you', 'waiting_for_check' (overnight 🔍, V0.9.21), 'done' or None (no chain)."""
        st = self.state(); ch = st.get('chain')
        if not ch: return None
        sent = {(x['shot'], x['take']) for x in st['submissions']}
        prev = None
        for n, t in ch['shots']:
            # your newest wording of the shot goes (an edited prompt / retake is a newer take of the same shot)
            newer = [q['take'] for q in (st['shots'].get(n) or {'plans': []})['plans'] if q['take'] >= t and q.get('workflow_file')
                     and (n, q['take']) not in st.get('frame_no', set())]                # 🔗 not a built frame you said no to
            if any((n, x) in sent for x in newer) or (n, t) in sent:
                prev = (n, t); continue
            t = max(newer) if newer else t
            if prev is not None and ch.get('overnight') and prev[0] not in (ch.get('skipped') or []):
                w = self._overnight_wait(st, ch, prev, url, opener)
                if w: return w
            elif prev is not None:
                pt = [x for x in st['shots'].get(prev[0], {'takes': []})['takes'] if x['status'] != 'REJECTED']
                if pt and all(x.get('analysis_error') == 'BROKEN_TAKE' for x in pt): return 'waiting_for_you'   # H3 failed: needs a new take from you
                if not pt or not any(x['analysis'] for x in pt): return 'waiting'     # the previous shot isn't back (or measured) yet
                if ch.get('review') and not any(x['status'] == 'ACCEPTED' and x['analysis'] for x in pt):
                    return 'waiting_for_you'                                          # your OK: keep a take of the shot before
                if not ch.get('review') and self._is_film_shot(st, prev[0]):
                    w = self._film_check(st, ch, prev[0], [x for x in pt if x['analysis']], prev, url, opener)
                    if w: return w
            if n not in st['shots'] or st['shots'][n]['takes']: prev = (n, t); continue
            w = self._film_continuity_wait(st, n, t)            # V0.9.37 🔗 Qwen rewrites the shot from how the one before really ends
            if w: return w
            t = self.carry_frame(n, t) or t                     # V0.9.60 🔗 opens on a frame built from where the shot before ends
            w = self.carry_redraw(n, t, url, opener)            # 🧪 ... redrawn in the poses of the cut (experiment, off by default)
            if isinstance(w, str): return w
            if w: t = w
            if self.state()['settings'].get('carry_ok') and self._frame_needs_ok(n, t): return 'waiting_for_frame'   # V0.9.61 👀 your OK first
            try: self.refresh_continuity(n, t)
            except StudioError: pass
            self.generate(n, t, url=url, opener=opener)
            for t2 in (self.more_takes(n, t, ch.get('takes', 1)) if ch.get('takes', 1) > 1 else []):   # more takes, different seeds
                self.generate(n, t2, url=url, opener=opener)
            return 'sent'
        if ch.get('overnight') and prev is not None and prev[0] not in (ch.get('skipped') or []):
            w = self._overnight_wait(self.state(), self.state()['chain'], prev, url, opener)     # the last shot gets its retry too
            if w: return w
        self._append('CHAIN_FINISHED', {}); return 'done'

    def _overnight_wait(self, st, ch, prev, url, opener):
        """Overnight: the shot before must be completely back (every take sent for it arrived and measured, or failed).
        If every take failed (black/NaN, ComfyUI error, never arrived), one more take with a new seed; after that the night
        moves on (the shot is marked for you). Returns 'waiting' / 'sent' (a retry) or None (go on)."""
        n = prev[0]; now = time.time()
        subs = [x for x in st['submissions'] if x['shot'] == n]
        takes = {t['take']: t for t in st['shots'].get(n, {'takes': []})['takes']}
        def failed_in_comfy(x):                         # ComfyUI says this prompt errored (out of memory, a node error...)
            if not x.get('prompt_id'): return False
            from urllib.request import urlopen
            try:
                with (opener or urlopen)(url.rstrip('/') + '/history/' + x['prompt_id'], timeout=10) as r: h = json.load(r)
                return ((h.get(x['prompt_id']) or {}).get('status') or {}).get('status_str') == 'error'
            except Exception: return False
        pending = [x for x in subs if x['take'] not in takes and now - float(x.get('at') or now) < self.OVERNIGHT_TIMEOUT and not failed_in_comfy(x)]
        if pending: return 'waiting'
        live = [t for t in takes.values() if t['status'] != 'REJECTED']
        if any(t['analysis'] is None and t.get('analysis_error') != 'BROKEN_TAKE' for t in live): return 'waiting'   # still measuring
        import studio_pose_v0921 as _ps                     # 🎭 a take with stick-figure lines in it counts as failed here
        good = [t for t in live if t['analysis'] and not _ps.lines_flagged(t['analysis'].get('pose_lines'))]
        if good:
            if self._is_film_shot(st, n): return self._film_check(st, ch, n, good, prev, url, opener)
            return self._night_check(st, ch, n, good, subs, prev, url, opener) if st['settings'].get('night_check_retry') else None
        tries = ch.get('retries', {}).get(str(n), 0)
        base = max((x['take'] for x in subs), default=prev[1])
        if tries < self.OVERNIGHT_RETRIES:
            t2 = self.more_takes(n, base, 2)[0]
            why = ('every take of this shot showed stick-figure lines' if live and all(t['analysis'] for t in live) else
                   'every take of this shot failed (broken video, ComfyUI error or never arrived)')
            self._append('CHAIN_RETRY', {'shot': n, 'take': t2, 'why': why})
            self.generate(n, t2, url=url, opener=opener)
            return 'sent'
        self._append('CHAIN_SKIPPED', {'shot': n, 'why': 'still no usable take after a retry: left for you, the night went on'})
        return None

    CONTINUITY_WAIT = 25 * 60      # 🔗 Qwen gets this long to rewrite a film shot from the one before; then it goes as planned
    def _film_continuity_need(self, st, n, t):
        """🔗 V0.9.37: does this unsent film shot still need Qwen's continuity pass? -> the job, or None."""
        if not st['settings'].get('film_continuity', True): return None
        try: p = self.plan_record(st, n, t)
        except StudioError: return None
        if not p.get('film') or p.get('continued_from') or f'{n}:{t}' in (st.get('continuity_done') or {}): return None
        if p.get('start_frame'): return None                  # V0.9.38 🎞: it opens on your picture; nothing to carry on from
        if p.get('film_join') in ('cut', 'skip'): return None # V0.9.44 ✂ a cut / ⏩ time skip starts on its own action: nothing to carry on
        if ((p.get('spec') or {}).get('positions') or {}).get('start'): return None   # 📍 V0.9.45 you set where everyone starts
        if any(x['shot'] == n and x['take'] == t for x in st['submissions']): return None
        pf = self.positions_from(st, n)
        known = bool(pf and pf.get('pending') is None and pf.get('shot') is not None)
        ap = p.get('film_action_photo')
        if ap and (ap not in st['action_photos'] or st['action_photos'][ap].get('removed')): ap = None
        if not known and not ap: return None                   # the first shot with no 📷: nothing to carry on from
        names = [st['characters'][c]['name'] for c in p.get('cast', []) if c in st['characters']]
        return {'shot': n, 'take': t, 'text': p.get('film_direction') or p['text'], 'people': names, 'framing': p.get('film_framing'),
                'action_photo': ap, 'prev': [pf['shot'], pf['take']] if known else None}

    def _film_continuity_wait(self, st, n, t):
        need = self._film_continuity_need(st, n, t)
        if not need: return None
        ready = bool(os.environ.get('DIRECTOR_REWRITE_FAKE'))
        if not ready:
            try: ready = bool(self.qwen_ready()['ready'])
            except Exception: ready = False
        if not ready: return None                             # no Qwen here: the shot goes with the measured continuity only
        t0 = self._NIGHT_WAIT.setdefault((str(self.root), 'continuity', n, t), time.time())
        if time.time() - t0 < self.CONTINUITY_WAIT: return 'waiting_for_continuity'
        self._append('FILM_CONTINUITY', {'shot': n, 'take': t, 'new_take': None, 'prev': need['prev'],
                                         'why': "🔗 Qwen didn't get to rewrite this shot from the one before in time: it goes as planned, with the measured positions"})
        return None

    def continuity_job(self):
        """🔗 The film shot of the running in-order run that is waiting for Qwen's continuity pass (or None)."""
        st = self.state(); ch = st.get('chain')
        if not ch: return None
        for n, t in ch['shots']:
            if n in st['shots'] and not st['shots'][n]['takes'] and not any(x['shot'] == n and x['take'] == t for x in st['submissions']):
                return self._film_continuity_need(st, n, t)
        return None

    def continuity_failed(self, job, why):
        st = self.state()
        if f"{job['shot']}:{job['take']}" in (st.get('continuity_done') or {}): return
        self._append('FILM_CONTINUITY', {'shot': job['shot'], 'take': job['take'], 'new_take': None, 'prev': job.get('prev'),
                                         'why': f"🔗 Qwen couldn't rewrite this shot from the one before ({str(why)[:140]}): it goes as planned, with the measured positions"})

    def apply_continuity(self, job, res):
        """🔗 Qwen's rewrite of a film shot from how the shot before REALLY ends (and its 📷 action photo) -> a new take of
        that shot (same seed, people, place and length) that the in-order run sends instead. Checked like every rewrite;
        nothing is kept or rejected. If anything changed meanwhile (the shot was sent, the shot before has another take in
        the movie now) it is dropped and the pass runs again."""
        import studio_film_v0936 as fm
        st = self.state(); n, t = job['shot'], job['take']
        now = self._film_continuity_need(st, n, t)
        if not now or now.get('prev') != job.get('prev'): return None
        p = self.plan_record(st, n, t)
        names = job['people']; d = ' '.join(str(res.get('direction') or '').split())
        if len(d) < 8: return self.continuity_failed(job, 'an empty rewrite')
        extra = {k: p[k] for k in ('film', 'film_shot', 'film_title', 'film_framing', 'film_action_photo', 'scene_context') if p.get(k) is not None}
        try:
            q = self.plan(f"{fm.lead(job.get('framing') or 'medium', names)}. {d}", mode='revise', target_shot=n, people=names or None,
                          background=p.get('location') or None, duration=(p.get('spec') or {}).get('duration'), pose='none', _seed=p.get('seed'),
                          _extra={**extra, 'written_by': 'qwen_continuity', 'film_direction': d[:2800], 'continued_from': job.get('prev'),
                                  'continuity_original': (p.get('film_direction') or '')[:2800]})
        except StudioError as e: return self.continuity_failed(job, e)
        if q.get('action') != 'PLANNED': return self.continuity_failed(job, q.get('question') or 'the Director could not plan it')
        src = (f"how shot {job['prev'][0]} take {job['prev'][1]} really ends" if job.get('prev') else 'nothing before it')
        self._append('FILM_CONTINUITY', {'shot': n, 'take': t, 'new_take': q['take'], 'prev': job.get('prev'),
                                         'why': f'🔗 Qwen rewrote this shot from {src}' + (' and your 📷 action photo' if job.get('action_photo') else '') +
                                                f": sent as take {q['take']} (take {t} as Qwen first wrote it stays on the card)"})
        return q

    FILM_CHECK_BELOW = 0.6         # 🎬 a film shot needs a take with at least this share of its actions seen ...
    FILM_CHECK_RETRIES = 2         # ... that also makes sense in the film; else up to this many more takes, then the run goes on
    def _is_film_shot(self, st, n):
        if not st['settings'].get('film_check', True): return False
        return any(q.get('film') for q in (st['shots'].get(n) or {'plans': []})['plans'])

    def _film_check(self, st, ch, n, live, prev, url, opener):
        """🎬 V0.9.36: after every take of a film shot, before the next shot goes, Qwen checks the takes: did they do what the
        shot says, with the right people, in the right place, carrying on from the shot before? If no take does, another take
        with a new seed (up to FILM_CHECK_RETRIES), then the run goes on with the best one and tells you. Nothing is kept or
        rejected: the best-checked take stands in, and you choose. Returns 'waiting_for_check', 'sent' or None (go on)."""
        import studio_check_v0915 as ck
        usable = [t for t in live if t['analysis'] and t.get('analysis_error') != 'BROKEN_TAKE']
        if not usable: return None
        can = {tk for n2, tk, _, _ in self.pending_checks(st, limit=40) if n2 == n}
        unchecked = [t for t in usable if not t.get('check') and t['take'] in can]
        ready = bool(os.environ.get('DIRECTOR_CHECK_FAKE'))
        if not ready:
            try: ready = bool(self.qwen_ready()['ready'])
            except Exception: ready = False
        if unchecked and ready:
            t0 = self._NIGHT_WAIT.setdefault((str(self.root), n, len(usable)), time.time())
            return 'waiting_for_check' if time.time() - t0 < self.NIGHT_CHECK_WAIT else None
        def swapped(t):                    # V0.9.37: measured (faces recognised), no Qwen needed: it starts on the other sides
            return any(str(c).startswith('SCREEN_SIDES') for c in (t.get('analysis') or {}).get('checks') or [])
        def misplaced(t):                  # 📐 V0.9.69 measured against your 📍 dots: someone is clearly not where the shot asked
            return next((str(c)[len('POSITIONS: '):] for c in (t.get('analysis') or {}).get('checks') or []
                         if str(c).startswith('POSITIONS: ') and 'not measured' not in str(c)), None)
        checked = any(t.get('check') for t in usable)
        def passes(t):
            if swapped(t) or misplaced(t): return False
            if not checked: return True                       # no 🔍 here: the sides and the positions are all that can be judged
            sc = ck.score(t.get('check'))
            return t.get('check') is not None and (sc is None or sc >= self.FILM_CHECK_BELOW) and ck.makes_sense(t.get('check')) is not False
        if any(passes(t) for t in usable): return None
        if not checked and not any(swapped(t) or misplaced(t) for t in usable): return None
        tries = (ch.get('check_retries') or {}).get(str(n), 0)
        best = max(usable, key=lambda t: ck.score(t.get('check')) or 0)
        miss = [p['point'] for p in (best.get('check') or {}).get('points', []) if p['seen'] == 'no']
        if all(swapped(t) for t in usable): miss = ['the people start on swapped sides compared with where the shot before ends'] + miss
        elif all(misplaced(t) for t in usable): miss = ['📍 ' + misplaced(best).rstrip('.')] + miss
        why = ('🎬 no take of this shot made sense in the film yet' + (': ' + '; '.join(m[:70] for m in miss[:3]) if miss else ''))
        if tries >= self.FILM_CHECK_RETRIES:
            if not any(e['kind'] == 'CHAIN_NOTE' and e['payload'].get('shot') == n for e in st['events'][-50:]):
                self._append('CHAIN_NOTE', {'shot': n, 'why': why + f' after {tries + 1} tries: the best one stands in; look at it (or make a New take)'})
            return None
        subs = [x for x in st['submissions'] if x['shot'] == n]
        base = max((x['take'] for x in subs), default=prev[1])
        t2, said = self._aimed_retry(st, n, base, best.get('check'), all(swapped(t) for t in usable), placed=all(bool(misplaced(t)) for t in usable))
        self._append('CHAIN_RETRY', {'shot': n, 'take': t2, 'kind': 'check', 'why': why + (f': one more take, aimed at it ({said})' if said else ': one more take')})
        self.generate(n, t2, url=url, opener=opener)
        return 'sent'

    EMPHASIS = 'Most important in this shot: '
    def _aimed_retry(self, st, n, base, check, swapped=False, placed=False):
        """V0.9.37: a retry that aims at what went wrong instead of only a new seed. The take is planned again with one
        positive line naming what wasn't seen (YOUR direction's own points, never Qwen's words), who is in the shot, where,
        and, when the sides were swapped, that everyone starts where the shot before ends. Same people, place, length; a new
        seed. If it can't be planned, a plain new-seed take of the same prompt. -> (take, what was added or '')."""
        p = self.plan_record(st, n, base)
        pts = (check or {}).get('points') or []
        miss = [x['point'].rstrip('.') for x in pts if x.get('seen') == 'no' and not x.get('sense')][:3]
        names = [st['characters'][c]['name'] for c in p.get('cast', []) if c in st['characters']]
        sense_miss = [x['point'] for x in pts if x.get('seen') == 'no' and x.get('sense')]
        bits = list(miss)
        if any('only' in x for x in sense_miss) and names:
            bits.append(('only ' + ' and '.join(names) + (' is' if len(names) == 1 else ' are') + ' in the shot'))
        loc = (st['locations'].get(p.get('location')) or {}).get('name')
        if loc and any(x.startswith('It takes place in') for x in sense_miss): bits.append(f'it all happens in {loc}')
        jn = p.get('film_join')
        if jn != 'skip' and (swapped or any('PREVIOUS SHOT' in x for x in sense_miss)):
            bits.append('everyone is on the same sides of the frame as in the shot before' if jn == 'cut' else
                        'everyone starts exactly where the shot before ends, on the same sides of the frame')
        if placed and ((p.get('spec') or {}).get('positions')):       # 📐 V0.9.69 measured: someone was not where the 📍 dots put them
            bits.append('everyone is exactly where the positions of this shot put them, on those sides of the frame and that near the camera')
        if not bits or not p.get('workflow_file') or p.get('mode') in ('edited', 'source'): return self.more_takes(n, base, 2)[0], ''
        said = '; '.join(bits)
        text = re.split(r'\s*' + re.escape(self.EMPHASIS), p['text'])[0].rstrip(' .') + '. ' + self.EMPHASIS + said + '.'
        extra = {k: p[k] for k in ('film', 'film_shot', 'film_title', 'film_framing', 'film_action_photo', 'film_direction', 'scene_context',
                                     'continued_from', 'written_by') if p.get(k) is not None}
        try:
            q = self.plan(text[:3400], mode='revise', target_shot=n, people=names or None, background=p.get('location') or None,
                          duration=(p.get('spec') or {}).get('duration'), pose='none' if not p.get('pose') else None,
                          _extra={**extra, 'retry_of': base, 'emphasis': bits})
            if q.get('action') == 'PLANNED' and q.get('shot') == n: return q['take'], said
        except StudioError: pass
        return self.more_takes(n, base, 2)[0], ''

    NIGHT_CHECK_BELOW = 0.5        # 🔍 best take of the shot below this share of your points seen -> one more take
    NIGHT_CHECK_WAIT = 25 * 60     # Qwen gets this long to check a shot; then the night goes on without it
    _NIGHT_WAIT = {}
    def _night_check(self, st, ch, n, live, subs, prev, url, opener):
        """Overnight with "one more take if 🔍 says it wasn't done" (V0.9.21, off unless you turn it on): Qwen checks the
        shot's takes before the next shot goes; if none shows what you asked, ONE more take with a new seed. It never keeps
        or rejects anything: you choose in the morning. Returns 'waiting_for_check', 'sent' or None (go on)."""
        import studio_check_v0915 as ck
        usable = [t for t in live if t['analysis'] and t.get('analysis_error') != 'BROKEN_TAKE']
        can = {tk for n2, tk, _, _ in self.pending_checks(st, limit=40) if n2 == n}
        unchecked = [t for t in usable if not t.get('check') and t['take'] in can]
        ready = bool(os.environ.get('DIRECTOR_CHECK_FAKE'))
        if not ready:
            try: ready = bool(self.qwen_ready()['ready'])
            except Exception: ready = False
        if unchecked and ready:
            t0 = self._NIGHT_WAIT.setdefault((str(self.root), n, len(usable)), time.time())
            if time.time() - t0 < self.NIGHT_CHECK_WAIT: return 'waiting_for_check'
            return None
        scores = [x for x in (ck.score(t.get('check')) for t in usable) if x is not None]
        if not scores or max(scores) >= self.NIGHT_CHECK_BELOW: return None
        if (ch.get('check_retries') or {}).get(str(n), 0) >= 1: return None
        base = max((x['take'] for x in subs), default=prev[1])
        t2 = self.more_takes(n, base, 2)[0]
        self._append('CHAIN_RETRY', {'shot': n, 'take': t2, 'kind': 'check',
                                     'why': f"🔍 Qwen didn't see what you asked in any take (best {round(100 * max(scores))}% of your points): one more take"})
        self.generate(n, t2, url=url, opener=opener)
        return 'sent'

    # -- generation ------------------------------------------------------------------
    def preflight(self, pairs):
        """✅ V0.9.49 PRE-FLIGHT: before anything is sent, everything that decides how the takes come out, in one place, so a
        forgotten setting is seen BEFORE 30 minutes of generating. Read-only: nothing is changed, approved or sent.
        pairs: [(shot, take)] about to go. -> {'models': [line], 'frame': [line], 'people': [line], 'shots': [row], 'look', 'fine'};
        a line is {'what', 'value', 'look': bool}: look = worth a look (not a block: you can still press Go)."""
        st = self.state(); S = st['settings']; chars = st['characters']; refs = st['refs']
        plans = []
        for sh, tk in pairs or []:
            try: plans.append(self.plan_record(st, int(sh), int(tk)))
            except (StudioError, ValueError, TypeError): raise StudioError(f'NO_SUCH_PLAN: shot {sh} take {tk}')
        if not plans: raise StudioError('NOTHING_TO_SEND')
        line = lambda what, value, look=False, where=None: {'what': what, 'value': value, 'look': bool(look), **({'where': where} if where else {})}
        # models
        lset = lambda ls: sorted((l.get('name'), round(float(l.get('strength', 1)), 2)) for l in (ls or []))
        loras = sorted({(l['name'], round(float(l.get('strength', 1)), 2)) for p in plans for l in (p.get('loras') or [])})
        kept = [p2 for n, s2 in st['shots'].items() for t2 in s2['takes'] if t2['status'] == 'ACCEPTED'
                for p2 in s2['plans'] if p2['take'] == t2['take']]
        last = max(kept, key=lambda p2: (p2['shot'], p2['take'])) if kept else None
        new = sorted(set(loras) - set(lset(last.get('loras')))) if last else []
        gone = sorted(set(lset(last.get('loras'))) - set(loras)) if last else []
        lv = ', '.join(f'{n} {s:g}' for n, s in loras) or 'none'
        if new or gone: lv += ' (' + '; '.join(x for x in (('new since your last kept take: ' + ', '.join(n for n, _ in new)) if new else '',
                                                              ('off now, on in your last kept take: ' + ', '.join(n for n, _ in gone)) if gone else '') if x) + ')'
        qm = {'8b': 'Qwen 8B', '32b': 'Qwen 32B', '27b': 'Qwen3.6 27B'}.get(str(S.get('qwen_model') or '8b'), str(S.get('qwen_model')))
        fw = {'same': 'no thinking (quickest)', 'think_plan': 'thinks about the plan only', 'think_all': 'thinks through every shot'}.get(S.get('film_writer') or 'same', S.get('film_writer'))
        film = any(p.get('film') or p.get('film_join') or (p.get('spec') or {}).get('lead_in') for p in plans) or len(plans) > 1
        models = [line('H3', f"full BF16 · {int(S.get('steps', DEFAULT_STEPS))} steps · {S.get('sampler', 'euler')}"),
                  line('LoRAs', lv, bool(new or gone), 'loras'),
                  line('Qwen (rewrites and 🔍 checks)', qm, False, 'qwen'),
                  line('🎬 Film writer', fw, film and (S.get('film_writer') or 'same') != 'think_all', 'film_writer')]
        # the frame
        frame = []
        sfs = {p.get('start_frame') for p in plans if p.get('start_frame')}
        frame.append(line('Opens on', ('your picture in ' + (f'all {len(plans)} shots' if len(sfs) and all(p.get('start_frame') for p in plans) else
                                       f"{sum(1 for p in plans if p.get('start_frame'))} of {len(plans)} shots")) if sfs else 'no start picture (H3 makes the first frame)',
                          False, 'start'))
        locs = sorted({(st['locations'].get(p.get('location')) or {}).get('name') or ('background from the people\'s photos' if (p.get('background') or {}).get('mode') == 'people' else 'automatic')
                       for p in plans})
        frame.append(line('Place', ', '.join(locs), len(locs) > 1 and len(plans) > 1, 'place'))
        cams = sorted({(f"🎥 {p['film_camera'].replace('pullback', 'pull back').replace('push', 'push in').replace('_', ' ')} (shot {p['shot']})" if p.get('film_camera') else
                        '🔒 locked to the opening frame' if (p.get('spec') or {}).get('frame_lock') else '📷 one angle' if (p.get('spec') or {}).get('place_view') else (p.get('spec') or {}).get('framing') or 'free')
                       for p in plans})
        frame.append(line('Camera', ', '.join(c.replace('_', ' ') for c in cams)))
        leads = sorted({float((p.get('spec') or {}).get('lead_in') or 0) for p in plans if p.get('start_frame')})
        if leads: frame.append(line('⏱ Lead-in', ', '.join((f'{x:g} s' if x else 'none') for x in leads), 0.0 in leads, 'lead'))
        # people
        people = []
        cast = []
        for p in plans:
            for c in p.get('cast') or []:
                if c not in cast: cast.append(c)
        for c in cast:
            C = chars.get(c) or {}; nm = C.get('name', c)
            mine = [r for p in plans for r in p.get('refs') or [] if (refs.get(r) or {}).get('owner') == c]
            fulls = [refs[r] for r in set(mine) if refs[r]['kind'] == 'full']
            bits, look = [], False
            bare = [p['shot'] for p in plans if c in (p.get('cast') or []) and not any((refs.get(r) or {}).get('owner') == c and refs[r]['kind'] == 'full' for r in p.get('refs') or [])]
            if bare: bits.append('no full-body reference in ' + (f"shot {', '.join(map(str, bare))}" if len(plans) > 1 else 'this shot') + ' (H3 guesses the legs and clothes)'); look = True
            if any(r.get('feet_in') is False for r in fulls): bits.append('full-body reference with the feet cut off'); look = True
            if any(r.get('variant') == 'clean' for r in (refs[x] for x in set(mine))): bits.append('cut-outs on grey')
            hid = [p['shot'] for p in plans if nm in ((p.get('spec') or {}).get('hide_faces') or [])]
            if hid: bits.append('🙈 face hidden in shot ' + ', '.join(map(str, hid)))
            rv = [p['shot'] for p in plans if nm in ((p.get('spec') or {}).get('reveal') or [])]
            if rv: bits.append('👁 revealed in shot ' + ', '.join(map(str, rv)))
            people.append(line(nm, '; '.join(bits) or 'face and body references', look, 'cast'))
        # shot by shot
        rows = []
        for p in plans:
            spec = p.get('spec') or {}; names = [chars[c]['name'] for c in p.get('cast') or [] if c in chars]
            pos = spec.get('positions') or {}
            if (pos.get('start') or []):
                cl = p.get('positions_warnings') or []
                pv, pl = ('set · the text disagrees: ' + cl[0]) if cl else 'set', bool(cl)
            else:
                g = pm.from_text(p.get('text') or '', names)
                pv, pl = (f"the text doesn't say where {' and '.join(g['vague'])} {'are' if len(g['vague']) > 1 else 'is'}", True) if g['vague'] else ('from the text', False)
            sec = round(int(p.get('frames') or 124) / 24, 1)
            prev = st['shots'].get(p['shot'] - 1)
            unkept = bool(p.get('film_join') == 'carry' and prev and prev['takes'] and not any(t2['status'] == 'ACCEPTED' for t2 in prev['takes']))
            rows.append({'shot': p['shot'], 'take': p['take'], 'action': (p.get('text') or '')[:160],
                         'positions': line('📍', pv, pl), 'faces': ', '.join(f'{n} hidden' for n in spec.get('hide_faces') or []) or 'all shown',
                         'join': p.get('film_join') or ('opens on picture' if p.get('start_frame') else 'carries on' if p['shot'] > 1 else 'first'),
                         'length': f"{sec:g} s" + (f" (incl. {float(spec['lead_in']):g} s lead-in)" if spec.get('lead_in') else ''),
                         'before': line('Shot before', f"shot {p['shot'] - 1} has no ✓ kept take yet: this one carries on from the best-measured one", True) if unkept else None,
                         'missing': p.get('missing_refs_for') or []})
        lines = models + frame + people + [r['positions'] for r in rows] + [r['before'] for r in rows if r['before']]
        look = sum(1 for x in lines if x['look']) + sum(1 for r in rows if r['missing'])
        return {'models': models, 'frame': frame, 'people': people, 'shots': rows, 'look': look, 'fine': len(lines) - sum(1 for x in lines if x['look'])}

    def plan_record(self, st, shot, take):
        for p in st['shots'].get(shot, {'plans': []})['plans']:
            if p['take'] == take: return p
        raise StudioError('NO_SUCH_PLAN')

    def api_prompt(self, shot, take):
        st = self.state(); p = self.plan_record(st, shot, take)
        if not p.get('workflow_file'): raise StudioError('IMPORTED_TAKE_HAS_NO_WORKFLOW_PLAN_A_RETAKE_OR_NEW_SHOT')
        wf = self.root / 'plans' / p['workflow_file']
        if not wf.is_file() and p.get('mode') != 'imported':
            # recovery: the plan ledger holds everything needed to rebuild the identical workflow
            g = build_graph([st['refs'][r]['input_name'] for r in p['refs']], p['prompt'], p['seed'], p['prefix'],
                            video_names=[st['refs'][r]['input_name'] for r in p.get('videos', [])],
                            video_audio=bool(p.get('videos')) and bool(st['settings'].get('voice_refs')) and not p.get('audios'),
                            audio_names=[st['refs'][r]['input_name'] for r in p.get('audios', [])], anchor_names=self._anchor_names(st, p), pose=p.get('pose'),
                            loras=[(l['name'], l['strength']) for l in p.get('loras', [])], length=int(p.get('frames', 124)), **(p.get('sampling') or {}))
            text_wf = json.dumps(g, indent=2, ensure_ascii=False) + '\n'
            if hashlib.sha256(text_wf.encode()).hexdigest() == p['workflow_sha256']: wf.write_text(text_wf)
        if not wf.is_file(): raise StudioError('WORKFLOW_FILE_MISSING ' + p['workflow_file'])
        g = json.loads(wf.read_text())
        if hashlib.sha256(wf.read_bytes()).hexdigest() != p['workflow_sha256']:
            raise StudioError('WORKFLOW_FILE_CHANGED')
        shas = {st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p['refs']}
        vids = {st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('videos', [])}
        auds = {st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('audios', [])}
        ancs = {st['refs'][r]['input_name']: st['refs'][r]['sha256'] for r in p.get('anchors', [])}
        for name, h in list(shas.items()) + list(vids.items()) + list(auds.items()) + list(ancs.items()):
            f = self.input_dir / name
            if not f.is_file() or media.sha256(f) != h: raise StudioError('REFERENCE_FILE_MISSING_OR_CHANGED ' + name)
        cg.validate_director_graph(g, shas, require_capture=False, length=int(p.get('frames', 124)), videos=vids or None,
                                   loras={l['name']: l['strength'] for l in p.get('loras', [])} or None, audios=auds or None, anchors=ancs or None, pose=self._pose_decl(p))
        return g, cg.to_api_prompt(g, {**shas, **ancs})

    def live_schema(self, url, opener=None):
        """What the running ComfyUI actually offers (nodes + LoRA files)."""
        from urllib.request import urlopen
        from urllib.error import URLError
        op = opener or urlopen
        out = {}
        for node in ('MiniMaxH3ReferenceToVideo', 'LoadVideo', 'GetVideoComponents', 'LoraLoaderModelOnly', 'LoadAudio', 'MiniMaxH3AddGuide'):
            try:
                with op(url.rstrip('/') + '/object_info/' + node, timeout=15) as r: out[node] = json.load(r).get(node)
            except URLError as e:
                if node == 'MiniMaxH3ReferenceToVideo': raise StudioError('COMFYUI_NOT_REACHABLE: ' + str(getattr(e, 'reason', e))[:200])
                out[node] = None
            except Exception:
                out[node] = None
        def has_input(node, name):
            spec = (out.get(node) or {}).get('input', {})
            return any(name in (spec.get(sec) or {}) for sec in ('required', 'optional'))
        lora_files = []
        try: lora_files = list(out['LoraLoaderModelOnly']['input']['required']['lora_name'][0])
        except Exception: pass
        return {'h3': out['MiniMaxH3ReferenceToVideo'] is not None,
                'video_refs': has_input('LoadVideo', 'file') and has_input('GetVideoComponents', 'video'),
                'lora_node': out['LoraLoaderModelOnly'] is not None, 'lora_files': lora_files, 'load_audio': out.get('LoadAudio') is not None,
                'guide': has_input('MiniMaxH3AddGuide', 'frame_idx') and has_input('LoadVideo', 'file') and has_input('GetVideoComponents', 'video')}

    _SOUND_CACHE = {}
    def _clip_sound(self, ref):
        """Does this reference clip have a sound track? (H3 labels only clips WITH sound as <Audio N>, in clip order.)"""
        key = ref.get('sha256') or ref.get('input_name')
        if key not in self._SOUND_CACHE:
            try:
                r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0',
                                    str(self.input_dir / ref['input_name'])], capture_output=True, text=True, timeout=30)
                self._SOUND_CACHE[key] = bool(r.stdout.strip()) if r.returncode == 0 else True
            except Exception: self._SOUND_CACHE[key] = True
        return self._SOUND_CACHE[key]

    def _anchor_names(self, st, p):
        """The pinned pictures of a plan for the workflow: [(input name, 0 = first frame | -1 = last frame)]."""
        at = p.get('anchor_at') or {}
        return [(st['refs'][r]['input_name'], int(at.get(r, 0))) for r in p.get('anchors', [])]

    # ⏱ V0.9.48: a shot that opens on your picture gets its first seconds to move everyone from the picture into place
    LEAD_IN = ('In the first {s:g} seconds everyone moves calmly and directly from where they are in that first frame to where this '
               'shot needs them; the action of the shot starts right after that.')
    def _compose(self, st, spec, cast, loc, refs, continuity, lessons, videos, bg, frames, loras, extend=None, audios=None, start=False, end=False):
        for r in videos or []:
            if r in st['refs'] and 'audio' not in st['refs'][r]: st['refs'][r]['audio'] = self._clip_sound(st['refs'][r])
        triggers = ' '.join(l['trigger'].strip() for l in loras if l.get('trigger', '').strip())
        if official_format(st):
            pr = compose_official(st, spec, cast, loc, refs, continuity, lessons, videos=videos, bg=bg, frames=frames, extend=extend, audios=audios or [], start=start, end=end)
            return pr.replace('detailed_description:\n', 'detailed_description:\n' + triggers + '. ', 1) if triggers else pr
        pr = compose(st, spec, cast, loc, refs, continuity, None, lessons, videos=videos, bg=bg, frames=frames, audios=audios or [])
        if end: pr = pr.replace(' AUDIO', ' The video ends exactly on the given last frame, a real photo of this scene: by then everyone is where that picture shows them.' + ' AUDIO', 1)
        if start: pr = pr.replace(' AUDIO', ' The video opens exactly on the given first frame, a real photo of this scene, and moves on from it without a cut.' + ' AUDIO', 1)
        if start and spec.get('lead_in'):
            down = {'kneeling': 'kneels down', 'sitting': 'sits down', 'crouching': 'crouches down', 'lying_back': 'lies down on the back',
                    'lying_front': 'lies down face down', 'lying_side': 'lies down on one side'}
            si = ''.join(f" {x['who']} is standing in that first frame and {down[x['posture']]} in those seconds." for x in spec.get('stand_ins') or [] if x.get('posture') in down)
            pr = pr.replace(' AUDIO', ' ' + self.LEAD_IN.format(s=float(spec['lead_in'])) + si + ' AUDIO', 1)   # ⏱ V0.9.48; V0.9.61 stand-ins
        if extend and extend.get('anchored') and not videos:
            pr = pr.replace(' AUDIO', ' The video opens on the final moment of the previous shot and carries on from it: same place, same people in the same positions, same camera and light, no cut, nothing repeated.' + ' AUDIO', 1)
        elif extend: pr = pr.replace(' AUDIO', ' Continue directly from where reference video 1 ends: same place, same people in the same positions, same camera and light, no cut.' + ' AUDIO', 1)
        if triggers: pr = triggers + '. ' + pr
        if videos: pr = pr.replace(' AUDIO', VIDEO_SENTENCE + ' AUDIO', 1)
        return pr

    def _fallback_plan(self, p, reason):
        """Same shot and direction, stills only (no reference clips)."""
        st = self.state()
        take = len(st['shots'][p['shot']]['plans']) + 1
        if p.get('extend'): raise StudioError('EXTENDING_A_VIDEO_NEEDS_THE_LOADVIDEO_NODES_UPDATE_COMFYUI')
        st0 = self.state()
        prompt = self._compose(st0, p['spec'], p['cast'], p['location'], p['refs'], p.get('continuity', ''), p.get('lessons_used'), [],
                               p.get('background'), int(p.get('frames', 124)), p.get('loras', []), audios=p.get('audios', []),
                               start=bool(p.get('start_frame')), end=bool(p.get('end_frame'))) if 'continuity' in p else p['prompt'].replace(VIDEO_SENTENCE, '')
        if 'continuity' in p: prompt = self._with_note(self._with_note(self._spec_notes(prompt, p['spec']), p.get('frame_note')), p.get('pose_note'))
        if p.get('pose'): prompt = self._no_lines(prompt)
        seed = self._fresh_seed(st, {'p': st['project']['slug'], 'shot': p['shot'], 'take': take, 'prompt': prompt})
        prefix = f"Studio_{st['project']['slug']}_S{p['shot']:02d}_T{take:02d}"
        loras = p.get('loras', [])
        graph = build_graph([st['refs'][r]['input_name'] for r in p['refs']], prompt, seed, prefix, loras=[(l['name'], l['strength']) for l in loras],
                            length=int(p.get('frames', 124)), audio_names=[st['refs'][r]['input_name'] for r in p.get('audios', [])],
                            anchor_names=self._anchor_names(st, p), pose=p.get('pose'), **(p.get('sampling') or {}))
        text_wf = json.dumps(graph, indent=2, ensure_ascii=False) + '\n'
        (self.root / 'plans' / f'{prefix}.json').write_text(text_wf)
        q = {**p, 'take': take, 'mode': 'fallback', 'videos': [], 'prompt': prompt, 'seed': seed, 'prefix': prefix,
             'workflow_file': f'{prefix}.json', 'workflow_sha256': hashlib.sha256(text_wf.encode()).hexdigest(),
             'ref_choice': [x for x in p.get('ref_choice', []) if 'reference clip' not in x] + ['stills only: ' + reason]}
        self._append('SHOT_PLANNED', q)
        return q

    def generate(self, shot, take, *, url='http://127.0.0.1:18188', opener=None):
        """Queue one planned take. Requires an explicit call (UI button / --generate).
        Reads the live node schema first; unsupported reference clips fall back to stills automatically."""
        from urllib.request import urlopen, Request
        from urllib.error import HTTPError, URLError
        op = opener or urlopen
        self._need_disk('generate')
        schema = self.live_schema(url, opener)
        if not schema['h3']: raise StudioError('H3_NODE_NOT_LOADED')
        st0 = self.state()
        if (shot, take) in st0.get('frame_no', set()): take = self._built_origin(st0, shot, take)[0]   # V0.9.61 you said no to that frame
        nt = self.carry_frame(shot, take)                      # V0.9.60 🔗 a carry-on film shot opens on a built frame
        if nt: take = nt
        st = self.state(); p = self.plan_record(st, shot, take); note = None
        if nt: note = f"🔗 Take {take} opens on a frame built from where shot {p['carry_frame']['from'][0]} ends (your own photos, placed where everyone was)."
        if p.get('loras'):
            if not schema['lora_node']: raise StudioError('LORA_NODE_NOT_AVAILABLE_TURN_OFF_LORAS_IN_SETTINGS')
            missing = [l['name'] for l in p['loras'] if schema['lora_files'] and l['name'] not in schema['lora_files']]
            if missing: raise StudioError('LORA_FILE_NOT_FOUND_IN_COMFYUI: ' + ', '.join(missing))
        if p.get('audios') and not schema.get('load_audio'):
            raise StudioError('COMFYUI_HAS_NO_LOADAUDIO_NODE: update ComfyUI, or remove the voice (Settings: voice recordings off) for this take')
        if p.get('anchors') and not schema.get('guide'):
            raise StudioError("COMFYUI_HAS_NO_ADD_GUIDE_NODE: this ComfyUI can't pin " + ("your original's ending at the start of the take. "
                              'Update ComfyUI, or set Settings > Extend to "clip only" and plan the extension again' if p.get('extend') else
                              'your start frame. Update ComfyUI, or plan the shot again without a start frame'))
        if p.get('pose'):                                   # 🎭: every pose node and model file on THIS ComfyUI, and the clip unchanged
            import studio_pose_v0921 as ps
            def fetch(node):
                try:
                    with op(url.rstrip('/') + '/object_info/' + node, timeout=15) as r: return json.load(r).get(node)
                except Exception: return None
            ready = ps.readiness(fetch)
            if not ready['ready']:
                raise StudioError('POSE_NOT_READY_IN_COMFYUI: ' + '; '.join(ready['problems'] + [f'missing {x}' for x in ready['missing_files']])[:600] +
                                  ' (Settings > 🎭 Movement: Download, then restart ComfyUI; or plan the shot again without a movement video)')
            self._ensure_pose_file(st, p)
        if p.get('videos') and not schema['video_refs']:
            p = self._fallback_plan(p, 'this ComfyUI has no LoadVideo/GetVideoComponents nodes'); take = p['take']
            note = f'Your ComfyUI has no video-loading nodes, so take {take} uses still references only.'
        try:                                           # planned before the previous shot came back? bring its continuity up to date
            if self.refresh_continuity(shot, take):
                note = ((note + ' ') if note else '') + 'Continuity updated from how the previous shot actually ends in the movie.'
        except StudioError: pass
        g, api = self.api_prompt(shot, take)
        body = json.dumps({'prompt': api, 'client_id': CLIENT_ID, 'extra_data': {'extra_pnginfo': {'workflow': g}}}).encode()
        try:
            with op(Request(url.rstrip('/') + '/prompt', data=body, headers={'Content-Type': 'application/json'}, method='POST'), timeout=30) as r:
                resp = json.load(r)
        except HTTPError as e:
            raise StudioError(f'COMFYUI_REFUSED_THE_JOB ({e.code}): ' + e.read().decode(errors='replace')[:400])
        except URLError as e:
            raise StudioError('COMFYUI_NOT_REACHABLE: ' + str(e.reason)[:200])
        if resp.get('node_errors'): raise StudioError('COMFYUI_REJECTED ' + json.dumps(resp['node_errors'])[:400])
        self._append('GENERATION_SUBMITTED', {'shot': shot, 'take': take, 'prompt_id': resp.get('prompt_id'),
                     'authorized_by': 'EXPLICIT_USER_ACTION', 'note': note, 'at': round(time.time())})
        self.last_note = note
        return resp.get('prompt_id')

    # -- intake + analysis + auto-edit --------------------------------------------------
    def sync(self, analyze=True):
        """Find finished takes in ComfyUI/output, verify they came from OUR plan (embedded prompt+seed),
        measure them, and auto-edit them into the movie. Never approves anything."""
        st = self.state(); notes = []; ingested = 0; self._edited = False; self._tried_now = []
        out_dir = self.output_dir
        have = {(n, t['take']) for n, s in st['shots'].items() for t in s['takes']}
        refused = {e['payload']['sha256'] for e in st['events'] if e['kind'] == 'TAKE_REFUSED'}
        for n, s in sorted(st['shots'].items()):
            for p in s['plans']:
                if (n, p['take']) in have: continue
                files = sorted(out_dir.glob(p['prefix'] + '_*-audio.mp4')) or sorted(out_dir.glob(p['prefix'] + '_*.mp4'))
                files = [f for f in files if not f.is_symlink()]
                if not files: continue
                f = files[-1]
                if not file_is_settled(f):                     # VHS still writing / muxing audio: look again next scan
                    notes.append(f"Shot {n} take {p['take']}: {f.name} is still being written; it will be picked up when finished."); continue
                h = media.sha256(f)
                if h in refused: continue                      # already reported once; never re-announced
                if h in st['generated_sha']: continue          # already in as another take (e.g. attached by hand)
                reason = self._matches_plan(st, p, f)
                if reason:
                    self._append('TAKE_REFUSED', {'shot': n, 'take': p['take'], 'file': f.name, 'sha256': h, 'reason': reason})
                    notes.append(f"{f.name}: {reason}; not ingested (plan a new take, or regenerate from the Director's workflow)."); continue
                meta = media.probe(f); ingested += 1
                self._append('TAKE_CAPTURED', {'shot': n, 'take': p['take'], 'file': f.name, 'sha256': h,
                             'frames': meta.get('frames'), 'fps': meta.get('fps'), 'audio': meta.get('audio'),
                             'provenance': 'EMBEDDED_PROMPT_AND_SEED_MATCH_PLAN', 'approved': False})
                if analyze: notes += self._analyze_take(st, n, p, f, h, first=True)
                else: notes.append(f"Shot {n} take {p['take']} arrived; measuring it in the background."); self._edited = True
        # Recovery: captured takes whose analysis never finished (crash, decode error, restart) are retried
        # on every scan until they succeed; a failure is announced once (and again only if the error changes).
        st2 = self.state()
        for n, s in sorted(st2['shots'].items()):
            for t in s['takes']:
                if t['status'] == 'REJECTED': continue
                if any(x['shot'] == n and x['take'] == t['take'] for x in getattr(self, '_tried_now', [])): continue
                p = self.plan_record(st2, n, t['take'])
                f = self.take_path(t)
                if not f.is_file() or f.is_symlink():
                    if t.get('analysis_error') != 'TAKE_FILE_MISSING':
                        self._append('TAKE_ANALYSIS_FAILED', {'shot': n, 'take': t['take'], 'error': 'TAKE_FILE_MISSING'})
                        notes.append(f"Shot {n} take {t['take']}: video file {t['file']} is missing from ComfyUI/output; it is left out of the movie until it is back.")
                    continue
                if not same_file(f, t['sha256']):
                    # An UNREVIEWED capture that was still growing (or re-saved by ComfyUI) is re-bound when the settled
                    # file still carries exactly this plan's embedded prompt + seed + references. Reviewed takes never are.
                    if t['status'] == 'CAPTURED' and p.get('workflow_file') and file_is_settled(f) and self._matches_plan(st2, p, f) is None:
                        h2 = media.sha256(f); meta = media.probe(f)
                        self._append('TAKE_RECAPTURED', {'shot': n, 'take': t['take'], 'file': f.name, 'sha256': h2, 'previous_sha256': t['sha256'],
                                     'frames': meta.get('frames'), 'fps': meta.get('fps'), 'audio': meta.get('audio')})
                        notes.append(f"Shot {n} take {t['take']}: the video file was finished after it was first seen; re-measured.")
                        notes += self._analyze_take(self.state(), n, p, f, h2, first=False)
                        continue
                    if t.get('analysis_error') != 'TAKE_FILE_CHANGED':
                        self._append('TAKE_ANALYSIS_FAILED', {'shot': n, 'take': t['take'], 'error': 'TAKE_FILE_CHANGED'})
                        notes.append(f"Shot {n} take {t['take']}: video file changed after capture; it is left out of the movie.")
                    continue
                if t['analysis'] is not None:
                    if t.get('analysis_error') in ('TAKE_FILE_MISSING', 'TAKE_FILE_CHANGED'):
                        self._append('TAKE_FILE_RESTORED', {'shot': n, 'take': t['take']})
                        notes.append(f"Shot {n} take {t['take']}: its video file is back and verified; it is in the movie again."); self._edited = True
                    af = self.root / 'analysis' / t.get('analysis_file', '')
                    if analyze and not _json_ok(af):           # analysis file lost/corrupted: measure again (same name)
                        try:
                            a = media.analyze_take(f); tmp = af.with_suffix('.partial')
                            tmp.write_text(json.dumps(a)); os.replace(tmp, af)
                            notes.append(f"Shot {n} take {t['take']}: its measurements were missing and were rebuilt.")
                        except Exception as e:
                            notes.append(f"Shot {n} take {t['take']}: rebuilding its measurements failed ({type(e).__name__}); retrying next scan.")
                    if not t.get('qwen_job') and not t.get('source') and st2['settings'].get('qwen', 'manual') != 'off':
                        try:
                            jid = qwen.enqueue_take(f, self.qwen_root())
                            self._append('QWEN_JOB_QUEUED', {'shot': n, 'take': t['take'], 'job_id': jid, 'video_sha256': t['sha256'], 'retry': True})
                            self._qwen_new = True
                        except Exception: pass                 # retried silently on the next scan
                    continue
                if analyze: notes += self._analyze_take(st2, n, p, f, t['sha256'], first=False, prior_error=t.get('analysis_error'))
        self._tried_now = []
        if getattr(self, '_edited', False) and self.state()['settings'].get('auto_edit'):
            notes.append('Movie re-edited automatically with the new take(s).')
        if getattr(self, '_qwen_new', False) and self.state()['settings'].get('qwen') == 'auto':
            self._qwen_new = False
            try:
                if self.qwen_ready()['ready']:
                    r = self.run_qwen(); notes.append('Qwen observation ' + ('started (runs when ComfyUI is idle).' if r == 'STARTED' else 'already running.'))
            except Exception as e:
                notes.append('Qwen not started: ' + str(e)[:120])
        return notes

    def analyze_pending(self, skip=()):
        """Measure every captured take that isn't measured yet (and rebuild lost measurement files).
        Heavy work: the web app runs it in a background worker so clicks never wait for it."""
        st = self.state(); notes = []; done = []
        work = [(n, t) for n, sh in sorted(st['shots'].items()) for t in sh['takes']]
        work.sort(key=lambda x: 0 if x[1]['status'] == 'ACCEPTED' else 1)      # a take you kept is measured first
        for n, t in work:
                if t['status'] == 'REJECTED' or (n, t['take']) in skip: continue
                if t.get('analysis_error') in ('TAKE_FILE_MISSING', 'TAKE_FILE_CHANGED', 'BROKEN_TAKE'): continue
                f = self.take_path(t)
                if not same_file(f, t['sha256']): continue
                if t['analysis'] is None:
                    p = self.plan_record(st, n, t['take'])
                    notes += self._analyze_take(self.state(), n, p, f, t['sha256'], first=t.get('analysis_error') is None,
                                                prior_error=t.get('analysis_error'))
                    done.append((n, t['take']))
                else:
                    af = self.root / 'analysis' / t.get('analysis_file', '')
                    if not _json_ok(af):
                        try:
                            a = self._measure_isolated(n, self.plan_record(st, n, t['take']), f)['analysis'] if getattr(self, 'isolate', False) else media.analyze_take(f)
                            tmp = af.with_suffix('.partial'); tmp.write_text(json.dumps(a)); os.replace(tmp, af)
                            notes.append(f"Shot {n} take {t['take']}: its measurements were missing and were rebuilt.")
                        except Exception as e:
                            notes.append(f"Shot {n} take {t['take']}: rebuilding its measurements failed ({type(e).__name__}).")
                        done.append((n, t['take']))
        if done and getattr(self, '_edited', False) and self.state()['settings'].get('auto_edit'):
            notes.append('Movie re-edited automatically with the new take(s).')
        if getattr(self, '_qwen_new', False) and self.state()['settings'].get('qwen') == 'auto':
            self._qwen_new = False
            try:
                if self.qwen_ready()['ready']: self.run_qwen()
            except Exception as e: notes.append('Qwen not started: ' + str(e)[:120])
        return notes, done

    def _previous_order(self, st, shot):
        for e in reversed(self.story(st, before=shot)):
            if e.get('take') is None: continue
            t = next((t for t in st['shots'][e['shot']]['takes'] if t['take'] == e['take']), None)
            o = ((t or {}).get('analysis') or {}).get('identity') or {}
            if o.get('left_to_right'): return o['left_to_right']
            return None
        return None

    def output_candidates(self, shot, take):
        """Videos in ComfyUI/output that could be this take (newest first), each with whether it matches the plan."""
        st = self.state(); p = self.plan_record(st, shot, take)
        used = {t['file'] for sh in st['shots'].values() for t in sh['takes']}
        out = []
        files = [f for f in self.output_dir.glob('*.mp4') if not f.is_symlink() and f.name not in used]
        for f in sorted(files, key=lambda f: -f.stat().st_mtime)[:40]:
            reason = self._matches_plan(st, p, f) if file_is_settled(f) else 'still being written'
            same_prefix = f.name.startswith(p['prefix'] + '_')
            if same_prefix or reason is None:
                out.append({'file': f.name, 'matches': reason is None, 'reason': reason, 'same_name': same_prefix,
                            'mb': round(f.stat().st_size / 1e6, 1), 'age_min': round((time.time() - f.stat().st_mtime) / 60, 1)})
        return out

    def attach_take(self, shot, take, *, file=None, upload=None, confirm=False):
        """Bring in a take by hand: a file already in ComfyUI/output (file=name) or an uploaded copy (upload=path).
        It must be the video ComfyUI made from this plan: its embedded workflow is checked; if that check fails
        (metadata lost by a download or re-save) it is only taken in when you confirm it (confirm=True), and it is
        marked as confirmed by you. Takes are never used as references (Clean-Chain), only as clips in the movie."""
        st = self.state(); p = self.plan_record(st, shot, take)
        if any(t['take'] == take for t in st['shots'][shot]['takes']): raise StudioError('THIS_TAKE_IS_ALREADY_IN')
        if (file is None) == (upload is None): raise StudioError('GIVE_A_FILE_OR_AN_UPLOAD')
        if file is not None:
            f = self.output_dir / Path(str(file)).name
            if not f.is_file() or f.is_symlink() or f.suffix.lower() != '.mp4': raise StudioError('NOT_A_VIDEO_IN_COMFYUI_OUTPUT')
        else:
            src = Path(upload)
            try: meta = media.probe(src)
            except ValueError: meta = {}
            if not meta.get('video'): raise StudioError('NOT_A_VIDEO')
            n = 90001
            while (self.output_dir / f"{p['prefix']}_{n:05d}-audio.mp4").exists(): n += 1
            f = self.output_dir / f"{p['prefix']}_{n:05d}-audio.mp4"
            if src.suffix.lower() == '.mp4': shutil.copyfile(src, f)
            else:
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(src), '-c', 'copy', '-movflags', '+faststart', str(f)], check=True)
            os.utime(f, (time.time() - 60, time.time() - 60))
        h = media.sha256(f)
        if h in st['generated_sha'] or any(r.get('sha256') == h for r in st['refs'].values()): raise StudioError('THIS_VIDEO_IS_ALREADY_USED_IN_THE_PROJECT')
        reason = self._matches_plan(st, p, f)
        if reason and not confirm:
            if upload is not None: f.unlink(missing_ok=True)
            raise StudioError(f'TAKE_NEEDS_CONFIRMATION: {reason}. If this really is the video ComfyUI made for shot {shot} take {take}, confirm to use it.')
        meta = media.probe(f)
        if (meta.get('frames') or 0) < 12: raise StudioError('VIDEO_TOO_SHORT')
        self._append('TAKE_CAPTURED', {'shot': shot, 'take': take, 'file': f.name, 'sha256': h, 'frames': meta.get('frames'), 'fps': meta.get('fps'),
                     'audio': meta.get('audio'), 'approved': False,
                     'provenance': 'EMBEDDED_PROMPT_AND_SEED_MATCH_PLAN' if reason is None else 'CONFIRMED_BY_YOU_METADATA_DID_NOT_MATCH',
                     'attached_by_hand': True, 'match_problem': reason})
        return {'file': f.name, 'verified': reason is None}

    def _matches_plan(self, st, p, f):
        """None when the take's embedded workflow is exactly this plan; otherwise the reason."""
        prov = cg.embedded_provenance(f)
        if prov.get('status') != 'OK' or prov['seed'] != p['seed'] or prov['prompt'] != p['prompt']:
            return 'embedded workflow does not match the plan'
        expect_v = sorted(st['refs'][r]['input_name'] for r in p.get('videos', []))
        expect_a = sorted(st['refs'][r]['input_name'] for r in p.get('audios', []))
        vid_links = [k for k in prov['video_or_audio_refs_linked'] if not k.startswith('ref_audios.')]
        if sorted(x for x in prov.get('videos', []) if x) != expect_v or (vid_links and not expect_v) \
                or sorted(x for x in prov.get('audios', []) if x) != expect_a:
            return 'video/audio references differ from the plan'
        if sorted(x for x in prov.get('anchors', []) if x) != sorted(st['refs'][r]['input_name'] for r in p.get('anchors', [])):
            return 'the pinned original ending differs from the plan'
        if sorted(map(tuple, prov.get('loras', []))) != sorted((l['name'], float(l['strength'])) for l in p.get('loras', [])):
            return 'LoRAs differ from the plan'
        if sorted(prov['images']) != sorted(st['refs'][r]['input_name'] for r in p['refs']):
            return 'reference images differ from the plan'
        return None

    def _measure_isolated(self, n, p, f):
        """Measure a take in the helper process: normal -> again with OpenCV's fast CPU paths off -> reduced (no face
        finder / optical flow). A reduced measurement still puts the take in the movie; the shot card says so."""
        import subprocess as sp, sys as _sys, tempfile as tf
        helper = HERE / 'studio_ingest_worker_v093.py'; safe = {'OPENCV_CPU_DISABLE': 'AVX512_SKX,AVX512_COMMON,AVX512_KNL,AVX512_KNM,AVX512_CNL,AVX512_CLX,AVX512_ICL,AVX2,FMA3,AVX',
                                                              'OPENCV_FOR_THREADS_NUM': '1'}
        where = None
        for name, extra, reduced in (('full', {}, False), ('safe_cpu', safe, False), ('reduced', safe, True)):
            with tf.TemporaryDirectory() as t:
                out = Path(t) / 'm.json'
                cmd = [_sys.executable, str(helper), '--root', str(self.root), '--comfy-root', str(self.comfy), '--input-dir', str(self.input_dir),
                       '--output-dir', str(self.output_dir), '--owner', '-', '--path', str(f), '--measure', str(n), str(p['take']), str(out)] + (['--reduced'] if reduced else [])
                r = sp.run(cmd, capture_output=True, text=True, timeout=1800, env={**os.environ, **extra})
                if r.returncode == 0 and out.is_file():
                    res = json.loads(out.read_text())
                    at = f' ({where})' if where else ''
                    res['note'] = None if name == 'full' else (
                        f'MEASURED_SAFELY: OpenCV crashed while measuring this take on this machine{at}; it was measured again with its fast CPU paths off.'
                        if name == 'safe_cpu' else
                        f'MEASURED_REDUCED: OpenCV crashed while measuring this take on this machine{at}; it was measured without the face finder and motion '
                        'analysis, so its cut points and identity check are rougher. Please tell Claude.')
                    return res
                m = re.findall(r'File "[^"]*?([^/"]+)", line (\d+) in (\w+)', r.stderr or '')
                mine = [x for x in m if x[0].startswith('studio_') and not x[0].startswith('studio_ingest')]
                where = where or (f'{mine[0][0]}:{mine[0][1]} {mine[0][2]}' if mine else None)
                print(f'[DirectorStudio] measuring shot {n} take {p["take"]} ({name}) failed: {r.returncode} {(r.stderr or "")[-1500:]}', flush=True)
                if r.returncode >= 0 and 'RESULT' not in r.stdout and name == 'full' and 'Error' in (r.stderr or '') and 'Fatal Python error' not in (r.stderr or ''):
                    raise ValueError(' '.join((r.stderr or '').strip().splitlines()[-1:])[:200] or 'MEASURING_FAILED')   # an ordinary error: report it, no fallbacks
        raise ValueError('MEASURING_CRASHED_EVERY_WAY' + (f' at {where}' if where else ''))

    def _analyze_take(self, st, n, p, f, h, *, first, prior_error=None):
        """Measure a captured take and record TAKE_ANALYZED; on failure record TAKE_ANALYSIS_FAILED (retried later)."""
        notes = []
        self._tried_now = getattr(self, '_tried_now', []) + [{'shot': n, 'take': p['take']}]
        iso = None
        try:
            if getattr(self, 'isolate', False):          # in the web app: measured in a helper process (a crash can't take ComfyUI down)
                iso = self._measure_isolated(n, p, f); a = iso['analysis']
            else:
                a = media.analyze_take(f)
            if a.get('sha256') != h: raise ValueError('TAKE_FILE_CHANGED_DURING_ANALYSIS')
            af = self.root / 'analysis' / f'{p["prefix"]}_{h[:10]}.json'
            tmp = af.with_suffix('.partial')
            tmp.write_text(json.dumps(a)); os.replace(tmp, af)          # atomic: never a half-written analysis
        except Exception as e:
            err = f'{type(e).__name__}: {str(e)[:160]}'
            if 'TOO_FEW_FRAMES' in err or 'BLACK_TAKE' in err:
                # the file has no usable picture: the generation itself failed (H3 / ComfyUI), not the measuring. Retrying can't help.
                if prior_error != 'BROKEN_TAKE':
                    self._append('TAKE_ANALYSIS_FAILED', {'shot': n, 'take': p['take'], 'error': 'BROKEN_TAKE'})
                    notes.append(f"Shot {n} take {p['take']}: H3 sent back a broken video (no usable picture). The generation itself failed in ComfyUI, "
                                 "not the Director; it stays out of the movie. Generate the shot again.")
                return notes
            if err != prior_error:
                self._append('TAKE_ANALYSIS_FAILED', {'shot': n, 'take': p['take'], 'error': err})
                notes.append(f"Shot {n} take {p['take']}: measuring the take failed ({err}); it will be retried automatically.")
            return notes
        checks = take_checks(st, p, a)
        if iso and iso.get('note'): checks.append(iso['note'])
        ident = None
        try:
            if iso is not None:
                ident = iso.get('identity')
                if iso.get('identity_error'): checks.append(f"IDENTITY: not measured ({iso['identity_error']})")
            else: ident = self.take_identity(st, p, f, a)
        except Exception as e:
            checks.append(f'IDENTITY: not measured ({type(e).__name__})')
        if ident:
            import studio_identity_v090 as idn
            names = {c: st['characters'][c]['name'] for c in p['cast'] if c in st['characters']}
            w, order = idn.identity_warnings(ident, names, p['cast'])
            checks += w; ident['left_to_right'] = order
            if ident.get('talking') and not ident['talking'].get('error'):
                import studio_talking_v0921 as tk
                speakers = [l.get('speaker') for l in (p.get('spec') or {}).get('dialogue', [])[-3:] if l.get('speaker') in names]
                checks += tk.warnings(ident['talking'], names, speakers)
                ident['talking']['shares'] = tk.shares(ident['talking'], names)
            # V0.9.37: where the previous shot ENDS (recognised at its cut) against where this take STARTS (recognised in its
            # first frames). Before, both were each person's average position over the whole take, so a lively take that
            # started right could be flagged, rank lower, and an older take stood in.
            try:
                pe = (self.story(st, before=p['shot']) or [{}])[-1]
                prev = (pe.get('at_cut') or {}).get('left_to_right') if pe.get('take') is not None else None
                lo0 = int(clean_range(a)[0])
                if (p.get('start_frame') and not p.get('carry_frame')) or p.get('film_join') == 'skip': prev = None   # 🎞 your picture / ⏩ time has passed: sides start fresh
                start = self._sides_in(st, f, h, [c for c in p['cast'] if c in st['characters']], tuple(lo0 + 4 * k for k in range(6))) if prev and len(prev) == 2 else []
            except Exception: prev, start = None, []
            if prev and len(start) == 2 and set(start) == set(prev) and start != prev:
                checks.append(f"SCREEN_SIDES: {start[0]} and {start[1]} start on swapped sides compared with where the previous shot ends.")
        placed = None
        try:                                                # 📐 V0.9.69 is everyone where the shot asked (your 📍 dots)?
            wrong, placed = self.take_positions(st, p, f, a)
            if wrong: checks.append('POSITIONS: ' + '; '.join(wrong) + '.')
        except Exception as e:
            if (p.get('spec') or {}).get('positions'): checks.append(f'POSITIONS: not measured ({type(e).__name__})')
        lines = None
        if p.get('pose'):                                   # 🎭: the stick figures must never show in the picture
            import studio_pose_v0921 as ps
            try:
                lines = ps.leaked_lines(f, frames=clean_range(a))
                if ps.lines_flagged(lines):
                    checks.append(f"SKELETON_LINES: lines like the stick figures show in {lines['frames_with_lines']} of {lines['sampled']} sampled frames. "
                                  "This take won't stand in for the shot in the movie unless you ✓ Keep it; try a lower 'how closely' for the movement.")
            except Exception as e: checks.append(f'SKELETON_LINES: not measured ({type(e).__name__})')
        summary = {'frames': a['frames'], 'candidate_cuts': a['candidate_cuts'], 'segments': a['segments'],
                   'end_state': a['end_state'], 'checks': checks, 'identity': ident, **({'pose_lines': lines} if lines is not None else {}),
                   **({'placed': placed} if placed else {}),
                   'movement': media.movement_score(a.get('flow'), fps=float(a.get('fps') or 24))}
        self._append('TAKE_ANALYZED', {'shot': n, 'take': p['take'], 'file': af.name, 'summary': summary})
        if st['settings'].get('qwen', 'manual') != 'off' and p.get('mode') != 'source':     # Qwen observes generated takes only
            try:
                jid = qwen.enqueue_take(f, self.qwen_root())
                self._append('QWEN_JOB_QUEUED', {'shot': n, 'take': p['take'], 'job_id': jid, 'video_sha256': h, 'at': round(time.time())})
                self._qwen_new = True
            except Exception as e:
                notes.append(f'Qwen job could not be queued: {str(e)[:120]}')
        warn = ''
        if a['candidate_cuts']: warn = f" — contains a hidden cut at F{a['candidate_cuts'][0]['frame']}; the editor will only use the clean part"
        if checks: warn += ' — ' + ' '.join(c.rstrip('.') for c in checks)
        notes.append(f"Shot {n} take {p['take']} {'arrived' if first else 'measured after a retry'} ({a['frames']} frames){warn}.")
        self._edited = True
        return notes

    # -- Qwen observer bridge ----------------------------------------------------------
    def qwen_root(self):
        return Path(os.environ.get('DIRECTOR_SAVED_ROOT', '/workspace/DirectorSavedShotQueueV066')) if not getattr(self, 'qwen_saved_root', None) else self.qwen_saved_root
    def qwen_profiles(self):
        st = self.state()
        path, mapping = qwen.write_profile(self.root.parent / '_qwen_profiles', st['project']['slug'], list(st['characters'].values()))
        return path, mapping
    def qwen_ready(self):
        return qwen.installed(getattr(self, 'qwen_paths', None))
    def run_qwen(self, comfy_url=None, popen=None):
        comfy_url = comfy_url or getattr(self, 'comfy_url', None) or 'http://127.0.0.1:18188'
        path, _ = self.qwen_profiles()
        if path is None: raise StudioError('ADD_PEOPLE_BEFORE_QWEN_OBSERVATION')
        kw = {'popen': popen} if popen else {}
        return qwen.run_once(path.parent, self.qwen_root(), comfy_url=comfy_url, v074=(getattr(self, 'qwen_paths', None) or {}).get('v074'), **kw)
    def qwen_status(self, st=None):
        st = st or self.state()
        _, mapping = qwen.write_profile(self.root.parent / '_qwen_profiles', st['project']['slug'], list(st['characters'].values())) if st['characters'] else (None, {})
        names = {c['id']: c['name'] for c in st['characters'].values()}
        out = {}
        for n, sh in st['shots'].items():
            for t in sh['takes']:
                if not t.get('qwen_job'): continue
                s_ = qwen.status(t['qwen_job'], self.qwen_root())
                if s_['state'] == 'DONE': s_ = {'state': 'DONE', 'hints': qwen.hints(s_['summary'], mapping, names)}
                out[f'{n}:{t["take"]}'] = s_
        return out

    def review(self, shot, take, decision, note=None):
        if decision not in ('ACCEPTED', 'REJECTED'): raise StudioError('DECISION')
        st = self.state()
        if not any(t['take'] == take for t in st['shots'].get(shot, {'takes': []})['takes']): raise StudioError('NO_SUCH_TAKE')
        self._append('TAKE_REVIEWED', {'shot': shot, 'take': take, 'decision': decision,
                     'note': (note or '')[:300] or None, 'final_film_approval': False})
        if decision == 'ACCEPTED' and shot in st['excluded']: self._append('CLIP_INCLUDED', {'shot': shot})   # keeping a take puts its shot back in the movie
        try: self._save_taste()                                           # what you keep is learned (and shared with new projects)
        except Exception: pass
        self._autosave_decisions()

    def _autosave_decisions(self):
        try:                                                              # V0.9.42: your decisions file is always up to date
            import studio_decisions_v0942 as dc; dc.autosave(self)
        except Exception: pass

    def set_trim(self, shot, take, cut_in, cut_out):
        st = self.state(); t = self._take(st, shot, take)
        n = t['frames'] or FMT['frames']
        if not (0 <= cut_in < cut_out <= n): raise StudioError('TRIM_OUT_OF_RANGE')
        if cut_out - cut_in < 24: raise StudioError('TRIM_AT_LEAST_1_SECOND: a clip in the movie needs at least 24 frames (1 s)')   # what the render needs
        learn = None
        if st['settings'].get('learn', True):
            tl = self.timeline(st, keep_decisions=True, extra_in={(shot, take): int(cut_in)})
            c = next((c for c in tl if c['shot'] == shot and c['take'] == take), None)
            dec = (c or {}).get('decision')
            if c and c['basis'] == 'AUTO' and dec and int(cut_in) != dec['chosen'] and str(int(cut_in)) in dec['features']:
                learn = {'auto': dec['features'][str(dec['chosen'])], 'human': dec['features'][str(int(cut_in))],
                         'auto_in': dec['chosen'], 'human_in': int(cut_in), 'offset': int(cut_in) - dec['lo'], 'frames': int(n)}
            elif c and c['basis'] == 'AUTO' and tl and tl[0] is c and int(cut_in) != c['in']:     # the movie's first clip: learn where you start it
                lo_, _ = clean_range(self._analysis(t))
                learn = {'first': True, 'offset': max(0, int(cut_in) - lo_), 'frames': int(n)}
        self._append('TRIM_SET', {'shot': shot, 'take': take, 'in': int(cut_in), 'out': int(cut_out), 'basis': 'HUMAN', 'learn': learn})
        self._save_taste(); self._autosave_decisions()

    def set_cuts(self, shot, take, splits, removed):
        """✂ V0.9.68 split a clip into pieces and remove some of them (the middle of a take, its end...). Only decisions
        on top of the take: the file never changes, ↶ Undo reverses it, and the movie renders the pieces you kept."""
        st = self.state(); t = self._take(st, shot, take)
        n = int(t['frames'] or FMT['frames'])
        try:
            sp = sorted({int(x) for x in splits or []})
            rm = sorted({(int(a), int(b)) for a, b in removed or []})
        except (TypeError, ValueError): raise StudioError('CUTS_NOT_FRAME_NUMBERS')
        if len(sp) > 40 or len(rm) > 40: raise StudioError('CUTS_TOO_MANY')
        if any(not 0 < x < n for x in sp) or any(not 0 <= a < b <= n for a, b in rm): raise StudioError('CUTS_OUT_OF_RANGE')
        c = next((c for c in self.timeline(st, ignore_cuts=True) if c['shot'] == shot and c['take'] == take), None)
        if c is None: raise StudioError('CLIP_NOT_IN_THE_MOVIE: only a clip in the movie can be split')
        runs, _ = pieces_of(c['in'], c['out'], sp, rm)
        if not runs: raise StudioError('NOTHING_LEFT: that would remove the whole clip; use ✕ to leave the shot out instead')
        short = [r for r in runs if r[1] - r[0] < 24]
        if short: raise StudioError(f'PIECE_SHORTER_THAN_1_SECOND: frames {short[0][0]}–{short[0][1] - 1} would be left on their own '
                                    '(a piece in the movie needs at least 24 frames); move the split or remove that bit too')
        self._append('CUTS_SET', {'shot': shot, 'take': take, 'splits': sp, 'removed': [list(r) for r in rm], 'basis': 'HUMAN'})
        self._autosave_decisions()

    def exclude(self, shot, on=True):
        self._append('CLIP_EXCLUDED' if on else 'CLIP_INCLUDED', {'shot': shot}); self._autosave_decisions()

    def reorder(self, order):
        st = self.state()
        if sorted(order) != sorted(st['shots']): raise StudioError('ORDER_MUST_LIST_EVERY_SHOT_ONCE')
        self._append('ORDER_SET', {'order': [int(x) for x in order]}); self._autosave_decisions()

    def setting(self, **kw):
        ok = {'auto_edit': bool, 'pacing': str, 'video_refs': str, 'voice_refs': bool, 'clean_refs': str, 'match_color': bool,
              'learn': bool, 'loras': list, 'qwen': str, 'ref_numbering': bool, 'audio_fades': bool, 'audio_overlap': str,
              'max_refs': int, 'identity_check': bool, 'fit_pictures': bool, 'position_check': bool, 'fix_sides': bool, 'gen_length': int, 'speech': str, 'ignore_text': bool,
              'prompt_format': str, 'dialogue_language': str, 'music': str, 'voice_audio': bool, 'enhance': bool, 'match_refs': bool,
              'fresh_models': bool, 'extend_with': str, 'direction_check': bool, 'film_check': bool, 'film_continuity': bool, 'carry_frame': bool, 'carry_redraw': bool, 'carry_ok': bool, 'qwen_model': str, 'film_writer': str, 'night_check_retry': bool, 'talking_check': bool, 'motion': str, 'look': str, 'faithful': bool, 'acting': str, 'steps': int, 'sampler': str, 'steady_color': bool, 'match_originals': bool, 'best_refs': bool, 'faces_per_person': int}
        out = {}
        for k, v in kw.items():
            if k == 'video_refs' and isinstance(v, bool): v = 'auto' if v else 'off'
            if k not in ok or not isinstance(v, ok[k]): raise StudioError('BAD_SETTING_' + k)
            out[k] = v
            if k == 'pacing' and v not in ('natural', 'snappy', 'relaxed'): raise StudioError('PACING_NATURAL_SNAPPY_RELAXED')
            if k == 'clean_refs' and v not in ('auto', 'always', 'never'): raise StudioError('CLEAN_REFS_AUTO_ALWAYS_NEVER')
            if k == 'video_refs' and v not in ('auto', 'off'): raise StudioError('VIDEO_REFS_AUTO_OFF')
            if k == 'qwen' and v not in ('off', 'manual', 'auto'): raise StudioError('QWEN_OFF_MANUAL_AUTO')
            if k == 'audio_overlap' and v not in ('off', 'auto'): raise StudioError('AUDIO_OVERLAP_OFF_AUTO')
            if k == 'extend_with' and v not in ('anchor', 'anchor_clip', 'clip'): raise StudioError('EXTEND_WITH_ANCHOR_ANCHOR_CLIP_CLIP')
            if k == 'qwen_model' and v not in ('8b', '32b', '27b'): raise StudioError('QWEN_MODEL_8B_32B_OR_27B')
            if k == 'film_writer' and v not in ('same', 'think_plan', 'think_all'): raise StudioError('FILM_WRITER_SAME_THINK_PLAN_OR_THINK_ALL')
            if k == 'speech' and v not in ('natural', 'lines_only', 'silent'): raise StudioError('SPEECH_NATURAL_LINES_ONLY_SILENT')
            if k == 'prompt_format' and v not in ('official', 'classic'): raise StudioError('PROMPT_FORMAT_OFFICIAL_CLASSIC')
            if k == 'motion' and v not in ('lively', 'natural'): raise StudioError('MOTION_LIVELY_OR_NATURAL')
            if k == 'look' and v not in ('real_life', 'cinematic'): raise StudioError('LOOK_REAL_LIFE_OR_CINEMATIC')
            if k == 'acting' and v not in ('expressive', 'understated'): raise StudioError('ACTING_EXPRESSIVE_OR_UNDERSTATED')
            if k == 'steps' and not 12 <= v <= 40: raise StudioError('STEPS_12_TO_40')
            if k == 'sampler' and v not in SAMPLERS: raise StudioError('SAMPLER_EULER_OR_RES_MULTISTEP')
            if k == 'dialogue_language' and not re.fullmatch(r'[A-Za-z ]{2,24}', v): raise StudioError('DIALOGUE_LANGUAGE')
            if k == 'music' and len(v) > 200: raise StudioError('MUSIC_TOO_LONG')
            if k == 'max_refs' and not 1 <= v <= MAX_REFS: raise StudioError('MAX_REFS_1_TO_9')
            if k == 'faces_per_person' and not 1 <= v <= 4: raise StudioError('FACES_PER_PERSON_1_TO_4')
            if k == 'gen_length': v = out[k] = OLD_GEN_LENGTHS.get(v, v)             # an older 96 means what H3 makes for it: 107
            if k == 'gen_length' and v not in GEN_LENGTHS: raise StudioError('GEN_LENGTH_' + '_'.join(map(str, sorted(GEN_LENGTHS))))
            if k == 'loras':
                st = self.state(); clean = []
                for l in v[:4]:
                    name = str(l.get('name', '')).strip()
                    if not re.fullmatch(r'[A-Za-z0-9_.\-/ ]{1,160}\.(safetensors|pt|ckpt)', name) or '..' in name: raise StudioError('LORA_NAME')
                    strength = float(l.get('strength', 0.8))
                    if not -2.0 <= strength <= 2.0: raise StudioError('LORA_STRENGTH_-2_TO_2')
                    scope = l.get('scope', 'all')
                    if scope != 'all' and scope not in st['characters']: raise StudioError('LORA_SCOPE_ALL_OR_A_PERSON')
                    clean.append({'name': name, 'strength': round(strength, 3), 'scope': scope,
                                  'trigger': str(l.get('trigger', ''))[:80], 'enabled': bool(l.get('enabled', True))})
                out['loras'] = clean
        self._append('SETTING_SET', out)

    def discard(self, shot, take=None):
        """Discard a planned shot (every take of it not generated yet), or one planned/queued take of it (V0.9.20).
        A take that already arrived is never discarded (reject it instead). Discarding the last shot frees its number.
        Returns the prompt ids still in ComfyUI (self.last_cancel) so the web app takes them out of its queue too."""
        st = self.state(); sh = st['shots'].get(shot)
        if not sh: raise StudioError(f'NO_SUCH_SHOT_{shot}')
        arrived = {t['take'] for t in sh['takes']}
        planned = sorted({q['take'] for q in sh['plans']})
        if take is not None:
            if take not in planned: raise StudioError(f'NO_SUCH_TAKE_{shot}_{take}')
            if take in arrived: raise StudioError(f'TAKE_{take}_OF_SHOT_{shot}_WAS_ALREADY_GENERATED: reject it instead (✗ Reject)')
            targets = [take]
        else:
            targets = [t for t in planned if t not in arrived]
            if not targets: raise StudioError(f'SHOT_{shot}_HAS_NOTHING_TO_DISCARD: every take of it was generated (reject takes instead)')
        ch = st.get('chain') or {}
        if ch and shot in {(x[0] if isinstance(x, (list, tuple)) else x) for x in (ch.get('shots') or [])}:
            raise StudioError('STOP_THE_STORYBOARD_RUN_FIRST: this shot is part of the run in order (press Stop, then discard)')
        cancel = [x['prompt_id'] for x in st['submissions'] if x['shot'] == shot and x['take'] in targets and x.get('prompt_id')]
        undone = {e['payload']['target'] for e in st['events'] if e['kind'] == 'UNDONE'}
        seqs = [e['seq'] for e in st['events'] if e['kind'] == 'SHOT_PLANNED' and e['seq'] not in undone
                and e['payload']['shot'] == shot and e['payload']['take'] in targets]
        for q in seqs: self._append('UNDONE', {'target': q, 'kind': 'SHOT_PLANNED', 'discarded': True})
        self.last_cancel = cancel
        whole = len(targets) == len([t for t in planned]) and not arrived
        return {'shot': shot, 'takes': targets, 'whole_shot': whole, 'in_comfyui': len(cancel)}

    def undo(self):
        """Undo the last change. A planned shot whose take already arrived can't be undone (reject the take instead);
        undoing one that was sent to ComfyUI but hasn't arrived returns its prompt id so the web app can cancel it there."""
        st = self.state()
        if not st['undoable']: return None
        e = st['undoable'][-1]; cancel = []
        if e['kind'] == 'SHOT_PLANNED':
            p = e['payload']
            if any(t['take'] == p['take'] for t in st['shots'].get(p['shot'], {'takes': []})['takes']):
                raise StudioError(f"CANNOT_UNDO_SHOT_{p['shot']}_TAKE_{p['take']}_IT_WAS_ALREADY_GENERATED: reject the take instead (✗ Reject)")
            cancel = [x['prompt_id'] for x in st['submissions'] if x['shot'] == p['shot'] and x['take'] == p['take'] and x.get('prompt_id')]
        self._append('UNDONE', {'target': e['seq'], 'kind': e['kind']})
        self.last_cancel = cancel
        return e['kind']

    def _take(self, st, shot, take):
        for t in st['shots'].get(shot, {'takes': []})['takes']:
            if t['take'] == take: return t
        raise StudioError('NO_SUCH_TAKE')

    # -- the movie ----------------------------------------------------------------------
    # -- learning ---------------------------------------------------------------------
    def taste(self, st=None):
        """Learned cutting taste: prior = other projects' taste (if any), then this project's corrections."""
        st = st or self.state()
        prior = dict(DEFAULT_W); n_prior = 0
        tdir = self.root.parent / '_taste'
        if tdir.is_dir():
            acc = {k: 0.0 for k in FEATURES}; tot = 0
            for f in tdir.glob('*.json'):
                if f.stem == self.slug: continue
                try: j = json.loads(f.read_text())
                except ValueError: continue
                if j.get('schema') != 'director-taste-v081' or not j.get('corrections'): continue
                for k in FEATURES: acc[k] += j['weights'][k] * j['corrections']
                tot += j['corrections']
            if tot:
                prior = {k: (acc[k] / tot) * 0.7 + DEFAULT_W[k] * 0.3 for k in FEATURES}; n_prior = tot
        prefs = st.get('prefs', []) if st['settings'].get('learn', True) else []
        cut_prefs = [p for p in prefs if not p.get('first')]
        w = learn_taste(prior, [(p['auto'], p['human']) for p in cut_prefs])
        offs = [p['offset'] for p in cut_prefs]
        base_target = 18 + 18 * PACING_IN.get(st['settings'].get('pacing', 'natural'), 0)
        target = (base_target * 2 + sum(offs)) / (2 + len(offs)) if offs else base_target
        return {'weights': w, 'pace_target': round(max(0, min(48, target)), 1), 'corrections': len(cut_prefs),
                'first_clip_corrections': len(prefs) - len(cut_prefs),
                'prior_corrections_from_other_projects': n_prior, 'defaults': DEFAULT_W, '_prefs': prefs, '_base': base_target}

    @staticmethod
    def pace_for(taste, frames, first=False):
        """How far into a take YOU like the cut to enter, for takes of this length (frames): learned from your trims of
        takes within +/-20 frames of it once there are 2 or more, else the overall average. 7 s takes learn their own."""
        prefs = [p for p in taste.get('_prefs', []) if bool(p.get('first')) == first]
        near = [p['offset'] for p in prefs if p.get('frames') and abs(int(p['frames']) - int(frames)) <= 20]
        cap = max(48, int(0.45 * int(frames)))
        if len(near) >= 2:
            base = taste.get('_base', 18) if not first else 0
            return round(max(0, min(cap, (base + 2 * sum(near)) / (1 + 2 * len(near)))), 1)
        if first: return None                                             # first clips: no habit yet -> the frozen-start rule
        return taste['pace_target']

    def _save_taste(self):
        t = self.taste(); d = self.root.parent / '_taste'; d.mkdir(exist_ok=True)
        try: m = self.rank_model(self.state())
        except Exception: m = {'w': None, 'pairs': 0}
        (d / f'{self.slug}.json').write_text(json.dumps({'schema': 'director-taste-v081', 'weights': t['weights'],
                                                         'corrections': t['corrections'], 'rank_w': m['w'], 'rank_pairs': m['pairs']}, indent=1))

    def lessons(self, st, cast):
        """Filmmaker's own review notes about earlier takes of these people (newest first, max 3 each kind)."""
        if not st['settings'].get('learn', True): return {'avoid': [], 'keep': []}
        cleared = st.get('lessons_cleared_at', 0)
        avoid, keep = [], []
        for e in st['events']:
            if e['kind'] != 'TAKE_REVIEWED' or e['seq'] <= cleared or not e['payload'].get('note'): continue
            p = e['payload']; sh = st['shots'].get(p['shot'])
            if not sh: continue
            plan = next((x for x in sh['plans'] if x['take'] == p['take']), sh['plans'][-1])
            if cast and not set(plan['cast']) & set(cast): continue
            (avoid if p['decision'] == 'REJECTED' else keep).append(p['note'])
        return {'avoid': list(dict.fromkeys(reversed(avoid)))[:3], 'keep': list(dict.fromkeys(reversed(keep)))[:3]}

    def clear_lessons(self):
        self._append('LESSONS_CLEARED', {})

    def timeline(self, st=None, keep_decisions=False, extra_in=None, ignore_cuts=False):
        """Derived movie: for each shot (in order) the best available take, cut intelligently.
        Accepted take > newest unreviewed take; rejected takes never used. Human trims win."""
        st = st or self.state()
        taste = self.taste(st)
        cache = {}
        def A(t):
            if t['analysis_file'] not in cache: cache[t['analysis_file']] = self._analysis(t)
            return cache[t['analysis_file']]
        order = [n for n in (st['order'] or []) if n in st['shots']]
        order += [n for n in sorted(st['shots']) if n not in order]      # shots added after a reorder go at the end
        clips = []
        for n in order:
            if n in st['excluded'] or n not in st['shots']: continue
            takes = [t for t in st['shots'][n]['takes'] if t['analysis'] and t.get('analysis_error') not in ('TAKE_FILE_MISSING', 'TAKE_FILE_CHANGED')]
            def readable(t):
                try: A(t); return True
                except Exception as e:                        # one broken take must never take the whole movie (and the page) down
                    self.timeline_problems = getattr(self, 'timeline_problems', []) + [f"Shot {n} take {t['take']}: {str(e)[:120]}"]; return False
            takes = [t for t in takes if readable(t)]
            acc = [t for t in takes if t['status'] == 'ACCEPTED']
            auto = st['settings'].get('auto_edit', True)
            if acc: cand = sorted(acc, key=lambda t: (t.get('reviewed_seq') or 0, t['take']))[-1:]   # the take you kept MOST RECENTLY
            elif auto:
                capt = [t for t in takes if t['status'] == 'CAPTURED']
                import studio_pose_v0921 as _ps                  # 🎭 a take with stick-figure lines in it never stands in on its own
                capt = [t for t in capt if not _ps.lines_flagged((t.get('analysis') or {}).get('pose_lines'))]
                cand = sorted(capt, key=lambda t: (-self.rank_score(st, t, n), -t['take']))[:1] if len(capt) > 1 else capt[-1:]
            else: cand = []
            if cand: clips.append({'shot': n, 'take': cand[0]['take'], 'status': cand[0]['status'], 't': cand[0]})
        pacing = st['settings'].get('pacing', 'natural')
        out = []; prev = None
        for c in clips:
            a = A(c['t'])
            key = f"{c['shot']}:{c['take']}"
            lo, hi = clean_range(a)
            decision = None
            pmode = (next((q for q in st['shots'][c['shot']]['plans'] if q['take'] == c['take']), {}) or {})
            # your trim (clipped to the clean part); shorter than 1 s it can't be rendered: the Director's own cut is used instead
            tr = st['trims'].get(key) if not (extra_in and (c['shot'], c['take']) in extra_in) else None
            if tr and min(hi, tr['out']) - max(lo, tr['in']) < 24:
                self.timeline_problems = getattr(self, 'timeline_problems', []) + [
                    f"Shot {c['shot']} take {c['take']}: your trim is shorter than 1 s (after the take's unusable start/end), so the Director's own cut is used; trim it again"]
                tr = None
            if not tr and pmode.get('mode') == 'source':
                tin, tout, why, basis = lo, hi, 'your original video (its ending), as the start of the extension', 'AUTO'
            elif not tr and pmode.get('extend'):
                if pmode.get('anchors'):
                    tin = min(max(lo, media.ANCHOR_FRAMES), max(lo, hi - 12)); why = 'continues your original video: starts right after its pinned last second'
                else:
                    tin = lo; why = 'continues your original video from its last frame'
                tout = tail_cut(a, tin, hi); basis = 'AUTO'
            elif tr:
                tin, tout = max(lo, tr['in']), min(hi, tr['out']); why = 'your trim'; basis = 'HUMAN'
            elif prev is None:
                tin, tout, why = first_clip_cut(a, lo, hi, learned=self.pace_for(taste, a['frames'], first=True)); basis = 'AUTO'
            else:
                pa, pc = prev
                pkey = f"{pc['shot']}:{pc['take']}"
                ex = [(extra_in or {}).get((c['shot'], c['take']))] if (extra_in or {}).get((c['shot'], c['take'])) is not None else []
                _, tin, why, decision = choose_cut(pa, pc['in'], pc['out'], a, lo, hi, pacing, weights=taste['weights'],
                                                   pace_target=self.pace_for(taste, a['frames']), extra=ex)
                tout = tail_cut(a, tin, hi); basis = 'AUTO'
            lead = (pmode.get('spec') or {}).get('lead_in') if basis == 'AUTO' else None
            if lead:                                           # ⏱ V0.9.48 the move into place is not part of the movie
                min_in = int(round(float(lead) * (a.get('fps') or 24)))
                if tin < min_in and hi - min_in >= 24:
                    tin = min_in; why = f'{why}; cut in after the {float(lead):g} s lead-in'
                    if tout - tin < 24: tout = tail_cut(a, tin, hi)
            if basis == 'AUTO':
                try: want = (self.plan_record(st, c['shot'], c['take']).get('spec') or {}).get('duration')
                except StudioError: want = None
                if want and want != 'full':
                    tout2, why2 = length_cut(a, tin, hi, float(want))
                    if tout2: tout = tout2; why = f'{why}; {why2}'
            clip = {'shot': c['shot'], 'take': c['take'], 'status': c['status'], 'file': c['t']['file'], 'take_frames': int(a.get('frames') or c['t'].get('frames') or 0),
                    'sha256': c['t']['sha256'], 'in': int(tin), 'out': int(tout), 'why': why, 'basis': basis,
                    'warnings': warnings_for(a, tin, tout) + list((c['t']['analysis'] or {}).get('checks', []))}
            if keep_decisions and basis == 'AUTO' and prev is not None: clip['decision'] = decision
            cu = None if ignore_cuts else st.get('cuts', {}).get(key)
            if cu and (cu['splits'] or cu['removed']):           # ✂ V0.9.68 the pieces you kept
                clip['span'] = [int(tin), int(tout)]
                runs, view = pieces_of(int(tin), int(tout), cu['splits'], cu['removed'])
                keep = [r for r in runs if r[1] - r[0] >= 24]
                if len(keep) < len(runs):
                    self.timeline_problems = getattr(self, 'timeline_problems', []) + [
                        f"Shot {c['shot']} take {c['take']}: a piece shorter than 1 s (after a trim) is left out; adjust its split"]
                if not keep:
                    self.timeline_problems = getattr(self, 'timeline_problems', []) + [f"Shot {c['shot']} take {c['take']}: every piece is removed"]
                    continue
                clip['pieces'] = keep; clip['piece_view'] = view; clip['cuts'] = cu
                clip['in'], clip['out'] = keep[0][0], keep[-1][1]
                if len(keep) > 1 or keep[0] != [int(tin), int(tout)]: clip['why'] = f"{clip['why']}; ✂ your cuts ({len(keep)} piece{'s' if len(keep) > 1 else ''})"
            out.append(clip); prev = (a, clip)
        by_take = {(c['shot'], c['take']): A(c['t']) for c in clips}
        if st['settings'].get('audio_overlap', 'auto') == 'auto':
            audio_overlaps(out, [by_take[(c['shot'], c['take'])] for c in out])
        for i in range(1, len(out)):
            j = jump_cut(by_take[(out[i - 1]['shot'], out[i - 1]['take'])], out[i - 1]['out'], by_take[(out[i]['shot'], out[i]['take'])], out[i]['in'])
            if j: out[i]['warnings'].append(j)
            sw = side_switch(st, out[i - 1], out[i])
            if sw: out[i]['warnings'].append(sw)
        for i, j in enumerate(self.look_checks(st, out)):
            if j:
                out[i]['look'] = j
                if not j['fixed']: out[i]['warnings'].append(look_text(j, f"shot {out[i - 1]['shot']}"))
        return out

    def _frame_lab(self, t, path):
        """Per-frame Lab look of a take (cached by its hash)."""
        import studio_color_v0933 as sc
        f = self.root / 'analysis' / f"lab_{t['sha256'][:16]}_{sc.VERSION}.json"
        if f.is_file():
            try: return json.loads(f.read_text())
            except ValueError: pass
        stats = sc.video_stats(path); f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(stats))
        return stats

    def _lab_grade(self, st, tl, segs):
        """🎨 V0.9.33: each generated clip -> a graded copy (steady within the shot, matched to YOUR originals), cached.
        The take itself never changes; your own videos are never graded."""
        import studio_color_v0933 as sc
        s = st['settings']; steady = s.get('steady_color', True) is not False; match = s.get('match_originals', True) is not False
        anchor, first = None, None
        for sg, c in zip(segs, tl):
            t = self._take(st, c['shot'], c['take']); path = self.take_path(t)
            if t.get('source'):                                # your video: never touched; later shots match its ending
                anchor = sc.mean_stats(sc.video_stats(path, max(c['in'], c['out'] - 48), c['out'], step=3)); continue
            stats = self._frame_lab(t, path)
            target, label = None, None
            if match and anchor: target, label = anchor, 'the end of your original video'
            elif match:
                o = self._originals_look(st, c['shot'], c['take'])
                if o: target, label = o[0], o[2]
                elif first: target, label = first, 'the first shot'
            if first is None: first = sc.mean_stats(stats[c['in']:c['in'] + sc.ANCHOR])
            params = sc.plan(stats, c['in'], c['out'], target=target, steady=steady)
            if not params: continue
            k = sc.key(t['sha256'], c['in'], c['out'], {x: round(v, 1) for x, v in (target or {}).items()}, steady)
            dst = self.root / 'renders' / 'graded' / f"{Path(c['file']).stem}_c{k}.mp4"
            if not dst.is_file(): sc.grade_file(path, dst, c['in'], c['out'], params)
            sg.update(filename=dst.name, sha256=media.sha256(dst),
                      color={'from_sha256': c['sha256'], 'steady': steady, 'target': label, 'method': sc.VERSION})
        return segs

    def color_mode(self, st):
        """🎨 V0.9.33: 'lab' (steady within a shot and/or matched to your originals, frame by frame), 'classic' (the old
        one fixed step toward the first clip) or None (Settings: match colour off)."""
        s = st['settings']
        if not s.get('match_color', True): return None
        return 'lab' if (s.get('steady_color', True) or s.get('match_originals', True)) else 'classic'

    _ORIG_STATS = {}
    def _originals_look(self, st, shot, take):
        """The look of YOUR originals for one clip: your photo/video of the shot's place, else the people's own photos.
        -> (lab stats, bgr stats, label) or None. Only raw originals (never cut-outs or colour-matched copies)."""
        import studio_color_v0933 as sc
        p = self.plan_record(st, shot, take) or {}
        R = st['refs']
        def raw(rid): r = R.get(rid) or {}; return r and not r.get('removed') and r.get('variant', 'raw') == 'raw' and not r.get('matched_from')
        loc = st['locations'].get(p.get('location')) if p.get('location') else None
        picks, label = [], None
        if loc:
            picks = [r for r in loc['refs'] if raw(r) and R[r]['kind'] == 'location']; label = f"your photo of {loc['name']}"
        if not picks:
            for c in p.get('cast') or []:
                ch = st['characters'].get(c)
                if ch: picks += [r for r in ch['refs'] if raw(r) and R[r]['kind'] in ('full', 'upper')]
            label = 'the people\'s own photos'
        if not picks: return None
        k = tuple(sorted(R[r]['sha256'] for r in picks[:6]))
        if k not in Studio._ORIG_STATS:
            ims = []
            for r in picks[:6]:
                try: ims.append(media.load_image(self.input_dir / R[r]['input_name']))
                except Exception: pass
            Studio._ORIG_STATS[k] = (sc.mean_stats([sc.lab_stats(im) for im in ims]), media.rgb_stats(ims)) if ims else None
        got = Studio._ORIG_STATS[k]
        return (got[0], got[1], label) if got and got[0] else None

    def _grades(self, st, tl, lazy=False):
        """The bounded colour grade render gives each clip (None = untouched): toward the first clip, or toward the end of
        your original video once one is in the movie. Your own videos are never graded. V0.9.33 ('lab' mode): what the
        cut check should expect (the frame-by-frame grade itself is made in render())."""
        out = [None] * len(tl)
        if not self.color_mode(st) or len(tl) < 2: return out
        lab_mode = self.color_mode(st) == 'lab' and st['settings'].get('match_originals', True)
        stats = [self._analysis(self._take(st, c['shot'], c['take'])).get('rgb_stats') for c in tl]
        ref = stats[0]; anchor_src = None
        for i, (stt, c) in enumerate(zip(stats, tl)):
            t = self._take(st, c['shot'], c['take'])
            if t.get('source'):
                key = ('src', t.get('sha256'), c['in'], c['out'])
                if key not in Studio._LOOK_CACHE:
                    job = (lambda p=self.take_path(t), a=max(c['in'], c['out'] - 48), b=c['out'], s0=stt: media.rgb_stats_window(p, a, b) or s0)
                    if lazy: Studio._LOOK_WANT[key] = job; anchor_src = stt; continue       # the page: measured in the background
                    Studio._LOOK_CACHE[key] = job()
                anchor_src = Studio._LOOK_CACHE[key]; continue
            if lab_mode and not anchor_src:                    # V0.9.33: toward YOUR originals of that place / those people
                o = self._originals_look(st, c['shot'], c['take'])
                if o: out[i] = color_grade(o[1], stt, max_gain=0.25, max_off=30.0); continue
            if i == 0: continue
            g = color_grade(anchor_src, stt, max_gain=0.25, max_off=30.0) if anchor_src else color_grade(ref, stt)
            if g and anchor_src: g = {**g, 'method': 'BOUNDED_MEAN_STD_MATCH_TO_THE_END_OF_YOUR_ORIGINAL_VIDEO'}
            out[i] = g
        return out

    _LOOK_CACHE = {}
    _LOOK_WANT = {}               # key -> job: measurements the page asked for; the background worker does them
    def _look_frames(self, t):
        """Per-frame look of a take (from its measurement). Takes measured before V0.9.8 lack it: it is read from the file
        once in the background (warm_looks) and cached by hash; until then the look check skips that cut."""
        lf = (self._analysis(t) or {}).get('look_frames')
        if lf: return lf
        key = ('lf', t.get('sha256'))
        if key not in Studio._LOOK_CACHE:
            Studio._LOOK_WANT[key] = (lambda p=self.take_path(t): media.look_frames_of(p)); return None
        return Studio._LOOK_CACHE[key]

    @classmethod
    def warm_looks(cls, max_seconds=4.0):
        """Background worker: do the queued look measurements for a few seconds. Returns how many are still waiting."""
        t0 = time.time()
        for key in list(cls._LOOK_WANT):
            job = cls._LOOK_WANT.pop(key, None)
            if job is None: continue
            try: cls._LOOK_CACHE[key] = job()
            except Exception: cls._LOOK_CACHE[key] = None
            if time.time() - t0 > max_seconds: break
        return len(cls._LOOK_WANT)

    def look_checks(self, st, tl):
        """For each clip after the first: how it looks next to the end of the clip before it (after render's grade)."""
        grades = self._grades(st, tl, lazy=True); out = [None] * len(tl)
        for i in range(1, len(tl)):
            try:
                a, b = self._take(st, tl[i - 1]['shot'], tl[i - 1]['take']), self._take(st, tl[i]['shot'], tl[i]['take'])
                if b.get('source'): continue                                   # your own video is never judged or graded
                out[i] = look_jump(self._look_frames(a), tl[i - 1]['out'], self._look_frames(b), tl[i]['in'], grades[i - 1], grades[i])
            except Exception: out[i] = None
        return out

    def take_look(self, st, tl, shot, t):
        """Look check for one take (kept or not) against the clip just before its shot in the movie: 'warmer, 15% darker
        than shot 2 ends' and whether render's colour match evens it out. None when it matches or can't be measured."""
        try:
            if t.get('source') or not t.get('analysis'): return None
            order = [c['shot'] for c in tl]
            if shot in order:
                i = order.index(shot)
                if tl[i]['take'] == t['take']:
                    j = tl[i].get('look'); Studio._LOOK_BY_SHA[t.get('sha256')] = j or {'raw': []}
                    return {**j, 'vs_shot': tl[i - 1]['shot'], 'text': look_text(j, f"shot {tl[i - 1]['shot']}")} if j else None
                before = tl[:i]
            else:
                before = [c for c in tl if c['shot'] < shot]
            if not before: return None
            here = {'shot': shot, 'take': t['take'], 'in': 0, 'out': int(t.get('frames') or 124)}
            key = ('take_look', t.get('sha256'), tuple((c['shot'], c['take'], c['in'], c['out']) for c in before), bool(st['settings'].get('match_color', True)))
            if key in Studio._LOOK_CACHE: j = Studio._LOOK_CACHE[key]
            else:
                waiting = len(Studio._LOOK_WANT); j = self.look_checks(st, before + [here])[-1]
                if len(Studio._LOOK_WANT) == waiting: Studio._LOOK_CACHE[key] = j          # only once every measurement was there
            if key in Studio._LOOK_CACHE: Studio._LOOK_BY_SHA[t.get('sha256')] = j or {'raw': []}     # for the take ranking
            return {**j, 'vs_shot': before[-1]['shot'], 'text': look_text(j, f"shot {before[-1]['shot']}")} if j else None
        except Exception: return None

    _ANALYSIS_CACHE = {}          # measurement files never change once written: keep them parsed (keyed by path, size, mtime)

    def take_movement(self, t):
        """🏃 V0.9.27: how much the people move in a take (measured). Older takes: worked out once from their analysis file."""
        a = t.get('analysis')
        if not a: return None
        if 'movement' in a: return a['movement']
        key = t.get('analysis_file'); cache = self.__dict__.setdefault('_movement_cache', {})
        if key not in cache:
            try: full = self._analysis(t); cache[key] = media.movement_score(full.get('flow'), fps=float(full.get('fps') or 24))
            except Exception: cache[key] = None
        return cache[key]

    def _analysis(self, t):
        af = self.root / 'analysis' / t['analysis_file']
        try:
            stt = af.stat(); key = (str(af), stt.st_size, stt.st_mtime_ns)
            hit = Studio._ANALYSIS_CACHE.get(key)
            if hit is not None: return hit
            a = json.loads(af.read_text())
            if len(Studio._ANALYSIS_CACHE) > 400: Studio._ANALYSIS_CACHE.clear()
            Studio._ANALYSIS_CACHE[key] = a
            return a
        except Exception:
            f = self.take_path(t)                             # self-heal: re-measure the same verified take
            if not f.is_file() or media.sha256(f) != t['sha256']: raise StudioError(f"TAKE_{t['file']}_MISSING_OR_CHANGED")
            a = self._measure_isolated(t['shot'], self.plan_record(self.state(), t['shot'], t['take']), f)['analysis'] if getattr(self, 'isolate', False) else media.analyze_take(f)
            tmp = af.with_suffix('.partial'); tmp.write_text(json.dumps(a)); os.replace(tmp, af)
            return a
    def _analysis_by(self, clip):
        st = self.state(); return self._analysis(self._take(st, clip['shot'], clip['take']))

    MIN_FREE_GB = 2.0

    def _need_disk(self, what):
        """Stop before a take or a movie can't be written (Vast disks fill up quietly)."""
        for d in (self.output_dir, self.root):
            try: free = shutil.disk_usage(d).free / 1e9
            except OSError: continue
            if free < self.MIN_FREE_GB:
                raise StudioError(f'DISK_ALMOST_FULL: only {free:.1f} GB free where {"takes" if d == self.output_dir else "the project"} are saved; '
                                  f'free some space (old renders, backups, unused takes) before you {what}.')

    def render(self):
        self._need_disk('render the movie')
        tl = self.timeline()
        if not tl: raise StudioError('NOTHING_TO_RENDER_YET')
        segs = [{'shot': i + 1, 'filename': c['file'], 'sha256': c['sha256'], 'in': c['in'], 'out': c['out']} for i, c in enumerate(tl)]
        st = self.state()
        for sg, c in zip(segs, tl):
            if c.get('a_in') is not None: sg['a_in'] = c['a_in']
            if c.get('a_out') is not None: sg['a_out'] = c['a_out']
            if st['settings'].get('audio_fades', True): sg['afade'] = AUDIO_FADE
            fr = (self._take(st, c['shot'], c['take']) or {}).get('frames')
            if fr and int(fr) != 124: sg['frames'] = int(fr)
        if self.color_mode(st) == 'lab': segs = self._lab_grade(st, tl, segs)       # 🎨 V0.9.33
        else:
            for sg, g in zip(segs, self._grades(st, tl)):
                if g: sg['grade'] = g
        segs = expand_pieces(segs, tl)                                            # ✂ V0.9.68 the pieces you kept
        for i, sg in enumerate(segs): sg['shot'] = i + 1
        key = hashlib.sha256(canonical(segs).encode()).hexdigest()[:10]
        dest = self.root / 'renders' / f"{self.slug}_movie_{len(segs)}clips_{key}.mp4"
        if dest.exists(): return {'path': str(dest), 'reused': True, 'timeline': tl}
        res = dv.render(segs, [self.output_dir, self.root / 'sources', self.root / 'renders' / 'graded'], dest)
        self._append('RENDERED', {'path': dest.name, 'sha256': res['sha256'], 'frames': res['frames'], 'segments': segs})
        return {**res, 'timeline': tl}

    def summary(self):
        st = self.state(); self.timeline_problems = []; tl = self.timeline(st)
        total = sum(b - a for a, b in (r for c in tl for r in (c.get('pieces') or [[c['in'], c['out']]])))   # ✂ V0.9.68
        return {'title': st['project']['title'], 'settings': st['settings'],
                'characters': [{'id': c['id'], 'name': c['name'], 'pronoun': c['pronoun'], 'descriptors': c['descriptors'],
                                'description': c.get('description', ''), 'retired': bool(c.get('retired')),
                                'voice': c.get('voice', ''), 'main_source': c.get('main_source'), 'voice_ref': c.get('voice_ref'),
                                'refs': [{'id': r, 'kind': st['refs'][r]['kind'], 'variant': st['refs'][r].get('variant', 'raw'), 'input_name': st['refs'][r]['input_name'],
                                          'matched': bool(st['refs'][r].get('matched_from')), 'owner': c['id'],
                                          'source_sha256': st['refs'][r].get('source_sha256'), 'support_of': st['refs'][r].get('support_of'),   # ⭐ V0.9.65
                                          **({k: st['refs'][r].get(k) for k in ('seconds', 'speech_seconds', 'snr_db')} if st['refs'][r]['kind'] == 'voice' else {}),
                                          **(self._clip_info(st['refs'][r]) if st['refs'][r]['kind'] == 'ref_clip' else {})} for r in c['refs']]}
                               for c in st['characters'].values()],
                'locations': [{'id': l['id'], 'name': l['name'], 'description': l.get('description', ''), 'retired': bool(l.get('retired')),
                               'refs': [{'id': r, 'kind': st['refs'][r]['kind'], 'input_name': st['refs'][r]['input_name'], 'matched': bool(st['refs'][r].get('matched_from'))} for r in l['refs']]}
                              for l in st['locations'].values()],
                'shots': [{'shot': n, 'plans': [{k: p.get(k) for k in ('take', 'text', 'understood_as', 'cast', 'workflow_file', 'prefix', 'assumptions', 'missing_refs_for', 'start_frame', 'film', 'start_pic', 'carry_frame', 'film_join', 'frame_ok', 'built_for')} | ({'pose': {**{k: p['pose'][k] for k in ('label', 'strength', 'start')}, 'who': (st['characters'].get(p['pose'].get('who')) or {}).get('name')}} if p.get('pose') else {}) | ({'positions': p['spec']['positions']} if isinstance((p.get('spec') or {}).get('positions'), dict) else {}) for p in s['plans']],   # 📍 V0.9.56 ✎ Edit brings your dots back
                           'source': bool(s.get('source')),
                           'takes': [{k: t.get(k) for k in ('take', 'file', 'status', 'note', 'frames', 'analysis_error', 'imported', 'source', 'remember', 'attached_by_hand', 'match_problem', 'check')} |
                                     {'measured': t.get('analysis') is not None} |
                                     {'identity': {st['characters'][c]['name']: v.get('median') for c, v in
                                                   (((t.get('analysis') or {}).get('identity') or {}).get('people') or {}).items() if c in st['characters']} or None} |
                                     {'talking': ((((t.get('analysis') or {}).get('identity') or {}).get('talking') or {}).get('shares') or None)} |
                                     {'movement': self.take_movement(t)} |
                                     {'cuts': [c['frame'] for c in (t['analysis'] or {}).get('candidate_cuts', [])]} |
                                     {'look': self.take_look(st, tl, n, t)} for t in s['takes']]}
                          for n, s in sorted(st['shots'].items())],
                'action_photos': [{k: a[k] for k in ('id', 'name', 'file')} for a in st.get('action_photos', {}).values() if not a.get('removed')],
                'timeline': tl, 'movie_frames': total, 'movie_seconds': round(total / FMT['fps'], 2),
                'taste': {k: v for k, v in self.taste(st).items() if not k.startswith('_')}, 'lessons': self.lessons(st, []), 'qwen': self.qwen_status(st),
                'can_undo': bool(st['undoable']),
                'waiting_for': [{'shot': x['shot'], 'take': x['take'], 'prompt_id': x.get('prompt_id')} for x in st['submissions']
                                if not any(t['take'] == x['take'] for t in st['shots'].get(x['shot'], {'takes': []})['takes'])],
                'story_so_far': self.story(st, tl=tl), 'ab': self.ab_summary(st), 'unsent': self.unsent_plans(st),
                'open_plan': self.open_plan(st), 'originals': self.originals(st), 'last_render': next((e['payload']['path'] for e in reversed(st['events']) if e['kind'] == 'RENDERED'), None),
                'ranking': {n: self.rank_takes(st, n) for n, sh in st['shots'].items() if len(sh['takes']) > 1},
                'start_frames': self.start_frames(st),
                'take_habits': {k: v for k, v in self.rank_model(st).items() if k in ('pairs', 'decisions', 'pairs_other_projects', 'trust', 'habits', 'min_pairs', 'agreement')},
                'timeline_problems': list(dict.fromkeys(getattr(self, 'timeline_problems', []))),
                'chain': st.get('chain'),
                'refused': {f"{e['payload']['shot']}:{e['payload']['take']}": e['payload']['reason'][:200] for e in st['events'] if e['kind'] == 'TAKE_REFUSED'
                            and not any(t['take'] == e['payload']['take'] for t in st['shots'].get(e['payload']['shot'], {'takes': []})['takes'])}}

