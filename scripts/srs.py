"""Spaced repetition for the review cards: FSRS-6, scheduled the way Anki schedules with it (review.py serves the
cards in Anki's order, feedback.py keeps the grades). Pure functions of a card's state, a grade and a time; a card's
state is the replay of its graded reviews (replay), so a change of parameters recomputes every card. Python 3.9+,
standard library only.

Sources, read on 2026-11-05: py-fsrs 6.3.2 (github.com/open-spaced-repetition/py-fsrs, fsrs/scheduler.py), the
reference implementation, for the formulas, the 21 default parameters and the learning and relearning steps; the FSRS-6
section of "The Algorithm" (github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm, where the fsrs4anki
wiki page now points) for the same-day formula and its floor; and Anki's scheduler (github.com/ankitects/anki, main:
rslib/src/scheduler/states/{learning,review,relearning,steps,fuzz}.rs and answering/{mod,learning}.rs) for what Anki
does around the memory model: day numbers, step delays, graduating intervals, the order of Hard, Good and Easy, fuzz.
tests/test_srs.py checks worked cases computed with py-fsrs's own code (memory states and unfuzzed intervals).

Memory model (FSRS-6; w = PARAMETERS, G the grade: 1 Again, 2 Hard, 3 Good, 4 Easy; S stability in days, D difficulty
1..10, R the chance of recall):
  forgetting curve  R(t, S) = (1 + F t / S)^-w20, F = 0.9^(-1 / w20) - 1, so R(S, S) = 0.9; t in whole days
                    (retrievability: the decay w20 is trainable, the curve's factor follows it)
  first review      S0(G) = w[G - 1]; D0(G) = w4 - e^(w5 (G - 1)) + 1, clamped to 1..10
  difficulty        D' = D + dD (10 - D) / 9, dD = -w6 (G - 3) (linear damping: a change shrinks near 10), then
                    D'' = w7 D0(4) + (1 - w7) D' (mean reversion toward Easy's first difficulty, unclamped), clamped
  recall (G > 1)    S' = S (1 + e^w8 (11 - D) S^-w9 (e^(w10 (1 - R)) - 1) h b), h = w15 for Hard, b = w16 for Easy
  forgetting (G 1)  S' = min(w11 D^-w12 ((S + 1)^w13 - 1) e^(w14 (1 - R)), S / e^(w17 w18))
  same day (t = 0)  S' = S e^(w17 (G - 3 + w18)) S^-w19, the factor at least 1 when G > 1 (short-term stability)
  interval          I(r, S) = S / F (r^(-1 / w20) - 1) days, r the desired retention (DESIRED_RETENTION 0.9: I = S)
Stabilities are floored at 0.001. The difficulty is updated on every review, same-day ones included.

Scheduling, as Anki (v3 scheduler, FSRS on, default deck options):
- Days. Anki's day starts at ROLLOVER_HOUR (4am local time); t is the difference of day numbers (day_number), so a
  review the evening before and one the next morning are a day apart, and two before 4am the same day are 0 days.
- New and learning cards go through LEARNING_STEPS (1m, 10m): Again goes back to the first step, Hard repeats the step
  (on the first step the average of the first two: 5.5m), Good moves to the next step or, past the last, graduates at
  the Good interval (I rounded, at least a day, fuzzed); Easy graduates at the Easy interval, at least a day more than
  the fuzzed Good one. A review card's Again is a lapse: the card relearns through RELEARNING_STEPS (10m; Hard on a
  single step is 1.5 times it, 15m) and graduates as a learning card does. A step that would reach past the next day
  boundary is counted in days instead and the card is due at that day's start (Anki's maybe_as_days).
- A review card's Hard, Good and Easy intervals are each fuzzed, Hard at least 1 day, Good at least Hard + 1, Easy at
  least Good + 1, and none below the previous interval when its own grew (Anki's minimum_review_fuzz_interval);
  at most MAXIMUM_INTERVAL (36500) days. A review card is due from the start of its due day.
- Fuzz (Anki's ranges): none under 2.5 days; otherwise the interval moves within +-(1 + 15% of its days between 2.5
  and 7 + 10% of those between 7 and 20 + 5% of those past 20), rounded, at a point set by a fuzz factor that is a
  pure function of the card's id and its number of reviews so far (deterministic: a replay gives the same days, and
  the intervals shown on the buttons are the ones that will be used). A learning step gets up to 25% more, at most 5
  minutes, from the same factor; the buttons show the step without it, as Anki's do.
- The buttons are what a grade gets (answer's shown): the transition is worked out at the time the buttons were (the
  card's serving), and its interval counted from the grade (Anki answers with the states it showed, and counts a review
  interval from the day of the answer), so a grade given past 4am, or on a card held a while, gets its button's days.
- LEARN_AHEAD_S (20 minutes, Anki's learn-ahead limit): review.py shows a learning card that early when nothing else is
  due, and counts it among the learning cards due.

Where this differs from Anki, and why: the fuzz factor comes from SHA-256 of the card id and review count, not from
Rust's StdRng (ChaCha12) seeded with their sum, so the same history lands on another day of the same range (as uniform
and as stable; matching Anki bit for bit would mean porting ChaCha12 for no gain), and the learning-step fuzz reuses
that factor instead of a second draw; there is no load balancer (Anki 24.11+ can pick, within the fuzz range, the day
with the fewest cards due); and arithmetic is in doubles where Anki's is in single precision. Where py-fsrs differs from
Anki, this follows Anki: py-fsrs counts elapsed days in 24-hour periods (Anki in day numbers at the 4am boundary),
rounds half to even (Anki away from zero), fuzzes with a fresh random draw, and doesn't order Hard < Good < Easy.
"""
import datetime as dt
import hashlib
import math
from typing import NamedTuple, Optional

# FSRS-6's default parameters (py-fsrs DEFAULT_PARAMETERS, fsrs-rs and Anki alike): w0..w3 the first stabilities by
# grade, w4 w5 the first difficulty, w6 w7 the difficulty's step and mean reversion, w8..w10 recall, w11..w14
# forgetting, w15 w16 Hard's penalty and Easy's bonus, w17..w19 same-day reviews, w20 the forgetting curve's decay.
# The optimizer can fit them from the review log (fw.py review revlog) once there are about a thousand graded reviews.
PARAMETERS = (0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796, 1.4835, 0.0614,
              0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542)
DESIRED_RETENTION = 0.9
LEARNING_STEPS = (60, 600)      # seconds: Anki's default 1m 10m
RELEARNING_STEPS = (600,)       # 10m
MAXIMUM_INTERVAL = 36500        # days
ROLLOVER_HOUR = 4               # the next day starts at 4am local time
LEARN_AHEAD_S = 20 * 60         # Anki's learn-ahead limit
STABILITY_MIN = 0.001
DAY_S = 86_400
DAY_MS = DAY_S * 1000
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
GRADES = (AGAIN, HARD, GOOD, EASY)
NAMES = {AGAIN: 'again', HARD: 'hard', GOOD: 'good', EASY: 'easy'}
NEW, LEARNING, REVIEW, RELEARNING = 'new', 'learning', 'review', 'relearning'
QUEUES = {NEW: 'new', LEARNING: 'learning', RELEARNING: 'learning', REVIEW: 'review'}  # what the client is told
REVLOG_STATES = {NEW: 0, LEARNING: 1, REVIEW: 2, RELEARNING: 3}  # the FSRS optimizer's review_state
FUZZ_RANGES = ((2.5, 7.0, 0.15), (7.0, 20.0, 0.1), (20.0, math.inf, 0.05))  # (from, to, share), days
LEARN_FUZZ, LEARN_FUZZ_MAX_S = 0.25, 300


class Card(NamedTuple):
    """A card's scheduling state: state (NEW, LEARNING, REVIEW or RELEARNING), step (the index into the learning or
    relearning steps), stability and difficulty (None while new), last (ms of the last review), due (ms: when a
    learning card is due, a review card's due day's start), interval (a review card's scheduled days), reps (reviews
    so far) and lapses (Again on a review card)."""
    state: str = NEW
    step: int = 0
    stability: Optional[float] = None
    difficulty: Optional[float] = None
    last: Optional[int] = None
    due: Optional[int] = None
    interval: int = 0
    reps: int = 0
    lapses: int = 0


NEW_CARD = Card()


# ---- days ----

def day_number(ms, rollover=ROLLOVER_HOUR):
    """Anki's day of a time (ms): the ordinal of the local date `rollover` hours earlier, so a day runs from 4am to
    4am."""
    return (dt.datetime.fromtimestamp(ms / 1000) - dt.timedelta(hours=rollover)).date().toordinal()


def day_start(number, rollover=ROLLOVER_HOUR):
    """When Anki's day `number` (day_number) starts: its date's `rollover` o'clock local time, in ms."""
    d = dt.date.fromordinal(number)
    return int(dt.datetime(d.year, d.month, d.day, rollover).timestamp() * 1000)


def _round(x):
    """Rust's f32::round for x >= 0: half away from zero (Python's round goes to even)."""
    return int(math.floor(x + 0.5))


# ---- the memory model (FSRS-6) ----

def _curve():
    """(decay, factor) of the forgetting curve: -w20 and 0.9^(1 / decay) - 1."""
    decay = -PARAMETERS[20]
    return decay, 0.9 ** (1 / decay) - 1


def retrievability(elapsed_days, stability):
    """R(t, S): the chance of recall `elapsed_days` (whole days, never negative) after a review that left
    `stability`."""
    decay, factor = _curve()
    return (1 + factor * max(0, elapsed_days) / stability) ** decay


def next_interval(stability, retention=None):
    """I(r, S): the days until recall falls to the desired retention (DESIRED_RETENTION), unrounded."""
    decay, factor = _curve()
    retention = DESIRED_RETENTION if retention is None else retention
    return stability / factor * (retention ** (1 / decay) - 1)


def initial_stability(grade):
    return max(STABILITY_MIN, PARAMETERS[grade - 1])


def initial_difficulty(grade, clamp=True):
    w = PARAMETERS
    d = w[4] - math.exp(w[5] * (grade - 1)) + 1
    return min(max(d, 1.0), 10.0) if clamp else d


def next_difficulty(difficulty, grade):
    """The difficulty after a review: the step -w6 (G - 3) damped linearly toward 10, then reverted toward D0(4) by w7,
    clamped to 1..10."""
    w = PARAMETERS
    damped = difficulty + (-w[6] * (grade - 3)) * (10.0 - difficulty) / 9.0
    return min(max(w[7] * initial_difficulty(EASY, clamp=False) + (1 - w[7]) * damped, 1.0), 10.0)


def recall_stability(difficulty, stability, r, grade):
    w = PARAMETERS
    return stability * (1 + math.exp(w[8]) * (11 - difficulty) * stability ** -w[9] * (math.exp((1 - r) * w[10]) - 1)
                        * (w[15] if grade == HARD else 1.0) * (w[16] if grade == EASY else 1.0))


def forget_stability(difficulty, stability, r):
    w = PARAMETERS
    long_term = w[11] * difficulty ** -w[12] * ((stability + 1) ** w[13] - 1) * math.exp((1 - r) * w[14])
    return min(long_term, stability / math.exp(w[17] * w[18]))


def short_term_stability(stability, grade):
    """A same-day review's stability: S e^(w17 (G - 3 + w18)) S^-w19, the factor floored at 1 for Hard, Good, Easy."""
    w = PARAMETERS
    factor = math.exp(w[17] * (grade - 3 + w[18])) * stability ** -w[19]
    return stability * (max(factor, 1.0) if grade > AGAIN else factor)


def memory(card, grade, at):
    """(stability, difficulty) after grading `card` at `at` (ms): the first review's, a same-day review's (0 days since
    the last, by day_number) or a later one's, recall or forgetting at its retrievability."""
    if card.stability is None or card.difficulty is None or card.last is None:
        return initial_stability(grade), initial_difficulty(grade)
    elapsed = day_number(at) - day_number(card.last)
    if elapsed <= 0:
        s = short_term_stability(card.stability, grade)
    else:
        r = retrievability(elapsed, card.stability)
        s = forget_stability(card.difficulty, card.stability, r) if grade == AGAIN else \
            recall_stability(card.difficulty, card.stability, r, grade)
    return max(STABILITY_MIN, s), next_difficulty(card.difficulty, grade)


def next_states(card, at):
    """{grade: (stability, difficulty, interval days unrounded)} for each grade at `at` (fsrs-rs next_states, which
    Anki's scheduler reads)."""
    out = {}
    for g in GRADES:
        s, d = memory(card, g, at)
        out[g] = (s, d, next_interval(s))
    return out


# ---- Anki's scheduling ----

def card_id(word):
    """A card's id for the fuzz and the review log: a stable 48-bit number from its word."""
    return int(hashlib.sha256(word.encode('utf-8')).hexdigest()[:12], 16)


def fuzz_factor(cid, reps):
    """The fuzz factor in [0, 1) for a card with `reps` reviews so far (Anki seeds it with card id + reps)."""
    return int(hashlib.sha256(f'fsrs-fuzz:{cid}:{reps}'.encode('ascii')).hexdigest()[:13], 16) / 16 ** 13


def fuzz_bounds(interval, minimum=1, maximum=MAXIMUM_INTERVAL):
    """Anki's constrained_fuzz_bounds: the (lower, upper) days a fuzzed interval may land on, within minimum..maximum,
    with room for two days whenever there is any."""
    minimum = min(minimum, maximum)
    interval = min(max(interval, minimum), maximum)
    delta = 0.0 if interval < 2.5 else 1.0 + sum(share * max(min(interval, end) - start, 0.0)
                                                 for start, end, share in FUZZ_RANGES)
    lower = min(max(_round(interval - delta), minimum), maximum)
    upper = min(max(_round(interval + delta), minimum), maximum)
    if upper == lower and 2 < upper < maximum:
        upper = lower + 1
    return lower, upper


def fuzzed(u, interval, minimum=1, maximum=MAXIMUM_INTERVAL):
    """Anki's with_review_fuzz after min_and_max_review_intervals: whole days within minimum (at least 1)..maximum,
    at fuzz factor u (None: no fuzz, the interval rounded)."""
    maximum = max(maximum, 1)
    minimum = min(max(minimum, 1), maximum)
    if u is None:
        return min(max(_round(interval), minimum), maximum)
    lower, upper = fuzz_bounds(interval, minimum, maximum)
    return int(math.floor(lower + u * (1 + upper - lower)))


def least_review_interval(interval, previous, maximum=MAXIMUM_INTERVAL):
    """Anki's minimum_review_fuzz_interval: a grown interval stays above the previous one, one that barely moved
    doesn't fall below it, a shrunk one may (0)."""
    _lower, upper = fuzz_bounds(interval, 1, maximum)
    if _round(interval) > previous:
        return previous + 1
    return previous if previous <= upper else 0


def _whole_days(secs):
    """Anki's maybe_round_in_days: a delay over a day is rounded to whole days."""
    return _round(secs / DAY_S) * DAY_S if secs > DAY_S else secs


def hard_delay(steps, step):
    """Hard's delay on a step: the step again, except on the first: the average of the first two steps, or 1.5 times a
    single step (at most a day more)."""
    if step > 0:
        return steps[min(step, len(steps) - 1)]
    if len(steps) > 1:
        return _whole_days((steps[0] + steps[1]) // 2)
    return _whole_days(min(steps[0] * 3 // 2, steps[0] + DAY_S))


def schedule(card, grade, states, u):
    """What grading does, Anki's way: (the next Card without last and due, the delay ('secs', seconds) or ('days',
    days)). states: next_states(card, at); u: the fuzz factor (None: no fuzz)."""
    s, d, _i = states[grade]
    kept = dict(stability=s, difficulty=d, reps=card.reps + 1, lapses=card.lapses)
    good, easy = states[GOOD][2], states[EASY][2]

    def graduated(days):
        return Card(state=REVIEW, step=0, interval=days, **kept), ('days', days)

    if card.state in (NEW, LEARNING, RELEARNING):
        steps = RELEARNING_STEPS if card.state == RELEARNING else LEARNING_STEPS
        state = RELEARNING if card.state == RELEARNING else LEARNING
        step = 0 if card.state == NEW else min(card.step, len(steps) - 1)
        if grade == AGAIN:
            return Card(state=state, step=0, interval=card.interval, **kept), ('secs', steps[0])
        if grade == HARD:
            return Card(state=state, step=step, interval=card.interval, **kept), ('secs', hard_delay(steps, step))
        if grade == GOOD:
            if step + 1 < len(steps):
                return Card(state=state, step=step + 1, interval=card.interval, **kept), ('secs', steps[step + 1])
            return graduated(fuzzed(u, max(1, _round(good))))
        least = fuzzed(u, good) + 1
        return graduated(fuzzed(u, max(1, _round(easy)), least))
    if grade == AGAIN:  # a lapse: relearn
        kept['lapses'] += 1
        return Card(state=RELEARNING, step=0, interval=card.interval, **kept), ('secs', RELEARNING_STEPS[0])
    previous = card.interval
    hard = fuzzed(u, states[HARD][2], max(1, least_review_interval(states[HARD][2], previous)))
    good_days = fuzzed(u, good, max(hard + 1, least_review_interval(good, previous)))
    if grade == HARD:
        return graduated(hard)
    if grade == GOOD:
        return graduated(good_days)
    return graduated(fuzzed(u, easy, max(good_days + 1, least_review_interval(easy, previous))))


def _as_days(secs, at):
    """A learning delay that reaches the next day boundary, in days (Anki's maybe_as_days), else None."""
    until = (day_start(day_number(at) + 1) - at) // 1000
    return (secs - until) // DAY_S + 1 if secs >= until else None


def learn_fuzz(u, secs):
    """A learning step with Anki's fuzz: up to 25% more, at most 5 minutes, at fuzz factor u."""
    extra = int(math.floor(min(secs * LEARN_FUZZ, LEARN_FUZZ_MAX_S)))
    return secs if u is None or extra <= 0 else secs + int(math.floor(u * extra))


def answer(card, grade, at, cid, fuzz=True, shown=None):
    """The card after grading it `grade` (1..4) at `at` (ms): the memory state, the queue state and when it is due.
    cid: card_id; fuzz False: no fuzz at all (to compare with an unfuzzed reference). shown: when its buttons were
    worked out (intervals), if earlier: the memory state and the delay are the ones they showed, counted from the
    grade (Anki answers with the state its buttons were computed from, and counts a review interval from the day of
    the answer), so a grade given past 4am, or on a card held a while, gets what its button said."""
    u = fuzz_factor(cid, card.reps) if fuzz else None
    then = at if shown is None else min(at, max(shown, card.last if card.last is not None else shown))
    nxt, (unit, n) = schedule(card, grade, next_states(card, then), u)
    today = day_number(at)
    if unit == 'secs':
        days = _as_days(n, then)
        due = day_start(today + days) if days is not None else at + 1000 * learn_fuzz(u, n)
    else:
        due = day_start(today + n)
    return nxt._replace(last=at, due=due)


def intervals(card, at, cid, fuzz=True):
    """What Anki prints above its buttons, in seconds: {again, hard, good, easy} until the card would come back. A
    learning step without its fuzz (or in days when it reaches the next day), a review interval in days times 86,400
    (fuzzed: the days it will get)."""
    u = fuzz_factor(cid, card.reps) if fuzz else None
    states = next_states(card, at)
    out = {}
    for g in GRADES:
        _card, (unit, n) = schedule(card, g, states, u)
        days = _as_days(n, at) if unit == 'secs' else n
        out[NAMES[g]] = n if days is None else days * DAY_S
    return out


def replay(reviews, cid, fuzz=True):
    """A card's state after its graded reviews, [(at ms, grade)] or [(at, grade, shown: when its buttons were worked
    out)] in any order (sorted here by time, ties kept in the order given), and its review log: [(at, grade, the state
    it was in)]. No reviews: NEW_CARD."""
    card, log = NEW_CARD, []
    for review in sorted(reviews, key=lambda r: r[0]):
        at, grade = review[0], review[1]
        log.append((at, grade, card.state))
        card = answer(card, grade, at, cid, fuzz, review[2] if len(review) > 2 else None)
    return card, log


def queue_of(card):
    """The queue a client is told: 'new', 'learning' (learning or relearning) or 'review'."""
    return QUEUES[card.state]


def is_due(card, now_ms, ahead_s=0):
    """Whether a card that has been reviewed is due at now_ms (a learning card up to ahead_s early; a review card from
    its due day's start, so any time on that day)."""
    return card.state != NEW and card.due is not None and card.due <= now_ms + ahead_s * 1000
