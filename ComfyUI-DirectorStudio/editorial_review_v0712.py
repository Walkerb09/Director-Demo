#!/usr/bin/env python3
"""H3 Reference Director 0.7.12: evidence-gated, SHADOW editorial *review*.

Frame clarity/technical triage is not acting quality, permission to cut, or
semantic continuity. No video is ever passed to H3 or a generative model.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parent
DENIED=("h3_visual_conditioning","h3_generation","automatic_editing","automatic_cut_selection",
        "memory_mutation","continuity_handoff","verified_scene_dna","gesture_certification",
        "prop_ownership_certification","production_integration")
ALLOWED_TYPES={"REENCODED_GENERATED_OBSERVATION_ONLY","AUTHENTIC_ORIGINAL_OBSERVATION_ONLY"}
SCHEMA='director-evidence-editorial-review-v0712'

class EvidenceError(ValueError):pass

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def source_profile(rel):
    p=(ROOT/'profiles'/f'{rel}.json')
    d=json.loads(p.read_text())
    if d.get('schema')!='director-auto-motion-background-shadow-v0710':raise EvidenceError('PROFILE_SCHEMA')
    if d.get('source_type') not in ALLOWED_TYPES:raise EvidenceError('UNKNOWN_PROVENANCE')
    f=Path(d.get('source_relpath',''))
    if f.is_absolute() or '..' in f.parts or f.parts[:1]!=('media',):raise EvidenceError('UNSAFE_SOURCE_PATH')
    video=(ROOT/f).resolve()
    if not video.is_relative_to((ROOT/'media').resolve()):raise EvidenceError('SOURCE_OUTSIDE_MEDIA')
    if sha(video)!=d.get('source_sha256'):raise EvidenceError('SOURCE_HASH_MISMATCH')
    cuts=d.get('cut_frames',[])
    if not isinstance(cuts,list) or cuts!=sorted(set(cuts)) or any(type(c)!=int or c<1 or c>=d['frame_count'] for c in cuts):
        raise EvidenceError('INVALID_CUT_BARRIERS')
    return video,d,sha(p)

def video_frames(video,profile):
    cap=cv2.VideoCapture(str(video))
    if not cap.isOpened():raise EvidenceError('VIDEO_OPEN_FAILED')
    fps=float(cap.get(cv2.CAP_PROP_FPS));count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if abs(fps-float(profile['fps']))>.02 or count!=profile['frame_count'] or count>1200:
        cap.release();raise EvidenceError('VIDEO_METADATA_MISMATCH_OR_TOO_LONG')
    images=[];flags=[]
    for i in range(count):
        ok,frame=cap.read()
        if not ok:cap.release();raise EvidenceError('VIDEO_FRAME_MISSING')
        h,w=frame.shape[:2]
        new_w=224;new_h=max(1,round(h*new_w/w))
        gray=cv2.cvtColor(cv2.resize(frame,(new_w,new_h),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY)
        images.append(gray)
    cap.release()
    if len(images)!=count:raise EvidenceError('INCOMPLETE_VIDEO')
    return images

def frame_metrics(images):
    result=[]
    last=None
    for i,gray in enumerate(images):
        contrast=float(cv2.Laplacian(gray,cv2.CV_64F).var())
        clipped=float(np.mean((gray<=8)|(gray>=247)))
        median=float(np.median(gray))
        change=None if last is None else float(np.mean(cv2.absdiff(gray,last)))
        result.append({'frame':i,'laplacian_variance':round(contrast,3),
          'clipped_fraction':round(clipped,4),'median_luminance':round(median,2),
          'raw_frame_change_MAD':None if change is None else round(change,3)})
        last=gray
    return result

def segments(count,cuts):
    bounds=[0,*cuts,count]
    return [[bounds[i],bounds[i+1]-1] for i in range(len(bounds)-1)]

def frame_window_candidates(start,end,fps):
    """Deterministic, duration-normalized frame sampling, not automatic cuts."""
    stride=max(1,round(fps/4))  # ~4 review stills per second
    safe_first=start+min(3,max(0,(end-start)//4))
    safe_last=end-min(3,max(0,(end-start)//4))
    if safe_first>safe_last:return []
    return list(range(safe_first,safe_last+1,stride))

def triage(metrics,seg,fps,exclude=None,max_choices=4):
    """Technical still review order; cannot evaluate performance or edit transitions."""
    exclude=set(exclude or ())
    start,end=seg
    ids=frame_window_candidates(start,end,fps)
    values=[metrics[i]['laplacian_variance'] for i in range(start,end+1)]
    # In-scene normalization ONLY: prevents false comparisons of footage with different optics.
    lo,hi=np.percentile(values,[10,90])
    span=max(1.0,float(hi-lo))
    scored=[]
    for i in ids:
        if i in exclude:continue
        row=metrics[i]
        sharp=max(0.,min(1.,(row['laplacian_variance']-lo)/span))
        motion_vals=[metrics[k]['raw_frame_change_MAD'] for k in range(max(start+1,i-2),min(end,i+2)+1)]
        change=float(statistics.median(motion_vals)) if motion_vals else None
        # Frame differences include camera motion: this is a *technical review* measure only.
        calm=0.5 if change is None else max(0.,min(1.,1.-change/28.))
        exposed=max(0.,min(1.,1.-row['clipped_fraction']*4.))
        score=round(.5*sharp+.25*calm+.25*exposed,4)
        scored.append({'frame':i,'time_s':round(i/fps,3),'review_priority_score':score,
         'relative_clarity':round(sharp,3),'raw_image_change':None if change is None else round(change,2),
         'clipped_fraction':row['clipped_fraction'],'decision':'TECHNICAL_STILL_REVIEW_ONLY',
         'performance_quality':'UNKNOWN','actor_motion':'UNKNOWN','transition_quality':'UNKNOWN'})
    scored.sort(key=lambda r:(-r['review_priority_score'],r['frame']))
    return scored[:max_choices]

def check_wrist_evidence(video,source_type,pose_evidence):
    if pose_evidence is None:return {'status':'NO_POSE_EVIDENCE_FOR_SOURCE','gesture_authority':False}
    data=json.loads(Path(pose_evidence).read_text())
    if data.get('schema')!='director-actor-wrist-integrity-v07114' or data.get('mode')!='SHADOW_ONLY':
        raise EvidenceError('WRIST_SCHEMA_INVALID')
    if data.get('source_sha256')!=sha(video) or data.get('source_type')!=source_type:
        raise EvidenceError('WRIST_SOURCE_MISMATCH')
    if not data.get('authorizations') or any(data['authorizations'].values()):raise EvidenceError('WRIST_AUTHORIZATION_LEAK')
    if data.get('torso_tracking',{}).get('status')!='CONTIGUOUS_TORSO_TRACKLETS_CANDIDATE':
        return {'status':'POSE_TRACKING_UNCERTAIN','gesture_authority':False,'report_sha256':sha(pose_evidence)}
    withheld=[{'track':t['anonymous_track_id'],'wrist':t['wrist_keypoint_coco17'],
               'reason':t['summary']['status']}
              for t in data.get('wrist_tracks',[]) if t['summary']['status'].startswith('WITHHELD')]
    return {'status':'UNVERIFIED_POSE_TRACKLETS_WITH_VETO' if withheld else 'UNVERIFIED_POSE_TRACKLETS',
        'source_window':data['frame_range'],'anonymous_actor_count':data['torso_tracking']['track_count'],
        'withheld_wrist_tracks':withheld,'all_remaining_wrists':'UNVERIFIED',
        'report_sha256':sha(pose_evidence),'gesture_authority':False}

def assess_case(case):
    if case not in ('shot7','shadow','original'):raise EvidenceError('UNSUPPORTED_CASE')
    video,profile,profile_digest=source_profile(case)
    images=video_frames(video,profile)
    metrics=frame_metrics(images)
    boundaries=segments(len(images),profile['cut_frames'])
    pose=(ROOT/'reference/actor_wrist_integrity_v07114.json') if case=='shot7' else None
    wrist=check_wrist_evidence(video,profile['source_type'],pose)
    camera_source=(ROOT/'reference/v0710_benchmark_summary.json')
    cross=json.loads(camera_source.read_text())
    if cross.get('sources',{}).get('shadow_pre' if case=='shadow' else case,{}).get('sha256')!=sha(video):
        raise EvidenceError('INDEPENDENT_BENCHMARK_HASH_MISMATCH')
    cuts=profile['cut_frames']
    result={'schema':SCHEMA,'version':'0.7.12','mode':'SHADOW_ONLY',
        'case':case,'source_sha256':sha(video),'source_type':profile['source_type'],
        'profile_sha256':profile_digest,'historical_background_benchmark_sha256':sha(camera_source),
        'fps':float(profile['fps']),'frame_count':len(images),'cut_barriers':cuts,
        'segments':[],'beat_review':[],'actor_motion_evidence':wrist,
        'performance_judgment':{'completed_actions':'UNKNOWN','facial_expression_arc':'UNKNOWN',
          'natural_acting_quality':'UNKNOWN','microphone_ownership':'UNKNOWN',
          'note':'Technical image quality and partial pose/flow evidence cannot establish acting quality'},
        'cut_recommendation':{'status':'REVIEW_CANDIDATES_ONLY','cross_shot_continuity':'NOT_ASSESSED',
          'predecessor_footage':'NOT_SUPPLIED','human_approval':'REQUIRED'},
        'source_is_H3_visual_input_eligible':False,
        'authorizations':{k:False for k in DENIED},
        'limits':['No semantic performance scoring','Candidate rankings do not approve actual edits',
                  'Image-change includes camera movement','No automatically verified hand or microphone identity',
                  'No continuity prediction without independently verified predecessor state',
                  'Tested footage is not proof of arbitrary-video generalization']}
    for a,b in boundaries:
        item={'frame_range':[a,b], 'duration_s':round((b-a+1)/profile['fps'],4),
              'candidate_review_stills':triage(metrics,[a,b],profile['fps']),
              'cross_cut_motion_inference_allowed':False,'operator_review_required':True}
        if pose and a<=wrist['source_window'][0] and b>=wrist['source_window'][1]:
            item['actor_observation']='POSE_EVIDENCE_PARTIAL_WITH_VETO_NOT_GESTURE_CERTIFICATION'
        else:item['actor_observation']='UNKNOWN_NO_POSE_FOR_SEGMENT'
        
        if pose is not None:
            for still in item['candidate_review_stills']:
                if wrist.get('source_window',[0,-1])[0]<=still['frame']<=wrist.get('source_window',[0,-1])[1]:
                    still['actor_motion']='WITHHELD_PARTIAL_WRIST_EVIDENCE'
        result['segments'].append(item)
    # The beat boundaries come from pre-existing human baseline or older V0.7.10
    # temporal sampling profiles. They are NEVER inferred by this assessor.
    if case=='shot7':
        beats=[('opening',0,21),('interaction',42,63),('ending',84,105)]
        basis='HUMAN_STYLE_ARCHIVED_VISUAL_BASELINE_ONLY'
    else:
        beats=[(f'window_{i}',w['start'],w['end']) for i,w in enumerate(profile['windows'])]
        basis='PREEXISTING_V0710_SAMPLING_WINDOWS'
    for name,a,b in beats:
        if any(a<c<=b for c in cuts):raise EvidenceError('BEAT_CROSSES_CUT')
        choices=triage(metrics,[a,b],profile['fps'],max_choices=2)
        for still in choices:
            if pose is not None and wrist['source_window'][0]<=still['frame']<=wrist['source_window'][1]:
                still['actor_motion']='WITHHELD_PARTIAL_WRIST_EVIDENCE'
        result['beat_review'].append({'name':name,'frame_range':[a,b],
            'beat_boundary_provenance':basis,'review_stills':choices,
            'performance_interpretation':'WITHHELD','review_required':True})
    # Archived human baseline is *not* a training label or editor permission.
    if case=='shot7':
        result['editorial_context']={'source':'INDEPENDENT_HUMAN_STYLE_BASELINE_NOT_AUTOMATED',
          'context_only':'Presenter offers microphone during interaction; motion/holder not independently certified',
          'historical_working_edit':'Shot 7 F60 is a HUMAN-SELECTED working cut, not a benchmark label or automatically approved edit',
          'review_questions':['Are both actors readable at a candidate entry?','Is microphone continuity believable to a human?','Does the entry interrupt an expressive movement?']}
    elif case=='shadow':
        result['editorial_context']={'source':'ARCHIVED_CUT_EVIDENCE',
          'review_questions':['Is a candidate separated from the F101 barrier?','Do not infer a cross-cut gesture.']}
    else:
        result['editorial_context']={'source':'AUTHENTIC_REFERENCE_VIDEO_OBSERVATION_ONLY',
          'review_questions':['Does the frame retain readable face/identity?','Does apparent camera motion compromise technical stability?']}
    return result


def render_review_sheet(video,review,target):
    """Human triage sheet, not a cut approval or H3 conditioning asset."""
    refs=[]
    for seg_idx,seg in enumerate(review.get('beat_review') or review['segments']):
        for c in seg.get('review_stills',seg.get('candidate_review_stills',[])):
            refs.append((seg.get('name',f'S{seg_idx}'),c))
    if not refs:return
    cap=cv2.VideoCapture(str(video))
    thumbs=[]
    for seg_name,c in refs:
        cap.set(cv2.CAP_PROP_POS_FRAMES,c['frame'])
        ok,img=cap.read()
        if not ok:cap.release();raise EvidenceError('REVIEW_SHEET_DECODE_FAILURE')
        img=cv2.resize(img,(144,252),interpolation=cv2.INTER_AREA)
        t=np.full((293,144,3),27,np.uint8)
        t[38:290]=img
        cv2.putText(t,f"{seg_name[:10]} F{c['frame']}",(5,16),cv2.FONT_HERSHEY_SIMPLEX,.41,(250,250,250),1,cv2.LINE_AA)
        cv2.putText(t,f"TECH {c['review_priority_score']:.2f}",(5,32),cv2.FONT_HERSHEY_SIMPLEX,.38,(190,220,255),1,cv2.LINE_AA)
        cv2.putText(t,"REVIEW ONLY",(7,290),cv2.FONT_HERSHEY_SIMPLEX,.36,(255,255,255),1,cv2.LINE_AA)
        thumbs.append(t)
    cap.release()
    rows=int(math.ceil(len(thumbs)/4))
    canvas=np.full((rows*293+48,576,3),27,np.uint8)
    cv2.putText(canvas,f"{review['case'].upper()}  /  NO AUTOMATIC EDIT AUTHORITY",(10,28),
               cv2.FONT_HERSHEY_SIMPLEX,.48,(240,240,240),1,cv2.LINE_AA)
    for i,t in enumerate(thumbs):
        r,c=divmod(i,4);canvas[48+r*293:48+(r+1)*293,c*144:(c+1)*144]=t
    if not cv2.imwrite(str(target),canvas):raise EvidenceError('CONTACT_SHEET_WRITE_FAILED')

def run(output):
    output=Path(output)
    staging=output.with_name(output.name+'_staging')
    if output.exists() or output.is_symlink() or staging.exists() or staging.is_symlink():
        raise FileExistsError('REFUSE_OVERWRITE_EXISTING_OUTPUT_OR_STAGING')
    staging.mkdir(parents=True)
    try:
        cases={name:assess_case(name) for name in ('shot7','shadow','original')}
        # Separate independent source count from number of profiles that share shadow footage.
        sources={v['source_sha256'] for v in cases.values()}
        summary={'schema':'director-v0712-shadow-validation-summary','version':'0.7.12',
           'source_count':len(sources),'profile_count':len(cases),
           'fps_values':sorted({v['fps'] for v in cases.values()}),
           'cut_barrier_f101_preserved':cases['shadow']['cut_barriers']==[101],
           'pose_independent_video_generalization':'UNTESTED_ONLY_SHOT7_HAS_ACTUAL_POSE',
           'human_edit_comparison':'NOT_PERFORMED_WORKING_F60_IS_CONTEXT_NOT_MODEL_LABEL',
           'automatic_editing_authorized':False,'clean_chain_preserved':True,
           'result':'TECHNICAL_REVIEW_CANDIDATES_ONLY_NOT_AUTONOMOUS_CINEMATOGRAPHY'}
        for case,v in cases.items():
            (staging/f'{case}_editorial_review.json').write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
            video,_,_=source_profile(case)
            render_review_sheet(video,v,staging/f'{case}_review_contact.jpg')
        (staging/'validation_summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
        os.replace(staging,output)
    except Exception:
        # Preserve failure context and never overwrite existing staging.
        raise
    return summary

def run_external(video,expected_sha,cut_frames,output):
    """Unfamiliar video ingest. Provenance is UNVERIFIED even with matching hash."""
    video=Path(video)
    if not video.is_file() or sha(video)!=expected_sha:raise EvidenceError('EXTERNAL_SOURCE_HASH_MISMATCH')
    if video.stat().st_size>2_000_000_000:raise EvidenceError('EXTERNAL_INPUT_TOO_LARGE')
    cap=cv2.VideoCapture(str(video))
    if not cap.isOpened():raise EvidenceError('EXTERNAL_VIDEO_OPEN_FAILED')
    fps=float(cap.get(cv2.CAP_PROP_FPS));count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));cap.release()
    if not (8<=fps<=120 and 20<=count<=1200):raise EvidenceError('EXTERNAL_VIDEO_UNSUPPORTED_DURATION_FPS')
    if cut_frames!=sorted(set(cut_frames)) or any(type(i)!=int or not 0<i<count for i in cut_frames):
        raise EvidenceError('EXTERNAL_CUT_LIST_INVALID')
    prof={'fps':fps,'frame_count':count,'cut_frames':cut_frames,
          'source_type':'UNVERIFIED_EXTERNAL_OBSERVATION_ONLY'}
    images=video_frames(video,prof)
    metrics=frame_metrics(images)
    out={'schema':SCHEMA,'version':'0.7.12','mode':'SHADOW_ONLY',
      'source_sha256':expected_sha,'case':'external','source_type':prof['source_type'],
      'source_is_H3_visual_input_eligible':False,'cut_barriers':cut_frames,
      'cut_barriers_are_user_supplied_not_detector_verified':True,
      'fps':fps,'frame_count':count,'segments':[],
      'actor_motion_evidence':{'status':'NO_VERIFIED_POSE','gesture_authority':False},
      'performance_judgment':{'natural_acting_quality':'UNKNOWN','completed_actions':'UNKNOWN',
        'mic_holder':'UNKNOWN','facial_expression_arc':'UNKNOWN'},
      'cut_recommendation':{'status':'TECHNICAL_REVIEW_ONLY','human_approval':'REQUIRED'},
      'authorizations':{k:False for k in DENIED},
      'limits':['External content is NOT authentic source by default',
        'Cut barriers must be externally supplied; empty cut list does not prove continuous footage',
        'Sharpness, exposure and raw frame-change alone do not certify acting or cuts']}
    for a,b in segments(count,cut_frames):
        out['segments'].append({'frame_range':[a,b],
          'candidate_review_stills':triage(metrics,[a,b],fps),
          'actor_observation':'UNKNOWN','cross_cut_motion_inference_allowed':False})
    output=Path(output);staging=output.with_name(output.name+'_staging')
    if output.exists() or output.is_symlink() or staging.exists() or staging.is_symlink():
        raise FileExistsError('REFUSE_OVERWRITE_EXISTING_OUTPUT_OR_STAGING')
    staging.mkdir(parents=True)
    (staging/'external_editorial_review.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    render_review_sheet(video,out,staging/'external_review_contact.jpg')
    os.replace(staging,output)
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'results/editorial_review_v0712')
    p.add_argument('--external-video',type=Path);p.add_argument('--expected-sha256');p.add_argument('--cut-frames',type=int,nargs='*',default=[])
    a=p.parse_args()
    if a.external_video:
        if not a.expected_sha256: p.error('--expected-sha256 required for external video')
        print(json.dumps(run_external(a.external_video,a.expected_sha256,a.cut_frames,a.output),indent=2))
    else:print(json.dumps(run(a.output),indent=2))
if __name__=='__main__':main()
