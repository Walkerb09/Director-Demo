"""V0.9.65 Best references from ALL of a person's photos and videos, around ONE main reference (measurement, choice and
crops of your own originals only; nothing is generated or blended: Clean-Chain).

  The MAIN REFERENCE sets how the person looks in the shot: clothes, hair, makeup, accessories. It is the upload you ★,
  else the one that shows them best (a full body first). Uploads that are clearly the same look count with it: the same
  clothes on the torso (measured light-independently), or the same photo session (practically the same face, e.g. a
  face crop of the same picture). Every body still, the person's reference clip and the first face come from the main
  look, so clothes and makeup from different days never mix.

  SUPPORTING FACES from the other uploads add identity only: H3 gets them as close crops of the face, and the prompt says
  they are for the facial features. They are ranked by:
  - identity agreement with the person's ROBUST identity (all their stills, odd ones out left out; a face that reads
    unlike the rest, e.g. the wrong person from a group photo, is left out entirely),
  - quality (sharpness, face size, exposure),
  - variety: another upload and another angle (left / front / right) first; near-copies skipped.

All measurements are cached by the files' hashes. Pure numpy/OpenCV and the models already shipped (YuNet + SFace)."""
from __future__ import annotations
import json, os
from pathlib import Path
import cv2, numpy as np

VERSION = 'bestref-5'
OUTLIER = 0.30          # identity agreement below this, with 3+ face stills, = left out (measured: other person 0.02-0.34)
DROP_BELOW_MEDIAN = 0.18
DUPLICATE = 0.93        # two faces this alike (and same angle) add nothing new
SAME_OUTFIT = 0.24      # clothes distance (torso). Measured on the bundled originals: same clothes in other photos 0.12-0.17,
                        # in other light mostly 0.01-0.08 (grey clothes under strong colour casts up to 0.27); recoloured clothes
                        # 0.33-0.38; Maya vs Grandpa 0.39. Unsure = kept apart (only the main reference's look is used)
SAME_SESSION = 0.80     # a face this alike (SFace) is the same photo session (a crop of the same picture scores 0.98);
                        # the same person on other days mostly 0.35-0.75
YAW_SIDE = 0.14

def yaw_bucket(y):
    if y is None: return 'front'
    return 'left' if y < -YAW_SIDE else 'right' if y > YAW_SIDE else 'front'

def _yaw(f):
    try:
        px = f['px']; rx, lx, nx = px[4], px[6], px[8]
        return max(-1.0, min(1.0, (nx - (rx + lx) / 2) / max(1.0, abs(lx - rx))))
    except (KeyError, IndexError, TypeError): return None

def _unit(v):
    v = np.asarray(v, np.float32); return v / (np.linalg.norm(v) + 1e-8)

def robust_centroid(embs):
    """Mean identity with the stills that disagree with the rest left out (two rounds). -> (centroid, kept_indices)."""
    E = [_unit(e) for e in embs if e is not None]
    if not E: return None, []
    idx = list(range(len(E)))
    c = _unit(np.mean(E, axis=0))
    for _ in range(2):
        if len(idx) < 3: break
        s = np.array([float(E[i] @ c) for i in idx])
        med = float(np.median(s))
        thr = min(0.55, max(OUTLIER, med - DROP_BELOW_MEDIAN))     # side views score lower but are kept
        keep = [i for i, v in zip(idx, s) if v >= thr]
        if len(keep) < 2 or keep == idx: break
        idx = keep; c = _unit(np.mean([E[i] for i in idx], axis=0))
    return c, idx

def _soft(h):
    h = cv2.GaussianBlur(h.astype(np.float32), (3, 3), 0) if h.ndim == 2 and min(h.shape) >= 3 else h.astype(np.float32)
    return h / (h.sum() + 1e-8)

def clothes_signature(im, face_px, mask=None):
    """The clothes on the person's torso in one of YOUR photos, measured from the face down (so an upper-body and a full-body
    photo compare alike), in a form that stays put when only the light changes: colour white-balanced against the
    person's own skin (cheeks), brightness relative to it. face_px: the face box [x, y, w, h]; mask: the person mask."""
    x, y, w, h = [float(v) for v in face_px[:4]]
    H, W = im.shape[:2]
    y0, y1 = int(y + 1.35 * h), int(min(H, y + 4.0 * h)); x0, x1 = int(max(0, x - 1.2 * w)), int(min(W, x + 2.2 * w))
    if y1 - y0 < 8 or x1 - x0 < 8: return None
    reg = im[y0:y1, x0:x1]
    mm = (mask[y0:y1, x0:x1] > 0.5) if mask is not None else np.ones(reg.shape[:2], bool)
    sk = im[int(y + 0.35 * h):int(y + 0.8 * h), int(x + 0.25 * w):int(x + 0.75 * w)].reshape(-1, 3).astype(np.float32) + 1.0
    px = reg[mm].astype(np.float32) + 1.0
    if len(px) < 300 or len(sk) < 20: return None
    hch = np.median(sk / sk.sum(1, keepdims=True), axis=0); ch = px / px.sum(1, keepdims=True)
    chroma, _, _ = np.histogram2d(np.log(ch[:, 2] / hch[2]), np.log(ch[:, 1] / hch[1]), bins=10, range=[[-1.0, 1.0], [-0.8, 0.8]])
    rel, _ = np.histogram(px.mean(1) / max(8.0, float(np.median(sk.mean(1)))), bins=8, range=(0.0, 2.0))
    return [round(float(v), 5) for v in np.concatenate([_soft(chroma).flatten(), _soft(rel.reshape(1, -1)).flatten()])]

def clothes_distance(a, b):
    if a is None or b is None: return None
    a = np.asarray(a, np.float32); b = np.asarray(b, np.float32)
    if a.shape != b.shape or a.size != 108: return None
    c = float(cv2.compareHist(a[:100].reshape(10, 10), b[:100].reshape(10, 10), cv2.HISTCMP_BHATTACHARYYA))
    r = float(cv2.compareHist(a[100:].reshape(8, 1), b[100:].reshape(8, 1), cv2.HISTCMP_BHATTACHARYYA))
    return 0.6 * c + 0.4 * r


class RefInfo:
    """Per-reference measurements (identity, angle, clothes), cached on disk by the reference's hash."""
    def __init__(self, cache_dir, input_dir, load_image):
        self.dir = Path(cache_dir); self.input_dir = Path(input_dir); self.load = load_image; self.mem = {}

    def get(self, ref):
        sha = ref.get('sha256')
        if not sha: return {}
        key = f"{sha[:20]}_{ref.get('kind')}_{ref.get('variant', 'raw')}"
        if key in self.mem: return self.mem[key]
        f = self.dir / f'bestref_{key}_{VERSION}.json'
        if f.is_file():
            try: self.mem[key] = json.loads(f.read_text()); return self.mem[key]
            except Exception: pass
        info = self._measure(ref)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + '.partial'); tmp.write_text(json.dumps(info)); os.replace(tmp, f)
        except OSError: pass
        self.mem[key] = info
        return info

    def _measure(self, ref):
        out = {}
        try: im = self.load(self.input_dir / ref['input_name'])
        except Exception: return out
        if ref.get('no_face') or ref.get('variant', 'raw') != 'raw': return out
        try:
            import studio_identity_v090 as idn
            if not idn.available(): return out
            fs = idn.embed(im)
        except Exception: return out
        if len(fs) == 1:
            out['emb'] = [round(float(x), 6) for x in fs[0]['emb']]
            out['yaw'] = _yaw(fs[0]); out['face_h'] = round(fs[0]['h'], 4); out['px'] = [round(float(v), 1) for v in fs[0]['px'][:4]]
            if ref.get('kind') in ('full', 'upper'):
                try:
                    import studio_media_v080 as media
                    mf = media.faces(im); mask = media.person_mask(im, mf[0] if len(mf) == 1 else None)
                except Exception: mask = None
                out['clothes'] = clothes_signature(im, fs[0]['px'], mask)
        out['faces'] = len(fs)
        return out


def _families(refs, rids, kind):
    """raw + clean versions of one still (same upload, kind, moment) -> one family."""
    fam = {}
    for r in rids:
        R = refs[r]
        if R.get('kind') != kind or R.get('removed') or R.get('matched_from'): continue
        k = (R.get('source_sha256'), bool(R.get('alt')), R.get('time'), bool(R.get('no_face')))
        d = fam.setdefault(k, {'raw': None, 'clean': None, 'source': R.get('source_sha256')})
        slot = 'clean' if R.get('variant') == 'clean' else 'raw' if R.get('variant', 'raw') == 'raw' else None
        if slot and d[slot] is None: d[slot] = r
    return [d for d in fam.values() if d['raw'] or d['clean']]


def identity_crop(im, face_px, pad=(0.14, 0.22, 0.10), min_side=112):
    """A SUPPORTING face from another upload, cropped close to the face (forehead to chin, a little at the sides) so its
    hair, makeup at the edges, neck and clothes say as little as possible: it is there for the facial features only.
    Only a crop of your original photo: nothing is drawn or blended. face_px: YuNet box [x, y, w, h, ...]."""
    x, y, w, h = [float(v) for v in face_px[:4]]
    H, W = im.shape[:2]
    x0 = max(0, int(x - pad[0] * w)); x1 = min(W, int(x + w + pad[0] * w))
    y0 = max(0, int(y - pad[1] * h)); y1 = min(H, int(y + h + pad[2] * h))
    if x1 - x0 < 32 or y1 - y0 < 32: return None
    crop = im[y0:y1, x0:x1]
    if min(crop.shape[:2]) < min_side:
        sc = min_side / min(crop.shape[:2]); crop = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
    return crop


def plan_person(refs, rids, info, *, main_source=None, max_faces=3, standing=True):
    """The ranked choice for one person, around ONE MAIN REFERENCE (one upload).
    The main reference sets how the person looks in the shot: clothes, hair, makeup, accessories. Every body still and
    the first face come from it. Faces from the person's OTHER uploads only SUPPORT it (facial features, as close crops),
    so clothes and makeup from different photos are never mixed.
    -> {'main', 'main_why', 'faces': [rid...], 'support': {rid...}, 'full': [family...], 'upper': [family...],
        'outfit_sources': [main], 'face_info', 'notes', 'centroid'}"""
    notes = []
    ok = lambda r: not refs[r].get('removed') and not refs[r].get('matched_from') and not refs[r].get('support_of')
    face_rids = [r for r in rids if refs[r].get('kind') == 'face' and refs[r].get('variant', 'raw') == 'raw' and not refs[r].get('no_face') and ok(r)]
    body_raw = [r for r in rids if refs[r].get('kind') in ('full', 'upper') and refs[r].get('variant', 'raw') == 'raw' and not refs[r].get('no_face') and ok(r)]
    # identity: every raw still with one face, robust mean
    pool = face_rids + body_raw
    embs = [(r, info.get(refs[r]).get('emb')) for r in pool]
    embs = [(r, e) for r, e in embs if e]
    centroid, kept = robust_centroid([e for _, e in embs])
    sim = {}
    if centroid is not None:
        for r, e in embs: sim[r] = float(_unit(e) @ centroid)
    sources = {refs[r].get('source_sha256') for r in face_rids}

    cands = []
    for r in face_rids:
        R = refs[r]; I = info.get(R)
        q = float(((R.get('quality') or {}).get('score')) or 0.0)
        s = sim.get(r)
        if s is not None and len(embs) >= 3 and s < OUTLIER:
            notes.append(f"left out a face still that reads unlike the others (identity agreement {s:.2f})"); continue
        cands.append({'rid': r, 'score': 4.0 * (s if s is not None else 0.5) + 0.35 * q, 'sim': s, 'yaw': yaw_bucket(I.get('yaw')),
                      'emb': I.get('emb'), 'src': R.get('source_sha256'), 'px': I.get('px')})

    def fam_score(d, kind):
        raw = refs[d['raw']] if d['raw'] else None; cl = refs[d['clean']] if d['clean'] else None
        any_r = raw or cl
        v = 0.3 * float(((any_r.get('quality') or {}).get('score')) or 0.0)
        v += 3.0 * sim[d['raw']] if d['raw'] and d['raw'] in sim else 1.5
        if kind == 'full':
            fi = (cl or {}).get('feet_in')
            v += 1.5 if fi else (-1.0 if fi is False else 0.0)
        sf = (cl or {}).get('subject_fraction')
        if sf: v += min(1.0, sf * 3)
        if standing and any_r.get('pose') in ('lying', 'sitting'): v -= 2.0
        return v
    fams = {k: [d for d in _families(refs, [r for r in rids if ok(r)], k)
                if not (d['raw'] and refs[d['raw']].get('no_face')) and not (d['clean'] and refs[d['clean']].get('no_face'))] for k in ('full', 'upper')}

    # ---- the main reference: the upload you starred, else the one that shows the person best (body first) -----------
    ups = {c['src'] for c in cands} | {d['source'] for k in fams for d in fams[k]}
    def upload_score(u):
        fu = [fam_score(d, 'full') for d in fams['full'] if d['source'] == u]
        up = [fam_score(d, 'upper') for d in fams['upper'] if d['source'] == u]
        fc = [c['score'] for c in cands if c['src'] == u]
        return (max(fu) + 2.0 if fu else max(up) if up else -3.0) + 0.5 * (max(fc) if fc else 0.0)
    if main_source and main_source in ups: main, main_why = main_source, 'starred'
    elif ups:
        main = max(sorted(ups, key=str), key=upload_score); main_why = 'auto'
        if main_source: notes.append('the ★ main reference has no usable face or body photo, so the best upload is the main one')
    else: main, main_why = None, None

    # ---- the main LOOK: the main upload plus uploads that are clearly the same look (same clothes on the torso, or the
    # same photo session: the face practically identical, e.g. a face crop of the same picture) ---------------------
    look = {main} if main else set()
    by_up = {}
    for r in pool:
        by_up.setdefault(refs[r].get('source_sha256'), []).append(r)
    clothes_of = lambda u: [info.get(refs[r]).get('clothes') for r in by_up.get(u, []) if refs[r]['kind'] in ('full', 'upper') and info.get(refs[r]).get('clothes')]
    emb_of = lambda u: [info.get(refs[r]).get('emb') for r in by_up.get(u, []) if info.get(refs[r]).get('emb')]
    joined = {}
    for _ in range(2):
        for u in sorted(ups - look, key=str):
            cl = [clothes_distance(a, b) for a in clothes_of(u) for m_ in look for b in clothes_of(m_)]
            cl = [x for x in cl if x is not None]
            fe = [float(_unit(a) @ _unit(b)) for a in emb_of(u) for m_ in look for b in emb_of(m_)]
            if cl and min(cl) < SAME_OUTFIT: look.add(u); joined[u] = 'the same clothes'
            elif fe and max(fe) >= SAME_SESSION and not (cl and min(cl) >= SAME_OUTFIT): look.add(u); joined[u] = 'the same photo session'
    if joined: notes.append(f"{len(joined)} more upload(s) count as the main reference too: " + ', '.join(sorted(set(joined.values()))))

    # ---- bodies: from the main look only --------------------------------------------------------------------------
    full = sorted([d for d in fams['full'] if d['source'] in look], key=lambda d: -(fam_score(d, 'full') + (0.5 if d['source'] == main else 0)))
    upper = sorted([d for d in fams['upper'] if d['source'] in look], key=lambda d: -(fam_score(d, 'upper') + (0.5 if d['source'] == main else 0)))
    others_with_body = {d['source'] for k in fams for d in fams[k]} - look
    if not full and not upper and others_with_body:
        notes.append('the main reference shows no body, so no body photo is used (other uploads may wear other clothes): '
                     '★ an upload that shows the body to use it')
    elif others_with_body:
        notes.append(f"body photos only from the main reference; {len(others_with_body)} other upload(s) with a body are left out so the clothes never mix")

    # ---- faces: the first from the main reference, then supporting faces (other uploads first, other angles) ---------
    faces = []
    mine = [c for c in cands if c['src'] in look]
    if mine: faces.append(max(mine, key=lambda c: c['score'] + (0.5 if c['yaw'] == 'front' else 0.0)))
    elif cands:
        notes.append('the main reference has no clear face: the face comes from the other uploads, as close crops')
    while len(faces) < max_faces:
        best = None
        for c in cands:
            if c in faces: continue
            if c['src'] not in look and not c.get('px'): continue          # a supporting face must be croppable to the face
            v = c['score']
            if c['src'] not in {f['src'] for f in faces}: v += 0.6
            if c['yaw'] not in {f['yaw'] for f in faces}: v += 0.5
            if any(c['emb'] and f['emb'] and float(_unit(c['emb']) @ _unit(f['emb'])) > DUPLICATE and c['yaw'] == f['yaw'] for f in faces): continue
            if best is None or v > best[0]: best = (v, c)
        if not best: break
        faces.append(best[1])
    support = {f['rid'] for f in faces if f['src'] not in look}
    return {'main': main, 'main_why': main_why, 'faces': [f['rid'] for f in faces], 'support': support,
            'face_info': {f['rid']: {'sim': f['sim'], 'yaw': f['yaw'], 'src': f['src']} for f in faces},
            'n_face_cands': len(cands), 'face_sources': len(sources), 'full': full, 'upper': upper,
            'outfit_sources': sorted(look, key=lambda u: (u != main, str(u))), 'look': look, 'notes': notes, 'centroid': centroid, 'n_sources': len(ups)}
