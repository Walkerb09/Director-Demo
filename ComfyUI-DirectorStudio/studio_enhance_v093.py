"""Prompt enhancer: turn any direction into the explicit, physical description MiniMax H3 follows best.

The official MiniMax-H3 reference guide asks the detailed_description to be "as detailed and explicit as possible":
composition, subjects' appearance and position, environment and lighting, actions and state changes, camera
movement (type, amplitude and speed), and the current sound; about 350-500 words; never a plot summary.

Rules this enhancer keeps (so it works with ANY sentence and never changes what you asked for):
  * your words stay exactly as written; it only ADDS separate, plain sentences after them;
  * it never adds spoken words, quotation marks, people, props or places you didn't mention;
  * it never mentions frames from generated takes (Clean-Chain) and never contradicts your framing/camera chips;
  * every addition is listed, so the plan card can show what was added.
It is a phrasebook, not a language model: deterministic, instant, and it can't invent story.
"""
from __future__ import annotations
import re

# action -> (label, how it looks, what it sounds like)
ACTIONS = [
    (r'\b(walk(s|ed|ing)?|stroll(s|ing)?|step(s|ping)? (forward|in|towards|toward))\b', 'walking',
     'The walk is unhurried and natural: weight shifts from foot to foot, arms swing loosely, shoulders stay relaxed and the head stays level.',
     'soft, regular footsteps'),
    (r'\b(run(s|ning)?|jog(s|ging)?|sprint(s|ing)?|rush(es|ing)?)\b', 'running',
     'The run is athletic and believable: knees lift, arms pump in rhythm, clothing and hair move with each stride.', 'quick footsteps and breathing'),
    (r'\b(sit(s|ting)? down|sits?|seated|take(s)? a seat)\b', 'sitting',
     'Sitting down is controlled and natural: a hand finds support, the body lowers in one smooth movement and settles.', 'a soft creak as the seat takes the weight'),
    (r'\b(stand(s|ing)? up|get(s)? up|rise(s)?)\b', 'standing up',
     'Standing up is one smooth push from the legs, with a small shift of balance before settling.', 'clothing rustle'),
    (r'\b(turn(s|ing)? (around|to|towards|toward|away)|turns?)\b', 'turning',
     'The turn leads with the eyes, then the head, then the shoulders, in one fluid motion.', None),
    (r'\b(look(s|ing)? at|glance(s|d)? at|stare(s)? at|gaze(s)? at|watch(es|ing)?)\b', 'looking',
     'The eyes move first and settle; the gaze is steady and motivated, with natural blinks.', None),
    (r'\b(look(s|ing)? (around|up|down|away)|glance(s)? (around|away))\b', 'looking around',
     'The eyes and head move together in small, natural steps, pausing briefly on each point of interest.', None),
    (r'\b(hug(s|ging)?|embrace(s|d|ing)?)\b', 'hugging',
     'The hug is warm and real: arms wrap around the back, hands rest gently, heads tilt slightly and both hold still for a beat.',
     'fabric rustling and a soft exhale'),
    (r'\b(nod(s|ding)?)\b', 'nodding', 'The nod is small and sincere, a slight dip of the chin.', None),
    (r'\b(wave(s|d)?|waving)\b', 'waving', 'The wave is relaxed: the hand lifts to about shoulder height and moves gently side to side.', None),
    (r'\b(point(s|ing)? (at|to|towards))\b', 'pointing', 'The arm extends naturally, index finger leading, then relaxes back down.', None),
    (r'\b(lean(s|ing)? (in|forward|back|against))\b', 'leaning', 'The lean is gradual, weight moving through the hips, posture staying natural.', None),
    (r'\b(pick(s|ing)? up|grab(s|bing)?|take(s)? (the|a|her|his|their))\b', 'picking up',
     'The hand reaches, closes around it with a natural grip, and lifts it with believable weight.', 'a light handling sound'),
    (r'\b(hold(s|ing)?|carr(y|ies|ying))\b', 'holding', 'The grip stays consistent and the object keeps the same size and shape throughout.', None),
    (r'\b(open(s|ing)? (the|a) door|opens? the door)\b', 'opening a door', 'The door swings open on its hinge at a natural speed, the hand following it.', 'a door latch and hinge'),
    (r'\b(enter(s|ing)?|come(s)? in|walk(s)? in)\b', 'entering', 'The entrance is natural: the body crosses into the space and the eyes take it in.', 'footsteps arriving'),
    (r'\b(leave(s)?|leaving|exit(s|ing)?|walk(s)? (away|out|off))\b', 'leaving', 'The exit is unhurried; the figure moves away in a straight, steady line.', 'footsteps fading'),
    (r'\b(dance(s|ing)?)\b', 'dancing', 'The dancing is loose and rhythmic, the whole body moving with the beat, feet light.', None),
    (r'\b(cr(y|ies|ying)|tear(s)? up|weep(s|ing)?)\b', 'crying', 'The eyes glisten and redden, the lips tremble slightly, the breath catches; restrained, never theatrical.', 'a quiet, shaky breath'),
    (r'\b(laugh(s|ing)?|chuckl(e|es|ing))\b', 'laughing', 'The laugh starts in the eyes, the cheeks lift and the shoulders shake slightly; genuine and unforced.', 'a soft laugh'),
    (r'\b(smil(e|es|ing))\b', 'smiling', 'The smile reaches the eyes: the cheeks lift and fine lines form at the corners of the eyes.', None),
    (r'\b(talk(s|ing)?|speak(s|ing)?|chat(s|ting)?|say(s)?|answer(s|ing)?|ask(s|ing)?|tell(s|ing)?)\b', 'talking',
     'While talking, the mouth moves naturally with the words, with small head movements and natural hand gestures.', None),
    (r'\b(listen(s|ing)?)\b', 'listening', 'Listening is active: steady eye contact, small nods, and subtle reactions on the face.', None),
    (r'\b(drink(s|ing)?|sip(s|ping)?)\b', 'drinking', 'The cup lifts smoothly to the lips, tilts slightly for a sip, and lowers again.', 'a quiet sip'),
    (r'\b(eat(s|ing)?|bite(s)?)\b', 'eating', 'The bite is natural, the jaw moving at a relaxed pace.', None),
    (r'\b(write(s)?|writing|type(s)?|typing)\b', 'writing', 'The hand moves in small, precise motions, the eyes following it.', 'soft scratching or key clicks'),
    (r'\b(think(s|ing)?|ponder(s|ing)?|wonder(s|ing)?)\b', 'thinking', 'The gaze drifts slightly off to one side, the brow softens in thought, and the face stays still.', None),
    (r'\b(kiss(es|ing)?)\b', 'kissing', 'The kiss is gentle and brief, faces close, eyes closing naturally.', None),
    (r'\b(shake(s)? hands|handshake)\b', 'shaking hands', 'The handshake is firm and natural, two short pumps, eye contact held.', None),
]

VAGUE = [  # vague word -> concrete visual it stands for (added to the style; your word stays)
    (r'\bcinematic\b', 'shallow depth of field, a gentle film-like contrast and motivated light'),
    (r'\b(beautiful|gorgeous|stunning)\b', 'soft, flattering light with clean, natural colour'),
    (r'\b(dramatic|intense)\b', 'stronger contrast between light and shadow and a still, focused camera'),
    (r'\b(epic)\b', 'a wide composition with depth and a slow, steady camera'),
    (r'\b(moody|dark)\b', 'low-key light with deep, soft shadows and muted colour'),
    (r'\b(dreamy)\b', 'soft focus at the edges and a gentle glow in the highlights'),
    (r'\b(realistic|real|natural)\b', 'documentary-style realism: true-to-life skin, light and motion'),
]

MOVE_DETAIL = {   # the Director's camera moves -> amplitude and speed (as the guide asks)
    'push_in': 'Over the whole shot the push-in brings the camera only about a quarter closer, at a steady, even speed.',
    'pull_out': 'Over the whole shot the camera eases back to show a little more of the space, at a steady, even speed.',
    'handheld': 'The handheld sway is small and slow, a few centimetres at most, never jittery.',
    'static': 'Nothing moves the frame: a tripod shot.',
    'pan': 'Any pan is small (a few degrees) and slow, following the person who speaks.',
}

CONNECT = re.compile(r'\s*(?:,\s*)?(?:\band then\b|\bthen\b|\bafter that\b|\bbefore\b|\bfinally\b|;)\s*', re.I)

# V0.9.27 🏃 lively: the descriptions that held people still, rewritten for continuous motion (MiniMax's guides describe
# motion as onset -> development -> result; "hold still", "the face stays still", "restrained" read as "don't move")
LIVELY_LOOK = {
    'walking': 'The walk is natural and alive: weight shifts from foot to foot, arms swing loosely, the body moves with each step and the head turns as they go.',
    'leaving': 'The exit is natural: the figure moves away with a relaxed, living stride, the body turning with the path.',
    'hugging': 'The hug is warm and real: arms wrap around the back, hands press gently, heads tilt and the two bodies sway slightly together.',
    'crying': 'The eyes glisten and redden, the lips tremble, the breath catches and the shoulders move with it; real, not performed.',
    'listening': 'Listening is active: eye contact, nods, and the face and posture react to what is said.',
    'thinking': 'The gaze drifts off to one side and back, the brow moves with the thought, the head or a hand shifts as the thought forms.',
    'nodding': 'The nod is sincere and visible, the head and shoulders moving together.',
    'waving': 'The wave is relaxed and lively: the hand lifts to about shoulder height and moves side to side, the body turning slightly with it.',
}

def enhance(text, *, framing=None, move=None, place_known=True, lively=False):
    """Returns {'extra': [sentences to add after your words], 'sound': [sound cues], 'style': [style words], 'changes': [labels]}."""
    t = re.sub(r'["“][^"”]*["”]', ' ', text or '')          # never look inside spoken lines
    low = ' ' + t.lower() + ' '
    extra, sound, style, changes = [], [], [], []
    seen = set()
    for rx, label, look, snd in ACTIONS:
        if label in seen or not re.search(rx, low): continue
        if label == 'looking' and 'looking around' in seen: continue
        seen.add(label); extra.append(LIVELY_LOOK.get(label, look) if lively else look); changes.append(label)
        if snd: sound.append(snd)
        if len(seen) >= 4: break                                            # the main actions; more would crowd the shot
    beats = [b.strip(' ,.') for b in CONNECT.split(t) if b and len(b.strip(' ,.').split()) >= 2]
    if len(beats) >= 2:                                                     # your order stays; give each part its own time
        extra.insert(0, f'The {len(beats)} actions happen in the order written and flow into one another without stopping: each one begins as the one before it is finishing.'
                     if lively else f'Each of the {len(beats)} actions completes before the next begins, with a brief, natural pause between them, in the order written.')
        changes.insert(0, f'{len(beats)} actions in order')
    for rx, concrete in VAGUE:
        if re.search(rx, low): style.append(concrete); changes.append('concrete look for "' + re.search(rx, low).group(0).strip() + '"')
    if move in MOVE_DETAIL: extra.append(MOVE_DETAIL[move]); changes.append('camera amplitude and speed')
    if place_known:
        extra.append('The light keeps one consistent direction and colour throughout, matching the place reference, with soft, natural shadows.')
        changes.append('light continuity')
    if not seen:                                                            # nothing recognised: still make it physical
        extra.append('Everyone keeps moving naturally the whole time, with believable weight and balance: weight shifts, gestures, glances.'
                     if lively else 'Every movement is small, natural and readable, with believable weight and balance.')
        changes.append('natural movement')
    return {'extra': extra, 'sound': sound, 'style': style, 'changes': changes}
