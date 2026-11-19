# Director V0.6.5 — Geometry-First Semantic Observer (shadow upgrade)

This is a **guarded in-place upgrade of the existing V0.6.3 ComfyUI saved-video observer**, not a new Director runtime. It retains the ComfyUI node ID `DirectorV063SemanticTargetShadow`, its six input fields, and its two string outputs. Its underlying Qwen observation is upgraded from a single `MIC_TARGET` label to four independent fields:

- `HOLDER`: who physically holds the microphone handle;
- `TIP_TARGET`: whose **mouth the visible foam microphone head is physically nearest**;
- `TIP_VISIBLE`: whether the foam head can be identified;
- `TIP_Q`: uncalibrated model confidence (never proof of accuracy).

A single Qwen request sees a wide and upper-body view of **one verified frame at a time**. A deterministic pass compares sampled targets; it never invents intervening motion or authorizes edits. The source SHA-256 is checked again inside the worker. The source can be a new H3-generated clip **for observation only**. **Clean-Chain remains strict:** no generated frames or video are returned as H3 visual conditioning, no edits are authorized, and no Director memory is written.

## Install/test on Vast

Upload `Director_SemanticObserver_v065.zip` into `/workspace` and run:

```bash
cd /workspace
unzip -oq Director_SemanticObserver_v065.zip -d /workspace/DirectorSemanticObserverV065
bash /workspace/DirectorSemanticObserverV065/run_v065.sh
```

The runner:

1. Runs the local unit/FFmpeg/installer/rollback tests.
2. Verifies hash-pinned Shot 7 and Shot 8 **re-encoded review excerpts**, which are observational test evidence, never H3 conditioning.
3. Runs Qwen processor-only preflight on four frames of each clip, then runs Qwen on those frames.
4. Compares outputs with **eight earlier assistant visually reviewed source-frame labels**, with no labels shown to Qwen. These are a narrow regression reference, not a scientifically validated accuracy estimate.
5. Writes **one** `geometry_upgrade_review_bundle.zip` containing both Qwen reports and a regression comparison.
6. Only if all eight sampled targets match their prior visual review, checks the existing plugin and installs the upgrade in place, taking an **out-of-plugin rollback backup** first. Even a passing test grants NO editorial or continuity authority.

The installer refuses to overwrite customized/unknown plugin files or operate if ComfyUI queue state cannot be verified as idle. It will not restart ComfyUI, unload ComfyUI models, regenerate H3 content, alter human-approved cuts, or change Director memory. Existing `ComfyUI-H3-Reference-Director` and Observation Bridge V0.6.2 are untouched.

**Output:** Upload `geometry_upgrade_review_bundle.zip` from the path printed by the runner. If a worker fails, provide the corresponding `worker.log`.

**Prerequisites (already working in V0.6.3/0.6.4):** `/venv/main/bin/python`, `/workspace/h3-qwen-venv/bin/python` with Qwen3-VL-8B-Instruct, GPU with at least 22 GiB free memory, `ffmpeg`, `ffprobe`. No package installation or Supervisor restart.

## Backup/rollback

A successful upgrade prints a backup path like:

```
/workspace/DirectorSemanticObserverBackups/ComfyUI-Director-SemanticObserver-V063_before_v065_<token>
```

For rollback (after a queue-idle check), run:

```bash
/venv/main/bin/python /workspace/DirectorSemanticObserverV065/install_v065.py \
  --rollback /workspace/DirectorSemanticObserverBackups/<ACTUAL_BACKUP_DIRECTORY_PRINTED_BY_INSTALLER>
```

Replace the placeholder with the actual printed backup directory. The rollback validates the backup against the exact original V0.6.3 hashes. No restart is performed during either upgrade or rollback. After upgrading or restoring, a **separate operator-controlled restart when ComfyUI is idle** is required for the loaded GUI node to change.

## Intended next step

If the live regression succeeds, use the upgraded observer on other *newly saved* shots in shadow mode. Then design guarded workflow routing from verified observation JSON into continuity planning, with source-provenance and uncertainty checks. Don't give the model automatic editorial authority based on these two examples.
