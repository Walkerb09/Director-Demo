#!/usr/bin/env python3
"""V0.7.28: offline human-claim-gated working review and TEXT-ONLY next-beat candidate.

This component never verifies reviewer identity. A human-declared review is an
unauthenticated attestation, not independently verified film truth. Never writes
memory, changes cuts, feeds generated media to H3, or triggers H3 inference.
"""
from __future__ import annotations
import argparse, hashlib, html, json, re
from pathlib import Path
import director_lifecycle_v0727 as old

SCHEMA='director-working-review-v0728'
REVIEW_INDEX='director-review-index-v0728'
OUTPUT='director-review-bridge-v0728'
DECISIONS={'ACCEPT_WORKING','REJECT_WORKING','NEEDS_MORE_REVIEW'}
CLAIMS={'TEST_FIXTURE','HUMAN_DECLARED_UNAUTHENTICATED'}
STATES={'OBSERVED_BY_REVIEWER','UNKNOWN'}
FACT_KEYS=('last_visible_action','ending_positions','ending_facing','expression_state','prop_state','lighting_state')
SHA=old.SHA
IDENT=old.IDENT
Refuse=old.Refuse

def _strict(d, keys, reason):
    if not isinstance(d,dict) or set(d)!=set(keys):raise Refuse(reason)

def _text(s, limit=240, allow_unknown=False):
    if not isinstance(s,str) or not s or len(s)>limit or any(ord(c)<32 or ord(c)==127 for c in s):raise Refuse('INVALID_REVIEW_TEXT')
    if not allow_unknown and s.upper()=='UNKNOWN':raise Refuse('UNSUPPORTED_FACT_DESCRIPTION')
    return s

def _fingerprint(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()

def _frame_range(r,n,cuts):
    if not isinstance(r,list) or len(r)!=2 or any(type(x) is not int for x in r):raise Refuse('INVALID_FRAME_RANGE')
    start,end=r
    if start<0 or end>=n or start>=end:raise Refuse('INVALID_FRAME_RANGE')
    if any(start < cut <= end for cut in cuts):raise Refuse('CROSS_CUT_REVIEW_FORBIDDEN')
    return start,end

def _facts(data):
    _strict(data,FACT_KEYS,'INVALID_REVIEW_FACT_FIELDS')
    verified={}
    for key in FACT_KEYS:
        fact=data[key]
        _strict(fact,('status','text'),'INVALID_FACT')
        if fact['status'] not in STATES:raise Refuse('INVALID_FACT_STATUS')
        if fact['status']=='UNKNOWN':
            if fact['text'] is not None:raise Refuse('UNKNOWN_MUST_BE_NULL')
            verified[key]=None
        else:
            verified[key]=_text(fact['text'])
    return verified

def validate_review(review, *, plan_hash, index_hash, link, obs, source_type):
    expected=('schema','review_id','shot_id','observation_id','plan_sha256','lifecycle_index_sha256',
              'source_sha256','report_sha256','reviewer_claim','reviewer_label','review_method','decision',
              'shot_membership_assertion','selected_frame_range','facts','scope','authority')
    _strict(review,expected,'INVALID_REVIEW_SCHEMA_FIELDS')
    if review['schema']!=SCHEMA:raise Refuse('WRONG_REVIEW_SCHEMA')
    if not isinstance(review['review_id'],str) or not IDENT.fullmatch(review['review_id']):raise Refuse('BAD_REVIEW_ID')
    if review['shot_id']!=link[0] or review['observation_id']!=link[1]:raise Refuse('REVIEW_LINK_MISMATCH')
    if review['plan_sha256']!=plan_hash or review['lifecycle_index_sha256']!=index_hash:raise Refuse('REVIEW_PLAN_OR_INDEX_MISMATCH')
    if review['source_sha256']!=obs['source_sha256'] or review['report_sha256']!=obs['report_sha256']:raise Refuse('REVIEW_SOURCE_REPORT_MISMATCH')
    if review['reviewer_claim'] not in CLAIMS:raise Refuse('BAD_REVIEW_ORIGIN')
    _text(review['reviewer_label'],80)
    if review['review_method']!='DIRECT_PLAYBACK_ASSERTED_NOT_INDEPENDENTLY_VERIFIED':raise Refuse('NOT_DIRECT_REVIEW')
    if review['decision'] not in DECISIONS:raise Refuse('INVALID_DECISION')
    if review['shot_membership_assertion'] not in ('ASSERTED_BY_REVIEWER','NOT_ESTABLISHED'):
        raise Refuse('INVALID_MEMBERSHIP')
    if review['scope']!='HUMAN_WORKING_ONLY_NOT_FINAL':raise Refuse('WRONG_SCOPE')
    if review['authority']!={k:False for k in old.AUTH_FLAGS}:raise Refuse('REVIEW_CANNOT_GRANT_AUTHORITY')
    if source_type!='UNVERIFIED_EXTERNAL_OBSERVATION_ONLY':raise Refuse('UNSUPPORTED_MEDIA_CLASS')
    span=_frame_range(review['selected_frame_range'],obs['source_frames'],obs['declared_cut_candidates'])
    facts=_facts(review['facts'])
    if review['decision']=='ACCEPT_WORKING' and review['shot_membership_assertion']!='ASSERTED_BY_REVIEWER':raise Refuse('WORKING_REQUIRES_MEMBERSHIP_ASSERTION')
    if review['decision']!='ACCEPT_WORKING' and any(v is not None for v in facts.values()):raise Refuse('FACTS_ON_NONACCEPTED_REVIEW')
    return {'span':list(span),'facts':facts,'mode':review['reviewer_claim'],'decision':review['decision'],
            'reviewer_claim_unverified':True,'fixture':review['reviewer_claim']=='TEST_FIXTURE'}

def reconcile(review_index,base):
    _strict(review_index,('schema','lifecycle_index_path','lifecycle_index_sha256','reviews'),'INVALID_REVIEW_INDEX')
    if review_index['schema']!=REVIEW_INDEX:raise Refuse('INVALID_REVIEW_INDEX_SCHEMA')
    old.ensure_hash(review_index['lifecycle_index_sha256'])
    index_path=old.safe_file(base,review_index['lifecycle_index_path'])
    if old.hash_file(index_path)!=review_index['lifecycle_index_sha256']:raise Refuse('LIFECYCLE_INDEX_BYTES_CHANGED')
    index=old.read_json(index_path)
    plan_path=old.safe_file(base,index['plan_path']);plan=old.read_json(plan_path)
    lifecycle=old.reconcile(plan,index,base)
    observations={}
    for o in index['observations']:
        report=old.read_json(old.safe_file(base,o['report_path']))
        details=old.verify_bridge(report,old.safe_file(base,o['source_path']))
        details['report_sha256']=o['report_sha256']
        observations[o['id']]=details
    bindings={(b['shot_id'],b['observation_id']) for b in index['bindings']}
    if not isinstance(review_index['reviews'],list) or len(review_index['reviews'])>32:raise Refuse('INVALID_REVIEW_LIST')
    proposals=[]; seen_ids=set();seen_links=set()
    shot_order=[s['shot_id'] for s in plan['shots']]
    for entry in review_index['reviews']:
        _strict(entry,('review_path','review_sha256'),'INVALID_REVIEW_ENTRY')
        old.ensure_hash(entry['review_sha256'])
        review_path=old.safe_file(base,entry['review_path'])
        if old.hash_file(review_path)!=entry['review_sha256']:raise Refuse('REVIEW_HASH_MISMATCH')
        review=old.read_json(review_path)
        link=(review.get('shot_id'),review.get('observation_id'))
        if link not in bindings:raise Refuse('UNBOUND_REVIEW_CANNOT_ACCEPT')
        if review.get('review_id') in seen_ids or link in seen_links:raise Refuse('DUPLICATE_REVIEW_OR_ASSOCIATION')
        seen_ids.add(review['review_id']);seen_links.add(link)
        details=validate_review(review,plan_hash=index['plan_sha256'],index_hash=review_index['lifecycle_index_sha256'],
                                link=link,obs=observations[link[1]],source_type=observations[link[1]]['source_class'])
        is_working=details['decision']=='ACCEPT_WORKING' and not details['fixture']
        ix=shot_order.index(link[0]);next_id=shot_order[ix+1] if ix<len(shot_order)-1 else None
        candidates={k:({'reviewed_text':v,'evidence':'HUMAN_ASSERTED_NOT_INDEPENDENTLY_VERIFIED'} if v is not None else {'reviewed_text':None,'evidence':'UNKNOWN'}) for k,v in details['facts'].items()}
        proposals.append({'review_id':review['review_id'],'shot_id':link[0],'observation_id':link[1],
            'source_sha256':observations[link[1]]['source_sha256'],'selected_frame_range':details['span'],
            'reviewer_claim':details['mode'],'reviewer_identity_cryptographically_verified':False,
            'review_fixture':details['fixture'],'review_decision':details['decision'],
            'working_selection_status':('HUMAN_DECLARED_WORKING_NOT_FINAL_UNAUTHENTICATED' if is_working else
               'SIMULATED_TEST_ONLY_NOT_AN_ACTUAL_APPROVAL' if details['fixture'] else 'NOT_ACCEPTED'),
            'target_next_shot':next_id,
            'next_shot_semantic_candidate':({'scope':'TEXT_ONLY_CANDIDATE_UNTRUSTED_REVIEWER_ASSERTION',
                'source_video_not_visual_conditioning':True,'suggested_facts':candidates,
                'status':'REQUIRES_SEPARATE_VALIDATION_AND_AUTHORIZATION'} if is_working and next_id else None),
            'automatic_scene_dna_or_handoff_authorized':False,'generation_authorized':False,
            'accepted_final_film_shot':False,'any_automatic_edit_authorized':False,
            'review_record_sha256':entry['review_sha256']})
    return {'schema':OUTPUT,'version':'0.7.28','mode':'SHADOW_ONLY','operational_baseline':'V0.7.5_UNMODIFIED',
        'source_lifecycle_index_sha256':review_index['lifecycle_index_sha256'],
        'shot_plan_sha256':index['plan_sha256'],'planned_shots':lifecycle['planned_shots'],
        'declared_links':lifecycle['bound_observations'],'review_records':proposals,
        'review_records_count':len(proposals),'reviewed_working_claims_not_authenticated':sum(p['working_selection_status'].startswith('HUMAN_DECLARED') for p in proposals),
        'historical_working_cut':lifecycle['historical_working_cut'],
        'unreviewed_shot_ids':[s['shot_id'] for s in lifecycle['shot_review_queue'] if not any(p['shot_id']==s['shot_id'] for p in proposals)],
        'unknown_is_not_false':True,'no_assumed_action_or_prop_ownership':True,
        'reviewer_identity_unverified':True,
        'no_generated_visual_inputs_to_h3':True,
        'original_reference_declarations_runtime_verification_still_required':plan['authentic_source_declarations'],
        'automation':{**{key:False for key in old.AUTH_FLAGS},'memory_update':False,'postshot_worker_modified':False,'final_film_approved':False},
        'boundary':'Human-declared working review is a claim, not authentication of reviewer or objective performance truth. All continuity is unpromoted text. No H3/video/edit/memory operations.'}

def render(report):
    esc=lambda x:html.escape(str(x),quote=True)
    rows=[]
    for r in report['review_records']:
        notes=r['next_shot_semantic_candidate']
        details=''.join(f'<li><strong>{esc(k.replace("_"," "))}</strong>: {esc(v["reviewed_text"] if v["reviewed_text"] is not None else "UNKNOWN")}</li>' for k,v in notes['suggested_facts'].items()) if notes else '<li>No candidate continuity facts available</li>'
        rows.append(f'<article><h2>{esc(r["shot_id"])}</h2><p>{esc(r["working_selection_status"])}</p><p>Next planned beat: {esc(r["target_next_shot"] or "None")}</p><ul>{details}</ul></article>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Director V0.7.28 — Working Review</title><style>body{background:#111a25;color:#eef3fa;font:16px system-ui;max-width:950px;line-height:1.55;margin:36px auto;padding:0 20px}article{background:#1b2a3b;border:1px solid #40516c;border-radius:12px;padding:20px;margin:12px 0}h1{font-size:29px}h2{font-size:18px}aside{border-left:4px solid #e4b96a;padding:10px 16px;background:#28313d}</style>'
            f'<h1>Director V0.7.28 — Shot Review</h1><aside>SHADOW ONLY. {esc(report["boundary"])} Test fixtures never become real human approvals. Original references require runtime authentication.</aside>'
            f'<p>{report["planned_shots"]} planned shots · {report["declared_links"]} administrative associations · {report["review_records_count"]} review records</p>'+''.join(rows)+'</html>')

def create(index_path,out_dir):
    index_path=Path(index_path).resolve(strict=True)
    base=index_path.parent
    report=reconcile(old.read_json(index_path),base)
    out=Path(out_dir)
    if out.exists() or out.is_symlink():raise FileExistsError('EXISTING_RESULTS_REFUSED')
    if out.resolve().is_relative_to(base.resolve()):raise Refuse('OUTPUT_INSIDE_IMMUTABLE_REFERENCE')
    out.mkdir(parents=True,exist_ok=False)
    j=out/'working_review_v0728.json';p=out/'working_review_v0728.html'
    j.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    p.write_text(render(report),encoding='utf-8')
    (out/'SHA256_OUTPUTS.json').write_text(json.dumps({j.name:old.hash_file(j),p.name:old.hash_file(p)},indent=2)+'\n')
    return report

def cli():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-index',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    r=create(a.review_index,a.out)
    print('V0728_REVIEW_SHADOW',r['review_records_count'],'candidate_reviews',r['reviewed_working_claims_not_authenticated'],'unauthenticated_working')
if __name__=='__main__':cli()
