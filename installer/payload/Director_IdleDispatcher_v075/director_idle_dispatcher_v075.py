#!/usr/bin/env python3
"""V0.7.5: explicit opt-in, queue-idle, shadow-only V0.7.4 dispatcher.

Does not modify ComfyUI, H3, capture jobs, media, prior observations or Director memory.
This program cannot atomically lock ComfyUI's prompt admission: it rechecks the queue
and preempts its OWN Qwen/V074 subprocess when an H3 job becomes visible.
"""
from __future__ import annotations
import argparse, datetime, fcntl, hashlib, json, os, re, signal, subprocess, sys, time, urllib.request
from pathlib import Path

VERSION='director-idle-postshot-dispatcher-0.7.5'
DEFAULT_ROOT=Path('/workspace/DirectorSavedShotQueueV066')
DEFAULT_V074=Path('/workspace/DirectorUnifiedV074/director_postshot_v074.py')
DEFAULT_PROFILES=Path('/workspace/DirectorIdleDispatcherV075/profiles')
QUEUE_URL='http://127.0.0.1:18188/queue'
HEX24=re.compile(r'[0-9a-f]{24}\Z')
SAFETY={'automatic_editing_authorized':False,'continuity_handoff_authorized':False,'director_memory_changed':False,'generated_footage_for_H3_visual_conditioning':False}


def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode()
def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def utc():return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def queue_idle(fetch=None):
    if fetch is None:
        def fetch():
            with urllib.request.urlopen(QUEUE_URL,timeout=8) as r:return json.load(r)
    try:q=fetch()
    except Exception as e:raise RuntimeError('ComfyUI queue unavailable; refusing to start/continue Qwen') from e
    if not isinstance(q,dict) or not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
        raise RuntimeError('Unexpected /queue schema; fail closed')
    return not(q['queue_running'] or q['queue_pending'])


def profile_inventory(folder):
    folder=Path(folder)
    if not folder.is_dir() or folder.is_symlink():raise ValueError('Explicit profile directory missing or unsafe')
    result=[]; ids=set(); prefixes=set()
    for p in sorted(folder.glob('*.json')):
        if p.is_symlink() or not p.is_file():raise ValueError('Unsafe profile path: '+str(p))
        o=json.loads(p.read_text())
        if not isinstance(o,dict) or o.get('schema')!='director-scene-profile-v1':raise ValueError('Wrong profile schema')
        pid=o.get('project_id');prefix=o.get('video_name_prefix')
        if (not isinstance(pid,str) or not re.fullmatch('[a-z][a-z0-9_-]{1,40}',pid)
            or not isinstance(prefix,str) or not re.fullmatch('[A-Za-z0-9_-]{3,80}',prefix)):
            raise ValueError('Unsafe profile identity')
        if pid in ids or prefix in prefixes:raise ValueError('Ambiguous profiles (duplicate project or prefix)')
        ids.add(pid);prefixes.add(prefix)
        result.append({'id':pid,'prefix':prefix,'path':p,'sha256':sha(p)})
    if not result:raise ValueError('No explicit profiles configured')
    return result


def job_candidates(root,profiles,*,retry_failed=False):
    root=Path(root);jobs=root/'jobs';results=root/'results';state=root/'dispatcher_v075'
    if not jobs.is_dir() or jobs.is_symlink():raise ValueError('Trusted capture jobs directory missing/unsafe')
    if not results.is_dir() or results.is_symlink():raise ValueError('Results directory missing/unsafe')
    out=[];skips=[]
    for p in sorted(jobs.glob('*.json'),key=lambda x:(x.stat().st_mtime_ns,x.name)):
        if p.is_symlink() or not p.is_file() or not HEX24.fullmatch(p.stem):skips.append((p.name,'UNSAFE_JOB_NAME'));continue
        try:
            d=json.loads(p.read_text())
            if d.get('job_id')!=p.stem or d.get('version')!='director-saved-shot-capture-0.6.6' or d.get('phase')!='PENDING_SHADOW_OBSERVATION':
                raise ValueError('Unrecognized capture record')
            if any(d.get(k) is not v for k,v in SAFETY.items()) or d.get('observation_authority')!='NONE':
                raise ValueError('Unsafe/missing capture authority guards')
            src=d.get('source_video_path')
            if not isinstance(src,str) or not src.startswith('/'):raise ValueError('Invalid source pathname')
            # Source path and hash are independently verified by V0.7.4 BEFORE Qwen starts.
            matches=[x for x in profiles if Path(src).name.startswith(x['prefix'])]
            if not matches:skips.append((p.stem,'NO_EXPLICIT_CAST_PROFILE'));continue
            if len(matches)!=1:raise ValueError('Ambiguous profile prefix matches')
            dst=results/p.stem
            if dst.is_symlink():raise ValueError('Unsafe results symlink')
            done=dst/'director_postshot_v074.json'
            if done.is_symlink():raise ValueError('Unsafe summary symlink')
            if done.is_file():
                # Do not trust mere file existence as valid completion; V074 will verify it when requested.
                skips.append((p.stem,'V074_SUMMARY_EXISTS'));continue
            marker=state/'failures'/f'{p.stem}.json'
            if marker.is_symlink():raise ValueError('Unsafe failure marker')
            if marker.is_file() and not retry_failed:
                failure=json.loads(marker.read_text())
                if failure.get('job_sha256')==sha(p):
                    skips.append((p.stem,'PREVIOUS_FAILURE_REQUIRES_OPERATOR_RETRY'));continue
            out.append({'job_id':p.stem,'job_path':p,'job_sha256':sha(p),'profile':matches[0], 'source':src})
        except (ValueError,TypeError,KeyError,UnicodeDecodeError,json.JSONDecodeError) as e:
            skips.append((p.name,'INVALID_CAPTURE_RECORD:'+str(e)[:100]))
    return out,skips


def idle_grace(seconds, fetch=None, sleeper=time.sleep):
    if not queue_idle(fetch):return False
    if seconds>0:
        sleeper(seconds)
        if not queue_idle(fetch):return False
    return True


def kill_group(proc,*,grace_seconds=8,sleeper=time.sleep):
    if proc.poll() is not None:return
    try:os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError:return
    deadline=time.monotonic()+grace_seconds
    while proc.poll() is None and time.monotonic()<deadline:sleeper(.1)
    if proc.poll() is None:
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    proc.wait(timeout=8)


def supervised(proc, fetch=None, *, poll_interval=2, sleeper=time.sleep):
    """Monitor mid-inference queue changes; only kills OUR process group, never ComfyUI.

    Check immediately, then wait in short chunks. If a new generation enters
    queue, terminate v074 + child Qwen (same process group) before another poll.
    """
    while True:
        try:idle=queue_idle(fetch)
        except RuntimeError:idle=False
        if not idle:
            kill_group(proc,sleeper=sleeper)
            return 'PREEMPTED_OR_QUEUE_UNAVAILABLE'
        rc=proc.poll()
        if rc is not None:return 'COMPLETE' if rc==0 else 'FAILED'
        sleeper(poll_interval)


def write_failure(folder,job,reason):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    dest=folder/(job['job_id']+'.json')
    value={'version':VERSION,'job_id':job['job_id'],'job_sha256':job['job_sha256'],
           'reason':reason,'time_utc':utc(),'automatic_retry':False,
           'no_edit_no_handoff_no_h3':True}
    if dest.is_symlink():raise ValueError('Unsafe failure marker')
    if dest.exists():
        old=json.loads(dest.read_text())
        if old.get('job_sha256')==job['job_sha256']:return dest
        raise FileExistsError('Old failure refers to different job bytes; inspect manually')
    with dest.open('xb') as f:f.write(canonical(value))
    return dest


def dispatch_one(job,*,root=DEFAULT_ROOT,v074=DEFAULT_V074,fetch=None,grace=8,poll=2,python=sys.executable,sleeper=time.sleep):
    root=Path(root);v074=Path(v074)
    if not v074.is_file() or v074.is_symlink():raise ValueError('Trusted V074 runner missing/unsafe')
    if not idle_grace(grace,fetch,sleeper):
        print('V075_DEFER_BUSY_QUEUE',job['job_id'],flush=True);return 'DEFERRED'
    if sha(job['job_path'])!=job['job_sha256'] or sha(job['profile']['path'])!=job['profile']['sha256']:
        raise RuntimeError('Capture job or cast profile changed before execution')
    local=root/'dispatcher_v075';logs=local/'logs';logs.mkdir(parents=True,exist_ok=True)
    logfile=logs/(job['job_id']+'_'+utc()+'.log')
    args=[str(python),'-u',str(v074),'--job-id',job['job_id'],
          '--profile',str(job['profile']['path']),'--root',str(root),'--run']
    print('V075_START',job['job_id'],'PROFILE',job['profile']['id'],flush=True)
    with logfile.open('x') as f:
        proc=subprocess.Popen(args,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            status=supervised(proc,fetch,poll_interval=poll,sleeper=sleeper)
        except BaseException:
            # Ctrl+C or SIGTERM must not strand Qwen after the dispatcher exits.
            kill_group(proc)
            raise
    print('V075_WORKER_STATUS',status,'LOG',logfile,flush=True)
    if status=='PREEMPTED_OR_QUEUE_UNAVAILABLE':return 'DEFERRED'
    if status!='COMPLETE':
        dest=write_failure(local/'failures',job,'V074_CHILD_FAILED_SEE_LOG')
        print('V075_FAILURE_REQUIRES_OPERATOR_RETRY',dest,flush=True)
        return 'FAILED'
    result=root/'results'/job['job_id']/'director_postshot_v074.json'
    if not result.is_file() or result.is_symlink():
        write_failure(local/'failures',job,'CHILD_RETURNED_ZERO_BUT_SUMMARY_MISSING')
        return 'FAILED'
    summary=json.loads(result.read_text())
    if summary.get('job_id')!=job['job_id'] or summary.get('job_sha256')!=job['job_sha256'] or \
       summary.get('profile_sha256')!=job['profile']['sha256'] or summary.get('human_review_required') is not True or \
       summary.get('scene_dna_verified') is not False or summary.get('automatic_editing_authorized') is not False or \
       summary.get('continuity_handoff_authorized') is not False or summary.get('h3_visual_conditioning') is not False:
        write_failure(local/'failures',job,'PUBLISHED_SUMMARY_GUARD_FAILED')
        return 'FAILED'
    print('V075_REVIEW_AVAILABLE',result,flush=True)
    return 'COMPLETE'


def acquire_lock(root):
    p=Path(root)/'dispatcher_v075'
    if p.is_symlink():raise ValueError('Unsafe dispatcher state directory')
    p.mkdir(parents=True,exist_ok=True)
    lockpath=p/'dispatcher.lock'
    if lockpath.is_symlink():raise ValueError('Unsafe lock path')
    f=lockpath.open('a+')
    try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        f.close();raise RuntimeError('Another V0.7.5 dispatcher is already running')
    return f


def cli():
    a=argparse.ArgumentParser(description=__doc__)
    mode=a.add_mutually_exclusive_group()
    mode.add_argument('--once',action='store_true',help='Review at most one eligible job and exit')
    mode.add_argument('--watch',action='store_true',help='Opt-in foreground watch (Ctrl+C stops)')
    a.add_argument('--run',action='store_true',help='Required for any inference or report writes')
    a.add_argument('--retry-failed',action='store_true',help='Explicitly retry previously failed jobs')
    a.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    a.add_argument('--v074',type=Path,default=DEFAULT_V074)
    a.add_argument('--profiles-dir',type=Path,default=DEFAULT_PROFILES)
    a.add_argument('--idle-grace',type=int,default=8)
    a.add_argument('--poll',type=int,default=2)
    a.add_argument('--watch-interval',type=int,default=20)
    args=a.parse_args()
    if (args.once or args.watch) and not args.run:
        a.error('Actual processing requires explicit --run')
    if args.run and not (args.once or args.watch):a.error('--run requires --once or --watch')
    if args.retry_failed and args.watch:a.error('--retry-failed is one-shot only (no repeated inference loop)')
    if not 1<=args.poll<=10 or not 2<=args.idle_grace<=120 or not 10<=args.watch_interval<=600:
        a.error('Unsafe timing values')
    profiles=profile_inventory(args.profiles_dir)
    if args.run:
        def on_terminate(signum,frame):raise KeyboardInterrupt
        signal.signal(signal.SIGTERM,on_terminate)
    lock=acquire_lock(args.root) if args.run else None
    try:
        print('V075_PROFILES',[(p['id'],p['prefix']) for p in profiles],flush=True)
        while True:
            candidates,skips=job_candidates(args.root,profiles,retry_failed=args.retry_failed)
            print('V075_ELIGIBLE',len(candidates),'SKIPPED',len(skips),flush=True)
            for id,why in skips:print('V075_SKIP',id,why,flush=True)
            for j in candidates:print('V075_CANDIDATE',j['job_id'],j['source'],flush=True)
            if not args.run:
                print('V075_DRY_RUN_ONLY_NO_GPU_NO_WRITES',flush=True);return
            if candidates:
                # Do not stamp failures for transient busy-queue deferrals.
                try:status=dispatch_one(candidates[0],root=args.root,v074=args.v074,grace=args.idle_grace,poll=args.poll)
                except Exception as e:
                    path=write_failure(args.root/'dispatcher_v075'/'failures',candidates[0],type(e).__name__+':'+str(e)[:250])
                    print('V075_FAILED',path,flush=True)
                    if args.once:raise
                    status='FAILED'
                if args.once:
                    print('V075_ONCE_RESULT',status,flush=True);return
            else:
                if args.once:
                    print('V075_NO_ELIGIBLE_JOB',flush=True);return
            time.sleep(args.watch_interval)
    except KeyboardInterrupt:
        print('V075_WATCH_STOPPED_BY_OPERATOR',flush=True)
    finally:
        if lock:lock.close()

if __name__=='__main__':cli()
