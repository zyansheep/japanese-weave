"""Adaptive difficulty: per-word knowledge estimated from the gloss interaction log (see docs/adaptive-difficulty.md).
Python 3.9+, standard library only.

Model: P(peek) = sigmoid(offset_s - k_w). k_w is the reader's knowledge of a word on a log-odds scale, learned by
Elo-style updates. Every browser passage shown (its "shown" record) and every T3 message with a word count counts,
opened or not. A word left unopened is evidence the reader knew it in proportion to the chance it was read: certain
down to the lowest opened line (halving per line below it), else from time on screen against the time reading down to
the word takes (read_chance: the passage's stays in the browser; stay_chance: in T3, the stays of the paragraph the
word is in). The update is the gradient of log P(what happened) with P(open) = P(read) * P(peek), and the T3 offset
follows the same gradient. A T3 message without a word count (logged before T3 sent them, and not backfilled) is
replayed as episodes, which exist only when something was opened: there an update is conditioned on the episode having
at least one open, and only the line rule applies. Every lexicon word is scored, words not introduced yet included,
and a glossed word is matched to its entry through its dictionary form (inflection.py: T3 glosses conjugated verbs
whole). refresh() replays the whole log from scratch (deterministic, retunable), writes knowledge.json, steers the
peek-rate dial, makes a few guarded status moves, and reports whether the default amount of Japanese looks right
(calibration) and which opened words the lexicon doesn't have (unlisted).
"""
import json
import math
import os
from pathlib import Path
import statistics
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
FORGET_DAYS = 45           # k decays toward its prior, downward only, with this half-life
EVIDENCE_DAYS = 30         # evidence weight decays with this half-life, so unseen words grow uncertain again
ETA_MAX, ETA_MIN, ETA_SLOPE = 1.2, 0.2, 0.3
T3_RATE, T3_LIMIT = 0.02, 1.5
KNOWN_COMMAND_K, LEARN_COMMAND_K = 2.5, 0.0
TARGET_P = 0.25            # the predicted peek chance learning words are aimed at
Z80 = 0.8416               # one-sided 80% bound
MOVE_CAP = 3               # evidence moves per direction per day
IMMUNE_DAYS = 14           # no evidence moves this soon after a known or learn command
RECENT_DAYS = 14
RATE_DAYS = 3              # the dial's controller looks at the last 3 days of browser reading
RATE_MIN_WORDS = 300
SCALE_RANGE = (0.25, 4.0)
CHALLENGES = (0, 0.5, 1, 1.5, 2, 3)
PUNCTUATION = set('。？！?!.')
PACE_MS = 300              # ms per word a reader takes on a passage with Japanese in it (about 200 words a minute)
READ_MAX = 0.7             # the most a passage's time on screen alone says about a word in it being read
SLACK_PX = 10              # how far above a T3 paragraph's band a word's top may sit and still be in it (stay_chance)
PACES = (150, 250, 400, 650)   # the reading model's grid, scored by how well it predicts opens (tune)
READ_MAXES = (0.5, 0.7, 0.9)
TUNE_MIN_OPENS = 60        # browser opens before the tuned reading model replaces the defaults
CALIBRATION_DAYS = 30
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


def read_chance(context, y, line, pace=PACE_MS, top=READ_MAX):
    """The chance a word at height y of a passage was read, from the passage's time on screen alone: the time reading
    down to that line takes at `pace` against the time a reader could have been reading the passage."""
    height = context.get('height') or 0
    return _chance(context.get('active') or 0, context.get('words') or 0, (y + line) / height if height > 0 else 1.0,
                   pace, top)


def stay_chance(context, y, line, pace=PACE_MS, top=READ_MAX):
    """The chance a word at height y of a T3 message was read, from the stays on screen of the paragraph it is in (the
    stays whose band holds the word's top, from half a line above the band, at most SLACK_PX, down to its bottom; their
    ms added), as read_chance does for a passage. The paragraph's words are the stays' count when sent, else the
    message's words shared out by the band's height over the distinct bands on record. No stay holds the word: 0."""
    stays = context.get('stays') or []
    slack = min(line / 2, SLACK_PX)
    mine = [s for s in stays if s['height'] > 0 and s['y'] - slack <= y < s['y'] + s['height']]
    if not mine:
        return 0.0
    band = max(mine, key=lambda s: s['ms'])  # one paragraph; its bands differ only if its layout changed
    counted = [s['words'] for s in mine if s.get('words')]
    if counted:
        words = max(counted)
    else:
        bands = sum(h for _y, h in {(s['y'], s['height']) for s in stays}) or context.get('height') or 0
        words = (context.get('words') or 0) * band['height'] / bands if bands > 0 else 0
    return _chance(sum(s['ms'] for s in mine), words, (y - band['y'] + line) / band['height'], pace, top)


def _line_height(ys):
    gaps = [b - a for a, b in zip(sorted(set(ys)), sorted(set(ys))[1:]) if b - a > 2]
    return statistics.median(gaps) if gaps else 20.0


def matcher(listed):
    """listed as a function from a glossed word to its entry or None: a set of words matches as written."""
    return listed if callable(listed) else (lambda word: word if word in listed else None)


def observations(surface, context, items, listed, read=None):
    """[(entry, y, weight)] for the lexicon words of one passage or message. listed: a set of words, or a function
    from a glossed word (perhaps a conjugated form) to its entry (matcher).

    Opened: y 1 for a pin or flip or a peek open at least GLANCE_MS, GLANCE_Y for a shorter peek; T3 peeks under
    T3_NOISE_MS don't count. Unopened: y 0, weighted by the chance it was read: 1 down to the lowest opened line,
    halving per line below it, or read(y, line) when that is higher (time on screen: read_chance, stay_chance). Without
    read, nothing opened means [] (an episode exists only because something was opened). The unopened words of a
    sentence whose whole-sentence translation was opened get nothing (it was read in English); its opened words still
    count as opened."""
    match = matcher(listed)
    notes = context['annotations']
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
        else:
            weight = 0.0
            if lowest is not None:
                below = y_pos - lowest
                weight = 1.0 if below <= line / 2 else 0.5 ** math.ceil(below / line)
            if read is not None:
                weight = max(weight, read(y_pos, line))
            y = 0.0
        if weight < 0.05:
            continue
        old = best.get(word)
        if old is None or (y, weight) > (old[0], old[1]):
            best[word] = (y, weight)
    return [(word, y, weight) for word, (y, weight) in best.items()]


# ---- replay ----

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


def replay(rows, contexts, listed, intervals, commands, now_ms, pace=PACE_MS, top=READ_MAX, losses=None, chat=None):
    """Every word's knowledge after the whole log; also the T3 offset. listed: as in observations. pace, top: the
    reading model (read_chance, stay_chance). losses: a list to append, for every listed word of every browser passage
    shown, (opened, predicted chance of an open) before the update, with the read chance from time on screen alone: how
    well the model predicts (tune). chat: a list to append, for every T3 message shown (with a word count), {at, words,
    lookups: its opens (T3 peeks under T3_NOISE_MS left out), expected: the sum over its listed words of read chance
    from time on screen alone times P(peek), before the update, observed: its opened listed words (a glance counts
    GLANCE_Y)} (calibrate)."""
    match = matcher(listed)
    words = {}
    offset = {'browser': 0.0, 't3': 0.0}

    def get(ja):
        if ja not in words:
            words[ja] = Word(prior(intervals.get(ja)))
        return words[ja]

    timeline = [(e[2][-1]['at'], 0, e) for e in episodes(rows, contexts, now_ms)]
    timeline += [(e[0], 2, e) for e in showings(rows, contexts, now_ms)]
    timeline += [(c['at'], 1, c) for c in commands if match(c['word'])]
    for at, kind, item in sorted(timeline, key=lambda t: (t[0], t[1])):
        if kind == 1:
            word = get(match(item['word']))
            word.age(at)
            word.command_at = at
            if item['kind'] == 'known':
                word.k, word.n = max(word.k, KNOWN_COMMAND_K), word.n + 2
            else:
                word.k = min(word.k, LEARN_COMMAND_K)
            continue
        if kind == 2:
            _at, (surface, _view, _context), context, items = item
            chance = stay_chance if surface == 't3' else read_chance
            read = lambda y, line, c=context, f=chance: f(c, y, line, pace, top)
            obs = observations(surface, context, items, listed, read)
            line = _line_height([n[2] for n in context['annotations']])
            for ja, _y, _w in obs:
                get(ja).age(at)
            if chat is not None and surface == 't3':
                chat.append(_chat_record(at, context, items, obs, words, intervals, match, offset['t3'],
                                         lambda y: read(y, line)))
            residual = total = 0.0
            for ja, y, weight in obs:
                word = words[ja]
                q = sigmoid(offset[surface] - word.k)
                if losses is not None and surface == 'browser':
                    alone = max(0.05, read_chance(context, _y_of(context, ja, match), line, pace, top))
                    losses.append((y > 0, min(1 - 1e-6, max(1e-6, alone * q))))
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
                offset['t3'] = max(-T3_LIMIT, min(T3_LIMIT, offset['t3'] + T3_RATE * residual / total))
            continue
        (surface, _view, _context), context, items = item
        obs = observations(surface, context, items, listed)
        if not obs:
            continue
        for ja, _y, _w in obs:
            get(ja).age(at)
        p = {ja: sigmoid(offset[surface] - words[ja].k) for ja, _y, _w in obs}
        none = 1.0
        for value in p.values():
            none *= 1 - value
        engaged = max(1 - none, 1e-6)  # chance of at least one peek: only such episodes are logged
        residual = total = 0.0
        for ja, y, weight in obs:
            word = words[ja]
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
            offset['t3'] = max(-T3_LIMIT, min(T3_LIMIT, offset['t3'] + T3_RATE * residual / total))
    for word in words.values():
        word.age(now_ms)
        word.recent = [t for t in word.recent if t >= now_ms - RECENT_DAYS * DAY_MS]
    return words, offset


def _y_of(context, ja, match):
    return next((n[2] for n in context['annotations'] if match(n[0]) == ja), 0)


def _chat_record(at, context, items, obs, words, intervals, match, offset, read):
    """One T3 message for calibrate: its lookups, and the model's expected lookups of its listed words against them."""
    best = {}
    for written, _x, y in context['annotations']:
        ja = match(written)
        if ja is not None:
            best[ja] = max(best.get(ja, 0.0), read(y))
    expected = 0.0
    for ja, r in best.items():
        word = words.get(ja)
        expected += r * sigmoid(offset - (word.k_at(at) if word is not None else prior(intervals.get(ja))))
    lookups = sum(1 for r in items if not (r['kind'] == 'peek' and r['dwell_ms'] < T3_NOISE_MS))
    return {'at': at, 'words': context.get('words') or 0, 'lookups': lookups, 'expected': expected,
            'observed': sum(y for _ja, y, _w in obs if y > 0)}


def tune(rows, contexts, listed, intervals, commands, now_ms):
    """The reading model (pace, top) that best predicts browser opens: mean log loss of P(open) = P(read) * P(peek)
    over every listed word of every passage shown, each predicted before its own update. Returns {pace, top, opens,
    words, loss, grid}; pace and top stay the defaults until there are TUNE_MIN_OPENS opens to judge by."""
    grid = []
    opens = scored = 0
    for pace in PACES:
        for top in READ_MAXES:
            losses = []
            replay(rows, contexts, listed, intervals, commands, now_ms, pace, top, losses)
            if not losses:
                return {'pace': PACE_MS, 'top': READ_MAX, 'opens': 0, 'words': 0, 'loss': None, 'grid': []}
            opens, scored = sum(o for o, _ in losses), len(losses)
            loss = -sum(math.log(p if o else 1 - p) for o, p in losses) / len(losses)
            grid.append({'pace': pace, 'top': top, 'loss': round(loss, 5)})
    best = min(grid, key=lambda g: g['loss'])
    ready = opens >= TUNE_MIN_OPENS
    return {'pace': best['pace'] if ready else PACE_MS, 'top': best['top'] if ready else READ_MAX, 'tuned': ready,
            'best': best, 'opens': opens, 'words': scored, 'grid': grid}


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


def calibrate(rows, contexts, changes, now_ms, days=CALIBRATION_DAYS, chat=None):
    """Evidence on the default amount of Japanese, over the last `days` days.

    reading: browser passages shown with some time on screen, by the share of their words in Japanese (the first bin,
    low = high = 0, is passages planned that got none): how many, the median ms of screen time per word, and opens
    per 100 words. Screen time overstates reading (a passage stays on screen while its neighbours are read), so
    compare bins with each other, not with a reading speed.
    jitter: passages the extension randomly thinned (plan.jitter < 1, also to none) against untouched ones, among those
    with candidates (thinning can't change a passage without any). The thinning is random, so the difference between
    the two groups is caused by the amount alone; gap: how much less Japanese the thinned ones have (share), once both
    groups have THIN_MIN_PASSAGES; effect: change in log ms per word per unit of share, which approximates how much
    one more point of Japanese slows reading, once the gap is over THIN_MIN_GAP.
    presses: the reader's heavier/lighter presses per surface (intensity.py; one into off is lighter, into full
    heavier) and the share of the time spent at each level, from the ops log. A chat with Japanese turned off
    (threads.py, the "thread" changes) counts as one more chat lighter press (threadsOff: how many chats).
    chat: T3 messages shown (with a word count), from replay's chat list: how many, their words, lookups and lookups
    per 100 words, how many had none, and the lookups of listed words the model expected before seeing each message
    against those observed (how well calibrated it is on chat).
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
                         'candidates': plan.get('candidates') or 0})

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
    offered = [p for p in normal if p['candidates'] > 0]
    jittered = [p for p in offered if p['jitter'] is not None and p['jitter'] < 1]
    untouched = [p for p in offered if p['jitter'] == 1]
    jitter = {'thinned': summary(jittered), 'untouched': summary(untouched), 'gap': None, 'effect': None}
    if len(jittered) >= THIN_MIN_PASSAGES and len(untouched) >= THIN_MIN_PASSAGES:
        mean = lambda xs: sum(xs) / len(xs)
        gap = mean([p['share'] for p in untouched]) - mean([p['share'] for p in jittered])
        jitter['gap'] = round(gap, 4)
        if gap > THIN_MIN_GAP:
            slow = mean([math.log(p['msPerWord']) for p in untouched]) - mean([math.log(p['msPerWord']) for p in jittered])
            jitter['effect'] = round(slow / gap, 3)
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
    messages = [m for m in chat or [] if m['at'] >= since]
    shown, lookups = sum(m['words'] for m in messages), sum(m['lookups'] for m in messages)
    replies = {'messages': len(messages), 'words': shown, 'lookups': lookups,
               'per100': round(100 * lookups / shown, 2) if shown else None,
               'none': sum(1 for m in messages if not m['lookups']),
               'expected': round(sum(m['expected'] for m in messages), 1),
               'observed': round(sum(m['observed'] for m in messages), 1)}
    return {'days': days, 'passages': len(passages), 'bins': bins, 'jitter': jitter, 'chat': replies,
            'presses': presses}


def calibration_text(report):
    """fw.py calibrate: the report in plain lines."""
    lines = [f'Last {report["days"]} days, {report["passages"]} web passages with time on screen (normal intensity):']
    for b in report['bins']:
        if b['n']:
            share = f'{100 * b["low"]:4.0f}-{100 * b["high"]:3.0f}% Japanese' if b['high'] else 'no Japanese'.rjust(18)
            lines.append(f'  {share}: {b["n"]:4d} passages, '
                         f'{b["msPerWord"]:4d} ms on screen per word, {b["opensPer100"]:.2f} opens per 100 words')
    j = report['jitter']
    if j['thinned']['n'] and j['untouched']['n']:
        lines.append(f'Randomly thinned passages: {j["thinned"]["n"]} at {100 * j["thinned"]["share"]:.1f}% Japanese, '
                     f'{j["thinned"]["msPerWord"]} ms/word; untouched: {j["untouched"]["n"]} at '
                     f'{100 * j["untouched"]["share"]:.1f}%, {j["untouched"]["msPerWord"]} ms/word.')
        if j['effect'] is not None:
            lines.append(f'  Each extra point of Japanese changes screen time per word by about '
                         f'{j["effect"] / 100:+.1%}.')
        elif j.get('gap') is not None:  # enough of both, but the thinning took too little Japanese off
            lines.append(f'  Thinned passages have only {100 * j["gap"]:.1f} points less Japanese than untouched ones: '
                         f'too close to measure the effect ({100 * THIN_MIN_GAP:.1f} needed).' if j['gap'] > 0 else
                         f'  Thinned passages have no less Japanese than untouched ones ({-100 * j["gap"]:.1f} points '
                         "more): the effect can't be measured yet.")
        else:
            lines.append(f'  Not enough of either yet to measure the effect ({THIN_MIN_PASSAGES} each).')
    else:
        lines.append('No randomly thinned passages yet (extension 0.6.0 and later).')
    c = report.get('chat')  # knowledge.json from before has none
    if c and c['messages']:
        rate = f' ({c["per100"]:.2f} per 100 words)' if c['per100'] is not None else ''
        lines.append(f'Chat: {c["messages"]} messages of {c["words"]} words, {c["lookups"]} lookups{rate}, '
                     f'{c["none"]} messages with none; listed words: {c["expected"]:.1f} lookups expected, '
                     f'{c["observed"]:.1f} seen.')
    elif c is not None:
        lines.append('No chat messages with a word count yet.')
    for surface, name in (('chat', 'Chat replies'), ('browser', 'Web pages')):
        p = report['presses'][surface]
        at = lambda k: {str(intensity.OFF): 'off', str(intensity.FULL): 'at full'}.get(k, f'at {int(k):+d}')
        hours = ', '.join(f'{h} h {at(k)}' for k, h in p['hoursAt'].items()) or 'no time away from normal'
        verdict = f'; the default looks {p["verdict"]}' if p['verdict'] else ''
        n = p.get('threadsOff')
        chats = f', counting {n} chat{"s" if n != 1 else ""} with Japanese turned off' if n else ''
        lines.append(f'{name}: {p["lighter"]} lighter and {p["heavier"]} heavier presses ({hours}){chats}{verdict}.')
    return '\n'.join(lines)


def unlisted(rows, listed, now_ms, days=UNLISTED_DAYS):
    """Opened words with no lexicon entry over the last `days` days, most opened first: what the chat's Japanese
    sentences use that the word list doesn't have. {days, opens, words: number of such words, top: [[word, opens]]}.
    Sentence translations and T3 peeks under T3_NOISE_MS don't count."""
    match, since, counts = matcher(listed), now_ms - days * DAY_MS, {}
    for row in rows:
        if row['at'] < since or row['word'] in PUNCTUATION:
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


# ---- the dial ----

def peek_rate(rows, contexts, now_ms):
    """Browser peeks per 100 words shown over the last RATE_DAYS days: (rate or None, words shown, peeks). Passages
    shown without Japanese (blank) have nothing to peek at and don't count."""
    since = now_ms - RATE_DAYS * DAY_MS
    shown = sum(c['words'] or 0 for c in contexts.values()
                if c['surface'] == 'browser' and c['shown_at'] and c['shown_at'] >= since and c['annotations'])
    peeks = sum(1 for r in rows if r['surface'] == 'browser' and r['kind'] in ('peek', 'flip') and r['at'] >= since)
    return (100 * peeks / shown if shown else None), shown, peeks


def steer(controller, challenge, rate, shown, today):
    """Once a day: move scale by sqrt(target / realized) so realized peeks approach the dial."""
    controller = dict(controller or {'scale': 1.0, 'day': ''})
    if challenge > 0 and rate is not None and shown >= RATE_MIN_WORDS and controller.get('day') != today:
        scale = controller.get('scale', 1.0) * math.sqrt(challenge / max(rate, 0.05))
        controller = {'scale': round(min(SCALE_RANGE[1], max(SCALE_RANGE[0], scale)), 3), 'day': today}
    return controller


def challenge_of(state):
    value = (state.get('prefs') or {}).get('challenge', 0)
    return value if valid_challenge(value) else 0


def valid_challenge(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value in CHALLENGES


# ---- status moves ----

def bounds(word, offset=0.0):
    """80% bounds on the browser peek chance: (lower, upper)."""
    return sigmoid(offset - word.k - Z80 * word.sd), sigmoid(offset - word.k + Z80 * word.sd)


def plan_moves(state, words, moves_today, now_ms):
    """Evidence moves on top of the day rules: shaky -> known when the upper bound of p < 0.15, known -> shaky when
    the lower bound > 0.35 and it was opened recently. At most MOVE_CAP per direction per day, none right after a
    command. Also the graduation veto: active words whose lower bound > 0.3. Words not introduced yet (queue) never
    move: fwlib.refill brings them in."""
    moves, veto = [], []
    done = {'known': moves_today.get('known', 0), 'shaky': moves_today.get('shaky', 0)}
    for w in state['words']:
        word = words.get(w['ja'])
        if word is None or word.n < 2:
            continue
        if word.command_at and now_ms - word.command_at < IMMUNE_DAYS * DAY_MS:
            continue
        lower, upper = bounds(word)
        if w['status'] == 'active' and lower > 0.3:
            veto.append(w['ja'])
        elif w['status'] == 'shaky' and upper < 0.15 and done['known'] < MOVE_CAP:
            moves.append((w['ja'], 'shaky', 'known'))
            done['known'] += 1
        elif w['status'] == 'known' and lower > 0.35 and word.recent and done['shaky'] < MOVE_CAP:
            moves.append((w['ja'], 'known', 'shaky'))
            done['shaky'] += 1
    return moves, veto


# ---- refresh ----

def path():
    return fwlib.home() / KNOWLEDGE


def load():
    return fwlib.read_json(path(), {}) or {}


def is_stale(knowledge=None, now=None):
    updated = (knowledge if knowledge is not None else load()).get('updated', 0)
    return (now if now is not None else time.time()) - updated > STALE_S


def refresh(now=None):
    """Replay the gloss log, steer the dial, apply guarded status moves, write knowledge.json. One at a time; returns
    the new knowledge, or None when another refresh is running. With a collector set, the collector does this and
    the cache here is refreshed instead (hub.py)."""
    if fwlib.collector_url():
        import hub
        ok, error = hub.sync(scan=False)
        return dict(load(), synced=ok, syncError=error)
    now = now if now is not None else time.time()
    now_ms, today = int(now * 1000), time.strftime('%Y-%m-%d', time.localtime(now))
    with fwlib.locked(LOCK, blocking=False) as got:
        if not got:
            return None
        rows, contexts = feedback.load(feedback.default_path())
        state = fwlib.load_state()
        previous = load()
        listed = inflection.lexicon(state['words']).entry  # every lexicon word, queue words too
        intervals, commands = anki_intervals(), read_commands()
        reading = previous.get('reading')
        if not isinstance(reading, dict) or reading.get('day') != today:  # the reading model's grid, once a day
            reading = dict(tune(rows, contexts, listed, intervals, commands, now_ms), day=today)
        chat = []
        words, offset = replay(rows, contexts, listed, intervals, commands, now_ms, reading['pace'], reading['top'],
                               chat=chat)
        rate, shown, peeks = peek_rate(rows, contexts, now_ms)
        challenge = challenge_of(state)
        controller = steer(previous.get('controller'), challenge, rate, shown, today)
        history = [m for m in previous.get('moves', []) if isinstance(m, dict)]
        today_counts = {}
        for m in history:
            if m.get('day') == today:
                today_counts[m['to']] = today_counts.get(m['to'], 0) + 1
        shrank = len(rows) < previous.get('rows', 0)  # a truncated mirror must not move words
        # with the dial off, the day rules alone move words, as before adaptive difficulty
        moves, veto = ([], []) if shrank or not challenge else plan_moves(state, words, today_counts, now_ms)
        applied = []
        if moves:
            with fwlib.editing() as live:
                for ja, before, after in moves:
                    w = fwlib.find(live, ja)
                    if w is not None and w['status'] == before:
                        fwlib.set_status(w, after, today)
                        applied.append({'day': today, 'word': ja, 'from': before, 'to': after})
        knowledge = {
            'updated': now, 'rows': len(rows),
            'offset': {s: round(v, 3) for s, v in offset.items()},
            'challenge': challenge, 'controller': controller,
            'budget': round(challenge * controller.get('scale', 1.0), 3) if challenge else 0,
            'rate': {'per100': round(rate, 2) if rate is not None else None, 'shownWords': shown, 'peeks': peeks,
                     'days': RATE_DAYS},
            'veto': veto, 'moves': (history + applied)[-200:], 'reading': reading,
            'calibration': calibrate(rows, contexts, feedback.intensity_changes(feedback.default_path()), now_ms,
                                     chat=chat),
            'unlisted': unlisted(rows, listed, now_ms),
            'words': {ja: {'k': round(w.k, 3), 'n': round(w.n, 2), 'sd': round(w.sd, 3), 'mu': round(w.mu, 2),
                           'obs': w.obs, 'opened': w.opened, 'recent': len(w.recent), 'last': w.last,
                           'history': w.history} for ja, w in words.items()},
        }
        fwlib.write_json(path(), knowledge)
        return knowledge


def start_refresh():
    """A background `fw.py refresh`, never waited on (the native host and the hook call this)."""
    if os.environ.get('JAPANESE_WEAVE_NO_REFRESH'):
        return
    import subprocess
    import sys
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().with_name('fw.py')), 'refresh'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=dict(os.environ))


def word_estimate(knowledge, ja, surface='browser'):
    """(p, k, sd) for a word, or None without evidence."""
    entry = (knowledge.get('words') or {}).get(ja)
    if not entry:
        return None
    offset = (knowledge.get('offset') or {}).get(surface, 0.0)
    return sigmoid(offset - entry['k']), entry['k'], entry['sd']
