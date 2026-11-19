#!/usr/bin/env python3
"""V0.7.16: provenance-checked, presentation-blinded editorial review ledger.

Creates an evidence-linked review UI but NEVER auto-approves an editorial cut.
Pre-existing historical human preference is excluded from the blind review inputs.
"""
from __future__ import annotations
import argparse, hashlib, html, json, os, shutil
from pathlib import Path
import cv2

ROOT=Path(__file__).resolve().parent
SCHEMA='director-editorial-calibration-v0716'
SESSION_SCHEMA='director-blinded-cut-session-v0716'
REVIEW_SCHEMA='director-human-cut-review-v0716'
REJECTED_KEYS=('automatic_edit_authorized','handoff_authorized','memory_mutation_authorized','H3_visual_conditioning_authorized',
               'performance_verified','final_film_approved')
SHADOW={k:False for k in REJECTED_KEYS}

class CalibrationError(ValueError):pass

def sha(path:Path)->str:
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def read_json(p):return json.loads(Path(p).read_text())

def atomic_json(path,obj):
    tmp=path.with_name(path.name+'.temp')
    tmp.write_text(json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=True,allow_nan=False)+'\n')
    os.replace(tmp,path)

def verify_inputs(root=ROOT):
    root=Path(root)
    def load(name):return read_json(root/'reference'/name)
    profile=load('nine_shot_ab.json')
    a=load('paired_cut_review_v0713.json')
    b=load('v0715_temporal_cut_evidence_v0715.json')
    t=load('editorial_evidence_v0714.json')
    if profile['schema']!='director-editorial-cut-profile-v0713' or profile.get('join_frame')!=692 or profile.get('fps')!=24:
        raise CalibrationError('UNEXPECTED_OR_CHANGED_PROFILE')
    ph=sha(root/'reference/nine_shot_ab.json')
    if any(x.get('profile_sha256')!=ph for x in (a,b,t)):raise CalibrationError('PROFILE_PROVENANCE_MISMATCH')
    if not (a['version']=='0.7.13' and b['version']=='0.7.15'):
        raise CalibrationError('WRONG_PRIOR_AUDIT_VERSION')
    if not (a.get('human_choice_not_used_as_optimization_target') and b.get('human_choice_not_used_as_objective')):
        raise CalibrationError('HISTORICAL_LABEL_LEAKAGE_RISK')
    prior_manifest=load('v0715_SHA256_OUTPUTS.json')
    if prior_manifest['files'].get('temporal_cut_evidence_v0715.json')!=sha(root/'reference/v0715_temporal_cut_evidence_v0715.json'):
        raise CalibrationError('V0715_REPORT_CHECKSUM_CHANGED')
    if b.get('editorial_authority')!='NO_AUTOMATIC_CUT_DECISION' or not b.get('analysis_excludes_all_cross_cut_image_pairs'):
        raise CalibrationError('PRIOR_AUDIT_UNSAFE')
    bylabel={v['label']:v for v in profile['variants']}
    if set(bylabel)!={'F12','F24','F66'} or len(profile['variants'])!=3 or len(b['variants'])!=3:
        raise CalibrationError('EXPECTED_ONE_3_VARIANT_FAMILY')
    observed={v['label']:v for v in a['variants']}
    temporals={v['label']:v for v in b['variants']}
    if set(observed)!=set(bylabel) or set(temporals)!=set(bylabel):raise CalibrationError('VARIANT_SET_MISMATCH')
    candidates={}
    for label,record in bylabel.items():
        clip=root/'clips_source'/f'{label}.mp4'
        if not clip.is_file():raise CalibrationError('MISSING_CLIP')
        h=sha(clip)
        if observed[label]['preview']['sha256']!=h:raise CalibrationError('CLIP_HASH_MISMATCH:'+label)
        if observed[label]['source_sha256']!=record['sha256'] or temporals[label]['assembled_video_sha256']!=record['sha256']:
            raise CalibrationError('ASSEMBLY_HASH_MISMATCH:'+label)
        cap=cv2.VideoCapture(str(clip))
        try:
            count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps=cap.get(cv2.CAP_PROP_FPS)
            success,frame=cap.read()
        finally:cap.release()
        if not success or frame is None or count!=48 or abs(fps-24)>0.05 or frame.shape[0]<100:
            raise CalibrationError('CLIP_DECODING_OR_METADATA_MISMATCH:'+label)
        if observed[label]['preview']['preview_frames']!=48 or observed[label]['preview']['pre_join_frames']!=12 or observed[label]['preview']['post_join_frames']!=36:
            raise CalibrationError('INVALID_CLIP_CUT_POSITION')
        if temporals[label]['action_annotations']['status']!='NO_INDEPENDENT_ACTION_LABELS' or temporals[label]['automatic_edit_authorized']:
            raise CalibrationError('ACTION_LABELS_UNVERIFIED')
        candidates[label]={'clip_path':clip,'clip_sha256':h,'assembly_sha256':record['sha256'],
                          'trim':record['original_shot9_trim']}
    # All alternates draw from the same incoming shot and same outgoing scene; not independent data points.
    same_out={json.dumps(v['outgoing'],sort_keys=True) for v in b['variants']}
    if len(same_out)!=1:raise CalibrationError('OUTGOING_SCENE_DIFFERS')
    if b['joint_predecessor_frame_count']!=profile['join_frame'] or a['predecessor_sha256']!=profile['predecessor']['sha256']:
        raise CalibrationError('PREDECESSOR_PROVENANCE_MISMATCH')
    return {'profile_sha256':ph,'source_sha256':profile['predecessor']['sha256'],
            'family_id':'sha256:'+hashlib.sha256((profile['predecessor']['sha256']+'|'+str(profile['join_frame'])+'|shot9').encode()).hexdigest(),
            'candidates':candidates,'historical_working_choice':profile['human_working_choice']}

def blind_aliases(candidates,family_id):
    labels=sorted(candidates,key=lambda l: hashlib.sha256((family_id+'|REVIEW_ONLY_ALIAS_SEED_0716|'+candidates[l]['clip_sha256']).encode()).hexdigest())
    return dict(zip(['A','B','C'],labels))

HTML='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Director: Independent cut review</title>
<style>body{background:#151719;color:#f1f2f3;font:16px system-ui;max-width:1000px;margin:30px auto;padding:0 15px}h1{font-size:25px}p{line-height:1.45;color:#bec5ce}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.card{background:#24282e;border-radius:12px;padding:12px}video{width:100%;border-radius:8px}label{display:block;margin:14px 0 6px}select,input,textarea,button{background:#343942;color:white;border:1px solid #69717c;padding:9px;border-radius:7px;width:100%;box-sizing:border-box}button{cursor:pointer;background:#eef0f4;color:#131518;font-weight:bold;margin:18px 0;max-width:300px}.tiny{font-size:13px;color:#a6b0c0}</style></head>
<body><h1>Editorial seam review — anonymous alternatives</h1><p>Three two-second excerpts share the same incoming scene and outgoing cut. Watch each multiple times at normal speed. No sound is present. Rate only what is actually visible. All options are from one scene, not independent examples.</p>
<div class="grid"><div class="card"><b>Option A</b><video controls preload="metadata" src="clips/A.mp4"></video></div><div class="card"><b>Option B</b><video controls preload="metadata" src="clips/B.mp4"></video></div><div class="card"><b>Option C</b><video controls preload="metadata" src="clips/C.mp4"></video></div></div>
<label for="rid">Reviewer alias (non-identifying)</label><input id="rid" placeholder="reviewer-01" maxlength="64">
<label for="choice">Most natural cut, if discernible</label><select id="choice"><option value="UNSURE">Cannot tell</option><option value="A">A</option><option value="B">B</option><option value="C">C</option><option value="TIE">No meaningful difference</option></select>
<div id="rating"></div>
<label for="notes">What do you actually see? Optional observations</label><textarea id="notes" rows="3" maxlength="1000" placeholder="e.g. observable interruption; do not invent dialogue"></textarea>
<button id="save">Save independent review JSON</button><p class="tiny">This saves a local JSON file for optional later aggregation; it does NOT approve any edit. No submission or external service is used. Unknown is a valid answer. Reviewers should not see the historical working choice or the option mapping before rating.</p>
<script>
for(const id of ['A','B','C']){const div=document.createElement('div');div.innerHTML='<label for="i'+id+'">Does option '+id+' visibly interrupt an action?</label><select id="i'+id+'"><option value="UNKNOWN">Unknown</option><option value="YES">Yes</option><option value="NO">No</option></select>';document.getElementById('rating').append(div)}
document.getElementById('save').onclick=()=>{const reviewer_id=document.getElementById('rid').value.trim();if(!/^[a-zA-Z0-9_-]{3,64}$/.test(reviewer_id)){alert('Please use a 3–64 character non-identifying reviewer alias.');return}const data={schema:'director-human-cut-review-v0716',session_id:'__SESSION__',family_id:'__FAMILY__',reviewer_id,review_basis:'VISUAL_ONLY',preferred:document.getElementById('choice').value,visible_action_interrupted:Object.fromEntries(['A','B','C'].map(x=>[x,document.getElementById('i'+x).value])),notes:document.getElementById('notes').value.trim(),self_reported_independent_human_review:true};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='review_'+reviewer_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
</script></body></html>'''

def evidence_sidecar(root,order):
    # Private, source-derived measurements. No aesthetic ranking or preference prediction.
    source=read_json(Path(root)/'reference/v0715_temporal_cut_evidence_v0715.json')
    records={v['label']:v for v in source['variants']}
    return {'schema':'director-technical-context-v0716',
            'motion_units':'px_per_frame_at_192px_normalized_width',
            'no_actor_action_or_audio_ground_truth':True,
            'no_technical_metric_used_as_artistic_label':True,
            'options':{a:{'incoming_first_six_motion_dispersion':records[lab]['incoming']['first_six_flow_deviation_px'],
                         'outgoing_last_six_motion_dispersion':records[lab]['outgoing']['last_six_flow_deviation_px'],
                         'incoming_flow_coverage':records[lab]['incoming']['flow_coverage'],
                         'audio_stream':records[lab]['assembly_audio']['status'],
                         'action_completion':'UNKNOWN', 'automatic_edit_authorized':False}
                       for a,lab in order.items()}}

def make_session(root=ROOT, output=None):
    root=Path(root)
    data=verify_inputs(root)
    if output is None:output=root/'results/editorial_calibration_v0716'
    out=Path(output)
    stage=out.with_name(out.name+'_staging')
    if any(p.exists() or p.is_symlink() for p in (out,stage)):raise FileExistsError('REFUSE_OVERWRITE_OR_STAGING')
    order=blind_aliases(data['candidates'],data['family_id'])
    sid=hashlib.sha256((data['family_id']+'|'+','.join(data['candidates'][order[x]]['clip_sha256'] for x in 'ABC')).encode()).hexdigest()[:24]
    stage.mkdir(parents=True)
    try:
        (stage/'clips').mkdir();(stage/'private').mkdir();(stage/'independent_reviews').mkdir()
        opts={}
        for alias,label in order.items():
            c=data['candidates'][label]
            dest=stage/'clips'/f'{alias}.mp4'
            shutil.copyfile(c['clip_path'],dest)
            if sha(dest)!=c['clip_sha256']:raise CalibrationError('COPY_CHECKSUM_ERROR')
            opts[alias]={'clip':f'clips/{alias}.mp4','sha256':c['clip_sha256'],'duration_s':2.0,'audio':'NO_AUDIO_STREAM'}
        sess={'schema':SESSION_SCHEMA,'session_id':sid,'family_id':data['family_id'],'scene_family_count':1,
              'source_type':'ARCHIVED_GENERATED_EDITS_OBSERVATION_ONLY','fps':24,'join_in_excerpt_at_frame':12,
              'options':opts,'historical_choice_not_exposed_to_reviewer':True,
              'independence_warning':'All three edits derive from the same incoming performance and same preceding scene',
              'reviewer_inputs_unverified_until_independently_checked':True,
              'authorizations':SHADOW,'no_visual_inputs_to_H3':True}
        atomic_json(stage/'review_session.json',sess)
        sidecar=evidence_sidecar(root,order)
        atomic_json(stage/'private/technical_evidence_NOT_FOR_REVIEWERS.json',sidecar)
        atomic_json(stage/'private/alias_provenance_NOT_FOR_REVIEWERS.json',{'session_id':sid,'family_id':data['family_id'],
            'source_predecessor_sha256':data['source_sha256'],'profile_sha256':data['profile_sha256'],
            'alias_to_archived_edit':{alias:{'archive_label':lab,'original_trim':data['candidates'][lab]['trim'],
              'assembly_sha256':data['candidates'][lab]['assembly_sha256']} for alias,lab in order.items()},
            'historical_working_choice_context_not_scoring_target':data['historical_working_choice']})
        (stage/'index.html').write_text(HTML.replace('__SESSION__',sid).replace('__FAMILY__',data['family_id']))
        empty={'schema':REVIEW_SCHEMA,'session_id':sid,'family_id':data['family_id'],'reviewer_id':'reviewer-01',
            'review_basis':'VISUAL_ONLY','preferred':'UNSURE','visible_action_interrupted':{x:'UNKNOWN' for x in 'ABC'},
            'notes':'','self_reported_independent_human_review':False}
        atomic_json(stage/'independent_reviews/TEMPLATE_DO_NOT_TREAT_AS_RATING.json',empty)
        review_status=assess_reviews(sess,[],require_real_evidence=True)
        atomic_json(stage/'calibration_status_v0716.json',review_status)
        write_checksums(stage)
        os.replace(stage,out)
        return sess,review_status
    except Exception:
        # Preserve the partially staged directory for troubleshooting, never overwrite it.
        raise

def validate_review(session,r):
    expected={'schema','session_id','family_id','reviewer_id','review_basis','preferred','visible_action_interrupted','notes','self_reported_independent_human_review'}
    if not isinstance(r,dict) or set(r)!=expected:raise CalibrationError('REVIEW_SCHEMA_INVALID')
    if r['schema']!=REVIEW_SCHEMA or r['session_id']!=session['session_id'] or r['family_id']!=session['family_id']:
        raise CalibrationError('REVIEW_WRONG_SESSION_OR_FAMILY')
    rid=r['reviewer_id']
    if not isinstance(rid,str) or not 3<=len(rid)<=64 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in rid):
        raise CalibrationError('BAD_REVIEWER_ALIAS')
    if r['review_basis']!='VISUAL_ONLY':raise CalibrationError('SILENT_FOOTAGE_CANNOT_SUPPORT_AUDIO_REVIEW')
    if r['preferred'] not in ('A','B','C','TIE','UNSURE'):raise CalibrationError('BAD_CHOICE')
    if not isinstance(r['visible_action_interrupted'],dict) or set(r['visible_action_interrupted'])!=set(session['options']):
        raise CalibrationError('MISSING_ACTION_RATINGS')
    if any(x not in ('YES','NO','UNKNOWN') for x in r['visible_action_interrupted'].values()):
        raise CalibrationError('BAD_ACTION_RATING')
    if not isinstance(r['notes'],str) or len(r['notes'])>1000:raise CalibrationError('BAD_NOTES')
    if r['self_reported_independent_human_review'] is not True:raise CalibrationError('HUMAN_REVIEW_NOT_ATTESTED')
    return r

def assess_reviews(session,reviews,require_real_evidence=True):
    if session['schema']!=SESSION_SCHEMA or session['scene_family_count']!=1 or session['authorizations']!=SHADOW:
        raise CalibrationError('UNTRUSTED_OR_CHANGED_SESSION')
    seen=set();verified=[]
    for r in reviews:
        validate_review(session,r)
        if r['reviewer_id'] in seen:raise CalibrationError('DUPLICATE_REVIEWER_ID')
        seen.add(r['reviewer_id']);verified.append(r)
    decisive=[r['preferred'] for r in verified if r['preferred'] in 'ABC' and len(r['preferred'])==1]
    counts={x:decisive.count(x) for x in 'ABC'}
    majority=max(counts.values()) if counts else 0
    # Agreement is descriptive only; a single source scene is a single family.
    all_pair_agreement=None
    if len(decisive)>=2:
        total=len(decisive)*(len(decisive)-1)//2
        agree=sum(n*(n-1)//2 for n in counts.values())
        all_pair_agreement=round(agree/total,3)
    contradictions={x:sum(r['visible_action_interrupted'][x]=='YES' for r in verified) for x in 'ABC'}
    family_count=1
    return {'schema':SCHEMA,'mode':'SHADOW_ONLY','session_id':session['session_id'],
        'data_families':family_count,'independent_video_families_for_calibration':0,
        'human_review_count_self_reported':len(verified),
        'self_reported_reviews_are_not_verified_ground_truth':True,
        'decisive_preference_votes':counts,'descriptive_pair_agreement':all_pair_agreement,
        'reported_action_interruption_yes_counts':contradictions,
        'evaluation':'NO_HUMAN_REVIEWS' if not verified else 'PREFERENCES_RECORDED_NOT_VALIDATED',
        'failure_reasons':['ONLY_ONE_SCENE_FAMILY','NO_VERIFIED_ACTION_BOUNDARY_GROUND_TRUTH',
                           'NO_INDEPENDENT_HOLDOUT_VIDEO_FAMILIES','NO_AUDIO_ON_ARCHIVED_ASSEMBLIES'],
        'decision':'ABSTAIN_INSUFFICIENT_INDEPENDENT_EVIDENCE',
        'heuristic_motion_ranking_is_not_performance_ground_truth':True,
        'historical_preference_not_used_as_training_label':True,
        'automatic_cut_authorized':False,'authorizations':SHADOW,
        'no_visual_inputs_to_H3':True}

def write_checksums(folder):
    files={p.relative_to(folder).as_posix():sha(p) for p in sorted(folder.rglob('*')) if p.is_file() and p.name!='SHA256_OUTPUTS.json'}
    atomic_json(folder/'SHA256_OUTPUTS.json',{'files':files})

def assess_session(path:Path,review_paths,output:Path):
    sess=read_json(path/'review_session.json')
    files=read_json(path/'SHA256_OUTPUTS.json')['files']
    for key,digest in files.items():
        p=path/key
        if not p.is_file() or sha(p)!=digest:raise CalibrationError('SESSION_FILE_CHANGED:'+key)
    reviews=[read_json(Path(p)) for p in review_paths]
    report=assess_reviews(sess,reviews)
    if output.exists() or output.is_symlink():raise FileExistsError('REFUSE_OVERWRITE_REVIEW_RESULT')
    atomic_json(output,report)
    return report

def cli():
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path,default=ROOT/'results/editorial_calibration_v0716')
    p.add_argument('--assess',action='store_true')
    p.add_argument('--reviews',type=Path,nargs='*',default=[])
    p.add_argument('--report-out',type=Path,default=None)
    ns=p.parse_args()
    if ns.assess:
        if not ns.reviews:raise SystemExit('Provide one or more independent review JSON files; the template is not evidence')
        report=assess_session(ns.out,ns.reviews,ns.report_out or ns.out.parent/'review_aggregation_v0716.json')
        print('REVIEW_LEDGER',report['human_review_count_self_reported'],'decision',report['decision'])
    else:
        session,status=make_session(ROOT,ns.out)
        print('V0716_SESSION_READY',ns.out,'OPTIONS',','.join(session['options']),'DECISION',status['decision'])
        print('REVIEW_HTML',ns.out/'index.html','NO_AUTOMATIC_EDIT')
if __name__=='__main__':cli()
