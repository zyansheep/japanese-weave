"""Intensity: the reader turns the Japanese heavier or lighter for a while, separately in chat replies ('chat') and on
web pages ('browser'), with a keyboard shortcut (T3 Code, the extension), the popup or `fw.py intensity`. Python 3.9+,
standard library only.

A level is -3..3 and lasts DURATION_MS after the last change, then it is back to normal (0). -2..2 scale the usual
amount; the ends are modes of their own: -3 is off (no Japanese) and 3 is full (all the Japanese the reader can follow;
in chat, weave.py writes a brief of its own for it). Each change is a `set` op on the pref intensity_chat or
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
