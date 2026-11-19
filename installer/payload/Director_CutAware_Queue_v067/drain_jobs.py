#!/usr/bin/env python3
"""Observe captured H3 outputs after ComfyUI queue is idle; shadow-only, never edit or prompt."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,sys,urllib.request,subprocess,time,uuid,datetime,os
from pathlib import Path

ROOT=Path('/workspace/DirectorSavedShotQueueV066')
JOBS=ROOT/'jobs'
RESULTS=ROOT/'results'
DONE=ROOT/'completed'
OBSERVER=Path('/workspace/ComfyUI/custom_nodes/ComfyUI-Director-SemanticObserver-V063/semantic_observer.py')
OUTPUT=Path('/workspace/ComfyUI/output')


def require_idle():
    with urllib.request.urlopen('http://127.0.0.1:18188/queue',timeout=10) as response:
        q=json.load(response)
    if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
        raise RuntimeError('Unknown ComfyUI queue state; refusing to run Qwen')
    if q['queue_running'] or q['queue_pending']:
        raise RuntimeError('Generation queue is active. Drain cancelled before Qwen starts')


def observer_module(path=OBSERVER):
    if not Path(path).is_file():raise FileNotFoundError('V0.6.5 semantic observer not installed: '+str(path))
    spec=importlib.util.spec_from_file_location('director_v065_observer_for_v066',str(path))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    if getattr(mod,'VERSION',None)!='director-geometry-semantic-observer-0.6.5':
        raise RuntimeError('Expected geometry-first V0.6.5 observer; installed version differs')
    return mod


def job_record(path,video_root=OUTPUT):
    rec=json.loads(Path(path).read_text())
    if rec.get('version')!='director-saved-shot-capture-0.6.6' or rec.get('phase')!='PENDING_SHADOW_OBSERVATION':
        raise ValueError('Unsupported job schema')
    if (rec.get('observation_authority')!='NONE' or
        rec.get('continuity_handoff_authorized') is not False or
        rec.get('automatic_editing_authorized') is not False or
        rec.get('generated_footage_for_H3_visual_conditioning') is not False or
        rec.get('director_memory_changed') is not False):
        raise ValueError('Job claims unsupported authority')
    src=Path(rec['source_video_path']).resolve(strict=True)
    if not src.is_relative_to(Path(video_root).resolve(strict=True)):
        raise ValueError('Job video escapes ComfyUI output directory')
    token={k:rec[k] for k in ('source_video_path','source_sha256','source_class','samples')}
    key=hashlib.sha256(json.dumps(token,sort_keys=True).encode()).hexdigest()[:24]
    if key!=rec.get('job_id') or Path(path).name!=key+'.json':
        raise ValueError('Job ID provenance mismatch')
    if not (2<=rec['samples']<=24):raise ValueError('Invalid sampling count')
    if rec['source_class'] not in ('H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY','UNKNOWN_ORIGIN'):
        raise ValueError('Invalid source class')
    return rec,src


def gpu_free_mib():
    cp=subprocess.run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],
                      text=True,capture_output=True,timeout=15)
    if cp.returncode:raise RuntimeError('Unable to inspect GPU memory: '+cp.stderr[-300:])
    return int(cp.stdout.strip().splitlines()[0].strip())


def system_available_mib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1])//1024
    raise RuntimeError('Cannot determine available system RAM')


def ensure_qwen_resources(*, gpu_target_mib=24576, ram_target_mib=20480, sleep=time.sleep):
    """Auto handoff from cached H3 models, ONLY if queue confirmed idle.

    Never kills a process, restarts ComfyUI or unloads models when enough
    free GPU memory already exists. No guarantee against a new job race.
    """
    require_idle()
    free=gpu_free_mib();ram=system_available_mib()
    print('V067_RESOURCE_PRECHECK gpu_free_mib=%d ram_available_mib=%d'%(free,ram),flush=True)
    if free<gpu_target_mib:
        # Fail closed on unknown queue state and recheck just before /free.
        require_idle()
        request=urllib.request.Request('http://127.0.0.1:18188/free',
            data=json.dumps({'unload_models':True,'free_memory':True}).encode(),
            headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request,timeout=30) as response:
            if response.status!=200:raise RuntimeError('ComfyUI unload returned '+str(response.status))
        print('V067_COMFY_IDLE_MODELS_UNLOAD_REQUESTED',flush=True)
        for _ in range(8):
            sleep(2)
            require_idle()
            free=gpu_free_mib();ram=system_available_mib()
            if free>=gpu_target_mib:break
    require_idle()
    if free<gpu_target_mib or ram<ram_target_mib:
        raise RuntimeError('Insufficient free resources for Qwen: gpu_free=%d MiB ram_available=%d MiB; no worker started'%(free,ram))
    print('V067_QWEN_RESOURCE_READY gpu_free_mib=%d ram_available_mib=%d'%(free,ram),flush=True)


def archive_partial_result(dest, job_id):
    """Preserve failed worker artifacts; refuse completed or suspicious results."""
    dest=Path(dest)
    if not dest.exists() and not dest.is_symlink():return None
    if dest.is_symlink() or not dest.is_dir():raise RuntimeError('Unexpected result path; inspect manually: '+str(dest))
    if (dest/'semantic_observation.json').exists():
        raise FileExistsError('Observation report already exists; manual review required: '+str(dest))
    allowed={'request.json','worker.log'}
    names={x.name for x in dest.iterdir()}
    if names-allowed or not names or any(x.is_symlink() for x in dest.iterdir()):
        raise RuntimeError('Unrecognized partial result; manual review required: '+str(dest))
    archive=dest.parent.parent/'failed_attempts'
    archive.mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=archive/(job_id+'_'+stamp+'_'+uuid.uuid4().hex[:8])
    dest.rename(target)
    print('V067_FAILED_ATTEMPT_ARCHIVED',target,flush=True)
    return target


def write_cut_evidence(report_path, output_root=OUTPUT):
    # Cut audit failure must not destroy a successful Qwen report.
    try:
        import cut_audit
        return cut_audit.audit_report(report_path,output_root=output_root)
    except Exception as exc:
        # Preserve failure text in logs; do not assert a cut or modify report.
        print('V067_CUT_AUDIT_UNAVAILABLE',type(exc).__name__,str(exc)[:500],flush=True)
        return None


def run(jobs=JOBS,results=RESULTS,done=DONE,observer_path=OBSERVER,dry_run=False,max_jobs=1,video_root=OUTPUT,
        manage_memory=True,perform_cut_audit=True):
    require_idle()
    # Do not load full Comfy model when no jobs need processing.
    paths=sorted(Path(jobs).glob('*.json'))
    pending=[p for p in paths if not (Path(done)/(p.stem+'.json')).exists()]
    if not pending:
        print('V066_NO_PENDING_JOBS');return []
    if dry_run:
        print('V066_PENDING',len(pending))
        for p in pending[:max_jobs]:
            record,src=job_record(p,video_root=video_root)
            print('PENDING',record['job_id'],str(src))
        return []
    observer=observer_module(observer_path)
    completed=[]
    for p in pending[:max_jobs]:
        # Verify idle again before EACH GPU operation.
        require_idle()
        record,src=job_record(p,video_root=video_root)
        if observer.sha256(src)!=record['source_sha256']:
            raise ValueError('Saved video SHA-256 changed since capture; no analysis allowed')
        if observer.media_meta(src)!=record['video_meta']:
            raise ValueError('Saved video metadata changed since capture')
        dest=Path(results)/record['job_id']
        archive_partial_result(dest,record['job_id'])
        if manage_memory:ensure_qwen_resources()
        print('V067_QWEN_START job',record['job_id'],flush=True)
        path=observer.observe_video(src,dest,samples=record['samples'],
             source_class=record['source_class'],require_idle=True,allow_free=False)
        obj=json.loads(path.read_text())
        if obj.get('source_sha256')!=record['source_sha256'] or obj.get('preprocess_only'):
            raise RuntimeError('Observation source or phase mismatch')
        cut_path=write_cut_evidence(path,output_root=video_root) if perform_cut_audit else None
        Path(done).mkdir(parents=True,exist_ok=True)
        completion={'version':'director-saved-shot-drain-0.6.6','job_id':record['job_id'],
                    'observation_report':str(path),'cut_evidence_report':str(cut_path) if cut_path else None,'original_video_sha256':record['source_sha256'],
                    'read_only_shadow':True,'h3_visual_conditioning':False,
                    'automatic_editing_authorized':False,'continuity_handoff_authorized':False}
        marker=Path(done)/(record['job_id']+'.json')
        with marker.open('x') as f:json.dump(completion,f,sort_keys=True,indent=2)
        completed.append(str(path))
        print('V067_SHADOW_COMPLETE',path,flush=True)
    return completed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',type=Path,default=JOBS)
    parser.add_argument('--results',type=Path,default=RESULTS)
    parser.add_argument('--done',type=Path,default=DONE)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--max-jobs',type=int,default=1)
    parser.add_argument('--no-auto-unload',action='store_true',help='Disable queue-idle memory handoff; Qwen may run out of VRAM')
    args=parser.parse_args()
    if not 1<=args.max_jobs<=25:parser.error('max-jobs must be 1–25')
    run(jobs=args.jobs,results=args.results,done=args.done,dry_run=args.dry_run,max_jobs=args.max_jobs,
        manage_memory=not args.no_auto_unload)

if __name__=='__main__':main()
