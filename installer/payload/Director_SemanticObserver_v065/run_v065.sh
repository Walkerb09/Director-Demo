#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN=/venv/main/bin/python
QWEN=/workspace/h3-qwen-venv/bin/python
OBS="$ROOT/addon/ComfyUI-Director-SemanticObserver-V063/semantic_observer.py"
[[ -x "$MAIN" ]] || { echo 'Missing /venv/main/bin/python' >&2; exit 1; }
[[ -x "$QWEN" ]] || { echo 'Missing /workspace/h3-qwen-venv/bin/python' >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo 'ffmpeg missing' >&2; exit 1; }
command -v ffprobe >/dev/null || { echo 'ffprobe missing' >&2; exit 1; }
cd "$ROOT"
echo '=== V0.6.5 LOCAL TESTS ==='
"$MAIN" -m unittest discover -s "$ROOT/tests" -v

echo '=== VERIFY HISTORICAL TEST VIDEOS ==='
"$MAIN" - "$ROOT" <<'PY'
import hashlib, json,sys
from pathlib import Path
p=Path(sys.argv[1]);j=json.loads((p/'media_manifest.json').read_text())
for name, expected in j['sources'].items():
    f=p/'media'/(name+'.mp4')
    actual=hashlib.sha256(f.read_bytes()).hexdigest()
    if actual!=expected:raise SystemExit('SOURCE_HASH_MISMATCH '+name)
    print('SOURCE_VERIFIED '+name+' '+actual,flush=True)
PY

STAMP="$(date -u +%Y%m%dT%H%M%S)_$$"
RESULTS="$ROOT/results/$STAMP"
mkdir -p "$RESULTS"
for shot in shot7 shot8; do
  case "$shot" in
    shot7) FRAMES=60,75,90,105;;
    shot8) FRAMES=11,23,69,81;;
  esac
  SOURCE="$ROOT/media/$shot.mp4"
  PRE="$RESULTS/pre_$shot"
  OUT="$RESULTS/$shot"
  echo "=== PREPROCESS $shot (NO MODEL) ==="
  if ! "$MAIN" -u "$OBS" --video "$SOURCE" --output "$PRE" \
    --source-class REENCODED_REVIEW_ASSEMBLY --frame-indices "$FRAMES" --preprocess-only; then
    echo "PREPROCESS FAILED: $PRE/worker.log" >&2
    tail -n 100 "$PRE/worker.log" 2>/dev/null || true
    exit 1
  fi
  echo "=== QWEN OBSERVER $shot ==="
  if ! "$MAIN" -u "$OBS" --video "$SOURCE" --output "$OUT" \
    --source-class REENCODED_REVIEW_ASSEMBLY --frame-indices "$FRAMES"; then
    echo "QWEN FAILED: $OUT/worker.log" >&2
    tail -n 100 "$OUT/worker.log" 2>/dev/null || true
    exit 1
  fi
done

echo '=== REVIEWED-ANCHOR REGRESSION CHECK ==='
"$MAIN" "$ROOT/check_regression.py" \
  --shot7 "$RESULTS/shot7/semantic_observation.json" \
  --shot8 "$RESULTS/shot8/semantic_observation.json" \
  --out "$RESULTS/reviewed_frame_comparison.json"

echo '=== CREATE SMALL REVIEW BUNDLE ==='
"$MAIN" - "$RESULTS" <<'PY'
from pathlib import Path
import sys,zipfile,json
p=Path(sys.argv[1]);z=p/'geometry_upgrade_review_bundle.zip'
files={'shot7_semantic_observation.json':p/'shot7/semantic_observation.json',
       'shot8_semantic_observation.json':p/'shot8/semantic_observation.json',
       'reviewed_frame_comparison.json':p/'reviewed_frame_comparison.json'}
with zipfile.ZipFile(z,'w',compression=zipfile.ZIP_DEFLATED) as f:
    for name,path in files.items():f.write(path,name)
print('V065_REVIEW_BUNDLE '+str(z),flush=True)
PY

echo '=== CHECK UPGRADE SAFETY ==='
"$MAIN" "$ROOT/install_v065.py"

echo '=== INSTALL IF REGRESSION MATCHES REVIEWED ANCHORS ==='
if "$MAIN" - "$RESULTS/reviewed_frame_comparison.json" <<'PY'
import sys,json
r=json.load(open(sys.argv[1]));sys.exit(0 if r['passes_local_regression_gate'] else 1)
PY
then
    "$MAIN" "$ROOT/install_v065.py" --install
    echo 'V065 INSTALLATION DONE: restart ComfyUI separately when queue idle if you want the updated node loaded.'
else
    echo 'V065 REGRESSION FLAGGED: kept installed observer unchanged. Send review bundle for diagnosis.'
fi

echo '=== UPLOAD THIS FILE ==='
echo "$RESULTS/geometry_upgrade_review_bundle.zip"
echo 'No ComfyUI restart, H3 generation, original footage conditioning, memory change, or editorial edits.'
