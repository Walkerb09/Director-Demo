#!/usr/bin/env python3
"""Director V0.7.3: profile-independent, read-only Scene Context candidate.

Consumes a sealed V0.7.2.2 ZIP, verifies manifest and internal consistency, and
produces bounded cast/prop evidence with framing quarantined. No model, H3,
ComfyUI, edit, scene memory, or automated handoff interaction.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

VERSION='director-scene-context-shadow-0.7.3'
SOURCE='director-profiled-rolebinary-shadow-0.7.2.2'
ARCHIVE_FILES={'scene_review_v0722.json','scene_review_v0722.jpg','bundle_manifest.json'}
GUARDS=('automatic_editing_authorized','continuity_handoff_authorized','h3_visual_conditioning','h3_generation','director_memory_changed','production_director_changed')
ROLE=re.compile(r'CAST_([A-Z][A-Z0-9_]{1,19})\Z')
HEX64=re.compile(r'[0-9a-f]{64}\Z')
HEX24=re.compile(r'[0-9a-f]{24}\Z')


def canonical(data):
    return (json.dumps(data,indent=2,sort_keys=True,ensure_ascii=False)+'\n').encode('utf-8')


def digest(data):return hashlib.sha256(data).hexdigest()


def filehash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def load_bundle(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Bundle missing or symlink')
    with zipfile.ZipFile(path) as z:
        if set(z.namelist())!=ARCHIVE_FILES or len(z.namelist())!=3:raise ValueError('Unexpected bundle members')
        if any(i.is_dir() or ((i.external_attr >> 16)&0o170000)==0o120000 for i in z.infolist()):
            raise ValueError('Unsafe bundle entry type')
        if any(i.file_size>2*1024*1024 for i in z.infolist()):raise ValueError('Bundle member too large')
        manifest=json.loads(z.read('bundle_manifest.json'))
        if manifest.get('version')!=SOURCE or set(manifest.get('files',{}))!=ARCHIVE_FILES-{'bundle_manifest.json'}:
            raise ValueError('Manifest schema mismatch')
        for name,hexdigest in manifest['files'].items():
            if not isinstance(hexdigest,str) or not HEX64.fullmatch(hexdigest) or digest(z.read(name))!=hexdigest:
                raise ValueError('Bundle content hash mismatch: '+name)
        blob=z.read('scene_review_v0722.json')
        report=json.loads(blob)
    return report,digest(blob),filehash(path)


def _bool_guard(obj,field):
    if obj.get(field) is not False:raise ValueError('Missing or unsafe authority guard: '+field)


def _frame(entry,role_ids):
    if not isinstance(entry,dict) or not isinstance(entry.get('parsed'),dict) or not isinstance(entry.get('raw_reply'),str):
        raise ValueError('Malformed frame observation')
    p=entry['parsed']
    if p.get('valid') is False:
        if not isinstance(p.get('errors'),list) or not p['errors'] or not all(isinstance(e,str) for e in p['errors']):
            raise ValueError('Invalid sample without diagnostic evidence')
        return None
    if p.get('valid') is not True:raise ValueError('Unsupported parsed validity')
    labels=p.get('labels')
    if not isinstance(labels,dict) or p.get('errors')!=[]:raise ValueError('Invalid parsed schema')
    req={f'CAST_{r}' for r in role_ids}|{'OTHER_PERSON','MIC_VISIBLE','FRAMING','Q','CAST_PRESENT'}
    if set(labels)!=req:raise ValueError('Unexpected label schema')
    for r in role_ids:
        if labels['CAST_'+r] not in ('YES','NO','UNCERTAIN'):raise ValueError('Invalid cast field')
    for k in ('OTHER_PERSON','MIC_VISIBLE'):
        if labels[k] not in ('YES','NO','UNCERTAIN'):raise ValueError('Invalid '+k)
    if labels['FRAMING'] not in ('WIDE','MEDIUM','CLOSE','UNCERTAIN') or labels['Q'] not in ('LOW','MEDIUM','HIGH'):
        raise ValueError('Invalid framing/confidence')
    fields=[labels['CAST_'+r] for r in role_ids]
    present=','.join(r for r in role_ids if labels['CAST_'+r]=='YES') if all(x!='UNCERTAIN' for x in fields) else 'UNCERTAIN'
    if present=='':present='NONE'
    if labels['CAST_PRESENT']!=present:raise ValueError('Cast list disagrees with independent fields')
    if p.get('parsed_cast_ids')!=(None if present=='UNCERTAIN' else [r for r in role_ids if labels['CAST_'+r]=='YES']):
        raise ValueError('Parsed cast IDs disagree')
    if not isinstance(entry.get('raw_reply'),str):raise ValueError('Missing raw claim')
    raw_fields={x.strip().split('=',1)[0]:x.strip().split('=',1)[1] for x in entry['raw_reply'].split('|') if '=' in x}
    for k in req-{'CAST_PRESENT'}:
        if raw_fields.get(k)!=labels[k]:raise ValueError('Parsed labels inconsistent with raw Qwen text')
    return labels


def validate_report(report):
    if not isinstance(report,dict) or report.get('version')!=SOURCE or report.get('phase')!='SHADOW_SINGLEPASS_SCENE_STATE':
        raise ValueError('Unsupported observer report')
    for k in GUARDS:_bool_guard(report,k)
    for k in ('human_review_required','human_verification_still_required','cross_cut_action_inference_allowed'):
        if report.get(k) is not (False if k=='cross_cut_action_inference_allowed' else True):
            raise ValueError('Review/cut guard missing: '+k)
    if report.get('source_class') not in ('H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY','UNKNOWN_ORIGIN'):
        raise ValueError('Unsupported source class')
    if not HEX24.fullmatch(str(report.get('job_id',''))) or not HEX64.fullmatch(str(report.get('source_sha256',''))):
        raise ValueError('Missing source identity')
    meta=report.get('video_meta')
    if not isinstance(meta,dict) or type(meta.get('frames')) is not int or meta['frames']<8 or type(meta.get('fps')) not in (int,float) or meta['fps']<=0:
        raise ValueError('Invalid source metadata')
    if not report.get('single_qwen_model_load_per_video') or not report.get('output_grammar_token_constrained'):
        raise ValueError('Unsupported source decoder')
    obs=report.get('observations');segs=report.get('segments')
    if not isinstance(obs,dict) or not isinstance(segs,list) or not 1<=len(segs)<=5:raise ValueError('Invalid observations or segments')
    first_obs=next((v for v in obs.values() if v.get('parsed',{}).get('valid') is True),None)
    if first_obs is None:raise ValueError('No valid frames; require manual review')
    first=first_obs['parsed']['labels']
    raw_first=first_obs.get('raw_reply','')
    role_ids=[ROLE.fullmatch(key).group(1) for token in raw_first.split('|')
              if '=' in token for key in [token.split('=',1)[0].strip()]
              if key!='CAST_PRESENT' and ROLE.fullmatch(key)]
    if set(role_ids)!={ROLE.fullmatch(k).group(1) for k in first if k!='CAST_PRESENT' and ROLE.fullmatch(k)}:
        raise ValueError('Role order/raw schema mismatch')
    if not 1<=len(role_ids)<=4 or len(set(role_ids))!=len(role_ids):raise ValueError('Invalid cast IDs')
    validated={int(k):_frame(v,role_ids) for k,v in obs.items() if str(k).isdigit() and str(int(k))==k}
    if len(validated)!=len(obs):raise ValueError('Invalid frame indices')
    prev_end=-1;all_samples=set()
    for seg in segs:
        a,b=seg['bounds'];samples=seg['samples'];pair=seg['endpoint_pair']
        if (type(a) is not int or type(b) is not int or a!=prev_end+1 or not a<=b<meta['frames'] or
            len(samples)!=4 or samples!=sorted(set(samples)) or not all(a<=f<=b for f in samples) or
            pair!=samples[-2:] or pair[-1]!=b):raise ValueError('Unsafe segment boundaries/endpoint')
        if seg.get('endpoint_frame_actually_observed') is not True or seg.get('endpoint_human_verified') is not False:
            raise ValueError('Endpoint authority discrepancy')
        if len(seg['sampled_states'])!=len(samples):raise ValueError('Sampled states incomplete')
        for record,f in zip(seg['sampled_states'],samples):
            if (record.get('frame')!=f or record.get('valid') is not (validated[f] is not None) or
                record.get('labels')!=validated[f]):
                raise ValueError('Sampled states conflict with raw observations')
        endpoint=seg['endpoint_model_pair_agreement']
        for key in [f'CAST_{rid}' for rid in role_ids]+['CAST_PRESENT','OTHER_PERSON','MIC_VISIBLE','FRAMING']:
            a_val,b_val=validated[pair[0]],validated[pair[1]]
            if a_val is None or b_val is None:value=None
            else:
                x,y=a_val[key],b_val[key]
                value=x if x==y and x!='UNCERTAIN' and all(validated[f]['Q']!='LOW' for f in pair) else None
            if endpoint.get(key)!=value:raise ValueError('Endpoint pair inconsistent: '+key)
        all_samples.update(samples);prev_end=b
    if prev_end!=meta['frames']-1 or set(validated)!=all_samples:raise ValueError('Uncovered or extra sampled frames')
    cuts=report.get('candidate_cuts')
    if not isinstance(cuts,list) or len(cuts)!=len(segs)-1 or sorted(int(c['frame_estimate']) for c in cuts)!=[s['bounds'][0] for s in segs[1:]]:
        raise ValueError('Cut list differs from segments')
    if any(c.get('not_proof_of_edit') is not True for c in cuts):
        raise ValueError('Cut authority disclaimer missing')
    return role_ids,validated


def consensus(values):
    """Return candidate only if every sampled value agrees and all Q >= MEDIUM."""
    if any(v is None or q is None or q=='LOW' for v,q in values):return None
    labels=[v for v,_ in values]
    return labels[0] if labels and labels[0]!='UNCERTAIN' and len(set(labels))==1 else None


def context(report,report_sha,bundle_sha):
    role_ids,observed=validate_report(report)
    pieces=[]
    for seg in report['segments']:
        sampled=seg['samples'];endpoint=seg['endpoint_pair'];fields=[f'CAST_{rid}' for rid in role_ids]+['MIC_VISIBLE','OTHER_PERSON']
        series={str(f):({'model_valid':False,'cast':{rid:None for rid in role_ids},
                          'microphone_visible':None,'other_person':None,'global_model_q':None}
                         if observed[f] is None else
                         {'model_valid':True,'cast':{rid:observed[f]['CAST_'+rid] for rid in role_ids},
                          'microphone_visible':observed[f]['MIC_VISIBLE'],'other_person':observed[f]['OTHER_PERSON'],
                          'global_model_q':observed[f]['Q']}) for f in sampled}
        whole={k:consensus([(observed[f][k],observed[f]['Q']) if observed[f] is not None else (None,None)
                             for f in sampled]) for k in fields}
        end={k:consensus([(observed[f][k],observed[f]['Q']) if observed[f] is not None else (None,None)
                           for f in endpoint]) for k in fields}
        pieces.append({'segment_id':seg['segment_id'],'candidate_bounds':seg['bounds'],
            'sampled_frames':sampled,'sampled_model_claims':series,
            'sample_agreement_candidates':{'cast':{rid:whole[f'CAST_{rid}'] for rid in role_ids},
                                            'microphone_visible':whole['MIC_VISIBLE'],'other_person':whole['OTHER_PERSON']},
            'endpoint_candidate':{'frame':endpoint[-1],'pair':endpoint,
                'cast':{rid:end[f'CAST_{rid}'] for rid in role_ids},
                'microphone_visible':end['MIC_VISIBLE'],'other_person':end['OTHER_PERSON'],
                'model_pair_only_not_ground_truth':True},
            'framing_quarantined':True,'framing_authority':'NONE',
            'microphone_target_not_observed':True,
            'motion_between_stills_not_verified':True,
            'continuous_scene_not_independently_verified':True})
    return {'version':VERSION,'phase':'SHADOW_PROVENANCE_GATED_SCENE_CONTEXT',
       'source_report_version':report['version'],'source_report_sha256':report_sha,'source_bundle_sha256':bundle_sha,
       'source_video_path_declared':report['source_video_path'],'source_sha256':report['source_sha256'],
       'source_class':report['source_class'],'source_frames':report['video_meta']['frames'],
       'source_video_hash_reverified':False,'job_id':report['job_id'],
       'profile_id':report['profile_id'],'cast_ids_configured_in_observer':role_ids,
       'segments':pieces,
       'cut_barriers':[{'frame':int(c['frame_estimate']),'uncertain_candidate_cut':True,
                        'continuous_motion_inference_blocked':True} for c in report['candidate_cuts']],
       'framing_predictions_quarantined_globally':True,'scene_dna_verified':False,
       'observer_claims_not_ground_truth':True,'audio_not_analyzed':True,
       'microphone_holder_and_target_not_observed_by_this_detector':True,
       'human_review_required':True,'automatic_editing_authorized':False,
       'continuity_handoff_authorized':False,'h3_visual_conditioning_authorized':False,
       'h3_visual_conditioning':False,'h3_generation':False,
       'director_memory_changed':False,'production_director_changed':False}


def run(bundle,out,video=None):
    report,report_sha,bundle_sha=load_bundle(bundle)
    result=context(report,report_sha,bundle_sha)
    if video is not None:
        source=Path(video)
        if source.is_symlink() or not source.is_file() or filehash(source)!=report['source_sha256']:
            raise ValueError('Source video missing or SHA mismatch')
        result['source_video_hash_reverified']=True
    data=canonical(result)
    out=Path(out)
    if out.exists():
        if out.is_symlink() or not out.is_file() or out.read_bytes()!=data:
            raise FileExistsError('Conflicting Scene Context; refusing overwrite')
        print('V073_ALREADY_IDENTICAL',out)
    else:
        out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('xb') as dest:dest.write(data)
        print('V073_SCENE_CONTEXT_WRITTEN',out)
    print('V073_SOURCE',report['job_id'],'SEGMENTS',len(result['segments']),'CUT_BARRIERS',len(result['cut_barriers']))
    for seg in result['segments']:
        print('V073_SEGMENT',seg['candidate_bounds'],'CAST_CANDIDATES',seg['sample_agreement_candidates']['cast'],
              'MIC_VISIBLE',seg['sample_agreement_candidates']['microphone_visible'],
              'FRAMING_QUARANTINED True')
    print('V073_SHADOW_ONLY_NO_EDIT_NO_HANDOFF_NO_H3')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--video',type=Path,help='Optional strict SHA-256 verification of original source bytes')
    a=p.parse_args();run(a.bundle,a.out,a.video)

if __name__=='__main__':main()
