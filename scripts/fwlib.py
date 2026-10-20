"""Japanese Weave shared state: word statuses, stage, chat exposure, extension events.

Python 3.9+ standard library only. One state directory per user (JAPANESE_WEAVE_HOME, default ~/.japanese-weave)
holds everything; the Claude Code hook, the slash commands and the browser native messaging host all read it through
this module and change it through hub.py. With a collector set, the state lives on the collector's machine and the
files here are a cache of it (see hub.py).

Statuses: queue -> active -> known, with shaky as the penalty box (see README "How words advance").
"""
import contextlib
import csv
import datetime as dt
import hashlib
import json
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

# ---- promotion rules (documented in README "How words advance") ----
WINDOW = 20             # learning slots: active plus shaky words
NEW_PER_DAY = 5         # new words per day, including the first day
LOOKED_UP = 2           # a word not introduced yet, opened in this many messages or passages in 14 days, comes first
GRADUATE_DAYS = 3       # active -> known: exposure days needed, and length of the clean streak
SHAKY_DAYS = 7          # shaky -> known: days with no new flip, asked or peek
PEEK_WINDOW = 7         # a known word peeked on 2 different days within this many days becomes shaky
ASSUMED_WEIGHT = 1      # a day with only opt-in "assumed" exposure (fw.py log-chat-day) counts this much
STAGES = [(400, 4), (150, 3), (40, 2)]  # (known words, stage): first match wins; otherwise Stage 1
# Known at a glance: a word whose predicted lookup chance (knowledge.json) is under FREE_P costs nothing against the
# intensity's lookup budget (intensity.BUDGETS) and is used wherever it fits; it no longer takes a learning slot
# either. A word the log has nothing on yet starts at KNOWN_PRIOR_K (log-odds of not looking it up) if its status is
# known, else at the neutral prior (knowledge.prior).
FREE_P = 0.08
KNOWN_PRIOR_K = 2.5


KANJI = '\u3005\u3006\u4e00-\u9fff\uf900-\ufaff'   # 々 〆 and CJK ideographs
KANA = '\u3041-\u309f\u30a0-\u30ff\uff66-\uff9f'   # hiragana, katakana (with ー), half-width katakana
JAPANESE = re.compile(f'[{KANJI}{KANA}]')
CODE = re.compile(r"```.*?```|`[^`\n]*`|https?://\S+", re.S)
DEDUPE_DAYS = 45


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
    """A fresh state; known_top (the top lexicon words) and known (words by name, like mature Anki cards) are
    marked known before the first learning words are picked."""
    day = day or today()
    words = [blank(w) for w in (lexicon or load_lexicon())]
    state = {'schema': 1, 'created': day, 'stage': 1, 'stage_pinned': False, 'paused': False, 'words': words}
    seed_known(state, known_top, day)
    for name in known:
        w = find(state, name)
        if w and w['status'] == 'queue':
            set_status(w, 'known', day)
    refill(state, day)
    return state


def seed_known(state, n, day=None):
    """Marks the top n lexicon words known if they aren't introduced yet (queue). Words already learning, shaky or
    known keep their status, so seeding never resets progress. Returns how many words changed."""
    day = day or today()
    seeded = [w for w in state['words'][:max(n, 0)] if w['status'] == 'queue']
    for w in seeded:
        set_status(w, 'known', day)
    return len(seeded)


def blank(word):
    """A lexicon entry as a queued word: days = extension seen days, assumed = opt-in unscanned chat days."""
    return dict(word, status='queue', added='', changed='', days=[], assumed=[], peeks=[], last_bad='')


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
            state['words'].append(blank(w))
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
    """Load, let the caller change, promote and save the state under the lock. Only where the state lives: with a
    collector set, changes go through hub.submit instead."""
    if collector_url():
        raise RemoteState(f'the word state lives on {collector_url()}; change it with hub.submit')
    with locked():
        state = load_state(lock=False)
        before = json.dumps(state, sort_keys=True)
        merge_lexicon(state)
        yield state
        promote(state)
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


def stage_for(state):
    if state.get('stage_pinned'):
        return state['stage']
    known = sum(w['status'] == 'known' for w in state['words'])
    return next((s for n, s in STAGES if known >= n), 1)


def exposure_days(word, chat=None):
    """{day: 'seen' | 'assumed'} since the word joined the learning set.

    'seen' (counts 1): extension seen events and the word in a Claude Code or Codex reply (scanned transcripts are
    genuine exposure). 'assumed' (counts ASSUMED_WEIGHT): opt-in log-chat-day marks for chats that can't be scanned."""
    since = word.get('added') or '0000'
    days = {d: 'assumed' for d in word.get('assumed') or [] if d >= since}
    for d in list(word.get('days') or []) + list((chat or {}).get(word['ja'], {}).get('days', [])):
        if d >= since:
            days[d] = 'seen'
    return dict(sorted(days.items()))


def refill(state, day, cap=NEW_PER_DAY, looked=None):
    """Fill the learning set (active plus shaky words, which are being relearned) up to WINDOW, at most cap new words
    per day: first the words you looked up (looked_up, most opened first), then lexicon order."""
    words = state['words']
    last_day, count = state.get('refilled') or ['', 0]
    count = count if last_day == day else 0
    room = min(WINDOW - sum(w['status'] in ('active', 'shaky') for w in words), cap - count)
    added = 0
    if room > 0:
        looked = looked_up() if looked is None else looked
        queue = sorted((w for w in words if w['status'] == 'queue'), key=lambda w: -looked.get(w['ja'], 0))
        for w in queue[:room]:
            set_status(w, 'active', day)
            added += 1
    state['refilled'] = [day, count + added]
    return added


def graduates(word, chat=None):
    """active -> known: GRADUATE_DAYS of exposure, no flip/asked/peek on the last GRADUATE_DAYS exposure days,
    and at least one genuinely seen day (assumed days alone never graduate a word)."""
    days = exposure_days(word, chat)
    total = sum(1 if kind == 'seen' else ASSUMED_WEIGHT for kind in days.values())
    if total < GRADUATE_DAYS or len(days) < GRADUATE_DAYS:
        return False
    streak = list(days)[-GRADUATE_DAYS]
    peeks = word.get('peeks') or []
    clean = (not word['last_bad'] or word['last_bad'] < streak) and not (peeks and peeks[-1] >= streak)
    return clean and 'seen' in days.values()


def looked_up():
    """{word: messages or passages it was opened in over the last 14 days} for the words opened in at least
    LOOKED_UP of them (knowledge.json, which scores words not introduced yet too). One open is often curiosity, and
    opening the same word again in one message is still one encounter; two encounters mean the chat keeps using a
    word you don't know."""
    words = (read_json(home() / 'knowledge.json', {}) or {}).get('words') or {}
    return {ja: e['recent'] for ja, e in words.items()
            if isinstance(e, dict) and isinstance(e.get('recent'), int) and e['recent'] >= LOOKED_UP}


def graduation_veto():
    """Active words the gloss log says aren't known yet (knowledge.py): day counts alone don't graduate them."""
    return set((read_json(home() / 'knowledge.json', {}) or {}).get('veto') or [])


def promote(state, day=None, chat=None, veto=None):
    """Applies the promotion rules for today. Idempotent: safe to call as often as you like."""
    day = day or today()
    if chat is None:
        chat = (read_json(home() / 'exposure.json', {}) or {}).get('words', {})
    veto = graduation_veto() if veto is None else veto
    for w in state['words']:
        if w['status'] == 'active' and w['ja'] not in veto and graduates(w, chat):
            set_status(w, 'known', day)
        elif w['status'] == 'shaky' and days_between(w['last_bad'] or w['changed'], day) >= SHAKY_DAYS:
            set_status(w, 'known', day)
    refill(state, day)
    state['stage'] = stage_for(state)
    return state


EVENTS = ('seen', 'assumed', 'flip', 'asked', 'peek', 'known', 'active')


def apply_event(state, ja, kind, day=None):
    """Extension and command events. Returns False for unknown words or kinds.

    seen / assumed: exposure. asked: shaky. peek (hovered for the meaning) and flip (clicked back to English): block
    graduation of an active word (a flip restarts its count), restart a shaky word's clock, and a known word opened
    on 2 different days within PEEK_WINDOW days becomes shaky (one flip is often a test click). known / active: set
    directly (commands)."""
    day = day or today()
    w = find(state, ja)
    if w is None or kind not in EVENTS:
        return False
    if kind in ('seen', 'assumed'):
        field = 'days' if kind == 'seen' else 'assumed'
        if w['status'] != 'queue' and day not in (w.get(field) or []):
            w[field] = sorted((w.get(field) or []) + [day])[-60:]
    elif kind == 'asked':
        w['last_bad'] = day
        set_status(w, 'shaky', day)
    elif kind in ('peek', 'flip'):
        if kind == 'flip' and w['status'] == 'active':
            w['last_bad'] = day  # restarts its graduation count
        peeks = w.setdefault('peeks', [])
        if not peeks or peeks[-1] != day:
            peeks.append(day)
            del peeks[:-10]
        if w['status'] == 'shaky':
            w['last_bad'] = day
        elif w['status'] == 'known' and len(peeks) >= 2 and days_between(peeks[-2], day) < PEEK_WINDOW:
            w['last_bad'] = day
            set_status(w, 'shaky', day)
    elif kind == 'known':
        set_status(w, 'known', day)
    elif kind == 'active':
        w['last_bad'] = day  # restart its graduation count
        set_status(w, 'active', day)
    return True


def introduced(state):
    """Words in use (active, shaky, known), learning words first."""
    order = {'shaky': 0, 'active': 1, 'known': 2}
    words = [w for w in state['words'] if w['status'] in order]
    return sorted(words, key=lambda w: (order[w['status']], w['added']))


COLLAPSE_AT = 150      # from this many known words, the brief and exports name a rank band instead of listing them
EXCEPTIONS_MAX = 15    # most exceptions named on each side of the band


def known_band(state):
    """Compact description of the known set by lexicon rank (the lexicon is in frequency/usefulness order).

    Returns (n, missing, extra): known = words ranked 1..n, except `missing` (ranked <= n but not yet introduced;
    learning words are listed separately anyway), plus `extra` (known words ranked > n). n minimizes the number
    of exceptions; ties go to the larger band."""
    words = state['words']
    known_after = sum(w['status'] == 'known' for w in words)
    queue_before = 0
    best = (known_after, 0)
    for rank, w in enumerate(words, 1):
        known_after -= w['status'] == 'known'
        queue_before += w['status'] == 'queue'
        if queue_before + known_after <= best[0]:
            best = (queue_before + known_after, rank)
    n = best[1]
    missing = [w['ja'] for w in words[:n] if w['status'] == 'queue']
    extra = [w['ja'] for w in words[n:] if w['status'] == 'known']
    return n, missing, extra


def version(state):
    blob = json.dumps([stage_for(state), state.get('paused'), [(w['ja'], w['status']) for w in introduced(state)]])
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def public_state(state):
    """What the extension gets: stage, pause flag, and the words in use."""
    return {'version': version(state), 'stage': stage_for(state), 'paused': bool(state.get('paused')),
            'words': [{k: w.get(k, '') for k in ('ja', 'en', 'hint', 'kana', 'status')} for w in introduced(state)]}


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
    """Scan this machine's new transcript lines and count them, then re-apply promotion. With a collector set, the
    replies go to it (hub.py). Skips if another refresh is running."""
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
                pass  # promotion runs on save
        write_json(home() / SCAN, cursor)
    return True
