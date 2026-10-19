"""Intensity: the reader turns the Japanese heavier or lighter for a while, separately in chat replies ('chat') and on
web pages ('browser'), with a keyboard shortcut (T3 Code, the extension), the popup or `fw.py intensity`. Python 3.9+,
standard library only.

A level is -2..2 and lasts DURATION_MS after the last change, then it is back to normal (0). Each change is a `set` op on
the pref intensity_chat or intensity_browser ({level, until, at, source}), so the collector's ops table keeps every
press: how often and when the reader turns it down or up is the evidence for whether the default amount is right
(knowledge.py calibration)."""
import time

SURFACES = ('chat', 'browser')
LEVELS = (-2, -1, 0, 1, 2)
FACTORS = {-2: 0.5, -1: 0.7, 0: 1.0, 1: 1.4, 2: 1.8}  # times the stage's (or the popup's) amount of Japanese
NAMES = {-2: 'much lighter', -1: 'lighter', 0: 'normal', 1: 'heavier', 2: 'much heavier'}
DURATION_MS = 3 * 3600 * 1000
NORMAL = {'level': 0, 'until': 0, 'at': 0}


def now_ms():
    return int(time.time() * 1000)


def pref(surface):
    return f'intensity_{surface}'


def valid(value):
    """A stored intensity: {level, until, at, source?} (ops arrive over the network)."""
    integer = lambda x: isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 10 ** 14
    return (isinstance(value, dict) and set(value) <= {'level', 'until', 'at', 'source'} and value.get('level') in LEVELS
            and integer(value.get('until')) and integer(value.get('at'))
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


def step(prefs, surface, delta, source, now=None):
    """The pref after one press: delta 1 heavier, -1 lighter (from the level in effect, at most 2 either way), 0 back
    to normal. Each press restarts the DURATION_MS clock."""
    now = now if now is not None else now_ms()
    level = 0 if not delta else max(-2, min(2, current(prefs, surface, now)['level'] + (1 if delta > 0 else -1)))
    return {'level': level, 'until': now + DURATION_MS if level else 0, 'at': now, 'source': str(source or 'unknown')[:20]}


def factor(level):
    return FACTORS.get(level, 1.0)


def describe(value):
    """'heavier (+1) until 21:40' or 'normal'."""
    level = value.get('level', 0)
    if not level:
        return 'normal'
    until = time.strftime('%H:%M', time.localtime(value['until'] / 1000))
    return f'{NAMES[level]} ({level:+d}) until {until}'
