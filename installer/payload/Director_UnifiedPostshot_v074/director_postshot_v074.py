#!/usr/bin/env python3
"""Director 0.7.4: one-command, opt-in, shadow-only post-shot observation.

Works with existing V0.6.6 saved-shot capture, V0.7.2.2 profiled Qwen observer
and V0.7.3 Scene Context builder. Never modifies H3, ComfyUI, video, source
jobs, original reports, Director memory, the legacy worker or its done markers.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

VERSION='director-unified-postshot-shadow-0.7.4'
DEFAULT_QUEUE=Path('/workspace/DirectorSavedShotQueueV066')
DEFAULT_PROFILER=Path('/workspace/DirectorProfiledV0722/profiled_observer_v0722.py')
DEFAULT_CONTEXT=Path('/workspace/DirectorSceneContextV073/scene_context_v073.py')
DEFAULT_PROFILE=Path('/workspace/DirectorProfiledV0722/profiles/hallway-v1.json')
SAFE_FLAGS={'automatic_editing_authorized':False,
            'continuity_handoff_authorized':False,
            'h3_visual_conditioning':False,
            'h3_generation':False,
            'director_memory_changed':False,
            'production_director_changed':False}
HEX24=re.compile(r'[0-9a-f]{24}\Z')


def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode('utf-8')
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def load_py(path,name):
    path=Path(path)
    if not path.is_file() or path.is_symlink():raise ValueError('Missing or unsafe required dependency: '+str(path))
    spec=importlib.util.spec_from_file_location(name,str(path))
    if not spec or not spec.loader:raise RuntimeError('Cannot import dependency '+str(path))
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj;spec.loader.exec_module(obj)
    return obj


def dependencies(profiler=DEFAULT_PROFILER,context=DEFAULT_CONTEXT):
    p=load_py(profiler,'director_postshot_profiled_v0722')
    c=load_py(context,'director_postshot_context_v073')
    if p.VERSION!='director-profiled-rolebinary-shadow-0.7.2.2' or c.VERSION!='director-scene-context-shadow-0.7.3':
        raise ValueError('Unsupported observer or Scene Context version')
    return p,c


def job_file(root,job):
    if not isinstance(job,str) or not HEX24.fullmatch(job):raise ValueError('Invalid job ID')
    path=Path(root)/'jobs'/(job+'.json')
    if path.is_symlink() or not path.is_file():raise FileNotFoundError('Saved-shot job missing: '+str(path))
    return path


def select_latest(root,profile):
    # File metadata selection is not a claim that source provenance is verified.
    pf=json.loads(Path(profile).read_text())
    prefix=pf['video_name_prefix']
    jobs=Path(root)/'jobs'
    matches=[]
    for p in jobs.glob('*.json'):
        if p.is_symlink() or not HEX24.fullmatch(p.stem):continue
        try:
            d=json.loads(p.read_text())
            if (d.get('job_id')!=p.stem or
                not Path(str(d.get('source_video_path',''))).name.startswith(prefix)):
                continue
            ready=(Path(root)/'results'/p.stem/'director_postshot_v074.json').exists()
            matches.append((ready,-p.stat().st_mtime_ns,p.stem))
        except (OSError,ValueError,TypeError):continue
    if not matches:raise FileNotFoundError('No saved jobs match the chosen profile; verify capture first')
    matches.sort()
    return matches[0][2]


def guard(report):
    for field,expected in SAFE_FLAGS.items():
        if report.get(field) is not expected:
            raise ValueError('Missing/unsafe authority guard: '+field)
    if report.get('human_review_required') is not True or report.get('scene_dna_verified') is not False:
        raise ValueError('Scene Context claims excessive verification')
    if report.get('framing_predictions_quarantined_globally') is not True:
        raise ValueError('Framing quarantine missing')
    if report.get('source_video_hash_reverified') is not True:
        raise ValueError('Source video has not been reverified')
    if report.get('observer_claims_not_ground_truth') is not True:
        raise ValueError('Observer claims improperly promoted')
    for b in report.get('cut_barriers',[]):
        if b.get('continuous_motion_inference_blocked') is not True:
            raise ValueError('Cut barrier authority weakened')
    for s in report.get('segments',[]):
        if s.get('framing_authority')!='NONE' or s.get('motion_between_stills_not_verified') is not True:
            raise ValueError('Unsafe segment authority')
        if s.get('endpoint_candidate',{}).get('model_pair_only_not_ground_truth') is not True:
            raise ValueError('Endpoint candidate claims excessive certainty')


def verify_binding(plan,context_mod,review_file,bundle_file):
    # A manifest-valid bundle is necessary but insufficient: also bind to job,
    # profile, plan, current source video and a deterministic candidate context.
    report,report_sha,bundle_sha=context_mod.load_bundle(bundle_file)
    context_mod.validate_report(report)
    record=plan['record']
    for key,value in {
        'job_id':record['job_id'],'job_sha256':plan['job_sha256'],
        'source_sha256':record['source_sha256'],'source_video_path':plan['source'],
        'profile_id':plan['profile']['project_id'],'profile_sha256':plan['profile_sha256'],
        'video_meta':record['video_meta'],'source_class':record['source_class'],
    }.items():
        if report.get(key)!=value:raise ValueError('Profiled report/source mismatch: '+key)
    if [int(c['frame_estimate']) for c in report['candidate_cuts']]!=[int(c['frame_estimate']) for c in plan['cut_candidates']]:
        raise ValueError('Candidate cuts changed since Qwen run')
    if [f for s in report['segments'] for f in s['samples']]!=plan['samples']:
        raise ValueError('Scene sample plan changed since Qwen run')
    if not review_file.is_file() or review_file.is_symlink():raise ValueError('Missing/unsafe profiled review JSON')
    if sha(review_file)!=report_sha:raise ValueError('Published review and manifest disagree')
    expected=context_mod.context(report,report_sha,bundle_sha)
    expected['source_video_hash_reverified']=True
    guard(expected)
    if sha(plan['source'])!=record['source_sha256']:
        raise ValueError('Video changed during review')
    return expected


def verify_inputs(plan,context_mod,review_file,bundle_file,context_file):
    expected=verify_binding(plan,context_mod,review_file,bundle_file)
    if not context_file.is_file() or context_file.is_symlink() or context_file.read_bytes()!=context_mod.canonical(expected):
        raise ValueError('Scene Context missing or not identical to provenance-gated expectation')
    return expected


def make_summary(plan,ctx,review,context_file,bundle):
    guard(ctx)
    segment_views=[]
    for s in ctx['segments']:
        segment_views.append({
            'frames':s['candidate_bounds'],
            'sample_agreement_candidates':s['sample_agreement_candidates'],
            'endpoint_candidate':s['endpoint_candidate'],
            'framing_authority':'NONE',
            'motion_between_stills_not_verified':True,
            'segment_continuity_verified':False,
        })
    return {'version':VERSION,'phase':'OPERATOR_INITIATED_POSTSHOT_SHADOW_REVIEW',
            'job_id':plan['record']['job_id'],
            'source_video_path':plan['source'],
            'source_sha256':plan['record']['source_sha256'],
            'source_class':plan['record']['source_class'],
            'profile_id':plan['profile']['project_id'],
            'profile_sha256':plan['profile_sha256'],
            'job_sha256':plan['job_sha256'],
            'observer_version':'director-profiled-rolebinary-shadow-0.7.2.2',
            'observer_bundle_sha256':sha(bundle),
            'observer_review_sha256':sha(review),
            'scene_context_version':'director-scene-context-shadow-0.7.3',
            'scene_context_sha256':sha(context_file),
            'observed_candidate_segments':segment_views,
            'candidate_cut_barriers':ctx['cut_barriers'],
            'framing_predictions_quarantined_globally':True,
            'microphone_holder_target_and_action_not_verified':True,
            'operator_must_review_visual_evidence':True,
            'generated_video_never_H3_visual_conditioning':True,
            'scene_dna_verified':False,
            'human_review_required':True,
            **SAFE_FLAGS}


def publish_identical(path,data):
    path=Path(path);b=canonical(data)
    if path.is_symlink():raise FileExistsError('Unsafe output symlink')
    if path.exists():
        if not path.is_file() or path.read_bytes()!=b:raise FileExistsError('Existing summary differs, refusing overwrite: '+str(path))
        print('V074_SUMMARY_ALREADY_IDENTICAL',path,flush=True)
        return False
    with path.open('xb') as f:f.write(b)
    print('V074_SUMMARY_WRITTEN',path,flush=True)
    return True


def run_job(job_id,profile,*,root=DEFAULT_QUEUE,profiler=None,ctx_module=None,run_gpu=False):
    root=Path(root)
    profiler,ctx_module=(profiler,ctx_module) if profiler and ctx_module else dependencies()
    job=job_file(root,job_id)
    plan=profiler.plan(job,profile)
    result_root=root/'results'/job_id
    review=result_root/'scene_review_v0722.json'
    bundle=result_root/'scene_review_v0722_bundle.zip'
    scene=result_root/'scene_context_v073.json'
    final=result_root/'director_postshot_v074.json'
    if any(x.is_symlink() for x in (result_root,review,bundle,scene,final)):
        raise ValueError('Unexpected symbolic link in result paths')
    have_review=review.is_file() and bundle.is_file()
    if review.exists()!=bundle.exists():raise FileExistsError('Incomplete published review pair; inspect before retry')
    print('V074_JOB',job_id,'PROFILE',plan['profile']['project_id'],flush=True)
    print('V074_SOURCE',plan['source'],flush=True)
    print('V074_CUT_CANDIDATES',[x['frame_estimate'] for x in plan['cut_candidates']],flush=True)
    print('V074_SAMPLES',plan['samples'],flush=True)
    if not run_gpu:
        print('V074_PREFLIGHT_ONLY','PROFILED_REVIEW_PRESENT' if have_review else 'QWEN_WOULD_RUN',flush=True)
        print('V074_SCENE_CONTEXT_PRESENT',scene.is_file(),flush=True)
        print('V074_NO_FILES_WRITTEN',flush=True)
        return None
    if not have_review:
        # V0.7.2.2 owns its own idle checks, GPU handoff and error staging.
        profiler.run(job,profile,run_gpu=True)
    else:
        print('V074_REUSE_EXISTING_PROFILED_REPORT',bundle,flush=True)
    # Validate independently before generating Scene Context.
    report,report_sha,bundle_sha=ctx_module.load_bundle(bundle)
    ctx_module.validate_report(report)
    # Binding checks happen BEFORE any Scene Context is written.
    verify_binding(plan,ctx_module,review,bundle)
    if not scene.is_file():
        ctx_module.run(bundle,scene,video=plan['source'])
    else:
        print('V074_REUSE_EXISTING_SCENE_CONTEXT',scene,flush=True)
    context=verify_inputs(plan,ctx_module,review,bundle,scene)
    summary=make_summary(plan,context,review,scene,bundle)
    publish_identical(final,summary)
    print('V074_SHADOW_COMPLETE',final,flush=True)
    print('V074_NO_EDIT_NO_HANDOFF_NO_H3_NO_MEMORY_CHANGE',flush=True)
    return final


def cli():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--job-id')
    group.add_argument('--latest',action='store_true',help='Most recent unfinished saved shot matching profile')
    p.add_argument('--profile',type=Path,default=DEFAULT_PROFILE)
    p.add_argument('--root',type=Path,default=DEFAULT_QUEUE)
    p.add_argument('--profiler',type=Path,default=DEFAULT_PROFILER)
    p.add_argument('--context',type=Path,default=DEFAULT_CONTEXT)
    p.add_argument('--run',action='store_true',help='Explicitly allow Qwen if missing and publish shadow-only context')
    a=p.parse_args()
    profiler,context=dependencies(a.profiler,a.context)
    profiler.validate_profile(json.loads(a.profile.read_text()))
    job=select_latest(a.root,a.profile) if a.latest else a.job_id
    out=run_job(job,a.profile,root=a.root,profiler=profiler,ctx_module=context,run_gpu=a.run)
    if out:print('V074_REVIEW_FILE',out)

if __name__=='__main__':cli()
