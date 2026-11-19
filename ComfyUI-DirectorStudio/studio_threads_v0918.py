"""Use only the CPU cores this container really gets (V0.9.18).

On Vast a rental sees the whole host (e.g. 384 cores) but may only use a share of it (e.g. 46). PyTorch, OpenCV and
numpy start one worker thread per visible core, the kernel keeps pausing the process for going over its share, and
everything that runs on the CPU crawls: making references, decoding/saving a take after sampling (GPU at 0 %, minutes
instead of seconds), and slow pages end in HTTP 524. This caps those thread pools at the real share.

A cap you set yourself (OMP_NUM_THREADS, or DIRECTOR_CPU_THREADS) always wins; DIRECTOR_CPU_THREADS=0 turns this off.
"""
from __future__ import annotations
import os
from pathlib import Path

ENV_VARS = ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS')
APPLIED: dict = {}

def _quota(cg=Path('/sys/fs/cgroup')):
    """Cores allowed by the container's CPU quota, or None when there is no quota."""
    try:                                                          # cgroup v2: "max 100000" or "4608000 100000"
        q, p = (cg / 'cpu.max').read_text().split()[:2]
        if q != 'max' and int(p) > 0: return int(q) / int(p)
    except (OSError, ValueError): pass
    for d in (cg / 'cpu', cg / 'cpu,cpuacct', cg / 'cpuacct,cpu'):   # cgroup v1
        try:
            q = int((d / 'cpu.cfs_quota_us').read_text()); p = int((d / 'cpu.cfs_period_us').read_text())
            if q > 0 and p > 0: return q / p
        except (OSError, ValueError): continue
    return None

def cores(cg=Path('/sys/fs/cgroup')):
    """(cores this process may really use, cores it can see)."""
    try: seen = len(os.sched_getaffinity(0))
    except (AttributeError, OSError): seen = os.cpu_count() or 1
    q = _quota(cg)
    real = seen if q is None else max(1, min(seen, int(q)))
    return real, seen

def threads_for(real):
    """A couple of cores stay free for the web server, ffmpeg and the Director's helpers."""
    return max(1, real - 2) if real > 8 else real

def apply(cg=Path('/sys/fs/cgroup')):
    """Cap PyTorch / OpenCV / numpy threads in this process, and (through the environment) in every helper it starts."""
    want = os.environ.get('DIRECTOR_CPU_THREADS')
    if want == '0': APPLIED.update(state='off (DIRECTOR_CPU_THREADS=0)'); return APPLIED
    real, seen = cores(cg)
    if want and want.isdigit(): n, why = int(want), 'DIRECTOR_CPU_THREADS'
    elif os.environ.get('OMP_NUM_THREADS', '').isdigit(): n, why = int(os.environ['OMP_NUM_THREADS']), 'OMP_NUM_THREADS (yours)'
    elif real < seen: n, why = threads_for(real), f'container share {real} of {seen} cores'
    else:
        APPLIED.update(state='not needed', real=real, seen=seen); return APPLIED
    for v in ENV_VARS: os.environ.setdefault(v, str(n))           # helpers started from here inherit it
    done = []
    try:
        import torch
        if torch.get_num_threads() > n: torch.set_num_threads(n); done.append('torch')
    except Exception: pass
    try:
        import cv2
        if cv2.getNumThreads() > n or cv2.getNumThreads() <= 0: cv2.setNumThreads(n); done.append('opencv')
    except Exception: pass
    APPLIED.update(state='capped', threads=n, why=why, real=real, seen=seen, libs=done)
    return APPLIED

def describe():
    a = APPLIED or {}
    if a.get('state') == 'capped': return f"CPU threads capped at {a['threads']} ({a['why']}; {', '.join(a['libs']) or 'environment only'})"
    return f"CPU threads: {a.get('state', 'not checked')}"
