#!/usr/bin/env python3
"""H3 Reference Director V0.7.39: one entry point, ordinary language.

    python director_v0739.py "make her react"
    python director_v0739.py "try a closer angle, give it some time"
    python director_v0739.py "where are we?"
    python director_v0739.py "go back to my cut"            (edit the working film)
    python director_v0739.py "looks good"  ->  "yes"        (approve a captured take)

Every run:
  1. Opens the persistent project (V0.7.35 base ledger + V0.7.39 lifecycle ledger +
     this module's direction ledger, all hash-chained in the same SQLite file).
  2. Syncs evidence: if the planned shot has been captured, ingests it (capture is
     NEVER approval) and attributes it to the Director variant that produced it, by
     reading the workflow VHS embedded in the MP4.
  3. Interprets the instruction against project state and acts: plans/revises the
     next shot as a ComfyUI workflow JSON, edits the working film, reports, undoes, ...

Never: uses generated footage as H3 visual input, approves a take or final film by
itself, promotes Qwen output to truth, or queues H3 unless --generate is passed.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import director_project_v0735 as base
import director_project_progress_v0739 as life
import director_intent_v0739 as intent
import comfy_graph_v0739 as cg

VERSION = 'director-v0739'
TABLE = 'direction_events_v0739'
ZERO = '0' * 64
FPS = 24
SHOT_FRAMES = 124
ROUTINE_NUDGE = 6           # frames the Director may move a cut on its own ("tighten it a bit")
ROUTINE_LIMIT = 12          # largest routine change without an explicit number from the human
UNDOABLE = {'VARIANT_ISSUED', 'CUT_SET', 'STANDING_SET'}

class DirectorError(base.MemoryErrorSafety):
    pass

# ----------------------------------------------------------------------------- ledger
def _db(root):
    return life.db_path(root)

def _genesis(root):
    with closing(base.connect(_db(root))) as c:
        return base.read_events(c)[0]['event_hash']

def _read(c, genesis):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
        return []
    rows = c.execute(f'SELECT seq,kind,payload,prev_hash,event_hash,genesis FROM {TABLE} ORDER BY seq').fetchall()
    prev = ZERO; out = []
    for i, r in enumerate(rows, 1):
        try: data = json.loads(r['payload'])
        except ValueError as e: raise DirectorError('DIRECTION_JSON_INVALID') from e
        h = base.event_hash(i, r['kind'], data, prev)
        if r['seq'] != i or r['prev_hash'] != prev or r['event_hash'] != h or r['payload'] != base.canonical(data) or r['genesis'] != genesis:
            raise DirectorError('DIRECTION_LEDGER_INTEGRITY_FAILED')
        out.append({'seq': i, 'kind': r['kind'], 'payload': data, 'hash': h}); prev = h
    return out

def events(root):
    g = _genesis(root)
    with closing(base.connect(_db(root))) as c:
        return _read(c, g)

def _append(root, kind, payload, expect_tail):
    """Optimistic append: refuses if the ledger moved since state was computed."""
    g = _genesis(root)
    with closing(base.connect(_db(root))) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            ev = _read(c, g)
            tail = ev[-1]['hash'] if ev else ZERO
            if tail != expect_tail: raise DirectorError('STATE_CHANGED_CONCURRENTLY_RETRY')
            c.execute(f'CREATE TABLE IF NOT EXISTS {TABLE}(seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, '
                      'prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL, genesis TEXT NOT NULL)')
            seq = len(ev) + 1
            h = base.event_hash(seq, kind, payload, tail)
            c.execute(f'INSERT INTO {TABLE} VALUES (?,?,?,?,?,?)', (seq, kind, base.canonical(payload), tail, h, g))
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK'); raise
    return h

# ----------------------------------------------------------------------------- state
def baseline_timeline(b):
    w = b['working_edit']; cap = b['capture_binding']
    return [{'shot': 1, 'filename': cap['outgoing']['source_filename'], 'sha256': cap['outgoing']['source_sha256'],
             'in': w['shot1_source_start_frame'], 'out': w['shot1_source_end_exclusive']},
            {'shot': 2, 'filename': cap['incoming']['source_filename'], 'sha256': cap['incoming']['source_sha256'],
             'in': w['shot2_source_start_frame'], 'out': w['shot2_source_end_exclusive']}]

def state(root):
    root = Path(root)
    b = base.status(root)
    p = life.snapshot(root)
    ev = events(root)
    undone = {e['payload']['target_seq'] for e in ev if e['kind'] == 'UNDONE'}
    live = [e for e in ev if e['seq'] not in undone]
    variants = {}
    for e in live:
        if e['kind'] == 'VARIANT_ISSUED':
            variants.setdefault(e['payload']['shot'], []).append({**e['payload'], 'seq': e['seq']})
    standing = {}
    for e in live:
        if e['kind'] == 'STANDING_SET':
            standing.update(e['payload']['prefs'])
    timeline = baseline_timeline(b); cut_basis = 'BASE_LEDGER_F36_HUMAN_PROMOTED'
    for e in live:
        if e['kind'] == 'CUT_SET':
            timeline = e['payload']['segments']; cut_basis = e['payload']['basis']
    pending = None
    for e in ev:
        if e['kind'] == 'PENDING': pending = {**e['payload'], 'seq': e['seq']}
        elif e['kind'] in ('PENDING_RESOLVED',) and pending and e['payload']['pending_seq'] == pending['seq']: pending = None
    attributions = [e['payload'] for e in ev if e['kind'] == 'CAPTURE_ATTRIBUTED']
    carry = [e['payload'] for e in live if e['kind'] == 'CARRY_NOTE']
    with closing(base.connect(_db(root))) as c:
        prog = life._history(c, b['latest_event_hash'])
    accepted = [e['payload'] for e in prog if e['kind'] == 'WORKING_PERFORMANCE_REVIEWED']
    captures = {e['payload']['video_sha256']: e['payload'] for e in prog if e['kind'] == 'CAPTURE_INGESTED'}
    return {'base': b, 'progress': p, 'events': ev, 'tail': ev[-1]['hash'] if ev else ZERO,
            'variants': variants, 'standing': standing, 'timeline': timeline, 'cut_basis': cut_basis,
            'pending': pending, 'attributions': attributions, 'carry': carry,
            'accepted': accepted, 'captures': captures, 'undoable': [e for e in live if e['kind'] in UNDOABLE]}

def planned(root, n):
    return life._planned(Path(root), n)

def active_variant(st, shot):
    vs = st['variants'].get(shot) or []
    return vs[-1] if vs else None

# ----------------------------------------------------------------------------- prompts
FRAMING_TEXT = {
 'same': 'Both subjects share ONE stable medium-wide composition from the FIRST FRAME to the LAST FRAME: the presenter on frame LEFT and Grandpa on frame RIGHT.',
 'closer': 'A closer, more intimate two-shot (roughly waist-up) held from the FIRST FRAME to the LAST FRAME: the presenter on frame LEFT and Grandpa on frame RIGHT, both faces clearly readable together.',
 'wider': 'A wider, full-body two-shot that shows more of the hallway, held from the FIRST FRAME to the LAST FRAME: the presenter on frame LEFT and Grandpa on frame RIGHT, both clearly visible.',
 'new_angle': 'A fresh but natural camera angle on the same two-person moment, slightly off-axis from the earlier shots, held as one composition from the FIRST FRAME to the LAST FRAME; the presenter stays on frame LEFT and Grandpa on frame RIGHT so screen direction is preserved.',
 'profile': 'A side-on angle favoring {who} in profile, the other person still present in the frame, held as one composition from the FIRST FRAME to the LAST FRAME.',
 'reverse': 'A reverse angle on the same exchange from the opposite side of the hallway, held as one composition from the FIRST FRAME to the LAST FRAME; both people remain recognizable.',
 'low_angle': 'A slightly low, eye-level-minus camera position favoring {who}, both people in frame, held as one composition from the FIRST FRAME to the LAST FRAME.',
 'high_angle': 'A slightly high camera position looking gently down on the two of them, held as one composition from the FIRST FRAME to the LAST FRAME.',
 'over_shoulder': 'An over-the-shoulder shot from behind {other}, favoring {who} facing camera, held as one composition from the FIRST FRAME to the LAST FRAME; {other} is partly visible in the near foreground.',
 'close_up': 'A single close-up on {who} from the FIRST FRAME to the LAST FRAME: face and shoulders, natural lens, the same hallway softly behind. {other} is off-screen or only at the very edge of frame. This is an intentional single-subject shot, not a jump cut.',
}
MOVE_TEXT = {'push_in': 'The camera makes one very slow, subtle push-in across the take.',
             'pull_out': 'The camera eases back very slowly across the take.',
             'handheld': 'Gentle handheld documentary camera with small natural drift; no shake.',
             'static': 'The camera is locked off and does not move.',
             'pan': 'One small, motivated pan only if it follows the conversation naturally.'}
WHO = {'presenter': 'the presenter', 'grandpa': 'Grandpa', 'both': 'both of them'}
OTHER = {'presenter': 'Grandpa', 'grandpa': 'the presenter', 'both': 'the hallway'}

def beat_for(st, shot):
    """Base narrative of a shot + who is speaking/listening (assumptions labeled)."""
    if shot == 3:
        return {'beat': 'Grandpa is finishing his answer; the presenter listens attentively, lets him finish, and offers a small '
                        'genuine reaction or follow-up invitation when appropriate.',
                'speaker': 'grandpa', 'listener': 'presenter', 'source': 'PERSISTED_V0735_PLAN'}
    note = (st['accepted'][-1].get('story_observation_human_attested') if st['accepted'] else 'UNKNOWN') or 'UNKNOWN'
    beat = ('The interview continues naturally from the previous human-reviewed moment, without assuming unverified dialogue. '
            'Grandpa and the presenter listen and respond to each other with genuine pauses and small reactions.')
    if note != 'UNKNOWN':
        beat = f'Previously (noted by the filmmaker): {note}. ' + beat
    return {'beat': beat, 'speaker': 'grandpa', 'listener': 'presenter',
            'source': 'HUMAN_REVIEW_NOTE' if note != 'UNKNOWN' else 'GENERIC_CONTINUATION_SPEAKER_ASSUMED'}

def emotion_phrase(key, intensity):
    for k, _, mild, strong in intent.EMOTIONS:
        if k == key:
            return strong if intensity == 'strong' else ('a very subtle hint of ' + mild.replace('a ', '', 1) if intensity == 'subtle' else mild)
    return key

def compose_prompt(scene, beat, spec):
    framing = spec.get('framing') or 'same'
    who = spec.get('framing_subject') or spec.get('subject') or beat['speaker']
    if who == 'both': who = beat['speaker']
    ft = FRAMING_TEXT[framing].format(who=WHO[who], other=OTHER[who])
    tone = scene['tone']
    parts = [f"SINGLE UNBROKEN, naturalistic 9:16 cinematic hallway interview, 24 FPS. Setting: {scene['setting']}. Tone: {tone}.",
             'Use ONLY the SIX verified ORIGINAL reference images; the original full hallway frame is the primary scene anchor.',
             ft,
             "Maintain both people's real faces, appearance, wardrobe, lighting and spatial relationship exactly as in the originals.",
             'The conversation has already begun. DO NOT repeat the opening question or restart an introduction.',
             beat['beat']]
    subj, emo, inten = spec.get('subject'), spec.get('emotion'), spec.get('intensity', 'normal')
    if subj and emo:
        parts.append(f"Focus of this beat: {WHO[subj]} reacts with {emotion_phrase(emo, inten)}, motivated by what was just said; natural, never theatrical.")
    elif subj and spec.get('react'):
        parts.append(f"Focus of this beat: {WHO[subj]}'s honest, readable reaction to what was just said; understated and natural.")
    elif emo:
        parts.append(f"Emotional color of this beat: {emotion_phrase(emo, inten)}.")
    pacing = spec.get('pacing')
    if pacing == 'slower':
        parts.append('Let the moment breathe: unhurried timing, a real pause or held look before anyone speaks again; silence is part of the scene.')
    elif pacing == 'faster':
        parts.append('Keep the exchange moving: responsive timing, no long dead air, still natural.')
    else:
        parts.append('Allow genuine pauses and conversational timing.')
    if spec.get('move'): parts.append(MOVE_TEXT[spec['move']])
    elif framing in ('same', 'closer', 'wider'): parts.append('One fixed composition with only modest natural camera movement.')
    for line in spec.get('dialogue', [])[-3:]:
        sp = WHO.get(line.get('speaker') or '', 'One of them')
        parts.append(f"{sp[0].upper() + sp[1:]} says, naturally and in their own rhythm: \"{line['line']}\"")
    if spec.get('prop') == 'handoff':
        parts.append('The presenter may naturally offer the microphone and Grandpa may take it gently; one microphone only, '
                     'hands anatomically natural, the microphone never duplicates or disappears.')
    else:
        parts.append('The microphone remains visible as an interaction prop with subtle movement only; no scripted transfer.')
    if spec.get('notes'):
        parts.append("Filmmaker's direction for this shot, in their words: " + ' / '.join(f'"{n}"' for n in spec['notes'][-3:]) + '.')
    if spec.get('avoid'):
        parts.append('Avoid: ' + '; '.join(dict.fromkeys(spec['avoid'][-6:])) + '.')
    end = 'a visible, readable face' if framing == 'close_up' else 'a naturally readable shared two-person beat with visible faces'
    parts.append(f'End on {end}, not an artificial freeze. No embedded jump cut, character replacement, '
                 'or generated earlier footage as visual reference.')
    return ' '.join(parts)

SPEC_FIELDS = ('subject', 'emotion', 'intensity', 'react', 'framing', 'framing_subject', 'move', 'pacing', 'prop')

def merge_spec(prev, d, standing):
    spec = dict(standing or {})
    spec.update({k: v for k, v in (prev or {}).items()})
    for k in SPEC_FIELDS:
        v = d.get(k) if k != 'react' else bool(re.search(r'\breact|respon', d['normalized']))
        if k == 'intensity' and v == 'normal' and not d.get('emotion'): continue
        if v: spec[k] = v
    if d.get('framing') == 'same':
        spec['framing'] = 'same'
    def add(lst, new):
        out = list(lst or [])
        for x in new:
            if x not in out: out.append(x)
        return out
    spec['dialogue'] = add(spec.get('dialogue'), d.get('dialogue', []))
    spec['avoid'] = add(spec.get('avoid'), d.get('avoid', []))
    notes = add(spec.get('notes'), [d['note']] if d.get('note') and not d.get('defer') else [])
    spec['notes'] = notes
    spec['take'] = (prev or {}).get('take', 1)
    return spec

def seed_for(project_id, shot, prompt, take):
    return int(hashlib.sha256(base.canonical({'project': project_id, 'shot': shot, 'prompt': prompt, 'take': take}).encode()).hexdigest()[:12], 16)

# ----------------------------------------------------------------------------- workflows
def _template(path):
    g = json.loads(Path(path).read_text())
    return g

def issue_variant(root, st, shot, spec, *, reason, template, originals_dir, mirror_plan=False, direction=None):
    plan = planned(root, shot)
    b = st['base']; scene = b['scene_brief']
    base.validate_seed(scene, originals_dir)
    originals = {a['name']: a['sha256'] for a in scene['original_assets']}
    if mirror_plan:
        prompt, seed = plan['generation_prompt'], plan['rng_seed']
    else:
        prompt = compose_prompt(scene, beat_for(st, shot), spec)
        seed = seed_for(b['project_id'], shot, prompt, spec.get('take', 1))
    prefix = plan['savedshot_output_prefix']
    for v in st['variants'].get(shot, []):
        if v['prompt_sha256'] == hashlib.sha256(prompt.encode()).hexdigest() and v['seed'] == seed:
            if v is active_variant(st, shot):
                return {'reused': True, **v}
    graph = cg.derive_workflow(_template(template), prompt=prompt, seed=seed, prefix=prefix)
    info = cg.validate_director_graph(graph, originals)
    if info['prompt'] != prompt or info['seed'] != seed or info['filename_prefix'] != prefix: raise DirectorError('GRAPH_NOT_PLAN_BOUND')
    vid = len([e for e in st['events'] if e['kind'] == 'VARIANT_ISSUED' and e['payload']['shot'] == shot]) + 1
    out = Path(root) / 'director_v0739' / 'workflows'
    out.mkdir(parents=True, exist_ok=True)
    if out.is_symlink(): raise DirectorError('LINKED_WORKFLOW_DIR')
    name = f'Director_H3_Hallway_Shot{shot:02d}_v{vid:02d}_CANDIDATE.json'
    text = json.dumps(graph, indent=2, ensure_ascii=False) + '\n'
    path = out / name
    if path.exists() and path.read_text() != text: raise DirectorError('WORKFLOW_FILE_CONFLICT')
    payload = {'shot': shot, 'variant': vid, 'spec': spec, 'prompt': prompt,
               'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'seed': seed, 'prefix': prefix,
               'workflow_file': name, 'workflow_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'reason': reason, 'direction': direction, 'mirrors_lifecycle_plan': mirror_plan,
               'generation_authorized': False, 'generated_footage_as_h3_conditioning': False}
    _append(root, 'VARIANT_ISSUED', payload, st['tail'])
    if not path.exists():
        with path.open('x') as f: f.write(text)
    return {'reused': False, **payload}

def stage(root, variant, comfy_root):
    src = Path(root) / 'director_v0739' / 'workflows' / variant['workflow_file']
    if hashlib.sha256(src.read_bytes()).hexdigest() != variant['workflow_sha256']: raise DirectorError('WORKFLOW_FILE_CHANGED')
    d = Path(comfy_root) / 'user' / 'default' / 'workflows'
    if d.is_symlink(): raise DirectorError('LINKED_COMFY_WORKFLOW_DIR')
    d.mkdir(parents=True, exist_ok=True)
    dst = d / variant['workflow_file']
    if dst.exists() and dst.read_bytes() != src.read_bytes(): raise DirectorError('STAGED_WORKFLOW_CONFLICT')
    if not dst.exists(): shutil.copyfile(src, dst)
    return str(dst)

def generate(root, st, variant, url, originals_dir, *, opener=None):
    """Queue ONE variant on ComfyUI. Only called with an explicit --generate from the human."""
    from urllib.request import urlopen, Request
    op = opener or urlopen
    scene = st['base']['scene_brief']
    base.validate_seed(scene, originals_dir)
    src = Path(root) / 'director_v0739' / 'workflows' / variant['workflow_file']
    raw = src.read_bytes()
    if hashlib.sha256(raw).hexdigest() != variant['workflow_sha256']: raise DirectorError('WORKFLOW_FILE_CHANGED')
    graph = json.loads(raw)
    originals = {a['name']: a['sha256'] for a in scene['original_assets']}
    cg.validate_director_graph(graph, originals)
    if st['progress']['phase'] != 'PLAN' or st['progress']['next_shot'] != variant['shot']:
        raise DirectorError('GENERATION_ONLY_FOR_THE_CURRENT_PLANNED_SHOT')
    with op(url.rstrip('/') + '/object_info', timeout=15) as r: info = json.load(r)
    missing = {'MiniMaxH3ReferenceToVideo', 'VHS_VideoCombine', 'DirectorV066SavedShotShadowQueue'} - set(info)
    if missing: raise DirectorError('COMFYUI_NODES_NOT_LOADED_' + ','.join(sorted(missing)))
    with op(url.rstrip('/') + '/queue', timeout=15) as r: q = json.load(r)
    if q.get('queue_running') or q.get('queue_pending'): raise DirectorError('COMFYUI_QUEUE_NOT_IDLE')
    api = cg.to_api_prompt(graph, originals)
    body = json.dumps({'prompt': api, 'client_id': 'h3-director-v0739',
                       'extra_data': {'extra_pnginfo': {'workflow': graph}}}).encode()
    req = Request(url.rstrip('/') + '/prompt', data=body, headers={'Content-Type': 'application/json'}, method='POST')
    with op(req, timeout=30) as r: resp = json.load(r)
    if resp.get('node_errors'): raise DirectorError('COMFYUI_REJECTED_PROMPT: ' + json.dumps(resp['node_errors'])[:500])
    pid = resp.get('prompt_id')
    _append(root, 'GENERATION_SUBMITTED', {'shot': variant['shot'], 'variant': variant['variant'], 'prompt_id': pid,
            'workflow_sha256': variant['workflow_sha256'], 'authorized_by': 'HUMAN_CLI_FLAG_--generate'}, st['tail'])
    return pid

# ----------------------------------------------------------------------------- evidence sync
def sync(root, saved_root, source_root, *, synthetic=False):
    """Ingest a capture of the planned shot if one exists, then attribute it. Never approves."""
    st = state(root)
    notes = []
    if st['progress']['phase'] == 'PLAN':
        try:
            life.ingest(root, saved_root, source_root, synthetic=synthetic)
            notes.append(f"Found and ingested the capture for shot {st['progress']['next_shot']} (capture is not approval).")
        except base.MemoryErrorSafety as e:
            msg = str(e)
            if 'EXPECTED_ONE_MATCHED_NEW_CAPTURE_FOUND_0' in msg or 'INVALID_SAVEDSHOT_JOBS_ROOT' in msg:
                pass
            else:
                notes.append('Capture intake needs attention: ' + msg[:300])
        st = state(root)
    if st['progress']['phase'] == 'CAPTURE':
        cap = st['progress']['pending_capture']
        if not any(a['video_sha256'] == cap['video_sha256'] for a in st['attributions']):
            prov = cg.embedded_provenance(Path(cap['video_path']))
            match = None
            if prov.get('status') == 'OK':
                ph = hashlib.sha256((prov['prompt'] or '').encode()).hexdigest()
                for v in st['variants'].get(cap['shot_number'], []):
                    if v['prompt_sha256'] == ph and v['seed'] == prov['seed']: match = v['variant']
                if match is None:
                    for v in [e['payload'] for e in st['events'] if e['kind'] == 'VARIANT_ISSUED' and e['payload']['shot'] == cap['shot_number']]:
                        if v['prompt_sha256'] == ph and v['seed'] == prov['seed']: match = v['variant']
            violation = bool(prov.get('video_or_audio_refs_linked')) or (
                prov.get('status') == 'OK' and set(prov['images']) - {a['name'] for a in st['base']['scene_brief']['original_assets']})
            _append(root, 'CAPTURE_ATTRIBUTED', {'shot': cap['shot_number'], 'video_sha256': cap['video_sha256'],
                    'embedded_status': prov.get('status'), 'variant': match,
                    'clean_chain_violation': bool(violation)}, st['tail'])
            if violation: notes.append('WARNING: the embedded workflow shows non-original visual/audio references. Approval is blocked.')
            notes.append(f"Captured take matches Director variant v{match:02d}." if match else
                         'Captured take does not match any Director variant (edited in ComfyUI or unknown). Treated as a manual take.')
    return notes

# ----------------------------------------------------------------------------- timeline
def find_media(seg, roots):
    for r in roots:
        p = Path(r) / seg['filename']
        if p.is_file() and not p.is_symlink() and base.digest(p) == seg['sha256']: return p
    raise DirectorError('SOURCE_MEDIA_NOT_FOUND_OR_CHANGED_' + seg['filename'])

def validate_timeline(segs):
    if not 1 <= len(segs) <= 40: raise DirectorError('TIMELINE_SIZE')
    seen = set()
    for i, s in enumerate(segs):
        if s['shot'] in seen: raise DirectorError('SHOT_TWICE_IN_TIMELINE')
        seen.add(s['shot'])
        top = int(s.get('frames', SHOT_FRAMES))
        if not 24 <= top <= 400: raise DirectorError('SEGMENT_TAKE_LENGTH_%d' % s['shot'])
        if not (type(s['in']) is int and type(s['out']) is int and 0 <= s['in'] < s['out'] <= top and s['out'] - s['in'] >= 24):
            raise DirectorError('SEGMENT_BOUNDS_%d' % s['shot'])
        if not re.fullmatch('[0-9a-f]{64}', s['sha256']): raise DirectorError('SEGMENT_HASH')
        aa, ab = s.get('a_in', s['in']), s.get('a_out', s['out'])
        if not (type(aa) is int and type(ab) is int and 0 <= aa < ab <= top and ab - aa >= 12): raise DirectorError('AUDIO_BOUNDS_%d' % s['shot'])
        if s.get('afade') is not None and not (0 < float(s['afade']) <= 0.1): raise DirectorError('AUDIO_FADE_RANGE')
        if '/' in s['filename'] or '..' in s['filename']: raise DirectorError('SEGMENT_FILENAME')
    return segs

def frame_size(path):
    """(width, height) of a video's picture; even numbers (yuv420p needs them)."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(path)],
                       capture_output=True, text=True, timeout=30)
    try: st = json.loads(r.stdout)['streams'][0]; w, h = int(st['width']), int(st['height'])
    except (ValueError, KeyError, IndexError, TypeError): raise DirectorError('UNREADABLE_VIDEO_' + Path(path).name)
    return w - w % 2, h - h % 2

def render(segs, roots, dest):
    """Frame-accurate A/V render of the working film from hash-verified sources."""
    import cv2, numpy as np
    import editorial_audio_v0721 as audio
    validate_timeline(segs)
    dest = Path(dest)
    if dest.exists(): raise DirectorError('RENDER_EXISTS')
    paths = [find_media(s, roots) for s in segs]
    W, H = frame_size(paths[0])
    # every clip to the first clip's size, square pixels, one pixel format: ffmpeg's concat refuses clips whose SAR tags
    # differ (e.g. 0:1 vs 4095:4096 from different encoders) even at the same size
    norm = (f',scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p')
    fg = []; maps = ''
    for i, s in enumerate(segs):
        a, b = s['in'], s['out']
        grade = ''
        if s.get('grade'):
            gb, gg, gr = s['grade']['gain']; ob, og, orr = s['grade']['offset']
            grade = (f",scale=in_color_matrix=bt709:in_range=tv,format=rgb24,lutrgb=r=clip(val*{gr:.4f}+{orr:.2f}\\,0\\,255):g=clip(val*{gg:.4f}+{og:.2f}\\,0\\,255)"
                     f":b=clip(val*{gb:.4f}+{ob:.2f}\\,0\\,255),scale=out_color_matrix=bt709:out_range=tv")   # V0.9.56 the takes' own BT.709 both ways
        fg.append(f'[{i}:v]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS{grade}{norm}[v{i}]')
        # audio may lead (J-cut) or trail (L-cut) the picture: a_in/a_out are the audio's own source frames
        aa, ab = s.get('a_in', a), s.get('a_out', b)
        fade = ''
        if s.get('afade'):
            d = float(s['afade']); dur = (ab - aa) / FPS
            fade = f',afade=t=in:st=0:d={d:.4f},afade=t=out:st={max(0.0, dur - d):.6f}:d={d:.4f}'
        fg.append(f'[{i}:a]atrim=start={aa/FPS:.9f}:end={ab/FPS:.9f},apad=pad_dur=0.05,atrim=duration={(ab-aa)/FPS:.9f},asetpts=PTS-STARTPTS{fade}[a{i}]')
        maps += f'[v{i}][a{i}]'
    if any('a_in' in s or 'a_out' in s or s.get('afade') for s in segs):
        if sum(s.get('a_out', s['out']) - s.get('a_in', s['in']) for s in segs) != sum(s['out'] - s['in'] for s in segs):
            raise DirectorError('AUDIO_AND_PICTURE_LENGTHS_DIFFER')
        fg.append(''.join(f'[v{i}]' for i in range(len(segs))) + f'concat=n={len(segs)}:v=1:a=0[v]')
        fg.append(''.join(f'[a{i}]' for i in range(len(segs))) + f'concat=n={len(segs)}:v=0:a=1[a]')
    else:
        fg.append(f'{maps}concat=n={len(segs)}:v=1:a=1[v][a]')
    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n']
    for p in paths: cmd += ['-i', str(p)]
    cmd += ['-filter_complex', ';'.join(fg), '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'fast', '-crf', '17',
            '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv', '-c:a', 'aac', '-b:a', '160k', '-ar', '32000', '-ac', '2', '-movflags', '+faststart', str(dest)]
    tmp = dest.with_suffix('.partial.mp4'); cmd[-1] = str(tmp)
    try:
        subprocess.run(cmd, check=True, timeout=600, capture_output=True)
    except subprocess.CalledProcessError as e:
        tmp.unlink(missing_ok=True); raise DirectorError('FFMPEG_FAILED ' + e.stderr.decode(errors='replace')[-800:])
    total = sum(s['out'] - s['in'] for s in segs)
    pr = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries', 'stream=codec_type,nb_read_frames,r_frame_rate',
                                    '-of', 'json', str(tmp)], capture_output=True, text=True, check=True).stdout)['streams']
    v = [x for x in pr if x['codec_type'] == 'video']; au = [x for x in pr if x['codec_type'] == 'audio']
    if len(v) != 1 or len(au) != 1 or int(v[0]['nb_read_frames']) != total or v[0]['r_frame_rate'] != '24/1':
        tmp.unlink(missing_ok=True); raise DirectorError('RENDER_FRAME_COUNT_OR_STREAMS')
    def grab(path, idx):
        cap = cv2.VideoCapture(str(path)); out = {}
        try:
            for n in sorted(set(idx)):
                cap.set(cv2.CAP_PROP_POS_FRAMES, n); ok, im = cap.read()
                if not ok: raise DirectorError('DECODE_FAILED')
                out[n] = cv2.resize(im, (144, 252)).astype(np.float32)
        finally: cap.release()
        return out
    checks = {}; offset = 0
    rendered_idx = []
    for s in segs:
        rendered_idx += [offset, offset + (s['out'] - s['in']) // 2, offset + s['out'] - s['in'] - 1]; offset += s['out'] - s['in']
    rf = grab(tmp, rendered_idx); offset = 0
    pcm_out = audio.pcm_decode(tmp, total / FPS); audio_checks = []
    for s, p in zip(segs, paths):
        n = s['out'] - s['in']
        src = grab(p, [s['in'], s['in'] + n // 2, s['out'] - 1])
        if s.get('grade'):
            gain = np.array(s['grade']['gain'], np.float32); off = np.array(s['grade']['offset'], np.float32)
            src = {k: np.clip(v * gain + off, 0, 255) for k, v in src.items()}
        for k, (ro, so) in enumerate([(offset, s['in']), (offset + n // 2, s['in'] + n // 2), (offset + n - 1, s['out'] - 1)]):
            checks[f"shot{s['shot']}_{['first', 'mid', 'last'][k]}"] = round(float(np.mean(np.abs(rf[ro] - src[so]))), 3)
        # audio: 0.5 s window from the middle of the segment, only judged if audible
        pcm_src = audio.pcm_decode(p, int(s.get('frames', SHOT_FRAMES)) / FPS)
        t_src = (s['in'] + n / 2) / FPS; t_out = (offset + n / 2) / FPS; w = int(0.5 * audio.SR)
        if 'a_in' in s or 'a_out' in s:          # judge the middle of this clip's OWN audio span
            aa, ab = s.get('a_in', s['in']), s.get('a_out', s['out']); a_off = offset + (aa - s['in'])
            t_src = (aa + (ab - aa) / 2) / FPS; t_out = (a_off + (ab - aa) / 2) / FPS
        x = pcm_src[int(t_src * audio.SR): int(t_src * audio.SR) + w].astype(np.float64)
        y0 = int(round(t_out * audio.SR))
        y = pcm_out[y0: y0 + w].astype(np.float64)
        if len(x) == w and len(y) == w and np.std(x) > 0.005 and np.std(y) > 0.005:
            # segment starts fall between audio samples; allow +/-4 samples (0.25 ms) of rounding
            c = max(float(np.corrcoef(x, pcm_out[y0 + k: y0 + k + w].astype(np.float64))[0, 1])
                    for k in range(-4, 5) if y0 + k >= 0 and len(pcm_out[y0 + k: y0 + k + w]) == w)
            audio_checks.append({'shot': s['shot'], 'pcm_correlation': round(c, 5)})
            if c < 0.96: tmp.unlink(missing_ok=True); raise DirectorError('AUDIO_SOURCE_CORRESPONDENCE_FAILED')
        else:
            audio_checks.append({'shot': s['shot'], 'pcm_correlation': 'QUIET_WINDOW_NOT_JUDGED'})
        offset += n
    if max(checks.values()) > 12.0: tmp.unlink(missing_ok=True); raise DirectorError('RENDER_FRAME_CORRESPONDENCE_FAILED')
    os.replace(tmp, dest)
    return {'path': str(dest), 'frames': total, 'seconds': round(total / FPS, 5), 'sha256': base.digest(dest),
            'frame_mad': checks, 'audio': audio_checks}

def auto_entry(prev_seg, new_path, prev_path):
    """Technical entry choice for an incoming shot, reusing the V0.7.33 scorer."""
    import director_autoedit_v0733 as ae
    case = {'schema': 'director-edit-case-v0733', 'scene_id': 'director-v0739-append',
            'outgoing': {'filename': Path(prev_path).name, 'sha256': base.digest(prev_path),
                         'source_class': 'H3_GENERATED_OBSERVATION_AND_EDITORIAL_ONLY', 'retain_all_frames': True,
                         'human_working_status': 'SHOT1_APPROVED_WORKING_NOT_FINAL'},   # label required by the V0.7.33 scorer
            'incoming': {'filename': Path(new_path).name, 'sha256': base.digest(new_path),
                         'source_class': 'H3_GENERATED_OBSERVATION_AND_EDITORIAL_ONLY'},
            'candidate_policy': {'start': 24, 'stop_exclusive': 68, 'step': 2},
            'internal_candidate_barriers_outgoing': [], 'internal_candidate_barriers_incoming': [],
            'benchmark_not_in_case_by_design': True, 'no_generated_video_visual_conditioning_for_h3': True}
    rep = ae.select(Path(prev_path), Path(new_path), case['outgoing']['sha256'], case['incoming']['sha256'], case)
    return rep['selection']

# ----------------------------------------------------------------------------- reports
def status_text(st):
    b, p = st['base'], st['progress']
    tl = st['timeline']; total = sum(s['out'] - s['in'] for s in tl)
    lines = [f"Project: {b['project_title']} ({b['project_id']})",
             f"Working film: {len(tl)} shots, {total} frames = {total/FPS:.2f}s  [" +
             ', '.join(f"S{s['shot']} {s['in']}-{s['out']-1}" for s in tl) + f"]  basis: {st['cut_basis']}"]
    undone = {e['payload']['target_seq'] for e in st['events'] if e['kind'] == 'UNDONE'}
    hist = [e for e in st['events'] if e['kind'] == 'CUT_SET' and e['seq'] not in undone]
    lines.append('Edit history: base F36 (your F42 preserved)' + (f" + {len(hist)} later cut change(s)" if hist else ''))
    n = p['next_shot']
    if p['phase'] == 'PLAN':
        v = active_variant(st, n)
        lines.append(f"Next: shot {n} is planned, not yet generated." + (f" Active variant v{v['variant']:02d} -> {v['workflow_file']}" if v else ''))
    elif p['phase'] == 'CAPTURE':
        lines.append(f"Shot {n} is captured and waiting for YOUR review: {Path(p['pending_capture']['video_path']).name}")
    else:
        lines.append(f"Shot {n} accepted as a working performance. Say how to continue and I will plan shot {n+1}.")
    if p.get('rejected_takes'): lines.append(f"Rejected takes kept as evidence: {len(p['rejected_takes'])}")
    if st['standing']: lines.append('Standing preferences: ' + ', '.join(f'{k}={v}' for k, v in st['standing'].items()))
    lines.append('Unknown (not claimed): spoken dialogue, final poses, microphone holder, last-22-frame motion.')
    if st['pending']: lines.append(f"Waiting on you: {st['pending']['question']}")
    return '\n'.join(lines)

# ----------------------------------------------------------------------------- main logic
def direct(root, text, *, originals_dir, template, saved_root=None, source_root=None, media_roots=(), comfy_root=None,
           do_stage=False, do_generate=False, comfy_url='http://127.0.0.1:18188', yes=False, synthetic=False, opener=None):
    root = Path(root)
    out = {'instruction': text, 'notes': [], 'action': None}
    if saved_root and source_root:
        out['notes'] += sync(root, saved_root, source_root, synthetic=synthetic)
    st = state(root)
    p = st['progress']; n = p['next_shot']; phase = p['phase']
    beat = beat_for(st, n if phase != 'ACCEPTED' else n + 1)
    d = intent.interpret(text, {'phase': phase, 'next_shot': n, 'beat_listener': beat['listener'],
                                'beat_speaker': beat['speaker'], 'pending_confirmation': bool(st['pending'])})
    out['understood_as'] = d['reading']; out['assumptions'] = d['assumptions']
    out['refused'] = d['refused']
    act = d['action']
    if yes and act == 'APPROVE_TAKE': act = 'APPROVE_NOW'

    def ensure_v01(shot):
        nonlocal st
        if not st['variants'].get(shot):
            issue_variant(root, st, shot, {'take': 1}, reason='MIRROR_OF_LIFECYCLE_PLAN', template=template,
                          originals_dir=originals_dir, mirror_plan=True)
            st = state(root)

    def deliver(v, what):
        out['action'] = what
        out['variant'] = {k: v[k] for k in ('shot', 'variant', 'workflow_file', 'seed', 'prefix', 'reason')}
        out['workflow_path'] = str(root / 'director_v0739' / 'workflows' / v['workflow_file'])
        out['prompt'] = v['prompt']
        if do_stage and comfy_root: out['staged_to'] = stage(root, v, comfy_root)
        if do_generate:
            out['comfyui_prompt_id'] = generate(root, state(root), v, comfy_url, originals_dir, opener=opener)

    if act == 'STOP':
        out['action'] = 'HOLD'; out['message'] = 'Holding. Nothing changed.'
    elif act == 'STATUS':
        out['action'] = 'STATUS'; out['message'] = status_text(st)
    elif act == 'NEW_SCENE':
        out['action'] = 'NEEDS_NEW_SCENE_SETUP'
        out['message'] = ('A new scene needs its own one-time setup: new ORIGINAL reference stills (location + any characters), '
                          'their hashes, and a short brief. I will not invent a location from the hallway references. '
                          'The hallway project stays as it is.')
    elif act in ('CONFIRM', 'DENY'):
        pend = st['pending']
        if not pend:
            out['action'] = 'NOTHING_PENDING'; out['message'] = 'There was nothing waiting for confirmation.'
        elif act == 'DENY':
            _append(root, 'PENDING_RESOLVED', {'pending_seq': pend['seq'], 'outcome': 'CANCELLED'}, st['tail'])
            out['action'] = 'CANCELLED'; out['message'] = 'Cancelled.'
        else:
            if pend['op'] == 'approve':
                cap = p.get('pending_capture')
                if phase != 'CAPTURE' or cap['video_sha256'] != pend['video_sha256']: raise DirectorError('PENDING_APPROVAL_STALE')
                _approve(root, st, out)
                st = state(root)
                _append(root, 'PENDING_RESOLVED', {'pending_seq': pend['seq'], 'outcome': 'APPROVED'}, st['tail'])
            else:
                raise DirectorError('UNKNOWN_PENDING_OP')
    elif act == 'APPROVE_TAKE':
        if phase != 'CAPTURE':
            out['action'] = 'NOTHING_TO_APPROVE'
            out['message'] = 'No captured take is waiting for review. ' + ('Plan is ready to generate.' if phase == 'PLAN' else '')
        else:
            _block_if_violation(st, p['pending_capture'])
            q = f"Approve shot {n} ({Path(p['pending_capture']['video_path']).name}) as a WORKING performance? Reply 'yes' or 'no'."
            _append(root, 'PENDING', {'op': 'approve', 'shot': n, 'video_sha256': p['pending_capture']['video_sha256'],
                                      'question': q, 'carry_note': d['note'] or None}, st['tail'])
            out['action'] = 'CONFIRM_APPROVAL'; out['message'] = q
    elif act == 'APPROVE_NOW':
        _block_if_violation(st, p['pending_capture'])
        _approve(root, st, out)
    elif act == 'REJECT_TAKE' or (act == 'RETAKE' and phase == 'CAPTURE'):
        if phase != 'CAPTURE':
            act = 'RETAKE'
        else:
            life.reject_capture(root, reason=(d['note'] or 'HUMAN_REJECTED_TAKE')[:300], confirmed=True)
            out['notes'].append(f'Shot {n} take rejected (kept as evidence, not deleted).')
            st = state(root); ensure_v01(n)
            prev = active_variant(st, n)['spec']
            spec = merge_spec(prev, d, st['standing']); spec['take'] = prev.get('take', 1) + 1
            v = issue_variant(root, st, n, spec, reason='RETAKE_AFTER_REJECTION', template=template,
                              originals_dir=originals_dir, direction=d['reading'])
            deliver(v, 'RETAKE_PLANNED')
    if act == 'RETAKE' and phase == 'PLAN':
        ensure_v01(n)
        prev = active_variant(st, n)['spec']
        spec = merge_spec(prev, d, st['standing']); spec['take'] = prev.get('take', 1) + 1
        v = issue_variant(root, st, n, spec, reason='NEW_TAKE_SAME_BEAT', template=template,
                          originals_dir=originals_dir, direction=d['reading'])
        deliver(v, 'NEW_TAKE_PLANNED')
    elif act == 'RETAKE' and phase == 'ACCEPTED':
        out['action'] = 'NEEDS_DECISION'
        out['message'] = f"Shot {n} is already accepted. Say 'plan the next shot' to continue, or name what to change about shot {n+1}."
    elif act == 'RESET':
        target = n if phase == 'PLAN' else None
        if target is None:
            out['action'] = 'NOTHING_TO_RESET'; out['message'] = 'There is no unshot plan to reset.'
        else:
            ensure_v01(target)
            first = st['variants'][target][0]
            v = issue_variant(root, st, target, dict(first['spec']), reason='RESET_TO_BASE_PLAN', template=template,
                              originals_dir=originals_dir, mirror_plan=first['mirrors_lifecycle_plan'])
            deliver(v, 'RESET_TO_BASE_PLAN' if not v.get('reused') else 'ALREADY_AT_BASE_PLAN')
    elif act == 'UNDO':
        if not st['undoable']:
            out['action'] = 'NOTHING_TO_UNDO'; out['message'] = 'Nothing of mine to undo. (Approvals and captures are permanent evidence.)'
        else:
            last = st['undoable'][-1]
            if last['kind'] == 'VARIANT_ISSUED' and last['payload'].get('mirrors_lifecycle_plan'):
                out['action'] = 'NOTHING_TO_UNDO'; out['message'] = 'Only the base plan is left for this shot.'
            else:
                _append(root, 'UNDONE', {'target_seq': last['seq'], 'target_kind': last['kind']}, st['tail'])
                out['action'] = 'UNDONE'; out['message'] = f"Undid: {last['kind'].replace('_', ' ').lower()}."
                st2 = state(root)
                if last['kind'] == 'VARIANT_ISSUED':
                    av = active_variant(st2, last['payload']['shot'])
                    if av: out['message'] += f" Active again: {av['workflow_file']}"
                if last['kind'] == 'CUT_SET':
                    out['message'] += ' Working film: ' + ', '.join(f"S{s['shot']} {s['in']}-{s['out']-1}" for s in st2['timeline'])
    elif act == 'EDIT':
        _edit(root, st, d['edit'], out, media_roots)
    elif act == 'DIRECT':
        if d['standing']:
            prefs = {k: d[k] for k in ('framing', 'move', 'pacing', 'intensity') if d.get(k) and not (k == 'intensity' and d[k] == 'normal')}
            if prefs:
                _append(root, 'STANDING_SET', {'prefs': prefs, 'text': d['note']}, st['tail']); st = state(root)
                out['notes'].append('Kept as a standing preference for future shots: ' + ', '.join(f'{k}={v}' for k, v in prefs.items()))
        if phase == 'CAPTURE':
            out['action'] = 'NEEDS_YOUR_REVIEW'
            out['message'] = (f"Shot {n} has a captured take waiting for your review. Watch it, then say e.g. "
                              f"'looks good' (accept) or 'redo it, <what to change>' (retake). Your note is not applied yet.")
        else:
            if phase == 'ACCEPTED':
                life.continue_project(root, originals_dir, template, 'continue the interview')
                st = state(root); n = st['progress']['next_shot']
                out['notes'].append(f'Planned shot {n} from the reviewed project memory.')
                for c in [c for c in st['carry'] if c['for_shot'] == n]:
                    d['note'] = (c['note'] + ' / ' + d['note']).strip(' /') if d['note'] else c['note']
            ensure_v01(n)
            prev = active_variant(st, n)['spec']
            modifiers = any(d.get(k) for k in ('emotion', 'framing', 'move', 'pacing', 'dialogue', 'avoid', 'prop')) or \
                        bool(re.search(r'\breact|respon', d['normalized'])) or (d['note'] and not d['defer'] and not d['continue'])
            if d['defer'] and not modifiers:
                choice = _director_choice(st, n)
                if choice:
                    d.update(choice['fields']); modifiers = True; out['notes'].append("Director's choice: " + choice['why'])
            if not modifiers and not (st['standing'] and len(st['variants'].get(n, [])) == 1 and st['standing'].items() - prev.items()):
                v = active_variant(st, n)
                deliver(v, 'PLAN_ALREADY_READY')
                out['message'] = f"Shot {n} is planned and ready; nothing new to change. Open {v['workflow_file']} in ComfyUI."
            else:
                spec = merge_spec(prev, d, st['standing'])
                v = issue_variant(root, st, n, spec, reason='DIRECTED_REVISION', template=template,
                                  originals_dir=originals_dir, direction=d['reading'])
                deliver(v, 'PLAN_REVISED' if not v.get('reused') else 'PLAN_ALREADY_MATCHES')
    if d['refused']:
        out['notes'] += ['Not done: ' + r['part'] + ' -> ' + r['reason'] for r in d['refused']]
    out['state'] = status_text(state(root))
    return out

def _block_if_violation(st, cap):
    for a in st['attributions']:
        if a['video_sha256'] == cap['video_sha256'] and a['clean_chain_violation']:
            raise DirectorError('APPROVAL_BLOCKED_CLEAN_CHAIN_VIOLATION_IN_CAPTURE')

def _approve(root, st, out):
    p = st['progress']; n = p['next_shot']
    _block_if_violation(st, p['pending_capture'])
    life.approve_explicit(root, confirmed=True)
    out['action'] = 'TAKE_APPROVED'
    out['message'] = (f"Shot {n} accepted as a WORKING performance (not final, not added to the film yet). "
                      f"Say 'add shot {n} to the film' to cut it in, or tell me how to continue.")
    pend = st['pending']
    if pend and pend.get('carry_note'):
        _append(root, 'CARRY_NOTE', {'for_shot': n + 1, 'note': pend['carry_note']}, state(root)['tail'])

def _director_choice(st, n):
    """Small, explainable editorial instincts when the human defers."""
    tl = st['timeline']
    framings = []
    for s in tl:
        v = [x for x in st['variants'].get(s['shot'], [])]
        framings.append((v[-1]['spec'].get('framing') if v else None) or 'same')
    run = 0
    for f in reversed(framings):
        if f == 'same': run += 1
        else: break
    if run >= 3 and n > 3:
        return {'fields': {'framing': 'closer'}, 'why': f'the last {run} shots in the film share the same medium-wide framing, '
                'so this beat moves to a closer two-shot for coverage while keeping screen direction.'}
    return None

def _edit(root, st, e, out, media_roots):
    tl = [dict(s) for s in st['timeline']]
    op = e['op']
    if op == 'RENDER':
        dest = Path(root) / 'director_v0739' / 'renders'
        dest.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(base.canonical(tl).encode()).hexdigest()[:10]
        f = dest / f'Working_Film_{len(tl)}shots_{sum(s["out"]-s["in"] for s in tl)}f_{key}.mp4'
        res = {'path': str(f), 'reused': True} if f.exists() else render(tl, media_roots, f)
        out['action'] = 'RENDERED'; out['render'] = res
        out['message'] = f"Working film rendered: {f.name}"
        return
    if op == 'APPEND':
        acc = st['accepted']
        if not acc: raise DirectorError('NO_ACCEPTED_SHOT_TO_ADD')
        want = e.get('shot') or max(a['shot_number'] for a in acc)
        rec = next((a for a in acc if a['shot_number'] == want), None)
        if rec is None: raise DirectorError(f'SHOT_{want}_NOT_ACCEPTED_AS_WORKING_PERFORMANCE')
        if any(s['shot'] == want for s in tl): raise DirectorError('SHOT_ALREADY_IN_FILM')
        cap = st['captures'][rec['source_sha256']]
        new_path = Path(cap['video_path'])
        if base.digest(new_path) != rec['source_sha256']: raise DirectorError('ACCEPTED_SOURCE_CHANGED')
        prev_path = find_media(tl[-1], list(media_roots) + [str(new_path.parent)])
        sel = auto_entry(tl[-1], new_path, prev_path)
        seg = {'shot': want, 'filename': new_path.name, 'sha256': rec['source_sha256'], 'in': sel['incoming_entry_frame'], 'out': SHOT_FRAMES}
        new = tl + [seg]
        basis = 'HUMAN_REQUESTED_APPEND_DIRECTOR_CHOSE_ENTRY_TECHNICAL'
        why = f"entry F{sel['incoming_entry_frame']} (technical cost {sel['technical_cost']}, runner-up F{sel['runner_up']}, gap {sel['cost_gap']})"
    elif op == 'REMOVE':
        if e['shot'] in (1,): raise DirectorError('CANNOT_REMOVE_OPENING_SHOT')
        if not any(s['shot'] == e['shot'] for s in tl): raise DirectorError('SHOT_NOT_IN_FILM')
        new = [s for s in tl if s['shot'] != e['shot']]; basis = 'HUMAN_DIRECTED'; why = f"removed shot {e['shot']}"
    elif op in ('SET_ENTRY', 'NUDGE_ENTRY', 'AUTO_SELECT'):
        idx = len(tl) - 1
        if e.get('shot') is not None:
            idx = next((i for i, s in enumerate(tl) if s['shot'] == e['shot']), None)
            if idx is None: raise DirectorError('SHOT_NOT_IN_FILM')
        if idx == 0: raise DirectorError('SHOT_1_IS_KEPT_WHOLE_BY_YOUR_APPROVAL')
        cur = tl[idx]['in']
        if op == 'SET_ENTRY':
            val = e['value']; basis = 'HUMAN_DIRECTED'
        elif op == 'NUDGE_ENTRY':
            if e['delta'] is None:
                val = cur + e['direction'] * ROUTINE_NUDGE; basis = 'DIRECTOR_ROUTINE_WITHIN_DELEGATED_BOUNDS'
            else:
                val = cur + e['delta']; basis = 'HUMAN_DIRECTED'
                if abs(e['delta']) > ROUTINE_LIMIT * 4: raise DirectorError('NUDGE_TOO_LARGE_SAY_THE_EXACT_FRAME')
        else:
            roots = list(media_roots)
            prev_path = find_media(tl[idx - 1], roots); new_path = find_media(tl[idx], roots)
            sel = auto_entry(tl[idx - 1], new_path, prev_path); val = sel['incoming_entry_frame']
            basis = 'DIRECTOR_TECHNICAL_RESELECT'
            if abs(val - cur) > ROUTINE_LIMIT:
                basis = 'DIRECTOR_PROPOSAL_OUTSIDE_ROUTINE_BOUNDS'
                out['action'] = 'EDIT_PROPOSED'
                out['message'] = f"The scorer prefers F{val} (now F{cur}); that is more than {ROUTINE_LIMIT} frames, so I won't apply it alone. Say 'start shot {tl[idx]['shot']} at frame {val}' to use it."
                return
        if not 1 <= val <= SHOT_FRAMES - 24: raise DirectorError('ENTRY_OUT_OF_RANGE')
        new = [dict(s) for s in tl]; new[idx]['in'] = val
        why = f"shot {tl[idx]['shot']} entry F{cur} -> F{val}"
    else:
        raise DirectorError('UNKNOWN_EDIT')
    validate_timeline(new)
    if new == tl:
        out['action'] = 'EDIT_NO_CHANGE'; out['message'] = 'That is already the current cut.'; return
    _append(root, 'CUT_SET', {'segments': new, 'prior_segments': tl, 'basis': basis, 'op': op, 'why': why,
                              'final_film_approval': False, 'reversible': True}, st['tail'])
    total = sum(s['out'] - s['in'] for s in new)
    out['action'] = 'CUT_CHANGED'
    out['message'] = f"Working film updated ({why}): {total} frames = {total/FPS:.2f}s. Basis: {basis}. 'undo' reverts it."
    if media_roots:
        try:
            _edit(root, state(root), {'op': 'RENDER'}, sub := {}, media_roots)
            out['render'] = sub['render']
        except DirectorError as ex:
            out['notes'].append('Render skipped: ' + str(ex)[:200])

# ----------------------------------------------------------------------------- backup
def export_all(root, dest):
    """Backup of all three ledgers, each re-verified from the backup copy."""
    root = Path(root); dest = Path(dest)
    if dest.exists(): raise DirectorError('BACKUP_EXISTS')
    st = state(root)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / base.PROJECT_DB
        s, t = base.connect(_db(root)), base.connect(path)
        try: s.backup(t)
        finally: s.close(); t.close()
        tmp_root = Path(tmp)
        st2 = state(tmp_root)
        if st2['tail'] != st['tail'] or st2['progress']['progress_tail_hash'] != st['progress']['progress_tail_hash']:
            raise DirectorError('STATE_CHANGED_DURING_BACKUP')
        wf = sorted((root / 'director_v0739' / 'workflows').glob('*.json')) if (root / 'director_v0739' / 'workflows').is_dir() else []
        manifest = {'schema': 'director-memory-backup-v0739', 'project_id': st['base']['project_id'],
                    'direction_tail': st['tail'], 'progress_tail': st['progress']['progress_tail_hash'],
                    'base_hash': st['base']['latest_event_hash'], 'database_sha256': base.digest(path),
                    'workflows': {p.name: base.digest(p) for p in wf},
                    'media_and_original_references_included': False}
        with zipfile.ZipFile(dest, 'x', zipfile.ZIP_DEFLATED) as z:
            z.write(path, base.PROJECT_DB)
            for p in wf: z.write(p, 'director_v0739/workflows/' + p.name)
            z.writestr('manifest_v0739.json', base.canonical(manifest) + '\n')
    return manifest

def import_all(archive, project):
    dst = Path(project)
    if dst.exists(): raise DirectorError('RESTORE_TARGET_EXISTS')
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if 'manifest_v0739.json' not in names or base.PROJECT_DB not in names: raise DirectorError('NOT_A_V0739_BACKUP')
        m = json.loads(z.read('manifest_v0739.json'))
        raw = z.read(base.PROJECT_DB)
        if hashlib.sha256(raw).hexdigest() != m['database_sha256']: raise DirectorError('BACKUP_DB_HASH')
        extra = set(names) - {'manifest_v0739.json', base.PROJECT_DB} - {'director_v0739/workflows/' + k for k in m['workflows']}
        if extra: raise DirectorError('UNEXPECTED_BACKUP_ENTRIES')
        stage_dir = dst.with_name(dst.name + '.restoring')
        if stage_dir.exists(): raise DirectorError('RESTORE_STAGE_EXISTS')
        stage_dir.mkdir(parents=True)
        try:
            (stage_dir / base.PROJECT_DB).write_bytes(raw)
            for k, h in m['workflows'].items():
                data = z.read('director_v0739/workflows/' + k)
                if hashlib.sha256(data).hexdigest() != h: raise DirectorError('BACKUP_WORKFLOW_HASH')
                (stage_dir / 'director_v0739' / 'workflows').mkdir(parents=True, exist_ok=True)
                (stage_dir / 'director_v0739' / 'workflows' / k).write_bytes(data)
            st = state(stage_dir)
            if st['tail'] != m['direction_tail'] or st['progress']['progress_tail_hash'] != m['progress_tail']:
                raise DirectorError('RESTORED_STATE_DIVERGED')
            os.replace(stage_dir, dst)
        finally:
            if stage_dir.exists(): shutil.rmtree(stage_dir)
    return status_text(state(dst))

# ----------------------------------------------------------------------------- CLI
def print_result(r):
    if r.get('understood_as'): print('UNDERSTOOD AS:', r['understood_as'])
    for a in r.get('assumptions', []): print('  assumption:', a)
    for nte in r.get('notes', []): print('NOTE:', nte)
    print('ACTION:', r['action'])
    if r.get('message'): print(r['message'])
    if r.get('workflow_path'): print('COMFYUI WORKFLOW (drag into ComfyUI, not queued):', r['workflow_path'])
    if r.get('staged_to'): print('ALSO IN COMFYUI WORKFLOWS SIDEBAR:', r['staged_to'])
    if r.get('comfyui_prompt_id'): print('QUEUED ON COMFYUI (you passed --generate): prompt_id', r['comfyui_prompt_id'])
    if r.get('render'): print('RENDER:', json.dumps(r['render']))
    if r['action'] != 'STATUS': print('---\n' + r['state'])

def main(argv=None):
    ap = argparse.ArgumentParser(description='H3 Director V0.7.39: tell it what you want in plain words.')
    ap.add_argument('instruction', nargs='?', default='where are we?')
    ap.add_argument('--workspace', type=Path, default=Path('/workspace'))
    ap.add_argument('--project', type=Path)
    ap.add_argument('--stage', action='store_true', help='also copy the workflow into the ComfyUI Workflows sidebar')
    ap.add_argument('--generate', action='store_true', help='queue the planned shot on ComfyUI (your explicit go-ahead)')
    ap.add_argument('--yes', action='store_true', help='skip the confirmation question for an approval')
    ap.add_argument('--comfy-url', default='http://127.0.0.1:18188')
    ap.add_argument('--export', type=Path, help='write a full memory backup zip and exit')
    ap.add_argument('--import-backup', type=Path, help='restore a backup zip into --project and exit')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    ws = a.workspace
    root = a.project or ws / 'DirectorProjects' / 'hallway_interview_v1'
    if a.import_backup:
        print(import_all(a.import_backup, root)); return 0
    if not root.exists():
        base.init_project(root, HERE / 'scene_seed_v0735.json', HERE / 'autoedit_case.json', HERE / 'live_f36_decision.json',
                          ws / 'ComfyUI' / 'input', ws / 'DirectorSavedShotQueueV066', ws / 'ComfyUI' / 'output')
        base.continue_interview(root, 'continue the interview', ws / 'ComfyUI' / 'input', HERE / 'workflow_template_verified.json')
        print('PROJECT INITIALIZED FROM REAL CAPTURE RECORDS')
    if a.export:
        print(json.dumps(export_all(root, a.export), indent=2)); return 0
    r = direct(root, a.instruction, originals_dir=ws / 'ComfyUI' / 'input', template=HERE / 'workflow_template_verified.json',
               saved_root=ws / 'DirectorSavedShotQueueV066', source_root=ws / 'ComfyUI' / 'output',
               media_roots=[ws / 'ComfyUI' / 'output', HERE / 'media'], comfy_root=ws / 'ComfyUI',
               do_stage=a.stage, do_generate=a.generate, comfy_url=a.comfy_url, yes=a.yes)
    if a.json: print(json.dumps(r, indent=2, default=str))
    else: print_result(r)
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except (base.MemoryErrorSafety, cg.GraphError, ValueError) as e:
        print('DIRECTOR NEEDS ATTENTION:', type(e).__name__, str(e), file=sys.stderr); sys.exit(2)
