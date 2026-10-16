"""Gloss interaction log (Python 3.9+, standard library only): every peek, pin or flip on a glossed word, with how long
the gloss stayed open and where the other glossed words of the same passage sat. A peek shows the reader was reading
that spot, so unpeeked words next to it are the evidence that they knew them. Words and positions only, never text.

Stored in <state dir>/interactions.sqlite:
  contexts      one row per passage per page load (browser) or message render (T3): its box on the page and the
                glossed words in it as JSON [[word, x, y], ...], positions relative to the passage
  interactions  one row per peek (gloss opened by hovering), pin (opened by a click or tap, T3) or flip (word turned
                back to English, browser): when (ms since the epoch), how long the gloss was open, which word, and
                its index among the passage's glossed words

With a collector configured (setup --collector), the native host forwards here-bound items to it instead
(see collector.py), so every machine's interactions land in one database.
"""
import json
import math
import urllib.error
import urllib.request
from pathlib import Path
import sqlite3
import time

import fwlib

DB_NAME = 'interactions.sqlite'
SCHEMA_VERSION = 1
SURFACES = ('browser', 't3')
KINDS = ('peek', 'pin', 'flip')
MAX_ITEMS = 500
MAX_ANNOTATIONS = 200
DAY_MS = 86_400_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS contexts (
  surface TEXT NOT NULL,
  view TEXT NOT NULL,
  context TEXT NOT NULL,
  left REAL, top REAL, width REAL, height REAL,
  annotations TEXT NOT NULL,
  PRIMARY KEY (surface, view, context)
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
  position INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS interactions_word ON interactions (word, at);
CREATE INDEX IF NOT EXISTS interactions_context ON interactions (surface, view, context);
"""


def default_path():
    return fwlib.home() / DB_NAME


def connect(path=None):
    path = Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(SCHEMA)
    con.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    return con


def _clean(item, now):
    """The item as stored, or None when anything is malformed (the host checks again what the extension checked)."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    word = lambda x: isinstance(x, str) and 0 < len(x) <= 40
    ident = lambda x: isinstance(x, str) and 0 < len(x) <= 64 and all(c.isalnum() or c in '_:-' for c in x)
    if not isinstance(item, dict) or item.get('kind') not in KINDS or not word(item.get('word')):
        return None
    if not ident(item.get('view')) or not ident(item.get('context')) or not isinstance(item.get('position'), int):
        return None
    at, dwell, box, notes = item.get('at'), item.get('dwellMs'), item.get('box'), item.get('annotations')
    if not num(at) or not now - DAY_MS <= at <= now + 60_000 or not num(dwell) or not 0 <= dwell <= 600_000:
        return None
    if not isinstance(box, list) or len(box) != 4 or not all(num(v) for v in box):
        return None
    if not isinstance(notes, list) or len(notes) > MAX_ANNOTATIONS:
        return None
    if not all(isinstance(a, list) and len(a) == 3 and word(a[0]) and num(a[1]) and num(a[2]) for a in notes):
        return None
    return dict(item, at=int(at), dwellMs=int(dwell), annotations=[[a[0], round(a[1]), round(a[2])] for a in notes])


def record(items, surface, path=None, now=None):
    """Stores well-formed interactions; returns how many were stored."""
    if surface not in SURFACES or not isinstance(items, list):
        return 0
    now = now if now is not None else int(time.time() * 1000)
    cleaned = [c for c in (_clean(item, now) for item in items[:MAX_ITEMS]) if c]
    if not cleaned:
        return 0
    con = connect(path)
    try:
        with con:
            for c in cleaned:
                con.execute('INSERT OR REPLACE INTO contexts VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                            (surface, c['view'], c['context'], *c['box'],
                             json.dumps(c['annotations'], ensure_ascii=False)))
                con.execute('INSERT INTO interactions (at, surface, kind, word, dwell_ms, view, context, position) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                            (c['at'], surface, c['kind'], c['word'], c['dwellMs'], c['view'], c['context'],
                             c['position']))
    finally:
        con.close()
    return len(cleaned)


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


def _request(url, body=None, timeout=5):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        with error:
            try:
                return json.loads(error.read().decode('utf-8'))
            except ValueError:
                return {'ok': False, 'error': f'collector answered HTTP {error.code}'}
    except (OSError, ValueError) as error:
        return {'ok': False, 'error': f'collector unreachable: {error}'}


def forward(collector, items, surface):
    """Sends items to a collector (collector.py); its reply, or {ok: false, error} so callers keep them queued."""
    reply = _request(collector.rstrip('/') + '/v1/interactions', {'surface': surface, 'items': items})
    return reply if isinstance(reply, dict) else {'ok': False, 'error': 'collector reply is not an object'}


def remote_recent(collector, limit=20):
    reply = _request(f'{collector.rstrip("/")}/v1/recent?limit={int(limit)}')
    if not isinstance(reply, dict) or not reply.get('ok'):
        raise OSError((reply or {}).get('error', 'collector did not answer'))
    return reply['items']


def remote_health(collector):
    return _request(collector.rstrip('/') + '/v1/health', timeout=3)
