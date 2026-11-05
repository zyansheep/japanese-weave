"""The probe (PLAN.md section 1 and row R, NEXT_STEPS step 7): a measure of the goal taken directly instead of through
the model, whose own number moves when you get used to the glosses or it is refitted. Since the review wedge it lives
in the review stream: about one review card in ten shows a probe word and one in twenty a made-up one, in between the
Anki-scheduled cards (review.py; one in five and one in ten before grades, of a smaller stream), and the estimate here
is computed from those cards over the last ROLLING_DAYS days (rolling), on every refresh, from their reveal times (a
grade on a probe card is not read here yet). The
monthly 3-minute probe and its chat command are gone; its records stay readable (--history), and a terminal can still
take one (fw.py probe). Python 3.9+, standard library only.

  fw.py probe                    a one-off probe in a terminal, one word at a time: y known, n not, s skip, b back,
                                 q quit
  fw.py probe --history          every probe taken so far, then the review stream's rolling estimate

The goal is the share of the words in what you read that you would read in Japanese without a lookup, each word
weighted by how often it comes up in your reading.

Population. The dictionary's swappable entries (data/dictionary.tsv, swap 1: the words the weave can put in an
English sentence) ranked within TOP (6,394 entries, 86% of the swappable entries' frequency weight). Past it the
ranks rest on thin counts and a learner knows next to none of the words, so a probe would spend its words on sure
"no"s. Left out: words that are a number and a counter (二人, 三時, 一万円: their gloss is a number and their
written form has a numeral), which you read from their parts and which the weave leaves in digits. Katakana loanwords
are not swappable, so they are out already: an English reader sounds them out, so a "yes" would measure kana, not
vocabulary. Words you marked known or learning with a command stay in: they are part of what you read, and the probe
is the check on such labels. Entries spelled alike (方 ほう and 方 かた) are one item, since the probe shows only the
spelling; its weight is theirs added. The review stream also leaves out, while it lasts, any word the delayed-start
experiment hides or is about to probe (review.py): a few dozen words of 6,394, most of them unknown, so the estimate
is a little high by at most their weight.

Weights. A word's share of your reading. Until the reading counts exist (NEXT_STEPS step 13) that is its general
frequency, 1/score (Zipf's law on the dictionary's combined rank). population(counts=...) takes counts per entry id
instead; then every swappable entry counted at least once is in, at any rank.

Bands. The population sorted by weight, cut into BANDS bands of equal weight (with Zipf weights, equal widths in log
rank: ranks 15-50, 50-160, ... 5,500-18,000), so the words within a band are alike in frequency and, mostly, in how
likely you are to know them.

Sampling in the review stream (review.py). Each probe card draws its band in proportion to the band's weight, then a
word of the band at random among those not asked in the last REPEAT_DAYS days (by a probe card or a probe of old; when
none is left, the least recently asked), so within a band every word is equally likely and leaving recent ones out
biases nothing. Made-up words (data/fakes.tsv, tools/build_fakes.py) come from review.py too, never reused within its
FAKE_REUSE_DAYS while any other is left.

Estimate from the review stream (rolling). A probe card's outcome is a reveal time, or no reveal (knowledge.Components:
known at recall, a log time N(mu_K, s_K) and a chance a_K of no reveal; not known, N(mu_U, s_U) and a_U). Of the cards
of the last ROLLING_DAYS days, each word's latest probe card counts, each made-up word's card is of the "not known"
component alone, and the learning words' cards come in with a share known of their own (a nuisance parameter, never
reported) so that every card shapes the components. Fitted together by EM, without the model's k: per band h, pi_h
is the mixture weight on the known component of its cards (weighted by the words' weights, as the ratio estimator
below weighs answers), and the components are held by the same priors as the model's (knowledge.REVIEW_PRIOR). This is
the correction for guessing done card by card: with a yes/no outcome and a made-up word claimed with chance f, the
mixture weight is (t - f) / (1 - f) below; here "claimed" is a reveal time or no reveal that looks known, and the
made-up words show how often a word not known looks so. Overall known = sum(W_h pi_h), W_h the band's share of the
weight. Variance: per band, (sum(w^2) / sum(w)^2) p_h (1 - p_h) times ((1 - n_h/N_h) + (n_h / e_h - 1)), p_h = pi_h
with half a card known and half not added, and e_h = p_h (1 - p_h) I_h the effective number of cards, I_h the
observed information on pi_h (sum of ((A - B) / (p A + (1 - p) B))^2 over its cards): a card whose outcome can't tell
known from not counts for less, and with components that overlap every band's interval widens. Plus what the
components' own uncertainty adds, which moves every band at once (as the uncertainty of the over-claiming rate does in
the correction for guessing below): the delta method on the information of all the parameters together, from the
outer product of each card's score and the priors' curvature, less the bands' own part. The 90% interval is Wilson's
at the effective sample size, as below. Reported with it: the share that looks known at even odds before the
correction (naive), the made-up cards' mean chance of looking known at even odds (overclaim, the f below), and how well
the components separate (Components.separation): a weak separation, as when a reader gives up on unknown words as fast
as they reveal known ones, shows as a wide interval and a low worth. tests/test_review.py checks it on simulated
readers: on 20 of about 120 probe cards each, the error was +0.024 on average with a root mean square of 0.070, the
standard error about 0.08, and the interval covered the truth 17 times.

The probe of old (a probe op, still taken by fw.py probe in a terminal). About 60 words at once: each band gets words
in proportion to its weight (10 of 60), but at most a 1/SHARE of its words; FAKES made-up words not used in FAKE_DAYS;
the order shuffled. In band h, the weighted share of the answered words known: t_h = sum(w y) / sum(w) (a ratio
estimator, Cochran 1977, 6.2), with Tin's (1965) correction, which takes its bias from order 1/n to 1/n^2 (it matters
in the first band, 6 words whose weights differ threefold: there the plain ratio was 3 points low for a reader who
knows the commoner half); overall, t = sum(W_h t_h). Skipped words are left out; a band with no answers leaves out its
weight. Variance: sum(W_h^2 v_h), with v_h = (1 - n_h/N_h) * (sum(w^2) / sum(w)^2) * p_h (1 - p_h), p_h = t_h with
half a word known and half not added (so a band answered all yes still has some variance). The design effect enters
there: sum(w^2)/sum(w)^2 is 1/n_h times Kish's design effect for unequal weights (about 1.1 within a band), the first
factor is the finite population correction, and stratification takes out the differences between bands. deff is the
variance over p(1 - p)/n, a simple random sample's (p with half a word known and half not added, as in the bands); the
90% interval is Wilson's score interval at the effective sample size n/deff (Korn and Graubard 1998).
tools/probe_sim.py checks the bias, standard error and coverage on simulated readers.

Made-up words in a probe of old. The share claimed known, f, is the over-claiming rate. The corrected estimate is the
correction for guessing, (t - f) / (1 - f) (Anderson and Freebody 1983; Huibregtse, Admiraal and Meara 2002): if you
say yes to a word you don't know with the chance you say yes to a made-up one, t = k + (1 - k) f, so k = (t - f) /
(1 - f), on the same scale as the goal (detection measures such as d' measure telling words apart, not a share). f
pools the made-up words of the probes of the last FAKE_DAYS days. The interval adds f's uncertainty (binomial, half a
claim added) by the delta method; its top is never above the estimate's own.

Lookup habit (row R). For the words a probe of old answered not known, the share of their read showings in the gloss
log that were looked up: their opens over their read chances, from the showings (knowledge.showings,
knowledge.observations, with knowledge.reader's read chance) in the HABIT_DAYS before the probe. It is computed where
the log lives (the collector, or this machine without one) when the probe op is applied, with a 90% interval that
treats words as clusters.

Records. A probe of old is a `probe` op (hub.py): its words with their entry id, band, weight and answer, its made-up
words and answers, the bands, the estimate, how it was taken and how long it took, kept in state["probes"] (the newest
KEEP). The review stream's cards are rows of the cards table (feedback.py), and the rolling estimate is knowledge.json
"review" "probe" (knowledge.review_report), so every machine's cache has it for fw.py status.

The model and the probe. The answers of a probe of old are evidence for the model (knowledge.probe_answers), and so are
the review cards (knowledge.review_answers, with an offset of their own and the same two components, but labelled by
the model's k). But the probe stays the check on the model: its estimate here is computed from its cards alone, never
from the model's k, and anything that sets the two side by side takes the model as it stood before.
"""
import argparse
import functools
import math
from pathlib import Path
import random
import re
import sys
import time

import dictionary
import feedback
import fwlib
import knowledge

TOP = 10_000          # the population: swappable entries ranked within this
WORDS = 60            # real words a probe asks about
FAKES = 6             # made-up words among them
BANDS = 6
SHARE = 4             # a band gives at most 1/SHARE of its words to one probe
REPEAT_DAYS = 90      # a word probed this recently isn't drawn again
FAKE_DAYS = 365       # nor a made-up word
ROLLING_DAYS = 30     # the review stream's estimate reads the cards of this many days (rolling)
Z90 = 1.6449          # 90% intervals, as calibrate's
HABIT_DAYS = 60       # the lookup habit is measured on showings this long before a probe
KEEP = 100            # probes kept in the state
FAKES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'fakes.tsv'
DAY_MS = 86_400_000
NUMERAL = re.compile('[〇一二三四五六七八九十百千万億兆0-9０-９]')
NUMBER_WORD = re.compile(r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|hundreds?|'
                         r'thousands?|first|second|third|fourth|fifth|once|twice|thrice|tens|dozens?|\d+)\b')
RECORD = ('uid', 'at', 'day', 'via', 'seconds', 'weights', 'top', 'words', 'fakes', 'bands', 'estimate')


# ---- the population and the sample ----

def is_number(entry):
    """A number and a counter (二人, 三時, 第三): a numeral in the spelling and a number in the gloss. Words where
    the numeral is part of the meaning (一緒 together, 一応 for now, 十分 enough) stay."""
    return bool(NUMERAL.search(entry['ja'])) and bool(NUMBER_WORD.search(entry['en'].lower()))


# Grammar, not vocabulary: an expression led by a particle or the copula (という, について, だった, ではない). It
# is part of a sentence's frame, so "would you read it without a lookup" means little out of context. A connector
# that is also a conjunction (だから) or an expression that is also a noun or adverb (それから, 何も) stays.
GRAMMAR_POS = ({'exp'}, {'exp', 'adj-i'})
GRAMMAR_LEAD = ('に', 'と', 'の', 'で', 'だ', 'じゃ', 'よ', 'か', 'もの')


def is_grammar(entry):
    return set(entry['pos']) in GRAMMAR_POS and entry['ja'].startswith(GRAMMAR_LEAD)


def population(d=None, counts=None, top=TOP):
    """The items a probe draws from, heaviest first: [{ja, ids, rank, weight}], one per spelling. Weights are
    1/score among the swappable entries ranked within top, or with counts ({entry id: times in your reading}) the
    counts of every swappable entry counted."""
    d = d or dictionary.load()
    items = {}
    for e in d:
        if not e['swap'] or is_number(e) or is_grammar(e):
            continue
        if counts is None:
            if e['rank'] > top or not e['score']:
                continue
            weight = 1 / e['score']
        else:
            weight = counts.get(e['id'], 0)
            if weight <= 0:
                continue
        item = items.setdefault(e['ja'], {'ja': e['ja'], 'ids': [], 'rank': e['rank'], 'weight': 0.0})
        item['ids'].append(e['id'])
        item['weight'] += weight
    return sorted(items.values(), key=lambda it: (-it['weight'], it['rank']))


def cut(items, n=BANDS):
    """items (heaviest first) in n bands of equal weight, each item in the band its weight's middle falls in."""
    total = sum(it['weight'] for it in items)
    bands, before = [[] for _ in range(n)], 0.0
    for it in items:
        bands[min(n - 1, int(n * (before + it['weight'] / 2) / total))].append(it)
        before += it['weight']
    return [b for b in bands if b]


def allocate(sizes, shares, n=WORDS, most=SHARE):
    """Words per band: in proportion to its share of the weight, at most 1/most of its words (at least one), the rest
    spread over the other bands in proportion; rounded by largest remainders."""
    caps = [min(size, max(1, size // most)) for size in sizes]
    fixed = {}
    while True:
        free = [h for h in range(len(sizes)) if h not in fixed]
        total = sum(shares[h] for h in free)
        rest = n - sum(fixed.values())
        ideal = {h: rest * shares[h] / total if total > 0 else 0.0 for h in free}
        over = [h for h in free if ideal[h] > caps[h]]
        if not over:
            break
        fixed.update((h, caps[h]) for h in over)
    exact = {**ideal, **fixed}
    counts = {h: min(caps[h], int(x)) for h, x in exact.items()}
    for h in sorted(free, key=lambda h: counts[h] - exact[h]):
        if sum(counts.values()) >= n:
            break
        if counts[h] < caps[h]:
            counts[h] += 1
    return [counts[h] for h in range(len(sizes))]


@functools.lru_cache(maxsize=None)
def read_fakes(path=FAKES_PATH):
    """[(made-up word, its invented reading)] from data/fakes.tsv."""
    lines = [line for line in Path(path).read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')]
    return tuple(tuple(line.split('\t')[:2]) for line in lines[1:])


def last_asked(probes):
    """({word: the last day it was probed}, {made-up word: the last day it was used}) from state["probes"]."""
    words, fakes = {}, {}
    for p in probes or []:
        for found, rows in ((words, p.get('words') or []), (fakes, p.get('fakes') or [])):
            for row in rows:
                if row and isinstance(row[0], str):
                    found[row[0]] = max(found.get(row[0], ''), p.get('day') or '')
    return words, fakes


def recent(day, today, days):
    return bool(day) and fwlib.days_between(day, today) < days


def pick(pool, n, last, today, days, rng, key=lambda x: x):
    """n of pool at random among those not used in the last `days` days; when too few are left, the least recently
    used fill in."""
    fresh = [x for x in pool if not recent(last.get(key(x)), today, days)]
    if len(fresh) >= n:
        return rng.sample(fresh, n)
    stale = sorted((x for x in pool if recent(last.get(key(x)), today, days)), key=lambda x: last[key(x)])
    return fresh + stale[:n - len(fresh)]


def listed_share(state, items, d=None):
    """The share of the items' weight that the word list marks known: what the labels say, to set beside the probe."""
    d = d or dictionary.load()
    known = set()
    for w in (state or {}).get('words') or []:
        if w.get('status') == 'known':
            e = d.find(w['ja'], w.get('kana') or '', w.get('en') or '')
            if e is not None:
                known.add(e['ja'])
    total = sum(it['weight'] for it in items)
    return sum(it['weight'] for it in items if it['ja'] in known) / total if total else None


class Frame:
    """What a probe draws from: the population (population()) in its bands, with each band's share of the weight."""

    def __init__(self, d=None, counts=None):
        self.items = population(d, counts)
        self.bands = cut(self.items)
        total = sum(it['weight'] for it in self.items)
        self.shares = [sum(it['weight'] for it in band) / total for band in self.bands]
        self.weights, self.top = ('frequency', TOP) if counts is None else ('reading', 0)


@functools.lru_cache(maxsize=None)
def frequency_frame():
    """The Frame by general frequency, built once (review.py draws its probe cards from it, rolling weighs them)."""
    return Frame()


def sample(state=None, d=None, counts=None, rng=None, today=None, frame=None, words=WORDS, fakes=FAKES):
    """A probe to ask: {at, day, weights, top, bands: [[share, size]], listed, questions: [{ja, id, band, weight} or
    {ja, kana, fake: true}]} in the order to ask them. frame: a Frame to reuse (else one from d and counts)."""
    d, rng, today = d or dictionary.load(), rng or random.Random(), today or fwlib.today()
    frame = frame or Frame(d, counts)
    last_word, last_fake = last_asked((state or {}).get('probes'))
    questions = []
    for h, (band, n) in enumerate(zip(frame.bands, allocate([len(b) for b in frame.bands], frame.shares, words))):
        for it in pick(band, n, last_word, today, REPEAT_DAYS, rng, key=lambda it: it['ja']):
            questions.append({'ja': it['ja'], 'id': it['ids'][0], 'band': h, 'weight': float(f'{it["weight"]:.6g}')})
    for ja, kana in pick(read_fakes(), fakes, last_fake, today, FAKE_DAYS, rng, key=lambda f: f[0]):
        questions.append({'ja': ja, 'kana': kana, 'fake': True})
    rng.shuffle(questions)
    return {'at': int(time.time() * 1000), 'day': today, 'weights': frame.weights, 'top': frame.top,
            'bands': [[round(s, 6), len(b)] for s, b in zip(frame.shares, frame.bands)],
            'listed': listed_share(state, frame.items, d) if state else None, 'questions': questions}


# ---- the estimate ----

def wilson(p, n, z=Z90):
    """Wilson's score interval for a share p from n observations (n may be an effective sample size)."""
    if n <= 0:
        return 0.0, 1.0
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def interval(p, variance, n, z=Z90):
    """Wilson's interval at the effective sample size p (1 - p) / variance (n when that is undefined)."""
    effective = p * (1 - p) / variance if variance > 0 and 0 < p < 1 else n
    return wilson(p, effective, z)


def estimate(words, bands, z=Z90):
    """The goal from a probe's answers. words: [(band, weight, answer)], answer 1 known, 0 not, None skipped; bands:
    [(share of the weight, words in it)]. {known, se, low, high, deff, n} as the module docstring says, or None
    without an answer."""
    known = variance = missing = 0.0
    n = 0
    for h, (share, size) in enumerate(bands):
        mine = [(w, a) for b, w, a in words if b == h and a is not None]
        if not mine:
            missing += share
            continue
        k, sw, sw2 = len(mine), sum(w for w, _ in mine), sum(w * w for w, _ in mine)
        x, y = sw / k, sum(w * a for w, a in mine) / k
        t = y / x
        if k > 1 and y > 0:  # Tin's correction: the ratio's bias from order 1/n to 1/n^2
            sxy = sum((w - x) * (w * a - y) for w, a in mine) / (k - 1)
            sxx = sum((w - x) ** 2 for w, _ in mine) / (k - 1)
            t = min(1.0, max(0.0, t * (1 + max(0.0, 1 - k / size) / k * (sxy / (x * y) - sxx / (x * x)))))
        p = (t * k + 0.5) / (k + 1)
        known += share * t
        variance += share * share * max(0.0, 1 - k / size) * sw2 / (sw * sw) * p * (1 - p)
        n += k
    if not n or missing >= 1:
        return None
    known, variance = known / (1 - missing), variance / (1 - missing) ** 2
    smooth = (known * n + 0.5) / (n + 1)
    simple = smooth * (1 - smooth) / n
    low, high = interval(known, variance, n, z)
    return {'known': round(known, 4), 'se': round(math.sqrt(variance), 4), 'low': round(low, 4),
            'high': round(high, 4), 'deff': round(variance / simple, 3) if simple > 0 else None, 'n': n}


def corrected(found, claimed, shown, z=Z90):
    """The estimate corrected for guessing, (t - f) / (1 - f) with f = claimed / shown made-up words, and its interval
    (the delta method, f's variance from claimed + 1/2 of shown + 1; never above the estimate's own, since the share
    known is at most the share claimed); None without made-up words answered or when every one was claimed."""
    if not found or not shown or claimed >= shown:
        return None
    t, f = found['known'], claimed / shown
    k = min(1.0, max(0.0, (t - f) / (1 - f)))
    smooth = (claimed + 0.5) / (shown + 1)
    variance = found['se'] ** 2 / (1 - f) ** 2 + smooth * (1 - smooth) / shown * (1 - t) ** 2 / (1 - f) ** 4
    low, high = interval(k, variance, found['n'], z)
    return {'known': round(k, 4), 'low': round(min(low, k), 4), 'high': round(max(k, min(high, found['high'])), 4)}


def rolling(reviews, now_ms, frame=None, days=ROLLING_DAYS, z=Z90):
    """The goal from the review stream's cards of the `days` days before now_ms (feedback.reviews), without the model,
    as the module docstring says: {known, se, low, high, deff, n (probe cards), fakes, learning (cards that shaped the
    components), naive, overclaim, bands: [[share, size, cards, pi]], days, separation, components}, or None without a
    probe card. frame: the Frame the cards' bands are of (default: frequency_frame())."""
    import knowledge
    frame = frame or frequency_frame()
    bands = [(share, len(b)) for share, b in zip(frame.shares, frame.bands)]
    since, latest, fakes, learning = now_ms - days * DAY_MS, {}, [], []
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    for r in sorted((r for r in reviews or () if isinstance(r, dict) and number(r.get('at'))), key=lambda r: r['at']):
        if not since <= r['at'] < now_ms or not number(r.get('shown')) or r.get('reveal') is not None \
                and not number(r['reveal']):
            continue
        x = knowledge.review_outcome(r.get('reveal'), r['shown'])
        if x is False:
            continue
        if r.get('kind') == 'fake':
            fakes.append(x)
        elif r.get('kind') == 'learning':
            learning.append(x)
        elif r.get('kind') == 'probe' and isinstance(r.get('band'), int) and 0 <= r['band'] < len(bands) \
                and number(r.get('weight')) and r['weight'] > 0:
            latest[r.get('word')] = (r['band'], r['weight'], x)  # the word's latest card
    cards = list(latest.values())
    if not cards:
        return None
    parts = knowledge.Components()
    pi, rest = [0.5] * len(bands), 0.5
    clamp = lambda p: min(1 - 1e-9, max(1e-9, p))
    for _ in range(2000):  # EM: the components, every band's mixture weight and the learning cards'
        found = [(h, w, x, knowledge.sigmoid(knowledge.logit(clamp(pi[h])) + _ratio(parts, x))) for h, w, x in cards]
        mine = [(x, knowledge.sigmoid(knowledge.logit(clamp(rest)) + _ratio(parts, x))) for x in learning]
        moved = parts.update([(x, 1.0, r) for _h, _w, x, r in found] + [(x, 1.0, r) for x, r in mine],
                             [(x, 1.0) for x in fakes])
        for h in range(len(bands)):
            sw = sum(w for b, w, _x, _r in found if b == h)
            if sw:
                new = sum(w * r for b, w, _x, r in found if b == h) / sw
                moved, pi[h] = max(moved, abs(new - pi[h])), new
        if mine:
            new = sum(r for _x, r in mine) / len(mine)
            moved, rest = max(moved, abs(new - rest)), new
        if moved < 1e-7:
            break
    known = variance = missing = naive = 0.0
    n, rows = 0, []
    for h, (share, size) in enumerate(bands):
        mine = [(w, x) for b, w, x in cards if b == h]
        if not mine:
            missing += share
            rows.append([round(share, 6), size, 0, None])
            continue
        k, sw, sw2 = len(mine), sum(w for w, _x in mine), sum(w * w for w, _x in mine)
        t = pi[h]
        p = (t * k + 0.5) / (k + 1)
        ratios = [math.exp(max(-30.0, min(30.0, _ratio(parts, x)))) for _w, x in mine]
        information = sum(((r - 1) / (p * r + 1 - p)) ** 2 for r in ratios)
        effective = min(float(k), max(1e-6, p * (1 - p) * information))
        known += share * t
        naive += share * sum(w * knowledge.sigmoid(_ratio(parts, x)) for w, x in mine) / sw
        variance += share * share * sw2 / (sw * sw) * p * (1 - p) * (max(0.0, 1 - k / size) + k / effective - 1)
        n += k
        rows.append([round(share, 6), size, k, round(t, 4)])
    known, naive, variance = known / (1 - missing), naive / (1 - missing), variance / (1 - missing) ** 2
    looks = [knowledge.sigmoid(_ratio(parts, x)) for x in fakes]
    f = sum(looks) / len(looks) if looks else _even_odds_f(parts)
    variance += _components_variance(parts, cards, pi, learning, rest, fakes,
                                     {h: share / (1 - missing) for h, (share, _size) in enumerate(bands)})
    plain = (known * n + 0.5) / (n + 1)
    low, high = interval(known, variance, n, z)
    return {'known': round(known, 4), 'se': round(math.sqrt(variance), 4), 'low': round(low, 4),
            'high': round(high, 4), 'deff': round(variance / (plain * (1 - plain) / n), 3), 'n': n,
            'fakes': len(fakes), 'learning': len(learning), 'naive': round(naive, 4), 'overclaim': round(f, 4),
            'bands': rows, 'days': days, 'separation': parts.separation(), 'components': parts.summary()}


def _components_variance(parts, cards, pi, learning, rest, fakes, shares):
    """What the components' own uncertainty adds to the variance of sum(shares_h pi_h): the delta method on the
    information of every parameter at once (each band's logit pi, the learning cards', and each component's mu, log s
    and logit a), estimated by the outer product of each card's score (BHHH) plus the priors' curvature, less what the
    bands' information alone gives (the per-band variance has that already). The made-up words' cards inform the "not
    known" component only. 0 when the components are well pinned down."""
    import knowledge
    used = sorted({h for h, _w, _x in cards})
    index = {h: j for j, h in enumerate(used)}
    m = len(used) + (1 if learning else 0)
    size = m + 6
    info = [[0.0] * size for _ in range(size)]
    clamp = lambda p: min(1 - 1e-3, max(1e-3, p))

    def scores(x, c):  # d log density / d (mu, log s, logit a) of component c
        if x is None:
            return [0.0, 0.0, 1 - parts.a[c]]
        u = (x - parts.mu[c]) / parts.s[c]
        return [u / parts.s[c], u * u - 1, -parts.a[c]]

    def add(score, v):
        for a, sa in enumerate(score):
            if sa:
                row = info[a]
                for b, sb in enumerate(score):
                    row[b] += v * sa * sb

    mean = {h: sum(w for b, w, _x in cards if b == h) / sum(1 for b, _w, _x in cards if b == h) for h in used}
    for j, group in ((None, [(h, w / mean[h], x) for h, w, x in cards]), (m - 1, [(None, 1.0, x) for x in learning])):
        for h, v, x in group:
            p = clamp(pi[h] if h is not None else rest)
            r = knowledge.sigmoid(knowledge.logit(p) + _ratio(parts, x))
            score = [0.0] * size
            score[index[h] if h is not None else j] = r - p
            score[m:m + 3] = [r * a for a in scores(x, 0)]
            score[m + 3:] = [(1 - r) * a for a in scores(x, 1)]
            add(score, v)
    for x in fakes:
        add([0.0] * (m + 3) + scores(x, 1), 1.0)
    n0 = knowledge.REVIEW_CARDS
    for c, name in enumerate(knowledge.Components.NAMES):
        mu0, s0, _a0 = knowledge.REVIEW_PRIOR[name]
        s2, a = parts.s[c] ** 2, parts.a[c]
        for j, curve in enumerate((n0 / s2, 2 * n0 * ((parts.mu[c] - mu0) ** 2 + s0 * s0) / s2, n0 * a * (1 - a))):
            info[m + 3 * c + j][m + 3 * c + j] += curve
    for j in range(size):
        info[j][j] += 1e-9
    g = [0.0] * size
    for h in used:
        p = clamp(pi[h])
        g[index[h]] = shares[h] * p * (1 - p)
    full = sum(a * b for a, b in zip(g, knowledge._solve(info, g)))
    alone = sum(g[j] ** 2 / info[j][j] for j in range(len(used)))
    return max(0.0, full - alone)


def _ratio(parts, x):
    """log A - log B: how much more likely an outcome is from a word known at recall than from one not."""
    la, lb = parts.logs(x)
    return la - lb


def _even_odds_f(parts, steps=200):
    """Without made-up cards: the chance a word not known looks known at even odds, by the components alone (no reveal,
    then the reveal times on a grid)."""
    import knowledge
    a_u = parts.a[1]
    total = a_u * knowledge.sigmoid(_ratio(parts, None))
    low, high = parts.mu[1] - 6 * parts.s[1], parts.mu[1] + 6 * parts.s[1]
    dx = (high - low) / steps
    for j in range(steps + 1):
        x = low + j * dx
        density = math.exp(parts.logs(x)[1])  # (1 - a_U) times the not known component's density
        total += (0.5 if j in (0, steps) else 1.0) * dx * density * knowledge.sigmoid(_ratio(parts, x))
    return total


def claims(probes, today, days=FAKE_DAYS):
    """(made-up words claimed, made-up words answered) in the probes of the last `days` days."""
    mine = [a for p in probes or [] if p.get('day') and fwlib.days_between(p['day'], today) < days
            for _ja, a in p.get('fakes') or [] if a is not None]
    return sum(mine), len(mine)


def record(probe, answers, via, seconds, earlier=(0, 0)):
    """The probe op for a probe (sample) and its answers (1 known, 0 not, None skipped, in question order). earlier:
    (made-up words claimed, answered) in the probes of the last year (claims), pooled with this one's for the
    correction: over-claiming is a habit of the reader, and 6 made-up words alone pin it down poorly."""
    words, fakes = [], []
    for q, a in zip(probe['questions'], answers):
        if q.get('fake'):
            fakes.append([q['ja'], a])
        else:
            words.append([q['ja'], q['id'], q['band'], q['weight'], a])
    found = estimate([(b, w, a) for _ja, _id, b, w, a in words], probe['bands'])
    answered = [a for _ja, a in fakes if a is not None]
    claimed, shown = sum(answered) + earlier[0], len(answered) + earlier[1]
    out = dict(found or {}, claimed=sum(answered), fakes=len(answered), claimed_year=claimed, fakes_year=shown,
               listed=probe.get('listed'))
    fixed = corrected(found, claimed, shown)
    if fixed:
        out.update(corrected=fixed['known'], corrected_low=fixed['low'], corrected_high=fixed['high'])
    if out.get('listed') is not None:
        out['listed'] = round(out['listed'], 4)
    return {'kind': 'probe', 'via': via, 'seconds': max(0, int(seconds)), 'weights': probe['weights'],
            'top': probe['top'], 'words': words, 'fakes': fakes, 'bands': probe['bands'], 'estimate': out}


# ---- the op, where the state lives (hub.py) ----

def keep(state, op):
    """Applies a probe op to the state: kept in state["probes"] once (by uid), the newest KEEP."""
    probes = state.setdefault('probes', [])
    if any(p.get('uid') == op['uid'] for p in probes):
        return {'kept': False}
    probes.append({k: op[k] for k in RECORD if k in op})
    del probes[:-KEEP]
    return {'kept': True}


def attach(state, uid, habit):
    """Adds a probe's lookup habit (habit_here) to its record in the state; returns its summary."""
    for p in state.get('probes') or []:
        if p.get('uid') == uid:
            p['habit'] = habit
    return habit_summary([habit])


def lookup_habit(rows, contexts, unknown, at_ms, days=HABIT_DAYS, d=None):
    """{words: [[word, looked up, read, showings]], for each word in unknown shown in the `days` days before at_ms}:
    looked up sums the opens (a glance counts knowledge.GLANCE_Y), read the read chances (1 when opened), as
    knowledge.observations weighs them, over the browser passages and T3 messages shown (knowledge.showings)."""
    d, unknown, since = d or dictionary.load(), set(unknown), at_ms - days * DAY_MS
    if not unknown:
        return {'words': []}

    def match(written):
        e = d.find(written)
        return e['ja'] if e is not None and e['ja'] in unknown else None

    per = {}
    for last, (surface, _view, _context), context, items in knowledge.showings(rows, contexts,
                                                                                at_ms + knowledge.SETTLE_MS):
        if last < since:
            continue
        for ja, y, weight in knowledge.observations(surface, context, items, match, knowledge.reader(surface, context)):
            entry = per.setdefault(ja, [0.0, 0.0, 0])
            entry[0] += y
            entry[1] += weight
            entry[2] += 1
    return {'words': [[ja, round(y, 3), round(r, 3), n] for ja, (y, r, n) in sorted(per.items())]}


def habit_here(op):
    """lookup_habit for a probe op's words answered not known, from the gloss log of this state dir; None without a
    log, or when it can't be read (the probe is kept either way)."""
    path = feedback.default_path()
    if not path.exists():
        return None
    try:
        rows, contexts = feedback.load(path)
        return lookup_habit(rows, contexts, [w[0] for w in op['words'] if w[4] == 0], op['at'])
    except Exception as error:  # the log or the knowledge helpers changed: no habit, but the probe stands
        print(f'probe: no lookup habit: {error.__class__.__name__}: {error}', file=sys.stderr)
        return None


def habit_summary(habits, z=Z90):
    """The lookup habit c over one or more probes' habits: {c, low, high, showings, words, read} or None when none
    of their words was read. The interval treats words as clusters (a word's showings go together)."""
    per = [w for h in habits if h for w in h.get('words') or []]
    looked, read = sum(w[1] for w in per), sum(w[2] for w in per)
    if read <= 0:
        return None
    c = looked / read
    m = len(per)
    variance = m / (m - 1) * sum((w[1] - c * w[2]) ** 2 for w in per) / read ** 2 if m > 1 else 0.0
    effective = c * (1 - c) / variance if variance > 0 and 0 < c < 1 else read
    low, high = wilson(c, min(effective, read), z)
    return {'c': round(c, 3), 'low': round(low, 3), 'high': round(high, 3), 'showings': sum(w[3] for w in per),
            'words': m, 'read': round(read, 1)}


# ---- what to print ----

def latest(state):
    probes = (state or {}).get('probes') or []
    return probes[-1] if probes else None


def rolling_text(know):
    """The review stream's estimate (knowledge.json "review", knowledge.review_report) in a line or two, for fw.py
    status and --history."""
    review = (know or {}).get('review') if isinstance((know or {}).get('review'), dict) else None
    est = (review or {}).get('probe') if isinstance((review or {}).get('probe'), dict) else None
    if est is None or est.get('known') is None:
        return (f'probe: no probe cards reviewed in the last {ROLLING_DAYS} days yet (T3 Code\'s review panel and the '
                'phone\'s Review screen show the review cards; about one in ten is a probe word)')
    sep = est.get('separation') or {}
    rests = (f'{est["n"]} probe card{"s" if est["n"] != 1 else ""} and {est.get("fakes", 0)} made-up '
             f'one{"s" if est.get("fakes", 0) != 1 else ""}')
    line = (f'probe (review cards, last {est.get("days", ROLLING_DAYS)} days): you would read {percent(est["known"])} '
            f'of your reading without a lookup (90% interval {span(est["low"], est["high"])}), from {rests}')
    if sep.get('worth') is not None:
        line += (f'; known and not known reveals separate {"weakly" if sep.get("weak") else "well"} (a card is worth '
                 f'{sep["worth"]:.2f} of a yes/no answer; a known word revealed sooner {100 * sep["auc"]:.0f}% of the '
                 'time)')
    return line


def status_line(know):
    """fw.py status: the review stream's rolling estimate and how many cards it rests on."""
    return '  ' + rolling_text(know)


def percent(x):
    return '?' if x is None else f'{100 * x:.0f}%'


def span(low, high):
    return f'{100 * low:.0f}-{100 * high:.0f}%'


def report(op, habit=None, fakes=()):
    """What a probe found, in plain lines. fakes: the made-up words with their invented readings, to reveal."""
    est = op['estimate']
    words, made_up = op['words'], op['fakes']
    answered = [w for w in words if w[4] is not None]
    took = f'{op["seconds"] // 60} min {op["seconds"] % 60} s' if op['seconds'] < 3600 else 'over an hour'
    lines = [f'Probe: {len(words)} words and {len(made_up)} made up, answered in {took}'
             + (f' ({len(words) - len(answered)} skipped)' if len(answered) < len(words) else '') + '.']
    if est.get('known') is None:
        lines.append('  No words answered, so no estimate.')
        return '\n'.join(lines)
    basis = ('by general frequency, among the 10,000 most frequent words, until your reading is counted'
             if op['weights'] == 'frequency' else 'by how often each came up in your reading')
    lines.append(f'  You would read {percent(est["known"])} of the words in your reading without a lookup (90% '
                 f'interval {span(est["low"], est["high"])}), each word weighted {basis}.')
    claimed = [ja for ja, a in made_up if a == 1]
    line = f'  Made-up words you marked known: {est["claimed"]} of {est["fakes"]}'
    line += f' ({", ".join(claimed)})' if claimed else ''
    if est.get('fakes_year', 0) > est['fakes']:
        line += f'; {est["claimed_year"]} of {est["fakes_year"]} in the last year'
    if est.get('corrected') is not None:
        line += (f'. Corrected for guessing: {percent(est["corrected"])} '
                 f'({span(est["corrected_low"], est["corrected_high"])}).')
    lines.append(line)
    by_band = []
    for h in range(len(op['bands'])):
        mine = [w[4] for w in words if w[2] == h and w[4] is not None]
        by_band.append(f'{sum(mine)}/{len(mine)}')
    lines.append(f'  Known by frequency band, most frequent first: {", ".join(by_band)}.')
    if est.get('listed') is not None:
        lines.append(f'  Your word list marks {percent(est["listed"])} of the same weight known.')
    lines.append(habit_text(habit))
    if fakes:
        lines.append('  Made up: ' + ', '.join(f'{ja} ({kana})' for ja, kana in fakes) + '.')
    return '\n'.join(line for line in lines if line)


def habit_text(habit):
    if habit is None:
        return ('  Lookup habit: none of the words you marked not known were shown in the last '
                f'{HABIT_DAYS} days, so nothing to measure yet.')
    rough = ' Few, so it is rough.' if habit['showings'] < 30 or habit['words'] < 5 else ''
    showings, words = habit['showings'], habit['words']
    return (f'  Lookup habit: you looked up {percent(habit["c"])} of the showings you likely read of words you '
            f'marked not known (90% interval {span(habit["low"], habit["high"])}), from {showings} '
            f'showing{"s" if showings != 1 else ""} of {words} word{"s" if words != 1 else ""} in the {HABIT_DAYS} '
            f'days before.{rough}')


def history_text(state, know=None):
    """Every probe of old, then the review stream's rolling estimate (rolling_text)."""
    probes = (state or {}).get('probes') or []
    stream = 'Now: ' + rolling_text(know) + '.'
    if not probes:
        return 'No probes of old (fw.py probe takes one in a terminal).\n' + stream
    lines = ['Probes (90% intervals): the share of your reading you would read without a lookup; made-up words '
             'marked known; that share corrected for guessing; what your word list marks known; the lookup habit.']
    for p in probes:
        est = p.get('estimate') or {}
        if est.get('known') is None:
            lines.append(f'  {p["day"]}  no answers')
            continue
        parts = [f'{p["day"]}  known {percent(est["known"])} ({span(est["low"], est["high"])})',
                 f'made up {est.get("claimed", 0)}/{est.get("fakes", 0)}'
                 + (f' ({est["claimed_year"]}/{est["fakes_year"]} in the year)'
                    if est.get('fakes_year', 0) > est.get('fakes', 0) else '')]
        if est.get('corrected') is not None:
            parts.append(f'corrected {percent(est["corrected"])} ({span(est["corrected_low"], est["corrected_high"])})')
        if est.get('listed') is not None:
            parts.append(f'list {percent(est["listed"])}')
        habit = habit_summary([p.get('habit')])
        parts.append(f'habit {percent(habit["c"])} ({span(habit["low"], habit["high"])}, {habit["showings"]} '
                     'showings)' if habit else 'habit -')
        lines.append('  ' + ', '.join(parts))
    pooled = habit_summary([p.get('habit') for p in probes])
    if pooled and len(probes) > 1:
        lines.append(f'Lookup habit over all probes: {percent(pooled["c"])} ({span(pooled["low"], pooled["high"])}), '
                     f'from {pooled["showings"]} showings of {pooled["words"]} words.')
    lines.append(stream)
    return '\n'.join(lines)


# ---- fw.py probe ----

def finish(probe, answers, via, seconds):
    """Keeps a probe's answers where the state lives (a probe op) and prints what it found."""
    import hub  # here, not at the top: hub imports this module
    op = hub.stamp(record(probe, answers, via, seconds, claims(fwlib.load_state().get('probes'), fwlib.today())))
    result = hub.submit(op)
    if not result.get('ok', True):
        sys.exit(f'The probe was not kept: {result.get("error")}')
    kept = next((p for p in fwlib.load_state().get('probes') or [] if p.get('uid') == op['uid']), {})
    habit = result.get('habit') or habit_summary([kept.get('habit')])
    fakes = [(q['ja'], q['kana']) for q in probe['questions'] if q.get('fake')]
    print(report(op, habit, fakes))
    if result.get('queued'):
        print(f'(Kept here: {fwlib.collector_url()} did not answer, so the next sync sends it.)')
    print('fw.py probe --history lists every probe, and the review stream\'s rolling estimate.')


def keys():
    """Keys pressed, one at a time from a terminal (no Enter needed), else the first letter of each line."""
    if sys.stdin.isatty():
        try:
            import termios
            import tty
        except ImportError:
            termios = None
        if termios is not None:
            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while True:
                    key = sys.stdin.read(1)
                    if not key:
                        return
                    yield key.lower()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            return
    for line in sys.stdin:
        yield line.strip()[:1].lower()


ANSWERS = {'y': 1, 'n': 0, 's': None}
SAID = {1: 'known', 0: 'not known', None: 'skipped'}


def ask(probe, pressed=None, out=None):
    """The questions one at a time; returns the answers, or None when quit."""
    out = out or sys.stdout
    if pressed is None:
        pressed = keys()
        try:
            return ask(probe, pressed, out)
        finally:
            pressed.close()  # the terminal back as it was, before anything else is printed
    questions, answers = probe['questions'], []
    out.write(f'{len(questions)} words, one at a time. Would you read it without a lookup, met in an English '
              'sentence?\n  y yes   n no   s skip (not sure)   b back   q quit (nothing kept)\n')
    while len(answers) < len(questions):
        i = len(answers)
        out.write(f'{i + 1:3d}/{len(questions)}  {questions[i]["ja"]}  ')
        out.flush()
        for key in pressed:
            if key in ANSWERS or key in ('b', 'q'):
                break
        else:
            key = 'q'
        if key == 'q':
            out.write('\nStopped: nothing kept.\n')
            return None
        if key == 'b':
            out.write('(back)\n')
            if answers:
                answers.pop()
            continue
        answers.append(ANSWERS[key])
        out.write(SAID[ANSWERS[key]] + '\n')
    return answers


def add_arguments(p):
    p.add_argument('--history', action='store_true',
                   help='every probe so far, then the review stream\'s rolling estimate')
    p.add_argument('--seed', type=int, help=argparse.SUPPRESS)  # a repeatable sample, for tests and trials


def run(args):
    import hub
    import knowledge
    if args.history:
        state, error = hub.current()
        if error:
            print(f'({fwlib.collector_url()} did not answer: {error}. Showing the copy here from the last sync.)')
        print(history_text(state, knowledge.load()))
        return
    state, _error = hub.current()
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    if not sys.stdin.isatty():
        print('(Not a terminal: one answer a line.)')
    probe = sample(state, rng=rng)
    started = time.time()
    try:
        answers = ask(probe)
    except KeyboardInterrupt:
        sys.exit('\nStopped: nothing kept.')
    if answers is not None:
        finish(probe, answers, 'terminal', time.time() - started)
