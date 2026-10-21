"""A simulated reader whose memory, reading and lookup habits are known, for checking models of lookup chances against
the truth (PLAN.md section 10). Python 3.9+, standard library only. Ported from the review's scripts
(~/.local/share/japanese-weave-review/2026-10-20/sim/reader.py), with passages and messages laid out as the extension
and T3 log them, so the real replay reads them unchanged (logs.py).

Memory: each word has a stability S in days and the time it was last read. Recall decays with the time since on an
FSRS-4.5 curve, R = (1 + F dt / S)^C (R = 0.9 at dt = S); S = 0 is a word never learned (R = 0). A read in which the
word is recognised strengthens it, more the more it had faded (FSRS's success update scaled by rho: reading is weaker
than an Anki review); a lookup of a word not recognised drops S to FSRS's post-lapse value (0.4 days times its ease
for a word never learned). A showing not read changes nothing.

Reading: a passage, or a message's paragraph, has a time on screen, and a word in it was read with the chance
knowledge.read_chance (or stay_chance) gives at the reader's true pace and cap: top (1 - exp(-ms / (pace words
share))). A reader reads a passage or message down to a depth drawn once for it, so everything above a lookup was read
(the line rule) and each word's chance of having been read is still the formula's.

Lookups: a word read and not recognised is looked up with chance `habit` (0.9); one recognised, with chance `check`
(0.02; `form_check` for a conjugated form, whose ending may need a look). A day's mood scales both, by
exp(day_sd z - day_sd^2 / 2), z one draw a day, each chance at most 1; in chat, chat_factor scales them too (1: the
surfaces alike). So the true P(lookup | read) is (1 - R) min(1, factor habit) + R min(1, factor check).
A whole sentence in Japanese is read in English (its translation opened) with chance `sentence_p`.

The world: known words (Anki words whose intervals track their stability, and the lexicon's top words without one),
learning words that come in from the front of the queue a day at a time and graduate after learn_days, queue words
that chat uses with the conversation's topic, words off the list, and whole sentences. REALISTIC volumes match the
live log: web about 8,000 words shown and 5 lookups a day, chat about 12,000 words and 35 to 40 lookups.

Starting values from the word itself (NEXT_STEPS step 13; `prior`, off by default): every word has its entry in
data/dictionary.tsv, and `extra` more entries spread over the dictionary's ranks join the queue, which is then in rank
order. Each word not known starts from k0 = base + sum_j beta_j x_j + noise z, z standard normal, x its word features
on the prior's scale (knowledge.word_features: rank, kanji known at a glance in the known words, script, part of
speech, length, rare, loan), beta the dict `prior`. k0 is the log-odds of not looking it up: its chance when read,
q0 = sigmoid(-k0), sets its recall R0 = (habit - q0) / (habit - check), so q0 is held between check and habit; a word
with R0 = 0 was never met, any other was last met 10 to 100 days before the first day, with the stability that leaves
it at R0 then (an Anki card's interval follows it). Known words are known whatever they are like, as before: their
chance sits at check.
"""
import csv
import math
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import fwlib  # noqa: E402
import inflection  # noqa: E402
import knowledge  # noqa: E402

DAY = 86_400_000
HOUR = 3_600_000
MINUTE = 60_000
F, C = 19 / 81, -0.5                         # FSRS-4.5's forgetting curve
W8, W9, W10 = 1.6, 0.15, 1.0                 # its success update
W11, W12, W13, W14 = 1.9, 0.11, 0.29, 2.27   # its post-lapse stability
DIFFICULTY = 5.0
LINE_PX = 24
WEB_PER_LINE, CHAT_PER_LINE, CHAT_PER_PARAGRAPH = 12, 14, 90
CANDIDATES = ROOT / 'tools' / 'candidates.csv'
SENTENCE_END = '。'

DEFAULTS = dict(
    seed=1, start='2026-09-01',
    # the vocabulary: Anki words come from tools/candidates.csv (words the lexicon doesn't have), the rest from it
    anki_known=150, top_known=150, learning=25, anki_queue=140, offlist=300,
    known_S=(40.0, 1.0),        # median stability of known words in days, and the SD of its log
    anki_noise=0.3,             # SD of log(Anki interval / stability)
    ease_sd=0.4,                # SD of the log of a word's ease, which scales its gains
    learn_days=21, new_per_day=None,  # None: learning / learn_days
    learn_new=0.4,              # share of new learning words never seen before (S = 0)
    queue_new=0.7,              # and of queue words
    offlist_seen=0.5,           # share of words off the list met before (loanwords, words from elsewhere)
    zipf_known=0.8, zipf_queue=1.0, zipf_offlist=1.0,
    # memory and habits
    rho=0.1, habit=0.9, check=0.02, form_check=0.1, sentence_p=0.2, day_sd=0.25,
    chat_factor=1.0,            # chat scales both lookup chances by this too: a surface difference (PLAN.md row M)
    # reading: the true pace and cap, and the log of time on screen over the time reading it all takes
    pace=knowledge.PACE_MS, top=knowledge.READ_MAX, web_screen=(0.0, 1.0), chat_screen=(0.3, 0.8),
    # chat: sessions of replies; each reply's Japanese words and whole sentences
    chat_sessions=5, chat_replies=5, chat_gap=3, chat_words=(430, 0.5), chat_japanese=22,
    chat_learning=5, chat_learning_p=0.5, chat_topical=8, chat_topical_p=0.3, chat_offlist=1.0, chat_sentences=3.0,
    chat_noise=0.3,             # chance a message gets a T3 peek under 300 ms: the mouse passing over a word
    form_share=0.3,             # chance a verb in chat is written in its polite form
    # web: sessions of passages
    web_sessions=6, web_passages=40, web_gap=1, web_words=(30, 0.4), web_japanese=1.3, web_learning_p=0.15,
    web_budget=0.5,             # the plan's budget per 100 words, logged with each passage
    # starting values from the word itself: None, or the true coefficients on knowledge.word_features' scale, with
    # 'base' (k0 of the reference word) and 'noise' (the SD of a word's own part); extra dictionary entries in the queue
    prior=None, extra=0,
)
PRIOR = dict(base=0.0, noise=0.8, rank=-1.2, length=-0.3, rare=-0.5, loan=-0.5, kana=0.3, katakana=0.8, mixed=0.0,
             verb=-0.3, adjective=0.0, adverb=-0.4, other=0.5, **{'kanji known': 0.8, 'all kanji known': 0.4})
REALISTIC = {}  # the defaults are tuned to the live log's volumes (run.py volumes)


def retention(S, days):
    if S <= 0:
        return 0.0
    return (1 + F * max(0.0, days) / S) ** C


def local_midnight(day):
    return int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)


def day_name(at):
    return time.strftime('%Y-%m-%d', time.localtime(at / 1000))


class TrueWord:
    """One word's true memory and what the word state says about it."""
    __slots__ = ('row', 'ja', 'kind', 'S', 'last', 'ease', 'interval', 'form', 'weight', 'status', 'added',
                 'changed', 'born', 'reads', 'lookups', 'shown', 'k0')

    def __init__(self, row, kind, S, last, ease, interval=None):
        self.row, self.ja, self.kind, self.S, self.last, self.ease = row, row['ja'], kind, S, last, ease
        self.interval, self.form, self.weight = interval, None, 1.0
        self.status, self.added, self.changed, self.born = 'queue', '', '', None
        self.reads = self.lookups = self.shown = 0
        self.k0 = None  # with starting values from features: the true k on the first day (World._start_from_features)

    def recall(self, t):
        if self.S <= 0 or self.last is None:
            return 0.0
        return retention(self.S, (t - self.last) / DAY)

    def chance(self, t, cfg, factor=1.0, form=False):
        """True P(lookup | read) at t, as read() decides it."""
        R = self.recall(t)
        return (1 - R) * min(1.0, factor * cfg['habit']) + R * min(1.0, factor * (cfg['form_check'] if form else
                                                                                   cfg['check']))

    def read(self, t, rng, cfg, factor=1.0, form=False):
        """The word is read at t: whether it is looked up. Updates its memory."""
        R = self.recall(t)
        self.reads += 1
        if rng.random() < R:
            looked = rng.random() < min(1.0, factor * (cfg['form_check'] if form else cfg['check']))
            if cfg['rho'] > 0:
                gain = cfg['rho'] * math.exp(W8) * (11 - DIFFICULTY) * self.S ** -W9 * (math.exp(W10 * (1 - R)) - 1)
                self.S *= 1 + gain * self.ease
            self.last = t
        else:
            looked = rng.random() < min(1.0, factor * cfg['habit'])
            if looked:
                if self.S <= 0:
                    self.S = 0.4 * self.ease
                else:
                    lapse = W11 * DIFFICULTY ** -W12 * ((self.S + 1) ** W13 - 1) * math.exp(W14 * (1 - R))
                    self.S = max(0.05, min(self.S, lapse))
                self.last = t
        self.lookups += looked
        return looked


def polite(row):
    """The ます form of a verb entry, or None (not a verb, or a form the lexicon wouldn't take back to it)."""
    ja, kana = row['ja'], row.get('kana') or ''
    kind = inflection.word_class(ja, kana)
    if kind == inflection.VS:
        return ja[:-2] + 'します'
    if kind == inflection.VK:
        return ja[:-2] + ('来ます' if ja.endswith('来る') else 'きます')
    if kind == inflection.V1:
        return ja[:-1] + 'ます'
    if kind == inflection.V5 and ja[-1] in inflection.GODAN:
        return ja[:-1] + inflection.GODAN[ja[-1]][0] + 'ます'
    return None


class Mark:
    """One annotation of a showing: what it is and what happened."""
    __slots__ = ('written', 'x', 'y', 'word', 'kind', 'form', 'status', 'q', 'read', 'was_read', 'looked', 'dwell')

    def __init__(self, written, word, kind, form=False):
        self.written, self.word, self.kind, self.form = written, word, kind, form
        self.x = self.y = 0
        self.status, self.q, self.read, self.was_read, self.looked, self.dwell = None, 0.0, 0.0, False, False, 0


class Showing:
    """A web passage or chat message as the extension or T3 logs it, with what the reader did."""
    __slots__ = ('at', 'surface', 'view', 'context', 'words', 'height', 'marks', 'active', 'stays', 'plan', 'noise',
                 'paragraphs')

    def __init__(self, at, surface, view, context, words):
        self.at, self.surface, self.view, self.context, self.words = at, surface, view, context, words
        self.height, self.marks, self.active, self.stays, self.plan, self.noise = 0, [], 0, [], None, None
        self.paragraphs = []

    def as_context(self):
        """As feedback.load returns it, for knowledge's read chances."""
        c = {'surface': self.surface, 'annotations': [[m.written, m.x, m.y] for m in self.marks], 'words': self.words,
             'height': self.height, 'active': self.active}
        if self.surface == 't3':
            c['stays'] = [dict(s) for s in self.stays]
        return c


def _cumulative(weights):
    out, total = [], 0.0
    for w in weights:
        total += w
        out.append(total)
    return out


class World:
    """The vocabulary, the reader, and each day's showings (simulate_day)."""

    def __init__(self, **cfg):
        unknown = set(cfg) - set(DEFAULTS)
        if unknown:
            raise ValueError(f'unknown settings: {", ".join(sorted(unknown))}')
        self.cfg = c = dict(DEFAULTS, **cfg)
        self.rng = rng = random.Random(c['seed'])
        self.start = c['start']
        self.day = 0
        lexicon = fwlib.read_lexicon(fwlib.LEXICON)
        in_lexicon = {fwlib.key(w['ja']) for w in lexicon}
        extra, candidates = [], set()
        with open(CANDIDATES, encoding='utf-8', newline='') as handle:
            for r in csv.DictReader(handle):
                candidates.add(fwlib.key(r['ja']))
                if r['ja'] and fwlib.key(r['ja']) not in in_lexicon:
                    extra.append({'ja': r['ja'], 'en': r['en'], 'hint': r['hint'], 'kana': r.get('kana') or ''})
        anki_rows = extra[:c['anki_known'] + c['anki_queue']]
        rng.shuffle(anki_rows)  # which Anki words are mature doesn't follow frequency
        self.words = []
        ease = lambda: math.exp(c['ease_sd'] * rng.gauss(0, 1))
        known_S = lambda: min(3000.0, max(3.0, c['known_S'][0] * math.exp(c['known_S'][1] * rng.gauss(0, 1))))
        for i, row in enumerate(anki_rows):
            if i < c['anki_known']:
                S, last = known_S(), -rng.uniform(0, 2) * DAY
            else:  # reviewed in Anki, never mature
                S, last = math.exp(rng.gauss(0, 0.8)), -rng.uniform(10, 100) * DAY
            w = TrueWord(row, 'known' if i < c['anki_known'] else 'queue', S, last, ease(),
                         S * math.exp(c['anki_noise'] * rng.gauss(0, 1)))
            self.words.append(w)
        for i, row in enumerate(lexicon):
            if i < c['top_known']:
                w = TrueWord(row, 'known', known_S(), -rng.uniform(0, 2) * DAY, ease())
            elif rng.random() < c['queue_new']:
                w = TrueWord(row, 'queue', 0.0, None, ease())
            else:
                w = TrueWord(row, 'queue', math.exp(rng.gauss(0, 0.8)), -rng.uniform(10, 100) * DAY, ease())
            self.words.append(w)
        self.entries = {}
        if c['prior'] is not None:
            self.words += [TrueWord(row, 'queue', 0.0, None, ease())
                           for row in self._extra(c['extra'], in_lexicon | candidates)]
            self.entries = knowledge.word_entries([w.row for w in self.words])
        self.by_ja = {w.ja: w for w in self.words}
        self.lexicon = inflection.Lexicon([w.row for w in self.words])
        for w in self.words:
            form = polite(w.row)
            w.form = form if form and self.lexicon.entry(form) == w.ja else None
        offlist = [r for r in extra[len(anki_rows):] if self.lexicon.entry(r['ja']) is None][:c['offlist']]
        self.offlist = [TrueWord(r, 'offlist', 0.0, None, ease()) for r in offlist]
        for w in self.offlist:
            if rng.random() < c['offlist_seen']:
                w.S, w.last = math.exp(math.log(5.0) + rng.gauss(0, 1)), -rng.uniform(1, 30) * DAY
        t0 = self.t0 = local_midnight(self.start)
        for w in self.words + self.offlist:  # the clock starts at the first day's midnight
            w.last = None if w.last is None else t0 + w.last
            if w.kind == 'known':
                w.status, w.added, w.changed = 'known', self.start, self.start
        if c['prior'] is not None:
            self._start_from_features(t0)
        self.known = [w for w in self.words if w.kind == 'known']
        weights = [1 / (r + 1) ** c['zipf_known'] for r in range(len(self.known))]
        rng.shuffle(weights)  # how often a known word comes up doesn't follow its stability
        for w, weight in zip(self.known, weights):
            w.weight = weight
        self.queue = [w for w in self.words if w.status == 'queue']
        if c['prior'] is not None:  # chat's topical words come up by frequency: the queue in rank order
            self.queue.sort(key=lambda w: (self.entries.get(w.ja) or {}).get('rank') or 10 ** 9)
        for r, w in enumerate(self.queue):
            w.weight = 1 / (r + 1) ** c['zipf_queue']
        for r, w in enumerate(self.offlist):
            w.weight = 1 / (r + 1) ** c['zipf_offlist']
        self.offlist_cum = _cumulative(w.weight for w in self.offlist)
        self.learning, self.graduated = [], []
        for _ in range(c['learning']):
            self._introduce(t0 - rng.uniform(0, c['learn_days']) * DAY, self.start)
        self.new_per_day = c['new_per_day'] if c['new_per_day'] is not None else c['learning'] / c['learn_days']
        self.view = 0

    def _introduce(self, born, day):
        """The front of the queue joins the learning set."""
        c, rng = self.cfg, self.rng
        w = next((w for w in self.queue if w.status == 'queue'), None)
        if w is None:
            return None
        self.queue.remove(w)
        if w.S <= 0 and c['prior'] is None and rng.random() >= c['learn_new'] / max(c['queue_new'], 1e-9):
            w.S, w.last = math.exp(math.log(3.0) + 0.8 * rng.gauss(0, 1)), born - rng.uniform(5, 60) * DAY
        w.kind, w.status, w.added, w.changed, w.born = 'learning', 'active', day, day, born
        self.learning.append(w)
        return w

    def _extra(self, n, taken):
        """Up to n dictionary entries outside the vocabulary and `taken` (fwlib.key of lexicon and candidates.csv
        words), spread evenly over the rest of the dictionary's ranks (katakana words too, which the lexicon leaves
        out): ranked ones of two characters or more that dictionary.find takes back to themselves and the
        vocabulary's words don't take as a form, as rows (ja, en, hint, kana)."""
        if n <= 0:
            return []
        import dictionary
        d = dictionary.load()
        vocabulary = inflection.Lexicon([w.row for w in self.words])
        pool = [e for e in d if e['score'] and len(e['ja']) > 1 and fwlib.key(e['ja']) not in taken]
        out = []
        for i in range(n):
            e = pool[int((i + 0.5) * len(pool) / n)]
            if d.find(e['ja'], e['kana'], e['en']) is e and vocabulary.entry(e['ja']) is None:
                out.append({'ja': e['ja'], 'en': e['en'], 'hint': e['romaji'], 'kana': e['kana']})
        return out

    def _start_from_features(self, t0):
        """Every word not known starts from its features (the module docstring; cfg prior)."""
        c, rng = self.cfg, self.rng
        truth = dict(c['prior'])
        base, noise = truth.pop('base', 0.0), truth.pop('noise', 0.0)
        known = knowledge.kanji_counts(w.ja for w in self.words if w.kind == 'known')
        for w in self.words + self.offlist:
            if w.kind == 'known':
                continue
            entry = self.entries.get(w.ja)
            x = knowledge.word_features(entry, known) if entry else {}
            k0 = base + sum(truth.get(name, 0.0) * value for name, value in x.items()) + noise * rng.gauss(0, 1)
            q0 = 1 / (1 + math.exp(min(30.0, max(-30.0, k0))))
            recall = (c['habit'] - q0) / (c['habit'] - c['check'])
            if recall <= 0:
                w.S, w.last = 0.0, None
            else:
                days = rng.uniform(10, 100)
                w.S, w.last = F * days / (min(recall, 0.999) ** (1 / C) - 1), t0 - days * DAY
            if w.interval is not None:
                w.interval = w.S * math.exp(c['anki_noise'] * rng.gauss(0, 1))
            q = w.chance(t0, c)
            w.k0 = math.log((1 - q) / q)

    def time(self, day=None):
        """Local midnight of a simulated day (default: the next one to simulate)."""
        return local_midnight(day_name(local_midnight(self.start) + (self.day if day is None else day) * DAY
                                       + 12 * HOUR))

    def snapshot(self, at):
        """Every word's true P(lookup | read) at `at` (a mood factor of 1), and its status then."""
        return {w.ja: (w.chance(at, self.cfg), 'learning' if w.status == 'active' else
                       'new' if w.status == 'queue' else w.status) for w in self.words}

    def simulate_day(self):
        """One day of reading, in time order: [Showing]."""
        c, rng = self.cfg, self.rng
        d0 = self.time()
        today = day_name(d0)
        keep = []
        for w in self.learning:
            if d0 - w.born >= c['learn_days'] * DAY:
                w.kind, w.status, w.changed = 'known', 'known', today
                self.graduated.append(w)
            else:
                keep.append(w)
        self.learning = keep
        for _ in range(int(self.new_per_day) + (rng.random() < self.new_per_day % 1)):
            self._introduce(d0 + rng.uniform(0, 9) * HOUR, today)
        factor = math.exp(c['day_sd'] * rng.gauss(0, 1) - c['day_sd'] ** 2 / 2)
        known = self.known + self.graduated
        known_cum = _cumulative([w.weight for w in self.known] + [0.3 * min(w.weight for w in self.known)] *
                                len(self.graduated))
        queue_cum = _cumulative(w.weight for w in self.queue)
        showings = []
        for start in sorted(rng.uniform(9, 22.5) * HOUR for _ in range(c['chat_sessions'])):
            picks = list({id(w): w for w in rng.choices(self.learning, k=min(len(self.learning), c['chat_learning']))}
                         .values()) if self.learning else []
            topical = rng.choices(self.queue, cum_weights=queue_cum, k=c['chat_topical']) if self.queue else []
            self.view += 1
            for r in range(c['chat_replies']):
                showings.append(self._message(int(d0 + start + r * c['chat_gap'] * MINUTE), f'c{self.view}', f'm{r}',
                                              picks, topical, known, known_cum))
        for start in sorted(rng.uniform(9, 22.5) * HOUR for _ in range(c['web_sessions'])):
            self.view += 1
            for r in range(c['web_passages']):
                showings.append(self._passage(int(d0 + start + r * c['web_gap'] * MINUTE), f'w{self.view}', f'p{r}',
                                              known, known_cum))
        showings.sort(key=lambda s: s.at)
        for s in showings:
            self._read(s, factor * (c['chat_factor'] if s.surface == 't3' else 1.0))
        self.day += 1
        return showings

    def _known(self, known, cum, n, taken):
        out = []
        for _ in range(4 * n + 10):
            if len(out) >= n:
                break
            w = self.rng.choices(known, cum_weights=cum)[0]
            if w.ja not in taken:
                taken.add(w.ja)
                out.append(w)
        return out

    def _poisson(self, mean):
        n, total = 0, self.rng.expovariate(1)
        while total < mean:
            n += 1
            total += self.rng.expovariate(1)
        return n

    def _message(self, at, view, context, picks, topical, known, known_cum):
        c, rng = self.cfg, self.rng
        words = int(min(3000, max(60, c['chat_words'][0] * math.exp(c['chat_words'][1] * rng.gauss(0, 1)))))
        s = Showing(at, 't3', view, context, words)
        taken = set()
        content = []
        for w in [w for w in picks if rng.random() < c['chat_learning_p']] + \
                 [w for w in topical if rng.random() < c['chat_topical_p']]:
            if w.ja not in taken:
                taken.add(w.ja)
                content.append(w)
        offlist = [rng.choices(self.offlist, cum_weights=self.offlist_cum)[0] for _ in
                   range(self._poisson(c['chat_offlist']))] if self.offlist else []
        offlist = list({w.ja: w for w in offlist}.values())
        sentences = []
        for _ in range(self._poisson(c['chat_sentences'])):
            sentences.append(self._known(known, known_cum, rng.randint(2, 4), taken))
        fill = max(0, c['chat_japanese'] - len(content) - sum(len(x) for x in sentences))
        content += self._known(known, known_cum, fill, taken)
        mark = lambda w: (Mark(w.form, w, 'word', True) if w.form and rng.random() < c['form_share'] else
                          Mark(w.ja, w, 'word'))
        units = [[mark(w)] for w in content] + [[Mark(w.ja, w, 'offlist')] for w in offlist]
        units += [[mark(w) for w in sentence] + [Mark(SENTENCE_END, None, 'sentence')] for sentence in sentences]
        rng.shuffle(units)
        count = max(1, round(words / CHAT_PER_PARAGRAPH))
        shares = [rng.expovariate(1) for _ in range(count)]
        sizes = [max(5, round(words * x / sum(shares))) for x in shares]
        japanese = [i for i in range(count) if count == 1 or rng.random() < 0.7] or [0]
        placed = {i: [] for i in japanese}
        for unit in units:
            placed[rng.choice(japanese)].append(unit)
        for i, units_here in placed.items():  # Japanese is at most a third of a paragraph's words
            sizes[i] = max(sizes[i], 3 * sum(len(u) for u in units_here))
        s.words = max(words, sum(sizes))
        y = 0
        for i, size in enumerate(sizes):
            lines = math.ceil(size / CHAT_PER_LINE)
            height = lines * LINE_PX
            s.paragraphs.append((y, height, size))
            spots = sorted(rng.sample(range(lines * CHAT_PER_LINE), min(lines * CHAT_PER_LINE,
                                                                           sum(len(u) for u in placed.get(i, [])))))
            for m in (m for u in placed.get(i, []) for m in u):
                spot = spots.pop(0)
                m.x, m.y = (spot % CHAT_PER_LINE) * 40, y + (spot // CHAT_PER_LINE) * LINE_PX + 3
                s.marks.append(m)
            if i in placed:
                ms = size * c['pace'] * math.exp(c['chat_screen'][0] + c['chat_screen'][1] * rng.gauss(0, 1))
                if ms >= 1000:  # T3 logs a paragraph on screen for a second
                    first = ms * rng.uniform(0.5, 1) if rng.random() < 0.3 else ms
                    s.stays.append({'y': y, 'height': height, 'ms': int(first), 'words': size})
                    if first < ms:
                        s.stays.append({'y': y, 'height': height, 'ms': int(ms - first), 'words': size})
            y += height + 16
        s.height = y - 16
        s.marks.sort(key=lambda m: (m.y, m.x))
        if s.marks and rng.random() < c['chat_noise']:
            s.noise = rng.randrange(len(s.marks))
        return s

    def _passage(self, at, view, context, known, known_cum):
        c, rng = self.cfg, self.rng
        words = int(min(400, max(12, c['web_words'][0] * math.exp(c['web_words'][1] * rng.gauss(0, 1)))))
        s = Showing(at, 'browser', view, context, words)
        taken = set()
        chosen = []
        if self.learning and rng.random() < c['web_learning_p']:
            w = rng.choice(self.learning)
            taken.add(w.ja)
            chosen.append(w)
        chosen += self._known(known, known_cum, self._poisson(c['web_japanese']), taken)
        lines = math.ceil(words / WEB_PER_LINE)
        s.height = lines * LINE_PX
        spots = sorted(rng.sample(range(lines * WEB_PER_LINE), min(len(chosen), lines * WEB_PER_LINE)))
        for w, spot in zip(chosen, spots):
            m = Mark(w.ja, w, 'word')
            m.x, m.y = (spot % WEB_PER_LINE) * 50, (spot // WEB_PER_LINE) * LINE_PX + 3
            s.marks.append(m)
        s.active = int(words * c['pace'] * math.exp(c['web_screen'][0] + c['web_screen'][1] * rng.gauss(0, 1)))
        s.plan = {'level': 0, 'intensity': 1, 'jitter': 1, 'limit': len(s.marks), 'candidates': len(s.marks),
                  'budget': round(c['web_budget'] * words / 100, 3)}
        return s

    def _read(self, s, factor):
        """Who read what and looked up what, at the showing's time: each mark's truth and outcome."""
        c, rng = self.cfg, self.rng
        if not s.marks:
            return
        context = s.as_context()
        line = knowledge._line_height([m.y for m in s.marks])
        chance = knowledge.stay_chance if s.surface == 't3' else knowledge.read_chance
        depth = rng.random()  # one reading of the passage: a word is read when its read chance is above this
        for m in s.marks:
            m.read = chance(context, m.y, line, c['pace'], c['top'])
            m.was_read = depth < m.read
            if m.kind == 'sentence':
                m.q = min(1.0, factor * c['sentence_p'])
                m.looked = m.was_read and rng.random() < m.q
            else:
                w = m.word
                w.shown += 1
                m.status = ('learning' if w.status == 'active' else 'new' if w.status == 'queue' else w.status)
                m.q = w.chance(s.at, c, factor, m.form)
                m.looked = w.read(s.at, rng, c, factor, m.form) if m.was_read else False
            m.dwell = int(math.exp(math.log(1500) + 0.5 * rng.gauss(0, 1))) if m.looked else 0
            if m.looked and m.dwell < knowledge.GLANCE_MS:
                m.dwell = knowledge.GLANCE_MS  # a lookup is an open: glances are left out of the simulated reader

    def state(self):
        """The word state as state.json keeps it, every word with its status and the days it was added and changed."""
        words = []
        for w in self.words:
            entry = fwlib.blank(w.row)
            entry.update(status=w.status, added=w.added, changed=w.changed)
            words.append(entry)
        return {'schema': 1, 'created': self.start, 'stage': 3, 'stage_pinned': False, 'paused': False,
                'words': words, 'prefs': {}}

    def anki_rows(self):
        """anki.csv: the Anki words with their intervals."""
        return [dict(w.row, interval=round(w.interval, 1)) for w in self.words if w.interval is not None]
