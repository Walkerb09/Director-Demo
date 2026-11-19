#!/usr/bin/env python3
"""Independent CPU-only high-contrast scene-boundary evidence for saved video.

No Qwen load, H3 input, edits, Director memory writes, or cut authorization.
This is a conservative high-contrast-change candidate finder, not a semantic
scene-cut classifier. Quiet on subtle edits and potentially alerts on flashes.
"""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from pathlib import Path

VERSION='director-cut-evidence-shadow-0.6.7'
DEFAULT_OUTPUT_ROOT=Path('/workspace/ComfyUI/output')
SCENE_THRESHOLD=0.20


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def scene_candidates(video, fps, frames, *, threshold=SCENE_THRESHOLD):
    """Extract ffmpeg select(scene) high-contrast PTS evidence; frame is CFR estimate."""
    if not (0 < threshold < 1):raise ValueError('Invalid threshold')
    if not (fps>0 and frames>=2):raise ValueError('Invalid stream metadata')
    # Video-only decoding: audio isn't analyzed or altered. ffmpeg's scene
    # metric is a candidate trigger, not proof of an intentional editorial cut.
    filt="select='gt(scene,{:.4f})',showinfo".format(threshold)
    cmd=['ffmpeg','-hide_banner','-nostdin','-loglevel','info','-threads','2',
         '-i',str(video),'-map','0:v:0','-vf',filt,'-an','-sn','-f','null','-']
    cp=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240)
    if cp.returncode:
        raise RuntimeError('Scene evidence ffmpeg failed: '+cp.stderr.decode('utf-8','replace')[-800:])
    log=cp.stderr.decode('utf-8','replace')
    hits=[]
    for line in log.splitlines():
        if 'Parsed_showinfo' not in line or ' pts_time:' not in line or ' n:' not in line:continue
        m=re.search(r'\bpts_time:\s*(-?\d+(?:\.\d+)?)',line)
        if not m:continue
        seconds=float(m.group(1))
        approximate=round(seconds*fps)
        if not (1<=approximate<frames):continue
        hits.append({'frame_estimate':approximate,'timestamp_seconds':round(seconds,6),
                     'metric':'FFMPEG_SCENE_SCORE_ABOVE_THRESHOLD','threshold':threshold,
                     'not_proof_of_edit':True})
    return list({(r['frame_estimate'],r['timestamp_seconds']):r for r in hits}.values())


def audit_report(report_path, *, output_path=None, output_root=DEFAULT_OUTPUT_ROOT, threshold=SCENE_THRESHOLD):
    report_path=Path(report_path).resolve(strict=True)
    data=json.loads(report_path.read_text())
    if (data.get('preprocess_only') or data.get('h3_visual_conditioning') is not False or
        data.get('automatic_editing_authorized') is not False or
        data.get('audit',{}).get('continuity_handoff_authorized') is not False):
        raise ValueError('Expected completed read-only video observation')
    if data.get('version')!='director-geometry-semantic-observer-0.6.5':
        raise ValueError('Unsupported semantic observer version')
    video=Path(data['source_path']).resolve(strict=True)
    root=Path(output_root).resolve(strict=True)
    if not video.is_relative_to(root):raise ValueError('Source is outside ComfyUI output')
    video_hash=sha256(video)
    if video_hash!=data.get('source_sha256'):raise ValueError('Video hash mismatches observation; refusing cut correlation')
    meta=data['video_meta']
    if not isinstance(meta,dict):raise ValueError('Missing video metadata')
    # ffprobe verifies the video source matches the recorded metadata without loading a GPU model.
    cmd=['ffprobe','-v','error','-count_frames','-select_streams','v:0',
         '-show_entries','stream=width,height,nb_read_frames,avg_frame_rate','-of','json',str(video)]
    cp=subprocess.run(cmd,text=True,capture_output=True,timeout=100)
    if cp.returncode:raise RuntimeError('ffprobe failed: '+cp.stderr[-700:])
    st=(json.loads(cp.stdout).get('streams') or [None])[0]
    if not st:raise ValueError('No video stream')
    num,den=(int(v) for v in st['avg_frame_rate'].split('/'))
    actual={'frames':int(st['nb_read_frames']),'width':int(st['width']),
            'height':int(st['height']),'fps':num/den}
    if actual!=meta:raise ValueError('Video metadata changed since observation')
    cuts=scene_candidates(video,meta['fps'],meta['frames'],threshold=threshold)
    samples=data.get('sampled_frames',[])
    if not isinstance(samples,list) or len(samples)<2 or samples!=sorted(set(samples)):
        raise ValueError('Bad sampled frames in observation report')
    intervals=[]
    for a,b in zip(samples,samples[1:]):
        crossing=[c['frame_estimate'] for c in cuts if a < c['frame_estimate'] <= b]
        if crossing:
            intervals.append({'sampled_from':a,'sampled_to':b,'candidate_boundaries_inside':crossing,
                              'semantic_transition_must_not_bridge_this_interval':True})
    raw_obs=data.get('observations',{})
    invalid=[f for f in samples if not raw_obs.get(str(f),{}).get('parsed',{}).get('valid')]
    output={
        'version':VERSION,'phase':'CPU_ONLY_SHADOW_CUT_EVIDENCE',
        'source_video_path':str(video),'source_sha256':video_hash,
        'source_observation_report_path':str(report_path),'source_observation_sha256':sha256(report_path),
        'source_frames':meta['frames'],'fps':meta['fps'],
        'candidate_cuts':cuts,'sample_intervals_crossing_candidate_cuts':intervals,
        'invalid_qwen_sample_frames':invalid,
        'detector_scope':'CONSERVATIVE_HIGH_CONTRAST_CHANGES_ONLY',
        'frame_estimate_assumes_constant_fps':True,
        'possible_flash_or_hard_motion_false_positive':True,
        'subtle_cuts_may_be_missed':True,
        'intentional_vs_unintentional_cut_not_determined':True,
        'no_semantic_cut_claim_without_independent_review':True,
        'source_audio_not_analyzed':True,
        'automatic_editing_authorized':False,'continuity_handoff_authorized':False,
        'director_memory_changed':False,'h3_generation':False,'h3_visual_conditioning':False
    }
    destination=Path(output_path) if output_path else report_path.parent/'cut_evidence.json'
    if destination.resolve()==report_path:raise ValueError('Refuse to overwrite Qwen evidence')
    encoded=json.dumps(output,sort_keys=True,indent=2)+'\n'
    if destination.exists():
        if destination.read_text()!=encoded:raise FileExistsError('Cut evidence already exists with different content: '+str(destination))
        print('CUT_EVIDENCE_ALREADY_IDENTICAL',destination,flush=True)
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('x') as f:f.write(encoded)
        print('CUT_EVIDENCE_SAVED',destination,flush=True)
    print('CUT_CANDIDATE_FRAMES',','.join(str(c['frame_estimate']) for c in cuts) or 'NONE',flush=True)
    print('CUT_CROSSING_INTERVALS',len(intervals),flush=True)
    return destination


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT_ROOT)
    args=ap.parse_args()
    audit_report(args.report,output_path=args.output,output_root=args.output_root)

if __name__=='__main__':main()
