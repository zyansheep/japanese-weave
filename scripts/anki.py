"""Words from your Anki cards under review, for the front of the Japanese Weave list (Python 3.9+, stdlib only).

Reads a collection (collection.anki2, or a .colpkg / .apkg export) read-only from a temporary copy, so Anki can stay
open. A card is "under review" when it is in learning, review or relearning, not new, suspended or buried. Field roles
(word, reading, meaning) come from the note type's field names (Core 2k/6k, Kaishi, JLPT and Yomitan-mined decks all
name them), else from what the fields hold. Kana charts (the "meaning" is the romaji), conjugated forms and grammar
explanations are skipped. Words are ordered most familiar first: mature cards (interval of at least MATURE_DAYS), then
the rest by interval.
"""
import csv
import html
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import zipfile

import kana as kana_lib

MATURE_DAYS = 21
MAX_WORD = 8  # longer "words" are sentences or notes, not vocabulary
FIELDS = ['ja', 'en', 'hint', 'kana', 'interval']


def _norm(name):
    """A field name for matching: lower case, letters and digits only, accents dropped (Rōmaji -> romaji)."""
    name = ''.join(c for c in unicodedata.normalize('NFKD', name.lower()) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9぀-鿿]', '', name)


WORD_NAMES = ['expression', 'word', 'vocabularykanji', 'vocabkanji', 'vocab', 'vocabulary', 'kanji', 'japanese',
              'term', 'headword', 'target', 'front', '単語', '語彙', '表記']
READING_NAMES = ['reading', 'expressionreading', 'wordreading', 'vocabularykana', 'vocabkana', 'kana', 'hiragana',
                 'yomi', 'furigana', 'vocabularyfurigana', 'vocabfurigana', 'expressionfurigana', 'wordfurigana', '読み']
MEANING_NAMES = ['meaning', 'english', 'vocabularyenglish', 'vocabenglish', 'wordmeaning', 'definition',
                 'maindefinition', 'glossary', 'gloss', 'translation', 'back', '意味']
BASE_NAMES = ['dictionaryform', 'baseform', 'base', 'plainform']  # conjugation decks: the verb the card conjugates
ROMAJI_NAMES = ['romaji', 'romanization', 'romanisation', 'transliteration', 'pronunciation']  # never the meaning
JAPANESE = re.compile(r'[々ぁ-ヿ一-鿿]')


def clean(text):
    """Field HTML to plain text: no tags, sounds, images or cloze markup."""
    text = re.sub(r'\[sound:[^\]]*\]', '', text or '')
    text = re.sub(r'<(style|script)\b.*?</\1>', '', text, flags=re.S | re.I)
    text = re.sub(r'<rt>.*?</rt>|<rp>.*?</rp>', '', text, flags=re.S)  # ruby: keep the base text only
    text = re.sub(r'<br\s*/?>|</div>|</p>|</li>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\{\{c\d+::(.*?)(::[^}]*)?\}\}', r'\1', text)
    return html.unescape(text).replace('\xa0', ' ').strip()


def furigana(text):
    """Anki furigana syntax 漢字[かんじ] -> (base, reading)."""
    text = clean(text)
    if '[' not in text:
        return text, ''
    base = re.sub(r'\s*([^\s\[\]]+?)\[[^\]]*\]', r'\1', text)
    reading = re.sub(r'\s*[々一-鿿]+\[([^\]]*)\]', r'\1', text)
    return re.sub(r'\s+', '', base), re.sub(r'[\s\[\]]', '', reading)


def short_gloss(text):
    """The first one or two senses: 'to eat; to consume' -> 'eat / consume'. Empty when the meaning opens with an
    explanation rather than a gloss (grammar notes)."""
    glossary = re.search(r'<ul[^>]*data-sc-content="glossary"[^>]*>(.*?)</ul>', text or '', re.S)
    if glossary:  # Yomitan structured content: leave out the dictionary name, part-of-speech tags and examples
        text = '; '.join(re.findall(r'<li[^>]*>(.*?)</li>', glossary.group(1), re.S))
    text = clean(text).split('\n')[0]
    while re.search(r'\([^()]*\)', text):  # nested too: (n, JMdict (English))
        text = re.sub(r'\([^()]*\)', '', text)
    text = re.sub(r'\[[^\]]*\]|^\s*\d+[.)]\s*', '', text)
    parts = [p.strip().lower() for p in re.split(r'[;,/]|\s\d+[.)]\s', text) if p.strip()]
    if parts and len(parts[0]) > 30:
        return ''
    parts = [re.sub(r'^to ', '', p) for p in parts if len(p) <= 30 and not JAPANESE.search(p)]
    return ' / '.join(dict.fromkeys(parts[:2]))


def roles(names):
    """{'word': index, 'reading': index, 'meaning': index, 'base': index} from a note type's field names."""
    keyed = [_norm(n) for n in names]
    found = {}
    for role, wanted in (('word', WORD_NAMES), ('reading', READING_NAMES), ('meaning', MEANING_NAMES),
                         ('base', BASE_NAMES)):
        for w in wanted:
            if w in keyed and keyed.index(w) not in found.values():
                found[role] = keyed.index(w)
                break
    return found


def guess(fields, names=()):
    """Field roles from content, for note types with unhelpful field names (Front/Back, Field 1...). A field named
    like a romaji column is never the meaning."""
    romaji = {i for i, n in enumerate(names) if _norm(n) in ROMAJI_NAMES}
    found = {}
    for i, f in enumerate(fields):
        text = clean(f)
        base = furigana(f)[0]
        if 'word' not in found and JAPANESE.search(base) and len(base) <= MAX_WORD:
            found['word'] = i
        elif 'reading' not in found and text and kana_lib.is_kana(text):
            found['reading'] = i
        elif 'meaning' not in found and i not in romaji and re.search('[A-Za-z]{2}', text) \
                and not JAPANESE.search(text):
            found['meaning'] = i
    return found


def open_collection(path, tmp):
    """A readable SQLite copy of the collection (packages are unzipped; the newest database inside wins)."""
    path = Path(path).expanduser()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in ('collection.anki21b', 'collection.anki21', 'collection.anki2') if n in z.namelist()]
            if not names:
                raise SystemExit(f'{path}: no collection inside')
            if names[0].endswith('anki21b'):
                raise SystemExit(f'{path}: zstd-compressed package; export it with "Support older Anki versions" '
                                 'ticked, or point at collection.anki2 in your Anki profile folder instead')
            z.extract(names[0], tmp)
            db = Path(tmp) / names[0]
    else:
        db = Path(tmp) / 'collection.anki2'
        shutil.copy2(path, db)
        for suffix in ('-wal', '-shm'):  # Anki open: the latest changes may still be in the WAL
            side = Path(str(path) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(db) + suffix))
    con = sqlite3.connect(str(db))
    # Anki's case-insensitive collation: current schemas declare it on note type, field, deck and tag names, and
    # SQLite won't read those tables without it
    con.create_collation('unicase', lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
    return con


def note_types(con):
    """{note type id: [field names in order]} for both the current (notetypes/fields tables) and old (col.models)
    schemas."""
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    if 'fields' in tables:
        out = {}
        for ntid, ord_, name in con.execute('select ntid, ord, name from fields order by ntid, ord'):
            out.setdefault(ntid, []).append(name)
        return out
    models = json.loads(con.execute('select models from col').fetchone()[0])
    return {int(mid): [f['name'] for f in sorted(m['flds'], key=lambda f: f['ord'])] for mid, m in models.items()}


def deck_names(con):
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    if 'decks' in tables:
        return {did: name.replace('\x1f', '::') for did, name in con.execute('select id, name from decks')}
    decks = json.loads(con.execute('select decks from col').fetchone()[0])
    return {int(did): d['name'] for did, d in decks.items()}


def review_words(collection, deck=None, mature_days=MATURE_DAYS, base=()):
    """([{ja, en, hint, kana, interval, mature}], report). base: lexicon rows used for readings and glosses."""
    by_word = {w['ja']: w for w in base}
    by_kana = {}
    for w in base:
        by_kana.setdefault(w.get('kana'), w)
    report = {'cards': 0, 'notes': 0, 'skipped': {}}
    skip = lambda why: report['skipped'].__setitem__(why, report['skipped'].get(why, 0) + 1)
    letters = lambda text: re.sub('[^a-z]', '', text)
    with tempfile.TemporaryDirectory() as tmp:
        con = open_collection(collection, tmp)
        types, decks = note_types(con), deck_names(con)
        rows = con.execute('select n.id, n.mid, n.flds, c.did, c.ivl from cards c join notes n on n.id = c.nid '
                           'where c.type in (1, 2, 3) and c.queue >= 0').fetchall()
        con.close()
    notes = {}
    for nid, mid, flds, did, ivl in rows:
        if deck and deck.lower() not in decks.get(did, '').lower():
            continue
        report['cards'] += 1
        note = notes.setdefault(nid, {'mid': mid, 'fields': flds.split('\x1f'), 'interval': 0})
        note['interval'] = max(note['interval'], ivl if ivl > 0 else 0)  # negative: learning steps in seconds
    report['notes'] = len(notes)
    out, seen = [], set()
    for note in sorted(notes.values(), key=lambda n: -n['interval']):
        fields = note['fields']
        names = types.get(note['mid'], [])
        if len(names) != len(fields):
            names = []
        found = roles(names)
        if 'word' not in found:
            found = {**guess(fields, names), **found}
        if 'word' not in found:
            skip('no Japanese word field')
            continue
        ja, reading = furigana(fields[found['word']])
        ja = ja.strip()
        if not JAPANESE.search(ja) or len(ja) > MAX_WORD or re.search(r'[\s、。！？!?,.]', ja):
            skip('sentence or not a word')
            continue
        if 'base' in found and furigana(fields[found['base']])[0] not in ('', ja):
            skip('conjugated form')
            continue
        if 'reading' in found:
            reading = furigana(fields[found['reading']])[1] or clean(fields[found['reading']])
        reading = kana_lib.hiragana(re.sub(r'\s+', '', reading or ''))
        known = by_word.get(ja)
        if not kana_lib.is_kana(reading):
            reading = known['kana'] if known else (kana_lib.hiragana(ja) if kana_lib.is_kana(ja) else '')
        if not reading:
            skip('no reading')
            continue
        en = known['en'] if known else short_gloss(fields[found['meaning']]) if 'meaning' in found else ''
        if not en:
            skip('no English meaning')
            continue
        if not known and kana_lib.is_kana(ja) and letters(en) == letters(kana_lib.romaji(reading)):
            skip('romaji, not a meaning')  # a kana drill card
            continue
        if ja in seen:
            continue
        seen.add(ja)
        hint = known['hint'] if known else kana_lib.romaji(reading, verb_u=bool(re.search(r'[\u4e00-\u9fff]う$', ja)))
        out.append({'ja': ja, 'en': en, 'hint': hint, 'kana': reading, 'interval': note['interval'],
                    'mature': note['interval'] >= mature_days})
    return out, report


def write_csv(path, words):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        out = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        out.writeheader()
        out.writerows(words)
