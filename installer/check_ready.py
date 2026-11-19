#!/usr/bin/env python3
"""Read-only readiness check after the full install + a ComfyUI restart. Never starts jobs."""
import json, os, sys, urllib.request
from pathlib import Path
COMFY = Path('/workspace/ComfyUI')
try: PROFILE = json.loads(Path('/workspace/DirectorStudio/_profile.json').read_text()).get('profile', 'full')
except (OSError, ValueError): PROFILE = 'full'
PROFILE = os.environ.get('DIRECTOR_PROFILE', PROFILE)
H3 = ({'unet': 'minimax_h3_ref2va_pruned_w6a8', 'te': 'qwen3vl_32b_minimax_h3_nvfp4_awq', 'vae': 'minimax_h3_video_vae_int8_convrot'} if PROFILE == 'lite24'
      else {'unet': 'minimax_h3_ref2va_bf16', 'te': 'qwen3vl_32b_minimax_h3_bf16', 'vae': 'minimax_h3_video_vae_fp16'})
print('MODEL_PROFILE', PROFILE)
required = [
 COMFY/f"models/diffusion_models/{H3['unet']}.safetensors",
 COMFY/f"models/text_encoders/{H3['te']}.safetensors",
 COMFY/f"models/vae/{H3['vae']}.safetensors",
 COMFY/'models/vae/minimax_h3_audio_vae_fp32.safetensors',
 COMFY/'custom_nodes/ComfyUI-DirectorStudio/__init__.py',
 COMFY/'custom_nodes/ComfyUI-DirectorStudio/models/face_recognition_sface_2021dec.onnx',
 Path('/workspace/h3-qwen-venv/bin/python'),
 Path('/workspace/DirectorSavedShotQueueV066/drain_jobs.py'),
 Path('/workspace/DirectorUnifiedV074/director_postshot_v074.py'),
 Path('/workspace/DirectorIdleDispatcherV075/director_idle_dispatcher_v075.py'),
]
failed = False
for p in required:
    okay = p.is_file() and p.stat().st_size > 0
    print('FILE_' + ('OK' if okay else 'MISSING'), p); failed |= not okay
def node_info(port, node):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/object_info/{node}', timeout=20) as r: return node in json.load(r)
    except Exception: return False
port = None
for cand in (os.environ.get('COMFYUI_PORT'), '18188', '8188'):
    if not cand: continue
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{cand}/queue', timeout=5): port = cand; break
    except Exception: pass
if port is None:
    print('COMFY_NOT_RUNNING (tried 18188 / 8188): start or restart ComfyUI, then run this again'); failed = True
else:
    print('COMFY_PORT', port)
    for node in ['MiniMaxH3ReferenceToVideo', 'VHS_VideoCombine', 'LoadVideo', 'GetVideoComponents', 'LoraLoaderModelOnly']:
        okay = node_info(port, node); optional = node in ('LoadVideo', 'GetVideoComponents', 'LoraLoaderModelOnly')
        print('COMFY_NODE_' + ('READY' if okay else 'MISSING' + (' (optional)' if optional else '')), node)
        failed |= (not okay and not optional)
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/director/api/health', timeout=60) as r: h = json.load(r)
        for i in h['items']:
            print(('STUDIO_OK      ' if i['ok'] else 'STUDIO_OPTIONAL' if i.get('optional') else 'STUDIO_PROBLEM '), i['item'], '' if i['ok'] else '-> ' + i.get('fix', ''))
        failed |= not h['ok']
        print('STUDIO_URL     your ComfyUI link + /director')
    except Exception as e:
        print('STUDIO_NOT_LOADED', repr(e)[:200], '-> restart ComfyUI after the install; check its log for [DirectorStudio]'); failed = True
print('READY' if not failed else 'NEEDS_ATTENTION')
raise SystemExit(1 if failed else 0)
