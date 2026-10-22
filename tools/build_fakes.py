#!/usr/bin/env python3
"""Builds data/fakes.tsv: made-up two-kanji words for the probe (scripts/probe.py), each checked against every
spelling and reading of the full JMdict, with an invented reading.

  python3 tools/build_fakes.py                  # the pinned JMdict of tools/build_dictionary.py, from its cache
  python3 tools/build_fakes.py --check          # check the committed list again (after a JMdict update)

A fake joins two common kanji (among the 600 most frequent in the dictionary's top 10,000 entries, weighted by
frequency), each used where it often stands in common JMdict compounds: the first kanji first in at least 8 of them,
the second second. Each kanji must have one dominant Sino-Japanese reading (at least 60% of its readings in those
compounds, of the shape of an on reading: one mora, perhaps with -u, -i, -n, -ku, -ki, -tsu or -chi), so fakes look
like ordinary compounds (経験, 旅行) rather than mixed ones (目線). The readings come from aligning each common
two-kanji compound's reading with its kanji (a few rounds of expectation maximisation over the possible splits). Left
out, so a fake is neither a word nor readable as one:
- numerals, a first kanji JMdict lists as a prefix (新, 不, 再: 新店 reads as "new shop") and a second it lists as a
  suffix (者, 的, 化);
- any pair that is a JMdict spelling, either way round, or is inside a longer one (産者 is in 生産者);
- any whose invented reading (the two readings joined, with the usual doubled consonant: がく + こう is がっこう) is a
  JMdict reading or kana spelling.
Fakes are drawn at random (seeded, so a rebuild gives the same file) with the more productive kanji more likely, and
no kanji in more than three fakes.

No kana fakes: an invented kana string that JMdict lacks is still often Japanese a reader knows, a conjugated form
(ふとり, こんだ) or a word with a particle (したも, いかも), which a dictionary of base forms can't rule out; a
reader saying yes to one wouldn't be over-claiming. Kanji pairs don't have that problem.
"""
import argparse
import collections
import json
from pathlib import Path
import random
import re
import sys

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / 'scripts'))
from build_dictionary import CACHE, SOURCES, fetch  # noqa: E402
import dictionary  # noqa: E402
from kana import hiragana  # noqa: E402

OUT = ROOT / 'data' / 'fakes.tsv'
COUNT = 300            # fakes in the list
COMMON_KANJI = 600     # the most frequent kanji of the dictionary's top entries
TOP = 10_000           # the dictionary's top entries, as the probe's population
MIN_COMPOUNDS = 8      # a kanji stands first (or second) in at least this many common two-kanji compounds
DOMINANT = 0.6         # share of a kanji's compound readings its main reading must have
PER_KANJI = 3          # fakes a kanji may be in
ROUNDS = 6             # rounds of aligning readings with kanji
SEED = 20261020
NUMERALS = set('〇一二三四五六七八九十百千万億兆')
ON = re.compile('^[あいうえおか-もやゆよら-ろわ][ゃゅょ]?[うんいくきつち]?$')
VOICELESS = set('かきくけこさしすせそたちつてとはひふへほ')
HALF_VOICED = str.maketrans('はひふへほ', 'ぱぴぷぺぽ')


def load_jmdict(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def inventory(words):
    """(every kanji spelling, every reading and kana spelling in hiragana, common two-kanji compounds as
    (first, second, reading), single kanji JMdict lists as a prefix, as a suffix)."""
    spellings, readings, pairs, prefixes, suffixes = set(), set(), [], set(), set()
    for w in words:
        spellings.update(k['text'] for k in w['kanji'])
        readings.update(hiragana(r['text']) for r in w['kana'])
        pos = {p for s in w['sense'] for p in s['partOfSpeech']}
        for k in w['kanji']:
            text = k['text']
            if len(text) == 1 and dictionary.KANJI.match(text):
                if 'pref' in pos:
                    prefixes.add(text)
                if 'suf' in pos:
                    suffixes.add(text)
            if len(text) == 2 and k['common'] and all(dictionary.KANJI.match(c) for c in text):
                kana = [r['text'] for r in w['kana'] if '*' in r['appliesToKanji'] or text in r['appliesToKanji']]
                if kana:
                    pairs.append((text[0], text[1], hiragana(kana[0])))
    return spellings, readings, pairs, prefixes, suffixes


def align(pairs, rounds=ROUNDS):
    """How often each kanji takes each reading first and second in a compound: ({kanji: Counter}, {kanji: Counter}),
    each compound's reading split between its kanji in proportion to how likely each split is."""
    first, second = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for x, y, reading in pairs:
        splits = [(reading[:i], reading[i:]) for i in range(1, len(reading))]
        for a, b in splits:
            first[x][a] += 1 / len(splits)
            second[y][b] += 1 / len(splits)
    for _ in range(rounds):
        f, s = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
        for x, y, reading in pairs:
            splits = [(reading[:i], reading[i:]) for i in range(1, len(reading))]
            scores = [first[x][a] * second[y][b] for a, b in splits]
            total = sum(scores) or 1
            for (a, b), score in zip(splits, scores):
                f[x][a] += score / total
                s[y][b] += score / total
        first, second = f, s
    return first, second


def main_reading(kanji, first, second):
    """A kanji's main reading in compounds and its share of them; a doubled-consonant form (がっ) counts for the
    reading it shortens (がく)."""
    counts = collections.Counter()
    for table in (first.get(kanji, {}), second.get(kanji, {})):
        for reading, n in table.items():
            if n >= 0.5:
                counts[reading] += n
    for reading in [r for r in counts if r.endswith('っ')]:
        full = max((r for r in counts if len(r) == len(reading) and r[:-1] == reading[:-1] and r[-1] in 'くきつち'),
                   key=counts.get, default=None)
        if full:
            counts[full] += counts.pop(reading)
    if not counts:
        return None, 0.0
    reading, n = counts.most_common(1)[0]
    return reading, n / sum(counts.values())


def join(a, b):
    """Two readings as one word: a final -tsu, -chi (or -ku before k) doubles the next consonant, and h becomes p
    after it (発 + 表: はっぴょう)."""
    if len(a) >= 2 and (a[-1] in 'つち' and b[0] in VOICELESS or a[-1] == 'く' and b[0] in 'かきくけこ'):
        return a[:-1] + 'っ' + (b[0].translate(HALF_VOICED) + b[1:] if b[0] in 'はひふへほ' else b)
    return a + b


def common_kanji(d, n=COMMON_KANJI, top=TOP):
    weight = collections.Counter()
    for e in d:
        if e['score'] and e['rank'] <= top:
            for k in e['kanji']:
                weight[k] += 1 / e['score']
    return [k for k, _ in weight.most_common(n)]


def candidates(words, d):
    """[(fake, reading, plausibility)]: every pair the rules in the module docstring allow."""
    spellings, readings, pairs, prefixes, suffixes = inventory(words)
    first, second = align(pairs)
    inside = {s[i:i + 2] for s in spellings if len(s) > 2 for i in range(len(s) - 1)}
    starts, ends = collections.Counter(p[0] for p in pairs), collections.Counter(p[1] for p in pairs)
    reading = {}
    for k in common_kanji(d):
        main, share = main_reading(k, first, second)
        if main and ON.match(main) and share >= DOMINANT and k not in NUMERALS:
            reading[k] = main
    xs = [k for k in reading if starts[k] >= MIN_COMPOUNDS and k not in prefixes]
    ys = [k for k in reading if ends[k] >= MIN_COMPOUNDS and k not in suffixes]
    out = []
    for x in xs:
        for y in ys:
            fake, said = x + y, join(reading[x], reading[y])
            if x == y or fake in spellings or y + x in spellings or fake in inside or said in readings:
                continue
            out.append((fake, said, starts[x] * ends[y]))
    return out


def pick(found, n=COUNT, seed=SEED):
    """n fakes, the more productive kanji more likely (weight: the square root of the product of their counts), no
    kanji in more than PER_KANJI."""
    rng = random.Random(seed)
    keyed = sorted(found, key=lambda c: (-rng.random() ** (1 / c[2] ** 0.5), c[0]))  # weighted order, no repeats
    used, out = collections.Counter(), []
    for fake, said, _ in keyed:
        if used[fake[0]] < PER_KANJI and used[fake[1]] < PER_KANJI:
            out.append((fake, said))
            used.update(fake)
            if len(out) == n:
                break
    return sorted(out, key=lambda f: f[1])


def check(fakes, words):
    """The fakes that are a JMdict spelling or whose reading is a JMdict reading or kana spelling (should be none)."""
    spellings, readings, *_ = inventory(words)
    return [(f, r) for f, r in fakes if f in spellings or hiragana(f) in readings or r in readings]


def read(path=OUT):
    lines = [line for line in Path(path).read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')]
    return [tuple(line.split('\t')[:2]) for line in lines[1:]]


def write(path, fakes, jmdict, candidates_found):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# Japanese Weave made-up words for the probe, format 1: built by tools/build_fakes.py (see its '
                'docstring), read by scripts/probe.py.\n')
        f.write(f'# Each is two common kanji that are no spelling of any JMdict entry, with an invented reading that '
                f'is no reading or kana spelling of any: checked against all {len(jmdict["words"])} entries of '
                f'JMdict {jmdict.get("version")} of {jmdict.get("dictDate")} (CC BY-SA 4.0, EDRDG). '
                f'{len(fakes)} drawn from {candidates_found} that passed.\n')
        f.write('ja\tkana\n')
        for fake, said in fakes:
            f.write(f'{fake}\t{said}\n')


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--jmdict', help='jmdict-eng JSON, all entries (default: the pinned release, from the cache)')
    p.add_argument('--cache', default=str(CACHE), help='where downloads are kept (default: %(default)s)')
    p.add_argument('--out', default=str(OUT))
    p.add_argument('--check', action='store_true', help='only check the list at --out against JMdict')
    args = p.parse_args()
    jmdict = load_jmdict(Path(args.jmdict) if args.jmdict else fetch('jmdict', Path(args.cache)))
    if args.check:
        fakes = read(args.out)
        bad = check(fakes, jmdict['words'])
        print(f'{len(fakes)} fakes in {args.out}; {len(bad)} in JMdict {jmdict.get("version")}'
              + (f': {" ".join(f for f, _ in bad)}' if bad else ''))
        sys.exit(1 if bad else 0)
    found = candidates(jmdict['words'], dictionary.load())
    fakes = pick(found)
    bad = check(fakes, jmdict['words'])
    if bad:
        sys.exit(f'{len(bad)} fakes are in JMdict after all: {bad}')
    write(args.out, fakes, jmdict, len(found))
    print(f'{len(found)} candidate fakes passed; {len(fakes)} written to {args.out}, each checked again against all '
          f'{len(jmdict["words"])} JMdict entries ({SOURCES["jmdict"]["file"]}).')


if __name__ == '__main__':
    main()
