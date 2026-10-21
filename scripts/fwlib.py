"""Japanese Weave shared state: word labels, stage, chat exposure, extension events.

Python 3.9+ standard library only. One state directory per user (JAPANESE_WEAVE_HOME, default ~/.japanese-weave)
holds everything; the Claude Code hook, the slash commands and the browser native messaging host all read it through
this module and change it through hub.py. With a collector set, the state lives on the collector's machine and the
files here are a cache of it (see hub.py).

Statuses are labels from the fitted model: queue (new), active (learning), known (see README "How words advance").
"""
import contextlib
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata

import inflection

try:
    import fcntl
except ImportError:  # Windows: no locking (untested platform)
    fcntl = None

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LEXICON = PLUGIN_ROOT / 'data' / 'lexicon.csv'
ANKI_LEXICON = 'anki.csv'  # in the state dir, written by `fw.py anki`: your Anki review words, ahead of the base list

# ---- word labels (documented in README "How words advance") ----
# A word's status is a label from the fitted model (knowledge.py), set on each refresh (relabel): known when it is
# known at a glance (free), learning ('active') once it has come into use and isn't free, new ('queue') before that.
# The day-count rules from French Weave (learning slots, exposure days, shaky words) are gone; their fields stay under
# each word's "legacy" for LEGACY_DAYS after the switch, so it can be undone (restore).
NEW_PER_DAY = 5         # new words a day at most, taken in while the lookup budget goes unspent (knowledge.intake_room)
LOOKED_UP = 2           # a word not introduced yet, opened in this many messages or passages in 14 days, comes first
STATUSES = ('queue', 'active', 'known')  # the labels: new, learning, known ('shaky' is from before the switch)
LEGACY_FIELDS = ('days', 'assumed', 'peeks', 'last_bad')  # the day-count rules' fields, kept under "legacy"
LEGACY_DAYS = 30
STAGES = [(400, 4), (150, 3), (40, 2)]  # (known words, stage): first match wins; otherwise Stage 1
# Known at a glance: a word knowledge.json marks free (its lookup chance under FREE_P, with hysteresis: is_free)
# costs nothing against the intensity's lookup budget (intensity.BUDGETS) and is used wherever it fits, and its label
# is known. knowledge.json has an estimate for every word, from the fitted prior for a word the log has nothing on;
# without knowledge.json a known word starts at KNOWN_PRIOR_K (log-odds of not looking it up), any other at the neutral
# prior (knowledge.prior). KNOWN_PRIOR_K is also the fit's prior mean for a word marked known without an Anki card
# (knowledge.SHARED).
FREE_P = 0.08
KNOWN_PRIOR_K = 2.5
# How a word came into use, its "origin": the fit's prior has an intercept for each (knowledge.origin_at). Unlike its
# status, nothing the model says sets it: intake ('learning'), setup's top words and Anki syncs ('known'), and once,
# the statuses from before labels came from the model (legacy_origin). 'new': none of these.
ORIGINS = ('new', 'learning', 'known')


KANJI = '\u3005\u3006\u4e00-\u9fff\uf900-\ufaff'   # 々 〆 and CJK ideographs
KANA = '\u3041-\u309f\u30a0-\u30ff\uff66-\uff9f'   # hiragana, katakana (with ー), half-width katakana
JAPANESE = re.compile(f'[{KANJI}{KANA}]')
CODE = re.compile(r"```.*?```|`[^`\n]*`|https?://\S+", re.S)
FENCE = re.compile(r'[ \t]*(`{3,}|~{3,})(.*)')
TICKS = re.compile(r'`+')
URL = re.compile(r'https?://\S+')
DEDUPE_DAYS = 45


def strip_code(text):
    """text with its code and links taken out, as markdown shows them: fenced blocks (a line opening with three or more
    backticks or tildes, closed by a line of the same character at least as long, or else running to the end), code
    spans (a run of backticks closed by the next run of the same length; an unclosed run is plain text) and links.
    Each piece becomes a space. Quoted words are data, not woven words."""
    lines, fence = [], None
    for line in str(text).split('\n'):
        if fence:
            if line.strip() and line.strip() == fence[0] * len(line.strip()) and len(line.strip()) >= len(fence):
                fence = None
            lines.append('')
            continue
        opening = FENCE.fullmatch(line)
        if opening and not (opening.group(1)[0] == '`' and '`' in opening.group(2)):  # ```js``` is a code span
            fence = opening.group(1)
            lines.append('')
            continue
        lines.append(line)
    text = '\n'.join(lines)
    runs, out, start, i = list(TICKS.finditer(text)), [], 0, 0
    while i < len(runs):
        close = next((j for j in range(i + 1, len(runs)) if len(runs[j].group()) == len(runs[i].group())), None)
        if close is None:
            i += 1
            continue
        out += [text[start:runs[i].start()], ' ']
        start, i = runs[close].end(), close + 1
    return URL.sub(' ', ''.join(out) + text[start:])


def collector_url():
    """The collector (collector.py) set by `fw.py setup --collector URL`: it holds the word state and the gloss log for
    every machine, and this one keeps a cache. None: the state lives here."""
    path = home() / 'collector'
    return path.read_text().strip() or None if path.exists() else None


_local = threading.local()


@contextlib.contextmanager
def using_home(path):
    """Within this thread, the state dir is path (the collector serves its own state dir, also in tests)."""
    previous = getattr(_local, 'home', None)
    _local.home = Path(path)
    try:
        yield
    finally:
        _local.home = previous


def home():
    """The state dir: set by using_home in this thread, else JAPANESE_WEAVE_HOME, else the dir this copy was installed
    into by setup (<home>/app/scripts), else ~/.japanese-weave."""
    if getattr(_local, 'home', None) is not None:
        return _local.home
    if os.environ.get('JAPANESE_WEAVE_HOME'):
        return Path(os.environ['JAPANESE_WEAVE_HOME']).expanduser()
    if PLUGIN_ROOT.name == 'app' and (PLUGIN_ROOT.parent / 'state.json').exists():
        return PLUGIN_ROOT.parent
    return Path.home() / '.japanese-weave'


def today():
    return dt.date.today().isoformat()


def days_between(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def norm(text):
    return unicodedata.normalize('NFKC', str(text)).replace('’', "'").replace('‘', "'").lower().strip()


def roman_key(text):
    """Romaji folded for lookups: no macrons, apostrophes, spaces or hyphens, long vowels single (tōkyō, toukyou,
    tokyo and Tōkyō all match)."""
    text = unicodedata.normalize('NFD', norm(text))
    text = re.sub(r"[\u0300-\u036f'’\s-]", '', text)
    return re.sub(r'(?<=o)u|(?<=([aiueo]))\1', '', text)


def key(ja):
    return re.sub(r'\s+', ' ', norm(ja))


def read_lexicon(path):
    """Rows of a lexicon CSV (ja, en, hint, kana), first of each word only."""
    with open(path, encoding='utf-8', newline='') as file:
        rows = [r for r in csv.DictReader(file) if (r.get('ja') or '').strip()]
    seen, out = set(), []
    for r in rows:
        k = key(r['ja'])
        if k not in seen:
            seen.add(k)
            out.append({'ja': r['ja'].strip(), 'en': r['en'].strip(), 'hint': r['hint'].strip(),
                        'kana': (r.get('kana') or '').strip()})
    return out


def load_lexicon(path=None):
    """The word list in teaching order. Without a path: your Anki review words (state dir anki.csv, from `fw.py
    anki`) first, then the base frequency list. A word in both keeps its Anki rank and the base list's gloss."""
    if path:
        return read_lexicon(path)
    base = read_lexicon(LEXICON)
    anki = home() / ANKI_LEXICON
    if not anki.exists():
        return base
    by_key = {key(w['ja']): w for w in base}
    out, seen = [], set()
    for w in read_lexicon(anki) + base:
        k = key(w['ja'])
        if k not in seen:
            seen.add(k)
            out.append(by_key.get(k, w))
    return out


# ---- files ----

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=1)
            file.write('\n')
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


def write_json_lines(path, items):
    """One JSON value per line, replacing the file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as file:
        file.writelines(json.dumps(item, ensure_ascii=False) + '\n' for item in items)
    os.replace(file.name, path)


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as file:
            return json.load(file)
    except (OSError, ValueError):
        return default


@contextlib.contextmanager
def locked(name='state.lock', blocking=True):
    """Exclusive lock in the state dir; yields False when non-blocking and someone else holds it."""
    directory = home()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / name, 'w') as handle:
        if fcntl is None:
            yield True
            return
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---- state ----

def new_state(day=None, lexicon=None, known_top=0, known=()):
    """A fresh state, its labels from the model from the start: known_top (the top lexicon words) and known (words by
    name, like mature Anki cards) are marked known, then the first NEW_PER_DAY words are taken in (outside the
    delayed-start experiment: a new reader starts with every first word on show)."""
    day = day or today()
    words = [dict(blank(w), origin='new') for w in (lexicon or load_lexicon())]
    state = {'schema': 1, 'created': day, 'stage': 1, 'stage_pinned': False, 'paused': False, 'words': words,
             'labels': {'from': 'model', 'since': day}}
    seed_known(state, known_top, day)
    mark_known(state, known, day)
    take_in(state, [[w['ja'], None, None] for w in newcomers(state, {})[:NEW_PER_DAY]], day)
    state['stage'] = stage_for(state)
    return state


def seed_known(state, n, day=None):
    """Marks the top n lexicon words known if they aren't introduced yet (queue), as words you know: their origin is
    known. Words already learning or known keep their label, so seeding never resets progress. Returns how many words
    changed."""
    day = day or today()
    seeded = [w for w in state['words'][:max(n, 0)] if w['status'] == 'queue']
    for w in seeded:
        set_status(w, 'known', day)
        w['origin'] = 'known'
    return len(seeded)


def mark_known(state, names, day=None):
    """Marks words known by name (mature Anki cards) when they aren't introduced yet, their origin known. Returns the
    entries found, whether they changed or not."""
    day = day or today()
    by_key = {key(w['ja']): w for w in state['words']}  # thousands of words: one lookup each
    entries = [e for e in (by_key.get(key(name)) or find(state, name) for name in names) if e]
    for e in entries:  # learning words keep learning; only words not introduced yet jump to known
        if e['status'] == 'queue':
            set_status(e, 'known', day)
            e['origin'] = 'known'
    return entries


def blank(word):
    """A lexicon entry as a word not introduced yet."""
    return dict(word, status='queue', added='', changed='')


def merge_lexicon(state, lexicon=None):
    """Adds lexicon words the state doesn't have yet (a plugin update or an Anki sync can grow the list), refreshes
    glosses and hints, and puts the words in lexicon order (an Anki sync moves your review words to the front of
    the queue). Never drops any: words no longer listed keep their status, at the end."""
    lexicon = lexicon or load_lexicon()
    rank = {key(w['ja']): i for i, w in enumerate(lexicon)}
    have = {key(w['ja']): w for w in state['words']}
    for w in lexicon:
        k = key(w['ja'])
        if k in have:
            have[k].update(en=w['en'], hint=w['hint'], kana=w.get('kana', ''))
        else:
            state['words'].append(dict(blank(w), origin='new') if state.get('labels') else blank(w))
    state['words'].sort(key=lambda w: rank.get(key(w['ja']), len(rank)))
    return state


class NoState(RuntimeError):
    """A collector is set but this machine has no copy of its state, and the collector can't be reached."""


PREVIOUS = 'state.json.prev'  # the state as it was before the last save: what a damaged state.json goes back to


def usable(state):
    return isinstance(state, dict) and isinstance(state.get('words'), list)


def damaged(path):
    """The file is there and readable but isn't a word state: cut short, edited by hand, or something else."""
    try:
        with open(path, encoding='utf-8') as file:
            return not usable(json.load(file))
    except ValueError:  # includes text that isn't UTF-8
        return True
    except OSError:  # missing, or unreadable for now: nothing to set aside
        return False


def recover_state(path):
    """Under the lock: a damaged state.json is kept as state.json.bad-<time>, never deleted, and the copy from before
    the last save takes its place when it is good. Returns whether state.json is good now."""
    if not damaged(path):
        return usable(read_json(path))
    bad = path.with_name(time.strftime('state.json.bad-%Y%m%d-%H%M%S'))
    os.replace(path, bad)
    previous = read_json(path.with_name(PREVIOUS))
    if usable(previous):
        write_json(path, previous)
    print(f'Japanese Weave: {path} was damaged; kept it as {bad.name}'
          + (' and went back to the copy from before the last change' if usable(previous) else ''), file=sys.stderr)
    return usable(previous)


def load_state(create=True, lock=True):
    """The saved state; creates it on first use. lock=False when the caller already holds state.lock. With a collector
    set this is the cached copy; without one yet, it is fetched (never created here). A damaged state.json goes back
    to the copy from before the last save (recover_state); without a good one, a cache is fetched again and anywhere
    else the state starts over."""
    path = home() / 'state.json'
    state = read_json(path)
    if usable(state):
        return state
    if damaged(path):
        with locked() if lock else contextlib.nullcontext():
            if recover_state(path):
                return read_json(path)
    if not create:
        return None
    if collector_url():
        import hub
        ok, error = hub.pull()
        state = read_json(path)
        if ok and isinstance(state, dict):
            return state
        raise NoState(f'no copy of the word state from {collector_url()} yet: {error}')
    with locked() if lock else contextlib.nullcontext():
        if not path.exists():
            write_json(path, new_state())
    return read_json(path)


def save_state(state):
    """Writes the state, keeping the one it replaces as state.json.prev (a second name for the same file, no copy)."""
    path = home() / 'state.json'
    if usable(read_json(path)):
        previous = path.with_name(PREVIOUS)
        staged = path.with_name(f'{PREVIOUS}.{os.getpid()}')
        with contextlib.suppress(OSError):
            staged.unlink(missing_ok=True)
            try:
                os.link(path, staged)
            except OSError:  # a filesystem without hard links
                shutil.copy2(path, staged)
            os.replace(staged, previous)
    write_json(path, state)


class RemoteState(RuntimeError):
    """The state lives on the collector: change it with hub.submit, not here."""


@contextlib.contextmanager
def editing():
    """Load, let the caller change, set the stage and save the state under the lock. Only where the state lives: with
    a collector set, changes go through hub.submit instead."""
    if collector_url():
        raise RemoteState(f'the word state lives on {collector_url()}; change it with hub.submit')
    with locked():
        state = load_state(lock=False)
        before = json.dumps(state, sort_keys=True)
        merge_lexicon(state)
        yield state
        state['stage'] = stage_for(state)
        if json.dumps(state, sort_keys=True) != before:  # unchanged: caches elsewhere needn't fetch it again
            save_state(state)


def find(state, word):
    """The entry for a word as written (多分), in kana (たぶん), in romaji (tabun), or as a form of it (なります is
    なる; inflection.py)."""
    k = key(word)
    for w in state['words']:
        if key(w['ja']) == k:
            return w
    for w in state['words']:
        if w.get('kana') and key(w['kana']) == k:
            return w
    r = roman_key(word)
    if r and not JAPANESE.search(word):
        for w in state['words']:
            if roman_key(w['hint']) == r:
                return w
        return None
    ja = inflection.lexicon(state['words']).entry(word) if JAPANESE.search(word) else None
    return next((w for w in state['words'] if w['ja'] == ja), None) if ja else None


def set_status(word, status, day):
    word['status'] = status
    word['changed'] = day
    if not word['added']:
        word['added'] = day


def legacy_origin(word):
    """A word's origin (ORIGINS) from its status under the day-count rules (before labels came from the model): a queue
    word new, an active one learning, a known or shaky one known (shaky words were known words opened again)."""
    status = (word or {}).get('status')
    return 'learning' if status == 'active' else 'known' if status in ('known', 'shaky') else 'new'


def stage_for(state):
    if state.get('stage_pinned'):
        return state['stage']
    known = sum(w['status'] == 'known' for w in state['words'])
    return next((s for n, s in STAGES if known >= n), 1)


def looked_up():
    """{word: messages or passages it was opened in over the last 14 days} for the words opened in at least
    LOOKED_UP of them (knowledge.json, which scores words not introduced yet too). One open is often curiosity, and
    opening the same word again in one message is still one encounter; two encounters mean the chat keeps using a
    word you don't know."""
    words = (read_json(home() / 'knowledge.json', {}) or {}).get('words') or {}
    return {ja: e['recent'] for ja, e in words.items()
            if isinstance(e, dict) and isinstance(e.get('recent'), int) and e['recent'] >= LOOKED_UP}


def is_free(entry, offset=0.0):
    """Whether a knowledge.json word entry is known at a glance: its free flag when it has one (knowledge.free_flag:
    it leaves the free set only when the low end of its lookup chance passes FREE_P, and rejoins when the high end
    falls below it), else a lookup chance under FREE_P at the web's offset."""
    if not isinstance(entry, dict):
        return False
    if isinstance(entry.get('free'), bool):
        return entry['free']
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    least = (offset if number(offset) else 0.0) - math.log(FREE_P / (1 - FREE_P))  # p < FREE_P exactly when k > least
    return number(entry.get('k')) and entry['k'] > least


# ---- labels from the model ----

def label(word, free):
    """A word's status from the fitted model: known when it is known at a glance (free: knowledge.free_flag, with
    hysteresis), else learning ('active') once it has come into use (it has an added day: taken in, marked, or once
    free), else new ('queue')."""
    if free:
        return 'known'
    return 'active' if word.get('added') else 'queue'


def relabel(state, changes, day):
    """Label changes [[ja, from, to]] (knowledge.decide), each made only while the word's status is still `from`, so a
    known or learn command since keeps its label. A label is not an origin: a word without one keeps 'new'. Returns
    the changes made."""
    by_ja = {w['ja']: w for w in state['words']}
    done = []
    for ja, before, after in changes:
        w = by_ja.get(ja)
        if w is not None and w['status'] == before != after and after in STATUSES:
            w.setdefault('origin', 'new')
            set_status(w, after, day)
            done.append([ja, before, after])
    return done


def switch(state, day):
    """Once, before the first labels from the model: each word's status, added and changed days and day-count fields
    (LEGACY_FIELDS) go under its "legacy" for LEGACY_DAYS, so the switch can be undone (restore), and it gets its
    origin from its status then (legacy_origin). Returns whether it switched now."""
    if state.get('labels'):
        return False
    for w in state['words']:
        old = {k: w.pop(k) for k in LEGACY_FIELDS if k in w}
        w['legacy'] = dict(old, status=w['status'], added=w['added'], changed=w['changed'])
        w.setdefault('origin', legacy_origin(w))
    state['labels'] = {'from': 'model', 'since': day}
    if 'refilled' in state:
        state['legacy'] = {'refilled': state.pop('refilled')}
    return True


def expire_legacy(state, day):
    """Drops the legacy fields LEGACY_DAYS after the switch. Returns whether it did."""
    since = (state.get('labels') or {}).get('since')
    if (state.get('labels') or {}).get('from') != 'model' or not since or days_between(since, day) < LEGACY_DAYS:
        return False
    had = state.pop('legacy', None) is not None
    for w in state['words']:
        had = w.pop('legacy', None) is not None or had
    return had


def restore(state, day):
    """Undoes the switch while the legacy fields last: each word gets back its status, added and changed days and
    day-count fields; one taken in since the switch comes back learning ('active') with empty day counts, any other
    word not introduced then back to the queue. The experiment's arms and the origins stay (code from before the
    switch ignores them). From then on labels come from the rules ("labels": {"from": "rules"}), which
    knowledge.refresh leaves alone: check out the code from before the switch to run them (README "How words
    advance"). Returns how many words went back, or None when there is nothing to restore."""
    if (state.get('labels') or {}).get('from') != 'model' or not any(isinstance(w.get('legacy'), dict)
                                                                       for w in state['words']):
        return None
    empty = {'days': [], 'assumed': [], 'peeks': [], 'last_bad': ''}
    for w in state['words']:
        old = w.pop('legacy', None)
        old = old if isinstance(old, dict) else {'status': 'queue', 'added': '', 'changed': ''}
        fields = {k: old.get(k, v) for k, v in empty.items()}
        status, added, changed = old.get('status', 'queue'), old.get('added', ''), old.get('changed', '')
        if status == 'queue' and w.get('origin') == 'learning':  # taken in since the switch
            status, added, changed = 'active', w['added'], w['changed']
        w.update(fields, status=status, added=added, changed=changed)
    if isinstance(state.get('legacy'), dict) and state['legacy'].get('refilled'):
        state['refilled'] = state['legacy']['refilled']
    state.pop('legacy', None)
    state['labels'] = {'from': 'rules', 'since': day}
    return len(state['words'])


# ---- intake ----

def newcomers(state, looked=None, free=()):
    """The words that may be taken in next, in order: not introduced yet (queue) and not known at a glance (free:
    those are labeled known instead), the ones you looked up first (looked_up: most opened first), then the word
    list's order."""
    looked = looked_up() if looked is None else looked
    queue = [w for w in state['words'] if w['status'] == 'queue' and w['ja'] not in free]
    return sorted(queue, key=lambda w: -looked.get(w['ja'], 0))


def taken_today(state, day):
    """Words taken in on `day` so far (state["intake"])."""
    last, count = state.get('intake') or ['', 0]
    return count if last == day else 0


def take_in(state, entries, day):
    """Takes words in, [[ja, arm, draw]]: each a learning word from today, its origin learning, and in the delayed-start
    experiment with its arm and draw (assign; arm None: outside it). Counts toward the day's NEW_PER_DAY
    (state["intake"]). Returns the words taken in."""
    by_ja = {w['ja']: w for w in state['words']}
    done = []
    for ja, arm, u in entries:
        w = by_ja.get(ja)
        if w is None or w['status'] != 'queue':
            continue
        set_status(w, 'active', day)
        w['origin'] = 'learning'
        if arm in ARMS:
            w['arm'] = {'arm': arm, 'draw': u, 'day': day, 'probe': add_days(day, PROBE_DAY)}
        done.append(ja)
    state['intake'] = [day, taken_today(state, day) + len(done)]
    return done


# ---- the delayed-start experiment (PLAN.md section 7) ----
# Each word taken in is drawn into an arm (assign): held back, appearing nowhere, for PROBE_DAY days; or shown like any
# learning word for SHOW_DAYS, then paused until PROBE_DAY. From PROBE_DAY both are probed: each surface gives the
# word one guaranteed showing at its first chance (the web admits it outside the budget once; the chat brief asks for
# it once), and the outcome is its first showing that was read (knowledge.probe_outcomes): looked up or not, with its
# read chance. Then it is an ordinary learning word. A probe with no showing read in PROBE_WAIT days is given up.
ARMS = ('held', 'shown')
SHOW_DAYS = 14
PROBE_DAY = 21
PROBE_WAIT = 14


def add_days(day, n):
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


def draw(seed, ja):
    """A word's draw, uniform on [0, 1): from the experiment's seed and the word alone, so it can be checked."""
    return int(hashlib.sha256(f'{seed}:{ja}'.encode('utf-8')).hexdigest()[:13], 16) / 16 ** 13


def assign(seed, ja):
    """(arm, draw): held when the draw is under one half, else shown."""
    u = draw(seed, ja)
    return ('held' if u < 0.5 else 'shown'), round(u, 6)


def phase(word, day=None):
    """Where a word stands in the experiment on `day` (default today): None (not in it), 'held' or 'paused' (hidden),
    'shown' (its shown days), 'probe' (due its guaranteed showing) or 'done' (probed, or the probe given up)."""
    arm = word.get('arm')
    if not isinstance(arm, dict) or arm.get('arm') not in ARMS or not arm.get('day'):
        return None
    if arm.get('outcome') or arm.get('missed'):
        return 'done'
    age = days_between(arm['day'], day or today())
    if age >= PROBE_DAY:
        return 'probe'
    if arm['arm'] == 'held':
        return 'held'
    return 'shown' if age < SHOW_DAYS else 'paused'


def hidden(word, day=None):
    """The one test every surface makes before offering a word: whether the delayed-start experiment keeps it out of
    sight on `day` (its held days, or its shown arm's paused days). A hidden word appears nowhere: not in the chat
    brief's lists, the words sent to the browser (so neither the web model's list nor the planner's candidates), the
    claude.ai and ChatGPT blocks, nor later the chat's topical words."""
    return phase(word, day) in ('held', 'paused')


def arm_label(word, day=None):
    """A word's arm as plan and brief records give it: 'shown' in its shown days, 'held:probe' or 'shown:probe' while
    its probe is due, else None (not in the experiment, hidden, or done)."""
    now = phase(word, day)
    return 'shown' if now == 'shown' else f'{word["arm"]["arm"]}:probe' if now == 'probe' else None


def record_probes(state, outcomes, day):
    """The probes' outcomes, [[ja, outcome]]: {at, surface, y, read} for the first showing read, or 'missed' (none in
    PROBE_WAIT days). Only for a word whose probe is due. Returns how many were recorded."""
    by_ja = {w['ja']: w for w in state['words']}
    done = 0
    for ja, outcome in outcomes:
        w = by_ja.get(ja)
        if w is None or phase(w, day) != 'probe':
            continue
        if outcome == 'missed':
            w['arm']['missed'] = day
        else:
            w['arm']['outcome'] = dict(outcome)
        done += 1
    return done


def apply_labels(state, op, day):
    """A refresh's state edit (knowledge.decide; hub's labels op): the switch the first time (switch), the label
    changes (relabel), the experiment's seed when it starts, the words taken in (take_in) and the probes' outcomes
    (record_probes). Nothing once labels come from the rules again (restore). Returns what it did."""
    if (state.get('labels') or {}).get('from') == 'rules':
        return {'labels': [], 'intake': [], 'probes': 0, 'switched': False}
    switched = switch(state, day)
    expire_legacy(state, day)
    changed = relabel(state, op.get('labels') or [], day)
    if isinstance(op.get('experiment'), dict) and not isinstance(state.get('experiment'), dict):
        state['experiment'] = {'name': 'delayed-start', 'seed': op['experiment']['seed'], 'since': day}
    taken = take_in(state, op.get('intake') or [], day)
    return {'labels': changed, 'intake': taken, 'probes': record_probes(state, op.get('probes') or [], day),
            'switched': switched}


EVENTS = ('seen', 'assumed', 'flip', 'asked', 'peek', 'known', 'active')


def apply_event(state, ja, kind, day=None):
    """Extension and command events. Returns False for unknown words or kinds.

    known, and asked or active (learn): the word's label at once, known or learning; the next refresh labels it from
    the model, which takes the command as evidence (knowledge.py). seen, assumed, peek and flip change nothing here:
    no day counts any more, and the ops log keeps them."""
    day = day or today()
    w = find(state, ja)
    if w is None or kind not in EVENTS:
        return False
    if kind == 'known':
        set_status(w, 'known', day)
    elif kind in ('asked', 'active'):
        set_status(w, 'active', day)
    return True


def introduced(state, day=None):
    """The words every surface may use on `day` (default today): in use (learning and known) and not hidden by the
    delayed-start experiment (hidden), learning words first."""
    day = day or today()
    order = {'shaky': 0, 'active': 1, 'known': 2}
    words = [w for w in state['words'] if w['status'] in order and not hidden(w, day)]
    return sorted(words, key=lambda w: (order[w['status']], w['added']))


def visible(state, day=None):
    """A copy of the state for a surface that reads statuses itself (export.py): words hidden by the experiment read
    as not introduced."""
    day = day or today()
    return dict(state, words=[dict(w, status='queue') if hidden(w, day) else w for w in state['words']])


COLLAPSE_AT = 150      # from this many known words, the exports name a rank band instead of listing them (the chat
                       # brief names the most useful words: weave.py)
EXCEPTIONS_MAX = 15    # most exceptions named on each side of the band


def known_band(state, known=None, listed=None):
    """Compact description of the known set by rank in the word list (the exports' band). The list is the base list's
    frequency/usefulness order only without Anki words: an Anki sync puts its words first, so the exports pass the
    words in the base list's order (export.by_lexicon).

    Returns (n, missing, extra): known = words ranked 1..n, except `missing` (ranked <= n but not yet introduced;
    learning words are listed separately anyway), plus `extra` (known words ranked > n). n minimizes the number
    of exceptions; ties go to the larger band. known: the words (ja) to describe instead of those with status known;
    listed: the words listed separately instead of the active and shaky ones (any other word in the band is missing)."""
    words = state['words']
    is_known = (lambda w: w['status'] == 'known') if known is None else (lambda w: w['ja'] in known)
    aside = (lambda w: w['status'] in ('active', 'shaky')) if listed is None else (lambda w: w['ja'] in listed)
    gap = lambda w: not is_known(w) and not aside(w)
    known_after = sum(map(is_known, words))
    gaps_before = 0
    best = (known_after, 0)
    for rank, w in enumerate(words, 1):
        known_after -= is_known(w)
        gaps_before += gap(w)
        if gaps_before + known_after <= best[0]:
            best = (gaps_before + known_after, rank)
    n = best[1]
    missing = [w['ja'] for w in words[:n] if gap(w)]
    extra = [w['ja'] for w in words[n:] if is_known(w)]
    return n, missing, extra


def version(state):
    blob = json.dumps([stage_for(state), state.get('paused'), [(w['ja'], w['status']) for w in introduced(state)]])
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def public_state(state, day=None):
    """What the extension gets: stage, pause flag, and the words in use (introduced: none the experiment hides), each
    with its arm while one is current (arm_label: the web plans record it, and a due probe is shown first)."""
    day = day or today()
    words = []
    for w in introduced(state, day):
        entry = {k: w.get(k, '') for k in ('ja', 'en', 'hint', 'kana', 'status')}
        arm = arm_label(w, day)
        if arm:
            entry['arm'] = arm
        words.append(entry)
    return {'version': version(state), 'stage': stage_for(state), 'paused': bool(state.get('paused')), 'words': words}


# ---- chat exposure from Claude Code transcripts ----

def forms(ja, kana=''):
    """How a word can appear in chat prose (normalized): as written, or in kana when that is 3+ characters (shorter
    kana collides with ordinary Japanese)."""
    out = [norm(ja)]
    if kana and len(kana) >= 3 and norm(kana) != out[0]:
        out.append(norm(kana))
    return out


def pattern(ja, kana=''):
    """A word in chat prose (see forms). Japanese has no spaces, so only same-script neighbors block a match: a kanji
    word can't sit inside a longer kanji run (今 in 今日) and a kana word can't sit inside a kana run (いま in います)."""
    return re.compile('|'.join(_form(f) for f in forms(ja, kana)))


def _form(form):
    if not JAPANESE.search(form):
        return r"(?<![\w'-])" + re.escape(form) + r"(?![\w-])"
    before = f'[{KANJI}]' if re.match(f'[{KANJI}]', form) else f'[{KANJI}{KANA}]'
    after = f'[{KANJI}]' if re.search(f'[{KANJI}]$', form) else f'[{KANA}]'
    return f'(?<!{before}){re.escape(form)}(?!{after})'


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()


def transcript_roots():
    """Chat transcripts to scan: JAPANESE_WEAVE_TRANSCRIPTS, else Claude Code's projects and Codex's sessions."""
    if os.environ.get('JAPANESE_WEAVE_TRANSCRIPTS'):
        return [Path(os.environ['JAPANESE_WEAVE_TRANSCRIPTS']).expanduser()]
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').expanduser() / 'projects'
    return [claude, codex_home() / 'sessions']


class Lines:
    """Complete lines of a log after a byte offset; .offset ends just past the last complete line read."""

    def __init__(self, path, offset):
        self.path, self.offset = path, offset

    def __iter__(self):
        with open(self.path, 'rb') as file:
            file.seek(self.offset)
            for raw in file:
                if not raw.endswith(b'\n'):
                    return  # half-written; read it next time
                self.offset += len(raw)
                yield raw.decode('utf-8', 'replace')


def replies(lines):
    """Yield (day, text) for main-thread assistant replies in Claude Code or Codex transcript lines."""
    for line in lines:
        if len(line) > 1_000_000 or '"assistant"' not in line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        day = local_day(item.get('timestamp'))
        if item.get('type') == 'assistant' and not item.get('isSidechain'):  # Claude Code
            for block in item.get('message', {}).get('content') or []:
                if isinstance(block, dict) and block.get('type') == 'text':
                    yield day, block.get('text', '')
        elif item.get('type') == 'response_item':  # Codex
            payload = item.get('payload') or {}
            if payload.get('type') == 'message' and payload.get('role') == 'assistant':
                yield day, ' '.join(b.get('text', '') for b in payload.get('content') or [] if isinstance(b, dict))


def local_day(stamp):
    try:
        return dt.datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).astimezone().date().isoformat()
    except ValueError:
        return str(stamp)[:10]


def read_replies(words, offsets, roots=None, start='0000-00-00'):
    """New transcript lines since offsets (path -> byte offset, mutated): [{d: digest, day, words: [ja]}] for the
    main-thread replies that use any of the words. Code, links and replies about the plugin itself don't count."""
    roots = [Path(r) for r in ([roots] if isinstance(roots, (str, Path)) else roots)] if roots else transcript_roots()
    words = [w if isinstance(w, dict) else {'ja': w} for w in words]
    written = {w['ja']: forms(w['ja'], w.get('kana', '')) for w in words}
    compiled = {}

    def uses(w, prose):  # a cheap substring test first: compiling every word's pattern takes seconds
        if not any(f in prose for f in written[w['ja']]):
            return False
        if w['ja'] not in compiled:
            compiled[w['ja']] = pattern(w['ja'], w.get('kana', ''))
        return bool(compiled[w['ja']].search(prose))

    since = time.mktime(time.strptime(start, '%Y-%m-%d')) if start > '0000-00-00' else 0
    out = []
    for path in (p for root in roots if root.exists() for p in root.rglob('*.jsonl')):
        name = str(path)
        try:
            if 'subagents' in path.parts or path.stat().st_mtime < since:
                continue
            offset = offsets.get(name, 0)
            if path.stat().st_size < offset:
                offset = 0
        except OSError:
            continue
        lines = Lines(path, offset)
        for day, text in replies(lines):
            if day < start or 'Japanese Weave' in text:
                continue
            prose = norm(CODE.sub(' ', text))
            used = [w['ja'] for w in words if uses(w, prose)]
            if used:
                out.append({'d': hashlib.sha1(text.encode()).hexdigest()[:16], 'day': day, 'words': used})
        offsets[name] = lines.offset
    for name in [k for k in offsets if not os.path.exists(k)]:
        del offsets[name]
    return out


def fold(index, found, words=()):
    """Adds replies (read_replies) to the exposure index (mutated and returned): {words: {ja: {n, days}}, recent:
    {digest: day}}. A reply already counted (same digest: forked and resumed chats copy old replies, and two machines
    may send the same one) counts once. words: entries to create at zero, so every listed word has a count."""
    counts, recent = index.setdefault('words', {}), index.setdefault('recent', {})
    for ja in words:
        counts.setdefault(ja, {'n': 0, 'days': []})
    for reply in found:
        if reply['d'] in recent:
            continue
        recent[reply['d']] = reply['day']
        for ja in reply['words']:
            entry = counts.setdefault(ja, {'n': 0, 'days': []})
            entry['n'] += 1
            if reply['day'] not in entry['days']:
                entry['days'] = sorted(entry['days'] + [reply['day']])[-60:]
    cutoff = (dt.date.today() - dt.timedelta(days=DEDUPE_DAYS)).isoformat()
    index['recent'] = {k: v for k, v in recent.items() if v >= cutoff}
    index['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return index


def scan(words, index, roots=None, start='0000-00-00'):
    """Fold new transcript lines into the exposure index (mutated and returned); its offsets say where to resume."""
    found = read_replies(words, index.setdefault('offsets', {}), roots, start)
    return fold(index, found, [w if isinstance(w, str) else w['ja'] for w in words])


SCAN = 'scan.json'  # where this machine's transcript scan resumes: {offsets: {path: byte offset}}


def scan_cursor():
    """This machine's scan offsets. Older installs kept them in exposure.json."""
    cursor = read_json(home() / SCAN)
    if isinstance(cursor, dict) and isinstance(cursor.get('offsets'), dict):
        return cursor
    old = (read_json(home() / 'exposure.json', {}) or {}).get('offsets')
    return {'offsets': old if isinstance(old, dict) else {}}


def refresh_exposure():
    """Scan this machine's new transcript lines and count them. With a collector set, the replies go to it (hub.py).
    Skips if another refresh is running."""
    with locked('exposure.lock', blocking=False) as got:
        if not got:
            return False
        state = load_state()
        cursor = scan_cursor()
        found = read_replies(state['words'], cursor['offsets'], start=state.get('created', '0000-00-00'))
        import hub
        if collector_url():
            if found and not hub.send_exposure(found):
                return False  # the cursor stays, so the same replies go again next time
        elif found:
            hub.run(hub.stamp({'kind': 'exposure', 'replies': found}))
        else:
            with editing():
                pass  # the stage is set on save
        write_json(home() / SCAN, cursor)
    return True
