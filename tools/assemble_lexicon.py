#!/usr/bin/env python3
"""Writes data/lexicon.csv from the reviewed candidates in tools/curation/out-*.csv.

Each reviewed row has a tier: A (connectors and adverbs that replace an English word), T (words technical chat
uses), B (everyday words), C (poor fits), X (dropped), and the candidate's frequency score (geometric mean of its
JPDB and BCCWJ ranks). Teaching order is that score scaled by tier, so connectors and technical words come early and
poor fits late; the list is cut at SIZE words.

  python3 tools/assemble_lexicon.py
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEIGHT = {'A': 0.3, 'T': 0.3, 'B': 1.0, 'C': 3.0}
SIZE = 1500


def main():
    rows = []
    for path in sorted((ROOT / 'tools' / 'curation').glob('out-*.csv'), key=lambda p: int(p.stem.split('-')[1])):
        rows += list(csv.DictReader(open(path, encoding='utf-8')))
    kept = sorted((r for r in rows if r['tier'] in WEIGHT), key=lambda r: float(r['score']) * WEIGHT[r['tier']])
    seen, out = set(), []
    for r in kept:
        if r['ja'] not in seen:
            seen.add(r['ja'])
            out.append({'ja': r['ja'], 'en': r['en'].strip(), 'hint': r['hint'].strip(), 'kana': r['kana']})
    out = out[:SIZE]
    with open(ROOT / 'data' / 'lexicon.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['ja', 'en', 'hint', 'kana'])
        writer.writeheader()
        writer.writerows(out)
    print(f'{len(out)} words -> data/lexicon.csv')


if __name__ == '__main__':
    main()
