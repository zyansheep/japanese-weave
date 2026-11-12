"""Review cards, scheduled the way Anki schedules them (FSRS-6, srs.py). T3 Code's review panel (desktop sidebar) and
the phone's Review screen show one Japanese word alone; the reader reveals its reading and meaning, then grades it
with Anki's four buttons (Again, Hard, Good, Easy), or leaves it. The client asks the collector for each card (POST
/v1/review/next, collector.py) and reports what happened as a review item (POST /v1/interactions, feedback.py): the
time to the reveal, the grade and the time to it. Python 3.9+, standard library only.

  POST /v1/review/next  {"source": "desktop"|"mobile", "exclude"?: [up to EXCLUDE_MAX card ids the client holds],
                         "more"?: true to lift today's new-card limit for this request,
                         "peek"?: true for the counts alone (a badge): card null, nothing served,
                         "release"?: [up to EXCLUDE_MAX card ids served to this client and never shown],
                         "newPerDay"?: 0..fwlib.REVIEW_NEW_MAX, the reader's new cards a day, set before answering}
                        -> {"ok": true, "card": {"id", "ja", "kana", "gloss", "queue": "new"|"learning"|"review",
                            "intervals": {"again", "hard", "good", "easy"}} or null,
                            "counts": {"new", "learning", "review"}, "newPerDay": the limit in effect}

intervals: the seconds until the card would come back after each grade, what Anki prints above its buttons (srs.
intervals). counts: Anki's three numbers: new cards left today within the day's limit, learning cards due now or
within the learn-ahead window (srs.LEARN_AHEAD_S, 20 minutes), review cards due today. card null with every count 0:
done for today (the client offers "Review more", which asks again with more). card null with a count left happens only
while the cards counted are held by the client or pending (below); the client asks again later. peek answers the
counts alone, as the same request without it would have them, and serves nothing: no cards row is written, so no word
is held back as pending and no made-up or probe word is used up (T3 Code's header badge and the phone's badge poll
it). release gives back cards served to this client (source) and never shown, a dropped prefetch (feedback.
release_cards): they are no longer pending, never count as left, and nothing reads them (no probe or made-up word used
up, no showing); it is applied first, with peek too. A review item for a released card is rejected.

The deck: every word with a graded learning card (its schedule: the replay of its graded reviews in time order, srs.
replay, each from the time its buttons were worked out, so the grade gets what its button showed; kept under the
word's dictionary entry id when its cards had one, else its spelling, feedback.deck_key; cached per database until
another graded card is stored: schedules), plus the new cards: the learning words
(status learning) without one. A word the reader marked known themselves (the known command or op: its latest known
or learn command is known and its status still is; known_by_command) leaves the deck; a word the model labels known
keeps its schedule, since the model moving on says nothing about recall; a later learn command (or the model labelling
it learning again) brings it back with its schedule. A word the delayed-start experiment hides or is about to probe
(fwlib.phase held, paused or probe) is never served, nor counted, as before. Words are one card by deck key: two
spellings of one entry (a kanji spelling and its kana one) give one new card and share one schedule, and a spelling
whose cards have a schedule is never new again. Text is cut to its limits as T3 Code counts length (UTF-16 code
units: cut), so a character beyond the BMP at a limit can't fail its check.

Queue, Anki's order (v3, default options): first the learning and relearning cards due now, the earliest due first; then
the review cards due today (Anki's day starts at 4am: srs.day_number), by due day, then in an order fixed for the day (a
hash of the day and the word, so ties don't reshuffle between requests); then new cards while today's limit lasts
(the reader's pref review_new_per_day, set from T3 Code's review menu as newPerDay or by fw.py review new N, else
fwlib.REVIEW_NEW_DEFAULT, 10: a word is new on the day of its first graded review, and a word served as new today
whose review hasn't come back yet counts against the limit too, so a grade in flight can't let one more in), in
today's order: the words that come up most in the reader's text first (times their dictionary entry came up in the
replies and pages read in the last fwlib.READING_DAYS days, exposure.json's reading counts: reading_counts), so the
cards teach the words the reader keeps meeting; among words that came up equally often (none at all, say), the words
behind on showings first (in their first BEHIND_DAYS days in use, from the delayed-start draw's time, else the day the
word became learning, fewer than BEHIND_PER_DAY showings a day so far: the gloss log's, knowledge.json "history", and
their review cards; the furthest behind first), then the least certain (the highest sd in knowledge.json); ties
broken by the same daily hash; last, when nothing else is due, a learning card due within the learn-ahead
window. more lifts the day's limit for one request (Anki's custom study "increase today's new card limit", without
remembering it). Words of the cards in exclude are skipped, and so are words served in the last PENDING_MS whose review
hasn't come back yet (a grade sent through T3 Code's batched events can arrive after the next request, which would
otherwise show the same word again); neither is taken off the counts. A card left without a grade schedules nothing and
stays due, but for LEFT_HOURS it waits behind every other card that can be served (the longest left first): a reader who
closed the panel on it sees it again, and a client from before grades, which leaves every card, still moves through the
words instead of getting the same one again. RECENT_HOURS no longer applies to these cards: learning steps come back
within minutes.

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

NEW_PER_DAY = fwlib.REVIEW_NEW_DEFAULT  # new cards a day without the reader's own (fwlib.review_new_per_day)
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
        return ('need {"source": "desktop"|"mobile", "exclude"?: [card ids], "more"?: true|false, "peek"?: true|false, '
                '"release"?: [card ids]}')
    for name in ('exclude', 'release'):
        ids = body.get(name)
        if ids is not None and (not isinstance(ids, list) or len(ids) > EXCLUDE_MAX or not all(ident(x) for x in ids)):
            return f'{name}: a list of at most {EXCLUDE_MAX} card ids'
    for name in ('more', 'peek'):
        if body.get(name) is not None and not isinstance(body[name], bool):
            return f'{name}: true or false'
    if body.get('newPerDay') is not None and not fwlib.valid_review_new(body['newPerDay']):
        return f'newPerDay: a whole number 0..{fwlib.REVIEW_NEW_MAX}'
    return None


_READING = {}


def reading_counts(path=None):
    """{dictionary entry id (str): times it came up in the reader's text over the last fwlib.READING_DAYS days}, from
    the exposure index's reading counts (exposure.json, fwlib.fold), cached while the file is unchanged; {} without
    one."""
    path = path or fwlib.home() / 'exposure.json'
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    if _READING.get('path') != str(path) or _READING.get('stamp') != stamp:
        index = fwlib.read_json(path, {}) or {}
        first = fwlib.add_days(fwlib.today(), 1 - fwlib.READING_DAYS)
        out = {}
        for day, counts in (index.get('reading') or {}).items() if isinstance(index, dict) else ():
            if isinstance(day, str) and day >= first and isinstance(counts, dict):
                for id_, n in counts.items():
                    if _number(n) and n > 0:
                        out[str(id_)] = out.get(str(id_), 0) + n
        _READING.update(path=str(path), stamp=stamp, counts=out)
    return _READING['counts']


def schedules(con, path=None):
    """{key: (srs.Card, review log, word, cid, words)} for every word with a graded learning card, under
    feedback.deck_key (its entry id, else its spelling; feedback.deck_groups: one group per word, spellings and ids
    joined): its schedule, the replay of its graded reviews in the order they were graded (feedback.graded; a review's
    time is when the grade was given, worked out from when the card was served, its buttons' time), the log, [(reviewed
    ms, grade, the state it was in, grade_ms)], its latest spelling, its card id (srs.card_id of its first graded card's
    spelling, as a new card's buttons have it: stable when the word gains or changes its entry id), and every spelling
    its cards had. Cached per database (path) under feedback.graded_mark and the scheduler's settings, so storing a
    grade, or changing a parameter, recomputes them."""
    key = (feedback.graded_mark(con), srs.PARAMETERS, srs.DESIRED_RETENTION, srs.LEARNING_STEPS, srs.RELEARNING_STEPS,
           srs.MAXIMUM_INTERVAL, srs.ROLLOVER_HOUR)
    name = str(path or feedback.default_path())
    with _SCHEDULES_LOCK:
        found = _SCHEDULES.get(name)
    if found and found[0] == key:
        return found[1]
    out = {}
    for key, rows in feedback.deck_groups(feedback.graded(con=con)).items():
        cid = srs.card_id(rows[0]['word'])
        card, log = srs.replay([(r['reviewed'], r['grade'], r['served']) for r in rows], cid)
        out[key] = (card, [(at, grade, state, r['grade_ms']) for (at, grade, state), r in zip(log, rows)],
                    rows[-1]['word'], cid, frozenset(r['word'] for r in rows))
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


def entry_of(w, know):
    """A word state entry's dictionary entry id, as a card served for it records it (knowledge.json's, else the word
    state's), or None."""
    entries = know.get('words') if isinstance(know.get('words'), dict) else {}
    entry = entries.get(w['ja']) if isinstance(entries.get(w['ja']), dict) else {}
    return entry.get('id') if isinstance(entry.get('id'), int) else w.get('id') if isinstance(w.get('id'), int) \
        else None


def daily(day, word):
    """A tie-break fixed for the day: the same order on every request of Anki's day `day`."""
    return fwlib.draw(f'review:{day}', word)


def queues(state, know, served, decks, now_ms, rng=None, more=False, commands=(), reading=None):
    """Today's queues and counts, as the module docstring orders them: {learning: [(card, word, cid)] due now,
    earliest first; review: [(card, word, cid)] due today; new: [word] in today's order, one a deck key (two spellings
    of one entry are one card) and none whose key or spelling has a schedule, within today's room (all with more), less
    the words served as new and still pending; ahead: [(card, word, cid)] learning cards due within the learn-ahead
    window; counts: {new, learning, review}; introduced: words whose first graded review was today; pending: words
    served as new today, their review not back}. word: the word state's entry; cid: the card id the fuzz uses.
    served: feedback.cards (released ones are left out here). rng: unused (the order is fixed per day). reading:
    reading_counts (None: none, the order as before reading counts)."""
    today, day = knowledge.day_of(now_ms), srs.day_number(now_ms)
    served = [c for c in served if c.get('released') is None]
    out = {'learning': [], 'review': [], 'new': [], 'ahead': [], 'introduced': 0}
    by_ja = {w['ja']: w for w in (state or {}).get('words') or ()
             if isinstance(w, dict) and isinstance(w.get('ja'), str)}
    by_entry = {}
    for w in by_ja.values():
        entry = entry_of(w, know)
        if entry is not None:
            by_entry.setdefault(entry, w)
    mine = known_by_command(state, commands)
    spellings = {word for _card, _log, _spelling, _cid, words in decks.values() for word in words}
    for key, (card, log, spelling, cid, words) in decks.items():
        if log and srs.day_number(log[0][0]) == day:
            out['introduced'] += 1
        w = by_ja.get(spelling) or (by_entry.get(int(key[6:])) if key.startswith('entry:') else None) or \
            next((by_ja[s] for s in sorted(words) if s in by_ja), None)  # the latest graded spelling shown, if it can be
        if w is None:
            continue
        ja = w['ja']
        if ja in mine or fwlib.phase(w, today) in HIDDEN or not 0 < utf16(ja) <= MAX_JA:
            continue
        if card.state == srs.REVIEW:
            if srs.is_due(card, now_ms):
                out['review'].append((srs.day_number(card.due), daily(day, ja), card, w, cid))
        elif srs.is_due(card, now_ms):
            out['learning'].append((card.due, card, w, cid))
        elif srs.is_due(card, now_ms, srs.LEARN_AHEAD_S):
            out['ahead'].append((card.due, card, w, cid))
    out['learning'] = [t[1:] for t in sorted(out['learning'], key=lambda t: (t[0], t[3]))]
    out['ahead'] = [t[1:] for t in sorted(out['ahead'], key=lambda t: (t[0], t[3]))]
    out['review'] = [t[2:] for t in sorted(out['review'], key=lambda t: t[:2])]
    fresh, keys = [], set()
    for w in _new_order(state, know, served, mine, today, now_ms, day, reading):  # one card a word: by deck key, as the deck
        key = feedback.deck_key(entry_of(w, know), w['ja'])
        if key not in decks and key not in keys and w['ja'] not in spellings:
            keys.add(key)
            fresh.append(w)
    pending = {c['word'] for c in served if c['kind'] == 'learning' and c.get('at') is None
               and c['served_at'] >= now_ms - PENDING_MS and srs.day_number(c['served_at']) == day}
    out['pending'] = {w['ja'] for w in fresh if w['ja'] in pending}
    fresh = [w for w in fresh if w['ja'] not in out['pending']]
    room = len(fresh) if more else max(0, fwlib.review_new_per_day(state) - out['introduced'] - len(out['pending']))
    out['new'] = fresh[:room]
    out['counts'] = {'new': len(out['new']), 'learning': len(out['learning']) + len(out['ahead']),
                     'review': len(out['review'])}
    return out


def next_card(source, exclude=(), db=None, now_ms=None, rng=None, more=False, peek=False, release=(), reading=None):
    """The next card for a client, served and recorded here, and today's counts: ({id, ja, kana, gloss, queue,
    intervals} or None, {new, learning, review}). peek: (None, the counts), nothing served or written. release: card
    ids served to this source and never shown, given back first (feedback.release_cards). reading: reading_counts
    (default: this machine's). The word state must exist (the collector answers 409 before asking)."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rng = rng or random.SystemRandom()
    since = now_ms - max(probe.REPEAT_DAYS, FAKE_REUSE_DAYS) * DAY_MS
    if release:
        feedback.release_cards(release, source, now_ms, db)
    reading = reading_counts() if reading is None else reading
    if peek:
        return None, counts_now(db, now_ms, more, reading)
    with SERVING:
        state = fwlib.load_state(create=False)
        con = feedback.connect(db)
        try:
            decks = schedules(con, db)
            card, counts = choose(state, knowledge.load(), feedback.cards(since=since, con=con), decks, exclude,
                                  now_ms, rng, more=more, commands=knowledge.read_commands(), reading=reading)
            if card is None:
                return None, counts
            card = dict(card, id=secrets.token_hex(8), served_at=now_ms, source=source)
            feedback.serve_card(card, con=con)
        finally:
            con.close()
    return {k: card[v] for k, v in (('id', 'id'), ('ja', 'word'), ('kana', 'kana'), ('gloss', 'gloss'),
                                    ('queue', 'queue'), ('intervals', 'intervals'))}, counts


def counts_now(db=None, now_ms=None, more=False, reading=None):
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
    return queues(state, knowledge.load(), served, decks, now_ms, None, more, knowledge.read_commands(),
                  reading_counts() if reading is None else reading)['counts']


def choose(state, know, served, decks, exclude=(), now_ms=None, rng=None, frame=None, d=None, more=False,
           commands=(), reading=None):
    """The next card ({word, kind, kana, gloss, queue, intervals, entry?, band?, weight?}) or None, and today's counts,
    from the whole word state, knowledge.json, served (feedback.cards of the last probe.REPEAT_DAYS days at least),
    decks (schedules) and the known and learn commands, as the module docstring says."""
    if not isinstance(state, dict) or state.get('paused'):
        return None, dict(NO_COUNTS)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rng = rng or random.SystemRandom()
    served = [c for c in served if c.get('released') is None]  # given back unshown: as if never served
    q = queues(state, know, served, decks, now_ms, rng, more, commands, reading)
    held = set(exclude or ())
    busy = {c['word'] for c in served
            if c['id'] in held or (c.get('at') is None and c['served_at'] >= now_ms - PENDING_MS)}
    left = _left(served, now_ms)
    ready = [(card, w, cid) for name in ('learning', 'review', 'new', 'ahead')
             for card, w, cid in (q[name] if name != 'new' else
                                  ((srs.NEW_CARD, w, srs.card_id(w['ja'])) for w in q['new']))
             if w['ja'] not in busy]
    found = next((t for t in ready if t[1]['ja'] not in left), None) or \
        min(ready, key=lambda t: left[t[1]['ja']], default=None)
    if found is None:
        return None, q['counts']
    real = _deck_card(found[1], found[0], found[2], know, now_ms)
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


def utf16(text):
    """A string's length as T3 Code counts it (UTF-16 code units: a character beyond the BMP is two)."""
    return len(text.encode('utf-16-le')) // 2


def cut(text, limit):
    """`text` cut to at most `limit` UTF-16 code units, never inside a character (T3 Code checks lengths so)."""
    text = text or ''
    if utf16(text) <= limit:
        return text
    out, used = [], 0
    for ch in text:
        used += 2 if ord(ch) > 0xFFFF else 1
        if used > limit:
            break
        out.append(ch)
    return ''.join(out)


def _deck_card(w, card, cid, know, now_ms):
    """A deck word's card: its word, reading and meaning, entry id, queue and the four intervals (cid: the card id the
    fuzz uses, the replay's)."""
    return {'word': w['ja'], 'kind': 'learning', 'kana': cut(w.get('kana'), MAX_KANA),
            'gloss': cut(w.get('en'), MAX_GLOSS), 'entry': entry_of(w, know), 'queue': srs.queue_of(card),
            'intervals': srs.intervals(card, now_ms, cid)}


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


def _new_order(state, know, served, out, today, now_ms, day, reading=None):
    """The learning words not in `out` (the deck's words and the ones marked known), in today's order: the ones that
    came up most in the reader's text first (reading: reading_counts, by the word's entry id; a number counts none,
    since its count is the digits read: probe.is_number), then, among equals,
    those behind on showings, the furthest behind first, then the least certain first; ties by a hash fixed for Anki's
    day `day` (daily)."""
    entries = know.get('words') if isinstance(know.get('words'), dict) else {}
    reviewed = {}
    for c in served:
        if c.get('at') is not None:
            reviewed.setdefault(c['word'], []).append(c['at'])
    order = []
    for w in state.get('words') or []:
        if w.get('status') not in ('active', 'shaky') or fwlib.phase(w, today) in HIDDEN:
            continue
        if w['ja'] in out or not 0 < utf16(w['ja']) <= MAX_JA:
            continue
        entry = entries.get(w['ja']) if isinstance(entries.get(w['ja']), dict) else {}
        id_ = entry_of(w, know)
        times = (reading or {}).get(str(id_), 0) if id_ is not None else 0
        if times and probe.is_number({'ja': w['ja'], 'en': w.get('en') or ''}):
            times = 0  # a number's entry counts the digits read in English text (6 for 六), not its kanji
        start = learning_start(w)
        if start is not None and 0 <= now_ms - start < BEHIND_DAYS * DAY_MS:
            days = max(1.0, (now_ms - start) / DAY_MS)
            shown = sum(1 for h in entry.get('history') or () if isinstance(h, dict) and _number(h.get('at'))
                        and h['at'] >= start) + sum(1 for at in reviewed.get(w['ja'], ()) if at >= start)
            if shown < BEHIND_PER_DAY * days:
                order.append((-times, 0, shown / days, daily(day, w['ja']), w))
                continue
        sd = entry.get('sd') if _number(entry.get('sd')) else math.inf  # no estimate yet: the least certain
        order.append((-times, 1, -sd, daily(day, w['ja']), w))
    return [t[-1] for t in sorted(order, key=lambda t: t[:-1])]


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
                and utf16(it['ja']) <= MAX_JA]
        while pool:
            it = probe.pick(pool, 1, last, today, probe.REPEAT_DAYS, rng, key=lambda it: it['ja'])[0]
            e = d.get(it['ids'][0])
            if e is not None and fwlib.key(e['kana']) not in readings:
                return {'word': it['ja'], 'kind': 'probe', 'kana': cut(e['kana'], MAX_KANA),
                        'gloss': cut(e['en'], MAX_GLOSS),
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
    return {'word': ja, 'kind': 'fake', 'kana': cut(kana, MAX_KANA), 'gloss': MADE_UP}


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
    log = [entry for _card, entries, _word, _cid, _words in decks.values() for entry in entries]
    kept = lambda since: [sum(1 for at, g, s, _ms in log if s == srs.REVIEW and at >= since and g > srs.AGAIN),
                          sum(1 for at, _g, s, _ms in log if s == srs.REVIEW and at >= since)]
    return {'counts': dict(counts), 'newPerDay': fwlib.review_new_per_day(fwlib.load_state(create=False)),
            'deck': len(decks), 'today': sum(1 for at in graded if at >= start),
            'month': sum(1 for at in graded if at >= month), 'graded': len(log), 'optimize': OPTIMIZE_REVIEWS,
            'retention': {'month': kept(month), 'all': kept(0)}, 'desired': srs.DESIRED_RETENTION}


REVLOG_COLUMNS = ('card_id', 'review_time', 'review_rating', 'review_state', 'review_duration')


def revlog(db=None):
    """The graded learning cards as the FSRS optimizer reads a review log (REVLOG_COLUMNS: the card's id, the one
    its fuzz uses (schedules); when it was graded, ms; the grade 1..4; the state it was in, 0 new, 1 learning, 2
    review, 3 relearning; ms from shown to the grade), oldest first."""
    con = feedback.connect(db)
    try:
        decks = schedules(con, db)
    finally:
        con.close()
    rows = [(cid, at, grade, srs.REVLOG_STATES[state], ms if ms is not None else '')
            for _card, log, _word, cid, _words in decks.values() for at, grade, state, ms in log]
    return sorted(rows, key=lambda r: (r[1], r[0]))


def summary_text(found):
    """summary's numbers as fw.py review prints them."""
    counts, retention = found.get('counts') or NO_COUNTS, found.get('retention') or {}
    share = lambda pair: (f'{pair[0] / pair[1]:.0%} of {pair[1]}' if pair and pair[1] else 'none yet')
    lines = [f'Due today: {counts["new"]} new, {counts["learning"]} learning, {counts["review"]} review (Anki\'s day '
             f'starts at {srs.ROLLOVER_HOUR}am; new cards at most {found.get("newPerDay", NEW_PER_DAY)} a day).',
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
