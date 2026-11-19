#!/usr/bin/env python3
"""V0.7.29 read-only unified shot-board controller.

Only validates and reconciles claims. Never writes to source files, invokes ComfyUI,
performs H3 inference, mutates memory, certifies actors, or approves edits.
"""
from __future__ import annotations
import argparse, hashlib, html, json
from pathlib import Path
import director_review_v0728 as reviews
import director_lifecycle_v0727 as lifecycle

SCHEMA='director-unified-controller-v0729'
REVIEW_STATES={'ACCEPT_WORKING','REJECT_WORKING','NEEDS_MORE_REVIEW'}
NO_AUTH={**{k:False for k in lifecycle.AUTH_FLAGS},
         'memory_update':False,'persistent_scene_dna':False,
         'unattended_gpu_scheduler':False,'h3_execution':False,
         'shot_acceptance':False,'final_film_approval':False}
ALLOWED_TRANSITIONS={
    'CAPTURE_OR_DECLARE_ASSOCIATION',
    'VERIFY_MEMBERSHIP_AND_REVIEW_FOOTAGE',
    'RESOLVE_REVIEW_UNCERTAINTY',
    'CONSIDER_REWORK_FROM_AUTHENTIC_ORIGINALS',
    'DISREGARD_SIMULATION_AND_REQUEST_REAL_REVIEW',
    'PROPOSE_NEXT_BEAT_TEXT_ONLY',
    'REVIEW_FINAL_BEAT_WITHOUT_NEXT_SHOT',
}

def _digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def build(index_path:Path):
    index_path=Path(index_path).resolve(strict=True)
    base=index_path.parent
    rev_index=lifecycle.read_json(index_path)
    # Delegate to the validated V0728 and V0727 reconciliation paths.
    reviewed=reviews.reconcile(rev_index,base)
    lifecycle_index=lifecycle.read_json(lifecycle.safe_file(base,rev_index['lifecycle_index_path']))
    plan=lifecycle.read_json(lifecycle.safe_file(base,lifecycle_index['plan_path']))
    board=lifecycle.reconcile(plan,lifecycle_index,base)
    if reviewed['shot_plan_sha256']!=lifecycle_index['plan_sha256'] or reviewed['source_lifecycle_index_sha256']!=rev_index['lifecycle_index_sha256']:
        raise lifecycle.Refuse('CONTROLLER_SNAPSHOT_MISMATCH')
    if [x['shot_id'] for x in board['shot_review_queue']] != [x['shot_id'] for x in plan['shots']]:
        raise lifecycle.Refuse('CONTROLLER_SHOT_ORDER_MISMATCH')
    review_by_shot={}
    for record in reviewed['review_records']:
        sid=record['shot_id']
        if sid in review_by_shot:raise lifecycle.Refuse('MULTIPLE_REVIEWS_FOR_ONE_SHOT_UNSUPPORTED')
        review_by_shot[sid]=record
    shots=[];actions=[]
    for seq,card in enumerate(board['shot_review_queue']):
        sid=card['shot_id']; rec=review_by_shot.get(sid)
        association=card['association_status']; candidate=None
        if rec is None:
            if association=='NO_ASSOCIATED_OBSERVATION':
                state='PLANNED_NO_FOOTAGE';action='CAPTURE_OR_DECLARE_ASSOCIATION'
            elif association=='REVIEW_PENDING_UNVERIFIED_ASSOCIATION':
                state='FOOTAGE_LINK_UNVERIFIED';action='VERIFY_MEMBERSHIP_AND_REVIEW_FOOTAGE'
            else:raise lifecycle.Refuse('UNKNOWN_ASSOCIATION_STATUS')
        else:
            if rec['observation_id']!=card['observation_id']:
                raise lifecycle.Refuse('REVIEW_OBSERVATION_NOT_ON_SHOT')
            decision=rec['review_decision']
            if decision not in REVIEW_STATES:raise lifecycle.Refuse('UNKNOWN_REVIEW_DECISION')
            if rec['review_fixture']:
                state='TEST_FIXTURE_NOT_ACCEPTANCE';action='DISREGARD_SIMULATION_AND_REQUEST_REAL_REVIEW'
            elif decision=='REJECT_WORKING':
                state='REVIEWER_REJECTED_WORKING';action='CONSIDER_REWORK_FROM_AUTHENTIC_ORIGINALS'
            elif decision=='NEEDS_MORE_REVIEW':
                state='REVIEW_UNRESOLVED';action='RESOLVE_REVIEW_UNCERTAINTY'
            elif decision=='ACCEPT_WORKING':
                if rec['working_selection_status']!='HUMAN_DECLARED_WORKING_NOT_FINAL_UNAUTHENTICATED':
                    raise lifecycle.Refuse('WORKING_STATUS_CONTRADICTION')
                state='HUMAN_CLAIM_WORKING_NOT_AUTHENTICATED';action='PROPOSE_NEXT_BEAT_TEXT_ONLY'
                candidate=rec['next_shot_semantic_candidate']
                # Final shot may have no next shot; never invent a successor.
                if rec['target_next_shot'] is None:
                    candidate=None
                    action='REVIEW_FINAL_BEAT_WITHOUT_NEXT_SHOT'
                elif not isinstance(candidate,dict) or candidate.get('scope')!='TEXT_ONLY_CANDIDATE_UNTRUSTED_REVIEWER_ASSERTION':
                    raise lifecycle.Refuse('INVALID_CONTINUITY_CANDIDATE')
        if action not in ALLOWED_TRANSITIONS:raise lifecycle.Refuse('INVALID_ACTION')
        # Creative intent is copied verbatim from the verified plan, never inferred from image labels.
        plan_shot=plan['shots'][seq]
        if plan_shot['shot_id']!=sid:raise lifecycle.Refuse('SHOT_ORDER_CORRUPTED')
        entry={
            'shot_id':sid,'order':seq+1,'beat':plan_shot['narrative_beat'],
            'state':state,'next_review_action':action,
            'action_kind':'ADMINISTRATIVE_OR_CREATIVE_PROPOSAL_ONLY',
            'associated_observation_id':card['observation_id'],
            'source_association_verified_as_shot_membership':False,
            'review_record_id':rec['review_id'] if rec else None,
            'reviewer_identity_authenticated':False,
            'working_cut_finalized':False,
            'performance_truth_certified':False,
            'declared_cut_candidates':card['cut_candidate_frames'],
            'cut_boundaries_are_not_verified_editorial_intent':True,
            'next_shot_id':rec['target_next_shot'] if rec else None,
            'text_only_next_shot_candidate':candidate,
            'unverified_candidate_direction':plan_shot['candidate_h3_direction_not_executed'],
        }
        shots.append(entry)
        actions.append({'priority':seq+1,'shot_id':sid,'action':action,'status':state,
                        'execution_allowed':False})
    # Historical F66 selection is kept, but never used to rank, rewrite or accept new cuts.
    status_counts={s:sum(x['state']==s for x in shots) for s in sorted({x['state'] for x in shots})}
    return {
        'schema':SCHEMA,'version':'0.7.29','mode':'SHADOW_READ_ONLY',
        'operational_baseline':'V0.7.5_UNMODIFIED',
        'source_review_index_sha256':lifecycle.hash_file(index_path),
        'source_lifecycle_index_sha256':rev_index['lifecycle_index_sha256'],
        'source_plan_sha256':lifecycle_index['plan_sha256'],
        'scene_id':plan['scene_id'],
        'scene_state':'UNVERIFIED_CANDIDATE_ONLY',
        'working_shot_claims_unauthenticated':reviewed['reviewed_working_claims_not_authenticated'],
        'status_counts':status_counts,'shots':shots,'review_queue':actions,
        'first_administrative_action':actions[0] if actions else None,
        'unassociated_observations':board['unbound_observations'],
        'historical_working_cut':board['historical_working_cut'],
        'original_reference_declarations_runtime_verification_still_required':plan['authentic_source_declarations'],
        'generated_footage_is_observation_only_not_h3_conditioning':True,
        'reviewer_authentication_implemented':False,
        'semantic_handoff_promoted_to_scene_memory':False,
        'any_automated_edit_approved':False,
        'authorizations':dict(NO_AUTH),
        'provenance_boundary':'Source checksums verify consistency of archived bytes, not authentic origin or scene membership.',
        'interpretation_boundary':'Reviewer claims and calculated motion are not independently verified cinematic truth.',
        'safety_boundary':'No media routed to H3. No ComfyUI or GPU operations. No edits, acceptance, continuity or memory writes.',
    }

def render(report):
    e=lambda v:html.escape(str(v),quote=True)
    tiles=[]
    for shot in report['shots']:
        candidate=shot['text_only_next_shot_candidate']
        if candidate:
            facts=''.join(f'<li><b>{e(k)}</b>: {e(v["reviewed_text"] if v["reviewed_text"] is not None else "UNKNOWN")}</li>' for k,v in candidate['suggested_facts'].items())
            c=f'<details><summary>Unverified textual continuity candidate</summary><ul>{facts}</ul></details>'
        else:c='<p class="muted">No text continuity candidate authorized.</p>'
        tiles.append(f'<article><span class="small">SHOT {shot["order"]} · {e(shot["beat"])}</span><h2>{e(shot["shot_id"])}</h2><p class="state">{e(shot["state"].replace("_"," "))}</p><p>Next: {e(shot["next_review_action"].replace("_"," ").lower())}</p><details><summary>Proposed creative direction—not executed</summary><p>{e(shot["unverified_candidate_direction"])}</p></details>{c}</article>')
    first=report['first_administrative_action']
    intro=(f'{e(first["shot_id"])}: {e(first["action"].replace("_"," ").lower())}' if first else 'No pending administrative action')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Director V0.7.29</title><style>body{font:15px system-ui;background:#101721;color:#eaf0f8;max-width:1100px;margin:35px auto;padding:0 20px;line-height:1.55}h1{font-size:30px}h2{font-size:18px;margin:5px 0}article{background:#1b293a;border:1px solid #394e68;border-radius:14px;padding:20px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(315px,1fr));gap:14px}.small,.muted{color:#aabdd3;font-size:12px}.state{font-weight:700;color:#f3d394}aside{padding:14px 18px;border-left:4px solid #f3d394;background:#293341;margin:25px 0}details{margin-top:12px}summary{cursor:pointer}li{padding:3px 0}</style>'+f'<h1>Director · Unified Shot Controller</h1><p>Scene {e(report["scene_id"])} · {len(report["shots"])} proposed shots · Shadow-only</p><aside><b>Next operator task:</b> {intro}<p>All proposed actions are non-executing. Human declarations are unauthenticated; no cut, scene state or generation is approved.</p></aside><main>'+''.join(tiles)+'</main><p class="muted">V0.7.5 operational baseline unchanged · No generated imagery used for H3 conditioning · Original asset hashes must be verified at runtime</p></html>'

def create(index_path:Path,out_dir:Path):
    index_path=Path(index_path).resolve(strict=True)
    base=index_path.parent
    out=Path(out_dir)
    # Refuse overwrite and writes within the immutable evidence tree.
    if out.exists() or out.is_symlink():raise FileExistsError('EXISTING_RESULTS_REFUSED')
    if out.resolve().is_relative_to(base):raise lifecycle.Refuse('OUTPUT_INSIDE_IMMUTABLE_REFERENCE')
    report=build(index_path)
    out.mkdir(parents=True,exist_ok=False)
    j=out/'director_controller_v0729.json';h=out/'director_board_v0729.html';q=out/'review_queue_v0729.json'
    j.write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    h.write_text(render(report),encoding='utf-8')
    q.write_text(json.dumps({'schema':'director-review-queue-v0729','read_only':True,
         'actions':report['review_queue'],'authorization_for_execution':False},indent=2,sort_keys=True)+'\n',encoding='utf-8')
    (out/'SHA256_OUTPUTS.json').write_text(json.dumps({f.name:lifecycle.hash_file(f) for f in (j,h,q)},indent=2)+'\n',encoding='utf-8')
    return report

def cli():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-index',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    result=create(a.review_index,a.out)
    print('V0729_UNIFIED_SHADOW',len(result['shots']),'shots',len(result['review_queue']),'non-executable actions')
if __name__=='__main__':cli()
