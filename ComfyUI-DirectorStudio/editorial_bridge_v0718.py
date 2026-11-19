#!/usr/bin/env python3
"""V0.7.18: read-only bridge from V0.7.4 shadow postshot to editorial intake.

No generated frames may enter H3 conditioning; no automatic edit or memory writes.
Existing V0.7.17 report generation is intentionally unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import cv2
import editorial_review_v0712 as technical
import editorial_unified_v0717 as archive

ROOT=Path(__file__).resolve().parent
SCHEMA='director-postshot-editorial-bridge-shadow-v0718'
DENIED=tuple(sorted(set(technical.DENIED)|set(archive.AUTH_KEYS)|{
    'director_memory_changed','production_director_changed','scene_dna_verified',
    'performance_certification','actor_identity_certification'}))

class BridgeError(ValueError): pass

def sha(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def metadata(video:Path):
    cap=cv2.VideoCapture(str(video))
    if not cap.isOpened(): raise BridgeError('VIDEO_UNREADABLE')
    try:
        fps=float(cap.get(cv2.CAP_PROP_FPS))
        count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:cap.release()
    if not (8<=fps<=120 and 20<=count<=1200 and width>=64 and height>=64):
        raise BridgeError('UNSUPPORTED_VIDEO_METADATA')
    return fps,count,width,height

def cuts_valid(cuts,count):
    if not isinstance(cuts,list) or any(type(c) is not int for c in cuts):
        raise BridgeError('CUTS_MUST_BE_INTEGER_FRAMES')
    if cuts!=sorted(set(cuts)) or any(not 0<c<count for c in cuts):
        raise BridgeError('CUTS_OUT_OF_RANGE_OR_DUPLICATED')
    return cuts

def report_cuts(postshot:Path, source_sha:str, count:int):
    """Validate *structural* consistency. Observer claims remain unverified."""
    d=json.loads(Path(postshot).read_text())
    if d.get('version')!='director-unified-postshot-shadow-0.7.4':
        raise BridgeError('UNKNOWN_POSTSHOT_VERSION')
    if d.get('source_class')!='H3_GENERATED_OBSERVATION_ONLY':
        raise BridgeError('WRONG_POSTSHOT_PROVENANCE')
    if d.get('source_sha256')!=source_sha:
        raise BridgeError('POSTSHOT_VIDEO_HASH_MISMATCH')
    if (d.get('automatic_editing_authorized') is not False
        or d.get('continuity_handoff_authorized') is not False
        or d.get('h3_generation') is not False
        or d.get('h3_visual_conditioning') is not False
        or d.get('director_memory_changed') is not False
        or d.get('production_director_changed') is not False
        or d.get('scene_dna_verified') is not False
        or d.get('framing_predictions_quarantined_globally') is not True
        or d.get('generated_video_never_H3_visual_conditioning') is not True
        or d.get('human_review_required') is not True):
        raise BridgeError('POSTSHOT_AUTHORITY_OR_QUARANTINE_INVALID')
    if not all(isinstance(d.get(key),str) and len(d[key])==64 and
               all(c in '0123456789abcdef' for c in d[key])
               for key in ('source_sha256','job_sha256','profile_sha256',
                           'observer_bundle_sha256','scene_context_sha256')):
        raise BridgeError('POSTSHOT_INCOMPLETE_HASH_PROVENANCE')
    if not isinstance(d.get('job_id'),str) or not d['job_id']:
        raise BridgeError('POSTSHOT_JOB_ID_MISSING')
    barriers=d.get('candidate_cut_barriers')
    if not isinstance(barriers,list):raise BridgeError('NO_CUT_BARRIER_FIELD')
    cuts=cuts_valid([c.get('frame') for c in barriers if isinstance(c,dict)],count)
    if len(cuts)!=len(barriers) or any(c.get('continuous_motion_inference_blocked') is not True for c in barriers):
        raise BridgeError('CUT_BARRIER_NOT_ENFORCED')
    segments=d.get('observed_candidate_segments')
    if not isinstance(segments,list) or len(segments)!=len(cuts)+1:
        raise BridgeError('SEGMENT_COUNT_CONTRADICTION')
    borders=[0,*cuts,count]
    for i,seg in enumerate(segments):
        if not isinstance(seg,dict) or seg.get('frames')!=[borders[i],borders[i+1]-1]:
            raise BridgeError('SEGMENT_BOUNDARY_MISMATCH')
        if seg.get('segment_continuity_verified') is not False or seg.get('motion_between_stills_not_verified') is not True:
            raise BridgeError('SEGMENT_FALSE_CONTINUITY_AUTHORITY')
    return cuts, {'postshot_sha256':sha(postshot),'postshot_job_id':d['job_id'],
                  'postshot_profile_id':d.get('profile_id'),'postshot_source_sha256':d['source_sha256'],
                  'cut_provenance':'UNVERIFIED_V074_CANDIDATE_CUT_BARRIERS_ONLY'}

def validate_source(video:Path,expected_sha:str):
    if not video.is_file() or video.is_symlink():raise BridgeError('SOURCE_IS_NOT_REGULAR_FILE')
    if not isinstance(expected_sha,str) or len(expected_sha)!=64 or any(c not in '0123456789abcdef' for c in expected_sha):
        raise BridgeError('EXPECTED_SHA256_REQUIRED')
    if sha(video)!=expected_sha:raise BridgeError('INPUT_VIDEO_HASH_MISMATCH')
    if video.stat().st_size>2_000_000_000:raise BridgeError('VIDEO_TOO_LARGE')
    return metadata(video)

def prepare(video:Path,expected_sha:str,postshot=None,cut_frames=None):
    """Preflight MUST finish before writing any output."""
    video=Path(video);fps,count,width,height=validate_source(video,expected_sha)
    if postshot is not None:
        if cut_frames is not None:raise BridgeError('CUTS_AND_POSTSHOT_ARE_MUTUALLY_EXCLUSIVE')
        cuts,provenance=report_cuts(Path(postshot),expected_sha,count)
        status='HASH_BOUND_V074_REPORT_BUT_MODEL_CLAIMS_UNVERIFIED'
    else:
        if cut_frames is None:raise BridgeError('EXPLICIT_CUT_LIST_REQUIRED_EVEN_IF_EMPTY')
        cuts=cuts_valid(cut_frames,count)
        provenance={'postshot_sha256':None,'cut_provenance':'OPERATOR_DECLARED_UNVERIFIED_CANDIDATES'}
        status='UNVERIFIED_EXTERNAL_OBSERVATION_ONLY'
    return {'video':video,'source_sha256':expected_sha,'fps':fps,'frame_count':count,
            'dimensions':[width,height], 'cuts':cuts,'status':status,'provenance':provenance}

def assess(video,expected_sha,out,postshot=None,cut_frames=None):
    spec=prepare(video,expected_sha,postshot,cut_frames)
    out=Path(out);stage=out.with_name(out.name+'_staging')
    if out.exists() or out.is_symlink() or stage.exists() or stage.is_symlink():
        raise FileExistsError('REFUSE_EXISTING_DESTINATION_OR_STAGING')
    stage.mkdir(parents=True)
    # If something fails, leave the stage as evidence. Never clobber old results.
    technical_out=stage/'technical_review_v0712'
    report=technical.run_external(spec['video'],expected_sha,spec['cuts'],technical_out)
    if report.get('source_sha256')!=expected_sha or report.get('cut_barriers')!=spec['cuts'] or any(report.get('authorizations',{}).values()):
        raise BridgeError('TECHNICAL_REPORT_RECONCILIATION_FAILED')
    dossier={
        'schema':SCHEMA,'version':'0.7.18','mode':'SHADOW_ONLY',
        'input_binding_status':spec['status'],
        'source':{'sha256':spec['source_sha256'],'frame_count':spec['frame_count'],
                  'fps':round(spec['fps'],5),'dimensions':spec['dimensions'],
                  'source_class':'H3_GENERATED_OBSERVATION_ONLY' if postshot is not None else 'UNVERIFIED_EXTERNAL_OBSERVATION_ONLY',
                  'h3_visual_reference_eligible':False},
        'evidence_provenance':spec['provenance'],
        'cut_barriers':{'frames':spec['cuts'],'status':'CANDIDATE_ONLY_NOT_VERIFIED_EDITORIAL_INTENT',
                        'empty_list_does_not_prove_uncut_footage':not bool(spec['cuts']),
                        'no_cross_cut_motion_inference':True},
        'technical_report':{'relative_path':'technical_review_v0712/external_editorial_review.json',
                            'sha256':sha(technical_out/'external_editorial_review.json'),
                            'review_contact_sha256':sha(technical_out/'external_review_contact.jpg') if (technical_out/'external_review_contact.jpg').exists() else None,
                            'candidate_segment_count':len(report['segments'])},
        'semantics':{'gesture':'UNKNOWN','actor_identity':'UNKNOWN','microphone_holder':'UNKNOWN',
                     'naturalness':'UNKNOWN','emotion_arc':'UNKNOWN','audio_timing':'UNKNOWN',
                     'performance_interruptions':'UNKNOWN'},
        'decision':'REVIEW_CANDIDATES_ONLY_NO_AUTOMATIC_EDIT',
        'authorizations':{key:False for key in DENIED},
        'integration':'READ_ONLY_POSTSHOT_ADAPTER_NOT_ATTACHED_TO_V075_WORKER',
        'limitations':['A matching SHA establishes bytes, not semantic truth',
                       'Reported cut barriers are candidate discontinuities, not editorial decisions',
                       'V0.7.12 technical still rankings are not acting or cut quality scores',
                       'This has not been tested as part of live V0.7.5 dispatch',
                       'Generated review material never enters H3 visual conditioning']}
    (stage/'editorial_bridge_v0718.json').write_text(json.dumps(dossier,indent=2,allow_nan=False)+'\n')
    filehashes={str(p.relative_to(stage)):sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}
    (stage/'SHA256_OUTPUTS.json').write_text(json.dumps({'files':filehashes},indent=2)+'\n')
    os.replace(stage,out)
    return dossier,out

def demo(root=ROOT):
    """Offline demonstration includes three distinct videos and the existing blinded seam dossier."""
    root=Path(root)
    results=root/'results/v0718_demo';stage=results.with_name(results.name+'_staging')
    if any(p.exists() or p.is_symlink() for p in (results,stage)):
        raise FileExistsError('REFUSE_EXISTING_DEMO_OR_STAGE')
    fixture=json.loads((root/'reference/fixture_sources_v0718.json').read_text())
    # Validate all sources and historical mismatch guard prior to writing outputs.
    for case in fixture['cases']:
        p=root/case['video']
        prepare(p,case['sha256'],cut_frames=case['cuts'])
    # V0.7.17 consolidated report is not touched or altered.
    archive_dossier=archive.consolidate(root)
    stage.mkdir(parents=True)
    for case in fixture['cases']:
        p=root/case['video'];s=stage/case['name']
        assess(p,case['sha256'],s,cut_frames=case['cuts'])
    (stage/'archived_editorial_v0717.json').write_text(json.dumps(archive_dossier,indent=2)+'\n')
    summary={'schema':'director-editorial-demo-summary-v0718',
             'distinct_video_source_count':len({c['sha256'] for c in fixture['cases']}),
             'historical_archival_scene_family_count':1,
             'historical_working_trim_label_not_training_ground_truth':True,
             'hard_cut_source_has_F101_candidate':next(c['cuts'] for c in fixture['cases'] if c['name']=='shadow_00002')==[101],
             'v074_real_audio_media_absent_from_archive':True,
             'positive_v074_binding_test':'SYNTHETIC_TEST_ONLY_REAL_SOURCE_NOT_AVAILABLE',
             'decision':'ABSTAIN_SHADOW_ONLY','authorizations':{k:False for k in DENIED}}
    (stage/'demo_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (stage/'SHA256_OUTPUTS.json').write_text(json.dumps({'files':{str(p.relative_to(stage)):sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}},indent=2)+'\n')
    os.replace(stage,results)
    return summary,results

def main():
    parser=argparse.ArgumentParser(description='Read-only V0.7.18 editorial bridge')
    parser.add_argument('--demo',action='store_true');parser.add_argument('--video',type=Path)
    parser.add_argument('--expected-sha256');parser.add_argument('--postshot',type=Path)
    parser.add_argument('--cut-frames',nargs='*',type=int);parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.demo:
        if args.video or args.out or args.postshot or args.cut_frames is not None:parser.error('--demo is exclusive')
        x,p=demo();print('V0718_DEMO_OK',p,'SOURCES',x['distinct_video_source_count']);return
    if args.video is None or args.expected_sha256 is None or args.out is None:parser.error('provide --video --expected-sha256 --out')
    if (args.postshot is None)==(args.cut_frames is None):parser.error('choose exactly one: --postshot or --cut-frames (may be empty)')
    x,p=assess(args.video,args.expected_sha256,args.out,args.postshot,args.cut_frames)
    print('V0718_POSTSHOT_EDITORIAL_BRIDGE_READY',p,x['input_binding_status'])
    print('V0718_DECISION',x['decision'])
if __name__=='__main__':main()
