"""😮 Acting (V0.9.35): emotions PLAYED, not hinted.

The emotion phrases the Director gave H3 were written to be understated on purpose ("a small genuine smile", "a flicker
of fear", "a held-back heartbreak", "real sadness, held with dignity"), and even "strong" was hedged ("still natural",
"still grounded"). H3 is CFG-distilled and does about what it is told, so the most it was ever asked for was a restrained
feeling: stiff, slow takes. Here every emotion is visible acting, in three real steps (a little / normal / very), each a
physical description that starts with a verb ("<Maya> laughs out loud, head tipping back, ..."). Setting 'acting':
'expressive' (default) or 'understated' (the old phrases).
"""
from __future__ import annotations

PERF = {   # key: (a little, normal, very)
    'laugh': ('lets out a quick, real laugh, shoulders shaking', 'laughs out loud, head tipping back, shoulders shaking, eyes creasing',
              'breaks into helpless laughter, bending forward with a hand on the chest, laughing until out of breath'),
    'chuckle': ('chuckles under the breath, a grin spreading', 'chuckles, the grin widening into a laugh, shaking the head',
                'can\'t stop chuckling, laughing through the words, wiping an eye'),
    'smile': ('smiles warmly, the smile reaching the eyes', 'breaks into a big, warm smile, cheeks lifting, eyes creasing',
              'beams with a huge open smile, the whole face lighting up, almost laughing'),
    'smirk': ('gives a sly half-smile, one corner of the mouth lifting', 'smirks, eyebrows raised, with a small shake of the head',
              'grins openly mischievous, eyes narrowing, leaning in'),
    'surprise': ('reacts with eyebrows jumping and a quick intake of breath', 'is visibly surprised: eyebrows shoot up, mouth falls open, the body pulls back',
                 'is completely stunned: jaw drops, hands fly up, a step back, a sharp gasp'),
    'amazed': ('stares with eyes widening in wonder', 'stares in open-mouthed amazement, eyes wide, slowly shaking the head',
               'is overwhelmed with amazement, hands to the face, mouth open, lost for words'),
    'moved': ('is moved: eyes glisten, a hard swallow, the breath catches', 'is visibly moved: eyes fill with tears, lips press together, the chin trembles',
              'is overcome: bursts into tears, a hand pressed over the mouth, shoulders heaving'),
    'heartbroken': ('lets the face fall, eyes dropping, a slow breath out', 'is heartbroken: the face crumples, tears well up, shoulders sink',
                    'breaks down sobbing, face crumpling, gasping for breath between sobs, the body folding in, tears streaming'),
    'sad': ('lets the face fall, eyes down, with a heavy breath', 'is clearly sad: eyes wet, mouth pulled down, shoulders slumped',
            'cries openly, tears running down, breath hitching, wiping the face again and again'),
    'angry': ('tightens the jaw, eyes narrowing', 'is angry: jaw clenched, nostrils flaring, sharp gestures, leaning in',
              'explodes with fury: face flushed, veins standing out, shouting, jabbing a finger, the whole body lunging forward'),
    'annoyed': ('rolls the eyes with a short sigh', 'is visibly annoyed: rolls the eyes, sighs loudly, arms folding',
                'is fed up: throws the hands up, turns away, groaning in exasperation'),
    'scared': ('flinches, eyes flicking wide, the body tensing', 'is scared: eyes wide, breath fast, pulling back, hands coming up',
               'is terrified: recoils screaming, eyes huge, breath ragged and panicked, the whole body shaking'),
    'nervous': ('fidgets with the hands, glancing away', 'is nervous: fidgeting, shifting weight, swallowing, glancing around',
                'is very nervous: rubbing the hands, laughing awkwardly, breath shallow, unable to keep still'),
    'confused': ('frowns quickly, tilting the head', 'is confused: brow furrowed, head tilted, hands opening in a question',
                 'is completely lost: frowning hard, shaking the head, looking around, hands up'),
    'curious': ('leans in a little, eyebrows lifting', 'leans in with curiosity, eyes bright, head tilted, a small step closer',
                'moves right in, eager, eyes wide, pointing, the whole body asking'),
    'relieved': ('lets out a small breath, shoulders dropping', 'is relieved: a big exhale, shoulders dropping, a relieved laugh',
                 'sags with relief, a hand on the chest, laughing and breathing out hard'),
    'hopeful': ('looks up hopefully, eyebrows lifting', 'is hopeful: eyes bright, a small smile, leaning forward',
                'is full of hope: hands clasped, eyes shining, rising onto the toes'),
    'determined': ('sets the jaw, holding a steady look', 'is determined: chin up, shoulders squared, a firm nod',
                   'is fiercely determined: jaw set, stepping forward, fists clenched'),
    'proud': ('smiles with pride, chin lifting', 'stands taller with pride, chest out, a big smile',
              'beams with pride, a huge grin, chest out, unable to stop smiling'),
    'grateful': ('nods with a grateful smile', 'is grateful: a hand on the heart, eyes warm, nodding',
                 'is overwhelmed with gratitude: eyes wet, both hands on the heart, reaching out'),
    'loving': ('looks on fondly, softening', 'gazes with open love, smiling softly, leaning closer',
               'is full of adoration: can\'t look away, smiling, reaching out to touch'),
    'flirty': ('throws a playful look, a quick smile', 'flirts: holding the look, a slow smile, tilting the head',
               'flirts boldly: a wink, a confident grin, stepping closer'),
    'embarrassed': ('smiles bashfully, looking down', 'is embarrassed: blushing, laughing nervously, covering the face for a moment',
                    'is mortified: hiding the face in the hands, laughing, turning away'),
    'guilty': ('glances away guiltily', 'looks guilty: eyes down, shoulders hunched, rubbing the neck',
               'is crushed by guilt: can\'t meet the eyes, face tight, close to tears'),
    'jealous': ('narrows the eyes, glancing over', 'is jealous: arms crossed, jaw tight, watching sideways',
                'seethes with jealousy: glaring, lips pressed thin, turning away sharply'),
    'disgusted': ('wrinkles the nose with a grimace', 'is disgusted: grimacing, pulling back, a hand coming up',
                  'recoils in disgust: gagging, turning the head away, waving it off'),
    'bored': ('sighs, eyes drifting away', 'is bored: slumping, sighing, checking around, tapping a foot',
              'is utterly bored: rolling the head back, groaning, slumped'),
    'tired': ('rubs the eyes, shoulders heavy', 'is tired: slumped, yawning, rubbing the face',
              'is exhausted: barely upright, eyes closing, a long heavy breath'),
    'calm': ('breathes easily, relaxed and open', 'is calm: loose shoulders, an easy smile, slow breathing',
             'is deeply at peace: eyes soft, a slow full breath, completely relaxed'),
    'excited': ('lights up, bouncing a little', 'is excited: eyes wide, grinning, hands moving fast, can\'t keep still',
                'is bursting with excitement: jumping, clapping, talking with the whole body'),
    'delighted': ('lights up with a delighted smile', 'is delighted: a big smile, eyes shining, a happy laugh',
                  'is overjoyed: laughing, arms opening, almost jumping'),
    'thoughtful': ('pauses, eyes drifting up in thought', 'thinks it over: eyes to the side, lips pursed, a slow nod',
                   'is deep in thought: a long pause, a hand on the chin, eyes far away, then a slow nod'),
    'nostalgic': ('gets a faraway look as a memory surfaces', 'drifts into a memory: eyes far away, a small wistful smile, a sigh',
                  'is lost in a memory: eyes glistening, smiling and sad at once, a long breath'),
    'skeptical': ('raises an eyebrow', 'looks skeptical: one eyebrow up, head tilted back, arms folding',
                  'is openly skeptical: squinting, shaking the head, a disbelieving laugh'),
    'serious': ('looks straight on, earnest', 'is serious: a steady gaze, still hands, leaning in to make the point',
                'is deadly serious: holding the gaze hard, jaw set, every word weighted'),
    'warm': ('softens, a warm look', 'is warm: an open smile, a light touch on the arm, leaning in',
             'is full of warmth: pulling the other close, smiling, eyes shining'),
    'awkward': ('shifts awkwardly, glancing away', 'is awkward: an uncomfortable laugh, rubbing the neck, looking anywhere else',
                'is painfully awkward: frozen smile, darting eyes, a nervous laugh, shuffling the feet'),
    'funny': ('gets a playful look, a grin', 'jokes around: grinning, playful gestures, laughing',
              'clowns around: big playful gestures, laughing, nudging the other'),
}
# V0.9.35: at the strong step the feeling also reaches the voice and the soundtrack (H3 makes both)
INTENSE_TAIL = 'played at full intensity, raw and real, building through the shot'
VOICE = {   # how a line sounds when the speaker feels this strongly (only when you wrote no "how" yourself)
    'laugh': 'laughing through the words', 'chuckle': 'laughing through the words', 'smile': 'beaming, the voice bright',
    'surprise': 'in a shocked, breathless rush', 'amazed': 'breathless with wonder', 'moved': 'the voice cracking with tears',
    'heartbroken': 'through sobs, the voice breaking', 'sad': 'the voice breaking, tearful', 'angry': 'shouting, the voice raw with anger',
    'annoyed': 'snapping, exasperated', 'scared': 'in a panicked, shaking voice', 'nervous': 'stammering, the voice tight',
    'excited': 'loud and fast, almost shouting with excitement', 'delighted': 'loud and joyful, laughing', 'relieved': 'laughing and breathless with relief',
    'determined': 'hard and loud, every word punched', 'proud': 'loud and glowing', 'grateful': 'the voice thick with emotion',
    'embarrassed': 'laughing awkwardly, the voice going up', 'guilty': 'barely holding it together, the voice cracking',
    'jealous': 'bitter and sharp', 'disgusted': 'with a loud groan of disgust', 'serious': 'low and hard', 'loving': 'softly, full of feeling',
    'awkward': 'stumbling over the words', 'funny': 'laughing, playful', 'skeptical': 'with a disbelieving laugh',
}
SOUND = {   # what the soundtrack carries when a feeling is strong
    'laugh': 'loud, helpless laughter', 'chuckle': 'laughter', 'moved': 'crying and shaky breaths', 'heartbroken': 'sobbing and ragged, gasping breaths',
    'sad': 'crying and shaky breaths', 'angry': 'sharp, heavy breathing and raised voices', 'scared': 'fast, panicked breathing and a scream',
    'surprise': 'a sharp gasp', 'excited': 'excited squeals and laughter', 'delighted': 'joyful laughter', 'relieved': 'a long exhale and relieved laughter',
    'nervous': 'quick, shallow breathing', 'disgusted': 'a loud groan', 'tired': 'heavy breathing', 'annoyed': 'a loud, exasperated sigh',
}
LISTEN = ('reacts visibly while {speaker} talks: eyes on {speaker}, the face answering every line (eyebrows, a smile or '
          'a frown), nodding or shaking the head, shifting weight')

def phrase(key, intensity='normal'):
    """The acting for an emotion at an intensity ('subtle' = a little, 'normal', 'strong' = very), or None if unknown."""
    p = PERF.get(key)
    if not p: return None
    return p[{'subtle': 0, 'strong': 2}.get(intensity, 1)]

def expressive(st):
    return st['settings'].get('acting', 'expressive') != 'understated'

def strong_tail(intensity): return (', ' + INTENSE_TAIL) if intensity == 'strong' else ''
def voice(key, intensity): return VOICE.get(key) if intensity == 'strong' else None
def sound(key, intensity): return SOUND.get(key) if intensity == 'strong' else None
