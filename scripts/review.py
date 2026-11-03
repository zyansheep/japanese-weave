"""Review cards: the review wedge. Every gloss popover in T3 Code (and the phone app's flashcard popup) has a wedge
that turns it into a card: one Japanese word alone; up (or a swipe up) reveals its reading and meaning, right (or a
swipe left) goes to the next card. The client asks the collector for each card (POST /v1/review/next, collector.py)
and reports what happened as a review item (POST /v1/interactions, feedback.py): the time from the card appearing to
the reveal, or that the reader moved on without one. Python 3.9+, standard library only.

  POST /v1/review/next  {"source": "desktop"|"mobile", "exclude"?: [up to EXCLUDE_MAX card ids the client holds]}
                        -> {"ok": true, "card": {"id", "ja", "kana", "gloss"}} or {"ok": true, "card": null}

A card is one of three kinds, never told to the client (its id is random, and a made-up word's gloss only says that it
is one, after the reveal):

- learning (MIX about 70%): a word in use (learning). First the words behind on showings: in their first BEHIND_DAYS
  days in use (from the delayed-start draw's time, else the day the word became learning), fewer than BEHIND_PER_DAY
  showings a day so far (the gloss log's, knowledge.json "history", and their review cards), the furthest behind
  first; then the rest, the least certain first (the highest sd in knowledge.json).
- probe (about 20%): a word of the probe's population (probe.py, frequency_frame), its band drawn in proportion to the
  band's weight, then at random among the band's words not asked in probe.REPEAT_DAYS (by a probe card or a probe of
  old; when none is left, the least recently asked): the cards the probe's rolling estimate reads.
- fake (about 10%): a made-up word (data/fakes.tsv, 300 of them), with its made-up reading and a gloss saying it is
  made up. Its outcomes pin the "not known" component of the model's mixture and the probe's (knowledge.Components).
  The year-long FAKE_DAYS of the probe of old would run dry in weeks here (a hundred cards a day uses ten made-up
  words), so a made-up word is not reused within FAKE_REUSE_DAYS while another is left; past that, the least recently
  used comes back, so the stream never runs dry. A word seen again after two months is unlikely to be remembered as
  made up; if a reader did, their quick dismissal would show in the components as a faster "not known" side.

The kind is drawn at random each time with MIX's chances, so the pattern can't be learned; when the kind drawn has no
word to offer, the other real kind is tried, and a made-up word only when it was drawn (so made-up words stay about one
card in ten when the learning words run out for a while). No word is served that the delayed-start experiment hides or
is about to probe (fwlib.phase held, paused or probe, checked against the whole word state as the web plan does,
webdata._kept_out: by spelling, reading and entry id), nor one served in the last RECENT_HOURS, nor one of the cards
in exclude. No card at all while Japanese Weave is paused, or when no real word can be offered.

Records. Every card served is a row of the cards table (feedback.serve_card): its id, when, the client, the word, its
kind and, for a probe word, its entry id, band and weight; a review item fills in the rest. A review card is a showing
of its word wherever showings of learning words are counted: the recency clock (knowledge.read_clocks: a read), the
delayed-start experiment's exposure (experiment.exposure) and, here, a word's showings while it is behind.
"""
import math
import random
import secrets
import threading
import time

import feedback
import fwlib
import knowledge
import probe

MIX = (('learning', 0.7), ('probe', 0.2), ('fake', 0.1))
RECENT_HOURS = 4           # a word served this recently isn't served again
BEHIND_DAYS = 14           # a learning word's first days, when it may be behind on showings
BEHIND_PER_DAY = 1.0       # and is, with fewer showings a day than this so far
FAKE_REUSE_DAYS = 60       # a made-up word isn't reused this soon while another is left
EXCLUDE_MAX = 20
SOURCES = feedback.REVIEW_SOURCES
MADE_UP = '(made-up word: not a real word)'
HIDDEN = ('held', 'paused', 'probe')  # the delayed-start phases a word is never served in
MAX_JA, MAX_KANA, MAX_GLOSS = 40, 40, 200
DAY_MS = 86_400_000
SERVING = threading.Lock()  # one card at a time, so two quick requests never get the same word


def request_error(body):
    """What is wrong with a POST /v1/review/next body, or None."""
    ident = lambda x: isinstance(x, str) and 0 < len(x) <= 64 and all(c.isalnum() or c in '_:-' for c in x)
    if not isinstance(body, dict) or body.get('source') not in SOURCES:
        return 'need {"source": "desktop"|"mobile", "exclude"?: [card ids]}'
    exclude = body.get('exclude')
    if exclude is not None and (not isinstance(exclude, list) or len(exclude) > EXCLUDE_MAX
                                or not all(ident(x) for x in exclude)):
        return f'exclude: a list of at most {EXCLUDE_MAX} card ids'
    return None


def next_card(source, exclude=(), db=None, now_ms=None, rng=None):
    """The next card for a client, served and recorded here: {id, ja, kana, gloss}, or None when there is nothing to
    review. The word state must exist (the collector answers 409 before asking)."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rng = rng or random.SystemRandom()
    since = now_ms - max(probe.REPEAT_DAYS, FAKE_REUSE_DAYS) * DAY_MS
    with SERVING:
        state = fwlib.load_state(create=False)
        con = feedback.connect(db)
        try:
            card = choose(state, knowledge.load(), feedback.cards(since=since, con=con), exclude, now_ms, rng)
            if card is None:
                return None
            card = dict(card, id=secrets.token_hex(8), served_at=now_ms, source=source)
            feedback.serve_card(card, con=con)
        finally:
            con.close()
    return {'id': card['id'], 'ja': card['word'], 'kana': card['kana'], 'gloss': card['gloss']}


def choose(state, know, served, exclude=(), now_ms=None, rng=None, frame=None, d=None):
    """The next card ({word, kind, kana, gloss, entry?, band?, weight?}) or None, from the whole word state,
    knowledge.json and served (feedback.cards of the last probe.REPEAT_DAYS days at least), as the module docstring says."""
    if not isinstance(state, dict) or state.get('paused'):
        return None
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rng = rng or random.SystemRandom()
    today = knowledge.day_of(now_ms)
    held = set(exclude or ())
    blocked = {fwlib.key(c['word']) for c in served
               if c['served_at'] >= now_ms - RECENT_HOURS * 3_600_000 or c['id'] in held}
    hidden = [w for w in state.get('words') or [] if fwlib.phase(w, today) in HIDDEN]
    found = {}

    def offer(kind):
        if kind not in found:
            if kind == 'learning':
                found[kind] = _learning(state, know, served, blocked, today, now_ms, rng)
            elif kind == 'probe':
                found[kind] = _probe(state, served, blocked, hidden, know, today, rng, frame, d)
            else:
                found[kind] = _fake(served, blocked, now_ms, rng)
        return found[kind]

    kinds, weights = [k for k, _w in MIX], [w for _k, w in MIX]
    first = rng.choices(kinds, weights)[0]
    if first == 'fake':
        return offer('fake') if offer('learning') or offer('probe') else None
    other = 'probe' if first == 'learning' else 'learning'
    return offer(first) or offer(other)


def learning_start(word):
    """When a word's learning began (ms): its delayed-start draw's time, else that day's start, else the day it last
    changed status; None when it has none."""
    arm = word.get('arm') if isinstance(word.get('arm'), dict) else {}
    if isinstance(arm.get('at'), int) and not isinstance(arm.get('at'), bool):
        return arm['at']
    day = arm.get('day') or word.get('changed')
    try:
        return knowledge.day_start(day) if day else None
    except (TypeError, ValueError):
        return None


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _learning(state, know, served, blocked, today, now_ms, rng):
    entries = know.get('words') if isinstance(know.get('words'), dict) else {}
    reviewed = {}
    for c in served:
        if c.get('at') is not None:
            reviewed.setdefault(c['word'], []).append(c['at'])
    behind, rest = [], []
    for w in state.get('words') or []:
        if w.get('status') not in ('active', 'shaky') or fwlib.phase(w, today) in HIDDEN:
            continue
        if fwlib.key(w['ja']) in blocked or not 0 < len(w['ja']) <= MAX_JA:
            continue
        entry = entries.get(w['ja']) if isinstance(entries.get(w['ja']), dict) else {}
        start = learning_start(w)
        if start is not None and 0 <= now_ms - start < BEHIND_DAYS * DAY_MS:
            days = max(1.0, (now_ms - start) / DAY_MS)
            shown = sum(1 for h in entry.get('history') or () if isinstance(h, dict) and _number(h.get('at'))
                        and h['at'] >= start) + sum(1 for at in reviewed.get(w['ja'], ()) if at >= start)
            if shown < BEHIND_PER_DAY * days:
                behind.append((shown / days, rng.random(), w))
                continue
        sd = entry.get('sd') if _number(entry.get('sd')) else math.inf  # no estimate yet: the least certain
        rest.append((-sd, rng.random(), w))
    ordered = sorted(behind, key=lambda t: t[:2]) + sorted(rest, key=lambda t: t[:2])
    if not ordered:
        return None
    w = ordered[0][2]
    entry = entries.get(w['ja']) if isinstance(entries.get(w['ja']), dict) else {}
    found = entry.get('id') if isinstance(entry.get('id'), int) else w.get('id') if isinstance(w.get('id'), int) \
        else None
    return {'word': w['ja'], 'kind': 'learning', 'kana': (w.get('kana') or '')[:MAX_KANA],
            'gloss': (w.get('en') or '')[:MAX_GLOSS], 'entry': found}


def _probe(state, served, blocked, hidden, know, today, rng, frame=None, d=None):
    import dictionary
    if not dictionary.PATH.exists():
        return None
    d = d or dictionary.load()
    frame = frame or probe.frequency_frame()
    entries = know.get('words') if isinstance(know.get('words'), dict) else {}
    ids, spellings, readings = set(), set(), set()
    for w in hidden:  # as webdata._kept_out: by entry id, spelling and reading
        spellings.add(fwlib.key(w['ja']))
        if w.get('kana'):
            readings.add(fwlib.key(w['kana']))
        e = d.find(w['ja'], w.get('kana') or '', w.get('en') or '')
        mine = entries.get(w['ja']) if isinstance(entries.get(w['ja']), dict) else {}
        ids.update(x for x in (w.get('id'), mine.get('id'), e['id'] if e else None) if isinstance(x, int))
    last, _fakes = probe.last_asked(state.get('probes'))
    for c in served:
        if c['kind'] == 'probe':
            day = knowledge.day_of(c['served_at'])
            last[c['word']] = max(last.get(c['word'], ''), day)
    out = blocked | spellings | readings
    bands = list(range(len(frame.bands)))
    while bands:
        h = rng.choices(bands, [frame.shares[b] for b in bands])[0]
        bands.remove(h)
        pool = [it for it in frame.bands[h] if fwlib.key(it['ja']) not in out and not ids.intersection(it['ids'])
                and len(it['ja']) <= MAX_JA]
        while pool:
            it = probe.pick(pool, 1, last, today, probe.REPEAT_DAYS, rng, key=lambda it: it['ja'])[0]
            e = d.get(it['ids'][0])
            if e is not None and fwlib.key(e['kana']) not in readings:
                return {'word': it['ja'], 'kind': 'probe', 'kana': e['kana'][:MAX_KANA], 'gloss': e['en'][:MAX_GLOSS],
                        'entry': e['id'], 'band': h, 'weight': float(f'{it["weight"]:.6g}')}
            pool.remove(it)
    return None


def _fake(served, blocked, now_ms, rng):
    last = {}
    for c in served:
        if c['kind'] == 'fake':
            last[c['word']] = max(last.get(c['word'], 0), c['served_at'])
    pool = [f for f in probe.read_fakes() if fwlib.key(f[0]) not in blocked]
    if not pool:
        return None
    fresh = [f for f in pool if now_ms - last.get(f[0], -math.inf) >= FAKE_REUSE_DAYS * DAY_MS]
    ja, kana = rng.choice(fresh) if fresh else min(pool, key=lambda f: last[f[0]])
    return {'word': ja, 'kind': 'fake', 'kana': kana[:MAX_KANA], 'gloss': MADE_UP}
