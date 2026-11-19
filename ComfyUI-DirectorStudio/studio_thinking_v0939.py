"""🧠 Thinking for the 🎬 film writer (V0.9.39; V0.9.40 🎯 the thinking checks against H3). Pure Python: the Qwen film worker imports it, the tests drive it with a fake model.

A Qwen3 *Thinking* model writes its reasoning first, then '</think>', then the answer. Two things go wrong if that is left alone:
the reasoning can use up the whole word limit so no answer comes, and it can go on for a very long time. So each step gets:
  - a THINKING BUDGET (tokens): thinking stops there; if the model hasn't finished, a closing sentence and '</think>' are added
    for it (Qwen's own way of cutting thinking short) and it writes the answer from what it has thought;
  - an ANSWER LIMIT of its own, as without thinking, so the answer always has room.
A budget of 0 skips thinking for that step (an empty '<think></think>', the way Qwen3 turns thinking off).
Thinking samples (Qwen's recommended temperature 0.6 / top-p 0.95 / top-k 20; plain greedy thinking can loop) from a FIXED seed per
step, so the same film request gets the same thinking on the same machine; the answer after it is greedy, as everywhere else.
Only the answer is used: the reasoning is never part of a prompt, and it is saved for you to read (qwen_logs/film_thinking_last.txt).
"""
from __future__ import annotations
import re

OUTLINE_THINK = 8000          # tokens of thinking before the shot list (the plan: story, who is where, how shots hand on)
SHOT_THINK = 2000             # tokens of thinking before each shot is written out (Film writer: plan and every shot)
CLOSE_EARLY = '\n\nConsidering the limited time, I have to give the solution based on the thinking directly now.\n</think>\n\n'
CLOSE_EMPTY = '\n</think>\n\n'            # after the template's '<think>\n': thinking off for this step
SAMPLE = {'do_sample': True, 'temperature': 0.6, 'top_p': 0.95, 'top_k': 20}
SEED = 20261031
THINK_TIMEOUT = 4 * 3600      # a whole film with thinking (the film is written while the GPU is free, before anything generates)
THINK_REWRITE_TIMEOUT = 2 * 3600

# V0.9.40 🎯: what the thinking is FOR. A Thinking model left alone spends its room re-telling the story; these turn the thinking
# into a check of the plan / the shot against what MiniMax H3 needs (the same rules as the system prompt, as questions), so the
# wait buys an H3-ready answer. Added to the system prompt only for a step that thinks (budget > 0); without thinking: unchanged.
# The answer format is unchanged (JSON only); the checklist is worked through in the thinking, never written into the answer.
OUTLINE_CHECK = """
BEFORE YOU ANSWER, in your thinking, check your plan against what MiniMax H3 can make, and fix anything that fails:
1. Each shot is ONE continuous, uncut moment in ONE place that H3 can make in its seconds (3 to 10). A shot that needs a cut,
   a jump in time or two places is split into two shots.
2. The "seconds" add up to about the film's total, and each shot's beat fits its seconds: enough happens to fill them
   (H3 plays a thin beat slowly), and not more than one clear moment.
3. Lines fit: every line's words divided by 2.5, plus 1 second, fits inside its shot; long speeches are split across shots.
4. Joins: each shot after the first has a "join". A "cut" starts with its action already under way (nobody walks into
   position); a "carry" goes straight on from where the shot before ends; a "skip" shows what happens after the time has
   passed. The framing changes at every cut and skip. Across every join, what people hold or changed carries over, and
   anyone new walks in, anyone who leaves walks out. Every shot's "after" says what is true when it ends, and the next shot
   starts from exactly that (nothing held, worn, said or felt changes between shots unless a shot shows it change).
5. Coverage: framings vary, and every reaction plays IN THE SAME SHOT as what causes it, with both people in it
   (no shot that is only someone reacting).
6. Only PEOPLE and PLACES appear; nothing about how anyone looks; no narration, on-screen text or music.
7. The story has a build-up, a turn and an ending that lands, and shot 1 STARTS IN THE ACTION: its people are already doing
   or saying the first real thing of the brief (no shot of people just standing, looking around or arriving), and the LAST
   shot is the ending itself: nothing after the outcome, no shot added only to fill the length.
Then answer with the JSON only."""
SHOT_CHECK = """
BEFORE YOU ANSWER, in your thinking, check your direction against what MiniMax H3 needs, and fix anything that fails:
1. Time: beats in playback order, with timing words, that fill ALL of the shot's seconds. Every BIG action (crossing a
   room, sitting down, getting up, lying down, a hug, picking something up and using it) gets 2 to 3 real seconds, so the
   shot holds no more of them than fit; small living movement fills the rest, so something changes about every second.
2. Lines: every spoken line is kept exactly, in double quotes, with how it is said right before it; its words divided by
   2.5, plus 1 second, fit the shot; the listener visibly reacts.
3. Continuity: it follows on from "Just before this shot" the way this shot joins it (cuts to the action already under
   way, carries straight on, or shows what happens after the time skip), keeps everything in WHERE THINGS STAND true (what
   people hold, wear, know and feel), repeats nothing that ALREADY HAPPENED, does none of what is STILL TO COME or what the
   NEXT shot will show, ends with what must be true by its end, and says nothing about where anyone starts if that is not
   known.
4. Camera: at most ONE move, in H3's words (pushes in, pulls out, pans, trucks, tilts, arcs around, tracks with, stays
   static), or none if the director chose it.
5. Positive wording: every sentence says what IS seen and heard; rewrite any "not", "no", "never" or "without" as what
   happens instead.
6. Looks: nothing about faces, hair, skin, age, body type or clothes; H3 takes looks from the photos.
7. Doable: each action is a continuous path (start, middle, end) at real-time speed; contact between people is simple and
   clear (one grab, one hug, one push); no new people, objects or text; the movement fits the framing.
8. Sound: the sounds the actions make are named where they happen.
Then answer with the JSON only."""

COVERAGE = ('5. Coverage: framings vary, and every reaction plays IN THE SAME SHOT as what causes it, with both people in it\n'
            '   (no shot that is only someone reacting).')
ONE_ANGLE = '5. One angle: every shot keeps the film\'s ONE fixed camera angle; reactions and movement happen inside that frame.'
assert COVERAGE in OUTLINE_CHECK
REFRAME = 'The framing changes at every cut and skip.'
ONE_ANGLE_JOIN = 'The camera keeps the film\'s one angle at every join.'      # V0.9.46
assert REFRAME in OUTLINE_CHECK

def check_for(step, angle=None):
    """The H3 checklist for a step that thinks: 'outline' -> the plan's, anything else -> a shot's. V0.9.41 📷 with one angle
    for the film, the plan checks that every shot keeps it instead of varying the framing."""
    if step == 'outline':
        return OUTLINE_CHECK.replace(COVERAGE, ONE_ANGLE).replace(REFRAME, ONE_ANGLE_JOIN) if angle else OUTLINE_CHECK
    return SHOT_CHECK

def budgets(mode):
    """mode None / 'plan' / 'all' -> {'outline': tokens, 'shots': tokens}, or None without thinking."""
    if mode not in ('plan', 'all'): return None
    return {'outline': OUTLINE_THINK, 'shots': SHOT_THINK if mode == 'all' else 0}

def split_reply(text):
    """A thinking model's decoded reply -> (thoughts, answer). The template's '<think>' is in the prompt, so the reply is
    usually 'thoughts</think>answer'; a reply with no '</think>' at all is all answer (a model that didn't think)."""
    t = str(text or '')
    if '</think>' not in t: return '', re.sub(r'(?s)^\s*<think>\s*', '', t).strip()
    head, _, tail = t.rpartition('</think>')
    return re.sub(r'(?s)^\s*<think>\s*', '', head).strip(), tail.strip()

def after_think(ids, think_end_id):
    """Index just after the LAST '</think>' token in ids (0 when there is none: it is all answer)."""
    ids = list(ids)
    for i in range(len(ids) - 1, -1, -1):
        if ids[i] == think_end_id: return i + 1
    return 0

def think_then_answer(gen, prompt_ids, *, think_end_id, encode, budget, answer_tokens, seed=SEED, on_phase=None, end_of_turn=()):
    """Run one step of a thinking model.
    gen(ids, max_new_tokens, stop_ids, sample, seed) -> the NEW token ids (a list); it stops after a token in stop_ids.
    encode(text) -> token ids (no special tokens added). end_of_turn: the model's end-of-turn ids (dropped from cut-off thinking). Returns {'ids': all new ids (thinking + answer), 'thought_tokens',
    'cut': True when thinking hit the budget and was closed for it, 'skipped': True with budget 0}."""
    ids = list(prompt_ids); new = []
    if budget and budget > 0:
        if on_phase: on_phase('thinking')
        t = list(gen(ids, int(budget), [think_end_id], True, seed))
        cut = not t or t[-1] != think_end_id
        if cut:
            t = [x for x in t if x != think_end_id and x not in end_of_turn] + list(encode(CLOSE_EARLY))   # it may have ended its turn while thinking
        new += t; thought = len(t); skipped = False
    else:
        new += list(encode(CLOSE_EMPTY)); thought = 0; cut = False; skipped = True
    if on_phase: on_phase('writing')
    a = list(gen(ids + new, int(answer_tokens), [], False, seed))
    return {'ids': new + a, 'thought_tokens': thought, 'cut': cut, 'skipped': skipped}

# V0.9.43 🧠✋ watch Qwen think and step in. The page shows the thinking as it is written; a note you type is put INTO the
# thinking at that point, as the director's correction, and Qwen carries on from there (nothing it thought is thrown away).
# ⏩ "Write it now" closes the thinking at once (as the budget does) and Qwen writes the answer from what it has.
NOTE = ('\n\nWait. The director has just added a note while I am thinking: "{text}". The director\'s note overrides anything '
        'I assumed or planned before it, so I will follow it from here and check my plan against it.\n\n')
NOTE_BUDGET = 600             # at least this much more thinking after a note (a step that wasn't thinking thinks this long about it)
CLOSE_THINK = '\n</think>\n\n'

def note_text(text):
    return NOTE.format(text=' '.join(str(text or '').replace('"', "'").split())[:300])

def think_live(gen, prompt_ids, *, think_end_id, encode, budget, answer_tokens, poll, seed=SEED, on_phase=None, end_of_turn=(),
               on_note=None, note_budget=NOTE_BUDGET):
    """think_then_answer, with the director able to step in. poll() -> None, {'kind': 'note', 'text'} or {'kind': 'finish'}
    (each item once). gen(...) must stop early as soon as an item is waiting (the worker's stopping criterion), so a note lands
    within a moment. A note while Qwen writes the answer sends it back into its thinking with the note, then it writes again.
    Returns think_then_answer's dict plus 'notes' (the director's notes, in order)."""
    ids = list(prompt_ids); t = []; notes = []; left = int(budget or 0); thought = 0; cut = False
    def add_note(c):
        notes.append(' '.join(str(c.get('text') or '').split())[:300]); t.extend(encode(note_text(c.get('text'))))
        if on_note: on_note(notes[-1])
    while True:
        if left > 0:
            if on_phase: on_phase('thinking')
            got = list(gen(ids + t, left, [think_end_id], True, seed + len(notes)))
            left -= len(got); thought += len(got)
            ended = bool(got) and got[-1] == think_end_id
            t += [x for x in got if x != think_end_id and x not in end_of_turn]
            c = poll()
            if c and c.get('kind') == 'note': add_note(c); left = max(left, note_budget); continue
            cut = (not ended) or bool(c and c.get('kind') == 'finish')
            closing = list(encode(CLOSE_EARLY if cut else CLOSE_THINK))
        else:
            c = poll()
            if c and c.get('kind') == 'note': add_note(c); left = note_budget; continue
            closing = list(encode(CLOSE_EMPTY if not t else CLOSE_EARLY))
        if on_phase: on_phase('writing')
        a = list(gen(ids + t + closing, int(answer_tokens), [], False, seed))
        c = poll()
        while c and c.get('kind') != 'note' and len(a) < int(answer_tokens):       # ⏩ while it was already writing: it just goes on
            more = list(gen(ids + t + closing + a, int(answer_tokens) - len(a), [], False, seed))
            a += more; c = poll()
            if not more: break
        if c and c.get('kind') == 'note': add_note(c); left = note_budget; continue          # back into the thinking with it
        return {'ids': t + closing + a, 'thought_tokens': thought, 'cut': cut, 'skipped': thought == 0, 'notes': notes}

def note(stats):
    """The worker's per-step stats -> one line for the film's note, and warnings."""
    stats = [s for s in (stats or []) if isinstance(s, dict)]
    if not stats: return '', []
    o = next((s for s in stats if s.get('step') == 'outline'), None)
    shots = [s for s in stats if s.get('step') != 'outline' and not s.get('skipped')]
    bits = []
    if o and not o.get('skipped'): bits.append(f"thought about the plan first (about {int(o.get('thought_tokens') or 0) * 3 // 4} words)")
    if shots: bits.append(f'and before each of {len(shots)} shot(s)')
    warn = []
    if o and o.get('cut'): warn.append('Qwen used all its thinking room on the plan and wrote it from what it had thought so far.')
    cut = [str(s.get('step')) for s in shots if s.get('cut')]
    if cut: warn.append('Qwen used all its thinking room on shot(s) ' + ', '.join(cut) + ' and wrote them from what it had thought.')
    return (' '.join(bits)), warn
