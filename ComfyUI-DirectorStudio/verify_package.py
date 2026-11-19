#!/usr/bin/env python3
"""Integrity check for the Director Studio V0.8 package: every file hashed, nothing missing, nothing extra."""
from pathlib import Path
import hashlib, json, sys
root = Path(__file__).resolve().parent
m = json.loads((root / 'package_manifest.json').read_text())
if m.get('schema') != 'director-v0970-package-manifest-v1': raise SystemExit('INVALID_MANIFEST_SCHEMA')
files = m['files']
for name, expected in sorted(files.items()):
    p = root / name
    if p.is_symlink() or not p.is_file(): raise SystemExit('MISSING_OR_LINKED_PAYLOAD:' + name)
    if hashlib.sha256(p.read_bytes()).hexdigest() != expected: raise SystemExit('HASH_MISMATCH:' + name)
extra = [str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts
         and p.suffix in ('.py', '.sh', '.json', '.js', '.html') and str(p.relative_to(root)) not in files
         and p.name != 'package_manifest.json' and 'demo_result' not in p.parts]
if extra: raise SystemExit('UNEXPECTED_CODE_OR_CONFIG_FILES:' + ','.join(sorted(extra)[:10]))
print('V0970_PACKAGE_INTEGRITY_PASS', len(files), 'files verified')
