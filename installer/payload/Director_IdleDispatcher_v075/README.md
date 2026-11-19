# H3 Director V0.7.5 — Idle-Aware Post-Shot Dispatcher

V0.7.5 discovers saved-shot jobs from V0.6.6 and invokes the proven V0.7.4 unified profiled observer (Qwen + V0.7.3 Scene Context) **only after the ComfyUI queue has remained idle**. No new ComfyUI node, Director memory migration, H3 generation, output-video mutation, or visual reconditioning. The sole cast profile included is the explicit hallway-v1 profile, copied from V0.7.2.2. Other videos are ignored until an operator configures and validates an appropriate cast profile.

## Install and preview (no GPU, no writes)

Upload ZIP to `/workspace` then:

```bash
cd /workspace
unzip -oq Director_IdleDispatcher_v075.zip -d /workspace/DirectorIdleDispatcherV075
bash /workspace/DirectorIdleDispatcherV075/run_v075.sh
```

Preview reports eligible jobs only. Completed V0.7.4 jobs, unmatched profiles, and known failures are skipped. A summary merely *existing* is not proof of truth; the actual V0.7.4 stage validates evidence before publishing.

## Opt-in, one job at a time

```bash
bash /workspace/DirectorIdleDispatcherV075/run_v075.sh --once --run
```

This will process the oldest eligible hallway job, or say no eligible jobs. It uses V0.7.4 unchanged, whose observer validates video hash/job/profile, checks queue idle, unloads H3 caches if needed, and produces shadow candidate context. Runtime logs: `/workspace/DirectorSavedShotQueueV066/dispatcher_v075/logs/`. On a recognized non-transient failure, it marks the job as needing operator review; no repeated failing inference loop. Inspect logs before choosing `--once --run --retry-failed`.

## Opt-in foreground watch (NOT a daemon)

```bash
bash /workspace/DirectorIdleDispatcherV075/run_v075.sh --watch --run
```

Keep this terminal open; **Ctrl+C** to stop. At most one review at a time, with an exclusive process lock. Every time a new H3 job appears, the dispatcher waits for the ComfyUI generation queue to be idle for eight seconds before Qwen inference; it polls `/queue` during inference and **terminates its own V0.7.4/Qwen process group** if generation becomes queued or running, or the server cannot be reached. Partial Qwen stages are preserved by the existing observer and archived before its next attempt. The dispatcher doesn't kill or restart ComfyUI.

**Important concurrency limitation:** `/queue` polling is not an atomic GPU reservation or ComfyUI admission lock. An H3 prompt could start between polls (default two seconds); this approach minimizes contention but does *not* guarantee no overlap. For strict production isolation, use an explicit generation admission gate, two GPUs, or a scheduler controlling both H3 and Qwen. Avoid scheduling unattended H3 jobs during a Qwen review until that stronger coordination exists.

## Controls and protections

* `--once --run`: one eligible job and exit; `--watch --run`: recurring checks in the foreground.
* `--retry-failed` permits jobs marked as failed to be retried manually **with `--once --run` only**; it is rejected in watch mode to prevent repeated GPU failures.
* `--profiles-dir /path`: explicitly controlled directory of profile JSONs; no inference of actor identity for unknown projects. Ambiguous overlapping prefixes are rejected.
* `--idle-grace 8 --poll 2 --watch-interval 20`: tunable safety times; bounds enforced.
* Never alter H3 conditioning or cast/profile content without authorization.
* Conservatively fail closed if ComfyUI `/queue` is unreachable or malformed.
* Existing reports are not overwritten. The observer's own partial-stage archival handles interrupted Qwen jobs.
* Worker logs and failure markers are additive and live only in `dispatcher_v075` under the saved-shot queue root.

This is **observation**, not artistic interpretation or an auto-editor. Framing, action trajectories, microphone target, expression, and cross-cut continuity are not promoted to facts, and all editorial authority stays false. The dispatcher does not claim completed reviews are approved Scene DNA.
