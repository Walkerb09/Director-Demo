"""'Report a problem': one zip with what's needed to find a bug, and none of your media.

Included: versions (package, Python, OpenCV, numpy, aiohttp, ComfyUI, GPU/RAM, ComfyUI's launch options, the container's
memory limit and out-of-memory kills, ComfyUI restarts), the health check, recent Director errors
and crash reports (this server), the project's settings, counts, event kinds and its last events (text only), what the
summary/story/timeline give or the error they raise, the browser's own errors and what the page was showing, background
jobs, the Director/ error lines of the ComfyUI log, and (V0.9.18) the stage recorder: while one of your takes runs, every
15 s which ComfyUI node is working, what each thread is doing (code location only) and how much CPU it uses, GPU use and RAM.
Never included: photos, videos, audio, references, takes, renders, backups or the ledger file itself.
"""
from __future__ import annotations
import collections, io, json, os, platform, re, shutil, subprocess, sys, time, traceback, zipfile
from pathlib import Path

RECENT = collections.deque(maxlen=300)          # (time, kind, message) for everything that went wrong on this server

def note(kind, message):
    RECENT.append({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'kind': kind, 'message': str(message)[-4000:]})

MEDIA_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.heic', '.heif', '.avif', '.mp4', '.mov', '.m4v', '.webm', '.mkv', '.wav', '.mp3', '.m4a',
             '.aac', '.ogg', '.opus', '.flac', '.aif', '.aiff', '.wma', '.amr', '.zip', '.npz', '.sqlite3'}

STAGES = collections.deque(maxlen=400)         # stage recorder samples (V0.9.18)
_CPU = {}                                       # tid -> (cpu ticks, time) for per-thread CPU %

def _thread_cpu():
    """{native thread id: CPU % since the last sample} from /proc (no ptrace needed)."""
    out, now = {}, time.time()
    try: tids = os.listdir('/proc/self/task')
    except Exception: return out
    for t in tids:
        try:
            f = Path(f'/proc/self/task/{t}/stat').read_text(); f = f[f.rindex(')') + 2:].split()
            ticks = int(f[11]) + int(f[12]); tid = int(t)
        except Exception: continue
        old = _CPU.get(tid); _CPU[tid] = (ticks, now)
        if old and now > old[1]: out[tid] = round(100 * (ticks - old[0]) / os.sysconf('SC_CLK_TCK') / (now - old[1]))
    return out

def stacks(depth=14):
    """Every Python thread of this ComfyUI: name, CPU %, and where its code is (file:line function), innermost last."""
    import threading
    cpu = _thread_cpu(); frames = sys._current_frames(); out = []
    for th in threading.enumerate():
        f = frames.get(th.ident)
        if f is None: continue
        where = [f'{Path(x.filename).name}:{x.lineno} {x.name}' for x in traceback.extract_stack(f)][-depth:]
        out.append({'thread': th.name, 'cpu_pct': cpu.get(th.native_id), 'where': where})
    out.sort(key=lambda x: -(x['cpu_pct'] or 0))
    return out

def sample_stage(prompt_id, node_class, label):
    """One stage-recorder sample (called every ~15 s while a Director take runs)."""
    g = _run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,power.draw', '--format=csv,noheader,nounits'], timeout=5)
    try: rss = int(re.search(r'VmRSS:\s+(\d+)', Path('/proc/self/status').read_text()).group(1)) // 1024
    except Exception: rss = None
    th = stacks()
    STAGES.append({'at': time.strftime('%H:%M:%S'), 'take': label, 'prompt_id': prompt_id, 'node': node_class, 'gpu_util_mem_power': g,
                   'rss_mib': rss, 'busy_threads': [t for t in th if (t['cpu_pct'] or 0) >= 5][:8] or th[:3]})

def stage_summary():
    """How long each node ran per take (from the samples)."""
    out = collections.OrderedDict()
    for s in STAGES:
        k = (s['take'], s['node']); out[k] = out.get(k, 0) + 1
    return [{'take': k[0], 'node': k[1], 'about_seconds': n * 15} for k, n in out.items()]

def _run(cmd, timeout=10):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception as e: return f'n/a ({type(e).__name__})'

def versions(package_root, comfy_root):
    out = {'python': sys.version.split()[0], 'platform': platform.platform(), 'time': time.strftime('%Y-%m-%d %H:%M:%S %Z')}
    for mod in ('cv2', 'numpy', 'aiohttp', 'torch', 'PIL'):
        try: out[mod] = getattr(__import__(mod), '__version__', '?')
        except Exception as e: out[mod] = f'not importable ({type(e).__name__})'
    try: out['package_manifest'] = json.loads((Path(package_root) / 'package_manifest.json').read_text()).get('schema')
    except Exception: out['package_manifest'] = None
    try:
        vf = Path(comfy_root) / 'comfyui_version.py'
        out['comfyui'] = re.search(r'__version__\s*=\s*["\']([^"\']+)', vf.read_text()).group(1) if vf.is_file() else None
    except Exception: out['comfyui'] = None
    out['gpu'] = _run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,driver_version', '--format=csv,noheader'])
    out['ram'] = _run(['free', '-g'])
    out['disk_workspace'] = _run(['df', '-h', str(Path(comfy_root).parent)])
    out['ffmpeg'] = (_run(['ffmpeg', '-version']).splitlines() or [''])[0]
    try:
        import studio_threads_v0918 as thr
        real, seen = thr.cores(); out['cpu_cores_real_of_seen'] = f'{real} of {seen}'; out['cpu_threads'] = thr.describe()
        out['cpu_throttled'] = (_run(['cat', '/sys/fs/cgroup/cpu.stat']) if Path('/sys/fs/cgroup/cpu.stat').is_file()
                                else _run(['cat', '/sys/fs/cgroup/cpu/cpu.stat']))[:400]
    except Exception as e: out['cpu_threads'] = f'n/a ({type(e).__name__})'
    out['comfyui_command_line'] = ' '.join(sys.argv)[:600]            # e.g. whether --disable-dynamic-vram is on
    try:
        import comfy.cli_args as ca
        out['comfyui_dynamic_vram_disabled'] = bool(getattr(ca.args, 'disable_dynamic_vram', None))
    except Exception: out['comfyui_dynamic_vram_disabled'] = None
    for f in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.events', '/sys/fs/cgroup/memory.peak'):   # container memory limit and kills
        try: out['cgroup_' + Path(f).name] = Path(f).read_text().strip()[:400]
        except Exception: pass
    out['supervisor_comfyui'] = '\n'.join(l for l in _run(['tail', '-n', '400', '/var/log/supervisor/supervisord.log']).splitlines() if 'comfyui' in l)[-1500:]
    return out

def project_part(studio):
    """Settings, counts, event kinds, the last events (text only), and what the page's data calls give."""
    out = {}
    try:
        evs = studio.events()
        kinds = collections.Counter(e['kind'] for e in evs)
        def slim(p):
            q = {}
            for k, v in p.items():
                if k in ('prompt', 'summary', 'continuity'): q[k] = (str(v)[:600] + '…') if v and len(str(v)) > 600 else v
                elif isinstance(v, (str, int, float, bool)) or v is None: q[k] = v
                elif isinstance(v, (list, dict)): q[k] = v if len(json.dumps(v, default=str)) <= 1500 else f'<{type(v).__name__} of {len(v)}>'
            return q
        out['event_count'] = len(evs); out['event_kinds'] = dict(kinds.most_common())
        out['last_events'] = [{'seq': e['seq'], 'kind': e['kind'], 'payload': slim(e['payload'])} for e in evs[-80:]]
    except Exception as e: out['events_error'] = traceback.format_exc()[-3000:]
    try:
        st = studio.state()
        out['settings'] = st['settings']
        out['counts'] = {'characters': len(st['characters']), 'locations': len(st['locations']), 'refs': len(st['refs']),
                         'shots': len(st['shots']), 'takes': sum(len(s['takes']) for s in st['shots'].values()),
                         'plans': sum(len(s['plans']) for s in st['shots'].values()), 'submissions': len(st['submissions'])}
        out['takes'] = [{'shot': n, 'take': t['take'], 'status': t['status'], 'file': t['file'], 'frames': t.get('frames'),
                         'measured': t['analysis'] is not None, 'analysis_error': t.get('analysis_error'), 'source': bool(t.get('source'))}
                        for n, s in sorted(st['shots'].items()) for t in s['takes']]
        out['refs'] = [{'id': r, 'owner': x['owner'], 'kind': x['kind'], 'variant': x.get('variant'), 'removed': bool(x.get('removed')),
                        'file_present': (studio.input_dir / x['input_name']).is_file()} for r, x in st['refs'].items()]
    except Exception: out['state_error'] = traceback.format_exc()[-3000:]
    for name in ('summary', 'timeline', 'story'):
        try:
            r = getattr(studio, name)() if name != 'story' else studio.story(studio.state())
            out[name + '_ok'] = True
            if name == 'timeline': out['timeline'] = [{k: c.get(k) for k in ('shot', 'take', 'in', 'out', 'why', 'warnings')} for c in r]
        except Exception: out[name + '_error'] = traceback.format_exc()[-4000:]
    try:
        out['output_folder'] = sorted((p.name, p.stat().st_size) for p in studio.output_dir.glob('Studio_*') if p.is_file())[-60:]
    except Exception: pass
    return out

def log_tail(paths=('/var/log/portal/comfyui.log',), keep=500):
    """Only the Director's own lines and error tracebacks from the ComfyUI log (never prompts from other workflows)."""
    for p in paths:
        try:
            lines = Path(p).read_text(errors='replace').splitlines()[-6000:]
        except Exception: continue
        out, grab = [], 0
        for l in lines:
            if 'DirectorStudio' in l or 'Traceback' in l or 'Error' in l or 'Fatal Python' in l or 'Segmentation' in l: grab = 25
            if grab: out.append(l); grab -= 1
        return out[-keep:]
    return []

def build(dest, *, package_root, comfy_root, studio=None, health=None, client=None, jobs=None):
    """Write the report zip to `dest`; returns the list of files in it."""
    parts = {
        'versions.json': versions(package_root, comfy_root),
        'health.json': health,
        'recent_errors.json': list(RECENT),
        'browser.json': client or {},
        'jobs.json': jobs or [],
        'comfyui_log_director_lines.txt': '\n'.join(log_tail()),
        'stage_recorder.json': {'summary': stage_summary(), 'samples': list(STAGES)},
        'threads_now.json': stacks(),
    }
    if studio is not None: parts['project.json'] = project_part(studio)
    dest = Path(dest); tmp = dest.with_name(dest.name + '.partial')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data if isinstance(data, str) else json.dumps(data, indent=1, default=str))
        z.writestr('README.txt', __doc__)
    os.replace(tmp, dest)
    with zipfile.ZipFile(dest) as z: names = z.namelist()
    assert not any(Path(n).suffix.lower() in MEDIA_EXT for n in names)
    return names
