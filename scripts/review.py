"""Review cards, scheduled the way Anki schedules them (FSRS-6, srs.py). T3 Code's review panel (desktop sidebar) and
the phone's Review screen show one Japanese word alone; the reader reveals its reading and meaning, then grades it
with Anki's four buttons (Again, Hard, Good, Easy), or leaves it. The client asks the collector for each card (POST
/v1/review/next, collector.py) and reports what happened as a review item (POST /v1/interactions, feedback.py): the
time to the reveal, the grade and the time to it. Python 3.9+, standard library only.

  POST /v1/review/next  {"source": "desktop"|"mobile", "exclude"?: [up to EXCLUDE_MAX card ids the client holds],
                         "more"?: true to lift today's new-card limit for this request,
                         "peek"?: true for the counts alone (a badge): card null, nothing served}
                        -> {"ok": true, "card": {"id", "ja", "kana", "gloss", "queue": "new"|"learning"|"review",
                            "intervals": {"again", "hard", "good", "easy"}} or null,
                            "counts": {"new", "learning", "review"}}

intervals: the seconds until the card would come back after each grade, what Anki prints above its buttons (srs.
intervals). counts: Anki's three numbers: new cards left today within the day's limit, learning cards due now or
within the learn-ahead window (srs.LEARN_AHEAD_S, 20 minutes), review cards due today. card null with every count 0:
done for today (the client offers "Review more", which asks again with more). card null with a count left happens only
while the cards counted are held by the client or pending (below); the client asks again later. peek answers the
counts alone, as the same request without it would have them, and serves nothing: no cards row is written, so no word
is held back as pending and no made-up or probe word is used up (T3 Code's header badge and the phone's badge poll
it).

The deck: every word with a graded learning card (its schedule: the replay of its graded reviews in time order, srs.
replay, cached per database until another graded card is stored: schedules), plus the new cards: the learning words
(status learning) without one. A word the reader marked known themselves (the known command or op: its latest known
or learn command is known and its status still is; known_by_command) leaves the deck; a word the model labels known
keeps its schedule, since the model moving on says nothing about recall; a later learn command (or the model labelling
it learning again) brings it back with its schedule. A word the delayed-start experiment hides or is about to probe
(fwlib.phase held, paused or probe) is never served, nor counted, as before.

Queue, Anki's order (v3, default options): first the learning and relearning cards due now, the earliest due first;
then the review cards due today (Anki's day starts at 4am: srs.day_number), by due day, then at random; then new cards
while today's limit lasts (NEW_PER_DAY, Anki's default 20: a word is new on the day of its first graded review), in
today's order: the words behind on showings first (in their first BEHIND_DAYS days in use, from the delayed-start
draw's time, else the day the word became learning, fewer than BEHIND_PER_DAY showings a day so far: the gloss log's,
knowledge.json "history", and their review cards; the furthest behind first), then the least certain (the highest sd
in knowledge.json); last, when nothing else is due, a learning card due within the learn-ahead window. more lifts the
day's limit for one request (Anki's custom study "increase today's new card limit", without remembering it). Words
of the cards in exclude are skipped, and so are words served in the last PENDING_MS whose review hasn't come back yet
(a grade sent through T3 Code's batched events can arrive after the next request, which would otherwise show the same
word again); neither is taken off the counts. A card left without a grade schedules nothing and stays due, but for
LEFT_HOURS it waits behind every other card that can be served (the longest left first): a reader who closed the panel
on it sees it again, and a client from before grades, which leaves every card, still moves through the words instead
of getting the same one again. RECENT_HOURS no longer applies to these cards: learning steps come back within
minutes.

Probe and made-up cards are interleaved at random while there is a real card to serve: PROBE_SHARE (10%) of the cards
served are probe words and FAKE_SHARE (5%) made-up words, drawn per request so the pattern can't be learned; they look
exactly like new cards (queue "new" and a new card's intervals), are never counted, and never get a schedule (a grade
on one is stored but schedules nothing). Before grades they were 20% and 10% of a stream of about 20 cards a day; a
day of Anki now serves several times that (each new word comes back through its learning steps, then its reviews), so
half the share gives the probe's rolling estimate (probe.rolling) as many cards as before or more, and keeps the
made-up words at half the probe words, as they were. With no real card to serve there is none (card null): never a
probe or made-up card on its own.
- probe: a word of the probe's population (probe.py, frequency_frame), its band drawn in proportion to the band's
  weight, then at random among the band's words not asked in probe.REPEAT_DAYS (by a probe card or a probe of old; when
  none is left, the least recently asked), never one served in the last RECENT_HOURS: the cards the probe's rolling
  estimate reads.
- fake: a made-up word (data/fakes.tsv, 300 of them), with its made-up reading and a gloss saying it is made up. Its
  outcomes pin the "not known" component of the model's mixture and the probe's (knowledge.Components). A made-up
  word is not reused within FAKE_REUSE_DAYS while another is left; past that, the least recently used comes back, so
  the stream never runs dry. A word seen again after two months is unlikely to be remembered as made up.
Neither is a word the delayed-start experiment hides or is about to probe (checked against the whole word state as the
web plan does, webdata._kept_out: by spelling, reading and entry id). No card at all while Japanese Weave is paused.

Version 1 feeds only graded review cards to FSRS. FSRS assumes the reviews are the only times the reader meets a word,
but this reader also meets their words in chat and on web pages, glossed (opened or not): every such meeting is
practice FSRS doesn't see, so its intervals come out conservative (more reviews than strictly needed). A gloss opened
while reading is a candidate future "Again" signal once there is data to check it against (does an open predict an
Again at the next review?); until then the reading log feeds the reading model (knowledge.py), and the reading model
reads a graded card as an explicit answer (knowledge.review_answers).

Records. Every card served is a row of the cards table (feedback.serve_card): its id, when, the client, the word, its
kind (learning for every deck card, new ones included) and, for a probe word, its entry id, band and weight; a review
item fills in the rest, the grade included. A review card is a showing of its word wherever showings of learning words
are counted: the recency clock (knowledge.read_clocks: a read), the delayed-start experiment's exposure
(experiment.exposure) and, here, a word's showings while it is behind. Old clients (no grade, no more) keep working:
their items store no grade, so their cards stay new and keep being served, and the extra response fields are theirs
to ignore.
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
import srs

NEW_PER_DAY = 20           # new cards a day (Anki's default), lifted for a request by more
PROBE_SHARE = 0.10         # of the cards served while there is a real card to serve
FAKE_SHARE = 0.05
PENDING_MS = 10 * 60_000   # a card served this recently and not reported yet keeps its word off the next cards
LEFT_HOURS = 4             # a word whose last card was left without a grade this recently waits behind the others
RECENT_HOURS = 4           # a probe word served this recently isn't served again
BEHIND_DAYS = 14           # a learning word's first days, when it may be behind on showings
BEHIND_PER_DAY = 1.0       # and is, with fewer showings a day than this so far
FAKE_REUSE_DAYS = 60       # a made-up word isn't reused this soon while another is left
EXCLUDE_MAX = 20
OPTIMIZE_REVIEWS = 1000    # graded reviews before FSRS's parameters are worth fitting (fw.py review)
SOURCES = feedback.REVIEW_SOURCES
MADE_UP = '(made-up word: not a real word)'
HIDDEN = ('held', 'paused', 'probe')  # the delayed-start phases a word is never served in
MAX_JA, MAX_KANA, MAX_GLOSS = 40, 40, 200
DAY_MS = 86_400_000
NO_COUNTS = {'new': 0, 'learning': 0, 'review': 0}
SERVING = threading.Lock()  # one card at a time, so two quick requests never get the same word
_SCHEDULES, _SCHEDULES_LOCK = {}, threading.Lock()


def request_error(body):
    """What is wrong with a POST /v1/review/next body, or None."""
    ident = lambda x: isinstance(x, str) and 0 < len(x) <= 64 and all(c.isalnum() or c in '_:-' for c in x)
    if not isinstance(body, dict) or body.get('source') not in SOURCES:
        return 'need {"source": "desktop"|"mobile", "exclude"?: [card ids], "more"?: true|false, "peek"?: true|false}'
    exclude = body.get('exclude')
    if exclude is not None and (not isinstance(exclude, list) or len(exclude) > EXCLUDE_MAX
                                or not all(ident(x) for x in exclude)):
        return f'exclude: a list of at most {EXCLUDE_MAX} card ids'
    for name in ('more', 'peek'):
        if body.get(name) is not None and not isinstance(body[name], bool):
            return f'{name}: true or false'
    return None


def schedules(con, path=None):
    """{word: (srs.Card, review log)} for every word with a graded learning card: its schedule, the replay of its graded
    reviews in the order they were graded (feedback.graded; a review's time is when the grade was given), and the log,
    [(reviewed ms, grade, the state it was in, grade_ms)]. Cached per database (path) under feedback.graded_mark and
    the scheduler's settings, so storing a grade, or changing a parameter, recomputes them."""
    key = (feedback.graded_mark(con), srs.PARAMETERS, srs.DESIRED_RETENTION, srs.LEARNING_STEPS, srs.RELEARNING_STEPS,
           srs.MAXIMUM_INTERVAL, srs.ROLLOVER_HOUR)
    name = str(path or feedback.default_path())
    with _SCHEDULES_LOCK:
        found = _SCHEDULES.get(name)
    if found and found[0] == key:
        return found[1]
    by_word = {}
    for r in feedback.graded(con=con):
        by_word.setdefault(r['word'], []).append(r)
    out = {}
    for word, rows in by_word.items():
        card, log = srs.replay([(r['reviewed'], r['grade']) for r in rows], srs.card_id(word))
        out[word] = (card, [(at, grade, state, r['grade_ms']) for (at, grade, state), r in zip(log, rows)])
    with _SCHEDULES_LOCK:
        _SCHEDULES[name] = (key, out)
    return out


def known_by_command(state, commands):
    """The words the reader marked known themselves: their latest known or learn command (knowledge.read_commands) is
    known, and their status still is."""
    last = {}
    for c in sorted((c for c in commands or () if isinstance(c, dict)), key=lambda c: c.get('at') or 0):
        last[c.get('word')] = c.get('kind')
    return {w['ja'] for w in (state or {}).get('words') or ()
            if w.get('status') == 'known' and last.get(w['ja']) == 'known'}


def queues(state, know, served, decks, now_ms, rng, more=False, commands=()):
    """Today's queues and counts, as the module docstring orders them: {learning: [(card, word)] due now, earliest
    first; review: [(card, word)] due today; new: [word] in today's order, within today's room (all with more); ahead:
    [(card, word)] learning cards due within the learn-ahead window; counts: {new, learning, review}; introduced: words
    whose first graded review was today}. word: the word state's entry."""
    today, day = knowledge.day_of(now_ms), srs.day_number(now_ms)
    out = {'learning': [], 'review': [], 'new': [], 'ahead': [], 'introduced': 0}
    by_ja = {w['ja']: w for w in (state or {}).get('words') or ()
             if isinstance(w, dict) and isinstance(w.get('ja'), str)}
    mine = known_by_command(state, commands)
    for ja, (card, log) in decks.items():
        if log and srs.day_number(log[0][0]) == day:
            out['introduced'] += 1
        w = by_ja.get(ja)
        if w is None or ja in mine or fwlib.phase(w, today) in HIDDEN or not 0 < len(ja) <= MAX_JA:
            continue
        if card.state == srs.REVIEW:
            if srs.is_due(card, now_ms):
                out['review'].append((srs.day_number(card.due), rng.random(), card, w))
        elif srs.is_due(card, now_ms):
            out['learning'].append((card.due, card, w))
        elif srs.is_due(card, now_ms, srs.LEARN_AHEAD_S):
            out['ahead'].append((card.due, card, w))
    out['learning'] = [(c, w) for _due, c, w in sorted(out['learning'], key=lambda t: t[0])]
    out['ahead'] = [(c, w) for _due, c, w in sorted(out['ahead'], key=lambda t: t[0])]
    out['review'] = [(c, w) for _d, _r, c, w in sorted(out['review'], key=lambda t: t[:2])]
    fresh = _new_order(state, know, served, set(decks) | mine, today, now_ms, rng)
    room = len(fresh) if more else max(0, NEW_PER_DAY - out['introduced'])
    out['new'] = fresh[:room]
    out['counts'] = {'new': len(out['new']), 'learning': len(out['learning']) + len(out['ahead']),
                     'review': len(out['review'])}
    return out


def next_card(source, exclude=(), db=None, now_ms=None, rng=None, more=False, peek=False):
    """The next card for a client, served and recorded here, and today's counts: ({id, ja, kana, gloss, queue,
    intervals} or None, {new, learning, review}). peek: (None, the counts), nothing served or written. The word state
    must exist (the collector answers 409 before asking)."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rng = rng or random.SystemRandom()
    since = now_ms - max(probe.REPEAT_DAYS, FAKE_REUSE_DAYS) * DAY_MS
    if peek:
        return None, counts_now(db, now_ms, more)
    with SERVING:
        state = fwlib.load_state(create=False)
        con = feedback.connect(db)
        try:
            decks = schedules(con, db)
            card, counts = choose(state, knowledge.load(), feedback.cards(since=since, con=con), decks, exclude,
                                  now_ms, rng, more=more, commands=knowledge.read_commands())
            if card is None:
                return None, counts
            card = dict(card, id=secrets.token_hex(8), served_at=now_ms, source=source)
            feedback.serve_card(card, con=con)
        finally:
            con.close()
    return {k: card[v] for k, v in (('id', 'id'), ('ja', 'word'), ('kana', 'kana'), ('gloss', 'gloss'),
                                    ('queue', 'queue'), ('intervals', 'intervals'))}, counts


def counts_now(db=None, now_ms=None, more=False):
    """Today's counts as POST /v1/review/next would answer them (with more: past the day's new-card limit), reading
    the cards table and writing nothing: peek's answer and fw.py review's."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    state = fwlib.load_state(create=False)
    if not isinstance(state, dict) or state.get('paused'):
        return dict(NO_COUNTS)
    con = feedback.connect(db)
    try:
        decks = schedules(con, db)
        served = feedback.cards(since=now_ms - max(probe.REPEAT_DAYS, FAKE_REUSE_DAYS) * DAY_MS, con=con)
    finally:
        con.close()
    return queues(state, knowledge.load(), served, decks, now_ms, random.Random(0), more,
                  knowledge.read_commands())['counts']


def choose(state, know, served, decks, exclude=(), now_ms=None, rng=None, frame=None, d=None, more=False,
           commands=()):
    """The next card ({word, kind, kana, gloss, queue, intervals, entry?, band?, weight?}) or None, and today's counts,
    from the whole word state, knowledge.json, served (feedback.cards of the last probe.REPEAT_DAYS days at least),
    decks (schedules) and the known and learn commands, as the module docstring says."""
    if not isinstance(state, dict) or state.get('paused'):
        return None, dict(NO_COUNTS)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rng = rng or random.SystemRandom()
    q = queues(state, know, served, decks, now_ms, rng, more, commands)
    held = set(exclude or ())
    busy = {c['word'] for c in served
            if c['id'] in held or (c.get('at') is None and c['served_at'] >= now_ms - PENDING_MS)}
    left = _left(served, now_ms)
    ready = [(card, w) for name in ('learning', 'review', 'new', 'ahead')
             for card, w in (q[name] if name != 'new' else ((srs.NEW_CARD, w) for w in q['new']))
             if w['ja'] not in busy]
    found = next((t for t in ready if t[1]['ja'] not in left), None) or \
        min(ready, key=lambda t: left[t[1]['ja']], default=None)
    if found is None:
        return None, q['counts']
    real = _deck_card(found[1], found[0], know, now_ms)
    draw = rng.random()
    if draw < PROBE_SHARE + FAKE_SHARE:
        today = knowledge.day_of(now_ms)
        blocked = {fwlib.key(c['word']) for c in served
                   if c['served_at'] >= now_ms - RECENT_HOURS * 3_600_000 or c['id'] in held}
        if draw < PROBE_SHARE:
            hidden = [w for w in state.get('words') or [] if fwlib.phase(w, today) in HIDDEN]
            extra = _probe(state, served, blocked, hidden, know, today, rng, frame, d)
        else:
            extra = _fake(served, blocked, now_ms, rng)
        if extra:  # looks like a new card
            shown = srs.intervals(srs.NEW_CARD, now_ms, srs.card_id(extra['word']))
            return dict(extra, queue='new', intervals=shown), q['counts']
    return real, q['counts']


def _left(served, now_ms):
    """{word: when its card was left}: the words whose latest reviewed learning card (feedback.cards) came back
    without a grade in the last LEFT_HOURS."""
    latest = {}
    for c in served:
        if c['kind'] == 'learning' and c.get('at') is not None and c['at'] >= latest.get(c['word'], (-1, None))[0]:
            latest[c['word']] = (c['at'], c.get('grade'))
    return {word: at for word, (at, grade) in latest.items()
            if grade is None and at >= now_ms - LEFT_HOURS * 3_600_000}


def _deck_card(w, card, know, now_ms):
    """A deck word's card: its word, reading and meaning, entry id, queue and the four intervals."""
    entries = know.get('words') if isinstance(know.get('words'), dict) else {}
    entry = entries.get(w['ja']) if isinstance(entries.get(w['ja']), dict) else {}
    found = entry.get('id') if isinstance(entry.get('id'), int) else w.get('id') if isinstance(w.get('id'), int) \
        else None
    return {'word': w['ja'], 'kind': 'learning', 'kana': (w.get('kana') or '')[:MAX_KANA],
            'gloss': (w.get('en') or '')[:MAX_GLOSS], 'entry': found, 'queue': srs.queue_of(card),
            'intervals': srs.intervals(card, now_ms, srs.card_id(w['ja']))}


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


def _new_order(state, know, served, out, today, now_ms, rng):
    """The learning words not in `out` (the deck's words and the ones marked known), in today's order: those behind on
    showings first, the furthest behind first, then the least certain first."""
    entries = know.get('words') if isinstance(know.get('words'), dict) else {}
    reviewed = {}
    for c in served:
        if c.get('at') is not None:
            reviewed.setdefault(c['word'], []).append(c['at'])
    behind, rest = [], []
    for w in state.get('words') or []:
        if w.get('status') not in ('active', 'shaky') or fwlib.phase(w, today) in HIDDEN:
            continue
        if w['ja'] in out or not 0 < len(w['ja']) <= MAX_JA:
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
    return [t[2] for t in sorted(behind, key=lambda t: t[:2]) + sorted(rest, key=lambda t: t[:2])]


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


# ---- fw.py review: what the schedule looks like ----

def summary(db=None, now_ms=None):
    """fw.py review's numbers (also GET /v1/review/stats): counts (today's, as POST /v1/review/next has them); deck
    (words with a schedule); today and month (cards graded today, Anki's day from 4am, and in the last 30 days, every
    kind); graded (the graded learning cards FSRS replays, all time) and optimize (OPTIMIZE_REVIEWS); retention: the
    review-queue cards' share not Again, [kept, of], in the last 30 days and all time, against desired
    (srs.DESIRED_RETENTION)."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    counts = counts_now(db, now_ms)
    con = feedback.connect(db)
    try:
        decks = schedules(con, db)
        served = feedback.cards(since=now_ms - max(probe.REPEAT_DAYS, FAKE_REUSE_DAYS) * DAY_MS, con=con)
    finally:
        con.close()
    start, month = srs.day_start(srs.day_number(now_ms)), now_ms - 30 * DAY_MS
    graded = [c['at'] + (c['grade_ms'] or 0) for c in served if c.get('grade') is not None]
    log = [entry for _card, entries in decks.values() for entry in entries]
    kept = lambda since: [sum(1 for at, g, s, _ms in log if s == srs.REVIEW and at >= since and g > srs.AGAIN),
                          sum(1 for at, _g, s, _ms in log if s == srs.REVIEW and at >= since)]
    return {'counts': dict(counts), 'deck': len(decks), 'today': sum(1 for at in graded if at >= start),
            'month': sum(1 for at in graded if at >= month), 'graded': len(log), 'optimize': OPTIMIZE_REVIEWS,
            'retention': {'month': kept(month), 'all': kept(0)}, 'desired': srs.DESIRED_RETENTION}


REVLOG_COLUMNS = ('card_id', 'review_time', 'review_rating', 'review_state', 'review_duration')


def revlog(db=None):
    """The graded learning cards as the FSRS optimizer reads a review log (REVLOG_COLUMNS: the card's id, srs.card_id
    of its word; when it was graded, ms; the grade 1..4; the state it was in, 0 new, 1 learning, 2 review, 3
    relearning; ms from shown to the grade), oldest first."""
    con = feedback.connect(db)
    try:
        decks = schedules(con, db)
    finally:
        con.close()
    rows = [(srs.card_id(word), at, grade, srs.REVLOG_STATES[state], ms if ms is not None else '')
            for word, (_card, log) in decks.items() for at, grade, state, ms in log]
    return sorted(rows, key=lambda r: (r[1], r[0]))


def summary_text(found):
    """summary's numbers as fw.py review prints them."""
    counts, retention = found.get('counts') or NO_COUNTS, found.get('retention') or {}
    share = lambda pair: (f'{pair[0] / pair[1]:.0%} of {pair[1]}' if pair and pair[1] else 'none yet')
    lines = [f'Due today: {counts["new"]} new, {counts["learning"]} learning, {counts["review"]} review (Anki\'s day '
             f'starts at {srs.ROLLOVER_HOUR}am; new cards at most {NEW_PER_DAY} a day).',
             f'Graded: {found.get("today", 0)} cards today, {found.get("month", 0)} in the last 30 days; '
             f'{found.get("deck", 0)} words have a schedule.',
             f'Retention of review cards (graded Hard, Good or Easy, not Again): {share(retention.get("month"))} in '
             f'the last 30 days, {share(retention.get("all"))} in all; desired {found.get("desired", 0.9):.0%}.',
             f'FSRS reviews (graded learning cards): {found.get("graded", 0)}.']
    if found.get('graded', 0) < found.get('optimize', OPTIMIZE_REVIEWS):
        lines.append(f'  Scheduling uses FSRS-6\'s default parameters: fitting your own needs on the order of '
                     f'{found.get("optimize", OPTIMIZE_REVIEWS):,} graded reviews.')
    else:
        lines.append('  Enough to fit your own FSRS parameters: `fw.py review revlog > revlog.csv` writes them in the '
                     'FSRS optimizer\'s format (scripts/srs.py PARAMETERS takes the result).')
    return '\n'.join(lines)
