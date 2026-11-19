#!/usr/bin/env python3
"""Recover already-generated H3 final A/V VHS video into read-only shadow queue."""
from __future__ import annotations
import argparse, importlib.util
from pathlib import Path
CAP=Path('/workspace/ComfyUI/custom_nodes/ComfyUI-Director-SavedShotQueue-V066/shot_capture.py')
OUTPUT=Path('/workspace/ComfyUI/output')
DEFAULT_STEM='Director_H3_TEST_Shadow_UNAPPROVED_00001'

def recover(stem=DEFAULT_STEM,capture_module=CAP,output_root=OUTPUT):
    stem=Path(stem).name
    if not stem or stem.endswith('.mp4') or '/' in stem or '\\' in stem:
        raise ValueError('Expected a filename stem, not a path or filename extension')
    base=output_root/(stem+'.mp4')
    av=output_root/(stem+'-audio.mp4')
    if not base.is_file() or not av.is_file():
        raise FileNotFoundError('Expected previously saved VHS MP4 pair, found '+str(base.is_file())+'/'+str(av.is_file())+' (base/audio). No regeneration or fabricated job performed.')
    spec=importlib.util.spec_from_file_location('v0661_capture_recovery',str(capture_module))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    selected=m.resolve_vhs_video([str(base),str(av)],output_root)
    if selected!=av.resolve():
        raise RuntimeError('Capture did not select the verified audio-equipped file')
    dest,record,created=m.capture([str(base),str(av)],samples=8,
       source_class='H3_GENERATED_OBSERVATION_ONLY',output_root=output_root)
    print('RECOVERED_VHS_PAIR',str(base),str(av))
    print('SELECTED_AUDIO_VIDEO',record['source_video_path'])
    print('CAPTURE_JOB',record['job_id'],'CREATED' if created else 'ALREADY_QUEUED')
    print('SHADOW_JOB_JSON',dest)
    print('VIDEO_FRAMES',record['video_meta']['frames'],'FPS',record['video_meta']['fps'])
    print('NO_H3_REGENERATION_NO_EDIT_AUTHORITY')
    return dest

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stem',default=DEFAULT_STEM)
    recover(ap.parse_args().stem)

if __name__=='__main__':main()
