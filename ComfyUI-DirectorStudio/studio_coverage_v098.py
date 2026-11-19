"""Coverage: one line of direction -> 2 to 4 shots that cut together like a filmed scene.

Independent H3 takes never match each other's timing, so the Director does NOT film the same action from several cameras
(that would show it twice, out of sync). Instead each shot covers the NEXT beat, with film-grammar framing:

  1. a wide shot establishes who is where (with the first action, or the first line if the moment opens with dialogue);
  2. each line of dialogue gets the speaker's shot: over the shoulder when someone listens, alternating with a close-up
     while the same person keeps talking (so the cut between their shots isn't a jump cut);
  3. after a line, the listener's reaction close-up (no one speaks) when there's room;
  4. physical action between people (a hug, a handshake) is a waist-up two-shot.

At most 4 shots; extra beats are folded into the last one. Plain words only: the shots are planned by the normal
Director (same references, continuity, Clean-Chain), one after the other, and each is undoable like any planned shot.
"""
from __future__ import annotations
import re

MAX_SHOTS = 4
SPEAKS = r'(?:says|said|answers|answered|replies|replied|asks|asked|tells|told|whispers|shouts|yells|adds|laughs)'
CLAUSE = re.compile(r'\s*(?:,\s*)?(?:\band then\b|\bthen\b|\bafter that\b|\bfinally\b|;|(?<=[.!?\x00])\s+(?=[A-Z])|'
                    r'\band\b(?=\s+(?:the\s+)?\w+(?:\s\w+)?\s+' + SPEAKS + r'\b))\s*')
TOGETHER = re.compile(r'\b(hug|embrace|kiss|shake hands|handshake|high[- ]five|dance|fight|wrestle|hold hands|walk together|sit together|'
                      r'they|both|each other|together)', re.I)

def clauses(text):
    """Split a direction into beats, never inside a quoted line."""
    keep = {}
    def hide(m):
        k = f'\x00{len(keep)}\x00'; keep[k] = m.group(0); return k
    t = re.sub(r'["“][^"”]*["”]', hide, (text or '').strip())
    parts = []
    for piece in CLAUSE.split(t):
        piece = (piece or '').strip(' ,.;')
        piece = re.sub(r'^(?:and then|then|after that|finally|and)\b[\s,]*', '', piece, flags=re.I).strip(' ,.;')
        if not piece: continue
        piece = piece[0].upper() + piece[1:]
        for k, v in keep.items(): piece = piece.replace(k, v)
        parts.append(piece)
    return parts

def name_people(parts, cast, names, pronouns):
    """'she fixes her hair' -> 'Girl fixes her hair': each shot is planned on its own, so a bare she/he must say who.
    A pronoun goes to the person it last pointed at in this moment (or the only one in the scene with it); unclear: left as is."""
    last = {}
    for c in cast:                                   # the only she / the only he in the scene
        pr = (pronouns or {}).get(c)
        if pr in ('she', 'he'): last.setdefault(pr, []).append(c)
    only = {pr: cs[0] for pr, cs in last.items() if len(cs) == 1}
    recent = {}
    out = []
    for part in parts:
        keep = {}
        def hide(m):
            k = f'\x00{len(keep)}\x00'; keep[k] = m.group(0); return k
        t = re.sub(r'["“][^"”]*["”]', hide, part)
        def sub(m):
            pr = m.group(1).lower(); c = recent.get(pr) or only.get(pr)
            return names[c] if c in names else m.group(0)
        t = re.sub(r'(?<![\w\x00])(she|he)(?![\w\x00])', sub, t, flags=re.I)
        for k, v in keep.items(): t = t.replace(k, v)
        mentioned = sorted(((m.start(), c) for c in cast if names.get(c) for m in re.finditer(r'(?<!\w)' + re.escape(names[c]) + r'(?!\w)', t, re.I)))
        for _, c in mentioned:
            pr = (pronouns or {}).get(c)
            if pr in ('she', 'he'): recent[pr] = c
        out.append(t)
    return out

def beats(text, interpret):
    """[(kind, clause, speaker_id or None)] in order. interpret(clause) -> the Director's reading of it."""
    out = []
    for c in clauses(text):
        lines = interpret(c).get('dialogue') or []
        out.append(('line', c, lines[0].get('speaker')) if lines else ('action', c, None))
    return out

def shot_texts(text, interpret, cast, names, focus=None, pronouns=None):
    """The coverage plan: [{'text': direction, 'people': [ids], 'role': 'wide'|'speaker'|'reaction'|'two_shot'|'action',
    'before': the beat(s) just before this shot (context, never re-acted)}]."""
    parts = name_people(clauses(text), list(cast), names, pronouns)
    bs = []
    for c in parts:
        lines = interpret(c).get('dialogue') or []
        bs.append(('line', c, lines[0].get('speaker')) if lines else ('action', c, None))
    if not bs: return []
    cast = list(cast)
    # someone who walks in on a later beat isn't in the shots before it (the wide shot can't show them yet)
    ENTERS = r'\b(?:walks? in|walks? into|walks? up|enters?|arrives?|comes? in|comes? into|steps? in|appears?|joins?|approaches|sneaks? up)\b'
    enters_at = {}
    for i, (_, c, _) in enumerate(bs):
        for x in cast:
            if x not in enters_at and names.get(x) and re.search(r'(?<!\w)' + re.escape(names[x]) + r'(?!\w)[^.;]*?' + ENTERS, c, re.I): enters_at[x] = i
    full_cast = list(cast)
    def here(i): return [x for x in full_cast if enters_at.get(x, 0) <= i]
    name = lambda c: names.get(c, 'them')
    cast = here(0) or full_cast; two = len(cast) >= 2
    everyone = ' and '.join(name(c) for c in cast) if cast else 'them'
    shots = [{'role': 'wide', 'people': cast, 'text': f'wide shot of {everyone}, establishing where everyone is. {bs[0][1]}'}]
    last_speaker, run = (bs[0][2] if bs[0][0] == 'line' else None), 0
    for idx in range(1, len(bs)):
        kind, c, sp = bs[idx]; nxt = bs[idx + 1] if idx + 1 < len(bs) else None
        cast = here(idx) or full_cast; two = len(cast) >= 2
        everyone = ' and '.join(name(x) for x in cast) if cast else 'them'
        if len(shots) >= MAX_SHOTS:                       # more beats than shots: the last shot carries the rest
            shots[-1]['text'] += f'. {c}'; continue
        if kind == 'line':
            sp = sp if sp in cast else (focus if focus in cast else (cast[0] if cast else None))
            run = run + 1 if sp == last_speaker else 0; last_speaker = sp
            listener = next((x for x in cast if x != sp), None)
            if listener and run % 2 == 0:
                shots.append({'role': 'speaker', 'people': [sp, listener],
                              'text': f'over the shoulder on {name(sp)}, {name(listener)} in the foreground. {c}'})
            else:
                shots.append({'role': 'speaker', 'people': [sp], 'text': f'close-up on {name(sp)}. {c}'})
            # the listener's reaction, when the moment ends on it or moves on to an action
            if listener and len(shots) < MAX_SHOTS and (nxt is None or nxt[0] == 'action'):
                shots.append({'role': 'reaction', 'people': [listener],
                              'text': f'close-up on {name(listener)}, alone in frame, reacting to what was just said; nobody speaks in this shot'})
        else:
            together = two and bool(TOGETHER.search(c))
            named = [x for x in cast if names.get(x) and re.search(r'(?<!\w)' + re.escape(names[x]) + r'(?!\w)', c, re.I)]
            # everyone stays in the scene: an action beat keeps the whole cast in frame unless it's about one person alone
            who = cast if (together or len(named) != 1 or not re.search(r'\balone\b', c, re.I)) else named
            shots.append({'role': 'two_shot' if together else 'action', 'people': who,
                          'text': f'waist-up two-shot of {everyone}. {c}' if (together or len(who) > 1) else f'waist-up shot of {name(who[0])}. {c}'})
    # what happened in the beat(s) just before each shot: given to it as context (state), so it knows who is where
    done = [b[1] for b in bs]
    b = 0
    for i, sh in enumerate(shots):
        if i == 0: sh['before'] = None; continue
        if sh['role'] == 'reaction': sh['before'] = done[b]                # reacting to the line just said
        else: b += 1; sh['before'] = done[b - 1] if b - 1 < len(done) else None
    return shots
