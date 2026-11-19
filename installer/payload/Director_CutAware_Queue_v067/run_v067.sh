#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${V067_PYTHON:-/venv/main/bin/python}"
if [[ ! -x "$PY" ]]; then PY=python3; fi

echo '=== V0.6.7 OFFLINE UNIT TESTS (NO GPU) ==='
"$PY" -m unittest discover -s "$ROOT/tests" -v

echo '=== STRICT INSTALLED WORKER CHECK ==='
"$PY" "$ROOT/install_v067.py"

echo '=== PATCH QUEUE WORKER (NO NODE RESTART) ==='
"$PY" "$ROOT/install_v067.py" --install

echo '=== RETROACTIVE CUT AUDIT FOR SAVED SHOT 00002 ==='
REPORT='/workspace/DirectorSavedShotQueueV066/results/7a3275aa22778e5b32b50c94/semantic_observation.json'
if [[ -f "$REPORT" ]]; then
    "$PY" /workspace/DirectorSavedShotQueueV066/cut_audit.py --report "$REPORT"
else
    echo "PREVIOUS_REPORT_NOT_FOUND (safe to skip): $REPORT"
fi

echo '=== DONE: NO H3, NO QWEN, NO COMFY RESTART ==='
echo 'For next pending shot (when ComfyUI queue idle):'
echo '  /venv/main/bin/python /workspace/DirectorSavedShotQueueV066/drain_jobs.py --max-jobs 1'
