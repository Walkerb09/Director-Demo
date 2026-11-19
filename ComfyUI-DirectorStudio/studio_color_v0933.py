"""🎨 Colour for the movie render (V0.9.33): steady within a shot, and matched to YOUR originals.

The render used to give every generated clip ONE fixed per-channel correction toward the first clip of the movie. That
could not follow a take whose colour drifts while it plays, and matching to shot 1 copies shot 1's look (often too warm,
saturated or contrasty for your real footage). This does both properly, frame by frame, in Lab (L = brightness,
a = green<->magenta, b = blue<->yellow, chroma = saturation):

  steady    each frame is held to the look of the clip's own first second in the movie (a drift is pulled back while
            the take plays; the curve is smoothed over half a second, so nothing flickers).
  target    the look of YOUR originals: the end of your original video when the movie cuts from it, else your photo or
            video of the place, else the people's own photos; with none, the first clip (as before). Saturation,
            contrast and warmth/tint are matched; brightness only halfway (a photo and a take can be framed differently).

Every change is bounded (it can never wreck a shot), your own videos are never touched, and the take itself never
changes: the render reads a graded copy (cached by what went into it).
"""
from __future__ import annotations
import hashlib, json, subprocess
from pathlib import Path
import numpy as np

VERSION = 'lab-steady-target-2-bt709'        # V0.9.56 the graded copy keeps the take's colour matrix (it was read and written as BT.601)
SMOOTH = 13                      # frames (~0.5 s) of the curve's moving average
ANCHOR = 24                      # the first second of the used part is the clip's own look
# bounds (8-bit OpenCV Lab: L 0-255, a/b centred on 128)
MAX_L_SHIFT, MAX_AB_SHIFT = 22.0, 9.0
CONTRAST = (0.8, 1.25)
SATURATION = (0.93, 1.3)          # V0.9.56 never takes much colour out: H3's takes already look like your references
BRIGHT_WEIGHT = 0.5              # brightness moves only halfway toward the originals

def lab_stats(im):
    """{'L', 'sdL', 'a', 'b', 'C'} of one BGR image (C = mean chroma, the saturation)."""
    import cv2
    s = im if max(im.shape[:2]) <= 400 else cv2.resize(im, (int(im.shape[1] * 400 / max(im.shape[:2])), int(im.shape[0] * 400 / max(im.shape[:2]))))
    lab = cv2.cvtColor(s, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    a, b = lab[:, 1] - 128, lab[:, 2] - 128
    return {'L': float(lab[:, 0].mean()), 'sdL': float(lab[:, 0].std()), 'a': float(lab[:, 1].mean()), 'b': float(lab[:, 2].mean()),
            'C': float(np.sqrt(a * a + b * b).mean())}

def mean_stats(stats):
    stats = [s for s in stats if s]
    return {k: float(np.mean([s[k] for s in stats])) for k in stats[0]} if stats else None

def video_stats(path, lo=0, hi=None, step=1):
    """Per-frame lab_stats of a video ([lo, hi), every `step` frames; others None)."""
    import cv2
    cap = cv2.VideoCapture(str(path)); out = []; i = 0
    try:
        while True:
            ok, im = cap.read()
            if not ok: break
            out.append(lab_stats(im) if (i >= lo and (hi is None or i < hi) and (i - lo) % step == 0) else None)
            i += 1
    finally: cap.release()
    return out

def _smooth(vals, k=SMOOTH):
    v = np.array(vals, dtype=np.float64); n = len(v)
    if n == 0: return v
    pad = k // 2; e = np.concatenate([np.full(pad, v[0]), v, np.full(pad, v[-1])])
    return np.convolve(e, np.ones(k) / k, mode='valid')[:n]

def plan(frame_stats, lo, hi, *, target=None, steady=True):
    """Per-frame correction for frames [lo, hi) of a clip: [{'mL', 'kL', 'tL', 'kC', 'ta', 'tb'}] (index = frame - lo), or
    None when there is nothing worth doing. frame_stats: per-frame lab_stats of the whole take (None where not measured)."""
    rows = [frame_stats[i] if 0 <= i < len(frame_stats) else None for i in range(lo, hi)]
    if not any(rows): return None
    # fill gaps, then smooth each statistic over half a second
    keys = ('L', 'sdL', 'a', 'b', 'C'); last = next(r for r in rows if r)
    filled = []
    for r in rows:
        last = r or last; filled.append(last)
    cur = {k: _smooth([r[k] for r in filled]) for k in keys}
    anc = {k: float(np.median(cur[k][:ANCHOR])) for k in keys}          # the clip's own look: its first second here
    want = dict(anc)
    if target:
        want['L'] = anc['L'] + BRIGHT_WEIGHT * (target['L'] - anc['L'])
        for k in ('sdL', 'a', 'b', 'C'): want[k] = target[k]
    out, biggest = [], 0.0
    for i in range(len(rows)):
        src = {k: (float(cur[k][i]) if steady else anc[k]) for k in keys}   # steady: follow the frame; else one fixed step
        dL = max(-MAX_L_SHIFT, min(MAX_L_SHIFT, want['L'] - src['L']))
        kL = max(CONTRAST[0], min(CONTRAST[1], want['sdL'] / max(src['sdL'], 1e-3)))
        kC = max(SATURATION[0], min(SATURATION[1], want['C'] / max(src['C'], 1e-3)))
        ta = 128 + kC * (src['a'] - 128) + max(-MAX_AB_SHIFT, min(MAX_AB_SHIFT, want['a'] - (128 + kC * (src['a'] - 128))))
        tb = 128 + kC * (src['b'] - 128) + max(-MAX_AB_SHIFT, min(MAX_AB_SHIFT, want['b'] - (128 + kC * (src['b'] - 128))))
        out.append({'mL': src['L'], 'kL': kL, 'tL': src['L'] + dL, 'kC': kC, 'ma': src['a'], 'mb': src['b'], 'ta': ta, 'tb': tb})
        biggest = max(biggest, abs(dL), abs(kL - 1) * 60, abs(kC - 1) * 40, abs(ta - (128 + kC * (src['a'] - 128))) * 2,
                      abs(tb - (128 + kC * (src['b'] - 128))) * 2)
    return out if biggest >= 1.5 else None

def apply(im, p):
    """One BGR frame through one plan entry."""
    import cv2
    lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] = (lab[..., 0] - p['mL']) * p['kL'] + p['tL']
    for c, m, t in ((1, p['ma'], p['ta']), (2, p['mb'], p['tb'])):
        x = 128 + (lab[..., c] - 128) * p['kC']                        # saturation around neutral
        lab[..., c] = x + (t - (128 + (m - 128) * p['kC']))              # then the tint shift
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

def color_tags(path):
    """The take's colour matrix and range, as ffmpeg names them (ComfyUI's video saver tags BT.709, limited range).
    Untagged -> BT.709 (what browsers assume for HD video)."""
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=color_space,color_range',
                            '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
        s = (json.loads(r.stdout).get('streams') or [{}])[0]
    except Exception: s = {}
    cs = s.get('color_space') if s.get('color_space') in ('bt709', 'bt470bg', 'smpte170m', 'bt2020nc') else 'bt709'
    rng = 'pc' if s.get('color_range') in ('pc', 'jpeg') else 'tv'
    return cs, rng

MATRIX = {'bt709': 'bt709', 'bt470bg': 'bt601', 'smpte170m': 'bt601', 'bt2020nc': 'bt2020'}

def grade_file(src, dst, lo, hi, params):
    """A graded copy of the whole take (same frames, same sound): frames [lo, hi) get params; outside it the nearest
    entry (a trim or J/L-cut may still show them). Written via a .partial file.
    V0.9.56: frames are read and written with the take's OWN colour matrix and the copy is tagged like the take, so an
    unchanged frame looks exactly like the take in any player (it used to be read and written as BT.601 and left untagged:
    players showed it greyer and darker than the take)."""
    import cv2
    cap = cv2.VideoCapture(str(src)); W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); cap.release()
    cs, rng = color_tags(src); mx = MATRIX[cs]
    dst = Path(dst); dst.parent.mkdir(parents=True, exist_ok=True); tmp = dst.with_name(dst.stem + '.partial.mp4')
    dec = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(src), '-map', '0:v:0', '-vf',
                            f'scale=in_color_matrix={mx}:in_range={rng}:flags=accurate_rnd+full_chroma_int,format=bgr24',
                            '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', '24', '-i', '-',
           '-i', str(src), '-map', '0:v', '-map', '1:a?', '-vf', f'scale=out_color_matrix={mx}:out_range={rng}:flags=accurate_rnd,format=yuv420p',
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '12', '-pix_fmt', 'yuv420p',
           '-colorspace', cs, '-color_range', rng, '-color_primaries', 'bt709' if cs == 'bt709' else ('bt2020' if cs == 'bt2020nc' else 'smpte170m'),
           '-color_trc', 'bt709' if cs in ('bt709', 'smpte170m', 'bt470bg') else 'bt2020-10',
           '-c:a', 'copy', '-map_metadata', '-1', str(tmp)]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    i, n = 0, W * H * 3
    try:
        while True:
            buf = dec.stdout.read(n)
            if len(buf) < n: break
            im = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            p = params[min(max(i - lo, 0), len(params) - 1)]
            pr.stdin.write(apply(im, p).tobytes()); i += 1
        pr.stdin.close(); err = pr.stderr.read(); pr.wait(timeout=600)
    finally:
        dec.stdout.close(); dec.wait(timeout=60)
    if pr.returncode != 0 or i == 0: tmp.unlink(missing_ok=True); raise RuntimeError('COLOUR_GRADE_FAILED: ' + err.decode(errors='replace')[-300:])
    tmp.replace(dst)
    return dst

def key(*parts):
    return hashlib.sha256(json.dumps([VERSION, *parts], sort_keys=True, default=str).encode()).hexdigest()[:16]

def look_after(stats_window, target, steady=True):
    """What a window of a clip looks like after the plan (for the cut check): its Lab mean moved like plan() moves it."""
    if not stats_window: return None
    p = plan(stats_window, 0, len(stats_window), target=target, steady=steady)
    s = mean_stats(stats_window)
    if not p: return s
    q = p[len(p) // 2]
    return {'L': q['tL'], 'sdL': s['sdL'] * q['kL'], 'a': q['ta'], 'b': q['tb'], 'C': s['C'] * q['kC']}
