"""Voice references for MiniMax H3: a clean stretch of one person's speech, from YOUR audio or video file.

H3 takes a standalone audio asset as <Audio N> (official MiniMax-H3 reference prompt guide): declared in
subject_definitions as "the voice-timbre reference for <Subject n> (S1)", the summary tag gets "+ audio reference",
retention_analysis marks it "reference", and the original words of the clip are NOT carried into the new video.

Any common file works: wav, mp3, m4a/aac, ogg/opus, flac, aiff, wma, amr, or the soundtrack of a video (mp4/mov/webm/mkv).
The Director finds the stretch with the most continuous speech (energy-based voice activity), 5-15 s (about 10 s
by default), removes rumble below 70 Hz, levels it to about -20 dBFS RMS with a safe peak, adds 20 ms fades and
saves 48 kHz mono 16-bit WAV. It measures what it can (speech seconds, signal-to-noise, clipping) and says so;
it can't tell whether two people talk in the clip, so you're asked for a clip with only that person speaking.
"""
from __future__ import annotations
import json, math, subprocess
from pathlib import Path
import numpy as np

AUDIO_EXT = {'.wav', '.mp3', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.flac', '.aif', '.aiff', '.wma', '.amr', '.caf', '.weba',
             '.mp4', '.mov', '.m4v', '.webm', '.mkv', '.3gp'}
SR = 16000                     # analysis rate
HOP = 0.03                     # 30 ms analysis frames
MIN_SPEECH = 3.0               # seconds of speech needed at least
TARGET, MAX_LEN, MIN_LEN = 10.0, 15.0, 5.0

class VoiceError(ValueError):
    pass

def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,sample_rate,channels:format=duration',
                        '-of', 'json', str(path)], capture_output=True, text=True, timeout=60)
    if r.returncode: raise VoiceError('UNREADABLE_AUDIO_FILE')
    j = json.loads(r.stdout or '{}')
    a = [s for s in j.get('streams', []) if s.get('codec_type') == 'audio']
    if not a: raise VoiceError('NO_SOUND_IN_THIS_FILE')
    return {'duration': float((j.get('format') or {}).get('duration') or 0), 'codec': a[0].get('codec_name'),
            'sample_rate': int(a[0].get('sample_rate') or 0), 'channels': int(a[0].get('channels') or 0)}

def decode(path, sr=SR, start=None, length=None):
    cmd = ['ffmpeg', '-nostdin', '-v', 'error']
    if start is not None: cmd += ['-ss', f'{start:.3f}']
    cmd += ['-i', str(path), '-vn', '-ac', '1', '-ar', str(sr)]
    if length is not None: cmd += ['-t', f'{length:.3f}']
    cmd += ['-f', 'f32le', '-']
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode: raise VoiceError('AUDIO_DECODE_FAILED ' + r.stderr.decode(errors='replace')[-200:])
    return np.frombuffer(r.stdout, dtype=np.float32)

def frames_db(x, sr=SR, hop=HOP):
    n = int(sr * hop); k = len(x) // n
    if k == 0: return np.zeros(0)
    f = x[:k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt((f.astype(np.float64) ** 2).mean(1)) + 1e-9)

def speech_mask(db):
    """Energy voice activity: loud enough above the noise floor; short gaps (< 300 ms) inside speech are filled,
    blips shorter than 150 ms removed."""
    if not len(db): return np.zeros(0, bool), -90.0, -90.0
    floor = float(np.percentile(db, 10)); loud = float(np.percentile(db, 95))
    thr = max(floor + 12.0, loud - 30.0, -50.0)
    m = db > thr
    gap = int(0.3 / HOP); blip = int(0.15 / HOP)
    i = 0; n = len(m)
    while i < n:                                   # fill short gaps
        if not m[i]:
            j = i
            while j < n and not m[j]: j += 1
            if 0 < i and j < n and j - i <= gap: m[i:j] = True
            i = j
        else: i += 1
    i = 0
    while i < n:                                   # drop blips
        if m[i]:
            j = i
            while j < n and m[j]: j += 1
            if j - i < blip: m[i:j] = False
            i = j
        else: i += 1
    return m, floor, thr

def best_window(mask, total_s, target=TARGET, max_len=MAX_LEN, min_len=MIN_LEN):
    """(start_s, length_s) of the window with the most speech; starts/ends nudged onto pauses when possible."""
    n = len(mask); per = HOP
    if total_s <= max_len: return 0.0, total_s
    w = int(round(min(max(target, min_len), max_len) / per))
    c = np.concatenate([[0], np.cumsum(mask.astype(np.int32))])
    sums = c[w:] - c[:-w] if n > w else np.array([c[-1]])
    s0 = int(np.argmax(sums))
    # start at the beginning of the speech run inside the window (skip leading silence)
    while s0 < n - w and not mask[s0]: s0 += 1
    s0 = max(0, s0 - int(0.2 / per))                # keep 200 ms of breath before the first word
    return s0 * per, min(w * per + 0.4, total_s - s0 * per)

def extract_voice(src, out_wav, *, target=TARGET):
    """Pick and clean the best stretch of speech. Returns a report dict; raises VoiceError when there isn't enough speech."""
    src = Path(src); out_wav = Path(out_wav)
    meta = probe(src)
    x = decode(src)
    total = len(x) / SR
    if total < 1.0: raise VoiceError('AUDIO_TOO_SHORT_NEED_AT_LEAST_3_SECONDS_OF_SPEECH')
    db = frames_db(x); mask, floor, thr = speech_mask(db)
    speech_total = float(mask.sum() * HOP)
    if speech_total < MIN_SPEECH:
        raise VoiceError(f'NOT_ENOUGH_CLEAR_SPEECH: found about {speech_total:.1f} s of speech, need at least {MIN_SPEECH:.0f} s '
                         '(a clip where only this person talks, without music)')
    start, length = best_window(mask, total, target=target)
    a, b = int(start / HOP), int((start + length) / HOP)
    seg_mask = mask[a:b]; seg_db = db[a:b]
    speech_s = float(seg_mask.sum() * HOP)
    speech_level = float(np.median(seg_db[seg_mask])) if seg_mask.any() else float(np.median(seg_db))
    noise_level = float(np.percentile(seg_db[~seg_mask], 50)) if (~seg_mask).sum() > 5 else floor
    snr = speech_level - noise_level
    seg = x[int(start * SR):int((start + length) * SR)]
    clip_frac = float((np.abs(seg) > 0.985).mean()) if len(seg) else 0.0
    sm = np.repeat(seg_mask, int(SR * HOP)); sm = np.pad(sm, (0, max(0, len(seg) - len(sm))))[:len(seg)]
    voiced = seg[sm] if sm.any() else seg
    rms_db = 20 * math.log10(float(np.sqrt((voiced.astype(np.float64) ** 2).mean())) + 1e-9) if len(voiced) else -90.0
    gain = max(-20.0, min(24.0, -20.0 - rms_db))
    fade_out = max(0.0, length - 0.02)
    flt = f'highpass=f=70,volume={gain:.2f}dB,alimiter=limit=0.891:level=false,afade=t=in:d=0.02,afade=t=out:st={fade_out:.3f}:d=0.02'
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_wav.with_suffix('.partial.wav')
    r = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(src), '-t', f'{length:.3f}', '-vn',
                        '-ac', '1', '-ar', '48000', '-af', flt, '-c:a', 'pcm_s16le', '-map_metadata', '-1', str(tmp)],
                       capture_output=True, timeout=600)
    if r.returncode: raise VoiceError('AUDIO_ENCODE_FAILED ' + r.stderr.decode(errors='replace')[-200:])
    tmp.replace(out_wav)
    warnings = []
    if snr < 10: warnings.append(f'the clip is noisy (speech only {snr:.0f} dB above the background); a quieter recording gives a cleaner voice')
    if clip_frac > 0.001: warnings.append('the recording is distorted (clipped) in places; a less loud recording works better')
    if speech_s < 5: warnings.append(f'only {speech_s:.1f} s of speech; 8-15 s gives H3 more of the voice')
    warnings.append('make sure only this person speaks in the clip (the Director cannot tell two voices apart)')
    return {'source': meta, 'start': round(start, 2), 'seconds': round(length, 2), 'speech_seconds': round(speech_s, 2),
            'speech_ratio': round(speech_s / max(length, 1e-6), 2), 'snr_db': round(snr, 1), 'clipped_fraction': round(clip_frac, 5),
            'gain_db': round(gain, 1), 'file': out_wav.name, 'format': 'wav 48 kHz mono 16-bit', 'warnings': warnings,
            'method': 'ENERGY_VAD_MEASUREMENT_ONLY'}

# ---------------------------------------------------------------------------------------- Clean-Chain for sound
AUDIO_MATCH = 0.90            # loudness-envelope correlation that means "this is the sound of that generated take" (measured: copies 0.99+, unrelated speech <= 0.45)

def _envelope(x):
    db = frames_db(x)
    return db if len(db) else np.zeros(0)

def generated_audio_match(src, takes, *, max_seconds=180):
    """Is this sound (part of) a GENERATED take's sound? Compares 30 ms loudness envelopes (survives re-encoding,
    volume changes and trimming) against every take's soundtrack. Returns {'take','at','score'} or None.
    Measured: the same take re-encoded scores 0.98+; unrelated speech stays far below 0.9."""
    try: x = decode(src, length=max_seconds)
    except VoiceError: return None
    u = _envelope(x)
    if len(u) < 60: return None
    best = None
    for t in takes:
        try: y = decode(t)
        except VoiceError: continue
        e = _envelope(y)
        if len(e) < 60 or float(e.std()) < 3.0: continue            # silent / flat takes match anything: skip
        a, b = (u, e) if len(u) <= len(e) else (e, u)                 # slide the shorter over the longer
        n = len(a)
        if n < 60: continue
        az = (a - a.mean()) / (a.std() + 1e-9)
        for k in range(0, len(b) - n + 1):
            w = b[k:k + n]
            if float(w.std()) < 3.0: continue
            c = float(np.dot((w - w.mean()) / (w.std() + 1e-9), az) / n)
            if best is None or c > best['score']: best = {'take': Path(t).name, 'at': round(k * HOP, 2), 'score': round(c, 4)}
    LAST_SCORE['best'] = best
    return best if best and best['score'] >= AUDIO_MATCH else None

LAST_SCORE = {}
