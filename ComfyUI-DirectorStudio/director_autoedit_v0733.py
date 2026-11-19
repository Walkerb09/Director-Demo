#!/usr/bin/env python3
"""H3 Director v0.7.33: source-bound, deterministic AUTO EDIT **audition**.

Real automated edit selection AND real A/V rendering. Reuses verified V0.7.19
source/provenance checks and V0.7.21 audio decoder. One user-approved outgoing
working take is held intact; all incoming edits are isolated sandbox drafts.
No H3/ComfyUI/Qwen, no production/memory writes, no authority to approve cuts.
"""
from __future__ import annotations
import argparse, hashlib, html, json, math, os, shutil, subprocess
from pathlib import Path
import cv2
import numpy as np
import editorial_candidates_v0719 as scout
import editorial_audio_v0721 as audio

SCHEMA='director-autoeditor-source-bound-audition-v0733'
FPM=24
LABEL='DIRECTOR_AUTOMATED_DRAFT_REVIEW_ONLY_NOT_APPROVED'
class AutoEditError(ValueError):pass

def sha(p):return scout.sha(p)
def exact(path, expected):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise AutoEditError('SOURCE_MISSING_OR_SYMLINK')
    result=scout.validate_source(path,expected)
    if result['frames']!=124 or abs(result['fps']-24)>0.01 or (result['width'],result['height'])!=(768,1344):
        raise AutoEditError('UNSUPPORTED_SOURCE_METADATA_OR_FRAMERATE')
    sound=audio.probe(path)
    if sound['audio_track_count']!=1:raise AutoEditError('ONE_AUDIO_TRACK_REQUIRED')
    return result

def frames(path):
    cap=cv2.VideoCapture(str(path));res=[]
    try:
        while True:
            ok,img=cap.read()
            if not ok:break
            # Small LAB pictures for cross-shot composition (NOT optical-flow/action proof).
            res.append(cv2.cvtColor(cv2.resize(img,(144,252),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2LAB))
    finally:cap.release()
    if len(res)!=124:raise AutoEditError('FRAME_DECODE_COUNT_MISMATCH')
    return np.asarray(res,dtype=np.float32)

def rms(pcm,t0,t1):
    s=max(0,int(round(t0*audio.SR)));e=min(len(pcm),int(round(t1*audio.SR)))
    if e<=s:return None
    a=pcm[s:e].astype(np.float64)
    return float(np.sqrt(np.mean(a*a)))

def normalized(vals):
    v=np.array(vals,dtype=np.float64); low,high=np.percentile(v,[5,95])
    if high-low<=1e-10:return np.zeros(len(v))
    return np.clip((v-low)/(high-low),0,1)

def select(outgoing:Path,incoming:Path,expected_o:str,expected_i:str,case:dict):
    # Source/algorithm contract is wholly independent of historical selected F42.
    if case.get('schema')!='director-edit-case-v0733' or case.get('benchmark_not_in_case_by_design') is not True or case.get('no_generated_video_visual_conditioning_for_h3') is not True:
        raise AutoEditError('INVALID_CASE_SCHEMA_OR_CLEAN_CHAIN')
    if set(case)!={'schema','scene_id','outgoing','incoming','candidate_policy','internal_candidate_barriers_outgoing','internal_candidate_barriers_incoming','benchmark_not_in_case_by_design','no_generated_video_visual_conditioning_for_h3'}:
        raise AutoEditError('CASE_UNEXPECTED_FIELDS_REFUSED')
    for k in ('outgoing','incoming'):
        if case[k].get('source_class')!='H3_GENERATED_OBSERVATION_AND_EDITORIAL_ONLY':
            raise AutoEditError('SOURCE_CLASS_REFUSED')
    o=exact(outgoing,expected_o);i=exact(incoming,expected_i)
    if case['outgoing'].get('human_working_status')!='SHOT1_APPROVED_WORKING_NOT_FINAL':
        raise AutoEditError('OUTGOING_WORKING_STATUS_INVALID')
    if case['outgoing'].get('retain_all_frames') is not True:
        raise AutoEditError('WORKING_OUTGOING_MUST_BE_PRESERVED')
    if case.get('internal_candidate_barriers_outgoing')!=[] or case.get('internal_candidate_barriers_incoming')!=[]:
        raise AutoEditError('INTERNAL_CUT_BARRIERS_REQUIRE_REVIEW')
    # Candidate range is a declared editorial experiment, not an inferred human preference.
    c=case.get('candidate_policy',{})
    if any(type(c.get(k)) is not int for k in ('start','stop_exclusive','step')):
        raise AutoEditError('INVALID_CANDIDATE_POLICY')
    if c['start']<12 or c['stop_exclusive']>80 or not 1<=c['step']<=6:
        raise AutoEditError('UNSAFE_CANDIDATE_POLICY')
    candidates=list(range(c['start'],c['stop_exclusive'],c['step']))
    if not 6<=len(candidates)<=30 or len(candidates)!=len(set(candidates)):
        raise AutoEditError('BAD_ENTRY_GRID')
    fa,fb=frames(outgoing),frames(incoming)
    ao,ai=audio.pcm_decode(outgoing,124/24),audio.pcm_decode(incoming,124/24)
    if len(ao)<(124/24-.05)*audio.SR or len(ai)<(124/24-.05)*audio.SR:
        raise AutoEditError('AUDIO_TOO_SHORT_FOR_FRAME_ACCURATE_EDIT')
    last=fa[-1]; metrics=[]
    for f in candidates:
        # All inputs for candidate F are self-contained in incoming or final outgoing frame.
        composition=float(np.mean(np.abs(last-fb[f])))
        motion=float(np.mean(np.abs(fb[f+1]-fb[f-1]))/2)
        tm=f/24
        before=rms(ai,tm-.075,tm);after=rms(ai,tm,tm+.075)
        if before is None or after is None:raise AutoEditError('AUDIO_WINDOW_UNAVAILABLE')
        metrics.append({'entry_frame':f,'incoming_before_rms':before,'incoming_after_rms':after,
            'cross_shot_composition_lab_mad':composition,'incoming_local_motion_lab_mad':motion})
    weights={'cross_shot_composition_lab_mad':.40,'incoming_local_motion_lab_mad':.15,
             'incoming_before_rms':.25,'incoming_after_rms':.20}
    for metric,w in weights.items():
        v=normalized([m[metric] for m in metrics])
        for m,x in zip(metrics,v):m[metric+'_scaled']=round(float(x),6)
    for m in metrics:
        m['technical_cost']=round(sum(weights[k]*m[k+'_scaled'] for k in weights),6)
        m['possible_conversation_or_action_interruption']='UNKNOWN'
        m['performance_naturalness']='UNKNOWN'
    ordered=sorted(metrics,key=lambda x:(x['technical_cost'],x['entry_frame']))
    best=ordered[0]
    # No calibrated naturalness classifier exists, so a mechanical winner is NOT an artistic approval.
    report={'schema':SCHEMA,'mode':LABEL,'selector':'DETERMINISTIC_WEIGHTED_TECHNICAL_COST',
        'inputs':{'outgoing':{**o,'filename':outgoing.name},'incoming':{**i,'filename':incoming.name}},
        'audio':{'outgoing':audio.probe(outgoing),'incoming':audio.probe(incoming)},
        'edited_source_policy':{'outgoing_retained_frames':[0,124], 'incoming_start_frame':best['entry_frame'],
                                'incoming_end_exclusive':124,'frame_rate':24},
        'weights':weights,'normalization':'5th-to-95th percentile, within one scene-family candidate grid',
        'selection':{'incoming_entry_frame':best['entry_frame'],'technical_cost':best['technical_cost'],
                     'runner_up':ordered[1]['entry_frame'],'cost_gap':round(ordered[1]['technical_cost']-best['technical_cost'],6)},
        'candidates':ordered,
        'reference_to_human_approval_consulted_during_ranking':False,
        'cannot_infer':['speaker identity','spoken content','naturalness','gestures','emotional continuity'],
        'outgoing_pause_policy':'PRESERVE_COMPLETE_HUMAN_APPROVED_WORKING_SHOT1_WITH_ALL_AUDIO',
        'filmmaking_status':'AUTONOMOUS_TECHNICAL_DRAFT_NEEDS_HUMAN_EDITORIAL_REVIEW',
        'authorization':{'review_only_auto_render':True,'production_edit':False,'working_edit_approval':False,
            'final_film_approval':False,'continuity_handoff':False,'director_memory_update':False,
            'h3_generation':False,'generated_video_for_h3_visual_conditioning':False},
        'limitations':['One two-shot scene family is NOT calibration for naturalness',
            'Color/position mismatch across shots is not semantic actor continuity',
            'Acoustic RMS is not speech detection; quiet can be intentional',
            'Audiovisual encoder seams may have tiny re-encode differences',
            'Selection is deterministic but heuristic: this is a candidate, not a validated artistic winner']}
    return report

def ffprobe(path):
    p=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,nb_frames,avg_frame_rate,r_frame_rate,channels',
                      '-of','json',str(path)],capture_output=True,text=True,timeout=20,check=True)
    return json.loads(p.stdout)['streams']

def render(outgoing:Path,incoming:Path,entry:int,dest:Path):
    if type(entry) is not int or not 1<=entry<=80:raise AutoEditError('ENTRY_OUT_OF_RANGE')
    if dest.exists() or dest.is_symlink():raise FileExistsError('OUTPUT_ALREADY_EXISTS')
    dur=124/24; st=entry/24
    import director_v0739 as _dv
    W,H=_dv.frame_size(outgoing)
    norm=f',scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p'
    fg=(f'[0:v]trim=start_frame=0:end_frame=124,setpts=PTS-STARTPTS{norm}[v0];'
        f'[1:v]trim=start_frame={entry}:end_frame=124,setpts=PTS-STARTPTS{norm}[v1];'
        f'[0:a]atrim=start=0:end={dur:.9f},apad=pad_dur=0.03,atrim=duration={dur:.9f},asetpts=PTS-STARTPTS[a0];'
        f'[1:a]atrim=start={st:.9f}:end={dur:.9f},apad=pad_dur=0.03,'
        f'atrim=duration={dur-st:.9f},asetpts=PTS-STARTPTS[a1];'
        f'[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]')
    cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-n','-i',str(outgoing),'-i',str(incoming),
         '-filter_complex',fg,'-map','[v]','-map','[a]','-c:v','libx264','-preset','fast',
         '-crf','17','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-ar','32000','-ac','2',
         '-movflags','+faststart',str(dest)]
    try:subprocess.run(cmd,check=True,timeout=180,capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise AutoEditError('FFMPEG_RENDER_FAILED: '+exc.stderr.decode(errors='replace')[-2000:]) from exc
    streams=ffprobe(dest); video=[s for s in streams if s['codec_type']=='video']; aud=[s for s in streams if s['codec_type']=='audio']
    expected=124+(124-entry)
    if len(video)!=1 or len(aud)!=1 or int(video[0]['nb_frames'])!=expected or video[0]['r_frame_rate']!='24/1':
        raise AutoEditError('RENDER_STREAM_OR_FRAME_COUNT_MISMATCH')
    # Frame-alignment checks at both sides of the source join; do not infer performance.
    def get_frames(path,indices):
        cap=cv2.VideoCapture(str(path));d={}
        try:
            for n in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES,n)
                ok,im=cap.read()
                if not ok:raise AutoEditError('RENDER_DECODE_FAILED')
                d[n]=cv2.resize(im,(144,252))
        finally:cap.release()
        return d
    o=get_frames(outgoing,[0,100,123]);i=get_frames(incoming,[entry,entry+12,123]);d=get_frames(dest,[0,100,123,124,136,expected-1])
    samples=[('shot1_f0',d[0],o[0]),('shot1_f100',d[100],o[100]),('shot1_f123',d[123],o[123]),
             ('shot2_entry',d[124],i[entry]),('shot2_plus12',d[136],i[entry+12]),
             ('shot2_last',d[expected-1],i[123])]
    errors={name:round(float(np.mean(np.abs(a.astype(np.float32)-b.astype(np.float32)))),3) for name,a,b in samples}
    if max(errors.values())>12.0:raise AutoEditError('RENDER_FRAME_CORRESPONDENCE_FAILED')
    # Verify ACTUAL audible PCM source correspondence on both sides, beyond track existence.
    # These are source-relative A/V positions, not lip-synchronization or speech judgements.
    outpcm=audio.pcm_decode(dest,expected/24)
    pcm0=audio.pcm_decode(outgoing,124/24)
    pcm1=audio.pcm_decode(incoming,124/24)
    windows=[('shot1_early',pcm0,0.50,0.50,0.60),
             ('shot2_response',pcm1,2.70,124/24+(2.70-entry/24),0.55)]
    matches=[]
    for label,src,t_source,t_output,duration in windows:
        def portion(arr,start):
            a=int(round(start*audio.SR));b=a+int(round(duration*audio.SR))
            if b>len(arr):raise AutoEditError('AUDIO_CORRESPONDENCE_WINDOW_OUT_OF_RANGE')
            return arr[a:b].astype(np.float64)
        x=portion(src,t_source);y=portion(outpcm,t_output)
        if np.std(x)<=0.005 or np.std(y)<=0.005:
            raise AutoEditError('AUDIO_VERIFICATION_WINDOW_NOT_AUDIBLE')
        corr=float(np.corrcoef(x,y)[0,1])
        if not np.isfinite(corr) or corr<0.96:raise AutoEditError('AUDIO_SOURCE_CORRESPONDENCE_FAILED')
        matches.append({'window':label,'decoded_pcm_correlation':round(corr,5)})
    return {'frames':expected,'fps':24,'audio_stream_count':len(aud),'source_correspondence_mad':errors,'sha256':sha(dest),
            'audible_pcm_source_matches':matches,
            'audio_alignment':'SOURCE_PCM_CORRESPONDENCE_VERIFIED_TWO_AUDIBLE_WINDOWS_NOT_LIPSYNC_VERIFIED'}

def benchmark(report,approval_path):
    # Called ONLY after the ranking and the render are complete.
    if approval_path is None:return {'status':'NOT_PROVIDED'}
    appr=json.loads(Path(approval_path).read_text())
    if appr.get('schema')!='director-editorial-working-selection-v1' or appr.get('status')!='HUMAN_APPROVED_WORKING_EDIT_IN_CHAT_NOT_FINAL':
        raise AutoEditError('BENCHMARK_SCHEMA_NOT_APPROVED_WORKING')
    timeline=appr.get('timeline',[])
    if len(timeline)!=2 or any(timeline[j]['source_sha256']!=report['inputs'][('outgoing','incoming')[j]]['sha256'] for j in range(2)):
        raise AutoEditError('BENCHMARK_SOURCE_MISMATCH')
    f=timeline[1]['source_in_frame']
    if type(f) is not int:raise AutoEditError('BENCHMARK_INVALID_FRAME')
    return {'status':'POST_SELECTION_COMPARATOR_ONLY','human_approved_working_entry_frame':f,
      'machine_proposal_entry_frame':report['selection']['incoming_entry_frame'],
      'delta_frames':report['selection']['incoming_entry_frame']-f,
      'human_working_preference_never_used_for_scoring':True,'human_preference_not_overwritten':True}

def run(case_path:Path, shot1:Path, shot2:Path, out:Path, approval:Path|None=None):
    case=json.loads(case_path.read_text()); expected_o=case['outgoing']['sha256'];expected_i=case['incoming']['sha256']
    # No benchmark file/path appears in `select` or its case input.
    report=select(shot1,shot2,expected_o,expected_i,case)
    if out.exists() or out.is_symlink():raise FileExistsError('OUTPUT_DIR_ALREADY_EXISTS')
    stage=out.with_name(out.name+'_STAGING')
    if stage.exists() or stage.is_symlink():raise FileExistsError('STAGING_DIR_ALREADY_EXISTS')
    stage.mkdir(parents=True)
    try:
        entry=report['selection']['incoming_entry_frame']
        clip=stage/f'Director_AutoEdit_F{entry:02d}_REVIEW_ONLY.mp4'
        report['render_validation']=render(shot1,shot2,entry,clip)
        if sha(shot1)!=expected_o or sha(shot2)!=expected_i:
            raise AutoEditError('SOURCES_CHANGED_DURING_RENDER')
        report['post_selection_benchmark']=benchmark(report,approval)
        (stage/'decision.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n')
        items=''.join(f'<tr><td>F{m["entry_frame"]}</td><td>{m["technical_cost"]:.4f}</td><td>{m["cross_shot_composition_lab_mad"]:.2f}</td><td>{m["incoming_local_motion_lab_mad"]:.2f}</td><td>{m["incoming_before_rms"]:.4f}</td><td>{m["incoming_after_rms"]:.4f}</td></tr>' for m in report['candidates'][:12])
        b=report['post_selection_benchmark']
        bench=(f'Human working F{b["human_approved_working_entry_frame"]}; proposed F{entry}; delta {b["delta_frames"]:+d} frames.' if b['status']=='POST_SELECTION_COMPARATOR_ONLY' else 'No human comparator supplied.')
        page=f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Director Automated Edit v0.7.33</title><style>body{{max-width:900px;margin:40px auto;padding:0 15px;font:16px system-ui;background:#111924;color:#f1f3f7;line-height:1.5}}video{{max-width:400px;width:100%}}section{{background:#1b2b3d;padding:18px;border-radius:12px}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid #42546c;padding:7px;text-align:left}}.muted{{color:#aec0d2}}</style><h1>Director's first automated edit</h1><section><p><b>Machine proposed F{entry}</b> (technical-cost heuristic, uncalibrated artistic quality).</p><video controls src="{html.escape(clip.name)}"></video><p>{html.escape(bench)}</p><p class="muted">Preserves the full human-approved first shot and its intentional pause. The automated edit is REVIEW ONLY. No production or memory approval; no H3 generation or conditioning.</p></section><h2>Ranked technical candidates</h2><table><tr><th>Frame</th><th>Cost ↓</th><th>Visual seam</th><th>Local motion</th><th>Audio before</th><th>Audio after</th></tr>{items}</table><p class="muted">Visual LAB difference and source-local optical change are NOT narrative continuity judgments. RMS isn't speech detection. Candidate ranking did not consult the human cut.</p>'''
        (stage/'index.html').write_text(page)
        manifest={'files':{p.name:sha(p) for p in sorted(stage.iterdir()) if p.is_file()}}
        (stage/'SHA256_OUTPUTS.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        os.replace(stage,out)
        print('V0733_AUTOMATED_EDIT_RENDERED_REVIEW_ONLY','F'+str(entry),'frames='+str(report['render_validation']['frames']))
        print('BENCHMARK',report['post_selection_benchmark'])
        print('OUTPUT',out)
        return report
    except Exception:
        shutil.rmtree(stage,ignore_errors=True)
        raise

def cli():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',required=True,type=Path);p.add_argument('--shot1',required=True,type=Path)
    p.add_argument('--shot2',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--benchmark',type=Path)
    a=p.parse_args();run(a.case,a.shot1,a.shot2,a.out,a.benchmark)
if __name__=='__main__':cli()
