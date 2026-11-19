"""V0.6.6: capture saved VHS video provenance; never run Qwen during H3 generation.

Shadow-only. Nothing here supplies H3 images, prompts or editorial decisions.
"""
from __future__ import annotations
import hashlib, json, os, re, subprocess, tempfile
from pathlib import Path

OUTPUT_ROOT=Path('/workspace/ComfyUI/output')
JOB_ROOT=Path('/workspace/DirectorSavedShotQueueV066/jobs')
SOURCE_CLASSES=('H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY','UNKNOWN_ORIGIN')
VIDEO_SUFFIXES=('.mp4','.mov','.mkv','.webm')


def sha256_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def candidate_names(value):
    found=[]
    def walk(v):
        if isinstance(v,str):
            if v.lower().endswith(VIDEO_SUFFIXES):found.append(v)
        elif isinstance(v,(tuple,list)):
            for item in v:walk(item)
        elif isinstance(v,dict):
            for key in ('filenames','files','output','path','filename','video'):
                if key in v:walk(v[key])
    walk(value)
    return list(dict.fromkeys(found))


def resolve_vhs_video(vhs_payload,root=OUTPUT_ROOT):
    """Allow exactly one saved video under ComfyUI/output; reject symlinks escaping it."""
    root=Path(root).resolve(strict=True)
    names=candidate_names(vhs_payload)
    paths=[]
    for name in names:
        p=Path(name).expanduser()
        if not p.is_absolute():
            # VHS may report a ComfyUI-root-relative 'output/name.mp4'.
            p=root.joinpath(*p.parts[1:]) if p.parts and p.parts[0]=='output' else root/p
        try:p=p.resolve(strict=True)
        except FileNotFoundError:continue
        if not p.is_relative_to(root):raise ValueError('VHS video path escapes approved output root: '+str(p))
        if p.is_file() and p.stat().st_size>1024 and p.suffix.lower() in VIDEO_SUFFIXES:
            paths.append(p)
    paths=list(dict.fromkeys(paths))
    if len(paths)!=1:
        raise ValueError('Expected exactly one saved video within ComfyUI/output; found '+str(len(paths))+'; VHS values '+repr(names)[:250])
    return paths[0]


def video_meta(path):
    command=['ffprobe','-v','error','-count_frames','-select_streams','v:0',
             '-show_entries','stream=width,height,nb_read_frames,avg_frame_rate',
             '-of','json',str(path)]
    cp=subprocess.run(command,capture_output=True,text=True,timeout=120)
    if cp.returncode:raise RuntimeError('ffprobe failed '+cp.stderr[-500:])
    stream=(json.loads(cp.stdout).get('streams') or [None])[0]
    if not isinstance(stream,dict):raise ValueError('No readable video stream')
    parts=stream.get('avg_frame_rate','').split('/')
    if len(parts)!=2:raise ValueError('Invalid video FPS')
    n,d=(int(x) for x in parts)
    if n<=0 or d<=0:raise ValueError('Invalid video FPS')
    meta={'frames':int(stream['nb_read_frames']), 'width':int(stream['width']),
          'height':int(stream['height']), 'fps':n/d}
    if not (2<=meta['frames']<=100000 and meta['width']>0 and meta['height']>0):
        raise ValueError('Invalid video dimensions or frame count')
    return meta


def capture(saved_video,samples=8,source_class='H3_GENERATED_OBSERVATION_ONLY',
            output_root=OUTPUT_ROOT,job_root=JOB_ROOT):
    if source_class not in SOURCE_CLASSES:raise ValueError('Invalid source origin declaration')
    if type(samples) is not int or not 2<=samples<=24:raise ValueError('samples outside 2-24')
    src=resolve_vhs_video(saved_video,output_root)
    before=src.stat()
    digest=sha256_file(src)
    after=src.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise RuntimeError('Saved video changed during hashing; do not enqueue')
    metadata=video_meta(src)
    token={'source_video_path':str(src),'source_sha256':digest,
           'source_class':source_class,'samples':samples}
    jobid=hashlib.sha256(json.dumps(token,sort_keys=True).encode()).hexdigest()[:24]
    record={'version':'director-saved-shot-capture-0.6.6','phase':'PENDING_SHADOW_OBSERVATION',
            'job_id':jobid,**token,'video_meta':metadata,
            'video_size_bytes':after.st_size, 'origin_declared_not_independently_certified':True,
            'generated_footage_for_H3_visual_conditioning':False,
            'observation_authority':'NONE','automatic_editing_authorized':False,
            'continuity_handoff_authorized':False,
            'director_memory_changed':False,'h3_generation_performed':False}
    job_root=Path(job_root).resolve()
    if job_root.is_relative_to(src.parent) or job_root==src.parent:
        raise ValueError('Job directory must not be inside video source folder')
    job_root.mkdir(parents=True,exist_ok=True)
    dest=job_root/(jobid+'.json')
    data=(json.dumps(record,sort_keys=True,indent=2)+'\n').encode()
    if dest.exists():
        if dest.read_bytes()!=data:raise ValueError('Existing job id has different content')
        return dest,record,False
    try:
        with dest.open('xb') as f:f.write(data)
    except FileExistsError:
        if dest.read_bytes()!=data:raise ValueError('Concurrent job id collision')
        return dest,record,False
    return dest,record,True
