#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:---preflight}"
if [[ "$MODE" != "--preflight" && "$MODE" != "--run" ]]; then echo 'Usage: bash run_v0722.sh [--preflight|--run]' >&2; exit 2; fi
PY=/venv/main/bin/python
if [[ ! -x "$PY" ]];then echo 'Missing /venv/main/bin/python' >&2; exit 1; fi
printf '=== V0.7.2.2 ROLE-INDEPENDENT A/B OBSERVER ===\n'
"$PY" "$HERE/verify_package.py"
"$PY" -m unittest discover -s "$HERE/tests" -q
for dependency in \
  /workspace/DirectorSavedShotQueueV066/drain_jobs.py \
  /workspace/DirectorSavedShotQueueV066/cut_audit.py \
  /workspace/ComfyUI/custom_nodes/ComfyUI-Director-SemanticObserver-V063/semantic_observer.py \
  /workspace/DirectorSavedShotQueueV066/jobs/7a3275aa22778e5b32b50c94.json;do
  if [[ ! -f "$dependency" ]];then echo "MISSING_REQUIRED: $dependency" >&2; exit 1; fi
done
printf '=== EXISTING SHOT 00002: CHECK HASH, BOUNDARIES, AND NEW SAMPLE PLAN ===\n'
EXTRA=()
if [[ "$MODE" == '--run' ]];then EXTRA=(--run);fi
"$PY" "$HERE/profiled_observer_v0722.py" \
  --job-id 7a3275aa22778e5b32b50c94 \
  --profile "$HERE/profiles/hallway-v1.json" \
  "${EXTRA[@]}"
printf '=== V0.7.2.2 COMPLETE (SHADOW ONLY) ===\n'
