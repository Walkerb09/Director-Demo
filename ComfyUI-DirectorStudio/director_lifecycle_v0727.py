#!/usr/bin/env python3
"""Director V0.7.27: read-only creative-plan to post-shot evidence lifecycle bridge.

This is an evidence index, NOT an automatic editing/generation/director policy.
Explicit operator links are unverified administrative associations, never proof
that footage depicts the claimed actors/beat, was captured for the shot, or was accepted.
"""
from __future__ import annotations
import argparse, hashlib, html, json, re
from pathlib import Path

SCHEMA = 'director-shot-lifecycle-v0727'
INDEX = 'director-lifecycle-index-v0727'
PLAN = 'director-creative-policy-v0726'
BRIDGE = 'director-postshot-editorial-bridge-shadow-v0718'
SHA = re.compile(r'^[a-f0-9]{64}$')
IDENT = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{1,79}$')
ALLOWED_LIVE = {'draft_creative_shot_plan', 'technical_review_prioritization'}
AUTH_FLAGS = ['h3_generation','h3_visual_conditioning','automatic_editing','automatic_cut_selection',
    'continuity_handoff','memory_mutation','production_approval','performance_certification',
    'scene_dna_verification','actor_identity_certification','prop_ownership_certification','gesture_certification']
UNKNOWN_FIELDS = ('gesture','actor_identity','microphone_holder','naturalness','emotion_arc',
                  'audio_timing','performance_interruptions')

class Refuse(ValueError): pass

def hash_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()

def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def ensure_hash(value):
    if not isinstance(value,str) or not SHA.fullmatch(value):raise Refuse('INVALID_SHA256')

def safe_file(base, value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:raise Refuse('INVALID_RELATIVE_PATH')
    relative=Path(value)
    if relative.is_absolute() or any(x in ('..','.') for x in relative.parts):raise Refuse('UNSAFE_RELATIVE_PATH')
    p=base / relative
    if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(base.resolve()):raise Refuse('MISSING_OR_LINKED_FILE')
    return p

def ensure_flags_off(flags, allow=()):
    if not isinstance(flags,dict) or not flags:raise Refuse('MISSING_AUTHORITY_FLAGS')
    if any(k not in allow and v is not False for k,v in flags.items()):raise Refuse('PRODUCTION_AUTHORIZATION_FORBIDDEN')
    for key in AUTH_FLAGS:
        if flags.get(key) is not False:raise Refuse('UNSAFE_AUTHORITY')

def verify_plan(plan):
    if not isinstance(plan,dict) or plan.get('schema')!=PLAN or plan.get('mode')!='SHADOW_ONLY' or plan.get('operational_baseline')!='V0.7.5_UNMODIFIED':raise Refuse('WRONG_PLAN')
    ensure_flags_off(plan.get('authority'),ALLOWED_LIVE)
    if not all(plan['authority'].get(k) is True for k in ALLOWED_LIVE):raise Refuse('INVALID_PLANNING_AUTHORITY')
    if plan.get('provenance_rule')!='NEVER_ROUTE_GENERATED_IMAGES_OR_VIDEOS_TO_H3_VISUAL_INPUT':raise Refuse('CLEAN_CHAIN_VIOLATION')
    if plan.get('creative_autonomy')!='PROPOSE_VARIATIONS_WITHOUT_EXECUTION' or plan.get('planning_basis')!='RULE_BASED_COVERAGE_HEURISTICS_NOT_MODEL_FILM_JUDGMENT':raise Refuse('UNTRUSTED_PLANNING_CLAIM')
    decl=plan.get('authentic_source_declarations')
    if not isinstance(decl,list) or not decl:raise Refuse('NO_SOURCE_DECLARATIONS')
    originals=set()
    for a in decl:
        ensure_hash(a.get('sha256'))
        if a.get('provenance')!='CHECKPOINT_RECORDED_ORIGINAL_NOT_RUNTIME_VERIFIED' or not IDENT.fullmatch(a.get('asset_id','')):raise Refuse('UNSAFE_ORIGINAL_DECLARATION')
        originals.add(a['asset_id'])
    shots=plan.get('shots')
    if not isinstance(shots,list) or not 1<=len(shots)<=12:raise Refuse('INVALID_SHOT_LIST')
    ids=[]
    for s in shots:
        if not isinstance(s,dict) or not isinstance(s.get('shot_id'),str) or not IDENT.fullmatch(s['shot_id']):raise Refuse('INVALID_SHOT_ID')
        ids.append(s['shot_id'])
        if (s.get('proposal_only') is not True or s.get('h3_executable') is not False or
            s.get('never_use_generated_previous_frame_or_clip_as_reference') is not True or
            s.get('conditioning_source_policy')!='AUTHENTIC_ORIGINALS_ONLY_REQUIRE_RUNTIME_SHA_VERIFICATION' or
            s.get('observed_gesture') is not None or s.get('verified_continuity_handoff') is not None):
            raise Refuse('PLAN_HAS_UNAUTHORIZED_EXECUTION_OR_CLAIMS')
        refs=s.get('source_reference_ids_by_actor')
        if not isinstance(refs,dict) or not refs or any(not isinstance(r,list) or not r or not set(r)<=originals for r in refs.values()):raise Refuse('UNVERIFIED_VISUAL_SOURCE')
    if len(set(ids))!=len(ids):raise Refuse('DUPLICATE_SHOT_ID')
    wc=plan.get('historical_working_cut_preserved')
    if wc is not None and wc.get('status')!='HUMAN_WORKING_NOT_FINAL':raise Refuse('PROMOTED_HISTORICAL_CUT')
    return ids

def verify_bridge(rep, source):
    if rep.get('schema')!=BRIDGE or rep.get('mode')!='SHADOW_ONLY' or rep.get('decision')!='REVIEW_CANDIDATES_ONLY_NO_AUTOMATIC_EDIT':raise Refuse('BAD_BRIDGE')
    ensure_flags_off(rep.get('authorizations'))
    if rep.get('integration')!='READ_ONLY_POSTSHOT_ADAPTER_NOT_ATTACHED_TO_V075_WORKER':raise Refuse('UNEXPECTED_INTEGRATION')
    if rep.get('input_binding_status')!='UNVERIFIED_EXTERNAL_OBSERVATION_ONLY':raise Refuse('UNSUPPORTED_SOURCE_CLASS')
    src=rep.get('source',{})
    ensure_hash(src.get('sha256'))
    if src['sha256']!=hash_file(source):raise Refuse('BRIDGE_SOURCE_HASH_MISMATCH')
    if src.get('source_class')!='UNVERIFIED_EXTERNAL_OBSERVATION_ONLY' or src.get('h3_visual_reference_eligible') is not False:raise Refuse('UNSAFE_VIDEO_PROVENANCE')
    frames=src.get('frame_count')
    if type(frames) is not int or frames<20:raise Refuse('BAD_FRAME_COUNT')
    cuts=rep.get('cut_barriers',{})
    if cuts.get('status')!='CANDIDATE_ONLY_NOT_VERIFIED_EDITORIAL_INTENT' or cuts.get('no_cross_cut_motion_inference') is not True:raise Refuse('UNSAFE_CUT_BARRIER')
    indices=cuts.get('frames')
    if not isinstance(indices,list) or any(type(n) is not int or not 0<n<frames for n in indices) or sorted(set(indices))!=indices:raise Refuse('INVALID_CUT_LIST')
    if cuts.get('empty_list_does_not_prove_uncut_footage') is not (len(indices)==0):raise Refuse('FALSE_UNCUT_CLAIM')
    if rep.get('technical_report',{}).get('candidate_segment_count')!=len(indices)+1:raise Refuse('CUT_SEGMENTS_MISMATCH')
    if any(rep.get('semantics',{}).get(k)!='UNKNOWN' for k in UNKNOWN_FIELDS):raise Refuse('UNVERIFIED_SEMANTIC_CLAIM')
    return {'source_sha256':src['sha256'],'source_frames':frames,'declared_cut_candidates':indices,
            'candidate_segment_count':len(indices)+1,'semantic_evidence':'UNKNOWN_NOT_CERTIFIED',
            'source_class':'UNVERIFIED_EXTERNAL_OBSERVATION_ONLY'}

def reconcile(plan,index,base):
    ids=verify_plan(plan)
    if not isinstance(index,dict) or index.get('schema')!=INDEX:raise Refuse('BAD_BINDINGS_INDEX')
    ensure_hash(index.get('plan_sha256'))
    plan_path=safe_file(base,index.get('plan_path'))
    if hash_file(plan_path)!=index['plan_sha256']:raise Refuse('PLAN_HASH_MISMATCH')
    if read_json(plan_path)!=plan:raise Refuse('PLAN_FILE_DIFFERS')
    observations=index.get('observations')
    if not isinstance(observations,list) or len(observations)>32:raise Refuse('INVALID_OBSERVATION_LIST')
    observed={}
    for o in observations:
        if not isinstance(o,dict) or set(o)!={'id','report_path','report_sha256','source_path'}:raise Refuse('BAD_OBSERVATION_RECORD')
        oid=o['id']
        if not isinstance(oid,str) or not IDENT.fullmatch(oid) or oid in observed:raise Refuse('DUPLICATE_OBSERVATION')
        ensure_hash(o['report_sha256'])
        report_path=safe_file(base,o['report_path'])
        if hash_file(report_path)!=o['report_sha256']:raise Refuse('REPORT_HASH_MISMATCH')
        source_path=safe_file(base,o['source_path'])
        data=verify_bridge(read_json(report_path),source_path)
        data['observation_id']=oid
        data['report_sha256']=o['report_sha256']
        observed[oid]=data
    bindings=index.get('bindings')
    if not isinstance(bindings,list):raise Refuse('INVALID_BINDINGS')
    linked={}; used=set()
    for b in bindings:
        if not isinstance(b,dict) or set(b)!={'shot_id','observation_id','association_status'} or b['association_status']!='OPERATOR_DECLARED_NOT_VERIFIED':raise Refuse('UNAUTHORIZED_ASSOCIATION')
        shot,obs=b['shot_id'],b['observation_id']
        if shot not in ids or obs not in observed or shot in linked or obs in used:raise Refuse('AMBIGUOUS_OR_DUPLICATE_BINDING')
        linked[shot]=obs;used.add(obs)
    cards=[]
    for s in plan['shots']:
        sid=s['shot_id']; obs=observed.get(linked.get(sid))
        state=('REVIEW_PENDING_UNVERIFIED_ASSOCIATION' if obs else 'NO_ASSOCIATED_OBSERVATION')
        checklist=[{'requirement':r,'status':'NOT_VERIFIED'} for r in s['observer_evidence_requirements']]
        cards.append({'shot_id':sid,'narrative_beat':s['narrative_beat'],'emotional_intent_is_proposal':s['creative_emotional_intent'],
            'association_status':state,'observation_id':obs['observation_id'] if obs else None,
            'technical_review_segments':obs['candidate_segment_count'] if obs else None,
            'cut_candidate_frames':obs['declared_cut_candidates'] if obs else [],
            'performance_assessment':'NOT_VERIFIED','observed_action':'UNKNOWN',
            'approved_working_cut':None,'accepted_shot':False,
            'human_review_required':True,'semantic_continuity_promoted':False,
            'missing_verifications':checklist,
            'recommended_next_step':('REVIEW_LINK_VALIDITY_THEN_INSPECT_PERFORMANCE' if obs else 'CAPTURE_OR_EXPLICITLY_LINK_A_SHOT_FOR_REVIEW'),
            'candidate_direction_not_executed':s['candidate_h3_direction_not_executed']})
    unbound=[observed[k] for k in sorted(set(observed)-used)]
    unbound_list=[{'observation_id':o['observation_id'],'source_sha256':o['source_sha256'],
                   'cut_candidate_frames':o['declared_cut_candidates'],'status':'UNASSOCIATED_OBSERVATION'} for o in unbound]
    first=next((x for x in cards if x['association_status']=='NO_ASSOCIATED_OBSERVATION'),None)
    return {'schema':SCHEMA,'version':'0.7.27','mode':'SHADOW_ONLY',
       'plan_sha256':index['plan_sha256'],'bindings_index_sha256':hashlib.sha256(json.dumps(index,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
       'scene_id':plan['scene_id'],'planned_shots':len(cards),'bound_observations':len(linked),'unbound_observations':unbound_list,
       'shot_review_queue':cards,'next_unassociated_coverage_proposal':first['shot_id'] if first else None,
       'next_action_is_administrative_not_execution':True,
       'historical_working_cut':plan['historical_working_cut_preserved'],
       'identity_and_scene_state':'NOT_VERIFIED','automated_performance_judgment':'ABSTAIN',
       'automatic_edit_choice':'ABSTAIN','original_ref_hashes_preserved':plan['authentic_source_declarations'],
       'source_bytes_verified_against_bridge_sha256':True,
       'source_authenticity_automatically_certified':False,
       'h3_visual_inputs_from_generated_media':False,
       'authority':{**{k:False for k in AUTH_FLAGS},'production_integration':False,
                    'director_memory_changed':False,'shot_acceptance':False,
                    'draft_creative_shot_plan':True,'technical_review_prioritization':True},
       'operational_baseline':'V0.7.5_UNMODIFIED',
       'disclaimer':'Links are operator-declared and not verified scene membership, acting, identity, action, or continuity. No jobs are launched.'}

def render_page(report):
    e=lambda x:html.escape(str(x),quote=True)
    rows=''.join(f'<article><p class="tag">{e(s["shot_id"])} · {e(s["narrative_beat"])}</p><h2>{e(s["association_status"])}</h2><p>{e(s["recommended_next_step"])}</p><p>Actions, identity, emotion and ending: <strong>Not verified</strong></p><details><summary>Candidate direction (not executed)</summary><p>{e(s["candidate_direction_not_executed"])}</p></details></article>' for s in report['shot_review_queue'])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Director shot lifecycle</title><style>body{{background:#10141b;color:#e8f0f7;font:16px system-ui;max-width:1050px;margin:35px auto;padding:0 20px;line-height:1.5}}h1{{font-size:29px}}h2{{font-size:17px}}.tag,small{{color:#b5c4d5;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}article{{background:#1a2635;border:1px solid #40506a;border-radius:12px;padding:20px}}details{{color:#d7e1ed}}.warning{{border-left:3px solid #c2a977;padding-left:12px;margin:25px 0}}</style><h1>Director Shot Lifecycle · Shadow Review</h1><p>Scene: {e(report['scene_id'])} · {report['planned_shots']} planned shots · {report['bound_observations']} explicitly associated observation(s)</p><p class="warning">Review preparation only. A linked video is not a validated performance, accepted shot, or authentic conditioning reference. No H3 generation, editing, continuity promotion, or memory mutation is authorized.</p><div class="grid">{rows}</div><p><small>V0.7.5 operational baseline unchanged · No automatic artistic selection · Clean-Chain protected</small></p></html>'''

def create(index_path,out_dir):
    index_path=Path(index_path).resolve(strict=True)
    base=index_path.parent
    index=read_json(index_path)
    plan_path=safe_file(base,index.get('plan_path'))
    plan=read_json(plan_path)
    report=reconcile(plan,index,base)
    out=Path(out_dir)
    if out.exists() or out.is_symlink():raise FileExistsError('EXISTING_RESULTS_REFUSED')
    if out.resolve().is_relative_to(base.resolve()):raise Refuse('OUTPUT_INSIDE_IMMUTABLE_REFERENCE')
    out.mkdir(parents=True,exist_ok=False)
    j=out/'shot_lifecycle_v0727.json'; p=out/'shot_board_v0727.html'
    j.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n')
    p.write_text(render_page(report))
    (out/'SHA256_OUTPUTS.json').write_text(json.dumps({j.name:hash_file(j),p.name:hash_file(p)},indent=2)+'\n')
    return report

def cli():
    p=argparse.ArgumentParser()
    p.add_argument('--index',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();result=create(a.index,a.out)
    print('V0727_SHADOW_REPORT',result['planned_shots'],'linked',result['bound_observations'],'unbound',len(result['unbound_observations']),'generation',result['authority']['h3_generation'])
if __name__=='__main__':cli()
