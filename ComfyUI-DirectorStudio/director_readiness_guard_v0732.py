#!/usr/bin/env python3
"""V0.7.32 offline handoff guard: reconcile V075 failure state with V0731 read-only inventory.

NEVER runs H3/Qwen, changes ComfyUI, grants any generation/edit/continuity privileges,
or treats generated footage as eligible H3 conditioning. Prior snapshots are unsigned.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
import director_live_readiness_v0731 as v0731

SCHEMA = 'director-readiness-guard-shadow-0.7.32'
HEX24 = re.compile(r'^[0-9a-f]{24}$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
SNAPSHOT_FIELDS = ('job_name','classification','source_sha256','profile_sha256',
                   'job_sha256','summary_sha256','failure_marker_sha256',
                   'readiness','reason','failure_state')
DECISIONS = ('REFUSED_INPUT','INSPECT_FAILURE_CONFLICT','MANUAL_RETRY_DECISION',
             'REVIEW_SHADOW_SUMMARY','AWAITING_OPERATOR_ACTION')

def digest(obj):
    payload=json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def safe_marker(root, name):
    saved=Path(root)
    base=saved/'dispatcher_v075'
    folder=base/'failures'
    for p in (base,folder):
        if p.is_symlink(): return {'status':'UNSAFE_FAILURE_DIRECTORY'}
        if p.exists() and not p.is_dir():return {'status':'UNSAFE_FAILURE_DIRECTORY'}
    if not HEX24.fullmatch(name):return {'status':'UNSAFE_JOB_IDENTIFIER'}
    path=folder/(name+'.json')
    if path.is_symlink():return {'status':'UNSAFE_FAILURE_MARKER'}
    if not path.exists():return {'status':'NONE'}
    if not path.is_file():return {'status':'UNSAFE_FAILURE_MARKER'}
    try:
        b=path.read_bytes()
        rec=json.loads(b)
        if (not isinstance(rec,dict) or
            rec.get('version')!='director-idle-postshot-dispatcher-0.7.5' or
            rec.get('job_id')!=name or not isinstance(rec.get('job_sha256'),str) or
            not HEX64.fullmatch(rec['job_sha256']) or
            rec.get('automatic_retry') is not False or
            rec.get('no_edit_no_handoff_no_h3') is not True or
            not isinstance(rec.get('reason'),str) or not rec['reason'].strip()):
            return {'status':'INVALID_FAILURE_MARKER','sha256':hashlib.sha256(b).hexdigest()}
        return {'status':'VALID_MARKER','job_sha256':rec['job_sha256'],
                'sha256':hashlib.sha256(b).hexdigest(), 'reason':rec['reason'][:160]}
    except (OSError,ValueError,TypeError,UnicodeError):
        return {'status':'INVALID_FAILURE_MARKER'}

def annotate(result,saved_root):
    obj=copy.deepcopy(result)
    if obj.get('schema')!=v0731.VERSION:raise ValueError('UNEXPECTED_V0731_SCHEMA')
    items=obj.get('job_inventory')
    if not isinstance(items,list):raise ValueError('INVALID_JOB_INVENTORY')
    for job in items:
        name=job.get('job_name','')
        if not isinstance(name,str):raise ValueError('INVALID_JOB_NAME')
        base=name[:-5] if name.endswith('.json') else ''
        marker=safe_marker(saved_root,base)
        job['failure_state']=marker['status']
        if marker.get('sha256'):job['failure_marker_sha256']=marker['sha256']
        if job.get('classification')=='REFUSED':
            job['readiness']='REFUSED_INPUT'
        elif marker['status'] not in ('NONE','VALID_MARKER'):
            job['readiness']='INSPECT_FAILURE_CONFLICT'
        elif marker['status']=='VALID_MARKER':
            if marker['job_sha256']!=job.get('job_sha256'):
                job['failure_state']='STALE_FAILURE_MARKER_FOR_DIFFERENT_JOB_BYTES'
                job['readiness']='INSPECT_FAILURE_CONFLICT'
            elif job.get('classification')=='SUMMARY_HASH_BOUND_SHADOW_REVIEW_ONLY':
                job['readiness']='INSPECT_FAILURE_CONFLICT'
                job['failure_state']='VALID_FAILURE_MARKER_WITH_VALID_SUMMARY'
            else:
                job['readiness']='MANUAL_RETRY_DECISION'
        elif job.get('classification')=='SUMMARY_HASH_BOUND_SHADOW_REVIEW_ONLY':
            job['readiness']='REVIEW_SHADOW_SUMMARY'
        else:
            job['readiness']='AWAITING_OPERATOR_ACTION'
        job['recommendation']='OPERATOR_REVIEW_ONLY_NO_AUTOMATIC_ACTION'
    obj['schema']=SCHEMA
    obj['source_bridge_schema']=v0731.VERSION
    obj['readiness_counts']={name:sum(j['readiness']==name for j in items) for name in DECISIONS}
    obj['job_evidence']=[{k:job.get(k) for k in SNAPSHOT_FIELDS if k in job} for job in sorted(items,key=lambda x:x['job_name'])]
    obj['evidence_digest']=digest(obj['job_evidence'])
    obj['no_production_authority']=True
    obj['no_memory_mutation']=True
    obj['all_review_states_are_administrative_only']=True
    obj['previous_snapshot_is_not_an_authentication_proof']=True
    return obj

def validate_previous(prior):
    if not isinstance(prior,dict) or prior.get('schema')!=SCHEMA:raise ValueError('INVALID_PREVIOUS_SNAPSHOT_SCHEMA')
    ledger=prior.get('job_evidence')
    if not isinstance(ledger,list) or not all(isinstance(x,dict) and isinstance(x.get('job_name'),str) for x in ledger):
        raise ValueError('INVALID_PREVIOUS_LEDGER')
    if prior.get('evidence_digest')!=digest(ledger):raise ValueError('PREVIOUS_SNAPSHOT_DIGEST_MISMATCH')
    if prior.get('no_production_authority') is not True:raise ValueError('PREVIOUS_SNAPSHOT_UNSAFE_FLAGS')
    names=[x['job_name'] for x in ledger]
    if len(set(names))!=len(names) or names!=sorted(names):raise ValueError('PREVIOUS_SNAPSHOT_DUPLICATE_OR_UNSORTED')
    return ledger

def compare(older,newer):
    old={r['job_name']:r for r in validate_previous(older)}
    cur={r['job_name']:r for r in newer['job_evidence']}
    entries=[]
    for name in sorted(old.keys()|cur.keys()):
        a,b=old.get(name),cur.get(name)
        if a is None:kind='NEW_JOB'
        elif b is None:kind='DISAPPEARED_JOB'
        elif a!=b:kind='EVIDENCE_CHANGED'
        else:continue
        diff=sorted(k for k in (a or {}).keys()|(b or {}).keys() if (a or {}).get(k)!=(b or {}).get(k))
        entries.append({'job_name':name,'change':kind,'changed_fields':diff,'manual_review_required':True})
    if digest(older.get('controller'))!=digest(newer.get('controller')):
        entries.append({'job_name':'__CONTROLLER_REVIEW__','change':'CONTROLLER_REVIEW_CHANGED',
                        'changed_fields':['controller_evidence'],'manual_review_required':True})
    return {'comparison_scope':'UNSIGNED_OFFLINE_SNAPSHOT_NOT_AUTHENTICATED',
            'old_evidence_digest':older['evidence_digest'],
            'new_evidence_digest':newer['evidence_digest'],
            'changes':entries,'changes_require_review':bool(entries),
            'automatic_state_promotion':False}

def build(saved_root,source_root,profiles_dir,queue_json=None,queue_url=None,review_index=None,prior=None):
    # Two independent reads to detect evidence changing during inspection. Queue is advisory and excluded.
    args=dict(queue_json=queue_json,queue_url=queue_url,review_index=review_index)
    first=annotate(v0731.build(saved_root,source_root,profiles_dir,**args),saved_root)
    second=annotate(v0731.build(saved_root,source_root,profiles_dir,**args),saved_root)
    if first['evidence_digest']!=second['evidence_digest']:
        raise ValueError('EVIDENCE_CHANGED_DURING_PREFLIGHT')
    if digest(first.get('controller'))!=digest(second.get('controller')):
        raise ValueError('CONTROLLER_REVIEW_CHANGED_DURING_PREFLIGHT')
    second['double_checked_evidence_digest_matches']=True
    second['double_checked_controller_review_matches']=True
    if prior is not None:second['snapshot_comparison']=compare(prior,second)
    return second

def create(out,saved_root,source_root,profiles_dir,**kwargs):
    target=Path(out)
    if target.exists() or target.is_symlink():raise FileExistsError('NO_OVERWRITE')
    if not target.is_absolute():target=target.absolute()
    for inp in (saved_root,source_root,profiles_dir):
        if target.resolve().is_relative_to(Path(inp).resolve()):
            raise ValueError('OUTPUT_INSIDE_PROTECTED_INPUT')
    prior_path=kwargs.pop('previous_snapshot',None)
    prior=None
    if prior_path is not None:
        prior=v0731.load_json(Path(prior_path))
        validate_previous(prior)
    report=build(saved_root,source_root,profiles_dir,prior=prior,**kwargs)
    target.mkdir(parents=True,exist_ok=False)
    data=target/'handoff_guard_v0732.json'
    data.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n')
    (target/'SHA256_OUTPUTS.json').write_text(json.dumps({data.name:v0731.filehash(data)},indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--saved-root',required=True,type=Path)
    p.add_argument('--source-root',required=True,type=Path)
    p.add_argument('--profiles-dir',required=True,type=Path)
    p.add_argument('--queue-json',type=Path)
    p.add_argument('--queue-url')
    p.add_argument('--review-index',type=Path)
    p.add_argument('--previous-snapshot',type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    r=create(a.out,a.saved_root,a.source_root,a.profiles_dir,queue_json=a.queue_json,
             queue_url=a.queue_url,review_index=a.review_index,previous_snapshot=a.previous_snapshot)
    print('V0732_SHADOW_ONLY',r['readiness_counts'],'DRIFT',
          len(r.get('snapshot_comparison',{}).get('changes',[])),'NO_AUTOMATIC_ACTION')
if __name__=='__main__':main()
