"""What the browser extension needs from the plugin's data files, sent by the native host (fw_host.py) so the extension
carries no dictionary (docs/swap-prompt.md, "The web pipeline"). Python 3.9+, standard library only.

- For each word in use (the state reply): its English glosses as the matcher indexes them (matcher.gloss_alternatives
  of its gloss), which of them are safe (data/senses.tsv: code may swap them without a model's check), and its
  dictionary entry (data/dictionary.tsv), whose id the web's reading counts are kept under.
- For counting what you read on the web (NEXT_STEPS step 13; reading.py counts chat): the safe glosses of every
  swappable entry (reading_index), sent once per version of the two files.
- For +1 and +2 on the web: the dictionary words they may add (dictionary_words), sent once per version.

  webdata.add_word_data(words, state)   # the state reply's words, each given glosses, safe and id
  webdata.reading_version()             # changes when either file does
  webdata.reading_index()               # {"version", "entries": [[id, pos, "gloss;gloss"], ...]} in rank order
  webdata.dictionary_version(state, know)   # changes when the words kept out, the fitted prior or the files do
  webdata.dictionary_words(state, know)     # {"version", "entries": [[id, ja, kana, romaji, "gloss;gloss", k, sd], ...]}
"""
import hashlib
import json
import sys

import dictionary
import fwlib
import knowledge
import matcher
import reading
import senses

FORMAT = 1  # of the reading index: a change makes the extension ask for it again
DICTIONARY_FORMAT = 1  # likewise, of the dictionary words
DICTIONARY_WORDS = 2500  # the most frequent that qualify: about 170 KB of the 1 MB a native message may carry
GLOSSES_MAX = 4  # of a dictionary word, its first safe ones
KATAKANA = ('\u30a0', '\u30ff')
# Medical, legal and financial entries stay out, as for chat's topical words (weave.py SENSITIVE_FIELDS).
SENSITIVE = frozenset(('med', 'pharm', 'dent', 'vet', 'psy', 'anat', 'physiol', 'embryo', 'law', 'finc', 'stockm', 'econ', 'bus'))


def _flags():
    """The sense flags, or None without the file (every gloss is then doubtful). A file that can't be read is reported
    and treated as none, so the browser still gets its words."""
    if not senses.PATH.exists():
        return None
    try:
        return senses.load(senses.PATH)
    except Exception as error:
        print(f'Japanese Weave: sense flags unreadable ({error.__class__.__name__}: {error})', file=sys.stderr)
        return None


def _dictionary():
    if not dictionary.PATH.exists():
        return None
    try:
        return dictionary.load(dictionary.PATH)
    except Exception as error:
        print(f'Japanese Weave: dictionary unreadable ({error.__class__.__name__}: {error})', file=sys.stderr)
        return None


def add_word_data(words, state=None):
    """Gives each word of the state reply (dicts with ja, en, kana; changed in place) its glosses, the ones of them
    flagged safe, and its entry id: the state word's own (a dictionary word taken in by intake carries it), else the
    dictionary entry it is (dictionary.find on its spelling, reading and gloss, as reading.Tally finds chat's words),
    else none. Returns words."""
    flags, d = _flags(), _dictionary()
    own = {w['ja']: w.get('id') for w in (state or {}).get('words') or [] if isinstance(w, dict) and 'ja' in w}
    for w in words:
        glosses = matcher.gloss_alternatives(w.get('en', ''))
        w['glosses'] = glosses
        w['safe'] = [g for g in glosses if flags is not None and senses.sense_flag(flags, w, g) == 's']
        id_ = own.get(w['ja'])
        if not isinstance(id_, int) and d is not None:
            e = d.find(w['ja'], w.get('kana', ''), w.get('en', ''))
            id_ = e['id'] if e else None
        if isinstance(id_, int) and not isinstance(id_, bool) and id_ > 0:
            w['id'] = id_
    return words


def reading_version():
    """The reading index's version: its format and the dictionary and sense flags files by size and time, as
    reading.signature names what chat's index is built from."""
    parts = [f'f{FORMAT}']
    for path in (dictionary.PATH, senses.PATH):
        try:
            info = path.stat()
            parts.append(f'{path.name}:{info.st_size}:{info.st_mtime_ns}')
        except OSError:
            parts.append(f'{path.name}:-')
    return hashlib.sha1(' '.join(parts).encode()).hexdigest()[:12]


def reading_index():
    """The safe glosses of every swappable dictionary entry, for the extension's reading counts: {version, entries:
    [[id, pos (JMdict codes, comma separated), glosses (separated by ;)]]} in rank order, entries with no safe gloss
    left out. The glosses are chat's (reading.index_entries: the display gloss, then the first sense's others), so a
    page and a chat count an entry by the same English. None without both files: no flags means nothing counts."""
    flags, d = _flags(), _dictionary()
    if flags is None or d is None:
        return None
    rows = []
    swappable = [e for e in d if e['swap']]
    for e, x in zip(swappable, reading.index_entries(swappable)):
        safe = [g for g in x['en'] if senses.sense_flag(flags, {'ja': e['ja'], 'kana': e['kana']}, g) == 's']
        if safe:
            rows.append([e['id'], ','.join(e['pos']), ';'.join(safe)])
    return {'version': reading_version(), 'entries': rows}


def _kept_out(state, day=None):
    """The words dictionary words must not repeat or reveal, from the whole state and not from what the extension gets
    (fwlib.public_state leaves hidden words out, so a held word would look free to offer): every word in use, and every
    word the delayed-start experiment hides now. A word waiting in the queue, hidden by nothing, may be offered, as a
    topical word in chat may."""
    return [w for w in state.get('words') or [] if w.get('status') != 'queue' or fwlib.hidden(w, day)]


def dictionary_version(state, know, day=None):
    """The dictionary words' version, without building them: their format, the two files, the fitted prior they are
    priced by and the words they must leave out."""
    parts = [f'd{DICTIONARY_FORMAT}', reading_version(), json.dumps(know.get('prior'), sort_keys=True),
             sorted(w['ja'] for w in _kept_out(state, day))]
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:12]


def dictionary_words(state, know, day=None, limit=DICTIONARY_WORDS):
    """The words +1 and +2 may add on the web (docs/swap-prompt.md, "Heavier than normal"): {version, entries: [[id, ja,
    kana, romaji, "gloss;gloss", k, sd]]}, the most frequent first, each with its safe glosses (data/senses.tsv), its k
    and sd from the fitted prior as a new word (the planner ranks and prices it by them) and a lookup chance of at least
    fwlib.FREE_P, since these are words to learn. Left out: loanwords, katakana words, numbers and counters, sensitive
    fields, any entry that isn't swappable or has no safe gloss, and (_kept_out) any entry a word in use or a hidden word
    is, by its id, by any spelling of the entry or, for a hidden word, by its reading. None without the files or a
    fitted prior: then +1 and +2 use the words in use alone."""
    flags, d = _flags(), _dictionary()
    if flags is None or d is None or not isinstance(know.get('prior'), dict):
        return {'version': dictionary_version(state, know, day), 'entries': []}
    ids, spellings, readings = set(), set(), set()
    for w in _kept_out(state, day):
        found = d.find(w['ja'], w.get('kana', ''), w.get('en', ''))
        ids.update(x for x in (w.get('id'), found['id'] if found else None) if isinstance(x, int))
        spellings.add(fwlib.key(w['ja']))
        if fwlib.hidden(w, day) and w.get('kana'):
            readings.add(fwlib.key(w['kana']))
    rows = []
    for e in d:
        if len(rows) >= limit:
            break
        if not e['swap'] or e['loan'] or e['id'] in ids or SENSITIVE.intersection(e['field']) or reading.is_number(e):
            continue
        if e['en'].startswith('counter') or all(KATAKANA[0] <= c <= KATAKANA[1] for c in e['ja']):
            continue
        forms = [fwlib.key(x) for x in [e['ja'], *e['forms']]]
        if spellings.intersection(forms) or readings.intersection(forms) or fwlib.key(e['kana']) in readings:
            continue
        safe = [g for g in reading.index_entries([e])[0]['en'] if senses.sense_flag(flags, {'ja': e['ja'], 'kana': e['kana']}, g) == 's']
        guess = knowledge.prior_estimate(know, 'new', None, e) if safe else None
        if guess is None or knowledge.sigmoid(-guess[0]) < fwlib.FREE_P:
            continue
        rows.append([e['id'], e['ja'], e['kana'], e['romaji'], ';'.join(safe[:GLOSSES_MAX]), round(guess[0], 2), round(guess[1], 2)])
    return {'version': dictionary_version(state, know, day), 'entries': rows}
