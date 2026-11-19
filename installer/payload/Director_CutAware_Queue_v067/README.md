# H3 Director V0.6.7 — Cut-aware shadow evidence + resilient Qwen drain

An **additive, hash-guarded** upgrade of `/workspace/DirectorSavedShotQueueV066/drain_jobs.py`. No changes to the generation workflow, node IDs, original source references, H3 conditioning, saved video, V0.6.5 Qwen observer, Director memory, or video assembly. No restart is needed: the queue worker is a standalone script, not a loaded ComfyUI node.

## Why

- First live observer report for `Director_H3_TEST_Shadow_UNAPPROVED_00002-audio.mp4` had valid MIC geometry observations at F0–F87 and rejected contradictory invisible-microphone responses at F105 and F123.
- Independent CPU-only FFmpeg scene-score check detected a **high-contrast boundary candidate at approximately frame 101 (4.208 s)** on the separately provided video-only export of the same generation. It did not trigger on the earlier selected Shot 7 / Shot 8 excerpts at this conservative threshold. This is **not** a determination that an intentional cut occurred.
- The old queue worker failed when ComfyUI retained ~93 GiB of H3 weights and subsequently refused to retry because of partial results. This update adds an idle-only optional model-cache handoff and narrow failed-log preservation.

## Install on Vast

Upload `Director_CutAware_Queue_v067.zip` into `/workspace`:

```bash
cd /workspace
unzip -oq Director_CutAware_Queue_v067.zip -d /workspace/DirectorCutAwareV067
bash /workspace/DirectorCutAwareV067/run_v067.sh
```

The script runs tests (CPU only), checks for an *identical, unmodified* V0.6.6 drain worker, backs it up and upgrades only the worker and independent `cut_audit.py`. It also generates a separate cut-evidence sidecar next to the successful `00002` report **if its on-Vast source hash still matches**. That report is never overwritten. The installer refuses unknown source files and won't overwrite modified code. Repeat installs are harmless.

### Important output

```
V067_WORKER_INSTALLED .../drain_jobs.py
CUT_EVIDENCE_SAVED .../results/7a3275aa22778e5b32b50c94/cut_evidence.json
CUT_CANDIDATE_FRAMES 101
CUT_CROSSING_INTERVALS 1
```

Exact detection result on the `-audio.mp4` is **not yet verified** until this on-Vast run. The local frame-101 result is from a separately uploaded video-only copy. There may be differences in its encoding or timing.

If the saved report is missing, `PREVIOUS_REPORT_NOT_FOUND` is safe and not an install failure. If the source hash does not match the Qwen observation report, the audit refuses rather than claiming a cut.

## Next pending shot

```
/venv/main/bin/python /workspace/DirectorSavedShotQueueV066/drain_jobs.py --max-jobs 1
```

This now inspects VRAM and available RAM; if the ComfyUI queue is idle **and** GPU memory is below 24 GiB, it calls ComfyUI's documented `/free` endpoint to release cached models. It waits briefly and fails safely if resource checks do not pass. A new queued generation could race with the Qwen process; no perpetual queue reservation is implemented. Do not intentionally schedule Qwen and H3 concurrently.

On a retry with recognized **failed-only** artifacts (`request.json`, `worker.log`), these logs are moved to `failed_attempts/` before Qwen starts again. Existing successful reports or unfamiliar contents are never overwritten/archived. After Qwen returns, the separate CPU cut-evidence report is produced. If cut detection fails, the Qwen report stays intact and the run logs `V067_CUT_AUDIT_UNAVAILABLE`.

## Limitations / authority

The scene-score threshold (0.20) detects conservative **high-contrast frame-to-frame changes**. It can miss subtle scene cuts, and camera flashes/large disturbances can be false positives. The frame number is a CFR estimate derived from timestamp and 24fps metadata. The detector **does not determine whether an apparent cut is artistically intentional or a generation defect**. Its output highlights intervals that should **not** be naively bridged into a continuous action. The whole pipeline remains **shadow observation only**: no automatic trims, approvals, continuity handoff, sound interpretation, memory writes, generation, or use of generated images as H3 references.

### Rollback

```
/venv/main/bin/python /workspace/DirectorCutAwareV067/install_v067.py --rollback
```

Only rolls back the known V0.6.7 worker to its backed-up V0.6.6 version; refuses if current files are unexpectedly modified.
