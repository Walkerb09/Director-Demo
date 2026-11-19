#!/usr/bin/env python3
"""Director 0.7.2.2: cast-profiled, cut-aware single-load Qwen shadow observer.

Only reads generated video for observation. Never edits video, sets production flags,
updates Director memory or provides H3 conditioning. Opt-in, queue-idle standalone worker.
"""
from __future__ import annotations
import argparse, datetime, hashlib, importlib.util, itertools, json, os, re, subprocess, sys, time, urllib.request, uuid, zipfile
from pathlib import Path

VERSION='director-profiled-rolebinary-shadow-0.7.2.2'
OUTPUT_ROOT=Path('/workspace/ComfyUI/output')
QUEUE_ROOT=Path('/workspace/DirectorSavedShotQueueV066')
V065=Path('/workspace/ComfyUI/custom_nodes/ComfyUI-Director-SemanticObserver-V063/semantic_observer.py')
DRAIN=QUEUE_ROOT/'drain_jobs.py'
CUT=QUEUE_ROOT/'cut_audit.py'
QWEN=Path('/workspace/h3-qwen-venv/bin/python')
STATIC_FIELDS={'OTHER_PERSON','MIC_VISIBLE','FRAMING','Q'}
ROLES_RX=re.compile(r'[A-Z][A-Z0-9_]{1,19}\Z')
PROFILE_RX=re.compile(r'[a-z][a-z0-9_-]{1,40}\Z')
LABELS={'OTHER_PERSON':{'YES','NO','UNCERTAIN'},'MIC_VISIBLE':{'YES','NO','UNCERTAIN'},
        'FRAMING':{'CLOSE','MEDIUM','WIDE','UNCERTAIN'},'Q':{'HIGH','MEDIUM','LOW'}}
GUARDS={'automatic_editing_authorized':False,'continuity_handoff_authorized':False,
        'h3_visual_conditioning':False,'h3_generation':False,'director_memory_changed':False,
        'production_director_changed':False}


def canonical(o): return json.dumps(o,ensure_ascii=False,sort_keys=True,indent=2)+'\n'

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def import_py(path,name):
    p=Path(path).resolve(strict=True)
    spec=importlib.util.spec_from_file_location(name,str(p))
    if not spec or not spec.loader:raise RuntimeError('Import unavailable: '+str(p))
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
    return mod


def validate_profile(profile):
    if not isinstance(profile,dict) or set(profile)!={'schema','project_id','video_name_prefix','cast','model_id'}:
        raise ValueError('Unexpected profile schema')
    if profile['schema']!='director-scene-profile-v1':raise ValueError('Unsupported profile schema')
    if not isinstance(profile['project_id'],str) or not PROFILE_RX.fullmatch(profile['project_id']):
        raise ValueError('Unsafe project ID')
    prefix=profile['video_name_prefix']
    if not isinstance(prefix,str) or not (3<=len(prefix)<=80) or not re.fullmatch(r'[a-zA-Z0-9_-]+',prefix):
        raise ValueError('Unsafe filename prefix')
    if profile['model_id']!='Qwen/Qwen3-VL-8B-Instruct':raise ValueError('Unexpected model')
    cast=profile['cast']
    if not isinstance(cast,list) or not 1<=len(cast)<=4:raise ValueError('Expected 1–4 cast descriptors')
    ids=[]
    for item in cast:
        if not isinstance(item,dict) or set(item)!={'id','description'}:raise ValueError('Malformed cast descriptor')
        rid,desc=item['id'],item['description']
        if not isinstance(rid,str) or not ROLES_RX.fullmatch(rid) or rid in {'NONE','UNCERTAIN'}:
            raise ValueError('Invalid cast ID')
        if (not isinstance(desc,str) or not 8<=len(desc)<=180 or any(ord(ch)<32 for ch in desc)
            or '|' in desc or '=' in desc):raise ValueError('Invalid cast description')
        ids.append(rid)
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate cast IDs')
    return profile


def parse_reply(reply,valid_ids):
    # Role-specific answers prevent a cast list from combining a character with NONE.
    # Unknown/invalid labels are never silently corrected or promoted to facts.
    if not isinstance(valid_ids,list) or not valid_ids or len(set(valid_ids))!=len(valid_ids):
        raise ValueError('Role IDs required in configured order')
    expected={f'CAST_{rid}' for rid in valid_ids}|STATIC_FIELDS
    errors=[];fields={}
    if not isinstance(reply,str) or '\n' in reply.strip():
        return {'valid':False,'labels':{},'parsed_cast_ids':None,'errors':['Multiline or non-text response']}
    for token in reply.strip().split('|'):
        m=re.fullmatch(r'\s*([A-Z][A-Z0-9_]{0,24})\s*=\s*([A-Za-z0-9_]+)\s*',token)
        if not m:errors.append('Malformed field');continue
        k,v=m.groups();v=v.upper()
        if k in fields:errors.append('Duplicate '+k)
        fields[k]=v
    if set(fields)!=expected:errors.append('Missing or unexpected fields')
    for k,opts in LABELS.items():
        if fields.get(k) not in opts:errors.append('Invalid '+k)
    for rid in valid_ids:
        k='CAST_'+rid
        if fields.get(k) not in ('YES','NO','UNCERTAIN'):errors.append('Invalid '+k)
    # Q is a subjective global model claim, NOT fieldwise confidence.
    # A role can remain UNCERTAIN even when Q=HIGH. Never promote it to NO.
    valid=not errors
    present=[rid for rid in valid_ids if fields.get('CAST_'+rid)=='YES'] if valid else None
    complete=valid and all(fields['CAST_'+rid]!='UNCERTAIN' for rid in valid_ids)
    labels=dict(fields)
    if complete:labels['CAST_PRESENT']=','.join(present) if present else 'NONE'
    elif valid:labels['CAST_PRESENT']='UNCERTAIN'
    return {'valid':valid,'labels':labels,'parsed_cast_ids':present if complete else None,'errors':errors}


def output_choices(valid_ids):
    """Generate only canonical, explicitly keyed field strings in fixed order.

    No free-form Qwen reply can enter the primary parser. All combinations are
    permitted to prevent a grammar from inventing particular scene semantics.
    """
    if not isinstance(valid_ids, list) or not 1 <= len(valid_ids) <= 4 or len(set(valid_ids)) != len(valid_ids):
        raise ValueError('Invalid cast role list')
    for rid in valid_ids:
        if not ROLES_RX.fullmatch(rid): raise ValueError('Invalid cast role')
    keys=['CAST_'+rid for rid in valid_ids]+['OTHER_PERSON','MIC_VISIBLE','FRAMING','Q']
    options=[('YES','NO','UNCERTAIN') for _ in valid_ids]+[
        ('YES','NO','UNCERTAIN'),('YES','NO','UNCERTAIN'),
        ('CLOSE','MEDIUM','WIDE','UNCERTAIN'),('HIGH','MEDIUM','LOW')]
    for values in itertools.product(*options):
        yield ' | '.join(k+'='+v for k,v in zip(keys,values))


class TokenChoiceGrammar:
    """Token-level whitelist for the *entire* model completion (not prompt).

    A trie avoids regex post-hoc repair. Every allowed path decodes to a
    complete, key/value answer. The worker still validates the semantic claims
    separately, and human visual review is still required.
    """
    END='_end_'
    def __init__(self, tokenizer, valid_ids, prompt_len, eos_token_id):
        if type(prompt_len) is not int or prompt_len<0: raise ValueError('Invalid prompt length')
        if type(eos_token_id) is not int or eos_token_id<0: raise ValueError('Invalid EOS token')
        self.prompt_len=prompt_len;self.eos_token_id=eos_token_id
        self.root={};self.longest=0;self.count=0
        for text in output_choices(valid_ids):
            ids=tokenizer.encode(text,add_special_tokens=False)
            if not isinstance(ids,list) or not ids or any(type(i) is not int or i<0 or i==eos_token_id for i in ids):
                raise ValueError('Unexpected grammar tokenization')
            # Forbid a tokenizer that cannot faithfully represent the permitted grammar.
            if tokenizer.decode(ids,skip_special_tokens=True,clean_up_tokenization_spaces=False)!=text:
                raise ValueError('Tokenizer round-trip changes grammar')
            node=self.root
            for i in ids:node=node.setdefault(i,{})
            node[self.END]=True
            self.longest=max(self.longest,len(ids));self.count+=1
        if self.count!=len(set(output_choices(valid_ids))):raise RuntimeError('Grammar enumeration incomplete')

    def allowed(self,batch_id,input_ids):
        """Callback signature for Transformers prefix_allowed_tokens_fn."""
        if batch_id!=0:raise RuntimeError('Only batch size 1 supported')
        seq=input_ids.tolist()
        if len(seq)<self.prompt_len:raise RuntimeError('Decoder prefix shorter than prompt')
        node=self.root
        for token_id in seq[self.prompt_len:]:
            node=node.get(int(token_id))
            if node is None:raise RuntimeError('Model token outside constrained grammar')
        choices=[i for i in node if type(i) is int]
        if node.get(self.END):choices.append(self.eos_token_id)
        if not choices:raise RuntimeError('Constrained grammar dead end')
        return choices


def prompt_for(profile):
    roles=profile['cast']
    lines=[]
    for item in roles:
        rid=item['id']
        lines.append(f'CAST_{rid}=YES if this person is visibly present, NO if visibly absent, UNCERTAIN if identification unclear. Description: {item["description"]}.')
    ordered=[f'CAST_{x["id"]}=<YES or NO or UNCERTAIN>' for x in roles]
    ordered.extend(['OTHER_PERSON=<YES or NO or UNCERTAIN>','MIC_VISIBLE=<YES or NO or UNCERTAIN>',
                    'FRAMING=<CLOSE or MEDIUM or WIDE or UNCERTAIN>','Q=<HIGH or MEDIUM or LOW>'])
    return (
       'Two pictures show EXACTLY THE SAME ONE VIDEO FRAME: full shot and closer crop. '
       'Read the visible people, independently for EACH cast role. Do not assume they appear together. '
       'A person without a microphone is still a person! Cast descriptions are visual hints, not proof of identity. '
       'For EVERY listed role choose YES, NO or UNCERTAIN. No cast lists and NEVER add NONE to a role answer. '
       + ' '.join(lines)+' '
       'OTHER_PERSON=YES if there is any visible person NOT matching the listed roles; otherwise NO; UNCERTAIN if unclear. '
       'MIC_VISIBLE=YES if a physical microphone is clearly visible; NO if absent; UNCERTAIN if ambiguous. '
       'FRAMING=CLOSE for face close-up, MEDIUM for upper body or torso, WIDE for much environment/full-body; UNCERTAIN if ambiguous. '
       'Q=HIGH, MEDIUM or LOW for subjective confidence. '
       'Classify only this one image; never infer a handoff, dialogue, camera motion, off-screen people, edit intent or identity through a cut. '
       'OUTPUT STRICTLY ONE LINE, in EXACT order, separated by |. Each value is a SINGLE KEYWORD, no explanations, no slashes, no added words. '
       'Fields in order: '+' | '.join(ordered)+'. '
       'The angle-bracket phrases above describe choices; replace them with ONE selected allowed keyword each.'
    )


def segments_for(frames,cuts,max_segments=5):
    if type(frames)!=int or not 8<=frames<=300000:raise ValueError('Invalid frame count')
    if not isinstance(cuts,list) or len(cuts)>max_segments-1:raise ValueError('Too many cuts; human review required')
    if any(type(c)!=int or not 1<=c<frames for c in cuts) or cuts!=sorted(set(cuts)):
        raise ValueError('Invalid cut order')
    boundaries=[0]+cuts+[frames];out=[]
    for i,(a,e) in enumerate(zip(boundaries,boundaries[1:])):
        b=e-1;n=e-a
        if n<8:raise ValueError('Segment under 8 frames; needs manual review')
        inset=min(4,(n-4)//4)
        lo=a+inset;hi=b-inset
        arr=[lo,(lo+hi)//2,b-2,b]
        if len(set(arr))<4:
            arr=[a,a+n//3,a+(2*n)//3,b]
        if len(set(arr))!=4 or sorted(arr)!=arr:raise ValueError('Unsafe sample allocation')
        out.append({'segment_id':i+1,'bounds':[a,b],'samples':arr,'endpoint_pair':arr[-2:]})
    if len(out)*4>20:raise RuntimeError('Sampling budget exceeded')
    return out


def match_pair(a,b,valid_ids):
    keys=[f'CAST_{rid}' for rid in valid_ids]+['CAST_PRESENT','OTHER_PERSON','MIC_VISIBLE','FRAMING']
    if not a.get('valid') or not b.get('valid') or any(x['labels']['Q']=='LOW' for x in (a,b)):
        return {k:None for k in keys}
    result={}
    for k in keys:
        x,y=a['labels'].get(k),b['labels'].get(k)
        result[k]=x if x==y and x is not None and x!='UNCERTAIN' else None
    return result


def summarize_observations(segments,observations,profile):
    ids=[x['id'] for x in profile['cast']]
    expected={str(f) for s in segments for f in s['samples']}
    if set(observations)!=expected:raise ValueError('Missing/extra frame observations')
    summaries=[]
    for seg in segments:
        vals=[]
        for f in seg['samples']:
            p=observations[str(f)]['parsed']
            if p['valid'] and parse_reply(observations[str(f)]['raw_reply'],ids)!=p:
                raise ValueError('Parsing audit mismatch')
            vals.append({'frame':f,'valid':p['valid'],
                         'labels':p['labels'] if p['valid'] else None})
        a,b=seg['endpoint_pair'];pair=match_pair(observations[str(a)]['parsed'],observations[str(b)]['parsed'],ids)
        summaries.append({**seg,'sampled_states':vals,'endpoint_model_pair_agreement':pair,
                          'endpoint_model_sample_valid':bool(observations[str(b)]['parsed']['valid']),
                          'endpoint_frame_actually_observed':True,
                          'endpoint_human_verified':False,'candidate_segment_not_verified_edit':True,
                          'model_claims_not_visual_ground_truth':True})
    return summaries


def validate_job(job_file,profile,root=OUTPUT_ROOT):
    record=json.loads(Path(job_file).read_text())
    if record.get('version')!='director-saved-shot-capture-0.6.6' or record.get('phase')!='PENDING_SHADOW_OBSERVATION':
        raise ValueError('Unsupported capture job')
    for k in ('automatic_editing_authorized','continuity_handoff_authorized','director_memory_changed'):
        if record.get(k) is not False:raise ValueError('Unsafe capture guard '+k)
    if record.get('generated_footage_for_H3_visual_conditioning') is not False:raise ValueError('Unsafe H3 conditioning')
    if record.get('observation_authority')!='NONE':raise ValueError('Unsafe job authority')
    src=Path(record['source_video_path']).resolve(strict=True)
    if not src.is_relative_to(Path(root).resolve(strict=True)):raise ValueError('Video outside trusted output')
    if not src.name.startswith(profile['video_name_prefix']):raise ValueError('Video does not belong to scene profile')
    if record['source_class'] not in ('H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY','UNKNOWN_ORIGIN'):
        raise ValueError('Unexpected source class')
    if sha(src)!=record['source_sha256']:raise ValueError('Saved video SHA mismatch')
    token={k:record[k] for k in ('source_video_path','source_sha256','source_class','samples')}
    key=hashlib.sha256(json.dumps(token,sort_keys=True).encode()).hexdigest()[:24]
    if key!=record.get('job_id') or Path(job_file).name!=key+'.json':raise ValueError('Job identity mismatch')
    meta=record.get('video_meta')
    if not isinstance(meta,dict) or set(meta)!={'frames','fps','height','width'} or meta['fps']<=0:
        raise ValueError('Missing source meta')
    return record,src


def plan(job_file,profile_path,*,output_root=OUTPUT_ROOT,cut_detector=None,meta_probe=None):
    profile_file=Path(profile_path).resolve(strict=True)
    profile=validate_profile(json.loads(profile_file.read_text()))
    record,src=validate_job(job_file,profile,root=output_root)
    if meta_probe:
        if meta_probe(src)!=record['video_meta']:raise ValueError('Video metadata changed')
    else:
        helper=import_py(V065,'v0722_media_helper')
        if helper.media_meta(src)!=record['video_meta']:raise ValueError('Video metadata changed')
    if cut_detector:
        hits=cut_detector(src,record['video_meta']['fps'],record['video_meta']['frames'])
    else:
        cutmod=import_py(CUT,'v0722_cpu_cut_detector')
        hits=cutmod.scene_candidates(src,record['video_meta']['fps'],record['video_meta']['frames'])
    cut_frames=sorted(set(h['frame_estimate'] for h in hits))
    segments=segments_for(record['video_meta']['frames'],cut_frames)
    return {'profile_path':str(profile_file),'profile':profile,'profile_sha256':sha(profile_file),
            'job_file':str(Path(job_file).resolve(strict=True)),'job_sha256':sha(job_file),
            'record':record,'source':str(src),'cut_candidates':hits,'segments':segments,
            'samples':[f for s in segments for f in s['samples']]}


def require_idle():
    with urllib.request.urlopen('http://127.0.0.1:18188/queue',timeout=10) as r:q=json.load(r)
    if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
        raise RuntimeError('Unknown ComfyUI queue response')
    if q['queue_running'] or q['queue_pending']:raise RuntimeError('ComfyUI busy: Qwen not started')


def worker(request_path):
    req=json.loads(Path(request_path).read_text())
    if req.get('version')!=VERSION:raise ValueError('Worker version mismatch')
    profile=validate_profile(req['profile'])
    src=Path(req['source']).resolve(strict=True)
    if sha(src)!=req['source_sha256']:raise ValueError('Worker source hash mismatch')
    helper=import_py(V065,'v0722_decode_helper')
    if helper.media_meta(src)!=req['meta']:raise ValueError('Worker metadata mismatch')
    indices=req['samples'];helper.validate_indices(indices,req['meta']['frames'])
    frames=helper.decode_selected(src,req['meta'],indices)
    import torch
    from transformers import AutoProcessor,Qwen3VLForConditionalGeneration
    if not torch.cuda.is_available():raise RuntimeError('Qwen CUDA unavailable')
    free,_=torch.cuda.mem_get_info(0)
    if free<22*2**30:raise RuntimeError('At least 22 GiB free GPU memory required')
    processor=AutoProcessor.from_pretrained(profile['model_id'])
    model=Qwen3VLForConditionalGeneration.from_pretrained(
        profile['model_id'],dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa')
    model.eval();print('V0722_QWEN_SINGLE_LOAD_READY',flush=True)
    text=prompt_for(profile);ids=[x['id'] for x in profile['cast']]
    observations={};grammar=None
    for f in indices:
        pair=helper.views(frames[f])
        msg=[{'role':'user','content':[{'type':'text','text':'FULL FRAME'}, {'type':'image','image':pair[0]},
                  {'type':'text','text':'CLOSER CROP SAME FRAME'}, {'type':'image','image':pair[1]},
                  {'type':'text','text':text}]}]
        inp=processor.apply_chat_template(msg,tokenize=True,add_generation_prompt=True,return_dict=True,return_tensors='pt')
        inp.pop('token_type_ids',None)
        if len(inp.get('image_grid_thw',[]))!=2:raise RuntimeError('Expected two Qwen views')
        n=inp['input_ids'].shape[-1]
        if grammar is None:
            eos_id=processor.tokenizer.eos_token_id
            grammar=TokenChoiceGrammar(processor.tokenizer,ids,n,eos_id)
            print('V0722_GRAMMAR_OPTIONS',grammar.count,'MAX_TOKENS',grammar.longest,flush=True)
        else:
            grammar.prompt_len=n  # frame-specific image tokens may change prompt length
        with torch.inference_mode():
            out=model.generate(**inp.to(model.device),do_sample=False,
                max_new_tokens=grammar.longest+2,
                prefix_allowed_tokens_fn=grammar.allowed)
        reply=processor.decode(out[0][n:],skip_special_tokens=True,clean_up_tokenization_spaces=False).strip()
        observations[str(f)]={'raw_reply':reply,'parsed':parse_reply(reply,ids),
                              'views_rgb_sha256':[hashlib.sha256(x.tobytes()).hexdigest() for x in pair]}
        print('V0722_FRAME',f,reply,flush=True)
    dst=Path(req['output_tmp'])
    with dst.open('x') as fp:fp.write(canonical(observations))


def save_contact(source,meta,samples,segments,observations,path):
    from PIL import Image,ImageDraw
    helper=import_py(V065,'v0722_contact_decoder')
    rgb=helper.decode_selected(Path(source),meta,samples)
    width,height=216,378;panel_h=448;columns=4
    canvas=Image.new('RGB',(columns*width,((len(samples)+columns-1)//columns)*panel_h),(22,25,30))
    d=ImageDraw.Draw(canvas)
    lookup={f:s['segment_id'] for s in segments for f in s['samples']}
    for i,f in enumerate(samples):
        x=(i%columns)*width;y=(i//columns)*panel_h
        im=Image.fromarray(rgb[f],'RGB');im.thumbnail((width,height),Image.Resampling.LANCZOS)
        canvas.paste(im,(x+(width-im.width)//2,y+(height-im.height)//2))
        p=observations[str(f)]['parsed'];l=p['labels'] if p['valid'] else {}
        d.text((x+3,y+381),f'SEG {lookup[f]} F{f} '+('OK' if p['valid'] else 'INVALID'),fill=(240,240,240))
        d.text((x+3,y+402),'CAST '+str(l.get('CAST_PRESENT','?'))[:23],fill=(221,222,240))
        d.text((x+3,y+421),'MIC '+str(l.get('MIC_VISIBLE','?'))+' / '+str(l.get('FRAMING','?')),fill=(221,222,240))
    canvas.save(path,format='JPEG',quality=89)


def archive_stage(stage):
    if not stage.exists() and not stage.is_symlink():return
    allowed={'request.json','worker.log','observations.json','scene_review_v0722.jpg','scene_review_v0722.json','scene_review_v0722_bundle.zip'}
    if stage.is_symlink() or not stage.is_dir() or not list(stage.iterdir()):
        raise RuntimeError('Unexpected stage path')
    if any(x.name not in allowed or x.is_symlink() or not x.is_file() for x in stage.iterdir()):
        raise RuntimeError('Unknown staged artifact; manual inspection required')
    dst=stage.with_name(stage.name+'_failed_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex[:8])
    stage.rename(dst);print('V0722_FAILED_STAGE_ARCHIVED',dst,flush=True)


def output_report(plan,observations):
    summary=summarize_observations(plan['segments'],observations,plan['profile'])
    return {'version':VERSION,'phase':'SHADOW_SINGLEPASS_SCENE_STATE',
        'profile_id':plan['profile']['project_id'],'profile_sha256':plan['profile_sha256'],
        'cast_descriptors_are_operator_hints_not_identity_ground_truth':True,
        'source_video_path':plan['source'],'source_sha256':plan['record']['source_sha256'],
        'video_meta':plan['record']['video_meta'],'job_id':plan['record']['job_id'],
        'job_sha256':plan['job_sha256'],'source_class':plan['record']['source_class'],
        'candidate_cuts':plan['cut_candidates'],'segments':summary,'observations':observations,
        'single_qwen_model_load_per_video':True,'role_specific_binary_presence_fields':True,
         'output_grammar_token_constrained':True,'global_confidence_not_fieldwise':True,
         'human_verification_still_required':True,'video_audio_not_analyzed':True,
        'possible_cut_false_positives_and_missed_subtle_cuts':True,
        'sampled_stills_not_proof_of_continuous_motion':True,
        'cross_cut_identity_continuity_verified':False,
        'cross_cut_action_inference_allowed':False,'human_review_required':True,
        'review_status':'HUMAN_REVIEW_REQUIRED', **GUARDS}


def publish(plan,observations,dest):
    dest=Path(dest);parent=dest.parent
    if dest.exists() or dest.is_symlink():raise FileExistsError('Review already exists; no overwrite')
    report=output_report(plan,observations)
    stage=parent/'scene_review_v0722_stage'
    if not stage.is_dir() or stage.is_symlink():raise RuntimeError('Expected owned stage')
    sheet=stage/'scene_review_v0722.jpg'
    save_contact(plan['source'],plan['record']['video_meta'],plan['samples'],plan['segments'],observations,sheet)
    # Hash recheck immediately before publishing; staging remains for inspection if source changed.
    if sha(plan['source'])!=plan['record']['source_sha256'] or sha(plan['job_file'])!=plan['job_sha256'] or sha(plan['profile_path'])!=plan['profile_sha256']:
        raise RuntimeError('Source, job or profile changed during observation')
    p=stage/'scene_review_v0722.json'
    p.write_text(canonical(report))
    zipout=stage/'scene_review_v0722_bundle.zip'
    manifest={'version':VERSION,'files':{'scene_review_v0722.json':sha(p),'scene_review_v0722.jpg':sha(sheet)}}
    with zipfile.ZipFile(zipout,'x',zipfile.ZIP_DEFLATED) as z:
        z.write(p,p.name);z.write(sheet,sheet.name);z.writestr('bundle_manifest.json',canonical(manifest))
    for f in (dest,parent/'scene_review_v0722.jpg',parent/'scene_review_v0722_bundle.zip',parent/'scene_review_v0722_worker.log',parent/'scene_review_v0722_request.json',parent/'scene_review_v0722_observations.json'):
        if f.exists() or f.is_symlink():raise FileExistsError('Published file exists: '+str(f))
    os.link(p,dest);os.link(sheet,parent/sheet.name);os.link(zipout,parent/zipout.name)
    os.link(stage/'worker.log',parent/'scene_review_v0722_worker.log')
    os.link(stage/'request.json',parent/'scene_review_v0722_request.json')
    os.link(stage/'observations.json',parent/'scene_review_v0722_observations.json')
    for f in stage.iterdir():f.unlink()
    stage.rmdir()
    return dest,parent/'scene_review_v0722_bundle.zip'


def run(job_file,profile_path,*,output_root=OUTPUT_ROOT,run_gpu=False,
        cut_detector=None,meta_probe=None,qwen_python=QWEN,timeout=2400,root=QUEUE_ROOT):
    ctx=plan(job_file,profile_path,output_root=output_root,cut_detector=cut_detector,meta_probe=meta_probe)
    dest=Path(root)/'results'/ctx['record']['job_id']/'scene_review_v0722.json'
    print('V0722_PROFILE',ctx['profile']['project_id'],'JOB',ctx['record']['job_id'],flush=True)
    print('V0722_CUT_CANDIDATES',[x['frame_estimate'] for x in ctx['cut_candidates']],flush=True)
    print('V0722_SAMPLE_PLAN',ctx['samples'],flush=True)
    print('V0722_QWEN_LOAD_COUNT_PLANNED',1,flush=True)
    if not run_gpu:
        print('V0722_DRY_RUN only; nothing written and GPU untouched',flush=True)
        return None
    if dest.exists() or dest.is_symlink():raise FileExistsError('Review exists; no automatic overwrite')
    require_idle()
    drain=import_py(DRAIN,'v0722_resource_handoff')
    drain.ensure_qwen_resources()
    require_idle()
    stage=dest.parent/'scene_review_v0722_stage';archive_stage(stage)
    if not Path(qwen_python).is_file():raise FileNotFoundError('Qwen environment unavailable')
    stage.mkdir(parents=True,exist_ok=False)
    request={'version':VERSION,'source':ctx['source'],'source_sha256':ctx['record']['source_sha256'],
             'meta':ctx['record']['video_meta'],'samples':ctx['samples'],'profile':ctx['profile'],
             'output_tmp':str(stage/'observations.json')}
    (stage/'request.json').write_text(canonical(request))
    with (stage/'worker.log').open('w') as log:
        proc=subprocess.Popen([str(qwen_python),'-u',str(Path(__file__).resolve()),'--worker-request',str(stage/'request.json')],stdout=log,stderr=subprocess.STDOUT)
        t=time.monotonic()
        while proc.poll() is None:
            if time.monotonic()-t>timeout:
                proc.kill();proc.wait();raise TimeoutError('Qwen timed out; inspect '+str(stage/'worker.log'))
            time.sleep(5);print('V0722_QWEN_RUNNING',int(time.monotonic()-t),'s',flush=True)
        if proc.returncode:raise RuntimeError('Qwen process failed; inspect '+str(stage/'worker.log'))
    obs=json.loads((stage/'observations.json').read_text())
    ids=[x['id'] for x in ctx['profile']['cast']]
    if set(obs)!={str(x) for x in ctx['samples']}:raise ValueError('Worker observations incomplete')
    for v in obs.values():
        if v['parsed']!=parse_reply(v['raw_reply'],ids):raise ValueError('Worker parser mismatch')
    dest,bundle=publish(ctx,obs,dest)
    print('V0722_SHADOW_REVIEW',dest,'VALID',sum(x['parsed']['valid'] for x in obs.values()),'/',len(obs),flush=True)
    print('V0722_REVIEW_BUNDLE',bundle,flush=True)
    print('V0722_NO_EDIT_NO_HANDOFF_NO_H3_NO_MEMORY_CHANGE',flush=True)
    return dest


def cli():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-id',required=False)
    p.add_argument('--profile',type=Path)
    p.add_argument('--run',action='store_true')
    p.add_argument('--worker-request',type=Path)
    a=p.parse_args()
    if a.worker_request:worker(a.worker_request);return
    if not a.job_id or not re.fullmatch('[0-9a-f]{24}',a.job_id) or not a.profile:
        p.error('Expected --job-id <24 lowercase hex> and --profile <file>')
    run(QUEUE_ROOT/'jobs'/(a.job_id+'.json'),a.profile,run_gpu=a.run)

if __name__=='__main__':cli()
