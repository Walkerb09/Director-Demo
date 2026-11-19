"""Learned take ranking: which takes YOU keep, from your own Keep / Reject decisions.

Every time you keep one take of a shot over another (or keep one and reject another), that is a comparison:
"this take beat that one". The Director measures every take anyway; here it learns which of those measurements
predict your choices (a small Bradley-Terry / logistic model on feature differences), starting from its built-in
scoring and pulled back toward it, so a handful of decisions can't swing it wildly.

It only ranks and explains. It never keeps, rejects or approves anything: the movie still uses the take you keep,
and until you keep one, the best-ranked take stands in (as before). Measurements, never story claims.
"""
from __future__ import annotations
import math
import numpy as np

# feature name -> (plain words when it pushes a take UP, plain words when it pushes DOWN)
FEATURES = {
    'clean':      ('more of the take is usable (no hidden cut)', 'a hidden cut leaves less usable'),
    'identity':   ('faces match the cast closely', 'faces match the cast less closely'),
    'problems':   ('no measured problems', 'measured problems (people count, drift, sides, faces)'),
    'motion':     ('more movement', 'calmer, less movement'),
    'quick':      ('the action starts quickly', 'a slow, settling start'),
    'tight':      ('tighter framing (bigger faces)', 'wider framing'),
    'talk':       ('talking that fits the shot (talk in a dialogue shot, quiet in a silent one)', 'talking that does not fit the shot'),
    'look':       ('its look matches the shot before it', 'its look jumps from the shot before it'),
}
NAMES = tuple(FEATURES)
PRIOR = {'clean': 0.5, 'identity': 1.0, 'problems': 1.0, 'motion': 0.0, 'quick': 0.0, 'tight': 0.0, 'talk': 0.0, 'look': 0.0}  # = the built-in score
MIN_PAIRS = 5            # fewer comparisons than this: the built-in score only, and it says so
FULL_TRUST = 15          # this many comparisons: the learned part counts fully

def features(a, summary=None, dialogue=None, look=None):
    """Measured features of one take, each roughly in 0..1 (higher = more of it). a = the take's measurement file
    (motion, faces, sound); summary = the take's measured checks and identity (kept with the take), merged in."""
    if summary: a = {**a, **{k: summary[k] for k in ('checks', 'identity', 'segments') if summary.get(k) is not None}}
    fr = a.get('frames') or 124
    segs = a.get('segments') or []
    longest = max((s['frames'][1] - s['frames'][0] for s in segs), default=fr)
    meds = [v['median'] for v in ((a.get('identity') or {}).get('people') or {}).values() if v.get('median') is not None]
    checks = a.get('checks', []) or []
    bad = sum(1 for c in checks if c.startswith(('PEOPLE_COUNT', 'BACKGROUND_DRIFT', 'SCREEN_SIDES', 'IDENTITY')) or (c.startswith('POSITIONS: ') and 'not measured' not in c))
    motion = a.get('motion') or [0.0]
    m = float(np.mean(motion[1:])) if len(motion) > 1 else 0.0
    start = next((i for i, v in enumerate(motion) if i and v > 0.25), len(motion))
    faces = a.get('faces') or {}
    hs = [max(f['h'] for f in v) for v in faces.values() if v]
    size = float(np.median(hs)) if hs else 0.0
    rms = a.get('audio_rms') or []
    thr = max(0.008, 0.6 * float(np.percentile(rms, 75))) if rms else 1.0
    talk = (sum(1 for r in rms if r > thr) / len(rms)) if rms else 0.0
    # talk is judged against the shot: talking helps a dialogue shot, quiet helps a shot with no lines (unknown: neutral)
    talk_fit = talk if dialogue is True else (1.0 - talk) if dialogue is False else 0.5
    # look: the look check's verdict against the shot before it (None = not measured / first shot: neutral)
    look_fit = 0.5 if look is None else (1.0 if not look.get('raw') else 0.6 if look.get('fixed') else max(0.0, 0.4 - 0.1 * len(look.get('after') or [])))
    return {'clean': longest / max(fr, 1), 'identity': float(np.mean(meds)) if meds else 0.5, 'problems': -min(bad, 3) / 3.0,
            'motion': min(1.0, m / 3.0), 'quick': 1.0 - min(1.0, start / 24.0), 'tight': min(1.0, size / 0.35), 'talk': talk_fit,
            'look': look_fit}

REJECTED_W, UNREVIEWED_W = 1.0, 0.5     # kept over a take you rejected says more than over one you never reviewed
HALF_LIFE = 40                            # decisions: older choices count half as much after this many newer ones

def pairs_from(decisions):
    """decisions (oldest first): [(shot, kept_features, [(other_features, 'REJECTED'|'CAPTURED')])] -> [(winner, loser, weight)]."""
    out = []; n = len(decisions)
    for i, (_, kept, others) in enumerate(decisions):
        age = n - 1 - i; decay = 0.5 ** (age / HALF_LIFE)
        for o in others:
            f, status = (o if isinstance(o, tuple) else (o, 'REJECTED'))
            out.append((kept, f, decay * (REJECTED_W if status == 'REJECTED' else UNREVIEWED_W)))
    return out

def _pw(p): return (p[0], p[1], p[2] if len(p) > 2 else 1.0)

def learn(pairs, prior=None, l2=1.0, steps=800, lr=0.5):
    """Weights w so that sigmoid(w . (winner - loser)) is high for your choices (weighted); pulled toward the prior."""
    p = dict(prior or PRIOR)
    w = np.array([p.get(k, 0.0) for k in NAMES], float); w0 = w.copy()
    if not pairs: return dict(zip(NAMES, w.tolist()))
    P = [_pw(x) for x in pairs]
    X = np.array([[a[k] - b[k] for k in NAMES] for a, b, _ in P], float); sw = np.array([c for _, _, c in P], float)
    tot = max(float(sw.sum()), 1e-6)
    for _ in range(steps):
        z = np.clip(X @ w, -30, 30); g = -((X * (1 - 1 / (1 + np.exp(-z)))[:, None]) * sw[:, None]).sum(0) / tot + l2 * (w - w0) / max(tot, 1)
        w -= lr * g
    return dict(zip(NAMES, [round(float(x), 4) for x in w]))

def evidence(pairs):
    """How much the comparisons are worth (weighted count) -- what trust() is measured in."""
    return sum(_pw(x)[2] for x in pairs)

def agreement(decisions, prior=None, last=10):
    """Honesty check: for each of your last `last` decisions, learn from all the OTHERS and see whether the ranking
    would have put your kept take first. Returns (agreed, out_of) or None when there's too little to say."""
    if len(decisions) < MIN_PAIRS + 1: return None
    agreed = total = 0
    for i in range(max(0, len(decisions) - last), len(decisions)):
        rest = decisions[:i] + decisions[i + 1:]
        w = learn(pairs_from(rest), prior); t = trust(evidence(pairs_from(rest)))
        _, kept, others = decisions[i]
        fs = [o[0] if isinstance(o, tuple) else o for o in others]
        if not fs: continue                      # nothing to beat: says nothing either way
        pr = prior or PRIOR
        s = lambda f: score(f, pr) + t * (score(f, w) - score(f, pr))
        total += 1; agreed += all(s(kept) > s(f) for f in fs)
    return (agreed, total) if total else None

def trust(n_pairs):
    """How much the learned weights count (0 below MIN_PAIRS, rising to 1 at FULL_TRUST)."""
    if n_pairs < MIN_PAIRS: return 0.0
    return min(1.0, (n_pairs - MIN_PAIRS + 1) / (FULL_TRUST - MIN_PAIRS + 1))

def score(f, w):
    return sum(w.get(k, 0.0) * f.get(k, 0.5) for k in NAMES)

def explain(f_best, f_other, w, top=2):
    """Plain reasons the best take ranks above the other, from the features that made the difference."""
    contrib = sorted(((w[k] * (f_best[k] - f_other[k]), k) for k in NAMES), reverse=True)
    return [FEATURES[k][0] if f_best[k] > f_other[k] else FEATURES[k][1] for c, k in contrib[:top] if c > 0.01]

HABIT_WORDS = {   # feature -> (you value it MORE than the built-in does, you value it LESS)
    'clean': ('you care a lot about takes without hidden cuts', "a hidden cut doesn't bother you much"),
    'identity': ('you keep the takes where faces match the cast most closely', 'you keep takes for other reasons even when faces match less closely'),
    'problems': ('you avoid takes with measured problems even more than the Director does', "you often keep takes despite measured problems (people count, drift, faces)"),
    'motion': ('you tend to keep takes with more movement', 'you tend to keep calmer takes'),
    'quick': ('you tend to keep takes where the action starts quickly', "a slow start doesn't put you off"),
    'tight': ('you tend to keep tighter framing', 'you tend to keep wider framing'),
    'talk': ('you care that the talking fits the shot', "you don't mind talk in quiet shots or quiet in dialogue shots"),
    'look': ('you keep the takes whose look matches the shot before', "a look jump from the shot before doesn't stop you keeping a take"),
}
def habits(w, prior=None, top=3):
    """What the learned weights say you care about, in words (only where they differ clearly from the built-in)."""
    p = prior or PRIOR
    diff = sorted(((w[k] - p[k], k) for k in NAMES), key=lambda x: -abs(x[0]))
    return [HABIT_WORDS[k][0 if d > 0 else 1] for d, k in diff[:top] if abs(d) >= 0.25]
