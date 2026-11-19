"""V0.9.66 ☁ Cloud backup: your projects and decisions go to YOUR private Hugging Face repo by themselves.

Vast instances are temporary, and ⬇ Backup / ⬇ Export decisions are easy to forget before closing. This keeps a copy in
the cloud without you doing anything:
  - every few minutes (10 / 15 / 30 / 60, your choice), every project that CHANGED since its last cloud copy is backed up
    (the same zip as ⬇ Backup: the ledger, plans, analysis, your original uploads and every reference file; takes
    only if you tick "with takes") and uploaded; your decisions files go too (small, every time something changed);
  - when you close or leave the /director tab, one more round goes at once (only what changed);
  - "Back up now" any time;
  - the repo keeps the newest 5 backups per project (your choice) and removes older ones, all in ONE commit per project
    per round (Hugging Face limits commits per hour);
  - a FRESH instance (no projects yet) restores the newest cloud backup of every project by itself; with
    DIRECTOR_BACKUP_REPO in your Vast template it doesn't even need the settings first;
  - "Restore from cloud" lists your backed-up projects and restores one onto this instance (a fresh instance too),
    with the same checks as a restore from a file (every hash, the ledger chain, never overwriting a different file).

The repo is a PRIVATE Hugging Face dataset repo (it holds your photos and videos): it is made private if it doesn't exist,
and the Director refuses to upload to a repo that is public. The token is the one the installer saved
(/workspace/.cache/huggingface/token) or HF_TOKEN; it needs WRITE access. The token is never written to the settings file.

Layout in the repo:
  projects/<slug>/<slug>_<YYYYmmdd-HHMMSS>[_with_takes].zip
  projects/<slug>/latest.json        {"file", "sha256", "title", "events", "made"}
  decisions/<slug>.json              the same file ⬇ Export decisions gives
"""
from __future__ import annotations
import hashlib, json, os, re, tempfile, threading, time
from pathlib import Path

CONFIG = '_cloud.json'            # in the projects folder (no secrets in it)
STATE = '_cloud_state.json'       # what was last uploaded, per project
EVERY = (10, 15, 30, 60)
REPO_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}$')
SLUG_RE = re.compile(r'^[a-z0-9_]{1,40}$')
DEFAULTS = {'enabled': False, 'repo': '', 'every_min': 15, 'with_takes': False, 'keep': 5, 'auto_restore': True}

class CloudError(ValueError): pass


def token(workspace=None):
    t = (os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN') or '').strip()
    if t: return t
    places = []
    if os.environ.get('HF_HOME'): places.append(Path(os.environ['HF_HOME']) / 'token')
    if workspace: places.append(Path(workspace) / '.cache' / 'huggingface' / 'token')
    places += [Path('/workspace/.cache/huggingface/token'), Path.home() / '.cache' / 'huggingface' / 'token']
    for p in places:
        try:
            if p.is_file():
                t = p.read_text().strip()
                if t: return t
        except OSError: pass
    return None


def _ops():
    try:
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete
        return CommitOperationAdd, CommitOperationDelete
    except ImportError:                     # only without huggingface_hub (the offline tests); a real upload needs it anyway
        from types import SimpleNamespace
        return (lambda path_in_repo, path_or_fileobj: SimpleNamespace(kind='add', path_in_repo=path_in_repo, path_or_fileobj=path_or_fileobj),
                lambda path_in_repo: SimpleNamespace(kind='delete', path_in_repo=path_in_repo))


def default_api(tok):
    from huggingface_hub import HfApi
    return HfApi(token=tok)


class Cloud:
    """One per ComfyUI process. make_backup(slug, dest, with_takes) and decisions(slug) -> path come from the routes
    (they hold the project lock); api_factory(token) -> an HfApi-like object (a fake one in the tests)."""
    def __init__(self, projects_root, *, workspace=None, make_backup=None, decisions_file=None, api_factory=default_api,
                 lock=None, clock=time.time):
        self.root = Path(projects_root); self.workspace = workspace
        self.make_backup = make_backup; self.decisions_file = decisions_file
        self.api_factory = api_factory; self.lock = lock or threading.Lock(); self.clock = clock
        self.busy = threading.Lock()
        self.status = {'state': 'idle', 'last_round': None, 'last_ok': None, 'last_error': None, 'uploaded': [], 'running': None}

    # -- settings ------------------------------------------------------------------------------------------------
    def config(self):
        try: c = json.loads((self.root / CONFIG).read_text())
        except (OSError, ValueError): c = {}
        return {**DEFAULTS, **{k: c[k] for k in DEFAULTS if k in c}}

    def set_config(self, **kw):
        c = self.config()
        for k, v in kw.items():
            if k not in DEFAULTS: raise CloudError('BAD_CLOUD_SETTING_' + k)
            if k == 'repo':
                v = str(v or '').strip(); self._repo_ok = None
                if v and not REPO_RE.match(v): raise CloudError('CLOUD_REPO_LOOKS_LIKE_yourname/director-backups')
            if k == 'every_min' and int(v) not in EVERY: raise CloudError('CLOUD_EVERY_10_15_30_OR_60')
            if k == 'keep' and not 1 <= int(v) <= 50: raise CloudError('CLOUD_KEEP_1_TO_50')
            if k in ('enabled', 'with_takes', 'auto_restore'): v = bool(v)
            if k in ('every_min', 'keep'): v = int(v)
            c[k] = v
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (CONFIG + '.partial'); tmp.write_text(json.dumps(c, indent=1)); os.replace(tmp, self.root / CONFIG)
        return c

    def _state(self):
        try: return json.loads((self.root / STATE).read_text())
        except (OSError, ValueError): return {}

    def _save_state(self, s):
        tmp = self.root / (STATE + '.partial'); tmp.write_text(json.dumps(s, indent=1)); os.replace(tmp, self.root / STATE)

    # -- the repo ------------------------------------------------------------------------------------------------
    def _api(self):
        tok = token(self.workspace)
        if not tok: raise CloudError('NO_HUGGING_FACE_TOKEN: paste a WRITE token when the installer asks (or run '
                                     '`hf auth login` in the terminal), then try again')
        try: return self.api_factory(tok)
        except ImportError: raise CloudError('HUGGINGFACE_HUB_MISSING: run /venv/main/bin/python -m pip install "huggingface_hub>=0.35,<2"')

    def suggested_repo(self):
        try: return self._api().whoami()['name'] + '/director-backups'
        except Exception: return ''

    def _repo(self, api, create=True):
        c = self.config(); repo = c['repo']
        if not repo:
            try: repo = api.whoami()['name'] + '/director-backups'
            except Exception as e: raise CloudError(f'CLOUD_NO_REPO: type one, like yourname/director-backups ({str(e)[:80]})')
            self.set_config(repo=repo)
        if getattr(self, '_repo_ok', None) == repo and self.clock() - getattr(self, '_repo_ok_t', 0) < 3600:
            return repo                                    # V0.9.68 checked within the hour: no create/check calls every round
        if create: api.create_repo(repo, repo_type='dataset', private=True, exist_ok=True)
        info = api.repo_info(repo, repo_type='dataset')
        if not getattr(info, 'private', False):
            self._repo_ok = None
            raise CloudError(f'CLOUD_REPO_IS_PUBLIC: {repo} holds your photos and videos; make it private on huggingface.co first')
        self._repo_ok, self._repo_ok_t = repo, self.clock()
        return repo

    # -- what changed ----------------------------------------------------------------------------------------------
    def projects(self):
        if not self.root.is_dir(): return []
        return [p.name for p in sorted(self.root.iterdir()) if p.is_dir() and SLUG_RE.match(p.name) and (p / 'studio.sqlite3').is_file()]

    @staticmethod
    def _sha(p):
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
        return h.hexdigest()

    def fingerprint(self, slug, with_takes):
        """Changes when the project changes: the ledger's last event (every decision, plan, take and reference is an
        event in it), plus the films, the taste and the decisions files; with takes, also which take files exist."""
        import sqlite3
        from contextlib import closing
        p = self.root / slug; h = hashlib.sha256()
        try:
            with closing(sqlite3.connect(f'file:{p / "studio.sqlite3"}?mode=ro', uri=True, timeout=10)) as c:
                last = c.execute('SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            h.update(f'ledger:{last};'.encode())
        except Exception as e: h.update(f'ledger-unreadable:{e};'.encode())
        extra = sorted((p / 'films').glob('*.json')) if (p / 'films').is_dir() else []
        for f in [*extra, self.root / '_taste' / f'{slug}.json']:
            try: st = f.stat(); h.update(f'{f.name}:{st.st_size}:{st.st_mtime_ns};'.encode())
            except OSError: pass
        if with_takes: h.update(b'takes')
        return h.hexdigest()

    # -- one round -----------------------------------------------------------------------------------------------------
    def due(self):
        c = self.config()
        if not c['enabled']: return False
        last = self.status.get('last_round_t') or 0
        return self.clock() - last >= c['every_min'] * 60

    def backup_round(self, *, force=False, only=None, why='scheduled'):
        """Back up every project that changed (all of them with force). Returns a report; never raises for one
        project's failure (the others still go)."""
        if not self.busy.acquire(blocking=False): return {'skipped': 'a cloud backup is already running'}
        try:
            c = self.config(); self.status.update(state='running', running=why, last_round_t=self.clock())
            api = self._api(); repo = self._repo(api)
            state = self._state(); done = []; errors = []
            for slug in self.projects():
                if only and slug not in only: continue
                fp = self.fingerprint(slug, c['with_takes'])
                if not force and state.get(slug, {}).get('fingerprint') == fp: continue
                try:
                    done.append(self._one(api, repo, slug, c, state, fp))
                except Exception as e:
                    errors.append(f'{slug}: {type(e).__name__}: {str(e)[:200]}')
            self._save_state(state)
            stamp = time.strftime('%Y-%m-%d %H:%M:%S')
            self.status.update(state='idle', running=None, last_round=stamp, uploaded=done,
                               last_error=('; '.join(errors) or None))
            if not errors: self.status['last_ok'] = stamp
            return {'repo': repo, 'uploaded': done, 'errors': errors, 'unchanged': not done and not errors}
        except Exception as e:
            self.status.update(state='idle', running=None, last_round=time.strftime('%Y-%m-%d %H:%M:%S'),
                               last_error=f'{type(e).__name__}: {str(e)[:300]}')
            raise
        finally: self.busy.release()

    def _one(self, api, repo, slug, c, state, fp):
        stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime(self.clock()))
        name = f"{slug}_{stamp}{'_with_takes' if c['with_takes'] else ''}.zip"
        tmp_root = self.workspace if self.workspace and Path(self.workspace).is_dir() else None
        self._room_for(slug, c['with_takes'], tmp_root)
        with tempfile.TemporaryDirectory(dir=tmp_root) as td:
            z = Path(td) / name
            with self.lock: man = self.make_backup(slug, z, c['with_takes'])      # a consistent snapshot, under the project lock
            sha = self._sha(z)
            latest = Path(td) / 'latest.json'
            latest.write_text(json.dumps({'file': name, 'sha256': sha, 'title': man.get('title'), 'events': man.get('events'),
                                          'with_takes': c['with_takes'], 'made': time.strftime('%Y-%m-%d %H:%M:%S')}, indent=1))
            adds = [(str(z), f'projects/{slug}/{name}'), (str(latest), f'projects/{slug}/latest.json')]
            if self.decisions_file:
                try:
                    d = self.decisions_file(slug)
                    if d and Path(d).is_file(): adds.append((str(d), f'decisions/{slug}.json'))
                except Exception: pass
            try:
                old = sorted(f for f in api.list_repo_files(repo, repo_type='dataset')
                             if f.startswith(f'projects/{slug}/{slug}_') and f.endswith('.zip'))
                drop = old[:max(0, len(old) + 1 - c['keep'])]            # the new one counts toward "keep"
            except Exception: drop = []
            # ONE commit per project per round (Hugging Face limits commits per hour)
            Add, Delete = _ops()
            ops = [Add(path_in_repo=r, path_or_fileobj=l) for l, r in adds] + \
                  [Delete(path_in_repo=f) for f in drop]
            api.create_commit(repo_id=repo, repo_type='dataset', operations=ops,
                              commit_message=f'Backup {slug} ({man.get("events")} events)' + (f', {len(drop)} older removed' if drop else ''))
        state[slug] = {'fingerprint': fp, 'file': name, 'sha256': sha, 'at': time.strftime('%Y-%m-%d %H:%M:%S')}
        return {'project': slug, 'file': name, 'events': man.get('events'), 'with_takes': c['with_takes']}

    def _room_for(self, slug, with_takes, where):
        """Enough disk for the zip before it's made (a full disk mid-generation would hurt more than a skipped backup)."""
        import shutil as _sh
        p = self.root / slug; need = 0
        for sub in ('plans', 'analysis', 'uploads', 'sources', 'films'):
            d = p / sub
            if d.is_dir(): need += sum(f.stat().st_size for f in d.iterdir() if f.is_file())
        need = need * (4 if with_takes else 2) + 2 * 1024 ** 3           # references and takes aren't counted exactly: room to spare
        free = _sh.disk_usage(where or tempfile.gettempdir()).free
        if free < need: raise CloudError(f'NOT_ENOUGH_DISK_FOR_THE_BACKUP: {free / 1024 ** 3:.1f} GB free, about {need / 1024 ** 3:.1f} GB needed')

    # -- a fresh instance: your cloud projects come back by themselves --------------------------------------------------
    def adopt_env(self):
        """DIRECTOR_BACKUP_REPO set (your Vast template) and no settings yet: cloud backup on, to that repo."""
        repo = (os.environ.get('DIRECTOR_BACKUP_REPO') or '').strip()
        if repo and not (self.root / CONFIG).is_file() and REPO_RE.match(repo):
            self.set_config(enabled=True, repo=repo, auto_restore=True)
            return True
        return False

    def auto_restore(self, restore):
        """On an instance with NO projects yet (a fresh one), restore the newest cloud backup of every project.
        restore(zip_path) does the checked restore. A project you removed on a running instance is never brought back,
        because this only runs while the instance has no projects at all."""
        c = self.config()
        if not (c['enabled'] and c.get('auto_restore') and c['repo']) or self.projects(): return []
        done = []
        for p in self.listing()['projects']:
            if p['here'] or not p['backups']: continue
            with tempfile.TemporaryDirectory(dir=self.workspace if self.workspace and Path(self.workspace).is_dir() else None) as td:
                try:
                    got = restore(self.download(p['project'], p['backups'][0]['file'], td))
                    if isinstance(got, tuple): got = got[0].root.name                 # (studio, notes) from restore_backup
                    done.append(got if isinstance(got, str) and SLUG_RE.match(got) else p['project'])
                except Exception as e:
                    self.status['last_error'] = f"auto-restore of {p['project']}: {type(e).__name__}: {str(e)[:200]}"
        self.status['restored'] = done
        if done: self._save_state({s: {'fingerprint': self.fingerprint(s, c['with_takes'])} for s in done})   # no instant re-upload
        return done

    # -- restore -------------------------------------------------------------------------------------------------------
    def listing(self):
        api = self._api(); repo = self._repo(api, create=False)
        files = api.list_repo_files(repo, repo_type='dataset'); out = {}
        for f in files:
            m = re.match(r'^projects/([a-z0-9_]{1,40})/(\1_(\d{8}-\d{6})(_with_takes)?\.zip)$', f)
            if m: out.setdefault(m.group(1), []).append({'file': m.group(2), 'made': m.group(3), 'with_takes': bool(m.group(4))})
        here = set(self.projects())
        return {'repo': repo, 'projects': [{'project': s, 'here': s in here, 'backups': sorted(v, key=lambda x: x['made'], reverse=True)}
                                           for s, v in sorted(out.items())]}

    def download(self, slug, file, dest_dir):
        if not SLUG_RE.match(slug or '') or not re.fullmatch(rf'{re.escape(slug)}_\d{{8}}-\d{{6}}(_with_takes)?\.zip', file or ''):
            raise CloudError('NOT_ONE_OF_YOUR_CLOUD_BACKUPS')
        api = self._api(); repo = self._repo(api, create=False)
        p = api.hf_hub_download(repo_id=repo, repo_type='dataset', filename=f'projects/{slug}/{file}', local_dir=str(dest_dir))
        return Path(p)

    # -- the timer -----------------------------------------------------------------------------------------------------
    def tick(self):
        """Called about once a minute by the Director's background thread."""
        if not self.due(): return None
        try: return self.backup_round()
        except Exception: return None
