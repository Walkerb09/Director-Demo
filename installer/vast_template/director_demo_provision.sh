#!/bin/bash
# Director Studio PUBLIC 24 GB DEMO: Vast provisioning script (Vast runs it on first boot via PROVISIONING_SCRIPT).
# Public: no token, no private repo, no personal files. It fetches the demo bundle from a PUBLIC Hugging Face repo,
# checks its SHA-256 against latest.json, and starts the demo installer in the background.
# Template environment variables (all public-safe):
#   DIRECTOR_HF_REPO   the public demo repo, e.g.  yourname/director-demo
# Nothing else is needed. Never put HF_TOKEN or DIRECTOR_BACKUP_REPO in a PUBLIC template: its variables are visible.
# Log: /workspace/director_provision.log, then /workspace/director_install.log
set -uo pipefail
director_provision_main() {
mkdir -p /workspace
LOG=/workspace/director_provision.log
exec >>"$LOG" 2>&1
echo "== Director DEMO provisioning started $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
fail() { echo "DIRECTOR_PROVISION_STOPPED: $*"; exit 1; }
[ -n "${DIRECTOR_HF_REPO:-}" ] || fail "DIRECTOR_HF_REPO is not set (the template's environment variables)"
PY=/venv/main/bin/python
for _ in $(seq 1 180); do [ -f /workspace/ComfyUI/main.py ] && [ -x "$PY" ] && break; sleep 10; done
[ -f /workspace/ComfyUI/main.py ] || fail "no /workspace/ComfyUI/main.py after 30 minutes (use Vast's ComfyUI template as the base)"
[ -x "$PY" ] || fail "no ComfyUI Python at $PY"
"$PY" -c "import huggingface_hub" 2>/dev/null || "$PY" -m pip install -q huggingface_hub || fail "could not install huggingface_hub"
DL=/workspace/DirectorProvision; mkdir -p "$DL"
ZIP=$(HF_HOME=/workspace/.cache/huggingface DL="$DL" "$PY" - <<'PY'
import hashlib, json, os, sys
from huggingface_hub import hf_hub_download
repo, dl = os.environ['DIRECTOR_HF_REPO'], os.environ['DL']
try:
    meta = json.load(open(hf_hub_download(repo, 'latest.json', local_dir=dl)))
    name, want = meta['file'], meta['sha256'].lower()
    if '/' in name or not name.startswith('Director_Demo24_Public_') or not name.endswith('.zip'):
        raise ValueError('latest.json names an unexpected file: ' + repr(name))
    path = hf_hub_download(repo, name, local_dir=dl)
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    if h.hexdigest() != want: raise ValueError('SHA-256 mismatch for ' + name)
except Exception as e:
    print('DOWNLOAD_FAILED', type(e).__name__, str(e)[:300], file=sys.stderr); sys.exit(1)
print(path)
PY
) || fail "could not fetch the demo bundle from $DIRECTOR_HF_REPO (is the repo public?)"
echo "Bundle verified: $(basename "$ZIP")"
T=/workspace/DirectorDemoInstall
"$PY" -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$ZIP" "$T" || fail "could not unpack the bundle"
echo "Starting the demo installer"
DIRECTOR_PROFILE=lite24 bash "$T/install.sh" --lite24 </dev/null || fail "install.sh did not start"
echo "== Handed over to the installer. Watch: tail -f /workspace/director_install.log"
echo "   When it says DEMO INSTALL COMPLETED: restart ComfyUI, then open your ComfyUI link with /director at the end."
}
mkdir -p /workspace
if [ "${DIRECTOR_PROVISION_FOREGROUND:-}" = "1" ]; then director_provision_main; exit $?; fi
setsid bash -c "$(declare -f director_provision_main); director_provision_main" </dev/null >>/workspace/director_provision.log 2>&1 &
echo "Director demo provisioning continues in the background (PID $!). Watch it: tail -f /workspace/director_provision.log"
exit 0
