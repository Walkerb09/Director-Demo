#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent
mf=json.loads((root/'package_manifest.json').read_text())
if mf.get('version')!='director-profiled-rolebinary-grammar-shadow-0.7.2.2':raise RuntimeError('Bad version')
for name,digest in mf['files'].items():
 p=(root/name).resolve(strict=True)
 if not p.is_relative_to(root) or not p.is_file():raise RuntimeError('Unsafe package path')
 if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('Modified '+name)
print('V0722_PACKAGE_HASHES_VERIFIED',len(mf['files']))
