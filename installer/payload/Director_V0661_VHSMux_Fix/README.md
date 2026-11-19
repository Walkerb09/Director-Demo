# Director Saved-Shot Capture V0.6.6.1 — VHS audio-mux pairing fix

## Error addressed

`VHS_VideoCombine` can return both `shot.mp4` and `shot-audio.mp4` in a single `VHS_FILENAMES` result. V0.6.6 wrongly rejected the result as two unrelated videos. This patch identifies **only** the exact base/`-audio` sibling pair in the same folder, checks that the `-audio` variant contains **both audio and video tracks**, and captures that file for shadow observation.

This fixes the capture stage only. The saved H3 video is not regenerated or changed.

## Run on Vast

Upload this ZIP to `/workspace`. In a Vast terminal:

```bash
cd /workspace
unzip -oq Director_V0661_VHSMux_Fix.zip -d /workspace/DirectorV0661Fix
bash /workspace/DirectorV0661Fix/run_v0661.sh
```

The runner: checks 48 local regression tests; detects the exact original plugin via pinned checksums; refuses to replace a modified plugin; checks ComfyUI is idle; backs up `shot_capture.py` to `/workspace/DirectorSavedShotQueueV066/backups`; atomically replaces **only** `shot_capture.py`; checks the queue again; restarts ComfyUI once; verifies the node loaded; queues the **existing** `/workspace/ComfyUI/output/Director_H3_TEST_Shadow_UNAPPROVED_00001-audio.mp4` via its associated non-audio sibling; runs a **CPU-only dry run** of pending shadow analysis. This does not run Qwen or H3, modify Director memory, grant editing authority, or create H3 conditioning imagery.

The test video files need to exist in `/workspace/ComfyUI/output/` as shown by the previous error log. If they were moved or deleted, the recovery step safely reports that it cannot recover and does not invent a job.

## Then analyze the saved video (when ComfyUI queue is idle)

```bash
/venv/main/bin/python /workspace/DirectorSavedShotQueueV066/drain_jobs.py --max-jobs 1
```

Send the resulting `semantic_observation.json` for inspection. Qwen processing is deliberately separate from the generation workflow.

## Safe rollback

The installer stores the exact prior original module. To revert the patched module (only if the current module still matches this fix):

```bash
/venv/main/bin/python /workspace/DirectorV0661Fix/install_v0661.py --rollback
```

Then separately perform a queue-idle ComfyUI restart to load the reverted module. The rollback intentionally does **not** delete any saved footage or observation job.

## Boundaries

- Accept a single valid ComfyUI/output video, or a **single exact** base/`-audio` same-directory filename pair. Other multiple-video scenarios fail closed.
- All output paths must resolve inside ComfyUI's output root; symlink escapes fail.
- Audio-mux candidate must contain a video stream and an audio stream.
- The existing ComfyUI node ID, input/output signature, job schema, and observer remain unchanged.
- Clean-Chain: generated footage is used for post-generation observation **only**, never as H3 visual conditioning.
