#!/usr/bin/env bash
# H3 Director V0.7.39 - say what you want in plain words:
#   bash run_director.sh "make her react"
#   bash run_director.sh "where are we?"
#   bash run_director.sh "try a closer angle" --stage      (also puts it in the ComfyUI Workflows sidebar)
#   bash run_director.sh "keep going" --generate           (queues it on ComfyUI: your explicit go-ahead)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/venv/main/bin/python; [ -x "$PY" ] || PY=python3
"$PY" "$HERE/verify_package.py" >/dev/null
"$PY" "$HERE/director_v0739.py" "${1:-where are we?}" "${@:2}"
