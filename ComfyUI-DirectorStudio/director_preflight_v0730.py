#!/usr/bin/env python3
"""V0.7.30 source-replayed, non-executing Director preflight and work prioritization.

Consumes the *validated* V0.7.29 controller input; never trusts a pre-rendered
report, an unauthenticated review assertion, or the presence of generated video
as permission to edit, condition H3, generate, or promote continuity.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
from pathlib import Path
import director_controller_v0729 as controller
import director_lifecycle_v0727 as lifecycle

SCHEMA = 'director-preflight-replay-v0730'
OUTPUT_VERSION='0.7.30'
# Review the data that already exists before proposing additional capture.
# These are INSPECTION priorities, never generation order or a film timeline.
PRIORITY = {
    'FOOTAGE_LINK_UNVERIFIED': 0,
    'REVIEW_UNRESOLVED': 1,
    'HUMAN_CLAIM_WORKING_NOT_AUTHENTICATED': 2,
    'TEST_FIXTURE_NOT_ACCEPTANCE': 3,
    'REVIEWER_REJECTED_WORKING': 4,
    'PLANNED_NO_FOOTAGE': 5,
}
REVIEWABLE={'FOOTAGE_LINK_UNVERIFIED','REVIEW_UNRESOLVED',
            'HUMAN_CLAIM_WORKING_NOT_AUTHENTICATED',
            'TEST_FIXTURE_NOT_ACCEPTANCE','REVIEWER_REJECTED_WORKING'}


def digest_json(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),
                                     allow_nan=False,ensure_ascii=False).encode('utf-8')).hexdigest()


def assert_safe(report):
    if report.get('schema')!=controller.SCHEMA or report.get('mode')!='SHADOW_READ_ONLY':
        raise lifecycle.Refuse('UNRECOGNIZED_CONTROLLER')
    if report.get('operational_baseline')!='V0.7.5_UNMODIFIED':
        raise lifecycle.Refuse('WRONG_OPERATIONAL_BASELINE')
    if any(v is not False for v in report['authorizations'].values()):
        raise lifecycle.Refuse('PRODUCTION_AUTHORITY_IN_CONTROLLER')
    if report.get('generated_footage_is_observation_only_not_h3_conditioning') is not True:
        raise lifecycle.Refuse('CLEAN_CHAIN_BROKEN')
    if report.get('semantic_handoff_promoted_to_scene_memory') is not False or report.get('any_automated_edit_approved') is not False:
        raise lifecycle.Refuse('UNSAFE_PROMOTION')
    if not isinstance(report.get('shots'),list) or not report['shots']:
        raise lifecycle.Refuse('MISSING_SHOTS')
    ids=set()
    for item in report['shots']:
        if item.get('state') not in PRIORITY or item.get('action_kind')!='ADMINISTRATIVE_OR_CREATIVE_PROPOSAL_ONLY':
            raise lifecycle.Refuse('UNSUPPORTED_SHOT_STATE')
        if item.get('shot_id') in ids or item.get('working_cut_finalized') is not False:
            raise lifecycle.Refuse('DUPLICATE_SHOT_OR_FINAL_CUT')
        ids.add(item['shot_id'])
        if item.get('performance_truth_certified') is not False or item.get('reviewer_identity_authenticated') is not False:
            raise lifecycle.Refuse('UNAUTHORIZED_PERFORMANCE_TRUTH')
        if item.get('source_association_verified_as_shot_membership') is not False:
            raise lifecycle.Refuse('SHOT_MEMBERSHIP_NOT_VERIFIED')
        if item.get('text_only_next_shot_candidate') is not None and item.get('state')!='HUMAN_CLAIM_WORKING_NOT_AUTHENTICATED':
            raise lifecycle.Refuse('CANDIDATE_ON_UNVERIFIED_SHOT')


def blocker_set(shot):
    s=shot['state']
    common=['AUTHENTIC_ORIGINAL_BYTES_NOT_RUNTIME_VERIFIED',
            'NO_GPU_ADMISSION_LOCK_OR_EXECUTION_AUTHORITY',
            'NO_AUTONOMOUS_CUT_OR_MEMORY_AUTHORITY']
    if s=='PLANNED_NO_FOOTAGE':
        return ['NO_FOOTAGE_ASSOCIATED','NO_PERFORMANCE_REVIEW']+common
    if s=='FOOTAGE_LINK_UNVERIFIED':
        return ['LINKED_SOURCE_IS_OBSERVATION_ONLY','SHOT_MEMBERSHIP_UNVERIFIED','NO_PERFORMANCE_REVIEW']+common
    if s=='TEST_FIXTURE_NOT_ACCEPTANCE':
        return ['SIMULATED_REVIEW_NOT_AUTHENTIC_APPROVAL','SHOT_MEMBERSHIP_UNVERIFIED']+common
    if s=='HUMAN_CLAIM_WORKING_NOT_AUTHENTICATED':
        return ['REVIEWER_IDENTITY_NOT_AUTHENTICATED','OBSERVED_FACTS_NOT_INDEPENDENTLY_CERTIFIED',
                'TEXT_CONTINUITY_UNPROMOTED']+common
    if s=='REVIEWER_REJECTED_WORKING':
        return ['REVIEWER_DECLARED_REJECTION_NOT_OBJECTIVE_GROUND_TRUTH']+common
    if s=='REVIEW_UNRESOLVED':
        return ['PERFORMANCE_REVIEW_UNRESOLVED']+common
    raise lifecycle.Refuse('UNKNOWN_STATE')


def validate_snapshot_pair(previous, current):
    # A differential replay is meaningful only for the same evidence-backed
    # scene/plan/source registry, even if the review index itself changed.
    for k in ('scene_id','source_plan_sha256','source_lifecycle_index_sha256'):
        if previous[k]!=current[k]:raise lifecycle.Refuse('INCOMPATIBLE_SNAPSHOTS_'+k)
    if [s['shot_id'] for s in previous['shots']]!=[s['shot_id'] for s in current['shots']]:
        raise lifecycle.Refuse('SHOT_ORDER_CHANGED')
    if previous['historical_working_cut']!=current['historical_working_cut']:
        raise lifecycle.Refuse('HISTORICAL_WORKING_EDIT_CHANGED')
    # No review assertion is permitted to alter the original-reference registry.
    if previous['original_reference_declarations_runtime_verification_still_required']!=current['original_reference_declarations_runtime_verification_still_required']:
        raise lifecycle.Refuse('ORIGINAL_REFERENCE_REGISTRY_CHANGED')


def build(review_index:Path, previous_review_index:Path|None=None):
    # Rebuild from exact source video and report bytes, not from a mutable board.
    cur=controller.build(review_index)
    assert_safe(cur)
    cards=[]
    for shot in cur['shots']:
        state=shot['state']
        candidate=shot['text_only_next_shot_candidate']
        card={
            'shot_id':shot['shot_id'],
            'narrative_order':shot['order'],
            'narrative_beat':shot['beat'],
            'status':state,
            'next_step':shot['next_review_action'],
            'priority_class':'INSPECT_EXISTING_EVIDENCE' if state in REVIEWABLE else 'PLAN_MISSING_FOOTAGE',
            'linked_observation_id':shot['associated_observation_id'],
            'candidate_cut_barriers':shot['declared_cut_candidates'],
            'cut_barriers_not_editorial_intent':True,
            'blockers':blocker_set(shot),
            'continuity_text_candidate_present_but_untrusted':candidate is not None,
            'source_membership_verified':False,
            'reviewer_identity_authenticated':False,
            'automatic_action_allowed':False,
            'generated_footage_as_h3_visual_input':False,
        }
        cards.append(card)
    cards.sort(key=lambda x:(PRIORITY[x['status']],x['narrative_order']))
    for i,card in enumerate(cards,1):card['inspection_priority']=i
    audit=[]
    if previous_review_index is not None:
        prev=controller.build(previous_review_index)
        assert_safe(prev)
        validate_snapshot_pair(prev,cur)
        p={s['shot_id']:s for s in prev['shots']}
        for shot in cur['shots']:
            before=p[shot['shot_id']]
            if before['state']!=shot['state'] or before['review_record_id']!=shot['review_record_id']:
                audit.append({
                    'shot_id':shot['shot_id'],
                    'old_state':before['state'],
                    'new_state':shot['state'],
                    'old_review_id':before['review_record_id'],
                    'new_review_id':shot['review_record_id'],
                    'interpretation':'SOURCE_VERIFIED_ADMINISTRATIVE_DIFF_NOT_APPROVAL',
                    'authority_gained':False,
                })
    return {
        'schema':SCHEMA,'version':OUTPUT_VERSION,'mode':'SHADOW_READ_ONLY',
        'operational_baseline':'V0.7.5_UNMODIFIED',
        'scene_id':cur['scene_id'],'shot_count':len(cur['shots']),
        'source_review_index_sha256':cur['source_review_index_sha256'],
        'validated_controller_digest':digest_json(cur),
        'previous_review_index_sha256':lifecycle.hash_file(previous_review_index) if previous_review_index else None,
        'validation':'REPLAYED_V0729_AND_ALL_SOURCE_BYTES',
        'work_queue_order':'EVIDENCE_INSPECTION_PRIORITY_NOT_FILM_OR_GENERATION_ORDER',
        'first_review_task':cards[0] if cards else None,
        'work_queue':cards,
        'ordered_narrative_shots':[s['shot_id'] for s in cur['shots']],
        'provenance_only_unbound_observations':cur['unassociated_observations'],
        'state_changes':audit,
        'historical_working_cut':cur['historical_working_cut'],
        'gates':{
            'original_source_authenticity_runtime_confirmed':False,
            'live_h3_capture_test_completed':False,
            'shot_membership_verified':False,
            'reviewer_authentication_implemented':False,
            'performance_truth_independently_verified':False,
            'automatic_edit_authorized':False,
            'continuity_promoted':False,
            'h3_visual_routing_of_generated_footage':False,
            'atomic_gpu_admission_lock_implemented':False,
            'production_integration_authorized':False,
        },
        'visual_input_policy':'ONLY_SHA_VERIFIED_AUTHENTIC_ORIGINALS_MAY_CONDITION_H3_AT_RUNTIME',
        'not_executable':True,'no_review_means_no_acceptance':True,
        'creative_continuation_allowed_as_unexecuted_proposal_only':True,
        'provenance_note':'Hash consistency is not evidence of authentic origin, actor correctness, or scene membership.',
        'scope_note':'This is a preflight and proposed review queue, not a simulated successful production run.',
    }


def render(report):
    e=lambda v:html.escape(str(v),quote=True)
    pieces=[]
    for card in report['work_queue']:
        blockers=''.join(f'<li>{e(b.replace("_"," ").capitalize())}</li>' for b in card['blockers'])
        pieces.append('<article>'
          f'<div class="top">REVIEW #{card["inspection_priority"]} · FILM SHOT {card["narrative_order"]}</div>'
          f'<h2>{e(card["narrative_beat"])} — {e(card["shot_id"])}</h2>'
          f'<p class="status">{e(card["status"].replace("_"," "))}</p>'
          f'<p>Suggested next step: {e(card["next_step"].replace("_"," ").lower())}</p>'
          f'<details><summary>Why this cannot advance automatically</summary><ul>{blockers}</ul></details>'
          '</article>')
    changes=''.join(f'<li>{e(x["shot_id"])}: {e(x["old_state"])} → {e(x["new_state"])}</li>' for x in report['state_changes'])
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
      '<title>Director V0.7.30 · Preflight</title><style>'
      'body{font:15px system-ui;background:#101820;color:#f0f5ff;margin:35px auto;max-width:1060px;padding:0 20px;line-height:1.55}'
      '.banner{background:#263345;border-left:4px solid #e6bf7b;padding:15px;margin:22px 0;border-radius:6px}'
      '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px}'
      'article{padding:20px;background:#1b2b3a;border:1px solid #3a5366;border-radius:14px}'
      'h1{font-size:28px}h2{font-size:17px}.top{font-size:12px;color:#a2bad4}.status{color:#efcb85;font-weight:700}'
      'details{margin-top:14px}summary{cursor:pointer}li{margin:5px 0}</style>'
      f'<h1>Director · Evidence-First Preflight</h1><p>Scene {e(report["scene_id"])} · {report["shot_count"]} proposed shots</p>'
      '<div class="banner"><b>Read-only rehearsal:</b> review work is prioritized by available evidence, separately from film order. '
      'No H3 generation, editing, continuity promotion, or production approval is enabled.</div>'
      '<div class="grid">'+''.join(pieces)+'</div>'
      '<h2>Source-verified administrative changes</h2>'+(('<ul>'+changes+'</ul>') if changes else '<p>No comparison changes supplied.</p>')+
      '<p>V0.7.5 operational baseline unchanged · Generated media is observation-only · Authentic-original reference bytes must be verified again before any future generation.</p></html>')


def create(index_path:Path,out_dir:Path,previous_review_index:Path|None=None):
    out=Path(out_dir)
    if out.exists() or out.is_symlink():raise FileExistsError('EXISTING_RESULTS_REFUSED')
    # Bind output refusal to *both* immutable source folders to protect evidence.
    dirs=[Path(index_path).resolve(strict=True).parent]
    if previous_review_index is not None:dirs.append(Path(previous_review_index).resolve(strict=True).parent)
    if any(out.resolve().is_relative_to(d) for d in dirs):raise lifecycle.Refuse('OUTPUT_INSIDE_IMMUTABLE_SOURCE')
    report=build(index_path,previous_review_index)
    out.mkdir(parents=True,exist_ok=False)
    paths={
        'director_preflight_v0730.json':json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',
        'evidence_first_board_v0730.html':render(report),
        'review_queue_v0730.json':json.dumps({'schema':'director-review-queue-v0730',
                  'read_only':True,'actions':report['work_queue'],
                  'execution_authorized':False,'ordering':'INSPECTION_NOT_PRODUCTION'},indent=2,sort_keys=True)+'\n',
    }
    for name,data in paths.items():(out/name).write_text(data,encoding='utf-8')
    (out/'SHA256_OUTPUTS.json').write_text(json.dumps({name:lifecycle.hash_file(out/name) for name in sorted(paths)},indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report


def cli():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--review-index',type=Path,required=True)
    ap.add_argument('--previous-review-index',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    ns=ap.parse_args()
    result=create(ns.review_index,ns.out,ns.previous_review_index)
    print('V0730_PREFLIGHT_READY',result['shot_count'],'shots',len(result['state_changes']),'verified administrative changes; execution FALSE')

if __name__=='__main__':cli()
