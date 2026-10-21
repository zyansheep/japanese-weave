"""What the browser extension needs from the plugin's data files, sent by the native host (fw_host.py) so the extension
carries no dictionary (docs/swap-prompt.md, "The web pipeline"). Python 3.9+, standard library only.

- For each word in use (the state reply): its English glosses as the matcher indexes them (matcher.gloss_alternatives
  of its gloss), which of them are safe (data/senses.tsv: code may swap them without a model's check), and its
  dictionary entry (data/dictionary.tsv), whose id the web's reading counts are kept under.
- For counting what you read on the web (NEXT_STEPS step 13; reading.py counts chat): the safe glosses of every
  swappable entry (reading_index), sent once per version of the two files.

  webdata.add_word_data(words, state)   # the state reply's words, each given glosses, safe and id
  webdata.reading_version()             # changes when either file does
  webdata.reading_index()               # {"version", "entries": [[id, pos, "gloss;gloss"], ...]} in rank order
"""
import hashlib
import sys

import dictionary
import matcher
import reading
import senses

FORMAT = 1  # of the reading index: a change makes the extension ask for it again


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
