"""📐 V0.9.69 Picture-true positions: the frame H3 really starts on, and where everyone is in it.

Why: a shot that opens (or ends) on one of your pictures got that picture as it is. H3's frame is 9:16 (768 x 1344); a
photo of another shape was fitted by the node itself (centre crop), so in a wide photo someone standing off-centre was cut
out of the first frame, while the Director still measured the WHOLE photo and told H3 "Maya is on the left". The words and
the first frame disagreed, and how well a shot was placed depended on the picture.

Now the Director makes the first frame itself and reads it:
  - fit: the picture is cropped to exactly 9:16 AROUND THE PEOPLE in it (every face, and as much of each body as fits),
    then sized to the frame. A crop of YOUR original; nothing is drawn (Clean-Chain). A picture that already is 9:16 is
    used as it is. When not everyone fits, the crop keeps the most people whole and says who is outside.
  - read: each person of the shot is found in that fitted frame (faces matched to their ORIGINAL photos) -> side of the
    frame and how near the camera, in the same words the 📍 dots use.
  - say: the prompt tells H3 who is who in the first frame ("In that first frame, <Subject 1> (Maya) is the person on the
    left ..."), so it does not have to guess which reference belongs to which person in the picture.
  - reconcile: dots that agree with the picture take the picture's exact spot; dots that differ get a ⏱ lead-in (H3 moves
    everyone from the picture to your dots, the movie cuts in after), with a warning when that means crossing; the shot's
    text is checked against the picture too.
  - check: every take is measured against the positions asked (where each person really is after the lead-in and at the
    end), which ranks takes and lets a film make another take when everyone is in the wrong place.

Pure numpy/OpenCV + the positions module; the Studio supplies faces."""
from __future__ import annotations
import cv2, numpy as np
import studio_positions_v0945 as pm

W, H = 768, 1344
TOL = 0.03                  # this close to the frame's shape (a 9:16 phone picture is 1.6% off 768x1344): used as it is
SIDE_OFF = 0.22             # further than this across the frame from where it was asked = the wrong side
SWAP_GAP = 0.12             # two people asked at least this far apart: their left/right order must hold


def body_box(face, w, h):
    """A person's rough extent from their face box [x, y, fw, fh] in pixels: shoulders to about the feet (standing)."""
    x, y, fw, fh = [float(v) for v in face[:4]]
    return [max(0.0, x - 1.1 * fw), max(0.0, y - 0.6 * fh), min(float(w), x + 2.1 * fw), min(float(h), y + 7.5 * fh)]


def _cover(a0, a1, b0, b1):
    """Share of [a0, a1] inside [b0, b1]."""
    return max(0.0, min(a1, b1) - max(a0, b0)) / max(1e-6, a1 - a0)


def fit_rect(w, h, faces, target=(W, H), tol=TOL):
    """The 9:16 crop of a w x h picture that keeps its people. faces: [[x, y, fw, fh], ...] in pixels.
    -> {'rect': [x0, y0, cw, ch], 'as_is': bool, 'outside': [indices of faces not whole in the frame],
        'bodies_cut': [indices whose body is less than 85% in the frame], 'scale': frame px per picture px}"""
    A = target[0] / target[1]; a = w / h
    faces = [[float(v) for v in f[:4]] for f in faces or []]
    if abs(a / A - 1) <= tol:
        return {'rect': [0, 0, int(w), int(h)], 'as_is': True, 'outside': [], 'bodies_cut': [], 'scale': target[0] / w}
    wide = a > A
    cw, ch = (int(round(h * A)), int(h)) if wide else (int(w), int(round(w / A)))
    span, size = (w, cw) if wide else (h, ch)
    lo = lambda f: f[0] if wide else f[1]
    hi = lambda f: (f[0] + f[2]) if wide else (f[1] + f[3])
    bodies = [body_box(f, w, h) for f in faces]
    blo = lambda b: b[0] if wide else b[1]
    bhi = lambda b: b[2] if wide else b[3]
    def score(p0):
        p1 = p0 + size
        whole = sum(1 for f in faces if _cover(lo(f), hi(f), p0, p1) > 0.999)
        body = sum(_cover(blo(b), bhi(b), p0, p1) for b in bodies)
        return (whole, round(body, 3), -abs((p0 + size / 2) - span / 2))     # most faces whole, then most body, then nearest the middle
    cands = {(span - size) / 2.0}
    if faces:
        u0, u1 = min(lo(f) for f in faces), max(hi(f) for f in faces)
        b0, b1 = min(blo(b) for b in bodies), max(bhi(b) for b in bodies)
        cands |= {(u0 + u1) / 2 - size / 2, (b0 + b1) / 2 - size / 2, b0, b1 - size, u0 - 0.04 * span, u1 + 0.04 * span - size}
        if not wide: cands.add(u0 - 0.9 * max(f[3] for f in faces))          # a tall picture: headroom above the highest face
        for f in faces: cands |= {lo(f) - 0.5 * (hi(f) - lo(f)), hi(f) + 0.5 * (hi(f) - lo(f)) - size, (lo(f) + hi(f)) / 2 - size / 2}
    cands = {float(min(max(0.0, c), span - size)) for c in cands}
    p0 = int(round(max(cands, key=score))); p0 = min(max(0, p0), span - size)
    p1 = p0 + size
    outside = [i for i, f in enumerate(faces) if _cover(lo(f), hi(f), p0, p1) <= 0.999]
    cut = [i for i, b in enumerate(bodies) if i not in outside and _cover(blo(b), bhi(b), p0, p1) < 0.85]
    rect = [p0, 0, cw, ch] if wide else [0, p0, cw, ch]
    return {'rect': rect, 'as_is': False, 'outside': outside, 'bodies_cut': cut, 'scale': target[0] / cw}


def fit_image(im, rect, target=(W, H)):
    x0, y0, cw, ch = [int(v) for v in rect]
    crop = im[y0:y0 + ch, x0:x0 + cw]
    if crop.shape[1] == target[0] and crop.shape[0] == target[1]: return crop.copy()
    return cv2.resize(crop, target, interpolation=cv2.INTER_AREA if crop.shape[1] > target[0] else cv2.INTER_LANCZOS4)


def shape_words(w, h):
    a = w / h
    if a > 1.15: return 'wide (landscape)'
    if a > 0.9: return 'square'
    if a > W / H * (1 + TOL): return 'less tall than the frame'
    return 'taller than the frame'


# ---- words -----------------------------------------------------------------------------------------------------------
def where(row):
    return f"{pm.side(row['x'])}, {pm.depth(row['y'])}"


def bind_sentence(rows, label=lambda n: n, moment='first'):
    """Who is who in the pinned picture: 'In that first frame, <Subject 1> (Maya) is the person on the left of the frame, in
    the foreground, close to the camera; <Subject 2> (Grandpa) is the person on the right of the frame, ...'."""
    rows = [r for r in rows or [] if r.get('who')]
    if not rows: return ''
    lead = 'In that first frame, ' if moment == 'first' else 'In that last frame, '
    return lead + '; '.join(f"{label(r['who'])} is the person {where(r)}" for r in sorted(rows, key=lambda r: r['x'])) + '.'


def differences(asked, seen):
    """Your start dots against where the picture has everyone. -> {'moves': [(who, asked row, seen row)], 'crossing': bool}.
    A move = another side of the frame (more than SIDE_OFF across) or foreground against background."""
    by = {r['who']: r for r in seen or []}
    moves = []
    for r in asked or []:
        s = by.get(r['who'])
        if not s: continue
        far = pm.depth(r['y']) != pm.depth(s['y']) and 'middle' not in (pm.depth(r['y']) + pm.depth(s['y']))
        if abs(r['x'] - s['x']) > SIDE_OFF or far: moves.append((r['who'], r, s))
    crossing = False
    a = [r for r in asked or [] if r['who'] in by]
    if len(a) == 2 and abs(a[0]['x'] - a[1]['x']) >= SWAP_GAP:
        s0, s1 = by[a[0]['who']], by[a[1]['who']]
        crossing = abs(s0['x'] - s1['x']) >= 0.05 and ((a[0]['x'] < a[1]['x']) != (s0['x'] < s1['x']))
    return {'moves': moves, 'crossing': crossing}


def snap(asked, seen):
    """Dots that agree with the picture take the picture's exact spot (posture, facing and what they're on stay yours)."""
    by = {r['who']: r for r in seen or []}
    return [{**r, 'x': by[r['who']]['x'], 'y': by[r['who']]['y']} if r['who'] in by else r for r in asked or []]


def text_side_clashes(text, seen):
    """The shot's own words against the picture: 'Maya ... on the left' while the picture has her on the right."""
    import re
    out = []
    sents = [x for x in re.split(r'(?<=[.!?])\s+', re.sub(r'"[^"]*"', ' ', str(text or ''))) if x.strip()]
    for r in seen or []:
        first = next((x for x in sents if re.search(r'\b' + re.escape(r['who']) + r'\b', x)), None)
        if not first: continue
        k = re.search(r'\b' + re.escape(r['who']) + r'\b', first); seg = first[k.start():]
        other = next((o['who'] for o in seen if o['who'] != r['who']), None)
        if other:
            j = re.search(r'\b' + re.escape(other) + r'\b', seg[len(r['who']):])
            if j: seg = seg[:len(r['who']) + j.start()]
        m = re.search(r'\b(?:on|to|at) the (?:far )?(left|right)\b', seg, re.I)
        if m and ((m.group(1).lower() == 'left' and r['x'] > 0.6) or (m.group(1).lower() == 'right' and r['x'] < 0.4)):
            out.append(f"the text puts {r['who']} on the {m.group(1).lower()}, but in the opening picture {r['who']} is "
                       f"{pm.side(r['x'])}")
    return out


# ---- a take against the positions asked ---------------------------------------------------------------------------------
STANDING = ('standing', 'walking', 'leaning')

def judge(asked, seen, moment):
    """One moment of a take (after the lead-in / at the end) against the dots. seen: {who: {'x', 'y'}} (only people clearly
    recognised). -> list of plain problems; [] = as asked, or nobody could be judged."""
    out = []
    rows = [r for r in asked or [] if r['who'] in seen and r.get('facing') != 'away']
    when = 'at the start' if moment == 'start' else 'at the end'
    swapped = False
    if len(rows) == 2 and abs(rows[0]['x'] - rows[1]['x']) >= SWAP_GAP:
        s0, s1 = seen[rows[0]['who']], seen[rows[1]['who']]
        if abs(s0['x'] - s1['x']) >= 0.05 and ((rows[0]['x'] < rows[1]['x']) != (s0['x'] < s1['x'])):
            l, r = sorted(rows, key=lambda q: q['x'])
            out.append(f"{when} {l['who']} and {r['who']} are on swapped sides (asked: {l['who']} left of {r['who']})"); swapped = True
    for r in rows:
        s = seen[r['who']]
        if not swapped and abs(r['x'] - s['x']) > SIDE_OFF:
            out.append(f"{when} {r['who']} is {pm.side(s['x'])} (asked: {pm.side(r['x'])})")
        elif r.get('posture', 'standing') in STANDING:
            da, ds = pm.depth(r['y']), pm.depth(s['y'])
            if da != ds and 'middle' not in (da + ds):
                out.append(f"{when} {r['who']} is {ds.split(',')[0]} (asked: {da.split(',')[0]})")
    return out
