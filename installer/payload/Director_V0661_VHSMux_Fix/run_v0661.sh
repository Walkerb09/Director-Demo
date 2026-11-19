#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=/venv/main/bin/python

printf '\n=== V0.6.6.1 VHS DUAL-OUTPUT FIX | UNIT TESTS ===\n'
"$PY" -m unittest discover -s "$ROOT/tests" -v

printf '\n=== VERIFY CURRENT V0.6.6 RELEASE ===\n'
"$PY" "$ROOT/install_v0661.py"

printf '\n=== INSTALL PATCH WITH ROLLBACK BACKUP (QUEUE-IDLE ONLY) ===\n'
"$PY" "$ROOT/install_v0661.py" --install

printf '\n=== RESTART COMFYUI TO LOAD PATCH (QUEUE-IDLE ONLY) ===\n'
"$PY" - <<'PY'
import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:18188/queue',timeout=10) as r:q=json.load(r)
if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
    raise SystemExit('Unknown queue state; restart refused')
if q['queue_running'] or q['queue_pending']:
    raise SystemExit('ComfyUI queue became active; restart refused')
print('QUEUE_IDLE_CONFIRMED')
PY
supervisorctl restart comfyui

printf '\n=== VERIFY COMFYUI NODE IS BACK ONLINE ===\n'
"$PY" - <<'PY'
import json,time,urllib.request
for i in range(80):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18188/object_info',timeout=5) as r:nodes=json.load(r)
        if 'DirectorV066SavedShotShadowQueue' not in nodes:
            raise RuntimeError('SavedShotQueue node missing after restart; inspect startup logs')
        print('V0661_COMFYUI_ONLINE_CAPTURE_NODE_REGISTERED')
        break
    except (OSError,TimeoutError):
        time.sleep(2)
else:raise SystemExit('ComfyUI did not come online after restart')
PY

printf '\n=== RECOVER ALREADY GENERATED VIDEO (NO H3 RERUN) ===\n'
"$PY" "$ROOT/recover_saved.py"

printf '\n=== DRY-RUN SHADOW ANALYSIS QUEUE (NO GPU LOAD) ===\n'
"$PY" /workspace/DirectorSavedShotQueueV066/drain_jobs.py --dry-run

printf '\n=== COMPLETE ===\n'
echo 'Generated video was captured without H3 regeneration.'
echo 'When ready for Qwen: /venv/main/bin/python /workspace/DirectorSavedShotQueueV066/drain_jobs.py --max-jobs 1'
