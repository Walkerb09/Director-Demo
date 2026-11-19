"""🎭 Movement from YOUR video (V0.9.21): H3 pose control inside the Director.

One of your own videos (a "movement video" you add, attested as yours) is read by SDPose (ComfyUI core) into a stick-figure
video, and the MiniMax H3 Fun ControlNet Union steers the take so people move like that. Only the stick figures reach H3,
never your video's pixels; appearance still comes only from your original photos.

Clean-Chain: a movement video goes through the same gate as every original (never a file from ComfyUI/output, never a
generated take or render, never a video carrying a generation workflow, never pixels of a take).

Nothing here was run on real H3 hardware when it was written: the node and input names come from ComfyUI's SDPose nodes
and the MiniMax H3 Fun ControlNet as documented, and `readiness()` checks them against YOUR ComfyUI's /object_info before
the feature is offered (a mismatch is shown in plain words, nothing is queued).
"""
from __future__ import annotations
import hashlib, json, os, subprocess
from pathlib import Path

import studio_posebuild_v0926 as pb
MODELS = {   # (ComfyUI models folder, file, download URL)
    # V0.9.26: BUILT on your machine (see studio_posebuild_v0926): Comfy-Org's pruned files use the "curve basis" AdaLN
    # of the PRUNED base models and ComfyUI refuses them with the full BF16 H3 ("adaln width 8 ... 2688")
    'controlnet': ('model_patches', pb.OUT_NAME, None),
    'sdpose': ('checkpoints', 'sdpose_wholebody_fp16.safetensors',
               'https://huggingface.co/Comfy-Org/SDPose/resolve/main/checkpoints/sdpose_wholebody_fp16.safetensors'),
    'detector': ('diffusion_models', 'rt_detr_v4-x-hgnet_fp16.safetensors',
                 'https://huggingface.co/Comfy-Org/SDPose/resolve/main/diffusion_models/rt_detr_v4-x-hgnet_fp16.safetensors'),
}
SIZE_GB = 23        # worst case on disk: Comfy-Org's 2.0 file (8.4 GB) + the built full-AdaLN file (~13.6 GB) + SDPose / RT-DETR
GB = {'comfy_controlnet': 8.4, 'full_adaln': 5.2, 'built': 13.6, 'sdpose_detector': 1.0}
# every pose node with the inputs the Director sets (checked against /object_info by readiness())
NODE_INPUTS = {
    'ModelPatchLoader': ['name'],
    'MiniMaxH3FunControlNetApply': ['model', 'model_patch', 'vae', 'strength', 'start_percent', 'end_percent', 'control_video'],
    'CheckpointLoaderSimple': ['ckpt_name'],
    'RTDETR_detect': ['model', 'image', 'threshold', 'class_name', 'max_detections'],
    'SDPoseKeypointExtractor': ['model', 'vae', 'image', 'batch_size', 'bboxes'],
    'SDPoseDrawKeypoints': ['keypoints', 'draw_body', 'draw_hands', 'draw_face', 'draw_feet', 'stick_width', 'face_point_size', 'score_threshold', 'draw_head'],
}
DRAW = {'draw_body': True, 'draw_hands': True, 'draw_face': True, 'draw_feet': True, 'stick_width': 4, 'face_point_size': 2,
        'score_threshold': 0.5, 'draw_head': True}
STRENGTH = (0.2, 1.0)        # higher strengths are what let a control map bleed into the picture
DEFAULT_STRENGTH = 0.6
END_PERCENT = 0.6            # the stick figures steer only the first 60% of sampling (where poses are laid out); the
                             # last steps paint detail freely, so the lines have nothing to show through
MAX_PEOPLE = 2

def readiness(fetch):
    """fetch(class) -> /object_info entry or None. {'ready', 'problems', 'missing_models'} for THIS ComfyUI."""
    problems, missing = [], []
    for node, keys in NODE_INPUTS.items():
        info = fetch(node)
        if not info:
            problems.append(f'node {node} is not in this ComfyUI (update ComfyUI: git pull, then restart)'); continue
        spec = info.get('input') or {}
        have = set((spec.get('required') or {})) | set((spec.get('optional') or {}))
        if have:
            for k in keys:
                if k not in have: problems.append(f'{node} has no input "{k}" in this ComfyUI version (please send this to Claude)')
    def options(node, key):
        try:
            spec = (fetch(node) or {})['input']
            v = (spec.get('required') or {}).get(key) or (spec.get('optional') or {}).get(key)
            return list(v[0]) if isinstance(v[0], list) else list((v[1] or {}).get('options') or [])
        except Exception: return []
    for k, (node, key) in {'controlnet': ('ModelPatchLoader', 'name'), 'sdpose': ('CheckpointLoaderSimple', 'ckpt_name'),
                           'detector': ('UNETLoader', 'unet_name')}.items():
        if MODELS[k][1] not in options(node, key): missing.append(k)
    have_comfy = pb.COMFY_NAME in options('ModelPatchLoader', 'name')
    dl = disk = 0.0
    if 'controlnet' in missing and MODELS['controlnet'][2]:        # V0.9.70 demo set: just Comfy-Org's file
        dl += GB['comfy_controlnet']; disk += GB['comfy_controlnet']
    elif 'controlnet' in missing:
        dl += GB['full_adaln'] + (0 if have_comfy else GB['comfy_controlnet'])
        disk += GB['built'] + (0 if have_comfy else GB['comfy_controlnet'])
    if {'sdpose', 'detector'} & set(missing): dl += GB['sdpose_detector']; disk += GB['sdpose_detector']
    return {'ready': not problems and not missing, 'problems': problems, 'missing_models': missing,
            'missing_files': [f'ComfyUI/models/{MODELS[k][0]}/{MODELS[k][1]}' for k in missing], 'download_gb': round(dl),
            'disk_gb': round(disk), 'builds_controlnet': 'controlnet' in missing and not MODELS['controlnet'][2]}

def download(missing, comfy_root, timeout=6 * 3600):
    """Fetch the missing pose models into ComfyUI/models (resumes; a restart of ComfyUI makes them visible)."""
    done = []
    for k in missing:
        folder, name, url = MODELS[k]; dest = Path(comfy_root) / 'models' / folder; dest.mkdir(parents=True, exist_ok=True)
        if k == 'controlnet' and url is None:              # the full BF16 base: built; the pruned demo base: downloaded as it is
            done.append(build_controlnet(dest, timeout=timeout)); continue
        part = dest / (name + '.part')
        r = subprocess.run(['wget', '-c', '-q', '-O', str(part), url], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0: raise RuntimeError(f'POSE_MODEL_DOWNLOAD_FAILED: {name}: ' + (r.stderr or '').strip()[-200:])
        os.replace(part, dest / name); done.append(name)
    return done

def build_controlnet(dest, *, timeout=6 * 3600, log=print):
    """The pose ControlNet for the full BF16 H3: Comfy-Org's 2.0 file (downloaded if you don't have it) + the full AdaLN
    layers from Alibaba's original (only those bytes). Comfy-Org's file is kept (other workflows may use it)."""
    dest = Path(dest); src = dest / pb.COMFY_NAME
    if not src.is_file():
        part = dest / (pb.COMFY_NAME + '.part')
        r = subprocess.run(['wget', '-c', '-q', '-O', str(part), pb.COMFY_URL], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0: raise RuntimeError(f'POSE_MODEL_DOWNLOAD_FAILED: {pb.COMFY_NAME}: ' + (r.stderr or '').strip()[-200:])
        os.replace(part, src)
    try:
        rep = pb.build(src, dest / pb.OUT_NAME, log=log)
    except pb.BuildError as e:
        raise RuntimeError(f'POSE_CONTROLNET_BUILD_FAILED: {e}')
    return Path(rep['file']).name

def prepare_control(src, dest, *, frames, start=0.0, width=768, height=1344):
    """YOUR movement video -> exactly `frames` frames at 24 fps, the take's size (centre-cropped), no sound. Shorter than the
    take: the last pose is held. Returns (dest, sha256, frames it really had)."""
    dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + '.partial.mp4')
    vf = (f'fps=24,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},'
          f'tpad=stop_mode=clone:stop_duration={frames / 24 + 1:.2f},format=yuv420p')
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y']
    if start: cmd += ['-ss', f'{float(start):.3f}']
    cmd += ['-i', str(src), '-an', '-vf', vf, '-frames:v', str(int(frames)), '-c:v', 'libx264', '-crf', '14', '-preset', 'fast',
            '-map_metadata', '-1', str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0: raise RuntimeError('MOVEMENT_VIDEO_COULD_NOT_BE_PREPARED: ' + (r.stderr or '').strip()[-200:])
    os.replace(tmp, dest)
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    real = video_frames(src) - int(round(float(start or 0) * 24))
    return dest, sha, max(0, real)

def video_frames(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=nb_frames,avg_frame_rate:format=duration',
                        '-of', 'json', str(path)], capture_output=True, text=True, timeout=60)
    try:
        j = json.loads(r.stdout); dur = float(j['format']['duration'])
        return int(round(dur * 24))
    except Exception: return 0

def pose_api(control_name, *, prefix=None, nid=900):
    """The pose branch as /prompt nodes: LoadVideo -> GetVideoComponents -> RT-DETR (people) -> SDPose -> stick figures.
    With prefix: also saved as a preview video (VideoHelperSuite). Returns (api dict, id of the stick-figure node)."""
    i = lambda k: str(nid + k)
    api = {
        i(2): {'class_type': 'LoadVideo', 'inputs': {'file': control_name}},
        i(3): {'class_type': 'GetVideoComponents', 'inputs': {'video': [i(2), 0]}},
        i(5): {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': MODELS['sdpose'][1]}},
        i(7): {'class_type': 'UNETLoader', 'inputs': {'unet_name': MODELS['detector'][1], 'weight_dtype': 'default'}},
        i(8): {'class_type': 'RTDETR_detect', 'inputs': {'model': [i(7), 0], 'image': [i(3), 0], 'threshold': 0.5, 'class_name': 'person',
                                                         'max_detections': MAX_PEOPLE}},
        i(9): {'class_type': 'SDPoseKeypointExtractor', 'inputs': {'model': [i(5), 0], 'vae': [i(5), 2], 'image': [i(3), 0], 'batch_size': 16,
                                                                   'bboxes': [i(8), 0]}},
        i(6): {'class_type': 'SDPoseDrawKeypoints', 'inputs': {'keypoints': [i(9), 0], **DRAW}},
    }
    if prefix:
        api[i(10)] = {'class_type': 'VHS_VideoCombine', 'inputs': {'images': [i(6), 0], 'frame_rate': 24.0, 'loop_count': 0, 'filename_prefix': prefix,
                                                                   'format': 'video/h264-mp4', 'pix_fmt': 'yuv420p', 'crf': 19, 'save_metadata': False,
                                                                   'trim_to_audio': False, 'pingpong': False, 'save_output': True}}
    return api, i(6)


# ---- did stick figures leak into the take? (measured on every take made with 🎭 movement) ----------------------
LINE_FRAME = 0.0015       # share of a frame in thin, bright, saturated line segments. Measured: real takes <= 0.0009,
                          # the same takes with SDPose-style stick figures drawn in >= 0.0022 (also half-faded)
LINE_FRAMES = 2           # this many of the sampled frames above LINE_FRAME -> flagged

def line_score(im):
    """Thin, bright, saturated, elongated pieces (what drawn stick figures look like) as a share of the frame."""
    import cv2, numpy as np
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 1] > 150) & (hsv[..., 2] > 140)).astype(np.uint8)
    op = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    thin = m & (1 - op)                                     # what a 9-px disc can't fill: lines, not coloured areas
    n, _, stats, _ = cv2.connectedComponentsWithStats(thin, 8)
    tot = 0
    for i in range(1, n):
        x, y, w, h, a = stats[i]; L = max(w, h)
        if L >= 25 and a / L <= 8: tot += int(a)
    return tot / m.size

def leaked_lines(path, frames=None, n=8):
    """{'frames_with_lines', 'sampled', 'max'} over n evenly spaced frames of the used part of the take."""
    import cv2
    cap = cv2.VideoCapture(str(path)); total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    lo, hi = frames if frames else (0, total)
    hi = min(hi, total); vals = []
    try:
        for k in range(n):
            idx = lo + int((k + 0.5) * max(1, hi - lo) / n)
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx); ok, im = cap.read()
            if ok: vals.append(line_score(im))
    finally: cap.release()
    return {'frames_with_lines': sum(v > LINE_FRAME for v in vals), 'sampled': len(vals), 'max': round(max(vals), 5) if vals else None}

def lines_flagged(m):
    return bool(m) and m.get('frames_with_lines', 0) >= LINE_FRAMES


def _profile_controlnet():
    """V0.9.70 the 24 GB demo set runs the PRUNED base, which takes Comfy-Org's pruned ControlNet as it is (no build)."""
    try:
        import studio_profile_v0970 as pf
        cn = pf.get().get('controlnet')
    except Exception: cn = None
    if cn: return ('model_patches', cn, 'https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/model_patches/' + cn)
    return ('model_patches', pb.OUT_NAME, None)


class _Models(dict):
    def __getitem__(self, k): return _profile_controlnet() if k == 'controlnet' else dict.__getitem__(self, k)
    def get(self, k, d=None): return self[k] if k in self else d
    def items(self): return [(k, self[k]) for k in self]
    def values(self): return [self[k] for k in self]


MODELS = _Models(MODELS)
