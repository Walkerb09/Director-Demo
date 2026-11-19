#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
p=Path(__file__).resolve().parent
m=json.loads((p/'package_manifest.json').read_text())
for filename,expected in m['files'].items():
    q=p/filename
    if not q.is_file() or q.is_symlink() or hashlib.sha256(q.read_bytes()).hexdigest()!=expected:
        raise SystemExit('PACKAGE_CHECK_FAILED: '+filename)
print('V075_PACKAGE_HASHES_OK',len(m['files']))
