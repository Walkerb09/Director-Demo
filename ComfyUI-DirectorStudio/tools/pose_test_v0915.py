"""One-generation test of H3 pose control with the Director's own references (V0.9.15).

Takes a shot you planned in the Director (its proven workflow, references and prompt) and adds:
  YOUR original video -> 24 fps, 9:16 -> SDPose (ComfyUI core) -> stick-figure pose video -> MiniMax H3 Fun ControlNet Union
so the take moves like the person in your video. The take is saved as PoseTest_<project>_S..._T... in ComfyUI/output; it does
NOT go into your movie (the Director only picks up its own takes). The pose video is saved next to it so you can see it.

Clean-Chain: the pose source must be one of YOUR original videos (never a file from ComfyUI/output, never a video that
carries a generation workflow). Only the stick figures go to H3, not your video's pixels.

usage (in the Vast terminal):
  cd /workspace/ComfyUI/custom_nodes/ComfyUI-DirectorStudio
  /venv/main/bin/python tools/pose_test_v0915.py --check                     # what's missing (models, nodes)
  /venv/main/bin/python tools/pose_test_v0915.py --download                  # get the missing models (about 9 GB)
  /venv/main/bin/python tools/pose_test_v0915.py --project my_project --shot 3 --take 1 --video /workspace/my_walk.mp4
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, uuid
from pathlib import Path
from urllib.request import urlopen, Request

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))
COMFY = Path(os.environ.get('COMFYUI_ROOT', '/workspace/ComfyUI'))
PROJECTS = Path(os.environ.get('DIRECTOR_STUDIO_ROOT', '/workspace/DirectorStudio'))
from studio_pose_v0921 import MODELS          # one list of pose models for the Director and this test (V0.9.21)
NODES = ['ModelPatchLoader', 'MiniMaxH3FunControlNetApply', 'SDPoseKeypointExtractor', 'SDPoseDrawKeypoints', 'RTDETR_detect',
         'CheckpointLoaderSimple', 'UNETLoader', 'LoadVideo', 'GetVideoComponents', 'VHS_VideoCombine']

def get(url, port, timeout=20):
    with urlopen(f'http://127.0.0.1:{port}{url}', timeout=timeout) as r: return json.load(r)

def check(port):
    """Every node the test needs, and every model file, on THIS ComfyUI. Returns (problems, missing_models)."""
    problems, missing = [], []
    for n in NODES:
        try: info = get(f'/object_info/{n}', port).get(n)
        except Exception as e: return [f'ComfyUI not reachable on port {port}: {e}'], []
        if not info: problems.append(f'node {n} is not in this ComfyUI (update ComfyUI: git pull + restart)')
    def options(node, key):
        try:
            spec = get(f'/object_info/{node}', port)[node]['input']
            v = (spec.get('required') or {}).get(key) or (spec.get('optional') or {}).get(key)
            return list(v[0]) if isinstance(v[0], list) else list((v[1] or {}).get('options') or [])
        except Exception: return []
    for k, (node, key) in {'controlnet': ('ModelPatchLoader', 'name'), 'sdpose': ('CheckpointLoaderSimple', 'ckpt_name'),
                           'detector': ('UNETLoader', 'unet_name')}.items():
        if MODELS[k][1] not in options(node, key): missing.append(k)
    return problems, missing

def download(missing):
    for k in missing:
        folder, name, url = MODELS[k]; dest = COMFY / 'models' / folder; dest.mkdir(parents=True, exist_ok=True)
        print(f'downloading {name} -> {dest}', flush=True)
        subprocess.run(['wget', '-c', '-q', '--show-progress', '-O', str(dest / name), url], check=True)
    print('done: restart ComfyUI (supervisorctl restart comfyui) so it sees the new files, then run --check again')

def pose_source(video, slug, frames):
    """YOUR original video -> 24 fps, 576x1024 (9:16, centre-cropped), the take's length, no sound, in ComfyUI/input."""
    import comfy_graph_v0739 as cg
    v = Path(video).resolve()
    if not v.is_file(): raise SystemExit(f'no such video: {v}')
    if (COMFY / 'output') in v.parents: raise SystemExit('REFUSED (Clean-Chain): that file is in ComfyUI/output; use one of YOUR original videos')
    if cg.embedded_provenance(v).get('status') == 'OK': raise SystemExit('REFUSED (Clean-Chain): that video carries a generation workflow; use one of YOUR originals')
    sha = hashlib.sha256(v.read_bytes()).hexdigest()
    rel = f'director_studio/{slug}/pose_src_{sha[:10]}_{frames}.mp4'; out = COMFY / 'input' / rel; out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(v), '-an', '-r', '24', '-frames:v', str(frames),
                    '-vf', 'scale=576:1024:force_original_aspect_ratio=increase,crop=576:1024,format=yuv420p', '-c:v', 'libx264', '-crf', '14', str(out)], check=True)
    got = int(subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames',
                              '-of', 'csv=p=0', str(out)], capture_output=True, text=True).stdout.strip() or 0)
    if got < frames: print(f'note: your video gives {got} of the {frames} frames this take needs; the last pose is held for the rest')
    return rel, sha

def build(api, pose_rel, prefix, strength):
    """The plan's own /prompt + the pose branch + the ControlNet patch on the H3 model."""
    api = json.loads(json.dumps(api))
    unet = next(k for k, v in api.items() if v['class_type'] == 'UNETLoader')
    video_vae = next(k for k, v in api.items() if v['class_type'] == 'VAELoader' and 'video' in v['inputs'].get('vae_name', ''))
    vhs = next(k for k, v in api.items() if v['class_type'] == 'VHS_VideoCombine')
    api['900'] = {'class_type': 'ModelPatchLoader', 'inputs': {'name': MODELS['controlnet'][1]}}
    api['902'] = {'class_type': 'LoadVideo', 'inputs': {'file': pose_rel}}
    api['903'] = {'class_type': 'GetVideoComponents', 'inputs': {'video': ['902', 0]}}
    api['905'] = {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': MODELS['sdpose'][1]}}
    api['907'] = {'class_type': 'UNETLoader', 'inputs': {'unet_name': MODELS['detector'][1], 'weight_dtype': 'default'}}
    api['908'] = {'class_type': 'RTDETR_detect', 'inputs': {'model': ['907', 0], 'image': ['903', 0], 'threshold': 0.5, 'class_name': 'person', 'max_detections': 2}}
    api['909'] = {'class_type': 'SDPoseKeypointExtractor', 'inputs': {'model': ['905', 0], 'vae': ['905', 2], 'image': ['903', 0], 'batch_size': 16, 'bboxes': ['908', 0]}}
    api['906'] = {'class_type': 'SDPoseDrawKeypoints', 'inputs': {'keypoints': ['909', 0], 'draw_body': True, 'draw_hands': True, 'draw_face': True,
                                                               'draw_feet': True, 'stick_width': 4, 'face_point_size': 2, 'score_threshold': 0.5, 'draw_head': True}}
    api['901'] = {'class_type': 'MiniMaxH3FunControlNetApply', 'inputs': {'model': [unet, 0], 'model_patch': ['900', 0], 'vae': [video_vae, 0],
                                                                       'strength': float(strength), 'start_percent': 0.0, 'end_percent': 0.6, 'control_video': ['906', 0]}}
    for k, v in api.items():                      # the scheduler and guider sample with the patched model
        if v['class_type'] in ('BasicScheduler', 'BasicGuider') and v['inputs'].get('model') == [unet, 0]: v['inputs']['model'] = ['901', 0]
        if v['class_type'] == 'LoraLoaderModelOnly' and v['inputs'].get('model') == [unet, 0]: v['inputs']['model'] = ['901', 0]
    api[vhs]['inputs']['filename_prefix'] = prefix
    pose_vhs = {k2: v2 for k2, v2 in api[vhs]['inputs'].items() if k2 not in ('images', 'audio')}
    api['910'] = {'class_type': 'VHS_VideoCombine', 'inputs': {**pose_vhs, 'images': ['906', 0], 'filename_prefix': prefix + '_POSE'}}
    return api

def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('--port', type=int, default=int(os.environ.get('COMFYUI_PORT', '18188')))
    a.add_argument('--check', action='store_true'); a.add_argument('--download', action='store_true')
    a.add_argument('--project'); a.add_argument('--shot', type=int); a.add_argument('--take', type=int, default=1)
    a.add_argument('--video', help='YOUR original video with the movement you want'); a.add_argument('--strength', type=float, default=0.6)
    x = a.parse_args()
    problems, missing = check(x.port)
    for p in problems: print('PROBLEM:', p)
    for k in missing: print(f'MISSING MODEL: {MODELS[k][1]} (ComfyUI/models/{MODELS[k][0]}/)')
    if x.download:
        if missing: download(missing)
        else: print('all models are already there')
        return
    if x.check or not x.project:
        print('READY' if not problems and not missing else 'NOT READY: fix the lines above (--download gets the models)'); return
    if problems or missing: raise SystemExit('NOT READY: run --check / --download first')
    import studio_v080 as sv
    s = sv.Studio(PROJECTS / x.project, COMFY)
    st = s.state(); p = s.plan_record(st, x.shot, x.take)
    if p.get('extend') or p.get('anchors'): print('note: this plan also pins frames (extend / start / end frame); that stays in')
    g, api = s.api_prompt(x.shot, x.take)                        # the plan's own, verified workflow
    rel, sha = pose_source(x.video, x.project, int(p.get('frames', 124)))
    if sha in st['generated_sha']: raise SystemExit('REFUSED (Clean-Chain): that video is a generated take')
    prefix = f"PoseTest_{x.project}_S{x.shot:02d}_T{x.take:02d}"
    body = {'prompt': build(api, rel, prefix, x.strength), 'client_id': 'director-pose-test-' + uuid.uuid4().hex[:8]}
    req = Request(f'http://127.0.0.1:{x.port}/prompt', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(req, timeout=60) as r: resp = json.load(r)
    except Exception as e:
        detail = getattr(e, 'read', lambda: b'')().decode(errors='replace')[:1500]
        raise SystemExit(f'ComfyUI refused the test: {e} {detail}')
    print('QUEUED', resp.get('prompt_id'))
    print(f'the take:       ComfyUI/output/{prefix}_00001-audio.mp4')
    print(f'the pose video: ComfyUI/output/{prefix}_POSE_00001.mp4')
    print('watch progress in the Director (Generation panel) or the ComfyUI log; it is not added to your movie.')

if __name__ == '__main__':
    main()
