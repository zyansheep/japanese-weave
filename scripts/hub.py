"""Changes to the word state, and the collector as its one home (Python 3.9+, standard library only).

Every change to the word state is an op, applied by apply() where the state lives. Without a collector that is this
machine (run). With one (`fw.py setup --collector URL`), the state lives on the collector's machine: ops go there
(POST /v1/ops), the collector applies and records them, and this machine keeps a cache of state.json, knowledge.json
and exposure.json (GET /v1/state). An op the collector can't take right now waits in outbox.jsonl and is applied to
the cache meanwhile, so the change shows at once; the next sync sends it. The hook never waits on the network: it reads
the cache and starts a background sync (`fw.py refresh`) when the cache is a few minutes old.

Ops (each also carries uid, day, at and machine; see stamp):
  events    {"events": [{"word", "kind": "seen"|"peek"|"flip", "day"?}]}   from the browser; kept in the ops log only:
                                                       nothing counts days any more (fwlib.apply_event)
  mark      {"words": [...], "as": "known"|"asked"}    the known and learn commands: the label at once
  set       {"stage"?: 1-4 | "auto", "paused"?: bool, "prefs"?: {"chat_glosses"?, "challenge"?, "extension_sites"?,
             "intensity_chat"?, "intensity_browser"?: {level -3..3, until, at, source}}}   (intensity.py)
  seed      {"n": N}                                   the top N lexicon words known, unless already introduced
  anki      {"words": [{ja, en, hint, kana, interval, mature}], "known": [ja]}   Anki review words first
  exposure  {"replies": [{"d": digest, "day", "words": [ja]}]}   chat replies scanned on some machine
  chat_day  {}                                         from before the switch (log-chat-day): changes nothing now
  exported  {"target": "claude-ai"|"chatgpt", "log_day"?: bool}   log_day changes nothing now
  noticed   {}                                         the stale-export notice was shown
  thread    {"session", "off": bool, "source"}         Japanese off (or back on) in one chat (threads.py)
  labels    {"labels": [[ja, from, to]], "intake": [[ja, arm|null, draw|null]], "probes": [[ja, outcome]],
             "experiment"?: {"seed"}}                a refresh's state edit where the state lives (knowledge.decide):
                                                       labels from the model, the words taken in with their
                                                       delayed-start arms, the probes' outcomes (fwlib.apply_labels)
  brief     {"session", "level", "intensity", "offered": [[ja, p]], "arms": {ja: arm}, "known", "whole", "chars",
             "budget"?, "ask"?, "sentences"?, "sentencePrice"?, "expected"?, "turn"?}
                                                       what one chat reply was offered (weave.compose); a record only
  probe     {"via", "seconds", "weights", "top", "words": [[ja, id, band, weight, answer]], "fakes": [[ja, answer]],
             "bands": [[share, words]], "estimate"}   a probe of the goal (probe.py), kept in state["probes"]; where
             the gloss log is, the lookup habit of its words answered not known is added
The collector records every op it applies (the ops table of its database), so the log of gloss interactions and the
log of everything that changed the word state sit side by side. Records (RECORDS) change nothing: they are kept in
the ops table and nowhere else (hub.keep; feedback.chat_briefs reads them back).
"""
import datetime as dt
import hashlib
import json
import math
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
import intensity
import knowledge
import threads

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
PROVISIONAL = ('events', 'mark', 'set', 'seed', 'chat_day', 'exported', 'noticed',  # applied to the cache while queued
               'thread', 'probe')
RECORDS = ('brief',)       # ops only kept in the ops table: they change nothing in the word state
RECORD_TIMEOUT = 2         # seconds a record waits for the database here before it is dropped
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
         'extension_sites': lambda v: v in ('all', 'ask'), 'intensity_chat': intensity.valid,
         'intensity_browser': intensity.valid}
LIVE_TIMEOUT = 0.3         # seconds the hook waits for the collector's intensity before using the cache


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
    elif kind == 'thread':
        if not threads.valid_session(op.get('session')) or not isinstance(op.get('off'), bool) \
                or not text(op.get('source'), 20):
            return 'thread: {"session", "off": true|false, "source"}'
    elif kind == 'labels':
        return labels_error(op)
    elif kind == 'brief':
        return brief_error(op)
    elif kind == 'probe':
        return probe_error(op)
    elif kind not in ('chat_day', 'noticed'):
        return f'unknown op {kind!r}'
    return None


BRIEF_FIELDS = {'uid', 'day', 'at', 'machine', 'kind', 'session', 'level', 'intensity', 'offered', 'arms', 'known',
                'whole', 'chars', 'budget', 'ask', 'sentences', 'sentencePrice', 'expected', 'turn'}


def brief_error(op):
    """What is wrong with a brief record, as feedback._plan_ok checks a web passage's plan, or None. Amounts are per
    100 words; full has no budget, ask, sentences or expected. sentencePrice: the lookups a whole sentence costs, as in
    a web plan; briefs from before sentences were priced have none."""
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    count = lambda x, top: isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= top
    amount = lambda key: key not in op or (number(op[key]) and 0 <= op[key] <= 1000)
    word = lambda x: isinstance(x, str) and 0 < len(x) <= 40
    offered, arms = op.get('offered'), op.get('arms')
    good = (set(op) <= BRIEF_FIELDS and threads.valid_session(op.get('session'))
            and isinstance(op.get('level'), int) and not isinstance(op['level'], bool) and op['level'] in intensity.LEVELS
            and number(op.get('intensity')) and 0 < op['intensity'] <= 5
            and isinstance(offered, list) and len(offered) <= 200
            and all(isinstance(o, list) and len(o) == 2 and word(o[0]) and number(o[1]) and 0 <= o[1] <= 1
                    for o in offered)
            and isinstance(arms, dict) and len(arms) <= 200
            and all(word(k) and isinstance(v, str) and 0 < len(v) <= 20 for k, v in arms.items())
            and count(op.get('known'), 100_000) and isinstance(op.get('whole'), bool) and count(op.get('chars'), 100_000)
            and amount('budget') and amount('ask') and amount('sentences') and amount('expected')
            and ('sentencePrice' not in op or (number(op['sentencePrice']) and 0 < op['sentencePrice'] <= 1))
            and ('turn' not in op or threads.valid_session(op['turn'])))
    return None if good else ('brief: {"session", "level", "intensity", "offered": [[word, p]], "arms": {word: arm}, '
                              '"known", "whole", "chars", "budget"?, "ask"?, "sentences"?, "sentencePrice"?, '
                              '"expected"?, "turn"?}')


LABELS_FIELDS = {'uid', 'day', 'at', 'machine', 'kind', 'labels', 'intake', 'probes', 'experiment'}


def labels_error(op):
    """What is wrong with a labels op (knowledge.decide), or None: label changes [[word, from, to]], words taken in
    [[word, arm or null, draw or null]], probes [[word, {at, surface, y, read} or "missed"]] and the experiment's
    seed when it starts."""
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    word = lambda x: isinstance(x, str) and 0 < len(x) <= 100
    rows = lambda key, most, ok: key not in op or (isinstance(op[key], list) and len(op[key]) <= most
                                                    and all(isinstance(r, list) and ok(r) for r in op[key]))
    label = lambda r: (len(r) == 3 and word(r[0]) and r[1] in fwlib.STATUSES + ('shaky',)
                       and r[2] in fwlib.STATUSES)
    intake = lambda r: (len(r) == 3 and word(r[0]) and (r[1] is None and r[2] is None
                                                         or r[1] in fwlib.ARMS and number(r[2]) and 0 <= r[2] < 1))
    outcome = lambda o: o == 'missed' or (isinstance(o, dict) and set(o) == {'at', 'surface', 'y', 'read'}
                                          and isinstance(o['at'], int) and o['surface'] in feedback.SURFACES
                                          and number(o['y']) and 0 <= o['y'] <= 1
                                          and number(o['read']) and 0 <= o['read'] <= 1)
    seed = op.get('experiment')
    good = (set(op) <= LABELS_FIELDS and rows('labels', 100_000, label)
            and rows('intake', fwlib.NEW_PER_DAY, intake)
            and rows('probes', 10_000, lambda r: len(r) == 2 and word(r[0]) and outcome(r[1]))
            and (seed is None or isinstance(seed, dict) and set(seed) == {'seed'} and isinstance(seed['seed'], int)
                 and not isinstance(seed['seed'], bool) and 0 <= seed['seed'] < 2 ** 32))
    return None if good else ('labels: {"labels": [[word, from, to]], "intake": [[word, arm, draw]], "probes": '
                              '[[word, outcome]], "experiment"?: {"seed"}}')


PROBE_FIELDS = {'uid', 'day', 'at', 'machine', 'kind', 'via', 'seconds', 'weights', 'top', 'words', 'fakes', 'bands',
                'estimate'}
PROBE_SHARES = ('known', 'se', 'low', 'high', 'corrected', 'corrected_low', 'corrected_high', 'listed')
PROBE_COUNTS = ('deff', 'n', 'claimed', 'fakes', 'claimed_year', 'fakes_year')


def probe_error(op):
    """What is wrong with a probe op (probe.record), or None: its words [[word, entry id, band, weight, answer]],
    made-up words [[word, answer]] and bands [[share of the weight, words]], an answer 1, 0 or null (skipped), and
    its estimate's shares between 0 and 1."""
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    count = lambda x, top: isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= top
    word = lambda x: isinstance(x, str) and 0 < len(x) <= 40
    answer = lambda x: x is None or count(x, 1)
    words, fakes, bands, found = op.get('words'), op.get('fakes'), op.get('bands'), op.get('estimate')
    good = (set(op) <= PROBE_FIELDS and op.get('via') in ('terminal', 'chat') and count(op.get('seconds'), 10 ** 7)
            and op.get('weights') in ('frequency', 'reading') and count(op.get('top'), 10 ** 6)
            and isinstance(bands, list) and 0 < len(bands) <= 20
            and all(isinstance(b, list) and len(b) == 2 and number(b[0]) and 0 <= b[0] <= 1 and count(b[1], 10 ** 6)
                    and b[1] > 0 for b in bands)
            and isinstance(words, list) and len(words) <= 200
            and all(isinstance(w, list) and len(w) == 5 and word(w[0]) and count(w[1], 10 ** 9)
                    and count(w[2], len(bands) - 1) and number(w[3]) and w[3] > 0 and answer(w[4]) for w in words)
            and isinstance(fakes, list) and len(fakes) <= 50
            and all(isinstance(f, list) and len(f) == 2 and word(f[0]) and answer(f[1]) for f in fakes)
            and isinstance(found, dict) and set(found) <= set(PROBE_SHARES + PROBE_COUNTS)
            and all(found[k] is None or number(found[k]) and 0 <= found[k] <= 1 for k in PROBE_SHARES if k in found)
            and all(found[k] is None or number(found[k]) and 0 <= found[k] <= 10 ** 6 for k in PROBE_COUNTS
                    if k in found))
    return None if good else ('probe: {"via": "terminal"|"chat", "seconds", "weights", "top", "words": [[word, id, '
                              'band, weight, answer]], "fakes": [[word, answer]], "bands": [[share, words]], '
                              '"estimate": {...}}')


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
        entries = fwlib.mark_known(state, op['known'], day)
        return {'words': len(op['words']), 'known': sum(e['status'] == 'known' for e in entries)}
    if kind == 'chat_day':  # it counted assumed exposure days, which nothing counts any more
        return {'logged': 0}
    if kind == 'exported':
        export.mark_exported(state, op['target'])
        return {'logged': 0}
    if kind == 'labels':
        return fwlib.apply_labels(state, op, day)
    if kind == 'noticed':
        state.setdefault('exported', {})['noticed'] = True
        return {}
    if kind == 'thread':  # merged per chat: a later change from another machine is kept
        return {'off': threads.merge(state, op['session'], {'off': op['off'], 'at': op['at'], 'source': op['source']})}
    if kind == 'probe':
        import probe  # here: the hook needn't load the dictionary
        return probe.keep(state, op)
    return {}  # exposure: run folds it into exposure.json


def run(op, db=None):
    """Applies an op where the state lives (this machine) and records it; returns its result with ok. An op already
    recorded (same uid: a resend after a lost reply) is not applied again."""
    error = validate(op)
    if error:
        return {'ok': False, 'error': error}
    if op['kind'] in RECORDS:  # nothing to apply, so no wait for the ops lock (an Anki sync holds it for a while)
        con = feedback.connect(db, timeout=RECORD_TIMEOUT)
        try:
            with con:
                added = con.execute('INSERT OR IGNORE INTO ops (uid, at, machine, kind, body) VALUES (?, ?, ?, ?, ?)',
                                    (op['uid'], op['at'], str(op.get('machine') or ''), op['kind'],
                                     json.dumps(op, ensure_ascii=False))).rowcount
        finally:
            con.close()
        return {'ok': True} if added else {'ok': True, 'duplicate': True}
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
    habit = None
    if kind == 'probe':  # the gloss log lives where the state does
        import probe
        habit = probe.habit_here(op)
    with fwlib.editing() as state:  # merges anki.csv into the list, applies, sets the stage, saves
        result = apply(state, op)
        if habit is not None and result.get('kept'):
            result['habit'] = probe.attach(state, op['uid'], habit)
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


def keep(op):
    """Keeps a record (RECORDS) without waiting on the network or the ops lock: with a collector it joins the outbox
    and goes with the next sync; else it goes into the ops table here. Returns its result."""
    op = stamp(op)
    error = validate(op)
    if error:
        raise ValueError(error)
    if fwlib.collector_url():
        queue(op)
        return {'ok': True, 'queued': True}
    return run(op)


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
            # malformed, or it failed there: resending won't help. A record refused (by a collector older than its
            # kind, say) is only a gap in the log, so it goes quietly.
            if not result.get('ok') and op.get('kind') not in RECORDS:
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
    """The state to read: where it lives (its stage brought up to date); or the cache, refreshed first when older
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


# ---- intensity (intensity.py) ----

def change_intensity_here(surface, delta, source, db=None):
    """One press where the state lives (this machine, or the collector for everyone): the new pref, recorded as a set
    op. The caller keeps presses one at a time (the collector's lock), so two quick presses make two steps."""
    value = intensity.step(fwlib.load_state().get('prefs'), surface, delta, source)
    result = run(stamp({'kind': 'set', 'prefs': {intensity.pref(surface): value}}), db)
    if not result.get('ok'):
        raise ValueError(result.get('error') or 'intensity not saved')
    return intensity.current({intensity.pref(surface): value}, surface)


def change_intensity(surface, delta, source):
    """One press from this machine: on the collector when there is one (it has the level other machines set), else
    here. With the collector out of reach, the step is taken from the cache and queued like any other op.
    Returns ({level, until, at}, error or None)."""
    url = fwlib.collector_url()
    if not url:
        return change_intensity_here(surface, delta, source), None
    reply = feedback.request(url.rstrip('/') + '/v1/intensity', {'surface': surface, 'step': delta, 'source': source},
                             timeout=TIMEOUT)
    if isinstance(reply, dict) and reply.get('ok') and intensity.is_current(reply.get('intensity')):
        knowledge.start_refresh()  # the cache catches up in the background
        return reply['intensity'], None
    value = intensity.step(fwlib.load_state().get('prefs'), surface, delta, source)
    submit({'kind': 'set', 'prefs': {intensity.pref(surface): value}}, wait=False)
    error = (reply or {}).get('error') if isinstance(reply, dict) else None
    return intensity.current({intensity.pref(surface): value}, surface), error or 'collector unreachable; queued'


def set_intensity(surface, level, source):
    """A level set directly (fw.py intensity off|full), for DURATION_MS from now: a set op, here or on the collector,
    queued when it is out of reach. Returns ({level, until, at}, error or None)."""
    value = intensity.at_level(level, source)
    queued = submit({'kind': 'set', 'prefs': {intensity.pref(surface): value}}).get('queued')
    error = 'collector unreachable; queued' if queued else None
    return intensity.current({intensity.pref(surface): value}, surface), error


def live_intensity(surface, timeout=LIVE_TIMEOUT):
    """The level in effect now. With a collector, asked of it first (a press in T3 Code reaches the collector, not this
    machine's cache), briefly, so a prompt never waits long; else, or when it doesn't answer, the cache."""
    url = fwlib.collector_url()
    if url:
        reply = feedback.request(f'{url.rstrip("/")}/v1/intensity?surface={surface}', timeout=timeout)
        if isinstance(reply, dict) and reply.get('ok') and intensity.is_current(reply.get('intensity')):
            return reply['intensity']
    return intensity.current(fwlib.load_state().get('prefs'), surface)


# ---- one chat without Japanese (threads.py) ----

def change_thread(session, off, source, wait=True):
    """Turns Japanese off (or back on) in one chat: a thread op, applied where the state lives or sent to the
    collector; when that can't happen now (or wait is False: the hook never waits) it is queued and shows in the cache
    at once, so the next prompt here honors it. Returns ({off, at, source}, error or None)."""
    value = {'off': bool(off), 'at': intensity.now_ms(), 'source': str(source or 'unknown')[:20]}
    result = submit(dict(value, kind='thread', session=session), wait=wait)
    return value, 'collector unreachable; queued' if wait and result.get('queued') else None


def live_chat(session=None, state=None, timeout=LIVE_TIMEOUT):
    """(chat intensity in effect, this chat's switch or None) for the hook: one brief question to the collector, as in
    live_intensity, else the cache. A change made here that the collector doesn't have yet is the later one, so it
    wins."""
    state = state if state is not None else fwlib.load_state()
    mine = threads.entry(state, session)
    url = fwlib.collector_url()
    if url:
        query = 'surface=chat' + (f'&session={quote(session)}' if threads.valid_session(session) else '')
        reply = feedback.request(f'{url.rstrip("/")}/v1/intensity?{query}', timeout=timeout)
        if isinstance(reply, dict) and reply.get('ok') and intensity.is_current(reply.get('intensity')):
            return reply['intensity'], threads.later(mine, reply.get('thread'))
    return intensity.current(state.get('prefs'), 'chat'), mine


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
    """On the collector, every few minutes: fresh estimates, and with them the labels, the day's new words and the
    probes (knowledge.refresh)."""
    if not (fwlib.home() / 'state.json').exists():
        return
    with fwlib.editing():
        pass
    knowledge.refresh()
