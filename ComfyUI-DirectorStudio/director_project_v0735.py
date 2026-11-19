#!/usr/bin/env python3
"""V0.7.35: source-bound, restart-safe DIRECTOR project memory and next-beat plans.

Existing capture intake from V0.7.34.1 is reused; the F36 human-working
selection is a bounded human attestation, NOT verified acting/Scene DNA, a
production edit, or generation authorization. No GPU/inference calls.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

SCHEMA = 'director-project-memory-v0735'
GENESIS = '0'*64
SELECTION_FRAME = 36
ORIGINAL_KIND = 'AUTHENTIC_ORIGINAL_DECLARED_HASH_VERIFIED'
ALLOWED_REQUESTS = {'continue the interview','continue interview','keep the interview going'}
PROJECT_DB = 'director_project.sqlite3'
DEMO_MARKER = 'SYNTHETIC_OFFLINE_FIXTURE_NOT_A_REAL_CAPTURE'

class MemoryErrorSafety(ValueError): pass

def canonical(v):
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()

def jread(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file():raise MemoryErrorSafety('MISSING_OR_LINKED_INPUT:'+str(p))
    try: obj=json.loads(p.read_text(encoding='utf-8'))
    except (OSError,ValueError,UnicodeError) as e: raise MemoryErrorSafety('INVALID_JSON:'+str(p)) from e
    if not isinstance(obj,dict):raise MemoryErrorSafety('EXPECTED_JSON_OBJECT')
    return obj

def ensure_plain_file(p):
    if Path(p).is_symlink() or not Path(p).is_file():raise MemoryErrorSafety('UNSAFE_OR_MISSING_FILE:'+str(p))

def validate_seed(seed,originals):
    if seed.get('schema')!='director-scene-seed-v0735' or seed.get('project_id')!='hallway-interview-v1':
        raise MemoryErrorSafety('UNTRUSTED_SCENE_SEED')
    if seed.get('fps') !=24 or seed.get('aspect_ratio')!='9:16':raise MemoryErrorSafety('UNSUPPORTED_SCENE_FORMAT')
    if [x.get('actor_id') for x in seed.get('characters',[])]!=['presenter','grandpa']:
        raise MemoryErrorSafety('UNSUPPORTED_CAST_SPEC')
    assets=seed.get('original_assets')
    if not isinstance(assets,list) or len(assets)!=6 or len({a.get('name') for a in assets})!=6:
        raise MemoryErrorSafety('INVALID_ORIGINAL_ASSET_MANIFEST')
    root=Path(originals)
    if root.is_symlink() or not root.is_dir():raise MemoryErrorSafety('MISSING_OR_LINKED_ORIGINAL_DIRECTORY')
    original_dir=root.resolve(strict=True)
    names=set()
    for a in assets:
        name=a.get('name');expected=a.get('sha256')
        if not isinstance(name,str) or name !=Path(name).name or not name.endswith('.png') or '\\' in name:
            raise MemoryErrorSafety('UNSAFE_ORIGINAL_NAME')
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise MemoryErrorSafety('INVALID_ORIGINAL_HASH')
        path=original_dir/name
        if path.is_symlink() or not path.is_file() or path.resolve().parent!=original_dir:
            raise MemoryErrorSafety('MISSING_OR_LINKED_PRISTINE_REFERENCE:'+name)
        if digest(path)!=expected:raise MemoryErrorSafety('PRISTINE_REFERENCE_BYTES_CHANGED:'+name)
        if 'ORIGINAL' not in a.get('provenance',''):raise MemoryErrorSafety('UNTRUSTED_ORIGINAL_DECLARATION')
        names.add(name)
    if seed.get('original_scene_anchor') not in names:raise MemoryErrorSafety('SCENE_ANCHOR_NOT_AN_ORIGINAL')
    for actor in seed['characters']:
        if not set(actor.get('authentic_ref_ids',[]))<=names:raise MemoryErrorSafety('ACTOR_REFERENCES_NOT_DECLARED')
    if seed.get('authority') != {'final_film_approval':False,'automatic_h3_generation':False,
       'generated_footage_as_h3_visual_conditioning':False,'scene_dna_verified':False}:
        raise MemoryErrorSafety('UNAUTHORIZED_SCENE_SEED_FLAGS')
    return True

def verify_decision(decision,case,live_render_sha=None):
    if decision.get('schema')!='director-autoeditor-source-bound-audition-v0733':
        raise MemoryErrorSafety('UNRECOGNIZED_AUTOEDIT_DECISION')
    selected=decision.get('selection',{}).get('incoming_entry_frame')
    if type(selected) is not int or selected!=SELECTION_FRAME:
        raise MemoryErrorSafety('AUTOEDIT_SELECTION_NOT_F36')
    for side in ('outgoing','incoming'):
        inp=decision.get('inputs',{}).get(side,{})
        if inp.get('sha256')!=case[side]['sha256'] or inp.get('filename')!=case[side]['filename']:
            raise MemoryErrorSafety('AUTOEDIT_SOURCE_BINDING_MISMATCH')
    flags=decision.get('authorization',{})
    for key in ('h3_generation','generated_video_for_h3_visual_conditioning','final_film_approval','continuity_handoff','director_memory_update','production_edit','working_edit_approval'):
        if flags.get(key) is not False:raise MemoryErrorSafety('UNSAFE_AUTOEDIT_DECISION_FLAG:'+key)
    if decision.get('reference_to_human_approval_consulted_during_ranking') is not False:
        raise MemoryErrorSafety('AUTOEDIT_NOT_INDEPENDENT_FROM_F42')
    validation=decision.get('render_validation',{})
    if validation.get('frames')!=212 or validation.get('fps')!=24 or validation.get('audio_stream_count')!=1:
        raise MemoryErrorSafety('AUTOEDIT_RENDER_UNVERIFIED')
    value=validation.get('sha256')
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise MemoryErrorSafety('INVALID_RENDER_HASH')
    if live_render_sha is not None and value != live_render_sha:raise MemoryErrorSafety('WRONG_LIVE_RENDER')
    return value

def event_hash(seq,kind,payload,previous):
    return hashlib.sha256(canonical({'seq':seq,'kind':kind,'payload':payload,'prev':previous}).encode('utf-8')).hexdigest()

def connect(path):
    con=sqlite3.connect(str(path),timeout=20,isolation_level=None)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA busy_timeout=20000')
    con.execute('PRAGMA synchronous=FULL')
    return con

def create_tables(c):
    c.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL)')
    c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.execute('INSERT INTO metadata(key,value) VALUES (?,?)',('schema',SCHEMA))

def read_events(c):
    try:
        ver=c.execute('SELECT value FROM metadata WHERE key=?',('schema',)).fetchone()
        if ver is None or ver[0]!=SCHEMA:raise MemoryErrorSafety('UNRECOGNIZED_PROJECT_DB_SCHEMA')
        rows=c.execute('SELECT seq,kind,payload,prev_hash,event_hash FROM events ORDER BY seq').fetchall()
    except sqlite3.DatabaseError as e:raise MemoryErrorSafety('BROKEN_PROJECT_DATABASE') from e
    if not rows:raise MemoryErrorSafety('EMPTY_PROJECT_LEDGER')
    previous=GENESIS
    out=[]
    for num,row in enumerate(rows,1):
        try: payload=json.loads(row['payload'])
        except ValueError as e:raise MemoryErrorSafety('INVALID_EVENT_PAYLOAD') from e
        if row['seq']!=num or row['prev_hash']!=previous or row['payload']!=canonical(payload) or row['event_hash']!=event_hash(num,row['kind'],payload,previous):
            raise MemoryErrorSafety('EVENT_CHAIN_INTEGRITY_FAILED')
        out.append({'seq':num,'kind':row['kind'],'payload':payload,'event_hash':row['event_hash']})
        previous=row['event_hash']
    if len(out)<2 or out[0]['kind']!='PROJECT_INITIALIZED' or out[1]['kind']!='WORKING_EDIT_SELECTED':
        raise MemoryErrorSafety('PROJECT_GENESIS_MISSING')
    if any(x['kind'] != 'PLAN_PROPOSED' for x in out[2:]):
        raise MemoryErrorSafety('UNEXPECTED_SECOND_GENESIS_OR_EDIT_REWRITE')
    return out

def append_event(c,kind,payload):
    last=c.execute('SELECT seq,event_hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
    seq=(last['seq']+1) if last else 1
    prev=last['event_hash'] if last else GENESIS
    c.execute('INSERT INTO events VALUES (?,?,?,?,?)',(seq,kind,canonical(payload),prev,event_hash(seq,kind,payload,prev)))

def current(events):
    init,selection=events[0]['payload'],events[1]['payload']
    if selection.get('active_entry_frame')!=SELECTION_FRAME or selection.get('prior_working_entry_frame')!=42:
        raise MemoryErrorSafety('UNRECOGNIZED_WORKING_EDIT_STATE')
    expected=init['source_bound_capture']
    if selection['source_sha256']!={'outgoing':expected['outgoing']['source_sha256'], 'incoming':expected['incoming']['source_sha256']}:
        raise MemoryErrorSafety('WORKING_EDIT_NOT_BOUND_TO_PROJECT_CAPTURE')
    if selection.get('timeline_frames')!=212 or selection.get('shot1_retain_all') is not True:
        raise MemoryErrorSafety('WORKING_TIMELINE_INCONSISTENT')
    if not isinstance(init['seed'],dict):raise MemoryErrorSafety('INVALID_PERSISTED_SCENE_SEED')
    plans=[x for x in events if x['kind']=='PLAN_PROPOSED']
    return {'schema':SCHEMA,'project_id':init['seed']['project_id'],'project_title':init['seed']['title'],
        'captured_source_origin':init['provenance'], 'scene_brief':init['seed'],
        'capture_binding':expected,'working_edit':selection,
        'working_shot_count':2,'next_unfilmed_shot':3,
        'unresolved_story_beat':init['seed']['working_story_state']['unfinished_narrative'],
        'last_proposal':plans[-1]['payload'] if plans else None,
        'latest_event_seq':events[-1]['seq'], 'latest_event_hash':events[-1]['event_hash'],
        'authority':{'automatic_generation':False,'memory_truth_from_qwen':False,'scene_dna_verified':False,
             'auto_final_film_approval':False,'visual_refs_from_generated_footage':False}}

def init_project(dest,seed_path,case_path,decision_path,original_dir, saved_root,source_root, *,synthetic=False):
    dest=Path(dest)
    if dest.exists() or dest.is_symlink():raise FileExistsError('PROJECT_DESTINATION_ALREADY_EXISTS')
    if dest.parent.is_symlink():raise MemoryErrorSafety('LINKED_PROJECT_PARENT')
    for protected in (Path(saved_root),Path(source_root),Path(original_dir)):
        if dest.resolve().is_relative_to(protected.resolve()):
            raise MemoryErrorSafety('PROJECT_MUST_NOT_RESIDE_IN_CAPTURE_OR_ORIGINALS_ROOT')
    seed=jread(seed_path);validate_seed(seed,original_dir)
    case=jread(case_path)
    decision=jread(decision_path)
    import director_unified_edit_v0734 as integ
    integ.validate_case(case)
    decision_render_hash=verify_decision(decision,case)
    matches=integ.find_jobs(case,Path(saved_root),Path(source_root),allow_synthetic_fixture=synthetic)
    if synthetic and str(dest).startswith('/workspace/'):
        raise MemoryErrorSafety('REFUSE_SYNTHETIC_FIXTURE_IN_LIVE_WORKSPACE')
    binding={k:{'job_id':v['job_id'],'job_sha256':v['job_sha256'],
                'source_filename':case[k]['filename'],'source_sha256':v['source_sha256'],
                'source_class':'H3_GENERATED_EDITORIAL_ONLY'} for k,v in matches.items()}
    if binding['outgoing']['source_sha256']==binding['incoming']['source_sha256']:
        raise MemoryErrorSafety('DUPLICATE_CAPTURE_SOURCES')
    # Recheck source bytes against the initialized membership after all reads.
    for k,v in matches.items():
        if digest(v['path'])!=binding[k]['source_sha256'] or digest(v['job_path'])!=binding[k]['job_sha256']:
            raise MemoryErrorSafety('CAPTURE_CHANGED_DURING_PROJECT_INIT')
    project_init={'seed':seed,'scene_seed_sha256':digest(seed_path),'source_bound_capture':binding,
        'provenance':DEMO_MARKER if synthetic else 'REAL_VAST_JOB_FILES_VERIFIED_NOT_INDEPENDENT_ORIGIN_CERTIFICATION',
        'never_use_generated_video_as_h3_visual_conditioning':True}
    approved={'active_entry_frame':36,'prior_working_entry_frame':42,'timeline_frames':212,'fps':24,
        'shot1_retain_all':True,'shot2_source_start_frame':36,'shot2_source_end_exclusive':124,
        'shot1_source_start_frame':0,'shot1_source_end_exclusive':124,
        'source_sha256':{k:binding[k]['source_sha256'] for k in ('outgoing','incoming')},
        'autoedit_decision_sha256':digest(decision_path),'autoedit_render_sha256':decision_render_hash,
        'selection_basis':'INDEPENDENT_TECHNICAL_AUTOEDITOR_PROPOSAL_F36_AND_USER_APPROVAL_IN_CHAT',
        'human_approval_authentication':'IN_CHAT_ATTESTATION_NOT_CRYPTOGRAPHICALLY_AUTHENTICATED',
        'history':[{'entry_frame':42,'status':'SUPERSEDED_HUMAN_WORKING_SELECTION_PRESERVED'},
                   {'entry_frame':36,'status':'ACTIVE_HUMAN_APPROVED_WORKING_SELECTION_NOT_FINAL'}],
        'human_confirmed_pause':'INTENTIONAL_CONVERSATIONAL_GAP_PRESERVE',
        'observation_truth':'END_POSE_DIALOGUE_CONTENT_UNVERIFIED',
        'permission':{'render_working_preview':True,'generate_h3':False,'final_approve':False,
                      'generated_video_for_h3_visual_reference':False,'promote_scene_dna':False}}
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_name(dest.name+'.creating.'+str(os.getpid()))
    if tmp.exists():raise FileExistsError('EXISTING_PROJECT_STAGING')
    tmp.mkdir(mode=0o700)
    try:
        with closing(connect(tmp/PROJECT_DB)) as c:
            c.execute('BEGIN IMMEDIATE')
            create_tables(c)
            append_event(c,'PROJECT_INITIALIZED',project_init)
            append_event(c,'WORKING_EDIT_SELECTED',approved)
            c.execute('COMMIT')
            events=read_events(c);current(events)
        os.replace(tmp,dest)
    finally:
        if tmp.exists():shutil.rmtree(tmp)
    return status(dest)

def status(project):
    root=Path(project)
    if root.is_symlink() or not root.is_dir():raise MemoryErrorSafety('NO_PROJECT_DIRECTORY')
    db=root/PROJECT_DB;ensure_plain_file(db)
    with closing(connect(db)) as c: return current(read_events(c))

def generate_prompt(state):
    scene=state['scene_brief']
    # This is controlled creative direction, never a claim about observed speech.
    return (
      f"SINGLE UNBROKEN, naturalistic 9:16 cinematic hallway interview, 24 FPS. "
      f"Setting: {scene['setting']}. Tone: {scene['tone']}. "
      "Use ONLY the SIX verified ORIGINAL reference images; the original full hallway frame is the primary scene anchor. "
      "Both subjects share ONE stable medium-wide composition from the FIRST FRAME to the LAST FRAME: "
      "the presenter on frame LEFT and Grandpa on frame RIGHT. Maintain both faces, their real appearance, wardrobe, "
      "lighting and spatial relationship. The conversation has already begun. DO NOT repeat the opening question, "
      "restart an introduction, or replace the shared shot with separate solo shots. "
      "Continue the interview naturally: Grandpa is finishing his answer; the presenter listens attentively, "
      "lets him finish, offers a small genuine reaction or follow-up invitation when appropriate. "
      "The microphone remains visible as an interaction prop, with subtle movements only; do not mandate a nod, "
      "touch, transfer, or scripted exact dialogue. Allow genuine pauses and conversational timing. "
      "One fixed composition with only modest natural camera movement. "
      "Maintain believable original-reference identities and consistent lighting throughout. "
      "End on a naturally readable shared two-person beat with visible faces, not an artificial freeze. "
      "No embedded jump cut, close-up swap, character replacement, or generated earlier footage as visual reference."
    )

def build_workflow(project,seed,original_dir,template_path):
    from director_shot2_preflight import validate_graph, ORIGINALS
    path=Path(template_path);ensure_plain_file(path)
    graph=validate_graph(path)
    # validate all runtime original images against the scene's saved declarations.
    validate_seed(seed,original_dir)
    if {a['name']:a['sha256'] for a in seed['original_assets']}!=ORIGINALS:
        raise MemoryErrorSafety('PRISTINE_MANIFEST_DISAGREES_WITH_VERIFIED_H3_GRAPH')
    node_by_id={n['id']:n for n in graph['nodes']}
    assert node_by_id[136]['type']=='MiniMaxH3ReferenceToVideo'
    assert node_by_id[129]['type']=='RandomNoise'
    assert node_by_id[221]['type']=='VHS_VideoCombine'
    node_by_id[136]['widgets_values'][0]=generate_prompt({'scene_brief':seed})
    source=canonical({'project':seed['project_id'],'shot':3,'working_cut':36})
    new_seed=int(hashlib.sha256(source.encode()).hexdigest()[:12],16)
    node_by_id[129]['widgets_values'][0]=new_seed
    node_by_id[129]['widgets_values'][1]='fixed'
    prefix='Director_H3_Hallway_Shot03_Continuation_Shadow_UNAPPROVED'
    node_by_id[221]['widgets_values']['filename_prefix']=prefix
    # Re-read original graph and forbid *all* other mutations, including any video
    # ref links or unauthorized LoadImage filenames being introduced.
    orig=validate_graph(path)
    changes=[]
    old={n['id']:n for n in orig['nodes']}
    for nid,node in node_by_id.items():
        if node!=old[nid]:changes.append(nid)
    if sorted(changes)!=[129,136,221]:raise MemoryErrorSafety('UNAUTHORIZED_WORKFLOW_MUTATION')
    if graph['links']!=orig['links']:raise MemoryErrorSafety('WORKFLOW_LINKS_CHANGED')
    assert {n['widgets_values'][0] for n in graph['nodes'] if n['type']=='LoadImage'}==set(ORIGINALS)
    for k in ('ref_videos.','ref_video_audios.','ref_audios.'):
        if any(i['name'].startswith(k) and i['link'] is not None for i in node_by_id[136]['inputs']):
            raise MemoryErrorSafety('GENERATED_OR_OTHER_VIDEO_CONDITIONING_DETECTED')
    return graph,prefix,new_seed

def continue_interview(project,instruction,original_dir,template_path):
    phrase=' '.join(instruction.strip().lower().split()).strip(' .!?')
    if phrase not in ALLOWED_REQUESTS:raise MemoryErrorSafety('UNSUPPORTED_COMMAND_REQUIRES_NEW_STORY_INTENT')
    root=Path(project)
    db=root/PROJECT_DB;ensure_plain_file(db)
    with closing(connect(db)) as c:
        c.execute('BEGIN IMMEDIATE')
        events=read_events(c)
        state=current(events)
        seed=state['scene_brief']
        graph,prefix,rng=build_workflow(root,seed,original_dir,template_path)
        existing=next((e['payload'] for e in reversed(events) if e['kind']=='PLAN_PROPOSED' and e['payload'].get('shot_number')==3),None)
        plan={'schema':'director-next-beat-proposal-v0735','project_id':state['project_id'],
          'normalized_instruction':'continue the interview','shot_number':3,
          'last_approved_working_cut':{'incoming_entry_frame':36,'source_count':2,'timeline_frames':212},
          'prior_human_preference':{'incoming_entry_frame':42,'status':'SUPERSEDED_PRESERVED_FOR_REVERSAL'},
          'narrative_objective':'Grandpa completes his response; presenter listens and acknowledges, then begins a natural follow-up if appropriate',
          'known_context_origin':'PERSISTED_HUMAN_CREATIVE_BRIEF_AND_APPROVED_EDIT_NOT_AUTOMATIC_QWEN_TRUTH',
          'dialogue_transcript':'UNKNOWN','last_microphone_owner':'UNKNOWN','verified_last_22_frame_motion':'UNKNOWN',
          'ending_state':'UNKNOWN_FROM_UNREVIEWED_PERFORMANCE_EVIDENCE',
          'generation_prompt':generate_prompt(state),
          'original_visual_reference_filenames':[a['name'] for a in seed['original_assets']],
          'original_visual_reference_sha256':{a['name']:a['sha256'] for a in seed['original_assets']},
          'never_condition_on_previous_generated_video':True,
          'captured_shot_refs':{k:v['source_sha256'] for k,v in state['capture_binding'].items()},
          'workflow_output_name':'Director_H3_Hallway_Shot03_Continuation_CANDIDATE.json',
          'savedshot_output_prefix':prefix,'rng_seed':rng,
          'status':'PLANNED_REQUIRES_EXPLICIT_HUMAN_GENERATION_PERMISSION',
          'automatic_generation_authorized':False,'automatic_edit_approval_authorized':False,
          'scene_dna_verified':False}
        if existing is not None:
            if existing!=plan:raise MemoryErrorSafety('SAME_STORY_BEAT_PLAN_CONFLICT')
            c.execute('COMMIT')
        else:
            append_event(c,'PLAN_PROPOSED',plan)
            c.execute('COMMIT')
        events=read_events(c)
    outputs=root/'plans'
    if outputs.is_symlink():raise MemoryErrorSafety('LINKED_PLANS_DIRECTORY')
    outputs.mkdir(exist_ok=True)
    expected={'plan_shot03_v0735.json':canonical(plan)+'\n', plan['workflow_output_name']:json.dumps(graph,indent=2,ensure_ascii=False)+'\n'}
    for name,data in expected.items():
        file=outputs/name
        if file.is_symlink():raise MemoryErrorSafety('LINKED_PLAN_OUTPUT')
        if file.exists():
            if file.read_text(encoding='utf-8')!=data:raise MemoryErrorSafety('EXISTING_PLAN_WAS_CHANGED')
        else:
            fd,tmp=tempfile.mkstemp(prefix='.stage.',dir=outputs)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as f:
                    f.write(data);f.flush();os.fsync(f.fileno())
                if file.exists():raise MemoryErrorSafety('PLAN_RACE_CONFLICT')
                os.replace(tmp,file)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
    return {'project':status(root),'plan':plan,'plan_path':str(outputs/'plan_shot03_v0735.json'),
            'workflow_path':str(outputs/plan['workflow_output_name']),
            'originals_verified':True,'generated_h3':False}

def export_project(project,out):
    root=Path(project);out=Path(out)
    if out.exists() or out.is_symlink():raise FileExistsError('BACKUP_EXISTS')
    current_state=status(root)
    if out.resolve().is_relative_to(root.resolve()):raise MemoryErrorSafety('BACKUP_MUST_BE_OUTSIDE_PROJECT')
    with tempfile.TemporaryDirectory() as temp:
        backup=Path(temp)/PROJECT_DB
        src=connect(root/PROJECT_DB); dst=connect(backup)
        try:src.backup(dst)
        finally:src.close();dst.close()
        with closing(connect(backup)) as c:
            after=current(read_events(c))
        if after['latest_event_hash']!=current_state['latest_event_hash']:
            raise MemoryErrorSafety('LEDGER_CHANGED_DURING_BACKUP')
        manifest={'schema':'director-project-backup-v0735','project_id':after['project_id'],
          'database_sha256':digest(backup),'last_event_hash':after['latest_event_hash'],
          'contains_media_or_original_image_bytes':False,
          'reference_assets_must_be_restored_separately':True}
        with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED) as z:
            z.write(backup,PROJECT_DB);z.writestr('backup_manifest.json',canonical(manifest)+'\n')
    return manifest

def import_project(backup,project):
    dst=Path(project)
    if dst.exists() or dst.is_symlink():raise FileExistsError('PROJECT_ALREADY_EXISTS')
    backup=Path(backup);ensure_plain_file(backup)
    with zipfile.ZipFile(backup,'r') as z:
        if set(z.namelist())!={PROJECT_DB,'backup_manifest.json'} or len(z.namelist())!=2:
            raise MemoryErrorSafety('BACKUP_CONTENTS_NOT_RECOGNIZED')
        if any(x.file_size>15_000_000 for x in z.infolist()):raise MemoryErrorSafety('BACKUP_SIZE_EXCEEDED')
        m=json.loads(z.read('backup_manifest.json'))
        if m.get('schema')!='director-project-backup-v0735':raise MemoryErrorSafety('BACKUP_MANIFEST_INVALID')
        raw=z.read(PROJECT_DB)
        if hashlib.sha256(raw).hexdigest()!=m.get('database_sha256'):
            raise MemoryErrorSafety('BACKUP_DATABASE_TAMPERED')
    dst.parent.mkdir(parents=True,exist_ok=True)
    stage=dst.with_name(dst.name+'.import.'+str(os.getpid()))
    if stage.exists() or stage.is_symlink():raise MemoryErrorSafety('IMPORTED_STAGE_EXISTS')
    stage.mkdir(mode=0o700)
    try:
        db=stage/PROJECT_DB;db.write_bytes(raw)
        status_pre=status(stage)
        if status_pre['latest_event_hash']!=m['last_event_hash'] or status_pre['project_id']!=m['project_id']:
            raise MemoryErrorSafety('BACKUP_LEDGER_MISMATCH')
        os.replace(stage,dst)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return status(dst)

def rebuild_working(project, saved_root, source_root):
    """Render the persisted *human-working* F36 cut from matching live captures.

    Reuses V0.7.33's frame and PCM verification; does not choose a different cut,
    run H3/Qwen, promote memory or claim final approval.
    """
    import director_autoedit_v0733 as ed
    import director_unified_edit_v0734 as integ
    root=Path(project)
    state=status(root)
    if state['captured_source_origin']==DEMO_MARKER and str(root).startswith('/workspace/'):
        raise MemoryErrorSafety('SIMULATED_CAPTURE_CANNOT_BECOME_LIVE_WORKING_EDIT')
    raw={'schema':'director-edit-case-v0733','outgoing':{'filename':state['capture_binding']['outgoing']['source_filename'],
        'sha256':state['capture_binding']['outgoing']['source_sha256'],
        'source_class':'H3_GENERATED_OBSERVATION_AND_EDITORIAL_ONLY','retain_all_frames':True,
        'human_working_status':'SHOT1_APPROVED_WORKING_NOT_FINAL'},
        'incoming':{'filename':state['capture_binding']['incoming']['source_filename'],
        'sha256':state['capture_binding']['incoming']['source_sha256'],
        'source_class':'H3_GENERATED_OBSERVATION_AND_EDITORIAL_ONLY'},
        'benchmark_not_in_case_by_design':True,'no_generated_video_visual_conditioning_for_h3':True}
    found=integ.find_jobs(raw,Path(saved_root),Path(source_root),allow_synthetic_fixture=state['captured_source_origin']==DEMO_MARKER)
    for slot in ('outgoing','incoming'):
        if found[slot]['job_sha256']!=state['capture_binding'][slot]['job_sha256'] or found[slot]['job_id']!=state['capture_binding'][slot]['job_id']:
            raise MemoryErrorSafety('CAPTURE_BINDING_CHANGED_SINCE_INIT')
    renders=root/'renders'
    if renders.is_symlink():raise MemoryErrorSafety('LINKED_RENDER_DIRECTORY')
    renders.mkdir(exist_ok=True)
    dest=renders/'Director_Hallway_2shot_F36_HUMAN_WORKING_NOT_FINAL.mp4'
    receipt=renders/'working_render_receipt_v0735.json'
    if dest.exists() or receipt.exists() or dest.is_symlink() or receipt.is_symlink():
        if dest.is_symlink() or receipt.is_symlink() or not dest.is_file() or not receipt.is_file():
            raise MemoryErrorSafety('INCOMPLETE_OR_LINKED_OLD_WORKING_RENDER')
        prev=jread(receipt)
        if prev.get('sha256')!=digest(dest) or prev.get('working_entry_frame')!=36 or prev.get('source_bindings')!=state['working_edit']['source_sha256']:
            raise MemoryErrorSafety('EXISTING_WORKING_RENDER_DIVERGED')
        return prev
    result=ed.render(found['outgoing']['path'],found['incoming']['path'],36,dest)
    if any(digest(found[slot]['path'])!=state['capture_binding'][slot]['source_sha256'] for slot in ('outgoing','incoming')):
        raise MemoryErrorSafety('MEDIA_CHANGED_DURING_RENDER')
    record={'schema':'director-working-render-v0735','sha256':result['sha256'],'frames':result['frames'],
            'working_entry_frame':36,'source_bindings':state['working_edit']['source_sha256'],
            'audio_correlations':result['audible_pcm_source_matches'],
            'human_working_approval_in_chat_not_final':True,
            'generated_video_as_H3_visual_conditioning':False,
            'render_path':str(dest)}
    fd,tmp=tempfile.mkstemp(prefix='.receipt.',dir=renders)
    try:
        with os.fdopen(fd,'w') as f:f.write(json.dumps(record,indent=2,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,receipt)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return record

def stage_workflow(project, originals_dir, template_path, comfy_root, queue_url):
    """Human-triggered stage for review. No ComfyUI queue insertion."""
    from urllib.request import urlopen
    root=Path(project)
    state=status(root)
    if not state['last_proposal']:raise MemoryErrorSafety('CONTINUE_FIRST_TO_CREATE_PLAN')
    comfy=Path(comfy_root)
    if comfy.is_symlink() or not (comfy/'main.py').is_file():raise MemoryErrorSafety('COMFYUI_NOT_FOUND')
    if Path(originals_dir).resolve()!= (comfy/'input').resolve():
        raise MemoryErrorSafety('ORIGINAL_DIR_NOT_COMFYUI_INPUT')
    seed=state['scene_brief']
    graph,_,_=build_workflow(root,seed,originals_dir,template_path)
    name=state['last_proposal']['workflow_output_name']
    candidate=root/'plans'/name
    ensure_plain_file(candidate)
    if jread(candidate)!=graph:raise MemoryErrorSafety('STAGED_GRAPH_NOT_VERIFIED')
    try:
        with urlopen(queue_url.rstrip('/')+'/object_info',timeout=8) as resp: info=json.load(resp)
        with urlopen(queue_url.rstrip('/')+'/queue',timeout=8) as resp: queue=json.load(resp)
    except Exception as e:raise MemoryErrorSafety('LIVE_COMFYUI_CHECK_FAILED') from e
    if not {'MiniMaxH3ReferenceToVideo','VHS_VideoCombine','DirectorV066SavedShotShadowQueue'}<=set(info):
        raise MemoryErrorSafety('COMFYUI_REQUIRED_NODES_MISSING')
    if type(queue.get('queue_pending')) is not list or type(queue.get('queue_running')) is not list or queue['queue_pending'] or queue['queue_running']:
        raise MemoryErrorSafety('COMFYUI_NOT_CONFIRMED_IDLE')
    targetdir=comfy/'user'/'default'/'workflows'
    if targetdir.is_symlink():raise MemoryErrorSafety('LINKED_COMFYUI_WORKFLOWS')
    targetdir.mkdir(parents=True,exist_ok=True)
    target=targetdir/name
    if target.is_symlink():raise MemoryErrorSafety('LINKED_STAGED_WORKFLOW')
    if target.exists():
        if digest(target)!=digest(candidate):raise MemoryErrorSafety('STAGED_WORKFLOW_ALREADY_DIFFERS')
    else:
        shutil.copyfile(candidate,target)
        if digest(target)!=digest(candidate):raise MemoryErrorSafety('STAGING_COPY_MISMATCH')
    return {'comfyui_workflow_path':str(target),'h3_queue_submitted':False,
            'next_action':'INSPECT_WORKFLOW_AND_AUTHORIZE_ONE_GENERATION_SEPARATELY',
            'runtime_pristine_refs_verified':True}

def cli():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init');a.add_argument('--project',required=True);a.add_argument('--seed',default=str(Path(__file__).parent/'scene_seed_v0735.json'));a.add_argument('--case',default=str(Path(__file__).parent/'autoedit_case.json'));a.add_argument('--decision',default=str(Path(__file__).parent/'live_f36_decision.json'));a.add_argument('--originals-dir',required=True);a.add_argument('--saved-root',required=True);a.add_argument('--source-root',required=True);a.add_argument('--synthetic-offline-fixture',action='store_true')
    a=sub.add_parser('continue');a.add_argument('--project',required=True);a.add_argument('instruction');a.add_argument('--originals-dir',required=True);a.add_argument('--template',default=str(Path(__file__).parent/'workflow_template_verified.json'))
    a=sub.add_parser('status');a.add_argument('--project',required=True)
    a=sub.add_parser('rebuild');a.add_argument('--project',required=True);a.add_argument('--saved-root',required=True);a.add_argument('--source-root',required=True)
    a=sub.add_parser('stage');a.add_argument('--project',required=True);a.add_argument('--originals-dir',required=True);a.add_argument('--template',default=str(Path(__file__).parent/'workflow_template_verified.json'));a.add_argument('--comfy-root',default='/workspace/ComfyUI');a.add_argument('--queue-url',default='http://127.0.0.1:18188')
    a=sub.add_parser('export');a.add_argument('--project',required=True);a.add_argument('--out',required=True)
    a=sub.add_parser('import');a.add_argument('--backup',required=True);a.add_argument('--project',required=True)
    args=p.parse_args()
    if args.command=='init': result=init_project(args.project,args.seed,args.case,args.decision,args.originals_dir,args.saved_root,args.source_root,synthetic=args.synthetic_offline_fixture)
    elif args.command=='continue':result=continue_interview(args.project,args.instruction,args.originals_dir,args.template)
    elif args.command=='status':result=status(args.project)
    elif args.command=='rebuild':result=rebuild_working(args.project,args.saved_root,args.source_root)
    elif args.command=='stage':result=stage_workflow(args.project,args.originals_dir,args.template,args.comfy_root,args.queue_url)
    elif args.command=='export':result=export_project(args.project,args.out)
    else:result=import_project(args.backup,args.project)
    print(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False))
    print('DIRECTOR_V0735_'+args.command.upper()+'_PASS_NO_GENERATION')

if __name__=='__main__':cli()
