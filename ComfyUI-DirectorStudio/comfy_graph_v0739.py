#!/usr/bin/env python3
"""V0.7.39 ComfyUI graph tools for the Director's proven H3 workflow family.

Three jobs, none of which queue anything:

1. validate_director_graph(): structural + Clean-Chain checks on an editor-format
   workflow (the JSON you drag into ComfyUI).
2. to_api_prompt(): editor-format -> /prompt API dictionary. The widget-name
   tables below are taken from what the user's LIVE Vast ComfyUI actually executed,
   recovered from the `prompt` metadata VHS embedded in the real Duo01 / Shot02 MP4s
   (reference/live_vast_truth/). Tests require exact equality with that ground truth.
   This replaces the V0.7.38 converter, which rejected inputs the live server accepts
   (`ref_images.ref_image_0`, VHS `crf`/`pix_fmt`/`save_metadata`/`trim_to_audio`).
3. embedded_provenance(): read the workflow a saved MP4 was generated from, so the
   Director can tell WHICH planned variant produced a capture.
"""
from __future__ import annotations
import json, subprocess
from pathlib import Path

class GraphError(ValueError):
    pass

# Widget order per node type for widgets_values lists (editor format).
# None = frontend-only widget that never reaches /prompt (seed control, upload button).
WIDGETS = {
    'VAELoader': ['vae_name'],
    'CLIPLoader': ['clip_name', 'type', 'device'],
    'KSamplerSelect': ['sampler_name'],
    'UNETLoader': ['unet_name', 'weight_dtype'],
    'RandomNoise': ['noise_seed', None],
    'BasicScheduler': ['scheduler', 'steps', 'denoise'],
    'BasicGuider': [],
    'SamplerCustomAdvanced': [],
    'VAEDecodeAudio': [],
    'VAEDecode': [],
    'MiniMaxH3ReferenceToVideo': ['prompt', 'width', 'height', 'length', 'ref_image_size'],
    'LoadImage': ['image', None],
    'DirectorV066SavedShotShadowQueue': ['capture_enabled', 'samples', 'source_class', 'run_nonce'],
    # Studio V0.8.1 additions (core ComfyUI nodes; schema re-checked against live /object_info before queueing)
    'LoadVideo': ['file', None],
    'GetVideoComponents': [],
    'LoraLoaderModelOnly': ['lora_name', 'strength_model'],
    'LoadAudio': ['audio', None, None],          # V0.9.3: your own voice recording (audio file, preview, upload button)
    'MiniMaxH3AddGuide': ['frame_idx'],          # V0.9.12: an extension starts on the ending of YOUR original (frame 0)
    # V0.9.21 🎭 movement from YOUR video: SDPose stick figures -> MiniMax H3 Fun ControlNet (core ComfyUI nodes)
    'ModelPatchLoader': ['name'],
    'MiniMaxH3FunControlNetApply': ['strength', 'start_percent', 'end_percent'],
    'CheckpointLoaderSimple': ['ckpt_name'],
    'RTDETR_detect': ['threshold', 'class_name', 'max_detections'],
    'SDPoseKeypointExtractor': ['batch_size'],
    'SDPoseDrawKeypoints': ['draw_body', 'draw_hands', 'draw_face', 'draw_feet', 'stick_width', 'face_point_size', 'score_threshold', 'draw_head'],
}
POSE_TYPES = {'ModelPatchLoader', 'MiniMaxH3FunControlNetApply', 'CheckpointLoaderSimple', 'RTDETR_detect', 'SDPoseKeypointExtractor', 'SDPoseDrawKeypoints'}
POSE_FILES = {'controlnet': 'minimax_h3_fun_controlnet_union_2.0_full_adaln_bf16.safetensors',   # V0.9.26: built for the full BF16 base
              'sdpose': 'sdpose_wholebody_fp16.safetensors',
              'detector': 'rt_detr_v4-x-hgnet_fp16.safetensors'}
MAX_IMAGE_REFS = 9          # MiniMax H3 R2V limit
MAX_VIDEO_REFS = 3          # ref_videos.ref_video_0..2 on the H3 node
# VHS_VideoCombine stores widgets as a dict; these keys are UI-only.
VHS_UI_ONLY = {'videopreview'}
VHS_KEYS = {'frame_rate', 'loop_count', 'filename_prefix', 'format', 'pix_fmt', 'crf',
            'save_metadata', 'trim_to_audio', 'pingpong', 'save_output'}
# Display names the live frontend wrote into _meta.title when a node had no custom title.
DISPLAY_NAMES = {'VAELoader': 'Load VAE', 'CLIPLoader': 'Load CLIP', 'KSamplerSelect': 'KSamplerSelect',
    'UNETLoader': 'Load Diffusion Model', 'RandomNoise': 'RandomNoise', 'BasicScheduler': 'BasicScheduler',
    'BasicGuider': 'Basic Guider', 'SamplerCustomAdvanced': 'SamplerCustomAdvanced',
    'VAEDecodeAudio': 'VAE Decode Audio', 'VAEDecode': 'VAE Decode', 'LoadImage': 'Load Image',
    'VHS_VideoCombine': 'Video Combine 🎥🅥🅗🅢', 'MiniMaxH3ReferenceToVideo': 'MiniMaxH3ReferenceToVideo',
    'DirectorV066SavedShotShadowQueue': 'DirectorV066SavedShotShadowQueue',
    'LoadVideo': 'Load Video', 'GetVideoComponents': 'Get Video Components', 'LoraLoaderModelOnly': 'LoraLoaderModelOnly',
    'LoadAudio': 'Load Audio', 'MiniMaxH3AddGuide': 'Add Guide for MiniMax H3',
    'ModelPatchLoader': 'ModelPatchLoader', 'MiniMaxH3FunControlNetApply': 'MiniMax H3 Fun ControlNet Apply', 'CheckpointLoaderSimple': 'Load Checkpoint',
    'RTDETR_detect': 'RT-DETR Detect', 'SDPoseKeypointExtractor': 'SDPose Keypoint Extractor', 'SDPoseDrawKeypoints': 'SDPose Draw Keypoints'}
FLOAT_INPUTS = {('BasicScheduler', 'denoise'), ('VHS_VideoCombine', 'frame_rate'), ('MiniMaxH3FunControlNetApply', 'strength'),
                ('MiniMaxH3FunControlNetApply', 'start_percent'), ('MiniMaxH3FunControlNetApply', 'end_percent'), ('RTDETR_detect', 'threshold'),
                ('SDPoseDrawKeypoints', 'score_threshold')}
REQUIRED_TYPES = {'MiniMaxH3ReferenceToVideo', 'VHS_VideoCombine', 'DirectorV066SavedShotShadowQueue',
                  'UNETLoader', 'CLIPLoader', 'LoadImage'}
FORBIDDEN_H3_INPUT_PREFIXES = ('ref_videos.', 'ref_video_audios.', 'ref_audios.')
MODEL_FILES = {
    'UNETLoader': 'minimax_h3_ref2va_bf16.safetensors',
    'CLIPLoader': 'qwen3vl_32b_minimax_h3_bf16.safetensors',
}

def _mf():
    """V0.9.70 the model files of this machine's profile (full BF16, or the 24 GB demo set)."""
    try:
        import studio_profile_v0970 as pf
        return pf.model_files()
    except ImportError: return MODEL_FILES

def _pose_cn():
    try:
        import studio_profile_v0970 as pf
        return pf.get().get('controlnet') or POSE_FILES['controlnet']
    except ImportError: return POSE_FILES['controlnet']

def _nodes(graph):
    ns = graph.get('nodes')
    if not isinstance(ns, list) or not isinstance(graph.get('links'), list):
        raise GraphError('NOT_AN_EDITOR_WORKFLOW')
    by = {}
    for n in ns:
        if n.get('id') in by: raise GraphError('DUPLICATE_NODE_ID')
        by[n['id']] = n
    return by

def validate_director_graph(graph, originals: dict, *, fps=24, width=768, height=1344, length=124, require_capture=True,
                            videos: dict | None = None, loras: dict | None = None, audios: dict | None = None, anchors: dict | None = None,
                            pose: dict | None = None):
    """Fail-closed checks. `originals`: allowed LoadImage filename -> sha256. `videos`: allowed ORIGINAL reference
    clips (LoadVideo file -> sha256); None = no video refs allowed. `loras`: allowed lora_name -> strength range."""
    by = _nodes(graph)
    types = {n['type'] for n in by.values()}
    need = REQUIRED_TYPES if require_capture else REQUIRED_TYPES - {'DirectorV066SavedShotShadowQueue'}
    if not need <= types: raise GraphError('MISSING_REQUIRED_NODE_TYPES')
    unknown = types - set(WIDGETS) - {'VHS_VideoCombine'}
    if unknown: raise GraphError('UNKNOWN_NODE_TYPES_' + ','.join(sorted(unknown)))
    link_ids = set()
    for l in graph['links']:
        if not (isinstance(l, list) and len(l) == 6): raise GraphError('BAD_LINK_RECORD')
        lid, o, oslot, t, tslot, typ = l
        if lid in link_ids: raise GraphError('DUPLICATE_LINK_ID')
        link_ids.add(lid)
        if o not in by or t not in by: raise GraphError('LINK_TO_MISSING_NODE')
        out = by[o]['outputs'][oslot]; inp = by[t]['inputs'][tslot]
        if lid not in (out.get('links') or []) or inp.get('link') != lid:
            raise GraphError('LINK_NOT_MIRRORED_%s' % lid)
        if out['type'] != typ or inp['type'] != typ: raise GraphError('LINK_TYPE_MISMATCH_%s' % lid)
    for n in by.values():
        for i in n.get('inputs', []):
            if i.get('link') is not None and i['link'] not in link_ids: raise GraphError('DANGLING_INPUT_LINK')
        for o in n.get('outputs', []):
            for lid in o.get('links') or []:
                if lid not in link_ids: raise GraphError('DANGLING_OUTPUT_LINK')
        if n.get('mode', 0) not in (0,): raise GraphError('MUTED_OR_BYPASSED_NODE_%s' % n['id'])
        if n['type'] in WIDGETS and len(n.get('widgets_values') or []) != len(WIDGETS[n['type']]):
            raise GraphError('WIDGET_COUNT_%s_%s' % (n['type'], n['id']))
    if max(link_ids) > graph.get('last_link_id', -1) or max(by) > graph.get('last_node_id', -1):
        raise GraphError('LAST_ID_COUNTERS_TOO_LOW')
    h3 = [n for n in by.values() if n['type'] == 'MiniMaxH3ReferenceToVideo']
    vhs = [n for n in by.values() if n['type'] == 'VHS_VideoCombine']
    if len(h3) != 1 or len(vhs) != 1: raise GraphError('EXPECTED_ONE_H3_AND_ONE_VHS')
    h3 = h3[0]; vhs = vhs[0]
    prompt, w, h, frames, _ = h3['widgets_values']
    if (w, h, frames) != (width, height, length): raise GraphError('H3_FORMAT_CHANGED')
    if not isinstance(prompt, str) or not 40 <= len(prompt) <= 7000: raise GraphError('H3_PROMPT_INVALID')   # H3's documented limit (V0.9.31; was 6000)
    links = {l[0]: l for l in graph['links']}
    used_videos = []
    used_audios = []
    for i in h3['inputs']:
        if not (i['name'].startswith(FORBIDDEN_H3_INPUT_PREFIXES) and i.get('link') is not None): continue
        if i['name'].startswith('ref_audios.'):              # only YOUR declared voice recordings, straight from LoadAudio
            if audios is None: raise GraphError('H3_VIDEO_OR_AUDIO_REFERENCE_FORBIDDEN')
            src = by[links[i['link']][1]]
            if src['type'] != 'LoadAudio' or src['widgets_values'][0] not in audios: raise GraphError('H3_AUDIO_REF_NOT_A_DECLARED_VOICE_RECORDING')
            used_audios.append(src['widgets_values'][0]); continue
        if videos is None:
            raise GraphError('H3_VIDEO_OR_AUDIO_REFERENCE_FORBIDDEN')
        lk = links[i['link']]; comp = by[lk[1]]
        want_slot = 0 if i['name'].startswith('ref_videos.') else 1
        if comp['type'] != 'GetVideoComponents' or lk[2] != want_slot: raise GraphError('H3_VIDEO_REF_NOT_FROM_GETVIDEOCOMPONENTS')
        vin = next((x for x in comp['inputs'] if x['name'] == 'video' and x.get('link') is not None), None)
        if vin is None: raise GraphError('VIDEO_COMPONENTS_UNCONNECTED')
        src = by[links[vin['link']][1]]
        if src['type'] != 'LoadVideo' or src['widgets_values'][0] not in videos: raise GraphError('H3_VIDEO_REF_NOT_A_DECLARED_ORIGINAL_CLIP')
        if i['name'].startswith('ref_videos.'): used_videos.append(src['widgets_values'][0])
    if videos is not None:
        if sorted(used_videos) != sorted(videos) or len(used_videos) > MAX_VIDEO_REFS:
            raise GraphError('H3_MUST_USE_EXACTLY_THE_DECLARED_ORIGINAL_CLIPS')
    la = [n['widgets_values'][0] for n in by.values() if n['type'] == 'LoadAudio']
    if la and (audios is None or set(la) - set(audios)): raise GraphError('NON_DECLARED_LOADAUDIO')
    if audios is not None and (sorted(used_audios) != sorted(audios) or len(used_audios) > 3):
        raise GraphError('H3_MUST_USE_EXACTLY_THE_DECLARED_VOICE_RECORDINGS')
    # Guides: only the declared ending of YOUR original, at frame 0, between the H3 node and the guider
    used_anchors = []
    for n in by.values():
        if n['type'] != 'MiniMaxH3AddGuide': continue
        if anchors is None: raise GraphError('GUIDE_FRAMES_NOT_DECLARED')
        if n['widgets_values'] not in ([0], [-1]): raise GraphError('GUIDE_NOT_AT_THE_START_OR_THE_END')
        ins = {x['name']: x.get('link') for x in n['inputs']}
        def src_of(name):
            return links[ins[name]] if ins.get(name) is not None else None
        pos, lat, img = src_of('positive'), src_of('latent'), src_of('image')
        if not pos or not lat or not img: raise GraphError('GUIDE_UNCONNECTED')
        if by[pos[1]]['type'] not in ('MiniMaxH3ReferenceToVideo', 'MiniMaxH3AddGuide') or by[lat[1]]['type'] != 'MiniMaxH3ReferenceToVideo' or lat[2] != 1:
            raise GraphError('GUIDE_NOT_ON_THE_H3_OUTPUT')
        comp = by[img[1]]
        if comp['type'] == 'LoadImage':                      # a start frame: one of YOUR original stills, straight from LoadImage
            if img[2] != 0 or comp['widgets_values'][0] not in anchors: raise GraphError('GUIDE_FRAMES_NOT_YOUR_DECLARED_ORIGINAL')
            if src_of('audio'): raise GraphError('GUIDE_SOUND_NOT_FROM_THE_SAME_ORIGINAL')
            used_anchors.append(comp['widgets_values'][0]); continue
        if n['widgets_values'] != [0]: raise GraphError('GUIDE_NOT_AT_FRAME_0')        # your original's ending only ever opens a take
        if comp['type'] != 'GetVideoComponents' or img[2] != 0: raise GraphError('GUIDE_FRAMES_NOT_FROM_GETVIDEOCOMPONENTS')
        au = src_of('audio')
        if au and (au[1] != comp['id'] or au[2] != 1): raise GraphError('GUIDE_SOUND_NOT_FROM_THE_SAME_ORIGINAL')
        vin = next((x for x in comp['inputs'] if x['name'] == 'video' and x.get('link') is not None), None)
        if vin is None: raise GraphError('VIDEO_COMPONENTS_UNCONNECTED')
        srcv = by[links[vin['link']][1]]
        if srcv['type'] != 'LoadVideo' or srcv['widgets_values'][0] not in anchors: raise GraphError('GUIDE_FRAMES_NOT_YOUR_DECLARED_ORIGINAL')
        used_anchors.append(srcv['widgets_values'][0])
    if anchors is not None and set(used_anchors) != set(anchors): raise GraphError('GUIDE_MUST_USE_EXACTLY_THE_DECLARED_ORIGINAL_ENDING')
    lv = [n['widgets_values'][0] for n in by.values() if n['type'] == 'LoadVideo']
    if lv and set(lv) - set(videos or {}) - set(anchors or {}) - set(pose or {}): raise GraphError('NON_ORIGINAL_LOADVIDEO')
    # 🎭 Movement: only stick figures read from YOUR declared movement video, patched onto the H3 model (V0.9.21)
    cns = [n for n in by.values() if n['type'] == 'MiniMaxH3FunControlNetApply']
    if (types & POSE_TYPES) and pose is None: raise GraphError('POSE_NOT_DECLARED')
    if pose is not None:
        if len(cns) != 1: raise GraphError('POSE_NEEDS_EXACTLY_ONE_CONTROLNET')
        cn = cns[0]; ins = {x['name']: x.get('link') for x in cn['inputs']}
        def up(nd, name):
            li = next((x.get('link') for x in nd['inputs'] if x['name'] == name), None)
            if li is None: raise GraphError('POSE_UNCONNECTED_' + name.upper())
            return by[links[li][1]], links[li][2]
        mp, _ = up(cn, 'model_patch')
        if mp['type'] != 'ModelPatchLoader' or mp['widgets_values'][0] != _pose_cn(): raise GraphError('POSE_CONTROLNET_NOT_THE_FULL_ADALN_BF16_UNION')
        vae, _ = up(cn, 'vae')
        if vae['type'] != 'VAELoader': raise GraphError('POSE_VAE_NOT_A_VAELOADER')
        dr, _ = up(cn, 'control_video')
        if dr['type'] != 'SDPoseDrawKeypoints': raise GraphError('POSE_CONTROL_NOT_STICK_FIGURES')
        kp, _ = up(dr, 'keypoints')
        if kp['type'] != 'SDPoseKeypointExtractor': raise GraphError('POSE_KEYPOINTS_NOT_FROM_SDPOSE')
        ck, _ = up(kp, 'model'); ck2, slot = up(kp, 'vae')
        if ck['type'] != 'CheckpointLoaderSimple' or ck['id'] != ck2['id'] or slot != 2 or ck['widgets_values'][0] != POSE_FILES['sdpose']:
            raise GraphError('POSE_SDPOSE_MODEL_WRONG')
        def from_declared(nd):
            comp, slot = up(nd, 'image')
            if comp['type'] != 'GetVideoComponents' or slot != 0: raise GraphError('POSE_FRAMES_NOT_FROM_GETVIDEOCOMPONENTS')
            srcv, _ = up(comp, 'video')
            if srcv['type'] != 'LoadVideo' or srcv['widgets_values'][0] not in pose: raise GraphError('POSE_FRAMES_NOT_YOUR_DECLARED_MOVEMENT_VIDEO')
            return srcv['widgets_values'][0]
        used_pose = from_declared(kp)
        rt, _ = up(kp, 'bboxes')
        if rt['type'] != 'RTDETR_detect' or from_declared(rt) != used_pose: raise GraphError('POSE_PEOPLE_NOT_FOUND_IN_THE_SAME_VIDEO')
        det, _ = up(rt, 'model')
        if det['type'] != 'UNETLoader' or det['widgets_values'][0] != POSE_FILES['detector']: raise GraphError('POSE_DETECTOR_MODEL_WRONG')
        if not 0.2 <= float(cn['widgets_values'][0]) <= 1.0: raise GraphError('POSE_STRENGTH_RANGE')
        if float(cn['widgets_values'][1]) != 0.0 or not 0.3 <= float(cn['widgets_values'][2]) <= 0.8:
            raise GraphError('POSE_MUST_LET_GO_BEFORE_THE_LAST_STEPS')    # the last steps are free: no stick figures in the picture
        if set(pose) != {used_pose}: raise GraphError('POSE_MUST_USE_EXACTLY_THE_DECLARED_MOVEMENT_VIDEO')
    # Model chain: UNETLoader -> (LoraLoaderModelOnly)* -> BasicScheduler / BasicGuider
    for n in by.values():
        if n['type'] == 'LoraLoaderModelOnly':
            name, strength = n['widgets_values']
            if loras is None or name not in loras: raise GraphError('LORA_NOT_DECLARED')
            if not (-2.0 <= float(strength) <= 2.0): raise GraphError('LORA_STRENGTH_RANGE')
    def model_root(node):
        seen = 0
        while node['type'] in ('LoraLoaderModelOnly', 'MiniMaxH3FunControlNetApply') and seen < 12:
            mi = next(x for x in node['inputs'] if x['name'] == 'model'); node = by[links[mi['link']][1]]; seen += 1
        return node
    for n in by.values():
        if n['type'] in ('BasicScheduler', 'BasicGuider'):
            mi = next((x for x in n['inputs'] if x['name'] == 'model' and x.get('link') is not None), None)
            if mi is None: raise GraphError('MODEL_CHAIN_INVALID')
            first = by[links[mi['link']][1]]; root = model_root(first)
            if root['type'] != 'UNETLoader': raise GraphError('MODEL_CHAIN_INVALID')
            if root['widgets_values'][0] != _mf()['UNETLoader']: raise GraphError('NOT_THE_FULL_BF16_H3_MODEL_SET')   # e.g. Turbo / FP8
            if pose is not None and first['type'] != 'MiniMaxH3FunControlNetApply': raise GraphError('POSE_NOT_ON_THE_SAMPLING_MODEL')
    # Every image reference must trace directly to a LoadImage of a declared original.
    used = []
    for i in h3['inputs']:
        if i['name'].startswith('ref_images.') and i.get('link') is not None:
            src = by[links[i['link']][1]]
            if src['type'] != 'LoadImage': raise GraphError('H3_IMAGE_REF_NOT_DIRECT_LOADIMAGE')
            used.append(src['widgets_values'][0])
    loads = [n['widgets_values'][0] for n in by.values() if n['type'] == 'LoadImage']
    if set(loads) - set(originals) - set(anchors or {}): raise GraphError('NON_ORIGINAL_LOADIMAGE')
    if sorted(used) != sorted(originals) or len(used) != len(set(used)):
        raise GraphError('H3_MUST_USE_EXACTLY_THE_DECLARED_ORIGINALS')
    if len(used) > MAX_IMAGE_REFS: raise GraphError('MORE_THAN_9_IMAGE_REFS')
    vw = vhs['widgets_values']
    if not isinstance(vw, dict) or set(vw) - VHS_UI_ONLY != VHS_KEYS: raise GraphError('VHS_WIDGETS_UNEXPECTED')
    if vw['frame_rate'] != fps or vw['save_metadata'] is not True or vw['save_output'] is not True:
        raise GraphError('VHS_SETTINGS_CHANGED')
    if not any(i['name'] == 'audio' and i.get('link') is not None for i in vhs['inputs']):
        raise GraphError('VHS_AUDIO_NOT_CONNECTED')
    for n in by.values():
        if n['type'] in _mf() and n['widgets_values'][0] != _mf()[n['type']]:
            if pose is not None and n['type'] == 'UNETLoader' and n['widgets_values'][0] == POSE_FILES['detector']:
                outs = [l for l in graph['links'] if l[1] == n['id']]
                if outs and all(by[l[3]]['type'] == 'RTDETR_detect' for l in outs): continue      # the person detector of the pose branch
            raise GraphError('NOT_THE_FULL_BF16_H3_MODEL_SET')
    if sum(1 for n in by.values() if n['type'] == 'UNETLoader' and n['widgets_values'][0] == _mf()['UNETLoader']) != 1:
        raise GraphError('NOT_THE_FULL_BF16_H3_MODEL_SET')
    cap = [n for n in by.values() if n['type'] == 'DirectorV066SavedShotShadowQueue']
    if not require_capture and not cap: pass
    elif len(cap) != 1 or not any(i['name'] == 'saved_video' and i.get('link') is not None for i in cap[0]['inputs']):
        raise GraphError('SAVED_SHOT_CAPTURE_NOT_WIRED')
    return {'prompt': prompt, 'filename_prefix': vw['filename_prefix'], 'images': used, 'videos': used_videos,
            'loras': [n['widgets_values'] for n in by.values() if n['type'] == 'LoraLoaderModelOnly'],
            'seed': next(n['widgets_values'][0] for n in by.values() if n['type'] == 'RandomNoise')}

def to_api_prompt(graph, image_sha256: dict | None = None):
    """Editor workflow -> /prompt `prompt` dict, as the live ComfyUI frontend would send it.
    image_sha256 (filename -> sha) reproduces the frontend's LoadImage `is_changed` field."""
    by = _nodes(graph)
    links = {l[0]: l for l in graph['links']}
    api = {}
    for nid in sorted(by):
        n = by[nid]; t = n['type']
        if n.get('mode', 0) != 0: continue
        inputs = {}
        if t == 'VHS_VideoCombine':
            vw = n['widgets_values']
            if not isinstance(vw, dict): raise GraphError('VHS_WIDGETS_NOT_DICT')
            for k, v in vw.items():
                if k in VHS_UI_ONLY: continue
                if k not in VHS_KEYS: raise GraphError('VHS_UNKNOWN_WIDGET_' + k)
                inputs[k] = float(v) if (t, k) in FLOAT_INPUTS else v
        elif t in WIDGETS:
            vals = n.get('widgets_values') or []
            if len(vals) != len(WIDGETS[t]): raise GraphError('WIDGET_COUNT_%s' % t)
            for name, v in zip(WIDGETS[t], vals):
                if name is None: continue
                inputs[name] = float(v) if (t, name) in FLOAT_INPUTS else v
        else:
            raise GraphError('NO_SCHEMA_FOR_NODE_TYPE_' + t)
        for i in n.get('inputs', []):
            if i.get('link') is None: continue
            l = links[i['link']]
            inputs[i['name']] = [str(l[1]), l[2]]
        entry = {'inputs': inputs, 'class_type': t, '_meta': {'title': n.get('title') or DISPLAY_NAMES[t]}}
        if t == 'LoadImage' and image_sha256 is not None:
            if inputs['image'] not in image_sha256: raise GraphError('LOADIMAGE_HASH_UNKNOWN')
            entry['is_changed'] = [image_sha256[inputs['image']]]
        api[str(nid)] = entry
    return api

def embedded_provenance(mp4: Path):
    """Read the /prompt JSON that VHS (save_metadata=True) embedded into a saved MP4."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags=prompt', '-of',
                        'default=nw=1:nk=1', str(mp4)], capture_output=True, text=True, timeout=30)
    raw = r.stdout.strip()
    if r.returncode != 0 or not raw:
        return {'status': 'NO_EMBEDDED_WORKFLOW'}
    try:
        api = json.loads(raw)
    except ValueError:
        return {'status': 'EMBEDDED_WORKFLOW_UNREADABLE'}
    def one(cls):
        xs = [v for v in api.values() if isinstance(v, dict) and v.get('class_type') == cls]
        return xs[0]['inputs'] if len(xs) == 1 else None
    h3, noise, vhs = one('MiniMaxH3ReferenceToVideo'), one('RandomNoise'), one('VHS_VideoCombine')
    if not (h3 and noise and vhs):
        return {'status': 'EMBEDDED_WORKFLOW_NOT_DIRECTOR_GRAPH'}
    images = []
    for k, v in sorted(h3.items()):
        if k.startswith('ref_images.') and isinstance(v, list):
            src = api.get(v[0], {})
            images.append(src.get('inputs', {}).get('image') if src.get('class_type') == 'LoadImage' else 'NON_LOADIMAGE')
    vids = []
    for k, v in sorted(h3.items()):
        if k.startswith('ref_videos.') and isinstance(v, list):
            comp = api.get(v[0], {}); src = comp.get('inputs', {}).get('video')
            lv = api.get(src[0], {}) if isinstance(src, list) else {}
            vids.append(lv.get('inputs', {}).get('file') if comp.get('class_type') == 'GetVideoComponents' and lv.get('class_type') == 'LoadVideo' else 'NON_ORIGINAL_SOURCE')
    auds = []
    for k, v in sorted(h3.items()):
        if k.startswith('ref_audios.') and isinstance(v, list):
            src = api.get(v[0], {})
            auds.append(src.get('inputs', {}).get('audio') if src.get('class_type') == 'LoadAudio' else 'NON_DECLARED_SOURCE')
    loras = [[v['inputs'].get('lora_name'), v['inputs'].get('strength_model')] for v in api.values()
             if isinstance(v, dict) and v.get('class_type') == 'LoraLoaderModelOnly']
    anchors = []
    for v in api.values():
        if isinstance(v, dict) and v.get('class_type') == 'MiniMaxH3AddGuide' and isinstance(v['inputs'].get('image'), list):
            comp = api.get(v['inputs']['image'][0], {}); src = comp.get('inputs', {}).get('video')
            if comp.get('class_type') == 'LoadImage': anchors.append(comp.get('inputs', {}).get('image')); continue
            lv = api.get(src[0], {}) if isinstance(src, list) else {}
            anchors.append(lv.get('inputs', {}).get('file') if comp.get('class_type') == 'GetVideoComponents' and lv.get('class_type') == 'LoadVideo' else 'NON_ORIGINAL_SOURCE')
    return {'status': 'OK', 'prompt': h3.get('prompt'), 'seed': noise.get('noise_seed'), 'videos': vids, 'loras': loras, 'audios': auds, 'anchors': anchors,
            'filename_prefix': vhs.get('filename_prefix'), 'images': images,
            'video_or_audio_refs_linked': sorted(k for k, v in h3.items()
                if k.startswith(FORBIDDEN_H3_INPUT_PREFIXES) and isinstance(v, list))}

def derive_workflow(template_graph, *, prompt, seed, prefix):
    """Copy of the proven template with ONLY prompt/seed/prefix changed."""
    g = json.loads(json.dumps(template_graph))
    by = _nodes(g)
    h3 = [n for n in by.values() if n['type'] == 'MiniMaxH3ReferenceToVideo']
    rn = [n for n in by.values() if n['type'] == 'RandomNoise']
    vhs = [n for n in by.values() if n['type'] == 'VHS_VideoCombine']
    if len(h3) != 1 or len(rn) != 1 or len(vhs) != 1: raise GraphError('TEMPLATE_SHAPE')
    if type(seed) is not int or not 0 <= seed < 2**53: raise GraphError('SEED_RANGE')
    h3[0]['widgets_values'][0] = prompt
    rn[0]['widgets_values'][0] = seed; rn[0]['widgets_values'][1] = 'fixed'
    vhs[0]['widgets_values']['filename_prefix'] = prefix
    return g
