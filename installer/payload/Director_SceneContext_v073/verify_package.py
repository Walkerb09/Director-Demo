#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
m=json.loads((ROOT/'package_manifest.json').read_text())
if m.get('schema')!='director-v073-files-v1' or not isinstance(m.get('files'),dict):
 raise SystemExit('Unexpected package manifest')
for rel,d in m['files'].items():
 p=ROOT/rel
 if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(ROOT.resolve()):
  raise SystemExit('Missing/unsafe package path '+rel)
 if hashlib.sha256(p.read_bytes()).hexdigest()!=d:
  raise SystemExit('Mismatched package file '+rel)
print('V073_PACKAGE_VERIFIED',len(m['files']),'files')
