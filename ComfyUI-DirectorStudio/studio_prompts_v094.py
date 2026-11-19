"""Director Studio prompts: the MiniMax-H3 official format and the classic wording, from the shot spec."""
from __future__ import annotations
from studio_common_v094 import *
from studio_common_v094 import _SHA_CACHE, _json_ok
from studio_edit_v094 import *
from studio_edit_v094 import _norm, _flow_at, _frame_hist, _hist_dist, _faces_near

# ----------------------------------------------------------------------------- planning helpers
def di_norm(t):
    import director_intent_v0739 as di
    return di.normalize(t)

SPEC_KEYS = ('subject', 'emotion', 'intensity', 'framing', 'framing_subject', 'move', 'pacing', 'prop', 'duration')
GEN_LENGTHS = {124: 'proven', 73: 'experimental', 107: 'experimental', 158: 'experimental', 175: 'experimental'}   # frames at 24 fps
# H3 only makes 17k+5 frames (73, 90, 107, 124, 141, 158, 175...): ComfyUI's node rounds any other length UP
# (nodes_minimax_h3.align_frame_count), so the Director only offers lengths H3 really makes.
OLD_GEN_LENGTHS = {72: 73, 96: 107, 148: 158, 172: 175}
LONG_LENGTHS = (124, 141, 158, 175, 192, 209, 226, 243)      # a shot you ask to run longer: up to ~10 s (H3 was trained on ~5-15 s)    # offered before V0.9.5 -> what H3 really made for them
def h3_length(n):
    """The frame count H3 really generates for a requested length (an older saved 96 becomes 107, etc.)."""
    n = max(5, int(n))
    while n % 17 != 5: n += 1
    return n

def merge(base, d):
    spec = dict(base)
    for k in SPEC_KEYS:
        v = d.get(k)
        if k == 'intensity' and v == 'normal' and not d.get('emotion'): continue
        if v: spec[k] = v
    spec['actions'] = list(base.get('actions', []))
    if d.get('note'):
        if d['note'] not in spec['actions']: spec['actions'].append(d['note'])
    spec['dialogue'] = list(base.get('dialogue', [])) + [x for x in d.get('dialogue', []) if x not in base.get('dialogue', [])]
    spec['avoid'] = list(dict.fromkeys(list(base.get('avoid', [])) + d.get('avoid', [])))
    spec['react'] = bool(base.get('react')) or bool(re.search(r'\breact|respon', d['normalized']))
    return spec

FRAMING = {
 None: 'Natural cinematic framing that suits the action; one continuous camera setup.',
 'same': 'Keep the same framing as the previous shot.',
 'closer': 'A closer, more intimate framing (roughly waist-up).',
 'wider': 'A wide shot showing the full bodies and the space around them.',
 'new_angle': 'A fresh camera angle on the same moment, keeping screen direction.',
 'profile': 'A side-on angle favoring {who} in profile.',
 'reverse': 'A reverse angle from the opposite side.',
 'low_angle': 'A slightly low camera position favoring {who}.',
 'high_angle': 'A slightly high camera position looking gently down.',
 'over_shoulder': 'An over-the-shoulder shot favoring {who}.',
 'close_up': 'A close-up on {who}: face and shoulders.',
}

ORDINAL = {2: 'second', 3: 'third', 4: 'fourth', 5: 'fifth'}
KIND_WORDS = {'face': 'face', 'upper': 'upper body', 'full': 'full body', 'location': 'the place'}

def _nums(idx):
    """[2,3,4,5] -> 'images 2-5'; [2] -> 'image 2'; [2,4] -> 'images 2 and 4'."""
    idx = sorted(idx)
    if len(idx) == 1: return f'image {idx[0]}'
    if idx == list(range(idx[0], idx[-1] + 1)): return f'images {idx[0]}-{idx[-1]}'
    return 'images ' + ', '.join(map(str, idx[:-1])) + f' and {idx[-1]}'

def ref_map(st, refs, videos=()):
    """Which numbered reference (1-based, in the order H3 receives them) shows whom."""
    R = st['refs']; chars = st['characters']; locs = st['locations']; out = []
    owners = []
    for r in refs:
        if R[r]['owner'] not in owners: owners.append(R[r]['owner'])
    for o in owners:
        idx = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == o]
        kinds = []; seen = {}                     # ⭐ V0.9.65 counted in order: face, second face, third face; full body, second full body
        for r in refs:
            if R[r]['owner'] != o or R[r]['kind'] == 'location': continue
            if R[r].get('variant') == 'support':           # ⭐ V0.9.65 a supporting face from another photo
                kinds.append('face close-up from another photo, for the facial features only'); continue
            k = R[r]['kind']; n = seen[k] = seen.get(k, 0) + 1
            if k == 'face' and n == 1 and R[r].get('alt') and not any(R[x]['owner'] == o and R[x]['kind'] == 'face' and not R[x].get('alt') for x in refs): n = 2
            word = KIND_WORDS.get(k, k)
            if n > 1 and k in ('face', 'full', 'upper'): word = f"{ORDINAL.get(n, str(n) + 'th')} {word}"
            kinds.append(word + (' (cut out on grey)' if R[r].get('variant') == 'clean' else ''))
        who = chars[o]['name'] if o in chars else f"the place ({locs[o]['name']})" if o in locs else o
        out.append({'owner': o, 'who': who, 'images': idx, 'kinds': kinds})
    vids = []
    for i, r in enumerate(videos):
        o = R[r]['owner']; nm = chars[o]['name'] if o in chars else f"the place ({locs[o]['name']})"
        if R[r].get('no_face') and o in chars:                                     # V0.9.50 a body-only video
            nm += f" (only {nm}'s body, without the face" + (', lying down' if R[r].get('pose') == 'lying' else '') + ': the body, legs and clothes, never a face)'
        vids.append({'owner': o, 'video': i + 1, 'who': nm})
    return out, vids

VIDEO_SENTENCE = ' The reference video clips show the same real people/place moving naturally; match their identity and movement style, not their exact actions.'

def unquote(t):
    """Direction text without quotation marks: quoted words read as lines to speak for an audio-video model."""
    return re.sub(r'["\u201c\u201d\u2018\u2019]', '', str(t)).strip()

WORDS_PER_SECOND = 2.5      # natural conversational speech; more than this in a shot sounds rushed

def dialogue_budget(lines, seconds):
    """Warnings when the dialogue can't be said naturally in the shot."""
    words = sum(len(re.findall(r"[\w']+", l['line'])) for l in lines)
    fit = max(3, int((seconds - 1.0) * WORDS_PER_SECOND))
    if words > fit:
        return [f'The dialogue is {words} words; about {fit} fit naturally in a {seconds:.0f} s shot, so it may sound rushed. '
                'Split it across two shots, or shorten it.']
    return []

def official_format(st): return st['settings'].get('prompt_format', 'official') == 'official'
# V0.9.27 🏃 Movement: 'lively' (default) drops the words that told H3 to barely move ("understated", "never theatrical",
# "unhurried", "pauses are welcome", "small movements") and asks for continuous motion, as MiniMax's prompt guides do;
# 'natural' is the old wording (compare the two with the A/B test).
def lively(st): return st['settings'].get('motion', 'lively') != 'natural'
# V0.9.35: H3 has no negative prompt (CFG 1), so every "never slow motion" / "nobody frozen" / "no waxy skin" put that
# very idea into the prompt. Everything below is now said positively: what IS on screen.
LIVELY_END = ('Continuous, physically plausible motion from the first frame to the last, at brisk real-time speed: everyone keeps '
              'moving naturally the whole time (weight shifts, gestures, glances, breathing), with natural hands and anatomy and '
              'consistent light. It ends on a clear, readable moment.')
LIVELY_CAMERA = 'The camera moves gently with the action, pushing in with small amplitude.'
# V0.9.27 🎥 Look: 'real_life' (default) asks for footage that looks filmed for real; 'cinematic' is the older polished
# film look (the default tone's "cinematic" became "shallow depth of field, film-like contrast" through the enhancer)
DEFAULT_TONE = 'Naturalistic, cinematic, believable human performance'
# V0.9.27: with no lines written, nobody talks. "Any talk is brief and in their own words" invited speech WITHOUT words,
# and H3 then fills it with gibberish or voices the scene description. 'natural' (ad-lib) stays available.
SPEECH_DEFAULT = 'lines_only'
NOBODY_TALKS_RAW = ('Everyone in this shot stays wordless: the feeling comes out in the face, the body and non-verbal sound alone '
                    '(sobs, gasps, laughter, cries, ragged breaths).')
NOBODY_TALKS = ('Everyone in this shot stays silent, lips together between breaths; they answer each other with faces, eyes and '
                'bodies, and the only voice sounds are natural non-verbal ones (a breath, a small laugh, a sigh).')
def real_look(st): return st['settings'].get('look', 'real_life') != 'cinematic'
REAL_STYLE = ('It looks filmed for real on a real camera: true-to-life skin with natural texture and pores, exactly as it is in '
              'real life; real-world light from the place\'s own light sources, with natural, slightly uneven exposure; natural depth '
              'of field; true, ungraded colours and natural sharpness; ordinary real-life details. Live-action photography of real people.')
REAL_CAMERA = 'The camera is handheld and moves gently with the action, shaking slightly with small amplitude.'
# V0.9.27 🪞 Faithful to the references (default on): exactly as in the photos, nothing improved (MiniMax's guide:
# name what must be preserved; negative wording works well on H3)
def faithful(st): return st['settings'].get('faithful', True) is not False
FAITHFUL = ('Every person appears unretouched, exactly as in their references: the same face shape and features, skin tone and '
            'real skin texture (pores, freckles, moles, wrinkles, blemishes, facial hair), teeth, hairline and hair, body shape, '
            'weight, height, age, makeup and wardrobe, all as they really are.')
# V0.9.35 😮 Acting: 'expressive' (default) plays feelings as visible acting beats; 'understated' is the old wording
import studio_acting_v0935 as acting
def acted(who, emo, intensity, st):
    """'<who> laughs out loud, head tipping back, ...' (expressive) or the old '<who> shows ...; natural, ...' line."""
    ph = acting.phrase(emo, intensity) if acting.expressive(st) else None
    if ph: return f'{who} {ph}{acting.strong_tail(intensity)}.'
    old = dv.emotion_phrase(emo, intensity)
    return f"{who} shows {old}; " + ('natural and motivated, shown in the face and the whole body.' if lively(st) else 'natural, motivated, never theatrical.')
def reacts(who, st):
    if acting.expressive(st): return f'{who} reacts visibly and at once: the face and the whole body answer what happens.'
    return f'{who} reacts visibly with the face and the whole body.' if lively(st) else f'{who} reacts honestly and readably; understated.'
def listens(who, speaker, st):
    """A silent person in a shot with lines: an explicit listener reaction (expressive) or the old line."""
    if acting.expressive(st) and speaker: return f'{who} stays silent and ' + acting.LISTEN.format(speaker=speaker) + '.'
    return f'{who} does not speak.'
FRAMING_REAL_NONE = 'Natural framing that suits the action; one continuous camera setup.'
def tone_of(st):
    t = unquote(st['project'].get('tone') or 'naturalistic, believable human performance').rstrip('.')
    return 'Naturalistic, true-to-life, believable human performance' if real_look(st) and t == DEFAULT_TONE else t
def camera_line(st): return REAL_CAMERA if real_look(st) else LIVELY_CAMERA

def short_summary(acts, words=40):
    """The summary is one short paragraph (MiniMax guide): the opening of a long direction, the full one is in the description."""
    w = acts.split()
    if len(w) <= words: return acts
    first = re.split(r'(?<=[.;!?])\s', acts, maxsplit=1)[0]
    return first if len(first.split()) <= words else ' '.join(w[:words]).rstrip(',;') + '…'

SPEECH_VERBS = r'(?:says?|said|asks?|asked|replies|replied|whispers?|whispered|shouts?|shouted|tells? (?:him|her|them)|adds?|answers?)'

# ---------------------------------------------------------------- lasting changes from earlier shots, as STATE (never as an action)
_OBJ = r"((?:the|a|an|her|his|their|its|my|some)\s+[\w' -]{1,30}?)(?=\s*(?:,|\.|;|$|\band\b|\bthen\b|\bwhile\b|\bas\b|\bto\b|\bfrom\b|\bon\b|\bin\b|\bwith\b))"
LASTING = [   # (pattern, kind, sets True/False)
    (r"\b(?:puts? on|putting on|wears|is wearing|slips? on|ties? on)\s+" + _OBJ, 'wearing', True),
    (r"\b(?:takes? off|taking off|removes?|removing|pulls? off)\s+" + _OBJ, 'wearing', False),
    (r"\b(?:picks? up|picking up|grabs?|grabbing|holds?|holding|is holding|carries|carrying|takes?|lifts?)\s+" + _OBJ, 'holding', True),
    (r"\b(?:puts? down|putting down|sets? down|drops?|hands? over|gives? away|throws? away|places?)\s+" + _OBJ, 'holding', False),
    (r"\b(?:sits? down|sitting down|takes? a seat|sits on|sits at|sits)\b(?!\s+up\b)", 'seated', True),
    (r"\b(?:stands? up|gets? up|rises?|standing up)\b", 'seated', False),
    (r"\b(?:lies? down|lying down|lays? down)\b", 'lying', True),
]
# letting go, with the thing BEFORE the word: "puts her microphone down", "puts it away", "hands the cup to Leo", "lets go of the rope"
_THING = r"(it|them|(?:the|a|an|her|his|their|its|my|some)\s+[\w' -]{1,30}?)"
RELEASE = [
    r"\b(?:puts?|putting|sets?|setting|lays?|laying|places?|placing|drops?|dropping|tosses?|tossing|throws?|throwing|leaves?|leaving|tucks?|slips?|lowers?|lowering|stows?|pockets?)\s+"
    + _THING + r"\s+(?:down|away|aside|back|on|onto|in|into|inside|under|behind|beside|next to)\b",
    r"\b(?:hands?|handing|gives?|giving|passes?|passing|offers?|tosses?|throws?)\s+" + _THING + r"\s+(?:to|over to|back to)\b",
    r"\b(?:hands?|gives?|passes?)\s+(?:him|her|them|\w+)\s+" + _THING + r"(?=\s*(?:,|\.|;|$|\band\b|\bthen\b))",
    r"\b(?:lets? go of|letting go of|releases?|releasing|puts? away|putting away|pockets?)\s+" + _THING + r"(?=\s*(?:,|\.|;|$|\band\b|\bthen\b|\bwhile\b|\bas\b))",
]
EMPTY_HANDS = r"\b(?:hands (?:are|were) (?:now )?empty|empty[- ]handed|nothing in (?:her|his|their) hands?|(?:her|his|their) hands are free)\b"
NOT_OBJECTS = set('''breath breaths step steps seat look looks glance moment moments pause pace sip bite bites turn chance risk voice gaze
head eyes eye face smile smiles grin frown expression position stance time while second seconds minute minutes nap shower walk run
deep picture photo photos selfie note notes stand break rest liking interest part place lead hint shot shots hold grip beat beats
attention care notice aim sigh laugh laughs hand hands arm arms leg legs knee knees shoulder shoulders hair heart'''.split())
def _real_object(o):
    """'the red hat' yes; 'a deep breath', 'a smile', 'a step', 'her hand' no: those are actions or the body, not things worn or held."""
    words = re.findall(r"[a-z']+", o.lower())
    return bool(words) and not any(w in NOT_OBJECTS for w in words)

def _norm_obj(o):
    o = re.sub(r"^(?:the|a|an|her|his|their|its|my|some)\s+", '', o.strip().lower()); return o.strip(" .,'")

def lasting_state(story_texts, names):
    """Earlier directions -> what is STILL true (wearing / holding / seated / lying), for the people in this shot.
    story_texts: [(text, cast_ids)] in film order. Only what was said; nothing is invented. Returns plain sentences."""
    state = {}      # cid -> {'wearing': {obj: words}, 'holding': {obj: words}, 'seated': bool, 'lying': bool}
    for text, cast in story_texts:
        t = re.sub(r'["\u201c][^"\u201d]*["\u201d]', ' ', str(text or ''))          # never inside spoken lines
        for clause in re.split(r'(?:[.;]|\bthen\b|\band then\b)', t):
            who = next((cid for cid in cast if names.get(cid) and re.search(r'(?<![\w])' + re.escape(names[cid]) + r'(?![\w])', clause, re.I)), None)
            if who is None and len(cast) == 1: who = cast[0]
            if who is None: continue
            cur = state.setdefault(who, {'wearing': {}, 'holding': {}, 'seated': False, 'lying': False})
            for rx, kind, on in LASTING:
                for m in re.finditer(rx, clause, re.I):
                    if kind in ('seated', 'lying'):
                        cur[kind] = on
                        if kind == 'seated' and on: cur['lying'] = False
                        continue
                    obj = m.group(1); key = _norm_obj(obj)
                    if not key or len(key) < 2 or not _real_object(obj): continue
                    if on: cur[kind][key] = re.sub(r"^(?:her|his|their|its|my)\s+", 'the ', obj.strip(), flags=re.I)
                    else: cur[kind].pop(key, None)
                    if kind == 'holding' and not on and re.search(r'\bplaces?\b', m.group(0), re.I): pass
            for rx in RELEASE:                                  # put down / away / handed over / let go
                for m in re.finditer(rx, clause, re.I):
                    thing = m.group(1)
                    if thing.lower() in ('it', 'them'):
                        if cur['holding']: cur['holding'].pop(next(reversed(cur['holding'])))   # the thing held most recently
                        if thing.lower() == 'them': cur['holding'].clear()
                    else: cur['holding'].pop(_norm_obj(thing), None)
            if re.search(EMPTY_HANDS, clause, re.I): cur['holding'].clear()
    out = []
    for cid, cur in state.items():
        n = names.get(cid)
        if not n: continue
        bits = [f"wearing {w}" for w in cur['wearing'].values()] + [f"holding {h}" for h in cur['holding'].values()]
        if cur['seated']: bits.append('seated')
        if cur['lying']: bits.append('lying down')
        if bits: out.append(f"{n} is " + ', '.join(bits))
    return out

def continuity_sentence(cut, names):
    """Plain words from the measured end state; only what was measured is said."""
    bits = []
    if cut.get('left_to_right') and len(cut['left_to_right']) >= 2:
        bits.append(f"{cut['left_to_right'][0]} on the left and {cut['left_to_right'][-1]} on the right")
    elif cut.get('people') == 1 and len(names) == 1: bits.append(f"{next(iter(names.values()))} alone in frame")
    if cut.get('framing'): bits.append(f"in a {cut['framing']}")
    motion = ('still moving ' + cut['heading']) if cut.get('heading') else ('moving' if cut.get('moving', 0) > 0.18 else 'nearly still')
    bits.append(motion)
    who = [b for b in bits if b.startswith(('in a ', 'still moving', 'moving', 'nearly still')) is False]
    rest = [b for b in bits if b not in who]
    out = ('The previous shot cuts away with ' + ', '.join(who) + (', ' + ', '.join(rest) if rest else '') + '.') if who else \
          ('The previous shot cuts away ' + ', '.join(rest) + '.')
    out += (' This shot picks up a moment later in the same scene: keep everyone on the same side of the screen, the same light, wardrobe and props'
            + ('; the movement carries on in the same direction.' if cut.get('heading') else '.'))
    return out

def action_without_lines(a):
    """Direction text with the spoken lines taken out: the words live only inside <d> tags, never twice."""
    # how it is said ("very angrily says", "says, laughing a little,") goes with the line into the voice, not into the action
    HOW = r'(?:(?:very|really|extremely|so|a little|slightly|a bit|softly|quietly|barely)\s+)?[a-z]+ly\s+'
    a = re.sub(r'(?:,?\s*(?:and|then)?\s*)?(?:\b' + HOW + r')?\b' + SPEECH_VERBS + r'\b(?:\s*,\s*[a-z][a-z ]{1,30}?\s*,)?\s*:?\s*(["\u201c\u2018\'][^"\u201d\u2019\']*["\u201d\u2019\'])(?:\s*,?\s*and\b)?', ' \x00 ', str(a), flags=re.I)
    a = re.sub(r'["\u201c\u2018][^"\u201d\u2019]*["\u201d\u2019]', '', a)
    a = re.sub(r"(?<![\w])'[^']{2,200}'(?![\w])", '', a)
    a = re.sub(r'\s+([,.;!?])', r'\1', re.sub(r'\s{2,}', ' ', a)).strip(' ,;')
    a = re.sub(r'(?:,\s*|\s+)(?:and\s+|then\s+)+[A-Z][\w\'-]*\s*\x00\s*(?=[.;!?]|$)', '', a)    # "..., then Maya" left after her line
    a = re.sub(r'(^|(?<=[.;!?]) ?)([A-Z][\w\'-]*)\s*\x00\s*(?=,\s*(?:and|then)\b)', r'\1\2 speaks', a)   # "Grandpa \x00, then Maya laughs"
    # several speakers: "..., then Grandpa \x00, then Maya \x00" / ". Maya \x00 Grandpa \x00" -> only the action is left (V0.9.20)
    for _ in range(4):
        a2 = re.sub(r'(?:,\s*|\s+|(?<=[.;!?]))(?:(?:and|then)\s+)*[A-Z][\w\'-]*\s*\x00\s*(?=,|[.;!?]|$|(?:and|then)\b|[A-Z][\w\'-]*\s*\x00)', '', a)
        if a2 == a: break
        a = a2
    a = re.sub(r'\x00\s*(?=(?:while|as|before|after|to|with|softly|quietly|slowly)\b)', 'speaks ', a)   # "Maya says ... while X" -> "Maya speaks while X"
    a = re.sub(r'\s+([,.;!?])', r'\1', re.sub(r'\s{2,}', ' ', a.replace('\x00', ' '))).strip(' ,;')
    parts = [x.strip() for x in re.split(r';', a)]
    keep = []
    for x in parts:                                                         # "Maya." left over after removing her line
        sents = [y for y in re.split(r'(?<=[.!?])\s+', x) if len(re.findall(r"[\w']+", y)) > 1]
        if sents: keep.append(' '.join(sents))
    return '; '.join(keep)

# 🙈 V0.9.48 someone whose face is never shown in this shot (it is revealed in a later shot)
HIDDEN = ('{who} is seen only from behind, turned away from the camera, or with the head out of the frame, for the whole shot: '
          "the camera shows {name}'s back, body and hands, and the face stays out of view.")

REVEAL = ('This is the first time {who}\'s face is seen in the film: {who} turns toward the camera so the face is seen plainly and '
          'clearly lit, a clear moment.')

def positions_text(st, spec, cast, label):
    """📍 V0.9.45: the positions you set for this shot (spec['positions'], by name) -> (start words, end words) for H3."""
    pos = spec.get('positions')
    if not pos: return '', ''
    import studio_positions_v0945 as pm
    chars = st['characters']; by = {chars[c]['name']: c for c in cast if c in chars}
    lab = lambda n: label(by[n]) if n in by else n
    pro = lambda n: chars[by[n]].get('pronoun') if n in by else 'they'
    keep = {m: [r for r in pos.get(m) or [] if r.get('who') in by] for m in ('start', 'end')}
    a, b = pm.for_h3(keep, lab, pro)
    if a and spec.get('lead_in'): a = a.replace('At the start, ', f"After the first {float(spec['lead_in']):g} seconds, ", 1)   # ⏱ V0.9.48
    return a, b

def compose_official(st, spec, cast, loc, refs, continuity, lessons=None, videos=(), bg=None, frames=None, extend=None, audios=(), start=False, end=False):
    """MiniMax H3 full-reference prompt, in the official six-section format (MiniMax-H3 docs,
    VIDEO_PROMPT_WRITING_GUIDE_ref_en.md): subject_definitions, summary, retention_analysis, detailed_description,
    overall_soundscape, non_diegetic_music. <Picture N> = N-th reference image H3 receives, <Video N> = N-th reference
    clip; dialogue only inside <d>[Language] ...</d> with speaker IDs; quotation marks are never used (they mean on-screen text)."""
    chars, R, locs = st['characters'], st['refs'], st['locations']
    fmt = st['project']['format']; nfr = frames or fmt['frames']; secs = nfr / fmt['fps']
    lang = st['settings'].get('dialogue_language', 'English')
    pics = {}; vids = {}
    for i, r in enumerate(refs): pics.setdefault(R[r]['owner'], []).append(i + 1)
    for i, r in enumerate(videos): vids.setdefault(R[r]['owner'], []).append(i + 1)
    def plist(nums, tag):
        if not nums: return ''
        if len(nums) > 1 and nums == list(range(nums[0], nums[-1] + 1)): return f'<{tag} {nums[0]}>–<{tag} {nums[-1]}>'
        return ', '.join(f'<{tag} {n}>' for n in nums)
    def sources(owner, skip_video=None):
        a = plist(pics.get(owner, []), 'Picture'); b = plist([n for n in vids.get(owner, []) if n != skip_video], 'Video')
        return ' and '.join(x for x in (a, b) if x)
    pinned = bool(extend and extend.get('anchored'))          # the take starts on frames of YOUR original (Add Guide)
    ext_clip = bool(extend) and bool(videos)                  # ... and/or gets its last 3 s as <Video 1>
    subj = {}; lines_def = []; n = 0; weak_vids = []
    place_name = None
    if loc:
        n += 1; subj[loc] = n; L = locs[loc]; place_name = L['name']
        src = sources(loc, skip_video=1 if ext_clip else None)
        lines_def.append(f"<Subject {n}> is the place, {L['name']}" + (f" ({unquote(L['description'])})" if L.get('description') else '') +
                         (f', from {src}.' if src else '.'))
    elif bg and bg.get('mode') == 'people' and bg.get('from') in chars:
        n += 1; subj['__bg'] = n; place_name = f"the place behind {chars[bg['from']]['name']}"
        first = (pics.get(bg['from']) or [None])[0]
        lines_def.append(f"<Subject {n}> is the real place seen behind {chars[bg['from']]['name']}" + (f' in <Picture {first}>.' if first else '.'))
    kind_word = {'she': 'a woman', 'he': 'a man'}
    for c in cast:
        n += 1; subj[c] = n; C = chars[c]
        clean = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == c and R[r].get('variant') == 'clean']
        src = sources(c, skip_video=1 if ext_clip else None)
        d = f"<Subject {n}> is {C['name']}, {kind_word.get(C.get('pronoun'), 'a person')}" + (f", {unquote(C['description'])}" if C.get('description') else '')
        d += f', from {src}' if src else ''
        if ext_clip and extend.get('owner') == c: d += ', the same person seen in <Video 1>'
        if clean: d += f"; {plist(clean, 'Picture')} show them cut out on a plain grey backdrop that is not part of the scene"
        body = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == c and R[r].get('no_face')]          # V0.9.48 a body-only photo
        withface = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == c and not R[r].get('no_face')]
        if body:
            d += (f"; {plist(body, 'Picture')} show{'s' if len(body) == 1 else ''} only {C['name']}'s body, without the face: take the body shape, "
                  f"build and clothes from {'it' if len(body) == 1 else 'them'}" + (f", and the face only from {plist(withface, 'Picture')}" if withface else ''))
        bodyv = [i + 1 for i, r in enumerate(videos) if R[r]['owner'] == c and R[r].get('no_face')]          # V0.9.50 a body-only video
        if bodyv:
            d += (f"; {plist(bodyv, 'Video')} show{'s' if len(bodyv) == 1 else ''} only {C['name']}'s body, without the face"
                  + (', lying down' if any(R[videos[i - 1]].get('pose') == 'lying' for i in bodyv) else '')
                  + ": take the body, legs and clothes from it, never a face")
        sup = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == c and R[r].get('variant') == 'support']      # ⭐ V0.9.65
        if sup:
            main = [i for i in withface if i not in sup]
            d += ((f"; {plist(main, 'Picture')} {'is' if len(main) == 1 else 'are'} {C['name']}'s main reference: {C['name']}'s clothes, hair, "
                   f"makeup and accessories are exactly as in {'it' if len(main) == 1 else 'them'}" if main else '')
                  + f"; {plist(sup, 'Picture')} {'is a close-up' if len(sup) == 1 else 'are close-ups'} of {C['name']}'s face from other photos, "
                  "only for the shape and features of the face")
        if C['name'] in (spec.get('hide_faces') or []):                                        # 🙈 V0.9.48
            d += f"; in this shot {C['name']}'s face stays out of view the whole time"
        weak = [i for i in vids.get(c, []) if not (ext_clip and i == 1)] if (lively(st) and pics.get(c)) else []
        if weak: d += f"; {plist(weak, 'Video')} {'is' if len(weak) == 1 else 'are'} for how they look and sound, while their movement in this shot is new"
        weak_vids.extend(weak)
        lines_def.append(d + '.')
    if cast and faithful(st): lines_def.append(FAITHFUL)
    S = lambda c: f'<Subject {subj[c]}>'
    # speakers in order of their lines
    lines = []
    for l in spec.get('dialogue', [])[-3:]:
        sp = l.get('speaker') if l.get('speaker') in subj else (cast[0] if len(cast) == 1 else None)
        lines.append({'speaker': sp, 'line': l['line'], 'how': l.get('how')})
    sid = {}
    for l in lines:
        if l['speaker'] and l['speaker'] not in sid: sid[l['speaker']] = f'S{len(sid) + 1}'
    voice_audio = st['settings'].get('voice_refs') and videos and not audios
    audio_lines = []; aud_of = {}
    for k, r in enumerate(audios or []):           # your own voice recordings: <Audio N> = ref_audios.ref_audio_{N-1}
        o = R[r]['owner']
        if o in chars and o in subj:
            aud_of[o] = k + 1
            audio_lines.append(f"<Audio {k + 1}> is the voice-timbre reference for {S(o)}" + (f' ({sid[o]})' if o in sid else '') +
                               f', containing a spoken {lang} vocal layer; only its voice is used, not its words.')
    if voice_audio:
        # H3 gives every clip soundtrack that HAS sound its own <Audio j>, in clip order, right before that <Video>
        # (ComfyUI nodes_minimax_h3: ref_video_audio_N belongs to ref_video_N; a silent clip's audio is None and is skipped).
        j = 0                                       # clips without a sound track get no <Audio> label at all
        for i, r in enumerate(videos):
            if R[r].get('audio') is False: continue
            j += 1; o = R[r]['owner']
            if o in chars and o in subj:
                who = S(o) + (f' ({sid[o]})' if o in sid else '')
                aud_of.setdefault(o, j)
                audio_lines.append(f'<Audio {j}> is the voice-timbre reference for {who}, from <Video {i + 1}>.')
            else:
                audio_lines.append(f'<Audio {j}> is the original sound of <Video {i + 1}>, a reference for the room tone only; nobody in the new shot speaks with it.')
    task = '[video continuation' if ext_clip else '[reference generation'
    task += ' + audio reference]' if audio_lines else ']'
    who_txt = ' and '.join(f"{S(c)} ({chars[c]['name']})" for c in cast)
    acts = '; then '.join(x for x in (action_without_lines(a).rstrip('.') for a in spec.get('actions', [])[-3:]) if x)
    place_txt = f' in {S(loc) if loc else S("__bg")}' if (loc or '__bg' in subj) else ''
    summ = f'{task} '
    if start:
        summ += ('Its first frame is already given: a real photo of this scene. The video opens exactly on that picture and moves on '
                 'from it without a cut. ')
        if spec.get('lead_in'):                            # ⏱ V0.9.48
            summ += (f"In the first {float(spec['lead_in']):g} seconds everyone moves calmly and directly from where they are in that "
                     'picture to where this shot needs them; the action starts right after that. ')
            down = {'kneeling': 'kneels down', 'sitting': 'sits down', 'crouching': 'crouches down', 'lying_back': 'lies down on the back',
                    'lying_front': 'lies down face down', 'lying_side': 'lies down on one side'}
            for x in spec.get('stand_ins') or []:          # V0.9.61 standing in the picture (no photo in that pose)
                cid = next((c for c in subj if c in chars and chars[c]['name'] == x.get('who')), None)
                if x.get('posture') in down and cid: summ += f"{S(cid)} is standing in that picture and {down[x['posture']]} in those seconds. "

    if end:
        summ += ('Its last frame is already given' + (' as well' if start else '') + ': a real photo of this scene. By the end of the '
                 'video everyone is exactly where that picture shows them, in the same pose and place, so the video moves naturally towards it. ')
    if pinned:
        summ += ('Its first second is already given: the final moment of the previous shot, exactly as filmed. The video carries on '
                 'from that moment without a cut, with the same place, the same people in the same positions, the same camera, light and '
                 'sound, and moves forward into what happens next; it never goes back to or repeats what came before. ')
    elif extend:
        summ += ('The target video picks up right after the final frame of <Video 1> and continues from there, without repeating '
                 'anything <Video 1> already shows: the same place, the same people in the same positions, the same camera, light '
                 'and sound, carrying on naturally without a cut into what happens next. ')
    summ += f'One continuous 9:16 shot of about {secs:.0f} seconds' + (f' with {who_txt}' if cast else '') + place_txt + (f': {short_summary(acts) if lively(st) else acts}.' if acts else '.')
    _ps, _ = positions_text(st, spec, cast, lambda c: f"{S(c)} ({chars[c]['name']})")      # 📍 V0.9.47 where you set them, in the summary too
    if _ps: summ += ' ' + _ps
    if continuity: summ += ' ' + action_without_lines(unquote(continuity))
    ret = [f'<Subject {i}>: fully_preserved' for i in sorted(subj.values())]
    if ext_clip: ret.insert(0, '<Video 1>: fully_preserved')
    ret += [f'<Video {i}>: weak_reference' for i in sorted(set(weak_vids))]     # V0.9.35: a person's clip must not lock pose and pace
    ret += [f'<Audio {i + 1}>: reference' for i in range(len(audio_lines))]
    # detailed description: one shot, as explicit as possible
    tone = tone_of(st)
    desc = [f"{'Live-action, real-life footage' if real_look(st) else 'Live-action film'}. Tone: {tone}. One continuous 9:16 camera setup at {fmt['fps']} fps, no cuts."]
    if real_look(st): desc.append(REAL_STYLE)
    if st['settings'].get('enhance', True):
        import studio_enhance_v093 as en
        _st = en.enhance(' '.join(spec.get('actions', [])[-3:]) + ' ' + tone)['style']
        if faithful(st): _st = [x for x in _st if 'flattering' not in x]      # 🪞 no beautifying light either
        if real_look(st): _st = [x for x in _st if 'film-like' not in x and 'documentary-style' not in x]   # REAL_STYLE says it
        if _st: desc.append('The look: ' + '; '.join(dict.fromkeys(_st)) + '.')
    sh = ['[Shot 1]']
    f = spec.get('framing'); fw = spec.get('framing_subject') or spec.get('subject') or (cast[0] if cast else None)
    sh.append((FRAMING_REAL_NONE if f is None and real_look(st) else FRAMING.get(f, FRAMING[None])).format(who=S(fw) if fw in subj else 'the subject'))
    if spec.get('move'): sh.append(dv.MOVE_TEXT[spec['move']])
    elif lively(st) and not re.search(r'\bcamera\b', acts or '', re.I): sh.append(camera_line(st))
    if spec.get('place_view') and loc:                 # 📷 V0.9.46 one angle for the film: the place photo's own viewpoint, every shot
        first = (pics.get(loc) or [None])[0]
        sh.append(f"The camera sees {S(loc)} from exactly the viewpoint of " + (f'<Picture {first}>' if first else 'its reference picture') +
                  ': the same camera position, height, direction and lens, for the whole shot.')
        if spec.get('frame_lock'):                         # 🔒 V0.9.47
            sh.append('The camera never moves and the frame never changes: everything happens inside this one frame, and everyone stays inside it '
                      'unless the action has them walk in or out.')
    if cast:
        sh.append(f"{who_txt} {'is' if len(cast) == 1 else 'are'}{' in ' + (S(loc) if loc else S('__bg')) if place_txt else ''}, each looking exactly like their references "
                  '(face, hair, body, age, wardrobe).' + (' They are the only people in the shot.' if cast else ''))
    else:
        sh.append('No people in this shot: only the place.')
    pos_start, pos_end = positions_text(st, spec, cast, lambda c: f"{S(c)} ({chars[c]['name']})")   # 📍 V0.9.45 you set where everyone is
    if pos_start: sh.append(pos_start)
    for c in cast:                                                                              # 🙈 V0.9.48
        if chars[c]['name'] in (spec.get('hide_faces') or []):
            sh.append(HIDDEN.format(who=f"{S(c)} ({chars[c]['name']})", name=chars[c]['name']))
        elif chars[c]['name'] in (spec.get('reveal') or []):                                    # 👁 V0.9.48 the first time it is seen
            sh.append(REVEAL.format(who=f"{S(c)} ({chars[c]['name']})"))
    if acts: sh.append(f'{acts[0].upper() + acts[1:]}.')
    enh = None
    if st['settings'].get('enhance', True):          # ✨ the prompt enhancer: explicit, physical detail after your words
        import studio_enhance_v093 as en
        enh = en.enhance(' then '.join(action_without_lines(a) for a in spec.get('actions', [])[-3:]), framing=spec.get('framing'),
                         move=spec.get('move'), place_known=bool(loc or '__bg' in subj), lively=lively(st))
        sh += enh['extra']
    subj_c, emo = spec.get('subject'), spec.get('emotion')
    if emo:
        sh.append(acted(S(subj_c), emo, spec.get('intensity', 'normal'), st) if subj_c in subj else f"The mood carries {dv.emotion_phrase(emo, spec.get('intensity', 'normal'))}.")
    elif subj_c in subj and spec.get('react'):
        sh.append(reacts(S(subj_c), st))
    feeling = {subj_c} if (emo and subj_c in subj) else set()
    feel_of = {subj_c: (emo, spec.get('intensity', 'normal'))} if (emo and subj_c in subj) else {}
    for oc, oe in (spec.get('emotions') or {}).items():
        if oc in subj and oc != (subj_c if emo else None):
            sh.append(acted(S(oc), oe['emotion'], oe.get('intensity', 'normal'), st)); feeling.add(oc)
            feel_of[oc] = (oe['emotion'], oe.get('intensity', 'normal'))
    if emo and not feel_of and spec.get('intensity') == 'strong': feel_of[None] = (emo, 'strong')   # the mood of the whole shot
    big = acting.expressive(st) and any(i == 'strong' for _, i in feel_of.values())
    if spec.get('duration') and spec['duration'] != 'full' and float(spec['duration']) < secs - 0.5:
        sh.append(f"The key moment reads clearly within about {float(spec['duration']):g} seconds.")
    pace = {'slower': 'an unhurried pace, leaving natural pauses between phrases', 'faster': 'a lively, quick but clear pace'}.get(
        spec.get('pacing'), 'a natural conversational pace' if lively(st) else 'a natural, unhurried conversational pace with small pauses')
    def voice_of(c):
        v = unquote(chars[c].get('voice') or '').strip().rstrip('.')
        if not v: return 'a natural, relaxed voice'
        v = v if re.search(r'\bvoice\b', v, re.I) else v + ' voice'
        return v if re.match(r'(?i)(a|an|the|his|her|their)\b', v) else ('an ' if v[0].lower() in 'aeiou' else 'a ') + v
    for k, l in enumerate(lines):
        lead = 'After a short pause, ' if k else ''
        if l['speaker']:
            c = l['speaker']; first = all(x['speaker'] != c for x in lines[:k])
            voice_txt = f'speaking in the same voice referenced from <Audio {aud_of[c]}>' if c in aud_of else f'with {voice_of(c)}'
            who = f"{S(c)} ({sid[c]})" + (f", {kind_word.get(chars[c].get('pronoun'), 'a person')} {voice_txt}, speaking at {pace}," if first else '')
        else:
            who = 'Someone in the shot'
        how = f" {unquote(l['how'])}" if l.get('how') else ''                 # V0.9.20: how this line is said
        if not how and acting.expressive(st) and l['speaker'] in feel_of:      # V0.9.35: a strong feeling reaches the voice
            vo = acting.voice(*feel_of[l['speaker']])
            if vo: how = f' {vo}'
        sh.append(f"{lead}{who} says{how}: <d>[{lang}] {l['line'].strip().rstrip()}{'' if l['line'].strip()[-1:] in '.?!' else '.'}</d>")
    if aud_of: sh.append('Do not carry the original dialogue from the reference audio into the target video.')
    speech = st['settings'].get('speech', SPEECH_DEFAULT)
    silent = [c for c in cast if c not in sid]
    if lines or speech != 'natural':
        first_sp = next((l['speaker'] for l in lines if l['speaker']), None)
        for c in silent: sh.append(listens(S(c), S(first_sp) if (first_sp and c not in feeling) else None, st))
        if not lines and cast: sh.append(NOBODY_TALKS_RAW if big else NOBODY_TALKS)
    elif cast and speech == 'natural':
        sh.append('Any talk is brief, natural and in their own words' + ('' if lively(st) else ', at an unhurried pace') + '; it is about their own moment, never a description of this scene.')
    if pos_end: sh.append(pos_end)
    if lessons and lessons.get('avoid'): sh.append('Unlike earlier takes the filmmaker rejected: ' + '; '.join(unquote(x) for x in lessons['avoid']) + '.')
    if lessons and lessons.get('keep'): sh.append('As in earlier takes the filmmaker liked: ' + '; '.join(unquote(x) for x in lessons['keep']) + '.')
    sh.append((LIVELY_END + ' ' if lively(st) else 'Physically plausible motion, natural hands and anatomy, consistent light. It ends on a clear, readable moment without freezing. ') +
              'There is no on-screen text, no subtitles and no watermark.')
    desc.append(' '.join(sh))
    place_sound = f"the {locs[loc]['name']}" if loc else 'the place'
    cues = ', '.join(dict.fromkeys((enh or {}).get('sound', []))) if enh else ''
    raw = [x for x in dict.fromkeys(acting.sound(e, i) for e, i in feel_of.values()) if x] if acting.expressive(st) else []
    base_sound = f'Natural room tone of {unquote(place_sound)}' + (f', {cues}' if cues else ', soft footsteps') + ', clothing movement and ' + (', '.join(raw) if raw else 'quiet breathing')
    if speech != 'natural' and not lines:
        sound = base_sound + ('; the only human sounds are non-verbal ones.' if raw else '; the only human sounds are breaths and small non-verbal reactions.')
    else:
        sound = base_sound + '.'
    music = unquote(st['settings'].get('music') or '') or 'N/A'
    if ext_clip and pinned:
        lines_def.insert(0, '<Video 1> is the source video, the seconds just before the target video begins; the target video continues '
                            'after it and never replays or repeats any part of <Video 1>.')
    elif ext_clip:  # <Video N> is reserved for whole-video roles (MiniMax guide): this one is the source being continued
        lines_def.insert(0, '<Video 1> is the source video. The target video is its continuation: it begins right after the final frame '
                            'of <Video 1> and shows only what happens next; it never replays or repeats any part of <Video 1>.')
    return ('subject_definitions:\n' + '\n'.join(lines_def + audio_lines) + '\n\nsummary:\n' + summ + '\n\nretention_analysis:\n' + '\n'.join(ret) +
            '\n\ndetailed_description:\n' + ' '.join(desc) + '\n\noverall_soundscape:\n' + sound + '\n\nnon_diegetic_music:\n' + music)

def compose(st, spec, cast, loc, refs, continuity, d, lessons=None, videos=(), bg=None, frames=None, audios=()):
    chars = st['characters']; R = st['refs']
    name = lambda c: chars[c]['name']
    fmt = st['project']['format']
    numbered = st['settings'].get('ref_numbering', True)
    rmap, vmap = ref_map(st, refs, videos)
    nums = {m['owner']: m['images'] for m in rmap}
    nfr = frames or fmt['frames']
    parts = [f"SINGLE UNBROKEN {'real-life' if real_look(st) else 'cinematic'} shot, 9:16 vertical, {fmt['fps']} FPS, about {nfr/fmt['fps']:.0f} seconds. Tone: {tone_of(st) if real_look(st) else st['project']['tone']}."]
    if real_look(st): parts.append(REAL_STYLE)
    if numbered and rmap:
        parts.append('Reference images, in order: ' + '; '.join(f"{_nums(m['images'])} = {m['who']}" + (f" ({', '.join(m['kinds'])})" if m['kinds'] else '') for m in rmap) + '.'
                     + (' Reference videos: ' + '; '.join(f"video {v['video']} = {v['who']}" for v in vmap) + '.' if vmap else ''))
    if loc:
        L = st['locations'][loc]
        anchor = f" ({_nums(nums[loc])})" if numbered and loc in nums else ''
        parts.append(f"Location: {L['name']}" + (f" — {L['description']}" if L['description'] else '') +
                     f'. The location reference image{anchor} is the scene anchor: same place, light and colour.')
    elif bg and bg.get('mode') == 'people' and bg.get('from') in chars:
        b = bg['from']
        anchor = f" ({_nums(nums[b][:1])})" if numbered and b in nums else ''
        their = {'she': 'her', 'he': 'his'}.get(chars[b].get('pronoun'), 'their')
        parts.append(f"Background: the real place seen behind {name(b)} in {their} own reference photo{anchor}. "
                     'Keep that same place, light and colour; it is the scene anchor.')
    who = []
    for c in cast:
        C = chars[c]; kinds = sorted({R[r]['kind'] for r in refs if R[r]['owner'] == c})
        desc = f" ({C['description']})" if C['description'] else ''
        where = f"shown in reference {_nums(nums[c])}" if numbered and c in nums else f"shown in the {', '.join(kinds) or 'provided'} reference image(s)"
        body = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == c and R[r].get('no_face')]          # V0.9.48 a body-only photo
        if body: where += (f" (image{'s' if len(body) > 1 else ''} {', '.join(map(str, body))} show{'s' if len(body) == 1 else ''} only the body, "
                           "without the face: body shape, build and clothes come from it; the face comes from the other images)")
        sup = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == c and R[r].get('variant') == 'support']      # ⭐ V0.9.65
        if sup and numbered:
            main = [i + 1 for i, r in enumerate(refs) if R[r]['owner'] == c and R[r].get('variant') != 'support' and not R[r].get('no_face')]
            where += ((f"; {_nums(main)} {'is' if len(main) == 1 else 'are'} the main reference for clothes, hair and makeup" if main else '')
                      + f"; {_nums(sup)} {'is a close-up' if len(sup) == 1 else 'are close-ups'} of the face from other photos, for the facial features only")
        who.append(f"{C['name']}{desc}, {where}")
    if who:
        parts.append('People in this shot: ' + '; '.join(who) + ('. ' + FAITHFUL if faithful(st) else '. Keep each person exactly as in their references: face, hair, body, age, wardrobe.'))
        if any(R[r].get('variant') == 'clean' for r in refs):
            parts.append('Person reference images are cut out on a plain grey backdrop: the grey is NOT part of the scene; '
                         + ('place them in the location from the location reference, lit by its light.' if loc else
                            'place them in the background described above, lit by its light.'))
        parts.append(f"Only {' and '.join(name(c) for c in cast)} appear{'s' if len(cast) == 1 else ''} in this shot; no other people.")
    else:
        parts.append('No people in this shot: show only the place.')
    if continuity: parts.append(continuity)
    pos_start, pos_end = positions_text(st, spec, cast, name)                # 📍 V0.9.45
    if pos_start: parts.append(pos_start)
    for c in cast:                                                            # 🙈 V0.9.48
        if name(c) in (spec.get('hide_faces') or []):
            parts.append(HIDDEN.format(who=name(c), name=name(c)))
        elif name(c) in (spec.get('reveal') or []):
            parts.append(REVEAL.format(who=name(c)))
    if spec.get('actions'):
        parts.append('Action: ' + '; then '.join(unquote(a).rstrip('.') for a in spec['actions'][-3:]) + '.')
    if pos_end: parts.append(pos_end)
    if spec.get('place_view') and loc:                 # 📷 V0.9.46
        parts.append('The camera sees the location from exactly the viewpoint of the location reference image: the same camera position, '
                     'height, direction and lens, for the whole shot.')
        if spec.get('frame_lock'):
            parts.append('The camera never moves and the frame never changes: everything happens inside this one frame, and everyone stays inside it '
                         'unless the action has them walk in or out.')
    f = spec.get('framing'); fw = spec.get('framing_subject') or spec.get('subject') or (cast[0] if cast else None)
    parts.append((FRAMING_REAL_NONE if f is None and real_look(st) else FRAMING.get(f, FRAMING[None])).format(who=name(fw) if fw else 'the subject'))
    if spec.get('move'): parts.append(dv.MOVE_TEXT[spec['move']])
    elif lively(st) and not re.search(r'\bcamera\b', ' '.join(spec.get('actions') or []), re.I): parts.append(camera_line(st))
    subj, emo = spec.get('subject'), spec.get('emotion')
    if emo:
        parts.append(acted(name(subj), emo, spec.get('intensity', 'normal'), st) if subj else f"Emotional colour: {dv.emotion_phrase(emo, spec.get('intensity', 'normal'))}.")
    elif subj and spec.get('react'):
        parts.append(reacts(name(subj), st))
    for oc, oe in (spec.get('emotions') or {}).items():
        if oc in cast and oc != (subj if emo else None):
            parts.append(acted(name(oc), oe['emotion'], oe.get('intensity', 'normal'), st))
    if spec.get('duration') and spec['duration'] != 'full' and float(spec['duration']) < nfr / fmt['fps'] - 0.5:
        parts.append(f"The key moment should read clearly within about {float(spec['duration']):g} seconds.")
    if spec.get('pacing') == 'slower': parts.append('Unhurried timing; let moments breathe; pauses are welcome.')
    elif spec.get('pacing') == 'faster': parts.append('Brisk, responsive timing without dead air.')
    lines = []
    for line in spec.get('dialogue', [])[-3:]:
        sp = name(line['speaker']) if line.get('speaker') in chars else 'One of them'
        lines.append(f"{sp} says{(' ' + line['how']) if line.get('how') else ''}: \"{line['line']}\"")
    if spec.get('avoid'): parts.append('Avoid: ' + '; '.join(spec['avoid'][-6:]) + '.')
    if lessons and lessons.get('avoid'):
        parts.append("Lessons from takes the filmmaker rejected (avoid these): " + '; '.join(unquote(x) for x in lessons['avoid']) + '.')
    if lessons and lessons.get('keep'):
        parts.append("What the filmmaker liked in earlier takes (keep): " + '; '.join(unquote(x) for x in lessons['keep']) + '.')
    parts.append('Physically plausible motion, natural hands and anatomy, consistent lighting. End on a clear, readable moment '
                 '(no freeze). No cuts inside the shot, no text, no extra people, no generated earlier footage as reference.')
    # H3 makes the sound too: it must never speak the directions. Directions are labelled silent, only the
    # lines in the AUDIO section are dialogue, and nothing else in the prompt is in quotation marks.
    speech = st['settings'].get('speech', SPEECH_DEFAULT)
    if lines:
        audio = ('AUDIO (the only words spoken in this shot, each said once, naturally): ' + ' '.join(lines) +
                 (' Apart from these lines, only natural sound and non-verbal reactions.' if speech != 'natural' else
                  ' Anything else anyone says is brief, natural and in their own words, never the directions above.'))
    elif speech == 'silent':
        audio = 'AUDIO: no spoken words at all. Only natural sound: room tone, footsteps, breathing, non-verbal reactions.'
    elif speech == 'lines_only':
        audio = 'AUDIO: nobody speaks in this shot. Natural sound and non-verbal reactions only. ' + NOBODY_TALKS
    else:
        audio = ('AUDIO: people may talk naturally in their own words if the moment calls for it. Nobody says, reads out, narrates or '
                 'describes any of the visual direction above (no talk about the light, weather, camera, mood, setting or these instructions).')
    if audios:
        R = st['refs']; ch = st['characters']
        audio += ' ' + ' '.join(f"{ch[R[r]['owner']]['name']}'s voice: the same voice as reference audio {i + 1} (its sound only, never its words)."
                                for i, r in enumerate(audios) if R[r]['owner'] in ch)
    return 'VISUAL DIRECTION (silent: never spoken, read aloud or shown as text): ' + ' '.join(parts) + ' ' + audio
