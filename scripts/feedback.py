"""Gloss interaction log (Python 3.9+, standard library only): every peek, pin or flip on a glossed word, with how long
the gloss stayed open and where the other glossed words of the same passage sat. A peek shows the reader was reading
that spot, so unpeeked words next to it are the evidence that they knew them. Clicks, text selections, mouse paths and
on-screen time in passages with glossed words are kept too, as more evidence of where the reader was and for how
long. Words, positions, times and counts only, never text.

Stored in <state dir>/interactions.sqlite (schema 5):
  contexts      one row per passage per page load (browser) or message render (T3): its box on the page, the
                glossed words in it as JSON [[word, x, y], ...] (positions relative to the passage), and for browser
                passages its word count, when its glossed words were first on screen ("shown") and how the amount
                was chosen ("plan", JSON {level: the reader's intensity -2..2, intensity: its factor, jitter: the
                passage's random factor (1: none), limit: swaps allowed, candidates: swaps the model offered}); seq
                orders changes for export
  interactions  one row per peek (gloss opened by hovering), pin (opened by a click or tap, T3) or flip (word turned
                back to English, browser): when (ms since the epoch), how long the gloss was open, which word, its
                index among the passage's glossed words, and a client id (uid) so a resend is stored once
  reading       one row per reading signal in a passage (browser) or message (T3) with glossed words, positions
                relative to it, and a client id (uid):
                  click    x, y; detail {"target": "text" | "link" (a link or control)}
                  select   x, y, width, height of the selection; ms: how long the drag took; detail {"chars": its
                           length, "covered": indexes of the glossed words in it (into the context's annotations)}
                  visible  one stay on screen of a paragraph: y, height (the paragraph's band; in the browser the whole
                           passage); ms: the time a reader could have been reading it; detail {"wall": all its time on
                           screen, and the rest by why it doesn't count: "away" (page hidden or window not focused),
                           "media" (sound or video playing), "idle" (no input for 30 s)}
                  trace    the mouse path over a message (T3): ms: its span; detail {"points": [[ms since at, x, y]]}

  ops           every change to the word state applied here, as sent (hub.py): uid, when, from which machine, kind
                and the op as JSON

Items are interactions, or {"kind": "shown", "words": N, ...} records that only describe a context (the denominator of
the peek rate). With a collector configured (setup --collector), the native host forwards items to it instead (see
collector.py), and knowledge.py runs there.
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

DB_NAME = 'interactions.sqlite'
MIRROR_NAME = 'mirror.sqlite'
SCHEMA_VERSION = 5
SURFACES = ('browser', 't3')
KINDS = ('peek', 'pin', 'flip')  # the gloss was opened, or the word turned back to English
READING = ('click', 'select', 'visible', 'trace')  # where the reader was; knowledge.py reads the visible stays
SHOWN = 'shown'
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
INDEXES = """
CREATE INDEX IF NOT EXISTS interactions_word ON interactions (word, at);
CREATE INDEX IF NOT EXISTS interactions_context ON interactions (surface, view, context);
CREATE UNIQUE INDEX IF NOT EXISTS interactions_uid ON interactions (uid);
CREATE INDEX IF NOT EXISTS contexts_seq ON contexts (seq);
CREATE INDEX IF NOT EXISTS reading_context ON reading (surface, view, context);
CREATE UNIQUE INDEX IF NOT EXISTS reading_uid ON reading (uid);
"""


def default_path():
    return fwlib.home() / DB_NAME


def connect(path=None):
    path = Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(TABLES)
    version = con.execute('PRAGMA user_version').fetchone()[0]
    if version == 3:  # its reading table needs x and y and has no ms column
        _migrate_reading(con)
    con.executescript(READING_TABLE)
    if version < SCHEMA_VERSION:
        _migrate(con)
    con.executescript(INDEXES)
    con.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    return con


def _migrate(con):
    """Schema 1 -> 2: word counts, shown times and a change sequence on contexts; client ids on interactions.
    Schemas 3 and 4 only added the reading table, which READING_TABLE creates. Schema 5: the plan of a shown passage."""
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
    clean = {'kind': item['kind'], 'view': item['view'], 'context': item['context'], 'at': int(at), 'box': box,
             'annotations': [[a[0], round(a[1]), round(a[2])] for a in notes]}
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
    integer = lambda x, top: isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= top
    return (isinstance(plan, dict) and set(plan) <= {'level', 'intensity', 'jitter', 'limit', 'candidates'}
            and plan.get('level') in (-2, -1, 0, 1, 2) and num(plan.get('intensity')) and 0 < plan['intensity'] <= 5
            and num(plan.get('jitter')) and 0 < plan['jitter'] <= 1 and integer(plan.get('limit'), 1000)
            and integer(plan.get('candidates'), 1000))


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
        ms, wall, away, media, idle = (round(t) for t in times)
        return dict(row, y=round(item['y']), height=round(item['height']), ms=ms,
                    detail={'wall': wall, 'away': away, 'media': media, 'idle': idle})
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


def record(items, surface, path=None, now=None):
    """Stores well-formed items. Returns {stored, duplicates, rejected}: a resent interaction (same uid) is stored
    once, and malformed or too old items are rejected (never worth resending)."""
    if surface not in SURFACES or not isinstance(items, list):
        return {'stored': 0, 'duplicates': 0, 'rejected': len(items) if isinstance(items, list) else 0}
    now = now if now is not None else int(time.time() * 1000)
    batch = items[:MAX_ITEMS]
    cleaned = [c for c in (_clean(item, now) for item in batch) if c]
    stored = duplicates = 0
    if cleaned:
        con = connect(path)
        try:
            with con:
                for c in cleaned:
                    shown = c['kind'] == SHOWN
                    plan = c.get('plan') if shown else None
                    con.execute(CONTEXT_UPSERT, (surface, c['view'], c['context'], *c['box'],
                                                 json.dumps(c['annotations'], ensure_ascii=False),
                                                 c['words'] if shown else None, c['at'] if shown else None,
                                                 json.dumps(plan) if plan else None))
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
    return {'stored': stored, 'duplicates': duplicates, 'rejected': len(batch) - len(cleaned)}


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
    (ms)] for gloss opens and reading signals; the same for browser words shown and for on-screen reading time (the
    ms of visible rows); and the newest item's time."""
    con = connect(path)
    try:
        counts, last = {}, 0
        for table in ('interactions', 'reading'):
            for surface, kind, total, recent_, newest in con.execute(
                    f'SELECT surface, kind, count(*), coalesce(sum(at >= ?), 0), max(at) FROM {table} '
                    'GROUP BY surface, kind', (since,)):
                counts[f'{surface}:{kind}'] = [total, recent_]
                last = max(last, newest)
        shown = con.execute('SELECT coalesce(sum(words), 0), coalesce(sum(CASE WHEN shown_at >= ? THEN words END), 0) '
                            'FROM contexts WHERE shown_at IS NOT NULL', (since,)).fetchone()
        onscreen = con.execute("SELECT coalesce(sum(ms), 0), coalesce(sum(CASE WHEN at >= ? THEN ms END), 0) "
                               "FROM reading WHERE kind = 'visible'", (since,)).fetchone()
    finally:
        con.close()
    return {'counts': counts, 'shownWords': list(shown), 'readingMs': list(onscreen), 'last': last or None,
            'since': since}


def count(path=None):
    con = connect(path)
    try:
        return con.execute('SELECT count(*) FROM interactions').fetchone()[0]
    finally:
        con.close()


INTERACTION_COLUMNS = ('id', 'at', 'surface', 'kind', 'word', 'dwell_ms', 'view', 'context', 'position', 'uid')
CONTEXT_COLUMNS = ('surface', 'view', 'context', 'left', 'top', 'width', 'height', 'annotations', 'words', 'shown_at',
                   'seq', 'plan')


def export(after_id=0, after_seq=0, limit=EXPORT_PAGE, path=None):
    """Interactions with id > after_id and contexts changed after seq after_seq, oldest first, for a mirror."""
    con = connect(path)
    try:
        rows = con.execute(f'SELECT {", ".join(INTERACTION_COLUMNS)} FROM interactions WHERE id > ? ORDER BY id LIMIT ?',
                           (after_id, limit)).fetchall()
        contexts = con.execute(f'SELECT {", ".join(CONTEXT_COLUMNS)} FROM contexts WHERE seq > ? ORDER BY seq LIMIT ?',
                               (after_seq, limit)).fetchall()
        max_id = con.execute('SELECT coalesce(max(id), 0) FROM interactions').fetchone()[0]
    finally:
        con.close()
    return {'interactions': [dict(zip(INTERACTION_COLUMNS, r)) for r in rows],
            'contexts': [dict(zip(CONTEXT_COLUMNS, r)) for r in contexts],
            'more': len(rows) == limit or len(contexts) == limit, 'maxId': max_id}


def sync_mirror(collector, path, timeout=4, pages=20):
    """Brings the local mirror of a collector's database up to date. Returns (ok, error). A collector whose
    database is behind the mirror (it was reset) makes the mirror start over."""
    con = connect(path)
    try:
        for _ in range(pages):
            after_id = con.execute('SELECT coalesce(max(id), 0) FROM interactions').fetchone()[0]
            after_seq = con.execute('SELECT coalesce(max(seq), 0) FROM contexts').fetchone()[0]
            reply = request(f'{collector.rstrip("/")}/v1/export?after={after_id}&seq={after_seq}&limit={EXPORT_PAGE}',
                             timeout=timeout)
            if not isinstance(reply, dict) or not reply.get('ok'):
                return False, (reply or {}).get('error', 'collector did not answer')
            if reply.get('maxId', 0) < after_id:
                with con:
                    con.execute('DELETE FROM interactions')
                    con.execute('DELETE FROM contexts')
                continue
            with con:
                con.executemany(f'INSERT OR IGNORE INTO interactions ({", ".join(INTERACTION_COLUMNS)}) '
                                f'VALUES ({", ".join("?" * len(INTERACTION_COLUMNS))})',
                                [tuple(r.get(c) for c in INTERACTION_COLUMNS) for r in reply.get('interactions', [])])
                con.executemany(f'INSERT OR REPLACE INTO contexts ({", ".join(CONTEXT_COLUMNS)}) '
                                f'VALUES ({", ".join("?" * len(CONTEXT_COLUMNS))})',
                                [tuple(r.get(c) for c in CONTEXT_COLUMNS) for r in reply.get('contexts', [])])
            if not reply.get('more'):
                return True, None
        return True, None
    finally:
        con.close()


def load(path=None):
    """Everything knowledge.py replays: interactions oldest first, and contexts keyed by (surface, view, context),
    each with its plan (None when not recorded), its time on screen when a reader could have been reading it
    ("active", the ms of its visible stays, 0 without any) and when its last stay began ("seen_until")."""
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
            contexts[(c['surface'], c['view'], c['context'])] = c
        for surface, view, context, ms, last in con.execute(
                "SELECT surface, view, context, coalesce(sum(ms), 0), max(at) FROM reading WHERE kind = 'visible' "
                'GROUP BY surface, view, context'):
            c = contexts.get((surface, view, context))
            if c is not None:
                c['active'], c['seen_until'] = ms, last
    finally:
        con.close()
    return rows, contexts


def intensity_changes(path=None):
    """Every change of the reader's intensity (intensity.py), oldest first: {at, machine, surface, level, until,
    source}, from the ops applied here; and every chat turned off or back on (threads.py) as {at, machine, surface:
    "thread", session, off, source}."""
    con = connect(path)
    try:
        ops = con.execute("SELECT at, machine, body FROM ops WHERE (kind = 'set' AND body LIKE '%intensity_%') "
                          "OR kind = 'thread' ORDER BY at").fetchall()
    finally:
        con.close()
    out = []
    for at, machine, body in ops:
        try:
            op = json.loads(body)
        except ValueError:
            continue
        if op.get('kind') == 'thread':
            out.append({'at': at, 'machine': machine, 'surface': 'thread', 'session': op.get('session'),
                        'off': op.get('off') is True, 'source': op.get('source') or ''})
            continue
        prefs = op.get('prefs') or {}
        for surface in ('chat', 'browser'):
            value = prefs.get(f'intensity_{surface}')
            if isinstance(value, dict) and value.get('level') in (-2, -1, 0, 1, 2):
                out.append({'at': at, 'machine': machine, 'surface': surface, 'level': value['level'],
                            'until': value.get('until') or 0, 'source': value.get('source') or ''})
    return out


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
