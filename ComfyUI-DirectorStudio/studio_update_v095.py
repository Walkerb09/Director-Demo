"""Update the Director in place: no reinstall, no terminal.

  1. You upload a Director package zip (the full one, or a small update zip, see below) in the Director.
  2. It is checked before anything changes: the zip must hold one ComfyUI-DirectorStudio folder with a package manifest,
     no file may point outside that folder, every file in the manifest must be present and match its SHA-256, and the
     package's own integrity check (verify_package.py) must pass. A zip that fails changes nothing.
  3. The running version is MOVED (never deleted) to /workspace/DirectorBackups/ComfyUI-DirectorStudio_<version>_<time>,
     and the new one takes its place. Your projects (/workspace/DirectorStudio) are a different folder and are never touched.
  4. ComfyUI is restarted when you say so (it refuses while a generation is running, unless you insist).
  5. Roll back = the same swap with a kept version. The last 5 kept versions stay; older ones are removed.

Small update zips: a file listed in the new manifest may be left out of the zip when the installed copy already has
exactly that SHA-256 (the 46 MB of models usually don't change). It is then copied from the installed version.
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess, sys, threading, time, zipfile
from pathlib import Path

FOLDER = 'ComfyUI-DirectorStudio'
SCHEMA = re.compile(r'^director-v(\d+)-package-manifest-v1$')
KEEP = 5
START_DIR = os.getcwd()          # where ComfyUI was started (a restart starts there again, even if that folder was swapped)

class UpdateError(ValueError):
    pass

def _sha(p, cache=None):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def version_of(folder):
    """'v094' etc. from a package folder's manifest (None if it has none)."""
    try:
        m = SCHEMA.match(json.loads((Path(folder) / 'package_manifest.json').read_text()).get('schema', ''))
        return 'v' + m.group(1) if m else None
    except Exception: return None

def backups_dir(workspace):
    return Path(workspace) / 'DirectorBackups'

def kept_versions(workspace):
    d = backups_dir(workspace)
    if not d.is_dir(): return []
    out = []
    for p in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.is_dir() and p.name.startswith(FOLDER + '_') and (p / 'package_manifest.json').is_file():
            out.append({'name': p.name, 'version': version_of(p), 'kept_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(p.stat().st_mtime))})
    return out

def _safe_members(z):
    names = []
    for i in z.infolist():
        n = i.filename.replace('\\', '/')
        if n.startswith('/') or '..' in n.split('/') or re.match(r'^[A-Za-z]:', n): raise UpdateError('ZIP_HAS_A_FILE_OUTSIDE_ITS_FOLDER: ' + n)
        if (i.external_attr >> 16) & 0o170000 == 0o120000: raise UpdateError('ZIP_HAS_A_LINK: ' + n)
        names.append(n)
    return names

def check_zip(zip_path, installed):
    """Read-only look at an update zip. Returns {'version', 'files', 'from_zip', 'from_installed', 'changed', 'same'}."""
    try: z = zipfile.ZipFile(zip_path)
    except Exception: raise UpdateError('NOT_A_ZIP_FILE')
    with z:
        names = _safe_members(z)
        tops = {n.split('/')[0] for n in names if n.strip('/')}
        if tops != {FOLDER}: raise UpdateError(f'THIS_IS_NOT_A_DIRECTOR_PACKAGE: the zip must hold one {FOLDER} folder (found {", ".join(sorted(tops)[:4]) or "nothing"})')
        try: man = json.loads(z.read(f'{FOLDER}/package_manifest.json'))
        except KeyError: raise UpdateError('THE_ZIP_HAS_NO_PACKAGE_MANIFEST')
        except Exception: raise UpdateError('THE_PACKAGE_MANIFEST_IS_DAMAGED')
        m = SCHEMA.match(str(man.get('schema', '')))
        if not m or not isinstance(man.get('files'), dict) or not man['files']: raise UpdateError('THE_PACKAGE_MANIFEST_IS_DAMAGED')
        inzip = set(n[len(FOLDER) + 1:] for n in names if not n.endswith('/'))
        old = {}
        try: old = json.loads((Path(installed) / 'package_manifest.json').read_text()).get('files', {})
        except Exception: pass
        from_zip, from_inst, missing = [], [], []
        for rel, sha in man['files'].items():
            if '..' in rel.split('/') or rel.startswith('/'): raise UpdateError('THE_PACKAGE_MANIFEST_IS_DAMAGED')
            if rel in inzip:
                h = hashlib.sha256()
                with z.open(f'{FOLDER}/{rel}') as f:
                    for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
                if h.hexdigest() != sha: raise UpdateError('A_FILE_IN_THE_ZIP_IS_DAMAGED: ' + rel + ' (download it again)')
                from_zip.append(rel)
            elif old.get(rel) == sha and (Path(installed) / rel).is_file():
                from_inst.append(rel)
            else: missing.append(rel)
        if missing: raise UpdateError(f'THE_ZIP_IS_MISSING_{len(missing)}_FILES that your installed version does not have either: ' + ', '.join(missing[:5]))
    changed = sorted(r for r, s in man['files'].items() if old.get(r) != s)
    return {'version': 'v' + m.group(1), 'installed': version_of(installed), 'files': len(man['files']), 'from_zip': len(from_zip),
            'from_installed': len(from_inst), 'changed': len(changed), 'changed_files': changed[:40],
            'same': not changed and set(old) == set(man['files'])}

def _prune(workspace):
    vs = kept_versions(workspace)
    for v in vs[KEEP:]:
        p = backups_dir(workspace) / v['name']
        if p.parent == backups_dir(workspace) and p.name.startswith(FOLDER + '_'): shutil.rmtree(p, ignore_errors=True)

def prune_project_backups(workspace, keep=3):
    """Only the last `keep` 'projects_before_update_*' folders stay (each holds a backup zip per project)."""
    d = backups_dir(workspace)
    if not d.is_dir(): return
    olds = sorted((p for p in d.iterdir() if p.is_dir() and p.name.startswith('projects_before_update_')), key=lambda p: p.name, reverse=True)
    for p in olds[keep:]:
        if p.parent == d: shutil.rmtree(p, ignore_errors=True)

def _swap(new, installed, workspace, label):
    """Move the running version to DirectorBackups and `new` into its place; puts the old one back if the move fails."""
    installed = Path(installed); base = f'{FOLDER}_{label}_{time.strftime("%Y%m%d_%H%M%S")}'; keep = backups_dir(workspace) / base; i = 1
    while keep.exists(): i += 1; keep = backups_dir(workspace) / f'{base}_{i}'
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(installed), str(keep)); os.utime(keep)                 # 'kept at' = now (a moved folder keeps its old time)
    try: shutil.move(str(new), str(installed))
    except Exception:
        if installed.exists(): shutil.rmtree(installed, ignore_errors=True)
        shutil.move(str(keep), str(installed)); raise
    return keep

def apply_zip(zip_path, installed, workspace, python=sys.executable):
    """Check, unpack next to the installed copy, verify, swap. Returns the check info plus 'kept' (where the old one went)."""
    installed = Path(installed); info = check_zip(zip_path, installed)
    if info['same']: raise UpdateError(f'ALREADY_INSTALLED: this is exactly the version you have ({info["version"]})')
    stage = installed.parent.parent / '.director_update_stage'                 # outside custom_nodes so ComfyUI never loads it
    if stage.exists(): shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as z: z.extractall(stage)
        new = stage / FOLDER
        man = json.loads((new / 'package_manifest.json').read_text())
        for rel, sha in man['files'].items():
            dst = new / rel
            if not dst.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(installed / rel, dst)
            if _sha(dst) != sha: raise UpdateError('A_FILE_CHANGED_WHILE_UPDATING: ' + rel)
        if (new / 'verify_package.py').is_file():
            r = subprocess.run([python, str(new / 'verify_package.py')], capture_output=True, text=True, timeout=300)
            if r.returncode: raise UpdateError('THE_NEW_VERSION_FAILED_ITS_OWN_CHECK: ' + (r.stdout + r.stderr).strip()[-300:])
        kept = _swap(new, installed, workspace, info['installed'] or 'old')
    finally:
        if stage.exists(): shutil.rmtree(stage, ignore_errors=True)
    _prune(workspace)
    info['kept'] = kept.name; info['restart_needed'] = True
    return info

def rollback(name, installed, workspace):
    src = backups_dir(workspace) / name
    if '/' in name or '..' in name or not name.startswith(FOLDER + '_') or not (src / 'package_manifest.json').is_file():
        raise UpdateError('NO_SUCH_KEPT_VERSION')
    stage = Path(installed).parent.parent / '.director_update_stage'
    if stage.exists(): shutil.rmtree(stage)
    shutil.copytree(src, stage / FOLDER, symlinks=True)                    # the kept copy stays kept
    try: kept = _swap(stage / FOLDER, installed, workspace, version_of(installed) or 'old')
    finally: shutil.rmtree(stage, ignore_errors=True)
    _prune(workspace)
    return {'version': version_of(installed), 'kept': kept.name, 'restart_needed': True}

def restart_comfyui(delay=1.5):
    """Restart this ComfyUI process the way ComfyUI-Manager does (same command line, same process id, so supervisord on
    Vast keeps watching it). Runs after `delay` seconds, so the web reply gets out first."""
    def go():
        time.sleep(delay)
        try: sys.stdout.flush(); sys.stderr.flush()
        except Exception: pass
        try: os.chdir(START_DIR)
        except Exception: pass
        os.execv(sys.executable, [sys.executable] + (list(sys.argv) or ['main.py']))
    threading.Thread(target=go, daemon=True).start()
