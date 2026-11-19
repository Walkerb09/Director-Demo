"""🧪 V0.9.60 experiment: the built carry-on frame, REDRAWN in the poses where the shot before ends (off by default).

The 🔗 built frame (studio_v080.carry_frame) puts each person's cut-out from YOUR photos where they were when the shot before
cuts, but in the pose of your photo. This experiment asks Qwen-Image-Edit (2511, full BF16, run in YOUR ComfyUI) to redraw
that built frame so everyone takes the pose they had at the cut:

  picture 1 = the built frame       (your originals only: faces, clothes, the room)
  picture 2 = stick figures         (SDPose reads the last frame of the shot before; only the skeleton goes in, never its pixels)

So identity still comes only from your originals; the generated take gives only where each limb is (the same "positions
only" rule the Qwen rewrites follow). The redrawn still IS an AI-drawn image: it is used only as frame 0 of that one take,
never as a reference, never as one of your originals, never as the base of another redraw (each one starts from a frame
built from your originals again). It is marked as generated so it can never be added back in.

Nothing here was run on real hardware when it was written: node and input names come from ComfyUI's core nodes
(nodes_qwen.py TextEncodeQwenImageEditPlus: clip, prompt, vae, image1..3) and the SDPose nodes the 🎭 movement feature uses;
readiness() checks them against YOUR ComfyUI's /object_info before anything is queued, and any problem is shown in plain words.
"""
from __future__ import annotations
import os, subprocess
from pathlib import Path

import studio_pose_v0921 as ps

HF_EDIT = 'https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/'
HF_IMAGE = 'https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/'
MODELS = {   # (ComfyUI models folder, file, download URL) - full precision, as for H3
    'edit': ('diffusion_models', 'qwen_image_edit_2511_bf16.safetensors', HF_EDIT + 'diffusion_models/qwen_image_edit_2511_bf16.safetensors'),
    'encoder': ('text_encoders', 'qwen_2.5_vl_7b.safetensors', HF_IMAGE + 'text_encoders/qwen_2.5_vl_7b.safetensors'),
    'vae': ('vae', 'qwen_image_vae.safetensors', HF_IMAGE + 'vae/qwen_image_vae.safetensors'),
    'sdpose': ps.MODELS['sdpose'], 'detector': ps.MODELS['detector'],
}
GB = {'edit': 40.9, 'encoder': 16.6, 'vae': 0.3, 'sdpose': 0.9, 'detector': 0.1}
NODE_INPUTS = {
    'UNETLoader': ['unet_name', 'weight_dtype'], 'CLIPLoader': ['clip_name', 'type'], 'VAELoader': ['vae_name'],
    'LoadImage': ['image'], 'ImageScaleToTotalPixels': ['image', 'upscale_method', 'megapixels'],
    'TextEncodeQwenImageEditPlus': ['clip', 'prompt', 'vae', 'image1', 'image2'],
    'ModelSamplingAuraFlow': ['model', 'shift'], 'CFGNorm': ['model', 'strength'],
    'VAEEncode': ['pixels', 'vae'], 'KSampler': ['model', 'seed', 'steps', 'cfg', 'sampler_name', 'scheduler', 'positive', 'negative', 'latent_image', 'denoise'],
    'VAEDecode': ['samples', 'vae'], 'SaveImage': ['images', 'filename_prefix'],
    'CheckpointLoaderSimple': ['ckpt_name'], 'RTDETR_detect': ps.NODE_INPUTS['RTDETR_detect'],
    'SDPoseKeypointExtractor': ps.NODE_INPUTS['SDPoseKeypointExtractor'], 'SDPoseDrawKeypoints': ps.NODE_INPUTS['SDPoseDrawKeypoints'],
}
FILE_INPUT = {'edit': ('UNETLoader', 'unet_name'), 'encoder': ('CLIPLoader', 'clip_name'), 'vae': ('VAELoader', 'vae_name'),
              'sdpose': ('CheckpointLoaderSimple', 'ckpt_name'), 'detector': ('UNETLoader', 'unet_name')}
STEPS, CFG, SHIFT, MEGAPIXELS = 40, 4.0, 3.1, 1.0       # Qwen-Image-Edit's own defaults (40 steps, CFG 4)
WAIT_S = 25 * 60                                         # the redraw gets this long; then the shot goes on the built frame
LOOK_WAIT_S = 30 * 60                                    # 👁 Qwen gets this long to look at a redraw; then the shot goes on the built frame


def readiness(fetch):
    """fetch(class) -> /object_info entry or None. -> {'ready', 'problems', 'missing_models', 'missing_files', 'download_gb'}."""
    problems, missing = [], []
    for node, keys in NODE_INPUTS.items():
        info = fetch(node)
        if not info:
            problems.append(f'node {node} is not in this ComfyUI (update ComfyUI: git pull, then restart)'); continue
        spec = info.get('input') or {}
        have = set(spec.get('required') or {}) | set(spec.get('optional') or {})
        if have:
            for k in keys:
                if k not in have: problems.append(f'{node} has no input "{k}" in this ComfyUI version (please send this to Claude)')
    def options(node, key):
        try:
            spec = (fetch(node) or {})['input']
            v = (spec.get('required') or {}).get(key) or (spec.get('optional') or {}).get(key)
            return list(v[0]) if isinstance(v[0], list) else list((v[1] or {}).get('options') or [])
        except Exception: return []
    for k, (node, key) in FILE_INPUT.items():
        if MODELS[k][1] not in options(node, key): missing.append(k)
    return {'ready': not problems and not missing, 'problems': problems, 'missing_models': missing,
            'missing_files': [f'ComfyUI/models/{MODELS[k][0]}/{MODELS[k][1]}' for k in missing],
            'download_gb': round(sum(GB[k] for k in missing), 1)}


def download(missing, comfy_root, timeout=6 * 3600):
    """Fetch the missing models into ComfyUI/models (resumes; restart ComfyUI afterwards so it lists them)."""
    done = []
    for k in missing:
        folder, name, url = MODELS[k]; dest = Path(comfy_root) / 'models' / folder; dest.mkdir(parents=True, exist_ok=True)
        part = dest / (name + '.part')
        r = subprocess.run(['wget', '-c', '-q', '-O', str(part), url], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0: raise RuntimeError(f'REDRAW_MODEL_DOWNLOAD_FAILED: {name}: ' + (r.stderr or '').strip()[-200:])
        os.replace(part, dest / name); done.append(name)
    return done


def prompt_text(names_left_to_right):
    """The instruction (positive only). Pose from picture 2, everything else from picture 1."""
    who = ' and '.join(names_left_to_right) if names_left_to_right else 'the people'
    order = (f" From left to right, the stick figures in picture 2 are {', then '.join(names_left_to_right)}." if len(names_left_to_right) > 1 else '')
    return (f"Picture 1 is a photo of {who} in a room. Picture 2 shows stick-figure poses.{order} "
            f"Change only how they hold their bodies: each person in picture 1 takes the pose of their stick figure in picture 2 "
            f"(head, arms, hands, legs and which way they face), staying in the same place in the frame and the same size. "
            f"Keep everything else exactly as in picture 1: each face, hair, skin tone, clothes and shoes, the room, the light "
            f"and the camera. One real, natural photograph with soft, even light; nobody floats: each person rests on the floor or furniture "
            f"the way their stick figure does (standing, kneeling or sitting).")


def api(built_name, cut_name, prompt, *, seed, prefix):
    """/prompt nodes: picture 1 = the built frame (scaled to ~1 megapixel), picture 2 = SDPose stick figures of the cut frame;
    Qwen-Image-Edit 2511 redraws picture 1 -> SaveImage(prefix). The cut frame only ever reaches the pose nodes."""
    d = {
        '1': {'class_type': 'LoadImage', 'inputs': {'image': built_name}},
        '2': {'class_type': 'ImageScaleToTotalPixels', 'inputs': {'image': ['1', 0], 'upscale_method': 'lanczos', 'megapixels': MEGAPIXELS}},
        '3': {'class_type': 'LoadImage', 'inputs': {'image': cut_name}},
        '4': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': MODELS['sdpose'][1]}},
        '5': {'class_type': 'UNETLoader', 'inputs': {'unet_name': MODELS['detector'][1], 'weight_dtype': 'default'}},
        '6': {'class_type': 'RTDETR_detect', 'inputs': {'model': ['5', 0], 'image': ['3', 0], 'threshold': 0.5, 'class_name': 'person',
                                                        'max_detections': ps.MAX_PEOPLE}},
        '7': {'class_type': 'SDPoseKeypointExtractor', 'inputs': {'model': ['4', 0], 'vae': ['4', 2], 'image': ['3', 0], 'batch_size': 1, 'bboxes': ['6', 0]}},
        '8': {'class_type': 'SDPoseDrawKeypoints', 'inputs': {'keypoints': ['7', 0], **ps.DRAW}},
        '10': {'class_type': 'UNETLoader', 'inputs': {'unet_name': MODELS['edit'][1], 'weight_dtype': 'default'}},
        '11': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['10', 0], 'shift': SHIFT}},
        '12': {'class_type': 'CFGNorm', 'inputs': {'model': ['11', 0], 'strength': 1.0}},
        '13': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': MODELS['encoder'][1], 'type': 'qwen_image'}},
        '14': {'class_type': 'VAELoader', 'inputs': {'vae_name': MODELS['vae'][1]}},
        '15': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['13', 0], 'prompt': prompt, 'vae': ['14', 0], 'image1': ['2', 0], 'image2': ['8', 0]}},
        '16': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['13', 0], 'prompt': '', 'vae': ['14', 0], 'image1': ['2', 0], 'image2': ['8', 0]}},
        '17': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['2', 0], 'vae': ['14', 0]}},
        '18': {'class_type': 'KSampler', 'inputs': {'model': ['12', 0], 'seed': int(seed) % (2 ** 63), 'steps': STEPS, 'cfg': CFG, 'sampler_name': 'euler',
                                                    'scheduler': 'simple', 'positive': ['15', 0], 'negative': ['16', 0], 'latent_image': ['17', 0], 'denoise': 1.0}},
        '19': {'class_type': 'VAEDecode', 'inputs': {'samples': ['18', 0], 'vae': ['14', 0]}},
        '20': {'class_type': 'SaveImage', 'inputs': {'images': ['19', 0], 'filename_prefix': prefix}},
    }
    return d
