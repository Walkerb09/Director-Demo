#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-/venv/main/bin/python}"
if [[ ! -x "$PY" ]]; then PY="$(command -v python3)"; fi
"$PY" "$ROOT/verify_package.py"
echo '=== V0.7.3 SAFETY TESTS (CPU ONLY) ==='
"$PY" -m unittest discover -s "$ROOT/tests" -v
SHOT7_ROOT=/workspace/DirectorShot7HeldoutV0722
SHOT7_BUNDLE="$SHOT7_ROOT/Shot7_TwoPerson_v0722_Result.zip"
SHOT7_VIDEO="$SHOT7_ROOT/media/Director_ObservationOnly_Shot7_REENCODED.mp4"
SHOT7_OUT="$SHOT7_ROOT/scene_context_v073.json"
SHOT2_ROOT=/workspace/DirectorSavedShotQueueV066/results/7a3275aa22778e5b32b50c94
SHOT2_BUNDLE="$SHOT2_ROOT/scene_review_v0722_bundle.zip"
SHOT2_VIDEO=/workspace/ComfyUI/output/Director_H3_TEST_Shadow_UNAPPROVED_00002-audio.mp4
SHOT2_OUT="$SHOT2_ROOT/scene_context_v073.json"
echo '=== REUSE HELD-OUT SHOT 7 EVIDENCE (NO QWEN) ==='
"$PY" "$ROOT/scene_context_v073.py" \
  --bundle "$SHOT7_BUNDLE" --video "$SHOT7_VIDEO" --out "$SHOT7_OUT"
echo '=== REUSE SAVED SHOT 00002 EVIDENCE (NO QWEN) ==='
"$PY" "$ROOT/scene_context_v073.py" \
  --bundle "$SHOT2_BUNDLE" --video "$SHOT2_VIDEO" --out "$SHOT2_OUT"
echo "V073_SHOT7_CONTEXT $SHOT7_OUT"
echo "V073_SHOT00002_CONTEXT $SHOT2_OUT"
echo 'V073_DONE: NO GPU, NO COMFYUI RESTART, NO H3, NO AUTOMATIC EDIT OR CONTINUITY HANDOFF'
