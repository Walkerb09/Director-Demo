"""V0.9.70 Model profiles: which H3 files this machine runs.

  full    your setup: the full BF16 H3 (66 GB), the BF16 text encoder (51.5 GB), the fp16 video VAE. 96 GB GPUs.
  lite24  a DEMO set for 24 GB GPUs (RTX 4090 / 3090 / A5000 / L4...), all from Comfy-Org/MiniMax-H3 and loaded by stock
          ComfyUI (no extra custom nodes): H3 Ref2VA pruned w6a8 (16 GB), the text encoder nvfp4_awq (15.7 GB; no
          Blackwell needed), the int8_convrot video VAE (2.8 GB), the same audio VAE. Qwen: the 8B only.
          Smaller files lose some detail and identity against the full set: it is for showing the Director, not for your
          films. The pose ControlNet uses Comfy-Org's pruned file as it is (it matches the pruned base; no build step).

Which one: DIRECTOR_PROFILE (the Vast template sets it), else the file the installer wrote (projects folder/_profile.json),
else what is in ComfyUI/models (the full BF16 H3 present -> full; only the lite file -> lite24), else full."""
from __future__ import annotations
import json, os
from pathlib import Path

PROFILES = {
    'full': {'label': 'Full BF16 H3 (96 GB GPUs)',
             'unet': 'minimax_h3_ref2va_bf16.safetensors', 'clip': 'qwen3vl_32b_minimax_h3_bf16.safetensors',
             'video_vae': 'minimax_h3_video_vae_fp16.safetensors', 'audio_vae': 'minimax_h3_audio_vae_fp32.safetensors',
             'controlnet': None, 'qwen_models': None, 'min_gpu_gb': 80},
    'lite24': {'label': '24 GB demo (pruned w6a8 H3, nvfp4 text encoder)',
               'unet': 'minimax_h3_ref2va_pruned_w6a8.safetensors', 'clip': 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
               'video_vae': 'minimax_h3_video_vae_int8_convrot.safetensors', 'audio_vae': 'minimax_h3_audio_vae_fp32.safetensors',
               'controlnet': 'minimax_h3_fun_controlnet_union_2.0_pruned_bf16.safetensors', 'qwen_models': ['8b'], 'min_gpu_gb': 20,
               'qwen_need_mib': {'8b': int(19.5 * 1024)}},
}
FILES = (('diffusion_models', 'unet'), ('text_encoders', 'clip'), ('vae', 'video_vae'), ('vae', 'audio_vae'))
_state = {'name': None, 'comfy_root': None, 'projects_root': None}


def init(comfy_root=None, projects_root=None):
    _state.update(comfy_root=comfy_root, projects_root=projects_root, name=None)
    return name()


def name():
    if _state['name']: return _state['name']
    if (Path(__file__).resolve().parent / 'PUBLIC_DEMO').is_file():  # the public 24 GB demo bundle
        _state['name'] = 'lite24'; return 'lite24'
    n = (os.environ.get('DIRECTOR_PROFILE') or '').strip().lower()
    if n not in PROFILES:
        n = None
        pr = _state.get('projects_root')
        if pr:
            try: n = json.loads((Path(pr) / '_profile.json').read_text()).get('profile')
            except (OSError, ValueError, AttributeError): n = None
        if n not in PROFILES:
            n = None; cr = _state.get('comfy_root')
            if cr:
                d = Path(cr) / 'models' / 'diffusion_models'
                if (d / PROFILES['full']['unet']).is_file(): n = 'full'
                elif (d / PROFILES['lite24']['unet']).is_file(): n = 'lite24'
    _state['name'] = n if n in PROFILES else 'full'
    return _state['name']


def get(): return PROFILES[name()]


def model_files():
    """For the graph checks: the loader node type -> the one file it must load."""
    p = get(); return {'UNETLoader': p['unet'], 'CLIPLoader': p['clip']}


def required_models():
    p = get(); return {'UNETLoader': [p['unet']], 'CLIPLoader': [p['clip']], 'VAELoader': [p['video_vae'], p['audio_vae']]}


def paths(comfy_root):
    p = get(); return [Path(comfy_root) / 'models' / folder / p[key] for folder, key in FILES]


def apply_to_graph(g):
    """Point the template's loaders at this profile's files (node ids of workflow_template_verified.json)."""
    p = get()
    for n in g['nodes']:
        w = n.get('widgets_values')
        if not isinstance(w, list) or not w: continue
        if n['type'] == 'UNETLoader' and n['id'] == 127: w[0] = p['unet']
        elif n['type'] == 'CLIPLoader' and n['id'] == 128: w[0] = p['clip']
        elif n['type'] == 'VAELoader' and n['id'] == 119: w[0] = p['video_vae']
        elif n['type'] == 'VAELoader' and n['id'] == 120: w[0] = p['audio_vae']
    return g
