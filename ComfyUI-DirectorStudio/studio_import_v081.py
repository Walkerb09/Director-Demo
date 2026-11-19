#!/usr/bin/env python3
"""Import the existing hallway Director project (V0.7.35 base + V0.7.36/39 lifecycle + V0.7.39 direction
ledger) into a Director Studio V0.8.1 project. READ-ONLY on the source: every ledger is re-verified by
the V0.7.39 code, every file is hash-checked against those ledgers, nothing in the source is written.

Imported: the six originals (hash-checked against the V0.7.35 scene seed), Presenter + Grandpa + Hallway,
Shot 1 and Shot 2 as the human-approved working takes they are, any later shots (accepted / pending /
rejected) from the V0.7.36/39 lifecycle, the next planned shot (ready to generate), and the ACTIVE working
cut (F36 unless you changed it in V0.7.39) as your own trims. The F36/F42 history is kept in IMPORTED.
Lenient: anything whose file is missing or changed is remembered as TEXT-ONLY story context (never as a
take or reference), so the Director can keep making shots with the earlier context.

  python studio_import_v081.py --from /workspace/DirectorProjects/hallway_interview_v1 \
                               --to /workspace/DirectorStudio/hallway_interview
"""
from __future__ import annotations
import argparse, os, shutil, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import director_v0739 as dv
import comfy_graph_v0739 as cg
import studio_media_v080 as media
import studio_v080 as sv

ORIG_ROLE = {  # V0.7.35 original -> (owner key, Studio kind)
    'scene_original_opening_full.png': ('loc', 'location'),
    'person1_original_face.png': ('presenter', 'face'),
    'person1_original_lowerbody_identity.png': ('presenter', 'full'),
    'person2_original_full.png': ('grandpa', 'full'),
    'person2_original_upper.png': ('grandpa', 'upper'),
    'person2_original_face.png': ('grandpa', 'face'),
}
PROV = 'IMPORTED_V0735_SCENE_SEED_HASH_VERIFIED_ORIGINAL'

def import_hallway(src, dest, *, comfy_root, input_dir=None, output_dir=None, title='Hallway Interview'):
    """Returns (studio, notes). Lenient: what can't be verified is carried as text-only story memory
    (MEMORY_SHOT) so the Director still knows what happened, but is never used as a take or a reference."""
    src = Path(src); dest = Path(dest); notes = []
    if dest.exists(): raise sv.StudioError('PROJECT_EXISTS')
    st = dv.state(src)                                  # verifies base + lifecycle + direction ledgers (read-only)
    b = st['base']; scene = b['scene_brief']
    work = dest.with_name(dest.name + '.importing')     # built aside, renamed when complete: a crash never leaves half a project
    if work.exists(): shutil.rmtree(work)
    try:
        s = _build(src, work, st, b, scene, notes, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir, title=title)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True); raise
    os.replace(work, dest)
    return sv.Studio(dest, comfy_root, input_dir=input_dir, output_dir=output_dir), notes

def _build(src, dest, st, b, scene, notes, *, comfy_root, input_dir, output_dir, title):
    s = sv.Studio.create(dest, title, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir, tone=scene['tone'])
    owners = {'presenter': s.add_character('Presenter', pronoun='she', descriptors=['the presenter', 'presenter', 'the woman', 'interviewer'],
                                           description='casual outfit, handheld microphone'),
              'grandpa': s.add_character('Grandpa', pronoun='he', descriptors=['grandpa', 'the old man', 'older man', 'grandfather', 'the guest'],
                                         description='white hair, gray hoodie, gray sweatpants, sneakers'),
              'loc': s.add_location('Hallway', description=scene['setting'])}
    cast = [owners['presenter'], owners['grandpa']]
    by_name = {}
    for a in scene['original_assets']:
        if a['name'] not in ORIG_ROLE: continue
        who, kind = ORIG_ROLE[a['name']]
        try:
            ids = s.register_original(owners[who], a['name'], kind, a['sha256'], PROV)
            by_name[a['name']] = f"ref_{a['sha256'][:10]}_orig"
        except sv.StudioError as e:
            notes.append(f"Original {a['name']} not imported ({e}); add it again from the Studio if you have it.")
    out = s.output_dir

    def remember(shot, text, note, why):
        s._append('MEMORY_SHOT', {'shot': shot, 'text': text, 'note': note, 'cast': ['Presenter', 'Grandpa'], 'location': 'Hallway',
                                  'source': 'IMPORTED_HALLWAY_LEDGER', 'why_history_only': why})
        notes.append(f'Shot {shot}: remembered as story context only ({why}).')

    def import_take(shot, video_name, video_sha, status, note, text):
        f = out / video_name
        if not f.is_file() or media.sha256(f) != video_sha:
            if status == 'ACCEPTED': remember(shot, text, note, 'the video file is not in ComfyUI/output or has changed')
            return None
        prov = cg.embedded_provenance(f)
        ok = prov.get('status') == 'OK'
        if ok and (prov['video_or_audio_refs_linked'] or set(prov['images']) - set(by_name)): ok = False
        stt = s.state(); take = len((stt['shots'].get(shot) or {'plans': []})['plans']) + 1
        plan = {'shot': shot, 'take': take, 'mode': 'imported', 'text': text, 'spec': {}, 'cast': cast, 'location': owners['loc'],
                'refs': [by_name[n] for n in prov['images']] if ok else [], 'videos': [], 'loras': [], 'missing_refs_for': [],
                'prompt': prov.get('prompt') if ok else None, 'seed': prov.get('seed') if ok else None,
                'prefix': prov.get('filename_prefix') if ok and prov.get('filename_prefix') else Path(video_name).stem.rsplit('_', 1)[0],
                'workflow_file': None, 'workflow_sha256': None, 'understood_as': 'imported from the hallway Director',
                'assumptions': [], 'refused': [], 'ref_choice': ['imported: the original references this take was generated with' if ok
                                                                 else 'imported: references unknown (no embedded workflow)'],
                'lessons_used': {'avoid': [], 'keep': []}, 'generation_authorized': False, 'imported': True}
        s._append('SHOT_PLANNED', plan)
        meta = media.probe(f)
        s._append('TAKE_CAPTURED', {'shot': shot, 'take': take, 'file': video_name, 'sha256': video_sha, 'frames': meta.get('frames'),
                  'fps': meta.get('fps'), 'audio': meta.get('audio'), 'provenance': 'IMPORTED_V0735_LEDGER_HASH_BOUND',
                  'approved': status == 'ACCEPTED', 'imported': True})
        notes.extend(x for x in s._analyze_take(s.state(), shot, plan, f, video_sha, first=True) if 'failed' in x)
        if status in ('ACCEPTED', 'REJECTED'):
            s._append('TAKE_REVIEWED', {'shot': shot, 'take': take, 'decision': status, 'note': note, 'final_film_approval': False,
                      'imported': True})
        return take

    cap = b['capture_binding']; story = scene['working_story_state']
    import_take(1, cap['outgoing']['source_filename'], cap['outgoing']['source_sha256'], 'ACCEPTED', story.get('approved_shot1'),
                'The presenter opens the interview and offers Grandpa the microphone')
    import_take(2, cap['incoming']['source_filename'], cap['incoming']['source_sha256'], 'ACCEPTED', story.get('approved_shot2'),
                'Grandpa answers; the presenter listens')
    prog = st['progress']; accepted = {a['shot_number']: a for a in st['accepted']}
    rejected = {r['video_sha256']: r for r in prog.get('rejected_takes', [])}
    for c in sorted(st['captures'].values(), key=lambda c: c['shot_number']):
        sha = c['video_sha256']; n = c['shot_number']
        if sha in rejected: status, note = 'REJECTED', rejected[sha].get('reason_human_attested')
        elif accepted.get(n, {}).get('source_sha256') == sha:
            status = 'ACCEPTED'; note = accepted[n].get('story_observation_human_attested')
            note = None if note in (None, 'UNKNOWN') else note
        else: status, note = 'CAPTURED', None
        import_take(n, Path(c['video_path']).name, sha, status, note, f'Continue the interview (shot {n})')
    # the active working cut (F36 unless changed in V0.7.39) as trims; accepted shots not in it are left out
    stt = s.state(); in_cut = {seg['sha256']: seg for seg in st['timeline']}
    for shot, sh in stt['shots'].items():
        hit = False
        for t in sh['takes']:
            seg = in_cut.get(t['sha256'])
            if seg:
                hit = True
                s._append('TRIM_SET', {'shot': shot, 'take': t['take'], 'in': seg['in'], 'out': seg['out'], 'basis': 'HUMAN', 'learn': None, 'imported': True})
        if not hit and any(t['status'] == 'ACCEPTED' for t in sh['takes']):
            s._append('CLIP_EXCLUDED', {'shot': shot, 'imported': True, 'reason': 'accepted but not in the V0.7.x working cut'})
    order = [seg['shot'] for seg in st['timeline'] if seg['shot'] in stt['shots']]
    rest = [n for n in sorted(stt['shots']) if n not in order]
    if order: s._append('ORDER_SET', {'order': order + rest, 'imported': True})
    s._append('IMPORTED', {'source': str(src), 'project_id': b['project_id'], 'base_hash': b['latest_event_hash'],
              'progress_tail': prog['progress_tail_hash'], 'direction_tail': st['tail'], 'working_cut': st['timeline'],
              'cut_basis': st['cut_basis'], 'edit_history': b['working_edit'].get('history'), 'next_shot_in_source': prog['next_shot'],
              'source_modified': False, 'notes': notes[:40]})
    return s

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='src', default='/workspace/DirectorProjects/hallway_interview_v1')
    ap.add_argument('--to', dest='dst', default='/workspace/DirectorStudio/hallway_interview')
    ap.add_argument('--comfy-root', default='/workspace/ComfyUI')
    a = ap.parse_args(argv)
    s, notes = import_hallway(a.src, a.dst, comfy_root=a.comfy_root)
    for n in notes: print(' -', n)
    x = s.summary()
    print('IMPORTED', x['title'], '| shots', [sh['shot'] for sh in x['shots']], '| movie', [(c['shot'], c['in'], c['out']) for c in x['timeline']],
          f"| {x['movie_seconds']}s")

if __name__ == '__main__':
    main()
