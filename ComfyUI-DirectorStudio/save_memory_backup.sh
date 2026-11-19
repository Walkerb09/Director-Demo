#!/usr/bin/env bash
# Back up ALL Director memory (base + lifecycle + direction ledgers + issued workflows) before stopping Vast.
# Download the zip it prints. Media and the six originals are NOT inside: keep those separately.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/venv/main/bin/python; [ -x "$PY" ] || PY=python3
OUT="/workspace/Director_Memory_Backup_v0739_$(date -u +%Y%m%dT%H%M%SZ).zip"
"$PY" "$HERE/director_v0739.py" --export "$OUT" "$@"
echo "BACKUP WRITTEN: $OUT   (restore: python director_v0739.py --import-backup <zip> --project <new path>)"
