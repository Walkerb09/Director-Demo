#!/usr/bin/env python3
"""Director V0.7.21: source-hash verified acoustic boundary *review*, never speech/action truth.

Analyzes decoded PCM **within** one media source on either side of a proposed entry.
Never guesses whether a voice speaks, an action completes, or an edit is artistically good.
Never evaluates acoustic continuity ACROSS an existing/candidate cut.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import numpy as np
import editorial_candidates_v0719 as scout
import editorial_decision_v0720 as decision

SCHEMA='director-editorial-acoustic-boundary-shadow-v0721'
SR=16000
WIN_MS=20
WIN_SAMPLES=SR*WIN_MS//1000
CONTEXT_SEC=0.24
ABS_ACTIVE_DB=-42.0
RELATIVE_ABOVE_MEDIAN_DB=10.0
DENIED=scout.DENIED
class AudioEvidenceError(ValueError):pass

def json_cmd(args):
    try:
        x=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=25)
        return json.loads(x.stdout.decode('utf8'))
    except (subprocess.SubprocessError,UnicodeError,json.JSONDecodeError,OSError) as e:
        raise AudioEvidenceError('MEDIA_METADATA_FAILED') from e

def probe(path):
    if not shutil.which('ffprobe'):raise AudioEvidenceError('FFPROBE_REQUIRED')
    d=json_cmd(['ffprobe','-v','error','-show_entries','stream=codec_type,codec_name,sample_rate,channels',
                '-show_entries','format=duration','-of','json',str(path)])
    streams=d.get('streams')
    if not isinstance(streams,list):raise AudioEvidenceError('INVALID_STREAM_LIST')
    audio=[s for s in streams if s.get('codec_type')=='audio']
    if len(audio)>1:raise AudioEvidenceError('MULTIPLE_AUDIO_STREAMS_REQUIRE_EXPLICIT_SELECTION')
    return {'audio_track_count':len(audio),'audio_codec':audio[0].get('codec_name') if audio else None,
            'codec_rate':audio[0].get('sample_rate') if audio else None}

def pcm_decode(path,expected_seconds):
    if not shutil.which('ffmpeg'):raise AudioEvidenceError('FFMPEG_REQUIRED')
    try:
        cp=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                '-i',str(path),'-map','0:a:0','-vn','-ac','1','-ar',str(SR),
                '-f','f32le','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                check=True,timeout=65)
    except (subprocess.SubprocessError,OSError) as e:raise AudioEvidenceError('AUDIO_DECODE_FAILED') from e
    if len(cp.stdout)%4:raise AudioEvidenceError('AUDIO_PCM_MALFORMED')
    pcm=np.frombuffer(cp.stdout,dtype='<f4')
    if not len(pcm) or len(pcm)>SR*125 or not np.isfinite(pcm).all():
        raise AudioEvidenceError('AUDIO_PCM_EMPTY_OVERSIZED_OR_NONFINITE')
    if abs(len(pcm)/SR-expected_seconds)>1.5:
        raise AudioEvidenceError('AUDIO_VIDEO_DURATION_MISMATCH')
    return pcm

def energy_bins(pcm):
    arr=np.asarray(pcm,dtype=np.float32)
    if arr.ndim!=1 or not len(arr) or not np.isfinite(arr).all():raise AudioEvidenceError('INVALID_PCM')
    n=(len(arr)+WIN_SAMPLES-1)//WIN_SAMPLES
    pad=np.pad(arr,(0,n*WIN_SAMPLES-len(arr)))
    bins=pad.reshape(n,WIN_SAMPLES)
    rms=np.sqrt(np.mean(np.square(bins.astype(np.float64)),axis=1))
    db=20*np.log10(np.maximum(rms,1e-9))
    return db

def classify(db,entry_sec,cut_at_entry=False,segment_start_sec=0.0,segment_end_sec=None):
    """No samples from the opposite side of an internal cut are compared."""
    ix=int(round(entry_sec*1000/WIN_MS))
    radius=int(CONTEXT_SEC*1000/WIN_MS)
    start_ix=max(0,int(math.ceil(segment_start_sec*1000/WIN_MS)))
    end_ix=len(db) if segment_end_sec is None else min(len(db),int(math.floor(segment_end_sec*1000/WIN_MS)))
    before=db[max(start_ix,ix-radius):min(len(db),ix)] if not cut_at_entry else db[0:0]
    after=db[max(0,ix):min(end_ix,ix+radius)]
    # Absolute floor avoids inferring speech from very quiet electronic noise.
    floor=float(np.median(db))
    threshold=ABS_ACTIVE_DB  # Fixed, interpretable level; never suppress sustained sound just because it is common.
    def metric(part):
        if len(part)<4:return None
        return {'rms_dbfs_median':round(float(np.median(part)),2),
                'peak_window_dbfs':round(float(np.max(part)),2),
                'strong_activity_fraction':round(float(np.mean(part>=threshold)),3),
                'bins':int(len(part))}
    b=metric(before);a=metric(after)
    if cut_at_entry:b=None
    if b is None or a is None:
        label='INSUFFICIENT_WITHIN_SEGMENT_BOUNDARY_AUDIO'
    else:
        yes_b=b['strong_activity_fraction']>=0.5
        yes_a=a['strong_activity_fraction']>=0.5
        if yes_b and yes_a:label='ACOUSTIC_ACTIVITY_ON_BOTH_SIDES_REVIEW'
        elif yes_b:label='ACOUSTIC_ACTIVITY_BEFORE_ENTRY_REVIEW'
        elif yes_a:label='ACOUSTIC_ACTIVITY_AFTER_ENTRY_REVIEW'
        else:label='NO_STRONG_ACOUSTIC_ACTIVITY_UNDER_THIS_THRESHOLD'
    return {'label':label,'pre_entry':b,'post_entry':a,'threshold_dbfs':round(threshold,2),'source_median_dbfs':round(floor,2),
            'audio_does_not_establish_speech_or_silence':True,
            'no_audio_comparison_across_candidate_cut':bool(cut_at_entry),
            'not_an_artistic_quality_score':True}

def validate_entry_list(entries,n,cut_candidates):
    if not isinstance(entries,list) or not entries or len(entries)>40 or any(type(x) is not int for x in entries):
        raise AudioEvidenceError('INVALID_ENTRY_LIST')
    if len(set(entries))!=len(entries) or any(x<0 or x>=n for x in entries):raise AudioEvidenceError('INVALID_ENTRY_RANGE')
    return scout.validate_cuts(cut_candidates,n)

def verify_decision(decision_dir,scout_dir,source_hash):
    """Bind optional decision aliases through V0.7.20's verified source-pair record."""
    d=Path(decision_dir)
    if not d.is_dir() or d.is_symlink():raise AudioEvidenceError('INVALID_DECISION_DIRECTORY')
    h=decision.load_json(d/'SHA256_OUTPUTS.json').get('files')
    if not isinstance(h,dict) or not h:raise AudioEvidenceError('DECISION_OUTPUTS_UNVERIFIED')
    for name,sha in h.items():
        if name not in ('editorial_decision_v0720.json','EDITORIAL_REVIEW.md'):
            raise AudioEvidenceError('UNRECOGNIZED_DECISION_FILE')
        p=d/name
        if not p.is_file() or p.is_symlink():raise AudioEvidenceError('DECISION_MISSING_OR_UNSAFE_FILE')
        if not isinstance(sha,str) or len(sha)!=64 or scout.sha(p)!=sha:raise AudioEvidenceError('DECISION_HASH_MISMATCH')
    rep=decision.load_json(d/'editorial_decision_v0720.json')
    sr,session,records=decision.verify_scout(scout_dir)
    if (rep.get('schema')!=decision.SCHEMA or rep.get('source_scout_session_id')!=session['session_id']
            or rep.get('scout_dossier_sha256')!=scout.sha(Path(scout_dir)/'private/editorial_entry_scout_v0719.json')
            or rep.get('source_hashes',{}).get('incoming')!=source_hash):
        raise AudioEvidenceError('DECISION_SOURCE_OR_SESSION_MISMATCH')
    if rep.get('authorizations')!={k:False for k in DENIED}:raise AudioEvidenceError('DECISION_AUTHORIZATION_INVALID')
    opts=rep.get('options')
    if not isinstance(opts,list) or len(opts)!=len(records):raise AudioEvidenceError('DECISION_OPTION_COUNT_MISMATCH')
    actual={x['alias']:x['incoming_entry_frame'] for x in opts}
    verified={x['alias']:x['incoming_entry_frame'] for x in sr['technical_evidence']}
    if actual!=verified:raise AudioEvidenceError('DECISION_OPTION_FRAMES_MISMATCH')
    if len(actual)!=len(opts):raise AudioEvidenceError('DECISION_DUPLICATE_ALIAS')
    return sorted(((alias,v) for alias,v in actual.items()),key=lambda item:item[0]),session['session_id']

def build(source,sha,out,entries=None,cuts=None,decision_dir=None,scout_dir=None):
    # FAIL BEFORE CREATING OUTPUT IF MEDIA IS FOREIGN OR PLANNED CUT METADATA IS INVALID.
    meta=scout.validate_source(source,sha)
    if meta['frames']>1200:raise AudioEvidenceError('VIDEO_TOO_LONG_FOR_THIS_DIAGNOSTIC')
    if (decision_dir is None)!=(scout_dir is None):raise AudioEvidenceError('DECISION_REQUIRES_SCOUT_PAIR')
    if decision_dir is not None:
        if entries is not None or cuts is not None:raise AudioEvidenceError('DECISION_MODE_AND_MANUAL_OPTIONS_CONFLICT')
        items,session_id=verify_decision(decision_dir,scout_dir,sha)
        scout_report,_,_=decision.verify_scout(scout_dir)
        cuts=scout_report['source_provenance']['cut_candidates_incoming']
    else:
        if entries is None or cuts is None:raise AudioEvidenceError('EXPLICIT_ENTRIES_AND_CUTS_REQUIRED')
        items=[(f'Entry_{f:04d}',f) for f in entries];session_id=None
    cuts=validate_entry_list([f for _,f in items],meta['frames'],cuts)
    stream=probe(source)
    # Source is a single cut-sensitive timeline. Every candidate is a boundary, not a claim of action completion.
    pcm=None;db=None
    if stream['audio_track_count']:
        pcm=pcm_decode(source,meta['frames']/meta['fps'])
        db=energy_bins(pcm)
    options=[]
    for alias,frame in items:
        on_cut=frame in cuts
        # Never look through a previously declared cut, even for acoustic measurements.
        preceding=max((c for c in cuts if c<=frame),default=0)
        following=min((c for c in cuts if c>frame),default=meta['frames'])
        boundary=classify(db,frame/meta['fps'],on_cut,preceding/meta['fps'],following/meta['fps']) if db is not None else {
            'label':'AUDIO_TRACK_ABSENT_NOT_SILENCE', 'pre_entry':None,'post_entry':None,
            'not_an_artistic_quality_score':True}
        options.append({'alias':alias,'source_entry_frame':frame,'time_seconds':round(frame/meta['fps'],6),
                        'on_declared_candidate_cut':on_cut,'audio_review':boundary,
                        'speech_activity':'UNKNOWN','dialogue_syntax':'UNKNOWN','emotion':'UNKNOWN',
                        'cut_interrupts_speech':'UNKNOWN','performance_naturalness':'UNKNOWN'})
    result={'schema':SCHEMA,'version':'0.7.21','mode':'SHADOW_ONLY',
        'source_sha256':sha,'media':{**meta,**stream},'source_provenance':'UNVERIFIED_FOOTAGE_OBSERVATION_ONLY',
        'source_scout_session_id':session_id,'source_cut_candidates':cuts,
        'method':{'sample_rate_hz':SR,'energy_window_ms':WIN_MS,'context_seconds_each_side':CONTEXT_SEC,
                  'strong_activity_threshold_dbfs_absolute':ABS_ACTIVE_DB,
                  'source_median_dbfs_is_context_only_not_an_activity_threshold':True,
                  'energy_activity_is_not_speech_recognition':True,
                  'source_audio_no_cross_cut_inference':True},
        'options':options,'artistic_verdict':'ABSTAIN_NO_VALIDATED_DIALOGUE_OR_PERFORMANCE_JUDGMENT',
        'authorizations':{k:False for k in DENIED}, 'h3_visual_reference_eligible':False,
        'limitations':['Acoustic energy is not speech, turn-taking, lip synchronization or emotion',
            'Low-level audio is not proof of silence and absent track is not silence',
            'Frame-to-audio times may differ at encoder boundaries and must be checked before edit use',
            'Cut candidates are boundaries, not verified editorial intent',
            'No automatic audio edits, cut selection, working-approval, scene-memory changes or H3 conditioning']}
    out=Path(out);staging=out.with_name(out.name+'_staging')
    if out.exists() or out.is_symlink() or staging.exists() or staging.is_symlink():
        raise FileExistsError('REFUSE_EXISTING_OUTPUT_OR_STAGING')
    staging.mkdir(parents=True)
    (staging/'audio_boundary_v0721.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    md=['# H3 Director V0.7.21 — Source-Bound Audio Boundary Evidence','',
        '- **SHADOW ONLY. No artistic selection or audio edits authorized.**',
        f'- Source SHA-256: `{sha}`',
        f'- Audio track count: {stream["audio_track_count"]}',
        '- A loud interval is not necessarily speech. A quiet interval may still contain speech.',
        '', '| Candidate | Frame | Acoustic review evidence |','|---|---:|---|']
    for a in options:md.append(f'| {a["alias"]} | {a["source_entry_frame"]} | {a["audio_review"]["label"]} |')
    md.extend(['','**Decision: ABSTAIN.** Acoustic activity flags are for human/editorial review only.'])
    (staging/'AUDIO_REVIEW.md').write_text('\n'.join(md)+'\n')
    (staging/'SHA256_OUTPUTS.json').write_text(json.dumps({'files':{p.name:scout.sha(p) for p in staging.iterdir() if p.is_file()}},indent=2)+'\n')
    os.replace(staging,out)
    return result

def demo():
    root=Path(__file__).resolve().parent
    video=root/'audio_sources/DirectorV5396_Hallway_Shot6_SilentNod_RAW_UNAPPROVED_00001-audio.mp4'
    build(video,scout.sha(video),root/'results/v0721_audible_demo',
          entries=[0,12,24,36,48,60,72,84,96,108],cuts=[])
    silent=root/'media/shot9_suffix_from_F12_REENCODED_OBSERVATION_ONLY.mp4'
    build(silent,scout.sha(silent),root/'results/v0721_decision_audio_lane',
          decision_dir=root/'results/v0720_demo',scout_dir=root/'results/v0719_demo')
    quiet=root/'audio_sources/AnimateDiff_ActionH_ShortOriginalVideo_00001-audio.mp4'
    build(quiet,scout.sha(quiet),root/'results/v0721_low_level_demo',
          entries=[0,24,48,72,96,120,144],cuts=[])
    print('V0721_REAL_AUDIO_AND_DECISION_TRIAL_OK 3 independent media sources')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--demo',action='store_true')
    p.add_argument('--source',type=Path)
    p.add_argument('--source-sha256')
    p.add_argument('--out',type=Path)
    p.add_argument('--entries',help='comma-separated zero-based source frames')
    p.add_argument('--cuts',help='comma-separated candidate cut indices; explicit empty string means no known cuts')
    p.add_argument('--decision-dir',type=Path)
    p.add_argument('--scout-dir',type=Path)
    a=p.parse_args()
    if a.demo:return demo()
    if not a.source or not a.source_sha256 or not a.out:p.error('--source, --source-sha256 and --out are required')
    def nums(s):return [int(v) for v in s.split(',') if v.strip()]
    b=build(a.source,a.source_sha256,a.out,
            None if a.entries is None else nums(a.entries),
            None if a.cuts is None else nums(a.cuts),a.decision_dir,a.scout_dir)
    print('V0721_AUDIO_EVIDENCE_READY',a.out, 'options',len(b['options']))
if __name__=='__main__':main()
