"""Which Qwen3-VL the rewriter and the 🔍 check use (Settings > Qwen model), and whether it is already downloaded."""
from __future__ import annotations
import os, re, subprocess
from pathlib import Path

# V0.9.36: the 32B is the abliterated Qwen3-VL-32B-Instruct (huihui-ai; BF16, same size and same loader as the official one)
# V0.9.39 🧠: '32b-think' (huihui-ai's abliterated Qwen3-VL-32B-THINKING) is only the 🎬 film writer (Settings > Film writer):
# it thinks before it answers. Rewrites and 🔍 checks during generation keep the model above, so they stay quick.
# V0.9.55 '27b': Qwen3.6-27B (April 2026, the generation after Qwen3-VL; vision built in) as huihui-ai's abliterated BF16. One model
# answers quickly (thinking off) or thinks first (🧠 film writer). It needs a newer transformers (5.x), so it runs in its own
# environment, h3-qwen36-venv (the installer's --qwen36, or ⬇ Download in Settings), and the 8B / 32B keep theirs.
MODELS = {'8b': 'Qwen/Qwen3-VL-8B-Instruct', '32b': 'huihui-ai/Huihui-Qwen3-VL-32B-Instruct-abliterated',
          '32b-think': 'huihui-ai/Huihui-Qwen3-VL-32B-Thinking-abliterated', '27b': 'huihui-ai/Huihui-Qwen3.6-27B-abliterated'}
SIZES_GB = {'8b': 17, '32b': 66, '32b-think': 66, '27b': 56}
NEED_GPU_MIB = {'8b': int(22.5 * 1024), '32b': int(74 * 1024), '32b-think': int(80 * 1024), '27b': int(64 * 1024), '27b-think': int(72 * 1024)}   # free GPU memory before Qwen starts (weights + working room; thinking makes much longer answers)
NEW_GEN = ('qwen3.5', 'qwen3.6')
NEW_VENV = '/workspace/h3-qwen36-venv'

def is_new_gen(model_id_):
    """Qwen3.5 / 3.6: vision built in, thinking switched by the chat template, transformers 5."""
    return any(k in str(model_id_ or '').lower() for k in NEW_GEN)

def python_for(model_id_, default):
    """The Python environment that runs this model: the new generation runs in its own (h3-qwen36-venv) when there is one."""
    if not is_new_gen(model_id_): return default
    p = os.environ.get('DIRECTOR_QWEN36_PYTHON') or (NEW_VENV + '/bin/python')
    return p if Path(p).is_file() else default
FILM_WRITERS = ('same', 'think_plan', 'think_all')       # 🎬 the Qwen model above, no thinking / 32B Thinking thinks about the plan / the plan and every shot

def is_thinking(model_id_):
    return 'thinking' in str(model_id_ or '').lower()

def need_mib(model_id_, think=False):
    try:                                                   # V0.9.70 the 24 GB demo set: the 8B with less working room
        import studio_profile_v0970 as pf
        q = pf.get().get('qwen_need_mib') or {}
        if q and not is_new_gen(model_id_) and '32B' not in str(model_id_): return q['8b']
    except ImportError: pass
    if is_new_gen(model_id_): return NEED_GPU_MIB['27b-think' if think else '27b']
    if is_thinking(model_id_) and '32B' in str(model_id_): return NEED_GPU_MIB['32b-think']
    return NEED_GPU_MIB['32b'] if '32B' in str(model_id_) else NEED_GPU_MIB['8b']

def film_model(settings):
    """-> (model id, thinking) for the 🎬 film writer. DIRECTOR_FILM_MODEL overrides; else Settings > Film writer: 'same' is the
    rewriter's model (DIRECTOR_REWRITE_MODEL / Qwen model), 'think_plan' / 'think_all' the 32B Thinking."""
    settings = settings or {}
    if os.environ.get('DIRECTOR_FILM_MODEL'): mid = os.environ['DIRECTOR_FILM_MODEL']
    elif settings.get('film_writer') in ('think_plan', 'think_all') and settings.get('qwen_model') == '27b': mid = MODELS['27b']   # V0.9.55 the same model, thinking on
    elif settings.get('film_writer') in ('think_plan', 'think_all'): mid = MODELS['32b-think']
    else: mid = model_id(settings.get('qwen_model'), 'DIRECTOR_REWRITE_MODEL')
    if is_new_gen(mid) and settings.get('film_writer') in ('think_plan', 'think_all'):
        return mid, ('all' if settings.get('film_writer') == 'think_all' else 'plan')
    if not is_thinking(mid): return mid, None
    return mid, ('all' if settings.get('film_writer') == 'think_all' else 'plan')

def model_id(choice=None, env_var=None):
    """An environment override (DIRECTOR_REWRITE_MODEL / DIRECTOR_CHECK_MODEL) wins; else the Settings choice."""
    if env_var and os.environ.get(env_var): return os.environ[env_var]
    return MODELS.get(choice or '8b', MODELS['8b'])

def _candidates():
    """Every Hugging Face cache this machine may use: Vast's ComfyUI sets its own HF_HOME, the installer uses
    /workspace/.cache/huggingface. (V0.9.18: the 32B showed 'not downloaded' because only HF_HOME was looked at.)"""
    out = []
    for h in (os.environ.get('HF_HOME'), '/workspace/.cache/huggingface', '/workspace/.hf_home', '/workspace/huggingface',
              str(Path.home() / '.cache' / 'huggingface')):
        if h and Path(h) not in out: out.append(Path(h))
    hub = os.environ.get('HF_HUB_CACHE') or os.environ.get('HUGGINGFACE_HUB_CACHE')
    if hub and Path(hub).parent not in out and Path(hub).name == 'hub': out.insert(0, Path(hub).parent)
    return out

def _complete(snap):
    """A snapshot folder with every weight file of its index (a half-finished download doesn't count)."""
    idx = snap / 'model.safetensors.index.json'
    try:
        if idx.is_file():
            import json
            files = set(json.loads(idx.read_text())['weight_map'].values())
            return bool(files) and all((snap / f).is_file() and (snap / f).stat().st_size > 0 for f in files)
        return any(f.is_file() and f.stat().st_size > 0 for f in snap.glob('*.safetensors'))
    except (OSError, ValueError, KeyError): return False

def where(model_id_):
    """The HF_HOME that holds a complete copy of this model, or None."""
    repo = str(model_id_).replace('/', '--')
    for h in _candidates():
        snaps = h / 'hub' / f'models--{repo}' / 'snapshots'
        try:
            if snaps.is_dir() and any(_complete(d) for d in snaps.iterdir() if d.is_dir()): return h
        except OSError: continue
    return None

def hf_home(model_id_=None):
    """Where to look for / put the model: the cache that already has it, else the installer's /workspace cache."""
    if model_id_:
        w = where(model_id_)
        if w: return w
    if Path('/workspace/.cache/huggingface').is_dir(): return Path('/workspace/.cache/huggingface')
    h = os.environ.get('HF_HOME')
    return Path(h) if h else Path.home() / '.cache' / 'huggingface'

def downloaded(choice):
    """True when the model's weights are complete in one of the Hugging Face caches (so first use doesn't download ~GB)."""
    mid = MODELS.get(choice, MODELS['8b'])
    if is_new_gen(mid) and not Path(os.environ.get('DIRECTOR_QWEN36_PYTHON') or (NEW_VENV + '/bin/python')).is_file(): return False   # V0.9.55 needs its own environment too
    return where(mid) is not None

NEW_VENV_PACKAGES = ('transformers>=5.2,<6', 'accelerate>=1,<2', 'huggingface_hub>=0.35,<2', 'safetensors', 'pillow', 'sentencepiece')
def ensure_new_venv(base_python, timeout=1800):
    """V0.9.55 the Qwen3.6 environment: shares the machine's torch (like h3-qwen-venv), with transformers 5. Made once."""
    py = Path(NEW_VENV) / 'bin' / 'python'
    if not py.is_file():
        r = subprocess.run([str(base_python), '-m', 'venv', '--system-site-packages', NEW_VENV], capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not py.is_file(): raise RuntimeError('QWEN36_ENV_FAILED: ' + (r.stderr or r.stdout).strip()[-300:])
    r = subprocess.run([str(py), '-c', 'import transformers,sys; v=tuple(int(x) for x in transformers.__version__.split(".")[:2]); sys.exit(0 if v>=(5,2) else 1)'],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        r = subprocess.run([str(py), '-m', 'pip', 'install', '--disable-pip-version-check', *NEW_VENV_PACKAGES], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0: raise RuntimeError('QWEN36_ENV_FAILED: ' + ' '.join((r.stderr or r.stdout).strip().splitlines()[-2:])[:300])
    return str(py)

def download(choice, qwen_python, timeout=4 * 3600):
    """Fetch the model into the shared cache with the Qwen environment's huggingface_hub (resumes if interrupted)."""
    repo = MODELS[choice]
    if is_new_gen(repo): qwen_python = ensure_new_venv(qwen_python)                  # V0.9.55 its own environment first
    env = dict(os.environ); env['HF_HOME'] = str(hf_home(repo)); env.setdefault('HF_HUB_DISABLE_TELEMETRY', '1'); env.setdefault('HF_XET_HIGH_PERFORMANCE', '1')
    code = ('from huggingface_hub import snapshot_download; import sys; print(snapshot_download(repo_id=sys.argv[1], '
            'allow_patterns=["*.safetensors","*.json","*.txt","*.model","*.jinja","merges.txt","vocab.json"]))')
    r = subprocess.run([str(qwen_python), '-c', code, repo], capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0: raise RuntimeError('QWEN_DOWNLOAD_FAILED: ' + ' '.join((r.stderr or r.stdout).strip().splitlines()[-2:])[:300])
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''

# -- V0.9.38: when a Qwen worker dies, say HOW (a crash, a kill, a Python error) and keep its whole output ---------------
SIGNALS = {11: 'crashed inside PyTorch/CUDA (segmentation fault)', 9: 'was killed by the system (often: out of memory)',
           6: 'aborted inside PyTorch/CUDA', 15: 'was stopped', 7: 'crashed (bus error)'}

def log_dir():
    return Path(os.environ.get('DIRECTOR_SAVED_ROOT', '/workspace/DirectorSavedShotQueueV066')) / 'qwen_logs'

def why_failed(returncode, output, kind):
    """-> one plain line: how the worker ended and the line that matters; its whole output is saved to
    <saved>/qwen_logs/<kind>_last.txt (the Director's message names the file)."""
    out = str(output or '')
    path = None
    try:
        d = log_dir(); d.mkdir(parents=True, exist_ok=True); path = d / f'{kind}_last.txt'
        path.write_text(f'exit {returncode}\n' + out)
    except OSError: path = None
    lines = [l.strip() for l in out.replace('\r', '\n').splitlines() if l.strip() and 'Loading checkpoint' not in l and 'generation flags' not in l]
    if returncode is not None and returncode < 0:
        what = 'Qwen ' + SIGNALS.get(-returncode, f'was stopped by signal {-returncode}')
    else:
        err = next((l for l in reversed(lines) if re.match(r'^[A-Za-z_.]*(Error|Exception|QWEN_[A-Z_]+)\b', l)), None)
        what = f'Qwen stopped with an error (exit {returncode})' + (f': {err[:220]}' if err else (f': {lines[-1][:220]}' if lines else ''))
    return what + (f' · full output: {path}' if path else '')
