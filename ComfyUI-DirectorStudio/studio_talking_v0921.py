"""Who's talking (V0.9.21): a measured hint that the person whose mouth moves while there is speech in the take's sound is
the one you gave the line to. Measurement only, never an approval; not calibrated on many takes yet.

How: every 2nd frame of the used part of the take, YuNet finds the faces and their mouth corners, SFace says who each face
is (the identity check's models), and a small picture of each person's mouth is compared with the one before it (how much
the mouth moves). The take's sound gives when someone is speaking. A person who talks moves their mouth more while there is
speech than while it is quiet; the one who does that most is "the one talking".
"""
from __future__ import annotations
import numpy as np, cv2

EVERY = 2
MIN_SPEECH_S = 0.8          # less speech than this: nothing to judge
MIN_SAMPLES = 6             # a person needs this many measured mouth samples during speech
LEAD = 1.3                  # the one talking must lead the person with the line by this much to be flagged

def _mouth(im, px):
    """Small normalised grey picture of the mouth from a face's landmarks (None if it falls outside the frame)."""
    x, y, w, h = px[:4]; rmx, rmy, lmx, lmy = px[10:14]; nx, ny = px[8:10]
    cx, cy = (rmx + lmx) / 2, (rmy + lmy) / 2
    d = max(float(np.hypot(lmx - rmx, lmy - rmy)), 0.25 * w)
    x0, x1 = int(cx - 0.9 * d), int(cx + 0.9 * d)
    y0, y1 = int(min(cy - 0.45 * d, ny + 0.3 * (cy - ny))), int(cy + 0.75 * d)
    H, W = im.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H or x1 - x0 < 6 or y1 - y0 < 6: return None
    g = cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (32, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
    return (g - g.mean()) / (g.std() + 1e-3)

def measure(path, people, frames=None, every=EVERY):
    """people: {pid: embedding}. -> {'per': {pid: {...}}, 'speech_s', 'method'} or None (no sound / no speech)."""
    import studio_identity_v090 as idn, studio_voice_v093 as vo
    try: x = vo.decode(path)
    except Exception: return None
    db = vo.frames_db(x); mask, _, _ = vo.speech_mask(db)
    speech_s = float(mask.sum()) * vo.HOP
    if speech_s < MIN_SPEECH_S: return {'per': {}, 'speech_s': round(speech_s, 2), 'method': 'NOT_ENOUGH_SPEECH'}
    cap = cv2.VideoCapture(str(path)); fps = cap.get(cv2.CAP_PROP_FPS) or 24
    lo, hi = frames if frames else (0, 10 ** 9)
    last = {p: (None, None) for p in people}; acts = {p: {'speech': [], 'quiet': []} for p in people}
    i = 0; k = 0
    try:
        while True:
            ok, im = cap.read()
            if not ok: break
            if lo <= i < hi and (i - lo) % every == 0:
                k += 1; t = i / fps; j = min(len(mask) - 1, int(t / vo.HOP))
                talking = bool(mask[j]) if len(mask) else False
                for pid, (sc, f, margin) in idn.assign(idn.embed(im), people).items():
                    if sc < idn.MATCH or margin < idn.MARGIN or 'px' not in f: continue
                    m = _mouth(im, f['px'])
                    if m is None: continue
                    pk, pm = last[pid]
                    if pk == k - 1 and pm is not None: acts[pid]['speech' if talking else 'quiet'].append(float(np.abs(m - pm).mean()))
                    last[pid] = (k, m)
            i += 1
    finally: cap.release()
    per = {}
    for pid, a in acts.items():
        sp, qu = a['speech'], a['quiet']
        per[pid] = {'speech': round(float(np.mean(sp)), 4) if sp else None, 'quiet': round(float(np.mean(qu)), 4) if qu else None,
                    'n_speech': len(sp), 'n_quiet': len(qu)}
    return {'per': per, 'speech_s': round(speech_s, 2), 'method': 'YUNET_MOUTH_MOTION_VS_SPEECH_UNCALIBRATED'}

def scores(talk):
    """{pid: how much more the mouth moves while there is speech} for everyone measured well enough."""
    out = {}
    for pid, v in ((talk or {}).get('per') or {}).items():
        if v.get('speech') is None or v['n_speech'] < MIN_SAMPLES: continue
        out[pid] = v['speech'] / (v['quiet'] + 1e-3) if v.get('quiet') is not None and v['n_quiet'] >= 4 else v['speech']
    if out and not all(((talk['per'][p].get('quiet') is not None) and talk['per'][p]['n_quiet'] >= 4) for p in out):
        out = {p: talk['per'][p]['speech'] for p in out}                 # compare like with like
    return out

def shares(talk, names):
    sc = scores(talk); tot = sum(sc.values())
    return {names.get(p, p): round(100 * v / tot) for p, v in sc.items()} if tot > 0 and len(sc) >= 2 else {}

def warnings(talk, names, speakers):
    """'TALKING: Grandpa seems to be the one talking, but the line is Maya's' when someone without a line clearly leads."""
    if not talk or (talk.get('speech_s') or 0) < MIN_SPEECH_S: return []
    sc = scores(talk); spk = [s for s in speakers if s in sc]
    if len(sc) < 2 or not spk: return []
    top = max(sc, key=sc.get)
    if top in speakers: return []
    best_spk = max(spk, key=sc.get)
    if sc[top] < LEAD * sc[best_spk]: return []
    who = ' and '.join(names.get(s, s) for s in dict.fromkeys(speakers) if s in names)
    return [f"TALKING: {names.get(top, top)} seems to be the one talking, but the line is {who}'s "
            "(mouth movement measured against the sound; a hint, check by watching)."]
