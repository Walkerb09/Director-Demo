#!/usr/bin/env python3
"""Guarded in-place V063 -> V065 upgrade with out-of-plugin rollback backup.

No restart, no Director memory access, no changes to generation workflow.
Never overwrite unknown customizations. Operator restores using --rollback.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, urllib.request, uuid
from pathlib import Path
HERE=Path(__file__).resolve().parent
NAME='ComfyUI-Director-SemanticObserver-V063'
SRC=HERE/'addon'/NAME
DST=Path('/workspace/ComfyUI/custom_nodes')/NAME
BACKUP_ROOT=Path('/workspace/DirectorSemanticObserverBackups')
FILES=('__init__.py','nodes.py','semantic_observer.py')
OLD_HASHES={
'__init__.py':'4d96f00d9766b261140a109e123f025667b65da0855ed00ad2710b33ef163106',
'nodes.py':'fce1b2055a49696c131bb509af506a23c775d2e4cc0cb45b0dd18abd14bf4dcd',
'semantic_observer.py':'349ac099b21e8562964c98642fc90039cbc81014b43ca488d99a764cbdf6564c',
}

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def hashes(path):return {f:digest(path/f) for f in FILES}
def verify():
    expected=json.loads((HERE/'manifest.json').read_text())
    if hashes(SRC)!=expected:raise RuntimeError('Upgrade ZIP is corrupted, source hashes mismatch')
    return expected

def idle():
    try:
        with urllib.request.urlopen('http://127.0.0.1:18188/queue',timeout=5) as resp:q=json.load(resp)
    except Exception as e:raise RuntimeError('Could not confirm idle ComfyUI queue: '+repr(e))
    if not isinstance(q.get('queue_pending'),list) or not isinstance(q.get('queue_running'),list) or q['queue_pending'] or q['queue_running']:
        raise RuntimeError('ComfyUI queue active or invalid; refusing plugin update')

def check_existing(dst,expected,old=OLD_HASHES):
    if not dst.exists():return 'absent'
    if not dst.is_dir() or dst.is_symlink():raise RuntimeError('Unexpected plugin directory type')
    for f in FILES:
        if not (dst/f).is_file() or (dst/f).is_symlink():raise RuntimeError('Unexpected or missing '+f)
    found=hashes(dst)
    if found==expected:return 'current'
    if found==old:return 'original'
    raise RuntimeError('Existing V063 plugin has unknown edits; refuse overwrite')

def valid_extra_files(dst):
    # __pycache__ is expected in working ComfyUI; never copy transient cache.
    unrecognized=[x.name for x in dst.iterdir() if x.name not in FILES and x.name!='__pycache__']
    if unrecognized:raise RuntimeError('Unknown files inside plugin; refuse to overwrite: '+repr(unrecognized))

def copy_staged(source,staged,expected):
    if staged.exists():raise RuntimeError('Temporary staging directory already exists')
    staged.mkdir(parents=True)
    for f in FILES:shutil.copy2(source/f,staged/f)
    if hashes(staged)!=expected:raise RuntimeError('Staged files changed')

def upgrade(dst=DST,backup_root=BACKUP_ROOT,dry_run=True,live=True):
    expected=verify();mode=check_existing(dst,expected)
    if mode!='absent':valid_extra_files(dst)
    if live:idle()
    print('V065_TARGET '+str(dst),flush=True)
    print('V065_EXISTING_STATUS '+mode,flush=True)
    if mode=='current':
        print('ALREADY_CURRENT_IDENTICAL',flush=True);return 'current'
    if dry_run:
        print('V065_DRY_RUN_OK: upgrade is possible, nothing written',flush=True);return mode
    dst.parent.mkdir(parents=True,exist_ok=True)
    backup_root.mkdir(parents=True,exist_ok=True)
    if backup_root.stat().st_dev!=dst.parent.stat().st_dev:
        raise RuntimeError('Backup and plugin locations on different file systems; refusing non-atomic upgrade')
    unique=uuid.uuid4().hex[:12]
    staged=dst.parent/('.'+NAME+'.staging-'+unique)
    backup=backup_root/(NAME+'_before_v065_'+unique)
    try:
        copy_staged(SRC,staged,expected)
        if mode=='original':
            os.rename(dst,backup)
            try:os.rename(staged,dst)
            except BaseException:
                os.rename(backup,dst)
                raise
            print('V063_BACKUP '+str(backup),flush=True)
        else:
            os.rename(staged,dst)
        print('V065_UPGRADED '+str(dst),flush=True)
        print('NO_RESTART: ComfyUI must be restarted later, when queue is idle, to load new code.',flush=True)
        return 'updated'
    finally:
        if staged.exists():shutil.rmtree(staged)

def rollback(backup,dst=DST,live=True):
    expected=verify()
    if live:idle()
    backup=Path(backup).resolve(strict=True)
    if backup.parent != BACKUP_ROOT.resolve():raise ValueError('Backup must be directly within DirectorSemanticObserverBackups')
    if backup.is_symlink() or not backup.is_dir() or hashes(backup)!=OLD_HASHES:raise ValueError('Invalid V063 rollback backup')
    if check_existing(dst,expected)!='current':raise RuntimeError('Current installed plugin must match V065 before rollback')
    valid_extra_files(dst)
    unique=uuid.uuid4().hex[:12]
    displaced=BACKUP_ROOT/('superseded_v065_'+unique)
    if displaced.parent.stat().st_dev!=dst.parent.stat().st_dev:raise RuntimeError('Cross-device rollback refused')
    os.rename(dst,displaced)
    try:os.rename(backup,dst)
    except BaseException:os.rename(displaced,dst);raise
    print('V063_RESTORED '+str(dst),flush=True)
    print('Previous V065 code saved to '+str(displaced),flush=True)
    print('RESTART NOT PERFORMED',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--install',action='store_true')
    p.add_argument('--rollback',type=Path)
    args=p.parse_args()
    if args.rollback:rollback(args.rollback)
    else:upgrade(dry_run=not args.install)
