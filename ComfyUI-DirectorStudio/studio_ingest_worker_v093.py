#!/usr/bin/env python3
"""Process ONE uploaded photo/video for the Director in its own process.

Why a separate process: reading phone videos, finding faces, cutting people out and removing captions uses
native libraries (FFmpeg, OpenCV). If any of that crashes or runs out of memory, only this helper dies;
ComfyUI keeps running and the Director reports the real reason instead of the upload silently vanishing.
Prints one line: RESULT {...} or ERROR {...}.
"""
import argparse, json, os, sys
from pathlib import Path
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)

def main():
    a = argparse.ArgumentParser()
    a.add_argument('--root', required=True); a.add_argument('--comfy-root', required=True)
    a.add_argument('--input-dir'); a.add_argument('--output-dir')
    a.add_argument('--owner', required=True); a.add_argument('--path', required=True)
    a.add_argument('--attest', action='store_true'); a.add_argument('--kind'); a.add_argument('--who'); a.add_argument('--replace', action='store_true')
    a.add_argument('--skip', default=''); a.add_argument('--voice', action='store_true')
    a.add_argument('--measure', nargs=3, metavar=('SHOT', 'TAKE', 'OUT'))   # measure one take (and its identity check)
    a.add_argument('--reduced', action='store_true')
    x = a.parse_args()
    import faulthandler; faulthandler.enable(all_threads=True)          # a native crash prints the exact Python line
    try:
        import cv2, numpy
        print(f'INFO opencv={cv2.__version__} numpy={numpy.__version__} python={sys.version.split()[0]}', file=sys.stderr, flush=True)
    except Exception as e: print(f'INFO opencv import failed: {e}', file=sys.stderr, flush=True)
    try:
        with open('/proc/self/oom_score_adj', 'w') as f: f.write('1000')     # if memory runs out, the kernel stops THIS helper, not ComfyUI
    except OSError: pass
    if os.environ.get('DIRECTOR_HELPER_TEST_CRASH') == 'segv':         # tests only: prove a native crash can't take ComfyUI down
        import signal; os.kill(os.getpid(), signal.SIGSEGV)
    import studio_v080 as sv
    s = sv.Studio(x.root, x.comfy_root, input_dir=x.input_dir, output_dir=x.output_dir)
    if x.measure:
        import json as _j, studio_media_v080 as media
        shot, take, out = int(x.measure[0]), int(x.measure[1]), x.measure[2]
        _t = os.environ.get('DIRECTOR_HELPER_TEST_CRASH')                    # tests only
        if (_t == 'measure' and not os.environ.get('OPENCV_CPU_DISABLE')) or (_t == 'measure_always' and not x.reduced):
            import signal, studio_media_v080 as _m
            def boom(*a, **k): os.kill(os.getpid(), signal.SIGSEGV)
            _m.optical_flow = boom
        st = s.state(); p = s.plan_record(st, shot, take)
        a = media.analyze_take(x.path, faces_on=not x.reduced, flow_on=not x.reduced)
        ident = None; ident_err = None
        if not x.reduced:
            try: ident = s.take_identity(st, p, Path(x.path), a)
            except Exception as e: ident_err = type(e).__name__
        Path(out).write_text(_j.dumps({'analysis': a, 'identity': ident, 'identity_error': ident_err, 'reduced': x.reduced}))
        print('RESULT ' + _j.dumps({'ok': True}), flush=True); return
    s.skip_steps = set(filter(None, x.skip.split(',')))
    if os.environ.get('DIRECTOR_HELPER_TEST_CRASH') == 'text' and 'text' not in s.skip_steps:   # tests only: a crash inside caption removal
        import signal, studio_text_v090 as tx
        def boom(*a, **k): os.kill(os.getpid(), signal.SIGSEGV)
        tx.detect = boom
    if 'identity' in s.skip_steps:
        import studio_media_v080 as media; media.SKIP_UPRIGHT = True
    try:
        for attempt in range(4):
            try:
                ids = ([s.add_voice(x.owner, x.path, attest_permission=x.attest)] if x.voice else
                       s.add_media(x.owner, x.path, attest_original=x.attest, kind=x.kind or None, who=x.who or None, replace=x.replace)); break
            except sv.StudioError as e:
                if str(e) != 'STATE_CHANGED_RETRY' or attempt == 3: raise
        print('RESULT ' + json.dumps({'refs': ids, 'warnings': getattr(s, 'last_warnings', [])}), flush=True)
    except (sv.StudioError, ValueError, FileNotFoundError) as e:
        print('ERROR ' + json.dumps({'error': str(e)}), flush=True); sys.exit(3)

if __name__ == '__main__':
    main()
