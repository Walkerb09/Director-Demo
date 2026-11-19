"""Keep captions, subtitles, watermarks and other on-screen text out of the references.

Detector: PP-OCRv3 text detection (OpenCV model zoo, Apache-2.0, models/text_detection_en_ppocrv3_2023may.onnx),
run as a plain ONNX net with DB post-processing done here (OpenCV's TextDetectionModel_DB wrapper is the likely
cause of a crash seen on Vast; this path is the one the face and cut-out models already use).
What counts as OVERLAY text (removed) vs text that belongs to the person (kept):
  - text that does not touch the person (outside their cut-out mask)            -> overlay (removed)
  - text ON the person: kept, because a jersey or a T-shirt print is part of who they are, UNLESS the video
    proves it is an overlay: it stays at the same place on screen while the person moves (captions don't move
    with the body; clothing text does)                                           -> overlay (removed)
Removal: stills are inpainted (cv2.inpaint, Telea); a reference clip gets the caption area filled from its
surroundings (ffmpeg delogo), or is skipped when a caption covers the face.
Measured on the user's real footage: jersey lettering is detected as text ON the person and
kept; burned-in two-line captions and a corner @username are found and removed.
"""
from __future__ import annotations
from pathlib import Path
import cv2, numpy as np
from studio_netlock_v092 import NET_LOCK

HERE = Path(__file__).resolve().parent
MODEL = HERE / 'models' / 'text_detection_en_ppocrv3_2023may.onnx'
_det = None

def available(): return MODEL.is_file()

def _model():
    """The PP-OCRv3 detector as a plain ONNX net (the same loading path as the face and cut-out models).
    OpenCV's TextDetectionModel_DB wrapper is the likely cause of a segmentation fault seen on Vast, so the
    DB post-processing is done here instead: probability map -> threshold -> boxes, expanded like DB's unclip."""
    global _det
    with NET_LOCK:
        if _det is None: _det = cv2.dnn.readNetFromONNX(str(MODEL))
        return _det

BIN_THR, BOX_THR, UNCLIP, MAX_BOXES = 0.3, 0.5, 2.0, 200

def detect(im, side=736):
    """Text boxes [x, y, w, h] in image pixels."""
    h, w = im.shape[:2]; s = side / max(h, w)
    W, H = max(32, int(round(w * s / 32)) * 32), max(32, int(round(h * s / 32)) * 32)
    blob = cv2.dnn.blobFromImage(im, 1.0 / 255.0, (W, H), (122.67891434, 116.66876762, 104.00698793), swapRB=True)
    with NET_LOCK:
        net = _model(); net.setInput(blob); o = np.asarray(net.forward())
        prob = o.reshape(o.shape[-2:]).astype(np.float32).copy()          # OpenCV 4 gives (1,1,H,W), OpenCV 5 (H,W)
    mask = (prob > BIN_THR).astype(np.uint8)
    cs, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cs[:MAX_BOXES]:
        x, y, bw, bh = cv2.boundingRect(c)
        (_, _), (rw, rh), _ = cv2.minAreaRect(c)
        if min(rw, rh) < 3: continue
        region = prob[y:y + bh, x:x + bw][mask[y:y + bh, x:x + bw] > 0]
        if not region.size or float(region.mean()) < BOX_THR: continue
        d = (rw * rh) * UNCLIP / (2 * (rw + rh))                           # DB "unclip": grow the box by area*ratio/perimeter
        x0, y0 = max(0, int((x - d) * w / W)), max(0, int((y - d) * h / H))
        x1, y1 = min(w, int(round((x + bw + d) * w / W))), min(h, int(round((y + bh + d) * h / H)))
        if x1 - x0 >= 6 and y1 - y0 >= 6: out.append([x0, y0, x1 - x0, y1 - y0])
    return out

def _iou(a, b):
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax2, bx2) - max(a[0], b[0])); ih = max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih; return inter / float(a[2] * a[3] + b[2] * b[3] - inter + 1e-6)

def _on_person(box, mask):
    if mask is None: return False
    x, y, w, h = box; m = mask[y:y + h, x:x + w]
    return m.size > 0 and float((m > 0.5).mean()) >= 0.3

def _gray(im, b):
    x, y, w, h = b; p = im[y:y + h, x:x + w]
    return cv2.cvtColor(p, cv2.COLOR_BGR2GRAY).astype(np.float32) if p.size else None

def _ncc(a, b):
    if a is None or b is None or a.shape != b.shape or a.size < 64: return 0.0
    a = a - a.mean(); b = b - b.mean(); d = float(np.sqrt((a * a).sum() * (b * b).sum())) + 1e-6
    return float((a * b).sum() / d)

def _overlaps(b, box, frac=0.3):
    if not box: return False
    x, y, w, h = b; fx, fy, fw, fh = box
    iw = max(0, min(x + w, fx + fw) - max(x, fx)); ih = max(0, min(y + h, fy + fh) - max(y, fy))
    return iw * ih >= frac * min(w * h, fw * fh)

def classify(frames, faces_per_frame, masks, boxes=None):
    """Per frame: (overlay boxes to remove, boxes kept). Identity first:
    - a box over a face is never touched;
    - a box off the person is overlay (captions, watermarks, usernames, signs);
    - a box ON the person (a jersey, a print) is overlay only with strong proof: its exact pixels stay fixed on
      screen while the person's body clearly moves (>= 2 such frames, and most of them). Otherwise kept."""
    boxes = boxes if boxes is not None else [detect(im) for im in frames]
    W = frames[0].shape[1] if frames else 1
    cent = []
    for mk in masks:
        if mk is None: cent.append(None); continue
        ys, xs = np.nonzero(mk > 0.5); cent.append((float(xs.mean()), float(ys.mean())) if xs.size else None)
    out = []
    for i, bs in enumerate(boxes):
        over, kept = [], []
        face = (faces_per_frame[i] or {}).get('box') if faces_per_frame[i] else None
        for b in bs:
            if _overlaps(b, face): kept.append(b); continue
            if not _on_person(b, masks[i]): over.append(b); continue
            here = _gray(frames[i], b); moved = static = 0
            for j in range(len(frames)):
                if j == i or cent[i] is None or cent[j] is None: continue
                if ((cent[j][0] - cent[i][0]) ** 2 + (cent[j][1] - cent[i][1]) ** 2) ** 0.5 < 0.05 * W: continue
                moved += 1
                if _ncc(here, _gray(frames[j], b)) > 0.85: static += 1
            (over if moved >= 2 and static >= 2 and static >= 0.6 * moved else kept).append(b)
        out.append((over, kept))
    return out

def inpaint(im, boxes, pad=6):
    if not boxes: return im
    m = np.zeros(im.shape[:2], np.uint8)
    for x, y, w, h in boxes: cv2.rectangle(m, (max(0, x - pad), max(0, y - pad)), (x + w + pad, y + h + pad), 255, -1)
    return cv2.inpaint(im, m, 5, cv2.INPAINT_TELEA)

def clean_frames(frames, faces_fn, mask_fn):
    """frames: list of (t, BGR). Returns (cleaned frames, report). faces_fn(im) -> list of faces; mask_fn(im, face) -> mask."""
    if not frames or not available(): return frames, {'checked': False}
    ims = [im for _, im in frames]
    boxes = [detect(im) for im in ims]                                   # fast (~0.1 s/frame)
    fs = [faces_fn(im) for im in ims]                                    # needed anyway; returned for reuse
    track = [f[0] if len(f) == 1 else (max(f, key=lambda z: z['h']) if f else None) for f in fs]
    masks = []
    for im, f, b in zip(ims, track, boxes):
        if not b or f is None: masks.append(None); continue               # no text here: no mask needed
        try: masks.append(mask_fn(im, f))
        except Exception: masks.append(None)
    cls = classify(ims, track, masks, boxes)
    out = []; removed = kept = 0; union = []; over_person = []
    # clothing text is there in (almost) every frame; a caption over the person comes and goes, so a frame with MORE
    # text on the person than usual is avoided when choosing reference stills
    # (measured as text AREA on the person: a caption crossing a shirt print merges with it into one bigger box)
    on_counts = [sum(b[2] * b[3] for b in keep if _on_person(b, mk)) if mk is not None else 0 for (_, keep), mk in zip(cls, masks)]
    base = float(np.percentile([c for c, mk in zip(on_counts, masks) if mk is not None] or [0], 20))   # the clothing-only baseline
    usual = base * 1.25 + 400
    for (t, im), (over, keep), f, mk, c in zip(frames, cls, track, masks, on_counts):
        removed += len(over); kept += len(keep); union += over
        over_person.append(bool(mk is not None and (any(_on_person(b, mk) for b in over) or c > usual)))
        out.append((t, inpaint(im, over)))
    return out, {'checked': True, 'frames_with_overlay_text': sum(1 for o, _ in cls if o), 'overlay_boxes': removed,
                 'text_on_person_kept': kept, 'union': union, 'faces': track, 'all_faces': fs, 'caption_over_person': over_person}

def clip_filter(union_boxes, face_boxes, W, H, pad=8):
    """ffmpeg delogo filters for the caption areas of a reference clip (sample-frame coordinates scaled to the
    clip). Returns (filter string or '', covers_face)."""
    if not union_boxes: return '', False
    rects = []
    for x, y, w, h in union_boxes:
        r = [max(1, x - pad), max(1, y - pad), min(W - 2, x + w + pad), min(H - 2, y + h + pad)]
        for k in rects:
            if not (r[2] < k[0] or k[2] < r[0] or r[3] < k[1] or k[3] < r[1]):
                k[:] = [min(k[0], r[0]), min(k[1], r[1]), max(k[2], r[2]), max(k[3], r[3])]; break
        else: rects.append(r)
    covers = False
    for fb in face_boxes:
        if not fb: continue
        fx, fy, fw, fh = fb
        for r in rects:
            if not (r[2] < fx or fx + fw < r[0] or r[3] < fy or fy + fh < r[1]): covers = True
    flt = ','.join(f'delogo=x={r[0]}:y={r[1]}:w={max(4, r[2] - r[0])}:h={max(4, r[3] - r[1])}' for r in rects if r[2] - r[0] >= 4 and r[3] - r[1] >= 4)
    return flt, covers
