#!/usr/bin/env python3
"""Additive installer, queue-idle gate, never touches H3 plugin or restarts ComfyUI."""
from __future__ import annotations
import argparse,filecmp,hashlib,json,shutil,urllib.request
from pathlib import Path
SOURCE=Path(__file__).resolve().parent/'addon/ComfyUI-Director-SavedShotQueue-V066'
TARGET=Path('/workspace/ComfyUI/custom_nodes/ComfyUI-Director-SavedShotQueue-V066')


def require_idle():
    with urllib.request.urlopen('http://127.0.0.1:18188/queue',timeout=8) as r:q=json.load(r)
    if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
        raise RuntimeError('Invalid queue state')
    if q['queue_running'] or q['queue_pending']:raise RuntimeError('ComfyUI queue active; install cancelled')


def install(source=SOURCE,target=TARGET,dry_run=True,queue_checker=require_idle):
    source=Path(source);target=Path(target)
    expected={p.relative_to(source):p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if set(expected)!={Path('__init__.py'),Path('nodes.py'),Path('shot_capture.py')}:
        raise RuntimeError('Unexpected add-on source inventory')
    if target.exists():
        actual={p.relative_to(target):p for p in target.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        if set(actual)!=set(expected) or any(a.read_bytes()!=actual[k].read_bytes() for k,a in expected.items()):
            raise FileExistsError('Existing add-on differs; refuse overwrite')
        print('V066_ALREADY_INSTALLED_IDENTICAL',target)
        return 'identical'
    if dry_run:
        print('V066_ADD_ONLY_DRY_RUN',target)
        return 'dry_run'
    queue_checker()
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise FileExistsError('Race: target appeared during install')
    shutil.copytree(source,target)
    print('V066_ADD_ONLY_INSTALLED',target)
    print('Node available after a later operator-controlled, queue-idle restart; NOT performed')
    return 'installed'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true')
    args=parser.parse_args()
    install(dry_run=not args.install)

if __name__=='__main__':main()
