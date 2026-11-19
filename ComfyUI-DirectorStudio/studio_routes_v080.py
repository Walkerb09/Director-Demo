"""Director Studio V0.8 web API, mounted on ComfyUI's own aiohttp server under /director.

Same port and host you already open ComfyUI on (Vast proxy link included): no extra
server, no SSH, no port 18189. All heavy work runs in a worker thread so ComfyUI stays
responsive.
"""
from __future__ import annotations
import asyncio, json, mimetypes, os, re, shutil, tempfile, threading, zipfile
from pathlib import Path
from aiohttp import web

HERE = Path(__file__).resolve().parent
SLUG = re.compile(r'^[a-z0-9_]{1,40}$')
FILE = re.compile(r'^[A-Za-z0-9_.\-]{1,160}$')
MAX_UPLOAD = 2 * 1024 ** 3
_LOCK = threading.Lock()          # one mutation at a time per process
LOCK_WAIT_S = float(os.environ.get('DIRECTOR_LOCK_WAIT_S', '45'))
class BusyError(Exception): pass

EMOTION_WORDS = {   # chip -> words the interpreter recognises (see director_intent_v0739.EMOTIONS)
 'laugh': 'laugh', 'chuckle': 'chuckle', 'smile': 'smile', 'smirk': 'smirk', 'surprise': 'look surprised',
 'amazed': 'be in awe', 'moved': 'get emotional', 'heartbroken': 'look heartbroken', 'sad': 'look sad',
 'angry': 'get angry', 'annoyed': 'look annoyed', 'scared': 'look scared', 'nervous': 'look nervous',
 'confused': 'look confused', 'curious': 'look curious', 'relieved': 'look relieved', 'hopeful': 'look hopeful',
 'determined': 'look determined', 'proud': 'look proud', 'grateful': 'look grateful', 'loving': 'look at them lovingly',
 'flirty': 'flirt', 'embarrassed': 'look embarrassed', 'guilty': 'look guilty', 'jealous': 'look jealous',
 'disgusted': 'look disgusted', 'bored': 'look bored', 'tired': 'look tired', 'calm': 'stay calm',
 'excited': 'get excited', 'delighted': 'look delighted', 'thoughtful': 'look thoughtful', 'nostalgic': 'look nostalgic',
 'skeptical': 'look skeptical', 'serious': 'turn serious', 'warm': 'be warm and tender', 'awkward': 'feel awkward',
 'funny': 'be playful'}
INTENSITY_WORDS = {'subtle': 'a little', 'normal': '', 'strong': 'a lot'}
FRAMING_WORDS = {'wide': 'wide shot', 'medium': 'waist-up', 'close': 'close-up on {who}', 'ots': 'over the shoulder on {who}',
                 'angle': 'another angle', 'low': 'low angle', 'profile': 'profile'}
CAMERA_WORDS = {'still': 'static camera', 'push': 'slow push in', 'handheld': 'handheld', 'pullback': 'pull back slowly'}
PACING_WORDS = {'slow': 'give it some time', 'brisk': 'pick up the pace'}
LENGTH_WORDS = {'2': 'about 2 seconds', '3': 'about 3 seconds', '4': 'about 4 seconds', '6': 'about 6 seconds', '8': 'about 8 seconds',
                '10': 'about 10 seconds', 'full': 'use the whole take'}

def _sv():
    import studio_v080 as sv; return sv

import subprocess as _sp, sys as _sys, signal as _signal
import studio_report_v094 as _rep
HELPER = Path(__file__).resolve().parent / 'studio_ingest_worker_v093.py'
STEP_WORDS = {'text': 'caption/text removal', 'cutout': 'the person cut-out (clean references)', 'identity': 'face matching / photo rotation'}

def crash_step(stderr):
    """From faulthandler's report: (step that crashed, 'file:line function') for the innermost Director frame."""
    frames = re.findall(r'File "([^"]+)", line (\d+) in (\w+)', stderr or '')
    mine = [(Path(f).name, int(l), fn) for f, l, fn in frames if Path(f).name.startswith('studio_') and not Path(f).name.startswith('studio_ingest_worker')]
    if not mine: return None, (f'{Path(frames[0][0]).name}:{frames[0][1]} {frames[0][2]}' if frames else None)
    f, l, fn = mine[0]; where = f'{f}:{l} {fn}'
    if f.startswith('studio_text'): return 'text', where
    if f.startswith('studio_identity') or fn in ('upright_rotation', '_recognise_side', 'take_identity', 'person_embedding'): return 'identity', where
    if fn in ('person_mask', '_seg', 'isolate', 'clean_person'): return 'cutout', where
    for _, _, fn2 in mine:                                  # the step further out in the call chain
        if fn2 in ('_clean_text', 'clean_frames'): return 'text', where
        if fn2 in ('person_mask',): return 'cutout', where
        if fn2 in ('_recognise_side', 'upright_rotation'): return 'identity', where
    return None, where

def ingest_in_helper(s, owner, path, attest, kind, who, rep, *, comfy_root, input_dir=None, output_dir=None, env=None, skip=(), voice=False):
    """Run add_media in a helper process so a crash there can never take ComfyUI down. If a library crashes in an
    OPTIONAL step (caption removal, cut-out, face matching), the file is processed again without that step."""
    skip = list(skip); notes = []
    for attempt in range(4):
        cmd = [_sys.executable, str(HELPER), '--root', str(s.root), '--comfy-root', str(comfy_root), '--owner', owner, '--path', str(path)]
        if input_dir: cmd += ['--input-dir', str(input_dir)]
        if output_dir: cmd += ['--output-dir', str(output_dir)]
        if attest: cmd.append('--attest')
        if kind: cmd += ['--kind', kind]
        if who: cmd += ['--who', who]
        if rep: cmd.append('--replace')
        if skip: cmd += ['--skip', ','.join(skip)]
        if voice: cmd.append('--voice')
        try: r = _sp.run(cmd, capture_output=True, text=True, timeout=1200, env=env)
        except _sp.TimeoutExpired: raise _sv().StudioError('PROCESSING_TOOK_TOO_LONG: over 20 minutes for one file; try a shorter clip')
        last = [l for l in r.stdout.splitlines() if l.startswith(('RESULT ', 'ERROR '))]
        if last and last[-1].startswith('RESULT '):
            out = json.loads(last[-1][7:]); out['warnings'] = notes + list(out.get('warnings') or []); return out
        if last: raise _sv().StudioError(json.loads(last[-1][6:])['error'])
        info = next((l[5:] for l in (r.stderr or '').splitlines() if l.startswith('INFO ')), '')
        tail = ' | '.join((r.stderr or '').strip().splitlines()[-3:])[-400:]
        if r.returncode < 0:
            sig = -r.returncode
            try: name = _signal.Signals(sig).name
            except ValueError: name = str(sig)
            step, where = crash_step(r.stderr)
            print(f'[DirectorStudio] upload helper died: {name} in {where} ({info}); skipped so far: {skip}\n{(r.stderr or "")[-3000:]}', flush=True)
            _rep.note('helper_crash', f'{name} in {where} ({info}); skipped {skip}\n{(r.stderr or "")[-3000:]}')
            if sig in (11, 6, 7, 4) and step and step not in skip:
                skip.append(step)
                notes.append(f"{STEP_WORDS[step]} was skipped for this file: it crashed on this ComfyUI ({name} in {where}; {info}). "
                             "Please send this line to Claude.")
                continue
            why = {9: 'it ran out of memory (the system stopped it)', 11: 'a video/image library crashed on this file (segmentation fault)',
                   6: 'a video/image library aborted on this file'}.get(sig, f'it was stopped by {name}')
            raise _sv().StudioError(f'PROCESSING_CRASHED: {why}. ComfyUI is fine and nothing was added. '
                                    f'Crashed in: {where or "unknown"} ({info}). Please send this message to Claude. ({name})')
        print(f'[DirectorStudio] upload helper failed ({r.returncode}): {tail}', flush=True)
        _rep.note('helper_failed', f'{r.returncode}: {(r.stderr or "")[-3000:]}')
        raise _sv().StudioError(f'PROCESSING_FAILED: {tail or "no details"}')
    raise _sv().StudioError('PROCESSING_CRASHED: every optional step was skipped and it still crashed. Please send this message to Claude.')


def check_project(s, error=None):
    """Open a project the way the page does (ledger, summary, story). {'ok': True} or the error and where it is."""
    import traceback as _tb
    try:
        if error is not None: raise error
        s.events(); s.summary(); s.story(s.state())
        return {'ok': True}
    except Exception as e:
        tb = _tb.extract_tb(e.__traceback__); mine = [f for f in tb if Path(f.filename).name.startswith('studio_')]
        at = mine[-1] if mine else (tb[-1] if tb else None)
        _rep.note('self_check', ''.join(_tb.format_exception(e))[-3000:])
        return {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:200]}', 'where': f'{Path(at.filename).name}:{at.lineno} {at.name}' if at else None}

MAX_LINES = 3                       # what one shot carries (the prompt keeps the last 3 lines)
HOW_KINDS = ['angry', 'sad', 'soft', 'nervous', 'excited', 'cheerful', 'sarcastic', 'firm', 'cold', 'playful', 'warm', 'scared',
             'desperate', 'hesitant', 'proud', 'tired', 'shy', 'whisper', 'shout', 'laugh', 'cry']
_ADV = {'angry': 'angrily', 'sad': 'sadly', 'soft': 'softly', 'nervous': 'nervously', 'excited': 'excitedly', 'cheerful': 'cheerfully',
        'sarcastic': 'sarcastically', 'firm': 'firmly', 'cold': 'coldly', 'playful': 'playfully', 'warm': 'warmly', 'scared': 'fearfully',
        'desperate': 'desperately', 'hesitant': 'hesitantly', 'proud': 'proudly', 'tired': 'wearily', 'shy': 'shyly'}
_PHRASE = {'whisper': ('says, almost whispering,', 'whispers', 'whispers, very quietly,'),
           'shout': ('says, raising their voice,', 'shouts', 'shouts, very loudly,'),
           'laugh': ('says, with a little laugh,', 'says, laughing,', 'says, laughing very hard,'),
           'cry': ('says, close to tears,', 'says, through tears,', 'says, sobbing very hard,')}

def said_how(how, strength='normal'):
    """How a line is said -> the words before it: 'very angrily says', 'whispers', 'says, a little nervously,'."""
    k = {'subtle': 0, 'normal': 1, 'strong': 2}.get(strength or 'normal', 1)
    if not how: return 'says'
    if how in _PHRASE: return _PHRASE[how][k]
    if how in _ADV: return (f'says, a little {_ADV[how]},', f'{_ADV[how]} says', f'very {_ADV[how]} says')[k]
    raise ValueError('UNKNOWN_DELIVERY: ' + str(how)[:30])

def spoken_lines(f, who=None):
    """The dialogue boxes, in speaking order -> ['Maya says "so what was she like?"', 'Grandpa says "trouble."'].
    f['lines'] = [{'who': name, 'line': words}, ...] (one box per person, V0.9.20), else the single f['line'] for the focus."""
    out = []
    lines = f.get('lines')
    if isinstance(lines, list) and lines:
        if len(lines) > MAX_LINES: raise ValueError('TOO_MANY_LINES: at most %d lines in one shot' % MAX_LINES)
        picked = f.get('who'); picked = picked if isinstance(picked, list) else ([picked] if picked else [])
        for x in lines:
            if not isinstance(x, dict): raise ValueError('BAD_LINES')
            w, words = str(x.get('who') or '').strip(), str(x.get('line') or '').strip()
            if not words: continue
            if picked and w not in picked: raise ValueError('LINE_FOR_SOMEONE_NOT_IN_THE_SHOT: ' + w[:40])
            words = words.replace('"', "'").replace('\u201c', "'").replace('\u201d', "'")[:200]
            out.append(f'{w or "they"} {said_how(x.get("how"), x.get("strength"))} "{words}"')
        return out
    if f.get('line'):
        words = str(f['line']).strip().replace('"', "'")[:200]
        if words: out.append(f'{who or "they"} {said_how(f.get("line_how"), f.get("line_strength"))} "{words}"')
    return out

def compose_direction(f, names):
    """UI chips -> plain words the interpreter already understands."""
    text = (f.get('text') or '').strip()
    picked = f.get('who')
    picked = [w for w in (picked if isinstance(picked, list) else [picked]) if w]
    picked = list(dict.fromkeys(picked))
    for w in picked:
        if w not in names: raise ValueError('UNKNOWN_PERSON')
    who = picked[0] if picked else None          # the first one picked is the focus: emotion, close-up and the line are theirs
    bits = [text] if text else []
    emo = f.get('emotion')
    if emo:
        if emo not in EMOTION_WORDS: raise ValueError('UNKNOWN_EMOTION')
        inten = INTENSITY_WORDS.get(f.get('intensity') or 'normal', '')
        bits.append(f"make {who or 'them'} {EMOTION_WORDS[emo]} {inten}".strip())
    elif who and len(picked) == 1 and not any(w.lower() in text.lower() for w in [who]):
        bits.append(f"focus on {who}")
    if f.get('framing'):
        fr = FRAMING_WORDS.get(f['framing'])
        if not fr: raise ValueError('UNKNOWN_FRAMING')
        bits.append(fr.format(who=who or 'them'))
    if f.get('camera'):
        if f['camera'] not in CAMERA_WORDS: raise ValueError('UNKNOWN_CAMERA')
        bits.append(CAMERA_WORDS[f['camera']])
    if f.get('pacing'):
        if f['pacing'] not in PACING_WORDS: raise ValueError('UNKNOWN_PACING')
        bits.append(PACING_WORDS[f['pacing']])
    if f.get('length'):
        if str(f['length']) not in LENGTH_WORDS: raise ValueError('UNKNOWN_LENGTH')
        bits.append(LENGTH_WORDS[str(f['length'])])
    said = spoken_lines(f, who)
    if said: bits.append(', then '.join(said))
    out = '. '.join(b for b in bits if b)
    if not out: raise ValueError('EMPTY_DIRECTION')
    return out

def pose_of(b):
    """The 🎭 movement choice from the page: {'source', 'strength', 'start'}, 'none' (removed) or None (keep a retake's)."""
    x = b.get('pose')
    if x in (None, ''): return None
    if x == 'none': return 'none'
    if not isinstance(x, dict) or not x.get('source'): raise web.HTTPBadRequest(text='POSE_PICK_A_MOVEMENT_VIDEO')
    try: return {'source': str(x['source'])[:80], 'strength': float(x.get('strength') or 0.6), 'start': float(x.get('start') or 0),
                 **({'who': str(x['who'])[:60]} if x.get('who') else {})}
    except (TypeError, ValueError): raise web.HTTPBadRequest(text='POSE_STRENGTH_AND_START_ARE_NUMBERS')

def picked_people(b):
    w = b.get('who'); w = w if isinstance(w, list) else ([w] if w else [])
    return [x for x in w if x] or None

def picked_emotions(b):
    """{'Grandpa': 'sad', ...} from the UI chips -> {'Grandpa': (interpreter emotion key, intensity)}."""
    import director_intent_v0739 as di
    em = b.get('emotions') or {}
    if not isinstance(em, dict): raise ValueError('EMOTIONS_MUST_BE_NAME_TO_EMOTION')
    out = {}; inten = b.get('intensity') or 'normal'
    for who, chip in em.items():
        if not chip: continue
        if chip not in EMOTION_WORDS: raise ValueError('UNKNOWN_EMOTION')
        key = di.interpret(EMOTION_WORDS[chip], {'phase': 'PLAN'}).get('emotion')
        if key: out[who] = (key, inten)
    return out or None

def register(routes, *, projects_root, comfy_root, input_dir=None, output_dir=None, port_fn=lambda: 8188):
    import studio_profile_v0970 as _pf
    _pf.init(comfy_root, projects_root)            # V0.9.70 full BF16 or the 24 GB demo set
    print(f'[DirectorStudio] model profile: {_pf.name()} ({_pf.get()["label"]})', flush=True)
    import studio_v080 as sv
    projects_root = Path(projects_root)

    def studio(slug):
        if not SLUG.match(slug or ''): raise web.HTTPBadRequest(text='bad project id')
        p = projects_root / slug
        if not (p / 'studio.sqlite3').is_file(): raise web.HTTPNotFound(text='NO_SUCH_PROJECT: that project is not on this machine; make or open a project first')
        st_ = sv.Studio(p, comfy_root, input_dir=input_dir, output_dir=output_dir); st_.comfy_url = f'http://127.0.0.1:{port_fn()}'
        st_.isolate = os.environ.get('DIRECTOR_MEASURE_IN_PROCESS') != '1'      # measure takes in the crash-safe helper
        return st_

    async def run(fn, *a, **k):
        def locked():
            for attempt in range(4):           # the background worker may append between our read and write: just redo it
                # never wait past ~45 s: the Vast/Cloudflare connection gives up at 100 s (HTTP 524). While a long job (making
                # references, a render) holds the project, answer "busy" at once and let the page try again.
                if not _LOCK.acquire(timeout=LOCK_WAIT_S):
                    kinds = sorted({j['kind'] for j in JOBS.values() if j['state'] == 'RUNNING'})
                    raise BusyError('BUSY_WORKING: the Director is still busy with ' + (', '.join(kinds) or 'measuring a take') +
                                    ' (this machine is slow right now). Wait a moment and try again; the page keeps refreshing by itself.')
                try: return fn(*a, **k)
                except sv.StudioError as e:
                    if str(e) != 'STATE_CHANGED_RETRY' or attempt == 3: raise
                finally: _LOCK.release()
        try:
            return await asyncio.to_thread(locked)
        except BusyError as e:
            raise web.HTTPServiceUnavailable(text=json.dumps({'error': str(e)}), content_type='application/json', headers={'Retry-After': '5'})
        except (sv.StudioError, ValueError, FileNotFoundError) as e:
            raise web.HTTPUnprocessableEntity(text=json.dumps({'error': str(e)}), content_type='application/json')
        except web.HTTPException: raise
        except Exception as e:                         # a bug: say exactly where, in the page and in the ComfyUI log
            import traceback as _tb
            tb = _tb.extract_tb(e.__traceback__); mine = [f for f in tb if 'DirectorStudio' in f.filename or Path(f.filename).name.startswith('studio_')]
            at = mine[-1] if mine else (tb[-1] if tb else None)
            where = f' at {Path(at.filename).name}:{at.lineno} {at.name}' if at else ''
            print('[DirectorStudio] internal error' + where + '\n' + ''.join(_tb.format_exception(e)), flush=True)
            _rep.note('internal_error', f'{type(e).__name__}: {e}{where}\n' + ''.join(_tb.format_exception(e))[-3000:])
            raise web.HTTPInternalServerError(text=json.dumps({'error': f'INTERNAL_ERROR: {type(e).__name__}: {str(e)[:200]}{where}. '
                                                                   'Your project is safe. Please send this message to Claude.'}), content_type='application/json')

    # -- background work: measuring takes, renders, backups, exports never block a click -----------------
    import threading as _th, time as _time, uuid as _uuid, re as _re_fresh
    NOTES = {}; PENDING = set(); TRIED = {}; JOBS = {}; CHAINS = set(); CHAIN_T = {}; UPDATE = {'restart_needed': False}
    def chain_tick():
        """In-order storyboard: send the next shot once the previous one is back and measured (every ~10 s)."""
        for slug in list(CHAINS):
            if _time.time() - CHAIN_T.get(slug, 0) < 10: continue
            CHAIN_T[slug] = _time.time()
            try:
                with _LOCK: r = studio(slug).chain_step(f'http://127.0.0.1:{port_fn()}')
                if r == 'waiting_for_check' and not CHECKING['busy'] and not REWRITING['busy'] \
                        and _time.time() - CHECKING['last_try'].get(slug, 0) > 120:
                    try:
                        if start_check(slug, 'night'): NOTES.setdefault(slug, []).append('🔍 Qwen checks the last shot (does it do what it says and make sense?) before the next one goes.')
                    except Exception as e: NOTES.setdefault(slug, []).append(f'🔍 Overnight check could not start: {str(e)[:120]}')
                if r == 'waiting_for_continuity' and not CHECKING['busy'] and not REWRITING['busy'] \
                        and _time.time() - CONTINUING['last_try'].get(slug, 0) > 120:
                    try:
                        if start_continuity(slug): NOTES.setdefault(slug, []).append('🔗 Qwen looks at how the last shot really ends and rewrites the next one to carry on from it.')
                    except Exception as e: NOTES.setdefault(slug, []).append(f'🔗 Continuity pass could not start: {str(e)[:120]}')
                if r == 'waiting_for_redraw_look' and not CHECKING['busy'] and not REWRITING['busy'] \
                        and _time.time() - CHECKING['last_try'].get('look:' + slug, 0) > 120:
                    try:
                        if start_redraw_look(slug): NOTES.setdefault(slug, []).append('👁 Qwen looks at the redrawn first frame (same people, same clothes, nothing odd) before the shot goes.')
                    except Exception as e: NOTES.setdefault(slug, []).append(f'👁 Qwen could not look at the redraw: {str(e)[:120]}')
                if r == 'sent': NOTES.setdefault(slug, []).append('Storyboard in order: the next shot was sent, continuing from how the previous one ends.')
                if r in (None, 'done'):
                    CHAINS.discard(slug)
                    if r == 'done': NOTES.setdefault(slug, []).append('Storyboard in order: every planned shot has been sent.')
            except Exception as e:
                NOTES.setdefault(slug, []).append(f'Storyboard in order paused: {str(e)[:160]} (press "Generate in order" to carry on)')
                try:
                    with _LOCK: studio(slug).stop_chain()
                except Exception: pass
                CHAINS.discard(slug)
    FRESH = {'flagged': set(), 'at': 0.0}
    TAKE_PREFIX = _re_fresh.compile(r'^Studio_(.+)_S\d+_T\d+')
    def fresh_tick():
        """Fresh models for every take: while ComfyUI runs one of our takes, ask it (POST /free) to drop its models and
        cached results once that take is done, so the next take loads H3 fresh, like the first one after a restart.
        (On some setups a reused H3 model returns invalid / NaN output: black takes.) Applied by ComfyUI between prompts,
        never during one. Off per project with the "Fresh models for every take" setting."""
        if _time.time() - FRESH['at'] < 3: return
        FRESH['at'] = _time.time()
        from urllib.request import urlopen as _uo, Request as _Rq
        base = f'http://127.0.0.1:{port_fn()}'
        with _uo(base + '/queue', timeout=5) as r: q = json.loads(r.read() or b'{}')
        try: stage_tick(q)                      # the recorder never gets in the way of freeing models
        except Exception: pass
        for item in q.get('queue_running') or []:
            if len(item) < 3 or item[1] in FRESH['flagged']: continue
            slug = None
            for node in (item[2] or {}).values():
                m = TAKE_PREFIX.match(str(((node or {}).get('inputs') or {}).get('filename_prefix', '')))
                if m: slug = m.group(1); break
            if not slug or not SLUG.match(slug) or not (projects_root / slug / 'studio.sqlite3').is_file(): continue
            try:
                with _LOCK: on = studio(slug).state()['settings'].get('fresh_models', True)
            except Exception: on = True
            FRESH['flagged'].add(item[1])
            if not on: continue
            body = json.dumps({'unload_models': True, 'free_memory': True}).encode()
            with _uo(_Rq(base + '/free', data=body, headers={'Content-Type': 'application/json'}, method='POST'), timeout=5): pass
        if len(FRESH['flagged']) > 500: FRESH['flagged'] = set(list(FRESH['flagged'])[-100:])
    STAGE = {'at': 0.0}
    def stage_tick(q):
        """Stage recorder (V0.9.18): every 15 s while one of our takes runs, note which node ComfyUI is on, what each thread
        is doing and its CPU use, GPU use and RAM. It goes into 'Report a problem'; nothing is changed."""
        if _time.time() - STAGE['at'] < 15: return
        for item in q.get('queue_running') or []:
            if len(item) < 3: continue
            pr = item[2] or {}; label = None
            for node in pr.values():
                m = TAKE_PREFIX.match(str(((node or {}).get('inputs') or {}).get('filename_prefix', '')))
                if m: label = m.group(0); break
            if not label: continue
            STAGE['at'] = _time.time()
            node_class = None
            try:
                import server as _srv
                nid = getattr(_srv.PromptServer.instance, 'last_node_id', None)
                node_class = (pr.get(str(nid)) or {}).get('class_type') or (str(nid) if nid is not None else None)
            except Exception: pass
            _rep.sample_stage(item[1], node_class, label)
            return
    def analyzer():
        while True:
            try: chain_tick()
            except Exception: pass
            try: fresh_tick()
            except Exception: pass
            try: check_tick()
            except Exception: pass
            busy = False
            for slug in list(PENDING):
                try:
                    st = studio(slug)
                    now = _time.time()
                    skip = {k[1:] for k, t in TRIED.items() if k[0] == slug and now - t < 30}
                    notes, done = st.analyze_pending(skip=skip)
                    for d in done: TRIED[(slug,) + tuple(d)] = now; busy = True
                    if notes: NOTES.setdefault(slug, []).extend(notes)
                    if not done: PENDING.discard(slug)
                except Exception as e:
                    NOTES.setdefault(slug, []).append(f'Background measuring paused: {type(e).__name__}: {str(e)[:120]}'); PENDING.discard(slug)
                    import traceback as _tb2; _rep.note('measuring', f'{slug}: ' + _tb2.format_exc()[-3000:])
            try:                                   # look checks for takes measured before V0.9.8 (read once from the file)
                if sv.Studio._LOOK_WANT and sv.Studio.warm_looks(4.0) >= 0: busy = busy or bool(sv.Studio._LOOK_WANT)
            except Exception: pass
            _time.sleep(0.3 if busy else 1.5)
    _th.Thread(target=analyzer, name='DirectorStudioAnalyzer', daemon=True).start()

    SELFCHECK = {'state': 'waiting', 'projects': {}, 'at': None}
    def self_check(delay=20):
        """Shortly after ComfyUI starts: open every project once (ledger integrity, page summary, story, timeline), so a
        problem shows in the top bar with where it is, before you run into it."""
        _time.sleep(delay); SELFCHECK['state'] = 'running'
        import traceback as _tb
        for p in sorted(projects_root.iterdir()) if projects_root.is_dir() else []:
            if not ((p / 'studio.sqlite3').is_file() and SLUG.match(p.name)): continue
            with _LOCK:
                try:
                    st_ = studio(p.name); res = check_project(st_)
                    if st_.state().get('chain'): CHAINS.add(p.name)       # an in-order / overnight run carries on after a restart
                except Exception as e: res = check_project(None, error=e)
            if not res['ok']: print(f'[DirectorStudio] self-check: project {p.name} has a problem: {res}', flush=True)
            SELFCHECK['projects'][p.name] = res
        SELFCHECK['state'] = 'done'; SELFCHECK['at'] = _time.strftime('%H:%M:%S')
    _th.Thread(target=self_check, name='DirectorStudioSelfCheck', daemon=True,
               kwargs={'delay': float(os.environ.get('DIRECTOR_SELFCHECK_DELAY', '20'))}).start()

    @routes.get('/director/api/selfcheck')
    async def selfcheck(request):
        return ok(SELFCHECK)

    JOB_DIR = projects_root / '_jobs'
    def save_job(j):
        try:
            JOB_DIR.mkdir(parents=True, exist_ok=True); t = JOB_DIR / (j['id'] + '.partial')
            t.write_text(json.dumps(j, default=str)); os.replace(t, JOB_DIR / (j['id'] + '.json'))
        except Exception: pass
    def start_job(slug, kind, fn):
        jid = _uuid.uuid4().hex[:12]; JOBS[jid] = {'id': jid, 'slug': slug, 'kind': kind, 'state': 'RUNNING', 'started': _time.time(), 'pid': os.getpid()}
        save_job(JOBS[jid])
        def go():
            try: JOBS[jid].update(state='DONE', result=fn())
            except Exception as e:
                JOBS[jid].update(state='FAILED', error=str(e)[:600])
                if not isinstance(e, sv.StudioError):
                    import traceback as _tb3; _rep.note('job_' + kind, _tb3.format_exc()[-3000:])
                else: _rep.note('job_' + kind, str(e)[:600])
            save_job(JOBS[jid])
        _th.Thread(target=go, name=f'DirectorStudioJob-{kind}', daemon=True).start()
        for k in [k for k, j in JOBS.items() if j['state'] != 'RUNNING' and _time.time() - j['started'] > 3600]: JOBS.pop(k, None)
        return jid

    @routes.get('/director/api/p/{slug}/progress')
    async def progress(request):
        """Which of this project's generations ComfyUI is running / has queued (from ComfyUI's own /queue).
        Step-by-step progress comes to the page directly over ComfyUI's websocket (same client id)."""
        s = studio(request.match_info['slug'])
        st = await asyncio.to_thread(s.state)
        ours = {x.get('prompt_id'): (x['shot'], x['take']) for x in st['submissions'] if x.get('prompt_id')}
        arrived = {(n, t['take']) for n, sh in st['shots'].items() for t in sh['takes']}
        def fetch():
            from urllib.request import urlopen
            with urlopen(f'http://127.0.0.1:{port_fn()}/queue', timeout=5) as r: return json.load(r)
        try: q = await asyncio.to_thread(fetch)
        except Exception as e:
            return ok({'comfy': 'UNREACHABLE', 'error': str(e)[:160], 'running': [], 'pending': [], 'client_id': sv.CLIENT_ID})
        def items(lst):
            out = []
            for pos, it in enumerate(sorted(lst, key=lambda x: x[0] if isinstance(x, list) and x else 0)):
                pid = it[1] if isinstance(it, list) and len(it) > 1 else None
                sh = ours.get(pid)
                x = {'prompt_id': pid, 'position': pos + 1, 'ours': sh is not None, 'shot': sh[0] if sh else None, 'take': sh[1] if sh else None}
                if sh and len(it) > 2 and isinstance(it[2], dict):          # node id -> its type, so the page names the real stage
                    x['nodes'] = {k: (v or {}).get('class_type') for k, v in it[2].items()}
                out.append(x)
            return out
        waiting = [{'shot': a, 'take': b, 'prompt_id': pid} for pid, (a, b) in ours.items() if (a, b) not in arrived]
        busy = {i[1] for i in q.get('queue_running', []) + q.get('queue_pending', []) if isinstance(i, list) and len(i) > 1}
        def history(pid):
            from urllib.request import urlopen
            try:
                with urlopen(f'http://127.0.0.1:{port_fn()}/history/{pid}', timeout=5) as r: h = json.load(r).get(pid) or {}
            except Exception: return None
            stt = h.get('status') or {}
            if stt.get('status_str') == 'error':
                for kind, msg in stt.get('messages', []):
                    if kind == 'execution_error': return ((msg.get('node_type') or '') + ': ' + (msg.get('exception_message') or 'error'))[:300]
                return 'ComfyUI reported an error'
            if stt.get('status_str') == 'success' or h.get('outputs'): return None
            return None if h else 'ComfyUI has no record of this job (it may have been cleared by a restart); generate it again'
        for w in waiting:
            if w['prompt_id'] not in busy: w['error'] = await asyncio.to_thread(history, w['prompt_id'])
        return ok({'comfy': 'OK', 'running': items(q.get('queue_running', [])), 'pending': items(q.get('queue_pending', [])),
                   'waiting_for_files': waiting, 'client_id': sv.CLIENT_ID})

    @routes.get('/director/api/jobs/{jid}')
    async def job(request):
        jid = request.match_info['jid']; j = JOBS.get(jid)
        if not j and re.fullmatch(r'[0-9a-f]{12}', jid or '') and (JOB_DIR / f'{jid}.json').is_file():
            try: j = json.loads((JOB_DIR / f'{jid}.json').read_text())
            except ValueError: j = None
            if j and j.get('state') == 'RUNNING':    # started before ComfyUI restarted: it can't still be running
                j = {**j, 'state': 'FAILED', 'error': 'INTERRUPTED_BY_RESTART: ComfyUI restarted while this was being processed, so nothing was added. '
                     'Please try again; if it happens again, the ComfyUI log will say why.'}
        if not j: raise web.HTTPNotFound(text='INTERRUPTED_BY_RESTART: this job is unknown (ComfyUI restarted). Please try again.')
        return ok(j)

    def ok(data): return web.json_response(data, dumps=lambda o: json.dumps(o, default=str))



    @routes.get('/director')
    async def page(request):
        return web.FileResponse(HERE / 'web' / 'index.html', headers={'Cache-Control': 'no-store'})

    HALLWAY_SRC = Path(os.environ.get('DIRECTOR_HALLWAY_PROJECT', '/workspace/DirectorProjects/hallway_interview_v1'))

    @routes.get('/director/api/import_hallway')
    async def import_hallway_info(request):
        dest = projects_root / 'hallway_interview'
        return ok({'source': str(HALLWAY_SRC), 'available': (HALLWAY_SRC / 'director_project.sqlite3').is_file(),
                   'already_imported': (dest / 'studio.sqlite3').is_file(), 'id': 'hallway_interview'})

    @routes.post('/director/api/import_hallway')
    async def import_hallway(request):
        import studio_import_v081 as imp
        dest = projects_root / 'hallway_interview'
        if (dest / 'studio.sqlite3').is_file(): return ok({'id': 'hallway_interview', 'notes': ['Already imported.']})
        def go():
            s, notes = imp.import_hallway(HALLWAY_SRC, dest, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir)
            return notes
        try:
            notes = await run(go)
        except web.HTTPException: raise
        except Exception as e:
            raise web.HTTPUnprocessableEntity(text=json.dumps({'error': f'{type(e).__name__}: {str(e)[:300]}'}), content_type='application/json')
        return ok({'id': 'hallway_interview', 'notes': notes})

    @routes.get('/director/api/p/{slug}/backup')
    async def backup(request):
        import studio_backup_v090 as bk
        s = studio(request.match_info['slug']); takes = request.query.get('takes') == '1'
        d = s.root / 'backups'; d.mkdir(exist_ok=True)
        for old in d.glob('*.zip'): old.unlink()                      # keep only the newest backup on the instance
        name = f"{s.slug}_backup{'_with_takes' if takes else ''}.zip"
        await run(bk.make_backup, s, d / name, with_takes=takes)
        return web.FileResponse(d / name, headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})

    @routes.get('/director/api/p/{slug}/export')
    async def export_edit(request):
        import studio_export_v090 as ex
        s = studio(request.match_info['slug'])
        d = s.root / 'renders'; name = f'{s.slug}_edit.zip'
        await run(ex.export_package, s, d / name)
        return web.FileResponse(d / name, headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})

    @routes.post('/director/api/restore')
    async def restore(request):
        import studio_backup_v090 as bk
        projects_root.mkdir(parents=True, exist_ok=True)
        reader = await request.multipart(); tmp = None
        try:
            async for part in reader:
                if part.name != 'file': continue
                fd, tmp = tempfile.mkstemp(suffix='.zip', dir=projects_root); size = 0
                with os.fdopen(fd, 'wb') as f:
                    while True:
                        chunk = await part.read_chunk(1 << 20)
                        if not chunk: break
                        size += len(chunk)
                        if size > 16 * 1024 ** 3: raise web.HTTPRequestEntityTooLarge(max_size=16 * 1024 ** 3, actual_size=size)
                        f.write(chunk)
            if not tmp: raise web.HTTPBadRequest(text='no file')
            def go():
                try: return bk.restore_backup(tmp, projects_root, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir)
                except (bk.BackupError, zipfile.BadZipFile) as e: raise sv.StudioError(str(e))
            st, notes = await run(go)
            import studio_decisions_v0942 as dc                    # V0.9.42: decisions you imported before this backup was here
            pend = dc.pending(projects_root, st.root.name, st.slug)
            if pend:
                try:
                    r = await run(dc.merge, studio(st.root.name), pend, apply=True)
                    dc.drop_pending(projects_root, pend)
                    notes.append('Your imported decisions were applied: ' + dc.sentence(r) + '.')
                except web.HTTPException as e: notes.append('Your imported decisions could not be applied yet: ' + (e.text or '')[:200])
            return ok({'id': st.root.name, 'notes': notes})
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)

    # -- ☁ V0.9.66 cloud backup to your private Hugging Face repo (studio_cloud_v0966) ------------------------------
    import studio_cloud_v0966 as _cl
    def _cloud_backup(slug, dest, with_takes):            # called with _LOCK held (Cloud takes it)
        import studio_backup_v090 as bk, studio_decisions_v0942 as dc
        s_ = studio(slug)
        try: dc.autosave(s_)                               # the decisions file is current before it goes up
        except Exception: pass
        return bk.make_backup(s_, dest, with_takes=with_takes)
    CLOUD = _cl.Cloud(projects_root, workspace=str(Path(os.environ.get('DIRECTOR_WORKSPACE') or projects_root.parent)),
                      make_backup=_cloud_backup, decisions_file=lambda slug: projects_root / '_decisions' / f'{slug}.json', lock=_LOCK)
    def _cloud_restore_one(z):
        import studio_backup_v090 as bk, studio_decisions_v0942 as dc
        with _LOCK: st_, _notes = bk.restore_backup(z, projects_root, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir)
        pend = dc.pending(projects_root, st_.root.name, st_.slug)
        if pend:
            with _LOCK: dc.merge(studio(st_.root.name), pend, apply=True)
            dc.drop_pending(projects_root, pend)
        print(f'[DirectorStudio] ☁ restored {st_.root.name} from your cloud backup', flush=True)
        return st_.root.name
    def cloud_timer():
        _time.sleep(float(os.environ.get('DIRECTOR_CLOUD_FIRST_DELAY', '90')))
        try:                                       # a fresh instance: your cloud projects come back by themselves
            CLOUD.adopt_env()
            if _cl.token(CLOUD.workspace): CLOUD.auto_restore(_cloud_restore_one)
        except Exception as e:
            CLOUD.status['last_error'] = f'auto-restore: {type(e).__name__}: {str(e)[:200]}'
        while True:
            try: CLOUD.tick()
            except Exception: pass
            _time.sleep(60)
    _th.Thread(target=cloud_timer, name='DirectorStudioCloud', daemon=True).start()
    def _cloud_start(**kw):
        def go():
            try: CLOUD.backup_round(**kw)
            except Exception: pass
        _th.Thread(target=go, name='DirectorStudioCloudNow', daemon=True).start()

    @routes.get('/director/api/cloud')
    async def cloud_info(request):
        c = CLOUD.config()
        out = {'config': c, 'status': {k: v for k, v in CLOUD.status.items() if k != 'last_round_t'},
               'token': bool(_cl.token(CLOUD.workspace)), 'projects': CLOUD.projects()}
        if not c['repo'] and out['token'] and request.query.get('suggest') == '1':
            out['suggested_repo'] = await asyncio.to_thread(CLOUD.suggested_repo)
        return ok(out)

    @routes.post('/director/api/cloud')
    async def cloud_set(request):
        b = await request.json()
        try: c = CLOUD.set_config(**{k: b[k] for k in _cl.DEFAULTS if k in b})
        except (_cl.CloudError, TypeError, ValueError) as e: raise web.HTTPBadRequest(text=json.dumps({'error': str(e)}), content_type='application/json')
        if b.get('enabled') and c['enabled']: _cloud_start(why='turned on')          # the first copy goes right away
        return ok({'config': c})

    @routes.post('/director/api/cloud/backup_now')
    async def cloud_now(request):
        try: b = await request.json()
        except Exception: b = {}
        if not _cl.token(CLOUD.workspace): raise web.HTTPBadRequest(text=json.dumps({'error': 'NO_HUGGING_FACE_TOKEN: paste a WRITE token when the installer asks, or run `hf auth login` in the terminal'}), content_type='application/json')
        _cloud_start(force=bool(b.get('force')), why='you pressed Back up now')
        return ok({'started': True})

    @routes.post('/director/api/cloud/leaving')
    async def cloud_leaving(request):
        """The /director tab is closing or hidden (sendBeacon): one round at once, only what changed."""
        if CLOUD.config()['enabled'] and _cl.token(CLOUD.workspace) and _time.time() - CLOUD.status.get('leaving_t', 0) > 600 \
                and _time.time() - (CLOUD.status.get('last_round_t') or 0) > 300:          # V0.9.68 at most every 10 min, never right after a round
            CLOUD.status['leaving_t'] = _time.time(); _cloud_start(why='you left the page')
        return ok({'ok': True})

    @routes.get('/director/api/cloud/list')
    async def cloud_list(request):
        try: return ok(await asyncio.to_thread(CLOUD.listing))
        except Exception as e: raise web.HTTPBadRequest(text=json.dumps({'error': f'{type(e).__name__}: {str(e)[:300]}'}), content_type='application/json')

    @routes.post('/director/api/cloud/restore')
    async def cloud_restore(request):
        import studio_backup_v090 as bk
        b = await request.json(); projects_root.mkdir(parents=True, exist_ok=True)
        td = tempfile.mkdtemp(dir=projects_root, prefix='.cloud_restore_')
        try:
            try: z = await asyncio.to_thread(CLOUD.download, str(b.get('project') or ''), str(b.get('file') or ''), td)
            except Exception as e: raise web.HTTPBadRequest(text=json.dumps({'error': f'{type(e).__name__}: {str(e)[:300]}'}), content_type='application/json')
            def go():
                try: return bk.restore_backup(z, projects_root, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir)
                except (bk.BackupError, zipfile.BadZipFile) as e: raise sv.StudioError(str(e))
            st, notes = await run(go)
            import studio_decisions_v0942 as dc
            pend = dc.pending(projects_root, st.root.name, st.slug)
            if pend:
                try:
                    r = await run(dc.merge, studio(st.root.name), pend, apply=True); dc.drop_pending(projects_root, pend)
                    notes.append('Your imported decisions were applied: ' + dc.sentence(r) + '.')
                except web.HTTPException as e: notes.append('Your imported decisions could not be applied yet: ' + (e.text or '')[:200])
            return ok({'id': st.root.name, 'notes': notes})
        finally: shutil.rmtree(td, ignore_errors=True)

    # -- 🩺 V0.9.68 is this machine as fast as it says? (studio_machine_v0968) --------------------------------------
    import studio_machine_v0968 as _mc
    def _queue_busy():
        from urllib.request import build_opener, ProxyHandler
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port_fn()}/queue', timeout=10) as r: q = json.load(r)
        return bool(q.get('queue_running') or q.get('queue_pending'))
    MACHINE = _mc.Checker(lambda: _pf.paths(comfy_root)[0], _queue_busy)
    def machine_first():
        _time.sleep(float(os.environ.get('DIRECTOR_MACHINE_FIRST_DELAY', '60')))
        MACHINE.run('ComfyUI started')
    _th.Thread(target=machine_first, name='DirectorStudioMachineFirst', daemon=True).start()

    @routes.get('/director/api/profile')
    async def profile_info(request):                     # V0.9.70 which model set this machine runs
        return ok({'name': _pf.name(), **{k: v for k, v in _pf.get().items() if k != 'qwen_need_mib'}})

    @routes.get('/director/api/machine')
    async def machine_info(request):
        return ok(MACHINE.state())

    @routes.post('/director/api/machine/check')
    async def machine_check(request):
        return ok({'started': MACHINE.run('you asked')})

    # -- V0.9.42 ⬇ Export / ⬆ Import your editing decisions (no photos, videos or voices) --------------------
    @routes.get('/director/api/decisions/export')
    async def decisions_export(request):
        import studio_decisions_v0942 as dc, studio_update_v095 as up_
        def go():
            ss = []
            for p in sorted(projects_root.iterdir()) if projects_root.is_dir() else []:
                if (p / 'studio.sqlite3').is_file() and SLUG.match(p.name):
                    try: ss.append(studio(p.name))
                    except Exception: pass
            return dc.export_all(ss, up_.version_of(HERE))
        data = await run(go)
        name = 'director_decisions_' + _time.strftime('%Y%m%d_%H%M') + '.json'
        return web.Response(text=json.dumps(data, ensure_ascii=False, indent=1, default=str), content_type='application/json',
                            headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})

    @routes.post('/director/api/decisions/import')
    async def decisions_import(request):
        """apply=0: only says what would happen. apply=1: adds it. settings=1: also use the file's settings."""
        import studio_decisions_v0942 as dc
        reader = await request.multipart(); raw = b''; fields = {}
        async for part in reader:
            if part.name == 'file':
                while True:
                    chunk = await part.read_chunk(1 << 20)
                    if not chunk: break
                    raw += chunk
                    if len(raw) > dc.MAX_BYTES: raise web.HTTPRequestEntityTooLarge(max_size=dc.MAX_BYTES, actual_size=len(raw))
            else: fields[part.name] = (await part.text())[:20]
        if not raw: raise web.HTTPBadRequest(text='no file')
        apply = fields.get('apply') == '1'; use_settings = fields.get('settings') == '1'
        def go():
            try: data = dc.read(raw)
            except dc.DecisionsError as e: raise sv.StudioError(str(e))
            out = {'projects': [], 'not_here': [], 'applied': apply}
            here = {}                                              # project folder -> the name it was made with
            for p in sorted(projects_root.iterdir()) if projects_root.is_dir() else []:
                if (p / 'studio.sqlite3').is_file() and SLUG.match(p.name):
                    try: here[p.name] = studio(p.name).slug
                    except Exception: pass
            for proj in data['projects']:
                folder = proj['slug'] if proj['slug'] in here else next((f for f, l in here.items() if proj.get('ledger_slug') and l == proj['ledger_slug']), None)
                if folder:
                    try: r = dc.merge(studio(folder), proj, apply=apply, settings=use_settings)
                    except dc.DecisionsError as e: raise sv.StudioError(f"{e} (project {proj['slug']})")
                    r['line'] = dc.sentence(r); out['projects'].append(r)
                else:
                    c = dc.counts(proj)
                    out['not_here'].append({'slug': proj['slug'], 'title': str(proj.get('title') or proj['slug'])[:60], **c})
                    if apply: dc.stash(projects_root, proj)
            return out
        return ok(await run(go))

    @routes.get('/director/api/projects')
    async def projects(request):
        projects_root.mkdir(parents=True, exist_ok=True)
        out = []
        for p in sorted(projects_root.iterdir()):
            if (p / 'studio.sqlite3').is_file() and SLUG.match(p.name):
                try: out.append({'id': p.name, 'title': sv.Studio(p, comfy_root, input_dir=input_dir, output_dir=output_dir).state()['project']['title']})
                except Exception as e: out.append({'id': p.name, 'title': p.name, 'error': str(e)[:80]})
        return ok(out)

    @routes.post('/director/api/projects')
    async def new_project(request):
        b = await request.json(); title = (b.get('title') or '').strip()[:60]
        if not title: raise web.HTTPBadRequest(text='title required')
        slug = sv.slug(title)
        await run(sv.Studio.create, projects_root / slug, title, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir)
        return ok({'id': slug})

    REQUIRED_MODELS = _pf.required_models()        # V0.9.70 this machine's model profile

    @routes.get('/director/api/health')
    async def health(request):
        """Fresh-instance check: nodes registered, model files visible to ComfyUI, folders writable, ffmpeg."""
        return ok(await asyncio.to_thread(health_check))

    def comfy_memory_note():
        """Full BF16 H3 is ~118 GB of weights. ComfyUI's dynamic VRAM (default since ~0.37) stages the text encoder and the
        video model in system RAM together: on an instance with less than ~192 GB of RAM, ComfyUI is killed mid-take."""
        try: import comfy.cli_args as ca
        except Exception: return None                                     # not inside ComfyUI (tests): nothing to check
        a = getattr(ca, 'args', None)
        if a is None or not hasattr(a, 'disable_dynamic_vram') or a.disable_dynamic_vram: return None
        try: ram_gb = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024 ** 3
        except Exception: ram_gb = 0
        if ram_gb >= 192: return None
        return {'item': f'ComfyUI memory: dynamic VRAM is on with {ram_gb:.0f} GB of RAM', 'ok': False,
                'fix': 'full BF16 H3 will crash ComfyUI mid-take. Add --disable-dynamic-vram --disable-pinned-memory to ComfyUI\'s '
                       'launch line (the Director installer does this on Vast), then restart ComfyUI'}

    def health_check():
        def check():
            from urllib.request import build_opener, ProxyHandler
            op = build_opener(ProxyHandler({})); items = []
            def info(node):
                try:
                    with op.open(f'http://127.0.0.1:{port_fn()}/object_info/{node}', timeout=10) as r: return json.load(r).get(node)
                except Exception: return None
            for node, label in (('MiniMaxH3ReferenceToVideo', 'MiniMax H3 node'), ('VHS_VideoCombine', 'VideoHelperSuite (Video Combine)'), ('LoadImage', 'Load Image')):
                items.append({'item': label, 'ok': info(node) is not None, 'fix': '' if info(node) else f'node {node} not registered: install it and restart ComfyUI'})
            for node, files in REQUIRED_MODELS.items():
                inf = info(node) or {}
                opts = json.dumps(inf.get('input', {}).get('required', {}))
                for f in files:
                    items.append({'item': f, 'ok': f in opts, 'fix': '' if f in opts else 'model file not visible to ComfyUI (check models/ folders)'})
            for label, d in (('input folder writable', Path(input_dir or Path(comfy_root) / 'input')), ('projects folder writable', projects_root)):
                try:
                    d.mkdir(parents=True, exist_ok=True); t = d / '.director_write_test'; t.write_text('x'); t.unlink(); ok_ = True
                except OSError: ok_ = False
                items.append({'item': label, 'ok': ok_, 'fix': '' if ok_ else f'cannot write to {d}'})
            for label, d in (('ComfyUI output (takes)', Path(output_dir or Path(comfy_root) / 'output')), ('projects', projects_root)):
                try:
                    free = shutil.disk_usage(d if d.exists() else d.parent).free / 1e9
                    items.append({'item': f'Free disk space for {label}: {free:.0f} GB', 'ok': free >= 3, 'optional': free >= 3,
                                  'fix': '' if free >= 15 else (f'only {free:.1f} GB left: delete old renders/backups or move takes off the instance'
                                                               if free >= 3 else f'only {free:.1f} GB left: generating and rendering will stop until you free space')})
                except OSError: pass
            items.append({'item': 'ffmpeg', 'ok': shutil.which('ffmpeg') is not None and shutil.which('ffprobe') is not None, 'fix': 'install ffmpeg'})
            for node, label in (('LoadVideo', 'Load Video (for reference clips)'), ('GetVideoComponents', 'Get Video Components (for reference clips)'),
                                ('LoraLoaderModelOnly', 'LoRA loader (optional)')):
                items.append({'item': label, 'ok': info(node) is not None, 'optional': True,
                              'fix': '' if info(node) else 'optional: without it the Director uses still references only' if 'Video' in node else 'optional: LoRAs unavailable'})
            import studio_qwen_v081 as q
            for label, okq in q.installed()['items'].items():
                items.append({'item': 'Qwen observer: ' + label, 'ok': okq, 'optional': True,
                              'fix': '' if okq else 'optional: run the full Vast installer (install.sh) once to enable Qwen observation'})
            items.append({'item': 'nvidia-smi (GPU memory hand-over for Qwen)', 'ok': shutil.which('nvidia-smi') is not None, 'optional': True,
                          'fix': 'optional: without it Qwen relies on its own memory check'})
            for m in ('face_detection_yunet_2023mar.onnx', 'face_recognition_sface_2021dec.onnx'):
                items.append({'item': f'Identity check model {m}', 'ok': (HERE / 'models' / m).is_file(), 'optional': True,
                              'fix': 'optional: without it takes are not checked for who is who'})
            try:                                                         # the Director's workflow against THIS ComfyUI's nodes
                import studio_graph_v094 as gr
                pf = gr.preflight(info)
                for pr in pf['problems']:
                    items.append({'item': f"Workflow check: {pr['node']}", 'ok': False, 'optional': bool(pr['optional']),
                                  'fix': pr['problem'] if pr.get('note') else pr['problem'] + (f" (only {pr['optional']} are affected)" if pr['optional'] else ' (update ComfyUI or the node)')})
                if not any(not pr['optional'] for pr in pf['problems']):
                    items.append({'item': f"Workflow check: the Director's workflow fits your ComfyUI ({pf['checked']} node types verified)", 'ok': True, 'fix': ''})
            except Exception as e:
                items.append({'item': 'Workflow check', 'ok': False, 'optional': True, 'fix': f'could not run ({type(e).__name__}: {str(e)[:120]})'})
            for m in ('version-RFB-640.onnx', 'u2netp.onnx'):
                items.append({'item': f'Director model {m}', 'ok': (HERE / 'models' / m).is_file(), 'fix': 'package incomplete: re-unzip'})
            mem = comfy_memory_note()
            if mem: items.append(mem)
            if UPDATE['restart_needed']:
                items.append({'item': f"Update to {UPDATE.get('pending')} installed", 'ok': False, 'optional': True, 'warn': True,
                              'fix': 'restart ComfyUI to finish the update (⬆ Update → Restart ComfyUI)'})
            return {'ok': all(i['ok'] for i in items if not i.get('optional')), 'items': items}
        return check()

    @routes.get('/director/api/threads')
    async def threads_now(request):
        """What every thread of ComfyUI is doing right now (code locations and CPU %), plus the stage recorder's summary."""
        def go():
            _rep.stacks(); _time.sleep(1.0)            # two readings, so CPU % covers the last second
            return {'threads': _rep.stacks(), 'stages': _rep.stage_summary()}
        r = await asyncio.to_thread(go)
        return web.Response(text=json.dumps(r, indent=1), content_type='application/json')

    @routes.post('/director/api/report')
    async def report(request):
        """'Report a problem': a zip with what's needed to find a bug (no photos, videos or voices)."""
        b = await request.json() if request.can_read_body else {}
        slug = b.get('project') if SLUG.match(str(b.get('project') or '')) else None
        def go():
            s = None
            if slug:
                try: s = studio(slug)
                except Exception as e: _rep.note('report', f'project {slug} would not open: {e}')
            try: h = health_check()
            except Exception as e: h = {'error': str(e)}
            d = projects_root / '_reports'; d.mkdir(parents=True, exist_ok=True)
            dest = d / f"director_report_{slug or 'no_project'}_{_time.strftime('%Y%m%d_%H%M%S')}.zip"
            _rep.build(dest, package_root=HERE, comfy_root=comfy_root, studio=s, health=h, client=b.get('client'),
                       jobs=[j for j in JOBS.values() if not slug or j['slug'] == slug])
            return dest
        dest = await asyncio.to_thread(go)
        return web.FileResponse(dest, headers={'Content-Disposition': f'attachment; filename="{dest.name}"', 'Cache-Control': 'no-store'})

    # ------------------------------------------------------------------ update the Director in place (studio_update_v095)
    import studio_update_v095 as _up
    WORKSPACE = Path(os.environ.get('DIRECTOR_WORKSPACE') or projects_root.parent)
    def comfy_busy():
        try:
            from urllib.request import urlopen
            with urlopen(f'http://127.0.0.1:{port_fn()}/queue', timeout=5) as r: q = json.load(r)
            return len(q.get('queue_running') or []) + len(q.get('queue_pending') or [])
        except Exception: return 0

    @routes.get('/director/api/update')
    async def update_info(request):
        return ok({'installed': _up.version_of(HERE), 'kept': _up.kept_versions(WORKSPACE), 'restart_needed': UPDATE['restart_needed'],
                   'pending': UPDATE.get('pending')})

    async def _receive_zip(request, field='file'):
        reader = await request.multipart(); tmp = None; fields = {}
        async for part in reader:
            if part.name != field: fields[part.name] = (await part.text())[:200]; continue
            d = WORKSPACE / '.director_update_uploads'; d.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix='.zip', dir=d); size = 0
            with os.fdopen(fd, 'wb') as f:
                while True:
                    chunk = await part.read_chunk(1 << 20)
                    if not chunk: break
                    size += len(chunk)
                    if size > 2 * 1024 ** 3: raise web.HTTPRequestEntityTooLarge(max_size=2 * 1024 ** 3, actual_size=size)
                    f.write(chunk)
        if not tmp: raise web.HTTPBadRequest(text='no file')
        return tmp, fields

    @routes.post('/director/api/update')
    async def update_apply(request):
        """Upload a Director zip: checked, every project backed up (story and settings, no videos), the running version
        kept in /workspace/DirectorBackups, the new one put in place. dry_run=1 only checks the zip."""
        tmp, fields = await _receive_zip(request)
        if fields.get('dry_run') == '1':
            try: return ok(await run(_up.check_zip, tmp, HERE))
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
        def go():
            try:
                info = _up.check_zip(tmp, HERE)
                if info['same']: raise _up.UpdateError(f'ALREADY_INSTALLED: this is exactly the version you have ({info["version"]})')
                import studio_backup_v090 as bk
                bdir = WORKSPACE / 'DirectorBackups' / ('projects_before_update_' + _time.strftime('%Y%m%d_%H%M%S')); saved = []
                for p in sorted(projects_root.iterdir()) if projects_root.is_dir() else []:
                    if (p / 'studio.sqlite3').is_file():
                        try:
                            bdir.mkdir(parents=True, exist_ok=True)
                            with _LOCK: bk.make_backup(studio(p.name), bdir / f'{p.name}.zip')
                            saved.append(p.name)
                        except Exception as e:
                            raise _up.UpdateError(f'COULD_NOT_BACK_UP_PROJECT_{p.name} ({str(e)[:120]}): nothing was changed')
                with _LOCK: r = _up.apply_zip(tmp, HERE, WORKSPACE)
                r['projects_backed_up'] = saved; r['projects_backup_folder'] = str(bdir) if saved else None
                _up.prune_project_backups(WORKSPACE)
                UPDATE.update(restart_needed=True, pending=r['version'])
                return r
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
        return ok({'job': start_job(None, 'update', go)})

    @routes.post('/director/api/update/rollback')
    async def update_rollback(request):
        b = await request.json()
        r = await run(_up.rollback, str(b.get('name') or ''), HERE, WORKSPACE)
        UPDATE.update(restart_needed=True, pending=r['version']); return ok(r)

    @routes.post('/director/api/update/restart')
    async def update_restart(request):
        b = await request.json() if request.can_read_body else {}
        n = await asyncio.to_thread(comfy_busy)
        if n and not b.get('force'):
            raise web.HTTPConflict(text=json.dumps({'error': f'COMFYUI_IS_BUSY: {n} generation(s) running or queued. Restart when they finish (or restart anyway: they will be lost).'}),
                                   content_type='application/json')
        _rep.note('update', 'ComfyUI restart requested from the Director')
        _up.restart_comfyui(); return ok({'restarting': True})

    @routes.post('/director/api/p/{slug}/qwen')
    async def qwen_run(request):
        s = studio(request.match_info['slug'])
        ready = s.qwen_ready()
        if not ready['ready']:
            missing = [k for k, v in ready['items'].items() if not v]
            raise web.HTTPUnprocessableEntity(text=json.dumps({'error': 'QWEN_OBSERVER_NOT_INSTALLED: ' + ', '.join(missing) +
                     '. Run the bundled fresh_instance bootstrap once (see README).'}), content_type='application/json')
        r = await run(s.run_qwen, f'http://127.0.0.1:{port_fn()}')
        return ok({'result': r})

    @routes.post('/director/api/p/{slug}/clear_lessons')
    async def clear_lessons(request):
        s = studio(request.match_info['slug']); await run(s.clear_lessons); return ok({'ok': True})

    @routes.get('/director/api/p/{slug}')
    async def summary(request):
        s = studio(request.match_info['slug'])
        notes = await run(s.sync, analyze=False) if request.query.get('sync', '1') == '1' else []
        PENDING.add(s.root.name)                       # the background worker measures new takes
        data = await run(s.summary)
        if data.get('chain'): CHAINS.add(s.root.name)  # survives a restart of ComfyUI / the server
        data['notes'] = notes + NOTES.pop(s.root.name, [])
        data['jobs'] = [j for j in JOBS.values() if j['slug'] == s.root.name and j['state'] == 'RUNNING']
        return ok(data)

    @routes.post('/director/api/p/{slug}/character')
    async def character(request):
        s = studio(request.match_info['slug']); b = await request.json()
        cid = await run(s.add_character, b.get('name', ''), pronoun=b.get('pronoun', 'they'),
                        descriptors=[x.strip() for x in (b.get('descriptors') if isinstance(b.get('descriptors'), list) else re.split(r'[,;]', str(b.get('descriptors') or ''))) if str(x).strip()],
                        description=b.get('description', ''), voice=b.get('voice', ''))
        return ok({'id': cid})

    @routes.post('/director/api/p/{slug}/character/{cid}')
    async def character_edit(request):
        s = studio(request.match_info['slug']); b = await request.json(); cid = request.match_info['cid']
        if 'retired' in b: await run(s.retire, cid, bool(b['retired']))
        if 'main_source' in b: await run(s.set_main_source, cid, b['main_source'] or None)
        if 'voice_ref' in b: await run(s.set_voice, cid, b['voice_ref'] or None)
        kw = {k: b[k] for k in ('name', 'pronoun', 'description', 'voice') if k in b}
        if 'descriptors' in b: kw['descriptors'] = [x for x in re.split(r'[,;]', b['descriptors'] or '') if x.strip()]
        if kw: await run(s.update_character, cid, **kw)
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/location/{lid}')
    async def location_edit(request):
        s = studio(request.match_info['slug']); b = await request.json(); lid = request.match_info['lid']
        if 'retired' in b: await run(s.retire, lid, bool(b['retired']))
        kw = {k: b[k] for k in ('name', 'description') if k in b}
        if kw: await run(s.update_location, lid, **kw)
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/ref/{rid}/clip_start')
    async def ref_clip_start(request):
        """🎞 V0.9.27: use other 3 seconds of your original video for this person's reference clip."""
        s = studio(request.match_info['slug']); b = await request.json()
        try: start = float(b.get('start'))
        except (TypeError, ValueError): raise web.HTTPBadRequest(text='start must be seconds')
        return ok({'id': await run(s.set_clip_start, request.match_info['rid'], start)})

    @routes.post('/director/api/p/{slug}/ref/{rid}/remove')
    async def ref_remove(request):
        s = studio(request.match_info['slug'])
        await run(s.remove_ref, request.match_info['rid'])
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/location')
    async def location(request):
        s = studio(request.match_info['slug']); b = await request.json()
        return ok({'id': await run(s.add_location, b.get('name', ''), description=b.get('description', ''))})

    @routes.post('/director/api/p/{slug}/voice')
    async def voice_upload(request):
        """A person's voice from your audio or video file (with your permission). Processed in the crash-safe helper."""
        import studio_voice_v093 as voice
        s = studio(request.match_info['slug']); reader = await request.multipart(); fields = {}; tmp = None
        try:
            async for part in reader:
                if part.name == 'file':
                    suffix = Path(part.filename or 'voice.bin').suffix.lower()[:6]
                    if suffix not in voice.AUDIO_EXT: raise web.HTTPBadRequest(text='UNSUPPORTED_AUDIO_FILE_TYPE: use wav, mp3, m4a, aac, ogg, opus, flac, aiff, wma or a video')
                    fd, tmp = tempfile.mkstemp(suffix=suffix, dir=s.root / 'uploads'); size = 0
                    with os.fdopen(fd, 'wb') as f:
                        while True:
                            chunk = await part.read_chunk(1 << 20)
                            if not chunk: break
                            size += len(chunk)
                            if size > MAX_UPLOAD: raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=size)
                            f.write(chunk)
                else: fields[part.name] = (await part.text())[:200]
            if not tmp: raise web.HTTPBadRequest(text='no file')
            path, tmp = tmp, None; owner = fields.get('owner', ''); ok_perm = fields.get('permission') == 'yes'
            def go():
                try:
                    with _LOCK: return ingest_in_helper(s, owner, path, ok_perm, None, None, False, comfy_root=comfy_root,
                                                        input_dir=input_dir, output_dir=output_dir, voice=True)
                finally:
                    if os.path.exists(path): os.unlink(path)
            return ok({'job': start_job(s.root.name, 'voice', go)})
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)

    @routes.post('/director/api/p/{slug}/upload')
    async def upload(request):
        s = studio(request.match_info['slug'])
        reader = await request.multipart()
        fields = {}; tmp = None
        try:
            async for part in reader:
                if part.name == 'file':
                    suffix = Path(part.filename or 'upload.bin').suffix.lower()[:6]
                    if suffix not in ('.png', '.jpg', '.jpeg', '.webp', '.heic', '.heif', '.avif', '.mp4', '.mov', '.m4v', '.webm', '.mkv'):
                        raise web.HTTPBadRequest(text='unsupported file type')
                    fd, tmp = tempfile.mkstemp(suffix=suffix, dir=s.root / 'uploads'); size = 0
                    with os.fdopen(fd, 'wb') as f:
                        while True:
                            chunk = await part.read_chunk(1 << 20)
                            if not chunk: break
                            size += len(chunk)
                            if size > MAX_UPLOAD: raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=size)
                            f.write(chunk)
                else:
                    fields[part.name] = (await part.text())[:200]
            if not tmp: raise web.HTTPBadRequest(text='no file')
            who = fields.get('who') or None
            if who not in (None, 'left', 'middle', 'right', 'only'): raise web.HTTPBadRequest(text='who must be left, middle, right or only')
            path, tmp = tmp, None                       # the background job owns the file now
            owner, attest, kind, rep = fields.get('owner', ''), fields.get('attest') == 'yes', fields.get('kind') or None, fields.get('replace') == 'yes'
            def go():                                   # analysing a video can take ~30 s: never inside the request
                try:
                    with _LOCK: return ingest_in_helper(s, owner, path, attest, kind, who, rep, comfy_root=comfy_root, input_dir=input_dir, output_dir=output_dir)
                finally:
                    if os.path.exists(path): os.unlink(path)
            return ok({'job': start_job(s.root.name, 'upload', go)})
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)

    @routes.post('/director/api/p/{slug}/plan')
    async def plan(request):
        s = studio(request.match_info['slug']); b = await request.json()
        names = [c['name'] for c in s.state()['characters'].values()]
        try: text = compose_direction(b, names); emo = picked_emotions(b)
        except ValueError as e: raise web.HTTPBadRequest(text=str(e))
        res = await run(s.plan, text, mode=b.get('mode', 'auto'), background=b.get('background') or None, people=picked_people(b), emotions=emo,
                        start_frame=b.get('start_frame') or None, end_frame=b.get('end_frame') or None, pose=pose_of(b),
                        positions=b.get('positions') if 'positions' in b else None, hide_faces=b.get('hide_faces') if 'hide_faces' in b else None,
                        **({'target_shot': int(b['target_shot'])} if str(b.get('target_shot') or '').isdigit() else {}),      # ✎ V0.9.50 a new take of shot N
                        compose_start=bool(b.get('compose_start')))                                                        # 🧩 V0.9.55
        res['direction_text'] = text
        return ok(res)

    @routes.post('/director/api/p/{slug}/extend')
    async def extend(request):
        """Continue one of YOUR original videos (never a generated take)."""
        s = studio(request.match_info['slug']); b = await request.json()
        names = [c['name'] for c in s.state()['characters'].values()]
        try: text = compose_direction(b, names); emo = picked_emotions(b)
        except ValueError as e: raise web.HTTPBadRequest(text=str(e))
        res = await run(s.extend, str(b.get('source', '')), text, background=b.get('background') or None, people=picked_people(b), emotions=emo)
        res['direction_text'] = text
        return ok(res)

    @routes.post('/director/api/p/{slug}/cover')
    async def cover(request):
        """One direction -> a scene of 2-4 shots (wide, speaker, reaction, two-shot). The framing/camera chips are ignored:
        coverage chooses the framing of each shot itself."""
        s = studio(request.match_info['slug']); b = await request.json()
        names = [c['name'] for c in s.state()['characters'].values()]
        who = picked_people(b) or []
        if any(w not in names for w in who): raise web.HTTPBadRequest(text='UNKNOWN_PERSON')
        text = (b.get('text') or '').strip()
        try: said = spoken_lines(b, who[0] if who else None)
        except ValueError as e: raise web.HTTPBadRequest(text=str(e))
        if said: text = (text + ', then ' if text else '') + ', then '.join(said)
        try:
            emo = picked_emotions(b) or {}
            if b.get('emotion') and who:
                one = picked_emotions({'emotions': {who[0]: b['emotion']}, 'intensity': b.get('intensity')}) or {}
                emo = {**one, **emo}
        except ValueError as e: raise web.HTTPBadRequest(text=str(e))
        return ok(await run(s.cover, text, people=who or None, emotions=emo or None, background=b.get('background') or None,
                            start_frame=b.get('start_frame') or None, end_frame=b.get('end_frame') or None, pose=pose_of(b)))

    CONTINUING = {'last_try': {}}
    def start_continuity(slug):
        """V0.9.37 🔗: the in-order run waits on a film shot: Qwen (the ✨ rewrite, with the end of the previous shot for
        positions only, what 🔍 saw done or missed there, and the shot's 📷 action photo) rewrites it; the run sends that."""
        s = studio(slug); CONTINUING['last_try'][slug] = _time.time()
        with _LOCK: job = s.continuity_job()
        if not job: return None
        def go():
            import studio_rewrite_v0913 as rw, importlib.util as _iu
            REWRITING['busy'] = True
            try:
                if not os.environ.get('DIRECTOR_REWRITE_FAKE'):
                    spec = _iu.spec_from_file_location('director_dispatcher_v0751', str(sv.qwen.DISPATCHER)); disp = _iu.module_from_spec(spec); spec.loader.exec_module(disp)
                    import studio_qwenmodel_v0916 as qm
                    rw.wait_for_gpu(f'http://127.0.0.1:{port_fn()}', dispatcher=disp, wait_minutes=20,
                                    need_mib=qm.need_mib(qm.model_id(s.state()['settings'].get('qwen_model'), 'DIRECTOR_REWRITE_MODEL')))
                try:
                    res = s.rewrite(job['text'], people=job['people'] or None, mode='shot', for_shot=job['shot'], framing=__import__('studio_film_v0936').FRAMING_TO_MOTION.get(job.get('framing')),
                                    **({'action_photo': job['action_photo']} if job.get('action_photo') else {}))
                except sv.StudioError as e:
                    with _LOCK: s.continuity_failed(job, e)
                    return {'continued': False, 'why': str(e)[:200]}
                with _LOCK: q = s.apply_continuity(job, res)
                return {'continued': bool(q), 'shot': job['shot'], 'take': (q or {}).get('take')}
            finally: REWRITING['busy'] = False
        return start_job(slug, 'continuity', go)

    CHECKING = {'busy': False, 'last_try': {}, 'idle_since': None}
    def start_redraw_look(slug):
        """👁 V0.9.60: the in-order run waits on a 🧪 redrawn first frame: Qwen compares it with the frame built from your photos."""
        import studio_check_v0915 as ck, importlib.util as _iu
        s = studio(slug); CHECKING['last_try']['look:' + slug] = _time.time()
        with _LOCK: job = s.redraw_look_job()
        if not job: return None
        CHECKING['busy'] = True
        def go():
            try:
                if not os.environ.get('DIRECTOR_CHECK_FAKE'):
                    spec = _iu.spec_from_file_location('director_dispatcher_v0751', str(sv.qwen.DISPATCHER)); disp = _iu.module_from_spec(spec); spec.loader.exec_module(disp)
                    import studio_rewrite_v0913 as rw, studio_qwenmodel_v0916 as qm
                    rw.wait_for_gpu(f'http://127.0.0.1:{port_fn()}', dispatcher=disp, wait_minutes=20, need_mib=qm.need_mib(job['model']))
                qp = (getattr(s, 'qwen_paths', None) or {}).get('qwen_python') or sv.qwen.DEFAULTS['qwen_python']
                try: replies = ck.run([{'key': job['key'], 'request': job['request']}], qwen_python=qp)
                except RuntimeError as e: raise sv.StudioError(str(e))
                with _LOCK: good = s.record_redraw_look(job, replies.get(job['key'], ''))
                return {'looked': True, 'ok': good}
            finally: CHECKING['busy'] = False
        return start_job(slug, 'redraw_look', go)
    def start_check(slug, why='auto'):
        """Qwen checks this project's unchecked takes against their directions (one Qwen load for the batch)."""
        import studio_check_v0915 as ck, importlib.util as _iu, tempfile as _tf
        s = studio(slug); CHECKING['last_try'][slug] = _time.time()          # an idle ComfyUI doesn't make it re-read every project every second
        with _LOCK: todo = s.pending_checks()
        if not todo: return None
        CHECKING['busy'] = True
        def go():
            try:
                with _tf.TemporaryDirectory() as d:
                    jobs = s.check_requests(todo, d)                                 # reading frames: never while holding the project lock
                    if not jobs: return {'checked': 0}
                    if not os.environ.get('DIRECTOR_CHECK_FAKE'):
                        spec = _iu.spec_from_file_location('director_dispatcher_v0751', str(sv.qwen.DISPATCHER)); disp = _iu.module_from_spec(spec); spec.loader.exec_module(disp)
                        import studio_rewrite_v0913 as rw
                        import studio_qwenmodel_v0916 as qm
                        rw.wait_for_gpu(f'http://127.0.0.1:{port_fn()}', dispatcher=disp, wait_minutes=20, need_mib=qm.need_mib(jobs[0].get('model')))
                    qp = (getattr(s, 'qwen_paths', None) or {}).get('qwen_python') or sv.qwen.DEFAULTS['qwen_python']
                    def comfy_busy():
                        from urllib.request import urlopen as _uo2
                        try:
                            with _uo2(f'http://127.0.0.1:{port_fn()}/queue', timeout=5) as r: q = json.loads(r.read() or b'{}')
                            return bool(q.get('queue_running') or q.get('queue_pending'))
                        except Exception: return False
                    try: replies = ck.run([{'key': j['key'], 'request': j['request']} for j in jobs], qwen_python=qp, busy=comfy_busy)
                    except RuntimeError as e: raise sv.StudioError(str(e))
                    with _LOCK: n = s.record_checks(jobs, replies)
                NOTES.setdefault(slug, []).append(f'🔍 Qwen checked {n} take(s) against what you asked (a hint on each take card, not an approval).')
                return {'checked': n}
            finally: CHECKING['busy'] = False
        return start_job(slug, 'check', go)
    def check_tick():
        """Check takes when ComfyUI has been idle for a while (never during a generation or an in-order / overnight run)."""
        if CHECKING['busy'] or REWRITING['busy'] or CHAINS or os.environ.get('DIRECTOR_NO_AUTO_CHECK') == '1': return
        if not sv.qwen.installed(None)['ready'] and not os.environ.get('DIRECTOR_CHECK_FAKE'): return
        from urllib.request import urlopen as _uo
        try:
            with _uo(f'http://127.0.0.1:{port_fn()}/queue', timeout=5) as r: q = json.loads(r.read() or b'{}')
        except Exception: return
        if q.get('queue_running') or q.get('queue_pending'): CHECKING['idle_since'] = None; return
        CHECKING['idle_since'] = CHECKING['idle_since'] or _time.time()
        if _time.time() - CHECKING['idle_since'] < float(os.environ.get('DIRECTOR_CHECK_IDLE_S', '90')): return
        for p in sorted(projects_root.iterdir(), key=lambda x: -x.stat().st_mtime) if projects_root.is_dir() else []:
            if not ((p / 'studio.sqlite3').is_file() and SLUG.match(p.name)): continue
            if _time.time() - CHECKING['last_try'].get(p.name, 0) < 600: continue
            try:
                if start_check(p.name): return
            except Exception: CHECKING['last_try'][p.name] = _time.time()

    @routes.post('/director/api/p/{slug}/check_takes')
    async def check_takes(request):
        """Check this project's takes now (it still waits for ComfyUI to be idle and frees the GPU first)."""
        if CHECKING['busy']: raise web.HTTPConflict(text='QWEN_IS_ALREADY_CHECKING')
        jid = await asyncio.to_thread(start_check, request.match_info['slug'], 'you')
        return ok({'job': jid, 'nothing_to_check': jid is None})

    DOWNLOADING = {'model': None}
    @routes.get('/director/api/qwen_models')
    async def qwen_models(request):
        import studio_qwenmodel_v0916 as qm
        return ok({'models': {k: {'id': v, 'gb': qm.SIZES_GB[k], 'downloaded': qm.downloaded(k), 'cache': str(qm.where(v) or '')} for k, v in qm.MODELS.items()},
                   'downloading': DOWNLOADING['model']})

    @routes.post('/director/api/qwen_models/download')
    async def qwen_model_download(request):
        """Download a Qwen model into the shared cache now (instead of on first use). A background job; resumes if interrupted."""
        import studio_qwenmodel_v0916 as qm
        b = await request.json(); choice = b.get('model')
        if choice not in qm.MODELS: raise web.HTTPBadRequest(text='QWEN_MODEL_8B_32B_OR_27B')
        if DOWNLOADING['model']: raise web.HTTPConflict(text='ALREADY_DOWNLOADING ' + DOWNLOADING['model'])
        h = qm.hf_home(); h.mkdir(parents=True, exist_ok=True); free = shutil.disk_usage(str(h)).free / 1e9
        if not qm.downloaded(choice) and free < qm.SIZES_GB[choice] + 10:
            raise web.HTTPConflict(text=f'NOT_ENOUGH_DISK: the {choice.upper()} model needs about {qm.SIZES_GB[choice]} GB and {free:.0f} GB is free')
        def go():
            DOWNLOADING['model'] = choice
            try: return {'path': qm.download(choice, sv.qwen.DEFAULTS['qwen_python']), 'model': choice}
            except RuntimeError as e: raise sv.StudioError(str(e))
            finally: DOWNLOADING['model'] = None
        return ok({'job': start_job(None, 'qwen_download', go)})

    REWRITING = {'busy': False}
    @routes.post('/director/api/p/{slug}/rewrite')
    async def rewrite(request):
        """Qwen rewrites your notes (one shot, or a scene of 2-4 shots). A background job: it waits until ComfyUI is idle, asks it
        to free the GPU, runs Qwen once, and returns a SUGGESTION for you to keep, edit or discard. Nothing is planned or sent."""
        s = studio(request.match_info['slug']); b = await request.json()
        mode = b.get('mode', 'shot')
        if mode not in ('shot', 'scene'): raise web.HTTPBadRequest(text='REWRITE_MODE_SHOT_OR_SCENE')
        text = (b.get('text') or '').strip()
        try: said = spoken_lines(b, (picked_people(b) or [None])[0])
        except ValueError as e: raise web.HTTPBadRequest(text=str(e))
        if said: text = (text + ', then ' if text else '') + ', then '.join(said)
        if not text: raise web.HTTPBadRequest(text='WRITE_WHAT_HAPPENS: type what happens in this moment first')
        if REWRITING['busy']: raise web.HTTPConflict(text='QWEN_IS_ALREADY_REWRITING: wait for the current suggestion')
        pics = {k: (b.get(k) or None) for k in ('extend_source', 'start_frame', 'end_frame')}
        await run(s.rewrite_request, text, people=picked_people(b), mode=mode, length=b.get('length'), action_photo=b.get('action_photo') or None, use_check=b.get('use_check', True) is not False, camera=b.get('camera') or None, framing=b.get('framing') or None, pose=pose_of(b), positions=b.get('positions') or None, hide_faces=b.get('hide_faces') or None)     # plain errors now, not in the job
        def go():
            import studio_rewrite_v0913 as rw, importlib.util as _iu, tempfile as _tf
            REWRITING['busy'] = True
            try:
                if not os.environ.get('DIRECTOR_REWRITE_FAKE'):
                    spec = _iu.spec_from_file_location('director_dispatcher_v0751', str(sv.qwen.DISPATCHER)); disp = _iu.module_from_spec(spec); spec.loader.exec_module(disp)
                    import studio_qwenmodel_v0916 as qm
                    rw.wait_for_gpu(f'http://127.0.0.1:{port_fn()}', dispatcher=disp,
                                    need_mib=qm.need_mib(qm.model_id(s.state()['settings'].get('qwen_model'), 'DIRECTOR_REWRITE_MODEL')))
                qp = (getattr(s, 'qwen_paths', None) or {}).get('qwen_python') or sv.qwen.DEFAULTS['qwen_python']
                with _tf.TemporaryDirectory() as d:
                    with _LOCK: req, _ = s.rewrite_request(text, people=picked_people(b), mode=mode, length=b.get('length'), action_photo=b.get('action_photo') or None, use_check=b.get('use_check', True) is not False, camera=b.get('camera') or None, framing=b.get('framing') or None, pose=pose_of(b), positions=b.get('positions') or None, hide_faces=b.get('hide_faces') or None, pictures_dir=d, **pics)   # the pictures Qwen looks at
                    try: reply, steps = rw.run_with_steps(req, qwen_python=qp)        # minutes: never while holding the project lock
                    except RuntimeError as e: raise sv.StudioError(str(e))
                    def checked(rep, stp):
                        with _LOCK: return s.rewrite(text, people=picked_people(b), mode=mode, reply=rep, steps=stp, action_photo=b.get('action_photo') or None,
                                                     end_frame=pics.get('end_frame'), use_check=b.get('use_check', True) is not False, extend_source=pics.get('extend_source'))
                    res = checked(reply, steps)
                    clash = s.rewrite_clashes(res, b.get('positions'), picked_people(b)) if mode == 'shot' else []
                    if clash:                                  # 📍 V0.9.49 it disagreed with your positions: one more go, told where
                        try:
                            reply2, steps2 = rw.run_with_steps(rw.position_fix(req, clash), qwen_python=qp)
                            res2 = checked(reply2, steps2); clash2 = s.rewrite_clashes(res2, b.get('positions'), picked_people(b))
                            if len(clash2) <= len(clash): res, clash = res2, clash2
                            res['position_retry'] = True
                        except (RuntimeError, sv.StudioError): res['position_retry'] = True
                    if clash: res['position_warnings'] = clash
                res['looked_at'] = [l for l, _ in req.get('images') or []]
                res['rewrite_id'] = os.urandom(4).hex()
                if req.get('in_pictures'): res['in_pictures'] = req['in_pictures']
                if req.get('pose_who'): res['pose_who'] = req['pose_who']          # V0.9.30      # V0.9.27: who the Director found in them        # V0.9.26: each result's boxes have their own keys (no old text put back)
                if not pics.get('extend_source') and not pics.get('start_frame'):
                    try:                                       # which take the rewrite took "the end of the previous shot" from
                        tl = s.timeline(); c = tl[-1] if tl else None
                        if c: res['previous'] = {'shot': c['shot'], 'take': c['take'], 'status': c.get('status')}
                    except Exception: pass
                return res
            finally: REWRITING['busy'] = False
        return ok({'job': start_job(s.root.name, 'rewrite', go)})

    @routes.post('/director/api/p/{slug}/character/{cid}/describe')
    async def describe_person(request):
        """📝 Qwen writes this person's look from their original photos: a SUGGESTION for their card (a background job, like
        the rewrite: it waits until ComfyUI is idle and frees the GPU first). Nothing is saved until you press Save."""
        s = studio(request.match_info['slug']); cid = request.match_info['cid']
        if REWRITING['busy']: raise web.HTTPConflict(text='QWEN_IS_BUSY: wait for the current rewrite')
        import tempfile as _tf0
        with _tf0.TemporaryDirectory() as d0: await run(s.describe_request, cid, d0)          # plain errors now, not in the job
        def go():
            import studio_rewrite_v0913 as rw, importlib.util as _iu, tempfile as _tf
            REWRITING['busy'] = True
            try:
                fake = os.environ.get('DIRECTOR_DESCRIBE_FAKE')
                with _tf.TemporaryDirectory() as d:
                    with _LOCK: req = s.describe_request(cid, d)
                    if fake: reply = Path(fake).read_text()
                    else:
                        spec = _iu.spec_from_file_location('director_dispatcher_v0751', str(sv.qwen.DISPATCHER)); disp = _iu.module_from_spec(spec); spec.loader.exec_module(disp)
                        import studio_qwenmodel_v0916 as qm
                        rw.wait_for_gpu(f'http://127.0.0.1:{port_fn()}', dispatcher=disp, need_mib=qm.need_mib(req.get('model_id')))
                        qp = (getattr(s, 'qwen_paths', None) or {}).get('qwen_python') or sv.qwen.DEFAULTS['qwen_python']
                        try: reply = rw.run(req, qwen_python=qp)
                        except RuntimeError as e: raise sv.StudioError(str(e))
                with _LOCK: return s.describe_look(cid, reply)
            finally: REWRITING['busy'] = False
        return ok({'job': start_job(s.root.name, 'describe', go)})

    @routes.post('/director/api/p/{slug}/plan_written_scene')
    async def plan_written_scene(request):
        s = studio(request.match_info['slug']); b = await request.json()
        try: emo = picked_emotions(b)
        except ValueError as e: raise web.HTTPBadRequest(text=str(e))
        return ok(await run(s.plan_written_scene, str(b.get('text') or ''), b.get('shots') or [], emotions=emo, background=b.get('background') or None,
                            start_frame=b.get('start_frame') or None, end_frame=b.get('end_frame') or None, pose=pose_of(b)))

    # ---- 🎬 make a film (V0.9.36): Qwen plans every shot; you read, edit, then plan and generate ----------------
    @routes.post('/director/api/p/{slug}/film/write')
    async def film_write(request):
        """A brief + a total length -> a background job: Qwen (when ComfyUI is idle, after freeing the GPU) plans the film and
        writes every shot. The result is a suggestion: nothing is planned until you press Plan."""
        s = studio(request.match_info['slug']); b = await request.json()
        try: seconds = float(b.get('seconds') or 0)
        except (TypeError, ValueError): raise web.HTTPBadRequest(text='FILM_LENGTH_IN_SECONDS')
        people = [x for x in (b.get('people') or []) if x] or None; places = [x for x in (b.get('places') or []) if x] or None
        brief = str(b.get('brief') or ''); opens_on = b.get('opens_on') or None          # V0.9.38 🎞
        angle = b.get('angle') or None                                               # V0.9.41 📷 one angle for the whole film
        lock = bool(b.get('lock_frame'))                                             # V0.9.47 🔒 stay in the opening frame
        lead = b.get('lead_in')                                                      # V0.9.48 ⏱ lead-in on shots that open on a picture
        pin_all = bool(b.get('pin_all'))                                             # V0.9.48 🎞 every shot opens on the picture
        cont = b.get('continue_from') or None                                         # V0.9.50 🎬 the next part of a film you made
        if REWRITING['busy']: raise web.HTTPConflict(text='QWEN_IS_BUSY: wait for the current rewrite or film')
        await run(s.film_request, brief, seconds, people=people, places=places, opens_on=opens_on, angle=angle, lock_frame=lock, lead_in=lead, pin_all=pin_all, continue_from=cont)          # plain errors now, not in the job
        prog = {'stage': 'waiting', 'done': 0, 'total': 0}; ctl = new_control()
        def go():
            import studio_rewrite_v0913 as rw, studio_film_v0936 as fm, importlib.util as _iu, tempfile as _tf
            REWRITING['busy'] = True
            try:
                with _tf.TemporaryDirectory() as d:
                    with _LOCK: req = s.film_request(brief, seconds, people=people, places=places, pictures_dir=d, opens_on=opens_on, angle=angle, lock_frame=lock, lead_in=lead, pin_all=pin_all, continue_from=cont)
                    if not os.environ.get('DIRECTOR_FILM_FAKE'):
                        spec = _iu.spec_from_file_location('director_dispatcher_v0751', str(sv.qwen.DISPATCHER)); disp = _iu.module_from_spec(spec); spec.loader.exec_module(disp)
                        import studio_qwenmodel_v0916 as qm
                        prog.update(stage='waiting_for_gpu')
                        rw.wait_for_gpu(f'http://127.0.0.1:{port_fn()}', dispatcher=disp, need_mib=qm.need_mib(req['model_id'], bool(req.get('think'))))
                    qp = (getattr(s, 'qwen_paths', None) or {}).get('qwen_python') or sv.qwen.DEFAULTS['qwen_python']
                    try: result = fm.run(req, qwen_python=qp, on_progress=prog.update, busy=_queue_busy, control=ctl)   # minutes: never while holding the project lock
                    except RuntimeError as e: raise sv.StudioError(str(e))
                with _LOCK: return s.film_from_reply(req, result)
            finally:
                REWRITING['busy'] = False
                if ctl.is_file(): ctl.unlink()
        jid = start_job(s.root.name, 'film', go); JOBS[jid]['progress'] = prog; CONTROLS[jid] = ctl
        return ok({'job': jid})

    # -- V0.9.43 🧠✋ watch Qwen write the film and step in -------------------------------------------------------
    CONTROLS = {}                                    # film job -> the file the Qwen worker reads your notes from
    def new_control():
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        return JOB_DIR / f'control_{_uuid.uuid4().hex[:12]}.json'

    @routes.get('/director/api/p/{slug}/framing/{ref}')
    async def picture_framing(request):
        """📐 V0.9.47 how one of your stills is framed, measured."""
        s = studio(request.match_info['slug']); ref = request.match_info['ref']
        if not re.fullmatch(r'[A-Za-z0-9_\-]{1,80}', ref or ''): raise web.HTTPBadRequest(text='bad picture id')
        return ok(await run(s.picture_framing, ref))

    @routes.post('/director/api/p/{slug}/preflight')
    async def preflight(request):
        """✅ V0.9.49 {shots: [[shot, take], ...]} -> the pre-flight list for what is about to be sent. Read-only."""
        s = studio(request.match_info['slug']); b = await request.json()
        pairs = b.get('shots') if isinstance(b, dict) else None
        if not isinstance(pairs, list) or not pairs or len(pairs) > 200: raise web.HTTPBadRequest(text='shots: a list of [shot, take]')
        try: return ok(await run(s.preflight, [tuple(x)[:2] for x in pairs]))
        except (TypeError, ValueError): raise web.HTTPBadRequest(text='shots: a list of [shot, take]')

    @routes.post('/director/api/p/{slug}/positions/guess')
    async def positions_guess(request):
        """📍 V0.9.49 {items: [{text, people, positions?}]} -> where each shot's text puts everyone, who it leaves vague, and
        where it disagrees with positions you set. Plain rules on your words; nothing is changed."""
        s = studio(request.match_info['slug']); b = await request.json()
        items = b.get('items') if isinstance(b, dict) else None
        if not isinstance(items, list): raise web.HTTPBadRequest(text='items: a list')
        return ok({'items': await run(s.positions_guess, items)})

    @routes.post('/director/api/p/{slug}/film/interject')
    async def film_interject(request):
        """A note for Qwen while it writes the film ({job, text}), or ⏩ {job, finish: true} = stop thinking and write it now.
        Your words, given to Qwen as the director's correction; never anything for H3 to look at."""
        s = studio(request.match_info['slug']); b = await request.json()
        jid = str(b.get('job') or ''); ctl = CONTROLS.get(jid); j = JOBS.get(jid)
        if not ctl or not j or j.get('slug') not in (None, s.root.name): raise web.HTTPNotFound(text='NO_SUCH_FILM_JOB')
        if j.get('state') != 'RUNNING': raise web.HTTPConflict(text='QWEN_HAS_FINISHED: this film is already written; edit a shot or press ✍ Qwen rewrites')
        finish = bool(b.get('finish')); text = ' '.join(str(b.get('text') or '').split())[:300]
        if not finish and not text: raise web.HTTPBadRequest(text='TYPE_A_NOTE_FIRST')
        if text:
            import director_intent_v0739 as di
            if di.FORBIDDEN_VISUAL_SIMPLE.search(text): raise web.HTTPUnprocessableEntity(text=json.dumps({'error': 'CLEAN_CHAIN_REFUSED: a note can change the story, never what H3 looks at'}), content_type='application/json')
        def add():
            try: items = json.loads(ctl.read_text()) if ctl.is_file() else []
            except ValueError: items = []
            items.append({'id': _uuid.uuid4().hex[:8], 'kind': 'finish' if finish else 'note', 'text': text, 'at': _time.time()})
            tmp = ctl.with_name(ctl.name + '.tmp'); tmp.write_text(json.dumps(items)); os.replace(tmp, ctl)
            return len(items)
        n = await asyncio.to_thread(add)
        return ok({'ok': True, 'queued': n})

    def _queue_busy():
        """V0.9.37: True when ComfyUI has work queued or running (you pressed Generate): Qwen steps aside for H3."""
        if os.environ.get('DIRECTOR_FILM_FAKE'): return False
        from urllib.request import urlopen as _uo3
        try:
            with _uo3(f'http://127.0.0.1:{port_fn()}/queue', timeout=5) as r: q = json.loads(r.read() or b'{}')
            return bool(q.get('queue_running') or q.get('queue_pending'))
        except Exception: return False

    @routes.post('/director/api/p/{slug}/film/rewrite')
    async def film_rewrite(request):
        """V0.9.37 ✍: Qwen writes chosen shots of the film again (a background job, when ComfyUI is idle). A suggestion like
        the rest of the film: nothing is planned until you press Plan."""
        s = studio(request.match_info['slug']); b = await request.json()
        film = b.get('film'); nums = b.get('shots') or []
        if REWRITING['busy']: raise web.HTTPConflict(text='QWEN_IS_BUSY: wait for the current rewrite or film')
        req = await run(s.film_rewrite_request, film, nums)          # plain errors now, not in the job
        prog = {'stage': 'waiting', 'done': 0, 'total': len(req['numbers'])}; ctl = new_control()
        def go():
            import studio_rewrite_v0913 as rw, studio_film_v0936 as fm, importlib.util as _iu
            REWRITING['busy'] = True
            try:
                import tempfile as _tf
                with _tf.TemporaryDirectory() as d:                      # V0.9.38 🎞: a shot's start picture, for Qwen to see
                    with _LOCK: rq = s.film_rewrite_request(film, nums, pictures_dir=d)
                    if not os.environ.get('DIRECTOR_FILM_FAKE'):
                        spec = _iu.spec_from_file_location('director_dispatcher_v0751', str(sv.qwen.DISPATCHER)); disp = _iu.module_from_spec(spec); spec.loader.exec_module(disp)
                        import studio_qwenmodel_v0916 as qm
                        prog.update(stage='waiting_for_gpu')
                        rw.wait_for_gpu(f'http://127.0.0.1:{port_fn()}', dispatcher=disp, need_mib=qm.need_mib(rq['model_id']))
                    qp = (getattr(s, 'qwen_paths', None) or {}).get('qwen_python') or sv.qwen.DEFAULTS['qwen_python']
                    try: result = fm.run(rq, qwen_python=qp, on_progress=prog.update, busy=_queue_busy, timeout=1800, control=ctl)
                    except RuntimeError as e: raise sv.StudioError(str(e))
                with _LOCK: return s.film_rewrite_apply(film, rq, result)
            finally:
                REWRITING['busy'] = False
                if ctl.is_file(): ctl.unlink()
        jid = start_job(s.root.name, 'film_rewrite', go); JOBS[jid]['progress'] = prog; CONTROLS[jid] = ctl
        return ok({'job': jid})

    @routes.post('/director/api/p/{slug}/joins')
    async def joins(request):
        """V0.9.37 📍 every join of the movie (or of these shots): who ends where, who starts where, what 🔍 said."""
        s = studio(request.match_info['slug']); b = await request.json()
        return ok({'joins': await run(s.join_report, b.get('shots') or None)})

    @routes.post('/director/api/p/{slug}/film/estimate')
    async def film_estimate(request):
        """V0.9.37 ⏱: about how long the film's shots take to generate, from how long your takes took on this ComfyUI."""
        s = studio(request.match_info['slug']); b = await request.json()
        return ok(await run(s.film_estimate, b.get('film') or {}))

    @routes.get('/director/api/p/{slug}/films')
    async def films_list(request):
        """🎬 V0.9.50 your saved films (the Films shelf)."""
        s = studio(request.match_info['slug']); return ok({'films': await run(s.films)})

    @routes.get('/director/api/p/{slug}/film/{fid:[0-9a-f]{1,10}}')
    async def film_one(request):
        s = studio(request.match_info['slug']); fid = request.match_info['fid']
        if not re.fullmatch(r'[0-9a-f]{1,10}', fid or ''): raise web.HTTPBadRequest(text='bad film id')
        return ok({'film': await run(s.load_film, fid)})

    @routes.get('/director/api/p/{slug}/film/last')
    async def film_last(request):
        s = studio(request.match_info['slug'])
        return ok({'film': await asyncio.to_thread(s.last_film)})

    @routes.post('/director/api/p/{slug}/film/plan')
    async def film_plan(request):
        """Plan the film's shots in order (a background job, one shot at a time so the page stays usable)."""
        s = studio(request.match_info['slug']); b = await request.json()
        film = b.get('film'); start_at = int(b.get('start_at') or 1)
        import studio_film_v0936 as fm
        try: shots = fm.clean_edited(film)
        except ValueError as e: raise web.HTTPBadRequest(text=str(e))
        prog = {'stage': 'planning', 'done': 0, 'total': len([x for x in shots if x['n'] >= start_at])}
        def go():
            f = film; plans = []; stopped = None; n = start_at; r = None
            while n <= len(shots):
                with _LOCK: r = s.plan_film(f, start_at=n, count=1)
                f = r['film']; plans += r['plans']; prog['done'] += 1
                if r['stopped']: stopped = r['stopped']; break
                n += 1
            return {'film': f, 'plans': plans, 'stopped': stopped, 'shots': (r or {}).get('shots', [])}
        jid = start_job(s.root.name, 'film_plan', go); JOBS[jid]['progress'] = prog
        return ok({'job': jid})

    # ---- 🎭 movement from YOUR video (V0.9.21) -------------------------------------------------------------
    POSE = {'ready': None, 'at': 0.0, 'downloading': False}
    def pose_ready(force=False):
        import studio_pose_v0921 as ps
        if not force and POSE['ready'] is not None and _time.time() - POSE['at'] < 60: return POSE['ready']
        from urllib.request import urlopen as _uo
        def fetch(node):
            try:
                with _uo(f'http://127.0.0.1:{port_fn()}/object_info/{node}', timeout=10) as r: return json.load(r).get(node)
            except Exception: return None
        if fetch('MiniMaxH3ReferenceToVideo') is None and fetch('UNETLoader') is None:
            return {'ready': False, 'problems': ['ComfyUI is not answering right now'], 'missing_models': [], 'missing_files': [], 'download_gb': 0}
        POSE['ready'] = ps.readiness(fetch); POSE['at'] = _time.time()
        return POSE['ready']

    @routes.get('/director/api/p/{slug}/pose')
    async def pose_info(request):
        """The videos a shot can take its movement from, and whether this ComfyUI has the pose nodes and models."""
        s = studio(request.match_info['slug'])
        src = await asyncio.to_thread(s.pose_sources)
        r = await asyncio.to_thread(pose_ready, request.query.get('fresh') == '1')
        if r.get('ready'): POSE['restart'] = False
        return ok({'sources': [{k: x.get(k) for k in ('id', 'label', 'kind', 'seconds')} for x in src], 'comfyui': r,
                   'downloading': POSE['downloading'], 'restart_comfyui': bool(POSE.get('restart'))})

    @routes.post('/director/api/pose/download')
    async def pose_download(request):
        """Download the missing pose models into ComfyUI/models (the ControlNet is BUILT for the full BF16 H3, V0.9.26): a
        background job; restart ComfyUI afterwards."""
        import studio_pose_v0921 as ps
        if POSE['downloading']: raise web.HTTPConflict(text='POSE_MODELS_ALREADY_DOWNLOADING')
        r = await asyncio.to_thread(pose_ready, True)
        if not r['missing_models']: return ok({'job': None, 'nothing_missing': True})
        free = shutil.disk_usage(str(comfy_root)).free / 1e9
        need = (r.get('disk_gb') or ps.SIZE_GB) + 5
        if free < need: raise web.HTTPConflict(text=f'NOT_ENOUGH_DISK: about {need} GB free needed, {free:.0f} GB free')
        POSE['downloading'] = True
        def go():
            try:
                done = ps.download(r['missing_models'], comfy_root); POSE['ready'] = None; POSE['restart'] = True
                return {'downloaded': done, 'restart_comfyui': True}
            finally: POSE['downloading'] = False
        return ok({'job': start_job(None, 'pose_download', go)})

    # ---- 🧪 V0.9.60 experiment: redraw the built carry-on frame in the poses where the last shot ended ---------------
    REDRAW = {'ready': None, 'at': 0.0, 'downloading': False, 'restart': False}
    def redraw_ready(force=False):
        import studio_redraw_v0960 as rd
        if not force and REDRAW['ready'] is not None and _time.time() - REDRAW['at'] < 60: return REDRAW['ready']
        from urllib.request import urlopen as _uo
        def fetch(node):
            try:
                with _uo(f'http://127.0.0.1:{port_fn()}/object_info/{node}', timeout=10) as r: return json.load(r).get(node)
            except Exception: return None
        if fetch('UNETLoader') is None:
            return {'ready': False, 'problems': ['ComfyUI is not answering right now'], 'missing_models': [], 'missing_files': [], 'download_gb': 0}
        REDRAW['ready'] = rd.readiness(fetch); REDRAW['at'] = _time.time()
        return REDRAW['ready']

    @routes.get('/director/api/redraw')
    async def redraw_info(request):
        """🧪 Is this ComfyUI ready to redraw built frames (Qwen-Image-Edit 2511 + SDPose), and what is missing."""
        r = await asyncio.to_thread(redraw_ready, request.query.get('fresh') == '1')
        if r.get('ready'): REDRAW['restart'] = False
        return ok({'comfyui': r, 'downloading': REDRAW['downloading'], 'restart_comfyui': bool(REDRAW['restart'])})

    @routes.post('/director/api/redraw/download')
    async def redraw_download(request):
        """🧪 Download the missing redraw models into ComfyUI/models (full BF16, ~58 GB): a background job; restart ComfyUI after."""
        import studio_redraw_v0960 as rd
        if REDRAW['downloading']: raise web.HTTPConflict(text='REDRAW_MODELS_ALREADY_DOWNLOADING')
        r = await asyncio.to_thread(redraw_ready, True)
        if not r['missing_models']: return ok({'job': None, 'nothing_missing': True})
        free = shutil.disk_usage(str(comfy_root)).free / 1e9; need = r['download_gb'] + 10
        if free < need: raise web.HTTPConflict(text=f'NOT_ENOUGH_DISK: about {need:.0f} GB free needed, {free:.0f} GB free')
        REDRAW['downloading'] = True
        def go():
            try:
                done = rd.download(r['missing_models'], comfy_root); REDRAW['ready'] = None; REDRAW['restart'] = True
                return {'downloaded': done, 'restart_comfyui': True}
            finally: REDRAW['downloading'] = False
        return ok({'job': start_job(None, 'redraw_download', go)})

    @routes.post('/director/api/p/{slug}/motion')
    async def motion_add(request):
        """Add a movement video of yours (multipart: file, name, attest=yes). Checked like every original."""
        s = studio(request.match_info['slug'])
        reader = await request.multipart(); fields = {}; tmp = None
        try:
            async for part in reader:
                if part.name == 'file':
                    suffix = Path(part.filename or 'movement.mp4').suffix.lower()[:6]
                    if suffix not in ('.mp4', '.mov', '.m4v', '.webm', '.mkv'): raise web.HTTPBadRequest(text='MOVEMENT_VIDEO_MUST_BE_A_VIDEO')
                    (s.root / 'uploads').mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(suffix=suffix, dir=s.root / 'uploads'); size = 0
                    with os.fdopen(fd, 'wb') as f:
                        while True:
                            chunk = await part.read_chunk(1 << 20)
                            if not chunk: break
                            size += len(chunk)
                            if size > MAX_UPLOAD: raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=size)
                            f.write(chunk)
                else: fields[part.name] = (await part.text())[:200]
            if not tmp: raise web.HTTPBadRequest(text='no file')
            path, tmp = tmp, None
            def go():
                try:
                    with _LOCK: return {'id': s.add_motion(path, name=fields.get('name', ''), attest=fields.get('attest') == 'yes')}
                finally:
                    if os.path.exists(path): os.unlink(path)
            return ok({'job': start_job(s.root.name, 'motion', go)})
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)

    @routes.post('/director/api/p/{slug}/action_photo')
    async def action_photo_add(request):
        """📷 One of YOUR original stills showing the action you want, for the rewriter only (multipart: file, name, attest=yes)."""
        s = studio(request.match_info['slug'])
        reader = await request.multipart(); fields = {}; tmp = None
        try:
            async for part in reader:
                if part.name == 'file':
                    suffix = Path(part.filename or 'action.jpg').suffix.lower()[:6]
                    if suffix not in ('.png', '.jpg', '.jpeg', '.webp'): raise web.HTTPBadRequest(text='ACTION_PHOTO_MUST_BE_A_PHOTO')
                    (s.root / 'uploads').mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(suffix=suffix, dir=s.root / 'uploads'); size = 0
                    with os.fdopen(fd, 'wb') as f:
                        while True:
                            chunk = await part.read_chunk(1 << 20)
                            if not chunk: break
                            size += len(chunk)
                            if size > 50 * 1024 ** 2: raise web.HTTPRequestEntityTooLarge(max_size=50 * 1024 ** 2, actual_size=size)
                            f.write(chunk)
                else: fields[part.name] = (await part.text())[:200]
            if not tmp: raise web.HTTPBadRequest(text='no file')
            aid = await run(s.add_action_photo, tmp, name=fields.get('name', ''), attest=fields.get('attest') == 'yes')
            return ok({'id': aid})
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)

    @routes.post('/director/api/p/{slug}/action_photo/{aid}/remove')
    async def action_photo_remove(request):
        s = studio(request.match_info['slug']); await run(s.remove_action_photo, request.match_info['aid']); return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/motion/{mid}/remove')
    async def motion_remove(request):
        s = studio(request.match_info['slug']); await run(s.remove_motion, request.match_info['mid']); return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/pose_preview')
    async def pose_preview(request):
        """👁 See the stick figures before a take: a small ComfyUI run (SDPose only, no H3) saved as PosePreview_... in
        ComfyUI/output (never picked up as a take, never usable as a reference)."""
        import studio_pose_v0921 as ps
        s = studio(request.match_info['slug']); b = await request.json()
        r = await asyncio.to_thread(pose_ready, True)
        if not r['ready']: raise web.HTTPConflict(text='POSE_NOT_READY_IN_COMFYUI: ' + '; '.join(r['problems'] + [f'missing {x}' for x in r['missing_files']])[:500])
        secs = {'6': 158, '8': 192, '10': 243}.get(str(b.get('length') or ''), None)
        def prep():
            import studio_prompts_v094 as _pv
            st = s.state(); L = secs or _pv.h3_length(st['settings'].get('gen_length', 124))
            return s._prepare_pose(st, {'source': b.get('source'), 'strength': b.get('strength'), 'start': b.get('start')}, L)
        try: rec = await asyncio.to_thread(prep)
        except sv.StudioError as e: raise web.HTTPUnprocessableEntity(text=json.dumps({'error': str(e)}), content_type='application/json')
        prefix = f"PosePreview_{s.root.name}_{rec['sha256'][:8]}"
        api, _ = ps.pose_api(rec['name'], prefix=prefix)
        def send():
            from urllib.request import urlopen as _uo, Request as _Rq
            body = json.dumps({'prompt': api, 'client_id': sv.CLIENT_ID}).encode()
            with _uo(_Rq(f'http://127.0.0.1:{port_fn()}/prompt', data=body, headers={'Content-Type': 'application/json'}, method='POST'), timeout=30) as resp:
                return json.load(resp)
        try: q = await asyncio.to_thread(send)
        except Exception as e:
            detail = getattr(e, 'read', lambda: b'')().decode(errors='replace')[:600]
            raise web.HTTPBadGateway(text=f'COMFYUI_REFUSED_THE_PREVIEW: {e} {detail}')
        return ok({'prompt_id': q.get('prompt_id'), 'prefix': prefix, 'short_by': rec['short_by']})

    @routes.get('/director/api/pose_preview/{pid}')
    async def pose_preview_result(request):
        """Where the preview video is once ComfyUI made it ({'state': 'running'|'done'|'error', 'file'})."""
        pid = request.match_info['pid']
        if not re.fullmatch(r'[0-9a-fA-F-]{8,64}', pid): raise web.HTTPBadRequest(text='bad id')
        def look():
            from urllib.request import urlopen as _uo
            with _uo(f'http://127.0.0.1:{port_fn()}/history/{pid}', timeout=10) as r: return json.load(r).get(pid)
        try: h = await asyncio.to_thread(look)
        except Exception as e: return ok({'state': 'running', 'note': str(e)[:80]})
        if not h: return ok({'state': 'running'})
        stt = (h.get('status') or {}).get('status_str')
        if stt == 'error':
            msg = next((m[1].get('exception_message') for m in (h.get('status') or {}).get('messages', []) if m[0] == 'execution_error'), 'error')
            return ok({'state': 'error', 'error': str(msg)[:400]})
        for out in (h.get('outputs') or {}).values():
            for g in out.get('gifs') or out.get('videos') or []:
                if str(g.get('filename', '')).startswith('PosePreview_'):
                    return ok({'state': 'done', 'file': g['filename'], 'subfolder': g.get('subfolder', ''), 'type': g.get('type', 'output')})
        return ok({'state': 'running' if stt != 'success' else 'error', 'error': None if stt != 'success' else 'no preview video in the result'})

    @routes.post('/director/api/p/{slug}/angles')
    async def angles(request):
        s = studio(request.match_info['slug']); b = await request.json(); url = f'http://127.0.0.1:{port_fn()}'
        def go():
            pairs = s.angles(int(b['shot']), int(b['take']))
            for sh, tk in pairs: s.generate(sh, tk, url=url)
            return {'takes': [tk for _, tk in pairs]}
        return ok(await run(go))

    @routes.get('/director/api/p/{slug}/candidates/{shot}/{take}')
    async def candidates(request):
        s = studio(request.match_info['slug'])
        await run(s.sync, analyze=False)
        return ok({'files': await run(s.output_candidates, int(request.match_info['shot']), int(request.match_info['take']))})

    @routes.post('/director/api/p/{slug}/attach')
    async def attach(request):
        """A take ComfyUI made that the Director didn't pick up: choose it from ComfyUI/output, or upload it."""
        s = studio(request.match_info['slug'])
        if request.content_type.startswith('multipart/'):
            fields = {}; tmp = None
            try:
                reader = await request.multipart()
                async for part in reader:
                    if part.name == 'file':
                        fd, tmp = tempfile.mkstemp(suffix=Path(part.filename or 'take.mp4').suffix.lower()[:6] or '.mp4')
                        size = 0
                        with os.fdopen(fd, 'wb') as fh:
                            while True:
                                chunk = await part.read_chunk(1 << 20)
                                if not chunk: break
                                size += len(chunk)
                                if size > 2_000_000_000: raise web.HTTPRequestEntityTooLarge(max_size=2_000_000_000, actual_size=size)
                                fh.write(chunk)
                    else: fields[part.name] = (await part.text()).strip()
                if not tmp: raise web.HTTPBadRequest(text='no file')
                res = await run(s.attach_take, int(fields['shot']), int(fields['take']), upload=tmp, confirm=fields.get('confirm') == 'yes')
            finally:
                if tmp and os.path.exists(tmp): os.unlink(tmp)
        else:
            b = await request.json()
            res = await run(s.attach_take, int(b['shot']), int(b['take']), file=str(b.get('file', '')), confirm=bool(b.get('confirm')))
        PENDING.add(s.root.name)
        return ok(res)

    @routes.post('/director/api/p/{slug}/retry')
    async def retry(request):
        s = studio(request.match_info['slug']); b = await request.json(); url = f'http://127.0.0.1:{port_fn()}'
        def go():
            sh, tk = s.retry_plan(int(b['shot']), int(b['take']), str(b.get('how', 'same')))
            return {'shot': sh, 'take': tk, 'prompt_id': s.generate(sh, tk, url=url), 'note': getattr(s, 'last_note', None)}
        return ok(await run(go))

    @routes.post('/director/api/p/{slug}/another_take')
    async def another_take(request):
        s = studio(request.match_info['slug']); b = await request.json(); url = f'http://127.0.0.1:{port_fn()}'
        def go():
            sh = int(b['shot']); tk = s.another_take(sh)
            return {'shot': sh, 'take': tk, 'prompt_id': s.generate(sh, tk, url=url), 'note': getattr(s, 'last_note', None)}
        return ok(await run(go))

    @routes.post('/director/api/p/{slug}/opening_frame')       # V0.9.61 🔗 build a carry-on shot's opening frame to look at (sends nothing)
    async def opening_frame(request):
        s = studio(request.match_info['slug']); b = await request.json()
        return ok(await run(s.opening_frame, int(b['shot'])))

    @routes.post('/director/api/p/{slug}/opening_frame/choice')    # ✓ use it / ✕ don't
    async def opening_frame_choice(request):
        s = studio(request.match_info['slug']); b = await request.json()
        return ok(await run(s.frame_choice, int(b['shot']), int(b['take']), bool(b.get('use'))))

    @routes.post('/director/api/p/{slug}/prompt')
    async def prompt_edit(request):
        s = studio(request.match_info['slug']); b = await request.json()
        return ok(await run(s.edit_prompt, int(b['shot']), int(b['take']), str(b.get('prompt', ''))))

    @routes.post('/director/api/p/{slug}/generate')
    async def generate(request):
        s = studio(request.match_info['slug']); b = await request.json()
        url = f'http://127.0.0.1:{port_fn()}'; shot, take = int(b['shot']), int(b['take']); n = int(b.get('count') or 1)
        def go():
            takes = [take] + (s.more_takes(shot, take, n) if n > 1 else [])
            ids = [s.generate(shot, t, url=url) for t in takes]
            return ids, takes
        ids, takes = await run(go)
        return ok({'prompt_id': ids[0], 'prompt_ids': ids, 'takes': takes, 'note': getattr(s, 'last_note', None)})

    @routes.post('/director/api/p/{slug}/ab')
    async def ab(request):
        s = studio(request.match_info['slug']); b = await request.json(); url = f'http://127.0.0.1:{port_fn()}'
        def go():
            r = s.ab_test(int(b['shot']), b.get('setting', ''))
            if b.get('generate', True):
                for side in ('a', 'b'): s.generate(r[side]['shot'], r[side]['take'], url=url)
            return {'id': r['id'], 'a': r['a']['take'], 'b': r['b']['take']}
        return ok(await run(go))

    @routes.post('/director/api/p/{slug}/ab/{aid}')
    async def ab_pick(request):
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.ab_result, request.match_info['aid'], b.get('winner', ''))
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/storyboard')
    async def storyboard(request):
        s = studio(request.match_info['slug']); b = await request.json()
        text = str(b.get('text', ''))
        import studio_script_v091 as sc
        chars = [c for c in s.state()['characters'].values() if not c.get('retired')]
        shots, _ = sc.parse(text, chars)
        if any(x['speaker'] for x in shots) or b.get('script'):        # "Name: line" -> a dialogue script
            r = await run(s.plan_script, text, preview=bool(b.get('preview')))
            return ok({'script': True, **r})
        lines = [l for l in text.splitlines()]
        return ok({'plans': await run(s.storyboard, lines)})

    @routes.post('/director/api/p/{slug}/generate_in_order')
    async def generate_in_order(request):
        """Storyboard: generate the planned shots one after another; each is rewritten (text only) from how the
        previous shot actually ends in the movie before it is sent. Your click authorises the whole sequence."""
        s = studio(request.match_info['slug']); url = f'http://127.0.0.1:{port_fn()}'
        b = (await request.json()) if request.can_read_body else {}
        only = [int(x) for x in b.get('shots')] if isinstance(b.get('shots'), list) else None
        if b.get('preview'): return ok({'shots': await run(s.start_chain, only, True)})
        def go():
            todo = s.start_chain(only, review=bool(b.get('review')), takes=int(b.get('takes') or 1), overnight=bool(b.get('overnight')))
            r = s.chain_step(url); return {'shots': todo, 'first': r}
        res = await run(go); CHAINS.add(s.root.name); CHAIN_T[s.root.name] = _time.time()
        return ok(res)

    @routes.post('/director/api/p/{slug}/generate_in_order/review')
    async def generate_in_order_review(request):
        """V0.9.38: change "Wait for my ✓ Keep after each shot" on the run that is going now."""
        s = studio(request.match_info['slug']); b = await request.json()
        return ok({'review': await run(s.set_chain_review, bool(b.get('review')))})

    @routes.post('/director/api/p/{slug}/generate_in_order/stop')
    async def generate_in_order_stop(request):
        s = studio(request.match_info['slug']); await run(s.stop_chain); CHAINS.discard(s.root.name); return ok({'stopped': True})

    @routes.post('/director/api/p/{slug}/remember')
    async def remember(request):
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.remember, int(b['shot']), int(b['take']), str(b.get('text', ''))); return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/generate_all')
    async def generate_all(request):
        s = studio(request.match_info['slug']); url = f'http://127.0.0.1:{port_fn()}'
        def go():
            sent = []
            for shot, take in s.unsent_plans(): s.generate(shot, take, url=url); sent.append([shot, take])
            return sent
        return ok({'sent': await run(go)})

    @routes.get('/director/api/p/{slug}/loras')
    async def loras(request):
        s = studio(request.match_info['slug'])
        try: sch = await run(s.live_schema, f'http://127.0.0.1:{port_fn()}')
        except web.HTTPException: sch = {'lora_files': [], 'lora_node': False}
        return ok({'available': sch.get('lora_files', []), 'node': sch.get('lora_node', False),
                   'configured': s.state()['settings'].get('loras', [])})

    @routes.post('/director/api/p/{slug}/review')
    async def review(request):
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.review, int(b['shot']), int(b['take']), b['decision'], b.get('note'))
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/trim')
    async def trim(request):
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.set_trim, int(b['shot']), int(b['take']), int(b['in']), int(b['out']))
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/cuts')
    async def cuts(request):                              # ✂ V0.9.68 split a clip, remove pieces of it
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.set_cuts, int(b['shot']), int(b['take']), b.get('splits') or [], b.get('removed') or [])
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/exclude')
    async def exclude(request):
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.exclude, int(b['shot']), bool(b.get('on', True)))
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/order')
    async def order(request):
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.reorder, [int(x) for x in b['order']])
        return ok({'ok': True})

    @routes.post('/director/api/p/{slug}/setting')
    async def setting(request):
        s = studio(request.match_info['slug']); b = await request.json()
        await run(s.setting, **b)
        return ok({'ok': True})

    def cancel_in_comfy(pid):
        """Take one of our prompts out of ComfyUI's queue, or stop it if it is the one running."""
        from urllib.request import urlopen, Request
        base = f'http://127.0.0.1:{port_fn()}'
        try:
            with urlopen(base + '/queue', timeout=5) as r: q = json.load(r)
            running = any(isinstance(i, list) and len(i) > 1 and i[1] == pid for i in q.get('queue_running', []))
            if running:
                urlopen(Request(base + '/interrupt', data=json.dumps({'prompt_id': pid}).encode(), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5).read()
            else:
                urlopen(Request(base + '/queue', data=json.dumps({'delete': [pid]}).encode(), headers={'Content-Type': 'application/json'},
                                method='POST'), timeout=5).read()
            return 'stopped' if running else 'removed from queue'
        except Exception as e: return 'could not cancel in ComfyUI: ' + str(e)[:80]

    @routes.post('/director/api/p/{slug}/discard')
    async def discard(request):
        """Discard a planned shot or one planned / queued take of it (also out of ComfyUI's queue)."""
        s = studio(request.match_info['slug']); b = await request.json()
        try: shot = int(b.get('shot')); take = None if b.get('take') in (None, '') else int(b.get('take'))
        except (TypeError, ValueError): raise web.HTTPBadRequest(text='SHOT_NUMBER')
        r = await run(s.discard, shot, take)
        r['comfyui'] = [await asyncio.to_thread(cancel_in_comfy, pid) for pid in getattr(s, 'last_cancel', []) or []]
        return ok(r)

    @routes.post('/director/api/p/{slug}/undo')
    async def undo(request):
        s = studio(request.match_info['slug'])
        undid = await run(s.undo); cancelled = []
        for pid in getattr(s, 'last_cancel', []) or []:          # also take it out of ComfyUI's queue (or stop it if running)
            cancelled.append(await asyncio.to_thread(cancel_in_comfy, pid))
        return ok({'undid': undid, 'cancelled': cancelled})

    @routes.post('/director/api/p/{slug}/render')
    async def render(request):
        s = studio(request.match_info['slug'])
        def go():
            r = s.render(); return {'file': Path(r['path']).name, 'frames': r.get('frames'), 'seconds': r.get('seconds')}
        return ok({'job': start_job(s.root.name, 'render', go)})

    @routes.post('/director/api/p/{slug}/prepare/{what}')
    async def prepare(request):
        """Backup / export built in the background; the page downloads the file when the job is done."""
        import studio_backup_v090 as bk, studio_export_v090 as ex
        s = studio(request.match_info['slug']); what = request.match_info['what']
        if what not in ('backup', 'backup_with_takes', 'export'): raise web.HTTPBadRequest(text='backup, backup_with_takes or export')
        def go():
            if what == 'export':
                name = f'{s.slug}_edit.zip'; ex.export_package(s, s.root / 'renders' / name); return {'kind': 'render', 'file': name}
            d = s.root / 'backups'; d.mkdir(exist_ok=True)
            for old in d.glob('*.zip'): old.unlink()
            name = f"{s.slug}_backup{'_with_takes' if what == 'backup_with_takes' else ''}.zip"
            bk.make_backup(s, d / name, with_takes=what == 'backup_with_takes'); return {'kind': 'backup', 'file': name}
        return ok({'job': start_job(s.root.name, what, go)})

    def safe_file(s, kind, name):
        if not FILE.match(name or ''): raise web.HTTPBadRequest(text='bad file name')
        base = {'render': s.root / 'renders', 'workflow': s.root / 'plans', 'take': s.output_dir, 'backup': s.root / 'backups',
                'ref': s.input_dir / s.input_sub, 'action': s.root / 'action_photos', 'upload': s.root / 'uploads'}.get(kind)
        if base is None: raise web.HTTPBadRequest(text='bad kind')
        p = base / name
        if kind == 'take' and not p.is_file() and (s.root / 'sources' / name).is_file():   # your original video placed in the movie
            p = s.root / 'sources' / name
        if kind == 'ref' and not p.is_file():                  # registered originals that live directly in ComfyUI/input
            if name in {r['input_name'] for r in s.state()['refs'].values() if '/' not in r['input_name']}: p = s.input_dir / name
        if not p.is_file() or p.is_symlink(): raise web.HTTPNotFound()
        if kind == 'take' and not name.startswith(f'Studio_{s.slug}_'):
            known = {t['file'] for sh in s.state()['shots'].values() for t in sh['takes']}   # e.g. imported hallway takes
            if name not in known: raise web.HTTPForbidden()
        return p

    @routes.get('/director/api/p/{slug}/file/{kind}/{name}')
    async def file(request):
        s = studio(request.match_info['slug'])
        p = safe_file(s, request.match_info['kind'], request.match_info['name'])
        headers = {'Cache-Control': 'no-store'}
        if request.match_info['kind'] == 'take' and p.suffix.lower() in ('.mp4', '.mov', '.webm') and request.query.get('raw') != '1':
            import studio_preview_v0968 as pv           # ▶ V0.9.68 a take the browser can't play -> its cached 8-bit preview
            p = await asyncio.to_thread(pv.preview_for, p, s.root / 'renders' / 'preview')
        elif request.query.get('raw') == '1': headers['Content-Disposition'] = f'attachment; filename="{p.name}"'
        if request.match_info['kind'] in ('workflow', 'backup') or p.suffix == '.zip':
            headers['Content-Disposition'] = f'attachment; filename="{p.name}"'
        return web.FileResponse(p, headers=headers)

    @routes.get('/director/api/p/{slug}/thumb/{name}/{frame}')
    async def thumb(request):
        s = studio(request.match_info['slug'])
        p = safe_file(s, 'take', request.match_info['name'])
        frame = int(request.match_info['frame'])
        def grab():
            import cv2
            cap = cv2.VideoCapture(str(p)); cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame)); ok_, im = cap.read(); cap.release()
            if not ok_: raise FileNotFoundError('FRAME')
            im = cv2.resize(im, (int(im.shape[1] * 240 / im.shape[0]), 240))
            return cv2.imencode('.jpg', im, [cv2.IMWRITE_JPEG_QUALITY, 82])[1].tobytes()
        data = await run(grab)
        return web.Response(body=data, content_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})
    return routes
