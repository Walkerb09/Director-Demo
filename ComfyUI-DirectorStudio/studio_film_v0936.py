"""🎬 Make a film (V0.9.36): a brief and a total length -> Qwen plans every shot and writes each shot's H3 prompt.

Two steps, both by the Qwen3-VL you already run (one model load, see studio_film_worker_v0936.py):
  1. OUTLINE   Qwen looks at your ORIGINAL photos of the people and the places and plans the film as a shot list: the
               story beat of each shot, its framing, who is in it, where, any spoken line, and about how long it runs.
  2. SHOTS     each beat is written out as a full direction for H3 with the same rules as the ✨ rewrite (timed beats,
               visible acting, listeners reacting, no looks), knowing the shot before it and the one after.
Each shot's length is then estimated from the prompt Qwen really wrote (spoken words at ~2.5 a second, about a second per
action beat), so the film's running time follows the prompts. Everything is a SUGGESTION: it is shown to you, every shot
can be edited, and nothing is planned or generated until you press the buttons. Qwen's text gets the same checks as the
✨ rewrite (known people only, Clean-Chain wording, no looks). This module is pure Python: the Qwen worker imports it too.
"""
from __future__ import annotations
import json, re

import studio_rewrite_v0913 as rw

MIN_TOTAL, MAX_TOTAL = 10, 300            # seconds the film may be (10 s .. 5 min)
MAX_SHOTS = 48
MIN_SHOT, MAX_SHOT = 3.0, 10.0            # one H3 shot in the movie (generated up to ~10 s: studio_prompts LONG_LENGTHS)
AVG_SHOT = 5.5                            # what a lively shot with one beat or line usually needs
WORDS_PER_SECOND = 2.5
SECONDS_PER_BEAT = 1.0
# V0.9.54 a BIG action (crossing a room, sitting down, getting up, lying down, a hug, picking something up...) takes 2.5 s of
# real time; counting it as 1 s made shots too short for what they show, and H3 rushed or skipped the action
BIG_SECONDS = 2.5
BIG_ACTION = re.compile(r"\b(?:walk(?:s|ing)?|cross(?:es|ing)?|paces?|runs?|rush(?:es)?|sits? down|sits? up|stands? up|gets? (?:up|out of|into|off)|"
                        r"lies? (?:down|back)|lays? down|climbs?|kneels?(?: down)?|crouch(?:es)?(?: down)?|hugs?|embraces?|picks? up|puts? (?:on|down|away)|"
                        r"takes? off|opens?|closes?|comes? (?:in|through|over)|goes? (?:out|over|to)|leaves?|enters?|turns? around|rolls? over|"
                        r"dances?|spins?|jumps?|pours?|pulls? (?:on|off|out)|carr(?:y|ies)|lifts?)\b", re.I)
OUTLINE_TOKENS = 9000                     # V0.9.37 (was 4200): 48 shots of JSON fit; a cut-off reply still keeps its shots
SHOT_TOKENS = 900

def shot_range(total):
    """How many shots a film of `total` seconds should have: (fewest, most). V0.9.54: fewer, LONGER shots (about 6 to 10 s
    each; it was 4 to 8): one shot carries a whole moment, there are fewer joins for continuity to break at, and fewer takes
    to generate."""
    t = max(MIN_TOTAL, min(MAX_TOTAL, float(total)))
    return max(1, int(t / 10.0 + 0.5)), max(2, min(MAX_SHOTS, int(t / 6.0 + 0.5)))

def mmss(sec):
    sec = int(round(sec)); return f'{sec // 60}:{sec % 60:02d}'

OUTLINE_RULES = """You are a film director planning a short live-action film for MiniMax H3, a video model that makes each shot as
ONE continuous, uncut clip of 3 to 10 seconds from reference photos of the people and the place. You plan the shots; each
shot will later be written out in detail, so here you decide WHAT happens in each shot, in story order.
Rules:
- The whole film runs about {total} seconds ({mmss}): plan {lo} to {hi} shots whose "seconds" add up to about {total}.
- Tell a complete story from the director's brief: a build-up, a turn or a peak moment, and an ending that lands. Each shot
  moves the story forward; nothing is repeated.
- A LONGER FILM NEEDS MORE STORY, NOT LONGER NOTHING: first work out the chain of EVENTS the film needs, about one for every
  10 to 15 seconds. An event changes something: a discovery, a decision, a disagreement, a reveal, a gift, a mistake, a
  reaction that changes what happens next. Take them from the brief; when the brief has fewer, invent ones that grow
  naturally from it and its people (an obstacle, a complication, a small surprise, a callback to something earlier), in
  character and in the same places. Then give each event the shots it needs. Never fill time with shots where nothing new
  happens, and never repeat an event with other words.
- START IN THE ACTION: shot 1 already shows the first real event of the brief happening (someone doing or saying the first
  thing the story needs). Never open on people just standing, sitting still, looking around, arriving, waking up or "taking
  in" the place, and never on an empty or scene-setting shot: the photos already show the place.
- END ON THE ENDING: the last shot IS the story's final real moment (the outcome, the last line, the payoff). Never add a shot
  after it where people just stand, look at each other, walk off, "take it in" or nothing new happens. Never pad the film
  with such shots to reach its length: give the real shots more seconds instead.
- Cut like a real film: shot 1 can be wide so everyone's place is clear, but they are already doing the first action; then
  vary the framing (medium for action, close_up for feelings and important lines, over_shoulder for a conversation,
  two_shot for two people together).
- A REACTION PLAYS IN THE SAME SHOT as what causes it: the listener's face and body answer while the line is said or the
  action happens, with both people in that shot. Never plan a shot that is only someone reacting: every shot moves the
  story forward with a new action or line, and the reaction to it is part of that shot.
- Every shot is ONE continuous moment in ONE place: "people" lists EVERYONE on screen, including whoever watches, listens
  or reacts, not only who acts (only names from PEOPLE; at most 2), "place" is
  one of PLACES (or "" if there are none).
- "beat": 1 to 3 sentences, present tense, concrete and physical: what happens in this shot only, in the order it happens.
  Feelings are played big enough to read, as visible actions (she laughs out loud and grabs his arm; his face falls).
  Name people by name. NEVER describe how anyone looks (faces, hair, clothes, age): the photos give that.
- Spoken words go ONLY in "lines" (at most 2 per shot), exactly as they are said, with who says them and how ("laughing",
  "whispering", "shouting"). Keep lines short and natural: a line needs about 1 second per 2.5 words, plus a second.
  Only add lines the story needs; many shots have none. No narration, no voice-over, no on-screen text, no music cues.
- Only the people in PEOPLE appear: no extra people, no crowds, no animals or objects that the brief does not imply.
- PREFER FEWER, LONGER SHOTS: most shots run 6 to 10 seconds and carry a whole moment (several actions, a line and the
  reaction to it) in one continuous take. Cut only when the place, the time or the story's moment changes, or for a close_up
  the story really needs; never cut a continuous action into several short shots.
- "seconds": how long the shot runs in the film, 3 to 10. A big action (crossing a room, sitting down or getting up, lying
  down, a hug, picking something up and using it) takes 2 to 3 real seconds, a line about 1 second per 2.5 words plus a
  second: give each shot the time its actions really take, and split a shot that needs more than 10 seconds.
- "after": what is TRUE when this shot ends, for the shots that follow: where each person is and their posture (sitting
  on the bed, standing by the door), what they hold, have put down, put on or taken off, what changed in the room, what has
  now been said or found out, and how each one feels. Short plain facts; never how anyone looks.
- "hide_face": the people whose FACE is never shown in this shot (seen from behind, turned away or with the head out of
  frame), when the brief keeps someone's face hidden; usually empty. They are still in "people".
- How each shot JOINS the one before ("join"; the first shot has none), cut like a real editor:
  "cut" (the usual): the film cuts straight to this shot's action, already under way a moment later. People can be in new
  places, in the middle of what they do; the shot never spends time walking anyone into position. Cut on action (end a shot
  as a movement starts, begin the next one already into the next action) and change the framing at every cut (wide to
  close_up, medium to over_shoulder): two cuts in a row with the same framing of the same people look like a jump.
  "carry": the same action goes straight on from the shot before (a walk that continues, the same movement seen from
  another angle). Use it only when the action really continues.
  "skip": time has passed; "later" says how much ("15 minutes later", "that evening"). Show it through what is visible: a
  new activity, the light, a coat now off, a cup now empty, never on-screen text. Change the framing at a skip too.
Answer with JSON only, no other text:
{{"title": "<short title>", "logline": "<the story in one sentence>", "shots": [{{"seconds": 5, "framing": "wide|medium|over_shoulder|close_up|two_shot", "people": ["<name>"], "place": "<place or empty>", "beat": "<what happens>", "lines": [{{"speaker": "<name>", "how": "<how it is said, or empty>", "text": "<the words>"}}], "join": "cut|carry|skip", "later": "<for skip: how much later, else empty>", "after": "<what is true when this shot ends>", "hide_face": ["<name whose face is never shown in this shot>"]}}]}}"""

# V0.9.41 📷 ONE ANGLE for the whole film: every shot from the same fixed camera (like a stage play). Cuts between angles made
# H3 lose who is where; with one angle, people stay where they were across every cut and the story is told by what they do.
ANGLES = ('wide', 'medium', 'two_shot')
VARY_RULE = """- Cut like a real film: shot 1 can be wide so everyone's place is clear, but they are already doing the first action; then
  vary the framing (medium for action, close_up for feelings and important lines, over_shoulder for a conversation,
  two_shot for two people together).
- A REACTION PLAYS IN THE SAME SHOT as what causes it: the listener's face and body answer while the line is said or the
  action happens, with both people in that shot. Never plan a shot that is only someone reacting: every shot moves the
  story forward with a new action or line, and the reaction to it is part of that shot."""
ANGLE_RULE = """- ONE CAMERA ANGLE for the whole film: every shot is filmed from the same fixed camera, a {words}, standing still in the
  same place for the whole film, like a stage play seen from one seat. Use "framing": "{angle}" for EVERY shot. Tell the story
  through what people do, where they move inside that one frame and what they say; reactions play inside the same frame.
  Everyone stays inside the frame unless the story has them walk in or out."""
CAMERA_STILL = 'The camera stays static on a tripod.'
# 🎥 V0.9.50 one shot of a locked / one-angle film may move the camera: it still opens exactly on the film's picture (so the cut
# matches), then makes this one move; every other shot stays locked. What comes into view outside the picture is H3's own.
CAMERA_MOVES = {'pullback': 'The camera starts exactly on the opening frame, then slowly and steadily pulls back, widening to show more of the room around them.',
                'push': 'The camera starts exactly on the opening frame, then slowly and steadily pushes in toward them.',
                'pan_left': 'The camera starts exactly on the opening frame, then slowly pans to the left.',
                'pan_right': 'The camera starts exactly on the opening frame, then slowly pans to the right.',
                'handheld': 'The camera starts exactly on the opening frame, then is handheld, moving gently with the action.'}

CUT_REFRAMES = (" and change the framing at every cut (wide to\n  close_up, medium to over_shoulder): two cuts in a row with the same framing of the same people look like a jump.")
CUT_ONE_ANGLE = ('. The camera stays on the film\'s ONE angle at every cut: a cut only jumps ahead in the action, never to another '
                 'view of the room.')
SKIP_REFRAMES = ' Change the framing at a skip too.'

def angle_ok(angle):
    """None (Qwen varies the framing) or one of ANGLES; anything else -> ValueError."""
    if angle in (None, '', 'vary'): return None
    if angle not in ANGLES: raise ValueError('FILM_ANGLE_WIDE_MEDIUM_OR_TWO_SHOT')
    return angle

OPENING_LABEL = 'OPENING PICTURE (an ORIGINAL photo): the first shot of the film opens exactly on this picture'
OPENING_NOTE = ("The first shot opens exactly on the OPENING PICTURE: plan shot 1 to start from what it shows (who is in it, "
                "where they are, what they are doing), and only put people in shot 1 who are in that picture or walk in.")

# 🎬 V0.9.50 continuing a film you already made: Qwen gets the film so far (what each shot showed, as you wrote and kept it)
# and where it ends, and writes only what comes next. Text only: never a frame of a take.
CONTINUE_NOTE = ("THE FILM SO FAR (already filmed: part {part} of the film comes next; your shots come AFTER all of these. "
                 "Continue the story from where it ends; never repeat, redo or undo what already happened):\n{shots}\n"
                 "WHERE IT ENDS NOW: {ends}")

def so_far_text(film, ends='', limit=2600):
    """The film's shots so far as short numbered lines (the newest kept whole; the oldest folded into a count when long)."""
    rows = []
    for i, s in enumerate(film.get('shots') or []):
        src = s.get('beat') if (s.get('beat') and not s.get('edited')) else s.get('direction') or ''
        rows.append(f"{i + 1}. " + re.sub(r'\s*["\u201c][^"\u201d]*["\u201d]', '', ' '.join(str(src).split())).strip(' ,.;')[:240] + '.')
    keep = []
    for r in reversed(rows):
        if sum(len(x) + 1 for x in keep) + len(r) > limit: break
        keep.insert(0, r)
    if len(keep) < len(rows): keep.insert(0, f"(shots 1-{len(rows) - len(keep)}: earlier in the film)")
    parts = len(film.get('parts') or []) or 1
    return CONTINUE_NOTE.format(part=parts + 1, shots='\n'.join(keep), ends=ends or 'as the last shot above ends')

def outline_request(brief, total, *, cast, places, pictures=None, model=None, opening=False, angle=None, lead_in=None, so_far=None):
    """cast: [{'name', 'pronoun'}]; places: [{'name', 'description'}]; pictures: [(label, jpg)] of ORIGINAL photos only.
    opening=True (V0.9.38): one of the pictures is the OPENING PICTURE the film's first shot opens on."""
    brief = ' '.join(str(brief or '').split())
    if len(brief) < 8: raise ValueError('FILM_WRITE_A_BRIEF: say in a sentence or two what the film is about')
    if len(brief) > 4000: raise ValueError('FILM_BRIEF_TOO_LONG: up to 4000 characters')
    total = float(total)
    if not MIN_TOTAL <= total <= MAX_TOTAL: raise ValueError(f'FILM_LENGTH_{MIN_TOTAL}_TO_{MAX_TOTAL}_SECONDS')
    if not cast: raise ValueError('FILM_NEEDS_A_PERSON: add at least one person first')
    angle = angle_ok(angle)
    lo, hi = shot_range(total)
    who = '\n'.join(f"- {c['name']} ({c.get('pronoun') or 'they'})" for c in cast)
    where = '\n'.join(f"- {p['name']}" + (f": {p['description']}" if p.get('description') else '') for p in places) or '- (none: use the place behind the people in their own photos; leave "place" empty)'
    user = (f"PEOPLE:\n{who}\nPLACES:\n{where}\n\n" + (so_far + '\n\n' if so_far else '') +
            f"THE DIRECTOR'S BRIEF{' FOR WHAT HAPPENS NEXT' if so_far else ''}:\n{brief}\n\n" + (OPENING_NOTE + '\n\n' if opening else '') +
            (LEAD_NOTE.format(s=float(lead_in)) + '\n\n' if (opening and lead_in) else '') +
            f"Plan the film now: about {int(total)} seconds, {lo} to {hi} shots.")
    rules = OUTLINE_RULES.format(total=int(total), mmss=mmss(total), lo=lo, hi=hi)
    if angle:                                       # 📷 V0.9.41 one angle; V0.9.46 and a cut keeps it (it only jumps ahead in the action)
        rules = rules.replace(VARY_RULE, ANGLE_RULE.format(words=rw.FRAMING_WORDS[angle], angle=angle))
        rules = rules.replace(CUT_REFRAMES, CUT_ONE_ANGLE).replace(SKIP_REFRAMES, '')
    return {'mode': 'film_outline', 'system': rules + (rw.PICTURE_RULES if pictures else ''),
            'user': user, 'model_id': model or rw.MODEL_ID, 'images': [[str(l), str(p)] for l, p in (pictures or [])], 'max_new_tokens': OUTLINE_TOKENS}

def _norm_framing(f):
    f = str(f or 'medium').lower().replace('-', '_').replace(' ', '_')
    f = {'closeup': 'close_up', 'close': 'close_up', 'ots': 'over_shoulder', 'over_the_shoulder': 'over_shoulder', 'two': 'two_shot',
         'twoshot': 'two_shot', 'establishing': 'wide', 'wide_shot': 'wide', 'medium_shot': 'medium'}.get(f, f)
    return f if f in rw.FRAMINGS else 'medium'

def _objects_in(text, start):
    """V0.9.37: every COMPLETE {...} object at the top level of the list that begins at text[start] (aware of strings, so a
    brace inside a line of dialogue is not counted) -> [(json_text, end)], stopping where the reply was cut off."""
    out, depth, i0, in_s, esc = [], 0, None, False, False
    for j in range(start, len(text)):
        ch = text[j]
        if in_s:
            if esc: esc = False
            elif ch == '\\': esc = True
            elif ch == '"': in_s = False
            continue
        if ch == '"': in_s = True
        elif ch == '{':
            if depth == 0: i0 = j
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and i0 is not None: out.append((text[i0:j + 1], j + 1)); i0 = None
            if depth < 0: break
        elif ch == ']' and depth == 0: break
    return out

def _loads(t):
    """json.loads, then once more with the usual slips fixed (trailing commas, curly quotes around keys/values)."""
    try: return json.loads(t)
    except ValueError: pass
    t2 = re.sub(r',\s*([}\]])', r'\1', t)
    t2 = re.sub(r'(?<=[{\[,:])\s*[“”]|[“”]\s*(?=\s*[:,}\]])', '"', t2)
    return json.loads(t2)

def read_outline_json(reply):
    """V0.9.37: Qwen's outline reply -> (dict, cut_off). Tolerant of a <think> block, ```json fences, trailing commas and a
    reply that was CUT OFF part-way (the token limit): then the complete shots before the cut are kept and cut_off is True."""
    if not isinstance(reply, str): return reply, False
    t = re.sub(r'(?s)<think>.*?</think>', '', reply)
    t = re.sub(r'```(?:json|JSON)?', '', t)
    i = t.find('{')
    while i >= 0:                                     # the first object that parses (whole reply, not cut off)
        objs = _objects_in(t, i)
        if objs:
            try:
                j = _loads(objs[0][0])
                if isinstance(j, dict) and isinstance(j.get('shots'), list): return j, False
            except ValueError: pass
        i = t.find('{', i + 1)
    m = re.search(r'"shots"\s*:\s*\[', t)
    if not m: raise ValueError('FILM_OUTLINE_NOT_JSON')
    shots = []
    for s, _end in _objects_in(t, m.end()):
        try: shots.append(_loads(s))
        except ValueError: continue
    if not shots: raise ValueError('FILM_OUTLINE_NOT_JSON')
    head = t[:m.start()]
    def field(k):
        mm = re.search(r'"' + k + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
        try: return json.loads('"' + mm.group(1) + '"') if mm else ''
        except ValueError: return mm.group(1)
    return {'title': field('title'), 'logline': field('logline'), 'shots': shots}, True

def _match(raw, known):
    """A name Qwen wrote -> the project's name: exact (any case, stray punctuation), else a unique first name / word match."""
    k = re.sub(r'[^\w\s\'-]', '', str(raw or '')).strip().lower()
    if not k: return None
    if k in known: return known[k]
    k2 = re.sub(r'^(?:the|a|an)\s+', '', k)
    if k2 in known: return known[k2]
    w = set(k2.split())
    hits = {v for n, v in known.items() if n.split()[0] == k2.split()[0] or w <= set(n.split()) or set(n.split()) <= w}   # whole words only
    return hits.pop() if len(hits) == 1 else None

MAX_IN_SHOT = 2                              # H3 handles at most two people in one shot (the planner asks above that)

def named_in(text, names):
    """The project's people a beat names (whole words, outside quoted lines), in the order they appear: the full name, or a
    first name only one person has."""
    t = re.sub(r'["\u201c][^"\u201d]*["\u201d]', ' ', str(text or ''))
    firsts = {}
    for n in names: firsts.setdefault(n.split()[0].lower(), []).append(n)
    found = []
    for n in names:
        pats = [re.escape(n)] + ([re.escape(n.split()[0])] if len(firsts[n.split()[0].lower()]) == 1 and ' ' in n else [])
        m = [x.start() for p in pats for x in re.finditer(r'(?<![\w])' + p + r"(?![\w])", t, re.I)]
        if m: found.append((min(m), n))
    return [n for _, n in sorted(found)]

LEAVES = r'(?:leaves|leave|walks out|walk out|exits|goes out|storms out|heads out|hurries out|runs out|walks away|is gone|disappears|steps out)'

def leaves_in(text, names):
    """The people a beat has walk out (their name and a leaving verb in the same sentence)."""
    out = []
    for sent in re.split(r'(?<=[.!?;])\s+', re.sub(r'["\u201c][^"\u201d]*["\u201d]', ' ', str(text or ''))):
        if re.search(r'(?i)\b' + LEAVES + r'\b', sent): out += [n for n in named_in(sent, names) if n not in out]
    return out

# V0.9.44 ✂ how a film shot JOINS the one before. With Clean-Chain H3 never sees the shot before, so "start exactly where it
# ended" was fragile and spent the shot's seconds on walking into position. A CUT (the default) starts with the action under way;
# only the STATE carries over (who is in the scene, which side of the screen, what they hold). CARRY = the same action goes
# straight on (the old way). SKIP = time has passed ("15 minutes later"): people may be anywhere, doing something new.
JOINS = ('cut', 'carry', 'skip')
LEADS = (0, 1, 1.5, 2, 3)
LEAD_DEFAULT = 1.5
def lead_of(film):
    """⏱ V0.9.48 the film's lead-in for shots that open on a picture (seconds; 0 = none). Not set = the default."""
    v = (film or {}).get('lead_in', LEAD_DEFAULT)
    try: v = float(v)
    except (TypeError, ValueError): return LEAD_DEFAULT
    return v if v in LEADS else LEAD_DEFAULT

def join_of(join, later=''):
    """Qwen's / your join -> ('cut' | 'carry' | 'skip', later). Unknown -> 'cut'; a skip with no time says 'a while later'."""
    j = str(join or '').strip().lower().replace('_', ' ')
    j = {'carry on': 'carry', 'carries on': 'carry', 'continue': 'carry', 'continuous': 'carry', 'time skip': 'skip',
         'skip': 'skip', 'later': 'skip', 'jump': 'cut', 'cut': 'cut', 'carry': 'carry'}.get(j, 'cut')
    later = ' '.join(re.sub(r'["“”<>]', '', str(later or '')).split())[:60]
    if j == 'skip' and not later: later = 'a while later'
    return j, (later if j == 'skip' else '')

def stay_in_scene(shots, names):
    """V0.9.41: Qwen lists who ACTS in a shot; whoever else is in the room was dropped from it (no photos for H3, so they
    vanished). People stay in the scene from one shot to the next in the same place until a beat has them walk out; a
    close-up is one face and keeps only its own people. At most two people (H3). Adds to 'people_added'."""
    present = []
    for i, s in enumerate(shots):
        prev = shots[i - 1] if i else None
        if prev is None or (prev.get('place') or '') != (s.get('place') or '') or s.get('join') == 'skip': present = []   # a new place (or time): a new scene
        gone = leaves_in(prev['beat'], names) if prev else []
        present = [p for p in present if p not in gone]
        if s['framing'] != 'close_up':
            for n in present:
                if n not in s['people'] and len(s['people']) < MAX_IN_SHOT:
                    s['people'].append(n); s.setdefault('people_added', []).append(n)
        present = list(dict.fromkeys(present + s['people']))
    return shots

def _seconds(v):
    if isinstance(v, (int, float)): return float(v)
    m = re.search(r'\d+(?:\.\d+)?', str(v or ''))
    return float(m.group(0)) if m else AVG_SHOT

def check_outline(reply, *, names, places, total, forbidden=None, angle=None):
    """Qwen's outline -> {'title', 'logline', 'shots': [...], 'warnings'}. Unknown people are dropped (with a warning);
    broken JSON, no shots or Clean-Chain wording fail closed. V0.9.37: a reply cut off part-way keeps its complete shots."""
    j, cut = read_outline_json(reply)
    shots = j.get('shots') if isinstance(j, dict) else None
    if not isinstance(shots, list) or not shots: raise ValueError('FILM_OUTLINE_HAS_NO_SHOTS')
    known = {n.lower(): n for n in names}; pknown = {p.lower(): p for p in places}
    warn, out, dropped = [], [], set()
    for s in shots[:MAX_SHOTS]:
        if not isinstance(s, dict): continue
        beat = ' '.join(str(s.get('beat') or s.get('direction') or s.get('action') or '').split())
        if len(beat) < 8: continue
        ppl = []
        pp = s.get('people') or []
        if isinstance(pp, str): pp = re.split(r'\s*(?:,|&|\band\b)\s*', pp)
        for p in pp:
            n = _match(p, known)
            if n and n not in ppl: ppl.append(n)
            elif not n and str(p).strip(): dropped.add(str(p).strip()[:40])
        lines = []
        ll = s.get('lines') or []
        if isinstance(ll, dict): ll = [ll]
        for l in (ll if isinstance(ll, list) else [])[:2]:
            if not isinstance(l, dict): continue
            text = ' '.join(str(l.get('text') or l.get('line') or '').replace('"', '').replace('“', '').replace('”', '').split())
            sp = _match(l.get('speaker') or l.get('who'), known)
            if not text: continue
            if not sp: dropped.add(str(l.get('speaker') or '?')[:40]); continue
            how = ' '.join(str(l.get('how') or '').replace('"', '').split())[:60]
            lines.append({'speaker': sp, 'how': how, 'text': text[:300]})
            if sp not in ppl: ppl.append(sp)
        added = []                                   # V0.9.41: Qwen's "people" list often has only the focus while the beat names
        framing = angle or _norm_framing(s.get('framing'))   # 📷 one angle: every shot keeps it, whatever Qwen wrote; both ("Maya hands Grandpa the letter"): everyone the beat names is in the shot,
        for n in (named_in(beat, names) if framing != 'close_up' else []):   # so H3 gets their photos and 🔍 looks for them
            if n not in ppl and len(ppl) < MAX_IN_SHOT: ppl.append(n); added.append(n)   # (a close-up is one face: not added)
        place = _match(s.get('place'), pknown) if pknown else None
        sec = _seconds(s.get('seconds'))
        jn, later = join_of(s.get('join'), s.get('later')) if out else (None, '')        # V0.9.44 ✂ how it joins the shot before
        hf = s.get('hide_face') or []                                                     # V0.9.48 🙈 faces never shown
        hf = [n for n in (_match(x, known) for x in (hf if isinstance(hf, list) else [hf])) if n]
        after = ' '.join(str(s.get('after') or '').replace('"', '').replace('“', '').replace('”', '').split())[:400]   # V0.9.54
        if after and forbidden is not None and forbidden.search(after): after = ''
        out.append({'framing': framing, 'people': ppl[:MAX_IN_SHOT], 'place': place, 'beat': beat[:900], 'lines': lines, **({'after': after} if after else {}),
                    'seconds_planned': max(MIN_SHOT, min(MAX_SHOT, sec)), **({'people_added': added} if added else {}),
                    **({'join': jn, 'later': later} if jn else {}), **({'hide_face': hf} if hf else {})})
    if not out: raise ValueError('FILM_OUTLINE_HAS_NO_USABLE_SHOTS')
    stay_in_scene(out, names)                                     # V0.9.41: who is in the room stays in the shot
    if cut: warn.append(f"Qwen's plan was cut off after {len(out)} shots (it ran out of room); those are kept. The ending may be missing: check the last shot, or add one.")
    if forbidden is not None:
        for s in out:
            m = forbidden.search(s['beat'] + ' ' + ' '.join(l['text'] for l in s['lines']))
            if m: raise ValueError('FILM_REFUSED_CLEAN_CHAIN: Qwen wrote "' + m.group(0).strip()[:60] + '"')
    if dropped: warn.append('Qwen named people who are not in this project, so they were left out: ' + ', '.join(sorted(dropped)[:6]))
    fixed = [f"shot {i + 1} ({', '.join(x['people_added'])})" for i, x in enumerate(out) if x.get('people_added')]
    if fixed: warn.append("Qwen's plan left people out of shots they are in (named in the beat, or still in the room from the shot before), "
                          "so they were added (H3 gets their photos and 🔍 looks for them): " + '; '.join(fixed[:8]))
    if len(shots) > MAX_SHOTS: warn.append(f'Qwen planned {len(shots)} shots; the first {MAX_SHOTS} are kept.')
    title = ' '.join(str((j or {}).get('title') or '').split())[:80] or 'Untitled film'
    return {'title': title, 'logline': ' '.join(str((j or {}).get('logline') or '').split())[:300], 'shots': out, 'warnings': warn, 'target': float(total)}

def notes_of(shot):
    """The outline beat as director's notes for the shot writer: the beat, then each line with who says it and how."""
    t = shot['beat'].rstrip('.') + '.'
    for l in shot['lines']:
        t += f" {l['speaker']} says" + (f", {l['how']}," if l.get('how') else '') + f' "{l["text"]}"'
    return t

FRAMING_TO_MOTION = {'close_up': 'close', 'medium': 'medium', 'wide': 'wide', 'over_shoulder': 'ots', 'two_shot': 'medium'}

OPENS_ON_STAND = (' This shot opens exactly on the START PICTURE (one of the director\'s original photos): start from what it shows, '
                  'who is in it and where they are; anyone not in it walks in during the shot.')

CUT_STAND = (' This shot CUTS straight to its action, already under way a moment after the shot before: begin in the middle of '
             'the action, with everyone already where this action needs them. Say where they are relative to each other and the '
             'place; the Director keeps everyone on the same side of the screen as before. The shot shows the action itself, from '
             'its first second.')
SKIP_STAND = (' TIME HAS PASSED since the shot before ({later}). Begin with what is happening NOW: people can be somewhere else '
              'in the place, doing something new; say where they are and what they are doing. Show the time that passed only '
              'through what is visible (a new activity, the light, what they hold or wear), never as on-screen text.')

HIDE_STAND = (' 🙈 {who}\'s FACE is never shown in this shot: write {who} seen from behind, turned away from the camera, or '
              'with the head out of frame, the whole time; describe only the body, the hands and what {who} do{s}.')
REVEAL_STAND = (' 👁 THE REVEAL: this is the first shot where {who}\'s face is seen. Make it a clear moment: {who} turns toward the '
                'camera (or into the light) so the face is seen plainly, and the other person reacts to it.')
LEAD_STAND = (' ⏱ LEAD-IN: this shot gets {s:g} extra seconds at its very start to move everyone calmly and directly from where they '
              'are in the START PICTURE to where this shot needs them; those seconds are cut off in the film. Write that move first, '
              'in one short sentence, then the shot\'s action. The action itself still fills the shot\'s own seconds.')
LEAD_NOTE = ('A shot that opens on a picture gets {s:g} extra seconds at its start for people to move into place (cut off in the film): '
             'plan its beat for its own seconds.')

# V0.9.54 continuity for the shot writer: everything that already happened (never shown again), what is TRUE as this shot
# begins (from the plan's "after" of the shot before), what must be true when it ends, and what is still to come (not here)
ALREADY = 'ALREADY HAPPENED earlier in the film, in order (never show any of it again): {shots}'
NOW_TRUE = ('WHERE THINGS STAND as this shot begins (from the film\'s plan; keep all of it true unless this shot changes it): {after}.')
NOW_TRUE_SKIP = ('Before the time skip, things stood like this: {after}. After the skip, keep what would still be true (what was '
                 'said, found out or decided, how people feel about it); what people hold and where they are can have changed.')
THIS_ENDS = 'By the end of this shot this must be true (the next shots start from it): {after}.'
STILL_TO_COME = 'STILL TO COME in later shots (none of it happens here): {shots}'
def _short(s, n=110):
    b = re.sub(r'\s*["\u201c][^"\u201d]*["\u201d]', '', ' '.join(str(s.get('beat') or s.get('direction') or '').split())).strip(' ,.;')
    return (b[:n].rsplit(' ', 1)[0] + '…') if len(b) > n else b + '.'

def shot_requests(outline, *, cast, place_names=None, motion='lively', model=None, opens_on=None, angle=None, lead_in=None):
    """One shot-writer request per outline shot (the ✨ rewrite's rules), each knowing the shot before and the shot after.
    opens_on (V0.9.38): {shot index (0-based): [label, jpg]} = that shot opens exactly on this ORIGINAL picture, so where
    everyone starts IS known (from the picture), and Qwen sees it."""
    shots = outline['shots']; byname = {c['name']: c for c in cast}; out = []; opens_on = {int(k): v for k, v in (opens_on or {}).items()}
    kept_hidden = set()                                  # 🙈 V0.9.48 faces hidden so far (the first shot that shows one again is its reveal)
    for i, s in enumerate(shots):
        pic = opens_on.get(i)
        who = [byname[n] for n in s['people'] if n in byname] or cast[:1]
        stand = f"This is shot {i + 1} of {len(shots)} of a short film" + (f": {outline['logline'].rstrip('.')}" if outline.get('logline') else '') + '.'
        if i:                                            # V0.9.54 the whole story so far, not only the shot before
            if i > 1: stand += ' ' + ALREADY.format(shots=' '.join(f"({k + 1}) {_short(shots[k])}" for k in range(max(0, i - 8), i - 1)))
            stand += ' Just before this shot: ' + re.sub(r'"[^"]*"', '', notes_of(shots[i - 1])).strip()
            if shots[i - 1].get('after') and (s.get('join') or 'cut') != 'skip': stand += ' ' + NOW_TRUE.format(after=shots[i - 1]['after'].rstrip('.'))
            elif shots[i - 1].get('after'): stand += ' ' + NOW_TRUE_SKIP.format(after=shots[i - 1]['after'].rstrip('.'))
        jn = (s.get('join') or 'cut') if i else None
        pos = s.get('positions') if isinstance(s.get('positions'), dict) else None     # 📍 V0.9.45 where you set everyone
        if pos and pos.get('start'):
            import studio_positions_v0945 as pm
            stand += ' ' + pm.for_qwen(pos, pronoun=lambda n: (byname.get(n) or {}).get('pronoun') or 'they')
        elif pos and pos.get('end'):
            import studio_positions_v0945 as pm
            stand += ' ' + pm.for_qwen({'end': pos['end']}, pronoun=lambda n: (byname.get(n) or {}).get('pronoun') or 'they')
        hidden = [n for n in (s.get('hide_face') or []) if n in byname]
        if hidden: stand += HIDE_STAND.format(who=' and '.join(hidden), s='' if len(hidden) > 1 else 's')   # 🙈 V0.9.48
        revealed = [n for n in s['people'] if n in kept_hidden and n not in hidden]
        if revealed: stand += REVEAL_STAND.format(who=' and '.join(revealed))
        kept_hidden = (kept_hidden - set(revealed)) | set(hidden)
        if pic and lead_in: lead_txt = LEAD_STAND.format(s=float(lead_in))           # ⏱ V0.9.48
        else: lead_txt = ''
        if pos and pos.get('start'): stand += lead_txt
        elif pic: stand += OPENS_ON_STAND + lead_txt
        elif jn == 'skip': stand += SKIP_STAND.format(later=s.get('later') or 'a while later')
        elif jn == 'cut': stand += CUT_STAND
        elif i: stand += (' The shots before this one are not generated yet, so where everyone is at the very start of this shot is '
                        'NOT known: do not say where anyone starts, stands or faces at the beginning; describe what happens and where '
                        'people end up. The Director adds the starting positions from how the previous shot really ends.')
        if i + 1 < len(shots): stand += ' (The NEXT shot will show: ' + re.sub(r'"[^"]*"', '', shots[i + 1]['beat']).strip().rstrip('.') + '. Do not do that here.)'
        if i + 2 < len(shots): stand += ' ' + STILL_TO_COME.format(shots=' '.join(f"({k + 1}) {_short(shots[k])}" for k in range(i + 2, min(len(shots), i + 6))))
        if s.get('after'): stand += ' ' + THIS_ENDS.format(after=s['after'].rstrip('.'))
        req = rw.build_request(notes_of(s), mode='shot', cast=[{'name': c['name'], 'pronoun': c.get('pronoun')} for c in who],
                               place=s.get('place'), where_things_stand=stand, seconds=s['seconds_planned'], model=model, motion=motion,
                               framing=FRAMING_TO_MOTION.get(angle or s['framing']), camera_chosen=bool(angle),
                               start_known=(True if (i == 0 or pic or jn == 'skip' or (pos and pos.get('start'))) else ('relative' if jn == 'cut' else False)),
                               pictures=[(str(pic[0]), str(pic[1]))] if (pic and pic[1]) else None)   # no picture file: the words only, never an empty path
        req['max_new_tokens'] = SHOT_TOKENS
        out.append(req)
    return out

_QUOTE = re.compile(r'["“]([^"”]+)["”]')
def estimate_seconds(direction):
    """How long a shot needs from what its prompt says: spoken words at ~2.5 a second (+1.2 s to react around them) or about
    a second per action beat, whichever is longer, +0.5 s; 3 to 10 s, in half seconds."""
    d = direction or ''
    words = sum(len(m.split()) for m in _QUOTE.findall(d))
    rest = _QUOTE.sub('. ', d)                         # a line ends a beat
    sents = [x for x in re.split(r'(?<=[.!?;])\s+', rest) if len(x.split()) >= 3]
    beats = [x for x in sents if not re.match(r'(?i)\s*(the camera|camera|natural framing|wide shot|medium shot|close-up|over the shoulder|waist-up)\b', x)]
    beats_n = len(beats) + sum(len(re.findall(r'(?i)\b(?:then|as|while|before|after|until)\b', x)) // 2 for x in beats)
    big = sum(len(BIG_ACTION.findall(x)) for x in beats)                     # V0.9.54 big actions get their real time
    action = max(beats_n * SECONDS_PER_BEAT, big * BIG_SECONDS + max(0, beats_n - big) * 0.5)
    need = max(action, (words / WORDS_PER_SECOND + 1.2) if words else 0) + 0.5
    return max(MIN_SHOT, min(MAX_SHOT, round(need * 2) / 2))

def lead(framing, people):
    """The framing words that lead the planned shot's text (as the scene planner does)."""
    return rw.FRAMING_WORDS[framing] + (' of ' + ' and '.join(people) if people and framing in ('wide', 'medium', 'two_shot', 'close_up') else '')

def assemble(outline, replies, *, names, forbidden=None):
    """Outline + Qwen's shot replies -> the film you see: each shot's direction (checked like a ✨ rewrite; the outline beat
    stands in if Qwen's version is unusable), its estimated seconds and the running total."""
    shots = []
    for i, s in enumerate(outline['shots']):
        notes = notes_of(s); warn = []; unwritten = i >= len(replies)
        if unwritten:
            direction = notes; warn.append("Qwen didn't get to write this shot out, so the planned beat is used: press ✍ Qwen rewrites to have it written, or edit it")
        else:
            try:
                r = rw.check(replies[i], mode='shot', original=notes, names=names, forbidden=forbidden)
                direction = r['direction']; warn += r['warnings']
            except (ValueError, KeyError, TypeError) as e:
                direction = notes; warn.append(f"Qwen's written-out version was not usable ({str(e)[:80]}), so the planned beat is used: check it")
        shots.append({'n': i + 1, 'framing': s['framing'], 'people': s['people'], 'place': s.get('place'), 'beat': s['beat'], 'lines': s['lines'],
                      **({'join': s.get('join') or 'cut', 'later': s.get('later') or ''} if i else {}),
                      **({'hide_face': list(s['hide_face'])} if s.get('hide_face') else {}), **({'after': s['after']} if s.get('after') else {}),
                      'direction': direction, 'seconds_planned': s['seconds_planned'], 'seconds': estimate_seconds(direction), 'auto': True, 'warnings': warn,
                      **({'unwritten': True} if unwritten else {})})
    return summarize({'title': outline['title'], 'logline': outline.get('logline', ''), 'target': outline['target'], 'shots': shots,
                      'warnings': list(outline.get('warnings') or [])})

# V0.9.53 a first or last shot where nothing happens (Qwen's habit: an "establishing" opener, an epilogue after the ending).
# Plain words on Qwen's beat: a hint for you to look, never a change (Qwen's words are a suggestion, and so is this check)
IDLE = re.compile(r"\b(?:stand(?:s|ing)? (?:still|there|quietly|in silence)|look(?:s|ing)? around|look(?:s|ing)? at each other|"
                  r"take(?:s|ing)? (?:it|in) (?:all )?in|taking in|in silence|silently|walk(?:s|ing)? (?:away|off|out)|"
                  r"the (?:room|house|place) (?:is |falls |goes )?(?:quiet|still|silent|empty)|settles? in|linger(?:s|ing)?|"
                  r"arriv(?:es|ing)|wak(?:es|ing) up|sits? (?:quietly|still)|stares? (?:into space|ahead|out))\b", re.I)
def idle_warnings(film):
    out = []; shots = film.get('shots') or []
    for i in ([0, len(shots) - 1] if len(shots) > 1 else [0]):
        s = shots[i]; beat = str(s.get('beat') or s.get('direction') or '')
        if s.get('lines') or not IDLE.search(beat): continue
        out.append(f"Shot {i + 1} may be a shot where nothing much happens ({'an opener before the action' if i == 0 else 'after the ending'}): "
                   f"\"{beat[:120]}\". Press ✍ Qwen rewrites on it, edit it, or remove it" + (" (the film ends on the shot before)." if i else "."))
    return out

def thin_brief(brief, total):
    """V0.9.54 a short brief for a long film: Qwen has to invent most of it, which is where it struggles. -> a plain hint or ''."""
    words = len(str(brief or '').split()); total = float(total or 0)
    if total < 45 or words >= total / 2.5: return ''
    return (f"Your brief is {words} words for a {mmss(total)} film, so Qwen had to invent most of what happens. For longer films, "
            f"say more of what happens (the events in order), or make it in parts: write the first 20-30 seconds, then ▶ Continue "
            f"the film from the Films shelf for each next part.")

def summarize(film):
    total = sum(float(s['seconds']) for s in film['shots'])
    film['total'] = round(total, 1)
    off = total - film['target']
    film['fit'] = ('on target' if abs(off) <= max(5.0, 0.1 * film['target']) else
                   (f'{mmss(-off)} short of {mmss(film["target"])}: add a shot or ask for more' if off < 0 else
                    f'{mmss(off)} over {mmss(film["target"])}: drop or shorten a shot'))
    return film

def clean_edited(film):
    """A film as you edited it in the page -> checked shots (directions, seconds, framing, people, place)."""
    if not isinstance(film, dict) or not isinstance(film.get('shots'), list) or not film['shots']: raise ValueError('FILM_HAS_NO_SHOTS')
    if len(film['shots']) > MAX_SHOTS: raise ValueError(f'FILM_AT_MOST_{MAX_SHOTS}_SHOTS')
    out = []
    for i, s in enumerate(film['shots']):
        d = ' '.join(str(s.get('direction') or '').split())
        if len(d) < 5: raise ValueError(f'FILM_SHOT_{i + 1}_IS_EMPTY: write what happens in it, or remove it')
        auto = s.get('auto', True) is not False
        try: sec = float(s.get('seconds'))
        except (TypeError, ValueError): sec = None
        sec = estimate_seconds(d) if (auto or sec is None) else max(MIN_SHOT, min(MAX_SHOT, sec))
        out.append({'n': i + 1, 'framing': _norm_framing(s.get('framing')), 'people': [str(p) for p in (s.get('people') or [])][:3],
                    'place': s.get('place') or None, 'direction': d, 'seconds': sec, 'auto': auto,
                    'start_frame': str(s['start_frame']) if s.get('start_frame') else None,
                    **({'positions': s['positions']} if isinstance(s.get('positions'), dict) and s['positions'] else {}),   # 📍 V0.9.45
                    **({'hide_face': [str(x) for x in s['hide_face']][:2]} if isinstance(s.get('hide_face'), list) and s['hide_face'] else {}),
                    **({'camera': s['camera']} if s.get('camera') in CAMERA_MOVES else {}),                                  # 🎥 V0.9.50
                    **(dict(zip(('join', 'later'), join_of(s.get('join'), s.get('later')))) if i else {})})
    return out

STOP_WHY = {'busy': 'ComfyUI got work (you pressed Generate), so Qwen stepped aside for H3',
            'timeout': 'it took longer than the time limit', 'failed': 'the Qwen worker stopped with an error'}

def run(request, *, qwen_python, on_progress=None, timeout=5400, busy=None, control=None):
    import studio_qwenmodel_v0916 as _qm; qwen_python = _qm.python_for(request.get('model_id'), qwen_python)   # V0.9.55 Qwen3.6: its own environment
    """Run the film worker in the Qwen environment -> {'outline_reply', 'shot_replies', 'model_id'}. on_progress(dict) is
    called as Qwen works. busy(): True when ComfyUI got work; Qwen is then stopped at once so H3 gets the GPU.
    V0.9.37: when Qwen stops part-way (busy, the time limit, a crash) AFTER the outline was written, what it wrote so far is
    returned with 'stopped' = why (the film keeps those shots; the rest use the planned beat). Before that it is an error.
    DIRECTOR_FILM_FAKE=<json file with those keys> (tests only) answers instead of Qwen; with request['shots_only'] the
    fake's 'rewrite_replies' (or 'shot_replies') answer."""
    import os, signal, subprocess, tempfile, time
    from pathlib import Path
    fake = os.environ.get('DIRECTOR_FILM_FAKE')
    if fake:
        j = json.loads(Path(fake).read_text())
        if request.get('shots_only'):
            rr = list(j.get('rewrite_replies') or j.get('shot_replies') or [])
            j = {'model_id': j.get('model_id'), 'shot_replies': [rr[i % len(rr)] if rr else '' for i in range(len(request['shots_only']))]}
        if on_progress: on_progress({'stage': 'done', 'done': len(j.get('shot_replies') or []), 'total': len(j.get('shot_replies') or [])})
        return j
    if not Path(qwen_python).is_file(): raise RuntimeError('QWEN_NOT_INSTALLED: the Qwen Python environment (h3-qwen-venv) was not found')
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as t:
        rq = Path(t) / 'req.json'; out = Path(t) / 'out.json'; prog = Path(t) / 'progress.json'; part = Path(t) / 'partial.json'
        extra = {}
        if request.get('think'):                 # V0.9.39 🧠 a Thinking model: more time, and its reasoning is kept for you to read
            import studio_thinking_v0939 as _th, studio_qwenmodel_v0916 as _qm0
            timeout = max(timeout, _th.THINK_REWRITE_TIMEOUT if request.get('shots_only') else _th.THINK_TIMEOUT)
            try: d0 = _qm0.log_dir(); d0.mkdir(parents=True, exist_ok=True); extra['think_log'] = str(d0 / 'film_thinking_last.txt')
            except OSError: pass
        if control: extra['control'] = str(control)         # V0.9.43 🧠✋ your notes while Qwen works (the page writes them here)
        rq.write_text(json.dumps({**request, **extra, 'progress': str(prog), 'partial': str(part), 'package_dir': str(here)}))
        env = dict(os.environ)
        env.pop('LD_PRELOAD', None)             # V0.9.38: ComfyUI's tcmalloc preload segfaults Qwen's generate
        import studio_qwenmodel_v0916 as _qm
        env['HF_HOME'] = str(_qm.hf_home(request.get('model_id'))); env.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
        errf = open(Path(t) / 'err.txt', 'w+')
        p = subprocess.Popen([str(qwen_python), '-I', str(here / 'studio_film_worker_v0936.py'), str(rq), str(out)], stdout=errf,
                             stderr=subprocess.STDOUT, env=env, start_new_session=True)
        start = time.time(); last = None; stop = None
        def kill():
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: return
            try: p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                p.wait()
        try:
            while p.poll() is None:
                if time.time() - start > timeout: stop = 'timeout'
                elif busy:
                    try: stop = 'busy' if busy() else None
                    except Exception: stop = None
                if stop: kill(); break
                if on_progress and prog.is_file():
                    try:
                        now = prog.read_text()
                        if now != last: last = now; on_progress(json.loads(now))
                    except ValueError: pass
                time.sleep(2)
        finally:
            if p.poll() is None: kill()
        if not stop and p.returncode == 0 and out.is_file(): errf.close(); return json.loads(out.read_text())
        stop = stop or 'failed'
        errf.seek(0); tail = _qm.why_failed(p.returncode, errf.read(), 'film') if stop == 'failed' else ''   # V0.9.38: how it ended + the whole output kept
        errf.close()
        try: got = json.loads(part.read_text()) if part.is_file() else None
        except ValueError: got = None
        if got and (got.get('outline_reply') or request.get('shots_only')) and not got.get('outline_error'):
            got['stopped'] = STOP_WHY[stop] + (f' ({tail})' if stop == 'failed' and tail else '')
            got['stopped_kind'] = stop
            return got
        if stop == 'busy': raise RuntimeError('QWEN_FILM_STOPPED_COMFYUI_GOT_WORK: Qwen stepped aside for H3 before the plan was written; write the film again when ComfyUI is idle')
        if stop == 'timeout': raise RuntimeError('QWEN_FILM_TOOK_TOO_LONG')
        raise RuntimeError('QWEN_FILM_FAILED: ' + (tail or f'exit {p.returncode}'))

NOTES_HEAD = "THE DIRECTOR'S NOTES (added while you were working; follow them, they override anything else here): "
def with_notes(req, notes):
    """V0.9.43 ✋: the director's notes -> the end of a request's user text (the outline's notes go to every shot; a shot's
    own notes stay with it for a later ✍ rewrite)."""
    notes = [' '.join(str(n).split())[:300] for n in (notes or []) if str(n).strip()]
    if not notes: return req
    return {**req, 'user': str(req.get('user') or '') + '\n\n' + NOTES_HEAD + ' '.join(f'({k + 1}) {n}' for k, n in enumerate(notes))}

def keep_notes(film, notes):
    """The worker's [{'step': 'outline' | shot number, 'text'}] -> kept in the film: film['notes'] and each shot's 'notes'."""
    for x in notes or []:
        if not isinstance(x, dict) or not str(x.get('text') or '').strip(): continue
        text = ' '.join(str(x['text']).split())[:300]; step = x.get('step')
        if step == 'outline': film.setdefault('notes', []).append(text)
        elif isinstance(step, int) and 1 <= step <= len(film.get('shots') or []): film['shots'][step - 1].setdefault('notes', []).append(text)
    return film

def rewrite_requests(film, numbers, *, cast, motion='lively', model=None, opens_on=None):
    lead = lead_of(film)
    angle = angle_ok(film.get('angle'))
    """V0.9.37 ✍: the film you see -> shot-writer requests for just the shots `numbers` (1-based), each knowing its
    neighbours as they stand NOW (your edits included). Uses each shot's planned beat and lines, or your direction when
    you have changed it."""
    shots = film.get('shots') or []
    nums = sorted({int(n) for n in numbers if 1 <= int(n) <= len(shots)})
    if not nums: raise ValueError('FILM_PICK_A_SHOT_TO_REWRITE')
    ol = {'logline': film.get('logline', ''), 'shots': []}
    for s in shots:
        beat = s.get('beat') or s.get('direction') or ''
        if s.get('edited'): beat = s.get('direction') or beat               # your words win over the old beat
        jn, later = join_of(s.get('join'), s.get('later'))
        ol['shots'].append({'join': jn, 'later': later, **({'positions': s['positions']} if isinstance(s.get('positions'), dict) and s['positions'] else {}),
                            **({'hide_face': list(s['hide_face'])} if s.get('hide_face') else {}), **({'after': s['after']} if s.get('after') else {}),
                            'framing': _norm_framing(s.get('framing')), 'people': list(s.get('people') or []), 'place': s.get('place'),
                            'beat': ' '.join(str(beat).split()), 'lines': [] if s.get('edited') else list(s.get('lines') or []),
                            'seconds_planned': float(s.get('seconds_planned') or s.get('seconds') or AVG_SHOT)})
    allreq = shot_requests(ol, cast=cast, motion=motion, model=model, opens_on={n - 1: v for n, v in (opens_on or {}).items()}, angle=angle, lead_in=lead)
    return nums, [with_notes(allreq[n - 1], list(film.get('notes') or []) + list(shots[n - 1].get('notes') or [])) for n in nums]   # V0.9.43 ✋

def apply_rewrites(film, numbers, replies, *, names, forbidden=None, stopped=None):
    """Qwen's new versions of shots `numbers` -> the film with those shots replaced (each checked like a ✨ rewrite; a
    shot whose new version is unusable, or that Qwen didn't get to, keeps what it had, with a note)."""
    shots = film['shots']
    for k, n in enumerate(numbers):
        s = shots[n - 1]
        if k >= len(replies):
            s['warnings'] = [f'Qwen stopped before rewriting this shot ({stopped or "no reply"}); it is unchanged.']; continue
        notes = notes_of({'beat': s.get('beat') or s['direction'], 'lines': s.get('lines') or []})
        try:
            r = rw.check(replies[k], mode='shot', original=notes, names=names, forbidden=forbidden)
        except (ValueError, KeyError, TypeError) as e:
            s['warnings'] = [f"Qwen's new version was not usable ({str(e)[:80]}); the shot is unchanged."]; continue
        s['direction'] = r['direction']; s['warnings'] = list(r['warnings']); s['edited'] = False; s.pop('unwritten', None)
        if s.get('auto', True): s['seconds'] = estimate_seconds(s['direction'])
    return summarize(film)
