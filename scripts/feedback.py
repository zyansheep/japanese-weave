"""Gloss interaction log (Python 3.9+, standard library only): every peek, pin or flip on a glossed word, with how long
the gloss stayed open and where the other glossed words of the same passage sat. A peek shows the reader was reading
that spot, so unpeeked words next to it are the evidence that they knew them. Clicks, text selections, mouse paths and
on-screen time in passages with glossed words are kept too, as more evidence of where the reader was and for how
long, and browser passages planned to get Japanese that got none (blank) are kept as shown and on screen, the baseline
for calibration (knowledge.py). Words, positions, times and counts only, never text.

Stored in <state dir>/interactions.sqlite (schema 8):
  contexts      one row per passage per page load (browser) or message render (T3): its box on the page, the
                glossed words in it as JSON [[word, x, y], ...] (positions relative to the passage), its word count
                and when it was shown ("shown": in the browser when its glossed words were first on screen, for a
                blank passage, with annotations [], when enough of it was; in T3 once per message, its prose words;
                older T3 messages have a word count from their transcript and no shown time), for browser passages
                how the amount was chosen ("plan", JSON {level: the reader's intensity -3..3, intensity: its factor,
                jitter: the passage's random factor (1: none), limit: swaps kept (before 2026-10-20: allowed),
                candidates: swaps the model offered, and since then budget: lookups the passage could spend on words
                not known at a glance (none at full), expected: lookups predicted if it is read; plans with
                sentencePrice, the lookups a whole sentence was priced at, are later still: their jitter is the share
                of free swaps kept and leaves the budget alone (before, it thinned every swap and scaled the budget),
                and candidates counts those the passage can place; since extension 0.11 paint: 'sure' if this is the
                painting of code's sure swaps, the model's answer not in, else 'final', and dictionary: dictionary
                words kept, at +1 and +2, when any}); seq orders changes for export. A sentence's
                closing mark (。) among the glossed words stands for its translation (in the browser since then too).
                A T3 message whose items carry the levels it was rendered at (render-time levels, twins.py) has a row
                per pair of levels it was shown at, its context "<message id>@<level>" when its word and sentence
                levels are equal, else "<message id>@<level>/<sentence level>" (level_context), each a showing of
                its own with the annotations that pair showed
  interactions  one row per peek (gloss opened by hovering), pin (opened by a click or tap, T3) or flip (word turned
                back to English, browser): when (ms since the epoch), how long the gloss was open, which word, its
                index among the passage's glossed words, and a client id (uid) so a resend is stored once
                (the phone app, T3: pins only, a view "phone-<run>-<word hash>" per word whose context lists that word
                alone and has no word count or stays: an episode that claims nothing about any other word or depth)
  reading       one row per reading signal in a passage (browser) or message (T3) with glossed words, positions
                relative to it, and a client id (uid); blank browser passages have visible rows only:
                  click    x, y; detail {"target": "text" | "link" (a link or control)}
                  select   x, y, width, height of the selection; ms: how long the drag took; detail {"chars": its
                           length, "covered": indexes of the glossed words in it (into the context's annotations)}
                  visible  one stay on screen of a paragraph: y, height (the paragraph's band; in the browser the whole
                           passage); ms: the time a reader could have been reading it; detail {"wall": all its time on
                           screen, and the rest by why it doesn't count: "away" (page hidden or window not focused),
                           "media" (sound or video playing), "idle" (no input for 30 s); "words": the paragraph's word
                           count, when sent (T3)}
                  trace    the mouse path over a message (T3): ms: its span; detail {"points": [[ms since at, x, y]]}

  ops           every change to the word state applied here, as sent (hub.py): uid, when, from which machine, kind
                and the op as JSON; also records that change nothing, such as what each chat reply was offered (kind
                "brief", read back by chat_briefs) and the Japanese sentences of chat replies (kind "sentences": per
                reply a fingerprint and per sentence counts and grammar pattern ids, never text; sentence_records)
  cards         one row per review card the collector served (review.py, POST /v1/review/next): its opaque id, when
                (served_at) and for which client (source: desktop or mobile), the word shown, its kind (learning: a
                word in use; probe: a word sampled for the probe of the goal, with its dictionary entry id, band and
                weight, probe.py; fake: a made-up word, data/fakes.tsv; the client is never told), the word's entry id
                when known; then what happened, from its review item (below): when it was shown (at), ms from shown to
                the reveal of its reading and meaning (reveal_ms, NULL when it was left without one), ms on screen in
                all (shown_ms), the client (via) and its uid, and the reader's grade, as Anki's buttons (grade: 1
                Again, 2 Hard, 3 Good, 4 Easy; NULL: left without one, or a client from before grades) with the ms
                from shown to it (grade_ms); released, when the client gave the card back unshown (a dropped
                prefetch, POST /v1/review/next's release: never reviewed, never read); seq orders changes for export,
                as for contexts. A word's graded learning cards, replayed in time order, are its spaced-repetition
                schedule (review.py, srs.py)

Items are interactions, or {"kind": "shown", "words": N, ...} records that only describe a context (the denominator of
the peek rate); from T3, any of them may carry "level": -3..3, the word level the message was rendered at, and with it
"sentenceLevel": -3..3, the sentence level (absent: the same; record); or
(T3 only) what happened to a review card: {"kind": "review", "at": ms when it was shown, "card": its id, "word": the
word it showed, "revealMs": ms to the reveal or null, "shownMs": ms on screen, "source": "desktop" | "mobile",
"uid"?, "grade"?: 1 | 2 | 3 | 4, "gradeMs"?: ms from shown to the grade}. A grade needs a reveal and its gradeMs,
revealMs <= gradeMs <= shownMs; an item without one is a card left (closed, backgrounded, skipped), which schedules
nothing. A review is stored on its card's row, once: it must name a card served here with that word and not
released, and a second review of a card (a resend, with or without the same uid) is a duplicate, except that a graded
item completes a card stored as left (no grade yet: a client that reported closing the card, then the grade), keeping
the first time it was shown. Every rejected item's reason comes back in the reply (reasons, by index), so a client can
settle items one by one and say why (shownMs over MAX_REVIEW_MS, a day, is one: clients clamp to it); a review item's
time up to FUTURE_MS ahead of the collector's clock is taken as now. With a collector configured (setup
--collector), the native host forwards items to it instead (see collector.py), and knowledge.py runs there.
"""
import gzip
import http.client
import json
import math
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request

import fwlib
import intensity

LEVEL_MARK = '@'  # a T3 message shown at a level is stored as <its context id>@<level> (level_context)
PAIR_MARK = '/'   # and at a word level and another sentence level as <its context id>@<level>/<sentence level>
DB_NAME = 'interactions.sqlite'
MIRROR_NAME = 'mirror.sqlite'
SCHEMA_VERSION = 8
SURFACES = ('browser', 't3')
KINDS = ('peek', 'pin', 'flip')  # the gloss was opened, or the word turned back to English
READING = ('click', 'select', 'visible', 'trace')  # where the reader was; knowledge.py reads the visible stays
SHOWN = 'shown'
REVIEW = 'review'  # what happened to a review card (cards table; review.py serves them)
REVIEW_SOURCES = ('desktop', 'mobile')
GRADES = (1, 2, 3, 4)  # a review card's grade, as Anki's buttons: Again, Hard, Good, Easy (srs.py)
CARD_KINDS = ('learning', 'probe', 'fake')
MAX_REVIEW_MS = 86_400_000  # the longest a card may have been on screen (knowledge.py drops those over a minute)
FUTURE_MS = 3_600_000      # a review item's time this far ahead of the collector's clock is taken as now (a client
                           # clock running fast would otherwise lose its grades for good); further ahead, rejected
BLANK = '[]'  # the annotations of a passage shown without Japanese
MAX_ITEMS = 500
MAX_ANNOTATIONS = 200
DAY_MS = 86_400_000
WINDOW_MS = 7 * DAY_MS  # how old an item may be when it arrives (a laptop offline for days keeps its queue)
EXPORT_PAGE = 5000

TABLES = """
CREATE TABLE IF NOT EXISTS contexts (
  surface TEXT NOT NULL,
  view TEXT NOT NULL,
  context TEXT NOT NULL,
  left REAL, top REAL, width REAL, height REAL,
  annotations TEXT NOT NULL,
  words INTEGER,
  shown_at INTEGER,
  seq INTEGER NOT NULL DEFAULT 0,
  plan TEXT,
  PRIMARY KEY (surface, view, context)
);
CREATE TABLE IF NOT EXISTS ops (
  uid TEXT PRIMARY KEY,
  at INTEGER NOT NULL,
  machine TEXT NOT NULL,
  kind TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interactions (
  id INTEGER PRIMARY KEY,
  at INTEGER NOT NULL,
  surface TEXT NOT NULL,
  kind TEXT NOT NULL,
  word TEXT NOT NULL,
  dwell_ms INTEGER NOT NULL,
  view TEXT NOT NULL,
  context TEXT NOT NULL,
  position INTEGER NOT NULL,
  uid TEXT
);
"""
READING_TABLE = """
CREATE TABLE IF NOT EXISTS reading (
  id INTEGER PRIMARY KEY,
  at INTEGER NOT NULL,
  surface TEXT NOT NULL,
  kind TEXT NOT NULL,
  view TEXT NOT NULL,
  context TEXT NOT NULL,
  x REAL,
  y REAL,
  width REAL,
  height REAL,
  ms INTEGER,
  detail TEXT NOT NULL,
  uid TEXT
);
"""
CARDS_TABLE = """
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY,
  served_at INTEGER NOT NULL,
  source TEXT NOT NULL,
  word TEXT NOT NULL,
  kind TEXT NOT NULL,
  entry INTEGER,
  band INTEGER,
  weight REAL,
  at INTEGER,
  reveal_ms INTEGER,
  shown_ms INTEGER,
  via TEXT,
  uid TEXT,
  seq INTEGER NOT NULL DEFAULT 0,
  grade INTEGER,
  grade_ms INTEGER,
  released INTEGER
);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS interactions_word ON interactions (word, at);
CREATE INDEX IF NOT EXISTS interactions_context ON interactions (surface, view, context);
CREATE UNIQUE INDEX IF NOT EXISTS interactions_uid ON interactions (uid);
CREATE INDEX IF NOT EXISTS contexts_seq ON contexts (seq);
CREATE INDEX IF NOT EXISTS reading_context ON reading (surface, view, context);
CREATE UNIQUE INDEX IF NOT EXISTS reading_uid ON reading (uid);
CREATE UNIQUE INDEX IF NOT EXISTS cards_uid ON cards (uid);
CREATE INDEX IF NOT EXISTS cards_seq ON cards (seq);
CREATE INDEX IF NOT EXISTS cards_served ON cards (served_at);
"""


def default_path():
    return fwlib.home() / DB_NAME


def connect(path=None, timeout=10):
    path = Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=timeout)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(TABLES)
    version = con.execute('PRAGMA user_version').fetchone()[0]
    if version == 3:  # its reading table needs x and y and has no ms column
        _migrate_reading(con)
    con.executescript(READING_TABLE)
    con.executescript(CARDS_TABLE)
    if version < SCHEMA_VERSION:
        _migrate(con)
    con.executescript(INDEXES)
    con.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    return con


def _migrate(con):
    """Schema 1 -> 2: word counts, shown times and a change sequence on contexts; client ids on interactions.
    Schemas 3 and 4 only added the reading table, which READING_TABLE creates. Schema 5: the plan of a shown passage.
    Schema 6 only added the cards table, which CARDS_TABLE creates. Schema 7: a review card's grade and grade_ms;
    schema 8: its released mark."""
    columns = lambda table: {row[1] for row in con.execute(f'PRAGMA table_info({table})')}
    with con:
        have = columns('contexts')
        for name, kind in (('words', 'INTEGER'), ('shown_at', 'INTEGER'), ('seq', 'INTEGER NOT NULL DEFAULT 0'),
                           ('plan', 'TEXT')):
            if name not in have:
                con.execute(f'ALTER TABLE contexts ADD COLUMN {name} {kind}')
        con.execute('UPDATE contexts SET seq = rowid WHERE seq = 0')
        if 'uid' not in columns('interactions'):
            con.execute('ALTER TABLE interactions ADD COLUMN uid TEXT')
        have = columns('cards')
        for name in ('grade', 'grade_ms', 'released'):
            if name not in have:
                con.execute(f'ALTER TABLE cards ADD COLUMN {name} INTEGER')


def _migrate_reading(con):
    """Schema 3 -> 4: the reading table, then only clicks and selections, gets nullable positions and an ms column."""
    have = {row[1] for row in con.execute('PRAGMA table_info(reading)')}
    if not have or 'ms' in have:
        return
    con.executescript(f"""BEGIN;
        ALTER TABLE reading RENAME TO reading_3;
        DROP INDEX IF EXISTS reading_context;
        DROP INDEX IF EXISTS reading_uid;
        {READING_TABLE}
        INSERT INTO reading (id, at, surface, kind, view, context, x, y, width, height, ms, detail, uid)
          SELECT id, at, surface, kind, view, context, x, y, width, height, json_extract(detail, '$.ms'),
                 json_remove(detail, '$.ms'), uid FROM reading_3;
        DROP TABLE reading_3;
        COMMIT;""")


def _clean(item, now):
    """The item as stored, or None when anything is malformed (the host checks again what the extension checked)."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    word = lambda x: isinstance(x, str) and 0 < len(x) <= 40
    ident = lambda x: isinstance(x, str) and 0 < len(x) <= 64 and all(c.isalnum() or c in '_:-' for c in x)
    if isinstance(item, dict) and item.get('kind') == REVIEW:
        return _clean_review(item, now, num, word, ident)
    if not isinstance(item, dict) or item.get('kind') not in KINDS + READING + (SHOWN,):
        return None
    if not ident(item.get('view')) or not ident(item.get('context')):
        return None
    at, box, notes = item.get('at'), item.get('box'), item.get('annotations')
    if not num(at) or not now - WINDOW_MS <= at <= now + 60_000:
        return None
    if not isinstance(box, list) or len(box) != 4 or not all(num(v) for v in box):
        return None
    if not isinstance(notes, list) or len(notes) > MAX_ANNOTATIONS:
        return None
    if not all(isinstance(a, list) and len(a) == 3 and word(a[0]) and num(a[1]) and num(a[2]) for a in notes):
        return None
    level, sentences = item.get('level'), item.get('sentenceLevel')
    dial = lambda x: x is None or (isinstance(x, int) and not isinstance(x, bool) and x in intensity.LEVELS)
    if not dial(level) or not dial(sentences):
        return None
    if level is None:  # an older message (no level markup): no dials, whatever else it says
        sentences = None
    elif sentences is None:  # a client with one dial: the sentence level is the word level
        sentences = level
    clean = {'kind': item['kind'], 'view': item['view'], 'context': item['context'], 'at': int(at), 'box': box,
             'annotations': [[a[0], round(a[1]), round(a[2])] for a in notes], 'level': level,
             'sentences': sentences}
    if item['kind'] == SHOWN:
        words = item.get('words')
        if not isinstance(words, int) or isinstance(words, bool) or not 0 < words <= 10_000:
            return None
        plan = item.get('plan')
        if plan is not None and not _plan_ok(plan, num):
            return None
        return dict(clean, words=words, plan=plan)
    uid = item.get('uid')
    if uid is not None and not ident(uid):
        return None
    if item['kind'] in READING:
        return _clean_reading(item, dict(clean, uid=uid), num)
    dwell = item.get('dwellMs')
    if not word(item.get('word')) or not isinstance(item.get('position'), int) or isinstance(item.get('position'), bool):
        return None
    if not num(dwell) or not 0 <= dwell <= 600_000:
        return None
    return dict(clean, word=item['word'], position=item['position'], dwellMs=int(dwell), uid=uid)


def _plan_ok(plan, num):
    """As the extension's cleanPlan (hostsource.js) checks it; budget, spent, expected, sentencePrice and arms are
    optional, as plans from before levels meant a lookup budget have none of them (and full's has no budget), plans
    from before whole sentences were priced have no sentencePrice (their thinning also scaled the budget), and plans
    from before extension 0.10 no spent (what the swaps within the budget cost) or arms (the delayed-start arm of each
    word shown that has one: {word: 'shown' | 'held:probe' | 'shown:probe'}), and plans from before 0.11 no paint ('sure':
    the swaps code is sure of, the model's answer not in; 'final') or dictionary (dictionary words kept, +1 and +2)."""
    integer = lambda x, top: isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= top
    amount = lambda key, top=1000: key not in plan or (num(plan[key]) and 0 <= plan[key] <= top)
    arms = plan.get('arms', {}) if isinstance(plan, dict) else None
    return (isinstance(plan, dict)
            and set(plan) <= {'level', 'intensity', 'jitter', 'limit', 'candidates', 'budget', 'spent', 'expected',
                              'sentencePrice', 'arms', 'paint', 'dictionary'}
            and plan.get('level') in intensity.LEVELS and num(plan.get('intensity')) and 0 < plan['intensity'] <= 5
            and num(plan.get('jitter')) and 0 < plan['jitter'] <= 1 and integer(plan.get('limit'), 1000)
            and integer(plan.get('candidates'), 1000) and amount('budget') and amount('spent') and amount('expected')
            and amount('sentencePrice', 1) and plan.get('paint', 'final') in ('sure', 'final')
            and ('dictionary' not in plan or integer(plan['dictionary'], 1000))
            and isinstance(arms, dict) and len(arms) <= 200
            and all(isinstance(w, str) and 0 < len(w) <= 40 and isinstance(a, str) and 0 < len(a) <= 20
                    for w, a in arms.items()))


def _clean_review(item, now, num, word, ident):
    """A review item as stored (cards table), or why it isn't (a string) when anything is malformed: at within the
    window, the card's id, the word it showed, revealMs null or 0..shownMs, shownMs 0..MAX_REVIEW_MS, the client, an
    optional uid, and an optional grade (1..4) with its gradeMs, which needs a reveal: revealMs <= gradeMs <= shownMs
    (a gradeMs without a grade is malformed too)."""
    at, reveal, shown, uid = item.get('at'), item.get('revealMs'), item.get('shownMs'), item.get('uid')
    grade, graded = item.get('grade'), item.get('gradeMs')
    if not num(at) or not now - WINDOW_MS <= at <= now + FUTURE_MS:
        return 'at: a time within the last 7 days'
    at = min(at, now)  # a client clock running ahead: taken as now (not up to FUTURE_MS: dropped)
    if not ident(item.get('card')) or not word(item.get('word')) or item.get('source') not in REVIEW_SOURCES:
        return 'card, word and source ("desktop" or "mobile") are needed'
    if not num(shown) or not 0 <= shown <= MAX_REVIEW_MS:
        return f'shownMs: 0 to {MAX_REVIEW_MS} ms'
    if reveal is not None and (not num(reveal) or not 0 <= reveal <= shown):
        return 'revealMs: null or 0 to shownMs'
    if uid is not None and not ident(uid):
        return 'uid: up to 64 letters, digits, _ : -'
    if grade is not None or graded is not None:
        if grade not in GRADES or isinstance(grade, bool) or reveal is None or not num(graded) \
                or not reveal <= graded <= shown:
            return 'grade: 1 to 4, with a reveal and gradeMs from revealMs to shownMs'
    return {'kind': REVIEW, 'at': int(at), 'card': item['card'], 'word': item['word'],
            'revealMs': None if reveal is None else round(reveal), 'shownMs': round(shown), 'source': item['source'],
            'uid': uid, 'grade': grade, 'gradeMs': None if graded is None else round(graded)}


MAX_TRACE_POINTS = 200
MAX_VISIT_MS = 3_600_000


def _clean_reading(item, clean, num):
    """A reading signal as stored: the row's columns (x, y, width, height, ms; None where they don't apply) and its
    detail. None when anything is malformed."""
    coord = lambda x: num(x) and abs(x) <= 100_000
    count = lambda x: isinstance(x, int) and not isinstance(x, bool)
    span = lambda x, top=600_000: num(x) and 0 <= x <= top
    kind = item['kind']
    row = dict(clean, x=None, y=None, width=None, height=None, ms=None)
    if kind == 'trace':
        points = item.get('points')
        if not isinstance(points, list) or not 0 < len(points) <= MAX_TRACE_POINTS:
            return None
        if not all(isinstance(p, list) and len(p) == 3 and span(p[0]) and coord(p[1]) and coord(p[2]) for p in points):
            return None
        if any(b[0] < a[0] for a, b in zip(points, points[1:])):
            return None
        points = [[round(t), round(x), round(y)] for t, x, y in points]
        return dict(row, ms=points[-1][0], detail={'points': points})
    if kind == 'visible':
        times = [item.get(k) for k in ('ms', 'wall', 'away', 'media', 'idle')]
        if not coord(item.get('y')) or not coord(item.get('height')) or item['height'] < 0:
            return None
        if not all(span(t, MAX_VISIT_MS) for t in times) or times[0] > times[1]:
            return None
        words = item.get('words')
        if words is not None and not (count(words) and 0 <= words <= 10_000):
            return None
        ms, wall, away, media, idle = (round(t) for t in times)
        detail = {'wall': wall, 'away': away, 'media': media, 'idle': idle}
        return dict(row, y=round(item['y']), height=round(item['height']), ms=ms,
                    detail=detail if words is None else dict(detail, words=words))
    if not coord(item.get('x')) or not coord(item.get('y')):
        return None
    row = dict(row, x=round(item['x']), y=round(item['y']))
    if kind == 'click':
        return dict(row, detail={'target': item['target']}) if item.get('target') in ('text', 'link') else None
    width, height, chars, covered, ms = (item.get(k) for k in ('width', 'height', 'chars', 'covered', 'ms'))
    if not coord(width) or not coord(height) or width < 0 or height < 0 or not count(chars) or not 0 < chars <= 1_000_000:
        return None
    if not isinstance(covered, list) or not all(count(i) and 0 <= i < len(clean['annotations']) for i in covered):
        return None
    if not span(ms):
        return None
    return dict(row, width=round(width), height=round(height), ms=round(ms),
                detail={'chars': chars, 'covered': sorted(set(covered))})


CONTEXT_UPSERT = (
    'INSERT INTO contexts (surface, view, context, left, top, width, height, annotations, words, shown_at, plan, seq) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (SELECT coalesce(max(seq), 0) + 1 FROM contexts)) '
    'ON CONFLICT (surface, view, context) DO UPDATE SET left = excluded.left, top = excluded.top, '
    'width = excluded.width, height = excluded.height, annotations = excluded.annotations, '
    'words = coalesce(excluded.words, contexts.words), shown_at = coalesce(contexts.shown_at, excluded.shown_at), '
    'plan = coalesce(contexts.plan, excluded.plan), seq = excluded.seq')


CARD_REVIEW = ('UPDATE cards SET at = ?, reveal_ms = ?, shown_ms = ?, via = ?, uid = ?, grade = ?, grade_ms = ?, '
               'seq = (SELECT coalesce(max(seq), 0) + 1 FROM cards) '
               'WHERE id = ? AND word = ? AND at IS NULL AND released IS NULL')
CARD_GRADE = ('UPDATE cards SET reveal_ms = ?, shown_ms = ?, grade = ?, grade_ms = ?, via = ?, '
              'seq = (SELECT coalesce(max(seq), 0) + 1 FROM cards) '
              'WHERE id = ? AND word = ? AND at IS NOT NULL AND grade IS NULL AND released IS NULL')


def _complete(con, c):
    """A graded item for a card stored as left: its reveal, end and grade on the row, in ms from the first time the
    card was shown (kept). Returns whether it was stored."""
    found = con.execute('SELECT at FROM cards WHERE id = ? AND word = ? AND at IS NOT NULL AND grade IS NULL',
                        (c['card'], c['word'])).fetchone()
    if c['grade'] is None or not found:
        return False
    shift = c['at'] - found[0]
    graded = min(MAX_REVIEW_MS, max(0, c['gradeMs'] + shift))  # 0 <= reveal <= graded <= shown <= MAX_REVIEW_MS
    reveal = min(graded, max(0, c['revealMs'] + shift))
    shown = min(MAX_REVIEW_MS, max(graded, c['shownMs'] + shift))
    return con.execute(CARD_GRADE, (reveal, shown, c['grade'], graded, c['source'], c['card'],
                                    c['word'])).rowcount > 0


def level_context(context, level, sentences=None):
    """The context id a T3 message shown at a pair of levels (the word level, and the sentence level: None or the same
    for one dial) is stored under: <message>@<level> when the two are equal, else <message>@<level>/<sentences>. A
    message shown at another pair, or re-rendered at one while on screen, is another showing, with its own
    annotations and items (render-time levels, twins.py)."""
    if sentences is None or sentences == level:
        return f'{context}{LEVEL_MARK}{level}'
    return f'{context}{LEVEL_MARK}{level}{PAIR_MARK}{sentences}'


def split_levels(context):
    """(message id, word level or None, sentence level or None) of a stored T3 context id (level_context): a context
    stored at one level has its sentence level equal to it; one without levels has neither."""
    base, mark, levels = str(context).rpartition(LEVEL_MARK)
    level = lambda x: int(x) if x.lstrip('-').isdigit() and int(x) in intensity.LEVELS else None
    words, pair, sentences = levels.partition(PAIR_MARK)
    if mark and base and level(words) is not None and (not pair or level(sentences) is not None):
        return base, level(words), level(sentences) if pair else level(words)
    return context, None, None


def split_context(context):
    """(message id, word level or None) of a stored T3 context id (level_context, split_levels)."""
    return split_levels(context)[:2]


def english_pair(c):
    """Whether a T3 item with levels was rendered with no Japanese: both dials at -3 (off, English only). An item with
    one dial at -3 still showed the other kind's Japanese."""
    return c.get('level') is not None and c['level'] <= intensity.OFF and (c.get('sentences') if c.get('sentences')
                                                                          is not None else c['level']) <= intensity.OFF


SIBLING_WORDS = ('SELECT words FROM contexts WHERE surface = ? AND view = ? AND words IS NOT NULL AND '
                 '(context = ? OR substr(context, 1, ?) = ?) ORDER BY seq DESC LIMIT 1')


def record(items, surface, path=None, now=None, hide=None):
    """Stores well-formed items. Returns {stored, duplicates, rejected} and, from T3 when any item was rejected,
    reasons: [{item: its index in items, error}], one per rejected item (every one, so T3 Code's server can settle
    items one by one; items past MAX_ITEMS in one batch are rejected too, each with its reason): a resent interaction (same uid) is stored once, and
    malformed or too old items are rejected (never worth resending). A review (T3 only) is stored on its card
    (CARD_REVIEW): one naming no card served here with its word, or a card released, is rejected; one for a card
    already reviewed is a duplicate, unless it brings the grade of a card stored as left (_complete). hide: (start,
    end) of an English-only spell in chat (intensity.py): a T3 message's shown record from then is dropped (counted as
    rejected), since its Japanese wasn't on screen.

    A T3 item with a level (the word level the message was rendered at, -3..3: render-time levels; the client sends
    one only for a message with level markup, so an older message stays one showing, as before) and its sentenceLevel
    (the sentence level, -3..3; absent: the same, a client with one dial; ignored without a level) is stored under
    level_context, so each pair of levels a message was shown at is a showing of its own, with its shown record (one
    per message and pair) giving its own word count. An item at (-3, -3) (off, English only) showed no Japanese: it
    is dropped (rejected), but for a shown record, kept with no annotations (never a showing: knowledge.is_showing) so
    its word count is there for the pairs that follow; an item with one dial at -3 showed the other kind's Japanese
    and is kept; hide leaves items with a level alone (they say what was shown). A pair's first item that isn't its
    shown record starts the showing: its word count from the message's showing at another pair, shown when that item
    came, until the pair's own shown record brings its own."""
    if surface not in SURFACES or not isinstance(items, list):
        return {'stored': 0, 'duplicates': 0, 'rejected': len(items) if isinstance(items, list) else 0}
    now = now if now is not None else int(time.time() * 1000)
    batch = items[:MAX_ITEMS]
    reasons = []
    cleaned = []
    for index, item in enumerate(batch):
        c = _clean(item, now)
        if not c or isinstance(c, str) or (c['kind'] == REVIEW and surface != 't3'):
            why = c if isinstance(c, str) else 'malformed or too old' if not c else \
                'review items come from T3 (surface t3)'
            reasons.append({'item': index, 'error': why})
        elif c['kind'] not in (REVIEW, SHOWN) and surface == 't3' and english_pair(c):
            # a shown record at (-3, -3) is kept for its word count (below)
            reasons.append({'item': index, 'error': 'at -3 (off or English only): no Japanese on screen'})
        elif hide and surface == 't3' and c['kind'] == SHOWN and c.get('level') is None \
                and hide[0] <= c['at'] < hide[1]:
            reasons.append({'item': index, 'error': 'shown while English only was on'})
        else:
            if c['kind'] != REVIEW and surface != 't3':
                c['level'] = c['sentences'] = None  # levels are chat's
            c['index'] = index
            cleaned.append(c)
    stored = duplicates = refused = 0
    if cleaned:
        con = connect(path)
        try:
            with con:
                for c in cleaned:
                    if c['kind'] == REVIEW:
                        try:
                            added = con.execute(CARD_REVIEW, (c['at'], c['revealMs'], c['shownMs'], c['source'],
                                                              c['uid'], c['grade'], c['gradeMs'], c['card'],
                                                              c['word'])).rowcount
                        except sqlite3.IntegrityError:  # its uid is stored already: a resend
                            added = 0
                        if added or _complete(con, c):
                            stored += 1
                            continue
                        row = con.execute('SELECT released FROM cards WHERE id = ? AND word = ?',
                                          (c['card'], c['word'])).fetchone()
                        if row and row[0] is None:
                            duplicates += 1
                            continue
                        refused += 1
                        reasons.append({'item': c['index'], 'error': 'card released unshown' if row else
                                        'no card served here with this id and word'})
                        continue
                    shown = c['kind'] == SHOWN
                    plan = c.get('plan') if shown else None
                    words, shown_at = (c['words'], c['at']) if shown else (None, None)
                    if c.get('level') is not None:
                        base = c['context']
                        c = dict(c, context=level_context(base, c['level'], c['sentences']))
                        if english_pair(c):  # shown in English: its words, for the levels that follow
                            c['annotations'] = []
                        if not shown and not con.execute('SELECT 1 FROM contexts WHERE surface = ? AND view = ? AND '
                                                         'context = ?', (surface, c['view'], c['context'])).fetchone():
                            sibling = con.execute(SIBLING_WORDS, (surface, c['view'], base, len(base) + 1,
                                                                  base + LEVEL_MARK)).fetchone()
                            words, shown_at = (sibling[0] if sibling else None), c['at']
                    con.execute(CONTEXT_UPSERT, (surface, c['view'], c['context'], *c['box'],
                                                 json.dumps(c['annotations'], ensure_ascii=False),
                                                 words, shown_at, json.dumps(plan) if plan else None))
                    if shown:
                        stored += 1
                        continue
                    if c['kind'] in READING:
                        added = con.execute(
                            'INSERT OR IGNORE INTO reading (at, surface, kind, view, context, x, y, width, height, ms, '
                            'detail, uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (c['at'], surface, c['kind'], c['view'], c['context'], c['x'], c['y'], c['width'],
                             c['height'], c['ms'], json.dumps(c['detail']), c['uid'])).rowcount
                        stored += added
                        duplicates += 1 - added
                        continue
                    added = con.execute(
                        'INSERT OR IGNORE INTO interactions (at, surface, kind, word, dwell_ms, view, context, position, '
                        'uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (c['at'], surface, c['kind'], c['word'], c['dwellMs'], c['view'], c['context'], c['position'],
                         c['uid'])).rowcount
                    stored += added
                    duplicates += 1 - added
        finally:
            con.close()
    reasons += [{'item': index, 'error': f'over {MAX_ITEMS} in one batch'} for index in range(len(batch), len(items))]
    out = {'stored': stored, 'duplicates': duplicates,
           'rejected': len(batch) - len(cleaned) + refused + len(items) - len(batch)}
    if not reasons or surface != 't3':  # T3 Code's server settles its items one by one; the browser's host needn't
        return out
    return dict(out, reasons=sorted(reasons, key=lambda r: r['item']))


CARD_COLUMNS = ('id', 'served_at', 'source', 'word', 'kind', 'entry', 'band', 'weight', 'at', 'reveal_ms', 'shown_ms',
                'via', 'uid', 'seq', 'grade', 'grade_ms', 'released')


def serve_card(card, path=None, con=None):
    """Keeps a served review card (review.py): {id, served_at, source, word, kind, entry?, band?, weight?}."""
    own = con is None
    con = con or connect(path)
    try:
        with con:
            con.execute('INSERT INTO cards (id, served_at, source, word, kind, entry, band, weight, seq) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, (SELECT coalesce(max(seq), 0) + 1 FROM cards))',
                        (card['id'], card['served_at'], card['source'], card['word'], card['kind'], card.get('entry'),
                         card.get('band'), card.get('weight')))
    finally:
        if own:
            con.close()


def cards(path=None, since=0, con=None):
    """Every review card served at or after `since` (ms), oldest first, reviewed or not: {id, served_at, source,
    word, kind, entry, band, weight, at, reveal_ms, shown_ms, via, uid, seq, grade, grade_ms, released} (at None: no
    review yet; grade None: none given; released: when it was given back unshown, else None)."""
    own = con is None
    con = con or connect(path)
    try:
        rows = con.execute(f'SELECT {", ".join(CARD_COLUMNS)} FROM cards WHERE served_at >= ? ORDER BY served_at, id',
                           (since,)).fetchall()
    finally:
        if own:
            con.close()
    return [dict(zip(CARD_COLUMNS, r)) for r in rows]


GRADED = "FROM cards WHERE grade IS NOT NULL AND at IS NOT NULL AND kind = 'learning'"


def graded_mark(con):
    """What changes whenever a graded learning card is stored (count, newest seq): review.py's schedules are cached
    under it."""
    return tuple(con.execute(f'SELECT count(*), coalesce(max(seq), 0) {GRADED}').fetchone())


def graded(path=None, con=None):
    """Every graded learning card (the reviews spaced repetition replays; probe and made-up cards never schedule
    anything), in the order they were graded: {card, word, entry, served (when it was served, its buttons' time), at
    (shown), grade, grade_ms, reviewed (at + grade_ms, when the grade was given)}."""
    own = con is None
    con = con or connect(path)
    try:
        rows = con.execute(f'SELECT id, word, entry, served_at, at, grade, grade_ms {GRADED} '
                           'ORDER BY at + coalesce(grade_ms, 0), id').fetchall()
    finally:
        if own:
            con.close()
    return [{'card': card, 'word': word, 'entry': entry, 'served': served, 'at': at, 'grade': grade, 'grade_ms': ms,
             'reviewed': at + (ms or 0)} for card, word, entry, served, at, grade, ms in rows]


def deck_key(entry, word):
    """The key a word's schedule is kept under: its dictionary entry id when the card had one, else its spelling."""
    return f'entry:{entry}' if isinstance(entry, int) and not isinstance(entry, bool) else word


def deck_groups(rows):
    """Graded learning cards (graded, or reviews' rows of kind learning; in the order given, oldest grade first) as one
    group per word: a spelling's cards all go to the group of the latest entry id any of them had (a card served before
    the word had an id joins it; a spelling whose id changed moves, all its cards with it, to the new id's group, and
    leaves the old id's other spellings where they are), spellings of one entry share its group (a kanji spelling and
    its kana one), and a spelling never given an id is a group of its own. {key: rows}, the key deck_key of the group's
    entry id, else its spelling."""
    latest = {}
    for r in rows:
        entry = r.get('entry')
        if isinstance(entry, int) and not isinstance(entry, bool):
            latest[r['word']] = entry
    out = {}
    for r in rows:
        out.setdefault(deck_key(latest.get(r['word']), r['word']), []).append(r)
    return out


def release_cards(ids, source, now, path=None, con=None):
    """Marks cards served to `source` and never reviewed as released (given back unshown: a dropped prefetch): no
    longer pending, never left, never read. Returns how many."""
    ids = list(ids or ())
    if not ids:
        return 0
    own = con is None
    con = con or connect(path)
    try:
        with con:  # one row at a time, so each gets a seq of its own
            return sum(con.execute('UPDATE cards SET released = ?, seq = (SELECT coalesce(max(seq), 0) + 1 FROM cards) '
                                   'WHERE id = ? AND source = ? AND at IS NULL AND released IS NULL',
                                   (now, card, source)).rowcount for card in dict.fromkeys(ids))
    finally:
        if own:
            con.close()


def reviews(path=None):
    """Every reviewed card, oldest review first, as knowledge.py and probe.py read them: {card, at (when it was shown),
    served (when it was served), word, kind, entry, band, weight, reveal (ms, None: left without a reveal), shown (ms
    on screen), source, grade (1..4, None: none given), grade_ms (ms from shown to the grade)}."""
    con = connect(path)
    try:
        rows = con.execute('SELECT id, at, served_at, word, kind, entry, band, weight, reveal_ms, shown_ms, via, grade, '
                           'grade_ms FROM cards WHERE at IS NOT NULL ORDER BY at, id').fetchall()
    finally:
        con.close()
    names = ('card', 'at', 'served', 'word', 'kind', 'entry', 'band', 'weight', 'reveal', 'shown', 'source', 'grade',
             'grade_ms')
    return [dict(zip(names, r)) for r in rows]


def recent(limit=20, path=None):
    """The latest interactions, clicks and selections, newest first, each with the glossed words of its passage."""
    con = connect(path)
    try:
        rows = con.execute(
            'SELECT i.at, i.surface, i.kind, i.word, i.dwell_ms, i.position, c.annotations FROM interactions i '
            'JOIN contexts c USING (surface, view, context) ORDER BY i.at DESC, i.id DESC LIMIT ?', (limit,)).fetchall()
        reading = con.execute(
            'SELECT r.at, r.surface, r.kind, r.x, r.y, r.ms, r.detail, c.annotations FROM reading r '
            'JOIN contexts c USING (surface, view, context) ORDER BY r.at DESC, r.id DESC LIMIT ?', (limit,)).fetchall()
    finally:
        con.close()
    items = [{'at': at, 'surface': surface, 'kind': kind, 'word': word, 'dwellMs': dwell, 'position': position,
              'annotations': json.loads(notes)} for at, surface, kind, word, dwell, position, notes in rows]
    items += [{'at': at, 'surface': surface, 'kind': kind, 'x': x, 'y': y, 'ms': ms, 'detail': json.loads(detail),
               'annotations': json.loads(notes)} for at, surface, kind, x, y, ms, detail, notes in reading]
    return sorted(items, key=lambda i: i['at'], reverse=True)[:limit]


def stats(since=0, path=None):
    """What has been collected, for the extension's popup and `fw.py log`: per "surface:kind", [all, since `since`
    (ms)] for gloss opens and reading signals (and "t3:review" for review cards reviewed, once there are any); the
    same for browser words shown and for on-screen reading time (the ms of visible rows), both in passages with
    Japanese, and for blank passages shown (a count); and the newest item's time."""
    con = connect(path)
    try:
        counts, last = {}, 0
        for table in ('interactions', 'reading'):
            for surface, kind, total, recent_, newest in con.execute(
                    f'SELECT surface, kind, count(*), coalesce(sum(at >= ?), 0), max(at) FROM {table} '
                    'GROUP BY surface, kind', (since,)):
                counts[f'{surface}:{kind}'] = [total, recent_]
                last = max(last, newest)
        reviewed, recent_, newest = con.execute('SELECT count(*), coalesce(sum(at >= ?), 0), max(at) FROM cards '
                                                'WHERE at IS NOT NULL', (since,)).fetchone()
        if reviewed:
            counts['t3:review'] = [reviewed, recent_]
            last = max(last, newest)
        shown = con.execute('SELECT coalesce(sum(words), 0), coalesce(sum(CASE WHEN shown_at >= ? THEN words END), 0) '
                            'FROM contexts WHERE shown_at IS NOT NULL AND annotations != ?', (since, BLANK)).fetchone()
        blank = con.execute('SELECT count(*), coalesce(sum(shown_at >= ?), 0) FROM contexts '
                            "WHERE surface = 'browser' AND shown_at IS NOT NULL AND annotations = ?",
                            (since, BLANK)).fetchone()  # a T3 message shown in English (at -3) is no blank passage
        onscreen = con.execute('SELECT coalesce(sum(r.ms), 0), coalesce(sum(CASE WHEN r.at >= ? THEN r.ms END), 0) '
                               'FROM reading r JOIN contexts c USING (surface, view, context) '
                               "WHERE r.kind = 'visible' AND c.annotations != ?", (since, BLANK)).fetchone()
    finally:
        con.close()
    return {'counts': counts, 'shownWords': list(shown), 'blankPassages': list(blank), 'readingMs': list(onscreen),
            'last': last or None, 'since': since}


def count(path=None):
    con = connect(path)
    try:
        return con.execute('SELECT count(*) FROM interactions').fetchone()[0]
    finally:
        con.close()


INTERACTION_COLUMNS = ('id', 'at', 'surface', 'kind', 'word', 'dwell_ms', 'view', 'context', 'position', 'uid')
CONTEXT_COLUMNS = ('surface', 'view', 'context', 'left', 'top', 'width', 'height', 'annotations', 'words', 'shown_at',
                   'seq', 'plan')


def export(after_id=0, after_seq=0, limit=EXPORT_PAGE, path=None, after_card=None):
    """Interactions with id > after_id and contexts changed after seq after_seq, oldest first, for a mirror; with
    after_card, also the review cards changed after that seq (a mirror from before cards asks for none, so its "more"
    never waits on them)."""
    con = connect(path)
    try:
        rows = con.execute(f'SELECT {", ".join(INTERACTION_COLUMNS)} FROM interactions WHERE id > ? ORDER BY id LIMIT ?',
                           (after_id, limit)).fetchall()
        contexts = con.execute(f'SELECT {", ".join(CONTEXT_COLUMNS)} FROM contexts WHERE seq > ? ORDER BY seq LIMIT ?',
                               (after_seq, limit)).fetchall()
        changed = [] if after_card is None else con.execute(
            f'SELECT {", ".join(CARD_COLUMNS)} FROM cards WHERE seq > ? ORDER BY seq LIMIT ?',
            (after_card, limit)).fetchall()
        max_id = con.execute('SELECT coalesce(max(id), 0) FROM interactions').fetchone()[0]
    finally:
        con.close()
    out = {'interactions': [dict(zip(INTERACTION_COLUMNS, r)) for r in rows],
           'contexts': [dict(zip(CONTEXT_COLUMNS, r)) for r in contexts],
           'more': len(rows) == limit or len(contexts) == limit or len(changed) == limit, 'maxId': max_id}
    if after_card is not None:
        out['cards'] = [dict(zip(CARD_COLUMNS, r)) for r in changed]
    return out


def sync_mirror(collector, path, timeout=4, pages=20):
    """Brings the local mirror of a collector's database up to date. Returns (ok, error). A collector whose
    database is behind the mirror (it was reset) makes the mirror start over."""
    con = connect(path)
    try:
        for _ in range(pages):
            after_id = con.execute('SELECT coalesce(max(id), 0) FROM interactions').fetchone()[0]
            after_seq = con.execute('SELECT coalesce(max(seq), 0) FROM contexts').fetchone()[0]
            after_card = con.execute('SELECT coalesce(max(seq), 0) FROM cards').fetchone()[0]
            reply = request(f'{collector.rstrip("/")}/v1/export?after={after_id}&seq={after_seq}&card={after_card}'
                            f'&limit={EXPORT_PAGE}', timeout=timeout)
            if not isinstance(reply, dict) or not reply.get('ok'):
                return False, (reply or {}).get('error', 'collector did not answer')
            if reply.get('maxId', 0) < after_id:
                with con:
                    con.execute('DELETE FROM interactions')
                    con.execute('DELETE FROM contexts')
                    con.execute('DELETE FROM cards')
                continue
            with con:
                con.executemany(f'INSERT OR IGNORE INTO interactions ({", ".join(INTERACTION_COLUMNS)}) '
                                f'VALUES ({", ".join("?" * len(INTERACTION_COLUMNS))})',
                                [tuple(r.get(c) for c in INTERACTION_COLUMNS) for r in reply.get('interactions', [])])
                con.executemany(f'INSERT OR REPLACE INTO contexts ({", ".join(CONTEXT_COLUMNS)}) '
                                f'VALUES ({", ".join("?" * len(CONTEXT_COLUMNS))})',
                                [tuple(r.get(c) for c in CONTEXT_COLUMNS) for r in reply.get('contexts', [])])
                con.executemany(f'INSERT OR REPLACE INTO cards ({", ".join(CARD_COLUMNS)}) '
                                f'VALUES ({", ".join("?" * len(CARD_COLUMNS))})',
                                [tuple(r.get(c) for c in CARD_COLUMNS) for r in reply.get('cards', [])])
            if not reply.get('more'):
                return True, None
        return True, None
    finally:
        con.close()


def load(path=None):
    """Everything knowledge.py replays: interactions oldest first, and contexts keyed by (surface, view, context),
    each with its plan (None when not recorded), its time on screen when a reader could have been reading it
    ("active", the ms of its visible stays, 0 without any) and when its last stay began ("seen_until"). T3 messages
    also keep their stays one by one, oldest first ("stays": [{y, height, ms, words}], words None when not sent): each
    is one paragraph's band; and the message they are and the levels they were shown at ("message", "level": the word
    level, "sentences": the sentence level, both None for a client that sends no level; split_levels)."""
    con = connect(path)
    try:
        rows = [dict(zip(INTERACTION_COLUMNS, r)) for r in con.execute(
            f'SELECT {", ".join(INTERACTION_COLUMNS)} FROM interactions ORDER BY at, id')]
        contexts = {}
        for r in con.execute(f'SELECT {", ".join(CONTEXT_COLUMNS)} FROM contexts'):
            c = dict(zip(CONTEXT_COLUMNS, r))
            c['annotations'] = json.loads(c['annotations'])
            c['plan'] = json.loads(c['plan']) if c['plan'] else None
            c['active'], c['seen_until'] = 0, None
            if c['surface'] == 't3':
                c['stays'] = []
                c['message'], c['level'], c['sentences'] = split_levels(c['context'])
            contexts[(c['surface'], c['view'], c['context'])] = c
        for surface, view, context, ms, last in con.execute(
                "SELECT surface, view, context, coalesce(sum(ms), 0), max(at) FROM reading WHERE kind = 'visible' "
                'GROUP BY surface, view, context'):
            c = contexts.get((surface, view, context))
            if c is not None:
                c['active'], c['seen_until'] = ms, last
        for view, context, y, height, ms, detail in con.execute(
                "SELECT view, context, y, height, ms, detail FROM reading WHERE kind = 'visible' AND surface = 't3' "
                'ORDER BY at, id'):
            c = contexts.get(('t3', view, context))
            if c is not None:
                words = json.loads(detail).get('words')
                c['stays'].append({'y': y, 'height': height, 'ms': ms or 0,
                                   'words': words if isinstance(words, int) and not isinstance(words, bool) else None})
    finally:
        con.close()
    return rows, contexts


def intensity_changes(path=None):
    """Every change of the reader's intensity (intensity.py), oldest first: {at, machine, surface (chat: chat's word
    level, sentences: its sentence level, browser), level, until, source}, from the ops applied here; every chat turned off or back on (threads.py) as {at, machine, surface:
    "thread", session, off, source}; and every press of English only in chat as {at, machine, surface: "english", on,
    until, source, contexts: [{view, context}]}."""
    con = connect(path)
    try:
        ops = con.execute("SELECT at, machine, body FROM ops WHERE (kind = 'set' AND body LIKE '%intensity_%') "
                          "OR kind IN ('thread', 'english') ORDER BY at").fetchall()
    finally:
        con.close()
    out = []
    for at, machine, body in ops:
        try:
            op = json.loads(body)
        except ValueError:
            continue
        if op.get('kind') == 'english':
            out.append({'at': at, 'machine': machine, 'surface': 'english', 'on': op.get('on') is True,
                        'until': op.get('until') or 0, 'source': op.get('source') or '',
                        'contexts': [c for c in op.get('contexts') or [] if isinstance(c, dict)]})
            continue
        if op.get('kind') == 'thread':
            out.append({'at': at, 'machine': machine, 'surface': 'thread', 'session': op.get('session'),
                        'off': op.get('off') is True, 'source': op.get('source') or ''})
            continue
        prefs = op.get('prefs') or {}
        for surface in intensity.SURFACES:
            value = prefs.get(f'intensity_{surface}')
            if isinstance(value, dict) and value.get('level') in intensity.LEVELS:
                out.append({'at': at, 'machine': machine, 'surface': surface, 'level': value['level'],
                            'until': value.get('until') or 0, 'source': value.get('source') or ''})
    return out


def sentence_records(path=None):
    """The Japanese sentences of chat replies the hooks sent (sentences records, hub.py; grammar.record), oldest
    first: [{fp, day, v, s}], a reply once per record that named it."""
    con = connect(path)
    try:
        rows = con.execute("SELECT body FROM ops WHERE kind = 'sentences' ORDER BY at").fetchall()
    finally:
        con.close()
    out = []
    for (body,) in rows:
        try:
            replies = json.loads(body).get('replies')
        except (ValueError, AttributeError):
            continue
        out += [r for r in replies or [] if isinstance(r, dict)]
    return out


def chat_briefs(path=None, session=None):
    """What each chat reply was offered (brief ops, weave.compose), oldest first, one chat's or every chat's: {at, machine,
    session, level, intensity, budget, ask, sentences, expected (per 100 words; None at full), sentencePrice (None
    before sentences were priced), offered: [[word, p]], arms, topical (the topical words offered, [[word, p]]; none
    before there were any), known, whole, chars, turn (Codex; else None)}. The hook writes one as a prompt is sent, so
    a reply's is the last of its chat at or before the reply (brief_for)."""
    con = connect(path)
    try:
        query = "SELECT at, machine, body FROM ops WHERE kind = 'brief'" + (' AND body LIKE ?' if session else '')
        rows = con.execute(query + ' ORDER BY at', (f'%"session": "{session}"%',) if session else ()).fetchall()
    finally:
        con.close()
    out = []
    for at, machine, body in rows:
        try:
            op = json.loads(body)
        except ValueError:
            continue
        if session and op.get('session') != session:
            continue
        out.append({'at': at, 'machine': machine, 'session': op.get('session'), 'level': op.get('level'),
                    'intensity': op.get('intensity'), 'budget': op.get('budget'), 'ask': op.get('ask'),
                    'sentences': op.get('sentences'), 'expected': op.get('expected'),
                    'sentencePrice': op.get('sentencePrice'), 'offered': op.get('offered') or [],
                    'arms': op.get('arms') or {}, 'topical': op.get('topical') or [],
                    'known': op.get('known'), 'whole': op.get('whole'), 'chars': op.get('chars'), 'turn': op.get('turn')})
    return out


def brief_for(briefs, session, at):
    """The brief a chat reply was written under: the last of its chat (session id) sent at or before the reply's time
    (ms), from chat_briefs; None when there is none."""
    found = None
    for b in briefs:
        if b['session'] == session and b['at'] <= at and (found is None or b['at'] >= found['at']):
            found = b
    return found


def request(url, body=None, timeout=5):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    headers = dict({'Content-Type': 'application/json'} if data else {}, **{'Accept-Encoding': 'gzip'})
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
            if res.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            return json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as error:
        with error:
            try:
                return json.loads(error.read().decode('utf-8'))
            except ValueError:
                return {'ok': False, 'error': f'collector answered HTTP {error.code}'}
    except (OSError, ValueError, EOFError, http.client.HTTPException) as error:
        return {'ok': False, 'error': f'collector unreachable: {error}'}


def forward(collector, items, surface):
    """Sends items to a collector (collector.py); its reply, or {ok: false, error} so callers keep them queued."""
    reply = request(collector.rstrip('/') + '/v1/interactions', {'surface': surface, 'items': items})
    return reply if isinstance(reply, dict) else {'ok': False, 'error': 'collector reply is not an object'}


def remote_recent(collector, limit=20):
    reply = request(f'{collector.rstrip("/")}/v1/recent?limit={int(limit)}')
    if not isinstance(reply, dict) or not reply.get('ok'):
        raise OSError((reply or {}).get('error', 'collector did not answer'))
    return reply['items']


def remote_stats(collector, since=0):
    reply = request(f'{collector.rstrip("/")}/v1/stats?since={int(since)}', timeout=3)
    return reply if isinstance(reply, dict) else {'ok': False, 'error': 'collector reply is not an object'}


def remote_health(collector):
    return request(collector.rstrip('/') + '/v1/health', timeout=3)
