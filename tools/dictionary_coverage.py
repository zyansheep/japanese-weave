#!/usr/bin/env python3
"""Checks that the dictionary (data/dictionary.tsv) has an entry for every word a state directory knows about: the
word list (data/lexicon.csv), the words in state.json, the Anki words (anki.csv), and every word opened in the gloss
log (interactions.sqlite), above all those the word state doesn't match (the unlisted words of knowledge.py).
Reads only; point it at a copy of a state directory, never at a live one.

  python3 tools/dictionary_coverage.py /tmp/state-copy [--dictionary data/dictionary.tsv] [--json]

A word resolves when dictionary.find() finds its entry: as written, by another spelling or its reading, or through
the inflection rules (inflection.py). A whole sentence opened as one gloss has no entry by design and is counted
apart.
"""
import argparse
import csv
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import dictionary  # noqa: E402
import inflection  # noqa: E402

PUNCTUATION = set('。？！?!.')  # as knowledge.PUNCTUATION: a sentence translation is opened on its end mark
SENTENCE = re.compile(r'[。、，,！？!?\s]')


def how(d, word, entry):
    """How a word reached its entry: written, spelling (another spelling), reading, or form (a conjugated form)."""
    folded = inflection.fold(word.strip('〜～~') or word)
    if folded == inflection.fold(entry['ja']):
        return 'written'
    if any(folded == inflection.fold(f) for f in entry['forms'] if not dictionary.is_kana(f)):
        return 'spelling'
    if folded in d._readings(entry['rank'] - 1):
        return 'reading'
    return 'form'


def resolve(d, words):
    """For (word, kana, en) triples (kana and en may be ''): {n, resolved, how: {way: n}, misses: [word],
    entries: {word: [ja, kana, id, way]}, other_reading: [[word, kana, ja, kana]] (found, but not under the word's
    reading), unrelated: [[word, en, ja, entry en]] (the word's gloss and the entry's share no English word: perhaps
    the wrong entry; check by hand)}."""
    out = {'n': 0, 'resolved': 0, 'how': {}, 'misses': [], 'entries': {}, 'other_reading': [], 'unrelated': []}
    for word, kana, en in words:
        out['n'] += 1
        entry = d.find(word, kana, en)
        if entry is None:
            out['misses'].append(word)
            continue
        out['resolved'] += 1
        way = how(d, word, entry)
        out['how'][way] = out['how'].get(way, 0) + 1
        out['entries'][word] = [entry['ja'], entry['kana'], entry['id'], way]
        if kana and inflection.fold(kana) not in d._readings(entry['rank'] - 1):
            out['other_reading'].append([word, kana, entry['ja'], entry['kana']])
        if dictionary.english_words(en) and not dictionary.english_words(en) & dictionary.english_words(
                entry['en'], *sum(entry['glosses'], [])):
            out['unrelated'].append([word, en, entry['ja'], entry['en']])
    return out


def lexicon_words(path):
    """(ja, kana, en) of a lexicon CSV (data/lexicon.csv, anki.csv); an en of 'none' (kanji cards) counts as none."""
    with open(path, encoding='utf-8', newline='') as f:
        return [(r['ja'], r.get('kana') or '', '' if r.get('en') == 'none' else r.get('en') or '')
                for r in csv.DictReader(f) if r['ja'].strip()]


def log_words(path):
    """{word: opens} of every word opened in the gloss log."""
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return dict(con.execute('select word, count(*) from interactions group by word'))
    finally:
        con.close()


def report(home, dictionary_path=None, lexicon_path=None):
    home = Path(home)
    d = dictionary.load(dictionary_path)
    out = {'dictionary': len(d)}
    listed = lexicon_words(lexicon_path or ROOT / 'data' / 'lexicon.csv')
    out['lexicon'] = resolve(d, listed)
    # the entry found is the one that carries the lexicon's reviewed gloss (the build puts it there)
    out['lexicon']['other_gloss'] = [w for w, kana, en in listed if d.find(w, kana, en)['en'] != en]
    state = json.loads((home / 'state.json').read_text(encoding='utf-8'))
    words = [(w['ja'], w.get('kana') or '', '' if w.get('en') == 'none' else w.get('en') or '')
             for w in state['words']]
    out['state'] = resolve(d, words)
    out['state_not_in_lexicon'] = resolve(d, [w for w in words if w[0] not in {ja for ja, _, _ in listed}])
    if (home / 'anki.csv').exists():
        out['anki'] = resolve(d, lexicon_words(home / 'anki.csv'))
    if (home / 'interactions.sqlite').exists():
        opened = log_words(home / 'interactions.sqlite')
        matched = inflection.lexicon(state['words']).entry  # as knowledge.py matches: every word in the state
        unlisted = {w: n for w, n in opened.items() if w not in PUNCTUATION and matched(w) is None}
        sentences = {w: n for w, n in unlisted.items() if SENTENCE.search(w)}
        out['log'] = resolve(d, [(w, '', '') for w in opened if w not in PUNCTUATION and w not in sentences])
        out['log']['translations'] = sum(n for w, n in opened.items() if w in PUNCTUATION)
        out['unlisted'] = resolve(d, [(w, '', '') for w in unlisted if w not in sentences])
        out['unlisted']['opens'] = sum(n for w, n in unlisted.items() if w not in sentences)
        out['unlisted']['sentences'] = sorted(sentences)
    if (home / 'knowledge.json').exists():
        top = json.loads((home / 'knowledge.json').read_text(encoding='utf-8')).get('unlisted', {}).get('top', [])
        out['knowledge_unlisted'] = resolve(d, [(w, '', '') for w, _ in top if not SENTENCE.search(w)])
        out['knowledge_unlisted']['sentences'] = [w for w, _ in top if SENTENCE.search(w)]
    return out


def text(out):
    lines = [f'dictionary: {out["dictionary"]} entries']
    names = {'lexicon': 'data/lexicon.csv', 'state': 'state.json words',
             'state_not_in_lexicon': 'state.json words not in the lexicon (Anki)', 'anki': 'anki.csv words',
             'log': 'words opened in the log', 'unlisted': 'opened words the word state does not match',
             'knowledge_unlisted': "knowledge.json's unlisted top words"}
    for key, name in names.items():
        if key not in out:
            continue
        r = out[key]
        share = 100 * r['resolved'] / r['n'] if r['n'] else 100
        ways = ', '.join(f'{n} {way}' for way, n in sorted(r['how'].items(), key=lambda kv: -kv[1]))
        lines.append(f'{name}: {r["resolved"]}/{r["n"]} resolve ({share:.1f}%; {ways})')
        if r['misses']:
            lines.append(f'  misses: {" ".join(r["misses"])}')
        if r['other_reading']:
            lines.append('  found under another reading: ' +
                         ', '.join(f'{w} {k} -> {ja} {kana}' for w, k, ja, kana in r['other_reading']))
        if r['unrelated']:
            lines.append('  glosses share no English word (check by hand): ' +
                         '; '.join(f'{w} "{en}" -> {ja} "{other}"' for w, en, ja, other in r['unrelated']))
        if r.get('other_gloss'):
            lines.append(f'  found an entry without the lexicon\'s gloss: {" ".join(r["other_gloss"])}')
        if r.get('sentences'):
            lines.append(f'  whole sentences, no entry by design: {len(r["sentences"])}')
        if key == 'unlisted':
            lines.append('  ' + ', '.join(f'{w} -> {v[0]} ({v[3]})' for w, v in r['entries'].items()))
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('home', help='a copy of a state directory (state.json, knowledge.json, anki.csv, '
                                'interactions.sqlite)')
    p.add_argument('--dictionary', help='default: data/dictionary.tsv')
    p.add_argument('--json', action='store_true', help='the whole report as JSON')
    args = p.parse_args()
    out = report(args.home, args.dictionary)
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else text(out))


if __name__ == '__main__':
    main()
