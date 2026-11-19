"""Director Studio graph: the proven H3 ComfyUI workflow with your references, clips, voices and LoRAs."""
from __future__ import annotations
from studio_common_v094 import *
from studio_common_v094 import _SHA_CACHE, _json_ok

def build_graph(image_names, prompt, seed, prefix, video_names=(), video_audio=False, loras=(), length=124, audio_names=(), anchor_names=(), pose=None, steps=None, sampler=None):
    """Proven H3 graph with 1..9 LoadImage refs, 0..3 ORIGINAL reference clips (core LoadVideo ->
    GetVideoComponents -> ref_videos / optional ref_video_audios) and an optional LoRA chain
    (UNETLoader -> LoraLoaderModelOnly* -> scheduler/guider). No custom capture node.
    anchor_names: the ending of YOUR original video (core LoadVideo -> GetVideoComponents -> MiniMaxH3AddGuide at frame 0),
    so an extension take starts exactly where your original stops (picture and sound pinned, never denoised).
    pose: {'name': the prepared copy of YOUR movement video in ComfyUI/input, 'strength'} -> SDPose stick figures steer the take
    through the MiniMax H3 Fun ControlNet (V0.9.21)."""
    if not 1 <= len(image_names) <= MAX_REFS: raise StudioError(f'REF_COUNT_1_TO_{MAX_REFS}')
    if len(video_names) > MAX_VIDEOS: raise StudioError(f'VIDEO_REF_COUNT_MAX_{MAX_VIDEOS}')
    g = json.loads((HERE / 'workflow_template_verified.json').read_text())
    import studio_profile_v0970 as pf; pf.apply_to_graph(g)        # V0.9.70 this machine's model files (full BF16, or the 24 GB demo set)
    by = {n['id']: n for n in g['nodes']}
    drop_nodes = {251, 139, 237, 250, 240, 242, 137}
    drop_links = {l[0] for l in g['links'] if l[1] in drop_nodes or l[3] in drop_nodes}
    g['links'] = [l for l in g['links'] if l[0] not in drop_links]
    for n in g['nodes']:
        for o in n.get('outputs', []):
            if o.get('links'): o['links'] = [x for x in o['links'] if x not in drop_links]
        for i in n.get('inputs', []):
            if i.get('link') in drop_links: i['link'] = None
    g['nodes'] = [n for n in g['nodes'] if n['id'] not in drop_nodes]
    for n in g['nodes']: n.pop('widgets_values_named', None)    # stale non-standard field from an older tool
    h3 = by[136]
    have = {i['name'] for i in h3['inputs']}
    for k in range(MAX_REFS):
        if f'ref_images.ref_image_{k}' not in have:
            h3['inputs'].append({'name': f'ref_images.ref_image_{k}', 'shape': 7, 'type': 'IMAGE', 'link': None})
    for k in range(MAX_VIDEOS):
        if f'ref_video_audios.ref_video_audio_{k}' not in have:
            h3['inputs'].append({'name': f'ref_video_audios.ref_video_audio_{k}', 'shape': 7, 'type': 'AUDIO', 'link': None})
    slot = {i['name']: k for k, i in enumerate(h3['inputs'])}
    ids = iter(range(400, 1000)); lids = iter(range(9001, 9999))
    def node(**kw):
        n = {'id': next(ids), 'flags': {}, 'order': 0, 'mode': 0, 'properties': {'Node name for S&R': kw['type']}, **kw}
        g['nodes'].append(n); return n
    def link(src, oslot, dst, islot, typ):
        lid = next(lids); g['links'].append([lid, src['id'], oslot, dst['id'], islot, typ])
        src['outputs'][oslot].setdefault('links', []); src['outputs'][oslot]['links'] = (src['outputs'][oslot]['links'] or []) + [lid]
        dst['inputs'][islot]['link'] = lid
    for k, name in enumerate(image_names):
        n = node(type='LoadImage', pos=[-900, 100 + 330 * k], size=[320, 300], title=f'ORIGINAL ref {k}: {Path(name).name[:40]}',
                 outputs=[{'name': 'IMAGE', 'type': 'IMAGE', 'links': []}, {'name': 'MASK', 'type': 'MASK', 'links': []}],
                 widgets_values=[name, 'image'])
        link(n, 0, h3, slot[f'ref_images.ref_image_{k}'], 'IMAGE')
    for k, name in enumerate(video_names):
        lv = node(type='LoadVideo', pos=[-1300, 100 + 330 * k], size=[320, 300], title=f'ORIGINAL clip {k}: {Path(name).name[:40]}',
                  outputs=[{'name': 'VIDEO', 'type': 'VIDEO', 'links': []}], widgets_values=[name, 'image'])
        gc = node(type='GetVideoComponents', pos=[-950, 3200 + 200 * k], size=[260, 90], title=f'clip {k} frames/audio',
                  inputs=[{'name': 'video', 'type': 'VIDEO', 'link': None}],
                  outputs=[{'name': 'images', 'type': 'IMAGE', 'links': []}, {'name': 'audio', 'type': 'AUDIO', 'links': []},
                           {'name': 'fps', 'type': 'FLOAT', 'links': []}], widgets_values=[])
        link(lv, 0, gc, 0, 'VIDEO')
        link(gc, 0, h3, slot[f'ref_videos.ref_video_{k}'], 'IMAGE')
        if video_audio: link(gc, 1, h3, slot[f'ref_video_audios.ref_video_audio_{k}'], 'AUDIO')
    if len(audio_names) > 3: raise StudioError('VOICE_REF_COUNT_MAX_3')
    for k in range(3):
        if f'ref_audios.ref_audio_{k}' not in slot:
            h3['inputs'].append({'name': f'ref_audios.ref_audio_{k}', 'shape': 7, 'type': 'AUDIO', 'link': None}); slot[f'ref_audios.ref_audio_{k}'] = len(h3['inputs']) - 1
    for k, name in enumerate(audio_names):          # your own voice recordings (voice-timbre references)
        la = node(type='LoadAudio', pos=[-1300, 2400 + 200 * k], size=[320, 130], title=f'YOUR voice {k}: {Path(name).name[:40]}',
                  outputs=[{'name': 'AUDIO', 'type': 'AUDIO', 'links': []}], widgets_values=[name, None, None])
        link(la, 0, h3, slot[f'ref_audios.ref_audio_{k}'], 'AUDIO')
    if len(anchor_names) > 2: raise StudioError('TWO_PINNED_PICTURES_AT_MOST')
    for k, item in enumerate(anchor_names):
        name, at = (item if isinstance(item, (tuple, list)) else (item, 0))           # at: 0 = the first frame, -1 = the last frame
        still = Path(name).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')
        if at not in (0, -1) or (at == -1 and not still): raise StudioError('A_PINNED_PICTURE_GOES_AT_THE_START_OR_THE_END')
        if still:          # a start frame: one of YOUR original stills, pinned as frame 0 (no sound)
            li = node(type='LoadImage', pos=[-1300, 4000 + 330 * k], size=[320, 300], title=f'YOUR original start frame: {Path(name).name[:40]}',
                      outputs=[{'name': 'IMAGE', 'type': 'IMAGE', 'links': []}, {'name': 'MASK', 'type': 'MASK', 'links': []}],
                      widgets_values=[name, 'image'])
            ag = node(type='MiniMaxH3AddGuide', pos=[400, 900 + 200 * k], size=[300, 160], title='starts on your original still (frame 0)' if at == 0 else 'ends on your original still (last frame)',
                      inputs=[{'name': 'positive', 'type': 'CONDITIONING', 'link': None}, {'name': 'vae', 'type': 'VAE', 'shape': 7, 'link': None},
                              {'name': 'audio_vae', 'type': 'VAE', 'shape': 7, 'link': None}, {'name': 'latent', 'type': 'LATENT', 'link': None},
                              {'name': 'image', 'type': 'IMAGE', 'shape': 7, 'link': None}, {'name': 'audio', 'type': 'AUDIO', 'shape': 7, 'link': None}],
                      outputs=[{'name': 'positive', 'type': 'CONDITIONING', 'links': []}], widgets_values=[at])
            old = next(l for l in g['links'] if l[3] == 126 and l[4] == 1)
            g['links'].remove(old); src = next(n for n in g['nodes'] if n['id'] == old[1])
            src['outputs'][old[2]]['links'] = [x for x in src['outputs'][old[2]]['links'] if x != old[0]]
            link(src, old[2], ag, 0, 'CONDITIONING')
            link(by[119], 0, ag, 1, 'VAE'); link(h3, 1, ag, 3, 'LATENT'); link(li, 0, ag, 4, 'IMAGE')
            link(ag, 0, by[126], 1, 'CONDITIONING')
            continue
        lv = node(type='LoadVideo', pos=[-1300, 4000 + 330 * k], size=[320, 300], title=f'YOUR original ending {k}: {Path(name).name[:40]}',
                  outputs=[{'name': 'VIDEO', 'type': 'VIDEO', 'links': []}], widgets_values=[name, 'image'])
        gc = node(type='GetVideoComponents', pos=[-950, 4000 + 200 * k], size=[260, 90], title=f'original ending {k} frames/audio',
                  inputs=[{'name': 'video', 'type': 'VIDEO', 'link': None}],
                  outputs=[{'name': 'images', 'type': 'IMAGE', 'links': []}, {'name': 'audio', 'type': 'AUDIO', 'links': []},
                           {'name': 'fps', 'type': 'FLOAT', 'links': []}], widgets_values=[])
        ag = node(type='MiniMaxH3AddGuide', pos=[400, 900 + 200 * k], size=[300, 160], title='starts on your original ending (frame 0)',
                  inputs=[{'name': 'positive', 'type': 'CONDITIONING', 'link': None}, {'name': 'vae', 'type': 'VAE', 'shape': 7, 'link': None},
                          {'name': 'audio_vae', 'type': 'VAE', 'shape': 7, 'link': None}, {'name': 'latent', 'type': 'LATENT', 'link': None},
                          {'name': 'image', 'type': 'IMAGE', 'shape': 7, 'link': None}, {'name': 'audio', 'type': 'AUDIO', 'shape': 7, 'link': None}],
                  outputs=[{'name': 'positive', 'type': 'CONDITIONING', 'links': []}], widgets_values=[0])
        link(lv, 0, gc, 0, 'VIDEO')
        old = next(l for l in g['links'] if l[3] == 126 and l[4] == 1)          # H3 positive -> BasicGuider: goes through the guide now
        g['links'].remove(old); src = next(n for n in g['nodes'] if n['id'] == old[1])
        src['outputs'][old[2]]['links'] = [x for x in src['outputs'][old[2]]['links'] if x != old[0]]
        link(src, old[2], ag, 0, 'CONDITIONING')
        link(by[119], 0, ag, 1, 'VAE'); link(by[120], 0, ag, 2, 'VAE'); link(h3, 1, ag, 3, 'LATENT')
        link(gc, 0, ag, 4, 'IMAGE'); link(gc, 1, ag, 5, 'AUDIO')
        link(ag, 0, by[126], 1, 'CONDITIONING')
    if loras:
        unet = by[127]; consumers = [l for l in g['links'] if l[1] == 127]
        g['links'] = [l for l in g['links'] if l[1] != 127]; unet['outputs'][0]['links'] = []
        prev = unet
        for k, (name, strength) in enumerate(loras):
            ln = node(type='LoraLoaderModelOnly', pos=[20, 1500 + 120 * k], size=[320, 82], title=f'LoRA {k}: {name[:40]}',
                      inputs=[{'name': 'model', 'type': 'MODEL', 'link': None}], outputs=[{'name': 'MODEL', 'type': 'MODEL', 'links': []}],
                      widgets_values=[name, float(strength)])
            link(prev, 0, ln, 0, 'MODEL'); prev = ln
        for l in consumers:
            dst = next(n for n in g['nodes'] if n['id'] == l[3]); link(prev, 0, dst, l[4], 'MODEL')
    if pose:                                                     # 🎭 movement from YOUR video: stick figures -> ControlNet on the H3 model
        import studio_pose_v0921 as ps
        M, I = lambda t: {'name': t, 'type': t, 'links': []}, lambda nm, t: {'name': nm, 'type': t, 'link': None}
        mp = node(type='ModelPatchLoader', pos=[20, 2600], size=[320, 60], title='🎭 H3 Fun ControlNet Union (bf16)',
                  outputs=[M('MODEL_PATCH')], widgets_values=[ps.MODELS['controlnet'][1]])
        lv = node(type='LoadVideo', pos=[-1300, 5200], size=[320, 300], title=f'YOUR movement video: {Path(pose["name"]).name[:40]}',
                  outputs=[M('VIDEO')], widgets_values=[pose['name'], 'image'])
        gc = node(type='GetVideoComponents', pos=[-950, 5200], size=[260, 90], title='movement video frames',
                  inputs=[I('video', 'VIDEO')], outputs=[M('IMAGE'), M('AUDIO'), {'name': 'fps', 'type': 'FLOAT', 'links': []}], widgets_values=[])
        det = node(type='UNETLoader', pos=[-950, 5400], size=[320, 82], title='🎭 person detector (RT-DETR)', outputs=[M('MODEL')],
                   widgets_values=[ps.MODELS['detector'][1], 'default'])
        ck = node(type='CheckpointLoaderSimple', pos=[-950, 5550], size=[320, 98], title='🎭 SDPose', outputs=[M('MODEL'), M('CLIP'), M('VAE')],
                  widgets_values=[ps.MODELS['sdpose'][1]])
        rt = node(type='RTDETR_detect', pos=[-600, 5400], size=[300, 130], title='🎭 find the people', inputs=[I('model', 'MODEL'), I('image', 'IMAGE')],
                  outputs=[M('BBOX')], widgets_values=[0.5, 'person', ps.MAX_PEOPLE])
        kp = node(type='SDPoseKeypointExtractor', pos=[-300, 5400], size=[300, 130], title='🎭 read their poses',
                  inputs=[I('model', 'MODEL'), I('vae', 'VAE'), I('image', 'IMAGE'), I('bboxes', 'BBOX')], outputs=[M('KEYPOINTS')], widgets_values=[16])
        dr = node(type='SDPoseDrawKeypoints', pos=[0, 5400], size=[300, 250], title='🎭 stick figures', inputs=[I('keypoints', 'KEYPOINTS')],
                  outputs=[M('IMAGE')], widgets_values=[ps.DRAW[k] for k in cg.WIDGETS['SDPoseDrawKeypoints']])
        cn = node(type='MiniMaxH3FunControlNetApply', pos=[400, 2600], size=[320, 150], title='🎭 move like YOUR video',
                  inputs=[I('model', 'MODEL'), I('model_patch', 'MODEL_PATCH'), I('vae', 'VAE'), I('control_video', 'IMAGE')],
                  outputs=[M('MODEL')], widgets_values=[float(pose.get('strength', ps.DEFAULT_STRENGTH)), 0.0, ps.END_PERCENT])
        link(lv, 0, gc, 0, 'VIDEO'); link(det, 0, rt, 0, 'MODEL'); link(gc, 0, rt, 1, 'IMAGE')
        link(ck, 0, kp, 0, 'MODEL'); link(ck, 2, kp, 1, 'VAE'); link(gc, 0, kp, 2, 'IMAGE'); link(rt, 0, kp, 3, 'BBOX')
        link(kp, 0, dr, 0, 'KEYPOINTS'); link(mp, 0, cn, 1, 'MODEL_PATCH'); link(by[119], 0, cn, 2, 'VAE'); link(dr, 0, cn, 3, 'IMAGE')
        into = [l for l in g['links'] if l[3] in (124, 126) and l[5] == 'MODEL']         # what the scheduler and the guider sample with
        src_node = next(n for n in g['nodes'] if n['id'] == into[0][1]); src_slot = into[0][2]
        for l in into:
            g['links'].remove(l); sn = next(n for n in g['nodes'] if n['id'] == l[1])
            sn['outputs'][l[2]]['links'] = [x for x in sn['outputs'][l[2]]['links'] if x != l[0]]
        link(src_node, src_slot, cn, 0, 'MODEL')
        for l in into: link(cn, 0, by[l[3]], l[4], 'MODEL')
    h3['title'] = 'Director Studio shot | original references only'
    by[221]['title'] = f'SAVE {prefix}'
    g['last_node_id'] = max(n['id'] for n in g['nodes']); g['last_link_id'] = max(l[0] for l in g['links'])
    out = cg.derive_workflow(g, prompt=prompt, seed=seed, prefix=prefix)
    if int(length) != 124:                                   # generation length (124 = the proven ~5.2 s)
        h3n = next(n for n in out['nodes'] if n['type'] == 'MiniMaxH3ReferenceToVideo'); h3n['widgets_values'][3] = int(length)
    if steps is not None:                                    # V0.9.35: sampling steps (template 20; 25 helps motion)
        st_ = int(steps)
        if not 8 <= st_ <= 60: raise ValueError('STEPS_OUT_OF_RANGE: 8 to 60')
        sch = next(n for n in out['nodes'] if n['type'] == 'BasicScheduler'); sch['widgets_values'][1] = st_
    if sampler is not None:                                  # V0.9.35: 'euler' (template) or 'res_multistep' (official H3 template)
        if sampler not in SAMPLERS: raise ValueError('UNKNOWN_SAMPLER: ' + str(sampler))
        ks = next(n for n in out['nodes'] if n['type'] == 'KSamplerSelect'); ks['widgets_values'][0] = sampler
    return out

SAMPLERS = ('euler', 'res_multistep')

OPTIONAL_NODES = {'LoadVideo': 'reference clips', 'GetVideoComponents': 'reference clips', 'LoadAudio': 'voice recordings',
                  'LoraLoaderModelOnly': 'LoRAs', 'MiniMaxH3AddGuide': 'starting an extension on your original ending'}

def preflight(fetch):
    """Check a representative Director workflow (2 stills, a reference clip, a voice, a LoRA) against what THIS ComfyUI
    offers: every node type exists and every input the Director sets is one the node accepts. fetch(class) -> the node's
    /object_info entry or None. Returns {'checked': n_nodes, 'problems': [...]}; a problem marked 'optional' only
    disables that feature (clips, voices, LoRAs). Nothing is queued."""
    g = build_graph(['preflight_a.png', 'preflight_b.png'], 'Preflight check of the Director workflow, nothing is generated. ' * 2, 1,
                    'Studio_preflight', video_names=['preflight_clip.mp4'], audio_names=['preflight_voice.wav'],
                    loras=[('preflight.safetensors', 0.8)], anchor_names=['preflight_anchor.mp4'])
    api = cg.to_api_prompt(g)
    problems, checked, seen = [], set(), set()
    for nid, node in sorted(api.items()):
        ct = node['class_type']
        info = fetch(ct)
        feature = OPTIONAL_NODES.get(ct)
        if info is None:
            if ct not in seen: problems.append({'node': ct, 'problem': 'this node is not installed in your ComfyUI', 'optional': feature})
            seen.add(ct); continue
        spec = info.get('input') or {}
        names = set()
        for sec in ('required', 'optional'):
            for key, v in (spec.get(sec) or {}).items():
                names.add(key)
                # VideoHelperSuite lists per-format settings (pix_fmt, crf, save_metadata, trim_to_audio...) under the
                # 'format' input as {'formats': {format: [[name, ...], ...]}}; they're real inputs it reads.
                if isinstance(v, (list, tuple)) and len(v) > 1 and isinstance(v[1], dict) and isinstance(v[1].get('formats'), dict):
                    for widgets in v[1]['formats'].values():
                        for w in widgets or []:
                            if isinstance(w, (list, tuple)) and w and isinstance(w[0], str): names.add(w[0])
        if not names: continue                                              # this ComfyUI doesn't describe its inputs: can't verify
        checked.add(ct)
        for k in node['inputs']:
            if k in names or k.split('.')[0] in names: continue
            feat = feature or ('voice recordings' if k.startswith('ref_audios') else 'reference clips' if k.startswith(('ref_videos', 'ref_video_audios')) else None)
            problems.append({'node': ct, 'problem': f'input "{k}" is not listed by this node; ComfyUI will ignore that setting', 'optional': feat or 'that one setting', 'note': True})
        for k in (spec.get('required') or {}):
            if k not in node['inputs'] and not any(x.split('.')[0] == k for x in node['inputs']):
                problems.append({'node': ct, 'problem': f'required input "{k}" is not set by the Director', 'optional': feature})
    return {'checked': len(checked), 'problems': problems}
