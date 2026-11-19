"""Prompt rewriter (V0.9.13): your plain direction -> a clearer one, written by the Qwen3-VL you already run for checking
takes, for H3 and for the way the Director plans (one ~5 s shot, or a scene of 2-4 shots).

What Qwen writes is a SUGGESTION: it is shown to you to keep, edit or throw away, and nothing is planned or sent until you
choose. It is never ground truth, and its text goes through the same Director checks as anything you type (Clean-Chain
refusals, references, dialogue).

V0.9.16: it also LOOKS. Your original photos of the people and the place (and your chosen start / end pictures, and for an
extension the last frame of your original video) are described fully; the end of the previous shot in your movie (a
generated take) is used ONLY for where people are, which way they face and what they hold or do, never for how anyone looks
(appearance always comes from your originals, so a take's drift can't creep into the next prompt). Qwen only looks: no
picture ever goes to H3, only Qwen's words, which you review. It runs only when ComfyUI is idle: the
Director asks ComfyUI to free the GPU first, exactly like the Qwen observer does.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE / 'studio_rewrite_worker_v0913.py'
MODEL_ID = os.environ.get('DIRECTOR_REWRITE_MODEL', 'Qwen/Qwen3-VL-8B-Instruct')
FRAMINGS = ('wide', 'medium', 'over_shoulder', 'close_up', 'two_shot')
FRAMING_WORDS = {'wide': 'wide shot', 'medium': 'medium shot', 'over_shoulder': 'over the shoulder', 'close_up': 'close-up', 'two_shot': 'waist-up two-shot'}
ROLE_OF = {'wide': 'wide', 'medium': 'action', 'over_shoulder': 'speaker', 'close_up': 'speaker', 'two_shot': 'two_shot'}

RULES = """You rewrite a film director's notes into clear directions for MiniMax H3, a video model that makes ONE continuous,
uncut shot of about {secs} seconds from reference photos of the people and the place. You only write what happens on camera.
Rules:
- Present tense. Name people by the names given (never a bare "he"/"she" at the start of a sentence). Say where they are
  (left/right of frame, behind, beside, how far apart, facing whom) and what each one does, in the order it happens.
{motion_rules}- Keep every spoken line EXACTLY as written, in double quotes, with who says it. Never invent new lines.
- If the notes have NO spoken line, nobody talks in this shot: never write that anyone talks, chats, tells, explains,
  asks, murmurs or mouths words (a video model turns that into gibberish); write what they do and how they react.
- Keep HOW each line is said exactly as the director wrote it, right next to the line ("very angrily says", "whispers",
  "says, laughing,", "says, close to tears,"): never drop it, soften it or move it to another line.
- Keep the director's intent: do not add new people, new objects, new places or new story beats; do not drop any.
- Keep every POSTURE the director's notes give (sits, stands, kneels, crouches, leans, lies on their back or stomach) exactly
  as written, from the first second, unless the notes have it change; never turn sitting into standing.
- Never mention reference photos, videos, frames, "the last frame", "previous video", cameras cutting, on-screen text or subtitles.
- NEVER describe how anyone looks: no faces, hair, skin, age, body type, clothes, shoes, accessories or their colours. The
  video model gets every person's look from their reference photos; words about looks fight those photos. Only a change the
  director's notes ask for (e.g. "she takes off her jacket") is written, as an action.
- Do not repeat actions that already happened before this shot (they are listed under WHERE THINGS STAND); only what happens now.
Answer with JSON only, no other text."""

MOTION_NATURAL = """- About {secs} seconds: at most 2-3 simple physical actions per shot; small natural movements; no rushing.
"""
# V0.9.27: written after MiniMax's own H3 prompt guides (VIDEO_PROMPT_WRITING_GUIDE_ref_en / _base_en): H3 was trained on
# "as detailed and explicit as possible" descriptions in which motion is a continuous path (onset -> development -> result),
# camera moves are "type [+ amplitude] [+ speed]" inside a sentence, and every sound belongs to a visible action.
# The old "small natural movements; no rushing" made takes stiff.
MOTION_LIVELY = """- About {secs} seconds of CONTINUOUS movement, written for a video model, not as a picture: describe motion, not poses.
  Write it as short beats IN PLAYBACK ORDER that fill all {secs} seconds, with timing words ("at once", "a second
  later", "halfway through", "by the end").
- GIVE BIG ACTIONS THEIR REAL TIME: crossing a room, sitting down or getting up, lying down, a hug, picking something up and
  using it, putting something on, opening a door and coming through it: each takes 2 to 3 real seconds, so these
  {secs} seconds hold at most {big} of them. Never squeeze a big action into one second, and never add big actions the
  director did not ask for to fill time. Fill the rest with small living movement (glances, breaths, a shift of weight,
  a gesture, a smile spreading) so something still changes about every second; a bare description over {secs} seconds
  plays far too slowly.
- Each action is a path: how it starts, how it develops, how it ends ("as", "while", "then"). Actions flow into each
  other at brisk, real-time speed, the way people really move.
- Everyone on screen is alive the whole time: weight shifts, small steps, gestures, glances, head turns, breathing,
  clothes and hair moving with the body (only that they move, never what they look like).
- ACTING: feelings are played big enough to read, as physical facts: she laughs out loud and tips her head back, his
  face falls and he looks away, her eyebrows shoot up and she steps back. Name the face (eyes, eyebrows, mouth) and the
  body. Use strong, clear verbs; leave out softeners such as "subtle", "slight", "a hint of", "faint" or "slowly".
- INTENSE feelings (furious, sobbing, terrified, devastated, ecstatic, screaming, breaking down, or "very" anything) are
  played at FULL force, raw and real: shouting, sobbing with heaving shoulders, shaking, gasping, collapsing onto a
  chair, jumping, grabbing the other person. The feeling hits early and keeps building to the end of the shot; the
  voice carries it too (cracking, shouting, through sobs) and the sounds are named (sobs, gasps, a scream).
- Whoever talks moves as people do while talking (hands, head, shoulders), and HOW they say it goes right before the
  line (she snaps, he whispers, laughing). Whoever listens reacts visibly to each line: eyes on the speaker, the face
  answering (a smile, a frown, raised eyebrows), nodding or shaking the head. Spoken lines take about 2.5 words a second,
  so give a line room and keep the bodies moving while it is said.
- {camera_rule}
- Name the sounds the actions make, right where they happen (footsteps on the floor, a chair scraping, fabric rustling).
- 90 to 170 words: dense, concrete, physical. No style talk, no mood words without a visible action.
"""
# V0.9.27: two worked examples (a model follows an example better than a list of rules). Made-up people, place and
# actions, no looks; Qwen is told never to reuse any of it.
# V0.9.27: the framing chip you picked, so the movement is written where the camera can see it
FRAMING_MOTION = {
    'close': 'The shot is a CLOSE-UP (face and shoulders): the movement is in the face, eyes, head, shoulders and any hands that come into frame; nobody walks across the room.',
    'medium': 'The shot is WAIST-UP: the movement is in the upper body, arms, hands, head and small steps.',
    'wide': 'The shot is WIDE (full bodies and the space): use the space: steps, turns, crossing the room, the distance between people changing.',
    'ots': 'The shot is OVER THE SHOULDER: the near person moves slightly in the foreground (head, shoulder) while the person facing the camera carries the action.',
}
EXAMPLE_NAMES = ('Ximena', 'Orrin')
MOTION_EXAMPLES = """
TWO EXAMPLES of how one shot's direction should read (the people, place and actions are made up: NEVER reuse the names
Ximena or Orrin or anything that happens in them; write only about the director's people and notes):
Notes: Ximena walks over to Orrin and asks if he's ready
Direction: Ximena strides in from the left of the frame towards Orrin, who leans against the counter on the right; her
steps are quick, her arms swinging, the floorboards creaking under her feet. At once Orrin looks up, pushes himself off
the counter and straightens, a grin spreading across his face. A second later Ximena reaches him, tilts her head with
raised eyebrows and asks, teasing, "Are you ready?" Orrin laughs, throws both hands up in a big shrug, glances at the
door and back at her, then nods hard twice. By the end she grabs his sleeve and tugs him towards the door, both of them
laughing. The camera pushes in with small amplitude as she reaches him.
Notes: Orrin tells Ximena "I can't do this anymore", she is shocked
Direction: Orrin sits on the edge of the bed on the right, elbows on his knees, rubbing his hands together hard as he
stares at the floor. Ximena stands in the doorway on the left, one hand on the frame. Orrin takes a sharp breath, lifts
his head and says, his voice cracking, "I can't do this anymore." At once Ximena's eyebrows shoot up and her mouth falls
open; her hand drops from the frame and she steps into the room, then stops dead, shaking her head. Halfway through
Orrin looks away towards the window, jaw tight, and back at her. By the end Ximena presses a hand to her chest, her eyes
filling with tears, as the bed creaks under his shifting weight. The camera stays static.
"""
CAMERA_FREE = ('Camera: at most ONE camera move, written into a sentence the way H3 understands it: the type (pushes in, pulls '
               'out, pans left/right, trucks left/right, tilts up/down, arcs around, tracks with, stays static, shakes slightly), '
               'optionally "with small amplitude" and/or "at slow speed", e.g. "the camera slowly pushes in with small amplitude '
               'as she speaks". Use the director\'s camera words if the notes have them.')
CAMERA_CHOSEN = 'Camera: the director already chose the camera move; do NOT write any camera movement.'

PICTURE_RULES = """
You are also shown PICTURES, each labelled. Use them:
- ORIGINAL photos (people, place, start / end pictures, the end of the director's own original video) are reliable: use them
  for what people wear, the place, the light and where things are.
- A picture labelled "END OF THE PREVIOUS SHOT (generated)" is only for WHERE THINGS STAND: who is where in frame, which way
  they face, what they hold and what they are doing at that moment. NEVER describe anyone's clothes, hair or face from it.
- The new shot starts where the previous shot (or the director's original video) ends: begin from that moment, do not
  describe what already happened in it again.
- A picture labelled "ACTION PHOTO" shows an action the director wants: copy ONLY the body action (posture, limbs, hands,
  head, direction) and positions onto the director's people. Never anything about how anyone in it looks or where they are.
"""

# V0.9.43: vision models mix up face up / face down. Decide it from what is visible, then say it with two cues.
LYING_RULE = """- Anyone LYING DOWN in a picture: before you write it, look at what is visible and decide from that:
  * the face (eyes, nose, mouth) and the chest or belly turned up toward the ceiling or sky = ON THEIR BACK, FACE UP;
  * the back of the head, the shoulder blades, the back or the backs of the legs turned up = ON THEIR STOMACH, FACE DOWN;
  * one shoulder and one hip up, the body turned to one side = ON THEIR SIDE.
  Then always write it with two of those cues, e.g. "lying on her back, face up toward the ceiling" or "lying on his stomach,
  face down, his back facing up". Never write only "lying". When the director's own words say how someone lies, use the
  director's words."""
PICTURE_RULES = PICTURE_RULES.rstrip('\n') + '\n' + LYING_RULE

ACTION_LABEL = ('ACTION PHOTO (an ORIGINAL still of the director\'s, perhaps of someone else or somewhere else): use ONLY the body action in it: '
                'posture, where the arms, hands, legs and head are, which way the body leans, turns or moves, what the hands do. '
                'NEVER describe this person, their face, hair, skin, age, clothes or the place: the people look like their ORIGINAL photos')
ACTION_LABEL_GENERATED = ('ACTION PHOTO (generated: a frame of one of the director\'s takes): use it ONLY for the position and the body '
                          'action: where each person stands, which way they face, posture, arms, hands, legs, head. NEVER describe anyone\'s '
                          'face, hair, clothes or the place from it: those come only from the ORIGINAL photos')
ACTION_ASK = ('The director wants {who} to take the pose described here (read from the ACTION PHOTO): {{POSE}}\n'
              'Put this pose into the direction exactly where the notes say (e.g. "ends up in this pose", "starts like this"; '
              'if they don\'t say, the shot ends in it), with all its details: where each person is, posture, arms, hands, legs, '
              'head and gaze. Nothing about how the person in the action photo looks.')
# V0.9.22b: step 1 of an action-photo rewrite, with only that one picture in front of Qwen (it describes a single picture
# very well, but loses the details when it has to weave them into a whole rewrite at once)
POSE_PROMPT = ('Look only at this picture. Describe the body pose as physical facts, written about {who} as if {they} were the one(s) in it'
               '{sides}: where in the frame (left, right, centre; near or far), which way they face, posture (standing, sitting, '
               'kneeling, crouching, leaning, and which way), arms and hands (what each hand does and where it is), legs and feet, '
               'head and gaze. One or two plain sentences per person, present tense. Do NOT describe faces, hair, skin, age, '
               'clothes, colours or the place. Answer with the description only.')

def _sides(cast_names):
    """📷 action photo only: it shows SOMEONE ELSE, so this is a mapping (which of the director's people takes which pose),
    not a claim about who is in the picture."""
    return (f' (the one on the left is to be {cast_names[0]}, the one on the right {cast_names[1]})' if len(cast_names) == 2 else '')

def pose_step(item_label, jpg, cast_names):
    """Step 1 for a 📷 action photo: the pose alone, from that one picture. Its text replaces {POSE} in the request."""
    who = ' and '.join(cast_names) or 'the person'
    return {'key': 'POSE', 'kind': 'pose', 'label': item_label, 'image': str(jpg),
            'prompt': POSE_PROMPT.format(who=who, they='they', sides=_sides(cast_names))}

STAND_PROMPT = ('Look only at this picture: {what}. Write where everyone is, as physical facts about {who}{sides}: where in the '
                'frame (left, right, centre; near or far), which way they face, posture, arms and hands (what each hand holds or '
                'does), head and gaze. One or two plain sentences per person, present tense. Do NOT describe faces, hair, skin, '
                'age, clothes, colours or the place. Answer with the description only.')
STAND_ASK = ('WHERE EVERYONE IS AT THE START OF THIS SHOT (read from {src}; begin exactly from here, keep these positions unless '
             'the notes move people): {{STAND}}')
END_ASK = ('WHERE EVERYONE MUST BE AT THE VERY END (the shot ends exactly on the END PICTURE): {{END}}\n'
           'The direction must lead into exactly these positions by its last moment.')

def _who_note(seen):
    """V0.9.27: what the Director measured about who is in the picture -> words for Qwen (never ask about someone who isn't there)."""
    if not seen: return ''
    if seen.get('nobody'): return ' Nobody is in this picture: answer only "Nobody is in frame." and nothing else.'
    if seen.get('absent'):
        return (f" Only {_and(seen['present'])} {'is' if len(seen['present']) == 1 else 'are'} in this picture. {_and(seen['absent'])} "
                f"{'is' if len(seen['absent']) == 1 else 'are'} NOT in it: do not write about {_them(seen['absent'])} at all.")
    if seen.get('count') is not None and seen.get('unsure'):
        return (f" Only {seen['count']} person{'' if seen['count'] == 1 else 's'} {'is' if seen['count'] == 1 else 'are'} in this picture "
                f"(of {_and(seen['names'])}): write only about who you see; call them by name only if you are sure who it is.")
    return ''
def _and(names): names = list(names); return ' and '.join(names) if len(names) <= 2 else ', '.join(names[:-1]) + ' and ' + names[-1]
def _them(names): return 'them' if len(list(names)) != 1 else 'that person'

def _measured_sides(names, seen):
    """V0.9.32: a side hint only from what the Director MEASURED (faces recognised against the ORIGINAL photos). Not sure
    -> no names by side: Qwen says "the person on the left / right" and the rewrite (which sees the photos) matches them."""
    if len(names) < 2: return ''
    lr = (seen or {}).get('left_to_right')
    if lr and len(lr) >= 2 and set(lr) >= set(names):
        lr = [x for x in lr if x in names]
        return (f' ({lr[0]} is the one on the left, {lr[1]} on the right: recognised from their photos)' if len(lr) == 2
                else f' (from left to right: {", ".join(lr)}: recognised from their photos)')
    return (' (it was not measured who is who: describe them as "the person on the left" / "the person on the right" '
            '(or centre), not by name)')

def stand_step(item_label, jpg, cast_names, *, start_picture=False, seen=None):
    """Step 1 for where the shot begins: the director's START PICTURE, or else the end of the previous shot (positions only).
    seen (V0.9.27): who the Director recognised in the picture; only those are asked about."""
    what = ('the first frame of the new shot (the shot opens exactly on it)' if start_picture else 'the last moment of the previous shot')
    names = (seen or {}).get('present') if (seen or {}).get('absent') else cast_names
    return {'key': 'STAND', 'kind': 'stand', 'label': item_label, 'image': str(jpg),
            'prompt': STAND_PROMPT.format(what=what, who=' and '.join(names) or 'the people', sides=_measured_sides(names, seen)) + _who_note(seen)}

def end_step(item_label, jpg, cast_names, *, seen=None):
    """Step 1 for an END PICTURE: where everyone must be when the shot ends."""
    names = (seen or {}).get('present') if (seen or {}).get('absent') else cast_names
    return {'key': 'END', 'kind': 'end', 'label': item_label, 'image': str(jpg),
            'prompt': STAND_PROMPT.format(what='the very last frame of the new shot (the shot ends exactly on it)',
                                          who=' and '.join(names) or 'the people', sides=_measured_sides(names, seen)) + _who_note(seen)}

def absent_ask(seen, where):
    """V0.9.27: the people the Director did NOT find in the start / end picture, or at the end of the previous shot."""
    if not seen or not (seen.get('absent') or seen.get('nobody')): return None
    gone = seen.get('absent') or seen.get('names') or []
    if not gone: return None
    who = _and(gone); one = len(gone) == 1
    if where == 'start':
        return (f"NOT IN THE START PICTURE: {who}. When the shot begins {who} {'is' if one else 'are'} not in frame; "
                f"{who} only come{'s' if one else ''} into frame the way the director's notes say (e.g. walking in from one side). "
                f"Never place {who} anywhere at the start.")
    if where == 'end':
        return (f"NOT IN THE END PICTURE: {who}. By the last moment {who} {'has' if one else 'have'} left the frame (as the notes say, or "
                f"naturally before the end).")
    return (f"{who} {'was' if one else 'were'} not on screen at the end of the previous shot: {who} {'is' if one else 'are'} not part of "
            f"WHERE EVERYONE IS; {who} come{'s' if one else ''} into this shot only the way the director's notes say.")

_STOP = set('the a an and of to in on at with his her their its is are by as one two person people left right side frame '
            'they them she he it while who that this towards toward from up down front back'.split())
def _words(t): return {w for w in re.findall(r"[a-z]+", (t or '').lower()) if len(w) > 2 and w not in _STOP}

def ensure_pose(checked, pose, notes, mode, *, where=None, field='pose'):
    """If the rewrite dropped or watered down the pose Qwen described (fewer than half of its key words are in it), the
    Director puts Qwen's own description in, where your notes say (start / during / by the end)."""
    if not pose: return checked
    key = _words(pose)
    if not key: return checked
    n = (notes or '').lower()
    where = where or ('start' if re.search(r'\b(start|starts|starting|begin|begins|opens?)\b', n) and not re.search(r'\b(ends?|ending|end up|ends up)\b', n)
             else 'during' if re.search(r'\b(holds?|holding|throughout|the whole (?:shot|time)|while)\b', n) and not re.search(r'\b(ends?|end up|ends up)\b', n)
             else 'end')
    lead = {'start': 'At the start of the shot: ', 'during': 'Throughout the shot: ', 'end': 'By the end of the shot: '}[where]
    sent = lead + pose.strip().rstrip('.') + '.'
    def fix(d):
        if len(key & _words(d)) >= 0.5 * len(key): return d, False
        return ((sent + ' ' + d) if where == 'start' else (d.rstrip() + ' ' + sent)), True
    inserted = False
    if mode == 'shot':
        checked['direction'], inserted = fix(checked['direction'])
    elif checked.get('shots'):
        k = 0 if where == 'start' else -1
        checked['shots'][k]['direction'], inserted = fix(checked['shots'][k]['direction'])
    checked[field] = pose.strip(); checked[field + '_where'] = where; checked[field + '_inserted'] = inserted
    if inserted: checked.setdefault('warnings', []).append(('Qwen left out part of the pose' if field == 'pose' else 'Qwen left out where everyone ends up')
                                                           + f', so the Director put its own description in ({where} of the shot). Check it before you use it.')
    return checked

# V0.9.23 📝 Describe from photos: the look of ONE person, written once from their original photos, saved on their card (you
# check and edit it), and used word for word in every shot's prompt, so the appearance words never change from shot to shot.
DESCRIBE_SYSTEM = ('You write the "look" line for one person in a film, from their photos, for a video model that must keep them '
                   'looking the same in every shot. Write only what can be seen and stays the same: each piece of clothing with its '
                   'colour and any visible logo or text, shoes, accessories (glasses, jewellery, watch, bag, hat), hair (colour, '
                   'length, style) and facial hair. Plain comma-separated phrases, most important first, at most 60 words. No '
                   'opinions, no guesses about age, body, skin, ethnicity or personality, no names, no background or place, and never '
                   'say what is NOT there. '
                   'Answer with the line only.')
def describe_request(name, pronoun, pictures, model=None):
    return {'mode': 'describe', 'system': DESCRIBE_SYSTEM, 'model_id': model or MODEL_ID,
            'user': f'These photos all show {name} ({pronoun or "they"}). Write {name}\'s look line.',
            'images': [[str(l), str(p)] for l, p in pictures]}

def clean_look(reply, name=''):
    t = ' '.join(str(reply or '').replace('"', '').replace('\u201c', '').replace('\u201d', '').split()).strip(' .')
    t = re.sub(r'^(?:look(?: line)?\s*:\s*)', '', t, flags=re.I)
    if name: t = re.sub(r'^' + re.escape(name) + r'(?:\s+(?:is\s+)?(?:wears|wearing|has))?[:,]?\s*', '', t, flags=re.I)
    # "no visible jewelry or accessories": a negative in a prompt invites the thing (and there's nothing to keep) -> dropped
    parts = [x.strip() for x in t.split(',')]
    t = ', '.join(x for x in parts if x and not re.match(r'(?i)^(?:no|not|without|none)\b', x))
    t = t[:500].rsplit(',', 1)[0] if len(t) > 500 else t
    if len(t) < 6: raise ValueError('DESCRIBE_EMPTY')
    return t

SHOT_FORMAT = 'Answer exactly: {"direction": "<the rewritten direction for this one shot>"}'
SCENE_FORMAT = ('Split the moment into 2 to 4 consecutive shots that cut together like a filmed scene (the first one usually a '
                'wide shot that establishes where everyone is). Each shot must make sense on its own: name who is in it and where '
                'they are, and only what happens in that shot. A spoken line goes in exactly one shot. Answer exactly: '
                '{"shots": [{"framing": "wide|medium|over_shoulder|close_up|two_shot", "people": ["<name>", ...], "direction": "<this shot>"}]}')

def build_request(direction, *, mode, cast, place=None, where_things_stand=None, seconds=5, pictures=None, model=None, motion='lively',
                  camera_chosen=False, framing=None, start_known=True):
    """cast: [{'name', 'pronoun', 'look'}]; pictures: [(label, jpg path)] shown to Qwen before the notes.
    start_known=False (V0.9.37): the shot before isn't generated yet, so Qwen must not state where anyone starts.
    Returns {'system', 'user', 'mode', 'images'} for the worker."""
    if mode not in ('shot', 'scene'): raise ValueError('REWRITE_MODE_SHOT_OR_SCENE')
    # V0.9.23: names and he/she only. Their looks are NOT sent (the 32B wrote them into the rewrite; H3 gets looks from the photos)
    who = '\n'.join(f"- {c['name']} ({c.get('pronoun') or 'they'})" for c in cast) or '- (nobody named)'
    user = (f"PEOPLE:\n{who}\n" + (f"PLACE: {place}\n" if place else '') +
            (f"WHERE THINGS STAND (already true; do not act it out again): {where_things_stand}\n" if where_things_stand else '') +
            (f"FRAMING: {FRAMING_MOTION[framing]}\n" if mode == 'shot' and framing in FRAMING_MOTION else '') +
            f"\nDIRECTOR'S NOTES:\n{direction.strip()}\n\n" + (SHOT_FORMAT if mode == 'shot' else SCENE_FORMAT))
    pics = [[str(l), str(p)] for l, p in (pictures or [])]
    secs = round(seconds)
    mr = (MOTION_NATURAL if motion == 'natural' else MOTION_LIVELY).format(secs=secs, big=max(1, int(float(secs) / 2.5)),       # V0.9.54
                                                                          camera_rule=CAMERA_CHOSEN if camera_chosen else CAMERA_FREE)
    ex = MOTION_EXAMPLES if motion != 'natural' else ''
    system = RULES.format(secs=secs, motion_rules=mr)
    if start_known == 'relative': system = system.replace(POSITIONS_RULE, POSITIONS_RELATIVE_RULE)   # V0.9.44 ✂ a cut
    elif not start_known: system = system.replace(POSITIONS_RULE, POSITIONS_UNKNOWN_RULE)
    return {'mode': mode, 'system': system + ex + (PICTURE_RULES if pics else ''), 'user': user, 'model_id': model or MODEL_ID,
            'motion': motion,
            'images': pics}

POSITIONS_RULE = ('Say where they are\n  (left/right of frame, behind, beside, how far apart, facing whom) and what each one does, in the order it happens.')
POSITIONS_UNKNOWN_RULE = ('Where everyone is at the very START is not\n  known yet (the shot before is not generated): never say where '
                          'anyone starts, stands or faces at the beginning. Say what each one does, in the order it happens, and '
                          'where they move to and end up.')
# V0.9.49 a rewrite that disagreed with the positions you set gets ONE more go, told exactly where it disagreed
POSITION_FIX = ("THE DIRECTOR'S POSITIONS WIN. Your last version disagreed with where the director set everyone:\n{clashes}\n"
                "Write it again so the first moment of the shot says exactly the positions given (posture and side of the frame); "
                "keep everything else the director asked for.")

def position_fix(req, clashes):
    """A copy of the request with the disagreement spelled out just before the director's notes."""
    r = dict(req); txt = POSITION_FIX.format(clashes='\n'.join('- ' + c for c in clashes))
    u = r.get('user') or ''
    r['user'] = u.replace("\nDIRECTOR'S NOTES:", '\n' + txt + "\n\nDIRECTOR'S NOTES:", 1) if "\nDIRECTOR'S NOTES:" in u else u + '\n\n' + txt
    return r

POSITIONS_RELATIVE_RULE = ('Say where they are\n  relative to each other and the place (beside, behind, across the table, how far apart, '
                           'facing whom), never left or right of the frame (the Director keeps everyone on the same side of the '
                           'screen as the shot before), and what each one does, in the order it happens.')
assert POSITIONS_RULE in RULES

MAX_PICTURES = 8
def prepare_pictures(items, out_dir, short=448):
    """items: [{'label', 'path', 'frame': None (a photo) | 'last' | frame index}] -> [(label, jpg)] small JPEGs for Qwen."""
    import cv2
    out = []
    for k, it in enumerate(items[:MAX_PICTURES]):
        p = Path(it['path'])
        if not p.is_file(): continue
        if it.get('frame') is None: im = cv2.imread(str(p))
        else:
            cap = cv2.VideoCapture(str(p)); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            idx = max(0, n - 1) if it['frame'] == 'last' else max(0, min(int(it['frame']), n - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx); ok, im = cap.read()
            if not ok and idx > 0: cap.set(cv2.CAP_PROP_POS_FRAMES, idx - 2); ok, im = cap.read()
            cap.release(); im = im if ok else None
        if im is None: continue
        h, w = im.shape[:2]; s = short / min(h, w)
        if s < 1: im = cv2.resize(im, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        j = Path(out_dir) / f'pic{k:02d}.jpg'; cv2.imwrite(str(j), im, [cv2.IMWRITE_JPEG_QUALITY, 88]); out.append((it['label'], j))
    return out

def _json_in(text):
    """The first JSON object in Qwen's reply (it sometimes wraps it in ```json fences)."""
    t = re.sub(r'```(?:json)?', '', text or '')
    i = t.find('{')
    while i >= 0:
        depth = 0
        for j in range(i, len(t)):
            depth += (t[j] == '{') - (t[j] == '}')
            if depth == 0:
                try: return json.loads(t[i:j + 1])
                except ValueError: break
        i = t.find('{', i + 1)
    raise ValueError('REWRITE_REPLY_NOT_JSON')

def _lines(text):
    return [m.strip() for m in re.findall(r'["“]([^"”]+)["”]', text or '') if m.strip()]

def _clean(s, limit=2800):                     # V0.9.34 (was 1600): as long as one shot's direction may be
    s = ' '.join(str(s or '').split())
    return s[:limit].rsplit(' ', 1)[0] if len(s) > limit else s

# V0.9.23: what a rewrite may NOT say about people (unless the director's own notes say it): appearance belongs to the photos
_GARMENT = (r'(?:jersey|hoodie|sweater|sweatshirt|shirt|t-shirt|tee|jacket|coat|blazer|dress|skirt|jeans|pants|trousers|shorts|sweatpants|'
            r'leggings|top|blouse|tank top|suit|uniform|cap|hat|beanie|scarf|sneakers|shoes|boots|socks|glasses|sunglasses|necklace|'
            r'earrings|bracelet|watch|backpack|outfit|clothes|clothing)')
APPEARANCE = [
    r',?\s*(?:who is\s+|who\'s\s+)?(?:wearing|dressed in|clad in|sporting)\s+[^,.;]+(?:\s*,(?=\s+[a-z]))?',
    r',?\s*\bin (?:his|her|their|a|an|the) (?:[\w-]+\s+){0,4}' + _GARMENT + r'\b(?:\s+(?:and|with) (?:[\w-]+\s+){0,3}' + _GARMENT + r'\b)?(?:\s*,(?=\s+[a-z]))?',
    r',?\s*\bwith (?:his|her|their|a|an)?\s*(?:[\w-]+\s+){0,3}(?:hair|beard|moustache|mustache|stubble|eyes|skin|freckles|ponytail|bun|braids)\b'
    r'(?:\s+(?:tied|pulled|loose|falling|swept|in a)\b[^,.;]*)?(?:\s*,(?=\s+[a-z]))?',
    r'\b(?:the\s+)?(?:young|old|older|elderly|white-haired|grey-haired|gray-haired|dark-haired|blond|blonde|brunette|redheaded|bald|bearded|tall|short|slim|petite|stocky)\s+(?=[A-Z])',
    r'[,;]?\s*\b(?:his|her|their) (?:[\w-]+\s+){0,2}(?:hair|beard)\s+(?:is|falling|tied|pulled)\b[^,.;]*',
]
def strip_appearance(text, notes):
    """Take out what a rewrite says about how people look, unless the same words are in your notes. -> (text, [removed])."""
    mine = (notes or '').lower(); removed = []
    def keep(m):
        words = [w for w in re.findall(r'[a-z]+', m.group(0).lower()) if len(w) > 3 and w not in ('with', 'wearing', 'dressed', 'their', 'who', 'sporting')]
        if words and all(w in mine for w in words): return m.group(0)     # you wrote it: e.g. "she puts on the red hat"
        removed.append(m.group(0).strip(' ,')); return ''
    out = text
    for pat in APPEARANCE: out = re.sub(pat, keep, out)
    out = re.sub(r'\s+([,.;])', r'\1', re.sub(r'\s{2,}', ' ', out)).strip()
    out = re.sub(r',\s*,', ',', out); out = re.sub(r'[,;]\s*\.', '.', out); out = re.sub(r'^\s*,\s*', '', out)
    return out, removed

def check(reply, *, mode, original, names, forbidden=None):
    """Qwen's reply -> {'direction'} or {'shots': [...]} plus plain warnings. Fail closed on anything malformed:
    unknown people, refused wording, a missing line of yours."""
    j = _json_in(reply) if isinstance(reply, str) else reply
    warn = []
    known = {n.lower(): n for n in names}
    def guard(text):
        if forbidden is not None:
            m = forbidden.search(text)
            if m: raise ValueError('REWRITE_REFUSED_CLEAN_CHAIN: Qwen wrote "' + m.group(0).strip()[:60] + '"')
        return text
    if mode == 'shot':
        d = _clean(j.get('direction') if isinstance(j, dict) else None)
        if len(d) < 10: raise ValueError('REWRITE_EMPTY')
        out = {'direction': guard(d)}; all_text = d
    else:
        shots = j.get('shots') if isinstance(j, dict) else None
        if not isinstance(shots, list) or not 1 <= len(shots) <= 4: raise ValueError('REWRITE_SCENE_NEEDS_1_TO_4_SHOTS')
        clean = []
        for s in shots:
            if not isinstance(s, dict): raise ValueError('REWRITE_SHOT_MALFORMED')
            d = _clean(s.get('direction'))
            if len(d) < 10: raise ValueError('REWRITE_EMPTY_SHOT')
            fr = str(s.get('framing') or 'medium').lower().replace('-', '_').replace(' ', '_')
            if fr not in FRAMINGS: fr = 'medium'
            ppl = []
            for p in s.get('people') or []:
                k = str(p).strip().lower()
                if k not in known: raise ValueError(f'REWRITE_UNKNOWN_PERSON: {str(p)[:40]}')
                if known[k] not in ppl: ppl.append(known[k])
            clean.append({'framing': fr, 'people': ppl[:2], 'direction': guard(d)})
        out = {'shots': clean}; all_text = ' '.join(s['direction'] for s in clean)
    for ex in EXAMPLE_NAMES:                             # V0.9.27: the brief's made-up example must never end up in your shot
        if ex.lower() not in known and re.search(r'\b' + ex + r'\b', all_text, re.I):
            raise ValueError(f'REWRITE_COPIED_THE_EXAMPLE: Qwen wrote about "{ex}" from the made-up example; rewrite again')
    gone = []                                            # V0.9.23: no appearance in the rewrite (it belongs to the photos)
    if mode == 'shot':
        out['direction'], g = strip_appearance(out['direction'], original); gone += g
    else:
        for x in out['shots']:
            x['direction'], g = strip_appearance(x['direction'], original); gone += g
    if gone:
        warn.append('Took out what Qwen wrote about how people look (H3 gets that from your photos): ' + '; '.join(f'"{x[:50]}"' for x in gone[:6]))
        all_text = out['direction'] if mode == 'shot' else ' '.join(x['direction'] for x in out['shots'])
    mine = _lines(original); theirs = _lines(all_text)
    missing = [l for l in mine if l not in theirs]
    if missing: warn.append('Qwen changed or dropped your line(s): ' + '; '.join(f'"{l[:60]}"' for l in missing) + ' (check before you use it)')
    # V0.9.21: how each of your lines is said ("very angrily says") must survive the rewrite
    def hows(text):
        import studio_intent_v080 as si
        out = {}
        for m in re.finditer(r'["\u201c]([^"\u201d]+)["\u201d]', text or ''):
            before = (text[max(0, m.start() - 50):m.start()]).split('"')[-1]
            out[m.group(1).strip()] = si.delivery_of(' '.join(before.split()[-5:]))
        return out
    h_mine, h_theirs = hows(original), hows(all_text)
    lost = [l for l, h in h_mine.items() if h and l in h_theirs and h_theirs[l] != h]
    if lost: warn.append('Qwen changed how a line is said: ' + '; '.join(f'"{l[:40]}" ({h_mine[l]} -> {h_theirs[l] or "plain"})' for l in lost) + ' (check before you use it)')
    extra = [l for l in theirs if l not in mine]
    if extra: warn.append('Qwen added spoken words you did not write: ' + '; '.join(f'"{l[:60]}"' for l in extra))
    out['warnings'] = warn
    return out

def scene_shots(checked):
    """Checked scene -> what the Director plans: [{'text', 'people', 'role'}] (the framing words lead each shot)."""
    out = []
    for s in checked['shots']:
        lead = FRAMING_WORDS[s['framing']] + (' of ' + ' and '.join(s['people']) if s['people'] and s['framing'] in ('wide', 'medium', 'two_shot', 'close_up') else '')
        out.append({'text': f"{lead}. {s['direction']}", 'people': s['people'], 'role': ROLE_OF[s['framing']]})
    return out

def run_with_steps(request, *, qwen_python, timeout=900):
    """(reply, {'POSE'|'STAND'|'END': description}). Tests: DIRECTOR_REWRITE_FAKE (the reply) and DIRECTOR_POSE_FAKE /
    DIRECTOR_STAND_FAKE / DIRECTOR_END_FAKE (the descriptions)."""
    keys = [x['key'] for x in request.get('pre_steps') or []]
    if os.environ.get('DIRECTOR_REWRITE_FAKE'):
        steps = {k: os.environ[f'DIRECTOR_{k}_FAKE'] for k in keys if os.environ.get(f'DIRECTOR_{k}_FAKE')}
        return Path(os.environ['DIRECTOR_REWRITE_FAKE']).read_text(), steps
    out = run(request, qwen_python=qwen_python, timeout=timeout, full=True)
    return out.get('reply', ''), {k: v for k, v in (out.get('steps') or {}).items() if v}

def run(request, *, qwen_python, timeout=900, full=False):
    import studio_qwenmodel_v0916 as _qm; qwen_python = _qm.python_for(request.get('model_id'), qwen_python)   # V0.9.55 Qwen3.6: its own environment
    """Run the worker in the Qwen environment; returns its raw reply text. DIRECTOR_REWRITE_FAKE=<file> (tests only) returns
    that file's text instead of running Qwen."""
    fake = os.environ.get('DIRECTOR_REWRITE_FAKE')
    if fake: return Path(fake).read_text()
    if not Path(qwen_python).is_file(): raise RuntimeError('QWEN_NOT_INSTALLED: the Qwen Python environment (h3-qwen-venv) was not found')
    with tempfile.TemporaryDirectory() as t:
        req = Path(t) / 'req.json'; out = Path(t) / 'out.json'
        req.write_text(json.dumps(request))
        env = dict(os.environ)
        env.pop('LD_PRELOAD', None)             # V0.9.38: ComfyUI's tcmalloc preload segfaults Qwen's generate
        import studio_qwenmodel_v0916 as _qm                  # the cache that really has the model (Vast sets its own HF_HOME)
        env['HF_HOME'] = str(_qm.hf_home(request.get('model_id')))
        env.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
        r = subprocess.run([str(qwen_python), '-I', str(WORKER), str(req), str(out)], capture_output=True, text=True, timeout=timeout, env=env)
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError('QWEN_REWRITE_FAILED: ' + _qm.why_failed(r.returncode, (r.stdout or '') + (r.stderr or ''), 'rewrite'))
        j = json.loads(out.read_text())
        return j if full else j['reply']

def wait_for_gpu(comfy_url, *, dispatcher, wait_minutes=30, sleeper=time.sleep, on_wait=None, need_mib=None):
    """Wait until ComfyUI is idle, ask it to free the GPU, and wait for enough memory (the Qwen observer's own hand-over)."""
    import urllib.request
    def fetch():
        with urllib.request.urlopen(comfy_url.rstrip('/') + '/queue', timeout=8) as r: return json.load(r)
    def post():
        rq = urllib.request.Request(comfy_url.rstrip('/') + '/free', data=json.dumps({'unload_models': True, 'free_memory': True}).encode(),
                                    headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(rq, timeout=20) as r: return r.status
    deadline = time.time() + wait_minutes * 60
    while True:
        res = dispatcher.release_gpu_for_qwen(fetch, post=post, sleeper=sleeper, **({'need': int(need_mib)} if need_mib else {}))
        if res == 'READY': return
        if time.time() > deadline:
            raise RuntimeError('QWEN_WAITED_TOO_LONG: ComfyUI stayed busy' if res == 'BUSY' else 'QWEN_NOT_ENOUGH_GPU_MEMORY even after ComfyUI freed it')
        if on_wait: on_wait(res)
        sleeper(10)
