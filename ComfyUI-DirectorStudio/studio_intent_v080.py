#!/usr/bin/env python3
"""Director Studio V0.8 — cast-aware interpretation for ANY scene.

Re-uses the V0.7.39 interpreter for emotion / framing / camera / pacing / dialogue /
refusals, then resolves WHO from the project's own characters: names, the descriptors
you gave them ("the woman", "grandpa", "the chef"), gendered pronouns when they are
unambiguous, and group words ("both", "they", "everyone").
"""
from __future__ import annotations
import re
import director_intent_v0739 as di

PRONOUNS = {'she': ['she', 'her', 'hers', 'herself'], 'he': ['he', 'him', 'his', 'himself'], 'they': []}
DEFAULT_DESCRIPTORS = {'she': ['woman', 'girl', 'lady', 'female'], 'he': ['man', 'guy', 'boy', 'gentleman', 'male'], 'they': ['person']}
GROUP = r'\b(both|both of them|they|them|everyone|everybody|the two of them|together|each other|the group|all of them)\b'
MOTION_WORDS = r'\b(walk\w*|run\w*|turn\w*|danc\w*|jump\w*|sit\w*|stand\w*|enter\w*|leave\w*|leaves|exit\w*|approach\w*|step\w*|wave\w*|hug\w*|reach\w*|spin\w*|move\w*|cross\w*|look\w* (back|around|up|down)|kneel\w*|lean\w*)\b'

def vocab(characters):
    """word/phrase -> set(char ids). Explicit names/descriptors first; defaults only when unique."""
    table = {}
    def add(word, cid):
        w = ' '.join(word.lower().split())
        if w: table.setdefault(w, set()).add(cid)
    for c in characters:
        add(c['name'], c['id'])
        for part in c['name'].split():
            if len(part) > 2: add(part, c['id'])
        for d in c.get('descriptors', []): add(d, c['id'])
        for p in PRONOUNS.get(c.get('pronoun', 'they'), []): add(p, c['id'])
    for c in characters:   # default descriptors (woman/man...) only if no explicit owner exists
        for d in DEFAULT_DESCRIPTORS.get(c.get('pronoun', 'they'), []):
            if d not in table: add(d, c['id'])
            elif table[d] != {c['id']} and not any(d in (x.get('descriptors') or []) for x in characters):
                table[d].add(c['id'])
    return table

SPEECH_TO = (r'\b(tells?|telling|told|asks?|asking|asked|answers?|answering|answered|replies|replied|replying|reminds?|warns?|'
             r'promises?|thanks?|greets?|(says?|said|saying|whispers?|whispered|explains?|explained|admits?|admitted|calls? out|called out) to|'
             r'(yells?|yelled|shouts?|shouted|snaps?|snapped|laughs?|laughed|smiles?|smiled) at)\b')

# how a line is said (V0.9.20): "Grandpa angrily says", "Maya whispers", "she says, laughing," -> words for H3's voice line
DELIVERY = [
    (r'close to tears', 'close to tears'), (r'rais\w* (?:their|his|her|my) voice', 'raising their voice'),
    (r'whisper\w*', 'in a whisper'), (r'(?:shout|yell|scream)\w*', 'shouting'), (r'angr(?:il)?y|furious(?:ly)?', 'angrily'),
    (r'sad(?:ly)?', 'sadly'), (r'soft(?:ly)?|gentl[ey]', 'softly'), (r'quiet(?:ly)?', 'quietly'), (r'nervous(?:ly)?', 'nervously'),
    (r'excited(?:ly)?', 'excitedly'), (r'happ(?:il)?y|cheerful(?:ly)?', 'cheerfully'), (r'sarcastic(?:ally)?', 'sarcastically'),
    (r'laugh\w*', 'through laughter'), (r'(?:cry|crie|sob)\w*|tearful(?:ly)?|through tears', 'through tears'), (r'firm(?:ly)?', 'firmly'),
    (r'cold(?:ly)?', 'coldly'), (r'playful(?:ly)?|teasing(?:ly)?', 'playfully'), (r'warm(?:ly)?', 'warmly'),
    (r'(?:scared|afraid|fearful(?:ly)?)', 'fearfully'), (r'desperate(?:ly)?', 'desperately'), (r'hesitant(?:ly)?|hesitat\w*', 'hesitantly'),
    (r'proud(?:ly)?', 'proudly'), (r'tired(?:ly)?|wear(?:il)?y', 'wearily'), (r'shy(?:ly)?|timid(?:ly)?', 'shyly'),
]
DELIVERY_STRENGTH = {'subtle': r'\b(?:slightly|a little|a bit|barely|faintly|somewhat|almost)\b', 'strong': r'\b(?:very|really|extremely|so|deeply|totally|furiously)\b'}

def delivery_of(seg):
    """'very angrily says' -> 'very angrily'; 'whispers' -> 'in a whisper'; None when nothing is said about it."""
    seg = di.normalize(seg or '')
    for pat, how in DELIVERY:
        if re.search(r'(?<![\w])(?:' + pat + r')(?![\w])', seg):
            if re.search(DELIVERY_STRENGTH['strong'], seg) and how.endswith('ly'): return 'very ' + how
            if re.search(DELIVERY_STRENGTH['subtle'], seg) and how.endswith('ly'): return 'a little ' + how
            strong, subtle = re.search(DELIVERY_STRENGTH['strong'], seg), re.search(DELIVERY_STRENGTH['subtle'], seg)
            more = {'through tears': ('sobbing, through tears', 'close to tears'), 'through laughter': ('laughing hard', 'with a little laugh'),
                    'in a whisper': ('in a hushed whisper', 'softly, almost in a whisper'), 'shouting': ('shouting at the top of their voice', 'raising their voice')}
            if how in more and (strong or subtle): return more[how][0 if strong else 1]
            return how
    return None

def mentions(text, characters):
    """Ordered list of (pos, word, ids) found in text."""
    s = di.normalize(text)
    out = []
    for word, ids in vocab(characters).items():
        for m in re.finditer(r'(?<![\w])' + re.escape(word) + r'(?![\w])', s):
            out.append((m.start(), m.end(), word, frozenset(ids)))
    # prefer longest match at a position ('old man' over 'man')
    out.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    res = []; last_end = -1
    for st, en, w, ids in out:
        if st < last_end: continue
        res.append((st, w, ids)); last_end = en
    return s, res

def interpret(text, characters, *, previous_cast=None, focus=None, phase='PLAN'):
    d = di.interpret(text, {'phase': phase})
    unquoted = re.sub(r'["\u201c][^"\u201d]*["\u201d]', ' ', text)     # names inside dialogue are not cast
    s, ms = mentions(unquoted, characters)
    allids = [c['id'] for c in characters]
    assumptions = [a for a in d['assumptions'] if not a.startswith('No specific instruction recognized')]
    question = None
    # drop hallway-specific subject guesses from the base interpreter; recompute with the real cast
    resolved = []
    for pos, word, ids in ms:
        if len(ids) == 1: resolved.append((pos, next(iter(ids))))
        else:
            pick = focus if focus in ids else None
            if pick:
                resolved.append((pos, pick))
                assumptions.append(f"'{word}' could mean several people; read as {name_of(characters, pick)} (in focus).")
            else:
                question = f"Who do you mean by '{word}': " + ' or '.join(name_of(characters, i) for i in sorted(ids)) + '?'
    cast = []
    for _, cid in resolved:
        if cid not in cast: cast.append(cid)
    if re.search(GROUP, s):
        for cid in (previous_cast or allids):
            if cid not in cast: cast.append(cid)
    if re.search(r'\b(alone|by (her|him|them)self|on (her|his|their) own|solo|only)\b', s) and cast:
        cast = cast[:1]
        alone = True
    else:
        alone = False
    if not cast and question is None:
        cast = list(previous_cast or allids)
        if characters: assumptions.append('No one named; same people as the previous shot.' if previous_cast else 'No one named; everyone in the scene.')
    # subject of an emotion/reaction: the mention closest before the emotion word, else first mention
    subject = None
    if d['emotion'] or re.search(r'\breact|respon', s):
        epos = di.find_emotion(s)[1]
        if epos is None:
            m = re.search(r'\breact|respon', s); epos = m.start() if m else len(s)
        before = [cid for pos, cid in resolved if pos <= epos]
        mk = re.search(r'\b(make|let|have|get)\s+(\w+(\s\w+)?)', s)
        if mk:
            hit = [cid for pos, cid in resolved if mk.start(2) <= pos <= mk.end(2)]
            if hit: before = hit
        subject = before[-1] if before else (resolved[0][1] if resolved else (cast[0] if len(cast) == 1 else None))
    framing_subject = None
    if d['framing'] in ('close_up', 'over_shoulder', 'profile', 'closer', 'low_angle', 'high_angle'):
        m = re.search(r'\b(on|of|at|toward|towards|favou?ring)\s+(the\s+)?(\w+(\s\w+)?)', s)
        hit = [cid for pos, cid in resolved if m and m.start(3) <= pos <= m.end(3)] if m else []
        framing_subject = hit[0] if hit else (subject or (cast[0] if cast else None))
    dialogue = []
    for line in d['dialogue']:
        sp = None
        raw = d['text']; idx = raw.find(line['line'])
        if idx > 0:
            win, ms2 = mentions(raw[max(0, idx - 80):idx], characters)
            ids = [i for _, _, i in ms2 if len(i) == 1]
            # a name in an OBJECT position ("tells Grandpa", "walks to Grandpa", "smiles at Grandpa") is who is spoken to or
            # acted on, not the speaker: the speaker is the last name in a subject position
            def is_object(pos):
                head = win[max(0, pos - 24):pos]
                return bool(re.search(SPEECH_TO + r'\s+(the\s+)?$', head) or re.search(r'\b(to|towards?|at|with|beside|behind|for|from|past|near|hugs?|hugging|holds?|kisses?|watches?|sees?|meets?|joins?|follows?|faces?)\s+(the\s+)?$', head))
            subj = [i for pos, _, i in ms2 if len(i) == 1 and not is_object(pos)]
            if subj: sp = next(iter(subj[-1]))
        how = None
        if idx > 0:                                   # between the last name (or the previous line) and this line's quote
            lo = max(0, idx - 60)
            prev = max((raw.rfind(q, lo, idx - 1) for q in ('"', '\u201d')), default=-1)
            seg = raw[max(lo, prev + 1):idx]
            names_at = [m.end() for c in characters for m in re.finditer(r'(?i)\b' + re.escape(c['name']) + r'\b', seg)]
            seg = seg[max(names_at):] if names_at else seg[-40:]
            vm = list(re.finditer(r'(?i)\b(?:says?|said|asks?|asked|replies|replied|answers?|whispers?|whispered|shouts?|shouted|yells?|yelled|adds?|tells?\s+\w+)\b', seg))
            if vm:                                    # only the words right at the verb: "very angrily says", "says, laughing,"
                v = vm[-1]; before = ' '.join(seg[:v.start()].split()[-3:])
                seg = before + ' ' + seg[v.start():]
            how = delivery_of(seg)
        dialogue.append({'line': line['line'], 'speaker': sp, **({'how': how} if how else {})})
    # recasting: "replace Maya with Ava", "Ava instead of Maya", "swap Maya for Ava", "without Grandpa"
    replaced = {}
    def after(p0): return next(((p, c) for p, c in resolved if p > p0), None)
    for m in re.finditer(r'\b(replace|swap(?: out)?|recast)\b', s):
        a = after(m.end()); b = after(a[0]) if a else None
        if a and b and re.search(r'\b(with|for|by|as)\b', s[a[0]:b[0]]): replaced[a[1]] = b[1]
    for m in re.finditer(r'\binstead of\b', s):
        a = after(m.end()); b = [x for x in resolved if x[0] < m.start()]
        if a and b: replaced[a[1]] = b[-1][1]
    removed = set()
    for m in re.finditer(r'\b(without|no more|leave out|drop)\b', s):
        a = after(m.end())
        if a and a[0] - m.end() < 6: removed.add(a[1])
    if replaced or removed:
        base_cast = list(previous_cast or allids)
        new_cast = []
        for c in base_cast:
            c2 = replaced.get(c, c)
            if c2 not in removed and c2 not in new_cast: new_cast.append(c2)
        for old_c, new_c in replaced.items():
            if new_c not in new_cast and new_c not in removed: new_cast.append(new_c)
        others = [c for c in cast if c not in replaced and c not in removed and c not in new_cast]
        cast = new_cast + others
        for old_c, new_c in replaced.items():
            assumptions.append(f'{name_of(characters, new_c)} replaces {name_of(characters, old_c)}; everyone else stays.')
        for c in removed: assumptions.append(f'{name_of(characters, c)} is left out of this shot.')
        if subject in replaced: subject = replaced[subject]
        if subject in removed: subject = None
    # background: the place behind the people in their own photos, or a named/new place
    bg_people = bool(re.search(
        r'\b(background|backdrop|setting|scenery)s?\b[^.;!?]{0,40}\b(from|in|of|behind)\b[^.;!?]{0,30}\b(photo|picture|pic|image|video|reference|ref|selfie)s?\b|'
        r'\b(keep|use)\s+(the\s+|their\s+|his\s+|her\s+)?(own\s+|original\s+)?(background|backdrop|setting)s?\b|'
        r'\bsame (background|place|setting) as (in )?(the |their |his |her )?(photo|picture|image|video|reference)s?\b|'
        r'\bwherever (they|he|she) (was|were|is|are) (filmed|photographed|shot)\b', s))
    bg_place = bool(re.search(r'\b(new|different|change (the )?)(background|backdrop|location|place|setting)\b', s))
    # shot length in the movie: "a quick 2 second shot", "about 3 seconds", "use the whole take"
    duration = None
    m = re.search(r'\b(\d+(?:\.\d+)?)\s*-?\s*(?:s|sec|secs|second|seconds)\b', s)
    if m: duration = max(1.0, min(10.0, float(m.group(1))))
    elif re.search(r'\b(quick|short|brief) (shot|cut|clip|moment)\b', s): duration = 2.5
    elif re.search(r'\b(whole|full|entire) (take|shot|clip|length)\b|\blet it run\b', s): duration = 'full'
    d['duration'] = duration
    # instructions to the Director (background, recasting) are not actions for H3
    if d.get('note') and (bg_people or bg_place or replaced or removed):
        clauses = re.split(r'(?<=[,;.!?])\s+|\s+(?:and|then)\s+(?=(?:keep|use|replace|swap|without|change)\b)', d['note'])
        keep = [c for c in clauses if not re.search(
            r'\b(background|backdrop|setting|scenery|replace|swap|recast|instead of|without|leave out)\b', c, re.I)]
        d['note'] = ' '.join(keep).strip(' ,;')
    d.update(question=question, cast=cast, subject=subject, framing_subject=framing_subject, dialogue=dialogue, alone=alone,
             replaced=replaced, removed=sorted(removed), background_from_people=bg_people, background_place=bg_place,
             has_motion=bool(re.search(MOTION_WORDS, s)), assumptions=assumptions)
    d['reading'] = describe(d, characters)
    return d

def name_of(characters, cid):
    for c in characters:
        if c['id'] == cid: return c['name']
    return cid

def describe(d, characters):
    if d['action'] not in ('DIRECT', 'RETAKE'):
        return di.describe({**d, 'subject': None, 'framing_subject': None})
    n = lambda i: name_of(characters, i)
    parts = []
    if d['action'] == 'RETAKE': parts.append('New take of the same shot')
    if d['cast']: parts.append('in shot: ' + ', '.join(n(i) for i in d['cast']) + (' (alone)' if d.get('alone') else ''))
    if d['subject'] and d['emotion']: parts.append(f"{n(d['subject'])}: {d['emotion']} ({d['intensity']})")
    elif d['subject']: parts.append(f"focus on {n(d['subject'])}'s reaction")
    elif d['emotion']: parts.append(f"tone: {d['emotion']} ({d['intensity']})")
    if d['framing']: parts.append('framing: ' + d['framing'].replace('_', ' ') + (f" on {n(d['framing_subject'])}" if d['framing_subject'] else ''))
    if d['move']: parts.append('camera: ' + d['move'].replace('_', ' '))
    if d['pacing']: parts.append('pacing: ' + d['pacing'])
    if d['dialogue']: parts.append(f"{len(d['dialogue'])} line(s) of dialogue")
    if d.get('has_motion'): parts.append('movement in shot')
    return '; '.join(parts) + '.'
