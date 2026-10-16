"""Gloss interaction log (Python 3.9+, standard library only): every peek, pin or flip on a glossed word, with how long
the gloss stayed open and where the other glossed words of the same passage sat. A peek shows the reader was reading
that spot, so unpeeked words next to it are the evidence that they knew them. Words and positions only, never text.

Stored in <state dir>/interactions.sqlite (schema 2):
  contexts      one row per passage per page load (browser) or message render (T3): its box on the page, the
                glossed words in it as JSON [[word, x, y], ...] (positions relative to the passage), and for browser
                passages its word count and when its glossed words were first on screen ("shown"); seq orders changes
                for export
  interactions  one row per peek (gloss opened by hovering), pin (opened by a click or tap, T3) or flip (word turned
                back to English, browser): when (ms since the epoch), how long the gloss was open, which word, its
                index among the passage's glossed words, and a client id (uid) so a resend is stored once

  ops           every change to the word state applied here, as sent (hub.py): uid, when, from which machine, kind
                and the op as JSON

Items are interactions, or {"kind": "shown", "words": N, ...} records that only describe a context (the denominator of
the peek rate). With a collector configured (setup --collector), the native host forwards items to it instead (see
collector.py), and knowledge.py reads a local mirror of the collector's database (sync_mirror).
"""
import gzip
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
SCHEMA_VERSION = 2
SURFACES = ('browser', 't3')
KINDS = ('peek', 'pin', 'flip')
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
INDEXES = """
CREATE INDEX IF NOT EXISTS interactions_word ON interactions (word, at);
CREATE INDEX IF NOT EXISTS interactions_context ON interactions (surface, view, context);
CREATE UNIQUE INDEX IF NOT EXISTS interactions_uid ON interactions (uid);
CREATE INDEX IF NOT EXISTS contexts_seq ON contexts (seq);
"""


def default_path():
    return fwlib.home() / DB_NAME


def connect(path=None):
    path = Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(TABLES)
    if con.execute('PRAGMA user_version').fetchone()[0] < SCHEMA_VERSION:
        _migrate(con)
    con.executescript(INDEXES)
    con.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    return con


def _migrate(con):
    """Schema 1 -> 2: word counts, shown times and a change sequence on contexts; client ids on interactions."""
    columns = lambda table: {row[1] for row in con.execute(f'PRAGMA table_info({table})')}
    with con:
        have = columns('contexts')
        for name, kind in (('words', 'INTEGER'), ('shown_at', 'INTEGER'), ('seq', 'INTEGER NOT NULL DEFAULT 0')):
            if name not in have:
                con.execute(f'ALTER TABLE contexts ADD COLUMN {name} {kind}')
        con.execute('UPDATE contexts SET seq = rowid WHERE seq = 0')
        if 'uid' not in columns('interactions'):
            con.execute('ALTER TABLE interactions ADD COLUMN uid TEXT')


def _clean(item, now):
    """The item as stored, or None when anything is malformed (the host checks again what the extension checked)."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    word = lambda x: isinstance(x, str) and 0 < len(x) <= 40
    ident = lambda x: isinstance(x, str) and 0 < len(x) <= 64 and all(c.isalnum() or c in '_:-' for c in x)
    if not isinstance(item, dict) or item.get('kind') not in KINDS + (SHOWN,):
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
        return dict(clean, words=words)
    dwell, uid = item.get('dwellMs'), item.get('uid')
    if not word(item.get('word')) or not isinstance(item.get('position'), int) or isinstance(item.get('position'), bool):
        return None
    if not num(dwell) or not 0 <= dwell <= 600_000 or (uid is not None and not ident(uid)):
        return None
    return dict(clean, word=item['word'], position=item['position'], dwellMs=int(dwell), uid=uid)


CONTEXT_UPSERT = (
    'INSERT INTO contexts (surface, view, context, left, top, width, height, annotations, words, shown_at, seq) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, (SELECT coalesce(max(seq), 0) + 1 FROM contexts)) '
    'ON CONFLICT (surface, view, context) DO UPDATE SET left = excluded.left, top = excluded.top, '
    'width = excluded.width, height = excluded.height, annotations = excluded.annotations, '
    'words = coalesce(excluded.words, contexts.words), shown_at = coalesce(contexts.shown_at, excluded.shown_at), '
    'seq = excluded.seq')


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
                    con.execute(CONTEXT_UPSERT, (surface, c['view'], c['context'], *c['box'],
                                                 json.dumps(c['annotations'], ensure_ascii=False),
                                                 c['words'] if shown else None, c['at'] if shown else None))
                    if shown:
                        stored += 1
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
    """The latest interactions, newest first, each with the glossed words of its passage."""
    con = connect(path)
    try:
        rows = con.execute(
            'SELECT i.at, i.surface, i.kind, i.word, i.dwell_ms, i.position, c.annotations FROM interactions i '
            'JOIN contexts c USING (surface, view, context) ORDER BY i.at DESC, i.id DESC LIMIT ?', (limit,)).fetchall()
    finally:
        con.close()
    return [{'at': at, 'surface': surface, 'kind': kind, 'word': word, 'dwellMs': dwell, 'position': position,
             'annotations': json.loads(notes)} for at, surface, kind, word, dwell, position, notes in rows]


def count(path=None):
    con = connect(path)
    try:
        return con.execute('SELECT count(*) FROM interactions').fetchone()[0]
    finally:
        con.close()


INTERACTION_COLUMNS = ('id', 'at', 'surface', 'kind', 'word', 'dwell_ms', 'view', 'context', 'position', 'uid')
CONTEXT_COLUMNS = ('surface', 'view', 'context', 'left', 'top', 'width', 'height', 'annotations', 'words', 'shown_at',
                   'seq')


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
    """Everything knowledge.py replays: interactions oldest first, and contexts keyed by (surface, view, context)."""
    con = connect(path)
    try:
        rows = [dict(zip(INTERACTION_COLUMNS, r)) for r in con.execute(
            f'SELECT {", ".join(INTERACTION_COLUMNS)} FROM interactions ORDER BY at, id')]
        contexts = {}
        for r in con.execute(f'SELECT {", ".join(CONTEXT_COLUMNS)} FROM contexts'):
            c = dict(zip(CONTEXT_COLUMNS, r))
            c['annotations'] = json.loads(c['annotations'])
            contexts[(c['surface'], c['view'], c['context'])] = c
    finally:
        con.close()
    return rows, contexts


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
    except (OSError, ValueError, EOFError) as error:
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


def remote_health(collector):
    return request(collector.rstrip('/') + '/v1/health', timeout=3)
