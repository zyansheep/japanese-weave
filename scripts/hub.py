"""Changes to the word state, and the collector as its one home (Python 3.9+, standard library only).

Every change to the word state is an op, applied by apply() where the state lives. Without a collector that is this
machine (run). With one (`fw.py setup --collector URL`), the state lives on the collector's machine: ops go there
(POST /v1/ops), the collector applies and records them, and this machine keeps a cache of state.json, knowledge.json
and exposure.json (GET /v1/state). An op the collector can't take right now waits in outbox.jsonl and is applied to
the cache meanwhile, so the change shows at once; the next sync sends it. The hook never waits on the network: it reads
the cache and starts a background sync (`fw.py refresh`) when the cache is a few minutes old.

Ops (each also carries uid, day, at and machine; see stamp):
  events    {"events": [{"word", "kind": "seen"|"peek"|"flip", "day"?}]}   from the browser (fwlib.apply_event)
  mark      {"words": [...], "as": "known"|"asked"}    the known and learn commands
  set       {"stage"?: 1-4 | "auto", "paused"?: bool, "prefs"?: {"chat_glosses"?, "challenge"?, "extension_sites"?}}
  seed      {"n": N}                                   the top N lexicon words known, unless already introduced
  anki      {"words": [{ja, en, hint, kana, interval, mature}], "known": [ja]}   Anki review words first
  exposure  {"replies": [{"d": digest, "day", "words": [ja]}]}   chat replies scanned on some machine
  chat_day  {}                                         log-chat-day: today counts as assumed exposure
  exported  {"target": "claude-ai"|"chatgpt", "log_day": bool}
  noticed   {}                                         the stale-export notice was shown
The collector records every op it applies (the ops table of its database), so the log of gloss interactions and the
log of everything that changed the word state sit side by side.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import sys
import time
from urllib.parse import quote
import uuid

import anki
import export
import feedback
import fwlib
import knowledge

OUTBOX = 'outbox.jsonl'
SYNCED = 'hub.json'        # {pulled: unix seconds of the last good pull, rev, collector}
OPS_LOCK = 'ops.lock'      # one op at a time where the state lives
OUTBOX_LOCK = 'outbox.lock'
SYNC_LOCK = 'sync.lock'
TIMEOUT = 4                # seconds per request to the collector
SLOW = {'anki': 60}        # ops that take longer to apply (an Anki sync carries every review word)
FLUSH_TIMEOUT = 15         # a background flush can wait longer for a batch
BATCH = 20                 # queued ops per request
REPLIES_PER_OP = 500
MAX_OPS = 100              # ops per request the collector takes
CACHED = ('state.json', 'knowledge.json', 'exposure.json')
PROVISIONAL = ('events', 'mark', 'set', 'seed', 'chat_day', 'exported', 'noticed')  # applied to the cache while queued
NO_STATE = 'no word state on the collector yet: set it up there first (fw.py setup on that machine)'
MOVED = ('state.json', 'exposure.json', 'commands.jsonl', 'anki.csv', 'knowledge.json')  # a machine's own state
BEFORE = 'before-collector'  # where a machine's own state is kept when it first takes the collector's
DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def is_day(value):
    """A real calendar day as YYYY-MM-DD (2025-02-30 is not)."""
    if not isinstance(value, str) or not DAY.match(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True
PREFS = {'chat_glosses': lambda v: v in ('abbr', 'footer'), 'challenge': knowledge.valid_challenge,
         'extension_sites': lambda v: v in ('all', 'ask')}


# ---- ops ----

def stamp(op):
    """The op with its id, day, time and machine (kept when already set, so a queued op keeps them)."""
    return dict({'uid': uuid.uuid4().hex, 'day': fwlib.today(), 'at': int(time.time() * 1000),
                 'machine': socket.gethostname()[:64]}, **op)


def validate(op):
    """None when the op is well formed, else what is wrong (ops arrive over the network)."""
    text = lambda x, n=100: isinstance(x, str) and 0 < len(x) <= n
    texts = lambda xs, most, n=100: isinstance(xs, list) and len(xs) <= most and all(text(x, n) for x in xs)
    day = is_day
    integer = lambda x, low, high: isinstance(x, int) and not isinstance(x, bool) and low <= x <= high
    if not isinstance(op, dict):
        return 'an op is a JSON object'
    if not text(op.get('uid'), 64) or not day(op.get('day')):
        return 'an op needs a uid and a day (YYYY-MM-DD)'
    if not integer(op.get('at'), 0, 10 ** 14) or not isinstance(op.get('machine', ''), str):
        return 'an op needs at (ms since the epoch)'
    kind = op.get('kind')
    if kind == 'events':
        events = op.get('events')
        if not isinstance(events, list) or len(events) > 500 or not all(
                isinstance(e, dict) and text(e.get('word')) and e.get('kind') in ('seen', 'peek', 'flip')
                and (e.get('day') is None or day(e['day'])) for e in events):
            return 'events: up to 500 of {"word", "kind": "seen"|"peek"|"flip", "day"?}'
    elif kind == 'mark':
        if not texts(op.get('words'), 200, 200) or op.get('as') not in ('known', 'asked'):
            return 'mark: {"words": [...], "as": "known"|"asked"}'
    elif kind == 'set':
        if not set(op) - {'uid', 'day', 'at', 'machine', 'kind'} <= {'stage', 'paused', 'prefs'}:
            return 'set: only stage, paused and prefs'
        if 'stage' in op and op['stage'] not in (1, 2, 3, 4, 'auto'):
            return 'set: stage is 1-4 or "auto"'
        if 'paused' in op and not isinstance(op['paused'], bool):
            return 'set: paused is true or false'
        prefs = op.get('prefs', {})
        if not isinstance(prefs, dict) or not all(k in PREFS and PREFS[k](v) for k, v in prefs.items()):
            return f'set: prefs are {", ".join(PREFS)} with valid values'
    elif kind == 'seed':
        if not integer(op.get('n'), 0, 100_000):
            return 'seed: {"n": N}'
    elif kind == 'anki':
        words, known = op.get('words'), op.get('known')
        if not isinstance(words, list) or len(words) > 50_000 or not all(
                isinstance(w, dict) and text(w.get('ja')) and text(w.get('en'), 500) for w in words):
            return 'anki: words are {ja, en, hint, kana, interval, mature}'
        if not texts(known, 50_000):
            return 'anki: known is a list of words'
    elif kind == 'exposure':
        replies = op.get('replies')
        if not isinstance(replies, list) or len(replies) > 5000 or not all(
                isinstance(r, dict) and text(r.get('d'), 64) and day(r.get('day')) and texts(r.get('words'), 2000)
                for r in replies):
            return 'exposure: replies are {"d", "day", "words": [...]}'
    elif kind == 'exported':
        if op.get('target') not in ('claude-ai', 'chatgpt') or not isinstance(op.get('log_day', False), bool):
            return 'exported: {"target": "claude-ai"|"chatgpt", "log_day"?: bool}'
    elif kind not in ('chat_day', 'noticed'):
        return f'unknown op {kind!r}'
    return None


def apply(state, op):
    """Applies a valid op to a state dict; returns what it did. No files: run() adds those (it is also how a queued
    op shows in the cache right away)."""
    kind, today = op['kind'], fwlib.today()
    day = min(op['day'], today)
    if kind == 'events':
        return {'applied': sum(fwlib.apply_event(state, e['word'], e['kind'], min(e.get('day') or day, today))
                               for e in op['events'])}
    if kind == 'mark':
        done, missing, canonical = [], [], []
        for chunk in (c.strip() for arg in op['words'] for c in arg.split(',')):
            # "jitsu wa" (romaji for 実は) is one word; "ima demo" is two.
            for word in [chunk] if fwlib.find(state, chunk) or ' ' not in chunk else chunk.split():
                if word and fwlib.apply_event(state, word, op['as'], day):
                    done.append(word)
                    canonical.append(fwlib.find(state, word)['ja'])
                elif word:
                    missing.append(word)
        return {'done': done, 'missing': missing, 'canonical': canonical}
    if kind == 'set':
        if op.get('stage') == 'auto':
            state['stage_pinned'] = False
        elif 'stage' in op:
            state['stage'], state['stage_pinned'] = op['stage'], True
        if 'paused' in op:
            state['paused'] = op['paused']
        for name, value in (op.get('prefs') or {}).items():
            state.setdefault('prefs', {})[name] = value
        return {}
    if kind == 'seed':
        return {'seeded': fwlib.seed_known(state, op['n'], day)}
    if kind == 'anki':  # the words reach the front of the list through anki.csv (run writes it first)
        by_key = {fwlib.key(w['ja']): w for w in state['words']}  # thousands of words: one lookup each
        entries = [e for e in (by_key.get(fwlib.key(name)) or fwlib.find(state, name) for name in op['known']) if e]
        for e in entries:  # learning words keep learning; only words not introduced yet jump to known
            if e['status'] == 'queue':
                fwlib.set_status(e, 'known', day)
        return {'words': len(op['words']), 'known': sum(e['status'] == 'known' for e in entries)}
    if kind == 'chat_day':
        return {'logged': export.log_chat_day(state, day)}
    if kind == 'exported':
        logged = export.log_chat_day(state, day) if op.get('log_day') else 0
        export.mark_exported(state, op['target'])
        return {'logged': logged}
    if kind == 'noticed':
        state.setdefault('exported', {})['noticed'] = True
        return {}
    return {}  # exposure: run folds it into exposure.json


def run(op, db=None):
    """Applies an op where the state lives (this machine) and records it; returns its result with ok. An op already
    recorded (same uid: a resend after a lost reply) is not applied again."""
    error = validate(op)
    if error:
        return {'ok': False, 'error': error}
    with fwlib.locked(OPS_LOCK):
        con = feedback.connect(db)
        try:
            if con.execute('SELECT 1 FROM ops WHERE uid = ?', (op['uid'],)).fetchone():
                return {'ok': True, 'duplicate': True}
            result = _apply_here(op)
            with con:
                con.execute('INSERT INTO ops (uid, at, machine, kind, body) VALUES (?, ?, ?, ?, ?)',
                            (op['uid'], int(op.get('at') or time.time() * 1000), str(op.get('machine') or ''),
                             op['kind'], json.dumps(op, ensure_ascii=False)))
        finally:
            con.close()
    return dict(result, ok=True)


def _apply_here(op):
    home, kind = fwlib.home(), op['kind']
    if kind == 'anki':  # replaced whole, so a reader never sees half of it
        with tempfile.NamedTemporaryFile(dir=home, suffix='.csv', delete=False) as handle:
            temporary = handle.name
        anki.write_csv(temporary, op['words'])
        os.replace(temporary, home / fwlib.ANKI_LEXICON)
    with fwlib.locked():
        if not (home / 'state.json').exists():  # first use: known words before the first learning words are picked
            fwlib.save_state(fwlib.new_state(known_top=op['n'] if kind == 'seed' else 0,
                                             known=op['known'] if kind == 'anki' else ()))
    counted = None
    if kind == 'exposure':
        path = home / 'exposure.json'
        index = fwlib.read_json(path, {}) or {}
        index.pop('offsets', None)  # each machine keeps its own, in scan.json
        before = set(index.get('recent') or {})
        replies = [r for r in op['replies'] if r['day'] <= fwlib.today()]  # a clock ahead can't count future days
        fwlib.fold(index, replies, [w['ja'] for w in fwlib.load_state()['words']])
        fwlib.write_json(path, index)
        counted = len({r['d'] for r in replies} - before)
    with fwlib.editing() as state:  # merges anki.csv into the list, applies, promotes, saves
        result = apply(state, op)
    if kind == 'mark' and result['canonical']:  # timestamped, so the knowledge replay applies them at the right moment
        knowledge.log_command(result['canonical'], 'known' if op['as'] == 'known' else 'learn', now=op['at'] / 1000)
    if counted is not None:
        result['counted'] = counted
    return result


# ---- this machine: apply, or send and cache ----

def submit(op, wait=True):
    """Makes a change: here when the state lives here; else on the collector, then refreshes the cache. When the
    collector can't be reached (or wait is False: the hook never waits), the op waits in the outbox, shows in the
    cache now, and a background sync sends it. Returns the op's result; queued ones carry "queued": true."""
    op = stamp(op)
    error = validate(op)
    if error:
        raise ValueError(error)
    url = fwlib.collector_url()
    if not url:
        return run(op)
    if wait and flush(url)[0]:
        reply = post(url, [op], SLOW.get(op['kind'], TIMEOUT))
        if reply is not None:
            pull(url, force=True)
            return reply['results'][0]
    queue(op)
    result = provisional(op)
    if not wait:
        knowledge.start_refresh()
    return result


def post(url, ops, timeout=TIMEOUT):
    """The collector's reply to a batch of ops, or None when it didn't take them (unreachable, no state yet)."""
    reply = feedback.request(f'{url.rstrip("/")}/v1/ops', {'machine': socket.gethostname()[:64], 'ops': ops},
                             timeout=timeout)
    ok = isinstance(reply, dict) and reply.get('ok') and isinstance(reply.get('results'), list)
    return reply if ok and len(reply['results']) == len(ops) else None


def queue(op):
    with fwlib.locked(OUTBOX_LOCK):
        with open(fwlib.home() / OUTBOX, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(op, ensure_ascii=False) + '\n')


def pending():
    """Queued ops, oldest first."""
    path = fwlib.home() / OUTBOX
    if not path.exists():
        return []
    ops = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            op = json.loads(line)
        except ValueError:
            continue
        if isinstance(op, dict):
            ops.append(op)
    return ops


def flush(url=None, timeout=TIMEOUT):
    """Sends queued ops, in order. Returns (none left, how many were sent). The outbox is only locked to read and
    to drop what was sent, so queueing never waits on the network (a resend is applied once: same uid)."""
    url = url or fwlib.collector_url()
    with fwlib.locked(OUTBOX_LOCK):
        ops = pending()
    sent = set()
    for start in range(0, len(ops), BATCH):
        batch = ops[start:start + BATCH]
        reply = post(url, batch, max([timeout] + [SLOW.get(op.get('kind'), 0) for op in batch]))
        if reply is None:
            break
        for op, result in zip(batch, reply['results']):
            if not result.get('ok'):  # malformed, or it failed there: resending won't help
                print(f'Japanese Weave: the collector refused a queued {op.get("kind")} change: '
                      f'{result.get("error")}', file=sys.stderr)
            sent.add(op.get('uid'))
    if not sent:
        return not ops, 0
    with fwlib.locked(OUTBOX_LOCK):
        left = [op for op in pending() if op.get('uid') not in sent]
        path = fwlib.home() / OUTBOX
        if left:
            fwlib.write_json_lines(path, left)
        else:
            path.unlink(missing_ok=True)
    return not left, len(sent)


def provisional(op):
    """A queued op applied to the cache, so it shows before the collector has it; the next pull replaces the cache."""
    if op['kind'] not in PROVISIONAL:
        return {'ok': True, 'queued': True}
    with fwlib.locked():
        state = fwlib.read_json(fwlib.home() / 'state.json')
        if not isinstance(state, dict) or not isinstance(state.get('words'), list):
            return {'ok': True, 'queued': True}
        result = apply(state, op)
        fwlib.save_state(state)
    return dict(result, ok=True, queued=True)


def pull(url=None, timeout=TIMEOUT, force=False):
    """Refreshes the cache from the collector. Returns (ok, error); on failure the cache stays as it was."""
    url = url or fwlib.collector_url()
    home = fwlib.home()
    synced = fwlib.read_json(home / SYNCED, {}) or {}
    # The cache changed since the last pull with nothing queued (something else wrote it): fetch it whole.
    edited = synced.get('sha') != fingerprint(home / 'state.json') and not pending()
    rev = '' if force or edited else synced.get('rev', '')
    reply = feedback.request(f'{url.rstrip("/")}/v1/state?rev={quote(str(rev))}', timeout=timeout)
    if not isinstance(reply, dict) or not reply.get('ok'):
        return False, (reply.get('error') if isinstance(reply, dict) else None) or 'collector did not answer'
    changed = not reply.get('unchanged')
    state = reply.get('state')
    if changed and (not isinstance(state, dict) or not isinstance(state.get('words'), list)):
        return False, 'the collector sent no word state'
    with fwlib.locked():
        if changed:
            if not (home / SYNCED).exists():  # the first time: this machine's own state, if any, is kept
                keep_own_state(home)
            if not (home / fwlib.SCAN).exists():  # older installs kept the scan offsets in exposure.json
                fwlib.write_json(home / fwlib.SCAN, fwlib.scan_cursor())
            for op in pending():  # still queued here: keep showing them
                if op.get('kind') in PROVISIONAL and not validate(op):
                    try:
                        apply(state, op)
                    except Exception:  # never let one queued change stop the refresh
                        pass
            fwlib.save_state(state)
            fwlib.write_json(home / 'knowledge.json', reply.get('knowledge') or {})
            fwlib.write_json(home / 'exposure.json', reply.get('exposure') or {})
        sha = fingerprint(home / 'state.json') if changed or not edited else synced.get('sha')
        fwlib.write_json(home / SYNCED, {'pulled': time.time(), 'rev': reply.get('rev'), 'collector': url, 'sha': sha})
    return True, None


def fingerprint(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def keep_own_state(home):
    """Copies this machine's own state files into before-collector/ (once each) before the collector's replace them."""
    own = [name for name in MOVED if (home / name).exists()]
    if not own:
        return
    backup = home / BEFORE
    backup.mkdir(exist_ok=True)
    for name in own:
        if not (backup / name).exists():
            shutil.copy2(home / name, backup / name)


def stale(seconds):
    """True when the cache was last refreshed more than this many seconds ago."""
    return time.time() - ((fwlib.read_json(fwlib.home() / SYNCED, {}) or {}).get('pulled') or 0) > seconds


def send_exposure(found, url=None):
    """Sends chat replies scanned here to the collector. True when it took them all."""
    url = url or fwlib.collector_url()
    for start in range(0, len(found), REPLIES_PER_OP):
        reply = post(url, [stamp({'kind': 'exposure', 'replies': found[start:start + REPLIES_PER_OP]})])
        if reply is None or not reply['results'][0].get('ok'):
            return False
    return True


def sync(scan=True, timeout=TIMEOUT):
    """With a collector: send queued ops and (scan) this machine's new chat replies, then refresh the cache. Returns
    (ok, error); one sync at a time."""
    url = fwlib.collector_url()
    if not url:
        return True, None
    with fwlib.locked(SYNC_LOCK, blocking=False) as got:
        if not got:
            return True, None  # another sync is running
        sent_all, sent = flush(url, timeout)
        if scan and (fwlib.home() / 'state.json').exists():
            fwlib.refresh_exposure()
        pulled, error = pull(url, timeout, force=bool(sent))
        if pulled and not sent_all:  # the cache is fresh, still showing what waits here
            return False, f'{len(pending())} change(s) wait here: {url} did not take them'
        return pulled, error


def current(max_age=0, wait=True):
    """The state to read: where it lives, with today's promotion applied; or the cache, refreshed first when older
    than max_age seconds (wait False: refreshed in the background instead). Returns (state, error): error says why a
    stale cache couldn't be refreshed."""
    if not fwlib.collector_url():
        with fwlib.editing() as state:
            pass
        return state, None
    error = None
    if stale(max_age) and (fwlib.home() / 'state.json').exists():
        if wait:
            ok, error = sync(scan=False)
        else:
            knowledge.start_refresh()
    return fwlib.load_state(), error


# ---- the collector ----

def revision():
    """Changes whenever the state, the estimates or the chat counts change."""
    parts = []
    for name in CACHED:
        try:
            info = (fwlib.home() / name).stat()
            parts.append(f'{info.st_mtime_ns}:{info.st_size}')
        except OSError:
            parts.append('-')
    return hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12]


def snapshot(rev=''):
    """(HTTP status, body) for GET /v1/state: everything a cache needs, or "unchanged" when rev is current."""
    home = fwlib.home()
    if not (home / 'state.json').exists():
        return 404, {'ok': False, 'error': NO_STATE}
    now = revision()
    if rev and rev == now:
        return 200, {'ok': True, 'rev': now, 'unchanged': True}
    exposure = fwlib.read_json(home / 'exposure.json', {}) or {}
    return 200, {'ok': True, 'rev': now, 'state': fwlib.load_state(create=False),
                 'knowledge': fwlib.read_json(home / 'knowledge.json', {}) or {},
                 'exposure': {'words': exposure.get('words') or {}, 'updated': exposure.get('updated')}}


def receive(body, db=None):
    """(HTTP status, body) for POST /v1/ops: each op applied in order, with its result."""
    if not isinstance(body, dict) or not isinstance(body.get('ops'), list) or len(body['ops']) > MAX_OPS:
        return 400, {'ok': False, 'error': f'need {{"ops": [...]}} with at most {MAX_OPS} ops'}
    if not (fwlib.home() / 'state.json').exists():
        return 409, {'ok': False, 'error': NO_STATE}
    results = []
    for op in body['ops']:
        try:
            results.append(run(op, db))
        except Exception as error:  # reported to the sender, who drops it; the next ops still apply
            print(f'op failed: {error.__class__.__name__}: {error}', file=sys.stderr, flush=True)
            results.append({'ok': False, 'error': f'failed on the collector: {error.__class__.__name__}: {error}'})
    return 200, {'ok': True, 'results': results, 'rev': revision()}


def maintain():
    """On the collector, every few minutes: today's promotion (a new day can graduate words) and fresh estimates."""
    if not (fwlib.home() / 'state.json').exists():
        return
    with fwlib.editing():
        pass
    knowledge.refresh()
