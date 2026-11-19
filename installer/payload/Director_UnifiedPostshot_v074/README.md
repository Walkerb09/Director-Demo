# Director V0.7.4 — Unified Post-Shot Shadow Runner

**Purpose:** Replace the manual sequence of separate V0.7.2.2 observation and V0.7.3 Scene Context commands with **one operator-initiated post-shot command**. It uses the existing V0.6.6 capture jobs as the source and the installed V0.7.2.2 + V0.7.3 implementations, without changing either program or adding another ComfyUI node.

## What it actually does

- Identifies the captured job and validates its video **SHA-256**, metadata, allowed project **profile**, original capture job hash and current cut/sampling plan through the existing V0.7.2.2 preflight.
- Reuses an existing manifest-sealed V0.7.2.2 report. If missing and `--run` was explicitly specified, lets that observer run **one Qwen load** under its existing ComfyUI queue-idle and resource guards.
- Validates the observation bundle, its manifest, parsed observations and its source/job/profile binding **before** creating Scene Context.
- Builds/reuses the V0.7.3 Scene Context using the exact media SHA-256 verification and preserves all candidate/uncertain labels.
- Creates `director_postshot_v074.json`, a concise, provenance-linked summary with candidate cut barriers, role/prop evidence, **framing excluded** and the authority flags still false.
- Reruns are idempotent. Existing differing reports, conflicting context, mismatched job/profile/video, and unexpected symlinks are **refused**; no silent overwrites.

**This does not run H3, modify ComfyUI or restart it, edit footage, update Director memory, authorize automatic editing or continuity handoffs, or feed generated media back to H3.** It is not a background automation or a production editor. It does **not** change V0.6.6's old `done` markers; the legacy `drain_jobs.py` is a **different** observation lane. For future shots, use the unified command below instead of also draining that same job through the legacy microphone-only observer, unless a separate comparison is intentional.

## Install/extract (Vast)

Upload `Director_UnifiedPostshot_v074.zip` to `/workspace`, then:

```bash
cd /workspace
unzip -oq Director_UnifiedPostshot_v074.zip -d /workspace/DirectorUnifiedV074
# Verify the completed Shot 00002 without any new Qwen inference:
bash /workspace/DirectorUnifiedV074/run_v074.sh \
  --job-id 7a3275aa22778e5b32b50c94 --run
```

**For a future H3 clip after it has been captured by V0.6.6:**

```bash
# Preview which latest unreviewed saved shot would be selected (no GPU, no writes):
bash /workspace/DirectorUnifiedV074/run_v074.sh --latest
# Run one profiled Qwen pass only if it does not already exist, then build Scene Context:
bash /workspace/DirectorUnifiedV074/run_v074.sh --latest --run
```

The `--latest` selector only considers job filenames whose video basename matches `--profile`'s configured `video_name_prefix`; it prefers jobs without a completed V0.7.4 summary. You can always use explicit `--job-id` to avoid any ambiguity.

To use another project's configured cast, pass `--profile /path/to/the/project_profile.json` with a matching video name prefix and 1–4 clearly described cast roles. The output is *candidate evidence*, not verified identity: future projects need their own visual evaluation. 

**Required existing Vast components:**

- `/workspace/DirectorProfiledV0722/profiled_observer_v0722.py` and scene profile (V0.7.2.2)
- `/workspace/DirectorSceneContextV073/scene_context_v073.py` (V0.7.3)
- V0.6.6 capture jobs and V0.6.7 resource/cut tools used by V0.7.2.2
- Existing V0.6.5 frame decoder, Qwen env `/workspace/h3-qwen-venv/bin/python` only when model inference is needed

The script also runs 20 fast, CPU-only unit tests from bundled fixtures. Those establish software invariants, **not** visual accuracy or live GPU compatibility. The successful V0.7.2.2 and V0.7.3 tests on Shot 7 and Shot 00002 remain the visual calibration evidence; framing remains quarantined.

## Review output

```
/workspace/DirectorSavedShotQueueV066/results/<job-id>/director_postshot_v074.json
```

This JSON includes input hashes, the V0.7.2.2 bundle SHA, V0.7.3 context SHA, role and microphone visibility candidates, verified exact-frame sampling *as model evidence*, cut barriers, and explicit false authority flags. The `scene_review_v0722_bundle.zip` is still the visual audit bundle. Review both before considering any editorial action.
