"""Dialogue script -> shots that can be spoken naturally.

    Maya: How was the war, Grandpa?
    (Grandpa looks away.)
    Grandpa: Long. Longer than anyone tells you. We were young, we thought it would be over by Christmas.
    Maya (softly): I didn't know that.

Each spoken line becomes one shot when it fits (about 2.5 words per second, minus a breath, so ~10 words in a
5.2 s H3 take). Longer lines are split at sentence (then comma) boundaries into consecutive shots of the same speaker.
Stage directions in parentheses or on their own line become the action of the next shot. Delivery notes like
"(softly)" go with the line. Framing alternates like a real dialogue scene: the speaker is favoured over the other
person's shoulder; a shot with no line is a two-shot.
"""
from __future__ import annotations
import re

LINE = re.compile(r'^\s*([A-Za-z][\w .\'-]{0,40}?)\s*(?:\(([^)]{1,60})\))?\s*[:—-]\s+(.+)$')

def words(t): return len(re.findall(r"[\w']+", t))

def split_line(text, budget):
    """Split a long line into chunks of <= budget words, at sentence ends first, then commas, then words."""
    text = ' '.join(text.split())
    if words(text) <= budget: return [text]
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, cur = [], ''
    for snt in sentences:
        parts = [snt] if words(snt) <= budget else re.split(r'(?<=,)\s+', snt)
        for part in parts:
            if words(part) > budget:                                    # one very long clause: cut by words
                ws = part.split()
                for i in range(0, len(ws), budget):
                    piece = ' '.join(ws[i:i + budget])
                    if cur: chunks.append(cur); cur = ''
                    chunks.append(piece)
                continue
            if cur and words(cur) + words(part) > budget: chunks.append(cur); cur = part
            else: cur = (cur + ' ' + part).strip()
    if cur: chunks.append(cur)
    return [c.rstrip(',') for c in chunks]

def parse(script, characters, budget=10):
    """characters: list of {'id','name','descriptors'}. Returns (shots, problems). Each shot:
    {'speaker': id or None, 'line': str or None, 'delivery': str, 'action': str}."""
    import studio_intent_v080 as si
    names = {}
    for c in characters:
        for w in [c['name']] + list(c.get('descriptors') or []) + c['name'].split():
            if len(w) > 1: names.setdefault(w.lower(), c['id'])
    shots, problems, pending_action = [], [], []
    for raw in (script or '').splitlines():
        line = raw.strip()
        if not line: continue
        m = LINE.match(line)
        who = names.get(m.group(1).strip().lower()) if m else None
        if m and who:
            delivery = (m.group(2) or '').strip(); text = m.group(3).strip()
            for k, act in enumerate(re.findall(r'\(([^)]{1,120})\)', text)): pending_action.append(act.strip().rstrip('.'))
            text = re.sub(r'\s*\([^)]{1,120}\)\s*', ' ', text).strip()
            text = text.strip('"“”')
            for k, chunk in enumerate(split_line(text, budget)):
                shots.append({'speaker': who, 'line': chunk, 'delivery': delivery if k == 0 else '',
                              'action': '; '.join(pending_action) if k == 0 else 'continuing'})
                pending_action = []
        elif m and not who:
            problems.append(f'"{m.group(1).strip()}" is not one of your people; add them on the left or fix the name (line: {line[:60]})')
        else:
            pending_action.append(line.strip('()').rstrip('.'))
    if pending_action: shots.append({'speaker': None, 'line': None, 'delivery': '', 'action': '; '.join(pending_action)})
    return shots, problems

def directions(shots, characters):
    """Plain directions the Director already understands, one per shot."""
    name = {c['id']: c['name'] for c in characters}
    out = []
    speakers = [s['speaker'] for s in shots if s['speaker']]
    others = lambda sp: [c for c in dict.fromkeys(speakers) if c != sp]
    run = 0
    for i, s in enumerate(shots):
        run = run + 1 if (i and s['speaker'] and shots[i - 1]['speaker'] == s['speaker']) else 0
        if s['speaker'] is None:
            out.append(f"{s['action']}, both in a two-shot" if s['action'] else 'both of them in a two-shot'); continue
        sp = name[s['speaker']]; oth = others(s['speaker'])
        # alternate the angle while one person keeps talking, so the cut between their shots isn't a jump cut
        frame = (f'close-up on {sp}' if run % 2 else (f'over the shoulder on {sp}' if oth else f'medium shot on {sp}'))
        act = '' if s['action'] in ('', 'continuing') else s['action'] + '. '
        cont = 'continuing, ' if s['action'] == 'continuing' else ''
        both = f"{sp} and {name[oth[0]]}. " if oth else ''
        delivery = f" ({s['delivery']})" if s['delivery'] else ''
        out.append(f'{both}{act}{frame}. {cont}{sp}{delivery} says "{s["line"]}"')
    return out
