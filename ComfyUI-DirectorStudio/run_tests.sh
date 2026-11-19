#!/usr/bin/env bash
# Offline test suite (no GPU, no ComfyUI, no network). ~20 minutes.
# Optional browser test of the /director UI: bash tests_v080/run_ui_e2e.sh  (needs aiohttp + playwright)
set -euo pipefail
cd "$(dirname "$0")"
PY=/venv/main/bin/python; [ -x "$PY" ] || PY=python3
export DIRECTOR_SAVED_ROOT="$(mktemp -d)"          # tests never touch /workspace
"$PY" verify_package.py
"$PY" -m unittest tests_v080.test_studio_v081
"$PY" -m unittest tests_v080.test_studio_v081b
"$PY" -m unittest tests_v080.test_studio_v082
"$PY" -m unittest tests_v080.test_studio_v090
"$PY" -m unittest tests_v080.test_studio_v080
"$PY" -m unittest tests_v080.test_update_v095
"$PY" -m unittest tests_v080.test_v0965
"$PY" -m unittest tests_v080.test_v0966
"$PY" -m unittest tests_v080.test_v0968
"$PY" -m unittest tests_v080.test_v0969
"$PY" -m unittest tests_v080.test_v0970
(cd qwen && "$PY" -m unittest discover -s tests -p 'test_*.py')
"$PY" -m unittest tests_v0739.test_director_v0739 tests_v0739.test_v0736_compat_on_v0739
"$PY" -m unittest discover -s tests -p 'test_*.py'
"$PY" -m unittest discover -s tests_controller -p 'test_*.py'
echo ALL_V0970_TESTS_PASS
