"""Sense flags (data/senses.tsv, written by tools/sense-flags.mjs; docs/matcher.md, "Sense flags"). Python 3.9+,
standard library only.

For a pair of an entry and one of its English glosses: safe ('s') or doubtful ('d'). Safe means that wherever that
English appears in ordinary prose, this Japanese word renders it correctly almost always, so code may count or swap a
span the matcher found (matcher.match_text) without a model reading the sentence. A pair the file doesn't have, and any
gloss of closed-class English (data/english.json "function"), is doubtful.

  flags = senses.load()
  senses.sense_flag(flags, {'ja': '犬', 'kana': 'いぬ'}, 'dog') == 's'
  senses.safe_options(flags, span)   # a span of matcher.match_text: whether code may take each option alone

The same lookups as extension/src/senses.js; tests/senses_cases.json holds the vectors both run.
"""
from pathlib import Path
import re

import matcher

PATH = Path(__file__).resolve().parent.parent / 'data' / 'senses.tsv'
FIELDS = ('id', 'ja', 'kana', 'safe', 'doubtful')  # one line per entry; glosses separated by ;
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
_KATAKANA = {c: c - 0x60 for c in range(ord('ァ'), ord('ヶ') + 1)}


def hiragana(text):
    return str(text or '').translate(_KATAKANA)


def function_gloss(gloss):
    """A gloss made only of closed-class words ("about", "this one", "there is"): never safe."""
    words = _WORD.findall(str(gloss).lower().replace('’', "'"))
    return bool(words) and all(matcher._bare(w) in matcher.FUNCTION for w in words)


def parse(text):
    """The file's text -> {(ja, gloss): [(kana, flag, id)]}."""
    lines = text.split('\n')
    start = next((i for i, line in enumerate(lines) if not line.startswith('#')), len(lines))
    if start >= len(lines) or tuple(lines[start].split('\t')) != FIELDS:
        raise ValueError(f'senses: fields {lines[start] if start < len(lines) else None}, expected {list(FIELDS)}')
    flags = {}
    for line in lines[start + 1:]:
        if not line:
            continue
        id_, ja, kana, safe, doubtful = (line.split('\t') + ['', ''])[:5]
        for flag, glosses in (('s', safe), ('d', doubtful)):
            for gloss in glosses.split(';') if glosses else ():
                flags.setdefault((ja, gloss), []).append((kana, flag, int(id_) if id_ else None))
    return flags


_loaded = {}


def load(path=None):
    """The flags at path (default data/senses.tsv), read once per process."""
    path = Path(path or PATH)
    if path not in _loaded:
        _loaded[path] = parse(path.read_text(encoding='utf-8'))
    return _loaded[path]


def sense_flag(flags, entry, gloss):
    """The flag of an entry ({'ja', 'kana'?}) for one of its glosses as the matcher matched it: 's', 'd', or None when
    the file doesn't have it. Homographs written alike are told apart by kana when the entry has it; otherwise all of
    them must be safe."""
    if function_gloss(gloss):
        return 'd'
    rows = flags.get((entry['ja'], gloss))
    if not rows:
        return None
    kana = hiragana(entry.get('kana', ''))
    own = [r for r in rows if r[0] == kana] if kana else []
    return 's' if all(r[1] == 's' for r in (own or rows)) else 'd'


def safe_options(flags, span):
    """For each option of a span (matcher.match_text), whether code may take it alone: never in a span of
    closed-class English, else when its pair is flagged safe."""
    return [not span['function'] and sense_flag(flags, o, g) == 's' for o, g in zip(span['options'], span['glosses'])]
