"""Who is who: face identity scores for references and takes (measurement only, never approval).

Models (shipped in models/, both from the OpenCV model zoo):
  YuNet face detector + 5-point landmarks   face_detection_yunet_2023mar.onnx   MIT
  SFace face recognition (128-d)            face_recognition_sface_2021dec.onnx Apache-2.0
Cosine similarity of SFace features. Measured on the user's real footage (originals vs real H3 takes):
  same person in a take        0.34 - 0.82 (small, soft faces score lower)
  the other person             0.02 - 0.34
  TEST take after its F101 hidden cut: 0.27 / 0.25 -> nobody matches
So a person is "recognised" at >= 0.30 when they also beat the other cast member by >= 0.05.
"""
from __future__ import annotations
import json
from pathlib import Path
import cv2, numpy as np
from studio_netlock_v092 import NET_LOCK

HERE = Path(__file__).resolve().parent
DET = HERE / 'models' / 'face_detection_yunet_2023mar.onnx'
REC = HERE / 'models' / 'face_recognition_sface_2021dec.onnx'
MATCH = 0.30          # recognised
MARGIN = 0.05         # must beat the other person by this much
WEAK = 0.30           # median below this -> "looks less like their reference"
_det = _rec = None

def available():
    return DET.is_file() and REC.is_file()

def _models():
    global _det, _rec
    with NET_LOCK:                                   # both made together, before anyone can see a half-ready pair
        if _det is None or _rec is None:
            d = cv2.FaceDetectorYN.create(str(DET), '', (320, 320), 0.6, 0.3, 5000)
            r = cv2.FaceRecognizerSF.create(str(REC), '')
            _rec = r; _det = d
        return _det, _rec

def embed(im, max_side=1280):
    """Faces in a BGR image: list of {'cx','cy','h','emb'} (cx/cy/h relative to the image)."""
    det, rec = _models()
    h, w = im.shape[:2]; s = min(1.0, max_side / max(h, w))
    im2 = cv2.resize(im, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else im
    with NET_LOCK:
        det.setInputSize((im2.shape[1], im2.shape[0]))
        _, faces = det.detect(im2)
        feats = []
        for f in (faces if faces is not None else []):
            try: feats.append((f, rec.feature(rec.alignCrop(im2, f)).flatten().astype(np.float32)))
            except cv2.error: continue
    out = []
    for f, e in feats:
        e /= (np.linalg.norm(e) + 1e-8)
        out.append({'cx': float((f[0] + f[2] / 2) / im2.shape[1]), 'cy': float((f[1] + f[3] / 2) / im2.shape[0]),
                    'h': float(f[3] / im2.shape[0]), 'emb': e,
                    'px': [float(v) / s for v in f[:14]]})      # box + 5 landmarks in the original image's pixels (V0.9.21: mouth)
    return out

def person_embedding(images):
    """Mean identity of one person from their reference stills (each must show exactly one face)."""
    es = []
    for im in images:
        fs = embed(im)
        if len(fs) == 1: es.append(fs[0]['emb'])
    if not es: return None
    m = np.mean(es, axis=0); return m / (np.linalg.norm(m) + 1e-8)

def assign(faces, people):
    """Best one-to-one assignment of faces to people (<= 3 x 3, brute force). Returns {pid: (score, face)}."""
    import itertools
    pids = list(people)
    if not faces or not pids: return {}
    S = np.array([[float(f['emb'] @ people[p]) for p in pids] for f in faces])
    # V0.9.27: every one-to-one pairing of faces and people, whichever side has more. (Before, with fewer faces than
    # people, e.g. one person off screen, the faces always went to the FIRST people in the list.)
    k = min(len(faces), len(pids)); best, best_v = None, -9
    for fsel in itertools.permutations(range(len(faces)), k):
        for psel in itertools.combinations(range(len(pids)), k):
            v = sum(S[fi, pi] for fi, pi in zip(fsel, psel))
            if v > best_v: best, best_v = list(zip(fsel, psel)), v
    out = {}
    for fi, pi in best:
        others = [S[fi, j] for j in range(len(pids)) if j != pi]
        out[pids[pi]] = (float(S[fi, pi]), faces[fi], float(S[fi, pi] - max(others)) if others else 1.0)
    return out

def take_identity(path, people, frames=None, every=6):
    """Per person: median similarity over sampled frames of the used range, where they were on screen,
    and frames where a face matched nobody. people: {pid: embedding}."""
    cap = cv2.VideoCapture(str(path)); i = 0; per = {p: [] for p in people}; xs = {p: [] for p in people}; strangers = []; seen = 0
    lo, hi = frames if frames else (0, 10 ** 9)
    try:
        while True:
            ok, im = cap.read()
            if not ok: break
            if lo <= i < hi and (i - lo) % every == 0:
                fs = embed(im); seen += 1
                got = assign(fs, people)
                for p, (sc, f, margin) in got.items():
                    per[p].append(sc)
                    if sc >= MATCH and margin >= MARGIN: xs[p].append(f['cx'])
                matched = {id(v[1]) for v in got.values() if v[0] >= MATCH}
                for f in fs:
                    if id(f) not in matched and f['h'] > 0.04 and max(float(f['emb'] @ e) for e in people.values()) < 0.22: strangers.append(i)
            i += 1
    finally: cap.release()
    res = {}
    for p in people:
        v = per[p]
        res[p] = {'median': round(float(np.median(v)), 3) if v else None, 'frames_seen': len(v), 'samples': seen,
                  'screen_x': round(float(np.median(xs[p])), 3) if xs[p] else None}
    return {'people': res, 'unrecognised_face_frames': sorted(set(strangers))[:20], 'method': 'YUNET_SFACE_COSINE_MEASUREMENT_ONLY'}

def identity_warnings(ident, names, cast):
    out = []
    for p in cast:
        r = (ident or {}).get('people', {}).get(p)
        if not r or r['median'] is None:
            if r and r['samples']: out.append(f"IDENTITY: {names.get(p, p)} was not found in the take.")
            continue
        if r['median'] < WEAK: out.append(f"IDENTITY: {names.get(p, p)} looks less like their reference (score {r['median']:.2f}).")
    if len(ident.get('unrecognised_face_frames', [])) >= 3:
        out.append(f"IDENTITY: a face that matches nobody in the cast appears (frames {', '.join(map(str, ident['unrecognised_face_frames'][:4]))}…).")
    xs = [(ident['people'][p]['screen_x'], p) for p in cast if ident.get('people', {}).get(p, {}).get('screen_x') is not None]
    return out, [p for _, p in sorted(xs)]

def side_of(images_faces, person):
    """For a multi-person upload: which position (left/middle/right) matches an existing person best."""
    ranked = sorted(images_faces, key=lambda f: f['cx'])
    if len(ranked) < 2: return None, None
    sc = [float(f['emb'] @ person) for f in ranked]
    k = int(np.argmax(sc)); others = [x for j, x in enumerate(sc) if j != k]
    if sc[k] < MATCH or sc[k] - max(others) < MARGIN: return None, round(sc[k], 3)
    pos = 'left' if k == 0 else 'right' if k == len(ranked) - 1 else 'middle'
    return pos, round(sc[k], 3)
