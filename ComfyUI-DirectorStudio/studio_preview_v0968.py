"""V0.9.68 Playable previews: a take the browser can't play (10-bit H.264 or 4:4:4, HEVC...) is shown through an 8-bit
copy made from it once and cached. The take itself is never changed: it stays exactly as H3 made it (download the
original with ?raw=1), the Director measures and renders from it, and its fingerprint in the history stays valid."""
from __future__ import annotations
import json, os, subprocess, threading
from pathlib import Path

VERSION = 'play-1'
PLAYABLE = {('h264', 'yuv420p'), ('h264', 'yuvj420p'), ('vp9', 'yuv420p'), ('av1', 'yuv420p')}
_probe_cache = {}
_locks = {}
_guard = threading.Lock()


def probe(path):
    """-> (codec, pix_fmt), cached by path + size + mtime."""
    p = Path(path)
    try: st = p.stat()
    except OSError: return None
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key in _probe_cache: return _probe_cache[key]
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name,pix_fmt',
                            '-of', 'json', str(p)], capture_output=True, text=True, timeout=30)
        s = json.loads(r.stdout)['streams'][0]; got = (s.get('codec_name'), s.get('pix_fmt'))
    except Exception: got = None
    _probe_cache[key] = got
    return got


def playable(path):
    got = probe(path)
    return got is None or got in PLAYABLE          # unknown: serve as is (never block a file over a failed probe)


def preview_for(path, cache_dir, sha=None):
    """The file to send to the browser: the take itself when it plays, else its cached 8-bit copy (made now, once)."""
    path = Path(path)
    if playable(path): return path
    cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    tag = (sha or '')[:12] or f'{path.stat().st_size:x}'
    dst = cache_dir / f'{path.stem}_{tag}_{VERSION}.mp4'
    if dst.is_file(): return dst
    with _guard: lk = _locks.setdefault(str(dst), threading.Lock())
    with lk:
        if dst.is_file(): return dst
        tmp = dst.with_name(dst.stem + '.partial.mp4')
        base = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(path), '-map', '0:v:0', '-map', '0:a?',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p', '-movflags', '+faststart']
        r = subprocess.run(base + ['-c:a', 'copy', str(tmp)], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:                                       # sound the browser can't take as it is: AAC
            r = subprocess.run(base + ['-c:a', 'aac', '-b:a', '192k', str(tmp)], capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not tmp.is_file():
            try: tmp.unlink()
            except OSError: pass
            return path                                             # couldn't make one: the take as it is
        os.replace(tmp, dst)
    return dst
