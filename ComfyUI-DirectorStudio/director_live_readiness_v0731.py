#!/usr/bin/env python3
"""Director V0.7.31: non-executing V075 saved-shot/readiness bridge.

All findings are administrative, never authorizations. No queue manipulation, GPU
execution, ComfyUI requests except optional read-only /queue, memory or media writes.
"""
from __future__ import annotations
import argparse, hashlib, html, json, os, re, urllib.parse, urllib.request
from pathlib import Path
import director_idle_dispatcher_v075 as v075
import director_preflight_v0730 as v0730

VERSION='director-live-readiness-shadow-0.7.31'
SHA=re.compile(r'^[0-9a-f]{64}$')
FALSE=['automatic_editing_authorized','continuity_handoff_authorized','director_memory_changed',
       'production_director_changed','h3_generation','h3_visual_conditioning','scene_dna_verified']

def filehash(p):return v075.sha(p)
def load_json(p):
    if p.is_symlink() or not p.is_file():raise ValueError('Unsafe/missing evidence file')
    return json.loads(p.read_text(encoding='utf-8'))

def restricted_file(source,root):
    """Reject symlink files and all symlink ancestors within the output root."""
    path=Path(source)
    if not path.is_absolute():raise ValueError('SOURCE_NOT_ABSOLUTE')
    root=Path(root).resolve(strict=True)
    if not root.is_dir():raise ValueError('OUTPUT_ROOT_NOT_DIRECTORY')
    try: rel=path.relative_to(root)
    except ValueError:raise ValueError('SOURCE_OUTSIDE_TRUSTED_OUTPUT_ROOT')
    if not rel.parts or any(x in ('.','..') for x in rel.parts):raise ValueError('SOURCE_TRAVERSAL')
    at=root
    for part in rel.parts:
        at=at/part
        if at.is_symlink():raise ValueError('SOURCE_SYMLINK')
    if not path.is_file():raise ValueError('SOURCE_MISSING')
    return path

def inspect_report(report,job,job_path,profile,video):
    if report.is_symlink():raise ValueError('REPORT_SYMLINK')
    if not report.exists():return 'NO_V074_SUMMARY',None
    data=load_json(report)
    if not isinstance(data,dict):raise ValueError('V074_REPORT_NOT_OBJECT')
    required={
      'version':'director-unified-postshot-shadow-0.7.4',
      'job_id':job['job_id'], 'job_sha256':filehash(job_path),
      'source_sha256':filehash(video),'profile_sha256':filehash(profile),
      'human_review_required':True,'generated_video_never_H3_visual_conditioning':True,
      'framing_predictions_quarantined_globally':True,
      'microphone_holder_target_and_action_not_verified':True,
    }
    for k,v in required.items():
        # `1 == True` in Python, but JSON number 1 must not impersonate
        # an explicitly asserted safety boolean in a post-shot report.
        ok = (data.get(k) is v) if type(v) is bool else (data.get(k) == v)
        if not ok:raise ValueError('V074_MISMATCH_'+k)
    if data.get('source_video_path')!=str(video):raise ValueError('V074_MISMATCH_SOURCE_PATH')
    if any(data.get(k) is not False for k in FALSE):raise ValueError('V074_UNSAFE_AUTHORIZATION_FLAG')
    if not isinstance(data.get('candidate_cut_barriers'),list):raise ValueError('V074_MISSING_CUT_BARRIERS')
    for c in data['candidate_cut_barriers']:
        if not isinstance(c,dict):raise ValueError('V074_INVALID_CUT_BARRIER_ENTRY')
        if type(c.get('frame')) is not int or c['frame']<1 or c.get('continuous_motion_inference_blocked') is not True:
            raise ValueError('V074_UNSAFE_CUT_BARRIER')
    # Report fields are claims; actual observation sidecar content is not re-certified here.
    return 'SUMMARY_HASH_BOUND_SHADOW_REVIEW_ONLY',filehash(report)

def inspect_job(job_path,profiles, saved_root, source_root):
    job_path=Path(job_path)
    item={'job_name':job_path.name,'classification':'REFUSED',
          'review_action':'OPERATOR_INSPECT', 'h3_generation_allowed':False,
          'auto_edit_allowed':False,'continuity_authorized':False}
    try:
        if job_path.is_symlink() or not re.fullmatch(r'[a-f0-9]{24}\.json',job_path.name):raise ValueError('UNSAFE_JOB_ENTRY')
        job=load_json(job_path)
        if not isinstance(job,dict):raise ValueError('CAPTURE_JOB_NOT_OBJECT')
        if job.get('job_id')!=job_path.stem or job.get('version')!='director-saved-shot-capture-0.6.6' or job.get('phase')!='PENDING_SHADOW_OBSERVATION':
            raise ValueError('UNRECOGNIZED_CAPTURE_JOB')
        if job.get('observation_authority')!='NONE' or any(job.get(k) is not v for k,v in v075.SAFETY.items()):
            raise ValueError('UNSAFE_CAPTURE_AUTHORITY')
        src=job.get('source_video_path')
        if not isinstance(src,str):raise ValueError('SOURCE_NOT_SET')
        # Reject unsafe source locations before attempting profile matching.
        vid=restricted_file(src,source_root)
        match=[p for p in profiles if vid.name.startswith(p['prefix'])]
        if len(match)!=1:raise ValueError('MISSING_OR_AMBIGUOUS_PROFILE')
        profile=match[0]['path']
        if not isinstance(job.get('source_sha256'),str) or not SHA.fullmatch(job['source_sha256']):
            raise ValueError('INVALID_SOURCE_SHA')
        if filehash(vid)!=job['source_sha256']:raise ValueError('SOURCE_SHA_CHANGED')
        record_dir=Path(saved_root)/'results'/job_path.stem
        if record_dir.is_symlink():raise ValueError('RESULTS_DIRECTORY_SYMLINK')
        report=record_dir/'director_postshot_v074.json'
        summary_state,summary_sha=inspect_report(report,job,job_path,profile,vid)
        item.update(classification=summary_state,source_sha256=job['source_sha256'],
                    profile_id=match[0]['id'],profile_sha256=filehash(profile),
                    job_sha256=filehash(job_path),summary_sha256=summary_sha,
                    declared_cut_barriers_authoritative_for_action=False,
                    review_action='REVIEW_EXISTING_SHADOW_SUMMARY' if summary_sha else 'OBSERVATION_NOT_YET_COMPLETE')
    except (ValueError,KeyError,TypeError,UnicodeError,json.JSONDecodeError,OSError) as exc:
        item['reason']=str(exc)[:140]
    return item

def queue_snapshot(queue_json=None,queue_url=None):
    if queue_json and queue_url:raise ValueError('QUEUE_INPUT_AMBIGUOUS')
    if queue_json:
        data=load_json(Path(queue_json));origin='SNAPSHOT_FILE_NOT_LIVE'
    elif queue_url:
        # Read only loopback /queue; no arbitrary URL fetch.
        u=urllib.parse.urlsplit(queue_url)
        if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost') or u.path!='/queue' or u.query or u.fragment or u.username or u.password:
            raise ValueError('QUEUE_URL_NOT_LOCAL_COMFYUI_ENDPOINT')
        try:
            with urllib.request.urlopen(queue_url,timeout=4) as r:data=json.load(r)
            origin='LIVE_READ_ONLY_QUEUE_SNAPSHOT'
        except Exception:
            return {'status':'UNKNOWN_UNREACHABLE','origin':'LIVE_READ_ONLY_QUEUE_SNAPSHOT','gpu_admission_authorized':False}
    else:return {'status':'NOT_QUERIED','origin':'NONE','gpu_admission_authorized':False}
    if not isinstance(data,dict) or type(data.get('queue_running')) is not list or type(data.get('queue_pending')) is not list:
        return {'status':'UNKNOWN_INVALID_SCHEMA','origin':origin,'gpu_admission_authorized':False}
    return {'status':'BUSY' if data['queue_running'] or data['queue_pending'] else 'IDLE_SNAPSHOT_ONLY',
            'origin':origin,'gpu_admission_authorized':False}

def build(saved_root,source_root,profile_dir,queue_json=None,queue_url=None,review_index=None):
    saved_root=Path(saved_root)
    if saved_root.is_symlink():raise ValueError('SAVED_ROOT_SYMLINK')
    jobs=saved_root/'jobs';results=saved_root/'results'
    if not jobs.is_dir() or jobs.is_symlink() or not results.is_dir() or results.is_symlink():
        raise ValueError('SAVED_SHOT_QUEUE_DIRECTORY_MISSING_OR_UNSAFE')
    profiles=v075.profile_inventory(profile_dir)
    evidence=[]
    for j in sorted(jobs.glob('*.json')):evidence.append(inspect_job(j,profiles,saved_root,source_root))
    queue=queue_snapshot(queue_json,queue_url)
    result={'schema':VERSION,'mode':'SHADOW_READ_ONLY', 'operational_baseline':'V0.7.5_UNMODIFIED',
        'queue':queue,'saved_job_count':len(evidence),'job_inventory':evidence,
        'verified_summary_candidates':sum(x['classification']=='SUMMARY_HASH_BOUND_SHADOW_REVIEW_ONLY' for x in evidence),
        'no_new_h3_visual_inputs':True,'automatic_edit_authorized':False,'continuity_authorized':False,
        'generation_authorized':False,'source_hash_match_does_not_prove_authentic_origin':True,
        'discovery_is_not_a_runtime_gpu_lock':True,'pending_jobs_are_not_commands':True,
        'new_shot_is_not_a_verified_scene_member':True}
    if review_index is not None:
        board=v0730.build(Path(review_index))
        if not board['not_executable'] or any(board['gates'].values()):raise ValueError('UNSAFE_V0730_BOARD')
        result['controller']={'scene_id':board['scene_id'],'shot_count':board['shot_count'],
          'first_review_task':board['first_review_task'],'validated_controller_digest':board['validated_controller_digest'],
          'automatic_action_allowed':False}
        result['bridge_scope']='CO_PRESENTATION_ONLY_NO_IMPLICIT_SHOT_MEMBERSHIP'
    return result

def create(out,saved_root,source_root,profile_dir,**kw):
    out=Path(out)
    if out.exists() or out.is_symlink():raise FileExistsError('NO_OVERWRITE')
    # Avoid accidental writes inside protected input trees.
    for inp in (saved_root,source_root,profile_dir):
        if out.resolve().is_relative_to(Path(inp).resolve()):raise ValueError('OUTPUT_INSIDE_PROTECTED_INPUT')
    result=build(saved_root,source_root,profile_dir,**kw)
    out.mkdir(parents=True,exist_ok=False)
    target=out/'live_readiness_v0731.json'
    target.write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    (out/'SHA256_OUTPUTS.json').write_text(json.dumps({target.name:filehash(target)},indent=2)+'\n')
    return result

def cli():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--saved-root',type=Path,required=True)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--profiles-dir',type=Path,required=True)
    p.add_argument('--review-index',type=Path)
    p.add_argument('--queue-json',type=Path)
    p.add_argument('--queue-url')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    r=create(a.out,a.saved_root,a.source_root,a.profiles_dir,
       queue_json=a.queue_json,queue_url=a.queue_url,review_index=a.review_index)
    print('V0731_SHADOW_READINESS',r['saved_job_count'],'jobs',r['verified_summary_candidates'],
          'hash-bound summaries; queue',r['queue']['status'],'AUTOMATIC_ACTION_FALSE')
if __name__=='__main__':cli()
