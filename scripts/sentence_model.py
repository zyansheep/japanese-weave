"""The sentence-open model and grammar intake (docs/grammar.md). Python 3.9+, standard library only.

Model: logit P(open | read) = a + b * (Japanese characters / 10) + the sum of the difficulty d_p of each pattern p in
the sentence that the reader doesn't know yet (grammar.py; a known pattern adds 0). An open is a hover on the
sentence's closing mark (its translation). Fitted by penalized maximum likelihood (Newton's method), like the word
model (knowledge.Fit): a Gaussian prior on each d_p (mean 0, sd DIFFICULTY_SD) and weak ones on a and b; each sentence
weighted by its chance of having been read (1 when opened; else the line rule down to the lowest open in the message,
or its paragraph's time on screen, as knowledge.observed weighs a word) and by its age (knowledge.EVIDENCE_DAYS). The
intervals are the fit's curvature (Laplace).

Data: chat replies only. The hook sends, per reply, its fingerprint and per sentence the index of its closing mark,
its Japanese characters, its English words and its pattern ids (grammar.record, kept as `sentences` records in the ops
table); the collector joins them to T3 messages shown by the same fingerprint of their annotations (units). A press of
"English only" in T3 Code (POST /v1/english, on) names the messages on screen: each Japanese sentence in them counts as
an open of weight PRESS_WEIGHT, weak evidence that it was too hard.

Uses: the longest sentence of known patterns that stays at the target open chance (max_chars, the weave's limit), the
open chance of a typical allowed sentence (typical, which prices a sentence in the weave once the fit is ready),
check-reply's prediction per sentence (predict), and intake: one pattern learned at a time, known once its difficulty
is near zero with enough evidence, the next one taken only while sentence opens per read sentence stay under TARGET.
"""
import math
import time

import grammar
import knowledge

TARGET = 0.05              # the sentence open chance aimed at: the weave's length limit, check-reply's flag, intake
DIFFICULTY_SD = 0.8        # the prior sd of a pattern's difficulty (log odds; mean 0)
A_PRIOR = (math.log(0.03 / 0.97), 2.0)  # weak: an open chance about 3% at no length, sd 2 in log odds
B_PRIOR = (0.7, 1.0)       # weak: odds times about 2 per 10 Japanese characters
PRESS_WEIGHT = 0.3         # a Japanese sentence on screen when "English only" was turned on: an open of this weight
READ_MIN = 0.05            # a sentence read with less chance than this is left out (as knowledge.observed does)
READY_READ = 150           # the fit replaces the fixed price once it rests on this many sentences read (weighted)
READY_OPENS = 8            # and this many opens
LENGTH_START = 14          # the weave's limit in Japanese characters before the fit is ready
LENGTH_RANGE = (8, 24)     # and the most it may be set to from the fit
TYPICAL_SHARE = 0.7        # a typical allowed sentence is this share of the limit long, of known patterns only
KNOWN_EVIDENCE = 15        # a learning pattern becomes known after this many read sentences with it (weighted)
KNOWN_D = 0.3              # with its fitted difficulty at most this (near zero: odds x1.35 at most). Its interval is
                           # too wide to judge on at 15 sentences: at a 4% open chance they hold about 0.6 opens
INTAKE_DAYS = 7            # intake looks at sentence opens per read sentence over these days
INTAKE_MIN = 20            # needing this many read sentences there (fewer: nothing against taking one)
FREQUENCY_DAYS = 30        # how often a pattern came up in replies, for choosing the next
ITERATIONS = 50
TOLERANCE = 1e-6
DAY_MS = 86_400_000
Z90 = 1.6449


class Unit:
    """One sentence read (or pressed away): when, its Japanese characters, its patterns, opened (y, 0..1), weight."""
    __slots__ = ('at', 'chars', 'ids', 'y', 'w', 'pseudo')

    def __init__(self, at, chars, ids, y, w, pseudo=False):
        self.at, self.chars, self.ids, self.y, self.w, self.pseudo = at, chars, tuple(ids), y, w, pseudo


# ---- the join ----

def records_by_fp(records):
    """{fingerprint: {mark index: (Japanese characters, English words, pattern ids)}} from sentences records (oldest
    first): the newest scanner version wins, then the latest."""
    out, versions = {}, {}
    for r in records:
        if not grammar.valid_record(r):
            continue
        v = r.get('v', 0)
        if v < versions.get(r['fp'], -1):
            continue
        versions[r['fp']] = v
        out[r['fp']] = {s[0]: (s[1], s[2], tuple(i for i in s[3] if i in grammar.BY_ID)) for s in r['s']}
    return out


def context_fp(context):
    """A T3 message's fingerprint from its annotations, as grammar.fingerprint takes a reply's."""
    return grammar.fingerprint([n[0] for n in context['annotations']])


def message_sentences(context, by_fp):
    """[(annotation index, chars, ids)] of a T3 message's Japanese sentences that its record names, each checked to
    be a closing mark in the message; [] without a record."""
    found = by_fp.get(context_fp(context))
    if not found:
        return []
    notes = context['annotations']
    return [(i, chars, ids) for i, (chars, _en, ids) in sorted(found.items())
            if i < len(notes) and notes[i][0] in grammar.MARKS]


def units(showings, by_fp, pace=knowledge.PACE_MS, top=knowledge.READ_MAX, presses=(), contexts=None):
    """The sentences read, joined: [Unit]. showings: knowledge.showings' [(at, key, context, items)] (T3 messages
    only count). An open is a peek or pin on the closing mark (knowledge._opened: y 1, GLANCE_Y for a glance); an
    unopened sentence weighs its read chance: 1 down to the lowest open in the message, halving per line below it, or
    its paragraph's time on screen (knowledge.stay_chance) when higher. presses: "English only" turned on
    ({at, contexts: [{view, context}]}), each Japanese sentence of the named messages (in contexts, every T3 message
    by key) an open of PRESS_WEIGHT."""
    out = []
    for at, key, context, items in showings:
        if key[0] != 't3':
            continue
        found = message_sentences(context, by_fp)
        if not found:
            continue
        notes = context['annotations']
        opened = knowledge._opened('t3', notes, items)
        line = knowledge._line_height([n[2] for n in notes])
        lowest = max(notes[i][2] for i in opened) if opened else None
        read = knowledge.reader('t3', context, pace, top)
        for index, chars, ids in found:
            y_pos = notes[index][2]
            if index in opened:
                out.append(Unit(at, chars, ids, opened[index], 1.0))
                continue
            near = 0.0
            if lowest is not None:
                below = y_pos - lowest
                near = 1.0 if below <= line / 2 else 0.5 ** math.ceil(below / line)
            weight = max(near, read(y_pos, line))
            if weight >= READ_MIN:
                out.append(Unit(at, chars, ids, 0.0, weight))
    for press in presses:
        for ref in press.get('contexts') or ():
            context = (contexts or {}).get(('t3', ref.get('view'), ref.get('context')))
            if context is None:
                continue
            for _index, chars, ids in message_sentences(context, by_fp):
                out.append(Unit(press['at'], chars, ids, 1.0, PRESS_WEIGHT, pseudo=True))
    return out


# ---- the fit ----

def _inverse(m):
    """The inverse of a small positive definite matrix (Gauss-Jordan with pivoting)."""
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [x / p for x in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [row[n:] for row in a]


def fit(found, statuses, now_ms=None):
    """The model fitted to units (found) for the reader's pattern statuses ({id: known|learning|new}): a dict with a,
    b (per 10 characters) and their sd, and per pattern not known that the units hold, its difficulty d, sd, 90%
    interval, the read sentences with it (read, weighted by read chance only) and their opens; read and opens in all
    (pseudo-observations left out), and ready (enough to replace the fixed price)."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    hard = sorted({i for u in found for i in u.ids if statuses.get(i, 'new') != 'known'},
                  key=lambda i: grammar.IDS.index(i))
    column = {pid: 2 + n for n, pid in enumerate(hard)}
    rows = []
    for u in found:
        decay = 0.5 ** (max(0, now_ms - u.at) / DAY_MS / knowledge.EVIDENCE_DAYS)
        rows.append((u.chars / 10, [column[i] for i in u.ids if i in column], u.y, u.w * decay))
    size = 2 + len(hard)
    means = [A_PRIOR[0], B_PRIOR[0]] + [0.0] * len(hard)
    precisions = [1 / A_PRIOR[1] ** 2, 1 / B_PRIOR[1] ** 2] + [1 / DIFFICULTY_SD ** 2] * len(hard)
    theta = means[:]
    hessian = None
    for _ in range(ITERATIONS):
        grad = [-(t - m) * p for t, m, p in zip(theta, means, precisions)]
        hessian = [[0.0] * size for _ in range(size)]
        for i in range(size):
            hessian[i][i] = precisions[i]
        for x, cols, y, w in rows:
            eta = theta[0] + theta[1] * x + sum(theta[c] for c in cols)
            p = knowledge.sigmoid(eta)
            r, h = w * (y - p), w * p * (1 - p)
            feats = [(0, 1.0), (1, x)] + [(c, 1.0) for c in cols]
            for i, fi in feats:
                grad[i] += r * fi
                for j, fj in feats:
                    hessian[i][j] += h * fi * fj
        step = knowledge._solve(hessian, grad)
        biggest = max(abs(s) for s in step)
        if biggest > 2:  # damp a wild first step
            step = [s * 2 / biggest for s in step]
        theta = [t + s for t, s in zip(theta, step)]
        if biggest < TOLERANCE:
            break
    cov = _inverse(hessian) if hessian else [[A_PRIOR[1] ** 2, 0], [0, B_PRIOR[1] ** 2]]
    sd = [math.sqrt(max(cov[i][i], 0.0)) for i in range(size)]
    real = [u for u in found if not u.pseudo]
    patterns = {}
    for pid in hard:
        c = column[pid]
        mine = [u for u in real if pid in u.ids]
        patterns[pid] = {'d': round(theta[c], 3), 'sd': round(sd[c], 3),
                         'low': round(theta[c] - Z90 * sd[c], 3), 'high': round(theta[c] + Z90 * sd[c], 3),
                         'read': round(sum(u.w for u in mine), 1), 'opens': round(sum(u.y for u in mine), 1),
                         'sentences': len(mine)}
    read, opens = sum(u.w for u in real), sum(u.y for u in real)
    return {'a': round(theta[0], 4), 'b': round(theta[1], 4), 'sd': {'a': round(sd[0], 4), 'b': round(sd[1], 4)},
            'patterns': patterns, 'read': round(read, 1), 'opens': round(opens, 1), 'units': len(real),
            'pressed': sum(1 for u in found if u.pseudo),
            'ready': read >= READY_READ and opens >= READY_OPENS, 'target': TARGET}


def logit(p):
    return math.log(p / (1 - p))


def predict(model, chars, ids, statuses):
    """The open chance of a sentence of `chars` Japanese characters using the patterns `ids`, for the reader's
    statuses; a pattern not known with no fitted difficulty adds 0 (its prior mean)."""
    a, b = (model['a'], model['b']) if model else (A_PRIOR[0], B_PRIOR[0])
    eta = a + b * chars / 10
    for pid in set(ids):
        if statuses.get(pid, 'new') != 'known':
            eta += ((model or {}).get('patterns') or {}).get(pid, {}).get('d', 0.0)
    return knowledge.sigmoid(eta)


def max_chars(model):
    """The weave's limit: the Japanese characters at which a sentence of known patterns reaches TARGET, within
    LENGTH_RANGE; LENGTH_START until the fit is ready."""
    if not model or not model.get('ready'):
        return LENGTH_START
    if model['b'] <= 0.05:
        return LENGTH_RANGE[1]
    chars = 10 * (logit(TARGET) - model['a']) / model['b']
    return int(max(LENGTH_RANGE[0], min(LENGTH_RANGE[1], math.floor(chars))))


def typical(model):
    """(Japanese characters, open chance) of a typical allowed sentence: TYPICAL_SHARE of the limit, known patterns
    only. None until the fit is ready."""
    if not model or not model.get('ready'):
        return None
    chars = round(TYPICAL_SHARE * max_chars(model))
    return chars, knowledge.sigmoid(model['a'] + model['b'] * chars / 10)


def allowed(ids, statuses):
    """(new patterns, learning patterns) among ids: a sentence may use known ones and at most one learning one."""
    new = [i for i in ids if statuses.get(i, 'new') == 'new']
    learning = [i for i in ids if statuses.get(i) == 'learning']
    return new, learning


# ---- intake ----

def recent_rate(found, now_ms, days=INTAKE_DAYS):
    """(sentence opens, read sentences) over the last `days` days, read chance weighted, pseudo-observations out."""
    since = now_ms - days * DAY_MS
    mine = [u for u in found if not u.pseudo and u.at >= since]
    return sum(u.y for u in mine), sum(u.w for u in mine)


def frequency(records, now_ms, days=FREQUENCY_DAYS):
    """{pattern id: sentences using it} in the replies of the last `days` days (sentences records, each reply once)."""
    first = time.strftime('%Y-%m-%d', time.localtime((now_ms - days * DAY_MS) / 1000))
    out, seen = {}, set()
    for r in reversed(list(records)):
        if not grammar.valid_record(r) or r['fp'] in seen or r['day'] < first:
            continue
        seen.add(r['fp'])
        for s in r['s']:
            for pid in set(s[3]):
                out[pid] = out.get(pid, 0) + 1
    return out


def learning_evidence(state, found):
    """(the pattern being learned, the day it began, the read sentences with it since then, weighted), or
    (None, None, 0.0). Only sentences since it began count toward KNOWN_EVIDENCE: the replies use it on purpose then."""
    current, since = grammar.learning(state)
    if current is None:
        return None, None, 0.0
    try:
        start = time.mktime(time.strptime(since, '%Y-%m-%d')) * 1000
    except (TypeError, ValueError):
        start = 0
    return current, since, sum(u.w for u in found if not u.pseudo and current.id in u.ids and u.at >= start)


def intake(state, model, found, counts, now_ms):
    """The grammar op the refresh makes (hub.py, grammar.apply), or None: the learning pattern known once
    KNOWN_EVIDENCE sentences with it were read since it began (learning_evidence) and its fitted difficulty is at most
    KNOWN_D; then, with none being learned, the next while sentence opens per read sentence over INTAKE_DAYS are under
    TARGET (or too few were read to say): the new pattern most used in replies (counts), the inventory's order breaking
    ties."""
    statuses = grammar.statuses(state)
    changes = []
    current, _since, read_since = learning_evidence(state, found)
    if current is not None:
        fitted = (model.get('patterns') or {}).get(current.id)
        if fitted and read_since >= KNOWN_EVIDENCE and fitted['d'] <= KNOWN_D:
            changes.append([current.id, 'known'])
            statuses[current.id] = 'known'
            current = None
    if current is None:
        opens, read = recent_rate(found, now_ms)
        if read < INTAKE_MIN or opens / read < TARGET:
            new = [p.id for p in grammar.PATTERNS if statuses[p.id] == 'new']
            if new:
                changes.append([max(new, key=lambda i: (counts.get(i, 0), -grammar.IDS.index(i))), 'learning'])
    return {'kind': 'grammar', 'set': changes, 'source': 'intake'} if changes else None


def summary(model, found, counts, now_ms, state=None):
    """knowledge.json "grammar": the fit (fit), the weave's limit (maxChars) and typical sentence (typical: chars, p),
    the recent rate intake reads (recent: days, read, opens), how often each pattern came up (frequency), and the
    pattern being learned with its read sentences since it began (learning: {id, since, read}, or None)."""
    opens, read = recent_rate(found, now_ms)
    shape = typical(model)
    current, since, read_since = learning_evidence(state, found)
    return dict(model, maxChars=max_chars(model), typical=list(shape) if shape else None,
                recent={'days': INTAKE_DAYS, 'read': round(read, 1), 'opens': round(opens, 1)},
                frequency=dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                learning={'id': current.id, 'since': since, 'read': round(read_since, 1)} if current else None)


def text(model, statuses=None):
    """fw.py knowledge's lines on the sentence model."""
    if not isinstance(model, dict) or 'a' not in model:
        return ['  sentences: no sentence model yet (it comes with the next refresh once replies are joined)']
    lines = [f'  sentences: {model["units"]} joined, {model["read"]} read and {model["opens"]} opened (weighted by read '
             f'chance){", " + str(model["pressed"]) + " from English-only presses" if model.get("pressed") else ""}; '
             f'{"ready" if model.get("ready") else "not ready yet: the weave keeps the fixed price"}',
             f'    open chance: logit = {model["a"]:+.2f} (sd {model["sd"]["a"]:.2f}) + {model["b"]:.2f} (sd '
             f'{model["sd"]["b"]:.2f}) per 10 Japanese characters, odds x{math.exp(model["b"]):.1f} per 10; known '
             f'patterns only reach {TARGET:.0%} at {max_chars(model)} characters (the weave\'s limit)']
    hard = sorted((model.get('patterns') or {}).items(), key=lambda kv: -kv[1]['d'])
    if hard:
        lines.append('    patterns not known yet, hardest first (difficulty in log odds, 90% interval, read sentences, '
                     'opens):')
    for pid, v in hard[:12]:
        name = grammar.BY_ID[pid].name if pid in grammar.BY_ID else pid
        status = f' [{statuses[pid]}]' if statuses and statuses.get(pid) == 'learning' else ''
        lines.append(f'      {name}{status}: {v["d"]:+.2f} [{v["low"]:+.2f}, {v["high"]:+.2f}], {v["read"]} read, '
                     f'{v["opens"]} opened')
    return lines
