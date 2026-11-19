#!/usr/bin/env python3
"""H3 Director V0.7.19: read-only cut-alternative *preparation*, not automatic selection.

This tool never outputs generated visual conditioning for H3, edits any source,
approves a film, derives actor actions, or updates production/scene memory.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import cv2
import numpy as np
import editorial_bridge_v0718 as bridge

ROOT=Path(__file__).resolve().parent
SCHEMA='director-evidence-gated-entry-scout-shadow-v0719'
DENIED=('automatic_editing','automatic_cut_selection','production_approval',
        'memory_mutation','continuity_handoff','scene_dna_verification',
        'gesture_certification','performance_certification','prop_ownership_certification',
        'actor_identity_certification','h3_generation','h3_visual_conditioning')

class ScoutError(ValueError):pass

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def write(path,data):
    Path(path).write_text(json.dumps(data,indent=2,sort_keys=True,allow_nan=False)+'\n')

def validate_source(path,expected):
    path=Path(path)
    if not path.is_file() or path.is_symlink():raise ScoutError('INVALID_SOURCE_PATH')
    if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
        raise ScoutError('EXPECTED_SHA256_REQUIRED')
    if sha(path)!=expected:raise ScoutError('SOURCE_HASH_MISMATCH')
    if path.stat().st_size>2_000_000_000:raise ScoutError('SOURCE_TOO_LARGE')
    c=cv2.VideoCapture(str(path))
    try:
        n=int(c.get(cv2.CAP_PROP_FRAME_COUNT));fps=float(c.get(cv2.CAP_PROP_FPS))
        w=int(c.get(cv2.CAP_PROP_FRAME_WIDTH));h=int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
        ok,img=c.read()
    finally:c.release()
    if not ok or img is None or img.shape[:2]!=(h,w) or not (8<=fps<=120) or not (48<=n<=1200) or min(w,h)<192:
        raise ScoutError('SOURCE_VIDEO_METADATA_UNSUPPORTED')
    return {'sha256':expected,'frames':n,'fps':fps,'width':w,'height':h}

def validate_cuts(frames,n):
    if frames is None:raise ScoutError('EXPLICIT_CANDIDATE_CUT_LIST_REQUIRED')
    if not isinstance(frames,list) or any(type(x) is not int for x in frames):raise ScoutError('CUT_LIST_INVALID')
    if len(frames)!=len(set(frames)) or frames!=sorted(frames) or any(x<1 or x>=n for x in frames):
        raise ScoutError('CUT_LIST_INVALID')
    return frames

def audio_status(path):
    if not shutil.which('ffprobe'):return 'UNVERIFIED_FFPROBE_UNAVAILABLE'
    try:
        cp=subprocess.run(['ffprobe','-v','error','-select_streams','a','-show_entries','stream=index',
                           '-of','json',str(path)],capture_output=True,text=True,timeout=12,check=True)
        tracks=json.loads(cp.stdout).get('streams',[])
        return 'PRESENT_NOT_EVALUATED_IN_SILENT_PREVIEWS' if tracks else 'NO_AUDIO_STREAM'
    except (OSError,subprocess.SubprocessError,ValueError):return 'UNVERIFIED_AUDIO_METADATA'

def choose_grid(n,post=36,interval=12,max_cases=10):
    if type(n) is not int or n<post:return []
    choices=list(range(0,n-post+1,interval))
    if len(choices)>max_cases:
        ind=np.linspace(0,len(choices)-1,max_cases,dtype=int)
        choices=[choices[i] for i in sorted(set(ind))]
    return choices

def check_window(cuts,a,b):
    # Candidate boundary at 'a' is permitted; inside a sample is not.
    return not any(a<c<b for c in cuts)

def preflight(predecessor,pred_hash,incoming,inc_hash,candidates,pred_cuts,inc_cuts,pre_frames=12,post_frames=36):
    p=validate_source(predecessor,pred_hash);i=validate_source(incoming,inc_hash)
    if abs(p['fps']-i['fps'])>0.05:raise ScoutError('FPS_MISMATCH_REQUIRES_EXPLICIT_RESAMPLING')
    if (p['width'],p['height'])!=(i['width'],i['height']):raise ScoutError('DIMENSIONS_MISMATCH_REQUIRES_EXPLICIT_RESAMPLING')
    pred_cuts=validate_cuts(pred_cuts,p['frames']);inc_cuts=validate_cuts(inc_cuts,i['frames'])
    if not check_window(pred_cuts,p['frames']-pre_frames,p['frames']):
        raise ScoutError('OUTGOING_WINDOW_CROSSES_CANDIDATE_CUT')
    if candidates is None:candidates=choose_grid(i['frames'],post_frames)
    if not candidates or any(type(x) is not int for x in candidates) or len(set(candidates))!=len(candidates):
        raise ScoutError('INVALID_CANDIDATE_ENTRY_LIST')
    if len(candidates)>16:raise ScoutError('TOO_MANY_CANDIDATES')
    if any(x<0 or x+post_frames>i['frames'] for x in candidates):raise ScoutError('ENTRY_OUTSIDE_SOURCE_RANGE')
    safe=[x for x in candidates if check_window(inc_cuts,x,x+post_frames)]
    withheld={str(x):'WINDOW_CROSSES_CANDIDATE_CUT' for x in candidates if x not in safe}
    if not safe:raise ScoutError('NO_CUT_SAFE_ENTRIES')
    return {'pred':p,'incoming':i,'pred_cuts':pred_cuts,'incoming_cuts':inc_cuts,
            'entries':sorted(safe),'withheld':withheld,
            'pre_frames':pre_frames,'post_frames':post_frames}

def read_frames(path,indices):
    indices=list(indices)
    if not indices:return []
    if any(b!=a+1 for a,b in zip(indices,indices[1:])):
        raise ScoutError('NONCONTIGUOUS_FRAME_REQUEST')
    cap=cv2.VideoCapture(str(path));res=[]
    try:
        if not cap.set(cv2.CAP_PROP_POS_FRAMES,indices[0]):raise ScoutError('FRAME_SEEK_FAILED')
        for idx in indices:
            ok,img=cap.read()
            if not ok or img is None:raise ScoutError('FRAME_READ_FAILED')
            res.append(img)
    finally:cap.release()
    return res

def summarize_motion(frames):
    # These are within-side image-variation statistics, NEVER inter-shot optical flow.
    small=[cv2.cvtColor(cv2.resize(im,(192,round(im.shape[0]*192/im.shape[1]))),cv2.COLOR_BGR2GRAY) for im in frames]
    diffs=[float(np.mean(np.abs(small[j].astype(np.float32)-small[j-1].astype(np.float32)))) for j in range(1,len(small))]
    black=sum(float(np.mean(a))<9 for a in small)
    white=sum(float(np.mean(a))>246 for a in small)
    near_duplicate=sum(d<0.15 for d in diffs)
    return {'adjacent_frame_mad_median':round(float(np.median(diffs)),4) if diffs else None,
            'possible_black_frames':black,'possible_white_frames':white,
            'near_duplicate_transitions':near_duplicate,
            'units':'8-bit luma mean absolute difference, within same shot only',
            'not_action_or_quality_score':True}

def create(predecessor,pred_hash,incoming,inc_hash,out,entries=None,pred_cuts=None,inc_cuts=None,
           demo_derived=None,incoming_postshot=None):
    if incoming_postshot is not None:
        if inc_cuts is not None:raise ScoutError('POSTSHOT_AND_MANUAL_CUTS_MUTUALLY_EXCLUSIVE')
        im=validate_source(incoming,inc_hash)
        inc_cuts, report_binding=bridge.report_cuts(Path(incoming_postshot),inc_hash,im['frames'])
        report_binding={'binding_type':'HASH_MATCHED_V074_CANDIDATE_CUTS_ONLY',**report_binding}
    else:
        if inc_cuts is None:raise ScoutError('EXPLICIT_CANDIDATE_CUT_LIST_OR_MATCHING_POSTSHOT_REQUIRED')
        report_binding={'binding_type':'EXPLICIT_OPERATOR_CANDIDATE_CUTS_NOT_VERIFIED_INTENT'}
    spec=preflight(predecessor,pred_hash,incoming,inc_hash,entries,pred_cuts,inc_cuts)
    out=Path(out);stage=out.with_name(out.name+'_staging')
    if any(p.exists() or p.is_symlink() for p in (out,stage)):
        raise FileExistsError('REFUSE_EXISTING_OUTPUT_OR_STAGING')
    source_pair=spec['pred']['sha256']+'|'+spec['incoming']['sha256']
    family_id=hashlib.sha256(source_pair.encode()).hexdigest()
    blinded=sorted(spec['entries'],key=lambda x:hashlib.sha256((family_id+f'|BLIND|{x}').encode()).hexdigest())
    aliases={x:f'Option_{i+1:02}' for i,x in enumerate(blinded)}
    stage.mkdir(parents=True)
    (stage/'reviewer_only/clips').mkdir(parents=True)
    (stage/'private').mkdir()
    pred=read_frames(predecessor,range(spec['pred']['frames']-12,spec['pred']['frames']))
    pred_motion=summarize_motion(pred)
    review_options=[];private=[]
    dims=(384,672) if spec['pred']['width']<spec['pred']['height'] else (672,384)
    for off in blinded:
        incoming_frames=read_frames(incoming,range(off,off+36))
        prev=stage/'reviewer_only/clips'/f'{aliases[off]}.mp4'
        writer=cv2.VideoWriter(str(prev),cv2.VideoWriter_fourcc(*'mp4v'),spec['pred']['fps'],dims)
        if not writer.isOpened():raise ScoutError('PREVIEW_ENCODER_UNAVAILABLE')
        try:
            for frame in pred+incoming_frames:
                writer.write(cv2.resize(frame,dims,interpolation=cv2.INTER_AREA))
        finally:writer.release()
        if not prev.is_file() or prev.stat().st_size<1000:raise ScoutError('PREVIEW_CREATION_FAILED')
        pc=cv2.VideoCapture(str(prev))
        try:count=int(pc.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:pc.release()
        if count!=48:raise ScoutError('PREVIEW_FRAME_COUNT_MISMATCH')
        record={'alias':aliases[off],'file':f'clips/{aliases[off]}.mp4','sha256':sha(prev),
                'frames':48,'join_frame':12,'audio':'SILENT_PREVIEW_NO_AUDIO_EVALUATION'}
        review_options.append(record)
        private.append({'alias':aliases[off],'incoming_entry_frame':off,
                        'incoming_review_interval':[off,off+35],
                        'outgoing':pred_motion,'incoming':summarize_motion(incoming_frames),
                        'technical_issues_not_an_artistic_ranking':True})
    pred_audio=audio_status(predecessor);inc_audio=audio_status(incoming)
    sid=hashlib.sha256((family_id+'|'+','.join(str(v) for v in sorted(spec['entries']))).encode()).hexdigest()[:24]
    session={'schema':'director-blinded-entry-review-v0719','session_id':sid,
             'family_id':family_id,'review_basis':'SILENT_VISUAL_ONLY',
             'options':review_options,'number_of_independent_scene_families':1,
             'sources_are_unverified_editorial_inputs_not_H3_references':True,
             'historical_human_selection_not_visible':True,
             'no_action_or_emotion_truth_supplied':True,
             'authorizations':{x:False for x in DENIED}}
    (stage/'reviewer_only/review_session.json').write_text(json.dumps(session,indent=2)+'\n')
    cards='\n'.join(f'<section><h2>{r["alias"]}</h2><video controls preload="metadata" src="{r["file"]}"></video></section>' for r in review_options)
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Director - blind cut review</title><style>body{font:16px system-ui;color:#eef;background:#11151a;max-width:940px;margin:20px auto;padding:15px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}section{padding:14px;background:#272d33;border-radius:12px}video{width:100%}h2{font-size:17px}p{line-height:1.5}</style><h1>Blind entry-cut alternatives</h1><p>Silent previews: 12 outgoing and 36 incoming frames. Options share the same source performance. No option is an approved cut or a verified performance assessment. Evaluate visually; abstaining is appropriate.</p><div class="grid">'''+cards+'</div></html>'
    (stage/'reviewer_only/index.html').write_text(html)
    report={'schema':SCHEMA,'version':'0.7.19','mode':'SHADOW_ONLY',
            'operation':'BLIND_ALTERNATIVE_PREPARATION_ONLY',
            'source_provenance':{'outgoing':spec['pred'],'incoming':spec['incoming'],
             'cut_candidates_outgoing':spec['pred_cuts'],'cut_candidates_incoming':spec['incoming_cuts'],
             'empty_cut_lists_are_not_proof_of_continuous_footage':True,
             'outgoing_audio':pred_audio,'incoming_audio':inc_audio,
             'derived_archival_context':demo_derived,'scene_family_id':family_id,
             'incoming_cut_report_binding':report_binding},
            'candidate_policy':'CHRONOLOGICAL_COVERAGE_OR_EXPLICIT_OPERATOR_FRAMES_NOT_AESTHETIC_RANKING',
            'candidate_count':len(private),'withheld_candidates':spec['withheld'],
            'technical_evidence':private,'blinded_session_id':sid,
            'cut_evidence':{'all_motion_within_each_side':True,'cross_cut_flow_forbidden':True,
                 'gesture_completion':'UNKNOWN','microphone_ownership':'UNKNOWN',
                 'performance_naturalness':'UNKNOWN','dialogue_timing':'UNKNOWN',
                 'edit_intent':'UNKNOWN'},
            'historical_working_preference':'PRESERVED_OUTSIDE_SCORING',
            'artistic_decision':'ABSTAIN_NO_INDEPENDENT_PERFORMANCE_OR_AUDIO_EVIDENCE',
            'authorizations':{x:False for x in DENIED},
            'integration':'READ_ONLY_FUTURE_V0718_INTAKE_EXTENSION_NOT_ATTACHED_TO_V075_WORKER',
            'h3_visual_reference_eligible':False}
    write(stage/'private/editorial_entry_scout_v0719.json',report)
    # Hash all deliverables before finalizing, and never overwrite prior runs.
    write(stage/'private/SHA256_OUTPUTS.json',{'files':{str(p.relative_to(stage)):sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}})
    os.replace(stage,out)
    return report,out

def demo():
    r=ROOT/'media'
    outgoing=r/'eight_shot_working_REVIEW_ONLY.mp4'
    incoming=r/'shot9_suffix_from_F12_REENCODED_OBSERVATION_ONLY.mp4'
    hist={'historical_reconstruction':'Eight-shot working edit is archival; incoming is only frames F12 onward, extracted from the archived F12 assembly, not raw generator output.',
          'incoming_original_trim_offset':12,'earlier_original_shot_frames_F0_to_F11':'UNAVAILABLE',
          'historical_working_F66_approval':'HUMAN_WORKING_ONLY_NOT_TRAINING_LABEL',
          'derived_from_archive_sha256':sha(r/'nine_shot_F12_REVIEW_ONLY.mp4'),
          'historical_assembly_join_frame':692}
    # Explicit 54 is a provenance-preserving comparator of the archived working cut,
    # NOT a target or weight in any evaluation formula.
    return create(outgoing,sha(outgoing),incoming,sha(incoming),ROOT/'results/v0719_demo',
                  entries=[0,12,24,36,48,54,60,72],pred_cuts=[],inc_cuts=[],demo_derived=hist)

def cli():
    p=argparse.ArgumentParser();p.add_argument('--demo',action='store_true');p.add_argument('--predecessor',type=Path)
    p.add_argument('--predecessor-sha256');p.add_argument('--incoming',type=Path);p.add_argument('--incoming-sha256')
    p.add_argument('--entry-frames',nargs='*',type=int);p.add_argument('--predecessor-cuts',nargs='*',type=int)
    p.add_argument('--incoming-cuts',nargs='*',type=int);p.add_argument('--incoming-postshot',type=Path)
    p.add_argument('--out',type=Path);a=p.parse_args()
    if a.demo:
        if any(v is not None for v in (a.predecessor,a.incoming,a.entry_frames,a.out,a.predecessor_cuts,a.incoming_cuts,a.incoming_postshot)):
            p.error('--demo accepts no extra options')
        r,o=demo()
    else:
        if None in (a.predecessor,a.predecessor_sha256,a.incoming,a.incoming_sha256,a.out,a.predecessor_cuts):
            p.error('Provide predecessor/incoming files and exact SHA256, explicit predecessor cuts (even if empty), and --out')
        if (a.incoming_postshot is None)==(a.incoming_cuts is None):
            p.error('Choose exactly one: --incoming-postshot or --incoming-cuts (may be empty)')
        r,o=create(a.predecessor,a.predecessor_sha256,a.incoming,a.incoming_sha256,a.out,
                   entries=a.entry_frames,pred_cuts=a.predecessor_cuts,inc_cuts=a.incoming_cuts,
                   incoming_postshot=a.incoming_postshot)
    print('V0719_SHADOW_ALTERNATIVES_READY',o,'COUNT',r['candidate_count'])
    print('V0719_ARTISTIC_DECISION',r['artistic_decision'])
if __name__=='__main__':cli()
