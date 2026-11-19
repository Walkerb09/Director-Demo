"""Director Studio editing: measured cut points, take scores, colour grades, continuity at the cut, warnings."""
from __future__ import annotations
from studio_common_v094 import *
from studio_common_v094 import _SHA_CACHE, _json_ok
_HIST_CACHE = {}       # frame colour signatures read from a video when an older analysis lacks them

def side_switch(st, c1, c2):
    """Screen direction across a cut (the 180-degree rule): two people who swap sides read as a jump in space."""
    def order(c):
        t = next((t for t in st['shots'][c['shot']]['takes'] if t['take'] == c['take']), None)
        ppl = (((t or {}).get('analysis') or {}).get('identity') or {}).get('people') or {}
        xs = sorted((v['screen_x'], k) for k, v in ppl.items() if v.get('screen_x') is not None and (v.get('median') or 0) >= 0.3)
        return [k for _, k in xs]
    a, b = order(c1), order(c2)
    both = [k for k in a if k in b]
    if len(both) < 2: return None
    if [k for k in a if k in both] != [k for k in b if k in both]:
        n = lambda k: (st['characters'].get(k) or {}).get('name', k)
        return (f"SCREEN_DIRECTION: {n(both[0])} and {n(both[1])} swap sides across this cut (measured); it can read as a jump. "
                "A new take of this shot keeps them on the same sides automatically.")
    return None

def end_at_cut(a, out, ident, names, cast, sides=None):
    """MEASURED state of a take over the last half second before the movie cuts away from it (not the take's own end).
    sides (V0.9.37): who is left to right, recognised in the frames just before the cut. Before, it was each person's
    MEDIAN position over the whole take: with lively shots people cross and move, so "where they end" was really where
    they were on average, and the next shot started from that. No sides given -> none are claimed."""
    out = max(1, min(int(out), a['frames'])); lo = max(0, out - 12)
    seen = [f for f in (_faces_near(a, i) for i in range(lo, out, 3)) if f is not None]
    counts = [len(f) for f in seen]
    n = max(set(counts), key=counts.count) if counts else 0
    hs = [max(x['h'] for x in f) for f in seen if f]
    size = sorted(hs)[len(hs) // 2] if hs else None
    framing = None if size is None else ('close-up' if size > 0.22 else 'medium shot' if size > 0.09 else 'wide shot')
    loc = [v for v in ((a.get('flow') or {}).get('local') or [])[lo:out] if v is not None]
    moving = (sum(loc) / len(loc)) if loc else 0.0
    dirs = [d for d, l in zip(((a.get('flow') or {}).get('dir') or [])[lo:out], loc) if d is not None and l > 0.15]
    import math as _m
    heading = None
    if moving > 0.18 and dirs:
        cx = sum(_m.cos(_m.radians(d)) for d in dirs) / len(dirs); cy = sum(_m.sin(_m.radians(d)) for d in dirs) / len(dirs)
        if abs(cx) > 0.5 and abs(cx) > abs(cy): heading = 'towards screen right' if cx > 0 else 'towards screen left'
    order = list(sides) if (sides and n >= 2 and len(sides) >= 2) else []
    return {'frame': out, 'people': n, 'framing': framing, 'moving': round(moving, 3), 'heading': heading, 'left_to_right': order,
            'method': 'MEASURED_AT_THE_CUT'}

def take_score(a):
    """One number from the measurements (higher = cleaner take), with the reasons. Not a judgement of performance."""
    score = 1.0; why = []
    fr = a.get('frames') or 124
    segs = a.get('segments') or []
    longest = max((s['frames'][1] - s['frames'][0] for s in segs), default=fr)
    frac = longest / fr; score += 0.5 * frac
    if a.get('candidate_cuts'): why.append(f'hidden cut (usable {longest} of {fr} frames)')
    for c in a.get('checks', []):
        if c.startswith('PEOPLE_COUNT'): score -= 0.4; why.append('wrong number of people')
        elif c.startswith('BACKGROUND_DRIFT'): score -= 0.3; why.append('background drifted')
        elif c.startswith('SCREEN_SIDES'): score -= 0.2; why.append('people swapped sides')
        elif c.startswith('IDENTITY') and 'not found' in c: score -= 0.5; why.append(c.split(': ', 1)[1].rstrip('.'))
        elif c.startswith('IDENTITY') and 'matches nobody' in c: score -= 0.3; why.append('an unknown face appears')
        elif c.startswith('IDENTITY') and 'looks less like' in c: score -= 0.3; why.append(c.split(': ', 1)[1].split(' (')[0])
    meds = [v['median'] for v in ((a.get('identity') or {}).get('people') or {}).values() if v.get('median') is not None]
    if meds: score += float(np.mean(meds)); why.append('identity ' + ', '.join(f'{m:.2f}' for m in meds))
    return round(score, 3), why

AUDIO_FADE = 0.012          # 12 ms fade at every cut: no clicks, too short to hear as a fade
OVERLAP = 6                 # J/L cut length in frames (0.25 s)

def audio_overlaps(clips, analyses, k=OVERLAP):
    """J/L cuts where speech runs across a cut (measured audio level, never a story claim):
    L-cut: the outgoing line is still going at the cut -> let it finish over the new picture.
    J-cut: the incoming clip starts talking right at its first frame -> hear it a moment before we see it.
    Picture cuts never move; total audio length never changes."""
    import numpy as np
    for i in range(1, len(clips)):
        a, b, pa, pb = clips[i - 1], clips[i], analyses[i - 1], analyses[i]
        ra, rb = pa.get('audio_rms'), pb.get('audio_rms')
        if not ra or not rb: continue
        thr_a = max(0.008, 0.6 * float(np.percentile(ra, 75))); thr_b = max(0.008, 0.6 * float(np.percentile(rb, 75)))
        out, tin = a['out'], b['in']
        a_in = a.get('a_in', a['in']); b_out = b.get('a_out', b['out'])
        def lvl(r, x, y): seg = r[max(0, x):min(len(r), y)]; return float(np.mean(seg)) if seg else 0.0
        if out + k <= min(len(ra), pa.get('frames') or len(ra)) and lvl(ra, out - 4, out) > thr_a and lvl(ra, out, out + k) > thr_a \
                and b_out - (tin + k) >= 24:
            a['a_out'] = out + k; b['a_in'] = tin + k; a.setdefault('a_in', a['in'])
            b['audio'] = f'L-cut: the previous line finishes {k} frames over this picture'
        elif tin - k >= 0 and lvl(rb, tin - k, tin) > thr_b and lvl(rb, tin, tin + 4) > thr_b and (out - k) - a_in >= 24:
            a['a_out'] = out - k; b['a_in'] = tin - k; a.setdefault('a_in', a['in'])
            b['audio'] = f'J-cut: this clip is heard {k} frames before it is seen'
    for c in clips:
        if 'a_in' in c or 'a_out' in c: c.setdefault('a_in', c['in']); c.setdefault('a_out', c['out'])
    return clips

# ---------------------------------------------------------------- the look across a cut (measured, after render's own grade)
LOOK_L, LOOK_AB, LOOK_CONTRAST = 13.0, 5.0, 1.25     # 8-bit Lab: ~5 L* / ~5 a*,b* / 25% contrast = noticeable (a note)
WARN_L, WARN_AB, WARN_CONTRAST = 18.0, 7.0, 1.35     # ~7 L* / ~7 a*,b* / 35% contrast = a visible jump at a cut (a warning)

def _look_window(lf, lo, hi, grade=None):
    rows = [r for r in lf[max(0, lo):max(lo + 1, hi)] if r]
    if not rows: return None
    m = [sum(r[k] for r in rows) / len(rows) for k in range(3)]; sd = [sum(r[3 + k] for r in rows) / len(rows) for k in range(3)]
    if grade:
        m = [grade['gain'][k] * m[k] + grade['offset'][k] for k in range(3)]; sd = [grade['gain'][k] * sd[k] for k in range(3)]
    import cv2
    px = np.uint8([[[min(255, max(0, round(v))) for v in m]]])
    L, A, B = [float(x) for x in cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0]]
    return {'L': L, 'a': A, 'b': B, 'contrast': sum(sd) / 3}

def _look_words(p, n, L=LOOK_L, AB=LOOK_AB, C=LOOK_CONTRAST):
    words = []
    dL = n['L'] - p['L']
    if abs(dL) >= L: words.append(f"{abs(dL) / max(p['L'], 1) * 100:.0f}% {'brighter' if dL > 0 else 'darker'}")
    db = n['b'] - p['b']
    if abs(db) >= AB: words.append('warmer' if db > 0 else 'cooler')
    da = n['a'] - p['a']
    if abs(da) >= AB: words.append('more magenta' if da > 0 else 'greener')
    r = n['contrast'] / max(p['contrast'], 1e-3)
    if r >= C: words.append('more contrasty')
    elif r <= 1 / C: words.append('flatter')
    return words

def look_jump(lf_prev, out_prev, lf_next, in_next, grade_prev=None, grade_next=None, n=12):
    """How the next clip's first half second looks next to the previous clip's last half second, as measured and as it
    will look after render's bounded colour grade. Returns {'raw': [...], 'after': [...], 'fixed'} or None if it matches."""
    if not lf_prev or not lf_next: return None
    out_prev = min(int(out_prev), len(lf_prev)); in_next = max(0, int(in_next))
    p0, q0 = _look_window(lf_prev, out_prev - n, out_prev), _look_window(lf_next, in_next, in_next + n)
    if not p0 or not q0: return None
    raw = _look_words(p0, q0)
    if not raw: return None
    p1, q1 = _look_window(lf_prev, out_prev - n, out_prev, grade_prev), _look_window(lf_next, in_next, in_next + n, grade_next)
    after = _look_words(p1, q1, WARN_L, WARN_AB, WARN_CONTRAST) if p1 and q1 else raw
    return {'raw': raw, 'after': after, 'fixed': not after}

def look_text(j, prev_label):
    if not j: return None
    if j['fixed']: return f"LOOK: {', '.join(j['raw'])} than {prev_label} ends; small enough for the render's colour match to even out."
    return (f"LOOK_JUMP: {', '.join(j['after'])} than {prev_label} ends, even after the render's colour match. A new take, "
            "or the same place reference for both shots, usually fixes it.")

def color_grade(ref, cur, max_gain=0.15, max_off=20.0):
    """Gentle per-channel mean/std match of a clip toward the reference clip (BGR). Bounded so it can
    never produce the near-negative colours heavy histogram matching caused before. None if negligible."""
    if not ref or not cur: return None
    gain, off = [], []
    for k in range(3):
        g = ref['std_bgr'][k] / max(1e-3, cur['std_bgr'][k]); g = min(1 + max_gain, max(1 - max_gain, g))
        o = ref['mean_bgr'][k] - g * cur['mean_bgr'][k]; o = min(max_off, max(-max_off, o))
        gain.append(round(g, 4)); off.append(round(o, 2))
    if all(abs(g - 1) < 0.02 for g in gain) and all(abs(o) < 2.0 for o in off): return None
    return {'gain': gain, 'offset': off, 'method': 'BOUNDED_MEAN_STD_MATCH_TO_FIRST_CLIP'}

DRIFT_WARN = 0.55       # measured: same hallway 0.26-0.40, different place 0.68-0.88
def take_checks(st, plan, a):
    """Measured consistency checks for a new take against its plan (warnings, never approvals)."""
    out = []
    plate = next((r for r in plan['refs'] if st['refs'][r]['kind'] == 'location'), None)
    sig = st['refs'][plate].get('plate_signature') if plate else None
    hh = a.get('hsv_hist_16x8') or []
    lo, hi = clean_range(a)
    if sig and hh:
        idx = [i for i in (lo + (hi - lo) // 2, hi - 2) if 0 <= i < len(hh)]
        d = sum(media.hist_dist(hh[i], sig) for i in idx) / len(idx)
        if d > DRIFT_WARN:
            out.append(f'BACKGROUND_DRIFT: the place looks different from its reference (score {d:.2f}); consider a retake.')
    framing = plan['spec'].get('framing')
    seg = next((x for x in a['segments'] if x['frames'] == [lo, hi]), None)
    seen = seg['face_count_mode'] if seg else None
    want = len(plan['cast'])
    if seen is not None and framing not in ('close_up', 'over_shoulder', 'profile') and want and seen != want:
        out.append(f'PEOPLE_COUNT: expected {want} {"person" if want == 1 else "people"}, measured {seen} face(s).')
    return out

# ----------------------------------------------------------------------------- editing intelligence
def clean_range(a):
    """Usable frame range: if the take has hidden cuts, keep the longest clean segment."""
    segs = a['segments']
    best = max(segs, key=lambda s: s['frames'][1] - s['frames'][0])
    return best['frames'][0], best['frames'][1]

def _norm(vals):
    lo, hi = min(vals), max(vals)
    return [0.0 if hi - lo < 1e-9 else (v - lo) / (hi - lo) for v in vals]

def first_clip_cut(a, lo, hi, learned=None):
    """The movie's first clip: skip a frozen start, or start where YOU usually start first clips of this length
    (learned from your trims, `learned` = frames after the clean start)."""
    m = a['motion']
    start = lo
    # skip a frozen/settling start (H3 often holds still for a few frames)
    for i in range(lo + 1, min(lo + 13, hi - 24)):
        if m[i] > 0.25: start = max(lo, i - 1); break
    else:
        start = lo
    why_extra = ' (skips a frozen start)' if start > lo else ''
    if learned is not None and lo + int(learned) > start and lo + int(learned) < hi - 36:
        start = lo + int(learned); why_extra = f' (where you usually start shots this long: {int(learned)} frames in)'
    out = tail_cut(a, start, hi)
    return start, out, 'starts at F%d%s' % (start, why_extra)

def length_cut(a, tin, hi, seconds, fps=24):
    """End point for a requested shot length: the quietest moment (a pause) within +/-6 frames of the target."""
    target = tin + int(round(seconds * fps))
    lo_, hi_ = max(tin + 24, target - 6), min(hi, target + 6)
    if lo_ > hi_: return None, None
    rms = a.get('audio_rms') or []
    best = min(range(lo_, hi_ + 1), key=lambda i: (rms[i - 1] if 0 < i <= len(rms) else 0, abs(i - target)))
    return best, f'about {seconds:g} s as you asked (ends at a pause, F{best - 1})'

def tail_cut(a, tin, hi):
    """Keep the tail unless it measurably degrades (frozen or faces drop out). Quiet is NOT a reason."""
    m = a['motion']; out = hi
    frozen = 0
    for i in range(hi - 1, max(tin + 24, hi - 18), -1):
        if m[i] < 0.05: frozen += 1
        else: break
    if frozen >= 6: out = hi - frozen + 2
    return out

PACING_IN = {'natural': 0.0, 'snappy': 1.0, 'relaxed': -1.0}
FEATURES = ('speech', 'settle', 'match', 'pace', 'camera', 'flow', 'energy')
DEFAULT_W = {'speech': 0.30, 'settle': 0.15, 'match': 0.20, 'pace': 0.10, 'camera': 0.10, 'flow': 0.10, 'energy': 0.05}
FEATURE_WORDS = {'speech': 'cut at pauses, not mid-sound', 'settle': "skip H3's still settling start",
                 'match': 'smooth visual match across the cut', 'pace': 'how much of the start to skip',
                 'camera': 'avoid cutting during camera moves', 'flow': 'cut on action (motion continues across the cut)',
                 'energy': 'no jarring jump in motion energy'}

def _flow_at(a, idx, key):
    fl = a.get('flow') or {}
    vals = fl.get(key) or []
    return [vals[i] for i in idx if 0 <= i < len(vals)]

def raw_cut_features(pa, p_out, a, f, lo, pace_target):
    import math as _m
    rin = a['audio_rms'] or [0.0] * a['frames']; rprev = pa['audio_rms'] or [0.0] * pa['frames']
    speech = sum(rin[f:f + 3]) / 3 + sum(rprev[max(0, p_out - 3):p_out]) / 3
    settle = 1.0 if max(a['motion'][f:f + 6] or [0]) < 0.2 else 0.0
    match = _hist_dist(_frame_hist(pa, p_out - 1), _frame_hist(a, f))
    pace = abs(f - lo - pace_target) / 60
    g_in = _flow_at(a, range(f, f + 3), 'global'); g_out = _flow_at(pa, range(p_out - 3, p_out), 'global')
    camera = (sum(_m.hypot(*v) for v in g_in) / max(1, len(g_in))) + (sum(_m.hypot(*v) for v in g_out) / max(1, len(g_out)))
    l_in = _flow_at(a, range(f, f + 3), 'local'); l_out = _flow_at(pa, range(p_out - 3, p_out), 'local')
    li = sum(l_in) / max(1, len(l_in)); lo_ = sum(l_out) / max(1, len(l_out))
    d_in = [d for d in _flow_at(a, range(f, f + 3), 'dir') if d is not None]
    d_out = [d for d in _flow_at(pa, range(p_out - 3, p_out), 'dir') if d is not None]
    if li > 0.15 and lo_ > 0.15 and d_in and d_out:
        diff = _m.radians(d_in[-1] - d_out[-1]); flow = (1 - _m.cos(diff)) / 2
    else:
        flow = 0.25
    energy = abs(_m.log((li + 0.05) / (lo_ + 0.05)))
    return {'speech': speech, 'settle': settle, 'match': match, 'pace': pace, 'camera': camera, 'flow': flow, 'energy': energy}

def normalise(grid_feats):
    out = {f: dict(v) for f, v in grid_feats.items()}
    for k in FEATURES:
        if k == 'settle': continue
        col = [v[k] for v in grid_feats.values()]; lo, hi = min(col), max(col)
        for f in out: out[f][k] = 0.0 if hi - lo < 1e-9 else (grid_feats[f][k] - lo) / (hi - lo)
    return out

def choose_cut(pa, p_in, p_out, a, lo, hi, pacing='natural', lock_out=False, weights=None, pace_target=None, extra=()):
    """Entry frame for the incoming take. Seven measured costs (normalised over the candidates),
    weighted by the LEARNED taste (defaults until you correct a cut). Silence in the outgoing shot is
    never trimmed. Returns (p_out, f, why, decision)."""
    w = weights or DEFAULT_W
    target = pace_target if pace_target is not None else 18 + 18 * PACING_IN.get(pacing, 0)
    reach = max(60, int(0.4 * (hi - lo)), int(target) + 24)          # longer takes (e.g. 7 s) can start later: search further in
    grid = sorted(set(list(range(lo, min(hi - 36, lo + reach) + 1, 2)) or [lo]) | {x for x in extra if lo <= x <= hi - 12})
    raw = {f: raw_cut_features(pa, p_out, a, f, lo, target) for f in grid}
    nf = normalise(raw)
    cost = {f: sum(w[k] * nf[f][k] for k in FEATURES) for f in grid}
    order = sorted(grid, key=lambda f: (cost[f], f))
    best = order[0]; ru = order[1] if len(order) > 1 else best
    top = sorted(FEATURES, key=lambda k: -(w[k] * (1 - nf[best][k])))[:2]
    why = (f"enters at F{best}: " + ('at a pause' if nf[best]['speech'] < 0.34 else 'during sound (no quieter option)')
           + (', picture already moving' if raw[best]['settle'] == 0 else ', note: incoming picture is still settling')
           + ('' if raw[best]['camera'] < 0.6 else ', camera is moving')
           + f"; strongest reasons: {', '.join(FEATURE_WORDS[k] for k in top)}; runner-up F{ru} (margin {cost[ru] - cost[best]:.3f})")
    decision = {'grid': grid, 'chosen': best, 'features': {str(f): {k: round(v, 5) for k, v in nf[f].items()} for f in grid},
                'weights': w, 'pace_target': target, 'lo': lo}
    return p_out, best, why, decision

def learn_taste(prior, prefs, *, lr=0.15):
    """Replay human corrections: each is (features of the cut the Director chose, features of the cut
    the human chose). Perceptron-style ranking update, bounded to [0.3x, 3x] of the defaults."""
    w = dict(prior)
    for auto_f, human_f in prefs:
        d = {k: human_f[k] - auto_f[k] for k in FEATURES}
        if sum(w[k] * d[k] for k in FEATURES) >= -0.02:
            for k in FEATURES: w[k] = min(3 * DEFAULT_W[k], max(0.3 * DEFAULT_W[k], w[k] - lr * d[k]))
            tot = sum(w.values()); w = {k: v / tot for k, v in w.items()}
    return {k: round(v, 5) for k, v in w.items()}

def _frame_hist(a, i):
    import cv2
    if a.get('hsv_hist_16x8') and 0 <= i < len(a['hsv_hist_16x8']):     # stored by analyze_take: no video read
        import numpy as np
        return np.array(a['hsv_hist_16x8'][i], dtype=np.float32).reshape(16, 8)
    key = (a['sha256'], i)
    if key in _HIST_CACHE: return _HIST_CACHE[key]
    if len(_HIST_CACHE) > 4000: _HIST_CACHE.clear()
    cap = cv2.VideoCapture(a['path']); cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, i)); ok, im = cap.read(); cap.release()
    if not ok: return None
    h = cv2.calcHist([cv2.cvtColor(cv2.resize(im, (96, 168)), cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(h, h); _HIST_CACHE[key] = h; return h

def _hist_dist(h1, h2):
    import cv2
    if h1 is None or h2 is None: return 1.0
    return float(cv2.compareHist(h1, h2, cv2.HISTCMP_BHATTACHARYYA))

def _faces_near(a, i):
    keys = sorted(int(k) for k in a['faces'])
    if not keys: return None
    k = min(keys, key=lambda x: abs(x - i))
    return a['faces'][str(k)] if abs(k - i) <= 4 else None

def jump_cut(pa, p_out, a, tin):
    f1, f2 = _faces_near(pa, p_out - 1), _faces_near(a, tin)
    if not f1 or not f2 or len(f1) != len(f2): return None
    same = all(abs(x['cx'] - y['cx']) < 0.08 and abs(x['h'] - y['h']) / max(x['h'], y['h']) < 0.25 for x, y in zip(f1, f2))
    if same:
        return 'JUMP_CUT_RISK: both sides have the same framing and positions; consider a different angle for one of them.'
    if len(f1) >= 2:
        return None
    return None

def warnings_for(a, tin, tout):
    w = []
    for c in a['candidate_cuts']:
        if tin < c['frame'] < tout: w.append(f"Contains hidden cut at F{c['frame']}.")
    if a['candidate_cuts'] and not w:
        w.append(f"Take had a hidden cut at F{a['candidate_cuts'][0]['frame']}; only the clean part is used.")
    return w
