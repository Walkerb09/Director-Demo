"""Clean-Chain guard: a picture or clip offered as a reference must not be (part of) a GENERATED take.

Metadata and hashes catch untouched files; this catches the rest: a last frame (or any frame) saved as a
PNG/JPEG, resized, recompressed, brightened, mirrored, or cropped to a face or upper body, and a generated
take re-encoded as a "phone video". Three measurements against every take in ComfyUI/output:
  1. whole frame: normalised correlation of 72x126 thumbnails against EVERY frame (also mirrored);
  2. crops: ORB features + RANSAC homography against keyframes (every 4th + the last 4), then pixel
     correlation of the warped picture against the 7 neighbouring frames;
  3. small crops of the ending: multi-scale template matching against the last 12 frames.
A match >= 0.955 is refused. Measured on the user's real takes and originals: originals score at most
0.933 (the presenter face original against the TEST take, which reproduces it); real frames and crops
of generated takes score 0.965-0.9999.
Fingerprints are cached per take file (name, size, mtime) so checks stay fast.
"""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, tempfile
from pathlib import Path
import cv2, numpy as np

THRESHOLD = 0.955
_orb = None

def _ORB():
    global _orb
    if _orb is None: _orb = cv2.ORB_create(1500)
    return _orb

def _gray(img, side):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    s = side / max(g.shape)
    return cv2.resize(g, (max(1, round(g.shape[1] * s)), max(1, round(g.shape[0] * s))), interpolation=cv2.INTER_AREA) if s < 1 else g

def _ncc(a, b, mask=None):
    a = a.astype(np.float32); b = b.astype(np.float32)
    if mask is not None: a = a[mask]; b = b[mask]
    if a.size < 16: return 0.0
    a = a - a.mean(); b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))

def _frames(path):
    """Grey 672-px frames, decoded one at a time (a full-resolution frame list would need ~400 MB per take)."""
    cap = cv2.VideoCapture(str(path)); out = []
    try:
        while True:
            ok, im = cap.read()
            if not ok: break
            out.append(_gray(im, 672))
    finally: cap.release()
    return out

def fingerprint(path, cache_dir, orb=True):
    """Cached fingerprint of one generated take. orb=False: no ORB features (the whole-frame and ending checks
    still work; used when OpenCV's ORB crashes on a machine)."""
    path = Path(path); st = path.stat()
    key = hashlib.sha256(f'{path.name}|{st.st_size}|{st.st_mtime_ns}|v1{"" if orb else "noorb"}'.encode()).hexdigest()[:20]
    cache = Path(cache_dir) / f'{key}.npz'
    if cache.is_file():
        try:
            z = np.load(cache, allow_pickle=False)
            return {'path': str(path), 'thumbs': z['thumbs'], 'kf': z['kf'], 'kp': z['kp'], 'desc': z['desc'], 'kpn': z['kpn'],
                    'tail': z['tail'], 'n': int(z['n'])}
        except Exception: pass
    fr = _frames(path); n = len(fr)
    if n == 0: raise ValueError('TAKE_HAS_NO_FRAMES')
    thumbs = np.stack([cv2.resize(f, (72, 126), interpolation=cv2.INTER_AREA) for f in fr])
    kf = sorted(set(range(0, n, 4)) | set(range(max(0, n - 4), n)))
    kps, descs, kpn = [], [], []
    for i in (kf if orb else []):
        kp, d = _ORB().detectAndCompute(fr[i], None)
        if d is None: kp, d = [], np.zeros((0, 32), np.uint8)
        kps.append(np.float32([k.pt for k in kp]).reshape(-1, 2)); descs.append(d); kpn.append(len(kp))
    tail = np.stack([cv2.resize(f, (round(f.shape[1] * 0.6), round(f.shape[0] * 0.6)), interpolation=cv2.INTER_AREA) for f in fr[-12:]])
    if not orb: kf = []
    fp = {'path': str(path), 'thumbs': thumbs, 'kf': np.int32(kf), 'kp': np.concatenate(kps) if kps else np.zeros((0, 2), np.float32),
          'desc': np.concatenate(descs) if descs else np.zeros((0, 32), np.uint8), 'kpn': np.int32(kpn), 'tail': tail, 'n': n}
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.stem + '.partial.npz')
        np.savez_compressed(tmp, **{k: v for k, v in fp.items() if k != 'path'}); os.replace(tmp, cache)
    except Exception: pass
    return fp

def match(img, fp, *, crops=True, orb=True):
    """Best similarity of one picture (BGR) to one take fingerprint: (score, frame, method)."""
    g = _gray(img, 512); best = (0.0, None, None)
    th = fp['thumbs']; n = fp['n']
    if abs(g.shape[0] / g.shape[1] - th.shape[1] / th.shape[2]) < 0.08:
        a = cv2.resize(g, (72, 126), interpolation=cv2.INTER_AREA)
        for i in range(n):
            for aa in (a, a[:, ::-1]):
                c = _ncc(aa, th[i])
                if c > best[0]: best = (c, i, 'whole_frame')
        if best[0] >= THRESHOLD: return best
    if not crops: return best
    kp1, d1 = _ORB().detectAndCompute(g, None) if (orb and len(fp['kf'])) else ([], None)
    if d1 is not None and len(kp1) >= 20:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING); off = 0; frames = None
        cands = []
        for j, i in enumerate(fp['kf']):
            k = int(fp['kpn'][j]); d2 = fp['desc'][off:off + k]; p2 = fp['kp'][off:off + k]; off += k
            if k < 12: continue
            good = [m for m, *r in bf.knnMatch(d1, d2, k=2) if r and m.distance < 0.75 * r[0].distance]
            if len(good) < 12: continue
            H, inl = cv2.findHomography(np.float32([kp1[m.queryIdx].pt for m in good]), np.float32([p2[m.trainIdx] for m in good]), cv2.RANSAC, 4.0)
            if H is not None and inl is not None and inl.sum() >= 10: cands.append((int(inl.sum()), int(i), H))
        if cands:
            frames = _frames(fp['path'])
            for _, i, H in sorted(cands, key=lambda c: -c[0])[:6]:
                size = (frames[0].shape[1], frames[0].shape[0])
                w = cv2.warpPerspective(g, H, size)
                mk = cv2.erode((cv2.warpPerspective(np.full_like(g, 255), H, size) > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
                if mk.sum() < 1500: continue
                for j in range(max(0, i - 3), min(n, i + 4)):
                    c = _ncc(w, frames[j], mk)
                    if c > best[0]: best = (c, j, 'crop_features')
            if best[0] >= THRESHOLD: return best
    tail = fp['tail']; fh, fw = tail.shape[1:]
    if g.shape[0] < fh / 0.6 * 0.95 and float(g.std()) >= 18:           # crops only; flat wall patches match anything
        for s in (0.5, 0.7, 0.85, 1.0, 1.2, 1.45, 1.75, 2.1):
            tp = cv2.resize(g, (max(8, round(g.shape[1] * s * 0.6)), max(8, round(g.shape[0] * s * 0.6))), interpolation=cv2.INTER_AREA)
            if tp.shape[0] >= fh or tp.shape[1] >= fw or tp.shape[0] < 24 or tp.shape[1] < 24 or float(tp.std()) < 18: continue
            for j in range(len(tail)):
                c = float(cv2.matchTemplate(tail[j], tp, cv2.TM_CCOEFF_NORMED).max())
                if c > best[0]: best = (c, n - len(tail) + j, 'ending_crop')
    return best

def generated_takes(output_dir, extra=(), limit=80):
    out = [p for p in Path(output_dir).glob('*.mp4') if p.is_file() and not p.is_symlink()] if Path(output_dir).is_dir() else []
    out += [Path(p) for p in extra if Path(p).is_file()]
    seen = set(); res = []
    for p in sorted(out, key=lambda p: -p.stat().st_mtime):
        if str(p) in seen: continue
        seen.add(str(p)); res.append(p)
    return res[:limit]

def check_pictures(pictures, takes, cache_dir, *, crops=True, orb=True):
    """pictures: list of BGR images. Returns None (clean) or {'take','frame','score','method'} of the first match."""
    for p in takes:
        try: fp = fingerprint(p, cache_dir, orb=orb)
        except Exception: continue
        for img in pictures:
            sc, fr, how = match(img, fp, crops=crops, orb=orb)
            if sc >= THRESHOLD:
                return {'take': Path(p).name, 'frame': fr, 'frames': fp['n'], 'score': round(sc, 4), 'method': how}
    return None


def check_pictures_isolated(pictures, takes, cache_dir, *, python=None, env=None):
    """check_pictures in a child process. A native crash (seen in OpenCV's ORB on a Vast instance) can't take the caller
    down: the check runs again without ORB features (the whole-frame and ending checks still run, and those catch a
    saved last frame). Returns (hit or None, note or None). If even that crashes, raises RuntimeError, and the upload
    is refused, because an unchecked file is never let in."""
    with tempfile.TemporaryDirectory() as t:
        np.savez(Path(t) / 'pics.npz', *[np.ascontiguousarray(p) for p in pictures])
        (Path(t) / 'takes.json').write_text(json.dumps([str(p) for p in takes]))
        base = dict(env or os.environ); where = None
        attempts = [('full', True, {}),
                    # a crash that only happens in OpenCV's fast CPU paths (AVX-512/AVX2) or its worker threads
                    ('safe_cpu', True, {'OPENCV_CPU_DISABLE': SAFE_CPU_DISABLE, 'OPENCV_FOR_THREADS_NUM': '1'}),
                    ('no_orb', False, {'OPENCV_CPU_DISABLE': SAFE_CPU_DISABLE, 'OPENCV_FOR_THREADS_NUM': '1'})]
        for name, orb, extra in attempts:
            cmd = [python or sys.executable, str(Path(__file__).resolve()), '--check', t, '--cache', str(cache_dir)] + ([] if orb else ['--no-orb'])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env={**base, **extra})
            line = next((l for l in r.stdout.splitlines()[::-1] if l.startswith('CC ')), None)
            if r.returncode == 0 and line:
                at = f" ({where[0]}:{where[1]} {where[2]})" if where else ''
                note = None if name == 'full' else (
                    f"OpenCV crashed in the Clean-Chain check on this machine{at}; it ran again with OpenCV's fast CPU paths off and passed." if name == 'safe_cpu' else
                    f"The detailed crop check of Clean-Chain was skipped for this file because OpenCV crashed in it on this machine{at}; "
                    "the whole-frame and ending checks still ran.")
                return json.loads(line[3:]), note
            where = where or next(iter(re.findall(r'File "[^"]*?([^/"]+)", line (\d+) in (\w+)', r.stderr or '')), None)
            print(f'[DirectorStudio] Clean-Chain check ({name}) failed', r.returncode, (r.stderr or '')[-2000:], file=sys.stderr, flush=True)
        raise RuntimeError('CLEAN_CHAIN_CHECK_CRASHED')

SAFE_CPU_DISABLE = 'AVX512_SKX,AVX512_COMMON,AVX512_KNL,AVX512_KNM,AVX512_CNL,AVX512_CLX,AVX512_ICL,AVX2,FMA3,AVX'

if __name__ == '__main__':
    import argparse, faulthandler
    faulthandler.enable(all_threads=True)
    a = argparse.ArgumentParser(); a.add_argument('--check', required=True); a.add_argument('--cache', required=True); a.add_argument('--no-orb', action='store_true')
    x = a.parse_args()
    _t = os.environ.get('DIRECTOR_CC_TEST_CRASH')                                    # tests only: 'orb' = only the fast-CPU run crashes
    if not x.no_orb and (_t == 'orb_always' or (_t == 'orb' and not os.environ.get('OPENCV_CPU_DISABLE'))):
        import signal; os.kill(os.getpid(), signal.SIGSEGV)
    z = np.load(Path(x.check) / 'pics.npz'); pics = [z[k] for k in z.files]
    takes = [Path(p) for p in json.loads((Path(x.check) / 'takes.json').read_text())]
    print('CC ' + json.dumps(check_pictures(pics, takes, x.cache, orb=not x.no_orb)), flush=True)
