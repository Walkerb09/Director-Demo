#!/usr/bin/env python3
"""V0.7.39 natural-language direction interpreter. No magic phrases.

interpret(text, context) turns ordinary filmmaker language into a structured
Direction: what ACTION the Director should take (plan / revise / retake / edit /
status / undo / approve ...) plus creative MODIFIERS (who reacts, emotion,
intensity, framing, camera move, pacing, dialogue lines, things to avoid).

Design rules
- Deterministic and offline: same text + same context -> same Direction.
- The user's own words also travel into the H3 prompt as a sanitized "director's
  note", because the H3 text encoder reads natural language. Structure is used for
  decisions and safety; it is not a whitelist of allowed sentences.
- Requests that would break Clean-Chain (generated frames/clips as H3 visual
  input) are split off and refused; the rest of the direction still goes through.
- Ambiguity is resolved from project context and stated in `reading` so the user
  always sees how they were understood. Approvals are never inferred from vague
  praise or hedged text.
"""
from __future__ import annotations
import re, unicodedata

ACTIONS = ('STATUS', 'UNDO', 'RESET', 'STOP', 'CONFIRM', 'DENY', 'APPROVE_TAKE', 'REJECT_TAKE',
           'RETAKE', 'EDIT', 'NEW_SCENE', 'DIRECT')   # DIRECT = plan/revise the next shot

CONTRACTIONS = {"don't": 'do not', "doesn't": 'does not', "didn't": 'did not', "can't": 'cannot',
    "won't": 'will not', "isn't": 'is not', "aren't": 'are not', "let's": 'let us', "it's": 'it is',
    "that's": 'that is', "what's": 'what is', "where's": 'where is', "he's": 'he is', "she's": 'she is',
    "i'm": 'i am', "i'd": 'i would', "you're": 'you are', "they're": 'they are', "shouldn't": 'should not',
    "wouldn't": 'would not', "there's": 'there is', "we're": 'we are', "gotta": 'got to', "gonna": 'going to',
    "wanna": 'want to', "kinda": 'kind of', "lemme": 'let me'}

PRESENTER = r"(she|her|hers|herself|presenter|interviewer|host|girl|woman|lady|reporter|the jersey|new york girl)"
GRANDPA = r"(he|him|his|himself|grandpa|grandfather|gramps|granddad|old man|older man|old guy|guy|gentleman|guest|man)"
BOTH = r"(both|them|they|each other|the two of them|everyone|both of them)"

# --- creative vocabularies ------------------------------------------------------------
EMOTIONS = [  # (key, regex, understated prompt phrase, strong phrase). Natural, never theatrical.
 ('laugh', r'\b(laugh\w*|crack(s|ing)? up|lol|burst\w* out laughing)\b', 'a genuine, unforced little laugh', 'a real, warm, open laugh'),
 ('chuckle', r'\b(chuckl\w*|giggl\w*|snicker\w*)\b', 'a quiet genuine chuckle', 'a delighted chuckle that grows into a laugh'),
 ('smile', r'\b(smil\w*|grin\w*|beaming)\b', 'a small genuine smile', 'a broad, unguarded smile'),
 ('smirk', r'\b(smirk\w*|mischiev\w*|sly|cheeky|knowing look)\b', 'a small knowing smirk', 'an openly mischievous grin'),
 ('surprise', r'\b(surpris\w*|shock\w*|caught off guard|taken aback|wow|stunned|startl\w*|gasp\w*)\b', 'a brief moment of honest surprise', 'clear, real surprise'),
 ('amazed', r'\b(amazed|amazement|awestruck|in awe|astonish\w*|full of wonder|wide-eyed)\b', 'quiet wonder', 'open-mouthed amazement'),
 ('moved', r'\b(emotional|moved|touched|tear\w*|cry|cries|crying|choke[sd]? up|sentimental|heartfelt|weep\w*)\b',
     'is quietly moved; the emotion shows in the eyes and breath, not theatrically', 'is visibly moved, eyes glistening, still natural'),
 ('heartbroken', r'\b(heartbr\w*|devastat\w*|grie\w*|crushed|despair\w*|sob\w*|bawl\w*|wail\w*|break(s|ing)? down|broke down|fall(s|ing)? apart)\b', 'a held-back heartbreak', 'raw, visible heartbreak, still human-scaled'),
 ('sad', r'\b(sad|sadly|sadness|saddened|wistful\w*|melanchol\w*|somber|sombre|gloomy|feel(s|ing)? (down|blue))\b', 'a wistful, slightly sad note', 'real sadness, held with dignity'),
 ('angry', r'\b(angr\w*|mad at|gets mad|furious\w*|rage|raging|enrag\w*|livid|seething|fuming|yell\w*|shout\w*|scream\w* at|explod\w*|los(es?|ing) (it|(his|her|their) temper))\b', 'controlled irritation tightening the jaw', 'real anger, voice and body tense, still grounded'),
 ('annoyed', r'\b(annoy\w*|irritat\w*|impatien\w*|exasperat\w*|fed up|eye ?roll\w*|rolls? (her|his|their) eyes)\b', 'mild, polite annoyance', 'clear exasperation'),
 ('scared', r'\b(scared|scary|scares?|afraid|fear|fearful\w*|terrif\w*|frighten\w*|panic\w*|horrif\w*|scream\w*|shriek\w*)\b', 'a flicker of fear', 'genuine fear, breath quick, eyes wide'),
 ('nervous', r'\b(nervous\w*|anxious\w*|anxiety|worried|worries|worrying|uneasy|jittery|on edge|fidget\w*)\b', 'slight nervousness, a small fidget', 'visible nerves, fidgeting, shallow breath'),
 ('confused', r'\b(confus\w*|puzzl\w*|baffl\w*|bewilder\w*|perplex\w*|(looks?|seems?|feels?) lost|unsure)\b', 'a brief puzzled look', 'open confusion, brow furrowed'),
 ('curious', r'\b(curious\w*|intrigu\w*|interested|inquisitive|lean\w* in)\b', 'quiet curiosity, leaning in a little', 'eager curiosity'),
 ('relieved', r'\b(relieved|relief|phew|sigh\w* of relief)\b', 'a small exhale of relief', 'obvious, full-body relief'),
 ('hopeful', r'\b(hopeful|hopefulness|hoping|optimis\w*|expectant)\b', 'a quiet, hopeful look', 'bright, open hope'),
 ('determined', r'\b(determin\w*|resolute|steely|defian\w*|stubborn)\b', 'quiet resolve', 'fierce determination'),
 ('proud', r'\b(proud|pride)\b', 'quiet pride', 'open pride'),
 ('grateful', r'\b(gratef\w*|thankful|thank\w* (her|him|them|you)|appreciat\w*)\b', 'quiet gratitude', 'deep, open gratitude'),
 ('loving', r'\b(in love|lovingly|adoring\w*|adores|longing|smitten|besotted|loving (look|gaze|eyes))\b', 'soft, loving attention', 'unguarded adoration'),
 ('flirty', r'\b(flirt\w*|coy|seductiv\w*|charming|wink\w*)\b', 'a light, flirtatious look', 'playful, confident flirting'),
 ('embarrassed', r'\b(embarrass\w*|shy|bashful|blush\w*|sheepish|flustered)\b', 'a slightly bashful, endearing reaction', 'a clearly embarrassed but good-natured reaction'),
 ('guilty', r'\b(guilt\w*|ashamed|remorse\w*|regret\w*|apologetic|sorry)\b', 'a guilty glance away', 'heavy, visible guilt'),
 ('jealous', r'\b(jealous\w*|envious|envy)\b', 'a flicker of jealousy', 'open, simmering jealousy'),
 ('disgusted', r'\b(disgust\w*|gross\w*|repuls\w*|revolt\w*|grimac\w*)\b', 'a small grimace of distaste', 'clear disgust'),
 ('bored', r'\b(bored|boring|uninterested|indifferen\w*|unimpressed|yawn\w*)\b', 'mild disinterest', 'obvious boredom'),
 ('tired', r'\b(tired|exhaust\w*|sleepy|weary|drained|worn out)\b', 'a little weariness', 'deep exhaustion'),
 ('calm', r'\b(calm\w*|peaceful|serene\w*|relaxed|at ease|contented)\b', 'easy calm', 'deep, settled peace'),
 ('excited', r'\b(excit\w*|thrill\w*|pumped|hyped|ecstatic|elated)\b', 'contained excitement', 'bubbling, can-not-sit-still excitement'),
 ('delighted', r'\b(delight\w*|happy|happi\w*|joy\w*|cheerful|glee\w*)\b', 'genuine delight', 'unmistakable joy'),
 ('thoughtful', r'\b(thoughtful|pensive|ponder\w*|reflect\w*|contemplat\w*|lost in thought|deep in thought|mull\w*)\b', 'a thoughtful pause, searching for words', 'a long, thoughtful pause'),
 ('nostalgic', r'\b(nostalgi\w*|reminisc\w*|memor\w*|remember\w*|old days)\b', 'a nostalgic, faraway look as a memory surfaces', 'deep nostalgia, lost in a memory for a moment'),
 ('skeptical', r'\b(skeptic\w*|sceptic\w*|doubt\w*|unconvinced|raise[sd]? an eyebrow|suspicious)\b', 'a mildly skeptical, amused look', 'an openly skeptical look'),
 ('serious', r'\b(serious|earnest|sincere|solemn)\b', 'an earnest, sincere moment', 'a fully serious, weighty moment'),
 ('warm', r'\b(warm\w*|tender\w*|affection\w*|sweet)\b', 'warmth and affection between them', 'deep warmth and affection'),
 ('awkward', r'\b(awkward\w*|uncomfortable|cringe\w*)\b', 'a small, human awkward beat', 'a clearly awkward silence'),
 ('funny', r'\b(funn\w*|humou?r\w*|joke\w*|joking|witty|playful|tease|teasing|silly|goofy)\b', 'a playful, humorous exchange', 'an openly funny, playful exchange'),
]

def find_emotion(s):
    """Earliest emotion mentioned in the (normalized) text -> (key, position) or (None, None)."""
    best = None
    for key, pat, *_ in EMOTIONS:
        m = re.search(pat, s)
        if m and (best is None or m.start() < best[1]): best = (key, m.start())
    return best or (None, None)

STRONG = (r'\b(big|huge|really|very|a lot|so much|way more|much more|over the top|intense|intensely|strong(ly)?|full|extremely|incredibly|'
          r'utterly|completely|totally|absolutely|wildly|deeply|uncontrollabl\w*|hysteric\w*|overwhelm\w*|full[- ]blown|all out|explosive\w*)\b')
# V0.9.35: words that are intense in themselves ("she is furious", "he sobs") mean the strong step without a "very"
INTENSE = (r'\b(furious\w*|livid|enrag\w*|rage|raging|seething|scream\w*|shriek\w*|yell\w*|shout\w*|explod\w*|sob\w*|bawl\w*|wail\w*|'
           r'devastat\w*|break(s|ing)? down|broke down|fall(s|ing)? apart|terrif\w*|horrif\w*|panic\w*|ecstatic|elated|overjoyed|'
           r'mortified|stunned|hysteric\w*|in tears|bursts? into tears|heartbr\w*|distraught|frantic\w*)\b')
SUBTLE = r'\b(a little|a bit|slight(ly)?|subtle|subtly|small|tiny|gentle|gently|barely|hint of|touch of|soft(ly)?)\b'

FRAMINGS = [  # order matters: more specific first
 ('over_shoulder', r'\b(over[- ]the[- ]shoulder|ots|over (his|her) shoulder)\b'),
 ('close_up', r'\b(extreme close|close[- ]?up|closeup|tight shot on|face shot|on (his|her) face|single on)\b'),
 ('closer', r'\b(closer|tighter (shot|framing|frame|angle)|move in|get in close|more intimate|medium close|waist[- ]up|frame tighter)\b'),
 ('wider', r'\b(wider|wide shot|pull back|pull out|full body|establishing|show more of the (hallway|room|space)|step back)\b'),
 ('profile', r'\b(profile|side angle|from the side)\b'),
 ('reverse', r'\b(reverse (angle|shot)|other side|opposite angle)\b'),
 ('low_angle', r'\b(low angle|from below|looking up at)\b'),
 ('high_angle', r'\b(high angle|from above|looking down)\b'),
 ('new_angle', r'\b(another angle|different angle|new angle|change (the )?angle|other angle|switch (the )?angle|angle change|different shot|another shot type)\b'),
 ('same', r'\b(same (framing|angle|shot|composition)|keep the (framing|angle|composition))\b'),
]
MOVES = [
 ('push_in', r'\b(push(es|ing)? in|dolly in|slow(ly)? (zoom|push)|creep in)\b'),
 ('pull_out', r'\b(pull(s|ing)? (back|out) slowly|dolly out)\b'),
 ('handheld', r'\b(hand[- ]?held|shaky|documentary camera|run and gun)\b'),
 ('static', r'\b(static|locked off|tripod|still camera|do not move the camera|no camera movement)\b'),
 ('pan', r'\b(pan(s|ning)?|whip)\b'),
]
DEFER_RX = r'\b(whatever you think( is best)?|your call|you decide|up to you|surprise me|do your thing|director.?s choice|what would you do|you pick|i trust you|dealer.?s choice|do what you think( is best)?|(use your )?best judge?ment|whatever is best|what you think is best)\b'
PACING_SLOW = r'\b(give (it|them|him|her) (some |a little |more )?(time|space|room)|let (it|the moment|them) breathe|breathe|slow(er|ly| down| it down)?|take (their|its|his|her) time|linger|longer pause|more pause|hold (the|that) (moment|beat)|unhurried|patient|sit (in|with) (it|the moment)|beat of silence|silence)\b'
PACING_FAST = r'\b(faster|quicker|speed (it )?up|pick up the pace|snappier|more energy|tighter pacing|keep it moving|less (dead air|silence|pause))\b'

FORBIDDEN_VISUAL_SIMPLE = re.compile(
 r'\b(last|final|end(ing)?) frame (of|from) (the )?(last|previous|shot|clip|take)|'
 r'\b(use|feed|plug)\b[^.;!?]{0,40}\b(shot|clip|video|take|footage) ?\d*\b[^.;!?]{0,20}\b(as|for) (a |the )?(ref|reference|input|conditioning|start)|'
 r'\bseamless(ly)? (continue|continuation) from (the )?(end|last frame)|'
 r'\bimage[- ]to[- ]video from\b|\bextend (the )?(last|previous) (clip|shot|video)\b|'
 r'\b(use|using|start(ing)?|begin(ning)?|feed|plug|condition(ed)?|ref(erence)?|continue|from|off)\b[^.;!?]{0,25}\b(last|final|end(ing)?|closing) (frame|still)s?\b|'
 r'\b(last|final|end(ing)?|closing) (frame|still)s?\b[^.;!?]{0,25}\b(as|for) (a |the )?(ref|reference|input|start|starting|first|conditioning)\b|'
 r'\b(frame ?grab|screen ?(shot|grab)|still|snapshot)s? (of|from) (the )?(last|previous|earlier|generated|shot|clip|take|video)\b', re.I)

def normalize(text: str) -> str:
    t = unicodedata.normalize('NFKC', text).replace('’', "'").replace('‘', "'")
    t = ''.join(ch for ch in t if ch == '\n' or ord(ch) >= 32)
    low = ' '.join(t.lower().split())
    for a, b in CONTRACTIONS.items():
        low = re.sub(r'(?<![\w])' + re.escape(a) + r'(?![\w])', b, low)
    return low

def _has(p, s):
    return re.search(p, s, re.I) is not None

def _first_subject(s: str):
    """Earliest-mentioned person, used as the focus subject."""
    hits = []
    for who, pat in (('presenter', PRESENTER), ('grandpa', GRANDPA), ('both', BOTH)):
        m = re.search(r'\b' + pat + r'\b', s)
        if m: hits.append((m.start(), who))
    hits.sort()
    return hits[0][1] if hits else None

def _subject_after(s, verbs):
    """Subject that directly follows/precedes a verb cue: 'make her laugh', 'grandpa laughs'."""
    # Prefer "make/let/have <subj>" constructions, then "<subj> <verb>", then earliest mention.
    m = re.search(r'\b(make|let|have|get|show|see|want|need|give)\s+(' + PRESENTER + '|' + GRANDPA + '|' + BOTH + r')\b', s)
    if m:
        w = m.group(2)
        return 'presenter' if re.fullmatch(PRESENTER, w) else 'grandpa' if re.fullmatch(GRANDPA, w) else 'both'
    for who, pat in (('presenter', PRESENTER), ('grandpa', GRANDPA), ('both', BOTH)):
        if re.search(r'\b' + pat + r'\s+(\w+\s+){0,2}' + verbs, s): return who
    m = re.search(r'\b(on|at|of|toward|towards|to|favou?ring)\s+(the\s+)?(' + PRESENTER + '|' + GRANDPA + r')\b', s)
    if m:
        w = m.group(3)
        return 'presenter' if re.fullmatch(PRESENTER, w) else 'grandpa'
    return _first_subject(s)

def sanitize_note(text: str, removed_spans) -> str:
    t = unicodedata.normalize('NFKC', text)
    for span in sorted(removed_spans, key=lambda x: -x[0]):
        t = t[:span[0]] + ' ' + t[span[1]:]
    t = ''.join(ch for ch in t if ord(ch) >= 32)
    t = t.replace('"', "'").replace('\\', ' ')
    t = ' '.join(t.split()).strip(' ,;')
    if len(t) <= NOTE_MAX: return t
    cut = t[:NOTE_MAX]                                         # never mid-word (V0.9.12: a long direction ended in "sto")
    end = max(cut.rfind('. '), cut.rfind('; '), cut.rfind(', '))
    return (cut[:end] if end > NOTE_MAX // 2 else cut.rsplit(' ', 1)[0]).rstrip(' ,;')

NOTE_MAX = 2800       # one shot's direction (V0.9.34, was 1600): MiniMax's guide wants 350-500 words (~2500 characters) for the shot description;
                     # H3's whole prompt is at most 7000 and the Director sends <= 6800 (too long with your references -> said plainly, never cut)
TEXT_MAX = 3500       # what you may type for one shot (V0.9.34, was 2000)

def interpret(text: str, context: dict | None = None) -> dict:
    """context keys (all optional): phase ('PLAN'|'CAPTURE'|'ACCEPTED'), next_shot,
    beat_listener ('presenter'|'grandpa'), pending_confirmation (bool)."""
    ctx = context or {}
    if not isinstance(text, str) or not text.strip():
        raise ValueError('EMPTY_DIRECTION')
    if len(text) > TEXT_MAX:
        raise ValueError('DIRECTION_TOO_LONG')
    raw = text
    s = normalize(text)
    words = s.split()
    d = {'text': raw, 'normalized': s, 'action': None, 'secondary': [], 'subject': None,
         'emotion': None, 'intensity': 'normal', 'framing': None, 'framing_subject': None,
         'move': None, 'pacing': None, 'dialogue': [], 'avoid': [], 'standing': False,
         'edit': None, 'refused': [], 'note': '', 'assumptions': [], 'reading': ''}

    # ---- Clean-Chain refusals (split off, never silently dropped) ----
    removed = []
    for m in FORBIDDEN_VISUAL_SIMPLE.finditer(raw):
        if True:
            removed.append(m.span())
            d['refused'].append({'part': m.group(0).strip(),
                'reason': 'Generated frames/clips are never used as H3 visual input (Clean-Chain). '
                          'Continuity is carried as text from reviewed shots; visuals come only from your original photos and videos.'})
    if re.search(r'\b(turbo|4[- ]?step|fast mode|lower quality|fp8)\b', s):
        d['refused'].append({'part': 'Turbo / reduced-precision model',
            'reason': 'Full BF16 H3 stays: Turbo caused visible identity drift in your earlier tests.'})

    # ---- dialogue in quotes (directed, never a verified transcript) ----
    for m in re.finditer(r'["“]([^"”]{2,200})["”]', raw):
        before = normalize(raw[max(0, m.start() - 60):m.start()])
        hits = [(mm.start(), who) for who, pat in (('presenter', PRESENTER), ('grandpa', GRANDPA))
                for mm in re.finditer(r'\b' + pat + r'\b', before)]
        d['dialogue'].append({'line': sanitize_note(m.group(1), []), 'speaker': max(hits)[1] if hits else None})

    # ---- standing preferences ----
    d['standing'] = _has(r'\b(from now on|going forward|always|for (the rest|every shot|all (the )?(future|next) shots)|every shot|all shots|in general|as a rule)\b', s)

    # ---- avoid list (explicit negations) ----
    for m in re.finditer(r'\b(do not|no more|no|without|never|avoid|stop|less)\s+((?:(?!and\b|but\b|then\b)[a-z\-]+\s?){1,4})', s):
        phrase = (m.group(1) + ' ' + m.group(2)).strip()
        if m.start() == 0 and m.group(1) == 'no' and len(words) <= 3: continue
        if re.match(r'(do not|never) (do anything|change anything|worry)', phrase): continue
        d['avoid'].append(phrase)

    # ---- action detection (ordered by priority) ----
    short = len(words) <= 6
    hedge = _has(r'\b(maybe|might|not sure|i guess|kind of|sort of|perhaps|what if|could we|should we)\b|\?', s)
    but = _has(r'\b(but|except|although|though|however)\b', s)
    has_creative = False

    if ctx.get('pending_confirmation') and short and _has(r'^(yes|yeah|yep|yup|sure|confirm(ed)?|do it|go ahead|ok(ay)?|approved?|correct|that is right|please do)\b', s):
        d['action'] = 'CONFIRM'
    elif ctx.get('pending_confirmation') and short and _has(r'^(no|nope|nah|cancel|never ?mind|do not|wait)\b', s):
        d['action'] = 'DENY'
    elif _has(r'^(undo|revert|scratch that|never ?mind( that)?|take that back|go back to (how it was|what we had|before|the previous( one)?))\b|\b(undo (that|the last)|revert (that|the last))\b', s):
        d['action'] = 'UNDO'
    elif _has(r'\b(start (over|fresh)|reset (the )?(plan|shot|direction)|back to (the )?(original|default) plan|clear (the|my) (notes|directions))\b', s):
        d['action'] = 'RESET'
    elif _has(r'^(stop|hold( on| up)?|wait|pause|freeze)\b', s) and short:
        d['action'] = 'STOP'
    elif _has(r'\b(where are we|status|what do we have|what is the state|recap|catch me up|what happened so far|summary of the project|show me the project|what is (planned|next)|what are you planning|state of (the )?(film|project))\b', s) and not _has(r'\b(make|add|cut|trim|try)\b', s):
        d['action'] = 'STATUS'
    elif _has(r'\b(new scene|different (scene|location|setting)|new (location|setting|character|actor)|another scene|change (the )?(location|setting)|go outside|new set)\b', s):
        d['action'] = 'NEW_SCENE'

    # editing language
    edit = _parse_edit(s)
    if d['action'] is None and edit:
        d['action'] = 'EDIT'; d['edit'] = edit

    phase = ctx.get('phase')
    approve_cue = _has(r'\b(approve[ds]?|accept(ed)?|keep (it|that|this|the take|shot \d+)|that is the one|this is the one|good take|great take|nailed it|love it|i like (it|that|this)|looks (good|great)|that works|use (it|that|this) (one|take)|perfect|print it|it is good)\b', s)
    reject_cue = _has(r'\b(reject|bad take|do not like (it|that|this)|did not like|hate (it|that)|that is wrong|scrap (it|that|this)|throw (it|that) out|redo|reshoot|re-?shoot|not good|no good|try (it )?again|another take|one more take|again)\b', s)
    if d['action'] is None and phase == 'CAPTURE':
        if approve_cue and not hedge and not but and not reject_cue:
            d['action'] = 'APPROVE_TAKE'
        elif reject_cue:
            d['action'] = 'REJECT_TAKE'
        elif approve_cue and (hedge or but):
            d['assumptions'].append('Praise with a reservation is not treated as approval.')
    if d['action'] is None and _has(r'\b(another take|one more take|try (it |that )?again|retake|re-?roll|new seed|different seed|same (thing|shot|beat) (but )?(different|again)|run it again|regenerate)\b', s):
        d['action'] = 'RETAKE'

    if re.search(r'\b(from|at) the (end|last (frame|moment|second)s?) of\b|\bpick up (where|from where) (it|we|the last)', s) and not d['refused']:
        d['assumptions'].append('Continuing the story from where the last reviewed shot left off, carried as text; '
                                'its frames are never fed to H3.')
    # ---- creative modifiers (parsed for DIRECT/RETAKE/REJECT and recorded otherwise) ----
    cs = re.sub(DEFER_RX, ' ', s)   # 'whatever you THINK' is not an emotion
    d['emotion'] = find_emotion(cs)[0]
    if _has(STRONG, s): d['intensity'] = 'strong'
    elif _has(SUBTLE, s): d['intensity'] = 'subtle'
    elif d['emotion'] and _has(INTENSE, cs): d['intensity'] = 'strong'
    react = _has(r'\b(react\w*|respond\w*|response|reaction)\b', s)
    for key, pat in FRAMINGS:
        if _has(pat, s):
            d['framing'] = key; break
    m = re.search(r'\bcut (away |over )?to\s+(the\s+)?(' + PRESENTER + '|' + GRANDPA + r')\b', s)
    if m and d['framing'] is None:
        d['framing'] = 'close_up'
    for key, pat in MOVES:
        if _has(pat, s) and not re.search(r'\b(no|without|do not|never)\s+(\w+\s+){0,2}' + pat.strip('\\b'), s):
            d['move'] = key; break
    if _has(PACING_FAST, s): d['pacing'] = 'faster'
    elif _has(PACING_SLOW, re.sub(r'\bslow(ly)?\s+(push|zoom|dolly|pan|pull|creep|tilt)', ' ', cs)): d['pacing'] = 'slower'
    if d['framing'] in ('close_up', 'over_shoulder', 'profile', 'closer', 'low_angle', 'high_angle'):
        d['framing_subject'] = _subject_after(s, r'(close|face|shot|angle|shoulder|profile)')
    if d['emotion'] or react:
        d['subject'] = _subject_after(s, r'(react|laugh|smile|cry|respond|chuckle|grin|look|pause|think|blush)\w*')
    prop = _has(r'\b(mic|microphone)\b', s)
    has_creative = any([d['emotion'], react, d['framing'], d['move'], d['pacing'], d['dialogue'], prop,
                        d['avoid'] and d['action'] is None])
    personal = d['emotion'] in ('laugh', 'chuckle', 'smile', 'surprise', 'embarrassed', 'skeptical', 'thoughtful', 'nostalgic', 'proud')
    if not d['subject'] and (react or personal):
        listener = ctx.get('beat_listener')
        if listener:
            d['subject'] = listener
            d['assumptions'].append(f'No one named, so the reaction goes to the listener in this beat ({listener}).')
    if d['framing'] in ('close_up', 'over_shoulder', 'profile') and not d['framing_subject']:
        d['framing_subject'] = d['subject'] or ctx.get('beat_speaker') or 'grandpa'
        d['assumptions'].append(f"Framing favors {d['framing_subject']} (no one named).")
    d['prop'] = None
    if prop:
        if _has(r'\b(hand|give|gives|pass|passes|offer|offers|take|takes|grab|grabs)\b[^.]{0,30}\b(mic|microphone)\b|\b(mic|microphone)\b[^.]{0,20}\b(to him|to her|over)\b', s):
            d['prop'] = 'handoff'
            d['assumptions'].append('Microphone handoffs failed in earlier H3 tests; it is directed softly and must be checked on review.')
        else:
            d['prop'] = 'mention'

    defer = _has(DEFER_RX, s)
    cont = _has(r'\b(continue|keep (going|rolling|it going)|carry on|go on|next (shot|beat|scene beat|part|moment)|what happens next|move on|proceed|onward|keep the (scene|interview|conversation) going|let us keep|more of (this|the scene)|and then)\b', s) or s in ('next', 'go', 'more', 'again')

    if d['action'] is None:
        if has_creative or defer or cont:
            d['action'] = 'DIRECT'
        elif d['refused'] and not has_creative:
            d['action'] = 'DIRECT'
        else:
            # Unrecognized but non-empty: treat as a free creative note for the next beat.
            d['action'] = 'DIRECT'
            d['assumptions'].append('No specific instruction recognized; your words are passed to H3 as a creative note for the next beat.')
    d['defer'] = defer
    d['continue'] = cont
    if d['action'] in ('DIRECT', 'RETAKE', 'REJECT_TAKE') or (d['action'] == 'APPROVE_TAKE' and has_creative):
        spans = list(removed)
        d['note'] = sanitize_note(raw, spans) if not defer or has_creative else ''
        if d['note'] and not re.search(r'[a-z]', d['note'], re.I): d['note'] = ''
    d['reading'] = describe(d)
    return d

def _parse_edit(s: str):
    """Recognize editorial instructions on the working film. Returns dict or None."""
    if re.search(r'\bcut (away |over )?to\b', s) and not re.search(r'\b(the cut|edit|trim|timeline)\b', s):
        return None   # 'cut to grandpa' = a framing request for the next shot
    e = None
    m = re.search(r'\b(add|put|insert|append|include|drop|place)\s+(shot\s*|take\s*)?(\d+|it|this|that|the new (shot|take)|the latest (shot|take))\s+(in|into|to|onto|on)\s+(the\s+)?(film|edit|timeline|cut|sequence|movie|working film)\b', s)
    if m:
        n = m.group(3)
        e = {'op': 'APPEND', 'shot': int(n) if n.isdigit() else None}
        return e
    if re.search(r'\b(remove|drop|take out|pull)\s+(shot\s*)?(\d+)\s+(from|out of)\s+(the\s+)?(film|edit|timeline|cut)\b', s):
        n = int(re.search(r'\b(remove|drop|take out|pull)\s+(shot\s*)?(\d+)', s).group(3))
        return {'op': 'REMOVE', 'shot': n}
    if re.search(r'\b(render|export|show me|play|build|make)\s+(me\s+)?(the\s+)?(current\s+|working\s+)?(film|edit|cut|movie|sequence|timeline)\b', s):
        return {'op': 'RENDER'}
    if re.search(r'\b(my (cut|edit|version|choice|trim)|mine|the one i (picked|chose)|f\s?42|frame 42)\b', s) and re.search(r'\b(use|go back|switch|restore|bring back|put back|revert|prefer|want)\b', s):
        return {'op': 'SET_ENTRY', 'value': 42, 'shot': 2, 'basis': 'HUMAN_DIRECTED', 'boundary': None}
    if re.search(r"\b(the director.?s|your|the machine.?s|the auto(matic)?) (cut|edit|version|choice|trim)\b|\bf\s?36\b", s) and re.search(r'\b(use|go back|switch|restore|bring back|keep|revert|prefer|want)\b', s):
        return {'op': 'SET_ENTRY', 'value': 36, 'shot': 2, 'basis': 'HUMAN_DIRECTED', 'boundary': None}
    m = re.search(r'\b(?:start|enter|begin|cut in(?:to)?|come in)\s+(?:shot\s*(\d+)\s+)?(?:at|on|from)\s+(?:frame\s*|f\s?)(\d{1,3})\b', s) or \
        re.search(r'\bshot\s*(\d+)\s+(?:should\s+)?(?:start|begin)s?\s+(?:at|on|from)\s+(?:frame\s*|f\s?)(\d{1,3})\b', s)
    if m:
        return {'op': 'SET_ENTRY', 'value': int(m.group(2)), 'shot': int(m.group(1)) if m.group(1) else None,
                'basis': 'HUMAN_DIRECTED', 'boundary': None}
    edit_context = re.search(r'\b(cut|edit|trim|transition|timeline|entry|the join|splice)\b', s)
    tighten = re.search(r'\b(tighten|tighter|trim|shorten|shave|snappier|quicker cut|cut (it )?(in )?(later|shorter)|lose (some|a few) frames|come in later|less (dead air|lead[- ]in))\b', s)
    loosen = re.search(r'\b(loosen|looser|longer (lead[- ]in|cut)|let (the cut|it) breathe|come in earlier|cut (it )?(in )?earlier|start (it |shot \d+ )?earlier|more (lead[- ]in|room before))\b', s)
    shot_ref = re.search(r'\bshot\s*(\d+)\b', s)
    if (tighten or loosen) and (edit_context or re.search(r'\b(tighten|trim|shave|shorten|loosen)\b', s)):
        m = re.search(r'\b(\d{1,2})\s*frames?\b', s)
        secs = re.search(r'\b(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?)\b', s)
        half = re.search(r'\bhalf a second\b', s)
        amount = int(m.group(1)) if m else int(round(float(secs.group(1)) * 24)) if secs else 12 if half else None
        sign = 1 if tighten else -1
        return {'op': 'NUDGE_ENTRY', 'delta': None if amount is None else sign * amount, 'direction': sign,
                'shot': int(shot_ref.group(1)) if shot_ref else None,
                'basis': 'HUMAN_DIRECTED' if amount is not None else 'DIRECTOR_ROUTINE_WITHIN_DELEGATED_BOUNDS'}
    if re.search(r'\b(fix|improve|clean up|smooth(en)?|better) (the )?(cut|edit|transition|join)\b', s):
        return {'op': 'AUTO_SELECT', 'shot': int(shot_ref.group(1)) if shot_ref else None}
    return e

SUBJECT_WORD = {'presenter': 'the presenter', 'grandpa': 'Grandpa', 'both': 'both of them'}

def describe(d) -> str:
    a = d['action']
    if a == 'STATUS': return 'Report where the project stands.'
    if a == 'UNDO': return 'Undo my last change.'
    if a == 'RESET': return 'Drop the notes for the upcoming shot and return to its base plan.'
    if a == 'STOP': return 'Hold. Do nothing.'
    if a == 'CONFIRM': return 'Confirm the pending action.'
    if a == 'DENY': return 'Cancel the pending action.'
    if a == 'NEW_SCENE': return 'Start a new scene (needs new original references).'
    if a == 'APPROVE_TAKE': return 'Accept the captured take as a working performance (asks to confirm).'
    if a == 'REJECT_TAKE': return 'Reject the captured take and set up a retake' + (' with your notes.' if d['note'] else '.')
    if a == 'EDIT':
        e = d['edit']; op = e['op']
        if op == 'SET_ENTRY': return f"Set the incoming cut point to frame {e['value']}."
        if op == 'NUDGE_ENTRY':
            amt = abs(e['delta']) if e['delta'] is not None else 'a few'
            return f"{'Tighten' if e['direction'] > 0 else 'Loosen'} the cut by {amt} frames."
        if op == 'APPEND': return 'Add the accepted shot to the working film; I pick the entry frame.'
        if op == 'REMOVE': return f"Remove shot {e['shot']} from the working film."
        if op == 'RENDER': return 'Render the current working film.'
        if op == 'AUTO_SELECT': return 'Re-pick the cut point technically.'
    parts = []
    if a == 'RETAKE': parts.append('New take of the same beat (new seed)')
    if d.get('defer') and not any([d['emotion'], d['framing'], d['pacing'], d['move']]):
        parts.append("Director's choice for the next beat")
    elif d.get('continue') and not parts: parts.append('Continue the scene')
    if d['subject'] and d['emotion']:
        parts.append(f"{SUBJECT_WORD[d['subject']]}: {d['emotion']} ({d['intensity']})")
    elif d['subject']: parts.append(f"focus on {SUBJECT_WORD[d['subject']]}'s reaction")
    elif d['emotion']: parts.append(f"tone: {d['emotion']} ({d['intensity']})")
    if d['framing']: parts.append('framing: ' + d['framing'].replace('_', ' ') + (f" on {SUBJECT_WORD.get(d['framing_subject'], '')}" if d['framing_subject'] and d['framing'] in ('close_up', 'over_shoulder', 'profile', 'closer') else ''))
    if d['move']: parts.append('camera: ' + d['move'].replace('_', ' '))
    if d['pacing']: parts.append('pacing: ' + d['pacing'])
    if d['dialogue']: parts.append(f"{len(d['dialogue'])} directed line(s)")
    if d.get('prop') == 'handoff': parts.append('microphone handoff (soft)')
    if d['standing']: parts.append('kept as a standing preference')
    return '; '.join(parts) + '.' if parts else 'Direct the next beat using your note.'
