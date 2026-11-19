#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${V074_PYTHON:-/venv/main/bin/python}"
if [[ ! -x "$PY" ]]; then PY="$(command -v python3)"; fi
"$PY" "$HERE/verify_package.py"
echo '=== V0.7.4 OFFLINE SAFETY TESTS (NO GPU) ==='
"$PY" -m unittest discover -s "$HERE/tests" -q
if [[ $# -eq 0 ]]; then
  set -- --job-id 7a3275aa22778e5b32b50c94
fi
echo '=== V0.7.4 UNIFIED SHADOW POST-SHOT ==='
"$PY" "$HERE/director_postshot_v074.py" "$@"
