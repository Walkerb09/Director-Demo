# H3 Reference Director V0.7.36 — Evolving Project Lifecycle (integrated memory)

V0.7.36 extends the **existing V0.7.35 project SQLite database** rather than inventing a new standalone project. Your current two-shot F36 working film, the superseded F42 decision, original-reference identities, and unverified-story safeguards remain unchanged. The new `progress_events` table has its own SHA-256 event chain bound to the existing V0.7.35 ledger tip. The V0.7.35 baseline data is never overwritten.

## The new behavior

- Before a new capture: `Continue the interview` retrieves the **same** planned Shot 3 and does not duplicate it.
- After a newly generated shot is captured: the Director discovers exactly one saved-shot JSON whose source filename has the **previously planned output prefix**; verifies job flags, SHA-256, video size, decoded stream type, FPS, dimensions, frame count and absence of an unresolved failure marker; **records CAPTURE only**. It does not use this generated video for H3 conditioning.
- If asked to continue while that shot is unreviewed: the Director stops at `HUMAN_REVIEW_EXISTING_CAPTURE`, rather than inventing acceptance or planning an incompatible new shot.
- After **explicit operator working-performance approval**, the Director can propose the next shot from original saved scene context and latest review state. No new detailed creative brief is necessary. The next proposal stays conservative when dialogue, action truth and ending pose remain unknown.
- Repeat `Continue the interview` and fresh process restarts preserve the same plan and event hashes. The current F36 **212-frame two-shot working film is not silently rewritten** or extended when a new *performance* is accepted. Adding that new footage to the film is a **separate editorial-cut decision**.
- Progress-aware export/import backs up **both** ledgers. The SQLite backup still does not include model weights, generated footage, or original images. Those must be restored independently on a new Vast instance.

## Install alongside V0.7.35 (no model downloads)

Upload `Director_ProjectLifecycle_v0736_Integrated.zip` to `/workspace`.

```bash
cd /workspace
unzip -n Director_ProjectLifecycle_v0736_Integrated.zip -d DirectorProjectLifecycleV0736
cd /workspace/DirectorProjectLifecycleV0736
/venv/main/bin/python verify_package.py
bash run_director.sh 'Continue the interview'
```

The first live run reopens `/workspace/DirectorProjects/hallway_interview_v1` **if already initialized** and shows `DIRECTOR_LIFECYCLE_ACTION REUSE_EXISTING_SHOT3_PLAN`. It does not submit a GPU job, automatically edit or approve, or touch V0.7.5.

To stage a prepared **candidate** workflow for visual inspection (without queueing H3):

```bash
/venv/main/bin/python director_project_progress_v0736.py stage
```

### Only after a future shot has actually been generated

These commands are **not needed now** for the uploaded `plan_shot03_v0735.json` alone. After the next actual saved-shot capture, run:

```bash
/venv/main/bin/python director_project_progress_v0736.py ingest
bash run_director.sh 'Continue the interview'
```

This should stop for review. Only **after you have personally watched the captured shot and chosen to accept its performance**:

```bash
/venv/main/bin/python director_project_progress_v0736.py approve --i-reviewed-and-approve-working
bash run_director.sh 'Continue the interview'
```

The one-line approval requires explicit confirmation but is not cryptographic identity authentication. It approves a **working performance only**, not a timeline cut, final film, verified transcript, H3 generation or Scene DNA promotion. An alternative programmatic `review --review-json ...` accepts a source-bound human review record with optional story note. The Director does not claim it independently verified such a note.

### Persistent backup before terminating Vast

```bash
bash save_memory_backup.sh
```

It writes an archive under `/workspace` that contains the project DB and both verified ledger tips. Download it **off** your ephemeral instance. Restore it with:

```bash
/venv/main/bin/python director_project_progress_v0736.py import \
  --backup /workspace/Director_Hallway_Memory_Backup_YYYYMMDDTHHMMSSZ.zip \
  --project /workspace/DirectorProjects/hallway_interview_v1
```

Restoration refuses to overwrite an existing project. Original media and six pristine references remain separate requirements.

## Evidence and limitations

This patch is **offline-tested** against real user-uploaded Shot 1/2 bytes, previously validated input hashes, and explicitly **synthetic test-only future-capture records**. We have NOT generated or accepted a real Shot 3. The live V0.7.36 progression and Shot 4 preparation still need authentic Vast validation when that shot exists.

Existing V0.7.35 behavior is unchanged until a future captured shot is ingested. V0.7.36's plan-after-review is still bounded, deterministic creative direction, not a model that understands the full conversational transcript or autonomously judges performance. The first two-shot F36 working cut remains active. All generated videos remain in the observation/editorial lane only.

## Offline tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests_controller -p 'test_*.py'
```
