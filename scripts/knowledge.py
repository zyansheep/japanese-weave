"""Adaptive difficulty: per-word knowledge estimated from the gloss interaction log (see docs/adaptive-difficulty.md).
Python 3.9+, standard library only.

Model: P(lookup | read) = sigmoid(offset_s + form - k_w). k_w is the reader's knowledge of a word on a log-odds scale;
offset_s is 0 on the web (the reference) and fitted for chat; form is a fitted offset for a conjugated form of the word.
Every browser passage shown (its "shown" record) and every T3 message with a word count counts, opened or not. A word
left unopened is evidence the reader knew it in proportion to the chance it was read: certain down to the lowest opened
line (halving per line below it), else from time on screen against the time reading down to the word takes
(read_chance: the passage's stays in the browser; stay_chance: in T3, the stays of the paragraph the word is in). A T3
message without a word count (logged before T3 sent them, and not backfilled) counts as an episode, which exists only
when something was opened: its likelihood is conditioned on at least one open, and only the line rule applies. Every
lexicon word is scored, words not introduced yet included, and a glossed word is matched to its entry through its
dictionary form (inflection.py: T3 glosses conjugated verbs whole).

Fit: one penalized maximum-likelihood fit of the whole log on every refresh (NEXT_STEPS step 4), replacing the online
updates and their pull toward the prior. Each word's k is held toward its prior mean by a Gaussian prior whose width is
fitted per kind (an Anki card or none); the prior mean is a regression on the word's features (an intercept by origin
and card, the uncapped ln(1 + Anki interval), and for a word not marked known its dictionary entry's frequency rank,
kanji known at a glance, script, part of speech, length, rarity and source language: word_features, NEXT_STEPS step
13), so any dictionary entry has a starting value (prior_estimate; extension/src/prior.js); evidence weighs half as
much every EVIDENCE_DAYS; known and learn commands are one-sided pseudo-observations; a lookup of a conjugated form
counts FORM_WEIGHT. Each word's uncertainty is the fit's curvature (Laplace). The reading model (pace and cap) is
chosen once a day by profile likelihood over a grid, on both surfaces. The fit is the Fit class, which evaluate.py's
"fit" model (evaluate.CURRENT) runs too; the online updates it replaced are the Online class, kept as evaluate.py's
"today" for comparison.

refresh() fits the whole log, writes knowledge.json (per word: k, its sd, and whether it is free: known at a glance,
with hysteresis), fits the price of a whole sentence per surface, and reports whether the default amount of Japanese
looks right and how the model's expected lookups per class compare with those seen, in lookups per 100 words read
(calibration), and which opened words the lexicon doesn't have (unlisted). Then it labels every word from the model
(known when free, learning once in use, else new), takes in the day's new words while the lookup budget goes unspent,
each with its delayed-start arm, and records the probes' outcomes (decide).
"""
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys
import time

import feedback
import fwlib
import inflection
import intensity

KNOWLEDGE = 'knowledge.json'
COMMANDS = 'commands.jsonl'
LOCK = 'knowledge.lock'
STALE_S = 15 * 60          # the native host starts a background refresh when knowledge.json is older than this

DAY_MS = 86_400_000
GAP_MS = 10 * 60_000       # an episode ends after this long without interactions in its context
SETTLE_MS = 10 * 60_000    # and is scored once its last interaction is this old
GLANCE_MS = 600            # a peek open shorter than this is a glance: soft evidence
GLANCE_Y = 0.6
T3_NOISE_MS = 300          # T3 opens popovers after 0.3 s, so shorter peeks are the mouse passing over a word
EVIDENCE_DAYS = 30         # evidence weight decays with this half-life, so unseen words grow uncertain again
# The online updates the fit replaced (Online, evaluate.py's "today"): k decays toward its prior, downward only, with
# FORGET_DAYS' half-life; step sizes; the T3 offset's rate and clamp; what a command sets k to.
FORGET_DAYS = 45
ETA_MAX, ETA_MIN, ETA_SLOPE = 1.2, 0.2, 0.3
T3_RATE, T3_LIMIT = 0.02, 1.5
KNOWN_COMMAND_K, LEARN_COMMAND_K = 2.5, 0.0
TARGET_P = 0.25            # the predicted peek chance learning words are aimed at
RECENT_DAYS = 14
CHALLENGES = (0, 0.5, 1, 1.5, 2, 3)  # normal's lookup budget per 100 words (0: intensity.BUDGETS[0])
PUNCTUATION = set('。？！?!.')
PACE_MS = 300              # ms per word a reader takes on a passage with Japanese in it (about 200 words a minute)
READ_MAX = 0.7             # the most a passage's time on screen alone says about a word in it being read
SLACK_PX = 10              # how far above a T3 paragraph's band a word's top may sit and still be in it (stay_chance)
READ_STEPS = 20            # words_read averages the read chance over this many depths of a passage or paragraph
PACES = (150, 200, 300, 450, 650)    # the reading model's grid, chosen by profile likelihood (tune)
READ_MAXES = (0.5, 0.6, 0.7, 0.8, 0.9)
TUNE_MIN_OPENS = 60        # opens, on both surfaces, before the tuned reading model replaces the defaults
TUNE_MARGIN = 3.0          # and how much more log likelihood than the defaults it needs (about a 95% test of two)
READ_FLOOR = 0.05          # the least read chance an open's prediction uses: an open far down still happens
CALIBRATION_DAYS = 30
CALIBRATED = (0.83, 1.2)   # seen over expected lookups inside this, at 90%, is calibrated (PLAN.md section 10)
MIN_DAYS = 5               # days before calibrate gives an interval: with 3, one from a model off by 20% passes
                           # 10-13% of the time (tools/sim/run.py calibration-days); with 5, 3-6%
BOOT_DRAWS = 1000          # resamples of days for an interval
BURST_MS = 60_000          # intensity presses this close together are one decision (calibrate)
SHARE_BINS = (0.0, 0.03, 0.06, 0.1, 0.2, 1.01)  # share of a passage's words in Japanese, after a bin for none
THIN_MIN_PASSAGES = 20     # thinned and untouched passages each before the thinning's effect is measured
THIN_MIN_GAP = 0.005       # and how much less Japanese the thinned ones must have on average
MIN_PASSAGE_WORDS = 15
MIN_ACTIVE_MS = 1000
UNLISTED_DAYS = 30         # opened words with no lexicon entry, counted over this many days
UNLISTED_TOP = 30          # and kept in knowledge.json, most opened first


def sigmoid(x):
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, x))))


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def learning_value(p):
    """How useful showing a word is now: highest when its peek chance is near TARGET_P."""
    return -abs(logit(p) - logit(TARGET_P))


def prior(interval):
    """Starting knowledge from outside the model: an Anki interval in days, else a neutral 1.0."""
    return min(3.0, 0.5 + 0.8 * math.log1p(interval)) if interval is not None else 1.0


# ---- inputs ----

def anki_intervals():
    path = fwlib.home() / fwlib.ANKI_LEXICON
    if not path.exists():
        return {}
    import csv
    with open(path, encoding='utf-8', newline='') as handle:
        return {row['ja']: float(row.get('interval') or 0) for row in csv.DictReader(handle) if row.get('ja')}


def log_command(words, kind, now=None):
    """Known/learn commands, timestamped, so a replay can apply them at the right moment."""
    at = int((now if now is not None else time.time()) * 1000)
    fwlib.home().mkdir(parents=True, exist_ok=True)
    with open(fwlib.home() / COMMANDS, 'a', encoding='utf-8') as handle:
        for word in words:
            handle.write(json.dumps({'at': at, 'word': word, 'kind': kind}, ensure_ascii=False) + '\n')


def read_commands():
    path = fwlib.home() / COMMANDS
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get('kind') in ('known', 'learn') and isinstance(entry.get('word'), str):
            out.append(entry)
    return out


# ---- episodes and observations ----

def is_showing(context):
    """A browser passage with a shown record, or a T3 message with a word count (from its shown record, or backfilled
    from its transcript): replayed whole, opened or not (showings)."""
    if context['surface'] == 't3':
        return context.get('words') is not None
    return context['surface'] == 'browser' and context.get('shown_at') is not None


def episodes(rows, contexts, now_ms):
    """Interactions grouped by (surface, view, context) and split at gaps over GAP_MS; only settled ones. Showings
    (is_showing) are left to showings()."""
    groups = {}
    for row in rows:
        groups.setdefault((row['surface'], row['view'], row['context']), []).append(row)
    out = []
    for key, items in groups.items():
        context = contexts.get(key)
        if context is None or is_showing(context):
            continue
        current = []
        for row in sorted(items, key=lambda r: (r['at'], r['id'])):
            if current and row['at'] - current[-1]['at'] > GAP_MS:
                out.append((key, context, current))
                current = []
            current.append(row)
        out.append((key, context, current))
    return sorted((e for e in out if e[2][-1]['at'] <= now_ms - SETTLE_MS), key=lambda e: e[2][-1]['at'])


def showings(rows, contexts, now_ms):
    """Every browser passage and T3 message shown (is_showing), with its interactions (maybe none), once settled:
    SETTLE_MS after the last thing that happened in it (its shown record, its last stay, its last interaction; a T3
    message with a backfilled word count has no shown record, and one with none of these is skipped).
    [(at, key, context, items)] oldest first."""
    groups = {}
    for row in rows:
        groups.setdefault((row['surface'], row['view'], row['context']), []).append(row)
    out = []
    for key, context in contexts.items():
        if not is_showing(context):
            continue
        items = sorted(groups.get(key, []), key=lambda r: (r['at'], r['id']))
        last = max([context.get('shown_at') or 0, context.get('seen_until') or 0] + [r['at'] for r in items])
        if last and last <= now_ms - SETTLE_MS:
            out.append((last, key, context, items))
    return sorted(out, key=lambda e: e[0])


def _chance(ms, words, share, pace, top):
    """The chance of having read down to `share` of the way through `words` words in `ms` of possible reading, at
    `pace` ms a word, at most `top`."""
    if words <= 0 or ms <= 0:
        return 0.0
    return top * (1 - math.exp(-ms / (pace * words * min(1.0, max(0.1, share)))))


def passage_reading(context, y, line):
    """What read_chance weighs for a word at height y of a browser passage: (ms, words, share), the passage's time on
    screen while a reader could have been reading it, its words, and how far down the word's line ends."""
    height = context.get('height') or 0
    return context.get('active') or 0, context.get('words') or 0, (y + line) / height if height > 0 else 1.0


def read_chance(context, y, line, pace=PACE_MS, top=READ_MAX):
    """The chance a word at height y of a passage was read, from the passage's time on screen alone: the time reading
    down to that line takes at `pace` against the time a reader could have been reading the passage."""
    return _chance(*passage_reading(context, y, line), pace, top)


def paragraph_reading(context, y, line):
    """What stay_chance weighs for a word at height y of a T3 message: (ms, words, share) of the paragraph it is in, or
    None when no stay holds it."""
    stays = context.get('stays') or []
    slack = min(line / 2, SLACK_PX)
    mine = [s for s in stays if s['height'] > 0 and s['y'] - slack <= y < s['y'] + s['height']]
    if not mine:
        return None
    band = max(mine, key=lambda s: s['ms'])  # one paragraph; its bands differ only if its layout changed
    counted = [s['words'] for s in mine if s.get('words')]
    if counted:
        words = max(counted)
    else:
        height = context.get('height') or sum(h for _y, h in {(s['y'], s['height']) for s in stays})
        total = context.get('words') or 0
        words = max(1.0, total * band['height'] / height) if total and height > 0 else 0
    return sum(s['ms'] for s in mine), words, (y - band['y'] + line) / band['height']


def stay_chance(context, y, line, pace=PACE_MS, top=READ_MAX):
    """The chance a word at height y of a T3 message was read, from the stays on screen of the paragraph it is in (the
    stays whose band holds the word's top, from half a line above the band, at most SLACK_PX, down to its bottom; their
    ms added), as read_chance does for a passage. The paragraph's words are the stays' count when sent, else the
    message's words per pixel of its height (of the distinct bands on record when the height is missing) times the
    band's height, at least 1: the message's words include paragraphs without Japanese, which have no stays. No stay
    holds the word: 0."""
    found = paragraph_reading(context, y, line)
    return _chance(*found, pace, top) if found else 0.0


def reading(surface, context, y, line):
    """(ms, words, share) for a word at height y of a passage or message, or None: what its read chance comes from."""
    return paragraph_reading(context, y, line) if surface == 't3' else passage_reading(context, y, line)


def reader(surface, context, pace=PACE_MS, top=READ_MAX):
    """The read chance of a word of this passage or message from its height and the line height (read_chance or
    stay_chance)."""
    chance = stay_chance if surface == 't3' else read_chance
    return lambda y, line: chance(context, y, line, pace, top)


def _read_share(ms, words, pace, top):
    """The mean read chance of the words of a passage or paragraph, over READ_STEPS evenly spaced depths."""
    return sum(_chance(ms, words, (i + 0.5) / READ_STEPS, pace, top) for i in range(READ_STEPS)) / READ_STEPS


def words_read(surface, context, pace=PACE_MS, top=READ_MAX):
    """The words of a passage or message, each weighted by its chance of having been read from time on screen
    (read_chance, stay_chance) at evenly spaced depths: the denominator of the one unit (per_100). A T3 message's
    paragraphs are the distinct bands of its stays, the longest stayed first where bands overlap (a paragraph whose
    layout changed), each with the words and ms stay_chance gives it; its words outside them (paragraphs without
    Japanese, which T3 doesn't watch) count at the mean read chance of the words it watched."""
    if surface != 't3':
        words = context.get('words') or 0
        return words * _read_share(context.get('active') or 0, words, pace, top)
    stays = [s for s in context.get('stays') or [] if s['height'] > 0]
    if not stays:
        return 0.0
    line = _line_height([n[2] for n in context['annotations']])
    bands = {}
    for s in stays:
        bands[(s['y'], s['height'])] = bands.get((s['y'], s['height']), 0) + s['ms']
    taken, watched, read = [], 0.0, 0.0
    for (y, height), _ms in sorted(bands.items(), key=lambda kv: -kv[1]):
        if any(y < ty + th and ty < y + height for ty, th in taken):
            continue
        taken.append((y, height))
        ms, words, _share = paragraph_reading(context, y, line)
        watched += words
        read += words * _read_share(ms, words, pace, top)
    rest = (context.get('words') or 0) - watched
    return read + rest * read / watched if rest > 0 and watched else read


def per_100(lookups, read):
    """The one unit of budgets and their checks (PLAN.md section 5): lookups per 100 words read, where read is
    words_read summed over the passages and messages the lookups were counted in (so each shown word counts by its
    chance of having been read). A planner, assuming the passage is read, divides by the words it holds instead.
    None when nothing was read."""
    return 100 * lookups / read if read > 0 else None


def _line_height(ys):
    gaps = [b - a for a, b in zip(sorted(set(ys)), sorted(set(ys))[1:]) if b - a > 2]
    return statistics.median(gaps) if gaps else 20.0


def matcher(listed):
    """listed as a function from a glossed word to its entry or None: a set of words matches as written."""
    return listed if callable(listed) else (lambda word: word if word in listed else None)


def is_sentence(word):
    """A sentence translation: T3's gloss on a sentence's end (。), or a whole sentence glossed as one."""
    return word in PUNCTUATION or (len(word) > 1 and word[-1] in PUNCTUATION)


def _opened(surface, notes, items):
    """{annotation index: y} for the opens among items: y 1 for a pin or flip or a peek open at least GLANCE_MS,
    GLANCE_Y for a shorter peek; T3 peeks under T3_NOISE_MS don't count."""
    opened = {}
    for row in items:
        if surface == 't3' and row['kind'] == 'peek' and row['dwell_ms'] < T3_NOISE_MS:
            continue
        index = row['position'] if 0 <= row['position'] < len(notes) and notes[row['position']][0] == row['word'] \
            else next((i for i, n in enumerate(notes) if n[0] == row['word']), None)
        if index is None:
            continue
        y = GLANCE_Y if row['kind'] == 'peek' and row['dwell_ms'] < GLANCE_MS else 1.0
        opened[index] = max(opened.get(index, 0), y)
    return opened


def observations(surface, context, items, listed, read=None):
    """[(entry, y, weight)] for the lexicon words of one passage or message. listed: a set of words, or a function
    from a glossed word (perhaps a conjugated form) to its entry (matcher).

    Opened: y 1 for a pin or flip or a peek open at least GLANCE_MS, GLANCE_Y for a shorter peek; T3 peeks under
    T3_NOISE_MS don't count. Unopened: y 0, weighted by the chance it was read: 1 down to the lowest opened line,
    halving per line below it, or read(y, line) when that is higher (time on screen: read_chance, stay_chance). Without
    read, nothing opened means [] (an episode exists only because something was opened). The unopened words of a
    sentence whose whole-sentence translation was opened get nothing (it was read in English); its opened words still
    count as opened."""
    return [(word, y, weight) for word, y, weight, _index, _near in observed(surface, context, items, listed, read)]


def observed(surface, context, items, listed, read=None):
    """observations, each with the index of the annotation it comes from (the one with the highest y, then weight)
    and the line rule's weight alone (1 when opened): [(entry, y, weight, index, near)]."""
    match = matcher(listed)
    notes = context['annotations']
    opened = _opened(surface, notes, items)
    if not opened and read is None:
        return []
    excluded = set()
    for index in opened:
        if notes[index][0] in PUNCTUATION:  # a sentence translation: skip back to the previous sentence end
            start = max((i for i in range(index) if notes[i][0] in PUNCTUATION), default=-1)
            excluded.update(range(start + 1, index))
    line = _line_height([n[2] for n in notes])
    lowest = max(notes[i][2] for i in opened) if opened else None
    best = {}
    for index, (written, _x, y_pos) in enumerate(notes):
        word = match(written)
        if word is None or (index in excluded and index not in opened):
            continue
        if index in opened:
            y, weight = opened[index], 1.0
            near = 1.0
        else:
            near = 0.0
            if lowest is not None:
                below = y_pos - lowest
                near = 1.0 if below <= line / 2 else 0.5 ** math.ceil(below / line)
            weight = max(near, read(y_pos, line)) if read is not None else near
            y = 0.0
        if weight < 0.05:
            continue
        old = best.get(word)
        if old is None or (y, weight) > (old[0], old[1]):
            best[word] = (y, weight, index, near)
    return [(word, y, weight, index, near) for word, (y, weight, index, near) in best.items()]


# ---- the online model the fit replaced (evaluate.py's "today") ----

class Word:
    __slots__ = ('mu', 'k', 'n', 'last', 'obs', 'opened', 'recent', 'history', 'command_at')

    def __init__(self, mu):
        self.mu, self.k, self.n, self.last = mu, mu, 0.0, None
        self.obs, self.opened, self.recent, self.history, self.command_at = 0, 0, [], [], None

    def age(self, at):
        """Forgetting between observations: evidence fades, knowledge decays toward the prior (downward only)."""
        if self.last is not None and at > self.last:
            days = (at - self.last) / DAY_MS
            self.n *= 0.5 ** (days / EVIDENCE_DAYS)
            if self.k > self.mu:
                self.k = self.mu + (self.k - self.mu) * 0.5 ** (days / FORGET_DAYS)
        self.last = at if self.last is None else max(self.last, at)

    def k_at(self, at):
        """k as age(at) would leave it, without aging the word."""
        if self.last is not None and at > self.last and self.k > self.mu:
            return self.mu + (self.k - self.mu) * 0.5 ** ((at - self.last) / DAY_MS / FORGET_DAYS)
        return self.k

    @property
    def sd(self):
        return math.sqrt(2 / (1 + self.n))


class Online:
    """The online model the fit replaced, kept as evaluate.py's "today" for comparison: every word's knowledge and the
    T3 offset, updated one passage, message, episode or command at a time, oldest first, each by a step of the gradient
    of log P(what happened), with P(open) = read chance x P(peek), and k pulled back toward its prior between
    observations (FORGET_DAYS, downward only). replay feeds it the whole log; evaluate.py's "today" model feeds it the
    log day by day."""

    def __init__(self, intervals):
        self.intervals, self.words, self.offset = intervals, {}, {'browser': 0.0, 't3': 0.0}

    def get(self, ja):
        if ja not in self.words:
            self.words[ja] = Word(prior(self.intervals.get(ja)))
        return self.words[ja]

    def chance(self, ja, surface, at):
        """P(peek) of a word read at `at` on a surface, as the model stands (no update; from the prior without
        evidence)."""
        word = self.words.get(ja)
        return sigmoid(self.offset[surface] - (word.k_at(at) if word is not None else prior(self.intervals.get(ja))))

    def command(self, at, ja, kind):
        """A known or learn command on a word."""
        word = self.get(ja)
        word.age(at)
        word.command_at = at
        if kind == 'known':
            word.k, word.n = max(word.k, KNOWN_COMMAND_K), word.n + 2
        else:
            word.k = min(word.k, LEARN_COMMAND_K)

    def age(self, at, obs):
        for ja, _y, _w in obs:
            self.get(ja).age(at)

    def showing(self, at, surface, obs):
        """A passage or message replayed whole: obs as observations gives them with a read chance."""
        self.age(at, obs)
        residual = total = 0.0
        for ja, y, weight in obs:
            word = self.words[ja]
            q = sigmoid(self.offset[surface] - word.k)
            eta = max(ETA_MIN, ETA_MAX / (1 + ETA_SLOPE * word.n))
            if y > 0:  # opened, so read: log P(open | read) = log q
                word.k += eta * (q - y)
                word.n += 1.0
                word.opened += 1
                word.recent.append(at)
                residual += y - q
                total += 1.0
            else:  # not opened, read with chance r: log (1 - r q)
                slope = weight * q * (1 - q) / (1 - weight * q)
                word.k += eta * slope
                word.n += weight
                residual -= slope
                total += weight
            word.obs += 1
            word.history.append({'at': at, 'surface': surface, 'y': y, 'w': round(weight, 2), 'k': round(word.k, 2)})
            del word.history[:-30]
        if surface == 't3' and total:  # the same gradients, with respect to the offset
            self.offset['t3'] = max(-T3_LIMIT, min(T3_LIMIT, self.offset['t3'] + T3_RATE * residual / total))

    def episode(self, at, surface, obs):
        """An episode (observations without a read chance, not empty): conditioned on at least one peek."""
        self.age(at, obs)
        p = {ja: sigmoid(self.offset[surface] - self.words[ja].k) for ja, _y, _w in obs}
        none = 1.0
        for value in p.values():
            none *= 1 - value
        engaged = max(1 - none, 1e-6)  # chance of at least one peek: only such episodes are logged
        residual = total = 0.0
        for ja, y, weight in obs:
            word = self.words[ja]
            expected = p[ja] / engaged
            eta = max(ETA_MIN, ETA_MAX / (1 + ETA_SLOPE * word.n))
            word.k += eta * weight * (expected - y)
            word.n += weight
            word.obs += 1
            if y > 0:
                word.opened += 1
                word.recent.append(at)
            word.history.append({'at': at, 'surface': surface, 'y': y, 'w': round(weight, 2), 'k': round(word.k, 2)})
            del word.history[:-30]
            residual += weight * (y - expected)
            total += weight
        if surface == 't3' and total:
            self.offset['t3'] = max(-T3_LIMIT, min(T3_LIMIT, self.offset['t3'] + T3_RATE * residual / total))

    def finish(self, now_ms):
        """Every word as of now: aged, with its opens of the last RECENT_DAYS days."""
        for word in self.words.values():
            word.age(now_ms)
            word.recent = [t for t in word.recent if t >= now_ms - RECENT_DAYS * DAY_MS]


EPISODE, COMMAND, SHOWING = 0, 1, 2


def timeline(rows, contexts, listed, commands, now_ms):
    """The log in the order replay reads it, oldest first: [(at, kind, item)], kind EPISODE (item as episodes gives
    it), COMMAND (a command on a listed word) or SHOWING (as showings gives it); at a tie episodes, then commands, then
    showings."""
    match = matcher(listed)
    out = [(e[2][-1]['at'], EPISODE, e) for e in episodes(rows, contexts, now_ms)]
    out += [(e[0], SHOWING, e) for e in showings(rows, contexts, now_ms)]
    out += [(c['at'], COMMAND, c) for c in commands if match(c['word'])]
    return sorted(out, key=lambda t: (t[0], t[1]))


def replay(rows, contexts, listed, intervals, commands, now_ms, pace=PACE_MS, top=READ_MAX, losses=None, shown=None):
    """The online model the fit replaced (Online, evaluate.py's "today"): every word's knowledge after the whole log;
    also the T3 offset. listed: as in observations. pace, top: the reading model (read_chance, stay_chance). losses: a
    list to append, for every listed word of every browser passage shown, (opened, predicted chance of an open) before
    the update, with the read chance from time on screen alone. shown: a list to append, for every browser passage and
    T3 message shown, its record before the update (shown_record, as calibrate reads it)."""
    match = matcher(listed)
    model = Online(intervals)
    for at, kind, item in timeline(rows, contexts, listed, commands, now_ms):
        if kind == COMMAND:
            model.command(at, match(item['word']), item['kind'])
        elif kind == SHOWING:
            _at, (surface, _view, _context), context, items = item
            read = reader(surface, context, pace, top)
            obs = observations(surface, context, items, listed, read)
            model.age(at, obs)
            if shown is not None:
                shown.append(shown_record(at, surface, context, items, model, match, pace, top))
            if losses is not None and surface == 'browser':
                line = _line_height([n[2] for n in context['annotations']])
                for ja, y, _w in obs:
                    alone = max(0.05, read_chance(context, _y_of(context, ja, match), line, pace, top))
                    losses.append((y > 0, min(1 - 1e-6, max(1e-6, alone * model.chance(ja, surface, at)))))
            model.showing(at, surface, obs)
        else:
            (surface, _view, _context), context, items = item
            obs = observations(surface, context, items, listed)
            if obs:
                model.episode(at, surface, obs)
    model.finish(now_ms)
    return model.words, model.offset


def _y_of(context, ja, match):
    return next((n[2] for n in context['annotations'] if match(n[0]) == ja), 0)


CLASSES = ('costly', 'free', 'topical', 'sentences', 'offlist')  # calibrate's classes of lookups


def shown_record(at, surface, context, items, model, match, pace=PACE_MS, top=READ_MAX):
    """One passage or message for calibrate, as the model stood before it: {at, shown (when it was shown: its shown
    record, else at), surface, words (shown), japanese (its annotations: none in a blank passage), read (words_read),
    lookups (its opens; T3 peeks under T3_NOISE_MS left out), budget (a browser plan's, per 100 words; else None),
    classes: {class: [expected, seen]}}. Expected is the read chance from time on screen times P(peek), each word at
    its best annotation, and seen its opens (a glance counts GLANCE_Y): listed words are free when the model says so
    (model.is_free, the free flag; without one, P(peek) under fwlib.FREE_P), else costly; a sentence translation
    (is_sentence) costs the model's price of a whole sentence (model.sentence_price, a chance per sentence read; without
    one it isn't priced); a word off the list costs its prior's P(peek). Topical words (chat words found by code,
    planned) have none yet. model: chance(word, surface, at), and maybe is_free(word) and sentence_price(surface)."""
    notes = context['annotations']
    line = _line_height([n[2] for n in notes])
    read = reader(surface, context, pace, top)
    opened = _opened(surface, notes, items)
    units = {}  # (kind, word or sentence index) -> [best read chance, seen]
    for index, (written, _x, y) in enumerate(notes):
        if is_sentence(written):
            unit = ('sentences', index)
        else:
            ja = match(written)
            unit = ('listed', ja) if ja is not None else ('offlist', written)
        best = units.setdefault(unit, [0.0, 0.0])
        best[0] = max(best[0], read(y, line))
        best[1] = max(best[1], opened.get(index, 0.0))
    is_free = getattr(model, 'is_free', None)
    price = getattr(model, 'sentence_price', lambda _surface: 0.0)(surface) or 0.0
    classes = {name: [0.0, 0.0] for name in CLASSES}
    for (kind, word), (chance, seen) in units.items():
        p = price if kind == 'sentences' else model.chance(word, surface, at)
        if kind == 'listed':
            kind = 'free' if (is_free(word) if is_free else p < fwlib.FREE_P) else 'costly'
        classes[kind][0] += chance * p
        classes[kind][1] += seen
    words = context.get('words') or 0
    plan = context.get('plan') or {}
    budget = plan.get('budget') if surface == 'browser' else None
    noise = lambda r: surface == 't3' and r['kind'] == 'peek' and r['dwell_ms'] < T3_NOISE_MS
    return {'at': at, 'shown': context.get('shown_at') or at, 'surface': surface, 'words': words,
            'japanese': len(notes),
            'read': words_read(surface, context, pace, top),
            'lookups': sum(1 for r in items if not noise(r)),
            'budget': 100 * budget / words if isinstance(budget, (int, float)) and words else None,
            'classes': classes}


# ---- the fit ----

FORM_WEIGHT = 0.3          # a conjugated form's outcome counts this much for its word (docs/adaptive-difficulty.md)
COMMAND_K = {'known': 3.5, 'learn': 0.0}  # a known command holds k at least at this, a learn command at most
COMMAND_WEIGHT = 4.0       # with this curvature (a pseudo-observation with an sd of 0.5), fading like evidence
POOL_STEP = 0.02           # unopened observations of a word pool by their read weight, rounded to this
FREE_Z = 1.2816            # the free line's hysteresis: a word's lookup chance at k -+ this many sd (90%, one-sided)
FIT_TOLERANCE = 1e-4       # the fit stops once no parameter moves more than this
WIDTH_TOLERANCE = 1e-3     # nor a prior width
FIT_ITERATIONS = 60
# The shared parameters, each with the prior that holds it while the data is thin: (mean, sd). The offsets: chat's
# (the web's is 0, the reference: k is on the web's scale) and a conjugated form's. Then a word's prior mean, a
# regression on its features (features): with an Anki card, an intercept by origin (origin_at; the names say new,
# learning and known) plus a slope on ln(1 + interval), centred on the old prior 0.5 + 0.8 ln(1 + interval); without
# one, an intercept by origin, centred on the old neutral prior (1.0) and, for words marked known, the planner's
# fwlib.KNOWN_PRIOR_K; for a word not marked known, plus its word features.
SHARED = {'t3': (0.0, 0.5), 'form': (0.0, 1.0),
          'card new': (0.5, 0.5), 'card learning': (0.5, 0.5), 'card known': (0.5, 0.5), 'interval': (0.8, 0.25),
          'new': (1.0, 1.0), 'learning': (1.0, 1.0), 'known': (fwlib.KNOWN_PRIOR_K, 1.0)}
# And the word's own features (word_features), each coefficient held toward 0, no effect, by WORD_SD: on their scales
# (a tenfold rank, a doubling of length, a script or part of speech, all kanji known) a typical word has about four
# features of size 1, so 0.5 lets them move its prior mean by about 1 before the data says so, the spread of words
# around their mean (WIDTHS). Wider, a few words with much evidence swing every unseen word; in the simulated reader
# 0.5 predicts first exposures best of 0.25, 0.5, 1 and 2 (tools/sim/run.py priors).
WORD_FEATURES = ('rank', 'kanji known', 'all kanji known', 'kana', 'katakana', 'mixed', 'verb', 'adjective', 'adverb',
                 'other', 'length', 'rare', 'loan')
WORD_SD = 0.5
SHARED.update({name: (0.0, WORD_SD) for name in WORD_FEATURES})
SHARED_NAMES = tuple(SHARED)
T3, FORM = SHARED_NAMES.index('t3'), SHARED_NAMES.index('form')
ORIGINS = fwlib.ORIGINS  # the prior's intercepts: how a word came into use (origin_at)
WIDTHS = {'card': 1.0, 'none': 1.5}  # the prior sd of k around its mean, by kind: fitted, and held toward these
WIDTH_WEIGHT = 5                     # with this many words' worth of weight


def day_of(at):
    """'YYYY-MM-DD', the local day of a time in ms."""
    return time.strftime('%Y-%m-%d', time.localtime(at / 1000))


def day_start(day):
    """Local midnight of a 'YYYY-MM-DD' day, in ms."""
    return int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)


def status_at(word, day):
    """A word's status on `day` ('YYYY-MM-DD'): 'known', 'learning' (active or shaky) or 'new' (not introduced yet),
    from the word state's added and changed days. Before it was added it was new; from its last change on, its status
    now; in between, where it most likely came from: a known word was learning (it graduated), a shaky word known (two
    peek days make a known word shaky), an active word learning. None for a word the state doesn't have."""
    if word is None:
        return None
    status = word.get('status')
    if status == 'queue' or not word.get('added') or day < word['added']:
        return 'new'
    if day >= (word.get('changed') or ''):
        return 'known' if status == 'known' else 'learning'
    return 'known' if status == 'shaky' else 'learning'


def origin_at(word, day):
    """How a word had come into use by `day` ('YYYY-MM-DD'), the prior's stand-in for its status, which now comes from
    the model (fwlib.relabel) and would feed the fit its own output: 'learning' (taken in to be taught), 'known' (marked
    known: setup's top words, an Anki sync, or known before labels came from the model) or 'new' (neither, or not yet
    on that day). The word's origin field (fwlib.ORIGINS), which only intake, setup, Anki syncs and the switch to the
    model's labels set; a word state from before the switch (and the simulated reader's) has none, and its statuses
    say it (fwlib.legacy_origin), with a known word learning before its last change, as status_at has it. None for a
    word the state doesn't have."""
    if word is None:
        return None
    if not word.get('added') or day < word['added']:
        return 'new'
    explicit = word.get('origin') if word.get('origin') in fwlib.ORIGINS else None
    old = word.get('legacy') if isinstance(word.get('legacy'), dict) else None if explicit else word
    origin = explicit or fwlib.legacy_origin(old)
    if old and origin == 'known' and old.get('status') == 'known' and day < (old.get('changed') or ''):
        return 'learning'  # it graduated by the day rules after that day
    return origin


KANJI = re.compile('[㐀-䶿一-鿿豈-﫿]')  # the kanji of a written form, as dictionary.KANJI
KANJI_LIKE = '々〆ヶヵ'                 # written among kanji (人々, 一ヶ月): a word of kanji and these is all kanji
RANK_CENTRE = 1000         # the reference word of the word features: the 1,000th most frequent entry,
LENGTH_CENTRE = 2          # two characters long, written in kanji alone, a noun, marked common, not borrowed from a
                           # language other than English, none of its kanji known at a glance
FEATURED = ('new', 'learning')  # the origins whose words take the word features (word_features)


def script(ja):
    """How a written form is written: 'kanji' (kanji alone, with 々 〆 ヶ ヵ), 'mixed' (kanji and kana), 'kana' (no
    kanji, some hiragana) or 'katakana' (no kanji, no hiragana: katakana, letters or digits)."""
    if KANJI.search(ja):
        return 'kanji' if all(KANJI.match(c) or c in KANJI_LIKE for c in ja) else 'mixed'
    return 'kana' if any('ぁ' <= c <= 'ゖ' or c in 'ゝゞ' for c in ja) else 'katakana'


def pos_group(pos):
    """A part-of-speech group from JMdict tags, the first sense's first: 'noun' (n..., num, ctr, pn), 'verb' (v...),
    'adjective' (adj...), 'adverb' (adv...) or 'other' (expressions, interjections, particles, conjunctions,
    auxiliaries, prefixes, suffixes)."""
    tag = pos[0] if pos else ''
    if tag.startswith('n') or tag in ('num', 'ctr', 'pn'):
        return 'noun'
    for prefix, group in (('v', 'verb'), ('adj', 'adjective'), ('adv', 'adverb')):
        if tag.startswith(prefix):
            return group
    return 'other'


def kanji_counts(words):
    """{kanji: how many of these words (as written) have it}: what word_features counts as known at a glance."""
    counts = {}
    for ja in words:
        for c in set(KANJI.findall(ja)):
            counts[c] = counts.get(c, 0) + 1
    return counts


def word_features(entry, known=None, own=False, centre=(RANK_CENTRE, LENGTH_CENTRE)):
    """A dictionary entry's word features, {name: value} (WORD_FEATURES; zeros left out), from its fields
    (dictionary.py: ja, rank, common, loan, pos, kanji):
      rank             log10(rank / RANK_CENTRE): +1 for each tenfold rarer (centre: the reference word's rank and
                       length, RANK_CENTRE and LENGTH_CENTRE)
      kanji known      the share of its kanji in words known at a glance (known: {kanji: words}, kanji_counts of the
                       free words; own: whether the entry's word is one of them, so its own kanji don't count)
      all kanji known  1 when it has kanji and all are known
      kana, katakana, mixed  how it is written (script), against kanji alone
      verb, adjective, adverb, other  its part of speech (pos_group), against a noun
      length           log2(characters / LENGTH_CENTRE): +1 for each doubling
      rare             1 when JMdict doesn't mark it common
      loan             1 when JMdict names a source language other than English: every katakana word is a loan
                       (kata: no source given), so the plain flag would repeat the katakana baseline
    """
    ja, rank = entry['ja'], entry.get('rank')
    x = {'rank': math.log10(rank / centre[0]) if rank else 0.0, 'length': math.log2(max(1, len(ja)) / centre[1])}
    kanji = entry.get('kanji') or []
    if kanji and known:
        mine = set(kanji) if own else ()
        share = sum(1 for c in kanji if known.get(c, 0) - (c in mine) > 0) / len(kanji)
        if share:
            x['kanji known'] = share
        if share == 1:
            x['all kanji known'] = 1.0
    for name in (script(ja), pos_group(entry.get('pos'))):
        if name in WORD_FEATURES:
            x[name] = 1.0
    if not entry.get('common'):
        x['rare'] = 1.0
    if any(lang not in ('', 'eng', 'kata') for lang in (entry.get('loan') or '').split(',')):
        x['loan'] = 1.0
    return {name: value for name, value in x.items() if value}


def features(interval, origin, entry=None, known=None, own=False):
    """A word's prior features, {shared parameter: value}, and the kind whose prior width it takes: with an Anki card,
    its origin's card intercept (origin_at) and ln(1 + interval in days), uncapped; without one, its origin's
    intercept. A word of an origin in FEATURED (one the reader hasn't vouched for) with a dictionary entry adds the
    entry's word features (word_features, with known and own); a word marked known keeps its intercept: its lookup
    chance is near its floor whatever the word is like, and its features would only blur theirs."""
    origin = origin if origin in fwlib.ORIGINS else 'new'
    if interval is not None:
        x, kind = {f'card {origin}': 1.0, 'interval': math.log1p(max(0.0, interval))}, 'card'
    else:
        x, kind = {origin: 1.0}, 'none'
    if entry is not None and origin in FEATURED:
        x.update(word_features(entry, known, own))
    return x, kind


def word_entries(words, ids=None):
    """{ja: its dictionary entry, or None} for the word state's entries (dictionary.find, by spelling, reading and
    gloss), or by its JMdict id where ids ({ja: id}, knowledge.json's) has one: a word keeps the entry it was first
    found as, and finding it again costs ten times as much. {} without data/dictionary.tsv."""
    import dictionary  # here: only the prior needs it
    if not dictionary.PATH.exists():
        return {}
    d, ids = dictionary.load(), ids or {}
    return {w['ja']: d.get(ids[w['ja']]) if w['ja'] in ids else d.find(w['ja'], w.get('kana') or '', w.get('en') or '')
            for w in words}


def evidenced(log, until):
    """The words with evidence in a log before `until`: an observation or a command."""
    out = set()
    for event in log.events:
        if event.at >= until:
            break
        if hasattr(event, 'obs'):
            out.update(o.word for o in event.obs)
        else:
            out.add(event.word)
    return out


def _solve(a, b):
    """x with a x = b, for a small positive definite a (Gaussian elimination with pivoting)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


class Fit:
    """One penalized maximum-likelihood fit of a log's events before `until` (an evaluate.Log: its events, Anki
    intervals and word state), at a reading pace and cap (default: the log's).

    An observation is a Bernoulli with P(open) = w q: q = P(lookup | read) = sigmoid(offset_s + form - k_w), w its read
    weight (1 when opened, else the larger of the line rule and the read chance at this pace and cap; an episode's,
    the line rule alone), weighted 0.5 ** (age / EVIDENCE_DAYS) and FORM_WEIGHT for a form. An episode's observations
    are conditioned on at least one open. A word's unopened observations with the same read weight, rounded to
    POOL_STEP, pool, so the work grows with the words more than with the log.

    Minimized: their negative log likelihood; (k_w - mean_w)^2 / 2 width^2 for every word with evidence, mean_w the
    regression on its features (features) and width its kind's; a command's one-sided term, COMMAND_WEIGHT (decayed)
    times (COMMAND_K - k)^2 / 2 while k is below a known command's or above a learn command's; and each shared
    parameter's prior (SHARED). By Newton's method with the expected information, positive definite, whose shape (a
    diagonal for the words, joined only through the shared parameters) the Schur complement solves in time linear in
    the words; the widths by EM toward WIDTHS. Each word's sd is its k's from the inverse of the information (Laplace),
    the shared parameters' uncertainty included. frozen: the shared parameters and widths stay at their priors, as for
    one word among many others. start: what an earlier fit learned (state()), to start from.

    The word features (features, word_features) come from each word's dictionary entry (log.entries) and, for the
    kanji known at a glance, from free: {ja: free flag} at the last refresh (or the day before). Its kanji are known
    from the free words with evidence before `until` (observations or a command), the word itself left out: a word free
    by its prior alone doesn't count, so no chain of priors can make words free, and the flags are from before the
    fit, so it never reads its own output (docs/adaptive-difficulty.md). plain: no word features (the prior by origin
    and Anki interval alone), for comparison."""

    def __init__(self, log, until, pace=None, top=None, start=None, frozen=False, free=None, plain=False):
        self.until, self.day, self.frozen, self.plain = until, day_of(until - 1), frozen, plain
        self.pace, self.top = pace or log.pace, top or log.top
        self.intervals = log.intervals
        self.by_ja = {w['ja']: w for w in log.words}
        self.entries = {} if plain else getattr(log, 'entries', None) or {}
        self.own = {ja for ja in evidenced(log, until) if (free or {}).get(ja)} if self.entries else set()
        self.known, self._features = kanji_counts(self.own), {}
        self.words, self.index, self.x, self.kind, self.n = [], {}, [], [], []
        self._terms(log)
        start = start or {}
        shared = start.get('shared') or {}
        self.theta = [shared.get(name, SHARED[name][0]) if not frozen else SHARED[name][0] for name in SHARED_NAMES]
        self.widths = dict(WIDTHS) if frozen else dict(WIDTHS, **(start.get('widths') or {}))
        before = start.get('k') or {}
        self.k = [before.get(ja, self._mean(i, self.theta)) for i, ja in enumerate(self.words)]
        started = time.time()
        self.iterations = self._run()
        self.seconds = time.time() - started

    def _word(self, ja):
        i = self.index.get(ja)
        if i is None:
            i = self.index[ja] = len(self.words)
            self.words.append(ja)
            x, kind = self.features(ja)
            self.x.append({SHARED_NAMES.index(name): value for name, value in x.items()})
            self.kind.append(kind)
            self.n.append(0.0)
        return i

    def features(self, ja):
        found = self._features.get(ja)
        if found is None:
            found = self._features[ja] = features(self.intervals.get(ja), origin_at(self.by_ja.get(ja), self.day),
                                                  self.entries.get(ja), self.known, ja in self.own)
        return found

    def _terms(self, log):
        pooled, self.episodes, self.commands = {}, [], []
        same = (self.pace, self.top) == (log.pace, log.top)
        for event in log.events:
            if event.at >= self.until:
                break
            decay = 0.5 ** ((self.until - event.at) / DAY_MS / EVIDENCE_DAYS)
            if not hasattr(event, 'obs'):  # a command
                self.commands.append((self._word(event.word), event.kind, decay))
                continue
            chosen = []
            for o in event.obs:
                i = self._word(o.word)
                v = decay * (FORM_WEIGHT if o.form else 1.0)
                if o.y > 0:
                    w = 1.0
                elif event.episode:
                    w = o.near
                else:
                    w = o.weight if same else max(o.near, _chance(o.ms, o.words, o.share, self.pace, self.top))
                    w = min(1.0, max(POOL_STEP, round(w / POOL_STEP) * POOL_STEP))
                key = (i, o.surface == 't3', bool(o.form), o.y, w)
                pooled[key] = pooled.get(key, 0.0) + v
                self.n[i] += v * w
                if event.episode:
                    chosen.append((i, o.surface == 't3', bool(o.form), w))
            if event.episode and chosen:
                self.episodes.append((chosen, decay))
        keys = list(pooled)
        self.terms = (tuple(k[0] for k in keys), tuple(k[1] for k in keys), tuple(k[2] for k in keys),
                      tuple(k[3] for k in keys), tuple(k[4] for k in keys), tuple(pooled[k] for k in keys))

    def _mean(self, i, theta):
        return sum(theta[f] * value for f, value in self.x[i].items())

    def _pass(self, k, theta):
        """The data's negative log likelihood and its derivatives: per word, the gradient and information; the
        information joining each word to the chat and form offsets; for the shared parameters, gradient and
        information."""
        words, shared = len(k), len(theta)
        grad, info, chat, form = [0.0] * words, [0.0] * words, [0.0] * words, [0.0] * words
        g, h = [0.0] * shared, [[0.0] * shared for _ in range(shared)]
        total, t3, phi = 0.0, theta[T3], theta[FORM]
        exp, log = math.exp, math.log
        for w, s, f, y, r, v in zip(*self.terms):
            eta = (t3 if s else 0.0) + (phi if f else 0.0) - k[w]
            q = 1 / (1 + exp(-min(30.0, max(-30.0, eta))))
            if y > 0:  # opened, so read: y log q + (1 - y) log (1 - q)
                d, i = y - q, q * (1 - q)
                total -= v * (y * log(q) + (1 - y) * log(1 - q))
            else:  # not opened, read with chance r: log (1 - r q)
                a = 1 - r * q
                d, i = -r * q * (1 - q) / a, r * q * (1 - q) * (1 - q) / a
                total -= v * log(a)
            vd, vi = v * d, v * i
            grad[w] += vd
            info[w] += vi
            if s:
                g[T3] -= vd
                h[T3][T3] += vi
                chat[w] -= vi
                if f:
                    h[T3][FORM] += vi
            if f:
                g[FORM] -= vd
                h[FORM][FORM] += vi
                form[w] -= vi
        h[FORM][T3] = h[T3][FORM]
        for chosen, v in self.episodes:  # conditioned on at least one open: minus log (1 - none)
            qs, none = [], 1.0
            for w, s, f, r in chosen:
                q = sigmoid((t3 if s else 0.0) + (phi if f else 0.0) - k[w])
                qs.append(q)
                none *= 1 - r * q
            none = min(none, 1 - 1e-12)
            total += v * log(1 - none)
            for (w, s, f, r), q in zip(chosen, qs):
                d = v * none / (1 - none) * r * q * (1 - q) / (1 - r * q)
                grad[w] -= d
                g[T3] += d if s else 0.0
                g[FORM] += d if f else 0.0
        return total, grad, info, chat, form, g, h

    def _priors(self, k, theta, grad, info, g, h):
        """Adds the priors' and commands' terms to the derivatives; returns their part of the objective."""
        total = 0.0
        for i in range(len(k)):
            w2 = self.widths[self.kind[i]] ** 2
            r = k[i] - self._mean(i, theta)
            total += 0.5 * r * r / w2
            grad[i] += r / w2
            info[i] += 1 / w2
            if not self.frozen:
                for f, x in self.x[i].items():
                    g[f] -= x * r / w2
                    for f2, x2 in self.x[i].items():
                        h[f][f2] += x * x2 / w2
        for i, kind, decay in self.commands:
            u, gap = COMMAND_WEIGHT * decay, (COMMAND_K[kind] - k[i]) * (1 if kind == 'known' else -1)
            if gap > 0:
                total += 0.5 * u * gap * gap
                grad[i] += -u * gap if kind == 'known' else u * gap
                info[i] += u
        for j, name in enumerate(SHARED_NAMES):
            mean, sd = SHARED[name]
            total += 0.5 * ((theta[j] - mean) / sd) ** 2
            g[j] += (theta[j] - mean) / sd ** 2
            h[j][j] += 1 / sd ** 2
        return total

    def _objective(self, k, theta, data=None):
        """The objective and its derivatives (as _pass gives them); data: the data's part at k and theta, if known."""
        data = data or self._pass(k, theta)
        found = [data[0], list(data[1]), list(data[2]), data[3], data[4], list(data[5]), [row[:] for row in data[6]]]
        found[0] += self._priors(k, theta, found[1], found[2], found[5], found[6])
        return tuple(found), data

    def _step(self, k, grad, info, chat, form, g, h):
        """The Newton step (dk, dtheta), by the Schur complement on the shared parameters; also each word's row of the
        information joining it to them, and the complement."""
        if self.frozen:
            return [-grad[i] / info[i] for i in range(len(k))], [0.0] * len(g), None, None
        schur, rest, joins = [row[:] for row in h], list(g), []
        for i in range(len(k)):
            c = {f: -x / self.widths[self.kind[i]] ** 2 for f, x in self.x[i].items()}
            if chat[i]:
                c[T3] = c.get(T3, 0.0) + chat[i]
            if form[i]:
                c[FORM] = c.get(FORM, 0.0) + form[i]
            joins.append(c)
            for a, ca in c.items():
                rest[a] -= ca * grad[i] / info[i]
                for b, cb in c.items():
                    schur[a][b] -= ca * cb / info[i]
        dtheta = _solve(schur, [-x for x in rest])
        dk = [-(grad[i] + sum(c * dtheta[a] for a, c in joins[i].items())) / info[i] for i in range(len(k))]
        return dk, dtheta, joins, schur

    def _run(self):
        k, theta = self.k, self.theta
        (total, grad, info, chat, form, g, h), data = self._objective(k, theta)
        last, iteration = {}, 0
        for iteration in range(1, FIT_ITERATIONS + 1):
            dk, dtheta, _joins, _schur = self._step(k, grad, info, chat, form, g, h)
            t = 1.0
            while True:  # halve the step until the objective doesn't rise
                k2 = [a + t * b for a, b in zip(k, dk)]
                theta2 = [a + t * b for a, b in zip(theta, dtheta)]
                found, data2 = self._objective(k2, theta2)
                if found[0] <= total + 1e-9 or t < 1e-3:
                    break
                t /= 2
            k, theta, data, (total, grad, info, chat, form, g, h) = k2, theta2, data2, found
            moved = t * max([abs(x) for x in dk + dtheta] or [0.0])
            widened = 0.0
            if not self.frozen and k:  # EM for the widths: mean squared distance from the prior mean, plus variance
                sums = {kind: [0.0, 0] for kind in self.widths}
                for i in range(len(k)):
                    s = sums[self.kind[i]]
                    s[0] += (k[i] - self._mean(i, theta)) ** 2 + 1 / info[i]
                    s[1] += 1
                for kind, (squares, count) in sums.items():
                    new = math.sqrt((squares + WIDTH_WEIGHT * WIDTHS[kind] ** 2) / (count + WIDTH_WEIGHT))
                    step = new - self.widths[kind]
                    before = last.get(kind)
                    last[kind] = step
                    if before and step * before > 0 and abs(step) < abs(before):  # EM creeps: jump toward its limit
                        step /= 1 - min(0.95, step / before)
                    widened = max(widened, abs(new - self.widths[kind]))
                    self.widths[kind] = max(0.1, self.widths[kind] + step)
                if widened:  # only the priors' terms change
                    (total, grad, info, chat, form, g, h), data = self._objective(k, theta, data)
            if moved < FIT_TOLERANCE and widened < WIDTH_TOLERANCE:
                break
        self.k, self.theta, self.objective = k, theta, total
        _dk, _dt, joins, schur = self._step(k, grad, info, chat, form, g, h)
        self.var = [1 / x for x in info]
        self.theta_sd = [0.0] * len(theta)
        if schur is not None:  # Laplace: the diagonal of the inverse information
            columns = [_solve(schur, [1.0 if i == j else 0.0 for i in range(len(theta))]) for j in range(len(theta))]
            inverse = [[columns[j][i] for j in range(len(theta))] for i in range(len(theta))]
            for i, c in enumerate(joins):
                extra = sum(ca * inverse[a][b] * cb for a, ca in c.items() for b, cb in c.items())
                self.var[i] += extra / info[i] ** 2
            self.theta_sd = [math.sqrt(max(0.0, inverse[j][j])) for j in range(len(theta))]
        return iteration

    def prior(self, ja):
        """(mean, sd) of a word's k before any evidence on it."""
        x, kind = self.features(ja)
        return sum(self.theta[SHARED_NAMES.index(name)] * value for name, value in x.items()), self.widths[kind]

    def estimate(self, ja):
        """(k, sd): the fit's for a word with evidence or a command, else its prior."""
        i = self.index.get(ja)
        return (self.k[i], math.sqrt(self.var[i])) if i is not None else self.prior(ja)

    def q(self, ja, surface, form=False):
        """P(lookup | read) of a word on a surface, as a conjugated form or not."""
        offset = (self.theta[T3] if surface == 't3' else 0.0) + (self.theta[FORM] if form else 0.0)
        return sigmoid(offset - self.estimate(ja)[0])

    def shared(self):
        return {name: round(v, 4) for name, v in zip(SHARED_NAMES, self.theta)}

    def state(self):
        """What a later fit may start from."""
        return {'shared': dict(zip(SHARED_NAMES, self.theta)), 'widths': dict(self.widths),
                'k': dict(zip(self.words, self.k))}


def nested_loglik(log, fit, pace, top):
    """The log likelihood of what was opened in the log's passages and messages shown before fit.until, under the
    reading model at `pace` and `top` and the fit's chances: a reader reads down to one depth, so everything with a
    higher read chance (at least READ_FLOOR) than an open was read. With the words ordered by read chance r_1 >= ... >=
    r_n, reading exactly the first j happens with chance r_j - r_(j+1) (r_(n+1) = 0; none: 1 - r_1), and then each
    read word is opened with its chance q. Weighted like evidence."""
    total = 0.0
    for event in log.events:
        if event.at >= fit.until:
            break
        if not hasattr(event, 'obs') or event.episode or not event.obs:
            continue
        rows = sorted(((max(READ_FLOOR, _chance(o.ms, o.words, o.share, pace, top)), o.y,
                        min(1 - 1e-9, max(1e-9, fit.q(o.word, o.surface, o.form)))) for o in event.obs),
                      key=lambda t: -t[0])
        lowest = max((i for i, (_r, y, _q) in enumerate(rows) if y > 0), default=-1)
        chance = 1 - rows[0][0] if lowest < 0 else 0.0
        product = 1.0
        for j, (r, y, q) in enumerate(rows):
            product *= q ** y * (1 - q) ** (1 - y)
            if j >= lowest:
                chance += (r - (rows[j + 1][0] if j + 1 < len(rows) else 0.0)) * product
        total += 0.5 ** ((fit.until - event.at) / DAY_MS / EVIDENCE_DAYS) * math.log(max(chance, 1e-300))
    return total


def tune(log, until, free=None):
    """The reading model (pace, top) by profile likelihood over the grid PACES x READ_MAXES: at a point the whole log is
    fitted (Fit, starting from the point before) and scored by the likelihood of what was opened under the reading
    model the read chances come from (nested_loglik), on both surfaces. The search scores every other point first
    (the defaults among them), then the neighbours of the best until it stays. Returns {pace, top, tuned, best,
    inside, opens, grid}; pace and top stay the defaults until there are TUNE_MIN_OPENS opens, the best pair beats the
    defaults by TUNE_MARGIN, and it lies inside the grid: on its edge the likelihood has no maximum in the plausible
    range, a sign the reading model doesn't fit (on the live log of 2026-10-20 it keeps rising past 5,000 ms a word).
    free: the free flags the fits take (Fit)."""
    opens = sum(o.y for e in log.events if hasattr(e, 'obs') and not e.episode and e.at < until for o in e.obs)
    scores, start = {}, None

    def score(i, j):
        nonlocal start
        if (i, j) not in scores and 0 <= i < len(PACES) and 0 <= j < len(READ_MAXES):
            fit = Fit(log, until, PACES[i], READ_MAXES[j], start=start, free=free)
            start = fit.state()
            scores[(i, j)] = nested_loglik(log, fit, PACES[i], READ_MAXES[j])

    for j in range(0, len(READ_MAXES), 2):
        for i in range(0, len(PACES), 2):
            score(i, j)
    best = None
    while best != max(scores, key=scores.get):
        best = max(scores, key=scores.get)
        for i in (best[0] - 1, best[0], best[0] + 1):
            for j in (best[1] - 1, best[1], best[1] + 1):
                score(i, j)
    grid = [{'pace': PACES[i], 'top': READ_MAXES[j], 'loglik': round(v, 3)} for (i, j), v in sorted(scores.items())]
    top_ = max(grid, key=lambda g: g['loglik'])
    default = next(g for g in grid if (g['pace'], g['top']) == (PACE_MS, READ_MAX))
    inside = 0 < best[0] < len(PACES) - 1 and 0 < best[1] < len(READ_MAXES) - 1
    ready = opens >= TUNE_MIN_OPENS and top_['loglik'] - default['loglik'] >= TUNE_MARGIN and inside
    return {'pace': top_['pace'] if ready else PACE_MS, 'top': top_['top'] if ready else READ_MAX, 'tuned': ready,
            'best': top_, 'inside': inside, 'opens': round(opens, 1), 'grid': grid}


def free_flag(k, sd, before, mean):
    """Whether a word is free, known at a glance, on the web's scale (offset 0), with hysteresis so that one lookup
    doesn't flip it: a free word stays free until the low end of its lookup chance, sigmoid(-(k + FREE_Z sd)), passes
    fwlib.FREE_P; any other word becomes free once the high end, sigmoid(-(k - FREE_Z sd)), falls below it. before:
    the word's flag at the last refresh, else (no history) whether its prior mean gives under FREE_P."""
    if before is None:
        before = sigmoid(-mean) < fwlib.FREE_P
    if before:
        return sigmoid(-(k + FREE_Z * sd)) <= fwlib.FREE_P
    return sigmoid(-(k - FREE_Z * sd)) < fwlib.FREE_P


def free_flags(fit, words, before):
    """{ja: free} for words (ja), each from the fit and its flag before ({ja: bool}; free_flag)."""
    out = {}
    for ja in words:
        k, sd = fit.estimate(ja)
        out[ja] = free_flag(k, sd, before.get(ja), fit.prior(ja)[0])
    return out


class Standing:
    """The model as it stood at a time, as shown_record reads it: a Fit, the free flags then ({ja: bool}; a word
    without one gets its prior's, free_flag) and the sentence prices then (sentence_prices)."""

    def __init__(self, fit, flags, prices):
        self.fit, self.flags, self.prices = fit, flags, prices

    def chance(self, ja, surface, _at=None):
        return self.fit.q(ja, surface)

    def is_free(self, ja):
        if ja not in self.flags:
            k, sd = self.fit.estimate(ja)
            self.flags[ja] = free_flag(k, sd, None, self.fit.prior(ja)[0])
        return self.flags[ja]

    def sentence_price(self, surface):
        return self.prices[surface]['p']


def fitted_records(rows, contexts, match, log, since, now_ms, pace, top, sentences, flags=None, start=None,
                   done=()):
    """calibrate's records (shown_record) of the passages and messages shown from `since` on, each as the model stood
    at the start of its day: the log (an evaluate.Log) fitted on everything before (Fit, each day starting from the
    fit before: start, a fit's state, for the first; with the free flags of the day before), the free flags carried
    from day to day (free_flags, from flags, else the priors'), and the sentence prices from the sentence ends before
    (sentence_prices of sentence_events). done: days to skip (their sums are known). Returns (records, {day: the free
    flags at its start})."""
    out, starts, flags, day, standing = [], {}, dict(flags or {}), None, None
    for at, (surface, _view, _context), context, items in showings(rows, contexts, now_ms):
        if at < since or day_of(at) in done:
            continue
        if day_of(at) != day:
            day = day_of(at)
            fit = Fit(log, day_start(day), pace, top, start=start, free=flags)
            start = fit.state()
            flags.update(free_flags(fit, fit.words, flags))
            starts[day] = dict(flags)
            standing = Standing(fit, dict(flags), sentence_prices(sentences, day_start(day)))
        out.append(shown_record(at, surface, context, items, standing, match, pace, top))
    return out, starts


def day_keys(log, rows, changes, since, now_ms, pace, top, challenge):
    """{day: key} for the days from `since` to yesterday: what their records depend on (the model, the reading model,
    the challenge, and how much of the log, its interactions and intensity changes came before the day's end), so a
    day's sums are reused while its key holds (refresh, knowledge.json "daily")."""
    import bisect
    ats, row_ats = [e.at for e in log.events], sorted(r['at'] for r in rows)
    counts, total = [], 0
    for e in log.events:
        total += len(getattr(e, 'obs', ()))
        counts.append(total)
    out, day = {}, day_of(since)
    while day_start(day) < day_start(day_of(now_ms)):
        end = day_start(day) + DAY_MS + 3 * 3_600_000  # past a daylight-saving change, then back to midnight
        end = day_start(day_of(end))
        n = bisect.bisect_left(ats, end)
        out[day] = (f'{MODEL} {pace} {top} {challenge} {n} {counts[n - 1] if n else 0} '
                    f'{bisect.bisect_left(row_ats, end)} {sum(1 for c in changes if c["at"] < end)}')
        day = day_of(end)
    return out


SENTENCE_PRICE = 0.08      # a whole sentence's price before the log says (levels.js SENTENCE_PRICE): the beta prior's
SENTENCE_WEIGHT = 25       # mean, and its weight in sentences read


def sentence_ends(surface, context, items, read):
    """[(y, read chance)] for each whole-sentence translation a passage or message offered (an annotation is_sentence
    takes): y as an open of a word counts (_opened), 0 if not opened, and its read chance from time on screen alone,
    read(y, line), as calibrate weighs it (shown_record)."""
    notes = context['annotations']
    opened = _opened(surface, notes, items)
    line = _line_height([n[2] for n in notes])
    return [(opened.get(index, 0.0), read(y, line)) for index, (written, _x, y) in enumerate(notes)
            if is_sentence(written)]


def sentence_events(rows, contexts, now_ms, pace=PACE_MS, top=READ_MAX):
    """[(at, surface, y, read chance)] for every sentence end of every passage and message shown (sentence_ends)."""
    out = []
    for at, (surface, _view, _context), context, items in showings(rows, contexts, now_ms):
        read = reader(surface, context, pace, top)
        out += [(at, surface, y, r) for y, r in sentence_ends(surface, context, items, read)]
    return out


def sentence_prices(events, until):
    """{surface: {p, read, opens}}: the chance a reader who reads a sentence in Japanese opens its translation: the
    opens of sentence ends before `until` over their read chances from time on screen (so calibrate, which expects
    read chance times p, agrees on the days it was fitted on), both weighted like evidence (EVIDENCE_DAYS) and shrunk
    toward SENTENCE_PRICE by SENTENCE_WEIGHT sentences of prior (a beta prior)."""
    sums = {'browser': [0.0, 0.0], 't3': [0.0, 0.0]}
    for at, surface, y, read in events:
        if at < until:
            decay = 0.5 ** ((until - at) / DAY_MS / EVIDENCE_DAYS)
            sums[surface][0] += decay * y
            sums[surface][1] += decay * read
    return {surface: {'p': round((opens + SENTENCE_PRICE * SENTENCE_WEIGHT) / (read + SENTENCE_WEIGHT), 4),
                      'read': round(read, 1), 'opens': round(opens, 1)} for surface, (opens, read) in sums.items()}


# ---- calibration: is the default amount right? ----

def decisions(changes):
    """[(at, before, after)] levels for one surface's intensity changes, oldest first: changes less than BURST_MS apart
    are one decision (trying the keys, overshooting), judged by where they end; a level that ran out was normal."""
    out, prev = [], None
    for c in changes:
        if prev is None or c['at'] - prev['at'] >= BURST_MS:
            if prev is not None:
                out[-1][2] = prev['level']
            out.append([c['at'], prev['level'] if prev and prev['level'] and prev['until'] > c['at'] else 0, None])
        prev = c
    if prev is not None:
        out[-1][2] = prev['level']
    return [tuple(d) for d in out]


def calibrate(rows, contexts, changes, now_ms, days=CALIBRATION_DAYS, shown=None, challenge=0, totals=None):
    """Evidence on the default amount of Japanese, over the last `days` days.

    reading: browser passages shown with some time on screen, by the share of their words in Japanese (the first bin,
    low = high = 0, is passages planned that got none): how many, the median ms of screen time per word, and opens
    per 100 words. Screen time overstates reading (a passage stays on screen while its neighbours are read), so
    compare bins with each other, not with a reading speed.
    jitter: passages the extension randomly thinned (plan.jitter < 1, also to none) against untouched ones, among those
    with candidates (thinning can't change a passage without any), planned since thinning took free words only (their
    plan has a sentencePrice, from extension 0.9.0, 2026-10-20). The thinning is random, so the difference between
    the two groups is caused by the free words alone; gap: how much less Japanese the thinned ones have (share), once
    both groups have THIN_MIN_PASSAGES; effect: change in log ms per word per unit of share, which approximates how
    much one more point of Japanese known at a glance slows reading, once the gap is over THIN_MIN_GAP. jitterBefore:
    the same for the passages planned before, when thinning scaled the budget too.
    presses: the reader's heavier/lighter presses per surface (intensity.py; one into off is lighter, into full
    heavier) and the share of the time spent at each level, from the ops log. A chat with Japanese turned off
    (threads.py, the "thread" changes) counts as one more chat lighter press (threadsOff: how many chats).
    classes: the model against what happened, from shown records (fitted_records; replay's for the model it replaced)
    and the day totals of days already summed (lookup_classes), in the one unit.
    """
    since = now_ms - days * DAY_MS
    opens = {}
    for row in rows:
        if row['surface'] == 'browser' and row['kind'] in ('peek', 'flip'):
            key = (row['surface'], row['view'], row['context'])
            opens[key] = opens.get(key, 0) + 1
    passages = []
    for key, c in contexts.items():
        if c['surface'] != 'browser' or not is_showing(c) or c['shown_at'] < since or \
                (c.get('words') or 0) < MIN_PASSAGE_WORDS:
            continue
        if (c.get('active') or 0) < MIN_ACTIVE_MS:
            continue
        japanese = sum(1 for n in c['annotations'] if n[0] not in PUNCTUATION)
        plan = c.get('plan') or {}
        passages.append({'share': japanese / c['words'], 'msPerWord': c['active'] / c['words'], 'words': c['words'],
                         'opens': opens.get(key, 0), 'jitter': plan.get('jitter'), 'level': plan.get('level', 0),
                         'candidates': plan.get('candidates') or 0, 'free': 'sentencePrice' in plan})

    def summary(group):
        if not group:
            return {'n': 0}
        words = sum(p['words'] for p in group)
        return {'n': len(group), 'share': round(sum(p['share'] for p in group) / len(group), 4),
                'msPerWord': round(statistics.median(p['msPerWord'] for p in group)),
                'opensPer100': round(100 * sum(p['opens'] for p in group) / words, 2)}

    normal = [p for p in passages if not p['level']]
    bins = [dict(summary([p for p in normal if not p['share']]), low=0.0, high=0.0)]
    for low, high in zip(SHARE_BINS, SHARE_BINS[1:]):
        group = [p for p in normal if low <= p['share'] < high and p['share']]
        bins.append(dict(summary(group), low=low, high=min(1.0, high)))
    def thinning(offered):
        jittered = [p for p in offered if p['jitter'] is not None and p['jitter'] < 1]
        untouched = [p for p in offered if p['jitter'] == 1]
        out = {'thinned': summary(jittered), 'untouched': summary(untouched), 'gap': None, 'effect': None}
        if len(jittered) >= THIN_MIN_PASSAGES and len(untouched) >= THIN_MIN_PASSAGES:
            mean = lambda xs: sum(xs) / len(xs)
            gap = mean([p['share'] for p in untouched]) - mean([p['share'] for p in jittered])
            out['gap'] = round(gap, 4)
            if gap > THIN_MIN_GAP:
                slow = mean([math.log(p['msPerWord']) for p in untouched]) - mean([math.log(p['msPerWord'])
                                                                                    for p in jittered])
                out['effect'] = round(slow / gap, 3)
        return out

    offered = [p for p in normal if p['candidates'] > 0]
    jitter = thinning([p for p in offered if p['free']])
    before = thinning([p for p in offered if not p['free']])
    presses = {}
    threads_off = len({c['session'] for c in changes if c['surface'] == 'thread' and c['off'] and c['at'] >= since})
    for surface in ('chat', 'browser'):
        mine = [c for c in changes if c['surface'] == surface]
        recent = [c for c in mine if c['at'] >= since]
        time_at = {}
        for i, c in enumerate(mine):
            start = max(c['at'], since)
            end = min(mine[i + 1]['at'] if i + 1 < len(mine) else now_ms, c['until'] if c['level'] else now_ms)
            if end > start and c['level']:
                time_at[c['level']] = time_at.get(c['level'], 0) + end - start
        decided = [(a, b) for at, a, b in decisions(mine) if at >= since]
        lighter, heavier = sum(1 for a, b in decided if b < a), sum(1 for a, b in decided if b > a)
        lighter += threads_off if surface == 'chat' else 0
        verdict = ('too heavy' if lighter >= 5 and lighter >= 3 * max(1, heavier) else
                   'too light' if heavier >= 5 and heavier >= 3 * max(1, lighter) else None)
        presses[surface] = {'lighter': lighter, 'heavier': heavier, 'hoursAt': {str(k): round(v / 3_600_000, 1)
                                                                               for k, v in sorted(time_at.items())},
                            'verdict': verdict}
    presses['chat']['threadsOff'] = threads_off
    return {'days': days, 'passages': len(passages), 'bins': bins, 'jitter': jitter, 'jitterBefore': before,
            'presses': presses,
            'classes': lookup_classes([r for r in shown or [] if r['at'] >= since], changes, challenge,
                                      {day: t for day, t in (totals or {}).items() if day >= day_of(since)})}


def chat_budget(changes, at, challenge=0):
    """The chat's lookup budget per 100 words at `at`: its level then (the last chat intensity change before it, while
    it lasted; else normal), with normal's budget the challenge when set. None at full (no budget)."""
    level = 0
    for c in changes:
        if c['surface'] == 'chat' and c['at'] <= at:
            level = c['level'] if c['level'] and c['until'] > at else 0
    return intensity.budget(level, challenge or None)


def day_interval(days, statistic, level=0.9, draws=BOOT_DRAWS, seed=1):
    """A `level` interval for statistic(days) from resampling the days with replacement (a percentile bootstrap; days
    are what varies most: a reader's mood, topic and amount of reading). None with fewer than MIN_DAYS days or when
    the statistic is undefined in most draws. Seeded, so a report repeats."""
    if len(days) < MIN_DAYS:
        return None
    rng = random.Random(seed)
    values = sorted(v for v in (statistic(rng.choices(days, k=len(days))) for _ in range(draws)) if v is not None)
    if len(values) < draws / 2:
        return None
    pick = lambda q: values[min(len(values) - 1, max(0, round(q * (len(values) - 1))))]
    return pick((1 - level) / 2), pick((1 + level) / 2)


def ratio(days):
    """Seen over expected, summed over [(expected, seen)] days; None when nothing was expected."""
    expected = sum(e for e, _s in days)
    return sum(s for _e, s in days) / expected if expected > 0 else None


def day_totals(records, changes=(), challenge=0):
    """The sums lookup_classes reads, per day and surface, from shown records (shown_record) of passages and messages
    with Japanese (blank passages have nothing to look up and don't count): {day: {surface: {n, words, read, lookups,
    none, budget: [sum of budget x read, read under a budget], classes: {class: [expected, seen]}}}}. A budget is the
    level's per 100 words read: a browser passage's plan's, a chat message's level's when shown (full has none)."""
    out = {}
    for r in records:
        if not r['japanese']:
            continue
        day = out.setdefault(day_of(r['at']), {})
        t = day.setdefault(r['surface'], {'n': 0, 'words': 0, 'read': 0.0, 'lookups': 0, 'none': 0,
                                          'budget': [0.0, 0.0], 'classes': {name: [0.0, 0.0] for name in CLASSES}})
        t['n'] += 1
        t['words'] += r['words']
        t['read'] += r['read']
        t['lookups'] += r['lookups']
        t['none'] += not r['lookups']
        for name, (expected, seen) in r['classes'].items():
            t['classes'][name][0] += expected
            t['classes'][name][1] += seen
        budget = r['budget'] if r['surface'] == 'browser' else chat_budget(changes, r.get('shown', r['at']), challenge)
        if budget is not None:
            t['budget'][0] += budget * r['read']
            t['budget'][1] += r['read']
    return out


def lookup_classes(records, changes=(), challenge=0, totals=None):
    """Per surface ('browser', 't3'), from shown records (shown_record) of passages and messages with Japanese, and
    totals (day_totals of other days, as cached), in lookups per 100 words read (per_100): {n, words (shown), read
    (words_read), lookups, none (with no lookup), days, budget (the level's budget per 100 words read, weighted by words
    read), classes: {class: {expected, seen, ratio: seen / expected, interval: its 90% interval from resampling days}}},
    for each of CLASSES and "all"."""
    days = dict(totals or {})
    days.update(day_totals(records, changes, challenge))
    out = {}
    for surface in ('browser', 't3'):
        mine = [d[surface] for d in days.values() if surface in d]
        read = sum(t['read'] for t in mine)
        classes = {}
        for name in CLASSES + ('all',):
            pairs = [tuple(t['classes'][name]) if name != 'all' else
                     (sum(e for e, _s in t['classes'].values()), sum(s for _e, s in t['classes'].values()))
                     for t in mine]
            expected, seen = sum(e for e, _s in pairs), sum(s for _e, s in pairs)
            interval = day_interval([d for d in pairs if d != (0.0, 0.0)], ratio)
            classes[name] = {'expected': round(per_100(expected, read) or 0.0, 4),
                             'seen': round(per_100(seen, read) or 0.0, 4),
                             'ratio': round(seen / expected, 3) if expected > 0 else None,
                             'interval': [round(v, 3) for v in interval] if interval else None}
        budget, weight = sum(t['budget'][0] for t in mine), sum(t['budget'][1] for t in mine)
        out[surface] = {'n': sum(t['n'] for t in mine), 'words': sum(t['words'] for t in mine), 'read': round(read),
                        'lookups': sum(t['lookups'] for t in mine), 'none': sum(t['none'] for t in mine),
                        'days': len(mine), 'classes': classes, 'budget': round(budget / weight, 3) if weight else None}
    return out


def calibration_text(report):
    """fw.py calibrate: the report in plain lines."""
    lines = [f'Last {report["days"]} days, {report["passages"]} web passages with time on screen (normal intensity):']
    for b in report['bins']:
        if b['n']:
            share = f'{100 * b["low"]:4.0f}-{100 * b["high"]:3.0f}% Japanese' if b['high'] else 'no Japanese'.rjust(18)
            lines.append(f'  {share}: {b["n"]:4d} passages, '
                         f'{b["msPerWord"]:4d} ms on screen per word, {b["opensPer100"]:.2f} opens per 100 words')
    lines += thinning_text(report['jitter'], report.get('jitterBefore'))
    lines += classes_text(report.get('classes'), report['days'])  # knowledge.json from before has none
    for surface, name in (('chat', 'Chat replies'), ('browser', 'Web pages')):
        p = report['presses'][surface]
        at = lambda k: {str(intensity.OFF): 'off', str(intensity.FULL): 'at full'}.get(k, f'at {int(k):+d}')
        hours = ', '.join(f'{h} h {at(k)}' for k, h in p['hoursAt'].items()) or 'no time away from normal'
        verdict = f'; the default looks {p["verdict"]}' if p['verdict'] else ''
        n = p.get('threadsOff')
        chats = f', counting {n} chat{"s" if n != 1 else ""} with Japanese turned off' if n else ''
        lines.append(f'{name}: {p["lighter"]} lighter and {p["heavier"]} heavier presses ({hours}){chats}{verdict}.')
    return '\n'.join(lines)


def thinning_text(jitter, before=None):
    """calibration_text's lines on the random thinning: since it took free words only, then before (if any)."""
    lines = []
    if jitter['thinned']['n'] and jitter['untouched']['n']:
        t, u = jitter['thinned'], jitter['untouched']
        lines.append(f'Randomly thinned passages (thinning free words only, since 2026-10-20): {t["n"]} at '
                     f'{100 * t["share"]:.1f}% Japanese, {t["msPerWord"]} ms/word; untouched: {u["n"]} at '
                     f'{100 * u["share"]:.1f}%, {u["msPerWord"]} ms/word.')
        if jitter['effect'] is not None:
            lines.append(f'  Each extra point of Japanese known at a glance changes screen time per word by about '
                         f'{jitter["effect"] / 100:+.1%}.')
        elif jitter.get('gap') is not None:  # enough of both, but the thinning took too little Japanese off
            gap = 100 * jitter['gap']
            lines.append(f'  Thinned passages have only {gap:.1f} points less Japanese than untouched ones: too close to '
                         f'measure the effect ({100 * THIN_MIN_GAP:.1f} needed).' if gap > 0 else
                         f'  Thinned passages have no less Japanese than untouched ones ({-gap:.1f} points more): the '
                         "effect can't be measured yet.")
        else:
            lines.append(f'  Not enough of either yet to measure the effect ({THIN_MIN_PASSAGES} each).')
    else:
        lines.append('No randomly thinned passages since thinning took free words only (extension 0.9.0, 2026-10-20).')
    if before and before['thinned']['n'] and before['untouched']['n']:
        t, u = before['thinned'], before['untouched']
        effect = (f'; about {before["effect"] / 100:+.1%} screen time per word per point of Japanese'
                  if before['effect'] is not None else '')
        lines.append(f'  Before, when thinning scaled the budget too: {t["n"]} thinned at {100 * t["share"]:.1f}% '
                     f'Japanese, {t["msPerWord"]} ms/word; {u["n"]} untouched at {100 * u["share"]:.1f}%, '
                     f'{u["msPerWord"]} ms/word{effect}.')
    return lines


CLASS_NAMES = {'costly': 'costly words', 'free': 'free words', 'topical': 'topical words',
               'sentences': 'whole sentences', 'offlist': 'off-list words', 'all': 'all lookups'}


def classes_text(classes, days):
    """calibration_text's lines for lookup_classes: per surface, then per class."""
    if not classes:
        return []
    low, high = CALIBRATED
    lines = [f'Lookups per 100 words read, last {days} days: expected by the model before each passage or message, '
             f'and seen (a glance counts {GLANCE_Y}); seen/expected with a 90% interval from resampling days '
             f'(calibrated: inside {low}-{high}).']
    for surface, name, unit in (('browser', 'Web', 'passages'), ('t3', 'Chat', 'messages')):
        c = classes[surface]
        if not c['n']:
            lines.append(f'  {name}: no {unit} with Japanese yet.')
            continue
        budget = f'; budget {c["budget"]:.2f}' if c['budget'] is not None else '; no budget (full)'
        lines.append(f'  {name}: {c["n"]} {unit} ({c["none"]} with no lookup) over {c["days"]} days, '
                     f'{c["words"]} words shown, {c["read"]} read, {c["lookups"]} lookups{budget}:')
        for key in CLASSES + ('all',):
            k = c['classes'][key]
            label = f'    {CLASS_NAMES[key] + ":":<17}'
            if key == 'topical' and not k['expected'] and not k['seen']:
                lines.append(f'{label}none yet (chat has no topical words before the dictionary)')
                continue
            if k['ratio'] is None:
                verdict = ': not priced yet' if key == 'sentences' else ''
            elif k['interval']:
                verdict = f': {k["ratio"]:.2f} ({k["interval"][0]:.2f}-{k["interval"][1]:.2f})'
            else:
                verdict = f': {k["ratio"]:.2f} (no interval before {MIN_DAYS} days)'
            lines.append(f'{label}{k["expected"]:.3f} expected, {k["seen"]:.3f} seen{verdict}')
    return lines


def unlisted(rows, listed, now_ms, days=UNLISTED_DAYS):
    """Opened words with no lexicon entry over the last `days` days, most opened first: what the chat's Japanese
    sentences use that the word list doesn't have. {days, opens, words: number of such words, top: [[word, opens]]}.
    Sentence translations and T3 peeks under T3_NOISE_MS don't count."""
    match, since, counts = matcher(listed), now_ms - days * DAY_MS, {}
    for row in rows:
        if row['at'] < since or is_sentence(row['word']):
            continue
        if row['surface'] == 't3' and row['kind'] == 'peek' and row['dwell_ms'] < T3_NOISE_MS:
            continue
        if match(row['word']) is None:
            counts[row['word']] = counts.get(row['word'], 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:UNLISTED_TOP]
    return {'days': days, 'opens': sum(counts.values()), 'words': len(counts), 'top': [list(kv) for kv in top]}


def unlisted_text(summary):
    """fw.py calibrate: the opened words the lexicon doesn't have, in one line."""
    if not summary or not summary.get('opens'):
        return f'Every word opened in the last {(summary or {}).get("days", UNLISTED_DAYS)} days is in the word list.'
    top = ', '.join(f'{word} {n}' for word, n in summary['top'])
    return (f'Opened but not in the word list, last {summary["days"]} days ({summary["opens"]} opens of '
            f'{summary["words"]} words): {top}')


# ---- the challenge: normal's lookup budget ----

def challenge_of(state):
    value = (state.get('prefs') or {}).get('challenge', 0)
    return value if valid_challenge(value) else 0


def valid_challenge(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value in CHALLENGES


# ---- labels, intake and probes: the refresh's state edit ----

SPENT_SHARE = 0.8          # a surface whose plans asked to spend under this share of their budget left it unspent
SPEND_HOURS = 48           # over its plans of this many hours
SPEND_MIN = 5              # and only with at least this many plans with a budget (web passages or chat briefs)
PROBE_READ = 0.1           # a probe's showing counts once its read chance from time on screen is this (or it was opened)


def spending(contexts, briefs, now_ms):
    """Per surface, what its plans of the last SPEND_HOURS asked to spend against their budget, in lookups if read:
    {surface: {plans, budget, spent, share}}. The web: each passage's plan (its shown record), its budget and spent
    (what its budgeted swaps cost; plans from before extension 0.10 have none, so their expected, which counts every
    listed word shown, stands in: an upper bound); chat: each brief (feedback.chat_briefs), its budget and expected,
    per 100 words. Full has no budget and doesn't count."""
    since = now_ms - SPEND_HOURS * 3_600_000
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    out = {surface: {'plans': 0, 'budget': 0.0, 'spent': 0.0} for surface in feedback.SURFACES}
    for c in contexts.values():
        plan = c.get('plan')
        if c['surface'] != 'browser' or not isinstance(plan, dict) or (c.get('shown_at') or 0) < since:
            continue
        spent = plan.get('spent', plan.get('expected'))
        if number(plan.get('budget')) and number(spent):
            web = out['browser']
            web['plans'], web['budget'], web['spent'] = web['plans'] + 1, web['budget'] + plan['budget'], \
                web['spent'] + spent
    for b in briefs:
        if b['at'] >= since and number(b.get('budget')) and number(b.get('expected')):
            chat = out['t3']
            chat['plans'], chat['budget'], chat['spent'] = chat['plans'] + 1, chat['budget'] + b['budget'], \
                chat['spent'] + b['expected']
    for v in out.values():
        v['share'] = v['spent'] / v['budget'] if v['budget'] > 0 else None
        v['budget'], v['spent'] = round(v['budget'], 3), round(v['spent'], 3)
    return out


def unspent(spend):
    """Whether the lookup budget goes unspent: on some surface with SPEND_MIN plans, they asked to spend under
    SPENT_SHARE of their budget, so more learning words would be shown. Without plans nothing is spent, but nothing is
    read either: no intake."""
    return any(v['plans'] >= SPEND_MIN and v['share'] is not None and v['share'] < SPENT_SHARE
               for v in spend.values())


def intake_room(state, spend, today):
    """How many new words come in today: what is left of NEW_PER_DAY while the budget goes unspent, else none."""
    return max(0, fwlib.NEW_PER_DAY - fwlib.taken_today(state, today)) if unspent(spend) else 0


def probe_outcomes(state, log, today):
    """[[ja, outcome]] for each word whose probe is due (fwlib.phase): its first showing on or after its probe day that
    was read (opened, or a read chance from time on screen of at least PROBE_READ), {at, surface, y, read}; 'missed'
    when there is none fwlib.PROBE_WAIT days on. Episodes don't count: they exist only because something was opened."""
    due = {w['ja']: day_start(w['arm']['probe']) for w in state['words'] if fwlib.phase(w, today) == 'probe'}
    found = {}
    for event in log.events:
        if not hasattr(event, 'obs') or event.episode:
            continue
        for o in event.obs:
            if o.word in due and o.word not in found and event.at >= due[o.word] and (o.y > 0 or o.read >= PROBE_READ):
                found[o.word] = {'at': int(event.at), 'surface': o.surface, 'y': round(o.y, 3),
                                 'read': round(max(o.read, READ_FLOOR) if o.y > 0 else o.read, 4)}
    out = [[ja, outcome] for ja, outcome in found.items()]
    out += [[w['ja'], 'missed'] for w in state['words'] if w['ja'] in due and w['ja'] not in found
            and fwlib.days_between(w['arm']['probe'], today) >= fwlib.PROBE_WAIT]
    return out


def decide(state, flags, looked, spend, log, today, seed=None, nominated=None):
    """The refresh's state edit, as a labels op (hub.py, fwlib.apply_labels): every word's label from its free flag
    (fwlib.label) where it changes, up to intake_room new words (fwlib.newcomers: looked up first, then the words your
    reading nominates, else the list's order; never one known at a glance) each with its delayed-start arm
    (fwlib.assign, from the experiment's seed, or a new seed when it starts; none for a word the fixed texts name,
    fwlib.IN_THE_RULES) and, for a dictionary word the state doesn't have yet, its entry id, and the due probes'
    outcomes (probe_outcomes). flags: {ja: free}; looked: {ja: messages opened in} (fwlib.looked_up's); nominated:
    the words from your reading (reading.nominations). Nothing when labels come from the rules (fwlib.restore)."""
    op = {'kind': 'labels', 'labels': [], 'intake': [], 'probes': []}
    if (state.get('labels') or {}).get('from') == 'rules':
        return op
    for w in state['words']:
        to = fwlib.label(w, flags.get(w['ja'], False))
        if to != w['status']:
            op['labels'].append([w['ja'], w['status'], to])
    room = intake_room(state, spend, today)
    if room:
        experiment = state.get('experiment') if isinstance(state.get('experiment'), dict) else None
        if experiment is None:
            seed = seed if seed is not None else random.SystemRandom().randrange(2 ** 32)
            op['experiment'] = {'seed': seed}
        else:
            seed = experiment['seed']
        free = {ja for ja, f in flags.items() if f}
        have = {w['ja'] for w in state['words']}
        for w in fwlib.newcomers(state, looked, free, nominated)[:room]:
            outside = w['ja'] in fwlib.IN_THE_RULES  # a word the fixed texts name can't be held back
            op['intake'].append([w['ja'], *((None, None) if outside else fwlib.assign(seed, w['ja']))]
                                + ([w['id']] if w['ja'] not in have and w.get('id') else []))
    op['probes'] = probe_outcomes(state, log, today)
    return op


# ---- refresh ----

def path():
    return fwlib.home() / KNOWLEDGE


def load():
    return fwlib.read_json(path(), {}) or {}


def is_stale(knowledge=None, now=None):
    updated = (knowledge if knowledge is not None else load()).get('updated', 0)
    return (now if now is not None else time.time()) - updated > STALE_S


MODEL = 'fit 2'  # the reading model is chosen again when this changes (knowledge.json "reading")


def word_stats(log, now_ms):
    """Per word, what the log says besides the fit: {ja: {obs, opened, recent (times of its opens in the last
    RECENT_DAYS days), last (its last observation or command), history (its last 30 observations: at, surface, y, w),
    command_at}}."""
    out = {}
    for event in log.events:
        if not hasattr(event, 'obs'):
            s = out.setdefault(event.word, {'obs': 0, 'opened': 0, 'recent': [], 'last': None, 'history': []})
            s['command_at'] = s['last'] = event.at
            continue
        for o in event.obs:
            s = out.setdefault(o.word, {'obs': 0, 'opened': 0, 'recent': [], 'last': None, 'history': []})
            s['obs'] += 1
            s['last'] = event.at
            if o.y > 0:
                s['opened'] += 1
                if event.at >= now_ms - RECENT_DAYS * DAY_MS:
                    s['recent'].append(event.at)
            s['history'].append({'at': event.at, 'surface': event.surface, 'y': o.y, 'w': round(o.weight, 2)})
            del s['history'][:-30]
    return out


def prior_table(fit):
    """What a word without evidence assumes, by kind (knowledge.json "prior"), for prior_estimate: k's mean with an Anki
    card, by origin (origin_at), plus interval times ln(1 + days); without one, by origin; the sd of each kind; and
    for a word of an origin in featured, plus the word features' coefficients (words, word_features) with what they
    are measured from: the kanji known at a glance (kanji, every kanji of the free words with evidence the fit took)
    and the reference word's rank and length (centre)."""
    shared = fit.shared()
    return {'card': {s: shared[f'card {s}'] for s in ORIGINS}, 'interval': shared['interval'],
            'none': {s: shared[s] for s in ORIGINS}, 'sd': {kind: round(w, 3) for kind, w in fit.widths.items()},
            'words': {name: shared[name] for name in WORD_FEATURES}, 'featured': list(FEATURED),
            'centre': {'rank': RANK_CENTRE, 'length': LENGTH_CENTRE}, 'kanji': ''.join(sorted(fit.known))}


def refresh(now=None):
    """Fit the gloss log (Fit), write knowledge.json, and make the state edit that follows (decide): every word's label
    from the model, the day's new words while the budget goes unspent, with their delayed-start arms, and the probes'
    outcomes, as one labels op (hub.run, so the ops log keeps it). One at a time; returns the new knowledge, or None
    when another refresh is running. With a collector set, the collector does this and the cache here is refreshed
    instead (hub.py).

    knowledge.json: per word of the word state, k and its sd (the fit's, else its prior's), mu (its prior mean), free
    (known at a glance, with hysteresis against the last refresh's flag: free_flag), n (evidence weight), obs, opened,
    recent (opens in the last RECENT_DAYS days), last and history, and id (the JMdict id of its dictionary entry,
    word_entries, which the next refresh reuses); offset (the web's 0, chat's fitted); fit (the
    shared parameters with their sd, the prior widths); prior (prior_table); sentencePrice per surface (sentence_prices;
    sentences has their counts); reading (tune, once a day); calibration, with daily, its sums for each day before
    today (day_totals), reused while the day's key holds (day_keys), and dailyFree, the free flags at the start of the
    last of those days, which today's records carry on from; unlisted; spend (spending: the budget's use that sets
    intake); moves (the label changes made, the last 200)."""
    if fwlib.collector_url():
        import hub
        ok, error = hub.sync(scan=False)
        return dict(load(), synced=ok, syncError=error)
    import evaluate  # the log as the fit reads it (evaluate.extract); evaluate imports this module
    now = now if now is not None else time.time()
    now_ms, today = int(now * 1000), time.strftime('%Y-%m-%d', time.localtime(now))
    with fwlib.locked(LOCK, blocking=False) as got:
        if not got:
            return None
        rows, contexts = feedback.load(feedback.default_path())
        state = fwlib.load_state()
        previous = load()
        match = inflection.lexicon(state['words']).entry  # every lexicon word, queue words too
        intervals, commands = anki_intervals(), read_commands()
        reading = previous.get('reading')
        fresh = isinstance(reading, dict) and reading.get('day') == today and reading.get('model') == MODEL
        pace, top = (reading['pace'], reading['top']) if fresh else (PACE_MS, READ_MAX)
        log = evaluate.extract(rows, contexts, state['words'], intervals, commands, now_ms, pace, top)
        known = previous.get('words') if isinstance(previous.get('words'), dict) else {}
        before = {ja: e['free'] for ja, e in known.items() if isinstance(e, dict) and isinstance(e.get('free'), bool)}
        log.entries = word_entries(state['words'], {ja: e['id'] for ja, e in known.items() if isinstance(e, dict)
                                                    and isinstance(e.get('id'), int)})
        if not fresh:  # the reading model, once a day
            reading = dict(tune(log, now_ms, before), day=today, model=MODEL)
            pace, top = reading['pace'], reading['top']
        start = None
        if (previous.get('fit') or {}).get('model') == MODEL:  # start from the last refresh's fit
            start = {'shared': previous['fit'].get('shared'), 'widths': previous['fit'].get('widths'),
                     'k': {ja: e['k'] for ja, e in known.items() if isinstance(e, dict) and e.get('n')}}
        fit = Fit(log, now_ms, pace, top, start=start, free=before)
        stats = word_stats(log, now_ms)
        flags = free_flags(fit, [w['ja'] for w in state['words']], before)
        sentences = sentence_events(rows, contexts, now_ms, pace, top)
        prices = sentence_prices(sentences, now_ms)
        changes, challenge = feedback.intensity_changes(feedback.default_path()), challenge_of(state)
        since = now_ms - CALIBRATION_DAYS * DAY_MS
        keys = day_keys(log, rows, changes, since, now_ms, pace, top, challenge)
        cached = previous.get('daily') if isinstance(previous.get('daily'), dict) else {}
        done = {day: cached[day]['totals'] for day, key in keys.items()
                if isinstance(cached.get(day), dict) and cached[day].get('key') == key}
        # the free flags to carry: at the start of the last day summed, else the last refresh's
        carried = previous.get('dailyFree') if isinstance(previous.get('dailyFree'), dict) else {}
        flags_then = carried.get('free') if carried.get('day') in done and isinstance(carried.get('free'), dict) else \
            before
        records, starts = fitted_records(rows, contexts, match, log, since, now_ms, pace, top, sentences, flags_then,
                                         fit.state(), set(done))
        totals = dict(done, **day_totals(records, changes, challenge))
        daily = {day: {'key': key, 'totals': totals[day]} for day, key in keys.items() if day in totals}
        last = max((day for day in starts if day in daily), default=None)
        daily_free = {'day': last, 'free': {ja: f for ja, f in starts[last].items() if ja in fit.index}} if last \
            else carried if carried.get('day') in daily else {}
        words = {}
        for w in state['words']:
            ja = w['ja']
            k, sd = fit.estimate(ja)
            words[ja] = {'k': round(k, 3), 'sd': round(sd, 3), 'mu': round(fit.prior(ja)[0], 3), 'free': flags[ja],
                         'n': 0.0, 'obs': 0}
            if log.entries.get(ja):
                words[ja]['id'] = log.entries[ja]['id']
            s, i = stats.get(ja), fit.index.get(ja)
            if s:  # a word the log has something on
                words[ja].update(n=round(fit.n[i], 2) if i is not None else 0.0, obs=s['obs'], opened=s['opened'],
                                 recent=len(s['recent']), last=s['last'], history=s['history'])
        shrank = len(rows) < previous.get('rows', 0)  # a truncated mirror must not move words
        spend = spending(contexts, feedback.chat_briefs(feedback.default_path()), now_ms)
        looked = {ja: len(st['recent']) for ja, st in stats.items() if len(st['recent']) >= fwlib.LOOKED_UP}
        try:  # words from your reading (reading.py); without its counts, the list's order
            import reading as counts  # refresh's `reading` is the reading model
            nominated = counts.nominations(state, fwlib.read_json(fwlib.home() / 'exposure.json', {}) or {},
                                            {'words': words, 'prior': prior_table(fit)}, today) \
                if intake_room(state, spend, today) else None
        except Exception as error:
            print(f'Japanese Weave: no words from your reading ({error.__class__.__name__}: {error})', file=sys.stderr)
            nominated = None
        op = decide(state, flags, looked, spend, log, today, nominated=nominated) if not shrank else {}
        applied = []
        if op.get('labels') or op.get('intake') or op.get('probes') or (op and not state.get('labels')):
            import hub  # here: hub imports this module
            result = hub.run(hub.stamp(dict(op, day=today, at=now_ms)))
            if not result.get('ok'):
                print(f'Japanese Weave: labels not changed: {result.get("error")}', file=sys.stderr)
            applied = [{'day': today, 'word': ja, 'from': a, 'to': b} for ja, a, b in result.get('labels') or []]
        history = [m for m in previous.get('moves', []) if isinstance(m, dict)]
        knowledge = {
            'updated': now, 'rows': len(rows),
            'offset': {'browser': 0.0, 't3': round(fit.theta[T3], 3)},
            'fit': {'model': MODEL, 'shared': fit.shared(),
                    'sd': {name: round(sd, 4) for name, sd in zip(SHARED_NAMES, fit.theta_sd)},
                    'widths': {kind: round(w, 4) for kind, w in fit.widths.items()}, 'pace': pace, 'top': top,
                    'words': len(fit.words), 'terms': len(fit.terms[0]), 'iterations': fit.iterations,
                    'seconds': round(fit.seconds, 3)},
            'prior': prior_table(fit),
            'sentencePrice': {surface: v['p'] for surface, v in prices.items()}, 'sentences': prices,
            'challenge': challenge, 'spend': spend, 'moves': (history + applied)[-200:], 'reading': reading,
            'calibration': calibrate(rows, contexts, changes, now_ms, totals=totals, challenge=challenge),
            'daily': daily, 'dailyFree': daily_free,
            'unlisted': unlisted(rows, match, now_ms),
            'words': words,
        }
        fwlib.write_json(path(), knowledge)
        return knowledge


def start_refresh():
    """A background `fw.py refresh`, never waited on (the native host and the hook call this)."""
    if os.environ.get('JAPANESE_WEAVE_NO_REFRESH'):
        return
    import subprocess
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().with_name('fw.py')), 'refresh'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=dict(os.environ))


def word_estimate(knowledge, ja, surface='browser'):
    """(p, k, sd) for a word, or None when knowledge.json has nothing on it. p is its lookup chance on the surface,
    kept on its free flag's side of fwlib.FREE_P when it has one (free_flag): the flag, with its hysteresis, says
    whether the word is known at a glance, and the chance how likely a lookup is otherwise. So a planner comparing p
    with FREE_P (weave.lists) agrees with the flag."""
    entry = (knowledge.get('words') or {}).get(ja)
    if not entry:
        return None
    offset = (knowledge.get('offset') or {}).get(surface, 0.0)
    p = sigmoid(offset - entry['k'])
    if isinstance(entry.get('free'), bool):
        p = min(p, fwlib.FREE_P * 0.999) if entry['free'] else max(p, fwlib.FREE_P)
    return p, entry['k'], entry['sd']


def prior_estimate(knowledge, origin, interval=None, entry=None):
    """(k, sd) a word without an estimate in knowledge.json assumes, from its "prior" (prior_table): by its Anki
    interval and origin (origin_at: new, learning or known), else its origin; and, given its dictionary entry (any
    entry of data/dictionary.tsv: a word with no history is origin new) and an origin the table's featured has, by
    the entry's word features (word_features, with the table's kanji known and centre). None when knowledge.json has
    no prior. extension/src/prior.js computes the same (tests/prior_cases.json)."""
    table = knowledge.get('prior')
    if not isinstance(table, dict):
        return None
    origin = origin if origin in ORIGINS else 'new'
    x, kind = features(interval, origin)
    try:
        if entry is not None and origin in (table.get('featured') or ()):
            centre = table.get('centre') or {}
            x.update(word_features(entry, {c: 1 for c in table.get('kanji') or ''}, False,
                                   (centre.get('rank', RANK_CENTRE), centre.get('length', LENGTH_CENTRE))))
        words = table.get('words') or {}
        mean = sum(value * (table['interval'] if name == 'interval' else
                            table['card'][name[5:]] if name.startswith('card ') else
                            table['none'][name] if name in ORIGINS else words.get(name, 0.0))
                   for name, value in x.items())
        return mean, table['sd'][kind]
    except (KeyError, TypeError, AttributeError):
        return None
