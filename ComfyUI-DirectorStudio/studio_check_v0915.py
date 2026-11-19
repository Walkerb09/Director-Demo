"""Did it do what I asked? (V0.9.15): Qwen3-VL watches a take and answers your direction point by point.

  "Grandpa walks in behind Maya"  -> seen (at about 1.5 s)
  "Maya fixes her hair"           -> not seen

A HINT, never an approval and never ground truth: it is shown on the take card, it can nudge the ranking a little, and it
never keeps, rejects or sends anything. Qwen only looks (frames of the take are never used as references for H3). It runs
when ComfyUI is idle (after a single take, or in the morning after an overnight run), the same way as the rewriter.
"""
from __future__ import annotations
import json, os, re, subprocess, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE / 'studio_check_worker_v0915.py'
MODEL_ID = os.environ.get('DIRECTOR_CHECK_MODEL', 'Qwen/Qwen3-VL-8B-Instruct')
N_FRAMES = 8
MAX_POINTS = 5
SEEN = ('yes', 'no', 'unclear')

def points(actions, names):
    """Your direction -> up to 5 short, checkable points (people named, spoken lines left out: sound isn't judged here)."""
    import studio_coverage_v098 as cv, studio_prompts_v094 as pv
    out = []
    for a in actions or []:
        for c in cv.clauses(pv.action_without_lines(a)):
            c = re.sub(r'^(?:and|then|while|as)\s+', '', c.strip(' ,.;'), flags=re.I)
            if len(c) < 6 or not re.search(r'[a-z]{3}', c, re.I): continue
            if re.match(r'^(?:wide|medium|close|over the shoulder|waist-up|two-shot)\b', c, re.I) and '. ' not in c: continue   # framing words only
            c = re.sub(r'^(?:wide shot|medium shot|close-up|waist-up (?:two-)?shot|over the shoulder)[^.]*\.\s*', '', c, flags=re.I)
            if c and c not in out: out.append(c[0].upper() + c[1:])
    return out[:MAX_POINTS]

PROMPT = """These {n} pictures are frames of ONE short video, in order, each labelled with its time in seconds.
People: {people}.
First look at every frame and note in a few plain words what it really shows: who is there and what they are doing.
Then, using only those notes, say for each statement below whether the video shows it happening.
Be strict, a wrong "yes" is worse than a "no":
- "yes" only when you can name the frame where you see it happening: give that frame's time and what you see in it.
- "no" when the frames show something else, or it never happens.
- "unclear" when you can't tell. When in doubt it is not "yes".
- Something only about to happen, only started, or only like the words, is not "yes".
Statements:
{statements}
Answer with JSON only: {{"frames": [{{"t": <seconds>, "shows": "..."}}, ...],
"answers": [{{"n": 1, "seen": "yes|no|unclear", "at": <time of that frame or null>, "evidence": "what that frame shows, or why not"}}, ...]}}"""

BEFORE_LABEL = 'PREVIOUS SHOT (its last moment, only to compare with; NOT part of this video)'
BEFORE_NOTE = ('The first picture, labelled PREVIOUS SHOT, is the last moment of the shot that comes before this video in the '
               'film: use it ONLY to judge whether this video carries on from it. The other pictures are the video. ')

def request(take_frames, pts, people, model=None, before=None):
    """take_frames: [(seconds, jpg_path)]; before (V0.9.36 🎬): a jpg of the last moment of the previous shot in the film."""
    frames = ([[BEFORE_LABEL, str(before)]] if before else []) + [[round(t, 2), str(p)] for t, p in take_frames]
    return {'model_id': model or MODEL_ID, 'frames': frames, 'max_new_tokens': 300 + 80 * len(take_frames) + 60 * len(pts),
            'prompt': (BEFORE_NOTE if before else '') + PROMPT.format(n=len(take_frames), people=', '.join(people) or 'as seen',
                                    statements='\n'.join(f'{i + 1}. {s}' for i, s in enumerate(pts)))}

def film_points(people, place=None, has_before=False, join=None, hidden=()):
    """V0.9.36 🎬: does this take make sense in the film? Who is on screen, where it is, and whether it carries on from
    the shot before (judged by Qwen; a hint for the in-order run, never an approval)."""
    out = []
    if people:
        who = ' and '.join(people)
        out.append(f"{who} {'is the only person' if len(people) == 1 else 'are the only people'} in the video; nobody else appears")
    for h in hidden or ():                # 🙈 V0.9.48 their face is never shown: look for the body, never for the face
        out.append(f"{h} is seen only from behind or without the face (the face never shows)")
    if place: out.append(f"It takes place in {place}")
    if has_before and join == 'cut':     # V0.9.44 ✂ a cut: new positions are fine, the scene and the sides of the screen are not
        out.append('It is the same scene as the PREVIOUS SHOT picture: the same place and light, with everyone on the same side of the screen')
    elif has_before and join == 'skip':  # ⏩ time has passed: people may be anywhere, the light may have changed
        out.append('It takes place in the same place as the PREVIOUS SHOT picture')
    elif has_before: out.append('It carries straight on from the PREVIOUS SHOT picture: the same place and light, with the people where that picture left them')
    return out

def makes_sense(check):
    """False when Qwen said 'no' to one of the 🎬 film points (an extra person, the wrong place, a jump from the shot before)."""
    if not check or not check.get('points'): return None
    film = [p for p in check['points'] if p.get('sense')]
    if not film: return None
    return not any(p['seen'] == 'no' for p in film)

def read_reply(reply, pts, times=None, need_frame=None):
    """Qwen's reply -> [{'point', 'seen', 'at', 'evidence'}] (one per point; anything missing or malformed is 'unclear').
    V0.9.55 stricter: a "yes" for one of your actions (the first need_frame points; all of them when None) counts only
    when Qwen names a frame of this video (its time, one of `times` when given) and says what it sees there; otherwise
    it becomes 'unclear' ("no frame shown"). Qwen's answers are still only a hint, never ground truth."""
    import studio_rewrite_v0913 as rw
    try: j = rw._json_in(reply)
    except ValueError: j = {}
    tl = sorted(float(x) for x in times or [] if isinstance(x, (int, float)))
    tol = max(0.25, min((b - a for a, b in zip(tl, tl[1:])), default=1.0) / 2)
    def frame(at):
        if not tl: return at
        best = min(tl, key=lambda x: abs(x - at))
        return round(best, 1) if abs(best - at) <= tol else None
    nf = len(pts) if need_frame is None else int(need_frame)
    ans = {}
    for a in (j.get('answers') if isinstance(j, dict) else None) or []:
        try: k = int(a.get('n'))
        except (TypeError, ValueError, AttributeError): continue
        seen = str(a.get('seen', '')).strip().lower()
        seen = seen if seen in SEEN else 'unclear'
        at = a.get('at')
        try: at = round(float(at), 1) if at is not None and not isinstance(at, bool) else None
        except (TypeError, ValueError): at = None
        ev = ' '.join(str(a.get('evidence') or '').split())[:160]
        if seen == 'yes':
            at = frame(at) if at is not None else None
            if k <= nf and (at is None or len(ev) < 4):
                seen, ev = 'unclear', 'no frame shown' + (f' (Qwen said: {ev})' if ev else '')
        ans[k] = (seen, at if seen == 'yes' else None, ev)
    miss = ('unclear', None, '')
    return [{'point': p, 'seen': ans.get(i + 1, miss)[0], 'at': ans.get(i + 1, miss)[1], 'evidence': ans.get(i + 1, miss)[2]}
            for i, p in enumerate(pts)]

def score(check):
    """0..1 share of the ACTION points seen (unclear counts half); None when there's nothing to go on."""
    if not check or not check.get('points'): return None
    v = {'yes': 1.0, 'unclear': 0.5, 'no': 0.0}
    pts = [p for p in check['points'] if not p.get('sense')] or check['points']
    return sum(v.get(p['seen'], 0.5) for p in pts) / len(pts)

def frames_of(video, out_dir, n=N_FRAMES, short=448, start=0.0):
    """n evenly spaced frames of the take as small JPEGs: [(seconds, path)]. start (V0.9.48 ⏱): seconds at the beginning that
    are left out (a lead-in where people only move into place; it is cut off in the movie)."""
    import cv2
    cap = cv2.VideoCapture(str(video)); total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0); fps = cap.get(cv2.CAP_PROP_FPS) or 24
    out = []
    if total < 2: cap.release(); return out
    first = min(max(0, int(round(float(start or 0) * fps))), max(0, total - 2 * n))
    for k in range(n):
        idx = min(total - 1, first + int(round((k + 0.5) * (total - first) / n)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx); ok, im = cap.read()
        if not ok: continue
        h, w = im.shape[:2]; s = short / min(h, w)
        if s < 1: im = cv2.resize(im, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        p = Path(out_dir) / f'f{k:02d}.jpg'; cv2.imwrite(str(p), im, [cv2.IMWRITE_JPEG_QUALITY, 88]); out.append((idx / fps, p))
    cap.release()
    return out

def run(jobs, *, qwen_python, timeout=1800, busy=None):
    import studio_qwenmodel_v0916 as _qm; qwen_python = _qm.python_for(((jobs or [{}])[0].get('request') or {}).get('model_id'), qwen_python)   # V0.9.55
    """jobs: [{'key', 'request'}] -> {key: reply text}. One Qwen load for the whole batch. busy(): True when ComfyUI got work
    (you pressed Generate): Qwen is stopped at once so H3 gets the GPU, and the check is simply tried again later.
    DIRECTOR_CHECK_FAKE=<file> (tests only): that file's JSON {key or '*': reply} instead of Qwen."""
    fake = os.environ.get('DIRECTOR_CHECK_FAKE')
    if fake:
        j = json.loads(Path(fake).read_text()); return {x['key']: j.get(x['key'], j.get('*', '')) for x in jobs}
    if not Path(qwen_python).is_file(): raise RuntimeError('QWEN_NOT_INSTALLED: the Qwen Python environment (h3-qwen-venv) was not found')
    with tempfile.TemporaryDirectory() as t:
        req = Path(t) / 'req.json'; out = Path(t) / 'out.json'
        req.write_text(json.dumps({'model_id': (jobs[0]['request'].get('model_id') if jobs else None) or MODEL_ID, 'jobs': jobs}))
        env = dict(os.environ)
        env.pop('LD_PRELOAD', None)             # V0.9.38: ComfyUI's tcmalloc preload segfaults Qwen's generate
        import studio_qwenmodel_v0916 as _qm                  # the cache that really has the model (Vast sets its own HF_HOME)
        env['HF_HOME'] = str(_qm.hf_home((jobs[0]['request'].get('model_id') if jobs else None) or MODEL_ID))
        env.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
        import signal, time as _t
        proc = subprocess.Popen([str(qwen_python), '-I', str(WORKER), str(req), str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env, start_new_session=True)
        t0 = _t.time()
        while proc.poll() is None:
            stop = 'busy' if busy and busy() else 'timeout' if _t.time() - t0 > timeout else None
            if stop:
                try: os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait()
                raise RuntimeError('QWEN_CHECK_STOPPED_COMFYUI_GOT_WORK: it will run again when ComfyUI is idle' if stop == 'busy' else 'QWEN_CHECK_TIMED_OUT')
            _t.sleep(2)
        so, se = proc.communicate()
        r = subprocess.CompletedProcess(proc.args, proc.returncode, so, se)
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError('QWEN_CHECK_FAILED: ' + _qm.why_failed(r.returncode, (r.stdout or '') + (r.stderr or ''), 'check'))
        return json.loads(out.read_text())['replies']
