"""Words from your reading (PLAN.md section 6; NEXT_STEPS steps 13 and 14): which dictionary entries turn up in what
you read, counted per entry and day, and which ones a chat's recent turns use, for its topical words. Counts only,
never text. Python 3.9+, standard library only.

English goes through the matcher (matcher.py) over every dictionary entry (data/dictionary.tsv), each with its display
gloss and its first sense's glosses, so the longest match wins against every word the dictionary has, verbs and set
phrases included: in "go back" the span is 戻る's, so "back" there is never 奥. A span counts for at most one entry, and
only where a sense check can trust it (Reader.trusted):
- never a span of closed-class English ("about", "one", "so": the matcher's function flag);
- with the sense flags (data/senses.tsv, read through senses.py; NEXT_STEPS step 12), the most frequent option flagged
  safe for the English it matched; the index is rebuilt when the flags file appears or changes (signature);
- without a flags file, the most frequent option, when this English is its main meaning (the first of its display
  gloss: "state" is not 国 "country / state", and no option counts) and no more frequent entry reaches the same
  English only by a rule's inflection, which the matcher's lexical rule drops ("used" is the past of 使う before it
  is 中古 "used"). On the user's chats this halves what the most frequent option alone counts, mostly wrong senses
  ("script" as 本 "book / script", "data" as 資料), but some remain ("back" as 奥); the flags count about a quarter
  as many entries a day again.
The chat's topical words take the main-meaning rule even with the flags (topical), each span with whether the flags
call it safe: weave.topical_words offers the safe ones first and a doubtful one only after them, and in a technical
chat none, where a word's main meaning is often not the one meant ("frame" as 額, "value" as 価値). On 40 of the
user's briefs the main-meaning rule alone offered 41% wrong senses, this 19%. The flags alone, strict enough for code
to swap, would leave out words a conversation is about ("update" as 更新 is doubtful).
Model-confirmed matches come through the Japanese a model wrote: in chat, Claude's own Japanese (japanese), through
its <abbr> tags where a reply has them (dictionary.find on the word, its reading and its meaning), else the word
state's words found by the script-aware matching (fwlib.pattern). A model's check of the doubtful spans in chat would
add its confirmed options where trusted decides.

The index is the matcher's trie split by first word into an SQLite file in the state dir (INDEX), each node keeping
only the hits that can decide a span (_slim). It is built in the background (ensure: about 5 seconds, once per
dictionary, English data and sense flags) and read for the words of one text only (Reader), which takes milliseconds,
so the hook can find a chat's topical words on every prompt.

What the counts are for: intake (nominations: a word is eligible after coming up on fwlib.ELIGIBLE_DAYS different
days of fwlib.READING_DAYS), the chat brief's topical words (weave.topical_words, which prefers words your reading
repeats) and, later, the probe's weights by your reading.
"""
import collections
import contextlib
import gc
import marshal
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

import fwlib
import matcher

ROOT = Path(__file__).resolve().parent.parent
DICTIONARY = ROOT / 'data' / 'dictionary.tsv'
SENSES = ROOT / 'data' / 'senses.tsv'
INDEX = 'reading-index.sqlite'
INDEX_LOCK = 'reading-index.lock'
VERSION = 3  # of the index's layout and the counting rule: a change rebuilds the index (3: entries keep their field)
# A first word whose subtree is larger than this many bytes is stored by its second words too: the 9 forms of "be"
# head 60 KB each of phrases ("is wrong", "was born") and any text has some of them, so a text reads only the
# second words it has. 335 first words, 17,638 second ones.
SPLIT = 2000
HELPERS = frozenset(name for name in matcher.AUX if name != 'between')  # glosses the helpers rule drops

GLOSSED = re.compile(r'<abbr\b([^>]*)>(.*?)</abbr>', re.I | re.S)  # a glossed Japanese word, its title the gloss
TITLE = re.compile(r'title\s*=\s*"([^"]*)"', re.I)
MARKUP = re.compile(r'</?[A-Za-z][^>\n]*>')
FOOTER = re.compile(r'^.*\s=\s.*".*$', re.M)  # a footer gloss line: (多分 = maybe, "tabun")
# Names, not prose: snake_case, paths, dotted names (fwlib.py, e.g.) and camelCase ("read_replies" is no reading)
IDENTIFIER = re.compile(r"[\w.\\-]*[_/\\][\w./\\-]*|\b\w+(?:\.\w+)+|\b[a-z]+[A-Z]\w*")


def prose(text):
    """The English of a chat reply or prompt to match: without code, links, glossed Japanese and its gloss, footer gloss
    lines, markup and identifiers."""
    text = fwlib.strip_code(GLOSSED.sub(' ', str(text or '')))
    return IDENTIFIER.sub(' ', MARKUP.sub(' ', FOOTER.sub(' ', text)))


def glossed(text):
    """[(word, reading, meaning)] for each <abbr>-glossed Japanese word in a reply ('hiragana reading · meaning', or
    just the meaning for a kana word). A sentence's closing mark, glossed with its translation, is no word."""
    out = []
    for attributes, inner in GLOSSED.findall(str(text or '')):
        word = MARKUP.sub('', inner).strip()
        if not fwlib.JAPANESE.search(word):
            continue
        title = TITLE.search(attributes)
        reading, _, meaning = (title.group(1) if title else '').partition('·')
        if not meaning:
            reading, meaning = '', reading
        out.append((word, reading.strip(), meaning.strip()))
    return out


# ---- the index ----

def signature():
    """What the index is built from: its layout's version and the dictionary, English data and sense flags files, by
    size and time. A plugin update or new flags changes it, and the index is built again."""
    parts = [f'v{VERSION}']
    for path in (DICTIONARY, matcher.DATA, SENSES):
        try:
            info = path.stat()
            parts.append(f'{path.name}:{info.st_size}:{info.st_mtime_ns}')
        except OSError:
            parts.append(f'{path.name}:-')
    return ' '.join(parts)


def index_entries(d):
    """One matcher entry per dictionary entry, in rank order: the alternatives of its display gloss, then its first
    sense's others, with its parts of speech."""
    out = []
    for e in d:
        glosses = matcher.gloss_alternatives(e['en'])
        glosses += [g for g in matcher.gloss_alternatives(e['glosses'][0] if e['glosses'] else []) if g not in glosses]
        out.append({'ja': e['ja'], 'kana': e['kana'], 'en': glosses, 'pos': e['pos']})
    return out


def safe_pairs(entries):
    """{(entry position, gloss)} flagged safe in data/senses.tsv (senses.py), or None without the file. A file that
    can't be read is reported and treated as none."""
    if not SENSES.exists():
        return None
    try:
        import senses
        flags = senses.load(SENSES)
        return {(i, g) for i, e in enumerate(entries) for g in e['en']
                if senses.sense_flag(flags, {'ja': e['ja'], 'kana': e['kana']}, g) == 's'}
    except Exception as error:  # counted as without flags until it is fixed
        print(f'Japanese Weave: sense flags unreadable ({error.__class__.__name__}: {error}); counting without them',
              file=sys.stderr)
        return None


def _slim(node, entries, kinds, safe):
    """A trie node keeping, of its hits, the most frequent of each kind the matcher's rules tell apart (an entry that
    is only a verb, a helper verb's gloss, how it was inflected) and trusted tells apart (whether the gloss is the
    entry's main meaning, and with sense flags whether it is safe). The rules keep or drop a hit by these alone, so
    the options a span keeps have the same most frequent one of each kind. Hits as the matcher's, plus main and
    safe."""
    best = {}
    for h in node['hits']:
        e, g = h['entry'], h['gloss']
        ok = safe is not None and (e, g) in safe
        main = g == entries[e]['en'][0]
        kind = (kinds[e] == 'v' and not g.startswith('be '), g if g in HELPERS else None, h['inflected'], ok, main)
        if kind not in best or e < best[kind]['entry']:
            best[kind] = {'entry': e, 'gloss': g, 'inflected': h['inflected'], 'safe': ok, 'main': main}
    return {'next': {k: _slim(v, entries, kinds, safe) for k, v in node['next'].items()},
            'hits': sorted(best.values(), key=lambda h: h['entry'])}


def load_dictionary():
    """The dictionary the counts are of (DICTIONARY), read once per process."""
    import dictionary
    return dictionary.load(DICTIONARY)


def build(path=None, d=None):
    """Builds the index (default: INDEX in the state dir) from the dictionary d (default DICTIONARY), written whole
    under another name and moved in place, so a reader never sees half of it. Returns its path."""
    import dictionary
    d = d or load_dictionary()
    path = Path(path or fwlib.home() / INDEX)
    entries = index_entries(d)
    safe = safe_pairs(entries)
    trees, children = [], []
    with dictionary.Unbuffered():
        index = matcher.build_index(entries)
        for word, node in index.root['next'].items():
            tree = _slim(node, entries, index.kinds, safe)
            blob = marshal.dumps(tree)
            if len(blob) > SPLIT:  # "is", "the", "in": read only the second words a text has
                children += [(f'{word}\n{key}', marshal.dumps(child)) for key, child in tree['next'].items()]
                blob = marshal.dumps({'next': {}, 'hits': tree['hits'], 'split': True})
            trees.append((word, blob))
    rows = []
    for i in range(len(d)):
        e = d.entry(i)
        rows.append((i, e['id'], e['ja'], e['kana'], e['en'], e['romaji'], e['rank'], e['common'], int(e['swap']),
                     ','.join(e['forms']), ','.join(e['pos']), e['loan'], ''.join(e['kanji']),
                     ','.join(e.get('field') or ())))
    meta = {'signature': signature(), 'flags': marshal.dumps(safe is not None), 'kinds': marshal.dumps(index.kinds),
            'ids': marshal.dumps([r[1] for r in rows]), 'swap': bytes(r[8] for r in rows)}
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix='.sqlite')
    os.close(handle)
    try:
        con = sqlite3.connect(temporary)
        with con:
            con.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value)')
            con.execute('CREATE TABLE first (word TEXT PRIMARY KEY, tree BLOB)')
            con.execute('CREATE TABLE second (key TEXT PRIMARY KEY, tree BLOB)')
            con.execute('CREATE TABLE entry (i INTEGER PRIMARY KEY, id INTEGER, ja TEXT, kana TEXT, en TEXT, '
                        'romaji TEXT, rank INTEGER, common INTEGER, swap INTEGER, forms TEXT, pos TEXT, loan TEXT, '
                        'kanji TEXT, field TEXT)')
            con.executemany('INSERT INTO meta VALUES (?, ?)', meta.items())
            con.executemany('INSERT INTO first VALUES (?, ?)', trees)
            con.executemany('INSERT INTO second VALUES (?, ?)', children)
            con.executemany('INSERT INTO entry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
        con.close()
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
    _built[signature()] = path
    return path


_built = {}  # signature -> an index this process built, copied rather than built again (tests make many state dirs)


def ensure(path=None):
    """The index, built when it is missing or out of date (one builder at a time). True when it is current."""
    path = Path(path or fwlib.home() / INDEX)
    if Reader.current(path):
        return True
    with fwlib.locked(INDEX_LOCK, blocking=False) as got:
        if not got:
            return False
        if not Reader.current(path):
            made = _built.get(signature())
            if made and made != path and Reader.current(made):
                import shutil
                shutil.copyfile(made, path)
            else:
                build(path)
    return True


class _Positions:
    """The index's entries, for the matcher: each option is its entry's position in rank order."""

    def __getitem__(self, i):
        return i


class Reader:
    """The index, read for the words of one text at a time (found). Open with Reader.open; None when it is missing or
    out of date."""

    def __init__(self, con):
        self.con = con
        meta = dict(con.execute('SELECT key, value FROM meta'))
        self.flags = marshal.loads(meta['flags'])
        self.kinds = marshal.loads(meta['kinds'])
        self.ids = marshal.loads(meta['ids'])
        self.swap = meta['swap']
        self.trees, self.seconds = {}, set()  # subtrees read so far, by first word; second words read

    @staticmethod
    def current(path):
        try:
            con = sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True)
            try:
                row = con.execute("SELECT value FROM meta WHERE key = 'signature'").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return False
        return bool(row) and row[0] == signature()

    @classmethod
    def open(cls, path=None):
        path = Path(path or fwlib.home() / INDEX)
        if not path.exists():
            return None
        try:
            con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        except sqlite3.Error:
            return None
        try:
            row = con.execute("SELECT value FROM meta WHERE key = 'signature'").fetchone()
            if row and row[0] == signature():
                return cls(con)
        except (sqlite3.Error, ValueError, EOFError, TypeError, KeyError):
            pass
        con.close()
        return None

    def close(self):
        self.con.close()

    def _rows(self, query, keys):
        """{key: tree} for the keys a table has, by a query with an IN list."""
        out = {}
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            for key, blob in self.con.execute(query.format(','.join('?' * len(chunk))), chunk):
                out[key] = marshal.loads(blob)
        return out

    def _load(self, tokens, seps):
        """The subtrees a text's words can start, each read once: by first word, and for a first word stored split
        (SPLIT), only by the second words that follow it in this text."""
        firsts = [w for w in {t['word'] for t in tokens} | {t['stem']['word'] for t in tokens if t['stem']}
                  if w not in self.trees]
        found = self._rows('SELECT word, tree FROM first WHERE word IN ({})', firsts)
        self.trees.update({w: found.get(w) for w in firsts})
        wanted = set()
        for i, t in enumerate(tokens[:-1]):
            head = self.trees.get(t['word'])
            if head and head.get('split') and seps[i] is not None:
                after = tokens[i + 1]
                for w in (after['word'], after['stem']['word'] if after['stem'] else None):
                    if w and f'{t["word"]}\n{seps[i]}{w}' not in self.seconds:
                        wanted.add(f'{t["word"]}\n{seps[i]}{w}')
        for key, tree in self._rows('SELECT key, tree FROM second WHERE key IN ({})', sorted(wanted)).items():
            head, child = key.split('\n', 1)
            self.trees[head]['next'][child] = tree
        self.seconds |= wanted

    def spans(self, text):
        """The matcher's spans of a text (matcher.match_text), each option an entry's position, and the trie node each
        ended at (its slim hits)."""
        tokens, seps = matcher._tokenize(text)
        firsts = {t['word'] for t in tokens} | {t['stem']['word'] for t in tokens if t['stem']}
        self._load(tokens, seps)
        root = {'next': {w: self.trees[w] for w in firsts if self.trees.get(w)}, 'hits': []}
        index = matcher.Index(_Positions(), self.kinds, root, frozenset(matcher.RULES))
        out = []
        for span in matcher.match_text(index, text):
            words, seps = matcher._tokenize(span['english'])
            node = root
            for k, t in enumerate(words):
                node = node['next'].get((seps[k - 1] if k else '') + t['word']) if node else None
            out.append((span, node))
        return out

    def trusted(self, span, node, flags=True):
        """The entry a span counts for (its position), or None: never closed-class English; with sense flags (the index
        was built with them, and flags is True) the most frequent option flagged safe; without, the most frequent
        option when this English is its main meaning, unless a more frequent entry reaches it only by a rule's
        inflection. Here a model's check of a doubtful span would name the option it confirms."""
        if span['function'] or not node:
            return None
        if self.flags and flags:
            safe = {h['entry'] for h in node['hits'] if h['safe']}
            return next((i for i in span['options'] if i in safe), None)
        best = span['options'][0]
        if not any(h['main'] for h in node['hits'] if h['entry'] == best):
            return None
        return None if any(h['inflected'] == 2 and h['entry'] < best for h in node['hits']) else best

    def found(self, text, flags=True):
        """{entry position: spans} for the English of a text (prose), trusted spans only. flags False: by the main
        meaning even when the index has sense flags, for words a model places itself (the chat's topical words: Claude
        uses one only where the English has its meaning)."""
        out = collections.Counter()
        for span, node in self.spans(prose(text)):
            i = self.trusted(span, node, flags)
            if i is not None:
                out[i] += 1
        return out

    def topical(self, text):
        """{entry position: (spans, safe spans)} for the English of a text (prose), by the main meaning (trusted with
        flags False), for the chat's topical words: each span with whether the sense flags call its entry safe for that
        English (without flags, none is)."""
        out = {}
        for span, node in self.spans(prose(text)):
            i = self.trusted(span, node, False)
            if i is not None:
                n, safe = out.get(i, (0, 0))
                out[i] = (n + 1, safe + (bool(self.flags) and any(h['safe'] for h in node['hits'] if h['entry'] == i)))
        return out

    def entries(self, positions):
        """{position: {id, ja, kana, en, hint (romaji), rank, common, swap, forms, pos, loan, kanji, field}} for
        entries by position: the dictionary's fields (dictionary.py) the brief, the topical words and the starting
        values (knowledge.word_features) read."""
        positions = list(positions)
        out = {}
        for start in range(0, len(positions), 500):
            chunk = positions[start:start + 500]
            query = ('SELECT i, id, ja, kana, en, romaji, rank, common, swap, forms, pos, loan, kanji, field FROM entry '
                     f'WHERE i IN ({",".join("?" * len(chunk))})')
            for i, id_, ja, kana, en, romaji, rank, common, swap, forms, pos, loan, kanji, field in self.con.execute(
                    query, chunk):
                out[i] = {'id': id_, 'ja': ja, 'kana': kana, 'en': en, 'hint': romaji, 'rank': rank, 'common': common,
                          'swap': bool(swap), 'forms': forms.split(',') if forms else [],
                          'pos': pos.split(',') if pos else [], 'loan': loan or '', 'kanji': list(kanji or ''),
                          'field': field.split(',') if field else []}
        return out


# ---- counting a machine's chat replies ----

class Tally:
    """Counts the entries each chat reply shows (count), for fwlib.read_replies. The index and the dictionary are
    opened on the first reply, so a scan with nothing new costs nothing."""

    def __init__(self, words, reader=None, d=None):
        self.words = {w['ja']: w for w in words}
        self.reader, self.d = reader, d
        self.ids = {}  # a state word -> its entry id (or None)
        self.failed = 0  # replies that couldn't be counted

    def _open(self):
        if self.reader is None:
            ensure()
            self.reader = Reader.open()
            if self.reader is None:
                raise RuntimeError('no reading index')
        if self.d is None:
            self.d = load_dictionary()

    def close(self):
        if self.reader is not None:
            self.reader.close()
            self.reader = None

    def _id(self, word, reading='', meaning=''):
        e = self.d.find(word, reading, meaning)
        return e['id'] if e else None

    def count(self, text, used=()):
        """{entry id (a string, as JSON keys are): times} for one reply: its English (Reader.found) and its Japanese
        (japanese). used: the word state's words the reply uses (fwlib.read_replies' script-aware matching). Without
        an index this raises; a reply that can't be counted counts nothing, so one odd reply never stops the rest."""
        self._open()
        out = collections.Counter()
        try:
            for i, n in self.reader.found(text).items():
                out[str(self.reader.ids[i])] += n
            for id_ in self.japanese(text, used):
                out[str(id_)] += 1
        except Exception as error:
            if not self.failed:
                print(f'Japanese Weave: a reply not counted ({error.__class__.__name__}: {error})', file=sys.stderr)
            self.failed += 1
            return {}
        return dict(out)

    def japanese(self, text, used=()):
        """The entry ids of the Japanese words a reply has, once per use: its <abbr>-glossed words when it has any,
        else the word state's words it uses (used), each once."""
        tagged = glossed(text)
        if tagged:
            return [i for i in (self._id(*t) for t in tagged) if i is not None]
        out = []
        for ja in used:
            if ja not in self.ids:
                w = self.words.get(ja) or {}
                self.ids[ja] = self._id(ja, w.get('kana', ''), w.get('en', ''))
            if self.ids[ja] is not None:
                out.append(self.ids[ja])
        return out


# ---- intake: the words your reading nominates ----

def starting_k(know, entry, word=None):
    """A word's knowledge on the log-odds scale before it comes in (k: its lookup chance is sigmoid(offset - k)): its
    estimate in knowledge.json when the word state has it (every word of the state is scored, new ones too) or a
    probe or a review card asked about this entry (knowledge.json keeps those words too, with the entry's id), else the
    starting value of a new word with this dictionary entry (knowledge.prior_estimate: the fitted prior by the word's own
    features: frequency rank, kanji known, script, word type, length), else the neutral prior."""
    import knowledge
    estimate = ((know or {}).get('words') or {}).get(word['ja'] if word else entry['ja'])
    if isinstance(estimate, dict) and isinstance(estimate.get('k'), (int, float)) and (
            word is not None or estimate.get('probed') is True and estimate.get('id') == entry.get('id')):
        return estimate['k']
    prior = knowledge.prior_estimate(know or {}, 'new', entry=entry)
    return prior[0] if prior else knowledge.prior(None)


def is_number(entry):
    """A number and a counter (二人, 三時), which the weave leaves in digits (probe.is_number)."""
    import probe
    return probe.is_number(entry)


STEP = 0.5  # the starting chance ranks nominations in steps of this much log-odds from knowledge.TARGET_P


def nominations(state, exposure, know=None, today=None, d=None):
    """The words your reading nominates for intake, best first (fwlib.newcomers), or None while the counts cover fewer
    than fwlib.ELIGIBLE_DAYS days, when no word could be eligible yet (intake keeps the word list's order).

    Eligible: a dictionary entry that can stand in for English (swap), not a number, that came up on ELIGIBLE_DAYS
    different days of the last READING_DAYS (exposure: the exposure index, fwlib.reading_days), with no spelling in
    use (a known or learning word of the state, hidden ones included) and not known at a glance by its starting chance
    (starting_k under fwlib.FREE_P: such a word is labeled known, not taught). Ranked by those days, most first (as in
    the simulation behind PLAN.md section 6, where days ranked first kept the admitted words coming back), then its
    starting chance, nearest knowledge.TARGET_P first in steps of STEP log-odds (so only a real difference counts:
    with the prior by origin every new word ties), then general frequency (the dictionary's rank). Each is the state's
    word when it has one (in the queue), else the entry as a word with its id (fwlib.take_in adds it)."""
    import knowledge
    today = today or fwlib.today()
    first = fwlib.add_days(today, 1 - fwlib.READING_DAYS)
    counted = [day for day in (exposure.get('reading') or {}) if first <= day <= today]
    if len(counted) < fwlib.ELIGIBLE_DAYS:
        return None
    d = d or load_dictionary()
    by_key = {}
    for w in state['words']:
        by_key.setdefault(fwlib.key(w['ja']), w)
    ranked, seen = [], set()
    for id_, days in fwlib.reading_days(exposure, today).items():
        e = d.get(int(id_)) if days >= fwlib.ELIGIBLE_DAYS and str(id_).isdigit() else None
        if e is None or not e['swap'] or e['ja'] in seen or is_number(e):
            continue
        mine = [w for w in (by_key.get(fwlib.key(s)) for s in [e['ja']] + e['forms']) if w is not None]
        if any(w['status'] != 'queue' for w in mine):
            continue
        word = mine[0] if mine else {'ja': e['ja'], 'en': e['en'], 'hint': e['romaji'], 'kana': e['kana'],
                                     'id': e['id']}
        p = knowledge.sigmoid(-starting_k(know, e, mine[0] if mine else None))
        if p < fwlib.FREE_P:
            continue
        seen.add(e['ja'])
        band = round(abs(knowledge.logit(p) - knowledge.logit(knowledge.TARGET_P)) / STEP)
        ranked.append((-days, band, e['rank'], word))
    ranked.sort(key=lambda r: r[:3])
    return [r[3] for r in ranked]
