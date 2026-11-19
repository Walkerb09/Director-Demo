"""V0.9.68 🩺 Is this machine as fast as it says? A rented GPU can be held back (a power cap set by the host, bad power
delivery, a failing card, poor cooling) and its disk can be slow, and Vast doesn't check. This finds out in seconds:

  GPU: a short load (bf16 matrix multiplies, ~8 s) while the clock, power and temperature are read once a second.
       Held back = the SM clock under load stays far below its maximum (healthy cards run near it; the bad machine of
       9 Nov 2026 sat at 600-700 MHz of 3090 at "600 W" and 40 °C). Power at its limit while the card stays cool is
       reported too: the power reading or its delivery is off.
       While a take is generating, nothing extra is run: the clock is read during the take (the GPU is loaded anyway).
  Disk: 2 GB read from the H3 model file, bypassing the cache when it can.

Nothing here changes anything; it only measures and explains."""
from __future__ import annotations
import re, statistics, subprocess, threading, time
from pathlib import Path

VERSION = 'machine-1'
HELD_BACK = 0.50          # SM clock under load below half its max = held back
COOL_C = 55               # "at its power limit" yet cooler than this = the power reading / delivery is off
SLOW_DISK_GBS = 0.8

FIELDS = ['clocks.sm', 'clocks.max.sm', 'power.draw', 'power.limit', 'temperature.gpu', 'utilization.gpu', 'name']


def _num(x):
    m = re.search(r'[-+]?\d+(\.\d+)?', str(x))
    return float(m.group()) if m else None


def snapshot():
    """One reading of the first GPU -> dict, or None (no nvidia-smi). Tolerates drivers that lack a field."""
    fields = list(FIELDS)
    for _ in range(len(FIELDS)):
        try:
            r = subprocess.run(['nvidia-smi', f'--query-gpu={",".join(fields)}', '--format=csv,noheader,nounits', '-i', '0'],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired): return None
        if r.returncode == 0 and r.stdout.strip():
            vals = [v.strip() for v in r.stdout.strip().splitlines()[0].split(',')]
            out = dict(zip(fields, vals))
            return {k: (out[k] if k == 'name' else _num(out[k])) for k in out}
        named = re.search(r'"([a-z_.]+)" is not a valid field', r.stdout + r.stderr)      # an older driver names the field it lacks
        drop = named.group(1) if named and named.group(1) in fields else \
            next((f for f in ('power.limit', 'clocks.max.sm', 'utilization.gpu', 'temperature.gpu', 'power.draw') if f in fields), None)
        if not drop: return None
        fields.remove(drop)
    return None


def _load(seconds, stop):
    try:
        import torch
        if not torch.cuda.is_available(): return 'no CUDA'
        a = torch.randn(8192, 8192, device='cuda', dtype=torch.bfloat16); b = torch.randn_like(a)
        t_end = time.time() + seconds; n = 0; torch.cuda.synchronize(); t0 = time.time()
        while time.time() < t_end and not stop.is_set():
            for _ in range(8): a @ b
            torch.cuda.synchronize(); n += 8
        dt = time.time() - t0
        del a, b; torch.cuda.empty_cache()
        return {'tflops': round(n * 2 * 8192 ** 3 / max(dt, 1e-6) / 1e12, 1)}
    except Exception as e:
        return f'{type(e).__name__}: {str(e)[:120]}'


def gpu_check(busy=False, seconds=8):
    """busy: a take is generating now (read the clock during it, run nothing). -> dict with 'warnings'."""
    s0 = snapshot()
    if s0 is None: return {'gpu': None, 'warnings': [], 'note': 'nvidia-smi not found: GPU not checked'}
    stop = threading.Event(); res = {}
    if not busy:
        th = threading.Thread(target=lambda: res.update(load=_load(seconds, stop)), daemon=True); th.start()
        time.sleep(2.0)                                       # let the clock settle under load
    samples = []
    for _ in range(max(4, seconds - 2)):
        s = snapshot()
        if s: samples.append(s)
        time.sleep(1.0)
    if not busy: stop.set(); th.join(timeout=30)
    loaded = [s for s in samples if (s.get('utilization.gpu') or 0) >= 80] or samples
    med = lambda k: statistics.median([s[k] for s in loaded if s.get(k) is not None]) if any(s.get(k) is not None for s in loaded) else None
    clk, mx, pw, lim, temp, util = (med('clocks.sm'), s0.get('clocks.max.sm'), med('power.draw'), s0.get('power.limit'),
                                    med('temperature.gpu'), med('utilization.gpu'))
    out = {'gpu': s0.get('name'), 'clock_mhz': clk, 'max_mhz': mx, 'power_w': pw, 'power_limit_w': lim, 'temp_c': temp,
           'util': util, 'mode': 'during your take' if busy else 'short load test', 'warnings': []}
    load = res.get('load')
    if isinstance(load, dict): out['tflops'] = load['tflops']
    elif load: out['note'] = f'load test not run ({load})'
    if busy and (util or 0) < 80:
        out['note'] = 'the GPU was not fully busy during the reading; check again while a take samples'
        return out
    if clk and mx and clk < HELD_BACK * mx:
        why = ''
        if pw and lim and pw >= 0.95 * lim and temp is not None and temp < COOL_C:
            why = (f' It reports {pw:.0f} W (its limit) yet stays at {temp:.0f} °C: a card truly drawing that would be far warmer, '
                   'so its power reading or power delivery is off.')
        elif temp is not None and temp >= 85: why = f' It is at {temp:.0f} °C: the host cools it badly.'
        out['warnings'].append(f'The GPU is held back: {clk:.0f} MHz under load of a possible {mx:.0f} MHz '
                               f'(about {100 * clk / mx:.0f}%), so takes run roughly {mx / max(clk, 1):.1f}× slower than they should.{why} '
                               'This is the machine, not the Director: save (☁ Back up now), note the machine ID, report it to Vast, '
                               'and rent another.')
    return out


def disk_check(path, gb=2):
    """Read `gb` GB of `path` (the H3 model) -> {'gbs': speed, 'direct': bool, 'warnings': [...]}"""
    p = Path(path)
    if not p.is_file(): return {'gbs': None, 'warnings': [], 'note': 'model file not found: disk not checked'}
    count = int(gb * 1024 / 16)
    for flags in (['iflag=direct'], []):
        try:
            r = subprocess.run(['dd', f'if={p}', 'of=/dev/null', 'bs=16M', f'count={count}', *flags], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired): continue
        m = re.search(r'([\d.]+)\s*([GMk]?B)/s', r.stderr)
        if r.returncode == 0 and m:
            v = float(m.group(1)) * {'GB': 1, 'MB': 1e-3, 'kB': 1e-6, 'B': 1e-9}[m.group(2)]
            out = {'gbs': round(v, 2), 'direct': bool(flags), 'warnings': []}
            if v < SLOW_DISK_GBS:
                out['warnings'].append(f'The disk is slow: {v:.2f} GB/s reading the H3 model. Loading H3 (~117 GB with its text encoder) '
                                       f'takes about {117 / max(v, 0.01) / 60:.0f} min whenever it has to come from disk '
                                       '(keep "Fresh models for every take" off on this machine, or rent another).')
            return out
    return {'gbs': None, 'warnings': [], 'note': 'disk speed could not be read'}


class Checker:
    """One per ComfyUI process: run() measures (in a thread), state() is what the page shows."""
    def __init__(self, model_path_fn, queue_busy_fn):
        self.model_path_fn = model_path_fn; self.queue_busy_fn = queue_busy_fn
        self.result = None; self.running = False; self.lock = threading.Lock()

    def state(self):
        return {'running': self.running, 'result': self.result}

    def run(self, why='startup'):
        with self.lock:
            if self.running: return False
            self.running = True
        def go():
            try:
                try: busy = bool(self.queue_busy_fn())
                except Exception: busy = True                        # unsure: never load the GPU next to a take
                g = gpu_check(busy=busy)
                try: d = disk_check(self.model_path_fn()) if not busy else {'gbs': None, 'warnings': [], 'note': 'disk not checked during a take'}
                except Exception as e: d = {'gbs': None, 'warnings': [], 'note': f'disk: {type(e).__name__}'}
                self.result = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'why': why, 'gpu': g, 'disk': d,
                               'warnings': g.get('warnings', []) + d.get('warnings', []), 'version': VERSION}
                if self.result['warnings']:
                    print('[DirectorStudio] 🩺 ' + ' | '.join(self.result['warnings']), flush=True)
            except Exception as e:
                self.result = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'why': why, 'warnings': [], 'error': f'{type(e).__name__}: {str(e)[:200]}'}
            finally: self.running = False
        threading.Thread(target=go, name='DirectorStudioMachineCheck', daemon=True).start()
        return True
