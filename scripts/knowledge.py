"""Adaptive difficulty: per-word knowledge estimated from the gloss interaction log (see docs/adaptive-difficulty.md).
Python 3.9+, standard library only.

Model: P(peek) = sigmoid(offset_s - k_w). k_w is the reader's knowledge of a word on a log-odds scale, learned by
Elo-style updates from engaged reading episodes (a peek, pin or flip shows the reader was reading there, so unopened
glossed words around it count as known). The update is conditioned on the episode having at least one peek, since
only such episodes are logged. refresh() replays the whole log from scratch (deterministic, retunable), writes
knowledge.json, steers the peek-rate dial, and makes a few guarded status moves.
"""
import json
import math
import os
from pathlib import Path
import statistics
import time

import feedback
import fwlib

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

def episodes(rows, contexts, now_ms):
    """Interactions grouped by (surface, view, context) and split at gaps over GAP_MS; only settled ones."""
    groups = {}
    for row in rows:
        groups.setdefault((row['surface'], row['view'], row['context']), []).append(row)
    out = []
    for key, items in groups.items():
        context = contexts.get(key)
        if context is None:
            continue
        current = []
        for row in sorted(items, key=lambda r: (r['at'], r['id'])):
            if current and row['at'] - current[-1]['at'] > GAP_MS:
                out.append((key, context, current))
                current = []
            current.append(row)
        out.append((key, context, current))
    return sorted((e for e in out if e[2][-1]['at'] <= now_ms - SETTLE_MS), key=lambda e: e[2][-1]['at'])


def _line_height(ys):
    gaps = [b - a for a, b in zip(sorted(set(ys)), sorted(set(ys))[1:]) if b - a > 2]
    return statistics.median(gaps) if gaps else 20.0


def observations(surface, context, items, listed):
    """[(word, y, weight)] for the listed words of one episode; [] when nothing was opened (not engaged).

    Opened: y 1 for a pin or flip or a peek open at least GLANCE_MS, GLANCE_Y for a shorter peek; T3 peeks under
    T3_NOISE_MS don't count. Unopened: y 0, weight 1 down to the lowest opened line, halving per line below it.
    Words of a sentence whose whole-sentence translation was opened get nothing (it was read in English)."""
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
    if not opened:
        return []
    excluded = set()
    for index in opened:
        if notes[index][0] in PUNCTUATION:  # a sentence translation: skip back to the previous sentence end
            start = max((i for i in range(index) if notes[i][0] in PUNCTUATION), default=-1)
            excluded.update(range(start + 1, index))
    line = _line_height([n[2] for n in notes])
    lowest = max(notes[i][2] for i in opened)
    best = {}
    for index, (word, _x, y_pos) in enumerate(notes):
        if word not in listed or index in excluded:
            continue
        if index in opened:
            y, weight = opened[index], 1.0
        else:
            below = y_pos - lowest
            y, weight = 0.0, 1.0 if below <= line / 2 else 0.5 ** math.ceil(below / line)
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

    @property
    def sd(self):
        return math.sqrt(2 / (1 + self.n))


def replay(rows, contexts, listed, intervals, commands, now_ms):
    """Every word's knowledge after the whole log; also the T3 offset."""
    words = {}
    offset = {'browser': 0.0, 't3': 0.0}

    def get(ja):
        if ja not in words:
            words[ja] = Word(prior(intervals.get(ja)))
        return words[ja]

    timeline = [(e[2][-1]['at'], 0, e) for e in episodes(rows, contexts, now_ms)]
    timeline += [(c['at'], 1, c) for c in commands if c['word'] in listed]
    for at, kind, item in sorted(timeline, key=lambda t: (t[0], t[1])):
        if kind == 1:
            word = get(item['word'])
            word.age(at)
            word.command_at = at
            if item['kind'] == 'known':
                word.k, word.n = max(word.k, KNOWN_COMMAND_K), word.n + 2
            else:
                word.k = min(word.k, LEARN_COMMAND_K)
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


# ---- the dial ----

def peek_rate(rows, contexts, now_ms):
    """Browser peeks per 100 words shown over the last RATE_DAYS days: (rate or None, words shown, peeks)."""
    since = now_ms - RATE_DAYS * DAY_MS
    shown = sum(c['words'] or 0 for c in contexts.values()
                if c['surface'] == 'browser' and c['shown_at'] and c['shown_at'] >= since)
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
    command. Also the graduation veto: active words whose lower bound > 0.3."""
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
    """Sync the mirror, replay, steer the dial, apply guarded status moves, write knowledge.json. One at a time;
    returns the new knowledge, or None when another refresh is running."""
    now = now if now is not None else time.time()
    now_ms, today = int(now * 1000), time.strftime('%Y-%m-%d', time.localtime(now))
    with fwlib.locked(LOCK, blocking=False) as got:
        if not got:
            return None
        collector = fwlib.collector_url()
        source = fwlib.home() / feedback.MIRROR_NAME if collector else feedback.default_path()
        synced, sync_error = feedback.sync_mirror(collector, source) if collector else (True, None)
        rows, contexts = feedback.load(source)
        state = fwlib.load_state()
        previous = load()
        listed = {w['ja'] for w in state['words'] if w['status'] != 'queue'}
        words, offset = replay(rows, contexts, listed, anki_intervals(), read_commands(), now_ms)
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
            'updated': now, 'rows': len(rows), 'synced': synced, 'syncError': sync_error,
            'offset': {s: round(v, 3) for s, v in offset.items()},
            'challenge': challenge, 'controller': controller,
            'budget': round(challenge * controller.get('scale', 1.0), 3) if challenge else 0,
            'rate': {'per100': round(rate, 2) if rate is not None else None, 'shownWords': shown, 'peeks': peeks,
                     'days': RATE_DAYS},
            'veto': veto, 'moves': (history + applied)[-200:],
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
