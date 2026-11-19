#!/usr/bin/env python3
"""V0.7.34: V075 real capture intake -> V0732 read-only inspection -> V0733 autoedit.

NEVER picks random saved shots. User-declared source memberships and exact hashes in
case file required. A captured source is video EDIT MATERIAL ONLY, never H3 input.
All rendered outputs are review-only and never become approved by this program.
"""
from __future__ import annotations
import argparse, hashlib, html, json, os, re, shutil
from pathlib import Path
import director_autoedit_v0733 as ed
import director_idle_dispatcher_v075 as d75
import director_readiness_guard_v0732 as guard
import director_live_readiness_v0731 as bridge

VERSION='director-unified-savedshot-autoedit-review-v0734'
HEX24=re.compile(r'[0-9a-f]{24}\Z')

class IntakeError(ValueError):pass

def digest(p:Path)->str:
    return d75.sha(p)

def read_json(p):
    if p.is_symlink() or not p.is_file():raise IntakeError('MISSING_OR_SYMLINK_JSON')
    try:
        r=json.loads(p.read_text())
    except (OSError,ValueError,UnicodeError) as ex:raise IntakeError('INVALID_JSON') from ex
    if not isinstance(r,dict):raise IntakeError('JSON_NOT_OBJECT')
    return r

def restricted_source(source:str, root:Path)->Path:
    if not isinstance(source,str) or not Path(source).is_absolute():raise IntakeError('SOURCE_NOT_ABSOLUTE')
    if root.is_symlink() or not root.is_dir():raise IntakeError('UNSAFE_SOURCE_ROOT')
    base=root.resolve(strict=True)
    p=Path(source)
    try: parts=p.relative_to(base).parts
    except ValueError:raise IntakeError('VIDEO_OUTSIDE_TRUSTED_ROOT')
    if not parts or any(x in ('.','..') for x in parts):raise IntakeError('UNSAFE_SOURCE_PATH')
    cur=base
    for part in parts:
        cur=cur/part
        if cur.is_symlink():raise IntakeError('SOURCE_SYMLINK')
    if not p.is_file():raise IntakeError('MISSING_VIDEO')
    return p

def validate_case(case):
    if (case.get('schema')!='director-edit-case-v0733' or
        case.get('no_generated_video_visual_conditioning_for_h3') is not True or
        case.get('benchmark_not_in_case_by_design') is not True):
        raise IntakeError('INVALID_CASE_OR_CLEAN_CHAIN')
    if case.get('outgoing',{}).get('human_working_status')!='SHOT1_APPROVED_WORKING_NOT_FINAL' or case['outgoing'].get('retain_all_frames') is not True:
        raise IntakeError('OUTGOING_NOT_APPROVED_WORKING')
    for slot in ('outgoing','incoming'):
        s=case[slot]
        if s.get('source_class')!='H3_GENERATED_OBSERVATION_AND_EDITORIAL_ONLY':raise IntakeError('INVALID_SOURCE_CLASS')
        name=s.get('filename');hashv=s.get('sha256')
        if not isinstance(name,str) or Path(name).name!=name or not re.fullmatch(r'[0-9a-f]{64}',str(hashv)):
            raise IntakeError('INVALID_EXPECTED_SOURCE_IDENTITY')
    if case['outgoing']['sha256']==case['incoming']['sha256'] or case['outgoing']['filename']==case['incoming']['filename']:
        raise IntakeError('DUPLICATED_TIMELINE_SOURCE')


def find_jobs(case, saved_root:Path, source_root:Path, *, allow_synthetic_fixture=False):
    """Capture job is required; malformed duplicate or forged matches never authorize rendering."""
    jobs_dir=saved_root/'jobs'
    if saved_root.is_symlink() or jobs_dir.is_symlink() or not jobs_dir.is_dir():raise IntakeError('UNSAFE_SAVEDSHOT_ROOT')
    matches={k:[] for k in ('outgoing','incoming')}
    errors=[]
    expect={case[k]['filename']:k for k in matches}
    for p in sorted(jobs_dir.glob('*.json')):
        if p.is_symlink() or not p.is_file():
            errors.append('UNSAFE_JOB_ENTRY:'+p.name); continue
        # avoid ignoring malformed records which may relate to one of our source files
        try:
            job=read_json(p)
            declared=job.get('source_video_path')
            slot=expect.get(Path(declared).name) if isinstance(declared,str) else None
            if slot is None: continue
            if not HEX24.fullmatch(p.stem) or job.get('job_id')!=p.stem:raise IntakeError('INVALID_JOB_IDENTIFIER')
            if job.get('version')!='director-saved-shot-capture-0.6.6' or job.get('phase')!='PENDING_SHADOW_OBSERVATION':raise IntakeError('UNKNOWN_JOB_SCHEMA')
            if job.get('observation_authority')!='NONE' or any(job.get(k) is not v for k,v in d75.SAFETY.items()):raise IntakeError('UNSAFE_JOB_AUTHORITY')
            if job.get('source_class')!='H3_GENERATED_OBSERVATION_ONLY':raise IntakeError('UNEXPECTED_SOURCE_CLASS')
            if job.get('synthetic_demo_fixture_only') is True and not allow_synthetic_fixture:
                raise IntakeError('SYNTHETIC_FIXTURE_REFUSED_IN_LIVE_MODE')
            path=restricted_source(declared,source_root)
            if path.name!=case[slot]['filename'] or job.get('source_sha256')!=case[slot]['sha256']:
                raise IntakeError('EXPECTED_VIDEO_HASH_IDENTITY_MISMATCH')
            if digest(path)!=case[slot]['sha256']:raise IntakeError('VIDEO_HASH_MISMATCH')
            if type(job.get('video_size_bytes')) is not int or job['video_size_bytes']!=path.stat().st_size:raise IntakeError('VIDEO_SIZE_MISMATCH')
            meta=job.get('video_meta',{})
            if not isinstance(meta,dict) or meta.get('frames')!=124 or float(meta.get('fps',0))!=24 or meta.get('width')!=768 or meta.get('height')!=1344:
                raise IntakeError('CAPTURE_VIDEO_METADATA_MISMATCH')
            marker=guard.safe_marker(saved_root,p.stem)
            if marker['status']!='NONE':raise IntakeError('CAPTURE_FAILURE_MARKER_NEEDS_INSPECTION:'+marker['status'])
            summary=saved_root/'results'/p.stem/'director_postshot_v074.json'
            if summary.is_symlink():raise IntakeError('UNSAFE_POSTSHOT_SUMMARY')
            report_state='NOT_RUN_NO_MODEL_REQUIRED_FOR_EDIT'
            report_hash=None
            if summary.exists():
                rep=read_json(summary)
                require={'job_id':job['job_id'],'job_sha256':digest(p), 'source_sha256':digest(path),
                  'human_review_required':True, 'automatic_editing_authorized':False,
                  'continuity_handoff_authorized':False,'h3_visual_conditioning':False}
                if any((rep.get(k) is not v if type(v) is bool else rep.get(k)!=v) for k,v in require.items()):
                    raise IntakeError('INVALID_POSTSHOT_REPORT')
                report_state='EXISTS_HASH_BOUND_BUT_ARTISTIC_REVIEW_STILL_REQUIRED'
                report_hash=digest(summary)
            matches[slot].append({'path':path,'job_path':p,'job_id':p.stem,'job_sha256':digest(p),
                                  'source_sha256':digest(path),'report_state':report_state,'report_sha256':report_hash})
        except (ValueError,TypeError,KeyError,OverflowError,OSError) as exc:
            errors.append(p.name+':'+str(exc)[:130])
    if errors:raise IntakeError('CAPTURE_INVENTORY_REQUIRES_ATTENTION: '+'; '.join(errors[:6]))
    for slot,found in matches.items():
        if len(found)!=1:raise IntakeError('EXPECTED_EXACTLY_ONE_CAPTURE_FOR_'+slot.upper()+'_FOUND_'+str(len(found)))
    if matches['outgoing'][0]['job_id']==matches['incoming'][0]['job_id']:
        raise IntakeError('ONE_CAPTURE_CANNOT_BE_TWO_SHOTS')
    return {k:v[0] for k,v in matches.items()}


def serializable(job):
    return {k:str(v) if isinstance(v,Path) else v for k,v in job.items() if k!='path'}

def run(case_file:Path,saved_root:Path,source_root:Path,out:Path,*,benchmark=None,profiles_dir=None,queue_url=None,render=False,allow_synthetic_fixture=False):
    case=read_json(case_file);validate_case(case)
    out=Path(out).absolute()
    if out.exists() or out.is_symlink():raise FileExistsError('OUTPUT_ALREADY_EXISTS')
    for protected in (saved_root,source_root):
        if out.resolve().is_relative_to(Path(protected).resolve()):raise IntakeError('OUTPUT_INSIDE_CAPTURE_OR_MEDIA_ROOT')
    matches=find_jobs(case,saved_root,source_root,allow_synthetic_fixture=allow_synthetic_fixture)
    before={k:matches[k]['job_sha256'] for k in matches}
    if profiles_dir is not None:
        observed=guard.build(saved_root,source_root,profiles_dir,queue_url=queue_url)
        if any(observed.get(k) is not False for k in ('automatic_edit_authorized','continuity_authorized','generation_authorized')):
            raise IntakeError('READINESS_GUARD_UNSAFE_AUTHORIZATION')
        queue_status=observed['queue']['status']
        if render and queue_status in ('BUSY','UNKNOWN_UNREACHABLE','UNKNOWN_INVALID_SCHEMA'):
            raise IntakeError('COMFYUI_QUEUE_NOT_SAFE_FOR_RENDER')
        job_state={j['job_name']: {'readiness':j['readiness'],
                                   'classification':j['classification'],
                                   **({'reason':j['reason']} if 'reason' in j else {})}
                   for j in observed['job_inventory']}
        observed_digest=observed['evidence_digest']
    else:
        job_state={};observed_digest=None;queue_status='NOT_CHECKED_NO_GPU_ACTION'
    selected_state={k:job_state.get(matches[k]['job_path'].name,{'readiness':'NOT_INSPECTED'}) for k in matches}
    # Source-bound capture validation happens above, independently of the legacy
    # post-shot observation profile. Only a *specific* missing/ambiguous observer
    # profile can be nonfatal for an EDITORIAL-ONLY review, never for observation.
    # Any other guard refusal, including malformed report/hash/unsafe path, blocks
    # rendering. An unknown refusal also blocks: do not make a denied state 'safe'.
    observation_profile_gaps=[]
    guard_blockers=[]
    if profiles_dir is not None:
        for k,st in selected_state.items():
            if st.get('readiness') == 'REFUSED_INPUT' or st.get('classification') == 'REFUSED':
                if st.get('reason') == 'MISSING_OR_AMBIGUOUS_PROFILE':
                    observation_profile_gaps.append(k)
                else:
                    guard_blockers.append(f"{k}:{st.get('reason','UNEXPLAINED_GUARD_REFUSAL')}")
            elif st.get('readiness') in ('INSPECT_FAILURE_CONFLICT','MANUAL_RETRY_DECISION'):
                guard_blockers.append(f"{k}:{st['readiness']}")
            elif st.get('readiness') not in ('AWAITING_OPERATOR_ACTION','REVIEW_SHADOW_SUMMARY'):
                guard_blockers.append(f"{k}:UNKNOWN_GUARD_STATE_{st.get('readiness')}")
    if render and guard_blockers:
        raise IntakeError('GUARD_BLOCKED_REVIEW_RENDER: '+ '; '.join(guard_blockers))
    result={'schema':VERSION,'mode':'REVIEW_ONLY_NO_PRODUCTION_AUTHORITY',
            'project_case_hash':digest(case_file),'synthetic_fixture_demo':bool(allow_synthetic_fixture),'capture_binding':{k:serializable(v) for k,v in matches.items()},
            'capture_state':selected_state,
            'readiness_digest':observed_digest,'comfyui_queue':queue_status,
            'reference_frames_used_for_H3':False,'generated_footage_as_H3_conditioning':False,
            'working_shot1_unchanged':True,'shot2_working_approval':False,
            'machine_can_propose_edit':True,'machine_can_approve_edit':False,
            'authenticates_human_approval':False,
            'film_order':['outgoing','incoming'],
            'benchmark_not_used_for_ranking':True,
            'observer_profiles_missing_or_ambiguous':observation_profile_gaps,
            'guard_blockers':guard_blockers,
            'safe_next_action':('INSPECT_CAPTURE_GUARD_REFUSAL' if guard_blockers else
                                ('RENDER_REVIEW_ONLY_PROFILE_GAP' if observation_profile_gaps else
                                 ('RENDER_REVIEW_ONLY' if not render else 'HUMAN_REVIEW_REQUIRED'))),
            'production_director_changed':False,'director_memory_changed':False}
    stage=out.with_name(out.name+'_STAGING')
    if stage.exists() or stage.is_symlink():raise FileExistsError('STAGING_ALREADY_EXISTS')
    stage.mkdir(parents=True)
    try:
        if render:
            decision=ed.run(case_file,matches['outgoing']['path'],matches['incoming']['path'],stage/'edit',benchmark)
            result['rendered_edit']={'frame':decision['selection']['incoming_entry_frame'],
                       'verified_frames':decision['render_validation']['frames'],
                       'sha256':decision['render_validation']['sha256'],
                       'audible_source_correspondence':decision['render_validation']['audible_pcm_source_matches']}
        after=find_jobs(case,saved_root,source_root,allow_synthetic_fixture=allow_synthetic_fixture)
        if {k:after[k]['job_sha256'] for k in after}!=before or any(after[k]['source_sha256']!=matches[k]['source_sha256'] for k in after):
            raise IntakeError('INPUT_EVIDENCE_CHANGED_DURING_EDIT')
        if profiles_dir is not None:
            later=guard.build(saved_root,source_root,profiles_dir,queue_url=queue_url)
            if later['evidence_digest']!=observed_digest:raise IntakeError('GUARD_EVIDENCE_CHANGED_DURING_EDIT')
        (stage/'director_intake_v0734.json').write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n')
        link='<p><a href="edit/index.html">Watch automated edit and inspect ranking</a></p>' if render else '<p>No render requested. Use --render to create a review-only edit.</p>'
        page=f'<!doctype html><html><meta charset="utf-8"><title>Director Live Editorial Intake</title><body style="background:#141a22;color:#eef3f7;font:16px system-ui;max-width:850px;margin:50px auto"><h1>Director integrated live intake</h1><p>Two source-hash-matched saved-shot captures. No authority to accept edits or condition H3.</p>{link}<p>ComfyUI queue snapshot: {html.escape(queue_status)}</p><p>All video clips remain observational and editorial only.</p></body></html>'
        (stage/'index.html').write_text(page)
        (stage/'SHA256_OUTPUTS.json').write_text(json.dumps({p.name:digest(p) for p in sorted(stage.iterdir()) if p.is_file()},indent=2,sort_keys=True)+'\n')
        os.replace(stage,out)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    print('V0734_LIVE_CAPTURE_BOUND',*[matches[k]['job_id'] for k in ('outgoing','incoming')])
    print('V0734_REVIEW_ONLY', 'AUTO_EDIT_RENDERED' if render else 'INSPECTION_ONLY',out)
    return result

def cli():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--case',type=Path,required=True);a.add_argument('--saved-root',type=Path,required=True)
    a.add_argument('--source-root',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
    a.add_argument('--profiles-dir',type=Path);a.add_argument('--queue-url');a.add_argument('--benchmark',type=Path)
    a.add_argument('--render',action='store_true',help='Opt-in CPU edit render; no H3/Qwen')
    z=a.parse_args()
    run(z.case,z.saved_root,z.source_root,z.out,profiles_dir=z.profiles_dir,
        queue_url=z.queue_url,benchmark=z.benchmark,render=z.render)
if __name__=='__main__':cli()
