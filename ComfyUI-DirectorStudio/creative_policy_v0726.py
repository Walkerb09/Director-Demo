#!/usr/bin/env python3
"""Read-only scene coverage proposal + V0.7.25 editorial review router.

Not a multimodal model, pose detector, scene-DNA verifier, or generation worker.
Any shot and performance directions here are PROPOSED creative intent, not observations.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import re
from pathlib import Path

SCHEMA = 'director-creative-policy-v0726'
BRIEF_SCHEMA = 'director-creative-brief-v0726'
EVIDENCE_SCHEMA = 'director-editorial-evidence-queue-v0725'
ASSET_TYPES = {'AUTHENTIC_ORIGINAL_VIDEO', 'AUTHENTIC_ORIGINAL_STILL'}
ALLOWED_CAST = {1, 2, 3, 4}
SHA = re.compile(r'^[0-9a-f]{64}$')
IDENT = re.compile(r'^[a-z][a-z0-9_-]{1,47}$')
UNAUTHORIZED = (
    'h3_generation', 'h3_visual_conditioning', 'automatic_editing',
    'automatic_cut_selection', 'continuity_handoff', 'memory_mutation',
    'production_approval', 'performance_certification',
    'scene_dna_verification', 'actor_identity_certification',
    'prop_ownership_certification', 'gesture_certification')

class PolicyError(ValueError):
    pass


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def sha_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def clean_string(x, label, maximum=500):
    if not isinstance(x, str) or not x.strip() or len(x) > maximum or any(ord(c) < 32 for c in x):
        raise PolicyError('INVALID_' + label)
    return x.strip()


def validate_brief(brief):
    if not isinstance(brief, dict) or brief.get('schema') != BRIEF_SCHEMA:
        raise PolicyError('INVALID_BRIEF_SCHEMA')
    for name in ('scene_id', 'setting', 'creative_goal', 'tone'):
        clean_string(brief.get(name), name.upper(), 600)
    if not IDENT.fullmatch(brief['scene_id']):
        raise PolicyError('INVALID_SCENE_ID')
    if brief.get('fps') != 24 or brief.get('aspect_ratio') not in ('9:16', '16:9', '1:1'):
        raise PolicyError('UNSUPPORTED_OUTPUT_FORMAT')
    cast = brief.get('cast')
    if not isinstance(cast, list) or len(cast) not in ALLOWED_CAST:
        raise PolicyError('UNSUPPORTED_CAST_COUNT')
    if any(not isinstance(actor, dict) or set(actor) != {'actor_id', 'role', 'authentic_ref_ids'} for actor in cast):
        raise PolicyError('INVALID_CAST_SPEC')
    ids=[]
    for actor in cast:
        if not isinstance(actor['actor_id'], str) or not IDENT.fullmatch(actor['actor_id']):
            raise PolicyError('INVALID_ACTOR_ID')
        ids.append(actor['actor_id'])
        clean_string(actor['role'], 'ACTOR_ROLE', 100)
        if not isinstance(actor['authentic_ref_ids'], list) or not actor['authentic_ref_ids']:
            raise PolicyError('MISSING_ACTOR_REFERENCE')
    if len(set(ids)) != len(ids):
        raise PolicyError('DUPLICATE_ACTORS')
    assets=brief.get('assets')
    if not isinstance(assets, list) or not assets:
        raise PolicyError('NO_ORIGINAL_ASSET_DECLARATIONS')
    asset_ids=[]
    for a in assets:
        if not isinstance(a, dict) or set(a) != {'asset_id', 'kind', 'filename', 'sha256', 'provenance'}:
            raise PolicyError('INVALID_ASSET_RECORD')
        if not isinstance(a['asset_id'], str) or not IDENT.fullmatch(a['asset_id']):
            raise PolicyError('INVALID_ASSET_ID')
        asset_ids.append(a['asset_id'])
        if a['kind'] not in ASSET_TYPES or a['provenance'] != 'CHECKPOINT_RECORDED_ORIGINAL_NOT_RUNTIME_VERIFIED':
            raise PolicyError('FORBIDDEN_VISUAL_REFERENCE_PROVENANCE')
        if not isinstance(a['sha256'], str) or not SHA.fullmatch(a['sha256']):
            raise PolicyError('INVALID_ASSET_HASH')
        filename=a['filename']
        if (not isinstance(filename, str) or not filename or
            Path(filename).name != filename or '\\' in filename or filename in ('.','..')):
            raise PolicyError('UNSAFE_ASSET_FILENAME')
    if len(set(asset_ids))!=len(asset_ids) or len({a['filename'] for a in assets}) != len(assets):
        raise PolicyError('DUPLICATE_ASSETS')
    for a in cast:
        if len(set(a['authentic_ref_ids'])) != len(a['authentic_ref_ids']) or any(x not in asset_ids for x in a['authentic_ref_ids']):
            raise PolicyError('ACTOR_REFERENCE_NOT_IN_DECLARED_ORIGINAL_ASSETS')
    arc=brief.get('emotional_arc')
    if not isinstance(arc,list) or len(arc)!=3:
        raise PolicyError('EMOTIONAL_ARC_REQUIRES_THREE_BEATS')
    for stage in arc: clean_string(stage, 'EMOTIONAL_BEAT', 90)
    if brief.get('requested_shots') not in (3,4):
        raise PolicyError('UNSUPPORTED_SHOT_COUNT')
    prop=brief.get('interaction_prop')
    if prop is not None:
        if not isinstance(prop,dict) or set(prop)!={'name','intended_use','observed_owner'}:
            raise PolicyError('INVALID_PROP')
        clean_string(prop['name'],'PROP_NAME',80)
        clean_string(prop['intended_use'],'PROP_INTENT',250)
        if prop['observed_owner']!='UNKNOWN':
            raise PolicyError('UNVERIFIED_PROP_OWNER_ASSERTION')
    working=brief.get('historical_working_cut')
    if working is not None:
        if (not isinstance(working,dict) or set(working)!={'shot','entry_frame','status'}
            or type(working['shot']) is not int or working['shot']<1
            or type(working['entry_frame']) is not int or working['entry_frame']<0
            or working['status']!='HUMAN_WORKING_NOT_FINAL'):
            raise PolicyError('HISTORICAL_CUT_STATUS_INVALID')
    return brief


def runtime_check_assets(brief, asset_dir):
    """Optional. Fail if any declared authentic source is not present/hash-identical.

    This only confirms declared hashes, never confers generation approval or proves
    the original source's real-world provenance independently.
    """
    if asset_dir is None:
        return {'performed':False, 'all_declared_bytes_match':False,
                'note':'Checkpoint recorded hashes; runtime source files were not supplied.'}
    root=Path(asset_dir).resolve(strict=True)
    checked=[]
    for asset in brief['assets']:
        p=root / asset['filename']
        if p.is_symlink() or not p.is_file() or p.resolve().parent != root:
            raise PolicyError('MISSING_OR_LINKED_DECLARED_ORIGINAL')
        if sha_file(p)!=asset['sha256']:
            raise PolicyError('ORIGINAL_SOURCE_HASH_MISMATCH')
        checked.append(asset['asset_id'])
    return {'performed':True,'all_declared_bytes_match':True,'matched_asset_ids':checked,
            'note':'Matching bytes do not independently certify authenticity or authorize H3.'}


def verify_evidence(report, expected_hash, file_path):
    if not isinstance(expected_hash,str) or not SHA.fullmatch(expected_hash) or sha_file(Path(file_path))!=expected_hash:
        raise PolicyError('EDITORIAL_EVIDENCE_SHA_MISMATCH')
    if report.get('schema')!=EVIDENCE_SCHEMA or report.get('mode')!='SHADOW_ONLY':
        raise PolicyError('WRONG_EDITORIAL_EVIDENCE')
    flags=report.get('authorizations')
    if not isinstance(flags,dict) or any(flags.get(x) is not False for x in UNAUTHORIZED) or any(v is not False for v in flags.values()):
        raise PolicyError('UNAUTHORIZED_EVIDENCE_FLAGS')
    if report.get('generated_footage_allowed_as_H3_visual_input') is not False:
        raise PolicyError('UNSAFE_CLEAN_CHAIN_FLAG')
    if report.get('approval_or_selection') != 'ABSTAIN_NO_VALIDATED_ARTISTIC_POLICY':
        raise PolicyError('UNTRUSTED_AUTONOMOUS_ARTISTIC_SELECTION')
    if report.get('verified_independent_human_preferences') != 0:
        raise PolicyError('CLAIMED_HUMAN_EVIDENCE_NOT_VALIDATED')
    fs=report.get('families')
    if not isinstance(fs,list) or len(fs)!=report.get('unique_source_families') or len(fs)<1:
        raise PolicyError('INCORRECT_SOURCE_FAMILY_COUNT')
    for fam in fs:
        if not isinstance(fam,dict) or set(fam.get('technical_inspection_order',[]))!={'A','B'}:
            raise PolicyError('INVALID_INSPECTION_ORDER')
        if fam.get('artistic_comparison')!='ABSTAIN_NOT_CALIBRATED':
            raise PolicyError('CLAIMED_ARTISTIC_WINNER')
        if any(l not in fam.get('measurements',{}) for l in ('A','B')):
            raise PolicyError('MISSING_TECHNICAL_MEASUREMENTS')
    return True


def shot_templates(n, with_prop):
    if n==1:
        templates=[('ESTABLISH','A readable establishing portrait','The actor settles naturally into the location.'),
                   ('DEVELOP','An observational medium shot','The actor pursues the scene objective through subtle, unforced behavior.'),
                   ('RESOLVE','An unhurried closing portrait','The actor completes or eases out of the action; avoid a forced freeze.')]
    elif n==2:
        templates=[('ESTABLISH','A balanced two-person composition','Let their spatial relationship and attention read clearly.'),
                   ('ENGAGE','A closer shared interaction','Invite one clear, natural interaction without requiring mechanical gestures.'),
                   ('REACT','A response-focused composition','Give the responding actor room to react spontaneously; do not mandate a nod.'),
                   ('RESOLVE','A readable two-person closing composition','Allow a small natural change in attention and a stable, non-frozen ending.')]
    else:
        templates=[('ESTABLISH','A balanced ensemble composition','Introduce the visible spatial relationship with readable faces.'),
                   ('DEVELOP','A clear interaction-focused composition','Let attention change naturally, keeping all people trackable.'),
                   ('RESOLVE','A quiet group closing composition','End with legible positions and no forced synchronized movement.')]
    if with_prop and n>=2:
        name,framing,_=templates[1]
        templates[1]=(name,framing, 'Creative intent: introduce the prop interaction without asserting who held the prop in observed footage. One naturally timed movement at most.')
    return templates


def make_shots(brief):
    cast=brief['cast']; n=len(cast); templates=shot_templates(n, brief.get('interaction_prop') is not None)
    count=brief['requested_shots']
    if count==3 and n==2:
        templates=[templates[0],templates[1],templates[-1]]
    if count==4 and n!=2:
        # Add an optional performance-response beat, without asserting one occurred.
        templates=templates[:2]+[('RESPONSE','A restrained response detail','Observe a reaction without prescribing precise facial motion.')]+templates[2:]
    shots=[]
    refs={a['actor_id']:list(a['authentic_ref_ids']) for a in cast}
    for i,(purpose,framing,direction) in enumerate(templates,1):
        arc_idx=min(2,round((i-1)*2/max(1,len(templates)-1)))
        aim=brief['emotional_arc'][arc_idx]
        shots.append({'shot_id':f"{brief['scene_id']}-p{i:02d}",
            'proposal_only':True,'narrative_beat':purpose,
            'creative_emotional_intent':aim,
            'composition_proposal':framing,
            'performance_direction_proposal':direction,
            'camera_policy':'Prefer stable, naturalistic framing; small movement only when identity and spatial clarity remain readable.',
            'duration_target_seconds':4,
            'planned_output_frames':4*brief['fps'],
            'source_reference_ids_by_actor':refs,
            'conditioning_source_policy':'AUTHENTIC_ORIGINALS_ONLY_REQUIRE_RUNTIME_SHA_VERIFICATION',
            'never_use_generated_previous_frame_or_clip_as_reference':True,
            'ending_directive':'Keep faces, actor positions and any relevant prop visible if natural; finish on a legible action state without freezing.',
            'candidate_h3_direction_not_executed': (
                f"Create a single naturalistic {brief['aspect_ratio']} cinematic shot in {brief['setting']}. "
                f"Creative objective: {brief['creative_goal']} Tone: {brief['tone']}. "
                f"This shot proposes {framing.lower()}. Performance: {direction} "
                f"The intended emotional beat is {aim}. Favor understated spontaneous expressions, "
                'authentic identity and lighting continuity over forced gestures. End on a clear, natural, '
                'analyzer-readable moment without freezing. Use authenticated original visual sources ONLY; '
                'never condition on earlier H3-generated frames, even from an approved edit.'),
            'observed_gesture':None,
            'verified_continuity_handoff':None,
            'candidate_cut_criterion':'Look for a completed action or legible pause. Cut timing unverified until actual footage is reviewed.',
            'observer_evidence_requirements':['actor_presence','shot_cut_barriers','independent_actor_motion_when_available',
                'ending_facing_if_verified','prop_visibility_without_inferred_owner'],
            'acceptance_policy':'REVIEW_BEFORE_PROMOTION;UNKNOWN_PERFORMANCE_IS_NOT_SUCCESS',
            'h3_executable':False})
    return shots


def make_policy(brief,evidence,evidence_hash,asset_state):
    # This is deterministic creative scaffolding, not a trained aesthetic policy.
    shots=make_shots(brief)
    families=[]
    for fam in evidence['families']:
        families.append({'family_id':fam['family_id'],
            'first_inspect_for_audio_sample_edge_only':fam['technical_inspection_order'][0],
            'sample_jump_values':{label:fam['measurements'][label]['measured_sample_jump'] for label in ('A','B')},
            'independent_human_preference':'NOT_AVAILABLE',
            'automatic_editorial_winner':'ABSTAIN',
            'recommendation':'INSPECT_BOUNDARY_AUDIO;DO_NOT_AUTO_REJECT_OR_SELECT'})
    working=brief.get('historical_working_cut')
    return {'schema':SCHEMA,'version':'0.7.26','mode':'SHADOW_ONLY',
        'brief_sha256':sha_json(brief),'prior_v0725_evidence_sha256':evidence_hash,
        'scene_id':brief['scene_id'],'scene_setting':brief['setting'],'creative_goal':brief['creative_goal'],
        'tone':brief['tone'],'fps':brief['fps'],'aspect_ratio':brief['aspect_ratio'],
        'creative_autonomy':'PROPOSE_VARIATIONS_WITHOUT_EXECUTION',
        'planning_basis':'RULE_BASED_COVERAGE_HEURISTICS_NOT_MODEL_FILM_JUDGMENT',
        'actor_count':len(brief['cast']),'shots':shots,
        'authentic_source_declarations':[{'asset_id':a['asset_id'],'sha256':a['sha256'],
                                            'provenance':a['provenance']} for a in brief['assets']],
        'runtime_asset_integrity':asset_state,
        'editorial_technical_review':families,
        'creative_review_checkpoint':{'question':'Does proposed coverage serve the scene objective without scripted-looking gestures?',
            'status':'PROPOSAL_REQUIRES_VISUAL_FEASIBILITY_AND_PERFORMANCE_VALIDATION'},
        'historical_working_cut_preserved':working,
        'scene_dna_truth':'UNKNOWN_UNLESS_INDEPENDENTLY_VERIFIED',
        'h3_generation_candidate_only':True,
        'provenance_rule':'NEVER_ROUTE_GENERATED_IMAGES_OR_VIDEOS_TO_H3_VISUAL_INPUT',
        'generated_footage_roles':['OBSERVATION','EDITORIAL_PREVIEW','NONVISUAL_CANDIDATE_METADATA_ONLY'],
        'authority':{'draft_creative_shot_plan':True,'technical_review_prioritization':True,
            **{x:False for x in UNAUTHORIZED}},
        'next_state':'AWAIT_SHOT_FEASIBILITY_REVIEW_OR_GENUINE_EDIT_CALIBRATION;NO_AUTOMATIC_GENERATION',
        'operational_baseline':'V0.7.5_UNMODIFIED'}


def render_html(report):
    esc=lambda v:html.escape(str(v),quote=True)
    cards=[]
    for shot in report['shots']:
        cards.append(f'''<article><div class="tag">{esc(shot['shot_id'])} · {esc(shot['narrative_beat'])}</div>
<h2>{esc(shot['composition_proposal'])}</h2><p>{esc(shot['performance_direction_proposal'])}</p>
<p class="fine">Emotional intention: {esc(shot['creative_emotional_intent'])} · {shot['duration_target_seconds']} seconds proposed</p>
<p class="fine">Ending: {esc(shot['ending_directive'])}</p></article>''')
    queue=''.join(f"<tr><td>{esc(x['family_id'])}</td><td>{esc(x['first_inspect_for_audio_sample_edge_only'])}</td><td>ABSTAIN</td></tr>" for x in report['editorial_technical_review'])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Director creative coverage board</title><style>
:root{{color-scheme:dark}}body{{margin:0;background:#10141b;color:#eaf0f7;font:16px system-ui;line-height:1.55}}main{{max-width:980px;margin:32px auto;padding:0 20px}}h1{{font-size:32px;margin:8px 0}}h2{{font-size:19px}}.muted,.fine{{color:#adbbcd}}.fine{{font-size:13px}}.tag{{color:#b5caed;font-size:12px;letter-spacing:1px;text-transform:uppercase}}.stack{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin:24px 0}}article{{padding:22px;border:1px solid #36475f;border-radius:12px;background:#1c2634}}.warn{{border-left:4px solid #b7c5d6;padding-left:15px;color:#d5deeb}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #415064}}
</style><main><div class="tag">H3 REFERENCE DIRECTOR · V0.7.26 · SHADOW PLANNING</div><h1>{esc(report['scene_setting'])}</h1><p>{esc(report['creative_goal'])}</p><p class="muted">{esc(report['tone'])} · {report['fps']} FPS · {esc(report['aspect_ratio'])}</p>
<div class="stack">{''.join(cards)}</div><p class="warn"><strong>Proposals, not generated footage.</strong> Authentic original references require runtime hash verification. No generated imagery enters H3 conditioning. No production edits, scene memory, or H3 jobs are authorized.</p>
<h2>Existing editorial review priority</h2><table><tr><th>Source family</th><th>Inspect audio edge</th><th>Artistic selection</th></tr>{queue}</table><p class="muted">The A/V evidence is from the V0.7.25 calibration examples, not proof about this proposed scene. No human preference data was supplied.</p>
<p class="fine">V0.7.5 operational baseline unchanged. This HTML is generated solely from the local proposal and verified report hash.</p></main></html>'''


def generate(brief_path,evidence_path,out_json,out_html=None,asset_dir=None):
    brief=validate_brief(load(Path(brief_path)))
    path=Path(evidence_path)
    if Path(out_json).exists() or Path(out_json).is_symlink() or (out_html is not None and (Path(out_html).exists() or Path(out_html).is_symlink())):
        raise FileExistsError('REFUSE_EXISTING_OUTPUT')
    if out_html is not None and Path(out_json).resolve()==Path(out_html).resolve():
        raise PolicyError('DUPLICATE_OUTPUT_TARGET')
    expected=brief.get('prior_v0725_evidence_sha256')
    evidence=load(path)
    verify_evidence(evidence,expected,path)
    assets=runtime_check_assets(brief,asset_dir)
    report=make_policy(brief,evidence,expected,assets)
    Path(out_json).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    if out_html is not None:
        Path(out_html).write_text(render_html(report),encoding='utf-8')
    return report


def cli():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brief',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--html',type=Path)
    parser.add_argument('--verify-assets',type=Path,help='Optional directory containing every declared authentic original asset')
    args=parser.parse_args()
    result=generate(args.brief,args.evidence,args.out,args.html,args.verify_assets)
    print('DIRECTOR_CREATIVE_PROPOSALS',len(result['shots']),
          'review_families',len(result['editorial_technical_review']),
          'h3_generation_authorized',result['authority']['h3_generation'])
if __name__=='__main__':cli()
