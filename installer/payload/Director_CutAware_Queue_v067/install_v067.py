#!/usr/bin/env python3
"""Hash-guarded in-place upgrade of read-only queue worker; no restart required."""
from __future__ import annotations
import argparse,hashlib,os,shutil,urllib.request,json
from pathlib import Path

BASE=Path(__file__).resolve().parent
TARGET=Path('/workspace/DirectorSavedShotQueueV066')
ORIGINAL_SHA='8d51dc6a1de545ddede2d80cf2c48152e367c7a52a78e53815eecc2c5ee3b168'
FILES=('drain_jobs.py','cut_audit.py')

def sha(data):return hashlib.sha256(data).hexdigest()

def install(target=TARGET,src=BASE,*,apply=False):
    target=Path(target);src=Path(src)
    current=target/'drain_jobs.py'
    if not current.is_file() or current.is_symlink():raise FileNotFoundError('Original V066 drain worker missing')
    expected={n:sha((src/n).read_bytes()) for n in FILES}
    for n in FILES:
        if not (src/n).is_file() or (src/n).is_symlink():raise RuntimeError('Missing/symlink source '+n)
    actual=sha(current.read_bytes())
    if actual==expected['drain_jobs.py']:
        extra=target/'cut_audit.py'
        if extra.is_file() and not extra.is_symlink() and sha(extra.read_bytes())==expected['cut_audit.py']:
            print('V067_WORKER_ALREADY_CURRENT',current)
            return 'current'
        raise RuntimeError('Worker already patched but cut_audit missing/changed; manual review required')
    if actual!=ORIGINAL_SHA:raise RuntimeError('Unknown installed worker SHA256; refusing overwrite: '+actual)
    extra=target/'cut_audit.py'
    if extra.exists() or extra.is_symlink():raise FileExistsError('Unrecognized cut_audit.py already exists')
    if not apply:
        print('V067_SAFE_UPGRADE_DRY_RUN',target)
        return 'dry_run'
    backups=target/'backups'
    backups.mkdir(parents=True,exist_ok=True)
    backup=backups/('drain_jobs_v066_'+ORIGINAL_SHA[:16]+'.py')
    if backup.exists():
        if sha(backup.read_bytes())!=ORIGINAL_SHA:raise RuntimeError('Backup conflict')
    else:
        with backup.open('xb') as f:f.write(current.read_bytes())
    if sha(current.read_bytes())!=ORIGINAL_SHA:raise RuntimeError('Concurrent worker modification')
    staging=[]
    try:
        for n in FILES:
            temp=target/('.'+n+'.v067.'+str(os.getpid())+'.tmp')
            with temp.open('xb') as f:
                b=(src/n).read_bytes();f.write(b);f.flush();os.fsync(f.fileno())
            staging.append((temp,target/n))
        # Add cut parser first; old worker remains valid if interrupted.
        os.replace(staging[1][0],staging[1][1]);staging=staging[:1]
        if sha(current.read_bytes())!=ORIGINAL_SHA:raise RuntimeError('Worker changed while staging')
        os.replace(staging[0][0],staging[0][1]);staging=[]
    finally:
        for temp,_ in staging:temp.unlink(missing_ok=True)
    for n in FILES:
        if sha((target/n).read_bytes())!=expected[n]:raise RuntimeError('Installed file mismatch: '+n)
    print('V067_WORKER_INSTALLED',current)
    print('V067_ROLLBACK_BACKUP',backup)
    print('V067_NO_COMFY_RESTART_NEEDED')
    return 'installed'

def rollback(target=TARGET):
    target=Path(target)
    current=target/'drain_jobs.py';new=target/'cut_audit.py'
    backup=target/'backups'/('drain_jobs_v066_'+ORIGINAL_SHA[:16]+'.py')
    if not backup.is_file() or sha(backup.read_bytes())!=ORIGINAL_SHA:raise RuntimeError('Verified backup missing')
    for n in FILES:
        if not (target/n).is_file() or sha((target/n).read_bytes())!=sha((BASE/n).read_bytes()):
            raise RuntimeError('Refusing rollback of unrecognized current worker')
    temp=target/('.drain_v066_rollback_'+str(os.getpid())+'.tmp')
    try:
        with temp.open('xb') as f:f.write(backup.read_bytes());f.flush();os.fsync(f.fileno())
        os.replace(temp,current)
        new.unlink()
    finally:temp.unlink(missing_ok=True)
    print('V067_WORKER_ROLLED_BACK',current)
    return 'rolled_back'

def main():
    ap=argparse.ArgumentParser()
    gp=ap.add_mutually_exclusive_group()
    gp.add_argument('--install',action='store_true');gp.add_argument('--rollback',action='store_true')
    args=ap.parse_args()
    if args.rollback:rollback()
    else:install(apply=args.install)

if __name__=='__main__':main()
