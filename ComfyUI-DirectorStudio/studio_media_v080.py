#!/usr/bin/env python3
"""Director Studio V0.8 — measured media analysis (no VLM, nothing hallucinated).

Everything here is a MEASUREMENT with a stated method, never a claim about meaning:
  * faces per frame (Ultra-Light RFB-640 ONNX detector, MIT, run through cv2.dnn)
  * candidate hard cuts (colour-histogram jump + face-layout jump, both required
    to be unusual relative to the clip's own frame-to-frame baseline)
  * motion energy (whole frame and per face region)
  * audio RMS envelope per frame (speech/quiet proxy, not speech recognition)
  * reference extraction from a person video the user supplies: sharp, well-lit,
    varied face / upper-body / full-body stills and a <=5 s 24 fps clip.
"""
from __future__ import annotations
import hashlib, json, math, subprocess
from pathlib import Path
import numpy as np
import cv2
from studio_netlock_v092 import NET_LOCK
SKIP_UPRIGHT = False            # set by the upload helper when the identity model crashed on this machine

HERE = Path(__file__).resolve().parent
FACE_MODEL = HERE / 'models' / 'version-RFB-640.onnx'
SEG_MODEL = HERE / 'models' / 'u2netp.onnx'          # U^2-Net-P (Apache-2.0), salient-object mask
_NET = None
_SEG = None
NEUTRAL = (128, 128, 128)
MIN_SIDE = 96          # hard floor; below 256 px a warning is given (your real grandpa face crop is 134x137 and worked)

def load_image(path):
    """Read a photo the way a person sees it: EXIF rotation applied, transparency flattened onto
    neutral grey, greyscale/CMYK converted. Returns BGR uint8."""
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ('RGBA', 'LA', 'P'):
                im = im.convert('RGBA'); bg = Image.new('RGBA', im.size, NEUTRAL + (255,)); bg.alpha_composite(im); im = bg
            im = im.convert('RGB')
            arr = np.array(im)[:, :, ::-1].copy()
    except Exception:
        arr = _ffmpeg_still(path)      # HEIC/HEIF/AVIF etc. via ffmpeg
        if arr is None: raise ValueError('UNREADABLE_IMAGE')
    if min(arr.shape[:2]) < MIN_SIDE: raise ValueError(f'IMAGE_TOO_SMALL_{arr.shape[1]}x{arr.shape[0]}_NEED_{MIN_SIDE}PX')
    return arr

def _ffmpeg_still(path):
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-frames:v', '1', f'{t}/x.png'], capture_output=True, timeout=60)
        return cv2.imread(f'{t}/x.png') if r.returncode == 0 else None

def sample_frames(path, n=48, max_side=1600):
    """Evenly spaced frames, auto-rotated and downscaled by ffmpeg (fast for 4K / long phone videos).
    Returns list of (time_seconds, BGR frame)."""
    import tempfile
    meta = probe(path)
    dur = meta.get('duration') or 0
    if not meta.get('video') or dur <= 0: raise ValueError('NOT_A_VIDEO')
    n = max(1, min(n, int(dur * (meta.get('fps') or 24))))
    with tempfile.TemporaryDirectory() as t:
        vf = (f"fps={n / dur:.6f},scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'")
        r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-vf', vf, '-frames:v', str(n),
                            '-vsync', 'vfr', f'{t}/f%04d.png'], capture_output=True, timeout=600)
        if r.returncode != 0: raise ValueError('VIDEO_DECODE_FAILED ' + r.stderr.decode(errors='replace')[-200:])
        files = sorted(Path(t).glob('f*.png'))
        out = [(i * dur / max(1, len(files)), cv2.imread(str(f))) for i, f in enumerate(files)]
    return [(ts, im) for ts, im in out if im is not None]

def small_hist(im):
    """Same colour signature analyze_take stores per frame (96x168 HSV 16x8)."""
    h = cv2.calcHist([cv2.cvtColor(cv2.resize(im, (96, 168)), cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(h, h); return h

def hist_dist(a, b):
    a = np.asarray(a, dtype=np.float32).reshape(16, 8); b = np.asarray(b, dtype=np.float32).reshape(16, 8)
    return float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA))

def _seg():
    global _SEG
    with NET_LOCK:
      if _SEG is None:
        if not SEG_MODEL.is_file(): raise FileNotFoundError('SEGMENTATION_MODEL_MISSING')
        _SEG = cv2.dnn.readNetFromONNX(str(SEG_MODEL))
      return _SEG

def person_mask(im, face=None):
    """Soft 0..1 mask of the main subject. If a face is given, keep only the region connected to it."""
    x = cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2RGB), (320, 320)).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    with NET_LOCK:
        net = _seg(); net.setInput(x.transpose(2, 0, 1)[None].astype(np.float32))
        m = net.forward()[0, 0].copy()
    m = (m - m.min()) / (m.max() - m.min() + 1e-8)
    m = cv2.resize(m, (im.shape[1], im.shape[0]), interpolation=cv2.INTER_LINEAR)
    if face is not None:
        n, lab = cv2.connectedComponents((m > 0.5).astype(np.uint8))
        x0, y0, w0, h0 = face['box']; cx, cy = min(im.shape[1] - 1, x0 + w0 // 2), min(im.shape[0] - 1, y0 + h0 // 2)
        keep = lab[cy, cx]
        if keep == 0: return None
        hard = (lab == keep).astype(np.float32)
        m = np.minimum(m, cv2.dilate(hard, np.ones((15, 15), np.uint8)))
    return cv2.GaussianBlur(m, (5, 5), 0)

FRAMING_WORDS = {'wide': 'wide shot (full body and the room around)', 'medium': 'medium shot (about waist up)',
                 'close_up': 'close-up (the face fills much of the frame)', 'two_shot': 'two-shot (two people, about waist up)'}
def shot_framing(im):
    """V0.9.47 📐 how a still is framed, MEASURED (faces and the person mask, never a model's opinion):
    -> {'framing': wide | medium | close_up | two_shot, 'people', 'face', 'heads', 'words'}.
    heads = how many face-heights of body show below the face's centre: ~7-8 full body (wide), ~2-4 waist or chest up
    (medium). A face over ~20% of the frame height is a close-up. A head cut off at the top of the frame means a close
    framing (the face found there is only part of it). Nobody in it = the room = wide."""
    H = im.shape[0]
    fs = faces(im); n = len(fs)
    if n == 0:
        pm = person_mask(im)
        if pm is not None and float((pm > 0.5).mean()) > 0.1:      # someone is there, face out of view: a close framing of the body
            return {'framing': 'medium', 'people': 1, 'face': 0.0, 'heads': None, 'words': 'a person with the face out of the frame, counted as a medium shot'}
        return {'framing': 'wide', 'people': 0, 'face': 0.0, 'heads': None, 'words': 'nobody in it: the room, a wide shot'}
    f0 = max(fs, key=lambda f: f['h']); face = float(f0['h'])
    m = person_mask(im)
    rows = np.flatnonzero((m > 0.5).any(1)) if m is not None else np.array([])
    bottom = int(rows[-1]) if len(rows) else H - 1
    heads = round((bottom - f0['cy'] * H) / max(1.0, face * H), 1)
    head_cut = (f0['cy'] - f0['h'] / 2) * H <= 2
    top = int(rows[0]) if len(rows) else 0
    floor_below = bottom <= 0.94 * H                  # V0.9.48: room shows under the feet: the whole body is in (sitting too)
    small = (bottom - top + 1) / H < 0.5              # the person takes up less than half the frame's height
    if face >= 0.2: fr = 'close_up'
    elif head_cut: fr = 'medium'
    elif heads >= 5.5 or floor_below or small: fr = 'wide'
    elif n >= 2: fr = 'two_shot'
    elif heads >= 1.6: fr = 'medium'
    else: fr = 'close_up'
    who = f"{n} person" if n == 1 else f"{n} people"
    return {'framing': fr, 'people': n, 'face': round(face, 3), 'heads': heads, 'words': f"{FRAMING_WORDS[fr]}, {who}"}

def fill_people(im, mask):
    """V0.9.47 🔒: the room with the people taken out, for a view-of-the-room reference. Each row of the masked area is filled
    from the room just left and right of it (a standing person leaves walls and floor at the same height on both sides), then
    lightly smoothed and given the surrounding grain. Plain image processing on your original; nothing is generated."""
    out = im.astype(np.float32).copy(); h, w = mask.shape
    for y in range(h):
        xs = np.flatnonzero(mask[y])
        if not len(xs): continue
        cut = np.diff(xs) > 1
        for a, b in zip(np.r_[xs[0], xs[1:][cut]], np.r_[xs[:-1][cut], xs[-1]]):
            L = out[y, max(0, a - 6):a].mean(0) if a > 0 else None
            R = out[y, b + 1:min(w, b + 7)].mean(0) if b + 1 < w else None
            if L is None and R is None: continue
            L = R if L is None else L; R = L if R is None else R
            t = np.linspace(0, 1, b - a + 1)[:, None]
            out[y, a:b + 1] = L * (1 - t) + R * t
    out = out.astype(np.uint8)
    sm = cv2.GaussianBlur(out, (1, 9), 0); out[mask > 0] = sm[mask > 0]
    ring = cv2.dilate(mask, np.ones((25, 25), np.uint8)) - mask
    if ring.any():
        hp = im.astype(np.float32) - cv2.GaussianBlur(im, (0, 0), 3).astype(np.float32)
        noise = np.random.default_rng(0).normal(0, min(6.0, max(1.5, float(np.abs(hp[ring > 0]).mean()))), out.shape)
        o = out.astype(np.float32); o[mask > 0] += noise[mask > 0]; out = np.clip(o, 0, 255).astype(np.uint8)
    return out

def isolate(im, face=None):
    """Subject on neutral grey. Returns (clean_image, info) or (None, reason)."""
    m = person_mask(im, face)
    if m is None: return None, 'FACE_NOT_INSIDE_SUBJECT_MASK'
    frac = float((m > 0.5).mean())
    if not 0.03 <= frac <= 0.92: return None, f'SUBJECT_MASK_IMPLAUSIBLE_{frac:.2f}'
    a = m[..., None]
    clean = (im.astype(np.float32) * a + np.array(NEUTRAL, np.float32) * (1 - a)).astype(np.uint8)
    rows = np.flatnonzero((m > 0.5).any(1))
    return clean, {'subject_fraction': round(frac, 3), 'background_signature': background_signature(im, m),
                   'box': body_box(m), 'feet_in': bool(len(rows)) and int(rows[-1]) < 0.99 * im.shape[0] - 1}   # V0.9.49

def body_box(mask, pad_y=0.05, pad_x=0.2, min_ratio=0.45):
    """V0.9.49 the person's own box (head to toe) in a mask, padded: x0, y0, x1, y1 or None."""
    ys, xs = np.nonzero(mask > 0.5)
    if not len(ys): return None
    H, W = mask.shape[:2]
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    ph, pw = y1 - y0 + 1, x1 - x0 + 1
    y0 = max(0, int(y0 - pad_y * ph)); y1 = min(H, int(y1 + pad_y * ph) + 1)
    w = max(pw * (1 + 2 * pad_x), min_ratio * (y1 - y0)); cx = (x0 + x1) / 2
    x0 = max(0, int(cx - w / 2)); x1 = min(W, int(cx + w / 2) + 1)
    return x0, y0, x1, y1

def body_crop(clean, info):
    """V0.9.49 a cut-out cropped to the person, head to toe, so the body fills the reference (in a wide photo the legs
    were a few pixels across inside a big grey frame and H3 had little to go on). Kept whole when the person already fills it."""
    b = (info or {}).get('box')
    if not b: return clean
    x0, y0, x1, y1 = b; H, W = clean.shape[:2]
    if (x1 - x0) * (y1 - y0) > 0.8 * W * H: return clean
    return clean[y0:y1, x0:x1]

def background_signature(im, mask=None):
    """HSV signature of the BACKGROUND only (subject masked out), comparable with small_hist."""
    small = cv2.resize(im, (96, 168)); hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mk = None
    if mask is not None:
        mk = (cv2.resize(mask, (96, 168)) < 0.2).astype(np.uint8)
        if mk.sum() < 200: mk = None
    h = cv2.calcHist([hsv], [0, 1], mk, [16, 8], [0, 180, 0, 256]); cv2.normalize(h, h)
    return [round(float(v), 5) for v in h.flatten()]

def quality(im, face):
    """Measured quality of a person reference: problems listed in plain words."""
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    x, y, w, h = face['box']
    fg = g[max(0, y):y + h, max(0, x):x + w]
    if fg.size == 0: return {'ok': False, 'problems': ['the face is cut off at the edge of the photo'], 'score': 0}
    fg128 = cv2.resize(fg, (int(128 * fg.shape[1] / max(1, fg.shape[0])) or 1, 128))
    sharp = sharpness(fg128); bright = float(fg.mean()); contrast = float(fg.std())
    probs = []
    if h < 64: probs.append(f'face is small ({h}px tall); a closer or higher-resolution photo gives better identity')
    if min(im.shape[:2]) < 256: probs.append(f'image is small ({im.shape[1]}x{im.shape[0]}px)')
    if sharp < 25 and h >= 64: probs.append('face is blurry')      # blur is not measurable on tiny faces
    if bright < 45: probs.append('face is too dark')
    if bright > 220: probs.append('face is overexposed')
    if contrast < 12: probs.append('face is flat / washed out')
    score = math.log1p(sharp) + min(h, 400) / 100 - abs(bright - 130) / 60
    return {'ok': not any(p.startswith(('face is blurry', 'face is too dark', 'face is overexposed')) for p in probs) and h >= 16,
            'problems': probs, 'sharpness': round(sharp, 1), 'brightness': round(bright, 1), 'face_px': int(h), 'score': round(score, 3)}

def look_frames(frames):
    """Per frame: mean B, G, R and their spread (std) -- the 'look' (brightness, warmth, tint, contrast) at any moment."""
    out = []
    for f in frames:
        px = cv2.resize(f, (48, 84)).reshape(-1, 3).astype(np.float32)
        out.append([round(float(v), 1) for v in px.mean(0)] + [round(float(v), 1) for v in px.std(0)])
    return out

def look_frames_of(path):
    meta = probe(path)
    return look_frames(read_frames(path, size=(96, 168) if meta.get('height', 0) >= meta.get('width', 1) else (168, 96)))

def rgb_stats(frames):
    px = np.concatenate([cv2.resize(f, (96, 168)).reshape(-1, 3).astype(np.float32) for f in frames])
    return {'mean_bgr': [round(float(v), 2) for v in px.mean(0)], 'std_bgr': [round(float(v), 2) for v in px.std(0)]}

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'stream=codec_type,width,height,r_frame_rate,nb_frames,duration:format=duration:format_tags',
                        '-of', 'json', str(path)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0: raise ValueError('UNREADABLE_MEDIA')
    j = json.loads(r.stdout)
    v = [s for s in j.get('streams', []) if s['codec_type'] == 'video']
    a = [s for s in j.get('streams', []) if s['codec_type'] == 'audio']
    out = {'video': bool(v), 'audio': bool(a), 'tags': j.get('format', {}).get('tags', {}) or {},
           'duration': float(j.get('format', {}).get('duration') or 0)}
    if v:
        num, den = (v[0].get('r_frame_rate') or '0/1').split('/')
        out.update(width=int(v[0]['width']), height=int(v[0]['height']),
                   fps=float(num) / float(den or 1), frames=int(v[0].get('nb_frames') or 0))
    return out

def read_frames(path, every=1, max_frames=None, size=None):
    cap = cv2.VideoCapture(str(path)); out = []; i = 0
    try:
        while True:
            ok, im = cap.read()
            if not ok: break
            if i % every == 0:
                out.append(cv2.resize(im, size) if size else im)
                if max_frames and len(out) >= max_frames: break
            i += 1
    finally: cap.release()
    return out

# ----------------------------------------------------------------------------- faces
def _net():
    global _NET
    if _NET is None:
        if not FACE_MODEL.is_file(): raise FileNotFoundError('FACE_MODEL_MISSING')
        _NET = cv2.dnn.readNetFromONNX(str(FACE_MODEL))
    return _NET

def faces(im, thr=0.75):
    """List of faces {cx, cy, w, h (fractions of frame), score}, left to right.
    Portrait frames are scanned as overlapping 4:3 windows, then merged."""
    scale = 1.0
    if max(im.shape[:2]) > 1600:
        scale = 1600 / max(im.shape[:2]); im = cv2.resize(im, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if scale != 1.0:
        out = faces(im, thr)
        for f in out: f['box'] = [int(v / scale) for v in f['box']]
        return out
    h, w = im.shape[:2]
    win = min(h, int(w * 0.75)) if h >= w else h
    ww = w if h >= w else int(h * 4 / 3)
    boxes = []
    ys = [0] if h <= win else list(np.linspace(0, h - win, max(2, math.ceil(h / win) * 2 - 1)).astype(int))
    xs = [0] if w <= ww else list(np.linspace(0, w - ww, max(2, math.ceil(w / ww) * 2 - 1)).astype(int))
    net = _net()
    for y0 in ys:
        for x0 in xs:
            crop = im[y0:y0 + win, x0:x0 + ww]; chh, cww = crop.shape[:2]     # a window can be smaller than asked at the edge
            blob = cv2.dnn.blobFromImage(cv2.resize(crop, (640, 480)), 1 / 128.0, (640, 480), (127, 127, 127), swapRB=True)
            with NET_LOCK:
                net.setInput(blob); s, b = net.forward(['scores', 'boxes']); s, b = s.copy(), b.copy()
            s = s[0, :, 1]; b = b[0]; k = s > thr
            for (x1, y1, x2, y2), sc in zip(b[k], s[k]):
                bx, by = x0 + max(0.0, x1) * cww, y0 + max(0.0, y1) * chh
                bw, bh = min(x2, 1.0) * cww + x0 - bx, min(y2, 1.0) * chh + y0 - by
                if bw > 4 and bh > 4: boxes.append([bx, by, bw, bh, float(sc)])
    # merge: same face found in overlapping windows (centre inside the other box)
    boxes.sort(key=lambda x: -x[4]); keep = []
    for bx in boxes:
        cx, cy = bx[0] + bx[2] / 2, bx[1] + bx[3] / 2
        if any(k[0] - k[2] * 0.25 <= cx <= k[0] + k[2] * 1.25 and k[1] - k[3] * 0.25 <= cy <= k[1] + k[3] * 1.25 for k in keep):
            continue
        keep.append(bx)
    return sorted(({'cx': round(float((b[0] + b[2] / 2) / w), 4), 'cy': round(float((b[1] + b[3] / 2) / h), 4),
                    'w': round(float(b[2] / w), 4), 'h': round(float(b[3] / h), 4), 'score': round(float(b[4]), 3),
                    'box': [int(b[0]), int(b[1]), int(b[2]), int(b[3])]} for b in keep), key=lambda f: f['cx'])

def sharpness(gray):
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

# ----------------------------------------------------------------------------- audio
def audio_rms(path, fps=24.0, n_frames=None, sr=16000):
    """Per-video-frame RMS of decoded mono audio (proxy for speech/quiet)."""
    r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-vn', '-ac', '1', '-ar', str(sr),
                        '-f', 's16le', '-'], capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout: return None
    pcm = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    hop = sr / fps
    n = n_frames or int(len(pcm) / hop)
    out = []
    for i in range(n):
        a, b = int(i * hop), int((i + 1) * hop)
        seg = pcm[a:b]
        out.append(float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0)
    return out

# ----------------------------------------------------------------------------- shot analysis
def analyze_take(path, *, face_every=3, faces_on=True, flow_on=True):
    """Measured profile of one generated take. Pure measurement; no story claims.
    faces_on/flow_on=False: a reduced measurement (no face finder / optical flow) for machines where those crash."""
    meta = probe(path)
    small = read_frames(path, size=(96, 168) if meta.get('height', 0) >= meta.get('width', 1) else (168, 96))
    n = len(small)
    if n < 2: raise ValueError('TOO_FEW_FRAMES')
    if max(float(f.max()) for f in small) < 12: raise ValueError('BLACK_TAKE')     # every frame black: H3 sent back no picture
    hsv = [cv2.calcHist([cv2.cvtColor(f, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256]) for f in small]
    for hh in hsv: cv2.normalize(hh, hh)
    hist_d = [0.0] + [float(cv2.compareHist(hsv[i - 1], hsv[i], cv2.HISTCMP_BHATTACHARYYA)) for i in range(1, n)]
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in small]
    motion = [0.0] + [float(np.mean(np.abs(gray[i] - gray[i - 1]))) for i in range(1, n)]
    # faces on every `face_every` frame at full resolution + densely around histogram spikes
    full = read_frames(path)
    med = float(np.median(hist_d[1:])); mad = float(np.median(np.abs(np.array(hist_d[1:]) - med))) + 1e-6
    spikes = [i for i in range(1, n) if hist_d[i] > med + 8 * mad and hist_d[i] > 0.08]
    face_idx = sorted(set(range(0, n, face_every)) | {n - 1} | {j for s in spikes for j in (s - 1, s)})
    fx = {i: faces(full[i]) for i in face_idx if 0 <= i < n} if faces_on else {}
    def layout_jump(a, b):
        fa, fb = fx.get(a), fx.get(b)
        if fa is None or fb is None: return False
        if len(fa) != len(fb): return True
        return any(abs(p['cx'] - q['cx']) > 0.12 or abs(p['h'] - q['h']) / max(p['h'], q['h']) > 0.45 for p, q in zip(fa, fb))
    cuts = []
    for s in spikes:
        jump = layout_jump(s - 1, s)
        strength = round((hist_d[s] - med) / mad, 1)
        if jump or hist_d[s] > 0.35:
            cuts.append({'frame': s, 'histogram_jump': round(hist_d[s], 4), 'robust_z': strength,
                         'face_layout_changed': jump, 'method': 'HSV_BHATTACHARYYA_SPIKE_PLUS_FACE_LAYOUT'})
    rms = audio_rms(path, meta.get('fps') or 24.0, n) if meta.get('audio') else None
    flow = optical_flow(path, n) if flow_on else {'global': [[0.0, 0.0]] * n, 'local': [0.0] * n, 'dir': [None] * n, 'zoom': [0.0] * n, 'method': 'NOT_MEASURED'}
    segs = []; start = 0
    for c in [c['frame'] for c in cuts] + [n]:
        segs.append([start, c]); start = c
    def seg_faces(a, b):
        cs = [len(fx[i]) for i in fx if a <= i < b]
        return max(set(cs), key=cs.count) if cs else None
    tail = list(range(max(0, n - 22), n))
    tail_faces = [fx[i] for i in tail if i in fx]
    end_state = {
        'frames': [tail[0], tail[-1]], 'method': 'MEASURED_LAST_22_FRAMES',
        'face_count_mode': (max(set(len(f) for f in tail_faces), key=[len(f) for f in tail_faces].count) if tail_faces else None),
        'face_positions_left_to_right': [round(float(np.mean([f[k]['cx'] for f in tail_faces if len(f) > k])), 3)
                                         for k in range(max((len(f) for f in tail_faces), default=0))],
        'motion_mean': round(float(np.mean([motion[i] for i in tail])), 3),
        'audio_rms_mean': round(float(np.mean([rms[i] for i in tail])), 5) if rms else None,
        'contains_cut': any(tail[0] <= c['frame'] <= tail[-1] for c in cuts)}
    return {'path': str(path), 'sha256': sha256(path), 'frames': n, 'fps': meta.get('fps'),
            'width': meta.get('width'), 'height': meta.get('height'), 'audio': meta.get('audio'),
            'candidate_cuts': cuts, 'segments': [{'frames': s, 'face_count_mode': seg_faces(*s)} for s in segs],
            'faces': {str(k): [{x: f[x] for x in ('cx', 'cy', 'w', 'h', 'score')} for f in v] for k, v in sorted(fx.items())},
            'motion': [round(m, 3) for m in motion], 'histogram_delta': [round(d, 4) for d in hist_d],
            'hsv_hist_16x8': [[round(float(x), 5) for x in hh.flatten()] for hh in hsv],
            'rgb_stats': rgb_stats(small[::6]), 'look_frames': look_frames(small),
            'flow': flow,
            'audio_rms': [round(r, 5) for r in rms] if rms else None, 'end_state': end_state,
            'authority': 'MEASUREMENT_ONLY_NOT_STORY_TRUTH'}

# ----------------------------------------------------------------------------- 🏃 how much the people move (V0.9.27)
MOVE_STILL = 0.12          # local flow (px/frame at 144x252, camera motion taken out) below this: nobody visibly moving
MOVE_BANDS = (0.15, 0.40)  # mean local flow: below = low, between = medium, above = high. Rough: set from the bundled takes
                           # (two H3 takes 0.08 / 0.10 with people still 66-80 % of the time, a lively clip 0.54);
                           # it compares takes with each other (A/B), it does not grade acting
def movement_score(flow, *, fps=24.0, skip=12):
    """{'level', 'mean', 'still_share', 'longest_still_s', 'camera'} from analyze_take's flow, or None if not measured.
    The first `skip` frames are left out (H3 often starts still; the movie cut skips them anyway)."""
    if not flow or flow.get('method') == 'NOT_MEASURED': return None
    L = [float(x) for x in (flow.get('local') or [])[1:]][skip:]
    if len(L) < 12: return None
    run = best = 0
    for x in L:
        run = run + 1 if x < MOVE_STILL else 0; best = max(best, run)
    mean = sum(L) / len(L)
    G = [math.hypot(*g) for g in (flow.get('global') or [])[1:]][skip:]
    return {'level': 'low' if mean < MOVE_BANDS[0] else 'medium' if mean < MOVE_BANDS[1] else 'high', 'mean': round(mean, 3),
            'still_share': round(sum(x < MOVE_STILL for x in L) / len(L), 2), 'longest_still_s': round(best / fps, 1),
            'camera': round(sum(G) / len(G), 3) if G else None}

# ----------------------------------------------------------------------------- optical flow
def optical_flow(path, n=None, size=(144, 252)):
    """Dense Farneback flow between consecutive frames (pixels per frame at 144x252).
    global = median flow vector (camera pan/tilt/shake proxy); local = mean |flow - global|
    (people/objects moving); dir = angle (deg) of the mean LOCAL motion; zoom = mean radial component
    (push-in/pull-out proxy). Measurement only."""
    frames = read_frames(path, size=size)
    if n: frames = frames[:n]
    g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    h, w = g[0].shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rx, ry = xx - w / 2, yy - h / 2; rn = np.sqrt(rx ** 2 + ry ** 2) + 1e-6
    out = {'global': [[0.0, 0.0]], 'local': [0.0], 'dir': [None], 'zoom': [0.0], 'size': list(size),
           'method': 'FARNEBACK_DENSE_FLOW'}
    for i in range(1, len(g)):
        f = cv2.calcOpticalFlowFarneback(g[i - 1], g[i], None, 0.5, 3, 15, 3, 5, 1.2, 0)
        gx, gy = float(np.median(f[..., 0])), float(np.median(f[..., 1]))
        lx, ly = f[..., 0] - gx, f[..., 1] - gy
        mag = np.sqrt(lx ** 2 + ly ** 2)
        local = float(mag.mean())
        strong = mag > max(0.3, float(np.percentile(mag, 90)))
        d = None
        if strong.sum() > 30:
            d = round(float(np.degrees(np.arctan2(ly[strong].mean(), lx[strong].mean()))), 1)
        zoom = float(((f[..., 0] * rx + f[..., 1] * ry) / rn).mean())
        out['global'].append([round(gx, 3), round(gy, 3)]); out['local'].append(round(local, 4))
        out['dir'].append(d); out['zoom'].append(round(zoom, 4))
    return out

# ----------------------------------------------------------------------------- references
def _crop(im, box, pad_x, pad_top, pad_bottom):
    h, w = im.shape[:2]; x, y, bw, bh = box
    x0 = max(0, int(x - bw * pad_x)); x1 = min(w, int(x + bw * (1 + pad_x)))
    y0 = max(0, int(y - bh * pad_top)); y1 = min(h, int(y + bh * (1 + pad_bottom)))
    return im[y0:y1, x0:x1]

def full_frame(ranked, look=8):
    """V0.9.49 the video frame for the full-body reference: the person whole, head to toe with the feet in the frame, and as
    tall as possible. (It used to be just the frame with the smallest face, never checked for feet.) Measured with the person
    mask; with no frame showing the feet, the old pick."""
    pool = sorted(ranked[:max(3, len(ranked) // 2)], key=lambda c: c['size'])[:look]
    best, best_h = None, 0
    for c in pool:
        try: mk = person_mask(c['im'], c['face'])
        except Exception: mk = None
        if mk is None: continue
        rows = np.flatnonzero((mk > 0.5).any(1)); H = mk.shape[0]
        if not len(rows) or rows[-1] >= 0.99 * H - 1: continue             # feet out of the frame
        h = (rows[-1] - rows[0] + 1) / H
        if h > best_h: best, best_h = c, h
    return best or min(ranked[:max(3, len(ranked) // 3)], key=lambda c: c['size'])

def _save(path, im):
    if not cv2.imwrite(str(path), im): raise ValueError('WRITE_FAILED')
    return sha256(path)

WHO_CHOICES = ('left', 'middle', 'right', 'only')   # 'only' = you said it's just one person: the clearest face, the rest ignored

SURE_FACE = 0.92        # the fast finder is this sure: counted even if YuNet misses it (YuNet misses some side-on faces)

def people_faces(im, fs=None):
    """The faces that are really separate PEOPLE (V0.9.26). The fast finder (RFB) sometimes calls a hand, a pattern or a
    face on a T-shirt / phone / picture a second face, and then a one-person photo "shows 2 people". With 2+ faces:
      - an extra face counts only if YuNet (the identity finder, more careful) finds it too, or the fast finder is very sure;
      - a face under half the size on the main person's own body (below their chin: a print, a screen, a photo they
        hold) is ignored; a bigger one there may be a child in front of them, so that is still asked about;
      - a tiny face far smaller than the main one (a poster, someone far away) is ignored."""
    fs = faces(im) if fs is None else fs
    if len(fs) < 2: return fs
    keep = list(fs)
    try:
        import studio_identity_v090 as idn
        if idn.available():
            det, _ = idn._models(); h, w = im.shape[:2]; sc = min(1.0, 1280 / max(h, w))
            small = cv2.resize(im, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA) if sc < 1 else im
            with NET_LOCK:
                det.setInputSize((small.shape[1], small.shape[0])); _, yn = det.detect(small)
            yn = [] if yn is None else [((z[0] + z[2] / 2) / small.shape[1], (z[1] + z[3] / 2) / small.shape[0]) for z in yn]
            ok = [f for f in keep if f['score'] >= SURE_FACE or
                  any(abs(x - f['cx']) <= max(f['w'], 0.02) * 0.6 and abs(y - f['cy']) <= max(f['h'], 0.02) * 0.6 for x, y in yn)]
            if ok: keep = ok
    except Exception:
        pass
    if len(keep) < 2: return keep
    main = max(keep, key=lambda f: f['h'] * f['score'])
    def on_body(f):
        # under half the size: a print / screen / photo. A face nearly as big there can be a child in front of a parent:
        # that one is still asked about (one click on "It's just one person" if it's wrong)
        return f is not main and f['h'] < main['h'] * 0.5 and f['cy'] > main['cy'] + main['h'] * 0.6 and abs(f['cx'] - main['cx']) < main['w'] * 1.8
    return sorted((f for f in keep if f is main or (not on_body(f) and f['h'] >= main['h'] * 0.3)), key=lambda f: f['cx'])

def main_face(fs):
    return max(fs, key=lambda f: f['h'] * f['score'])

def _region_of(im, fs, k):
    """Crop the vertical band that belongs to face k (split halfway between neighbouring faces)."""
    w = im.shape[1]; xs = [f['cx'] * w for f in fs]
    x0 = 0 if k == 0 else int((xs[k - 1] + xs[k]) / 2)
    x1 = w if k == len(fs) - 1 else int((xs[k] + xs[k + 1]) / 2)
    return im[:, x0:x1]

def pick_person(im, fs, who, track_cx=None):
    """One person out of a multi-person frame: (cropped image, its single face, centre x) or None.
    First choice by position (left / middle / right); after that the same person is followed by position
    continuity. Ambiguous frames (people too close, or the tracked person can't be told apart) are skipped."""
    fs = sorted(fs, key=lambda f: f['cx'])
    if len(fs) > 1 and min(b['cx'] - a['cx'] for a, b in zip(fs, fs[1:])) < 0.12: return None
    if track_cx is None:
        if len(fs) == 1: return None                         # can't know who a lone person is before we've seen the choice
        k = {'left': 0, 'right': len(fs) - 1, 'middle': len(fs) // 2}[who]
        if who == 'middle' and len(fs) != 3: return None
    else:
        d = sorted((abs(f['cx'] - track_cx), i) for i, f in enumerate(fs))
        if d[0][0] > 0.2 or (len(d) > 1 and d[1][0] - d[0][0] < 0.08): return None
        k = d[0][1]
    crop = _region_of(im, fs, k) if len(fs) > 1 else im
    cf = people_faces(crop)
    if len(cf) != 1: return None
    return crop, cf[0], fs[k]['cx']

def several_people_count(frames_faces):
    """How many people a multi-person upload shows (mode over frames with 2+ faces)."""
    ns = [len(f) for f in frames_faces if len(f) >= 2]
    return max(set(ns), key=ns.count) if ns else 0

def person_refs_from_frames(frames, out_dir, stem, *, max_faces=2, isolate_refs=True, who=None, faces_cache=None, penalty=None):
    """frames: list of (t, BGR). Picks sharp, well-exposed, varied single-person frames; writes
    face / alt face / upper / full stills and, for upper/full, a CLEAN (isolated on grey) version."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cands, skipped = [], {'no_face': 0, 'several_people': 0, 'low_quality': 0}
    if who is not None and who not in WHO_CHOICES: raise ValueError('WHO_LEFT_MIDDLE_RIGHT')
    found = faces_cache if faces_cache is not None and len(faces_cache) == len(frames) else [faces(im) for _, im in frames]
    found = [people_faces(im, f) for (_, im), f in zip(frames, found)]      # V0.9.26: hands, prints, posters are not people
    n_multi = sum(1 for f in found if len(f) >= 2); n_any = sum(1 for f in found if f)
    if who in ('left', 'middle', 'right') and not n_multi: who = None   # nobody to choose between: just the one person
    if who is None and n_multi and n_multi >= max(2, 0.2 * n_any):
        # several people: frames showing just one of them could be EITHER person, so ask instead of guessing
        raise ValueError(f'SEVERAL_PEOPLE_CHOOSE_WHICH:{several_people_count(found)}')
    track = None
    for i, (t, im) in enumerate(frames):
        fs = found[i]
        if not fs: skipped['no_face'] += 1; continue
        if who == 'only': fs = [main_face(fs)]
        elif who is not None:
            got = pick_person(im, fs, who, track)
            if got is None: skipped['several_people'] += 1; continue
            im, f0, track = got; fs = [f0]
        elif len(fs) > 1: skipped['several_people'] += 1; continue
        q = quality(im, fs[0])
        if not q['ok']: skipped['low_quality'] += 1; continue
        cands.append({'i': i, 't': t, 'im': im, 'face': fs[0], 'q': q, 'size': fs[0]['h'],
                      'score': q['score'] + fs[0]['score'] - (5.0 if penalty and penalty[i] else 0.0)})   # prefer frames without a caption across the person
    if not cands:
        why = max(skipped, key=skipped.get)
        raise ValueError({'no_face': 'NO_CLEAR_FACE_FOUND', 'several_people': 'EVERY_USABLE_FRAME_HAS_SEVERAL_PEOPLE_USE_ONE_PERSON_PER_REFERENCE',
                          'low_quality': 'FACE_TOO_BLURRY_DARK_OR_SMALL'}[why])
    ranked = sorted(cands, key=lambda c: -c['score'])
    picked = [ranked[0]]
    for c in ranked[1:]:
        if len(picked) >= max_faces: break
        if all(abs(c['i'] - p['i']) >= max(2, len(frames) // 6) and
               hist_dist(small_hist(c['im'][c['face']['box'][1]:, :]), small_hist(p['im'][p['face']['box'][1]:, :])) > 0.04 for p in picked):
            picked.append(c)
    outs = []
    def emit(kind, im, c, clean=False, extra=None):
        name = f"{stem}_{kind}{'_clean' if clean else ''}_t{int(c['t'] * 1000):06d}.png"
        h = _save(out_dir / name, im)
        outs.append({'kind': kind.split('_alt')[0] if kind.startswith('face') else kind, 'alt': '_alt' in kind, 'variant': 'clean' if clean else 'raw',
                     'file': name, 'path': str(out_dir / name), 'sha256': h, 'time': round(c['t'], 3), 'quality': c['q'], **(extra or {})})
    for k, c in enumerate(picked):
        emit('face' if k == 0 else f'face_alt{k}', _crop(c['im'], c['face']['box'], 0.45, 0.6, 0.5), c)
    best = picked[0]
    fullc = full_frame(ranked)
    try: shape = body_shape(fullc['im'], fullc['face'])
    except Exception: shape = None
    lying = bool(shape and shape['pose'] == 'lying')
    pose = {'pose': 'lying'} if lying else {}
    # V0.9.49 someone lying down: the waist-up crop is cut BELOW the face, which for a lying person is the bed, so it is
    # left out; the full-body still (cropped to the body) carries them, tagged as lying
    todo = [('full', fullc, fullc['im'])] if lying else [('upper', best, _crop(best['im'], best['face']['box'], 1.6, 0.8, 4.0)), ('full', fullc, fullc['im'])]
    for kind, c, im in todo:
        sig = None
        if isolate_refs:
            fs = faces(im)
            clean, info = isolate(im, fs[0] if len(fs) == 1 else None)
            if clean is not None:
                sig = info['background_signature']
                emit(kind, body_crop(clean, info), c, clean=True, extra={'subject_fraction': info['subject_fraction'],
                                                                       **({'feet_in': info['feet_in']} if kind == 'full' else {}), **pose})
        emit(kind, im, c, extra={'background_signature': sig or background_signature(im), **pose})
    return outs, skipped

TEXT_REPORT = {}     # last intake's caption/text report (read by the Studio for its warnings and the clip)

def _clean_text(frames, ignore_text):
    """Remove captions / subtitles / watermarks from frames (text on the person's clothes is kept)."""
    TEXT_REPORT.clear()
    if not ignore_text: return frames
    try:
        import studio_text_v090 as tx
        if not tx.available(): return frames
        out, rep = tx.clean_frames(frames, faces, lambda im, f: person_mask(im, f))
        rep['size'] = frames[0][1].shape[:2] if frames else None
        TEXT_REPORT.update(rep); return out
    except Exception as e:
        TEXT_REPORT.update({'checked': False, 'error': f'{type(e).__name__}: {str(e)[:100]}'}); return frames

def extract_person_refs(video, out_dir, stem, *, max_faces=2, isolate_refs=True, who=None, ignore_text=True, kind=None, orient=None):
    """From a user's ORIGINAL video (any orientation / resolution / container). With several people in it,
    `who` (left / middle / right) says which one; that person is then followed through the video.
    Captions, subtitles and watermarks are removed first (ignore_text). V0.9.50: a video with no face in it (the body only,
    e.g. the legs of someone on a bed) is asked about (NO_CLEAR_FACE_FOUND_IN_VIDEO); with your answer (kind, and orient
    'lying') its best frames become BODY-ONLY references (body_refs_from_frames)."""
    frames = _clean_text(sample_frames(video, 48), ignore_text)
    try:
        return person_refs_from_frames(frames, out_dir, stem, max_faces=max_faces, isolate_refs=isolate_refs, who=who,
                                       faces_cache=TEXT_REPORT.get('all_faces'), penalty=TEXT_REPORT.get('caption_over_person'))[0]
    except ValueError as e:
        if str(e) != 'NO_CLEAR_FACE_FOUND': raise
        if not kind: raise ValueError('NO_CLEAR_FACE_FOUND_IN_VIDEO')
        return body_refs_from_frames(frames, out_dir, stem, kind=kind, orient=orient, isolate_refs=isolate_refs)

def body_refs_from_frames(frames, out_dir, stem, *, kind='full', orient=None, isolate_refs=True, picks=2):
    """V0.9.50 BODY-ONLY references from a video with no face in it: the sharpest frames where the most of the person shows
    (measured with the person mask), each cut out on grey and cropped to the body, plus the frame as it is. Marked no_face
    (H3 takes the body, build and clothes from them; the face only from the person's face photos) and 'lying' when the body
    lies across the frame or you said so."""
    if kind not in ('full', 'upper'): kind = 'full'
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cands = []
    for i, (t, im) in enumerate(frames):
        try: m = person_mask(im)
        except Exception: continue
        if m is None: continue
        frac = float((m > 0.5).mean())
        if not 0.03 <= frac <= 0.92: continue
        ys, xs = np.nonzero(m > 0.5)
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY); x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        sh = sharpness(cv2.resize(g[y0:y1, x0:x1], (256, max(1, int(256 * (y1 - y0) / max(1, x1 - x0))))))
        cands.append({'i': i, 't': t, 'im': im, 'frac': frac, 'score': math.log1p(sh) + 4 * frac,
                      'lying': (x1 - x0) > 1.3 * (y1 - y0)})
    if not cands: raise ValueError('NO_PERSON_FOUND_IN_VIDEO: nobody could be cut out of it')
    ranked = sorted(cands, key=lambda c: -c['score']); chosen = [ranked[0]]
    for c in ranked[1:]:
        if len(chosen) >= picks: break
        if all(abs(c['i'] - p['i']) >= max(2, len(frames) // 6) for p in chosen): chosen.append(c)
    lying_all = orient == 'lying' or (orient is None and sum(c['lying'] for c in chosen) * 2 > len(chosen))
    outs = []
    for k, c in enumerate(chosen):
        q = {'ok': True, 'problems': ['no face in this video: a body-only reference'], 'score': 0}
        extra = {'no_face': True, **({'pose': 'lying'} if lying_all else {})}
        alt = '' if k == 0 else f'_alt{k}'
        sig = None
        if isolate_refs:
            clean, info = isolate(c['im'], None)
            if clean is not None:
                sig = info['background_signature']; cn = f"{stem}_{kind}{alt}_clean_t{int(c['t'] * 1000):06d}.png"
                outs.append({'kind': kind, 'alt': bool(k), 'variant': 'clean', 'file': cn, 'path': str(out_dir / cn), 'sha256': _save(out_dir / cn, body_crop(clean, info)),
                             'time': round(c['t'], 3), 'quality': q, 'subject_fraction': info['subject_fraction'], 'feet_in': info['feet_in'], **extra})
        rn = f"{stem}_{kind}{alt}_t{int(c['t'] * 1000):06d}.png"
        outs.append({'kind': kind, 'alt': bool(k), 'variant': 'raw', 'file': rn, 'path': str(out_dir / rn), 'sha256': _save(out_dir / rn, c['im']),
                     'time': round(c['t'], 3), 'quality': q, 'background_signature': sig or background_signature(c['im']), **extra})
    return outs

def upright_rotation(im):
    """None if the face in the photo is upright, else the cv2 rotation that makes it upright. YuNet scores upright faces
    clearly higher than sideways or upside-down ones, so the photo is only turned when another rotation wins clearly."""
    try:
        import studio_identity_v090 as idn
        if not idn.available(): return None
        det, _ = idn._models()
    except Exception: return None
    h, w = im.shape[:2]; sc = min(1.0, 960 / max(h, w))
    small = cv2.resize(im, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA) if sc < 1 else im
    def score(img):
        with NET_LOCK:
            det.setInputSize((img.shape[1], img.shape[0])); _, fs = det.detect(img)
        return max((float(z[14]) for z in fs), default=0.0) if fs is not None else 0.0
    here = score(small)
    if here >= 0.9: return None                           # clearly upright already: leave the photo alone
    best, pick = here, None
    for rot in (cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180):
        sc2 = score(cv2.rotate(small, rot))               # YuNet is trained on upright faces: they score clearly higher
        if sc2 > best: best, pick = sc2, rot
    return pick if (pick is not None and best >= 0.85 and best >= here + 0.1) else None

def body_shape(im, face):
    """V0.9.49 how much of the person a photo shows, measured with the person mask and independent of which way they lie:
    -> {'kind': face | upper | full, 'pose': 'lying' | 'upright', 'heads'}. heads = how far the body reaches from the face's
    centre, in face-heights (~1 a head shot, ~2-4 waist or chest up, ~6+ head to toe). It used to be the face's size alone,
    so a whole person lying on a bed, photographed close, was taken for a face photo."""
    m = person_mask(im, face)
    if m is None: return None
    ys, xs = np.nonzero(m > 0.5)
    if not len(ys): return None
    x, y, w, h = face['box']; cx, cy = x + w / 2, y + h / 2
    fh = max(1.0, float(max(w, h)))
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    side = max(xs.max() - cx, cx - xs.min()) / fh                    # along the body when lying
    down = (np.percentile(ys, 99.5) - cy) / fh                        # below the face when upright
    # V0.9.50: the face no longer has to be at one END of the mask: the cut-out often takes the bed (or couch) someone lies on as
    # part of them, so the mask is the whole bed. Lying = the body reaches much further sideways from the face than down.
    lying = bw > 1.2 * bh and side >= 3 and side > 1.4 * max(down, 0.5)
    heads = float(side if lying else down)
    if not lying and (face['h'] > 0.28 or y <= 2):                   # a big face: a face photo, as before; a head cut off at the top
        kind = 'face' if face['h'] > 0.28 else 'upper' if face['h'] > 0.12 else 'full'   # makes the face box too small to measure by
    else: kind = 'full' if heads >= 5 else 'upper' if heads >= 1.8 else 'face'
    return {'kind': kind, 'pose': 'lying' if lying else 'upright', 'heads': round(heads, 1)}

def person_refs_from_image(path, out_dir, stem, kind=None, isolate_refs=True, who=None, ignore_text=True, orient=None):
    """orient (V0.9.49): None = decide; 'lying' = the person is lying down (kept as it is, tagged lying); 'turn' = the photo
    is sideways (turned upright). A sideways face on a body lying across the frame is either one, and only you know which:
    then LYING_OR_SIDEWAYS is raised and the page asks."""
    if orient not in (None, 'lying', 'turn', 'sitting'): raise ValueError('ORIENT_LYING_OR_TURN')
    im = load_image(path)
    fs = faces(im)
    lying = False
    if len(fs) == 1:                                       # V0.9.49 someone lying down looks sideways: that is the pose, not a turned photo
        try: lying = (body_shape(im, fs[0]) or {}).get('pose') == 'lying'
        except Exception: lying = False
    if orient == 'lying': lying = True
    elif orient in ('turn', 'sitting'): lying = False
    rot = None if (SKIP_UPRIGHT or lying) else upright_rotation(im)   # a sideways / upside-down phone photo without rotation info
    if orient is None and lying and not SKIP_UPRIGHT and upright_rotation(im) is not None:
        raise ValueError('LYING_OR_SIDEWAYS')
    if rot is not None: im = cv2.rotate(im, rot)
    im = _clean_text([(0.0, im)], ignore_text)[0][1]
    fs = people_faces(im)
    if who is not None and who not in WHO_CHOICES: raise ValueError('WHO_LEFT_MIDDLE_RIGHT')
    if len(fs) > 1 and who == 'only': fs = [main_face(fs)]
    if len(fs) > 1:
        if who is None: raise ValueError(f'SEVERAL_PEOPLE_CHOOSE_WHICH:{len(fs)}')
        got = pick_person(im, fs, who)
        if got is None: raise ValueError('PEOPLE_TOO_CLOSE_TOGETHER_TO_SEPARATE_USE_ANOTHER_PHOTO')
        im, f0, _ = got; fs = [f0]
    if not fs and not kind: raise ValueError('NO_CLEAR_FACE_FOUND_IN_PHOTO')
    q = quality(im, fs[0]) if fs else {'ok': True, 'problems': ['no face visible (accepted because you chose the type)'], 'score': 0}
    if fs and not q['ok']: raise ValueError('PHOTO_QUALITY: ' + '; '.join(q['problems']))
    shape = None
    if fs:
        try: shape = body_shape(im, fs[0])
        except Exception: shape = None
    k = kind or (shape['kind'] if shape else ('face' if fs[0]['h'] > 0.28 else 'upper' if fs[0]['h'] > 0.12 else 'full'))
    pose = {'pose': 'lying'} if ((orient == 'lying' or (shape and shape['pose'] == 'lying')) and k in ('upper', 'full')) else {}
    if orient == 'sitting': pose = {'pose': 'sitting'}                     # 🪑 V0.9.55 you said they sit in it
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    outs = []
    name = f'{stem}_{k}.png'; h = _save(out_dir / name, im)
    sig = None
    if isolate_refs and k in ('upper', 'full'):
        clean, info = isolate(im, fs[0] if fs else None)
        if clean is not None:
            sig = info['background_signature']
            cn = f'{stem}_{k}_clean.png'
            outs.append({'kind': k, 'alt': False, 'variant': 'clean', 'file': cn, 'path': str(out_dir / cn), 'sha256': _save(out_dir / cn, body_crop(clean, info)),
                         'quality': q, 'subject_fraction': info['subject_fraction'], **({'no_face': True} if not fs else {}),
                         **({'feet_in': info['feet_in']} if k == 'full' else {}), **pose})
    outs.append({'kind': k, 'alt': False, 'variant': 'raw', 'file': name, 'path': str(out_dir / name), 'sha256': h, 'quality': q,
                 'background_signature': sig or background_signature(im), **({'no_face': True} if not fs else {}), **pose})   # V0.9.48 a body-only photo
    return outs

def location_plates(path, out_dir, stem, k=3, ignore_text=True):
    """Clean background plates for a place. From a video: empty (no-people) frames first, sharp,
    and visually different angles (up to k). From a photo: one plate. Reports people in each plate."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    meta = probe(path)
    frames = sample_frames(path, 36) if meta.get('frames', 0) > 1 else [(0.0, load_image(path))]
    frames = _clean_text(frames, ignore_text)
    scored = []
    for t, im in frames:
        people = len(faces(im))
        sh = sharpness(cv2.cvtColor(cv2.resize(im, (480, int(480 * im.shape[0] / im.shape[1]))), cv2.COLOR_BGR2GRAY))
        scored.append({'t': t, 'im': im, 'people': people, 'sharp': sh, 'hist': small_hist(im),
                       'score': math.log1p(sh) - 3.0 * people})
    scored.sort(key=lambda c: -c['score'])
    chosen = [scored[0]]
    for c in scored[1:]:
        if len(chosen) >= k: break
        if c['people'] > chosen[0]['people']: continue
        if all(cv2.compareHist(c['hist'], p['hist'], cv2.HISTCMP_BHATTACHARYYA) > 0.12 for p in chosen) and c['score'] > chosen[0]['score'] - 3:
            chosen.append(c)
    outs = []
    for i, c in enumerate(chosen):
        name = f'{stem}_plate{i}_t{int(c["t"] * 1000):06d}.png'
        outs.append({'kind': 'location', 'angle': i, 'file': name, 'path': str(out_dir / name), 'sha256': _save(out_dir / name, c['im']),
                     'people_in_plate': c['people'], 'plate_signature': [round(float(v), 5) for v in c['hist'].flatten()],
                     'rgb_stats': rgb_stats([c['im']])})
    return outs

def extract_location_anchor(video_or_image, out_path):
    """Backward-compatible single plate."""
    p = Path(out_path); r = location_plates(video_or_image, p.parent, p.stem, k=1)[0]
    Path(r['path']).replace(p); return {'kind': 'location', 'path': str(p), 'sha256': sha256(p)}

def conform_source(video, out_path, *, seconds=16.0, width=768, height=1344):
    """The ENDING (last `seconds`) of your original video as a movie clip: 24 fps, 9:16 (scaled to fill, centred),
    stereo audio (silence added if the video has none) so it cuts together with the H3 takes."""
    meta = probe(video); dur = float(meta.get('duration') or 0)
    start = max(0.0, dur - seconds)
    vf = f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=24,format=yuv420p'
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(video)]
    if not meta.get('audio'): cmd += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']
    cmd += ['-t', f'{min(seconds, dur):.3f}', '-vf', vf, '-map', '0:v:0', '-map', '0:a:0' if meta.get('audio') else '1:a:0',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '16', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
            '-shortest', '-map_metadata', '-1', '-movflags', '+faststart', str(out_path)]
    subprocess.run(cmd, check=True, timeout=300, capture_output=True)
    m2 = probe(out_path)
    frames = int(m2.get('frames') or round(float(m2.get('duration') or 0) * 24))
    if frames < 24: raise ValueError('ORIGINAL_VIDEO_TOO_SHORT')
    return {'frames': min(frames, 400), 'audio': True}

REF_CLIP_FRAMES = 73                 # 17k+5 frames (H3's own grid) = about 3 s at 24 fps: half the memory of a 5 s clip
REF_CLIP_SECONDS = REF_CLIP_FRAMES / 24
ANCHOR_FRAMES = 22                   # 17k+5: the last ~0.9 s of YOUR original, pinned at frame 0 of an extension take (MiniMaxH3AddGuide)

def best_clip_start(video, seconds=REF_CLIP_SECONDS, per_second=4, max_samples=90):
    """V0.9.27: where in YOUR video the 3 s reference clip should come from (it used to be the middle). Every 3 s stretch is
    scored by how much of it shows exactly one clear face (sharp, a good size) and how much the person moves.
    Returns (start seconds, {'why', 'score', 'duration'}). Measurement only; you can always pick the 3 s yourself."""
    meta = probe(video); dur = float(meta.get('duration') or 0)
    if dur <= seconds + 0.2: return 0.0, {'why': 'the video is about 3 s long', 'duration': round(dur, 2)}
    n = int(min(max_samples, max(12, dur * per_second)))
    fr = sample_frames(video, n)
    pts, prev = [], None
    for t, im in fr:
        fs = people_faces(im)
        g = cv2.cvtColor(cv2.resize(im, (96, 168) if im.shape[0] >= im.shape[1] else (168, 96)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        mv = float(np.mean(np.abs(g - prev))) if prev is not None else 0.0; prev = g
        one = len(fs) == 1
        sharp = 0.0
        if one:
            x, y, w, h = fs[0]['box']; crop = im[max(0, y):y + h, max(0, x):x + w]
            if crop.size: sharp = sharpness(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))
        pts.append({'t': t, 'one': one, 'size': fs[0]['h'] if one else 0.0, 'sharp': sharp, 'move': mv})
    smax = max((p['sharp'] for p in pts), default=0) or 1.0; mmax = max((p['move'] for p in pts), default=0) or 1.0
    best = (-1.0, 0.0, None)
    s = 0.0
    while s <= dur - seconds + 1e-6:
        win = [p for p in pts if s <= p['t'] < s + seconds]
        if win:
            face = sum(p['one'] for p in win) / len(win)
            sharp = sum(p['sharp'] for p in win if p['one']) / max(1, sum(p['one'] for p in win)) / smax
            move = min(1.0, (sum(p['move'] for p in win) / len(win)) / mmax)
            sc = 2.0 * face + 0.6 * sharp + 0.8 * move
            if sc > best[0] + 1e-9: best = (sc, s, {'face': round(face, 2), 'sharp': round(sharp, 2), 'move': round(move, 2)})
        s = round(s + 0.25, 2)
    if best[2] is None: return max(0.0, (dur - seconds) / 2), {'why': 'no clear stretch found: the middle', 'duration': round(dur, 2)}
    b = best[2]
    why = ('face visible' if b['face'] >= 0.8 else 'face visible most of the time' if b['face'] >= 0.5 else 'the clearest stretch') + \
          (' and moving' if b['move'] >= 0.5 else '')
    return float(best[1]), {'why': why, 'score': round(best[0], 3), 'duration': round(dur, 2), **b}

def make_ref_clip(video, out_path, *, seconds=REF_CLIP_SECONDS, start=None, cleanup=None):
    """<=73-frame (about 3 s), 24 fps reference clip (with audio if present) from the user's ORIGINAL video."""
    meta = probe(video)
    dur = meta['duration']
    if start is None: start = max(0.0, (dur - seconds) / 2)
    seconds = min(seconds, max(0.5, dur - start))
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(video), '-t', f'{seconds:.3f}',
           '-r', '24', '-vf', (f"scale={cleanup['w']}:{cleanup['h']},{cleanup['filter']}," if cleanup else '') +
           "scale='if(gt(iw,ih),min(1344,iw),-2)':'if(gt(iw,ih),-2,min(1344,ih))'",
           '-frames:v', str(REF_CLIP_FRAMES), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-pix_fmt', 'yuv420p', '-map_metadata', '-1']
    cmd += ['-c:a', 'aac', '-b:a', '160k'] if meta.get('audio') else ['-an']
    subprocess.run(cmd + [str(out_path)], check=True, timeout=120, capture_output=True)
    return {'kind': 'ref_clip', 'path': str(out_path), 'seconds': round(seconds, 3), 'sha256': sha256(out_path)}

def trim_ref_clip(clip, out_path, *, frames=REF_CLIP_FRAMES, at_end=False):
    """A shorter copy of one of the Director's own (already caption-cleaned) reference clips: the middle
    `frames` frames, or the last ones (at_end). Picture and sound stay the same length."""
    meta = probe(clip); dur = float(meta['duration'] or 0); sec = frames / 24
    start = max(0.0, dur - sec) if at_end else max(0.0, (dur - sec) / 2)
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(clip), '-t', f'{sec:.3f}', '-r', '24',
           '-frames:v', str(frames), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-pix_fmt', 'yuv420p', '-map_metadata', '-1']
    cmd += ['-c:a', 'aac', '-b:a', '160k'] if meta.get('audio') else ['-an']
    subprocess.run(cmd + [str(out_path)], check=True, timeout=120, capture_output=True)
    return {'path': str(out_path), 'seconds': round(min(sec, dur), 3), 'sha256': sha256(out_path)}

# ----------------------------------------------------------------------------- colour matching (your references, the joins)
def lab_stats(im, ignore_neutral=True):
    """Mean/std of L, a, b over the picture (the plain grey backdrop of a cut-out is left out)."""
    lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    if ignore_neutral:
        px = im.reshape(-1, 3).astype(np.int16)
        keep = np.abs(px - np.array(NEUTRAL, np.int16)).max(1) > 3
        if keep.sum() > 500: lab = lab[keep]
    return {'mean': lab.mean(0).tolist(), 'std': lab.std(0).tolist()}

def match_still(src, anchor, out, strength=0.5, max_ab=8.0, max_l=12.0):
    """Gently move a reference still toward the anchor's light: white balance and tint (a/b means, i.e. hue and chroma)
    and exposure (L mean), by `strength`, bounded so skin tones never swing. Contrast and detail are untouched; the
    cut-out's grey backdrop stays exactly grey. Returns the shifts applied (in LAB units)."""
    im = cv2.imread(str(src), cv2.IMREAD_COLOR); an = cv2.imread(str(anchor), cv2.IMREAD_COLOR)
    if im is None or an is None: raise ValueError('UNREADABLE_REFERENCE')
    s, a = lab_stats(im), lab_stats(an)
    d = [(a['mean'][k] - s['mean'][k]) * strength for k in range(3)]
    d = [max(-max_l, min(max_l, d[0])), max(-max_ab, min(max_ab, d[1])), max(-max_ab, min(max_ab, d[2]))]
    lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB).astype(np.float32) + np.array(d, np.float32)
    outim = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    grey = np.abs(im.astype(np.int16) - np.array(NEUTRAL, np.int16)).max(2) <= 3
    outim[grey] = NEUTRAL                                                   # the backdrop is not part of the scene: keep it grey
    if not cv2.imwrite(str(out), outim): raise ValueError('WRITE_FAILED')
    return {'dL': round(d[0], 2), 'da': round(d[1], 2), 'db': round(d[2], 2)}

def rgb_stats_window(path, start_frame, end_frame, step=3):
    """BGR mean/std over a frame range of a video (e.g. the last seconds of your original, where an extension joins)."""
    cap = cv2.VideoCapture(str(path)); vals = []
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(start_frame)))
        i = int(start_frame)
        while i < end_frame:
            ok, im = cap.read()
            if not ok: break
            if (i - start_frame) % step == 0: vals.append(cv2.resize(im, (96, 168)).reshape(-1, 3).astype(np.float32))
            i += 1
    finally: cap.release()
    if not vals: return None
    v = np.concatenate(vals)
    return {'mean_bgr': [round(float(x), 2) for x in v.mean(0)], 'std_bgr': [round(float(x), 2) for x in v.std(0)]}


# ----------------------------------------------------------------------------- V0.9.55 🧩 a first frame built from your dots
HORIZON = 0.38                                # where standing people's heads line up (eye-level camera), share of the frame height
POSTURE_H = {'standing': 1.0, 'walking': 1.0, 'leaning': 0.97, 'sitting': 0.72, 'kneeling': 0.7, 'crouching': 0.6}

def standing_horizon(im):
    """Where a STANDING person's head is in your picture (share of its height): the eye line other people are sized from. None
    when nobody stands in it whole."""
    fs = faces(im)
    if not fs: return None
    try: s = body_shape(im, fs[0])
    except Exception: return None
    if not s or s['kind'] != 'full' or s['pose'] != 'upright': return None
    m = person_mask(im, fs[0]); rows = np.flatnonzero((m > 0.5).any(1)) if m is not None else []
    if not len(rows) or rows[-1] >= 0.99 * im.shape[0] - 1: return None          # feet out of the picture: can't tell
    return round(float(rows[0]) / im.shape[0], 3)

def person_cutout(im, face=None, with_offset=False):
    """One of YOUR photos -> (BGR crop, alpha 0..1 float) of the person, cropped to the body (head to toe), or (None, None).
    with_offset (V0.9.60): also where the crop starts in the photo, (x0, y0)."""
    none = (None, None, (0, 0)) if with_offset else (None, None)
    m = person_mask(im, face)
    if m is None: return none
    b = body_box(m, pad_y=0.0, pad_x=0.02, min_ratio=0.0)
    if not b: return none
    x0, y0, x1, y1 = b
    a = m[y0:y1, x0:x1].astype(np.float32)
    a = np.clip((a - 0.25) / 0.5, 0, 1)                           # soft edge, nothing of the old background left behind
    return (im[y0:y1, x0:x1].copy(), a, (x0, y0)) if with_offset else (im[y0:y1, x0:x1].copy(), a)

def _match_light(crop, alpha, room):
    """Move the person's colour and brightness halfway toward the room's (LAB means/spreads), bounded, so a cut-out lit
    elsewhere sits in the room. Skin keeps its own tone: only half the gap, at most 18 levels of brightness."""
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32); rl = cv2.cvtColor(room, cv2.COLOR_BGR2LAB).astype(np.float32)
    w = alpha > 0.5
    if w.sum() < 50: return crop
    for c, lim in ((0, 18.0), (1, 6.0), (2, 6.0)):
        d = float(np.clip((rl[..., c].mean() - lab[..., c][w].mean()) * 0.35, -lim, lim))
        lab[..., c] += d
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

def cut_off_at_bottom(image, face=None):
    """V0.9.62 does this 'full-body' photo stop before the person's feet (they run off the bottom of the photo)?"""
    crop, a, (ox, oy) = person_cutout(image, face, with_offset=True)
    return crop is not None and oy + crop.shape[0] >= image.shape[0] - 2 and _short_body(crop, oy, image.shape[0], face)

def _short_body(crop, oy, ih, face):
    """Touching the bottom of the photo is fine if the whole body is there (shoes at the edge): a standing adult is about
    8 face-heights tall (7.9 to 8.5 in the test photos; a phone video frame cut mid-thigh measured 7.1), so a cut-out under
    7.5 of them stops above the feet. No single face: can't tell, assume whole."""
    if not face or not face.get('h'): return False
    fh = float(face['h']) * ih; top = float(face['cy']) * ih - fh * 0.65 - oy
    return (crop.shape[0] - top) / fh < 7.5

FACE_OF_HEIGHT = 0.12                          # a face box is about this share of a standing adult's height

def compose_start(room, people, horizon=None):
    """🧩 V0.9.55 the room (your photo, people taken out) with each person's cut-out (from YOUR photo in that posture) placed
    where you put their dot: feet (or where they sit / lie) at the dot, sized for how far from the camera it is, a soft
    contact shadow under them, light matched halfway to the room. people: [{'image', 'face', 'x', 'y', 'posture'}], drawn
    far to near. -> BGR picture (the room's size). Plain image processing on your originals; nothing is generated."""
    out = room.copy().astype(np.float32); H, W = room.shape[:2]
    for p in sorted(people, key=lambda q: q.get('fh') or q['y']):
        crop, a, (ox, oy) = person_cutout(p['image'], p.get('face'), with_offset=True)
        if crop is None: raise ValueError(f"COMPOSE_NO_CUTOUT: {p.get('who', 'someone')}")
        y = float(min(0.98, max(0.15, p['y']))); x = float(min(0.98, max(0.02, p['x'])))
        f = p.get('face'); ih, iw = p['image'].shape[:2]
        if p.get('fh') and f and f.get('h'):
            # 🔗 V0.9.60 measured where the movie cuts the shot before: their face that size, at that spot (not guessed from depth)
            s = float(p['fh']) * H / (float(f['h']) * ih)
            if oy + crop.shape[0] >= ih - 2 and (_short_body(crop, oy, ih, f) or p.get('posture', 'standing') not in ('standing', 'walking', 'leaning')):
                # the photo cuts them off (no feet): fade the bottom out, never a hard edge (V0.9.62 not shoes at the photo's edge)
                n_ = max(4, int(crop.shape[0] * 0.14)); a = a.copy(); a[-n_:] *= np.linspace(1, 0, n_, dtype=np.float32)[:, None]
            fcx, fcy = float(f['cx']) * iw - ox, float(f['cy']) * ih - oy           # the face's centre in the cut-out
            nw, nh = max(2, int(crop.shape[1] * s)), max(2, int(crop.shape[0] * s))
            crop = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
            a = cv2.resize(a, (nw, nh), interpolation=cv2.INTER_LINEAR)
            crop = _match_light(crop, a, room)
            bx, by = int(float(p['fx']) * W - fcx * s), int(float(p['fy']) * H - fcy * s)
            fx_, fy_ = bx + nw // 2, min(H - 1, by + nh)
            sh = np.zeros((H, W), np.float32)
            if by + nh <= H + 2:                                                      # feet in the frame: a contact shadow
                cv2.ellipse(sh, (fx_, fy_), (max(4, int(nw * 0.42)), max(2, int(nh * 0.035) + 2)), 0, 0, 360, 1.0, -1)
                sh = cv2.GaussianBlur(sh, (0, 0), max(2.0, nw * 0.06)) * 0.35
                out *= (1 - sh[..., None])
            x0, y0 = max(0, bx), max(0, by); x1, y1 = min(W, bx + nw), min(H, by + nh)
            if x1 <= x0 or y1 <= y0: continue
            cc = crop[y0 - by:y1 - by, x0 - bx:x1 - bx].astype(np.float32); aa = a[y0 - by:y1 - by, x0 - bx:x1 - bx][..., None]
            out[y0:y1, x0:x1] = cc * aa + out[y0:y1, x0:x1] * (1 - aa)
            continue
        hz = HORIZON if horizon is None else float(horizon)
        # perspective: with the camera at about eye height, standing people's heads sit on one line (the horizon), so a
        # standing person is as tall as the gap from that line down to their feet: far = small, near = big
        stand = min(0.95, max(0.08, y - hz))
        ch, cw = crop.shape[:2]
        if p.get('posture', '').startswith('lying'):
            length = stand * H * 0.95                                  # lying: the body's length runs across the frame
            s = length / max(ch, cw)
        else:
            s = stand * H * POSTURE_H.get(p.get('posture', 'standing'), 1.0) / ch
        f = p.get('face'); ih = p['image'].shape[0]
        short = (oy + ch >= ih - 2) and _short_body(crop, oy, ih, f) and not p.get('posture', '').startswith('lying') \
                and p.get('posture', 'standing') in ('standing', 'walking', 'leaning')        # (a seated photo is short anyway)
        if short:                                      # V0.9.62 the photo stops above their feet: size them by the face, fade the cut edge
            body = stand * H * POSTURE_H.get(p.get('posture', 'standing'), 1.0)
            s = body * FACE_OF_HEIGHT / (float(f['h']) * ih)
            n_ = max(4, int(ch * 0.14)); a = a.copy(); a[-n_:] *= np.linspace(1, 0, n_, dtype=np.float32)[:, None]
        nw, nh = max(2, int(cw * s)), max(2, int(ch * s))
        crop = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        a = cv2.resize(a, (nw, nh), interpolation=cv2.INTER_LINEAR)
        crop = _match_light(crop, a, room)
        bx, by = int(x * W - nw / 2), int(y * H - nh)                # feet (or the bottom of the body) on the dot
        if short:                                      # the top of the head where a whole body's would be, the rest runs on down
            top_of_head = float(f['cy']) * ih - oy - float(f['h']) * ih * 0.65       # in the cut-out, before scaling
            by = int(y * H - body - top_of_head * s)
        # contact shadow: a soft dark ellipse where they touch the floor / furniture (not under legs the photo doesn't show)
        if not short or by + nh >= y * H - 2:
            sh = np.zeros((H, W), np.float32)
            cv2.ellipse(sh, (int(x * W), int(y * H)), (max(4, int(nw * 0.42)), max(2, int(nh * 0.035) + 2)), 0, 0, 360, 1.0, -1)
            sh = cv2.GaussianBlur(sh, (0, 0), max(2.0, nw * 0.06)) * 0.35
            out *= (1 - sh[..., None])
        x0, y0 = max(0, bx), max(0, by); x1, y1 = min(W, bx + nw), min(H, by + nh)
        if x1 <= x0 or y1 <= y0: continue
        cc = crop[y0 - by:y1 - by, x0 - bx:x1 - bx].astype(np.float32); aa = a[y0 - by:y1 - by, x0 - bx:x1 - bx][..., None]
        out[y0:y1, x0:x1] = cc * aa + out[y0:y1, x0:x1] * (1 - aa)
    return np.clip(out, 0, 255).astype(np.uint8)
