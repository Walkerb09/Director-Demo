#!/usr/bin/env python3
"""Director v0.7.20: conservative, hash-bound editorial technical / annotation triage.

NEVER chooses or performs cuts, promotes unverified motion labels, rewrites scene memory,
or supplies H3 visual references. Must consume a verified V0.7.19 scout output.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import cv2
import numpy as np
import editorial_candidates_v0719 as scout

SCHEMA='director-editorial-decision-evidence-shadow-v0720'
EVENT_SCHEMA='director-temporal-review-annotations-v0720'
ALLOWED_EVENTS={'GESTURE_IN_PROGRESS','SPEECH_IN_PROGRESS','EXPRESSION_TRANSITION_IN_PROGRESS','OTHER_ACTION_IN_PROGRESS'}
DENIED=scout.DENIED

class DecisionError(ValueError): pass

def load_json(p):
    try:return json.loads(Path(p).read_text())
    except (OSError,UnicodeError,json.JSONDecodeError) as e:raise DecisionError('INVALID_JSON_OR_MISSING_FILE') from e

def verify_scout(root):
    root=Path(root)
    if not root.is_dir() or root.is_symlink():raise DecisionError('SCOUT_OUTPUT_NOT_DIRECTORY')
    manifest=load_json(root/'private/SHA256_OUTPUTS.json')
    records=manifest.get('files')
    if not isinstance(records,dict) or len(records)<3:raise DecisionError('SCOUT_OUTPUT_MANIFEST_INVALID')
    for rel,digest in records.items():
        p=Path(rel)
        if p.is_absolute() or any(v in ('..','.') for v in p.parts) or not p.parts:
            raise DecisionError('SCOUT_MANIFEST_UNSAFE_PATH')
        f=root/p
        if not f.is_file() or f.is_symlink():raise DecisionError('SCOUT_FILE_MISSING_OR_SYMLINK')
        if scout.sha(f)!=digest:raise DecisionError('SCOUT_OUTPUT_HASH_MISMATCH')
    reported={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name!='SHA256_OUTPUTS.json'}
    if reported!=set(records):raise DecisionError('SCOUT_OUTPUT_FILE_SET_CHANGED')
    report=load_json(root/'private/editorial_entry_scout_v0719.json')
    session=load_json(root/'reviewer_only/review_session.json')
    if report.get('schema')!=scout.SCHEMA or session.get('schema')!='director-blinded-entry-review-v0719':
        raise DecisionError('SCOUT_SCHEMA_MISMATCH')
    if report.get('blinded_session_id')!=session.get('session_id') or report.get('source_provenance',{}).get('scene_family_id')!=session.get('family_id'):
        raise DecisionError('SCOUT_BINDING_MISMATCH')
    if any(report.get('authorizations',{}).get(k) is not False for k in DENIED):
        raise DecisionError('SCOUT_AUTHORIZATION_NON_FALSE')
    if any(session.get('authorizations',{}).get(k) is not False for k in DENIED):
        raise DecisionError('SCOUT_SESSION_AUTHORIZATION_NON_FALSE')
    entries=report.get('technical_evidence');options=session.get('options')
    if not isinstance(entries,list) or not isinstance(options,list) or len(entries)!=len(options) or not entries:
        raise DecisionError('SCOUT_OPTION_COUNTS_MISMATCH')
    by_name={e.get('alias'):e for e in entries};bysession={v.get('alias'):v for v in options}
    if len(by_name)!=len(entries) or set(by_name)!=set(bysession):raise DecisionError('SCOUT_ALIAS_MISMATCH')
    for alias,item in bysession.items():
        if not isinstance(alias,str) or not alias.startswith('Option_'):raise DecisionError('SCOUT_ALIAS_INVALID')
        if item.get('frames')!=48 or item.get('join_frame')!=12 or item.get('file')!=f'clips/{alias}.mp4':
            raise DecisionError('SCOUT_CLIP_METADATA_MISMATCH')
        clip=root/'reviewer_only'/item['file']
        if item.get('sha256')!=scout.sha(clip):raise DecisionError('SCOUT_CLIP_HASH_MISMATCH')
    return report,session,by_name

def annotations_load(path, provenance, expected_frames):
    if path is None:return [],'NOT_PROVIDED'
    doc=load_json(path)
    if doc.get('schema')!=EVENT_SCHEMA or doc.get('annotation_authority')!='UNVERIFIED_OPERATOR_REVIEW_NOT_GROUND_TRUTH':
        raise DecisionError('ANNOTATION_SCHEMA_OR_AUTHORITY_INVALID')
    if doc.get('source_sha256') not in (provenance['incoming']['sha256'],provenance['outgoing']['sha256']):
        raise DecisionError('ANNOTATION_SOURCE_HASH_MISMATCH')
    side='incoming' if doc['source_sha256']==provenance['incoming']['sha256'] else 'outgoing'
    if doc.get('source_side')!=side or doc.get('frames')!=provenance[side]['frames']:
        raise DecisionError('ANNOTATION_SOURCE_METADATA_MISMATCH')
    events=doc.get('events')
    if not isinstance(events,list) or len(events)>200:raise DecisionError('ANNOTATION_EVENTS_INVALID')
    seen=set()
    for event in events:
        if not isinstance(event,dict) or set(event)!={'start_frame','end_frame','event_type','reviewer_tag'}:
            raise DecisionError('ANNOTATION_EVENT_FIELDS_INVALID')
        s,e=event['start_frame'],event['end_frame']
        if type(s) is not int or type(e) is not int or s<0 or e<s or e>=doc['frames']:
            raise DecisionError('ANNOTATION_EVENT_RANGE_INVALID')
        if event['event_type'] not in ALLOWED_EVENTS:raise DecisionError('ANNOTATION_TYPE_INVALID')
        tag=event['reviewer_tag']
        if not isinstance(tag,str) or not (1<=len(tag)<=80) or '/' in tag or '\\' in tag:
            raise DecisionError('ANNOTATION_REVIEWER_TAG_INVALID')
        key=(s,e,event['event_type'],tag)
        if key in seen:raise DecisionError('ANNOTATION_DUPLICATE_EVENT')
        seen.add(key)
    return events,side

def analyze_contiguous(frames,source_start):
    """Flag *possible* image defects inside a single cut-safe side; not actions or edit taste."""
    if len(frames)<5:raise DecisionError('TECHNICAL_WINDOW_TOO_SHORT')
    small=[cv2.cvtColor(cv2.resize(f,(160,round(f.shape[0]*160/f.shape[1]))),cv2.COLOR_BGR2GRAY) for f in frames]
    median=[float(np.median(a)) for a in small]
    means=[float(np.mean(a)) for a in small]
    diffs=[float(np.mean(np.abs(a.astype('float32')-b.astype('float32')))) for a,b in zip(small[:-1],small[1:])]
    baseline=float(np.median(diffs));normal=max(baseline,0.35)
    flags=[]
    for k in range(1,len(frames)-1):
        if means[k]<8 and means[k-1]>40 and means[k+1]>40:
            flags.append({'code':'POSSIBLE_ISOLATED_DARK_FRAME','source_frame':source_start+k})
        if means[k]>247 and means[k-1]<215 and means[k+1]<215:
            flags.append({'code':'POSSIBLE_ISOLATED_BRIGHT_FRAME','source_frame':source_start+k})
    run=None
    for i,d in enumerate(diffs+[1e9]):
        if d<=.14:
            if run is None:run=i
        elif run is not None:
            if i-run>=2 and baseline>.2:
                flags.append({'code':'POSSIBLE_REPEATED_FRAME_RUN','source_frame':source_start+run+1,'duration_transitions':i-run})
            run=None
    for i,d in enumerate(diffs):
        if d>max(22.,normal*8.):flags.append({'code':'POSSIBLE_INTERNAL_IMAGE_DISCONTINUITY','source_frame':source_start+i+1})
    return {'flags':flags,'median_luma':round(float(np.median(median)),3),
            'median_adjacent_luma_change':round(baseline,3),'window_frames':len(frames),
            'scope':'WITHIN_A_SINGLE_SOURCE_WINDOW_NO_CROSS_CUT_MOTION',
            'flags_may_be_INTENTIONAL_FILMMAKING':True}

def build_review(scout_dir,outgoing,incoming,out,annotations=None):
    report,session,entries=verify_scout(scout_dir)
    provenance=report['source_provenance']
    # Original source byte binding, independent of filenames or preview thumbnails.
    for side,path in (('incoming',incoming),('outgoing',outgoing)):
        expected=provenance[side]
        got=scout.validate_source(path,expected['sha256'])
        if any(got[key]!=expected[key] for key in ('frames','fps','width','height')):
            raise DecisionError('SCOUT_SOURCE_METADATA_MISMATCH')
    ev,evside=annotations_load(annotations,provenance,provenance['incoming']['frames'])
    source_events_hash=scout.sha(annotations) if annotations is not None else None
    out=Path(out);stage=out.with_name(out.name+'_staging')
    if any(p.exists() or p.is_symlink() for p in (out,stage)):
        raise FileExistsError('REFUSE_EXISTING_DECISION_OUTPUT_OR_STAGING')
    stage.mkdir(parents=True)
    try:
        o=provenance['outgoing'];i=provenance['incoming']
        pred_frames=scout.read_frames(outgoing,range(o['frames']-12,o['frames']))
        outgoing_metrics=analyze_contiguous(pred_frames,o['frames']-12)
        options=[]
        for a in session['options']:
            alias=a['alias'];item=entries[alias];start=item['incoming_entry_frame']
            # Check interval against the scout's own barrier list again.
            if not scout.check_window(provenance['cut_candidates_incoming'],start,start+36):
                raise DecisionError('SCOUT_CANDIDATE_CROSSES_CUT')
            frames=scout.read_frames(incoming,range(start,start+36))
            motion=analyze_contiguous(frames,start)
            annotation_hits=[]
            if evside=='incoming':
                for e in ev:
                    if e['start_frame']<start<=e['end_frame']:
                        annotation_hits.append({'reviewer_tag':e['reviewer_tag'],
                              'event_type':e['event_type'],'reviewed_interval':[e['start_frame'],e['end_frame']],
                              'interpretation':'ENTRY_WITHIN_OPERATOR_ANNOTATED_INTERVAL_REVIEW_REQUIRED_NOT_PROVEN_INTERRUPTION'})
            elif evside=='outgoing':
                for e in ev:
                    if e['start_frame']<=o['frames']-1<=e['end_frame']:
                        annotation_hits.append({'reviewer_tag':e['reviewer_tag'],'event_type':e['event_type'],
                                                'reviewed_interval':[e['start_frame'],e['end_frame']],
                                                'interpretation':'OUTGOING_END_WITHIN_ANNOTATED_INTERVAL_REVIEW_REQUIRED'})
            alerts=[]
            if motion['flags'] or outgoing_metrics['flags']:alerts.append('POSSIBLE_TECHNICAL_DEFECT_REQUIRES_REVIEW')
            if annotation_hits:alerts.append('ANNOTATED_PERFORMANCE_BOUNDARY_REQUIRES_REVIEW')
            if not alerts:alerts.append('NO_SUPPORTED_DEFECT_UNDER_NARROW_TECHNICAL_CHECKS')
            options.append({'alias':alias,'incoming_entry_frame':start,'technical_incoming':motion,
                            'technical_outgoing_shared':outgoing_metrics,
                            'operator_annotation_overlap':annotation_hits,
                            'review_reasons':alerts,
                            'cinematic_quality':'UNKNOWN',
                            'performance_action_completed':'UNKNOWN',
                            'cut_interrupts_action':'UNKNOWN',
                            'audio_alignment':'UNKNOWN' if provenance['incoming_audio']!='NO_AUDIO_STREAM' else 'UNAVAILABLE_SILENT_SOURCE',
                            'human_working_approval':'NOT_AUTHORIZED_BY_THIS_REPORT'})
        clean=[v['alias'] for v in options if v['review_reasons']==['NO_SUPPORTED_DEFECT_UNDER_NARROW_TECHNICAL_CHECKS']]
        result={'schema':SCHEMA,'version':'0.7.20','mode':'SHADOW_ONLY',
            'source_scout_session_id':session['session_id'],
            'scene_family_id':session['family_id'],
            'source_hashes':{'outgoing':o['sha256'],'incoming':i['sha256']},
            'scout_dossier_sha256':scout.sha(Path(scout_dir)/'private/editorial_entry_scout_v0719.json'),
            'operator_annotation_evidence':{'state':'ABSENT' if annotations is None else 'PRESENT_NOT_AUTHENTICATED_AS_GROUND_TRUTH',
                       'sha256':source_events_hash,'source_side':evside,'event_count':len(ev)},
            'cut_side_policy':'NO_CROSS_CUT_MOTION_COMPARISON',
            'options':options,'technically_unflagged_aliases_NOT_ranked':clean,
            'technical_review_priority_only':True,
            'artistic_verdict':'ABSTAIN_NO_INDEPENDENT_VALIDATED_PERFORMANCE_AND_AUDIO_COMPARISON',
            'historical_F66_choice_used_as_label':False,
            'same_scene_variants_do_not_constitute_independent_training_examples':True,
            'authorizations':{a:False for a in DENIED},
            'h3_visual_reference_eligible':False,
            'limitations':['Image defect flags are review signals; flashes and freezes can be intentional',
                           'Motion magnitude is not an acting/naturalness score',
                           'Operator annotations are unverified claims tied to exact source bytes',
                           'No automatic cut ranking, automatic edit, memory mutation or production authorization']}
        (stage/'editorial_decision_v0720.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        md=['# Director V0.7.20 — Editorial Review Priority (Shadow Only)',
            '',f'- Verified scout session: `{session["session_id"]}`',
            f'- Cut variants from **one** scene: {len(options)}',
            '- Performance/edit choice: **ABSTAIN** (no independently established artistic ranking)',
            '- Human working edit: preserved outside scoring',
            '', '| Blind alias | Review signals | Annotations |','|---|---|---|']
        for op in options:md.append(f'| {op["alias"]} | {", ".join(op["review_reasons"])} | {len(op["operator_annotation_overlap"])} |')
        md+=['','This dossier flags possible technical or operator-annotated review issues only. It never selects an edit, certifies a gesture, or crosses H3 visual reference boundaries.']
        (stage/'EDITORIAL_REVIEW.md').write_text('\n'.join(md)+'\n')
        scout.write(stage/'SHA256_OUTPUTS.json',{'files':{p.name:scout.sha(p) for p in stage.iterdir() if p.is_file()}})
        os.replace(stage,out)
        return result
    except Exception:
        # Intentionally retain any partial staging evidence for postmortem; don't overwrite reruns.
        raise

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--scout',type=Path,required=True)
    p.add_argument('--outgoing',type=Path,required=True)
    p.add_argument('--incoming',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--operator-annotations',type=Path)
    args=p.parse_args()
    r=build_review(args.scout,args.outgoing,args.incoming,args.out,args.operator_annotations)
    print('V0720_SHADOW_REVIEW_READY',args.out,'OPTIONS',len(r['options']))
    print('V0720_ARTISTIC_VERDICT',r['artistic_verdict'])
if __name__=='__main__':main()
