"""Model evaluation (NEXT_STEPS step 3, PLAN.md section 10): any model of lookup chances scored on the gloss log, day by
day, against each other. Python 3.9+, standard library only.

extract() turns a log into what the models learn from, by exactly the replay's rules (knowledge.timeline and
knowledge.observed: showings and episodes, read chances from time on screen, the line rule, glances, T3 noise,
sentence translations), each observation with what a model may use and what the scoring needs. evaluate() scores
models prequentially by day: for each day d, every model is fitted on the events before d's local midnight and then
predicts every scored observation of d. The score is the log loss of P(open) = read x q against the label y, where q
is the model's P(lookup | read) and read the chance the word was read from time on screen alone, at a reading pace and
cap held fixed for every model compared. The line rule weighs an update, never a prediction: it depends on what was
opened. Scored are the observations of showings (browser passages and T3 messages with a word count), on both
surfaces; episodes (T3 messages without a word count, logged only because something in them was opened) are fitted
on but not scored.

The interface. A model is an object with

    name             a label for reports
    fit(log, until)  learn from log.events, the log's events before `until` (ms since the epoch) in replay's order.
                     Called once per day with one more day of events each time. A model may keep what it learned and
                     read only the events it hasn't seen yet: log.origin is the same object for every day of one log.
    predict(obs)     q = P(lookup | read) for one Observation, a number in (0, 1), at obs.at on obs.surface.

A Log has events (each a Command or a Group), intervals (Anki intervals by word), words (the word state's entries:
ja, kana, status, added, changed), entries (each word's dictionary entry, found when first asked for), pace and top
(the fixed reading model), and origin. knowledge.refresh fits the
same Log of the whole log.

  Command      at, word (its entry), kind: 'known' or 'learn'
  Group        one showing or episode: at (when it was scored), surface ('browser' or 't3'), key (surface, view,
               context), episode (True for an episode), obs (its Observations)
  Observation  at, day ('YYYY-MM-DD', local), surface, key, episode; word (the lexicon entry), written (the
               annotation), form (written is a conjugated form of word, not the word or its reading); y (1 opened,
               knowledge.GLANCE_Y a glance, 0 not opened); weight (replay's: 1 opened, else the larger of near and
               read; an episode has no read chance), near (the line rule alone); ms, words, share (the time on screen of
               the word's passage or paragraph, its words and how far down the word sits: read is
               knowledge._chance(ms, words, share, pace, top), for a model that fits its own pace and cap); read;
               status (the word's that day: 'known', 'learning' or 'new', status_at); seen (the showings and
               episodes the word was in before this one)

The models: "fit", the one fitter (knowledge.Fit, NEXT_STEPS step 4), refitted on everything before each day;
"flat", the same with the prior by origin and Anki interval alone, without the word features (NEXT_STEPS step 13);
"today", the online updates it replaced (knowledge.Online, fed the log in replay's order); and "constant", one chance
per surface. CURRENT names the model knowledge.py runs, which the convergence test checks.

Reports: per model and class (all; web and chat; known, learning and new words, as written; forms; free and costly by
the model's own q against fwlib.FREE_P; first: each word's first FIRST exposures, where its prior decides most), the
mean log loss and seen/expected opens with a 90% interval from resampling days; for each other model, the paired loss difference against the first, day by day, with a 90% interval from
resampling days (free and costly by the first model's q); on a simulated log (tools/sim), the true chances' loss too:
the floor no model can beat.

  python3 scripts/evaluate.py [--home DIR] [--models today,fit,constant] [--truth FILE] [--skip DAYS]
                              [--pace MS --top X]
"""
import argparse
import bisect
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feedback  # noqa: E402
import fwlib  # noqa: E402
import inflection  # noqa: E402
import knowledge  # noqa: E402

READ_FLOOR = knowledge.READ_FLOOR  # the least read chance a prediction uses: an open far down still happens
FIRST = 3  # a word's first exposures, the "first" class: its showings and episodes before this many others
CLASSES = ('all', 'browser', 't3', 'known', 'learning', 'new', 'form', 'free', 'costly', 'first')
NAMES = {'browser': 'web', 't3': 'chat'}
TRUTH = 'truth.jsonl'


class Observation:
    __slots__ = ('at', 'day', 'surface', 'key', 'episode', 'word', 'written', 'form', 'y', 'weight', 'near', 'ms',
                 'words', 'share', 'read', 'status', 'seen')

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))


class Group:
    __slots__ = ('at', 'surface', 'key', 'episode', 'obs')

    def __init__(self, at, surface, key, episode, obs):
        self.at, self.surface, self.key, self.episode, self.obs = at, surface, key, episode, obs


class Command:
    __slots__ = ('at', 'word', 'kind')

    def __init__(self, at, word, kind):
        self.at, self.word, self.kind = at, word, kind


class Log:
    """The events of a log in replay's order, with what a model may use besides (module docstring)."""

    def __init__(self, events, intervals, words, pace=knowledge.PACE_MS, top=knowledge.READ_MAX, origin=None):
        self.events, self.intervals, self.words, self.pace, self.top = events, intervals, words, pace, top
        self.origin = origin if origin is not None else self
        self._ats = [e.at for e in events]
        self._entries = None

    @property
    def entries(self):
        """{ja: dictionary entry or None} for the word state's words (knowledge.word_entries), found once per log
        unless set."""
        if self.origin._entries is None:
            self.origin._entries = knowledge.word_entries(self.words)
        return self.origin._entries

    @entries.setter
    def entries(self, value):
        self.origin._entries = value

    def before(self, until):
        """The same log with only its events before `until` (ms)."""
        return Log(self.events[:bisect.bisect_left(self._ats, until)], self.intervals, self.words, self.pace,
                   self.top, self.origin)

    def between(self, start, end):
        return self.events[bisect.bisect_left(self._ats, start):bisect.bisect_left(self._ats, end)]

    def scored(self):
        """Every scored observation (of showings), oldest first."""
        return [o for e in self.events if isinstance(e, Group) and not e.episode for o in e.obs]


day_of = knowledge.day_of
status_at = knowledge.status_at  # a word's status on a day, from the word state's added and changed days


day_start = knowledge.day_start


def next_day(day):
    return day_of(day_start(day) + 36 * 3_600_000)  # past a daylight-saving change too


def is_form(written, entry, word=None):
    """Whether an annotation is a conjugated form of its entry: neither the entry as written nor its reading."""
    spellings = {inflection.fold(entry)} | ({inflection.fold(word['kana'])} if word and word.get('kana') else set())
    return inflection.fold(written) not in spellings


def extract(rows, contexts, words, intervals, commands, now_ms, pace=knowledge.PACE_MS, top=knowledge.READ_MAX):
    """The Log of a gloss log (feedback.load) as knowledge.replay reads it. words: the word state's entries (every
    lexicon word is listed, as in refresh); intervals, commands: as replay takes them."""
    match = inflection.lexicon(words).entry
    by_ja = {w['ja']: w for w in words}
    events, seen = [], {}
    for at, kind, item in knowledge.timeline(rows, contexts, match, commands, now_ms):
        if kind == knowledge.COMMAND:
            events.append(Command(at, match(item['word']), item['kind']))
            continue
        if kind == knowledge.SHOWING:
            _at, key, context, items = item
            found = knowledge.observed(key[0], context, items, match, knowledge.reader(key[0], context, pace, top))
        else:
            key, context, items = item
            found = knowledge.observed(key[0], context, items, match)
            if not found:
                continue
        notes = context['annotations']
        line = knowledge._line_height([n[2] for n in notes])
        day, surface, episode = day_of(at), key[0], kind == knowledge.EPISODE
        group = Group(at, surface, key, episode, [])
        for ja, y, weight, index, near in found:
            written, _x, y_pos = notes[index]
            ms, count, share = knowledge.reading(surface, context, y_pos, line) or (0, 0, 1.0)
            group.obs.append(Observation(
                at=at, day=day, surface=surface, key=key, episode=episode, word=ja, written=written,
                form=is_form(written, ja, by_ja.get(ja)), y=y, weight=weight, near=near, ms=ms, words=count,
                share=share, read=knowledge._chance(ms, count, share, pace, top), status=status_at(by_ja.get(ja), day),
                seen=seen.get(ja, 0)))
        for o in group.obs:
            seen[o.word] = o.seen + 1
        events.append(group)
    return Log(events, intervals, words, pace, top)


def load(home=None, now_ms=None, pace=None, top=None):
    """The Log of a state dir's gloss log (default: this one's), at the reading model knowledge.json has (else the
    defaults) unless pace and top are given."""
    home = Path(home) if home else fwlib.home()
    if not (home / feedback.DB_NAME).exists():
        raise fwlib.NoState(f'no gloss log in {home}')
    with fwlib.using_home(home):
        rows, contexts = feedback.load(feedback.default_path())
        state = fwlib.load_state(create=False, lock=False)
        intervals, commands = knowledge.anki_intervals(), knowledge.read_commands()
        reading = knowledge.load().get('reading') or {}
    if state is None:
        raise fwlib.NoState(f'no word state in {home}')
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return extract(rows, contexts, state['words'], intervals, commands, now_ms,
                   pace or reading.get('pace') or knowledge.PACE_MS, top or reading.get('top') or knowledge.READ_MAX)


# ---- models ----

class Today:
    """Today's model: knowledge.Online fed the log in replay's order (on the whole log, exactly replay's)."""
    name = 'today'

    def __init__(self):
        self.origin, self.done, self.model = None, 0, None

    def fit(self, log, until):
        if log.origin is not self.origin or len(log.events) < self.done:
            self.origin, self.done, self.model = log.origin, 0, knowledge.Online(log.intervals)
        for event in log.events[self.done:]:
            if isinstance(event, Command):
                self.model.command(event.at, event.word, event.kind)
            elif event.episode:
                self.model.episode(event.at, event.surface, [(o.word, o.y, o.weight) for o in event.obs])
            else:
                self.model.showing(event.at, event.surface, [(o.word, o.y, o.weight) for o in event.obs])
        self.done = len(log.events)

    def predict(self, obs):
        return self.model.chance(obs.word, obs.surface, obs.at)


class Constant:
    """One P(lookup | read) per surface for every word: its opens over its read chances so far (each at least
    READ_FLOOR, as scored). A yardstick: a model of words should beat it."""
    name = 'constant'
    START = 0.05

    def __init__(self):
        self.q = {}

    def fit(self, log, until):
        sums = {}
        for o in log.scored():
            s = sums.setdefault(o.surface, [0.0, 0.0])
            s[0] += o.y
            s[1] += max(READ_FLOOR, o.read)
        self.q = {surface: min(0.99, max(0.001, y / r)) for surface, (y, r) in sums.items() if r}

    def predict(self, obs):
        return self.q.get(obs.surface, self.START)


class Fitted:
    """The one fitter (knowledge.Fit, NEXT_STEPS step 4): a penalized maximum-likelihood fit of every event before the
    day, at the log's reading pace and cap, each day starting from the day before's, with the free flags carried from
    the day before as a refresh carries them (knowledge.free_flags, every word of the word state). frozen: the shared
    parameters and prior widths held at their priors. plain: the prior without the word features (by origin and Anki
    interval alone), the flat start the word features are compared with."""

    def __init__(self, frozen=False, plain=False):
        self.frozen, self.plain, self.last, self.flags, self.origin = frozen, plain, None, {}, None
        self.name = 'fit, priors held' if frozen else 'flat' if plain else 'fit'

    def fit(self, log, until):
        if log.origin is not self.origin:
            self.origin, self.last, self.flags = log.origin, None, {}
        self.last = knowledge.Fit(log, until, start=self.last.state() if self.last else None, frozen=self.frozen,
                                  free=self.flags, plain=self.plain)
        self.flags = knowledge.free_flags(self.last, list(dict.fromkeys([w['ja'] for w in log.words] +
                                                                         self.last.words)), self.flags)

    def predict(self, obs):
        return self.last.q(obs.word, obs.surface, obs.form)


MODELS = {'today': Today, 'constant': Constant, 'fit': Fitted, 'flat': lambda: Fitted(plain=True)}
CURRENT = 'fit'  # the model knowledge.py runs (knowledge.Fit)


def current():
    return MODELS[CURRENT]()


# ---- scoring ----

def load_truth(path):
    """A simulated log's ground truth (tools/sim): {(surface, view, context, word): (q, read)}, its true P(lookup |
    read) and read chance."""
    truth = {}
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            t = json.loads(line)
            truth[(t['surface'], t['view'], t['context'], t['word'])] = (t['q'], t['read'])
    return truth


def loss(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def chance(q, obs):
    """P(open) for a prediction q: the read chance from time on screen alone (at least READ_FLOOR) times q."""
    return max(READ_FLOOR, obs.read) * q


def evaluate(log, models, truth=None, skip=0):
    """Prequential scores: for each day after the first `skip` days with scored observations, every model fitted on
    the days before it predicts the day's. {days, models: names, rows: [(obs, [P per model], [q per model], true P or
    None)]}."""
    days = sorted({o.day for o in log.scored()})[skip:]
    rows = []
    for day in days:
        start, end = day_start(day), day_start(next_day(day))
        history = log.before(start)
        for model in models:
            model.fit(history, start)
        for event in log.between(start, end):
            if not isinstance(event, Group) or event.episode:
                continue
            for o in event.obs:
                qs = [model.predict(o) for model in models]
                true = None
                if truth is not None:
                    found = truth.get((o.key[0], o.key[1], o.key[2], o.word))
                    true = found[1] * found[0] if found else None
                rows.append((o, [chance(q, o) for q in qs], qs, true))
    return {'days': days, 'models': [m.name for m in models], 'rows': rows}


def _member(obs, q, name):
    if name == 'all':
        return True
    if name in ('browser', 't3'):
        return obs.surface == name
    if name == 'form':
        return obs.form
    if name == 'first':
        return obs.seen is not None and obs.seen < FIRST
    if name in ('free', 'costly'):
        return (q < fwlib.FREE_P) == (name == 'free')
    return not obs.form and obs.status == name


def _mean_difference(days):
    n = sum(c for _d, c in days)
    return sum(d for d, _c in days) / n if n else None


def summary(result):
    """Per model and class: {n, opens, expected, ratio, interval, loss}; per other model and class, paired with the
    first: {diff, interval, better: days it did better, days}; with truth, the truth's per class."""
    rows, names = result['rows'], result['models']
    out = {'models': {}, 'paired': {}, 'truth': None, 'days': result['days']}
    columns = [(name, i) for i, name in enumerate(names)]
    if rows and all(r[3] is not None for r in rows):
        columns.append(('truth', None))
    for name, i in columns:  # the truth: true P(open), free and costly by the first model's q
        table = {}
        for cls in CLASSES:
            mine = [(o, t if i is None else p[i]) for o, p, q, t in rows if _member(o, q[0 if i is None else i], cls)]
            by_day = {}
            for o, p in mine:
                d = by_day.setdefault(o.day, [0.0, 0.0])
                d[0] += p
                d[1] += o.y
            expected, opens = sum(p for _o, p in mine), sum(o.y for o, _p in mine)
            interval = knowledge.day_interval(list(by_day.values()), knowledge.ratio)
            table[cls] = {'n': len(mine), 'opens': round(opens, 1), 'expected': round(expected, 1),
                          'ratio': round(opens / expected, 3) if expected else None,
                          'interval': [round(v, 3) for v in interval] if interval else None,
                          'loss': sum(loss(p, o.y) for o, p in mine) / len(mine) if mine else None}
        if i is None:
            out['truth'] = table
        else:
            out['models'][name] = table
    for i, name in list(enumerate(names))[1:]:
        table = {}
        for cls in CLASSES:
            by_day = {}
            for o, p, q, _t in rows:
                if _member(o, q[0], cls):
                    d = by_day.setdefault(o.day, [0.0, 0])
                    d[0] += loss(p[i], o.y) - loss(p[0], o.y)
                    d[1] += 1
            days = list(by_day.values())
            diff = _mean_difference(days)
            interval = knowledge.day_interval(days, _mean_difference)
            table[cls] = {'diff': diff, 'interval': list(interval) if interval else None,
                          'better': sum(1 for d, _n in days if d < 0), 'days': len(days)}
        out['paired'][name] = table
    return out


def report_text(result, log):
    """The evaluation in plain lines."""
    s = summary(result)
    rows, names, days = result['rows'], result['models'], result['days']
    if not rows:
        return 'Nothing to score: no passage or message shown in the log.'
    episodes = sum(len(e.obs) for e in log.events if isinstance(e, Group) and e.episode)
    surfaces = {surface: sum(1 for r in rows if r[0].surface == surface) for surface in ('browser', 't3')}
    lines = [f'Prequential by day: {len(days)} days ({days[0]} to {days[-1]}), each predicted from the days before; '
             f'reading pace {log.pace:g} ms a word, cap {log.top:g}, for every model.',
             f'{len(rows)} scored observations ({surfaces["browser"]} web, {surfaces["t3"]} chat); {episodes} more '
             'in episodes, fitted on only.',
             'Per class: observations, opens (a glance counts '
             f'{knowledge.GLANCE_Y}), then per model the mean log loss of P(open) = read x q and seen/expected opens '
             'with a 90% interval from resampling days. Known, learning and new: words as written; form: conjugated '
             f'forms; free and costly: by each model\'s own q; first: each word\'s first {FIRST} exposures.']
    columns = names + (['truth'] if s['truth'] else [])
    header = f'  {"class":<9}{"n":>7}{"opens":>8}' + ''.join(f'  {name:<27}' for name in columns)
    lines.append(header.rstrip())
    for cls in CLASSES:
        first = s['models'][names[0]][cls]
        cells = []
        for name in columns:
            k = (s['truth'] if name == 'truth' else s['models'][name])[cls]
            if not k['n']:
                cells.append(f'{"-":<27}')
                continue
            ratio = f'{k["ratio"]:.2f}' if k['ratio'] is not None else '-'
            interval = f' ({k["interval"][0]:.2f}-{k["interval"][1]:.2f})' if k['interval'] else ''
            cells.append(f'{k["loss"]:.4f} {ratio}{interval}'.ljust(27))
        n, opens = (first['n'], first['opens']) if cls not in ('free', 'costly') else ('', '')
        lines.append((f'  {NAMES.get(cls, cls):<9}{n:>7}{opens:>8}' + ''.join(f'  {c}' for c in cells)).rstrip())
    for name, table in s['paired'].items():
        lines.append(f'{name} against {names[0]}: mean log loss difference per observation (negative: {name} better), '
                     f'with a 90% interval from resampling days, and the days {name} was better (free and costly by '
                     f'{names[0]}\'s q):')
        for cls in CLASSES:
            k = table[cls]
            if k['diff'] is None:
                continue
            interval = f' ({k["interval"][0]:+.4f} to {k["interval"][1]:+.4f})' if k['interval'] else ''
            lines.append(f'  {NAMES.get(cls, cls):<9}{k["diff"]:+.4f}{interval}, better on {k["better"]} of '
                         f'{k["days"]} days')
    if s['truth']:
        first, truth = s['models'][names[0]]['all']['loss'], s['truth']['all']['loss']
        for name in names[1:]:
            mine = s['models'][name]['all']['loss']
            if first - truth > 0:
                lines.append(f'{name} closes {(first - mine) / (first - truth):.0%} of the gap between {names[0]} and '
                             f'the true chances.')
    return '\n'.join(lines)


def add_arguments(parser):
    parser.add_argument('--home', help='the state dir (default: this one); a simulated one from tools/sim works too')
    parser.add_argument('--models', default='today,fit,constant',
                        help=f'comma-separated, the first is the reference: {", ".join(MODELS)}')
    parser.add_argument('--truth', help=f"a simulated log's ground truth (default: the state dir's {TRUTH}, if any)")
    parser.add_argument('--skip', type=int, default=0, metavar='DAYS', help='leave the first days unscored')
    parser.add_argument('--pace', type=float, help="ms a word (default: knowledge.json's reading model)")
    parser.add_argument('--top', type=float, help='the most a time on screen says (default: as pace)')
    parser.add_argument('--now', type=float, help='seconds since the epoch (default: now)')


def run(args):
    names = [n for n in args.models.split(',') if n]
    unknown = [n for n in names if n not in MODELS]
    if unknown or not names:
        sys.exit(f'Unknown model: {", ".join(unknown) or "none given"} (have {", ".join(MODELS)}).')
    home = Path(args.home).expanduser() if args.home else fwlib.home()
    now_ms = int((args.now if args.now is not None else time.time()) * 1000)
    try:
        log = load(home, now_ms, args.pace, args.top)
    except fwlib.NoState as error:
        sys.exit(f'Japanese Weave: {error}')
    truth_path = Path(args.truth) if args.truth else home / TRUTH
    truth = load_truth(truth_path) if truth_path.exists() else None
    print(report_text(evaluate(log, [MODELS[n]() for n in names], truth, args.skip), log))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Score models of lookup chances on the gloss log, day by day.')
    add_arguments(parser)
    run(parser.parse_args(argv))


if __name__ == '__main__':
    main()
