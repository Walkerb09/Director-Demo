#!/usr/bin/env python3
"""Safely upgrade ONLY installed V0.6.6 shot_capture.py to dual VHS output support."""
from __future__ import annotations
import argparse, hashlib, json, os, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATCH = ROOT/'patch'/'shot_capture.py'
TARGET = Path('/workspace/ComfyUI/custom_nodes/ComfyUI-Director-SavedShotQueue-V066/shot_capture.py')
NODE = TARGET.with_name('nodes.py')
INIT = TARGET.with_name('__init__.py')
BACKUP_DIR = Path('/workspace/DirectorSavedShotQueueV066/backups')
BASE_SHA = 'ef81e808476a49c21811e0b837099c673fbbea44f866e75e175ea018fb19015c'
NODE_SHA = 'd938cfe44102563af69a3711460eaabb00bb123c93f0b19af3cd23efa2073aa6'
INIT_SHA = '029da2fd1a62105692a0b8da36caae5f6abc4a0b1515402c6c3c60142fd294d9'

def digest(b):return hashlib.sha256(b).hexdigest()

def require_idle():
    with urllib.request.urlopen('http://127.0.0.1:18188/queue',timeout=10) as r:
        state=json.load(r)
    if (not isinstance(state.get('queue_running'),list) or
        not isinstance(state.get('queue_pending'),list)):
        raise RuntimeError('Queue state incomplete; refusing to modify ComfyUI')
    if state['queue_running'] or state['queue_pending']:
        raise RuntimeError('ComfyUI queue active; no upgrade/restart permitted')

def install(*, install=False,target=TARGET,node=NODE,init=INIT,backup_dir=BACKUP_DIR,patch=PATCH,check_idle=require_idle):
    for p in (target,node,init,patch):
        if not p.is_file() or p.is_symlink():raise FileNotFoundError('Expected regular file: '+str(p))
    if digest(node.read_bytes())!=NODE_SHA or digest(init.read_bytes())!=INIT_SHA:
        raise ValueError('Installed V0.6.6 node differs from expected release; refusing upgrade')
    patched=patch.read_bytes()
    if not patched or b'check_muxed_audio_stream' not in patched:raise ValueError('Patch incomplete')
    current=digest(target.read_bytes());updated=digest(patched)
    if current==updated:
        print('V0661_PATCH_ALREADY_CURRENT',target)
        return 'current'
    if current!=BASE_SHA:
        raise ValueError('V0.6.6 capture code has unexpected modifications, refuse overwrite: '+current)
    if not install:
        print('V0661_DRY_RUN_OK',target)
        return 'dry_run'
    check_idle()
    backup_dir.mkdir(parents=True,exist_ok=True)
    backup=backup_dir/('shot_capture_v066_prior_to_mux_'+BASE_SHA[:12]+'.py')
    source=target.read_bytes()
    if digest(source)!=BASE_SHA:raise RuntimeError('Installed capture source changed during upgrade')
    if backup.exists():
        if digest(backup.read_bytes())!=BASE_SHA:raise FileExistsError('Existing rollback backup differs; refusing upgrade')
    else:
        with backup.open('xb') as f:f.write(source)
    scratch=target.with_name('.shot_capture_v0661_'+str(os.getpid())+'.tmp')
    try:
        with scratch.open('xb') as f:
            f.write(patched)
            f.flush();os.fsync(f.fileno())
        scratch.chmod(target.stat().st_mode & 0o777)
        if digest(target.read_bytes())!=BASE_SHA:raise RuntimeError('Target changed during upgrade; refusing replacement')
        os.replace(scratch,target)
    finally:
        scratch.unlink(missing_ok=True)
    if digest(target.read_bytes())!=updated:raise RuntimeError('Upgrade verification failed; rollback manually from backup')
    print('V0661_PATCH_INSTALLED',target)
    print('V0661_ROLLBACK_BACKUP',backup)
    print('V0661_RESTART_REQUIRED_ONLY_IF_QUEUE_IDLE')
    return 'installed'

def rollback(*,target=TARGET,backup_dir=BACKUP_DIR,patch=PATCH,check_idle=require_idle):
    backup=backup_dir/('shot_capture_v066_prior_to_mux_'+BASE_SHA[:12]+'.py')
    if not backup.is_file() or digest(backup.read_bytes())!=BASE_SHA:raise FileNotFoundError('Valid rollback backup missing')
    if not target.is_file() or digest(target.read_bytes())!=digest(patch.read_bytes()):
        raise ValueError('Target differs from known v0.6.6.1 patch; refusing rollback')
    check_idle()
    scratch=target.with_name('.shot_capture_rollback_'+str(os.getpid())+'.tmp')
    try:
        with scratch.open('xb') as f:f.write(backup.read_bytes())
        scratch.chmod(target.stat().st_mode & 0o777)
        os.replace(scratch,target)
    finally:scratch.unlink(missing_ok=True)
    print('V0661_ROLLED_BACK restart ComfyUI only after verifying queue idle')

def main():
    ap=argparse.ArgumentParser()
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--install',action='store_true')
    group.add_argument('--rollback',action='store_true')
    args=ap.parse_args()
    if args.rollback:rollback()
    else:install(install=args.install)

if __name__=='__main__':main()
