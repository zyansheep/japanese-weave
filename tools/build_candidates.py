#!/usr/bin/env python3
"""Builds tools/candidates.csv: JMdict common words that can be swapped into an English sentence (nouns, adverbs,
conjunctions, adjectives, a few expressions), ranked by the geometric mean of their JPDB and BCCWJ frequency ranks.
Each candidate is then reviewed (tools/curation/out-*.csv: tier, gloss, romaji fixes) and tools/assemble_lexicon.py
writes data/lexicon.csv.

  python3 tools/build_candidates.py --jmdict jmdict-eng-common.json --jpdb jpdb_freq.csv --bccwj term_meta_bank_1.json

Sources: JMdict (EDRDG, CC BY-SA 4.0) via jmdict-simplified; JPDB v2.2 and BCCWJ frequency lists via
Kuuuube/yomitan-dictionaries.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from kana import romaji, hiragana, is_kana  # noqa: E402

KEEP_POS = {'n', 'adv', 'adv-to', 'conj', 'adj-i', 'adj-na', 'adj-no', 'n-t', 'n-adv', 'int', 'exp', 'adj-pn', 'vs', 'adj-t'}
DROP_POS = {'prt', 'aux', 'aux-v', 'aux-adj', 'cop', 'pref', 'suf', 'n-suf', 'n-pref', 'ctr', 'num', 'pn', 'unc'}
DROP_MISC = {'arch', 'obs', 'rare', 'vulg', 'X', 'derog', 'sens', 'obsc', 'hon', 'hum', 'pol', 'abbr', 'joc', 'male',
             'fem', 'yoji', 'proverb', 'id', 'sl', 'net-sl', 'char', 'dated', 'form'}
CJK = re.compile(r'[々一-鿿]')


def short_gloss(sense):
    out = []
    for g in sense['gloss']:
        text = re.sub(r'\s*\([^)]*\)', '', g['text']).strip()
        text = re.sub(r'^to ', '', text) if g['text'].startswith('to ') and 'v' in ''.join(sense['partOfSpeech']) else text
        if text and text not in out and len(text) <= 28:
            out.append(text)
        if len(out) == 2:
            break
    return ' / '.join(out)


def load_jpdb(path):
    ranks = {}
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            for key, col in (((row['term'], hiragana(row['reading'])), 'frequency'),
                             ((row['reading'], hiragana(row['reading'])), 'kana_frequency')):
                try:
                    r = int(row[col])
                except (TypeError, ValueError):
                    continue
                ranks[key] = min(r, ranks.get(key, r))
    return ranks


def load_bccwj(path):
    ranks = {}
    for term, _, meta in json.load(open(path, encoding='utf-8')):
        key = (term, hiragana(meta.get('reading', term)))
        r = int(meta['frequency'])
        ranks[key] = min(r, ranks.get(key, r))
    return ranks


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--jmdict', required=True)
    p.add_argument('--jpdb', required=True)
    p.add_argument('--bccwj', required=True)
    p.add_argument('--out', default=str(Path(__file__).resolve().parent / 'candidates.csv'))
    p.add_argument('--limit', type=int, default=4000)
    args = p.parse_args()
    jpdb, bccwj = load_jpdb(args.jpdb), load_bccwj(args.bccwj)
    words = json.load(open(args.jmdict, encoding='utf-8'))['words']
    rows = {}
    for w in words:
        sense = w['sense'][0]
        pos = set(sense['partOfSpeech'])
        if not pos & KEEP_POS or pos & DROP_POS or any(p.startswith('v') and p not in ('vs', 'vt', 'vi') for p in pos):
            continue
        if set(sense['misc']) & DROP_MISC:
            continue
        uk = 'uk' in sense['misc']
        kanji = [k['text'] for k in w['kanji'] if k['common']] or [k['text'] for k in w['kanji']]
        kana_forms = [k for k in w['kana'] if k['common']] or w['kana']
        if not kana_forms:
            continue
        written = kana_forms[0]['text'] if uk or not kanji else kanji[0]
        reading = next((k['text'] for k in kana_forms
                        if '*' in k['appliesToKanji'] or written in k['appliesToKanji']), kana_forms[0]['text'])
        reading = hiragana(reading)
        if is_kana(written) and re.search('[ァ-ヺ]', written):
            continue  # katakana loanwords are English already
        if not (CJK.search(written) or is_kana(written)) or len(written) > 6 or len(reading) < 2 or re.search('[0-9０-９]', written):
            continue
        gloss = short_gloss(sense)
        if not gloss or gloss[:1].isupper() and not gloss.startswith('I '):
            continue  # proper-noun-ish glosses
        # Kana-only rows collide with particles and homographs (殻 vs the particle から), so kanji words match
        # only their written forms.
        forms = {written, *kanji[:2]} if not is_kana(written) else {written, reading}
        jr = min((jpdb[(f, reading)] for f in forms if (f, reading) in jpdb), default=None)
        br = min((bccwj[(f, reading)] for f in forms if (f, reading) in bccwj), default=None)
        if jr is None and br is None:
            continue
        score = math.sqrt(jr * br) if jr and br else 3 * (jr or br)
        hint = romaji(reading)
        row = {'ja': written, 'kana': reading, 'en': gloss, 'hint': hint, 'pos': ','.join(sorted(pos)),
               'jpdb': jr or '', 'bccwj': br or '', 'score': round(score), 'uk': int(uk)}
        if written not in rows or rows[written]['score'] > row['score']:
            rows[written] = row
    ranked = sorted(rows.values(), key=lambda r: r['score'])[:args.limit]
    with open(args.out, 'w', encoding='utf-8', newline='') as f:
        out = csv.DictWriter(f, fieldnames=list(ranked[0]))
        out.writeheader()
        out.writerows(ranked)
    print(f'{len(ranked)} candidates -> {args.out}')


if __name__ == '__main__':
    main()
