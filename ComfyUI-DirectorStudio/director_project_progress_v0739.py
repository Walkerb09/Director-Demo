#!/usr/bin/env python3
"""V0.7.39 lifecycle = V0.7.36 + take rejection / retake (CAPTURE -> PLAN, same shot).

V0.7.36 had no way back from CAPTURE: a bad take blocked the project forever, and a
retake with the same prefix (VHS _00002) could never be ingested. V0.7.39 adds the
append-only CAPTURE_REJECTED event; rejected jobs are excluded from future intake but
stay in the ledger as evidence. A ledger containing CAPTURE_REJECTED is refused by the
old V0.7.36 module (fail-closed), never misread.

Original V0.7.36 notes:
V0.7.36: append-only shot lifecycle INSIDE the V0.7.35 SQLite project.

Real capture discovery -> explicit human working-performance selection -> next
creative plan. Source bytes and job hashes are checked; approvals are local human
attestations, NOT authenticated identities or final film approvals. No Qwen/H3.
The original V0.7.35 F36 working film is unchanged. New shots are not silently
edited into that film; an accepted performance isn't an approved timeline cut.
"""
from __future__ import annotations
import argparse, json, re, sqlite3, subprocess, hashlib, os, tempfile, zipfile
from pathlib import Path
from contextlib import closing
import director_project_v0735 as base
import director_unified_edit_v0734 as capture
import director_idle_dispatcher_v075 as dispatcher

VERSION='director-lifecycle-progress-v0739'
ZERO='0'*64
HEX24=re.compile(r'[0-9a-f]{24}\Z')

def db_path(root):
    root=Path(root)
    base.status(root)  # validates the original project ledger first
    db=root/base.PROJECT_DB
    base.ensure_plain_file(db)
    return db

def _history(c,base_hash):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='progress_events'").fetchone():
        return []
    entries=c.execute('SELECT seq,kind,payload,prev_hash,event_hash,base_hash FROM progress_events ORDER BY seq').fetchall()
    prev=ZERO;out=[]
    for i,e in enumerate(entries,1):
        try: data=json.loads(e['payload'])
        except ValueError as ex:raise base.MemoryErrorSafety('PROGRESS_JSON_INVALID') from ex
        expected=base.event_hash(i,e['kind'],data,prev)
        if (e['seq']!=i or e['payload']!=base.canonical(data) or e['prev_hash']!=prev
            or e['event_hash']!=expected or e['base_hash']!=base_hash):
            raise base.MemoryErrorSafety('PROGRESS_LEDGER_INTEGRITY_FAILED')
        out.append({'seq':i,'kind':e['kind'],'payload':data,'hash':expected})
        prev=expected
    _check_order(out)
    return out

def _check_order(events):
    expected_shot=3;phase='PLAN'   # Shot 3 is already planned in V0.7.35
    for e in events:
        payload=e['payload'];n=payload.get('shot_number')
        if type(n) is not int or n!=expected_shot:
            raise base.MemoryErrorSafety('PROGRESS_SHOT_ORDER_BROKEN')
        if phase=='PLAN' and e['kind']=='CAPTURE_INGESTED':phase='CAPTURE'
        elif phase=='CAPTURE' and e['kind']=='WORKING_PERFORMANCE_REVIEWED':phase='ACCEPTED'
        elif phase=='CAPTURE' and e['kind']=='CAPTURE_REJECTED':phase='PLAN'
        elif phase=='ACCEPTED' and e['kind']=='FOLLOWUP_PLAN_PROPOSED':
            if payload.get('following_shot')!=n+1:raise base.MemoryErrorSafety('INVALID_FOLLOWUP_SHOT')
            expected_shot+=1;phase='PLAN'
        else:raise base.MemoryErrorSafety('PROGRESS_TRANSITION_INVALID')
    return expected_shot,phase

def snapshot(root):
    p=base.status(root)
    with closing(base.connect(db_path(root))) as c: events=_history(c,p['latest_event_hash'])
    n,phase=_check_order(events)
    capture_event=(next((e['payload'] for e in reversed(events) if e['kind']=='CAPTURE_INGESTED' and e['payload']['shot_number']==n),None)
                   if phase=='CAPTURE' else None)
    rejected=[e['payload'] for e in events if e['kind']=='CAPTURE_REJECTED']
    last_review=next((e['payload'] for e in reversed(events) if e['kind']=='WORKING_PERFORMANCE_REVIEWED'),None)
    return {'schema':VERSION,'project_id':p['project_id'],'base_event_hash':p['latest_event_hash'],
      'progress_event_count':len(events),'progress_tail_hash':events[-1]['hash'] if events else ZERO,
      'next_shot':n,'phase':phase,'active_working_cut_f36_unchanged':p['working_edit']['active_entry_frame']==36,
      'working_film_frames_unchanged':p['working_edit']['timeline_frames'],
      'last_human_review':last_review,'pending_capture':capture_event,'rejected_takes':rejected,
      'generation_authorized':False,'automatic_edit_approval_authorized':False,
      'generated_footage_visual_conditioning_authorized':False,'automatic_story_truth_from_qwen':False}

def _append(root,kind,payload,phase):
    root=Path(root);db=db_path(root)
    with closing(base.connect(db)) as c:
        c.execute('BEGIN IMMEDIATE')
        events=_history(c,base.status(root)['latest_event_hash'])
        n,now=_check_order(events)
        if now!=phase or payload['shot_number']!=n:raise base.MemoryErrorSafety('STALE_PROGRESS_STATE')
        c.execute('CREATE TABLE IF NOT EXISTS progress_events(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL,base_hash TEXT NOT NULL)')
        seq=len(events)+1;prev=events[-1]['hash'] if events else ZERO
        chain=base.status(root)['latest_event_hash']
        c.execute('INSERT INTO progress_events VALUES (?,?,?,?,?,?)',
           (seq,kind,base.canonical(payload),prev,base.event_hash(seq,kind,payload,prev),chain))
        c.execute('COMMIT')
    return snapshot(root)

def _ffprobe(path):
    r=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,nb_frames,r_frame_rate,width,height',
       '-of','json',str(path)],capture_output=True,text=True,timeout=20,check=True)
    s=json.loads(r.stdout)['streams'];video=[a for a in s if a['codec_type']=='video'];audio=[a for a in s if a['codec_type']=='audio']
    if len(video)!=1 or len(audio)!=1 or video[0].get('r_frame_rate')!='24/1' or video[0].get('width')!=768 or video[0].get('height')!=1344:
        raise base.MemoryErrorSafety('NEW_CAPTURE_MEDIA_FORMAT_UNSUPPORTED')
    frames=int(video[0].get('nb_frames') or 0)
    if frames<=0:raise base.MemoryErrorSafety('NEW_CAPTURE_FRAME_COUNT_UNKNOWN')
    return frames

def _planned(root,n):
    if n==3:
        p=base.status(root)['last_proposal']
        if not p or p.get('shot_number')!=3:raise base.MemoryErrorSafety('SHOT3_NOT_PLANNED')
        return p
    p=root/'plans'/f'plan_shot{n:02d}_v0736.json'
    return base.jread(p)

def ingest(root,saved_root,source_root, *,synthetic=False):
    """Discover EXACTLY ONE source-bound captured video for the planned prefix.
    Inspection alone never creates a working edit/approval.
    """
    root=Path(root);s=snapshot(root)
    if s['phase']!='PLAN':raise base.MemoryErrorSafety('SHOT_ALREADY_INGESTED_OR_AWAITING_REVIEW')
    n=s['next_shot'];plan=_planned(root,n)
    prefix=plan['savedshot_output_prefix']
    saved=Path(saved_root);jobs=saved/'jobs'
    if saved.is_symlink() or jobs.is_symlink() or not jobs.is_dir():raise base.MemoryErrorSafety('INVALID_SAVEDSHOT_JOBS_ROOT')
    matches=[];fail=[]
    rejected_jobs={e['job_id'] for e in s['rejected_takes']}
    for file in sorted(jobs.glob('*.json')):
        if file.stem in rejected_jobs:continue
        if file.is_symlink():raise base.MemoryErrorSafety('UNSAFE_JOB_ENTRY')
        try: j=capture.read_json(file)
        except Exception:
            # A malformed unrelated job must not prevent this source-specific intake;
            # require operator review for any file with the shot prefix in its name.
            if prefix in file.name:raise
            continue
        v=j.get('source_video_path')
        if not isinstance(v,str) or not Path(v).name.startswith(prefix):continue
        try:
            if not HEX24.fullmatch(file.stem) or j.get('job_id')!=file.stem:raise base.MemoryErrorSafety('JOB_ID_INVALID')
            if j.get('version')!='director-saved-shot-capture-0.6.6' or j.get('phase')!='PENDING_SHADOW_OBSERVATION':raise base.MemoryErrorSafety('JOB_SCHEMA_INVALID')
            if j.get('source_class')!='H3_GENERATED_OBSERVATION_ONLY' or j.get('observation_authority')!='NONE':raise base.MemoryErrorSafety('JOB_AUTHORITY_INVALID')
            if any(j.get(k) is not v for k,v in dispatcher.SAFETY.items()):raise base.MemoryErrorSafety('JOB_SAFETY_FLAG_INVALID')
            if j.get('synthetic_demo_fixture_only') is True and not synthetic:raise base.MemoryErrorSafety('SYNTHETIC_JOB_REFUSED')
            video=capture.restricted_source(v,Path(source_root))
            if not re.fullmatch(re.escape(prefix)+r'_\d+-audio\.mp4',video.name):raise base.MemoryErrorSafety('UNEXPECTED_VIDEO_FILENAME')
            if base.digest(video)!=j.get('source_sha256'):raise base.MemoryErrorSafety('CAPTURE_HASH_MISMATCH')
            if type(j.get('video_size_bytes')) is not int or video.stat().st_size!=j['video_size_bytes']:raise base.MemoryErrorSafety('CAPTURE_SIZE_MISMATCH')
            frames=_ffprobe(video)
            meta=j.get('video_meta')
            if type(meta) is not dict or type(meta.get('frames')) is not int or meta['frames']!=frames or float(meta.get('fps') or 0)!=24 or meta.get('width')!=768 or meta.get('height')!=1344:
                raise base.MemoryErrorSafety('CAPTURE_METADATA_MISMATCH')
            from director_readiness_guard_v0732 import safe_marker
            if safe_marker(saved,file.stem)['status']!='NONE':raise base.MemoryErrorSafety('CAPTURE_HAS_UNRESOLVED_FAILURE_MARKER')
            matches.append({'shot_number':n,'job_id':file.stem,'job_sha256':base.digest(file),
              'video_sha256':base.digest(video),'video_path':str(video),'job_path':str(file),'video_frames':frames,
              'video_name':video.name,'provenance':'SYNTHETIC_OFFLINE_TEST' if synthetic else 'REAL_CAPTURE_JOB_HASH_BOUND',
              'generation_authorized':False,'working_edit_approved':False})
        except Exception as e:fail.append(f'{file.name}: {type(e).__name__}: {str(e)[:100]}')
    if fail:raise base.MemoryErrorSafety('MATCHED_CAPTURE_INVALID: '+'; '.join(fail))
    if len(matches)!=1:raise base.MemoryErrorSafety('EXPECTED_ONE_MATCHED_NEW_CAPTURE_FOUND_'+str(len(matches)))
    old=base.status(root)['capture_binding']
    if matches[0]['video_sha256'] in {old[k]['source_sha256'] for k in old}:
        raise base.MemoryErrorSafety('NEW_SHOT_DUPLICATES_PREVIOUS_SOURCE')
    if any(e.get('video_sha256')==matches[0]['video_sha256'] for e in _get_events(root)):
        raise base.MemoryErrorSafety('NEW_SHOT_DUPLICATED_PRIOR_CAPTURE')
    return _append(root,'CAPTURE_INGESTED',matches[0],'PLAN')

def _get_events(root):
    p=base.status(root)
    with closing(base.connect(db_path(root))) as c:return [e['payload'] for e in _history(c,p['latest_event_hash'])]

def review(root,review_json):
    """Human-reviewed working PERFORMANCE only, NOT an editorial cut or final film."""
    root=Path(root);s=snapshot(root)
    if s['phase']!='CAPTURE':raise base.MemoryErrorSafety('NO_CAPTURE_AWAITING_REVIEW')
    review_path=Path(review_json);review=base.jread(review_path)
    if review.get('schema')!='director-human-review-v0736' or review.get('decision')!='ACCEPT_WORKING_PERFORMANCE_NOT_FINAL':
        raise base.MemoryErrorSafety('EXPLICIT_WORKING_REVIEW_REQUIRED')
    cap=s['pending_capture'];n=s['next_shot']
    if (type(review.get('shot_number')) is not int or review['shot_number']!=n or
        review.get('source_sha256')!=cap['video_sha256'] or review.get('job_sha256')!=cap['job_sha256'] or
        review.get('acknowledge_human_review_only') is not True or
        review.get('no_automatic_edit_approval') is not True or
        review.get('no_generated_visual_conditioning') is not True):
        raise base.MemoryErrorSafety('REVIEW_NOT_BOUND_TO_CAPTURE_OR_PERMISSIONS')
    story=review.get('story_observation','UNKNOWN')
    if not isinstance(story,str) or len(story)>300 or any(ord(x)<32 for x in story):
        raise base.MemoryErrorSafety('INVALID_REVIEW_STORY_NOTE')
    if base.digest(cap['video_path'])!=cap['video_sha256']:
        raise base.MemoryErrorSafety('REVIEW_SOURCE_CHANGED')
    if base.digest(cap['job_path'])!=cap['job_sha256']:
        raise base.MemoryErrorSafety('REVIEW_CAPTURE_JOB_CHANGED')
    payload={'shot_number':n,'source_sha256':cap['video_sha256'],'job_sha256':cap['job_sha256'],
       'human_review_sha256':base.digest(review_path),'story_observation_human_attested':story,
       'working_performance_human_attested':True,'timeline_edit_selected':False,
       'scene_dna_verified':False,'h3_visual_conditioning_from_generated_footage':False,
       'final_film_approval':False,'human_identity_authentication':'NOT_CRYPTOGRAPHICALLY_VERIFIED'}
    return _append(root,'WORKING_PERFORMANCE_REVIEWED',payload,'CAPTURE')

def reject_capture(root,reason='HUMAN_REJECTED_TAKE',confirmed=False):
    """Human rejects the pending take of the current shot. Shot number does not advance;
    the same plan (or a new Director variant) can be re-generated as a retake."""
    if confirmed is not True:raise base.MemoryErrorSafety('EXPLICIT_HUMAN_REJECTION_REQUIRED')
    if not isinstance(reason,str) or len(reason)>300 or any(ord(x)<32 for x in reason):
        raise base.MemoryErrorSafety('INVALID_REJECTION_REASON')
    root=Path(root);s=snapshot(root)
    if s['phase']!='CAPTURE':raise base.MemoryErrorSafety('NO_CAPTURE_TO_REJECT')
    c=s['pending_capture']
    payload={'shot_number':s['next_shot'],'job_id':c['job_id'],'job_sha256':c['job_sha256'],
      'video_sha256':c['video_sha256'],'reason_human_attested':reason,
      'evidence_retained':True,'timeline_changed':False}
    return _append(root,'CAPTURE_REJECTED',payload,'CAPTURE')

def approve_explicit(root,confirmed=False):
    """One explicit operator action. No additional scene brief needed.

    This is a human attestation, not proof of having watched the clip and not
    automatic editorial/final-film approval. Does not change the F36 timeline.
    """
    if confirmed is not True:
        raise base.MemoryErrorSafety('EXPLICIT_HUMAN_WORKING_REVIEW_APPROVAL_REQUIRED')
    root=Path(root);s=snapshot(root)
    if s['phase']!='CAPTURE':raise base.MemoryErrorSafety('CAPTURE_REQUIRED_BEFORE_REVIEW')
    c=s['pending_capture']
    data={'schema':'director-human-review-v0736','decision':'ACCEPT_WORKING_PERFORMANCE_NOT_FINAL',
      'shot_number':s['next_shot'],'source_sha256':c['video_sha256'],'job_sha256':c['job_sha256'],
      'acknowledge_human_review_only':True,'no_automatic_edit_approval':True,
      'no_generated_visual_conditioning':True,'story_observation':'UNKNOWN'}
    reviews=root/'reviews'
    if reviews.is_symlink():raise base.MemoryErrorSafety('LINKED_REVIEW_DIRECTORY')
    reviews.mkdir(exist_ok=True)
    path=reviews/f"shot{s['next_shot']:02d}_human_working_attestation_v0736.json"
    if path.is_symlink() or (path.exists() and base.jread(path)!=data):
        raise base.MemoryErrorSafety('HUMAN_REVIEW_ATTESTATION_CONFLICT')
    if not path.exists():
        with path.open('x') as f:
            f.write(json.dumps(data,indent=2,sort_keys=True)+'\n')
            f.flush();os.fsync(f.fileno())
    state=review(root,path)
    return {'human_working_performance_attested':True,'scene_dialogue_truth':'UNKNOWN',
      'F36_working_edit_unchanged':True,'next_action':'CONTINUE_TO_PROPOSE_NEXT_BEAT',
      'status':state}

def _make_next(root,originals,template,*,restore_plan=False):
    root=Path(root);s=snapshot(root)
    if restore_plan:
        if s['phase']!='PLAN' or s['next_shot']<4:
            raise base.MemoryErrorSafety('NO_FOLLOWUP_PLAN_TO_RESTORE')
        n=s['next_shot']-1
    else:
        if s['phase']!='ACCEPTED':raise base.MemoryErrorSafety('SHOT_REVIEW_REQUIRED_BEFORE_NEXT_BEAT')
        n=s['next_shot']
    next_n=n+1
    state=base.status(root)
    graph,_,_=base.build_workflow(root,state['scene_brief'],originals,template)
    # This is a bounded creative continuation; it does not pretend the exact
    # dialogue, ending pose or prop owner were verified by the vision model.
    narrative=('Continue the same hallway interview after the previously HUMAN-REVIEWED performance. '
      'Allow the presenter and Grandpa to respond to each other naturally, with a genuine pause and a small reaction. '
      'Do not restart the introduction or repeat scripted dialogue. Keep the two-person shared composition, stable lighting, '
      'original identities and microphone interaction understated. End in a clear shared position. '
      'Use only the six pristine original stills; never feed earlier H3-generated media as visual conditioning.')
    prompt=base.generate_prompt(state)
    before='Continue the interview naturally: Grandpa is finishing his answer; the presenter listens attentively, '
    if before not in prompt:raise base.MemoryErrorSafety('UNKNOWN_PROMPT_TEMPLATE')
    # The existing prompt already carries the pristine-source and composition safeguards.
    prompt=prompt.replace(before,
      'Continue the ongoing interview without assuming unverified dialogue. Grandpa and presenter listen and respond naturally; ',1)
    prompt+=' Human-reviewed earlier performance precedes this new beat; its exact dialogue and final pose remain unspecified.'
    nodes={v['id']:v for v in graph['nodes']}
    nodes[136]['widgets_values'][0]=prompt
    prefix=f'Director_H3_Hallway_Shot{next_n:02d}_Continuation_Shadow_UNAPPROVED'
    nodes[221]['widgets_values']['filename_prefix']=prefix
    seed=int(hashlib.sha256(base.canonical({'project':state['project_id'],'shot':next_n,'review':s['last_human_review']['source_sha256']}).encode()).hexdigest()[:12],16)
    nodes[129]['widgets_values'][0]=seed
    graph['links']=base.build_workflow(root,state['scene_brief'],originals,template)[0]['links']
    base.validate_seed(state['scene_brief'],originals)
    assert {v['widgets_values'][0] for v in graph['nodes'] if v['type']=='LoadImage'}==set(a['name'] for a in state['scene_brief']['original_assets'])
    for name in ('ref_videos.','ref_video_audios.','ref_audios.'):
        if any(x['name'].startswith(name) and x['link'] is not None for x in nodes[136]['inputs']):
            raise base.MemoryErrorSafety('UNSAFE_H3_VISUAL_INPUT_LINK')
    plan={'schema':'director-next-beat-proposal-v0736','project_id':state['project_id'],
      'shot_number':next_n,'prior_working_film_cut':{'active_entry_frame':36,'timeline_frames':212,'unmodified':True},
      'prior_new_shot':n,'human_review_sha256':s['last_human_review']['human_review_sha256'],
      'dialogue_transcript':'UNKNOWN','verified_last_motion':'UNKNOWN','final_prop_owner':'UNKNOWN',
      'next_narrative_objective':narrative,'generation_prompt':prompt,'original_visual_reference_sha256':
      {a['name']:a['sha256'] for a in state['scene_brief']['original_assets']},
      'savedshot_output_prefix':prefix,'workflow_output_name':f'Director_H3_Hallway_Shot{next_n:02d}_Continuation_CANDIDATE.json',
      'rng_seed':seed,'automatic_generation_authorized':False,'automatic_edit_approval_authorized':False,
      'generated_footage_as_h3_conditioning':False,'status':'PLANNED_NOT_QUEUED'}
    out=root/'plans';out.mkdir(exist_ok=True)
    if out.is_symlink():raise base.MemoryErrorSafety('UNSAFE_PLAN_DIR')
    data={f'plan_shot{next_n:02d}_v0736.json':base.canonical(plan)+'\n',
        plan['workflow_output_name']:json.dumps(graph,indent=2,ensure_ascii=False)+'\n'}
    for name,content in data.items():
        path=out/name
        if path.is_symlink() or (path.exists() and path.read_text()!=content):raise base.MemoryErrorSafety('PLAN_ALREADY_CHANGED')
    record={'shot_number':n,'following_shot':next_n,'review_hash':s['last_human_review']['human_review_sha256'],
      'plan_sha256':hashlib.sha256(base.canonical(plan).encode()).hexdigest(),
      'workflow_sha256':hashlib.sha256(data[plan['workflow_output_name']].encode()).hexdigest(),
      'generated_h3':False,'previous_working_f36_untouched':True}

    if restore_plan:
        state_events=_get_events(root)
        if not state_events or state_events[-1]!=record:
            raise base.MemoryErrorSafety('FOLLOWUP_PLAN_RECOMPUTE_MISMATCH')
        after=s
    else:
        after=_append(root,'FOLLOWUP_PLAN_PROPOSED',record,'ACCEPTED')
    for name,content in data.items():
        path=out/name
        if not path.exists():
            with path.open('x') as f:f.write(content)
    return after,plan

def continue_project(root,originals,template,instruction):
    phrase=' '.join(instruction.strip().lower().split()).strip(' .!?')
    if phrase not in base.ALLOWED_REQUESTS:raise base.MemoryErrorSafety('UNSUPPORTED_INSTRUCTION')
    state=snapshot(root)
    if state['phase']=='PLAN':
        n=state['next_shot']
        if n==3:
            result=base.continue_interview(root,instruction,originals,template)
            return {'action':'REUSE_EXISTING_SHOT3_PLAN','shot':3,'plan_path':result['plan_path'],'no_generation':True}
        file=Path(root)/'plans'/f'plan_shot{n:02d}_v0736.json'
        if not file.exists():_make_next(root,originals,template,restore_plan=True)
        p=base.jread(file)
        return {'action':'REUSE_EXISTING_FOLLOWUP_PLAN','shot':n,'plan_path':str(file),'no_generation':True,
          'workflow_path':str(Path(root)/'plans'/p['workflow_output_name'])}
    if state['phase']=='CAPTURE':return {'action':'HUMAN_REVIEW_EXISTING_CAPTURE','shot':state['next_shot'],'no_generation':True,'pending':state['pending_capture']}
    state,plan=_make_next(root,originals,template)
    return {'action':'NEW_FOLLOWUP_PLAN_FROM_REVIEWED_MEMORY','shot':plan['shot_number'],'no_generation':True,
     'plan_path':str(Path(root)/'plans'/f"plan_shot{plan['shot_number']:02d}_v0736.json"),'state':state}

def stage_current_plan(root,originals,comfy_root,queue_url='http://127.0.0.1:18188'):
    """Copy current verified workflow to ComfyUI sidebar; never queue H3."""
    import shutil
    from urllib.request import urlopen
    root=Path(root);s=snapshot(root)
    if s['phase']!='PLAN':raise base.MemoryErrorSafety('REVIEW_BEFORE_STAGING_NEXT_SHOT')
    if s['next_shot']==3:
        return base.stage_workflow(root,originals,Path(__file__).parent/'workflow_template_verified.json',comfy_root,queue_url)
    plan=_planned(root,s['next_shot'])
    ref=plan['original_visual_reference_sha256']
    saved=base.status(root)
    base.validate_seed(saved['scene_brief'],originals)
    if ref!={a['name']:a['sha256'] for a in saved['scene_brief']['original_assets']}:
        raise base.MemoryErrorSafety('FOLLOWUP_PRISTINE_REFERENCES_CHANGED')
    source=root/'plans'/plan['workflow_output_name']
    base.ensure_plain_file(source)
    graph=base.jread(source)
    if not isinstance(graph.get('nodes'),list) or not isinstance(graph.get('links'),list):
        raise base.MemoryErrorSafety('INVALID_FOLLOWUP_GRAPH')
    evidence=_get_events(root)[-1]
    if (evidence.get('workflow_sha256')!=base.digest(source) or
        evidence.get('plan_sha256')!=hashlib.sha256(base.canonical(plan).encode()).hexdigest()):
        raise base.MemoryErrorSafety('FOLLOWUP_GRAPH_LEDGER_MISMATCH')
    c=Path(comfy_root)
    if c.is_symlink() or not (c/'main.py').is_file():raise base.MemoryErrorSafety('COMFYUI_ROOT_INVALID')
    if Path(originals).resolve()!= (c/'input').resolve():raise base.MemoryErrorSafety('ORIGINALS_ROOT_NOT_COMFY_INPUT')
    nodes={v['id']:v for v in graph['nodes']}
    if not {v['widgets_values'][0] for v in graph['nodes'] if v.get('type')=='LoadImage'}==set(ref):
        raise base.MemoryErrorSafety('GRAPH_ORIGINAL_SET_NOT_MATCHED')
    if (nodes[136]['widgets_values'][0]!=plan['generation_prompt'] or
        nodes[221]['widgets_values']['filename_prefix']!=plan['savedshot_output_prefix'] or
        nodes[129]['widgets_values'][0]!=plan['rng_seed']):
        raise base.MemoryErrorSafety('FOLLOWUP_GRAPH_NOT_PLAN_BOUND')
    for prefix in ('ref_videos.','ref_video_audios.','ref_audios.'):
        if any(x['name'].startswith(prefix) and x['link'] is not None for x in nodes[136]['inputs']):
            raise base.MemoryErrorSafety('H3_VIDEO_REFERENCE_FORBIDDEN')
    with urlopen(queue_url.rstrip('/')+'/object_info',timeout=8) as resp: info=json.load(resp)
    with urlopen(queue_url.rstrip('/')+'/queue',timeout=8) as resp: queue=json.load(resp)
    if not {'MiniMaxH3ReferenceToVideo','VHS_VideoCombine','DirectorV066SavedShotShadowQueue'}<=set(info):
        raise base.MemoryErrorSafety('COMFYUI_NODES_NOT_LOADED')
    if type(queue.get('queue_running')) is not list or type(queue.get('queue_pending')) is not list or queue['queue_running'] or queue['queue_pending']:
        raise base.MemoryErrorSafety('COMFYUI_QUEUE_NOT_IDLE')
    target_dir=c/'user'/'default'/'workflows'
    if target_dir.is_symlink():raise base.MemoryErrorSafety('LINKED_WORKFLOW_DIR')
    target_dir.mkdir(parents=True,exist_ok=True)
    target=target_dir/plan['workflow_output_name']
    if target.is_symlink() or (target.exists() and base.digest(target)!=base.digest(source)):
        raise base.MemoryErrorSafety('STAGED_GRAPH_CONFLICT')
    if not target.exists():
        shutil.copyfile(source,target)
        if base.digest(target)!=base.digest(source):raise base.MemoryErrorSafety('STAGED_COPY_FAILED')
    return {'workflow_path':str(target),'staged_for_review':True,'h3_generation_started':False}

def export_memory(root,destination):
    """Checkpoint BOTH event chains atomically in one SQLite backup."""
    root=Path(root);dest=Path(destination)
    if dest.exists() or dest.is_symlink():raise base.MemoryErrorSafety('BACKUP_DESTINATION_EXISTS')
    before=snapshot(root)
    if dest.resolve().is_relative_to(root.resolve()):raise base.MemoryErrorSafety('BACKUP_INSIDE_PROJECT')
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/base.PROJECT_DB
        source=base.connect(db_path(root));target=base.connect(path)
        try:source.backup(target)
        finally:source.close();target.close()
        # Verify backup state using the same original-ledger and new progress-ledger validators.
        with closing(base.connect(path)) as c:
            snapshot_base=base.current(base.read_events(c))
            progress=_history(c,snapshot_base['latest_event_hash'])
        if (snapshot_base['latest_event_hash']!=before['base_event_hash'] or
            len(progress)!=before['progress_event_count'] or
            (progress[-1]['hash'] if progress else ZERO)!=before['progress_tail_hash']):
            raise base.MemoryErrorSafety('STATE_CHANGED_DURING_BACKUP')
        manifest={'schema':'director-memory-backup-v0736','project_id':before['project_id'],
             'base_event_hash':before['base_event_hash'],
             'progress_tail_hash':before['progress_tail_hash'],
             'progress_event_count':before['progress_event_count'],
             'database_sha256':base.digest(path),
             'media_and_pristine_references_included':False,'automatic_generation_authorized':False}
        with zipfile.ZipFile(dest,'x',zipfile.ZIP_DEFLATED) as z:
            z.write(path,base.PROJECT_DB)
            z.writestr('manifest_v0736.json',base.canonical(manifest)+'\n')
    return manifest

def import_memory(archive,project):
    """Restore both ledgers without substituting for external media or originals."""
    dst=Path(project);p=Path(archive)
    if dst.exists() or dst.is_symlink():raise base.MemoryErrorSafety('RESTORE_PROJECT_ALREADY_EXISTS')
    base.ensure_plain_file(p)
    with zipfile.ZipFile(p) as z:
        if set(z.namelist())!={base.PROJECT_DB,'manifest_v0736.json'} or len(z.namelist())!=2:
            raise base.MemoryErrorSafety('INVALID_BACKUP_CONTENTS')
        if any(x.file_size>15_000_000 for x in z.infolist()):raise base.MemoryErrorSafety('BACKUP_TOO_LARGE')
        raw=z.read(base.PROJECT_DB);manifest=json.loads(z.read('manifest_v0736.json'))
        if manifest.get('schema')!='director-memory-backup-v0736' or hashlib.sha256(raw).hexdigest()!=manifest.get('database_sha256'):
            raise base.MemoryErrorSafety('BACKUP_HASH_OR_SCHEMA_WRONG')
    dst.parent.mkdir(parents=True,exist_ok=True)
    stage=dst.with_name(dst.name+'.restore.'+str(os.getpid()))
    if stage.exists() or stage.is_symlink():raise base.MemoryErrorSafety('RESTORE_STAGE_EXISTS')
    stage.mkdir(mode=0o700)
    try:
        (stage/base.PROJECT_DB).write_bytes(raw)
        state=snapshot(stage)
        for k,key in (('project_id','project_id'),('base_event_hash','base_event_hash'),
             ('progress_tail_hash','progress_tail_hash'),('progress_event_count','progress_event_count')):
            if state[k]!=manifest[key]:raise base.MemoryErrorSafety('RESTORED_LEDGER_DIVERGED:'+k)
        os.replace(stage,dst)
    finally:
        if stage.exists():
            import shutil
            shutil.rmtree(stage)
    return snapshot(dst)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['status','continue','ingest','review','approve','export','import','stage'])
    p.add_argument('--project',default='/workspace/DirectorProjects/hallway_interview_v1')
    p.add_argument('--saved-root',default='/workspace/DirectorSavedShotQueueV066')
    p.add_argument('--source-root',default='/workspace/ComfyUI/output')
    p.add_argument('--originals-dir',default='/workspace/ComfyUI/input')
    p.add_argument('--template',default=str(Path(__file__).parent/'workflow_template_verified.json'))
    p.add_argument('--instruction',default='Continue the interview')
    p.add_argument('--review-json')
    p.add_argument('--i-reviewed-and-approve-working',action='store_true')
    p.add_argument('--backup')
    p.add_argument('--comfy-root',default='/workspace/ComfyUI')
    p.add_argument('--queue-url',default='http://127.0.0.1:18188')
    p.add_argument('--synthetic-offline-fixture',action='store_true')
    a=p.parse_args()
    if a.command=='status':result=snapshot(a.project)
    elif a.command=='stage':result=stage_current_plan(a.project,a.originals_dir,a.comfy_root,a.queue_url)
    elif a.command=='ingest':result=ingest(a.project,a.saved_root,a.source_root,synthetic=a.synthetic_offline_fixture)
    elif a.command=='export':
        if not a.backup:p.error('--backup required')
        result=export_memory(a.project,a.backup)
    elif a.command=='import':
        if not a.backup:p.error('--backup required')
        result=import_memory(a.backup,a.project)
    elif a.command=='approve':
        result=approve_explicit(a.project,a.i_reviewed_and_approve_working)
    elif a.command=='review':
        if not a.review_json:p.error('--review-json required')
        result=review(a.project,a.review_json)
    else:result=continue_project(a.project,a.originals_dir,a.template,a.instruction)
    print(json.dumps(result,indent=2,sort_keys=True))
    print('V0736_PROJECT_PROGRESS_PASS_NO_H3_NO_QWEN_NO_AUTOMATIC_APPROVAL')

if __name__=='__main__':main()
