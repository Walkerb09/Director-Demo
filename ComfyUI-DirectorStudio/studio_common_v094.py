"""Director Studio shared basics: constants, the StudioError, hashing, slugs, provenance checks."""
from __future__ import annotations
import hashlib, json, math, os, re, shutil, sqlite3, struct, subprocess, time, zlib
import numpy as np
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
import comfy_graph_v0739 as cg
import studio_intent_v080 as si
import studio_media_v080 as media
import director_v0739 as dv
import studio_qwen_v081 as qwen

SCHEMA = 'director-studio-v080'
ZERO = '0' * 64
MAX_REFS = 9                    # MiniMax H3 R2V limit (6 proven on your instance; 7-9 verified live at generate)
MAX_VIDEOS = 3                  # ref_videos.ref_video_0..2
CLIENT_ID = 'director-studio-v081'    # ComfyUI sends step progress for our prompts to websocket clients with this id
MAX_PEOPLE = 2                  # H3 struggles with more than two people per shot
FMT = {'width': 768, 'height': 1344, 'fps': 24, 'frames': 124}
UNDOABLE = {'SHOT_PLANNED', 'SOURCE_ADDED', 'CHARACTER_MAIN_SOURCE', 'TAKE_REVIEWED', 'TRIM_SET', 'CUTS_SET', 'CLIP_EXCLUDED', 'ORDER_SET', 'SETTING_SET',
            'REF_REMOVED', 'CHARACTER_UPDATED', 'CHARACTER_RETIRED', 'CHARACTER_RESTORED',
            'LOCATION_UPDATED', 'LOCATION_RETIRED', 'LOCATION_RESTORED', 'TAKE_REMEMBER'}

class StudioError(ValueError):
    pass

def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def ehash(seq, kind, payload, prev):
    return hashlib.sha256(canonical({'seq': seq, 'kind': kind, 'payload': payload, 'prev': prev}).encode()).hexdigest()
def slug(t):
    """ASCII id part. Accents fold (Zoë -> zoe); names with no Latin letters (李娜, Анна) get a stable short hash so two
    different such names never collide on the same id."""
    import unicodedata, hashlib as _h
    t = t or ''
    a = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^a-z0-9]+', '_', a.lower()).strip('_')[:40]
    if not s and t.strip(): s = 'u' + _h.sha1(t.strip().lower().encode()).hexdigest()[:10]
    return s or 'item'

HE_WORDS = set('''man men guy guys boy boys grandpa granddad grandfather father dad daddy son brother uncle husband boyfriend king prince
gentleman sir mr mister he him his himself waiter actor chef_he priest monk nephew groom fiance'''.split())
SHE_WORDS = set('''woman women girl girls lady ladies grandma granny grandmother mother mom mum mommy daughter sister aunt wife girlfriend queen
princess madam mrs ms miss she her hers herself waitress actress nun niece bride fiancee'''.split())
def guess_pronoun(*texts):
    """'Old man' -> 'he', 'the girl' -> 'she'; None when the words don't say (or say both)."""
    words = set(re.findall(r"[a-z]+", ' '.join(str(t or '') for t in texts).lower()))
    he, she = bool(words & HE_WORDS), bool(words & SHE_WORDS)
    return 'he' if he and not she else 'she' if she and not he else None

def same_name(a, b):
    import unicodedata
    f = lambda x: ' '.join(unicodedata.normalize('NFKC', x or '').casefold().split())
    return f(a) == f(b)

# ----------------------------------------------------------------------------- provenance gate
def png_text_keys(path):
    keys = set()
    try:
        with open(path, 'rb') as f:
            if f.read(8) != b'\x89PNG\r\n\x1a\n': return keys
            while True:
                hdr = f.read(8)
                if len(hdr) < 8: break
                n, t = struct.unpack('>I4s', hdr)
                data = f.read(n); f.read(4)
                if t in (b'tEXt', b'iTXt', b'zTXt'): keys.add(data.split(b'\x00', 1)[0].decode('latin1', 'replace').lower())
                if t == b'IEND': break
    except OSError: pass
    return keys

def generation_markers(path):
    """Evidence that a file came out of ComfyUI (or similar) generation."""
    p = Path(path); found = []
    if p.suffix.lower() == '.png':
        k = png_text_keys(p) & {'prompt', 'workflow', 'parameters'}
        if k: found.append('PNG_GENERATION_METADATA:' + ','.join(sorted(k)))
    if p.suffix.lower() in ('.jpg', '.jpeg', '.webp', '.png'):
        with open(p, 'rb') as f: head = f.read(4 << 20)
        if b'"class_type"' in head and b'"inputs"' in head: found.append('EMBEDDED_COMFYUI_WORKFLOW')
    else:
        try:
            tags = {k.lower() for k in media.probe(p).get('tags', {})}
            k = tags & {'prompt', 'workflow'}
            if k: found.append('VIDEO_GENERATION_METADATA:' + ','.join(sorted(k)))
        except ValueError: pass
    return found

# ----------------------------------------------------------------------------- studio
SETTLE_SECONDS = float(os.environ.get('DIRECTOR_SETTLE_SECONDS', '4'))

def file_is_settled(f, seconds=None):
    """A take is only read once ComfyUI/VHS has stopped writing it (no change for a few seconds)."""
    try: return time.time() - f.stat().st_mtime >= (SETTLE_SECONDS if seconds is None else seconds)
    except OSError: return False

_SHA_CACHE = {}
def same_file(f, sha):
    """sha256(f) == sha, cached by (path, size, mtime) so frequent scans don't re-hash every take."""
    try:
        stt = f.stat()
        if f.is_symlink() or not f.is_file(): return False
    except OSError: return False
    key = (str(f), stt.st_size, stt.st_mtime_ns)
    if key not in _SHA_CACHE: _SHA_CACHE[key] = media.sha256(f)
    return _SHA_CACHE[key] == sha

def _json_ok(path):
    try:
        if not path.is_file() or path.stat().st_size < 2: return False
        json.loads(path.read_text()); return True
    except Exception: return False
