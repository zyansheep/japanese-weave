"""The dictionary (data/dictionary.tsv, built by tools/build_dictionary.py; docs/dictionary.md): every common JMdict
entry with its frequency rank, for finding the entry a word belongs to and for indexing English glosses. Python 3.9+,
standard library only.

  d = dictionary.load()
  d.find('なります')['ja'] == 'なる'       # as written or a conjugated form (inflection.py), as knowledge.py matches
  d.written('殆ど')[0]['ja'] == 'ほとんど'   # as written: the written form or another spelling
  d.reading('カレー'), d.romaji('tabun')    # by reading (katakana or hiragana alike) or romaji (macrons optional)

Entries are dicts: id (the JMdict sequence number, stable), ja (as normally written), kana (reading, hiragana),
romaji, en (a short gloss), glosses ([[gloss, ...] per sense], the first sense first), pos (JMdict part-of-speech
tags, the first sense's first), score (geometric mean of the JPDB and BCCWJ ranks, None without either), jpdb, bccwj,
common (bit 1: the written form is marked common in JMdict, bit 2: the reading is), loan (JMdict's source languages,
'kata' for a katakana word without one, '' if not a loanword), swap (build_candidates.py's filters: can stand in for
an English word), forms (other spellings), rank (1 is the most frequent; entries without a score come last) and kanji
(the kanji of the written form). The file is in rank order.

Loading splits the file into lines and nothing more; an entry becomes a dict when it is asked for, and each index is
built on first use, so a caller pays only for the lookups it makes (docs/dictionary.md has the timings).
"""
import gc
from pathlib import Path
import re
import unicodedata

import inflection

PATH = Path(__file__).resolve().parent.parent / 'data' / 'dictionary.tsv'
FIELDS = ('id', 'ja', 'kana', 'romaji', 'en', 'glosses', 'pos', 'score', 'jpdb', 'bccwj', 'common', 'loan', 'swap',
          'forms')
SENSES, GLOSSES, LIST = '|', ';', ','  # separators inside the glosses, pos and forms cells
NUMBERS = ('id', 'score', 'jpdb', 'bccwj', 'common')
KANJI = re.compile('[㐀-䶿一-鿿豈-﫿]')
JAPANESE = re.compile('[々ぁ-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]')
WRITTEN, READING, SPELLING = 0, 1, 2  # how a spelling belongs to an entry, best first
COMMONEST = {'the', 'and', 'for', 'not', 'one', 'with', 'something', 'someone', 'thing'}
_KATAKANA = {c: c - 0x60 for c in range(ord('ァ'), ord('ヶ') + 1)}  # as kana.hiragana
_SPACE = re.compile(r'[^\S\n]+')


def fold_all(words):
    """inflection.fold of each word, done on one joined string (a fraction of the time of one call per word)."""
    words = [w if unicodedata.is_normalized('NFKC', w) else unicodedata.normalize('NFKC', w) for w in words]
    return _SPACE.sub('', '\n'.join(words)).translate(_KATAKANA).lower().split('\n')


def is_kana(text):
    return bool(text) and all('ぁ' <= c <= 'ゖ' or 'ァ' <= c <= 'ヺ' or c in 'ーゝゞヽヾ' for c in text)


def english_words(*texts):
    """The English words of glosses, for telling homographs apart: words of three letters or more, by their first
    four (favor and favour, switch and switches alike), without the commonest."""
    words = re.findall('[a-z]+', ' '.join(texts).lower())
    return {w[:4] for w in words if len(w) >= 3 and w not in COMMONEST}


def kind(pos, ja, kana):
    """An entry's kind for inflection.py (V1, V5, VK, VS, ADJ or NOUN) from its part-of-speech tags; from how it
    ends (inflection.word_class) only for verb tags inflection.py has no rules for (vz, v2, v4)."""
    k = 0
    for tag in pos:
        if tag.startswith('v1'):
            k |= inflection.V1
        elif tag.startswith('v5'):
            k |= inflection.V5
        elif tag == 'vk':
            k |= inflection.VK
        elif tag in ('vs-i', 'vs-s'):
            k |= inflection.VS
        elif tag in ('adj-i', 'adj-ix'):
            k |= inflection.ADJ
    if k or not any(t.startswith('v') and t not in ('vs', 'vt', 'vi') for t in pos):
        return k or inflection.NOUN
    return inflection.word_class(ja, kana)


class Unbuffered:
    """Garbage collection paused while an index of many small objects is built: collecting then only costs time."""

    def __enter__(self):
        self.enabled = gc.isenabled()
        gc.disable()

    def __exit__(self, *exc):
        if self.enabled:
            gc.enable()


class Dictionary:
    def __init__(self, text):
        lines = text.split('\n')
        start = next(i for i, line in enumerate(lines) if not line.startswith('#'))
        self.comments = [line[1:].strip() for line in lines[:start]]
        header = lines[start].split('\t')
        if tuple(header) != FIELDS:
            raise ValueError(f'dictionary fields {header}, expected {list(FIELDS)}')
        self.lines = [line for line in lines[start + 1:] if line]
        self.entries = [None] * len(self.lines)
        self._ids = self._spellings = self._romaji = None
        self._kinds, self._found = {}, {}

    def __len__(self):
        return len(self.lines)

    def __iter__(self):
        return (self.entry(i) for i in range(len(self.lines)))

    def entry(self, index):
        """The entry at a position in rank order (0 is the most frequent)."""
        e = self.entries[index]
        if e is None:
            e = dict(zip(FIELDS, self.lines[index].split('\t')))
            for f in NUMBERS:
                e[f] = int(e[f]) if e[f] else None
            e['common'] = e['common'] or 0
            e['glosses'] = [sense.split(GLOSSES) for sense in e['glosses'].split(SENSES)] if e['glosses'] else []
            e['pos'] = e['pos'].split(LIST) if e['pos'] else []
            e['forms'] = e['forms'].split(LIST) if e['forms'] else []
            e['swap'] = e['swap'] == '1'
            e['rank'] = index + 1
            e['kanji'] = list(dict.fromkeys(KANJI.findall(e['ja'])))
            self.entries[index] = e
        return e

    def get(self, id):
        """The entry with this JMdict sequence number, or None."""
        if self._ids is None:
            with Unbuffered():
                self._ids = {int(line[:line.index('\t')]): i for i, line in enumerate(self.lines)}
        i = self._ids.get(int(id))
        return None if i is None else self.entry(i)

    def spellings(self):
        """{spelling folded as inflection.fold: [(index, WRITTEN, READING or SPELLING)]}, an entry once per
        spelling (its best way)."""
        if self._spellings is None:
            with Unbuffered():
                owners, words = [], []
                for i, line in enumerate(self.lines):
                    _id, ja, kana, _rest = line.split('\t', 3)
                    owners += [(i, WRITTEN), (i, READING)]
                    words += [ja, kana]
                    forms = line[line.rindex('\t') + 1:]
                    if forms:
                        forms = forms.split(LIST)
                        owners += [(i, SPELLING)] * len(forms)
                        words += forms
                index = {}
                for key, owner in zip(fold_all(words), owners):
                    found = index.get(key)
                    if found is None:
                        index[key] = [owner]
                    elif owner[0] != found[-1][0]:  # a katakana written form folds like its reading
                        found.append(owner)
                self._spellings = index
        return self._spellings

    def _kind(self, index):
        if index not in self._kinds:
            e = self.entry(index)
            self._kinds[index] = kind(e['pos'], e['ja'], e['kana'])
        return self._kinds[index]

    def _readings(self, index):
        e = self.entry(index)
        return {inflection.fold(e['kana'])} | {inflection.fold(f) for f in e['forms'] if is_kana(f)}

    def written(self, word):
        """Entries written this way (their written form or another spelling, katakana and hiragana alike): written
        forms before other spellings, then by rank."""
        found = [(via, i) for i, via in self.spellings().get(inflection.fold(word), ()) if via != READING]
        return [self.entry(i) for via, i in sorted(found)]

    def reading(self, kana):
        """Entries read this way (their reading or another kana spelling), by rank."""
        key = inflection.fold(kana)
        return [self.entry(i) for i in sorted({i for i, via in self.spellings().get(key, ())
                                               if via == READING or key in self._readings(i)})]

    def romaji(self, text):
        """Entries whose romaji folds to the same letters (fwlib.roman_key: tōkyō, toukyou and tokyo alike), by
        rank."""
        import fwlib  # here, not at the top: fwlib may come to import this module
        if self._romaji is None:
            with Unbuffered():
                self._romaji = {}
                for i, line in enumerate(self.lines):
                    self._romaji.setdefault(fwlib.roman_key(line.split('\t', 4)[3]), []).append(i)
        return [self.entry(i) for i in self._romaji.get(fwlib.roman_key(text), ())]

    def find(self, word, kana='', en=''):
        """The entry a written word is, as written or a form of it (なります is なる, 確認します is 確認), or None.
        As inflection.Lexicon.entry: the fewest steps back from the word first, then its written form before its
        reading before another spelling, then rank; a candidate must be of a kind the form can be (いります is 要る,
        never 居る). With kana, an entry with that reading comes before one without. With en (a gloss, such as the
        word state's), entries that tie on all that (homographs: つける attach and つける switch on) go to the one
        whose glosses share the most English words with it. An affix written as on a card (〜時) is the word."""
        key = (word, kana, en)
        if key not in self._found:
            self._found[key] = self._find(word.strip('〜～~') or word, kana, en)
        index = self._found[key]
        return None if index is None else self.entry(index)

    def _find(self, word, kana, en):
        spellings, found = self.spellings(), []
        reading = inflection.fold(kana) if kana else ''
        for form, kinds, steps in inflection.deinflect(word):
            if found and steps > found[0][0]:
                break
            if steps and not kinds & inflection.NOUN and len(form) < 2:
                continue
            for index, via in spellings.get(inflection.fold(form), ()):
                if kinds & inflection.NOUN or kinds & self._kind(index):
                    found.append((steps, bool(reading) and reading not in self._readings(index), via, index))
        if not found:
            return None
        best = min(found)
        tied = [c[3] for c in found if c[:3] == best[:3]]
        if not en or len(tied) == 1:
            return best[3]
        wanted = english_words(en)
        shared = lambda i: len(wanted & english_words(self.entry(i)['en'], *sum(self.entry(i)['glosses'], [])))
        return max(tied, key=lambda i: (shared(i), -i))

    def lookup(self, word):
        """The entry for a word as written, in kana, conjugated, or in romaji, or None."""
        if JAPANESE.search(word):
            return self.find(word)
        found = self.romaji(word)
        return found[0] if found else None

    def english(self):
        """Every English gloss, for an index from English to entries: (gloss, entry index, sense number), in rank
        order, each entry's first sense first."""
        for i, line in enumerate(self.lines):
            glosses = line.split('\t', 6)[5]
            if glosses:
                for number, sense in enumerate(glosses.split(SENSES)):
                    for gloss in sense.split(GLOSSES):
                        yield gloss, i, number


_loaded = {}


def load(path=None):
    """The dictionary at path (default data/dictionary.tsv), read once per process."""
    path = Path(path or PATH)
    if path not in _loaded:
        _loaded[path] = Dictionary(path.read_text(encoding='utf-8'))
    return _loaded[path]
