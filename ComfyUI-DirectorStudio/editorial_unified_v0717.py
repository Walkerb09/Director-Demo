#!/usr/bin/env python3
"""V0.7.17: read-only consolidation of real v0.7.13–v0.7.16 shadow evidence.

This code never authorizes or performs edits, model inference, scene-memory writes,
H3 generation, or visual conditioning. Review outputs remain anonymous to reviewers.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import editorial_calibration_v0716 as cal

ROOT = Path(__file__).resolve().parent
SCHEMA = 'director-editorial-unified-shadow-v0717'
REQUIRED = ('paired_cut_review_v0713.json','editorial_evidence_v0714.json',
            'v0715_temporal_cut_evidence_v0715.json','nine_shot_ab.json')
AUTH_KEYS = ('automatic_editing','automatic_cut_selection','production_approval',
             'memory_mutation','continuity_handoff','scene_dna_verification',
             'performance_certification','gesture_certification',
             'prop_ownership_certification','h3_generation','h3_visual_conditioning')


def sha(path): return cal.sha(Path(path))
def read(path): return cal.read_json(path)
def write(path,obj): return cal.atomic_json(Path(path),obj)

def no_authority(report):
    a = report.get('authorizations')
    if not isinstance(a,dict) or not set(AUTH_KEYS).issubset(a) or any(a.get(k) is not False for k in AUTH_KEYS):
        raise cal.CalibrationError('PRIOR_REPORT_HAS_AUTHORIZATION_OR_MISSING_GATE')
    if report.get('editorial_authority') not in ('NO_AUTOMATIC_CUT_DECISION','SHADOW_REVIEW_ONLY'):
        raise cal.CalibrationError('PRIOR_REPORT_AUTHORITY_UNVERIFIED')


def consolidate(root=ROOT):
    root=Path(root)
    provenance=cal.verify_inputs(root)
    r=root/'reference'
    a,b,c=(read(r/f) for f in REQUIRED[:3])
    for report in (a,b,c): no_authority(report)
    if not (b.get('technical_triage',{}).get('automatic_selection_authorized') is False
            and b['technical_triage'].get('automatic_edit_authorized') is False
            and c.get('analysis_excludes_all_cross_cut_image_pairs') is True):
        raise cal.CalibrationError('PRIOR_REPORT_MISSING_CUT_SAFETY')
    if a.get('historical_human_working_choice',{}).get('shot9_trim') != 66 or c.get('historical_human_working_choice',{}).get('shot9_trim') != 66:
        raise cal.CalibrationError('HISTORICAL_WORKING_CHOICE_CHANGED')
    s1={v['label']:v for v in a['variants']}
    s2={v['label']:v for v in b['variants']}
    s3={v['label']:v for v in c['variants']}
    if set(s1)!=set(s2) or set(s1)!=set(s3) or set(s1)!={'F12','F24','F66'}:
        raise cal.CalibrationError('MISMATCHED_ALTERNATIVES')
    rows=[]
    for label in sorted(s1):
        x,y,z=s1[label],s2[label],s3[label]
        h=provenance['candidates'][label]['assembly_sha256']
        if x['source_sha256']!=h or y['source_sha256']!=h or z['assembled_video_sha256']!=h:
            raise cal.CalibrationError('CROSS_REPORT_SOURCE_PROVENANCE_MISMATCH')
        if z['assembly_audio'].get('status')!='NO_AUDIO_STREAM':
            raise cal.CalibrationError('AUDIO_STATUS_CHANGED_REQUIRES_REVIEW')
        if z['action_annotations'].get('status')!='NO_INDEPENDENT_ACTION_LABELS' or z.get('automatic_edit_authorized') is not False:
            raise cal.CalibrationError('UNVERIFIED_ACTION_WAS_PROMOTED')
        flags=y['motion_evidence']['technical_review_flags']
        if not isinstance(flags,list):raise cal.CalibrationError('TECHNICAL_FLAGS_MALFORMED')
        rows.append({
            'archival_trim':label,'assembly_sha256':h,
            'review_clip_sha256':provenance['candidates'][label]['clip_sha256'],
            'within_incoming_image_difference':{
                'early_mad_median':y['motion_evidence']['first_six_transition_mad_median'],
                'later_mad_median':y['motion_evidence']['remaining_transition_mad_median'],
                'units':'per-frame normalized image intensity difference, not a physical-motion score'},
            'within_side_optical_flow':{
                'outgoing_last_six_deviation_px':z['outgoing']['last_six_flow_deviation_px'],
                'incoming_first_six_deviation_px':z['incoming']['first_six_flow_deviation_px'],
                'units':'px/frame at width 192; no cross-cut flow'},
            'technical_review_flags':flags,
            'technical_status':'REVIEW_FLAGGED' if flags else 'NO_FLAG_FOUND_NOT_PROVEN_DEFECT_FREE',
            'action_completion':'UNKNOWN','emotion_continuity':'UNKNOWN',
            'actor_identity_continuity':'UNKNOWN','audio_timing':'UNAVAILABLE_NO_STREAM',
            'may_be_prepared_for_review':True,'may_be_automatically_selected':False})
    manifest={f:sha(r/f) for f in REQUIRED}
    report={'schema':SCHEMA,'version':'0.7.17','mode':'SHADOW_ONLY',
            'scope':'ARCHIVED_NINE_SHOT_SEAM_REVIEW_NOT_PRODUCTION',
            'scene_family_id':provenance['family_id'],
            'scene_family_count':1,'independent_human_ratings':0,
            'source_provenance':{'source_type':'GENERATED_REENCODED_EDIT_REVIEW_ONLY',
                'reference_report_sha256':manifest,'source_profile_sha256':provenance['profile_sha256']},
            'cut_boundary':{'join_frame':692,'fps':24,'flow_across_cut_forbidden':True},
            'alternatives':rows,
            'technical_attention':{
               'may_automate_review_preparation':True,
               'flagged_options':[r['archival_trim'] for r in rows if r['technical_review_flags']],
               'unflagged_options':[r['archival_trim'] for r in rows if not r['technical_review_flags']],
               'not_an_aesthetic_ranking':True},
            'performance_judgment':'UNKNOWN_UNVERIFIED',
            'historical_working_edit':{'trim':66,'human_selected_working_only':True,
                'not_used_as_label':True,'not_final_film_approval':True},
            'next_input_needed':'INDEPENDENT_EDIT_REVIEW_FROM_DISTINCT_SCENE_FAMILIES_WITH_GROUNDED_ACTION_AND_AUDIO_WHEN_PRESENT',
            'decision':'ABSTAIN_NO_INDEPENDENT_PERFORMANCE_EVIDENCE',
            'authorizations':{k:False for k in AUTH_KEYS},
            'h3_visual_conditioning':False,'memory_mutation':False,
            'integration':'NOT_CONNECTED_TO_V074_OR_V075_POSTSHOT_WORKERS'}
    return report


def run(root=ROOT,out=None):
    root=Path(root)
    if out is None:out=root/'results/editorial_shadow_v0717'
    out=Path(out); stage=out.with_name(out.name+'_staging')
    if any(p.exists() or p.is_symlink() for p in (out,stage)):
        raise FileExistsError('REFUSE_EXISTING_RESULT_OR_STAGE')
    report=consolidate(root)  # All source assertions before writing output.
    stage.mkdir(parents=True)
    try:
        (stage/'private').mkdir()
        (stage/'reviewer_only').mkdir()
        session,status=cal.make_session(root,stage/'private'/'full_calibration_session')
        # Reviewer-only export must NOT include alias mappings or technical labels.
        reviewers=stage/'reviewer_only'
        shutil.copy2(stage/'private/full_calibration_session/index.html',reviewers/'index.html')
        shutil.copy2(stage/'private/full_calibration_session/review_session.json',reviewers/'review_session.json')
        shutil.copytree(stage/'private/full_calibration_session/clips',reviewers/'clips')
        if session['historical_choice_not_exposed_to_reviewer'] is not True:
            raise cal.CalibrationError('REVIEWER_BLINDING_NOT_CONFIRMED')
        write(stage/'private/unified_editorial_dossier_v0717.json',report)
        write(stage/'private/calibration_status_v0717.json',status)
        (stage/'README_FIRST.txt').write_text(
            'V0.7.17 evidence-only editorial review. Open reviewer_only/index.html.\n'
            'Share ONLY reviewer_only/ with reviewers: private/ contains edit identities.\n'
            'No actual human ratings supplied. F66 remains historical working edit.\n'
            'No automatic edits, H3 inputs, scene memory changes, or production approval.\n')
        cal.write_checksums(stage)
        os.replace(stage,out)
        return report,out
    except Exception:
        # Preserve staging for forensics as with upstream V0.7.16.
        raise


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path);a=p.parse_args()
    dossier,out=run(out=a.out)
    print('V0717_SHADOW_DOSSIER_READY',out)
    print('V0717_ALTERNATIVES',len(dossier['alternatives']),'TECHNICAL_FLAGS',len(dossier['technical_attention']['flagged_options']))
    print('V0717_DECISION',dossier['decision'],'NO_AUTO_EDITS')
    print('REVIEWER_ONLY',out/'reviewer_only/index.html')
if __name__=='__main__':main()
