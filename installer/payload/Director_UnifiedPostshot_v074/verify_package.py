#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent
manifest=json.loads((root/'package_manifest.json').read_text())
if manifest.get('schema')!='director-v074-files-v1' or not isinstance(manifest.get('files'),dict):
    raise SystemExit('Invalid package schema')
for name,digest in manifest['files'].items():
    path=root/name
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise SystemExit('Missing or unsafe package item: '+name)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise SystemExit('Package checksum differs: '+name)
print('V074_PACKAGE_VERIFIED',len(manifest['files']),'files')
