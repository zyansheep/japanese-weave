"""Adaptive difficulty: per-word knowledge estimated from the gloss interaction log (see docs/adaptive-difficulty.md).
Python 3.9+, standard library only.

Model: P(lookup | read) = sigmoid(offset_s + form + context s + recency_log_odds(k_w, u, floor)). k_w is the reader's
long-term knowledge of a word on a log-odds scale, its chance a week after the last read; offset_s is 0 on the web (the
reference) and fitted for chat; form is a fitted offset for a conjugated form of the word. The recency term (NEXT_STEPS
step 10): right after a read the odds fall to a fitted floor shared by every word and climb back to the word's
long-term odds over a week, u the share of the way back (since_read, from when the word was last read: read_clocks);
context is an offset for a repeat in the same page or T3 window, s the chance the last read was there. Free and known
are judged on the long-term chance, a lookup costs the current one (current_chance), and a costly word recency holds
under SPACING_P waits.
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
kanji known at a glance, script, part of speech, length, rarity and source language: word_features, NEXT_STEPS step 13),
so any dictionary entry has a starting value (prior_estimate; extension/src/prior.js); evidence weighs half as much
every EVIDENCE_DAYS; known and learn commands are one-sided pseudo-observations; a lookup of a conjugated form counts
FORM_WEIGHT; the monthly probe's answers (probe.py) are observations of their own kind with their own offset, corrected
for the over-claiming its made-up words show (probe_answers), and the probe's own estimate of the goal stays computed
from its answers alone, so it remains the check on the model; the review cards (review.py: a word alone, its reading and
meaning revealed on request, then graded as in Anki) are observations of their own kind too: a graded card of a real
word is an explicit answer (Again: not known at recall; Hard, Good, Easy: known), read as the probe's answers are, at
the review offset, unless it was a learning step's (counted_grades), and one a word a day; an ungraded one of the reveal
flow (the wedge, before a client grades) has its time to the reveal or its absence read as a mixture of known at recall
and not (Components, review_answers), whose made-up words pin the "not known" side, while the graded flow's ungraded and
made-up cards say nothing; and every one is a read for the recency clock. Each word's uncertainty is the fit's curvature
(Laplace). The reading model (pace and cap) is chosen once a day by profile likelihood over a grid, on both surfaces.
Each passage's or paragraph's time on screen can be evidence too (SPEED, off until the replay shows a gain: Fit's speed,
speed_units). The fit is the Fit class, which evaluate.py's "fit" model (evaluate.CURRENT) runs too; the online updates
it replaced are the Online class, kept as evaluate.py's "today" for comparison.

refresh() fits the whole log, writes knowledge.json (per word: k, its sd, and whether it is free: known at a glance,
with hysteresis), fits the price of a whole sentence per surface, and reports whether the default amount of Japanese
looks right and how the model's expected lookups per class compare with those seen, in lookups per 100 words read
(calibration), and which opened words the lexicon doesn't have (unlisted). Then it labels every word from the model
(known when free, learning once in use, else new), takes in the day's new words while the lookup budget goes unspent,
each with its delayed-start arm, and records the probes' outcomes (decide). It also fits the chat sentences' open
chance by length and grammar pattern and takes in the next grammar pattern (sentence_model.py, docs/grammar.md), and
leaves out T3 messages shown while English only was on.
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
import srs

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
SURFACES = ('browser', 't3')
# The reading model's grid, chosen by profile likelihood per surface (tune), the defaults at even positions, which the
# search scores first. On the live log of 2026-10-20 the web's best is 1,000 ms a word with a cap of 0.2 (10.7 log
# likelihood units over the defaults), outside the grid of 150 to 650 ms and 0.5 to 0.9 it had before: time on screen
# overstates reading on the web, where several passages are on screen at once and many pages are skimmed.
PACES = (150, 200, 300, 450, 650, 1000, 1500, 2200, 3300, 5000)
READ_MAXES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
# Opens on a surface before its tuned reading model is ready (the web had 32 on 2026-10-20, 15 of them on one page),
TUNE_MIN_OPENS = 60
TUNE_MARGIN = 3.0          # and how much more log likelihood than the defaults it needs (about a 95% test of two)
# A ready reading model is only reported (tune's ready), not used, until this is set: k is on the web's scale, so a
# new web reading model rescales every word and moves the free line, the chat offset and the one unit's denominator
# at once (on the 2026-10-20 snapshot, ready at 30 opens with web 1,000 ms and cap 0.2: the web's chance of every word
# rises about 2.7 times, 366 free words become 68, and the web's words read fall from 16,596 to 3,354). Which scale
# the free line is judged on is a decision to make first.
SURFACE_TUNING = False
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
    if context['surface'] == 't3':  # a message shown at -3 (English only) showed no Japanese: a word count only
        return context.get('words') is not None and (context.get('level') is None or context['level'] > intensity.OFF)
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


def by_surface(value, surface):
    """A reading-model parameter (pace or top) on a surface: the value, or its surface's when it is one per surface,
    {surface: value} (tune)."""
    return value[surface] if isinstance(value, dict) else value


def reading_text(pace, top):
    """A reading model in words: '300 ms a word, cap 0.7', or per surface 'web 1500 ms a word, cap 0.3; chat ...'."""
    if not isinstance(pace, dict) and not isinstance(top, dict):
        return f'{pace:g} ms a word, cap {top:g}'
    names = {'browser': 'web', 't3': 'chat'}
    return '; '.join(f'{names[s]} {by_surface(pace, s):g} ms a word, cap {by_surface(top, s):g}' for s in SURFACES)


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
    pace, top = by_surface(pace, surface), by_surface(top, surface)
    return lambda y, line: chance(context, y, line, pace, top)


def _read_share(ms, words, pace, top):
    """The mean read chance of the words of a passage or paragraph, over READ_STEPS evenly spaced depths."""
    return sum(_chance(ms, words, (i + 0.5) / READ_STEPS, pace, top) for i in range(READ_STEPS)) / READ_STEPS


def words_read(surface, context, pace=PACE_MS, top=READ_MAX):
    """The words of a passage or message, each weighted by its chance of having been read from time on screen
    (read_chance, stay_chance) at evenly spaced depths: the denominator of the one unit (per_100). A T3 message's
    paragraphs are the distinct bands of its stays, the longest stayed first where bands overlap (a paragraph whose
    layout changed), each with the words and ms stay_chance gives it; its words outside them (paragraphs without
    Japanese, which T3 doesn't watch) count at the mean read chance of the words it watched. pace and top: numbers,
    or per surface (by_surface)."""
    pace, top = by_surface(pace, surface), by_surface(top, surface)
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


def _in_english(notes, opened):
    """The indexes of the annotations read in English: those of a sentence whose whole-sentence translation (its end,
    a PUNCTUATION annotation) was opened, back to the previous sentence end."""
    excluded = set()
    for index in opened:
        if notes[index][0] in PUNCTUATION:  # a sentence translation: skip back to the previous sentence end
            start = max((i for i in range(index) if notes[i][0] in PUNCTUATION), default=-1)
            excluded.update(range(start + 1, index))
    return excluded


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
    excluded = _in_english(notes, opened)
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


def speed_units(surface, context, items, listed):
    """The reading times of a passage or message shown, for the speed term (Fit, SPEED): one per browser passage, one
    per paragraph of a T3 message (the distinct bands of its stays, the longest stayed first where bands overlap, as
    words_read takes them). Each is (ms, words, japanese, listed, active, dwell): active, its time on screen while a
    reader could have been reading it (its stays added: time away, idle or with media playing is already left out);
    dwell, the time its glosses were open (each open's, at most SPEED_DWELL_MS; T3 peeks under T3_NOISE_MS don't
    count), time spent on the gloss, not the text; ms, active less dwell; words, its word count (in T3 the
    paragraph's, as stay_chance takes it); japanese, its glossed words (sentence ends left out); listed, the lexicon
    entries of its glossed words, each once, opened or not: the time is the reading's, before anything was opened or
    not (the fit's lookups are already conditioned on it, through the read chance), so selecting the words left
    unopened would select those in passages read too briefly to open anything. A passage or paragraph with under
    SPEED_MIN_MS left, or without a word count, gives none: it wasn't read, or not long enough to say."""
    match = matcher(listed)
    notes = context['annotations']
    dwell = {}
    for row in items:  # the opens _opened counts, with their time open
        if surface == 't3' and row['kind'] == 'peek' and row['dwell_ms'] < T3_NOISE_MS:
            continue
        index = row['position'] if 0 <= row['position'] < len(notes) and notes[row['position']][0] == row['word'] \
            else next((i for i, n in enumerate(notes) if n[0] == row['word']), None)
        if index is not None:
            dwell[index] = dwell.get(index, 0) + min(max(row['dwell_ms'] or 0, 0), SPEED_DWELL_MS)
    line = _line_height([n[2] for n in notes])

    def unit(indexes, active, words):
        if not words or words <= 0:
            return None
        spent = sum(dwell.get(i, 0) for i in indexes)
        if active - spent < SPEED_MIN_MS:
            return None
        found = []
        for i in indexes:
            word = match(notes[i][0])
            if word is not None and word not in found:
                found.append(word)
        japanese = sum(1 for i in indexes if not is_sentence(notes[i][0]))
        return active - spent, words, japanese, tuple(found), active, spent

    if surface != 't3':
        found = unit(range(len(notes)), context.get('active') or 0, context.get('words') or 0)
        return [found] if found else []
    stays = [s for s in context.get('stays') or [] if s['height'] > 0]
    bands = {}
    for s in stays:
        bands[(s['y'], s['height'])] = bands.get((s['y'], s['height']), 0) + s['ms']
    taken, out = [], []
    slack = min(line / 2, SLACK_PX)
    for (y, height), _ms in sorted(bands.items(), key=lambda kv: -kv[1]):
        if any(y < ty + th and ty < y + height for ty, th in taken):
            continue
        taken.append((y, height))
        ms, words, _share = paragraph_reading(context, y, line)
        found = unit([i for i, n in enumerate(notes) if y - slack <= n[2] < y + height], ms, words)
        if found:
            out.append(found)
    return out


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
                    alone = max(0.05, read_chance(context, _y_of(context, ja, match), line,
                                                  by_surface(pace, surface), by_surface(top, surface)))
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
# A word in a T3 message is topical when a chat brief offered it as topical (weave.topical_words) in this long before
# the message was shown. Nothing links a T3 message to the chat its brief was for: T3 logs a window (view) and a
# message id (context), the brief op the Claude Code session id and the time, so the link is the word and the time.
# The hook writes a brief as each prompt is sent, and on the user's 2,995 prompts with a reply (2026-10-21) the
# reply's last text came within 30 minutes of the prompt for 90% and within an hour for 95%; a topical word comes from
# the conversation's own turns, so one offered in another chat within the hour and used off the lists here is rare.
TOPICAL_WINDOW_MS = 3_600_000


class Offers:
    """The topical words chat briefs offered (feedback.chat_briefs' topical, [[ja, p]]), for calibrate's topical class
    (shown_record): price(keys, at) is (ja, p) of the latest brief in the TOPICAL_WINDOW_MS before `at` that offered
    one of keys (spellings of the word as written), else None; p is the brief's own price, the chat's lookup chance it
    offered the word at. entry(written): the dictionary entry's spelling of a word as written (dictionary.find, the
    dictionary loaded on first use), so a conjugated form or another spelling of an offered entry counts."""

    def __init__(self, briefs, window=TOPICAL_WINDOW_MS):
        number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
        self.window, self.by_word, self.times, self._d = window, {}, [], None
        for b in briefs or ():
            for row in b.get('topical') or ():
                if isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and number(row[1]) \
                        and number(b.get('at')):
                    self.by_word.setdefault(row[0], []).append((b['at'], row[1]))
                    self.times.append(b['at'])
        self.times.sort()

    def active(self, at):
        """Whether any brief offered a topical word in the window before `at`."""
        import bisect
        i = bisect.bisect_right(self.times, at)
        return i > 0 and self.times[i - 1] >= at - self.window

    def entry(self, written):
        if self._d is None:
            import dictionary  # here: only a T3 message shown after a topical offer needs it
            self._d = dictionary.load() if dictionary.PATH.exists() else False
        e = self._d.find(written) if self._d else None
        return e['ja'] if e else None

    def price(self, keys, at):
        best = None
        for ja in keys:
            for t, p in self.by_word.get(ja, ()):
                if at - self.window <= t <= at and (best is None or t > best[0]):
                    best = (t, ja, p)
        return best[1:] if best else None


def shown_record(at, surface, context, items, model, match, pace=PACE_MS, top=READ_MAX, offers=None):
    """One passage or message for calibrate, as the model stood before it: {at, shown (when it was shown: its shown
    record, else at), surface, words (shown), level (the level a T3 message reports it was shown at, else None),
    japanese (its annotations: none in a blank passage), read (words_read),
    lookups (its opens; T3 peeks under T3_NOISE_MS left out), budget (a browser plan's, per 100 words; else None),
    classes: {class: [expected, seen]}}. Expected is the read chance from time on screen times P(peek), each word at
    its best annotation, and seen its opens (a glance counts GLANCE_Y): listed words are free when the model says so
    (model.is_free, the free flag; without one, P(peek) under fwlib.FREE_P), else costly; a sentence translation
    (is_sentence) costs the model's price of a whole sentence (model.sentence_price, a chance per sentence read; without
    one it isn't priced); a word off the list costs its prior's P(peek). In a T3 message, a word a chat brief offered
    as topical in the TOPICAL_WINDOW_MS before it was shown (offers: an Offers, by the word as written, its listed
    word or its dictionary entry) is topical whether listed or not, at the price the brief offered it at. model:
    chance(word, surface, at), and maybe is_free(word) and sentence_price(surface)."""
    notes = context['annotations']
    line = _line_height([n[2] for n in notes])
    read = reader(surface, context, pace, top)
    opened = _opened(surface, notes, items)
    shown = context.get('shown_at') or at
    topical = offers if surface == 't3' and offers is not None and offers.active(shown) else None
    units, prices = {}, {}  # (kind, word or sentence index) -> [best read chance, seen]; topical word -> price
    for index, (written, _x, y) in enumerate(notes):
        if is_sentence(written):
            unit = ('sentences', index)
        else:
            ja = match(written)
            found = topical.price({written, ja, topical.entry(written)} - {None}, shown) if topical else None
            if found:
                unit = ('topical', found[0])
                prices[found[0]] = found[1]
            else:
                unit = ('listed', ja) if ja is not None else ('offlist', written)
        best = units.setdefault(unit, [0.0, 0.0])
        best[0] = max(best[0], read(y, line))
        best[1] = max(best[1], opened.get(index, 0.0))
    is_free = getattr(model, 'is_free', None)
    price = getattr(model, 'sentence_price', lambda _surface: 0.0)(surface) or 0.0
    classes = {name: [0.0, 0.0] for name in CLASSES}
    for (kind, word), (chance, seen) in units.items():
        p = price if kind == 'sentences' else prices[word] if kind == 'topical' else model.chance(word, surface, at)
        if kind == 'listed':
            kind = 'free' if (is_free(word) if is_free else p < fwlib.FREE_P) else 'costly'
        classes[kind][0] += chance * p
        classes[kind][1] += seen
    words = context.get('words') or 0
    plan = context.get('plan') or {}
    budget = plan.get('budget') if surface == 'browser' else None
    noise = lambda r: surface == 't3' and r['kind'] == 'peek' and r['dwell_ms'] < T3_NOISE_MS
    return {'at': at, 'shown': shown, 'surface': surface, 'words': words, 'level': context.get('level'),
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
# (the web's is 0, the reference: k is on the web's scale) and a conjugated form's. The recency term's
# (recency_log_odds): the floor, the log odds of a lookup right after a read, centred above every word (no recency: the
# term enters only as the reader's own log shows it), and the context offset for a repeat in the same page or window.
# The monthly probe's (probe): its answers are observations of their own kind (probe_answers), recognition out of
# context, answered on purpose, so they get their own offset on the web's scale, centred on none and free to move by
# about the lookup habit (PLAN.md row R: a word not recognised in reading is looked up only some of the time).
# The review cards' (review): P(known at recall) = sigmoid(k - review) (Fit._review_terms), recall of the word alone,
# its own offset on the web's scale, centred on none.
# Then a word's prior mean, a regression on its features (features): with an Anki card, an intercept by origin
# (origin_at; the names say new, learning and known) plus a slope on ln(1 + interval), centred on the old prior
# 0.5 + 0.8 ln(1 + interval); without one, an intercept by origin, centred on the old neutral prior (1.0) and, for words
# marked known, the planner's fwlib.KNOWN_PRIOR_K; for a word not marked known, plus its word features.
SHARED = {'t3': (0.0, 0.5), 'form': (0.0, 1.0), 'floor': (1.0, 1.5), 'context': (0.0, 0.5), 'probe': (0.0, 1.0),
          'review': (0.0, 1.0),
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
T3, FORM, FLOOR, CONTEXT, PROBE, REVIEW = (SHARED_NAMES.index(name) for name in ('t3', 'form', 'floor', 'context',
                                                                                  'probe', 'review'))
JOINED = (T3, FORM, FLOOR, CONTEXT, PROBE, REVIEW)  # the shared parameters an observation's chance depends on
ORIGINS = fwlib.ORIGINS  # the prior's intercepts: how a word came into use (origin_at)
WIDTHS = {'card': 1.0, 'none': 1.5}  # the prior sd of k around its mean, by kind: fitted, and held toward these
WIDTH_WEIGHT = 5                     # with this many words' worth of weight


# Recency (NEXT_STEPS step 10): right after a word is read its lookup chance falls to a floor shared by every word, and
# climbs back to the word's long-term chance over LONG_DAYS (recency_log_odds); a repeat in the same page or T3 window
# gets an offset of its own (context). k is the long-term knowledge: the free line and the status moves judge on it,
# and a lookup costs the current chance.
LONG_DAYS = 7              # the long-term chance is the chance a week after the last read
SOFT_MIN = 8               # how sharply the floor gives way to a word known better than it (recency_log_odds)
CLOCK_EPS = 0.01           # the last-read chances stop once no read in the newer showings is less likely than this
CONTEXT_MS = 3_600_000     # an earlier showing in the same page view or T3 window this recent is the same context
CLOCK_PASSES = 2           # fits from nothing, each weighing the clock's showings by the chances the one before found
NO_FLOOR = 1.0             # a fit with nothing to start from starts from this floor (log odds), above every word,
LOW_FLOOR = math.log(0.02) # and from this one: a lookup 2% of the time right after a read (the simulated reader's)
FLOOR_STEP = 0.5           # the most the floor moves in one Newton step
SPACING_P = 0.10           # a costly word waits while recency holds its chance under this and its long-term chance
                           # isn't (PLAN.md section 5, rule 2; under it all along, it is cheap and never waits)

# Reading speed as evidence (NEXT_STEPS step 19; docs/adaptive-difficulty.md, Reading speed as evidence). Every
# passage's or paragraph's time on screen (speed_units: its stays added, the glosses' time taken off) is an observation
# of its own: log ms is a Student-t with SPEED_NU degrees of freedom and a scale per surface (spread) around
#     pace_s + group + length_s ln(words / SPEED_WORDS) + (japanese J + unknown U) / words
# J its glossed words, U the sum over its lexicon words of sigmoid(-k), their long-term chance of a lookup: how many
# words it held that the reader doesn't know. So a slow paragraph moves its words toward unknown and a fast one toward
# known, by unknown q (1 - q) / words each. This is the time given the words, P(time | k); the lookups' likelihood is
# already given the time (their read chances come from it), so the two multiply into the joint. group is the page's
# (a browser view) or the T3 message's own pace, a random intercept held toward 0 by an sd fitted by EM (toward
# SPEED_GROUP_SD, with SPEED_GROUP_WEIGHT groups' worth of weight): the passages of a page, and a message's
# paragraphs, are on screen together, so their times move together and say little about which of them was slow.
# japanese and unknown are in words of reading time per word (unknown 10: a word sure to be unknown costs as long as
# reading ten more). Only passages and paragraphs on screen long enough to have been read through count (SPEED_THROUGH).
# The term's parameters are fitted with the words held (Fit._speed_alone), unknown is held toward 0 by its prior, and
# each paragraph is tempered so that time gives no word more than SPEED_CAP times the information its lookups and
# prior give it: time on screen can't outweigh the lookups. Off (SPEED False) until the replay shows it predicts lookups better on the live log
# (docs/adaptive-difficulty.md says what result switches it on); `fw.py evaluate --models nospeed,speed` compares.
SPEED = False
SPEED_NU = 4               # the Student-t's degrees of freedom on log time: skims and pages left open are its tails
SPEED_WORDS = 20           # the reference length of the pace intercepts
SPEED_MIN_MS = MIN_ACTIVE_MS  # a passage or paragraph with less time than this (glosses' time off) gives no evidence
SPEED_DWELL_MS = 30_000    # an open's gloss time taken off its paragraph's time, at most
SPEED_THROUGH = 1.0        # a passage or paragraph on screen for less than this times the time reading it takes at
                           # the reading model's pace was not read through: its time says how much of it was read, not
                           # how fast (on the simulated reader with 40 words a word not recognised, counting them cost
                           # new words' first exposures 0.0014 a day; left out, nothing)
SPEED_WEIGHT = 1.0         # how much a passage's or paragraph's time counts, against an observation's 1
SPEED_CAP = 1.0            # the most information time may give a word, against what its lookups and prior give it
SPEED_GROUP_SD = 0.5       # the prior sd of a page's or message's own pace on log time: fitted, held toward this
SPEED_GROUP_WEIGHT = 5     # with this many groups' worth of weight
SPEED_SHARED = {'pace web': (math.log(SPEED_WORDS * PACE_MS), 2.0), 'pace chat': (math.log(SPEED_WORDS * PACE_MS), 2.0),
                'length web': (1.0, 1.0), 'length chat': (1.0, 1.0), 'japanese': (0.0, 5.0), 'unknown': (0.0, 10.0),
                'spread web': (0.0, 1.0), 'spread chat': (0.0, 1.0)}
SPEED_NAMES = tuple(SPEED_SHARED)


def last_reads(history, at, view=None):
    """When a word was last read before `at`, as chances: ([(shown, chance, same)] newest first, rest). chance is the
    chance that showing was the last one the word was read in: its read chance times the chance none of the newer ones
    was read; same, whether it was in the same context (view: the (surface, view) of the showing at `at`, and less than
    CONTEXT_MS before); rest, the chance none was read. history: the word's [(shown, read chance, (surface, view))]
    oldest first. Showings LONG_DAYS or more before `at` don't count: the word is back at its long-term chance by
    then, as if never read; nor do older ones once rest is under CLOCK_EPS."""
    out, rest = [], 1.0
    for shown, r, where in reversed(history):
        if shown >= at:
            continue
        if at - shown >= LONG_DAYS * DAY_MS or rest < CLOCK_EPS:
            break
        out.append((shown, rest * r, where == view and at - shown < CONTEXT_MS))
        rest *= 1 - r
    return out, rest


def since_read(reads, rest, at):
    """(u, s) from last_reads: u, how far the word is back toward its long-term chance at `at`, the mean over when it
    was last read of min(1, time since / LONG_DAYS), never read (in the last LONG_DAYS) counting 1; s, the chance it
    was last read in the same context. The week is every word's: a memory strength per word (NEXT_STEPS step 16) would
    give each word its own time scale here, grown by the reads read_clocks weighs, and current_chance and levels.js
    currentChance would take it from knowledge.json with "read"."""
    u, s = rest, 0.0
    for shown, chance, same in reads:
        u += chance * min(1.0, max(0.0, (at - shown) / (LONG_DAYS * DAY_MS)))
        s += chance if same else 0.0
    return min(1.0, u), s


def recency_log_odds(k, u, floor):
    """The log odds of a lookup on the web (offset 0, the word as written) of a word whose long-term knowledge is k, a
    share u of the way back to its long-term chance since it was last read (since_read): odds m + (o - m) u, where
    o = e^-k is its long-term odds and m the floor's, e^floor, or o for a word known better than the floor (a smooth
    minimum, SOFT_MIN, so a word that well known has no recency). At u = 1 it is -k."""
    k = max(-30.0, min(30.0, k))
    if u >= 1.0:
        return -k
    o, c = math.exp(-k), math.exp(max(-30.0, min(30.0, floor)))
    m = (c ** -SOFT_MIN + o ** -SOFT_MIN) ** (-1 / SOFT_MIN)
    return math.log(m + (o - m) * u)


def read_weight(o, episode, pace, top, same=True):
    """An observation's read chance as the fit weighs it: 1 when opened, the line rule's alone in an episode, else the
    larger of the line rule and the time on screen at `pace` and `top` (same: they are the log's, so its weight)."""
    if o.y > 0:
        return 1.0
    if episode:
        return o.near
    return o.weight if same else max(o.near, _chance(o.ms, o.words, o.share, by_surface(pace, o.surface),
                                                      by_surface(top, o.surface)))


def showings_of(log, pace, top):
    """{word: [(shown, read chance, (surface, view), observation)]} over the whole log (log.origin), oldest first: every
    showing and episode a word was observed in, with its read chance at `pace` and `top` (read_weight), and every review
    card of a real word (review_log's shown), a read for certain with no observation (None): it shows the word alone,
    and its reveal the meaning; kept with the log."""
    origin = log.origin
    cache = origin.__dict__.setdefault('_showings', {})
    key = tuple(tuple(sorted(v.items())) if isinstance(v, dict) else v for v in (pace, top))
    if key not in cache:
        same = (pace, top) == (origin.pace, origin.top)
        out = {}
        for event in origin.events:
            if not hasattr(event, 'obs'):
                continue
            shown = getattr(event, 'shown', event.at)
            for o in event.obs:
                out.setdefault(o.word, []).append((shown, read_weight(o, event.episode, pace, top, same), event.key[:2],
                                                   o))
        for at, ja in review_log(origin)[3]:
            out.setdefault(ja, []).append((at, 1.0, ('review', ''), None))
        for items in out.values():
            items.sort(key=lambda h: h[0])
        cache[key] = out
    return cache[key]


def read_clocks(log, pace, top, chance):
    """{id(observation): (u, s)} for every observation of the whole log: since_read at its showing's time, in its
    context, from the word's showings before it in the log. Each counts by the chance it was read given what happened
    there: 1 when opened, else r (1 - q) / (1 - r q), r its read chance (read_weight) and q = chance(observation, u, s)
    its P(lookup | read) at its own clock, so a showing of a word the reader didn't know, left unopened, was most likely
    skipped and doesn't reset the clock. A review card (no observation) is a read."""
    found = {}
    for items in showings_of(log, pace, top).values():
        history = []
        for shown, r, where, o in items:
            if o is None:
                history.append((shown, r, where))
                continue
            u, s = since_read(*last_reads(history, shown, where), shown)
            found[id(o)] = u, s
            if o.y <= 0:
                q = chance(o, u, s)
                r = r * (1 - q) / (1 - r * q)
            history.append((shown, r, where))
    return found


def word_reads(log, fit, now):
    """{word: (reads, rest)}: last_reads at `now` for every word of the log, its showings weighed as read_clocks weighs
    them by the fit's chances, in no context (a new page or reply)."""
    chance = fit._chance()
    out = {}
    for word, items in showings_of(log, fit.pace, fit.top).items():
        history = []
        for shown, r, where, o in items:
            if shown >= now:
                break
            if o is not None and o.y <= 0:
                u, s = since_read(*last_reads(history, shown, where), shown)
                q = chance(o, u, s)
                r = r * (1 - q) / (1 - r * q)
            history.append((shown, r, where))
        out[word] = last_reads(history, now)
    return out


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
    """The words with evidence in a log before `until`: an observation, a command, a probe answer or a review card."""
    out = set()
    for event in log.events:
        if event.at >= until:
            break
        if hasattr(event, 'obs'):
            out.update(o.word for o in event.obs)
        else:
            out.add(event.word)
    out.update(ja for at, ja, _y in probe_log(log)[0] if at < until)
    out.update(ja for at, ja, _x in review_log(log)[0] if at < until)
    out.update(ja for at, ja, _y in review_log(log)[4] if at < until)
    return out


OVERCLAIM_DAYS = 365       # the over-claiming rate pools the made-up words of the probes this long before a fit
                           # (probe.FAKE_DAYS)


def probe_answers(probes, entries, d=None):
    """The monthly probes' answers (state["probes"], probe.py's records) as the fit reads them: (answers, fakes,
    extra). answers: [(at, ja, y)], one per real word answered, y 1 when answered not known (the event the fit counts,
    as it counts a lookup) and 0 known; skipped words are left out, and so are the made-up words, which only say how
    often the reader claims a word not known. ja is the word state's word with the answered entry (entries: {ja: its
    dictionary entry}, knowledge.word_entries of the word state; by JMdict id, else by spelling), else the entry's
    own spelling, and extra has that entry ({ja: entry}, from the dictionary d) for the word features. fakes:
    [(at, claimed, answered)] per probe, for the over-claiming rate (overclaiming). A record that isn't one is left
    out whole."""
    by_id, by_key = {}, {}
    for ja, e in (entries or {}).items():
        if e is not None:
            by_id.setdefault(e['id'], ja)
        by_key.setdefault(fwlib.key(ja), ja)
    answers, fakes, extra = [], [], {}
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    for p in probes or ():
        if not isinstance(p, dict) or not number(p.get('at')):
            continue
        rows = [r for r in p.get('words') or () if isinstance(r, list) and len(r) == 5 and isinstance(r[0], str)
                and isinstance(r[1], int) and r[4] in (0, 1)]
        made_up = [r[1] for r in p.get('fakes') or () if isinstance(r, list) and len(r) == 2 and r[1] in (0, 1)]
        fakes.append((p['at'], sum(made_up), len(made_up)))
        for spelling, id_, _band, _weight, answer in rows:
            ja = by_id.get(id_) or by_key.get(fwlib.key(spelling))
            if ja is None:
                if d is None:
                    import dictionary  # here: only a probe of a word outside the state needs it
                    d = dictionary.load() if dictionary.PATH.exists() else False
                entry = d.get(id_) if d else None
                if entry is None:  # without the dictionary, the words outside the state are left out
                    continue
                ja = entry['ja']
                extra[ja] = entry
            answers.append((p['at'], ja, 1 - answer))
    return answers, fakes, extra


def probe_log(log):
    """probe_answers of a log's probes (log.probes: the state's records; none for a log without them), found once per
    log and its word state's entries."""
    origin = getattr(log, 'origin', log)
    found = origin.__dict__.get('_probed')
    if found is None:
        probes = getattr(log, 'probes', None) or ()
        found = probe_answers(probes, getattr(log, 'entries', None) or {}) if probes else ((), (), {})
        origin.__dict__['_probed'] = found
    return found


def overclaiming(fakes, until, days=OVERCLAIM_DAYS):
    """The share of made-up words the reader claimed known in the probes of the `days` days before `until` (fakes:
    probe_answers'), the over-claiming rate the probe's own correction for guessing uses (probe.corrected); 0 without
    any."""
    claimed = answered = 0
    for at, c, n in fakes:
        if until - days * DAY_MS <= at < until:
            claimed, answered = claimed + c, answered + n
    return claimed / answered if answered else 0.0


# ---- review cards (review.py serves them; feedback.py keeps what happened: the cards table) ----
# A review card shows one word alone; the reader reveals its reading and meaning, or moves on without. What is measured
# is the time from the card appearing to the reveal, or that there was none. Two kinds of outcome, a mixture over
# whether the reader knew the word at recall (Components): known, a log reveal time N(mu_K, s_K) and a chance a_K of
# moving on without a reveal; not known, N(mu_U, s_U) and a_U. The made-up words (data/fakes.tsv) are never known, so
# their outcomes are the "not known" component's alone and pin it, give-ups included: a reader who presses up at once
# on a word they don't know shows it on the made-up words too, and if that looks like a known reveal the two components
# overlap, every card's evidence shrinks with them (its likelihood ratio nears 1), and the separation (auc, worth) says
# so. Each component is held by a prior worth REVIEW_CARDS cards, so a handful of cards can't swing it.
REVIEW_MAX_MS = 60_000     # a card on screen longer than this before its reveal, or before it was left, is distraction:
                           # the outcome is dropped (the showing still counts as a read, read_clocks)
REVIEW_MIN_MS = 150        # a reveal sooner is taken as this soon (a key already down); a card left sooner wasn't
                           # looked at, and is dropped
REVIEW_CARDS = 10          # each component's prior weighs as much as this many cards
# (log ms mean, its sd, the chance of moving on without a reveal) a priori: a word known at recall revealed in about
# 1.2 s, one not known in about 3 s, and a known word more often left without a reveal (nothing to check)
REVIEW_PRIOR = {'known': (math.log(1200), 0.5, 0.3), 'unknown': (math.log(3000), 0.7, 0.1)}
REVIEW_TOLERANCE = 1e-4    # the components' EM stops once no parameter moves more than this
REVIEW_WEAK = 0.2          # a card worth less than this of a yes/no answer at even odds (auc about 0.78): weak
REVIEW_GRID = 50           # the expected information is tabled at this many steps of P(known)


def review_outcome(reveal, shown):
    """A reviewed card's outcome as the mixture reads it: its log reveal time (ms, floored at REVIEW_MIN_MS), None when
    left without a reveal, or False when dropped (over REVIEW_MAX_MS, or left within REVIEW_MIN_MS)."""
    if reveal is None:
        return None if REVIEW_MIN_MS <= (shown or 0) <= REVIEW_MAX_MS else False
    return math.log(max(REVIEW_MIN_MS, reveal)) if reveal <= REVIEW_MAX_MS else False


def softplus(x):
    return x + math.log1p(math.exp(-x)) if x > 0 else math.log1p(math.exp(x))


class Components:
    """The two components of a review outcome (the comment above REVIEW_MAX_MS): mu, s (log ms) and a (the chance of no
    reveal) for known and not known, fitted by EM with their priors (update), and what they make of an outcome: log
    densities (logs), the expected information a card gives on its word's log odds of being known (info), and how well
    they separate (separation)."""
    NAMES = ('known', 'unknown')

    def __init__(self, values=None):
        values = values if isinstance(values, dict) else {}
        self.mu, self.s, self.a = [], [], []
        for name in self.NAMES:
            mu, s, a = REVIEW_PRIOR[name]
            given = values.get(name) if isinstance(values.get(name), dict) else {}
            number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            self.mu.append(given['mu'] if number(given.get('mu')) else mu)
            self.s.append(given['s'] if number(given.get('s')) and given['s'] > 0 else s)
            self.a.append(given['a'] if number(given.get('a')) and 0 < given['a'] < 1 else a)
        self._table = None

    def values(self):
        return {name: {'mu': round(self.mu[c], 5), 's': round(self.s[c], 5), 'a': round(self.a[c], 5)}
                for c, name in enumerate(self.NAMES)}

    def logs(self, x):
        """(log A, log B): the outcome's log likelihood if known, and if not (x: review_outcome's)."""
        if x is None:
            return math.log(self.a[0]), math.log(self.a[1])
        return tuple(math.log(1 - self.a[c]) - math.log(self.s[c]) - 0.5 * math.log(2 * math.pi)
                     - 0.5 * ((x - self.mu[c]) / self.s[c]) ** 2 for c in (0, 1))

    def penalty(self):
        """The priors' part of the objective: per component, REVIEW_CARDS cards' worth of N(mu0, s^2 / REVIEW_CARDS) on
        mu, of an inverse gamma on s^2 around s0^2, and of Beta(n a0, n (1 - a0)) on a (update maximizes with it)."""
        n, total = REVIEW_CARDS, 0.0
        for c, name in enumerate(self.NAMES):
            mu0, s0, a0 = REVIEW_PRIOR[name]
            total += n * ((self.mu[c] - mu0) ** 2 + s0 * s0) / (2 * self.s[c] ** 2) + n * math.log(self.s[c])
            total -= n * (a0 * math.log(self.a[c]) + (1 - a0) * math.log(1 - self.a[c]))
        return total

    def update(self, real, fakes=()):
        """One M step: real, [(x, weight, P(known))] the cards of real words; fakes, [(x, weight)] the made-up words',
        never known. Returns how far the parameters moved."""
        n, before = REVIEW_CARDS, self.mu + self.s + self.a
        for c, name in enumerate(self.NAMES):
            mu0, s0, a0 = REVIEW_PRIOR[name]
            cards = [(x, v * (r if c == 0 else 1 - r)) for x, v, r in real] + ([(x, v) for x, v in fakes] if c else [])
            total = sum(v for _x, v in cards)
            timed = [(x, v) for x, v in cards if x is not None]
            weight = sum(v for _x, v in timed)
            self.a[c] = min(0.995, max(0.005, ((total - weight) + n * a0) / (total + n)))
            self.mu[c] = (sum(v * x for x, v in timed) + n * mu0) / (weight + n)
            spread = sum(v * (x - self.mu[c]) ** 2 for x, v in timed) + n * (self.mu[c] - mu0) ** 2 + n * s0 * s0
            self.s[c] = max(0.05, math.sqrt(spread / (weight + n)))
        self._table = None
        return max(abs(a - b) for a, b in zip(self.mu + self.s + self.a, before))

    def _j(self, z):
        """sum over outcomes of (A - B)^2 / (z A + (1 - z) B): no reveal, then the reveal times on a grid."""
        a, b = self.a
        total = (a - b) ** 2 / (z * a + (1 - z) * b)
        low = min(m - 6 * s for m, s in zip(self.mu, self.s))
        high = max(m + 6 * s for m, s in zip(self.mu, self.s))
        steps = 240
        dx = (high - low) / steps
        for j in range(steps + 1):
            x = low + j * dx
            fa, fb = (math.exp(v) for v in self.logs(x))
            mix = z * fa + (1 - z) * fb
            if mix > 0:
                total += (0.5 if j in (0, steps) else 1.0) * dx * (fa - fb) ** 2 / mix
        return total

    def info(self, z):
        """The expected information one card gives on its word's log odds of being known at recall, at P(known) z:
        z^2 (1 - z)^2 sum (A - B)^2 / (z A + (1 - z) B), z (1 - z) when the outcome tells known from not for sure, 0
        when the components are alike (tabled over z)."""
        if self._table is None:
            self._table = [(lambda z: z * z * (1 - z) * (1 - z) * self._j(z))((j + 0.5) / REVIEW_GRID)
                           for j in range(REVIEW_GRID)]
        t = min(REVIEW_GRID - 1.0, max(0.0, z * REVIEW_GRID - 0.5))
        j = min(REVIEW_GRID - 2, int(t))
        value = self._table[j] + (t - j) * (self._table[j + 1] - self._table[j])
        return max(0.0, min(value, z * (1 - z)))

    def separation(self):
        """How well the components tell a word known at recall from one not: auc, the chance a known word's reveal is
        sooner than a not known word's (by the reveal times alone, 0.5: none); worth, the information a card gives at
        even odds as a share of a yes/no answer's (no reveal included; 1: as good as asking, 0: worthless); weak, worth
        under REVIEW_WEAK."""
        spread = math.sqrt(self.s[0] ** 2 + self.s[1] ** 2)
        auc = 0.5 * math.erfc(-(self.mu[1] - self.mu[0]) / spread / math.sqrt(2))
        worth = self.info(0.5) / 0.25
        return {'auc': round(auc, 3), 'worth': round(worth, 3), 'weak': worth < REVIEW_WEAK}

    def summary(self):
        """For knowledge.json and reports: each component's median reveal (ms), its spread (log sd) and chance of no
        reveal, and the separation."""
        out = {name: {'ms': round(math.exp(self.mu[c])), 'sd': round(self.s[c], 3), 'skip': round(self.a[c], 3)}
               for c, name in enumerate(self.NAMES)}
        return dict(out, **self.separation())


def is_grade(g):
    return g in (1, 2, 3, 4) and not isinstance(g, bool)


def graded_flow(reviews):
    """{client: when it first sent a grade}: from then on its cards are of the graded flow (review_answers). A client is
    a review item's source (desktop or mobile): the items carry no finer client id, so an older build of the same
    source that never grades, still in use after a newer one graded, has its cards read as graded-flow cards left
    without a grade, which say nothing. That loses only the reveal times' weak evidence, for as long as the old build
    runs; a client id on review items would be the fix if that ever matters."""
    out = {}
    for r in reviews or ():
        if isinstance(r, dict) and is_grade(r.get('grade')) and isinstance(r.get('at'), (int, float)):
            out[r.get('source')] = min(out.get(r.get('source'), r['at']), r['at'])
    return out


def in_graded_flow(r, starts):
    return r.get('source') in starts and r['at'] >= starts[r.get('source')]


def counted_grades(reviews):
    """The ids of the graded cards whose grade is an answer about long-term knowledge: a learning card's graded from the
    new or the review state (its schedule's replay, srs, by feedback.deck_groups), or from a learning or relearning
    step on a later Anki day than the review before (a step left overnight tests the next day's memory); not one on a
    step the same day, minutes after the last, which says how the steps went, not what the reader knows; and every
    probe card's."""
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    rows = [dict(r, reviewed=r['at'] + (r.get('grade_ms') or 0)) for r in reviews or ()
            if isinstance(r, dict) and is_grade(r.get('grade')) and number(r.get('at'))
            and isinstance(r.get('word'), str) and r.get('kind') in ('learning', 'probe')]
    out = {r.get('card') for r in rows if r['kind'] == 'probe'}
    for group in feedback.deck_groups([r for r in rows if r['kind'] == 'learning']).values():
        group = sorted(group, key=lambda r: r['reviewed'])
        _card, log = srs.replay([(r['reviewed'], r['grade'], r.get('served')) for r in group],
                                srs.card_id(group[0]['word']))  # the card id review.schedules replays with
        before = None
        for r, (at, _g, state) in zip(group, log):
            if state in (srs.NEW, srs.REVIEW) or srs.day_number(at) > srs.day_number(before):
                out.add(r.get('card'))
            before = at
    return out


def review_answers(reviews, entries, d=None):
    """The reviewed cards (feedback.reviews) as the fit reads them: (answers, fakes, extra, shown, graded). Two flows:
    a client's cards are of the graded flow from its first grade on (graded_flow; the review panel, where every card
    graded is revealed first), the rest of the reveal flow (the wedge, which never graded). answers: [(at, ja, x)] per
    card of a real word (learning or probe) of the reveal flow with an outcome (review_outcome: x a log time, or None
    for no reveal); fakes: [(at, x)] per made-up word's card of the reveal flow with one, so the mixture's components
    are fitted from one flow (in the graded flow a made-up card is always revealed, to be graded, and a card closed
    unrevealed is not a reader who knew it: read as the reveal flow, either would bend the chances of no reveal);
    graded: [(at, ja, y)] per graded card of a real word whose grade is an answer (counted_grades: not a learning or
    relearning step's), at most one per word per Anki day (srs.day_number, the first), y 1 for Again (not known at
    recall, the event the fit counts, as a probe answer of not known) and 0 for Hard, Good or Easy; a graded-flow card
    left without a grade gives nothing. shown: [(at, ja)] every real word's card, dropped outcomes too (each is a read,
    for the recency clock). ja is the word state's word for the card's entry (entries: {ja: its dictionary entry}, by
    id, else by spelling), else the entry's own spelling, with that entry in extra (for the word features), as
    probe_answers finds a probe's words."""
    by_id, by_key = {}, {}
    for ja, e in (entries or {}).items():
        if e is not None:
            by_id.setdefault(e['id'], ja)
        by_key.setdefault(fwlib.key(ja), ja)
    answers, fakes, extra, shown, graded = [], [], {}, [], []
    starts, counted, days = graded_flow(reviews), counted_grades(reviews), set()
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    for r in sorted((r for r in reviews or () if isinstance(r, dict) and number(r.get('at'))),
                    key=lambda r: r['at'] + (r.get('grade_ms') or 0)):
        if not isinstance(r, dict) or not number(r.get('at')) or not isinstance(r.get('word'), str):
            continue
        reveal, seen = r.get('reveal'), r.get('shown')
        if reveal is not None and not number(reveal) or not number(seen):
            continue
        x = review_outcome(reveal, seen)
        flow = in_graded_flow(r, starts)
        if r.get('kind') == 'fake':
            if x is not False and not flow:
                fakes.append((r['at'], x))
            continue
        if r.get('kind') not in ('learning', 'probe'):
            continue
        entry_id = r.get('entry') if isinstance(r.get('entry'), int) else None
        ja = (by_id.get(entry_id) if entry_id is not None else None) or by_key.get(fwlib.key(r['word']))
        if ja is None:
            if entry_id is None:
                continue
            if d is None:
                import dictionary  # here: only a card of a word outside the state needs it
                d = dictionary.load() if dictionary.PATH.exists() else False
            entry = d.get(entry_id) if d else None
            if entry is None:
                continue
            ja = entry['ja']
            extra[ja] = entry
        shown.append((r['at'], ja))
        if is_grade(r.get('grade')):
            day = (ja, srs.day_number(r['at'] + (r.get('grade_ms') or 0)))
            if r.get('card') in counted and day not in days:
                days.add(day)
                graded.append((r['at'], ja, 1 if r['grade'] == 1 else 0))
        elif x is not False and not flow:
            answers.append((r['at'], ja, x))
    shown.sort()
    return answers, fakes, extra, shown, graded


def review_log(log):
    """review_answers of a log's reviewed cards (log.reviews; none for a log without them), found once per log and its
    word state's entries."""
    origin = getattr(log, 'origin', log)
    found = origin.__dict__.get('_reviewed')
    if found is None:
        reviews = getattr(origin, 'reviews', None) or ()
        found = review_answers(reviews, getattr(log, 'entries', None) or {}) if reviews else ((), (), {}, (), ())
        origin.__dict__['_reviewed'] = found
    return found


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

    An observation is a Bernoulli with P(open) = w q: q = P(lookup | read) = sigmoid(offset_s + form + context s +
    recency_log_odds(k_w, u, floor)), w its read weight (1 when opened, else the larger of the line rule and the read
    chance at this pace and cap; an episode's, the line rule alone), weighted 0.5 ** (age / EVIDENCE_DAYS) and
    FORM_WEIGHT for a form. u and s are the observation's clock (read_clocks): how far the word is back toward its
    long-term chance since it was last read, and the chance it was last read in the same context; recency=False puts
    every observation at its long-term chance (NEXT_STEPS step 4's model). The clock weighs earlier showings by the
    chances the fit finds, so the fit runs CLOCK_PASSES times, each from the one before. An episode's observations are
    conditioned on at least one open. A word's unopened observations with the same read weight and clock, rounded to
    POOL_STEP (u on its square root, finer near 0), pool, so the work grows with the words more than with the log.

    The monthly probes' answers before `until` (probe_log, without them when probes is False) are observations too,
    each at the word's long-term chance with an offset of its own (_probe_terms); a probed word outside the word state
    takes its features from its dictionary entry. So are the review cards' outcomes before `until` (review_log, without
    them when reviews is False; _review_terms): a graded card a yes/no answer, known at recall with chance
    sigmoid(k - review); an ungraded one a mixture, known at recall with that chance, whose two components (Components:
    reveal time and no reveal, known and not; the made-up words' cards are of the second alone) are fitted with the
    words by EM, each held by its prior (REVIEW_PRIOR), with k held at each step.

    Minimized: their negative log likelihood; (k_w - mean_w)^2 / 2 width^2 for every word with evidence, mean_w the
    regression on its features (features) and width its kind's; a command's one-sided term, COMMAND_WEIGHT (decayed)
    times (COMMAND_K - k)^2 / 2 while k is below a known command's or above a learn command's; and each shared
    parameter's prior (SHARED); and the review components' priors (Components.penalty). By Newton's method with the
    expected information, positive definite, whose shape (a
    diagonal for the words, joined only through the shared parameters) the Schur complement solves in time linear in
    the words; the widths by EM toward WIDTHS. Each word's sd is its k's from the inverse of the information (Laplace),
    the shared parameters' uncertainty included. frozen: the shared parameters and widths stay at their priors, as for
    one word among many others. start: what an earlier fit learned (state()), to start from; without its shared
    parameters, the fit starts from a low floor (LOW_FLOOR) and from one above every word (NO_FLOOR: no recency) and
    keeps the better, since where the floor is above every word only its prior bends the objective and the two starts
    can end in different minima. The floor moves at most FLOOR_STEP a step.

    The word features (features, word_features) come from each word's dictionary entry (log.entries) and, for the
    kanji known at a glance, from free: {ja: free flag} at the last refresh (or the day before). Its kanji are known
    from the free words with evidence before `until` (observations or a command), the word itself left out: a word free
    by its prior alone doesn't count, so no chain of priors can make words free, and the flags are from before the
    fit, so it never reads its own output (docs/adaptive-difficulty.md). plain: no word features (the prior by origin
    and Anki interval alone), for comparison.

    speed (default SPEED): the passages' and paragraphs' reading times are observations too (_speed_pass; their
    shared parameters, SPEED_SHARED, follow the lookup model's in theta and names), in two stages (_speed_alone): the
    fit without them; then their parameters with every word held there, each page's or message's own pace profiled
    out (alpha) and its sd fitted by EM (group_sd); then the fit again with them added at those parameters, so the
    words move with their reading times, but the time term's own parameters never move with the words."""

    def __init__(self, log, until, pace=None, top=None, start=None, frozen=False, free=None, plain=False,
                 recency=True, probes=True, speed=None, reviews=True):
        self.until, self.day, self.frozen, self.plain = until, day_of(until - 1), frozen, plain
        self.recency, self.probes, self.reviews = recency, probes, reviews
        self.speed = SPEED if speed is None else speed
        self.names = SHARED_NAMES + (SPEED_NAMES if self.speed else ())
        self.priors = dict(SHARED, **SPEED_SHARED) if self.speed else SHARED
        self.group_sd, self.groups, self.alpha, self.speed_sd, self._timed = SPEED_GROUP_SD, {}, [], {}, False
        self._temper = None
        self.pace, self.top = pace or log.pace, top or log.top
        self.intervals = log.intervals
        self.by_ja = {w['ja']: w for w in log.words}
        self.entries = {} if plain else getattr(log, 'entries', None) or {}
        if probes and not plain and probe_log(log)[2]:  # the entries of probed words outside the word state
            self.entries = dict(self.entries, **probe_log(log)[2])
        if reviews and not plain and review_log(log)[2]:  # and of review cards' words outside it
            self.entries = dict(self.entries, **review_log(log)[2])
        self.own = {ja for ja in evidenced(log, until) if (free or {}).get(ja)} if self.entries else set()
        self.known, self._features = kanji_counts(self.own), {}
        self.words, self.index, self.x, self.kind, self.n = [], {}, [], [], []
        started = time.time()
        start = start or {}
        self._review_start = None if frozen else start.get('review')
        shared = {} if frozen else start.get('shared') or {}
        if self.speed and not frozen and (start.get('cut') or {}).get('shared'):  # the fit without the term first
            shared = dict(shared, **start['cut']['shared'])
        theta = [shared.get(name, self.priors[name][0]) for name in self.names]
        self._group_start = start['group'] if self.speed and not frozen and start.get('group') else SPEED_GROUP_SD
        widths = dict(WIDTHS) if frozen else dict(WIDTHS, **(start.get('widths') or {}))
        cold = recency and not frozen and not shared  # nothing to start from: from a low floor and a high one
        passes = CLOCK_PASSES if cold or frozen else 1  # from an earlier fit, its chances weigh the clock already
        cut = ((start.get('cut') or {}).get('k') or start.get('k') or {}) if self.speed else start.get('k') or {}
        self._fit(log, [LOW_FLOOR if j == FLOOR and cold else v for j, v in enumerate(theta)], widths, cut, passes,
                  start.get('k') or {})
        if cold:
            first = dict(self.__dict__)
            self._fit(log, [NO_FLOOR if j == FLOOR else v for j, v in enumerate(theta)], widths, cut, passes,
                      start.get('k') or {})
            if first['objective'] <= self.objective:
                self.__dict__.update(first)
        self.seconds = time.time() - started

    def _fit(self, log, theta, widths, before, passes, timed=None):
        """Fits from theta, the widths and before ({ja: k}; the others from their priors): with recency, `passes`
        times, each with the clock weighed by the chances the fit before found (the first, by the start's). With
        speed, then the term's two stages (_speed_alone), the second from timed ({ja: k} of the last fit with it)."""
        self.theta, self.widths, self.k, self.var = list(theta), dict(widths), [], []
        self.group_sd, self._timed, self._temper = self._group_start, False, None
        self.review_parts = Components(self._review_start)
        self._terms(log, read_clocks(log, self.pace, self.top, self._chance(before)) if self.recency else {})
        self.k = [before.get(ja, self._mean(i, self.theta)) for i, ja in enumerate(self.words)]
        self.iterations = self._run()
        for _ in range(passes - 1 if self.recency else 0):
            self._terms(log, read_clocks(log, self.pace, self.top, self._chance()))
            self.iterations += self._run()
        if self.speed and self.units and not self.frozen:  # the reading times' parameters, from the words as the lookups
            # put them; then each unit tempered so that time gives no word more than SPEED_CAP times what its lookups
            # and prior give it; then the words again, with the reading times
            self.cut = {'k': dict(zip(self.words, self.k)), 'shared': dict(zip(SHARED_NAMES, self.theta))}
            self.theta = self._speed_alone(self.k, self.theta)
            room = [SPEED_CAP * i / x if x > 0 else math.inf for x, i in zip(self._speed_most(), self.info)]
            self._temper = [min([1.0] + [room[w] for w in unit[6]]) for unit in self.units]
            self._timed = True
            self.k = [(timed or {}).get(ja, k) for ja, k in zip(self.words, self.k)]
            self.iterations += self._run()

    def _speed_most(self):
        """The most information the reading times could give each word at the term's parameters: its paragraphs'
        expected information times (unknown q (1 - q) / words)^2 at q (1 - q) = 1/4, its largest, whatever its k."""
        nu, b = SPEED_NU, self.theta[self.names.index('unknown')]
        sigma = [math.exp(2 * max(-10.0, min(10.0, self.theta[self.names.index(name)])))
                 for name in ('spread web', 'spread chat')]
        most = [0.0] * len(self.k)
        for t3, _group, _y, _length, _jw, iw, ws, v in self.units:
            x = v * (nu + 1) / ((nu + 3) * sigma[t3]) * (b * iw / 4) ** 2
            for w in ws:
                most[w] += x
        return most

    def _speed_alone(self, k, theta):
        """Stage one of the speed term (SPEED): its parameters, and the groups' sd, with every word held at k (the
        fit's without the term): Newton's method on them alone, each held by its prior (SPEED_SHARED), with EM for the
        groups' sd (as for the widths, toward SPEED_GROUP_SD by SPEED_GROUP_WEIGHT groups). Returns theta with them;
        speed_sd has their sd. Fitted jointly with the words, the term is not identified: the larger unknown, the less
        a word's k must move to explain a paragraph's time, until time explains itself through the words (on the
        2026-10-20 snapshot's first day, unknown 33 and time 63% of all the words' information); with the words held,
        unknown is how well the lookups' chances predict reading time, which time can't inflate."""
        idx = [self.names.index(name) for name in SPEED_NAMES]
        theta, n, m = list(theta), len(k), len(theta)

        def objective(theta):
            g, h = [0.0] * m, [[0.0] * m for _ in range(m)]
            total = self._speed_pass(k, theta, [0.0] * n, [0.0] * n, g, h)
            for j, name in zip(idx, SPEED_NAMES):
                mean, sd = self.priors[name]
                total += 0.5 * ((theta[j] - mean) / sd) ** 2
                g[j] += (theta[j] - mean) / sd ** 2
                h[j][j] += 1 / sd ** 2
            return total, [g[j] for j in idx], [[h[a][b] for b in idx] for a in idx]

        total, g, h = objective(theta)
        last = None
        for _ in range(FIT_ITERATIONS):
            step, t = _solve(h, [-x for x in g]), 1.0
            while True:
                trial = list(theta)
                for j, x in zip(idx, step):
                    trial[j] += t * x
                found = objective(trial)
                if found[0] <= total + 1e-9 or t < 1e-3:
                    break
                t /= 2
            theta, (total, g, h) = trial, found
            new = math.sqrt((sum(a * a + 1 / i for a, i in zip(self.alpha, self._group_info))
                             + SPEED_GROUP_WEIGHT * SPEED_GROUP_SD ** 2) / (len(self.alpha) + SPEED_GROUP_WEIGHT))
            widened, change = abs(new - self.group_sd), new - self.group_sd
            before, last = last, change
            if before and change * before > 0 and abs(change) < abs(before):  # EM creeps: jump toward its limit
                change /= 1 - min(0.95, change / before)
            self.group_sd = max(0.01, self.group_sd + change)
            if widened > WIDTH_TOLERANCE / 10:
                total, g, h = objective(theta)
            if t * max(abs(x) for x in step) < FIT_TOLERANCE and widened < WIDTH_TOLERANCE:
                break
        columns = [_solve(h, [1.0 if i == j else 0.0 for i in range(len(idx))]) for j in range(len(idx))]
        self.speed_sd = {name: math.sqrt(max(0.0, columns[j][j])) for j, name in enumerate(SPEED_NAMES)}
        return theta

    def _chance(self, before=None):
        """chance(observation, u, s) for read_clocks: its P(lookup | read) by this fit, or with before ({ja: k}) by
        those k's and, for the other words, the priors at the current shared parameters."""
        if before is None:
            return lambda o, u, s: self.q(o.word, o.surface, o.form, u, s)

        def chance(o, u, s):
            k = before.get(o.word)
            if k is None:
                x, _kind = self.features(o.word)
                k = sum(self.theta[SHARED_NAMES.index(name)] * value for name, value in x.items())
            return self.q_at(k, o.surface, o.form, u, s)
        return chance

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

    def _terms(self, log, clocks):
        """The pooled terms (word, chat, form, y, read weight, u, s: weight) and episodes from the log's events before
        until, each observation at its clock (read_clocks; without one, at its long-term chance), and the commands;
        with speed, the reading times (units: (chat, group, ln ms, ln(words / SPEED_WORDS), japanese / words, 1 /
        words, the lexicon words, weight), a group being a page or T3 message)."""
        pooled, self.episodes, self.commands = {}, [], []
        same = (self.pace, self.top) == (log.pace, log.top)
        self.n = [0.0] * len(self.words)
        units, groups = [], {}
        for event in log.events:
            if event.at >= self.until:
                break
            decay = 0.5 ** ((self.until - event.at) / DAY_MS / EVIDENCE_DAYS)
            if not hasattr(event, 'obs'):  # a command
                self.commands.append((self._word(event.word), event.kind, decay))
                continue
            for ms, count, japanese, listed, _active, _dwell in getattr(event, 'units', ()) if self.speed else ():
                if ms < SPEED_THROUGH * by_surface(self.pace, event.surface) * count:
                    continue
                t3 = event.surface == 't3'
                group = groups.setdefault(event.key if t3 else event.key[:2], len(groups))
                units.append((t3, group, math.log(ms), math.log(count / SPEED_WORDS), japanese / count, 1 / count,
                              tuple(self._word(ja) for ja in listed), decay * SPEED_WEIGHT))
            chosen = []
            for o in event.obs:
                i = self._word(o.word)
                v = decay * (FORM_WEIGHT if o.form else 1.0)
                w = read_weight(o, event.episode, self.pace, self.top, same)
                if o.y <= 0 and not event.episode:
                    w = min(1.0, max(POOL_STEP, round(w / POOL_STEP) * POOL_STEP))
                u, s = clocks.get(id(o), (1.0, 0.0))
                u = 1.0 if u >= 1.0 else (round(math.sqrt(u) / POOL_STEP) * POOL_STEP) ** 2  # finer near 0
                s = round(s / POOL_STEP) * POOL_STEP
                key = (i, o.surface == 't3', bool(o.form), o.y, w, u, s)
                pooled[key] = pooled.get(key, 0.0) + v
                self.n[i] += v * w
                if event.episode:
                    chosen.append((i, o.surface == 't3', bool(o.form), w, u, s))
            if event.episode and chosen:
                self.episodes.append((chosen, decay))
        keys = list(pooled)
        self.terms = tuple(tuple(key[j] for key in keys) for j in range(7)) + (tuple(pooled[key] for key in keys),)
        self._probe_terms(log)
        self._review_terms(log)
        before = dict(zip(self.groups, self.alpha))  # each group's own pace starts where the last pass left it
        self.units, self.groups = units, groups
        self.alpha = [before.get(key, 0.0) for key in groups]
        self.group_units = [[] for _ in groups]
        for j, unit in enumerate(units):
            self.group_units[unit[1]].append(j)

    def _probe_terms(self, log):
        """The monthly probes' answers before until (probe_log), pooled by word and answer: probe_terms (word, y:
        weight), each weighted like evidence. A word answered not known (y 1) has a chance r q, one answered known
        1 - r q, with q = sigmoid(probe - k) the chance the reader doesn't recognise it at a glance out of context and
        r = 1 - f the chance such a word is answered not known, f the over-claiming rate of the made-up words
        (overclaiming, as the probe's own correction for guessing has it): a probe answer is a read showing with its
        own offset, at the word's long-term chance (a probe is no reading, and its clock doesn't move)."""
        pooled, self.probe_read = {}, 1.0
        answers, fakes, _extra = probe_log(log) if self.probes else ((), (), {})
        if answers:
            self.probe_read = 1.0 - min(0.9, overclaiming(fakes, self.until))
        for at, ja, y in answers:
            if at >= self.until:
                continue
            i = self._word(ja)
            v = 0.5 ** ((self.until - at) / DAY_MS / EVIDENCE_DAYS)
            pooled[(i, y)] = pooled.get((i, y), 0.0) + v
            self.n[i] += v * (1.0 if y else self.probe_read)
        keys = list(pooled)
        self.probe_terms = (tuple(i for i, _y in keys), tuple(y for _i, y in keys), tuple(pooled[k] for k in keys))

    def _review_terms(self, log):
        """The review cards' outcomes before until (review_log): review_terms (word, x, weight) for real words'
        ungraded cards, review_graded (word, y, weight) for their graded ones and review_fakes (x, weight) for made-up
        words', x review_outcome's, each weighted like evidence. With z = sigmoid(k - review) the chance the word is
        known at recall: a graded card has likelihood 1 - z if Again (y 1), else z, as a probe answer has (its offset
        the review one, and no over-claiming: the grade comes after the reveal); an ungraded real word's card z A +
        (1 - z) B, A and B the outcome's under the known and not known components; a made-up word's, B. A card adds its
        weight to its word's evidence (n)."""
        answers, fakes, _extra, _shown, graded = review_log(log) if self.reviews else ((), (), {}, (), ())
        terms, made_up, explicit = [], [], []
        for at, ja, y in graded:
            if at >= self.until:
                continue
            i, v = self._word(ja), 0.5 ** ((self.until - at) / DAY_MS / EVIDENCE_DAYS)
            explicit.append((i, y, v))
            self.n[i] += v
        self.review_graded = explicit
        for at, ja, x in answers:
            if at >= self.until:
                continue
            i, v = self._word(ja), 0.5 ** ((self.until - at) / DAY_MS / EVIDENCE_DAYS)
            terms.append((i, x, v))
            self.n[i] += v
        for at, x in fakes:
            if at < self.until:
                made_up.append((x, 0.5 ** ((self.until - at) / DAY_MS / EVIDENCE_DAYS)))
        self.review_terms, self.review_fakes = terms, made_up
        self._review_ready()

    def _review_ready(self):
        """Each card's log likelihoods under the components as they stand: (log A, log B) per real word's card, log B
        per made-up word's."""
        parts = self.review_parts
        self._review_logs = [parts.logs(x) for _i, x, _v in self.review_terms]
        self._fake_logs = [parts.logs(x)[1] for x, _v in self.review_fakes]

    def _review_em(self, k, theta):
        """The components by EM with every word held at k and the offset at theta's (each step lowers the objective:
        coordinate descent with the Newton steps on k and theta). Returns how far they moved."""
        if self.frozen or not (self.review_terms or self.review_fakes):
            return 0.0
        moved, c = 0.0, theta[REVIEW]
        for _ in range(50):
            real = []
            for (i, x, v), (la, lb) in zip(self.review_terms, self._review_logs):
                real.append((x, v, sigmoid(max(-30.0, min(30.0, k[i])) - c + la - lb)))
            step = self.review_parts.update(real, self.review_fakes)
            self._review_ready()
            moved = max(moved, step)
            if step < REVIEW_TOLERANCE:
                break
        return moved

    def _mean(self, i, theta):
        return sum(theta[f] * value for f, value in self.x[i].items())

    def _pass(self, k, theta):
        """The data's negative log likelihood and its derivatives: per word, the gradient and information; the
        information joining each word to the shared parameters its chances depend on (JOINED: the chat and form
        offsets, the floor and the context), {parameter: [per word]}; for the shared parameters, gradient and
        information. The information is the expected one, through each chance's log odds eta (Gauss-Newton)."""
        words, shared = len(k), len(theta)
        grad, info = [0.0] * words, [0.0] * words
        joins = {j: [0.0] * words for j in JOINED}
        g, h = [0.0] * shared, [[0.0] * shared for _ in range(shared)]
        total, t3, phi, delta = 0.0, theta[T3], theta[FORM], theta[CONTEXT]
        exp, log = math.exp, math.log
        c = exp(max(-30.0, min(30.0, theta[FLOOR])))
        cp, power = c ** -SOFT_MIN, -1 / SOFT_MIN

        def eta_of(kw, u, s, f, x):
            """eta, d eta / d k and d eta / d floor: recency_log_odds and the offsets."""
            kw = max(-30.0, min(30.0, kw))
            offset = (t3 if s else 0.0) + (phi if f else 0.0) + delta * x
            if u >= 1.0:  # back at the long-term chance
                return offset - kw, -1.0, 0.0
            o = exp(-kw)
            m = (cp + o ** -SOFT_MIN) ** power
            a = m + (o - m) * u
            return (offset + log(a), -o * ((m / o) ** (SOFT_MIN + 1) * (1 - u) + u) / a,
                    (1 - u) * (m / c) ** (SOFT_MIN + 1) * c / a)

        jt3, jform, jfloor, jcontext = joins[T3], joins[FORM], joins[FLOOR], joins[CONTEXT]
        # the shared parameters' gradient and information, summed here: t(3), f(orm), l (floor), c(ontext)
        gt = gf = gl = gc = htt = htf = htl = htc = hff = hfl = hfc = hll = hlc = hcc = 0.0
        for w, s, f, y, r, u, x, v in zip(*self.terms):
            kw = k[w]
            kw = -30.0 if kw < -30.0 else 30.0 if kw > 30.0 else kw
            eta = (t3 if s else 0.0) + (phi if f else 0.0) + delta * x
            if u >= 1.0:  # back at the long-term chance
                eta -= kw
                dk, dfl = -1.0, 0.0
            else:
                o = exp(-kw)
                m = (cp + o ** -SOFT_MIN) ** power
                a = m + (o - m) * u
                eta += log(a)
                dk = -o * ((m / o) ** (SOFT_MIN + 1) * (1 - u) + u) / a
                dfl = (1 - u) * (m / c) ** (SOFT_MIN + 1) * c / a
            q = 1 / (1 + exp(-(30.0 if eta > 30.0 else -30.0 if eta < -30.0 else eta)))
            if y > 0:  # opened, so read: y log q + (1 - y) log (1 - q)
                d, i = y - q, q * (1 - q)
                total -= v * (y * log(q) + (1 - y) * log(1 - q))
            else:  # not opened, read with chance r: log (1 - r q)
                a = 1 - r * q
                d, i = -r * q * (1 - q) / a, r * q * (1 - q) * (1 - q) / a
                total -= v * log(a)
            vd, vi = v * d, v * i
            grad[w] -= vd * dk
            vik = vi * dk
            info[w] += vik * dk
            if s:
                gt -= vd
                jt3[w] += vik
                htt += vi
                htl += vi * dfl
                htc += vi * x
                if f:
                    htf += vi
            if f:
                gf -= vd
                jform[w] += vik
                hff += vi
                hfl += vi * dfl
                hfc += vi * x
            if dfl:
                gl -= vd * dfl
                jfloor[w] += vik * dfl
                hll += vi * dfl * dfl
                hlc += vi * dfl * x
            if x:
                gc -= vd * x
                jcontext[w] += vik * x
                hcc += vi * x * x
        r, jprobe, gp, hpp = self.probe_read, joins[PROBE], 0.0, 0.0
        for w, y, v in zip(*self.probe_terms):  # eta = probe - k: d eta / d k = -1, d eta / d probe = 1
            kw = k[w]
            kw = -30.0 if kw < -30.0 else 30.0 if kw > 30.0 else kw
            q = 1 / (1 + exp(-(theta[PROBE] - kw)))
            q = 1e-12 if q < 1e-12 else 1 - 1e-12 if q > 1 - 1e-12 else q
            if y:  # answered not known: r q
                d, i = 1 - q, q * (1 - q)
                total -= v * log(r * q)
            else:  # answered known: 1 - r q
                a = 1 - r * q
                d, i = -r * q * (1 - q) / a, r * q * (1 - q) * (1 - q) / a
                total -= v * log(a)
            grad[w] += v * d
            info[w] += v * i
            jprobe[w] -= v * i
            gp -= v * d
            hpp += v * i
        g[PROBE] += gp
        h[PROBE][PROBE] += hpp
        recall, jreview, gr, hrr, parts = theta[REVIEW], joins[REVIEW], 0.0, 0.0, self.review_parts
        for (w, _x, v), (la, lb) in zip(self.review_terms, self._review_logs):  # eta = k - review
            kw = k[w]
            eta = (-30.0 if kw < -30.0 else 30.0 if kw > 30.0 else kw) - recall
            z, p = sigmoid(eta), sigmoid(eta + la - lb)  # known a priori, and given the outcome
            total -= v * (lb + softplus(eta + la - lb) - softplus(eta))
            d, i = z - p, v * parts.info(z)  # d(-log likelihood) / d k, and the expected information
            grad[w] += v * d
            info[w] += i
            jreview[w] -= i
            gr -= v * d
            hrr += i
        total -= sum(v * lb for (_x, v), lb in zip(self.review_fakes, self._fake_logs))
        for w, y, v in self.review_graded:  # eta = k - review, z its chance known: Again 1 - z, else z
            kw = k[w]
            eta = (-30.0 if kw < -30.0 else 30.0 if kw > 30.0 else kw) - recall
            z = sigmoid(eta)
            total += v * (softplus(eta) if y else softplus(-eta))
            d, i = z - (1 - y), v * z * (1 - z)
            grad[w] += v * d
            info[w] += i
            jreview[w] -= i
            gr -= v * d
            hrr += i
        g[REVIEW] += gr
        h[REVIEW][REVIEW] += hrr
        for (a, b), value in (((T3, T3), htt), ((T3, FORM), htf), ((T3, FLOOR), htl), ((T3, CONTEXT), htc),
                              ((FORM, FORM), hff), ((FORM, FLOOR), hfl), ((FORM, CONTEXT), hfc),
                              ((FLOOR, FLOOR), hll), ((FLOOR, CONTEXT), hlc), ((CONTEXT, CONTEXT), hcc)):
            h[a][b] += value
            if a != b:
                h[b][a] += value
        for j, value in ((T3, gt), (FORM, gf), (FLOOR, gl), (CONTEXT, gc)):
            g[j] += value
        for chosen, v in self.episodes:  # conditioned on at least one open: minus log (1 - none)
            found, none = [], 1.0
            for w, s, f, r, u, x in chosen:
                eta, dk, dfl = eta_of(k[w], u, s, f, x)
                q = sigmoid(eta)
                found.append((q, dk, dfl))
                none *= 1 - r * q
            none = min(none, 1 - 1e-12)
            total += v * log(1 - none)
            for (w, s, f, r, u, x), (q, dk, dfl) in zip(chosen, found):
                e = v * none / (1 - none) * r * q * (1 - q) / (1 - r * q)
                grad[w] += e * dk
                g[T3] += e if s else 0.0
                g[FORM] += e if f else 0.0
                g[FLOOR] += e * dfl
                g[CONTEXT] += e * x
        if self.speed and self._timed:
            total += self._speed_pass(k, theta, grad, info, g, h)
        return total, grad, info, joins, g, h

    def _speed_pass(self, k, theta, grad, info, g, h):
        """The reading times' part of _pass (SPEED_SHARED): adds their gradient and expected information to the words'
        and shared parameters', and returns their negative log likelihood with the groups' prior. A unit's log ms is a
        Student-t (SPEED_NU) around mu = pace_s + alpha_g + length_s ln(words / SPEED_WORDS) + (japanese J + unknown U)
        / words, U = the sum of sigmoid(-k_w) over its words, with scale e^spread_s. Each group's own pace alpha_g, held
        toward 0 by N(0, group_sd^2), is profiled out: solved for (by Fisher scoring, from where the last pass left it),
        so the gradient is the partial one there (the envelope theorem), and the information is the profile's: each
        group's sums with every parameter, times their transpose, over its own information, taken off. Of the
        information that joins words, to each other (two words of one paragraph or group) or to these parameters, only
        each word's own diagonal is kept: kept, the joins to the parameters without those to each other leave the
        information indefinite (a paragraph of two words: [[a2, 0, ac], [0, b2, bc], [ac, bc, c2]] has determinant
        -a2 b2 c2). It is small beside each word's prior, so the Newton step converges all the same, and the Laplace sd
        of a word or of these parameters leaves out only the other's uncertainty."""
        nu, exp, log = SPEED_NU, math.exp, math.log
        pw, pc, lw, lc, jj, uu, sw, sc = (self.names.index(name) for name in SPEED_NAMES)
        b, tau2, units = theta[uu], self.group_sd ** 2, self.units
        if self._timed and self._temper:  # each unit tempered (_fit)
            units = [unit[:7] + (unit[7] * c,) for unit, c in zip(units, self._temper)]
        sigma = (exp(max(-10.0, min(10.0, theta[sw]))), exp(max(-10.0, min(10.0, theta[sc]))))
        variance = (sigma[0] ** 2, sigma[1] ** 2)
        scale = (nu + 1) / (nu + 3)
        rests, xs_of, qs_of = [], [], []
        for t3, _group, y, length, jw, iw, ws, _v in units:
            qs = [1 / (1 + exp(-30.0 if k[w] < -30.0 else 30.0 if k[w] > 30.0 else k[w])) for w in ws]
            ux = sum(qs) * iw
            pace, slope = (theta[pc], theta[lc]) if t3 else (theta[pw], theta[lw])
            rests.append(y - pace - slope * length - theta[jj] * jw - b * ux)  # the residual but for alpha
            xs_of.append(ux)
            qs_of.append(qs)
        total, alpha = 0.0, self.alpha
        for i, members in enumerate(self.group_units):  # each group's own pace: the t's location, with its prior
            if not members:
                continue
            a, s2 = alpha[i], variance[units[members[0]][0]]  # a group is a page or a message: one surface
            found = [(rests[j], units[j][7] * (nu + 1)) for j in members]
            d2 = 1 / tau2 + sum(units[j][7] for j in members) * scale / s2  # the expected information: fixed
            ns2 = nu * s2
            for _ in range(30):
                d1 = a / tau2
                for r0, vn in found:
                    r = r0 - a
                    d1 -= vn * r / (ns2 + r * r)
                step = d1 / d2
                a -= step
                if -1e-7 < step < 1e-7:
                    break
            alpha[i] = a
            total += 0.5 * a * a / tau2
        # per surface, sums over its units: the spread's gradient and information; the gradient of the mean's
        # parameters (pace, length, japanese, unknown: x = 1, length, J / words, U / words) and their information
        moments = ([0.0] * 16, [0.0] * 16)
        sums = [[1 / tau2, [0.0] * 4, {}] for _ in self.group_units]  # per group: its information, and with the rest
        info_time = self._speed_info = {}
        for j, (t3, group, _y, length, jw, iw, ws, v) in enumerate(units):
            s, s2, r = sigma[t3], variance[t3], rests[j] - alpha[group]
            total += v * (0.5 * (nu + 1) * log(1 + r * r / (nu * s2)) + log(s))
            d = -v * (nu + 1) * r / (nu * s2 + r * r)  # d(-log likelihood) / d mu
            i = v * scale / s2  # its expected information
            ux = xs_of[j]
            il, ij, iu = i * length, i * jw, i * ux
            m = moments[t3]
            m[0] += v * (1 - (nu + 1) * r * r / (nu * s2 + r * r))
            m[1] += v * 2 * nu / (nu + 3)
            m[2] += d
            m[3] += d * length
            m[4] += d * jw
            m[5] += d * ux
            m[6] += i
            m[7] += il
            m[8] += ij
            m[9] += iu
            m[10] += il * length
            m[11] += il * jw
            m[12] += il * ux
            m[13] += ij * jw
            m[14] += ij * ux
            m[15] += iu * ux
            mine = sums[group]
            mine[0] += i
            ps = mine[1]
            ps[0] += i
            ps[1] += il
            ps[2] += ij
            ps[3] += iu
            if not b:
                continue
            joined = mine[2]
            for w, q in zip(ws, qs_of[j]):
                xw = -b * q * (1 - q) * iw  # d mu / d k_w
                ixw = i * xw
                grad[w] += d * xw
                info[w] += ixw * xw
                info_time[w] = info_time.get(w, 0.0) + ixw * xw
                joined[w] = joined.get(w, 0.0) + ixw
        for t3, m in enumerate(moments):
            spread, params = (sc, (pc, lc, jj, uu)) if t3 else (sw, (pw, lw, jj, uu))
            g[spread] += m[0]
            h[spread][spread] += m[1]
            for a in range(4):
                g[params[a]] += m[2 + a]
            square = ((m[6], m[7], m[8], m[9]), (m[7], m[10], m[11], m[12]), (m[8], m[11], m[13], m[14]),
                      (m[9], m[12], m[14], m[15]))
            for a in range(4):
                for c in range(4):
                    h[params[a]][params[c]] += square[a][c]
        for i, (a_info, ps, words) in enumerate(sums):  # the profile over each group's own pace
            members = self.group_units[i]
            if not members:
                continue
            params = (pc, lc, jj, uu) if units[members[0]][0] else (pw, lw, jj, uu)
            for a in range(4):
                for c in range(4):
                    h[params[a]][params[c]] -= ps[a] * ps[c] / a_info
            for w, xw in words.items():
                info[w] -= xw * xw / a_info
                info_time[w] -= xw * xw / a_info
        self._group_info = [x[0] for x in sums]
        return total

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
        if self.review_terms or self.review_fakes:
            total += self.review_parts.penalty()
        for j, name in enumerate(SHARED_NAMES):
            mean, sd = SHARED[name]
            total += 0.5 * ((theta[j] - mean) / sd) ** 2
            g[j] += (theta[j] - mean) / sd ** 2
            h[j][j] += 1 / sd ** 2
        for j in range(len(SHARED_NAMES), len(theta)):  # the speed term's: set by _speed_alone, held here
            g[j] = 0.0
            for row in h:
                row[j] = 0.0
            h[j] = [0.0] * len(theta)
            h[j][j] = 1.0
        return total

    def _objective(self, k, theta, data=None):
        """The objective and its derivatives (as _pass gives them); data: the data's part at k and theta, if known."""
        data = data or self._pass(k, theta)
        found = [data[0], list(data[1]), list(data[2]), data[3], list(data[4]), [row[:] for row in data[5]]]
        found[0] += self._priors(k, theta, found[1], found[2], found[4], found[5])
        return tuple(found), data

    def _step(self, k, grad, info, joined, g, h):
        """The Newton step (dk, dtheta), by the Schur complement on the shared parameters; also each word's row of the
        information joining it to them, and the complement."""
        if self.frozen:
            return [-grad[i] / info[i] for i in range(len(k))], [0.0] * len(g), None, None
        schur, rest, joins = [row[:] for row in h], list(g), []
        for i in range(len(k)):
            c = {f: -x / self.widths[self.kind[i]] ** 2 for f, x in self.x[i].items()}
            for j in JOINED:
                if joined[j][i]:
                    c[j] = c.get(j, 0.0) + joined[j][i]
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
        (total, grad, info, joined, g, h), data = self._objective(k, theta)
        last, iteration = {}, 0
        for iteration in range(1, FIT_ITERATIONS + 1):
            dk, dtheta, _joins, _schur = self._step(k, grad, info, joined, g, h)
            t = min(1.0, FLOOR_STEP / max(abs(dtheta[FLOOR]), 1e-12))  # where the floor is above every word, only
            # its prior bends the objective, and a whole step would jump it past the data's optimum
            while True:  # halve the step until the objective doesn't rise
                k2 = [a + t * b for a, b in zip(k, dk)]
                theta2 = [a + t * b for a, b in zip(theta, dtheta)]
                found, data2 = self._objective(k2, theta2)
                if found[0] <= total + 1e-9 or t < 1e-3:
                    break
                t /= 2
            k, theta, data, (total, grad, info, joined, g, h) = k2, theta2, data2, found
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
                    (total, grad, info, joined, g, h), data = self._objective(k, theta, data)
            mixed = self._review_em(k, theta)
            if mixed:  # the data's terms change
                (total, grad, info, joined, g, h), data = self._objective(k, theta)
            if moved < FIT_TOLERANCE and widened < WIDTH_TOLERANCE and mixed < REVIEW_TOLERANCE:
                break
        self.k, self.theta, self.objective = k, theta, total
        _dk, _dt, joins, schur = self._step(k, grad, info, joined, g, h)
        self.var, self.info = [1 / x for x in info], list(info)
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

    def q(self, ja, surface, form=False, u=1.0, s=0.0):
        """P(lookup | read) of a word on a surface, as a conjugated form or not, a share u of the way back to its
        long-term chance since it was last read (since_read; 1: the long-term chance) and with chance s last read in the
        same context."""
        return self.q_at(self.estimate(ja)[0], surface, form, u, s)

    def q_at(self, k, surface, form=False, u=1.0, s=0.0):
        """q for a word whose long-term knowledge is k."""
        offset = (self.theta[T3] if surface == 't3' else 0.0) + (self.theta[FORM] if form else 0.0)
        return sigmoid(offset + self.theta[CONTEXT] * s + recency_log_odds(k, u, self.theta[FLOOR]))

    def known_chance(self, ja):
        """The chance a probe answers this word known: 1 - r sigmoid(probe - k) (_probe_terms), at the over-claiming
        rate of this fit's probes."""
        return 1 - self.probe_read * sigmoid(self.theta[PROBE] - self.estimate(ja)[0])

    def shared(self):
        return {name: round(v, 4) for name, v in zip(self.names, self.theta)}

    def review_summary(self):
        """The review cards as fitted: how many (real words' ungraded and graded cards and made-up words' cards with an
        outcome, before until), the components (Components.summary: each one's median reveal, spread and chance of no
        reveal, and their separation) and the offset with its sd; None without any card."""
        if not (self.review_terms or self.review_fakes or self.review_graded):
            return None
        return dict(self.review_parts.summary(), cards=len(self.review_terms), fakes=len(self.review_fakes),
                    graded=len(self.review_graded), offset=round(self.theta[REVIEW], 4),
                    offsetSd=round(self.theta_sd[REVIEW], 4))

    def speed_summary(self):
        """The speed term as fitted (None without it): its shared parameters with their sd, the groups' sd, how many
        passages and paragraphs (units) and groups it read, and how much of the words' information came from it: the
        largest share of any word's, and the share of all of it."""
        if not self.speed:
            return None
        shares = [(x, self.info[i]) for i, x in getattr(self, '_speed_info', {}).items()] if self._timed else []
        return {'shared': {n: round(self.theta[self.names.index(n)], 4) for n in SPEED_NAMES},
                'sd': {n: round(getattr(self, 'speed_sd', {}).get(n, SPEED_SHARED[n][1]), 4) for n in SPEED_NAMES},
                'group': round(self.group_sd, 4), 'units': len(self.units), 'groups': len(self.groups),
                'words': len(shares), 'most': round(max((x / max(t, 1e-12) for x, t in shares), default=0.0), 4),
                'share': round(sum(x for x, _t in shares) / max(sum(self.info), 1e-12), 4)}

    def state(self):
        """What a later fit may start from."""
        found = {'shared': dict(zip(self.names, self.theta)), 'widths': dict(self.widths),
                 'k': dict(zip(self.words, self.k))}
        if self.review_terms or self.review_fakes:
            found['review'] = self.review_parts.values()
        return dict(found, group=self.group_sd, cut=dict(getattr(self, 'cut', {}))) if self.speed else found


def nested_loglik(log, fit, pace, top):
    """The log likelihood of what was opened in the log's passages and messages shown before fit.until, under the
    reading model at `pace` and `top` and the fit's chances: a reader reads down to one depth, so everything with a
    higher read chance (at least READ_FLOOR) than an open was read. With the words ordered by read chance r_1 >= ... >=
    r_n, reading exactly the first j happens with chance r_j - r_(j+1) (r_(n+1) = 0; none: 1 - r_1), and then each
    read word is opened with its chance q. Weighted like evidence."""
    total, clocks = 0.0, read_clocks(log, pace, top, fit._chance()) if fit.recency else {}
    for event in log.events:
        if event.at >= fit.until:
            break
        if not hasattr(event, 'obs') or event.episode or not event.obs:
            continue
        rows = sorted(((max(READ_FLOOR, _chance(o.ms, o.words, o.share, by_surface(pace, o.surface),
                                                by_surface(top, o.surface))), o.y,
                        min(1 - 1e-9, max(1e-9, fit.q(o.word, o.surface, o.form, *clocks.get(id(o), (1.0, 0.0))))))
                       for o in event.obs), key=lambda t: -t[0])
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
    """The reading model by profile likelihood, one per surface ({surface: pace}, {surface: top}): over the grid PACES x
    READ_MAXES of one surface, the other's held at what it has, the whole log is fitted at each point (Fit, starting
    from the point before, without recency: pace and cap are about time on screen, and dozens of fits with it would take
    most of a refresh on a long log; nor the speed term, whose times read no read chance) and scored by the likelihood
    of what was opened under the reading model the read chances come from (nested_loglik, both surfaces). The web first,
    then chat. Each search scores every other point first (the defaults among them), then the neighbours of the best
    until it stays. A surface keeps the defaults until it has TUNE_MIN_OPENS opens, its best pair beats the defaults by
    TUNE_MARGIN, and it lies inside the grid: on its edge the likelihood has no maximum in the plausible range, a sign
    the reading model doesn't fit (on the live log of 2026-10-20 chat's keeps rising past 5,000 ms a word), and uses it
    only with SURFACE_TUNING (else it is ready, reported, and the defaults stay). The surfaces differ: a web page stays
    on screen while it is skimmed or not read at all. Returns {pace, top (per surface), tuned (whether any surface uses
    its own), ready, best, inside, opens (per surface), grid (every point scored: surface, pace, top, loglik)}. free:
    the free flags the fits take (Fit)."""
    pace, top = {s: PACE_MS for s in SURFACES}, {s: READ_MAX for s in SURFACES}
    grid, start = [], None
    out = {'tuned': False, 'best': {}, 'inside': {}, 'opens': {}, 'ready': {}}
    for surface in SURFACES:
        opens = sum(o.y for e in log.events if hasattr(e, 'obs') and not e.episode and e.at < until
                    and e.surface == surface for o in e.obs)
        scores = {}

        def score(i, j):
            nonlocal start
            if (i, j) not in scores and 0 <= i < len(PACES) and 0 <= j < len(READ_MAXES):
                p, t = dict(pace, **{surface: PACES[i]}), dict(top, **{surface: READ_MAXES[j]})
                fit = Fit(log, until, p, t, start=start, free=free, recency=False, speed=False)
                start = fit.state()
                scores[(i, j)] = nested_loglik(log, fit, p, t)
                grid.append({'surface': surface, 'pace': PACES[i], 'top': READ_MAXES[j],
                             'loglik': round(scores[(i, j)], 3)})

        for j in range(0, len(READ_MAXES), 2):
            for i in range(0, len(PACES), 2):
                score(i, j)
        best = None
        while best != max(scores, key=scores.get):
            best = max(scores, key=scores.get)
            for i in (best[0] - 1, best[0], best[0] + 1):
                for j in (best[1] - 1, best[1], best[1] + 1):
                    score(i, j)
        default = scores[(PACES.index(PACE_MS), READ_MAXES.index(READ_MAX))]
        inside = 0 < best[0] < len(PACES) - 1 and 0 < best[1] < len(READ_MAXES) - 1
        ready = opens >= TUNE_MIN_OPENS and scores[best] - default >= TUNE_MARGIN and inside
        if ready and SURFACE_TUNING:
            pace[surface], top[surface] = PACES[best[0]], READ_MAXES[best[1]]
        out['tuned'] = out['tuned'] or ready and SURFACE_TUNING
        out['ready'][surface] = ready
        out['best'][surface] = {'pace': PACES[best[0]], 'top': READ_MAXES[best[1]], 'loglik': round(scores[best], 3),
                                'default': round(default, 3)}
        out['inside'][surface], out['opens'][surface] = inside, round(opens, 1)
    return dict(out, pace=pace, top=top, grid=grid)


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
    without one gets its prior's, free_flag), the sentence prices then (sentence_prices) and the clock of each word in
    each passage or message ({(key, ja): (u, s)}, read_clocks), the one at key now (set key before shown_record)."""

    def __init__(self, fit, flags, prices, clocks=None):
        self.fit, self.flags, self.prices, self.clocks, self.key = fit, flags, prices, clocks or {}, None

    def chance(self, ja, surface, _at=None):
        return self.fit.q(ja, surface, False, *self.clocks.get((self.key, ja), (1.0, 0.0)))

    def is_free(self, ja):
        if ja not in self.flags:
            k, sd = self.fit.estimate(ja)
            self.flags[ja] = free_flag(k, sd, None, self.fit.prior(ja)[0])
        return self.flags[ja]

    def sentence_price(self, surface):
        return self.prices[surface]['p']


def fitted_records(rows, contexts, match, log, since, now_ms, pace, top, sentences, flags=None, start=None,
                   done=(), offers=None):
    """calibrate's records (shown_record) of the passages and messages shown from `since` on, each as the model stood
    at the start of its day: the log (an evaluate.Log) fitted on everything before (Fit, each day starting from the
    fit before: start, a fit's state, for the first; refresh passes its fit's, which is from nothing once a day, so the
    floor's basin is the whole log's; with the free flags of the day before), each word at its clock in the passage or
    message (read_clocks: its current chance), the free flags carried from day to day (free_flags, from flags, else the
    priors'), and the sentence prices from the sentence ends before (sentence_prices of sentence_events). done: days to
    skip (their sums are known); offers: the chat briefs' topical words (Offers), for the topical class. Returns
    (records, {day: the free flags at its start})."""
    out, starts, flags, day, standing = [], {}, dict(flags or {}), None, None
    for at, key, context, items in showings(rows, contexts, now_ms):
        if at < since or day_of(at) in done:
            continue
        if day_of(at) != day:
            day = day_of(at)
            fit = Fit(log, day_start(day), pace, top, start=start, free=flags)
            start = fit.state()
            flags.update(free_flags(fit, fit.words, flags))
            starts[day] = dict(flags)
            found = read_clocks(log, pace, top, fit._chance())
            clocks = {(event.key, o.word): found[id(o)] for event in log.events
                      if hasattr(event, 'obs') and day_of(event.at) == day for o in event.obs}
            standing = Standing(fit, dict(flags), sentence_prices(sentences, day_start(day)), clocks)
        standing.key = key
        out.append(shown_record(at, key[0], context, items, standing, match, pace, top, offers))
    return out, starts


def day_keys(log, rows, changes, since, now_ms, pace, top, challenge, offers=None):
    """{day: key} for the days from `since` to yesterday: what their records depend on (the model, the reading model,
    the challenge, and how much of the log, its interactions, intensity changes, topical offers (offers: Offers) and
    review cards came before the day's end), so a day's sums are reused while its key holds (refresh, knowledge.json
    "daily")."""
    import bisect
    ats, row_ats, reviewed = [e.at for e in log.events], sorted(r['at'] for r in rows), review_log(log)[3]
    counts, total = [], 0
    for e in log.events:
        total += len(getattr(e, 'obs', ()))
        counts.append(total)
    out, day = {}, day_of(since)
    while day_start(day) < day_start(day_of(now_ms)):
        end = day_start(day) + DAY_MS + 3 * 3_600_000  # past a daylight-saving change, then back to midnight
        end = day_start(day_of(end))
        n = bisect.bisect_left(ats, end)
        out[day] = (f'{model_name()} {pace} {top} {challenge} {n} {counts[n - 1] if n else 0} '
                    f'{bisect.bisect_left(row_ats, end)} {sum(1 for c in changes if c["at"] < end)}'
                    + (f' r{sum(1 for at, _ja in reviewed if at < end)}' if reviewed else '')
                    + (f' {bisect.bisect_left(offers.times, end)}' if offers is not None and offers.times else ''))
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
    (threads.py, the "thread" changes) counts as one more chat lighter press (threadsOff: how many chats), and so does
    each press turning English only on in chat (intensity.py, the "english" changes; englishOn: how many).
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
    english_on = sum(1 for c in changes if c['surface'] == 'english' and c['on'] and c['at'] >= since)
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
        lighter += threads_off + english_on if surface == 'chat' else 0
        verdict = ('too heavy' if lighter >= 5 and lighter >= 3 * max(1, heavier) else
                   'too light' if heavier >= 5 and heavier >= 3 * max(1, lighter) else None)
        presses[surface] = {'lighter': lighter, 'heavier': heavier, 'hoursAt': {str(k): round(v / 3_600_000, 1)
                                                                               for k, v in sorted(time_at.items())},
                            'verdict': verdict}
    presses['chat']['threadsOff'] = threads_off
    presses['chat']['englishOn'] = english_on
    return {'days': days, 'passages': len(passages), 'bins': bins, 'jitter': jitter, 'jitterBefore': before,
            'presses': presses,
            'classes': lookup_classes([r for r in shown or [] if r['at'] >= since], changes, challenge,
                                      {day: t for day, t in (totals or {}).items() if day >= day_of(since)})}


def chat_level(changes, at):
    """The chat's level at `at`: the last chat intensity change before it, while it lasted; else normal."""
    level = 0
    for c in changes:
        if c['surface'] == 'chat' and c['at'] <= at:
            level = c['level'] if c['level'] and c['until'] > at else 0
    return level


def chat_budget(changes, at, challenge=0, level=None):
    """The chat's lookup budget per 100 words at `at`: its level then (chat_level), or the level a message reports it
    was shown at (render-time levels), with normal's budget the challenge when set. None at full (no budget)."""
    return intensity.budget(chat_level(changes, at) if level is None else level, challenge or None)


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
        budget = r['budget'] if r['surface'] == 'browser' else chat_budget(changes, r.get('shown', r['at']), challenge,
                                                                             r.get('level'))
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
        n, on = p.get('threadsOff'), p.get('englishOn')
        chats = f', counting {n} chat{"s" if n != 1 else ""} with Japanese turned off' if n else ''
        chats += (f'{" and" if n else ", counting"} {on} press{"es" if on != 1 else ""} of English only' if on else '')
        lines.append(f'{name}: {p["lighter"]} lighter and {p["heavier"]} heavier presses ({hours}){chats}{verdict}.')
    import sentence_model  # here: it imports this module
    twins = sentence_model.exploration_text(report.get('twins'))
    if twins:
        lines.append(twins)
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
                lines.append(f'{label}none yet (no word a brief offered as topical was shown in the hour after)'
                             if surface == 't3' else f'{label}none (chat only)')
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


def model_name():
    """MODEL, and ' speed' while SPEED is on: switching the speed term redoes the daily sums and fits from nothing."""
    return MODEL + (' speed' if SPEED else '')


MODEL = 'fit 6'  # the reading model is chosen again, and the daily sums redone, when this changes (fit 3: the recency
                 # term, with the prior by origin and word features; fit 4: the monthly probes' answers; fit 5: a
                 # reading model per surface; fit 6: the review cards)


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

    knowledge.json: per word of the word state, k and its sd (the fit's, else its prior's: the long-term knowledge),
    mu (its prior mean), free (known at a glance on its long-term chance, with hysteresis against the last refresh's
    flag: free_flag), n (evidence weight), obs, opened, recent (opens in the last RECENT_DAYS days), last and history,
    read (when it was last read: [shown, chance] per showing of the last LONG_DAYS, word_reads; a consumer gets its
    current chance from it, current_chance), and id (the JMdict id of its dictionary entry, word_entries, which the next
    refresh reuses); also each word a probe answered or a review card showed that the word state doesn't have (probed,
    with its entry's id: reading.starting_k takes its k for that entry); offset (the web's 0, chat's fitted); recency
    (the floor, the context offset, LONG_DAYS, SOFT_MIN and SPACING_P); fit (the shared parameters with their sd, the prior widths,
    and whether it started from nothing, as the day's first does); prior (prior_table); sentencePrice per surface (sentence_prices; sentences has their counts);
    reading (tune, once a day); calibration, with daily, its sums for each day before today (day_totals), reused while
    the day's key holds (day_keys), and dailyFree, the free flags at the start of the last of those days, which today's
    records carry on from; unlisted; spend (spending: the budget's use that sets intake); moves (the label changes
    made, the last 200); review (review_report: the review cards' components as fitted, with their separation, and
    the probe's rolling estimate of the goal from them); grammar (sentences_and_grammar: the sentence-open model, the
    weave's limit, intake's recent rate and the pattern statuses after intake); per word also reviews, its review
    cards. T3 messages shown during an English-only spell are left out (without_english)."""
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
        rows, everything = feedback.load(feedback.default_path())
        changes = feedback.intensity_changes(feedback.default_path())
        english = [c for c in changes if c['surface'] == 'english']
        contexts = without_english(everything, english, now_ms)
        reviews = feedback.reviews(feedback.default_path())
        state = fwlib.load_state()
        previous = load()
        match = inflection.lexicon(state['words']).entry  # every lexicon word, queue words too
        intervals, commands = anki_intervals(), read_commands()
        reading = previous.get('reading')
        fresh = isinstance(reading, dict) and reading.get('day') == today and reading.get('model') == model_name()
        pace, top = (reading['pace'], reading['top']) if fresh else (PACE_MS, READ_MAX)
        log = evaluate.extract(rows, contexts, state['words'], intervals, commands, now_ms, pace, top,
                               state.get('probes'), reviews)
        known = previous.get('words') if isinstance(previous.get('words'), dict) else {}
        before = {ja: e['free'] for ja, e in known.items() if isinstance(e, dict) and isinstance(e.get('free'), bool)}
        log.entries = word_entries(state['words'], {ja: e['id'] for ja, e in known.items() if isinstance(e, dict)
                                                    and isinstance(e.get('id'), int)})
        if not fresh:  # the reading model, once a day
            reading = dict(tune(log, now_ms, before), day=today, model=model_name())
            pace, top = reading['pace'], reading['top']
        start = None
        if fresh and (previous.get('fit') or {}).get('model') == model_name():  # start from the last refresh's fit; the
            # day's first fits from nothing, from two floors (Fit), in case the data now favour the other
            start = {'shared': previous['fit'].get('shared'), 'widths': previous['fit'].get('widths'),
                     'k': {ja: e['k'] for ja, e in known.items() if isinstance(e, dict) and e.get('n')}}
        fit = Fit(log, now_ms, pace, top, start=start, free=before)
        stats, reads = word_stats(log, now_ms), word_reads(log, fit, now_ms)
        flags = free_flags(fit, [w['ja'] for w in state['words']], before)
        sentences = sentence_events(rows, contexts, now_ms, pace, top)
        prices = sentence_prices(sentences, now_ms)
        challenge = challenge_of(state)
        briefs = feedback.chat_briefs(feedback.default_path())
        offers = Offers(briefs)
        since = now_ms - CALIBRATION_DAYS * DAY_MS
        keys = day_keys(log, rows, changes, since, now_ms, pace, top, challenge, offers)
        cached = previous.get('daily') if isinstance(previous.get('daily'), dict) else {}
        done = {day: cached[day]['totals'] for day, key in keys.items()
                if isinstance(cached.get(day), dict) and cached[day].get('key') == key}
        # the free flags to carry: at the start of the last day summed, else the last refresh's
        carried = previous.get('dailyFree') if isinstance(previous.get('dailyFree'), dict) else {}
        flags_then = carried.get('free') if carried.get('day') in done and isinstance(carried.get('free'), dict) else \
            before
        records, starts = fitted_records(rows, contexts, match, log, since, now_ms, pace, top, sentences, flags_then,
                                         fit.state(), set(done), offers)
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
            read = [[shown, round(chance, 3)] for shown, chance, _same in (reads.get(ja) or ((), 1.0))[0]
                    if chance >= 0.001]
            if read:  # when it was last read, for its current chance (current_chance)
                words[ja]['read'] = read
        for _at, ja in review_log(log)[3]:
            if ja in words:
                words[ja]['reviews'] = words[ja].get('reviews', 0) + 1
        outside = dict(review_log(log)[2], **probe_log(log)[2])
        for ja, entry in outside.items():  # a probed or reviewed word outside the word state, by its entry's id
            i = fit.index.get(ja)
            if ja not in words and i is not None:
                k, sd = fit.estimate(ja)
                mean = fit.prior(ja)[0]
                words[ja] = {'k': round(k, 3), 'sd': round(sd, 3), 'mu': round(mean, 3),
                             'free': free_flag(k, sd, before.get(ja), mean), 'n': round(fit.n[i], 2), 'obs': 0,
                             'id': entry['id'], 'probed': True}
        shrank = len(rows) < previous.get('rows', 0)  # a truncated mirror must not move words
        spend = spending(contexts, briefs, now_ms)
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
        grammar_report = sentences_and_grammar(state, rows, contexts, everything, english, now_ms, pace, top, today,
                                               previous.get('grammar'), apply=not shrank, changes=changes)
        history = [m for m in previous.get('moves', []) if isinstance(m, dict)]
        knowledge = {
            'updated': now, 'rows': len(rows),
            'offset': {'browser': 0.0, 't3': round(fit.theta[T3], 3)},
            'fit': {'model': model_name(), 'shared': fit.shared(),
                    'sd': {name: round(sd, 4) for name, sd in zip(SHARED_NAMES, fit.theta_sd)},
                    'widths': {kind: round(w, 4) for kind, w in fit.widths.items()}, 'pace': pace, 'top': top,
                    'words': len(fit.words), 'terms': len(fit.terms[0]), 'iterations': fit.iterations,
                    'cold': start is None,
                    'seconds': round(fit.seconds, 3), **({'speed': fit.speed_summary()} if fit.speed else {})},
            'prior': prior_table(fit),
            'recency': {'floor': round(fit.theta[FLOOR], 4), 'context': round(fit.theta[CONTEXT], 4),
                        'days': LONG_DAYS, 'softMin': SOFT_MIN, 'spacing': SPACING_P},
            'sentencePrice': {surface: v['p'] for surface, v in prices.items()}, 'sentences': prices,
            'challenge': challenge, 'spend': spend, 'moves': (history + applied)[-200:], 'reading': reading,
            'calibration': dict(calibrate(rows, contexts, changes, now_ms, totals=totals, challenge=challenge),
                                twins=(grammar_report or {}).get('exploration')),
            'daily': daily, 'dailyFree': daily_free,
            'unlisted': unlisted(rows, match, now_ms),
            'review': review_report(fit, log.reviews, now_ms),
            'grammar': grammar_report,
            'words': words,
        }
        fwlib.write_json(path(), knowledge)
        return knowledge


def without_english(contexts, english, now_ms):
    """The contexts with every T3 message shown during an English-only spell (intensity.english_windows) left out:
    its Japanese wasn't on screen, so it says nothing about the words (nor its opens: they had no message). A message
    that reports the level it was shown at (render-time levels) says itself what was on screen: the collector drops
    its items at -3, and it stays."""
    windows = intensity.english_windows(english, now_ms)
    if not windows:
        return contexts
    hidden = lambda c: c['surface'] == 't3' and c.get('level') is None and c.get('shown_at') and any(
        a <= c['shown_at'] < b for a, b in windows)
    return {key: c for key, c in contexts.items() if not hidden(c)}


def sentences_and_grammar(state, rows, contexts, everything, english, now_ms, pace, top, today, before, apply=True,
                          changes=()):
    """knowledge.json "grammar" (sentence_model.summary): the sentence-open model fitted to the chat replies' sentences
    (sentence records joined to the T3 messages shown, each at the level it was shown at; the Japanese sentences of the
    messages named by each press of English only as weak opens, as shown at the chat level then: changes, the
    intensity changes), and with apply, the grammar op intake makes (hub.run), with the statuses it leaves (statuses),
    and exploration (sentence_model.exploration: the twins placed at random against those the model ranked). A
    failure keeps the last report (before): the refresh stands without it."""
    try:
        import grammar
        import sentence_model
        records = feedback.sentence_records(feedback.default_path())
        presses = [c for c in english if c['on']]
        found = sentence_model.units(showings(rows, contexts, now_ms), sentence_model.records_by_fp(records), pace,
                                     top, presses, everything, changes)
        statuses = grammar.statuses(state)
        model = sentence_model.fit(found, statuses, now_ms)
        counts = sentence_model.frequency(records, now_ms)
        op = sentence_model.intake(state, model, found, counts, now_ms) if apply else None
        if op:
            import hub  # here: hub imports this module
            result = hub.run(hub.stamp(dict(op, day=today, at=now_ms)))
            if result.get('ok'):
                grammar.apply(state, op, today)
                statuses = grammar.statuses(state)
            else:
                print(f'Japanese Weave: grammar not changed: {result.get("error")}', file=sys.stderr)
        return dict(sentence_model.summary(model, found, counts, now_ms, state), statuses=statuses,
                    exploration=sentence_model.exploration(found))
    except Exception as error:
        print(f'Japanese Weave: no sentence model ({error.__class__.__name__}: {error})', file=sys.stderr)
        return before if isinstance(before, dict) else None


def review_report(fit, reviews, now_ms):
    """knowledge.json "review": the review cards as the fit has them (model: Fit.review_summary, or None), and the
    probe's estimate of the goal from the probe and made-up words' cards of the last probe.ROLLING_DAYS (probe: probe.
    rolling, computed without the fit); None without any reviewed card."""
    if not reviews:
        return None
    out = {'model': fit.review_summary(), 'probe': None}
    try:
        import probe  # here: the hook needn't load the dictionary
        out['probe'] = probe.rolling(reviews, now_ms)
    except Exception as error:  # the estimate is a report: the refresh stands without it
        print(f'Japanese Weave: no rolling probe estimate ({error.__class__.__name__}: {error})', file=sys.stderr)
    return out


def start_refresh():
    """A background `fw.py refresh`, never waited on (the native host and the hook call this)."""
    if os.environ.get('JAPANESE_WEAVE_NO_REFRESH'):
        return
    import subprocess
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().with_name('fw.py')), 'refresh'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=dict(os.environ))


def current_chance(entry, recency, now_ms, offset=0.0):
    """A knowledge.json word's P(lookup | read) at now_ms (ms) on a surface (its offset), in a new page or reply: its
    long-term knowledge k a share of the way back since it was last read (since_read of its "read": [shown, chance it
    was the last read] for its showings of the last LONG_DAYS), under the fit's floor (knowledge.json "recency").
    Without them, its long-term chance."""
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    floor = recency.get('floor') if isinstance(recency, dict) else None
    reads = [(r[0], r[1], False) for r in entry.get('read') or []
             if isinstance(r, list) and len(r) == 2 and number(r[0]) and number(r[1])]
    if not reads or not number(floor) or now_ms is None:
        return sigmoid(offset - entry['k'])
    u, _s = since_read(reads, max(0.0, 1 - sum(chance for _at, chance, _same in reads)), now_ms)
    return sigmoid(offset + recency_log_odds(entry['k'], u, floor))


def word_estimate(knowledge, ja, surface='browser', now_ms=None):
    """(p, k, sd) for a word, or None when knowledge.json has nothing on it. p is its lookup chance on the surface,
    kept on its free flag's side of fwlib.FREE_P (free_flag; without a flag, its long-term chance's side): the flag,
    with its hysteresis, says whether the word is known at a glance, judged on its long-term chance, and p how likely
    a lookup is otherwise. So a planner comparing p with FREE_P (weave.lists) agrees with the flag. With now_ms, a word
    that isn't free gets its current chance (current_chance), still at least FREE_P: one read an hour ago is near the
    floor, under SPACING_P, and waits (weave.lists); it isn't free."""
    entry = (knowledge.get('words') or {}).get(ja)
    if not entry:
        return None
    offset = (knowledge.get('offset') or {}).get(surface, 0.0)
    p = sigmoid(offset - entry['k'])
    if entry['free'] if isinstance(entry.get('free'), bool) else p < fwlib.FREE_P:
        return min(p, fwlib.FREE_P * 0.999), entry['k'], entry['sd']
    if now_ms is not None:
        p = current_chance(entry, knowledge.get('recency'), now_ms, offset)
    return max(p, fwlib.FREE_P), entry['k'], entry['sd']


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
