"""🎭 A pose ControlNet that fits YOUR full BF16 H3 (V0.9.26).

Why this exists: ComfyUI's ready-made MiniMax H3 Fun ControlNet files (Comfy-Org/MiniMax-H3, all "pruned") store each
control block's AdaLN layer over a small "curve basis" (8 wide). That matches the PRUNED base models, which carry the
same basis. The full `minimax_h3_ref2va_bf16` base has the full time embedding (2688 wide), and ComfyUI refuses the pair:
  "controlnet adaln width 8 does not match the base model's timestep embedding width 2688 (curve basis vs full)".
The curve form can't be expanded back. Alibaba's original ControlNet-Union-2.0 (what Comfy-Org's file was made from) has
the full AdaLN layers. So the Director builds one file:
  - everything from Comfy-Org's 2.0 file (already in ComfyUI's layout; ComfyUI loads it),
  - each control block's `adaln_proj.linear` (weight + bias) from Alibaba's original: the same name and the same row
    layout in both (VideoX-Fun's converter maps it 1:1, no reordering), shape [6*3*hidden, 2688],
  - without the "adaln_basis" marker, so ComfyUI builds the full form (2688, with SiLU), like the base.
Only those tensors are read from Alibaba's 13.6 GB file (HTTP range requests, ~5 GB), never the whole file.

Checks before anything is written (any failure: nothing is written, the reason is shown):
  - Comfy-Org's file really is the curve form, with N control blocks; Alibaba's has the same N;
  - every AdaLN tensor has exactly the shape the full form needs;
  - a tensor that is identical in both (control_blocks.0.after_proj.weight, a sample of it) matches: same version.
No torch needed (plain Python + numpy), so the installer can run it too.
"""
from __future__ import annotations
import json, os, re, struct, time, urllib.request
from pathlib import Path

COMFY_NAME = 'minimax_h3_fun_controlnet_union_2.0_pruned_bf16.safetensors'
COMFY_URL = 'https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/model_patches/' + COMFY_NAME
ALIBABA_URL = 'https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union-2.0/resolve/main/MiniMax-H3-Fun-Controlnet-Union-2.0.safetensors'
OUT_NAME = 'minimax_h3_fun_controlnet_union_2.0_full_adaln_bf16.safetensors'
FULL_T_DIM = 2688
SIZES = {'F32': 4, 'F16': 2, 'BF16': 2, 'F64': 8, 'I64': 8, 'I32': 4, 'I16': 2, 'I8': 1, 'U8': 1, 'BOOL': 1}

class BuildError(RuntimeError):
    pass

# ---- reading safetensors from a file or a URL (only the bytes asked for) ------------------------------------
class Source:
    def __init__(self, where, *, opener=None, retries=4):
        self.where = str(where); self.url = self.where.startswith(('http://', 'https://'))
        self.opener = opener or urllib.request.urlopen; self.retries = retries
        raw = self.read(0, 8); n = struct.unpack('<Q', raw)[0]
        if not 2 <= n <= 100_000_000: raise BuildError(f'NOT_A_SAFETENSORS_FILE: {self.where}')
        self.header = json.loads(self.read(8, n)); self.base = 8 + n
        self.meta = self.header.pop('__metadata__', None) or {}
    def read(self, start, length):
        if not self.url:
            with open(self.where, 'rb') as f:
                f.seek(start); b = f.read(length)
        else:
            for k in range(self.retries):
                try:
                    rq = urllib.request.Request(self.where, headers={'Range': f'bytes={start}-{start + length - 1}', 'User-Agent': 'DirectorStudio'})
                    with self.opener(rq, timeout=120) as r:
                        b = r.read()
                    break
                except Exception as e:
                    if k == self.retries - 1: raise BuildError(f'DOWNLOAD_FAILED: {type(e).__name__}: {str(e)[:200]}')
                    time.sleep(3 * (k + 1))
        if len(b) != length: raise BuildError(f'SHORT_READ: {self.where} gave {len(b)} of {length} bytes (range requests not honoured?)')
        return b
    def tensor_bytes(self, key):
        a, b = self.header[key]['data_offsets']; return self.read(self.base + a, b - a)
    def chunks(self, key, size=64 << 20):
        a, b = self.header[key]['data_offsets']
        for s in range(a, b, size): yield self.read(self.base + s, min(size, b - s))
    def find(self, suffix):
        """The key that is `suffix` or ends with '.'+suffix (Alibaba's file may carry a prefix such as 'transformer.')."""
        hits = [k for k in self.header if k == suffix or k.endswith('.' + suffix)]
        if len(hits) != 1: raise BuildError(f'ALIBABA_FILE_LAYOUT: {len(hits)} tensors named like {suffix} (expected 1)')
        return hits[0]

def to_bf16(raw, dtype):
    import numpy as np
    if dtype == 'BF16': return raw
    if dtype == 'F16': x = np.frombuffer(raw, dtype='<f2').astype('<f4')
    elif dtype == 'F32': x = np.frombuffer(raw, dtype='<f4')
    else: raise BuildError(f'UNSUPPORTED_DTYPE_{dtype}')
    u = x.view('<u4').astype(np.uint64)
    u = (u + 0x7FFF + ((u >> 16) & 1)) >> 16                       # round to nearest even
    nan = np.isnan(x); u[nan] = 0x7FC0
    return u.astype('<u2').tobytes()

def as_f32(raw, dtype):
    import numpy as np
    if dtype == 'BF16': return (np.frombuffer(raw, dtype='<u2').astype(np.uint32) << 16).view('<f4')
    if dtype == 'F16': return np.frombuffer(raw, dtype='<f2').astype('<f4')
    if dtype == 'F32': return np.frombuffer(raw, dtype='<f4')
    raise BuildError(f'UNSUPPORTED_DTYPE_{dtype}')

# ---- the plan (all checks, nothing written) ------------------------------------------------------------------
def plan(comfy: Source, ali: Source):
    blocks = 0
    while f'control_blocks.{blocks}.after_proj.weight' in comfy.header: blocks += 1
    if not blocks: raise BuildError('COMFY_FILE_IS_NOT_AN_H3_FUN_CONTROLNET')
    w0 = comfy.header['control_blocks.0.adaln_proj.linear.weight']['shape']
    curve = comfy.meta.get('minimax_h3_fun_controlnet') == 'adaln_basis'
    if not curve and w0[1] == FULL_T_DIM: return {'already_full': True, 'blocks': blocks}
    if not curve: raise BuildError(f'COMFY_FILE_UNEXPECTED_ADALN_WIDTH_{w0[1]}')
    ali_blocks = 0
    while any(k.endswith(f'control_blocks.{ali_blocks}.after_proj.weight') for k in ali.header): ali_blocks += 1
    if ali_blocks != blocks: raise BuildError(f'VERSION_MISMATCH: Comfy-Org file has {blocks} control blocks, Alibaba file {ali_blocks}')
    swap = {}
    for i in range(blocks):
        for part in ('weight', 'bias'):
            key = f'control_blocks.{i}.adaln_proj.linear.{part}'
            src = ali.find(key); sh = ali.header[src]['shape']
            mine = comfy.header.get(key, {}).get('shape')
            want = [w0[0], FULL_T_DIM] if part == 'weight' else [w0[0]]
            if sh != want: raise BuildError(f'ALIBABA_ADALN_SHAPE: {src} is {sh}, the full form needs {want}')
            if mine is not None and mine[0] != want[0]: raise BuildError(f'ADALN_ROWS_DIFFER: {key} {mine} vs {sh}')
            swap[key] = src
    # same version? a tensor both files hold unchanged
    k = 'control_blocks.0.after_proj.weight'; ka = ali.find(k)
    if comfy.header[k]['shape'] != ali.header[ka]['shape']: raise BuildError('VERSION_MISMATCH: after_proj shapes differ')
    n = min(65536, int(__import__('math').prod(comfy.header[k]['shape'])))
    def sample(src, key):
        d = src.header[key]['dtype']; a = src.header[key]['data_offsets'][0]
        return as_f32(src.read(src.base + a, n * SIZES[d]), d)
    import numpy as np
    x, y = sample(comfy, k), sample(ali, ka)
    rel = float(np.abs(x - y).mean() / max(1e-12, float(np.abs(y).mean())))
    if not rel < 0.02: raise BuildError(f'VERSION_MISMATCH: control_blocks.0.after_proj differs between the files (relative {rel:.3f})')
    dtype = comfy.header['control_blocks.0.after_proj.weight']['dtype']
    if dtype != 'BF16': raise BuildError(f'COMFY_FILE_NOT_BF16_{dtype}')
    entries = []; dropped = []
    for key in sorted(comfy.header, key=lambda k: comfy.header[k]['data_offsets'][0]):
        if key in swap: continue
        if 'adaln' in key and key.startswith('control_blocks.'): dropped.append(key); continue   # any other curve-form piece
        entries.append((key, 'comfy', key, comfy.header[key]['dtype'], comfy.header[key]['shape']))
    for key, src in swap.items():
        entries.append((key, 'ali', src, 'BF16', ali.header[src]['shape']))
    meta = {k: v for k, v in comfy.meta.items() if k != 'minimax_h3_fun_controlnet'}
    meta['director_build'] = json.dumps({'from': [COMFY_NAME, ALIBABA_URL.rsplit('/', 1)[1]], 'adaln': 'full (Alibaba original)',
                                         'blocks': blocks, 'sample_check_rel': round(rel, 5)})
    return {'already_full': False, 'blocks': blocks, 'entries': entries, 'meta': meta, 'sample_rel': rel, 'dropped': dropped,
            'ali_bytes': sum(ali.header[s]['data_offsets'][1] - ali.header[s]['data_offsets'][0] for s in swap.values())}

def build(comfy_path, out_path, *, ali=ALIBABA_URL, opener=None, log=print):
    """Writes out_path (via a .partial file). Returns a short report dict."""
    c = Source(comfy_path); a = Source(ali, opener=opener)
    p = plan(c, a)
    if p['already_full']: raise BuildError('ALREADY_FULL_FORM: this ControlNet already fits the full base; use it as it is')
    log(f"pose ControlNet: {p['blocks']} control blocks; reading {p['ali_bytes'] / 1e9:.1f} GB of full AdaLN layers from Alibaba's original")
    header, off = {}, 0
    for key, _, _, dt, sh in p['entries']:
        size = int(__import__('math').prod(sh)) * SIZES[dt]
        header[key] = {'dtype': dt, 'shape': sh, 'data_offsets': [off, off + size]}; off += size
    header['__metadata__'] = p['meta']
    hj = json.dumps(header, separators=(',', ':')).encode(); hj += b' ' * (-len(hj) % 8)
    out = Path(out_path); tmp = out.with_name(out.name + '.partial'); out.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, 'wb') as f:
        f.write(struct.pack('<Q', len(hj))); f.write(hj)
        for n, (key, which, src, dt, sh) in enumerate(p['entries']):
            if which == 'comfy':
                for b in c.chunks(src): f.write(b)
            else:
                f.write(to_bf16(a.tensor_bytes(src), a.header[src]['dtype']))
                log(f"  full AdaLN {key}")
    if tmp.stat().st_size != 8 + len(hj) + off: raise BuildError('WRITE_SIZE_MISMATCH')
    chk = Source(tmp)                                                       # read back what was written
    if chk.header['control_blocks.0.adaln_proj.linear.weight']['shape'][1] != FULL_T_DIM or 'minimax_h3_fun_controlnet' in chk.meta:
        raise BuildError('WRITTEN_FILE_CHECK_FAILED')
    os.replace(tmp, out)
    return {'file': str(out), 'blocks': p['blocks'], 'bytes': out.stat().st_size, 'sample_check_rel': round(p['sample_rel'], 5)}

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Build the full-AdaLN pose ControlNet for the full BF16 MiniMax H3')
    ap.add_argument('comfy_file'); ap.add_argument('out_file'); ap.add_argument('--alibaba', default=ALIBABA_URL)
    ap.add_argument('--check-only', action='store_true', help='read the headers, run every check, write nothing')
    x = ap.parse_args()
    try:
        if x.check_only:
            p = plan(Source(x.comfy_file), Source(x.alibaba))
            print(json.dumps({'ok': True, 'already_full': p['already_full'], 'blocks': p['blocks'], 'sample_check_rel': p.get('sample_rel'),
                              'would_read_gb': round(p.get('ali_bytes', 0) / 1e9, 2)}))
        else: print(json.dumps(build(x.comfy_file, x.out_file, ali=x.alibaba)))
    except BuildError as e:
        print(json.dumps({'ok': False, 'error': str(e)})); raise SystemExit(2)
