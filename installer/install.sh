#!/usr/bin/env bash
# One command for a fresh Vast instance:   bash install.sh
# Runs in the background (safe if your terminal disconnects), logs to /workspace/director_install.log,
# and can simply be run again: finished, hash-verified downloads are kept and partial ones resume.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; SELF="$HERE/$(basename "$0")"     # absolute: works from any folder
cd "$HERE"
PY=/venv/main/bin/python; [ -x "$PY" ] || { echo "Expected ComfyUI's Python at /venv/main/bin/python"; exit 1; }
LOG=/workspace/director_install.log
# HuggingFace token: saved where the installer's downloads (HF_HOME=/workspace/.cache/huggingface) look for it, so the
# background run always has it. From HF_TOKEN if you exported it, else asked here (hidden). Never printed or logged.
TOKF=/workspace/.cache/huggingface/token
if [ "${1:-}" != "--foreground" ]; then
  if [ -n "${HF_TOKEN:-}" ]; then mkdir -p "$(dirname "$TOKF")"; (umask 077; printf '%s' "$HF_TOKEN" > "$TOKF")
  elif [ ! -s "$TOKF" ] && [ -t 0 ]; then
    read -rsp "HuggingFace token (optional: the demo needs none; just Enter to skip): " T || T=""; echo
    if [ -n "$T" ]; then mkdir -p "$(dirname "$TOKF")"; (umask 077; printf '%s' "$T" > "$TOKF"); echo "Token saved for the downloads."; fi
  fi
  if pgrep -f "DirectorFullInstall_RUNNING" >/dev/null 2>&1; then
    echo "An install is already running: tail -f $LOG"
    echo "(To restart it, e.g. with a new token: pkill -f DirectorFullInstall_RUNNING   then run this again.)"; exit 0; fi
  nohup bash "$SELF" --foreground "${@}" > "$LOG" 2>&1 &
  echo "Installing in the background. Watch it with:  tail -f $LOG"
  echo "The downloads (~53 GB) can take a while. When it says DEMO INSTALL COMPLETED, restart ComfyUI, then run:"
  echo "  $PY $HERE/check_ready.py"
  exit 0
fi
shift || true
export DirectorFullInstall_RUNNING=1
exec -a DirectorFullInstall_RUNNING "$PY" -u install.py "$@"
