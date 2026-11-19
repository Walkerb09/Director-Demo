"""Director Studio V0.8.1 <-> Qwen observer bridge (V0.6.6 job format -> V0.7.5.1 -> V0.7.4 -> V0.7.2.2).

Studio takes are saved by VHS without the old V0.6.6 capture node, so the existing Qwen
dispatcher never saw them. This bridge:
  * writes a V0.6.6-identical capture job for each ingested take (same job-id formula, same guards)
  * writes a cast profile per project (prefix Studio_<slug>_, cast from your people, max 4)
  * starts ONE dispatcher run (--once --run) in the background on request or automatically
  * reads the published V0.7.4 summary back and maps role IDs to your character names.
Everything Qwen says stays UNVERIFIED observation; it never edits, approves or conditions H3.
"""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISPATCHER = HERE / 'qwen' / 'director_idle_dispatcher_v0751.py'
DEFAULTS = {
    'saved_root': Path(os.environ.get('DIRECTOR_SAVED_ROOT', '/workspace/DirectorSavedShotQueueV066')),
    'v074': Path(os.environ.get('DIRECTOR_V074', '/workspace/DirectorUnifiedV074/director_postshot_v074.py')),
    'qwen_python': Path(os.environ.get('DIRECTOR_QWEN_PYTHON', '/workspace/h3-qwen-venv/bin/python')),
    'profiler': Path('/workspace/DirectorProfiledV0722/profiled_observer_v0722.py'),
    'context': Path('/workspace/DirectorSceneContextV073/scene_context_v073.py'),
    'v065': Path('/workspace/ComfyUI/custom_nodes/ComfyUI-Director-SemanticObserver-V063/semantic_observer.py'),
}
ROLE_RX = re.compile(r'[A-Z][A-Z0-9_]{1,19}\Z')

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def video_meta(path):
    """Identical to V0.6.6 shot_capture.video_meta (count frames, avg_frame_rate)."""
    cp = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
                         'stream=width,height,nb_read_frames,avg_frame_rate', '-of', 'json', str(path)],
                        capture_output=True, text=True, timeout=120)
    if cp.returncode: raise RuntimeError('ffprobe failed ' + cp.stderr[-300:])
    st = json.loads(cp.stdout)['streams'][0]
    n, d = (int(x) for x in st['avg_frame_rate'].split('/'))
    return {'frames': int(st['nb_read_frames']), 'width': int(st['width']), 'height': int(st['height']), 'fps': n / d}

def enqueue_take(video, saved_root=None, samples=8):
    """Write the V0.6.6 capture record for a Studio take (idempotent). Returns job_id."""
    root = Path(saved_root or DEFAULTS['saved_root']); jobs = root / 'jobs'
    (root / 'results').mkdir(parents=True, exist_ok=True); jobs.mkdir(parents=True, exist_ok=True)
    src = Path(video).resolve(strict=True)
    before = src.stat(); digest = sha(src); after = src.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns): raise RuntimeError('VIDEO_CHANGED_WHILE_HASHING')
    token = {'source_video_path': str(src), 'source_sha256': digest, 'source_class': 'H3_GENERATED_OBSERVATION_ONLY', 'samples': samples}
    job_id = hashlib.sha256(json.dumps(token, sort_keys=True).encode()).hexdigest()[:24]
    record = {'version': 'director-saved-shot-capture-0.6.6', 'phase': 'PENDING_SHADOW_OBSERVATION', 'job_id': job_id, **token,
              'video_meta': video_meta(src), 'video_size_bytes': after.st_size, 'origin_declared_not_independently_certified': True,
              'generated_footage_for_H3_visual_conditioning': False, 'observation_authority': 'NONE',
              'automatic_editing_authorized': False, 'continuity_handoff_authorized': False,
              'director_memory_changed': False, 'h3_generation_performed': False}
    if jobs.resolve().is_relative_to(src.parent): raise ValueError('JOB_DIR_INSIDE_VIDEO_FOLDER')
    dest = jobs / f'{job_id}.json'
    data = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode()
    if dest.exists():
        if dest.read_bytes() != data: raise ValueError('JOB_ID_COLLISION_DIFFERENT_CONTENT')
    else:
        with dest.open('xb') as f: f.write(data)
    return job_id

def role_id(name, used):
    base = re.sub(r'[^A-Z0-9_]', '_', name.upper()).strip('_') or 'PERSON'
    if not base[0].isalpha(): base = 'P_' + base
    base = base[:20]; rid = base; k = 2
    while rid in used or rid in ('NONE', 'UNCERTAIN') or not ROLE_RX.fullmatch(rid):
        rid = (base[:17] + f'_{k}'); k += 1
    used.add(rid); return rid

def write_profile(profiles_dir, slug, characters):
    """Cast profile for the V0.7.2.2 observer: up to 4 people, plain-text descriptions (8-180 chars)."""
    d = Path(profiles_dir); d.mkdir(parents=True, exist_ok=True)
    used = set(); cast = []; mapping = {}
    for c in characters[:4]:
        rid = role_id(c['name'], used)
        desc = ' '.join(f"{c['name']}, {('a woman' if c.get('pronoun') == 'she' else 'a man' if c.get('pronoun') == 'he' else 'a person')}"
                        f"{', ' + c['description'] if c.get('description') else ''}".replace('|', ' ').replace('=', ' ').split())
        desc = (desc + ' (as in the reference images)')[:180] if len(desc) < 8 else desc[:180]
        cast.append({'id': rid, 'description': desc}); mapping[rid] = c['id']
    if not cast: return None, {}
    pid = ('studio-' + slug.replace('_', '-'))[:41]
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,40}', pid): pid = 'studio-' + hashlib.sha256(slug.encode()).hexdigest()[:10]
    prof = {'schema': 'director-scene-profile-v1', 'project_id': pid, 'video_name_prefix': f'Studio_{slug}_'[:80],
            'model_id': 'Qwen/Qwen3-VL-8B-Instruct', 'cast': cast}
    path = d / f'{pid}.json'
    data = json.dumps(prof, indent=2) + '\n'
    if not path.exists() or path.read_text() != data: path.write_text(data)
    hallway = HERE / 'qwen' / 'profiles' / 'hallway-v1.json'
    if hallway.is_file() and not (d / 'hallway-v1.json').exists(): (d / 'hallway-v1.json').write_text(hallway.read_text())
    return path, mapping

def installed(paths=None):
    p = {**DEFAULTS, **(paths or {})}
    items = {'dispatcher V0.7.5.1 (bundled)': DISPATCHER.is_file(), 'V0.7.4 unified observer': p['v074'].is_file(),
             'V0.7.2.2 Qwen observer': p['profiler'].is_file(), 'V0.7.3 scene context': p['context'].is_file(),
             'V0.6.5 frame helper (ComfyUI node)': p['v065'].is_file(), 'Qwen Python env (h3-qwen-venv)': p['qwen_python'].is_file()}
    return {'ready': all(items.values()), 'items': items}

def status(job_id, saved_root=None):
    root = Path(saved_root or DEFAULTS['saved_root'])
    summ = root / 'results' / job_id / 'director_postshot_v074.json'
    fail = root / 'dispatcher_v075' / 'failures' / f'{job_id}.json'
    if summ.is_file():
        try: return {'state': 'DONE', 'summary': json.loads(summ.read_text())}
        except ValueError: return {'state': 'UNREADABLE'}
    if fail.is_file():
        try: return {'state': 'FAILED', 'reason': json.loads(fail.read_text()).get('reason')}
        except ValueError: return {'state': 'FAILED'}
    return {'state': 'QUEUED'}

def hints(summary, mapping, names):
    """V0.7.4 summary -> short, explicitly unverified hints using the project's own names."""
    out = []
    for b in summary.get('candidate_cut_barriers', []):
        out.append(f"Qwen/observer: possible cut at F{b.get('frame')} (unverified)")
    for seg in summary.get('observed_candidate_segments', []):
        fr = seg.get('frames', ['?', '?'])
        cast = (seg.get('sample_agreement_candidates') or {}).get('cast') or {}
        seen = [names.get(mapping.get(r, ''), r.title()) + ('' if v == 'YES' else '?' if v == 'UNCERTAIN' else ' absent')
                for r, v in cast.items() if v in ('YES', 'UNCERTAIN', 'NO')]
        mic = (seg.get('sample_agreement_candidates') or {}).get('microphone_visible')
        out.append(f"Qwen sees F{fr[0]}-{fr[1]}: " + ', '.join(seen) + ('; a microphone is visible' if (mic or '').upper() == 'YES' else '') + ' (unverified)')
    return out

_RUNNING = {}

def is_running(saved_root=None):
    """Is a Qwen dispatcher pass started from this server still running?"""
    p = _RUNNING.get(str(saved_root or DEFAULTS['saved_root']))
    return p is not None and p.poll() is None

def run_once(profiles_dir, saved_root=None, *, comfy_url='http://127.0.0.1:18188', v074=None, python=None, popen=subprocess.Popen):
    """Start ONE dispatcher pass in the background (it observes the oldest eligible job, then exits).
    Returns 'STARTED' or 'ALREADY_RUNNING'. The dispatcher holds its own exclusive lock as well."""
    key = str(saved_root or DEFAULTS['saved_root'])
    p = _RUNNING.get(key)
    if p is not None and p.poll() is None: return 'ALREADY_RUNNING'
    root = Path(key); logs = root / 'dispatcher_v075'; logs.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'DIRECTOR_COMFY_URL': comfy_url}
    cmd = [str(python or sys.executable), '-u', str(DISPATCHER), '--once', '--run', '--root', key,
           '--v074', str(v074 or DEFAULTS['v074']), '--profiles-dir', str(profiles_dir)]
    log = open(logs / f'studio_launch_{time.strftime("%Y%m%dT%H%M%S")}.log', 'a')
    _RUNNING[key] = popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=env)
    return 'STARTED'
