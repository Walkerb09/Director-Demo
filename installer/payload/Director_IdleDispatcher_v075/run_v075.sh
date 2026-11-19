#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${V075_PYTHON:-/venv/main/bin/python}"
if [[ ! -x "$PY" ]]; then PY="$(command -v python3)"; fi
"$PY" "$HERE/verify_package.py"
echo '=== V0.7.5 LOCAL SAFETY TESTS (NO QWEN) ==='
"$PY" -m unittest discover -s "$HERE/tests" -q
echo '=== V0.7.5 IDLE DISPATCHER ==='
"$PY" "$HERE/director_idle_dispatcher_v075.py" --profiles-dir "$HERE/profiles" "$@"
