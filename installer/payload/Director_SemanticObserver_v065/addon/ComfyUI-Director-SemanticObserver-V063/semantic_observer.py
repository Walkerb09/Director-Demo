"""Director V0.6.5: saved-video microphone GEOMETRY observer (SHADOW ONLY).

Reads generated or edited video for observation; cannot feed pixels to H3, edit cuts,
write Director memory, or convert model guesses into authoritative continuity.
"""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess, time, urllib.request, uuid
from pathlib import Path

MODEL='Qwen/Qwen3-VL-8B-Instruct'
QWEN='/workspace/h3-qwen-venv/bin/python'
RESULTS=Path('/workspace/DirectorSemanticObserverV065/results')
VERSION='director-geometry-semantic-observer-0.6.5'
TARGETS=('PRESENTER','GRANDPA','NEITHER','UNCERTAIN')
HOLDERS=('PRESENTER','GRANDPA','UNCERTAIN')
CONFIDENCES=('HIGH','MEDIUM','LOW')
PROMPT=(
    'These two images show the EXACT SAME single video frame. This is a hallway interview: '
    'the young woman is PRESENTER and the older man is GRANDPA. '
    'Find the dark microphone FOAM HEAD, not merely the handle. '
    'Answer separately: (1) Who physically HOLDS the microphone handle? '
    '(2) Whose MOUTH is the foam microphone HEAD nearest IN THIS FRAME? '
    'The holder and the target can be different people. '
    'Choose TIP_TARGET=NEITHER only if the head is clearly away from both mouths; '
    'UNCERTAIN if obscured or ambiguous. TIP_VISIBLE=YES only when foam head is actually visible. '
    'Do not guess from who seems to be speaking; do not infer actions from other frames. '
    'Output exactly one line: HOLDER=... | TIP_TARGET=... | TIP_VISIBLE=... | TIP_Q=... . '
    'Replace every ... with ONE of the allowed labels. '
    'HOLDER: PRESENTER,GRANDPA,UNCERTAIN. TIP_TARGET: PRESENTER,GRANDPA,NEITHER,UNCERTAIN. '
    'TIP_VISIBLE: YES,NO,UNCERTAIN. TIP_Q: HIGH,MEDIUM,LOW.'
)


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as inp:
        for b in iter(lambda:inp.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def media_meta(video):
    cp=subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
        '-show_entries','stream=width,height,nb_read_frames,avg_frame_rate','-of','json',str(video)],
        text=True,capture_output=True,timeout=60)
    if cp.returncode:raise RuntimeError('ffprobe failed: '+cp.stderr[-500:])
    streams=json.loads(cp.stdout).get('streams') or []
    if len(streams)!=1:raise ValueError('Expected one video stream')
    s=streams[0];n,d=(int(a) for a in s['avg_frame_rate'].split('/'))
    data={'frames':int(s['nb_read_frames']),'width':int(s['width']),
          'height':int(s['height']),'fps':n/d}
    if n<=0 or d<=0 or any(data[x]<=0 for x in ('frames','width','height','fps')) or data['frames']>100000:
        raise ValueError('Unsupported or missing video metadata')
    return data


def frame_indices(frame_count,samples=8):
    if not isinstance(frame_count,int) or frame_count<2:raise ValueError('Need 2 or more frames')
    if not isinstance(samples,int) or not 2<=samples<=24:raise ValueError('Samples must be 2-24')
    count=min(frame_count,samples)
    idx=[i*(frame_count-1)//(count-1) for i in range(count)]
    return validate_indices(idx,frame_count)


def validate_indices(indices,frame_count):
    if not isinstance(indices,list) or not 2<=len(indices)<=24 or any(type(v) is not int for v in indices):
        raise ValueError('Expected 2-24 integer sample frames')
    if indices!=sorted(set(indices)) or indices[0]<0 or indices[-1]>=frame_count:
        raise ValueError('Frame indices must be unique, ordered, within source')
    return indices


def decode_selected(path,meta,idx):
    import numpy as np
    validate_indices(idx,meta['frames'])
    expr='+'.join('eq(n\\,%d)'%f for f in idx)
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','2',
         '-i',str(path),'-map','0:v:0','-vsync','0','-vf','select='+expr,
         '-pix_fmt','rgb24','-f','rawvideo','pipe:1']
    cp=subprocess.run(cmd,capture_output=True,timeout=240)
    if cp.returncode:raise RuntimeError('ffmpeg failed: '+cp.stderr.decode('utf8','replace')[-700:])
    framebytes=meta['width']*meta['height']*3
    if len(cp.stdout)!=framebytes*len(idx):
        raise RuntimeError('Decoded frame bytes mismatch; refusing to mislabel frames')
    frames=np.frombuffer(cp.stdout,dtype=np.uint8).reshape(len(idx),meta['height'],meta['width'],3)
    return {f:frames[i].copy() for i,f in enumerate(idx)}


def views(frame):
    from PIL import Image
    im=Image.fromarray(frame,'RGB');w,h=im.size
    def scaled(x,mw,mh):
        r=x.copy();r.thumbnail((mw,mh),Image.Resampling.LANCZOS);return r
    lo=int(h*.155);hi=int(h*.70)
    if hi<=lo:raise ValueError('Invalid upper-person crop')
    return (scaled(im,384,672),scaled(im.crop((0,lo,w,hi)),768,650))


def messages(pair):
    return [{'role':'user','content':[
        {'type':'text','text':'WIDE SCENE, SAME FRAME'},
        {'type':'image','image':pair[0]},
        {'type':'text','text':'CLOSER SCENE, SAME FRAME'},
        {'type':'image','image':pair[1]},
        {'type':'text','text':PROMPT}]}]


def parse_reply(reply):
    fields={};errors=[]
    if not isinstance(reply,str) or '\n' in reply.strip():
        return {'valid':False,'labels':{},'errors':['Expected exactly one line']}
    for part in reply.strip().split('|'):
        match=re.fullmatch(r'\s*(HOLDER|TIP_TARGET|TIP_VISIBLE|TIP_Q)\s*=\s*([A-Za-z_]+)\s*',part)
        if not match:errors.append('Malformed field '+part[:80]);continue
        k,v=match.groups()
        if k in fields:errors.append('Duplicate '+k)
        fields[k]=v.upper()
    if set(fields)!={'HOLDER','TIP_TARGET','TIP_VISIBLE','TIP_Q'}:errors.append('Missing or extra fields')
    if fields.get('HOLDER') not in HOLDERS:errors.append('Invalid holder')
    if fields.get('TIP_TARGET') not in TARGETS:errors.append('Invalid tip target')
    if fields.get('TIP_VISIBLE') not in ('YES','NO','UNCERTAIN'):errors.append('Invalid tip visibility')
    if fields.get('TIP_Q') not in CONFIDENCES:errors.append('Invalid confidence')
    if fields.get('TIP_VISIBLE')!='YES' and fields.get('TIP_TARGET')!='UNCERTAIN':
        errors.append('Target must be UNCERTAIN if foam head is not clearly visible')
    if fields.get('TIP_TARGET')=='UNCERTAIN' and fields.get('TIP_Q')=='HIGH':
        errors.append('Uncertain target cannot have HIGH confidence')
    return {'valid':not errors,'labels':fields,'errors':errors}


def audit(observations,indices):
    states=[];alerts=[];transitions=[]
    for f in indices:
        parsed=observations.get(str(f),{}).get('parsed',{})
        labels=parsed.get('labels',{}) if parsed.get('valid') else {}
        holder=labels.get('HOLDER');target=labels.get('TIP_TARGET')
        visible=labels.get('TIP_VISIBLE');q=labels.get('TIP_Q')
        usable=bool(parsed.get('valid') and visible=='YES' and target!='UNCERTAIN' and q in ('HIGH','MEDIUM'))
        states.append({'frame':f,'holder_model_claim':holder,'tip_target_model_claim':target,
            'tip_visible_model_claim':visible,'tip_confidence_model_claim':q,
            'holder_differs_from_target_model_claim':bool(holder in HOLDERS[:2] and target in TARGETS[:2] and holder!=target),
            'usable_for_shadow_comparison':usable,'semantic_truth_verified':False})
        if not parsed.get('valid'):alerts.append({'frame':f,'reason':'INVALID_MODEL_OUTPUT','errors':parsed.get('errors',[])})
        elif not usable:alerts.append({'frame':f,'reason':'AMBIGUOUS_OR_LOW_CONFIDENCE_MIC_TIP'})
    for a,b in zip(states,states[1:]):
        good=a['usable_for_shadow_comparison'] and b['usable_for_shadow_comparison']
        status=('UNKNOWN' if not good else
                'MODEL_REPORTS_DIFFERENCE' if a['tip_target_model_claim']!=b['tip_target_model_claim'] else 'MODEL_REPORTS_SAME')
        transitions.append({'from_frame':a['frame'],'to_frame':b['frame'],
            'from_model_claim':a['tip_target_model_claim'],'to_model_claim':b['tip_target_model_claim'],
            'status':status,'intervening_motion_not_observed':True,'semantic_truth_verified':False})
    return {'states':states,'sampled_transitions':transitions,'alerts':alerts,
        'endpoint_target_model_claim':states[-1]['tip_target_model_claim'] if states[-1]['usable_for_shadow_comparison'] else None,
        'endpoint_is_independently_verified':False,
        'not_a_continuous_motion_track':True,'claims_semantically_verified':False,
        'automatic_editing_authorized':False,'continuity_handoff_authorized':False,
        'h3_visual_conditioning_authorized':False}


def processor_inputs(proc,pair):
    inputs=proc.apply_chat_template(messages(pair),tokenize=True,add_generation_prompt=True,
                                     return_dict=True,return_tensors='pt')
    inputs.pop('token_type_ids',None)
    if 'pixel_values' not in inputs or 'image_grid_thw' not in inputs or len(inputs['image_grid_thw'])!=2:
        raise RuntimeError('Qwen processor must produce two image grids')
    return inputs


def worker(request_file):
    req=json.loads(Path(request_file).read_text())
    if req.get('mode') not in ('preprocess','qwen'):raise ValueError('Invalid worker mode')
    path=Path(req['source']).resolve(strict=True)
    if sha256(path)!=req['source_sha256']:raise ValueError('Video hash changed before Qwen')
    meta=media_meta(path)
    if meta!=req['meta']:raise ValueError('Video metadata changed before Qwen')
    idx=validate_indices(req['frame_indices'],meta['frames'])
    if req.get('selection_mode')=='uniform' and idx!=frame_indices(meta['frames'],len(idx)):
        raise ValueError('Uniform sampling list changed')
    if req.get('selection_mode') not in ('uniform','explicit'):raise ValueError('Unknown selection mode')
    frames=decode_selected(path,meta,idx)
    from transformers import AutoProcessor
    proc=AutoProcessor.from_pretrained(req['model_id'])
    model=None
    if req['mode']=='qwen':
        import torch
        from transformers import Qwen3VLForConditionalGeneration
        if not torch.cuda.is_available():raise RuntimeError('CUDA is unavailable')
        free,_=torch.cuda.mem_get_info(0)
        print('V065_QWEN_VRAM_GiB=%.1f'%(free/2**30),flush=True)
        if free<22*2**30:raise RuntimeError('At least 22 GiB free GPU memory required')
        model=Qwen3VLForConditionalGeneration.from_pretrained(
            req['model_id'],dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa')
        model.eval();print('V065_QWEN_READY',flush=True)
    obs={};hashes={}
    for f in idx:
        pair=views(frames[f]);hashes[str(f)]=[hashlib.sha256(im.tobytes()).hexdigest() for im in pair]
        inputs=processor_inputs(proc,pair)
        if model is None:continue
        print('V065_OBSERVING frame=%d'%f,flush=True)
        import torch
        n=inputs['input_ids'].shape[-1]
        with torch.inference_mode():token_ids=model.generate(**inputs.to(model.device),do_sample=False,max_new_tokens=110)
        reply=proc.decode(token_ids[0][n:],skip_special_tokens=True).strip()
        obs[str(f)]={'raw_reply':reply,'parsed':parse_reply(reply),
                     'image_grid_thw':inputs['image_grid_thw'].tolist()}
    report={'version':VERSION,'phase':'READ_ONLY_SAVED_VIDEO_GEOMETRY_OBSERVATION',
        'source_path':str(path),'source_sha256':req['source_sha256'],
        'origin_declared_by_operator':req['source_class'],
        'evidence_class':'SOURCE_HASH_VERIFIED_ORIGIN_NOT_INDEPENDENTLY_CERTIFIED',
        'video_meta':meta,'sampled_frames':idx,'sampling_mode':req['selection_mode'],
        'view_rgb_sha256':hashes,'preprocess_only':model is None,'model_id':req['model_id'],
        'observations':obs,'audit':audit(obs,idx) if model is not None else None,
        'audio_not_analyzed':True,'model_predictions_unverified':True,
        'director_memory_changed':False,'production_director_changed':False,
        'h3_generation':False,'h3_visual_conditioning':False,
        'working_edit_changed':False,'automatic_editing_authorized':False}
    output=Path(req['output'])/'semantic_observation.json'
    output.write_text(json.dumps(report,indent=2)+'\n')
    print('V065_REPORT '+str(output),flush=True)


def queue_idle_or_offline():
    try:
        with urllib.request.urlopen('http://127.0.0.1:18188/queue',timeout=5) as resp:q=json.load(resp)
    except Exception:
        print('V065_QUEUE_UNAVAILABLE_NO_UNLOAD',flush=True);return False
    if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
        raise RuntimeError('ComfyUI returned an invalid queue response')
    if q['queue_running'] or q['queue_pending']:raise RuntimeError('ComfyUI queue busy: wait before running Qwen')
    return True


def observe_video(source,output,qwen_python=QWEN,model_id=MODEL,samples=8,preprocess_only=False,
                  source_class='UNKNOWN_ORIGIN',allow_free=False,require_idle=True,explicit_frames=None):
    src=Path(source).expanduser().resolve(strict=True)
    if not src.is_file() or src.suffix.lower() not in ('.mp4','.mov','.mkv','.webm'):
        raise ValueError('Provide a saved video file (mp4/mov/mkv/webm)')
    if not Path(qwen_python).is_file():raise FileNotFoundError('Missing Qwen environment '+qwen_python)
    meta=media_meta(src)
    idx=validate_indices(explicit_frames,meta['frames']) if explicit_frames is not None else frame_indices(meta['frames'],samples)
    sh=sha256(src)
    dst=Path(output).resolve()
    if dst==src.parent or dst.is_relative_to(src.parent):raise ValueError('Output must be outside source video directory')
    if dst.exists():raise FileExistsError('Output directory exists, refuse overwrite')
    if source_class not in ('UNKNOWN_ORIGIN','H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY'):
        raise ValueError('Invalid source class')
    idle=queue_idle_or_offline() if require_idle else False
    if allow_free:
        if not (require_idle and idle):raise RuntimeError('Refuse GPU unload without confirmed idle queue')
        req=urllib.request.Request('http://127.0.0.1:18188/free',
            data=json.dumps({'unload_models':True,'free_memory':True}).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=30):pass
        print('V065_IDLE_MODELS_UNLOADED_NO_RESTART',flush=True)
    dst.mkdir(parents=True)
    request={'mode':'preprocess' if preprocess_only else 'qwen','source':str(src),'source_sha256':sh,
        'meta':meta,'frame_indices':idx,'selection_mode':'explicit' if explicit_frames is not None else 'uniform',
        'source_class':source_class,'model_id':model_id,'output':str(dst)}
    (dst/'request.json').write_text(json.dumps(request,indent=2)+'\n')
    logfile=dst/'worker.log'
    with logfile.open('w') as log:
        p=subprocess.Popen([str(qwen_python),'-u',str(Path(__file__).resolve()),
             '--worker-request',str(dst/'request.json')],stdout=log,stderr=subprocess.STDOUT)
        began=time.monotonic();timeout=420 if preprocess_only else 2400
        while p.poll() is None:
            if time.monotonic()-began>timeout:
                p.kill();p.wait();raise TimeoutError('Worker timeout; see '+str(logfile))
            time.sleep(5)
            print('V065_RUNNING pid=%d elapsed=%ds'%(p.pid,int(time.monotonic()-began)),flush=True)
        if p.returncode:raise RuntimeError('Qwen worker failed (%d). See %s'%(p.returncode,logfile))
    return dst/'semantic_observation.json'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--video',type=Path);ap.add_argument('--output',type=Path)
    ap.add_argument('--qwen-python',default=QWEN);ap.add_argument('--model-id',default=MODEL)
    ap.add_argument('--samples',type=int,default=8)
    ap.add_argument('--frame-indices',default='',help='Optional comma-separated exact source frames for a diagnostic')
    ap.add_argument('--source-class',choices=('UNKNOWN_ORIGIN','H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY'),default='UNKNOWN_ORIGIN')
    ap.add_argument('--preprocess-only',action='store_true')
    ap.add_argument('--unload-idle-comfy-models',action='store_true')
    ap.add_argument('--worker-request',type=Path)
    args=ap.parse_args()
    if args.worker_request:return worker(args.worker_request)
    if args.video is None or args.output is None:ap.error('Provide --video and --output')
    explicit=None
    if args.frame_indices:
        try:explicit=[int(t.strip()) for t in args.frame_indices.split(',')]
        except ValueError:ap.error('--frame-indices must be comma-separated integers')
    report=observe_video(args.video,args.output,args.qwen_python,args.model_id,args.samples,
             args.preprocess_only,args.source_class,args.unload_idle_comfy_models,
             explicit_frames=explicit)
    print('V065_SUCCESS '+str(report),flush=True)

if __name__=='__main__':main()
