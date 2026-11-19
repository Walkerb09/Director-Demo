"""Project backup / restore for Director Studio (Vast instances are temporary).

A backup is ONE zip:
  manifest.json                      schema, project, ledger tail, every file with sha256 + where it goes
  project/studio.sqlite3             the hash-chained ledger (a consistent sqlite snapshot)
  project/plans/*, project/analysis/*, project/uploads/*   workflows, measurements, your original uploads
  input/<name>                       every reference image / clip the project registered (ComfyUI/input)
  output/<take>.mp4                  the takes (optional: "with takes")
  taste/<slug>.json                  what the editor learned from you (also a prior for other projects)

Restore verifies every hash and the ledger chain BEFORE anything is written, never overwrites a different
file that is already there, and builds the project aside, renaming it into place only when complete.
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, sqlite3, tempfile, zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

SCHEMA = 'director-studio-backup-v090'
SAFE = re.compile(r'^[A-Za-z0-9_.\-/]{1,300}$')

class BackupError(ValueError): pass

def _sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def _safe_rel(name):
    p = PurePosixPath(name)
    if not SAFE.match(name) or p.is_absolute() or '..' in p.parts or name.startswith('/'): raise BackupError('UNSAFE_PATH_IN_BACKUP ' + name[:80])
    return p

def make_backup(studio, dest_zip, *, with_takes=False):
    """Write a backup of `studio` (a sv.Studio) to dest_zip. Returns the manifest."""
    st = studio.state(); root = studio.root; slug = st['project']['slug']
    files = []                                    # (arcname, source path)
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / 'studio.sqlite3'
        with closing(sqlite3.connect(studio.db)) as src, closing(sqlite3.connect(snap)) as dst: src.backup(dst)
        files.append(('project/studio.sqlite3', snap))
        for sub in ('plans', 'analysis', 'uploads', 'sources', 'films'):     # V0.9.42: films too
            d = root / sub
            if d.is_dir():
                for p in sorted(d.iterdir()):
                    if p.is_file() and not p.is_symlink() and not p.name.endswith('.partial'): files.append((f'project/{sub}/{p.name}', p))
        seen = set()
        for r in st['refs'].values():
            n = r['input_name']; f = studio.input_dir / n
            if n in seen or not f.is_file(): continue
            seen.add(n); files.append((f'input/{n}', f))
        if with_takes:
            for sh in st['shots'].values():
                for t in sh['takes']:
                    if t.get('source'): continue                 # your originals travel in project/sources
                    f = studio.output_dir / t['file']
                    if f.is_file() and not f.is_symlink(): files.append((f"output/{t['file']}", f))
        taste = root.parent / '_taste' / f'{slug}.json'
        if taste.is_file(): files.append((f'taste/{slug}.json', taste))
        for a, _ in files: _safe_rel(a)
        man = {'schema': SCHEMA, 'slug': slug, 'title': st['project']['title'], 'ledger_tail': st['tail'],
               'events': len(st['events']), 'with_takes': with_takes,
               'files': {a: {'sha256': _sha(p), 'size': p.stat().st_size} for a, p in files}}
        dest_zip = Path(dest_zip); tmp = dest_zip.with_name(dest_zip.name + '.partial')
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr('manifest.json', json.dumps(man, indent=1, sort_keys=True))
            for a, p in files:
                z.write(p, a, compress_type=zipfile.ZIP_STORED if p.suffix.lower() in ('.mp4', '.png', '.jpg', '.jpeg', '.webp', '.mov') else zipfile.ZIP_DEFLATED)
        os.replace(tmp, dest_zip)
    return man

def read_manifest(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        try: man = json.loads(z.read('manifest.json'))
        except KeyError: raise BackupError('NOT_A_DIRECTOR_STUDIO_BACKUP')
    if man.get('schema') != SCHEMA: raise BackupError('UNKNOWN_BACKUP_VERSION')
    return man

def restore_backup(zip_path, projects_root, *, comfy_root, input_dir=None, output_dir=None, new_slug=None):
    """Restore into projects_root/<slug> (or new_slug). Returns (studio, notes)."""
    import studio_v080 as sv
    projects_root = Path(projects_root); comfy_root = Path(comfy_root)
    input_dir = Path(input_dir) if input_dir else comfy_root / 'input'
    output_dir = Path(output_dir) if output_dir else comfy_root / 'output'
    man = read_manifest(zip_path)
    slug = new_slug or man['slug']
    if not re.fullmatch(r'[a-z0-9_]{1,40}', slug): raise BackupError('BAD_PROJECT_ID')
    dest = projects_root / slug
    if dest.exists(): raise BackupError('PROJECT_ALREADY_EXISTS_' + slug)
    notes = []
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist()) - {'manifest.json'}
        if names != set(man['files']): raise BackupError('BACKUP_FILE_LIST_MISMATCH')
        for a in names: _safe_rel(a)
        with tempfile.TemporaryDirectory(dir=projects_root if projects_root.is_dir() else None) as td:
            stage = Path(td) / 'x'; stage.mkdir()
            for a in sorted(names):                     # 1. extract + verify everything first
                info = man['files'][a]; out = stage / a; out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(a) as src, open(out, 'wb') as dst: shutil.copyfileobj(src, dst, 1 << 20)
                if _sha(out) != info['sha256']: raise BackupError('BACKUP_FILE_CORRUPTED ' + a)
            # 2. the ledger must verify on its own
            proj = stage / 'project'
            for sub in ('uploads', 'plans', 'analysis', 'renders', 'sources'): (proj / sub).mkdir(exist_ok=True)
            probe = sv.Studio(proj, comfy_root, input_dir=input_dir, output_dir=output_dir)
            st = probe.state()
            if st['tail'] != man['ledger_tail'] or len(st['events']) != man['events']: raise BackupError('LEDGER_DOES_NOT_MATCH_MANIFEST')
            # 3. reference files and takes into ComfyUI; never overwrite a different file
            conflicts = []
            for part, base in (('input', input_dir), ('output', output_dir)):
                d = stage / part
                if not d.is_dir(): continue
                for f in sorted(d.rglob('*')):
                    if not f.is_file(): continue
                    target = base / f.relative_to(d)
                    if target.exists() and _sha(target) != _sha(f): conflicts.append(str(f.relative_to(stage)))
            if conflicts: raise BackupError('DIFFERENT_FILES_ALREADY_IN_COMFYUI: ' + ', '.join(conflicts[:5]))
            copied = 0
            for part, base in (('input', input_dir), ('output', output_dir)):
                d = stage / part
                if not d.is_dir(): continue
                for f in sorted(d.rglob('*')):
                    if not f.is_file(): continue
                    target = base / f.relative_to(d)
                    if target.exists(): continue
                    target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, target); copied += 1
            tdir = stage / 'taste'
            if tdir.is_dir():
                (projects_root / '_taste').mkdir(parents=True, exist_ok=True)
                for f in tdir.iterdir():
                    t = projects_root / '_taste' / (f'{slug}.json' if new_slug else f.name)
                    if not t.exists(): shutil.copy2(f, t)
            projects_root.mkdir(parents=True, exist_ok=True)
            os.replace(proj, dest)
    s = sv.Studio(dest, comfy_root, input_dir=input_dir, output_dir=output_dir)
    try: s.sync()                                       # notices takes that didn't come along, so the movie leaves them out
    except Exception: pass
    missing = [t['file'] for sh in s.state()['shots'].values() for t in sh['takes'] if not s.take_path(t).is_file()]
    notes.append(f"Restored '{man['title']}' ({man['events']} history events, {copied} files copied into ComfyUI).")
    if missing: notes.append(f"{len(missing)} take video(s) are not in this backup or in ComfyUI/output; they are left out of the movie until the files are back "
                             '(the story memory still has them).')
    return s, notes
