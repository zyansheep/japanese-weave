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

Replies with render-time levels (twins.py) carry every level at once and T3 shows each message at the reader's two
levels (the word level for the swapped words, the sentence level for the twins), so a message's annotations are a
subset of the reply's glossed words, a different one at each pair. Their records (grammar.record_levels) hold every
element with its threshold and propensities and the fingerprint of what each pair shows; the collector keeps each pair
a message was shown at as a showing of its own (feedback.py: its items carry the levels, and a change of either splits
them), so the join (records_by_fp, message_sentences) matches a showing's annotations against the fingerprint at the
pair it reports and reads off which sentences it showed and where: only what was rendered is evidence, and a message
read again at another pair is another showing. A record from before the sentence level has the fingerprints of the
equal pairs only. Each unit keeps the sentence level, its twin's threshold and propensity there, the open chance
check-reply predicted and whether its place was drawn at random (explored), for exploration.

Selection. Which twins show at a level depends on the model's own predictions (twins.assign ranks by value per
expected open), so without randomness the fit would see only the sentences it already expects to be easy, and a
misspecified model could never learn it was wrong about the others. A share of each reply's places is random
(twins.EXPLORE): exploration compares the expected and seen opens of the twins placed at random, a sample the model
didn't choose, against those it chose. The fit itself is unweighted: when selection depends only on the features the
model has (length and patterns, and the learning words' value), the likelihood of the opens given the features is the
same with or without it, so the estimates stay consistent if the model is right. If exploration shows the chosen and
the random twins disagreeing, the fit can weight each unit by 1 / its propensity at its level (clipped, say at 20, and
normalized to the units' count), which makes it estimate the open chance over every twin written rather than over the
ones shown; or it can be fitted on the explored units alone as a check.

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
    """One sentence read (or pressed away): when, its Japanese characters, its patterns, opened (y, 0..1), weight; and
    for a reply with levels (Meta): the sentence level it was shown at, its twin's threshold, its propensity there, the
    open chance predicted when it was written (p) and whether its place was drawn at random (x), each None when
    unknown."""
    __slots__ = ('at', 'chars', 'ids', 'y', 'w', 'pseudo', 'level', 'on', 'prop', 'p', 'x')

    def __init__(self, at, chars, ids, y, w, pseudo=False, meta=None):
        self.at, self.chars, self.ids, self.y, self.w, self.pseudo = at, chars, tuple(ids), y, w, pseudo
        self.level, self.on, self.prop, self.p, self.x = meta if meta is not None else (None,) * 5


# ---- the join ----

class Leveled:
    """A sentences record of a reply with render-time levels (grammar.record_levels), as the join reads it: which of
    its sentences each pair of levels (word level, sentence level) shows, and where. A pair None: as if every glossed
    word showed (a client that doesn't know levels)."""

    def __init__(self, r):
        import twins
        self.r, self.twins = r, twins
        self.els = [{'kind': e[0], 'on': e[1], 'parent': None if e[2] < 0 else e[2]} for e in r['e']]

    def pairs(self):
        """[(fingerprint, pair)] of what each pair shows: every pair of twins.PAIRS from a record with pf, else the
        equal pairs -2..3 (fps, a record from before the sentence level)."""
        if 'pf' in self.r:
            return list(zip(self.r['pf'], self.twins.PAIRS))
        return [(fp, (level, level)) for fp, level in zip(self.r['fps'], self.twins.SHOWING)]

    def _shown(self, pair):
        owners = self.r['a']
        return list(range(len(owners))) if pair is None else [
            j for j, o in enumerate(owners) if self.twins.visible(self.els, o, pair)]

    def twin_groups(self, pair):
        """{index of a sentence mark among the annotations shown at a pair: the indexes there of every annotation of
        the same twin's Japanese}."""
        owners, shown = self.r['a'], self._shown(pair)
        by_twin = {}
        for k, j in enumerate(shown):
            o = owners[j]
            if o >= 0 and self.els[o]['kind'] == 's':
                by_twin.setdefault(o, []).append(k)
        marks = {s[0] for s in self.r['s']}
        return {k: set(by_twin[owners[j]]) for k, j in enumerate(shown)
                if j in marks and owners[j] >= 0 and owners[j] in by_twin}

    def sentences(self, pair):
        """[(index among the annotations shown at a pair, chars, en, ids, meta)] for the sentences shown at a pair of
        levels (word level, sentence level); None: as if every glossed word showed (a client that doesn't know
        levels). meta: (the sentence level, threshold, propensity there, predicted p, explored) of the twin the
        sentence is in (Unit): a twin shows by the sentence level alone."""
        owners = self.r['a']
        shown = self._shown(pair)
        level = None if pair is None else pair[1]
        place = {j: k for k, j in enumerate(shown)}
        out = []
        for s in self.r['s']:
            if s[0] not in place:
                continue
            o = owners[s[0]]
            e = self.r['e'][o] if o >= 0 else None
            prop = None
            if e is not None and level is not None:
                if level in self.twins.BUDGETED and e[3]:
                    prop = e[3][self.twins.BUDGETED.index(level)]
                elif level == self.twins.FULL:
                    prop = 1.0
            meta = (level, e[1] if e else None, prop, s[4] if len(s) > 4 else None, e[5] if e else None)
            out.append((place[s[0]], s[1], s[2], tuple(i for i in s[3] if i in grammar.BY_ID), meta))
        return out


def mark_twins(contexts, by_fp):
    """Gives each T3 message joined to a record with levels its twins: context "twins", {index of a sentence mark: the
    indexes of every annotation of the same twin's Japanese}, at the levels it reports (message_level), for
    knowledge._in_english. Returns how many it marked."""
    marked = 0
    for c in contexts.values():
        if c.get('surface') != 't3':
            continue
        found = by_fp.get(context_fp(c))
        if not isinstance(found, tuple):
            continue
        entry, levels = found
        groups = entry.twin_groups(message_level(c, levels))
        if groups:
            c['twins'] = groups
            marked += 1
    return marked


def message_pair(context):
    """The (word level, sentence level) a T3 message reports it was shown at (feedback.load's level and sentences; one
    dial: both the same), or None (no levels)."""
    level = context.get('level')
    if level is None:
        return None
    return level, context['sentences'] if context.get('sentences') is not None else level


def message_level(context, pairs):
    """The pair of levels a message joined to a record with levels is read at: the pair it reports when its
    annotations match the record's there, else the highest pair they match, by sentence level then word level (None:
    every glossed word, a client without levels)."""
    pair = message_pair(context)
    return pair if pair in pairs else max(pairs, key=lambda x: (-9, -9) if x is None else (x[1], x[0]))


def records_by_fp(records):
    """{fingerprint: {mark index: (Japanese characters, English words, pattern ids)}} from sentences records (oldest
    first): the newest scanner version wins, then the latest. A record with levels (Leveled) comes under the fingerprint
    of what each pair of levels shows that shows a sentence (Leveled.pairs: every pair of a word level and a sentence
    level, or for a record from before the sentence level the equal pairs -2..3), as (Leveled, [those pairs]), and
    under its own fingerprint (every glossed word) as (Leveled, [None])."""
    out, versions, chosen = {}, {}, {}
    for r in records:
        if not grammar.valid_record(r):
            continue
        v = r.get('v', 0)
        if v < versions.get(r['fp'], -1):
            continue
        versions[r['fp']] = v
        chosen.pop(r['fp'], None)
        chosen[r['fp']] = r
    for fp, r in chosen.items():
        if 'e' not in r:
            out[fp] = {s[0]: (s[1], s[2], tuple(i for i in s[3] if i in grammar.BY_ID)) for s in r['s']}
            continue
        entry = Leveled(r)
        out[fp] = (entry, [None])
        levels = {}
        for shown, pair in entry.pairs():
            if entry.sentences(pair):
                levels.setdefault(shown, []).append(pair)
        for shown, at in levels.items():
            out[shown] = (entry, at)
    return out


def context_fp(context):
    """A T3 message's fingerprint from its annotations, as grammar.fingerprint takes a reply's."""
    return grammar.fingerprint([n[0] for n in context['annotations']])


def message_sentences(context, by_fp):
    """[(annotation index, chars, ids)] of a T3 message's Japanese sentences that its record names, each checked to
    be a closing mark in the message; [] without a record. For a reply with levels each also has its meta (Leveled):
    the sentences shown at the pair of levels the message reports (context "level" and "sentences", feedback.load), or
    when it reports none or another, at the highest pair its annotations match."""
    found = by_fp.get(context_fp(context))
    if not found:
        return []
    notes = context['annotations']
    if isinstance(found, tuple):
        entry, levels = found
        level = message_level(context, levels)
        return [(i, chars, ids, meta) for i, chars, _en, ids, meta in entry.sentences(level)
                if i < len(notes) and notes[i][0] in grammar.MARKS]
    return [(i, chars, ids) for i, (chars, _en, ids) in sorted(found.items())
            if i < len(notes) and notes[i][0] in grammar.MARKS]


def press_context(contexts, ref, at, level, messages=None, sentences=None):
    """The T3 message a press of English only named ({view, context}), as shown when it was pressed: its showing at
    the chat's word and sentence levels then (feedback.level_context; sentences None: not known), else at the word
    level with both dials at it (a client with one dial), else its own (a client that sends no level), else its latest
    showing at any pair that began by then; None when the log has none."""
    import feedback
    view, context = ref.get('view'), ref.get('context')
    found = None
    if level is not None:
        found = (contexts.get(('t3', view, feedback.level_context(context, level, sentences)))
                 or contexts.get(('t3', view, feedback.level_context(context, level))))
    found = found or contexts.get(('t3', view, context))
    if found is None:
        mine = (messages or {}).get((view, context)) or [
            c for (surface, v, _c), c in contexts.items()
            if surface == 't3' and v == view and c.get('message') == context]
        before = [c for c in mine if (c.get('shown_at') or 0) <= at]
        found = max(before, key=lambda c: c.get('shown_at') or 0) if before else None
    return found


def units(showings, by_fp, pace=knowledge.PACE_MS, top=knowledge.READ_MAX, presses=(), contexts=None, changes=()):
    """The sentences read, joined: [Unit]. showings: knowledge.showings' [(at, key, context, items)] (T3 messages
    only count; a message shown at several levels is a showing at each). An open is a peek or pin on the closing mark
    (knowledge._opened: y 1, GLANCE_Y for a glance); an unopened sentence weighs its read chance: 1 down to the lowest
    open in the message, halving per line below it, or its paragraph's time on screen (knowledge.stay_chance) when
    higher. presses: "English only" turned on ({at, contexts: [{view, context}]}), each Japanese sentence of the named
    messages (in contexts, every T3 message by key, as shown at the chat's levels at the press: press_context, changes
    the intensity changes) an open of PRESS_WEIGHT."""
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
        for index, chars, ids, *meta in found:
            meta = meta[0] if meta else None
            y_pos = notes[index][2]
            if index in opened:
                out.append(Unit(at, chars, ids, opened[index], 1.0, meta=meta))
                continue
            near = 0.0
            if lowest is not None:
                below = y_pos - lowest
                near = 1.0 if below <= line / 2 else 0.5 ** math.ceil(below / line)
            weight = max(near, read(y_pos, line))
            if weight >= READ_MIN:
                out.append(Unit(at, chars, ids, 0.0, weight, meta=meta))
    messages = {}
    for (surface, view, _c), c in (contexts or {}).items():
        if surface == 't3' and c.get('message') is not None:
            messages.setdefault((view, c['message']), []).append(c)
    for press in presses:
        level, sentences = knowledge.chat_levels(changes, press['at']) if changes else (None, None)
        for ref in press.get('contexts') or ():
            context = press_context(contexts or {}, ref, press['at'], level, messages, sentences)
            if context is None:
                continue
            for _index, chars, ids, *meta in message_sentences(context, by_fp):
                out.append(Unit(press['at'], chars, ids, 1.0, PRESS_WEIGHT, pseudo=True,
                                meta=meta[0] if meta else None))
    return out


def exploration(found):
    """What calibrate says about the twins (knowledge.json calibration "twins"): over the read units of replies with
    levels whose prediction is known, {explored, ranked}: each {units, read (weighted), expected (the opens predicted
    when written, each times its read weight), seen}; explored: the twins placed at random (twins.EXPLORE), a sample
    the model didn't choose; ranked: those it ranked. And weighted: the open chance over every twin written, estimated
    from the read units each weighted by 1 / its propensity at its level (clipped at 20), against the predicted.
    None when no unit has a prediction."""
    rows = [u for u in found if not u.pseudo and u.p is not None and u.x is not None]
    if not rows:
        return None
    out = {}
    for name, group in (('explored', [u for u in rows if u.x]), ('ranked', [u for u in rows if not u.x])):
        out[name] = {'units': len(group), 'read': round(sum(u.w for u in group), 2),
                     'expected': round(sum(u.w * u.p for u in group), 3), 'seen': round(sum(u.y for u in group), 2)}
    weighted = [(u, 1 / max(u.prop, 0.05)) for u in rows if u.prop]
    read = sum(u.w * k for u, k in weighted)
    out['weighted'] = {'seen': round(sum(u.y * k for u, k in weighted) / read, 4) if read else None,
                       'expected': round(sum(u.w * u.p * k for u, k in weighted) / read, 4) if read else None}
    return out


def exploration_text(report):
    """calibrate's line on the twins (exploration), or None."""
    if not isinstance(report, dict) or not report.get('explored'):
        return None
    part = lambda g: (f'{g["expected"]:.2f} opens expected and {g["seen"]:g} seen over {g["read"]:g} read sentences'
                      f' ({g["units"]} showings)')
    w = report.get('weighted') or {}
    weighted = (f'; over every twin written (by inverse propensity) {w["expected"]:.1%} expected, {w["seen"]:.1%} '
                'seen' if w.get('seen') is not None else '')
    import twins
    return (f'Chat twins placed at random ({twins.EXPLORE:.0%} of places): {part(report["explored"])}; ranked by the '
            f'model: {part(report["ranked"])}{weighted}.')


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
    """{pattern id: sentences using it} in the replies of the last `days` days (sentences records, each reply once):
    what the writing uses, so a reply with levels counts every twin it carries, shown at some level or not, and the
    twins check-reply dropped (its record's d: Claude wrote them, but they broke the limits, as a new pattern does)."""
    first = time.strftime('%Y-%m-%d', time.localtime((now_ms - days * DAY_MS) / 1000))
    out, seen = {}, set()
    for r in reversed(list(records)):
        if not grammar.valid_record(r) or r['fp'] in seen or r['day'] < first:
            continue
        seen.add(r['fp'])
        for ids in [s[3] for s in r['s']] + list(r.get('d') or ()):
            for pid in set(ids):
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
