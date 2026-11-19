"""📍 V0.9.45 position setter: where each person is at the START of a shot (and, optionally, where they END), set by dragging
them on a picture of the frame. H3 placed people wrongly whenever a prompt left positions vague, so the positions you set are
turned into exact words: side of the frame, how near the camera, posture, which way they face, and how far apart they are.
They go to H3 in the prompt and to Qwen (as known facts it writes the shot from). Pure Python: the page sends
{'start': [{'who', 'x', 'y', 'posture', 'facing'}], 'end': [...]}, x 0 (left) to 1 (right), y 0 (top, far) to 1 (bottom, near).
"""
from __future__ import annotations
import re

POSTURES = {'standing': 'stands', 'sitting': 'sits', 'kneeling': 'kneels', 'crouching': 'crouches', 'leaning': 'leans',
            'walking': 'walks', 'lying_back': 'lies on {their} back, face up toward the ceiling',
            'lying_front': 'lies on {their} stomach, face down, {their} back facing up', 'lying_side': 'lies on {their} side'}
FACINGS = ('', 'camera', 'away', 'other', 'left', 'right')
MOMENTS = ('start', 'end')
THEIR = {'she': 'her', 'he': 'his'}


class PositionsError(ValueError): pass


def _num(v):
    try: x = float(v)
    except (TypeError, ValueError): raise PositionsError('POSITIONS_BAD_SPOT')
    return round(max(0.0, min(1.0, x)), 3)


def clean(raw, cast_names):
    """The page's positions -> checked {'start': [...], 'end': [...]} keyed by NAME (only people in the shot, each once), or None.
    cast_names: the people in this shot. Someone placed who isn't in the shot is an error, so nobody is silently dropped."""
    if not raw: return None
    if not isinstance(raw, dict): raise PositionsError('POSITIONS_BAD')
    out = {}
    for m in MOMENTS:
        rows = raw.get(m) or []
        if not isinstance(rows, list): raise PositionsError('POSITIONS_BAD')
        seen = []
        for r in rows[:2]:
            if not isinstance(r, dict): raise PositionsError('POSITIONS_BAD')
            who = str(r.get('who') or '').strip()
            if who not in cast_names: raise PositionsError(f'POSITIONS_FOR_SOMEONE_NOT_IN_THE_SHOT: {who[:40]} (add them to the shot, or take them off the frame)')
            if who in seen: continue
            posture = str(r.get('posture') or 'standing')
            if posture not in POSTURES: raise PositionsError('POSITIONS_BAD_POSTURE')
            facing = str(r.get('facing') or '')
            if facing not in FACINGS: raise PositionsError('POSITIONS_BAD_FACING')
            seen.append(who)
            on = ' '.join(re.sub(r'["“”<>{}]', '', str(r.get('on') or '')).split())[:50]       # V0.9.48 what they sit / lie / lean on
            if on and not re.search(r'[A-Za-z]', on): on = ''
            out.setdefault(m, []).append({'who': who, 'x': _num(r.get('x', 0.5)), 'y': _num(r.get('y', 0.6)), 'posture': posture, 'facing': facing,
                                          **({'on': on} if on else {})})
    return out or None


def side(x):
    if x < 0.2: return 'on the far left of the frame'
    if x < 0.4: return 'on the left of the frame'
    if x <= 0.6: return 'in the center of the frame'
    if x <= 0.8: return 'on the right of the frame'
    return 'on the far right of the frame'


def depth(y):
    if y < 0.35: return 'in the background, far from the camera'
    if y < 0.65: return 'in the middle of the room'
    return 'in the foreground, close to the camera'


def apart(a, b):
    dx, dy = abs(a['x'] - b['x']), abs(a['y'] - b['y'])
    d = (dx * dx + dy * dy) ** 0.5
    if d < 0.15: return 'side by side, close enough to touch'
    if d < 0.3: return "about an arm's length apart"
    if d < 0.55: return 'a few steps apart'
    return 'far apart, across the room from each other'


PREPS = re.compile(r'^(on|at|in|against|by|beside|next to|near|under|behind|in front of|across|inside|onto)\b', re.I)
def _on(on, posture):
    """'the bed' -> 'on the bed' (leaning: 'against the door'); words that start with a preposition are kept as typed."""
    return on if PREPS.match(on) else (('against ' if posture == 'leaning' else 'on ') + on)

def sentence(rows, moment, label=lambda n: n, pronoun=lambda n: 'they'):
    """One moment's rows -> words, e.g. 'At the start, Maya stands on the left of the frame in the foreground, close to the
    camera, facing Grandpa; Grandpa sits on the right of the frame in the background, facing the camera. They are a few steps
    apart.' label(name) -> how the prompt names them (e.g. '<Subject 2> (Maya)')."""
    if not rows: return ''
    names = [r['who'] for r in rows]; bits = []
    for r in rows:
        their = THEIR.get(pronoun(r['who']), 'their')
        verb = POSTURES.get(r.get('posture') or 'standing', POSTURES['standing']).format(their=their)   # V0.9.56 a row missing a field: the plain default
        face = {'camera': 'facing the camera', 'away': 'with ' + their + ' back to the camera', 'left': 'facing the left of the frame',
                'right': 'facing the right of the frame', '': ''}.get(r.get('facing') or '', '')
        if r.get('facing') == 'other':
            other = next((n for n in names if n != r['who']), None)
            face = f'facing {label(other)}' if other else 'facing the camera'
        if r.get('on'): verb = verb + ' ' + _on(r['on'], r.get('posture') or 'standing')                    # V0.9.48 "sits on the bed"
        bits.append(f"{label(r['who'])} {verb}{',' if (',' in verb or r.get('on')) else ''} {side(r['x'])}, {depth(r['y'])}" + (f', {face}' if face else ''))
    lead = 'At the start, ' if moment == 'start' else 'By the end, '
    text = lead + '; '.join(bits) + '.'
    if len(rows) == 2: text += f' They are {apart(rows[0], rows[1])}.'
    return text


def for_h3(pos, label=lambda n: n, pronoun=lambda n: 'they'):
    """-> (start sentence, end sentence) for the prompt."""
    pos = pos or {}
    return sentence(pos.get('start'), 'start', label, pronoun), sentence(pos.get('end'), 'end', label, pronoun)


# V0.9.48: the shot's own words must not fight the positions you set (H3 follows the action text): find the clashes
FAMILY = {'standing': 'standing', 'walking': 'standing', 'leaning': 'standing', 'sitting': 'sitting', 'kneeling': 'kneeling',
          'crouching': 'crouching', 'lying_back': 'lying', 'lying_front': 'lying', 'lying_side': 'lying'}
WORDS = {'standing': r'stands?|standing|stood', 'sitting': r'sits?|sitting|seated|sat', 'lying': r'lies|lying|lay|lays',
         'kneeling': r'kneels?|kneeling|knelt', 'crouching': r'crouch(?:es|ing)?|squat(?:s|ting)?'}
def clashes(text, pos):
    """The shot's text vs the START positions you set -> plain warnings (posture or side of the frame), checked in the first
    sentence that names each person (how the shot opens)."""
    out = []
    sents = [x for x in re.split(r'(?<=[.!?])\s+', re.sub(r'"[^"]*"', ' ', str(text or ''))) if x.strip()]
    for r in (pos or {}).get('start') or []:
        first = next((x for x in sents if re.search(r'\b' + re.escape(r['who']) + r'\b', x)), None)
        if not first: continue
        said = [fam for fam, w in WORDS.items() if re.search(r'\b(?:' + w + r')\b', first, re.I)]
        mine = FAMILY.get(r['posture'], 'standing')
        if said and mine not in said:
            out.append(f"The shot's text has {r['who']} {said[0]} at the start, but your positions have {r['who']} {mine}: H3 tends to follow the "
                       "text. Press ✨ Rewrite (Qwen writes from your positions) or change the text.")
        m = re.search(r'\b(?:on|to) the (left|right)\b', first, re.I)
        if m and ((m.group(1).lower() == 'left' and r['x'] > 0.6) or (m.group(1).lower() == 'right' and r['x'] < 0.4)):
            out.append(f"The shot's text puts {r['who']} on the {m.group(1).lower()}, but your positions have {r['who']} on the "
                       f"{'right' if r['x'] > 0.6 else 'left'}: press ✨ Rewrite or change the text.")
    return out

QWEN_HEAD = ("THE DIRECTOR SET WHERE EVERYONE IS (exact; write the shot from it): ")
def for_qwen(pos, pronoun=lambda n: 'they'):
    """The positions as known facts for Qwen's writing (start where they are; get them to the end positions)."""
    a, b = for_h3(pos, pronoun=pronoun)
    if not (a or b): return ''
    t = QWEN_HEAD + ' '.join(x for x in (a, b) if x)
    if a: t += ' Begin the shot with everyone exactly there.'
    if b: t += ' Write the movement that gets everyone to where they are by the end.'
    return t


# V0.9.49 📍 the setter starts from what the shot's own text says (plain rules, no model): you only fix what is wrong or vague
GUESS_POSTURE = [('lying_front', r'(?:lies|lying|lay|lays)\s+(?:face\s*down|on\s+(?:his|her|their)\s+(?:stomach|front|belly))'),
                 ('lying_side', r'(?:lies|lying|lay|lays)\s+on\s+(?:his|her|their)\s+side'),
                 ('lying_back', r'lies|lying|lay|lays'), ('sitting', WORDS['sitting']), ('kneeling', WORDS['kneeling']),
                 ('crouching', WORDS['crouching']), ('leaning', r'lean(?:s|ing)?|leant'), ('walking', r'walk(?:s|ing)?|pac(?:es|ing)'),
                 ('standing', WORDS['standing'])]
THINGS = ('bed', 'couch', 'sofa', 'chair', 'armchair', 'bench', 'stool', 'desk', 'table', 'floor', 'window', 'door', 'doorway', 'wall',
          'counter', 'kitchen counter', 'bar', 'stairs', 'steps', 'porch', 'railing', 'fireplace', 'lockers', 'locker', 'bathtub', 'sink',
          'mirror', 'dresser', 'car', 'tree', 'fence', 'ground', 'grass', 'rug', 'carpet', 'balcony', 'piano')
GUESS_ON = re.compile(r'\b(?:on|at|against|by|beside|next to|near|in)\s+the\s+(?:edge of the\s+)?(?:' + '|'.join(sorted(THINGS, key=len, reverse=True)) + r')\b', re.I)

def _sentences(text):
    return [x for x in re.split(r'(?<=[.!?])\s+', re.sub(r'"[^"]*"', ' ', str(text or ''))) if x.strip()]

def from_text(text, names):
    """The shot's text -> {'start': rows, 'known': {name: [what the text said]}, 'vague': [names]} for the people in the shot.
    Each person is read from the first sentence that names them (how the shot opens): posture, what they are on, the side of
    the frame, near or far, which way they face. Anything not said gets a plain default and counts as unknown; a person whose
    side AND distance are both unknown is 'vague' (📍? the text doesn't say where they are)."""
    names = [n for n in (names or []) if n][:2]
    sents = _sentences(text); rows, known, vague = [], {}, []
    for i, n in enumerate(names):
        other = next((o for o in names if o != n), None)
        first = next((x for x in sents if re.search(r'\b' + re.escape(n) + r'\b', x)), '')
        # only the words about this person: from their name to the next person's name
        seg = first
        k = re.search(r'\b' + re.escape(n) + r'\b', first)
        if k:
            seg = first[k.start():]
            if other:
                j = re.search(r'\b' + re.escape(other) + r'\b', seg[len(n):])
                if j: seg = seg[:len(n) + j.start()]
        said = []
        posture = 'standing'
        for pz, w in GUESS_POSTURE:
            if re.search(r'\b(?:' + w + r')\b', seg, re.I): posture = pz; said.append('posture'); break
        on = ''
        m = GUESS_ON.search(seg)
        if m: on = m.group(0).strip(); said.append('on')
        x = (0.7 if i else 0.3) if len(names) > 1 else 0.5
        side = re.search(r'\b(?:on|to|at)\s+the\s+(far\s+)?(left|right)\b|\b(left|right)\s+side\b|\b(center|centre|middle)\s+of\s+the\s+(?:frame|shot|room)\b', seg, re.I)
        if side:
            if side.group(4): x = 0.5
            else:
                lr = (side.group(2) or side.group(3)).lower(); far = bool(side.group(1))
                x = (0.1 if far else 0.25) if lr == 'left' else (0.9 if far else 0.75)
            said.append('side')
        y = 0.7
        if re.search(r'\b(?:foreground|close to the camera|near the camera|in front of the camera|toward(?:s)? the camera|up close)\b', seg, re.I): y = 0.82; said.append('distance')
        elif re.search(r'\b(?:background|far from the camera|back of the room|far end|in the distance|doorway|across the room)\b', seg, re.I): y = 0.3; said.append('distance')
        elif re.search(r'\bmiddle of the room\b', seg, re.I): y = 0.5; said.append('distance')
        facing = 'other' if other else 'camera'
        if re.search(r'\b(?:back to the camera|turned away|faces away|facing away|from behind)\b', seg, re.I): facing = 'away'; said.append('facing')
        elif re.search(r'\b(?:faces|facing|looks (?:in)?to|looking (?:in)?to|toward(?:s)?) the camera\b', seg, re.I): facing = 'camera'; said.append('facing')
        elif other and re.search(r'\b(?:faces|facing|looks at|looking at|turns to|toward(?:s)?)\s+' + re.escape(other) + r'\b', seg, re.I): facing = 'other'; said.append('facing')
        rows.append({'who': n, 'x': x, 'y': y, 'posture': posture, 'facing': facing, **({'on': on[:50]} if on else {})})
        known[n] = said
        if 'side' not in said and 'distance' not in said: vague.append(n)
    if len(rows) == 2 and 'side' not in known[names[0]] and 'side' in known[names[1]]:      # one side said: the other takes the other side
        rows[0]['x'] = 0.3 if rows[1]['x'] > 0.5 else 0.7
    if len(rows) == 2 and 'side' not in known[names[1]] and 'side' in known[names[0]]:
        rows[1]['x'] = 0.3 if rows[0]['x'] > 0.5 else 0.7
    return {'start': rows, 'known': known, 'vague': vague}
