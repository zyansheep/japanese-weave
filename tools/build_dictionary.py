#!/usr/bin/env python3
"""Builds data/dictionary.tsv: every common JMdict entry (about 22,600), with the frequency ranks of
tools/build_candidates.py (the geometric mean of the JPDB and BCCWJ ranks) and without its word-by-word review.
Entries without a rank are kept and come after the ranked ones. build_candidates.py's filters (parts of speech,
register, katakana, length) become a flag on each entry, swap, instead of dropping it.

  python3 tools/build_dictionary.py             # downloads the sources once into ~/.cache/japanese-weave-build
  python3 tools/build_dictionary.py --jmdict jmdict-eng-common.json --jpdb jpdb_freq.csv --bccwj term_meta_bank_1.json

Sources (SOURCES below has the exact URLs and sha256 sums): JMdict (EDRDG, CC BY-SA 4.0) via jmdict-simplified,
English, common entries only; JPDB v2.2 and BCCWJ frequency lists via Kuuuube/yomitan-dictionaries. Entries that are
in data/lexicon.csv keep its reviewed gloss and romaji (the review fixed romaji at morpheme boundaries, 実は jitsu wa,
which kana.romaji can't). Three things differ from build_candidates.py: a word written in kana is ranked by its kanji
spellings too (こと is 15th, not 1,419th: BCCWJ files it under 事), a search-only or rare kanji spelling is never the
written form (いけない, not 行けない), and homographs that share a rank put the swappable one first. The file is
described in docs/dictionary.md; scripts/dictionary.py and extension/src/dictionary.js read it.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.request
import zipfile

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / 'scripts'))
from build_candidates import CJK, DROP_MISC, DROP_POS, KEEP_POS, load_bccwj, load_jpdb, short_gloss  # noqa: E402
from kana import hiragana, is_kana, romaji  # noqa: E402

SOURCES = {
    'jmdict': {
        'url': 'https://github.com/scriptin/jmdict-simplified/releases/download/3.6.2%2B20261019180022/'
               'jmdict-eng-common-3.6.2%2B20261019180022.json.zip',
        'file': 'jmdict-eng-common-3.6.2+20261019180022.json.zip',
        'sha256': 'd9a43b326561f9d88d7b1acc4cab5ea2b834d3634f119548c427ab2f772d7841',
        'member': 'jmdict-eng-common-3.6.2.json',
    },
    'jpdb': {
        'url': 'https://raw.githubusercontent.com/Kuuuube/yomitan-dictionaries/'
               'd6fde809e3f26eb5aed6d41896f332179044998c/data/jpdb_v2.2_freq_list_2024-10-13.csv',
        'file': 'jpdb_v2.2_freq_list_2024-10-13.csv',
        'sha256': '5cf103c1538a2189cda2fe0d462ebfe08eb09f211a48b727f533d387b371c5eb',
    },
    'bccwj': {
        'url': 'https://raw.githubusercontent.com/Kuuuube/yomitan-dictionaries/'
               'd6fde809e3f26eb5aed6d41896f332179044998c/dictionaries/BCCWJ_SUW_LUW_combined.zip',
        'file': 'BCCWJ_SUW_LUW_combined.zip',
        'sha256': '7d17054735e738d02e9f7f62fdad5d6e592a458abd93301367500d04d0c000c3',
        'member': 'term_meta_bank_1.json',
    },
}
CACHE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'japanese-weave-build'
FIELDS = ['id', 'ja', 'kana', 'romaji', 'en', 'glosses', 'pos', 'score', 'jpdb', 'bccwj', 'common', 'loan', 'swap',
          'forms']
FORMAT = 1
SENSES, GLOSSES, LIST = '|', ';', ','  # separators inside the glosses, pos and forms cells
MAX_GLOSSES = 8         # glosses kept per entry: the first sense's first, then the other senses' in order
MAX_FIRST = 5           # of which the first sense keeps at most this many
MAX_GLOSS_CHARS = 60    # longer glosses are explanations, kept only when a sense has nothing shorter
ODD_KANJI = {'sK', 'rK', 'oK', 'iK'}  # search-only, rare, outdated, irregular: never the written form
ODD_KANA = {'sk', 'ok', 'ik', 'rk'}
KATAKANA = re.compile('^[ァ-ヺー・]+$')
COMMON_WRITTEN, COMMON_READING = 1, 2  # bits of the common field


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def fetch(name, cache):
    """The source file (the member, for a zip), downloaded into its own directory under cache once and checked
    against its sha256."""
    source = SOURCES[name]
    folder = cache / name
    path = folder / source['file']
    if not path.exists() or sha256(path) != source['sha256']:
        folder.mkdir(parents=True, exist_ok=True)
        print(f'downloading {source["url"]}', file=sys.stderr)
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as tmp, urllib.request.urlopen(source['url']) as r:
            for block in iter(lambda: r.read(1 << 20), b''):
                tmp.write(block)
        got = sha256(tmp.name)
        if got != source['sha256']:
            os.unlink(tmp.name)
            sys.exit(f'{name}: sha256 {got}, expected {source["sha256"]} ({source["url"]})')
        os.replace(tmp.name, path)
    if 'member' not in source:
        return path
    member = folder / source['member']
    if not member.exists() or member.stat().st_mtime < path.stat().st_mtime:
        with zipfile.ZipFile(path) as z:
            member.write_bytes(z.read(source['member']))
    return member


def gloss_text(text):
    """A gloss without its notes in parentheses, on one line, with no semicolons (they separate glosses)."""
    while re.search(r'\([^()]*\)', text):
        text = re.sub(r'\s*\([^()]*\)', '', text)
    return re.sub(r'\s+', ' ', text.replace(';', ',')).strip(' ,')


def glosses(senses):
    """[[gloss, ...] per sense]: the first sense's glosses first, then the other senses', without notes, duplicates or
    long explanations, at most MAX_GLOSSES in all. A sense left with nothing is dropped."""
    out, seen, total = [], set(), 0
    for number, sense in enumerate(senses):
        texts = [gloss_text(g['text']) for g in sense['gloss']]
        short = [t for t in texts if t and len(t) <= MAX_GLOSS_CHARS]
        kept = []
        for text in short or [t for t in texts if t][:1]:
            if text.lower() in seen or total >= MAX_GLOSSES or number == 0 and len(kept) >= MAX_FIRST:
                continue
            seen.add(text.lower())
            kept.append(text)
            total += 1
        if kept:
            out.append(kept)
    return out


def written_form(w):
    """(written, reading, uk) as build_candidates.py chooses them, except that a search-only, rare, outdated or
    irregular kanji spelling is never the written form (いけない, not 行けない; それから, not 其れから), nor is a
    spelling in letters that isn't marked common (オッケー, not ＯＫ)."""
    sense = w['sense'][0]
    uk = 'uk' in sense['misc']
    normal = [k for k in w['kanji'] if not ODD_KANJI & set(k['tags'])]
    kanji = [k['text'] for k in normal if k['common']] or [k['text'] for k in normal if CJK.search(k['text'])]
    kana_forms = [k for k in w['kana'] if k['common']] or w['kana']
    written = kana_forms[0]['text'] if uk or not kanji else kanji[0]
    reading = next((k['text'] for k in kana_forms
                    if '*' in k['appliesToKanji'] or written in k['appliesToKanji']), kana_forms[0]['text'])
    return written, hiragana(reading), uk


def swappable(w, written, reading):
    """build_candidates.py's filters, without its frequency cut: a noun, adverb, conjunction, adjective or expression
    that can stand in for an English word."""
    sense = w['sense'][0]
    pos = set(sense['partOfSpeech'])
    if not pos & KEEP_POS or pos & DROP_POS or any(p.startswith('v') and p not in ('vs', 'vt', 'vi') for p in pos):
        return False
    if set(sense['misc']) & DROP_MISC:
        return False
    if is_kana(written) and re.search('[ァ-ヺ]', written):
        return False  # katakana loanwords are English already
    if not (CJK.search(written) or is_kana(written)) or len(written) > 6 or len(reading) < 2 \
            or re.search('[0-9０-９]', written):
        return False
    gloss = short_gloss(sense)
    return bool(gloss) and not (gloss[:1].isupper() and not gloss.startswith('I '))


def frequency(written, reading, kanji, jpdb, bccwj):
    """(score, JPDB rank, BCCWJ rank) as build_candidates.py computes them; None where a list doesn't have the word.
    Kana-only entries collide with particles and homographs (殻 vs the particle から), so kanji words count only
    their written forms. Unlike build_candidates.py, a word written in kana counts its kanji spellings too: BCCWJ
    files 掛ける under its kanji, so かける alone ranks 536,048th there."""
    forms = {written, *kanji[:2]} if not is_kana(written) else {written, reading, *kanji[:2]}
    jr = min((jpdb[(f, reading)] for f in forms if (f, reading) in jpdb), default=None)
    br = min((bccwj[(f, reading)] for f in forms if (f, reading) in bccwj), default=None)
    if jr is None and br is None:
        return None, None, None
    return round((jr * br) ** 0.5 if jr and br else 3 * (jr or br)), jr, br


def loan(w, written):
    """The languages JMdict gives as the word's source (lsource), 'kata' for a katakana word without one, else ''."""
    langs = []
    for sense in w['sense']:
        for source in sense['languageSource']:
            if source['lang'] not in langs:
                langs.append(source['lang'])
    return ','.join(langs) if langs else 'kata' if KATAKANA.match(written) else ''


def read_lexicon(path):
    """{(ja, kana): row} of data/lexicon.csv, and its rows in order."""
    with open(path, encoding='utf-8', newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['ja'].strip()]
    return {(r['ja'], hiragana(r['kana'])): r for r in rows}, rows


def build(words, jpdb, bccwj, lexicon):
    """The entries in rank order, as dicts with FIELDS."""
    by_spelling, _ = lexicon
    entries = []
    for w in words:
        written, reading, uk = written_form(w)
        kanji = [k['text'] for k in w['kanji'] if k['common']] or [k['text'] for k in w['kanji']]
        score, jr, br = frequency(written, reading, kanji, jpdb, bccwj)
        pos = list(dict.fromkeys(p for sense in w['sense'] for p in sense['partOfSpeech']))
        verb_u = reading.endswith('う') and any(p.startswith('v5u') for p in w['sense'][0]['partOfSpeech'])
        spellings = [k['text'] for k in w['kanji'] if k['common']] + [k['text'] for k in w['kanji'] if not k['common']]
        spellings += [k['text'] for k in w['kana'] if not ODD_KANA & set(k['tags'])]
        forms = [f for f in dict.fromkeys(spellings) if f not in (written, reading)]
        common = (COMMON_WRITTEN if any(k['common'] and k['text'] == written for k in w['kanji'] + w['kana']) else 0) \
            | (COMMON_READING if any(k['common'] and hiragana(k['text']) == reading for k in w['kana']) else 0)
        listed = by_spelling.get((written, reading))
        en = short_gloss(w['sense'][0]) or gloss_text(w['sense'][0]['gloss'][0]['text'])
        entries.append({
            'id': int(w['id']), 'ja': written, 'kana': reading,
            'romaji': listed['hint'] if listed else romaji(reading, verb_u=verb_u),
            'en': en, 'glosses': glosses(w['sense']), 'pos': pos, 'score': score, 'jpdb': jr, 'bccwj': br,
            'common': common, 'loan': loan(w, written), 'swap': int(swappable(w, written, reading)), 'forms': forms,
        })
    # ranked entries by score, then the rest. Homographs written in kana share a score (both そう are ranked by the
    # kana そう), so a swappable entry comes before one that isn't, then one whose written form is marked common;
    # last, by JMdict id.
    entries.sort(key=lambda e: (e['score'] is None, e['score'] or 0, not e['swap'], not e['common'] & COMMON_WRITTEN,
                                e['id']))
    # a lexicon word keeps its reviewed gloss on the entry build_candidates.py took it from: the best-ranked
    # swappable entry with its spelling and reading (else the best-ranked one)
    claimed = set()
    for e in sorted(entries, key=lambda e: not e['swap']):
        key = (e['ja'], e['kana'])
        if key in by_spelling and key not in claimed:
            claimed.add(key)
            e['en'] = by_spelling[key]['en']
    return entries


def cell(field, value):
    """An entry's value as TSV text: lists joined by their separators, None empty, flags 0 or 1."""
    if field == 'glosses':
        assert not any(SENSES in g or GLOSSES in g for sense in value for g in sense), value
        text = SENSES.join(GLOSSES.join(sense) for sense in value)
    elif isinstance(value, list):
        assert not any(LIST in v for v in value), value
        text = LIST.join(value)
    else:
        text = '' if value is None else str(int(value) if isinstance(value, bool) else value)
    assert '\t' not in text and '\n' not in text, (field, value)
    return text


def write(path, entries, sources):
    """Comment lines (#) with the format, license and sources, a header row, then one entry per line in rank
    order."""
    lines = [f'# Japanese Weave dictionary, format {FORMAT}: built by tools/build_dictionary.py, described in '
             'docs/dictionary.md.',
             '# JMdict (c) the Electronic Dictionary Research and Development Group, CC BY-SA 4.0 '
             '(https://www.edrdg.org/edrdg/licence.html). Frequency ranks: JPDB v2.2 and BCCWJ via '
             'Kuuuube/yomitan-dictionaries.']
    lines += [f'# {name}: ' + ' '.join(f'{k} {v}' for k, v in source.items()) for name, source in sources.items()]
    lines.append('\t'.join(FIELDS))
    lines += ['\t'.join(cell(f, e[f]) for f in FIELDS) for e in entries]
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--jmdict', help='jmdict-eng-common JSON (default: download the pinned release)')
    p.add_argument('--jpdb', help='JPDB v2.2 frequency TSV (default: download)')
    p.add_argument('--bccwj', help='BCCWJ term_meta_bank_1.json (default: download)')
    p.add_argument('--cache', default=str(CACHE), help='where downloads are kept (default: %(default)s)')
    p.add_argument('--lexicon', default=str(ROOT / 'data' / 'lexicon.csv'))
    p.add_argument('--out', default=str(ROOT / 'data' / 'dictionary.tsv'))
    args = p.parse_args()
    paths = {name: Path(getattr(args, name)) if getattr(args, name) else fetch(name, Path(args.cache))
             for name in SOURCES}
    with open(paths['jmdict'], encoding='utf-8') as f:
        jmdict = json.load(f)
    lexicon = read_lexicon(args.lexicon)
    entries = build(jmdict['words'], load_jpdb(paths['jpdb']), load_bccwj(paths['bccwj']), lexicon)
    sources = {name: {'url': SOURCES[name]['url'], 'sha256': SOURCES[name]['sha256']} if not getattr(args, name)
               else {'file': paths[name].name, 'sha256': sha256(paths[name])} for name in SOURCES}
    sources['jmdict'].update(version=jmdict.get('version'), dictDate=jmdict.get('dictDate'))
    write(args.out, entries, sources)
    keys = {(e['ja'], e['kana']) for e in entries}
    missing = [r['ja'] for r in lexicon[1] if (r['ja'], hiragana(r['kana'])) not in keys]
    ranked = sum(e['score'] is not None for e in entries)
    print(f'{len(entries)} entries ({ranked} ranked, {sum(e["swap"] for e in entries)} swappable, '
          f'{sum(bool(e["loan"]) for e in entries)} loanwords) -> {args.out}')
    if missing:
        print(f'{len(missing)} lexicon words have no entry with their spelling and reading: {" ".join(missing)}')


if __name__ == '__main__':
    main()
