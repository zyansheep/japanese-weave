"""Intensity: the reader turns the Japanese heavier or lighter for a while, separately in chat replies ('chat') and on
web pages ('browser'), with a keyboard shortcut (T3 Code, the extension), the popup or `fw.py intensity`. Python 3.9+,
standard library only.

A level is -3..3 and lasts DURATION_MS after the last change, then it is back to normal (0). -2..2 are lookup budgets;
the ends are modes of their own: -3 is off (no Japanese) and 3 is full (all the Japanese the reader can follow: the
same choices with no budget). Each change is a `set` op on the pref intensity_chat or
intensity_browser ({level, until, at, source}), so the collector's ops table keeps every press: how often and when the
reader turns it down or up is the evidence for whether the default amount is right (knowledge.py calibration)."""
import time

SURFACES = ('chat', 'browser')
OFF, FULL = -3, 3  # the ends: no Japanese, and all the Japanese the reader can follow
LEVELS = (OFF, -2, -1, 0, 1, 2, FULL)
# Times the stage's (or the popup's) amount of Japanese. Full is a mode, not an amount: as heavy as 2 where a factor
# is all that fits.
FACTORS = {OFF: 0.0, -2: 0.5, -1: 0.7, 0: 1.0, 1: 1.4, 2: 1.8, FULL: 1.8}
NAMES = {OFF: 'off', -2: 'much lighter', -1: 'lighter', 0: 'normal', 1: 'heavier', 2: 'much heavier', FULL: 'full'}
# What a level means: the lookups per 100 words read the reader puts up with, spent on words they don't know at a
# glance yet. Words they do know (a predicted lookup chance under fwlib.FREE_P) cost nothing and are used wherever
# they fit, at every level but off. Normal starts at the chat's lookup rate before levels meant this (0.33 per 100
# words shown, 2026-10-20). Off has no budget and full no limit; the popup's challenge, when set, replaces normal's
# budget and scales the others with it.
BUDGETS = {-2: 0.15, -1: 0.3, 0: 0.5, 1: 0.8, 2: 1.2}
PER_SENTENCE = {-2: 1, -1: 1, 0: 1, 1: 2, 2: 2}  # most budgeted (not-yet-known) words in one sentence
# A whole sentence in Japanese costs its translation lookups: the chance a reader who reads it opens its translation,
# whatever words it uses. knowledge.json's sentencePrice per surface once a fit makes one (sentence_price), else this,
# as levels.js SENTENCE_PRICE says (keep them the same). Measured on chat, 2026-10-15 to 20: of the 439 sentence ends in
# the 123 T3 replies with a word count, 24 were opened, 5.5% a sentence shown, 8.4% a sentence read when each is weighed
# by its read chance (90% interval 5.4% to 11.4%).
SENTENCE_PRICE = 0.08
# The most of a level's budget whole sentences may take, on the web and in chat alike (split): learning words get the
# rest, and what one of the two can't use goes to the other. Chosen so normal asks for as many sentences as the chat
# brief did at Stage 3 before sentences were priced: one in five, about 1.1 per 100 words (about 5.5 prose sentences
# in 100 words), which at 0.08 a sentence is 0.088 of normal's 0.5, a share of 0.176. At 0.18, normal pays for
# 0.18 * 0.5 / 0.08 = 1.1 sentences per 100 words; much lighter for 0.34 and much heavier for 2.7.
SENTENCE_SHARE = 0.18
DURATION_MS = 3 * 3600 * 1000
NORMAL = {'level': 0, 'until': 0, 'at': 0}


def now_ms():
    return int(time.time() * 1000)


def pref(surface):
    return f'intensity_{surface}'


def valid(value):
    """A stored intensity: {level, until, at, source?} (ops arrive over the network)."""
    whole = lambda x: isinstance(x, int) and not isinstance(x, bool)  # True == 1 and 2.0 == 2, but neither is a level
    integer = lambda x: whole(x) and 0 <= x <= 10 ** 14
    return (isinstance(value, dict) and set(value) <= {'level', 'until', 'at', 'source'} and whole(value.get('level'))
            and value['level'] in LEVELS and integer(value.get('until')) and integer(value.get('at'))
            and (value.get('source') is None or (isinstance(value['source'], str) and 0 < len(value['source']) <= 20)))


def is_current(value):
    """A level in effect as current() returns it ({level, until, at}), e.g. from the collector."""
    return isinstance(value, dict) and set(value) == {'level', 'until', 'at'} and valid(value)


def current(prefs, surface, now=None):
    """{level, until, at} in effect now: an expired or missing one is normal."""
    value = (prefs or {}).get(pref(surface))
    if not valid(value):
        return dict(NORMAL)
    if not value['level'] or value['until'] <= (now if now is not None else now_ms()):
        return dict(NORMAL, at=value['at'])
    return {'level': value['level'], 'until': value['until'], 'at': value['at']}


def at_level(level, source, now=None):
    """The pref for a level set now: it lasts DURATION_MS (normal has no end)."""
    now = now if now is not None else now_ms()
    return {'level': level, 'until': now + DURATION_MS if level else 0, 'at': now, 'source': str(source or 'unknown')[:20]}


def step(prefs, surface, delta, source, now=None):
    """The pref after one press: delta 1 heavier, -1 lighter (from the level in effect, at most to off or full; a press
    past either end stays there), 0 back to normal. Each press restarts the DURATION_MS clock."""
    now = now if now is not None else now_ms()
    level = 0 if not delta else max(OFF, min(FULL, current(prefs, surface, now)['level'] + (1 if delta > 0 else -1)))
    return at_level(level, source, now)


def factor(level):
    return FACTORS.get(level, 1.0)


def budget(level, normal=None):
    """Lookups per 100 words a level allows for words not known at a glance: 0 at off, None (no limit) at full.
    normal: normal's budget when the reader set one (the challenge), which scales every level."""
    if is_off(level):
        return 0.0
    if is_full(level):
        return None
    base = BUDGETS.get(level, BUDGETS[0])
    return base * normal / BUDGETS[0] if normal else base


def per_sentence(level):
    return PER_SENTENCE.get(level, PER_SENTENCE[0])


def sentence_price(know, surface='t3'):
    """Lookups a whole sentence in Japanese costs on a surface ('t3': chat, 'browser'): knowledge.json's sentencePrice
    for it when the fit made one, else SENTENCE_PRICE."""
    prices = (know or {}).get('sentencePrice') if isinstance(know, dict) else None
    price = prices.get(surface) if isinstance(prices, dict) else None
    ok = isinstance(price, (int, float)) and not isinstance(price, bool) and 0 < price <= 1
    return float(price) if ok else SENTENCE_PRICE


def split(budget, words_most, sentences_most):
    """(learning words' part, whole sentences' part) of a budget: sentences up to SENTENCE_SHARE of it, learning words
    the rest, and what one can't use goes to the other. words_most and sentences_most: the most each could spend
    (None: no limit), so neither part passes it."""
    cap = lambda x, most: x if most is None else min(x, most)
    sentences = cap(SENTENCE_SHARE * budget, sentences_most)
    words = cap(budget - sentences, words_most)
    return words, cap(budget - words, sentences_most)


def is_off(level):
    return level == OFF


def is_full(level):
    return level == FULL


def clock(ms):
    """'21:40': a time in ms since the epoch, on this machine's clock."""
    return time.strftime('%H:%M', time.localtime(ms / 1000))


def describe(value):
    """'heavier (+1) until 21:40', 'off until 21:40', 'full (+3) until 21:40' or 'normal'."""
    level = value.get('level', 0)
    if not level:
        return 'normal'
    until = clock(value['until'])
    return f'off until {until}' if is_off(level) else f'{NAMES[level]} ({level:+d}) until {until}'
