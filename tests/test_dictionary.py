"""Offline tests for the dictionary: tools/build_dictionary.py on a small JMdict fixture, scripts/dictionary.py's
lookups, tools/dictionary_coverage.py on a fixture state directory, and the committed data/dictionary.tsv.
Run: python -m unittest tests/test_dictionary.py"""
import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import warnings

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tools'))

import build_dictionary  # noqa: E402
import dictionary  # noqa: E402
import dictionary_coverage  # noqa: E402
import inflection  # noqa: E402


def word(id, kanji, kana, senses):
    """A jmdict-simplified word. kanji and kana: [(text, common, tags)]; senses: [(pos, misc, glosses, lsource)]."""
    return {
        'id': str(id),
        'kanji': [{'common': c, 'text': t, 'tags': tags} for t, c, tags in kanji],
        'kana': [{'common': c, 'text': t, 'tags': tags, 'appliesToKanji': ['*']} for t, c, tags in kana],
        'sense': [{'partOfSpeech': pos, 'appliesToKanji': ['*'], 'appliesToKana': ['*'], 'related': [], 'antonym': [],
                   'field': [], 'dialect': [], 'misc': misc, 'info': [],
                   'languageSource': [{'lang': lang, 'full': True, 'wasei': False, 'text': None} for lang in lsource],
                   'gloss': [{'lang': 'eng', 'gender': None, 'type': None, 'text': g} for g in glosses]}
                  for pos, misc, glosses, lsource in senses],
    }


WORDS = [
    word(1375610, [('成る', True, [])], [('なる', True, [])],
         [(['v5r', 'vi'], ['uk'], ['to become', 'to get', 'to grow'], [])]),
    word(1577980, [('居る', False, ['rK'])], [('いる', True, [])], [(['v1', 'vi'], ['uk'], ['to be', 'to exist'], [])]),
    word(1546640, [('要る', True, [])], [('いる', True, [])],
         [(['v5r', 'vi'], ['uk'], ['to be needed', 'to be necessary'], [])]),
    word(1214970, [('確認', True, [])], [('かくにん', True, [])],
         [(['n', 'vs', 'vt'], [], ['confirmation', 'verification', 'check'], [])]),
    word(1207610, [('掛ける', True, []), ('懸ける', True, [])], [('かける', True, [])],
         [(['v1', 'vt'], ['uk'], ['to hang up', 'to let hang'], []), (['v1', 'vt'], [], ['to put on'], [])]),
    word(1000730, [('行けない', False, ['sK'])], [('いけない', True, [])],
         [(['exp', 'adj-i'], [], ['bad', 'wrong'], []), (['exp'], [], ['must not'], [])]),
    word(1031600, [('ＯＫ', False, [])], [('オッケー', True, []), ('オーケー', True, [])],
         [(['int', 'n'], [], ['OK', 'okay'], [])]),
    word(1597150, [('煙草', True, [])], [('タバコ', True, []), ('たばこ', True, [])],
         [(['n'], ['uk'], ['tobacco', 'cigarette'], ['por'])]),
    word(1320830, [('実は', True, [])], [('じつは', True, [])],
         [(['exp', 'adv'], [], ['as a matter of fact', 'by the way'], [])]),
    word(2137720, [('然う', False, ['rK'])], [('そう', True, [])],
         [(['adv', 'int'], ['uk'], ['in that way', 'thus', 'such'], [])]),
    word(1006610, [], [('そう', True, [])], [(['aux', 'adj-na'], [], ['appearing that', 'seeming that'], [])]),
    word(1327190, [('手', True, [])], [('て', True, [])],
         [(['n'], [], ['hand (body part)', 'arm'], []), (['n'], [], ['worker; help'], [])]),
    word(1002980, [], [('から', True, [])], [(['prt'], [], ['from', 'since'], [])]),
    word(1395200, [('殻', True, [])], [('から', True, [])], [(['n'], [], ['shell', 'husk'], [])]),
    word(1404975, [('早い', True, []), ('速い', True, [])], [('はやい', True, [])],
         [(['adj-i'], [], ['fast', 'quick', 'early'], [])]),
    word(2020680, [('時', True, [])], [('じ', True, [])], [(['suf', 'ctr'], [], ["hour", "o'clock"], [])]),
    word(1999990, [('稀語', True, [])], [('きご', True, [])], [(['n'], [], ['rare word'], [])]),
    word(1998880, [('珍語', True, [])], [('ちんご', True, [])], [(['n'], [], ['odd word'], [])]),
    word(2029080, [], [('ね', True, []), ('ねえ', True, [])], [(['prt'], [], ['right?', "isn't it?"], [])]),
    # not marked common: kept when ranked within FREQUENT by spellings no common entry has
    word(1590880, [('関数', False, [])], [('かんすう', False, [])], [(['n'], [], ['function'], [])]),
    word(2261500, [('伯母さん', False, [])], [('おばさん', False, [])], [(['n'], ['uk'], ['aunt'], [])]),
    word(1292450, [('再帰', False, [])], [('さいき', False, [])], [(['n'], [], ['recursion'], [])]),
    word(2654270, [], [('て', False, [])], [(['prt'], [], ['and'], [])]),
    word(1997770, [('無名語', False, [])], [('むめいご', False, [])], [(['n'], [], ['unranked word'], [])]),
    word(2266990, [('姉', False, [])], [('ねえ', False, [])], [(['n'], [], ['older sister'], [])]),
]
COMMON = 19  # the first 19 words above have a spelling marked common
JPDB = [('なる', 'なる', 16, ''), ('成る', 'なる', 500, ''), ('いる', 'いる', 18, 18), ('要る', 'いる', 600, ''),
        ('確認', 'かくにん', 400, ''), ('かける', 'かける', '', 202), ('掛ける', 'かける', 900, ''),
        ('いけない', 'いけない', 279, ''), ('オッケー', 'おっけー', 4753, ''), ('タバコ', 'たばこ', 10598, ''),
        ('実は', 'じつは', 595, ''), ('そう', 'そう', 31, 31), ('手', 'て', 114, ''), ('から', 'から', 9, 9),
        ('殻', 'から', 20000, ''), ('早い', 'はやい', 300, ''), ('時', 'じ', 700, ''), ('稀語', 'きご', 90000, ''),
        ('関数', 'かんすう', 43708, ''), ('おばさん', 'おばさん', 4090, ''), ('再帰', 'さいき', 104426, ''),
        ('て', 'て', 159, 159), ('ね', 'ね', 19, 19), ('姉', 'ねえ', 5000, '')]
BCCWJ = [('なる', 'なる', 100044), ('成る', 'なる', 19), ('居る', 'いる', 13), ('要る', 'いる', 1409),
         ('確認', 'かくにん', 500), ('かける', 'かける', 536048), ('掛ける', 'かける', 159), ('実は', 'じつは', 473),
         ('そう', 'そう', 57), ('手', 'て', 131), ('から', 'から', 22), ('殻', 'から', 15000), ('早い', 'はやい', 200),
         ('時', 'じ', 400), ('関数', 'かんすう', 3632), ('おばさん', 'おばさん', 2895), ('再帰', 'さいき', 43534),
         ('て', 'て', 3)]
LEXICON = [{'ja': '実は', 'en': 'actually', 'hint': 'jitsu wa', 'kana': 'じつは'},
           {'ja': 'そう', 'en': 'so / that way', 'hint': 'sō', 'kana': 'そう'}]


def build(tmp):
    """The fixture dictionary's path, built through build_dictionary's own steps from fixture source files."""
    tmp = Path(tmp)
    with open(tmp / 'jpdb.tsv', 'w', encoding='utf-8') as f:
        f.write('term\treading\tfrequency\tkana_frequency\n')
        f.writelines(f'{t}\t{r}\t{fr}\t{kf}\n' for t, r, fr, kf in JPDB)
    (tmp / 'bccwj.json').write_text(json.dumps([[t, 'freq', {'reading': r, 'frequency': n}] for t, r, n in BCCWJ]),
                                    encoding='utf-8')
    with open(tmp / 'lexicon.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['ja', 'en', 'hint', 'kana'])
        writer.writeheader()
        writer.writerows(LEXICON)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', ResourceWarning)  # build_candidates.load_bccwj leaves its file to the collector
        bccwj = build_dictionary.load_bccwj(tmp / 'bccwj.json')
    entries = build_dictionary.build(WORDS, build_dictionary.load_jpdb(tmp / 'jpdb.tsv'), bccwj,
                                     build_dictionary.read_lexicon(tmp / 'lexicon.csv'))
    out = tmp / 'dictionary.tsv'
    build_dictionary.write(out, entries, {'jmdict': {'file': 'fixture', 'sha256': '0' * 64}})
    return out


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = build(cls.tmp.name)
        cls.d = dictionary.Dictionary(cls.path.read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class Build(Fixture):
    def test_file_has_comments_header_and_every_entry(self):
        lines = self.path.read_text(encoding='utf-8').split('\n')
        self.assertTrue(lines[0].startswith('# Japanese Weave dictionary, format 1'))
        self.assertIn('CC BY-SA 4.0', lines[1])
        self.assertIn('# jmdict: file fixture sha256 ' + '0' * 64, lines)
        self.assertIn('\t'.join(dictionary.FIELDS), lines)
        self.assertEqual(len(self.d), COMMON + 3)
        self.assertEqual(len({e['id'] for e in self.d}), len(self.d))

    def test_written_as_normally_written(self):
        ja = {e['id']: e['ja'] for e in self.d}
        self.assertEqual(ja[1375610], 'なる')       # usually kana
        self.assertEqual(ja[1214970], '確認')
        self.assertEqual(ja[1000730], 'いけない')   # never a search-only kanji spelling
        self.assertEqual(ja[1031600], 'オッケー')   # nor letters not marked common
        self.assertEqual(ja[1597150], 'タバコ')
        self.assertEqual(self.d.get(1000730)['forms'], ['行けない'])
        self.assertEqual(self.d.get(1031600)['forms'], ['ＯＫ', 'オーケー'])

    def test_ranks_as_build_candidates_with_kanji_spellings_for_kana_words(self):
        self.assertEqual(self.d.get(1214970)['score'], round((400 * 500) ** 0.5))  # geometric mean
        self.assertEqual(self.d.get(1999990)['score'], 3 * 90000)  # one list only: three times its rank
        # かける counts its kanji spelling: BCCWJ files it under 掛ける (159), not かける (536,048)
        kakeru = self.d.get(1207610)
        self.assertEqual((kakeru['jpdb'], kakeru['bccwj']), (202, 159))
        # a kanji word counts only its own spellings: 殻 is not ranked as the particle から
        self.assertEqual(self.d.get(1395200)['jpdb'], 20000)
        unranked = self.d.get(1998880)
        self.assertIsNone(unranked['score'])
        self.assertEqual(unranked['rank'], len(self.d))  # entries without a rank come last
        scores = [e['score'] for e in self.d if e['score'] is not None]
        self.assertEqual(scores, sorted(scores))

    def test_homographs_sharing_a_score_put_the_swappable_one_first(self):
        first, second = self.d.reading('そう')
        self.assertEqual(first['score'], second['score'])
        self.assertEqual((first['id'], first['swap'], second['id'], second['swap']), (2137720, True, 1006610, False))

    def test_swap_flag_keeps_build_candidates_filters(self):
        swap = {e['id']: e['swap'] for e in self.d}
        self.assertTrue(swap[1214970])    # noun
        self.assertTrue(swap[1320830])    # expression, adverb
        self.assertFalse(swap[1375610])   # verb
        self.assertFalse(swap[1002980])   # particle
        self.assertFalse(swap[1031600])   # katakana
        self.assertFalse(swap[1597150])

    def test_loanwords_glosses_kanji_and_the_lexicon(self):
        self.assertEqual(self.d.get(1597150)['loan'], 'por')
        self.assertEqual(self.d.get(1031600)['loan'], 'kata')
        self.assertEqual(self.d.get(1214970)['loan'], '')
        te = self.d.get(1327190)
        self.assertEqual(te['glosses'], [['hand', 'arm'], ['worker, help']])  # no notes, no semicolons
        self.assertEqual(te['en'], 'hand / arm')
        self.assertEqual(self.d.get(1214970)['kanji'], ['確', '認'])
        both = build_dictionary.COMMON_WRITTEN | build_dictionary.COMMON_READING
        self.assertEqual(self.d.get(1214970)['common'], both)
        jitsu = self.d.get(1320830)
        self.assertEqual((jitsu['romaji'], jitsu['en']), ('jitsu wa', 'actually'))  # reviewed romaji and gloss
        self.assertEqual(self.d.get(2137720)['en'], 'so / that way')  # the gloss goes to the swappable homograph
        self.assertEqual(self.d.get(1006610)['en'], 'appearing that / seeming that')

    def test_entries_not_marked_common_are_kept_for_their_rank(self):
        functions = self.d.get(1590880)
        self.assertEqual((functions['common'], functions['score']), (0, round((43708 * 3632) ** 0.5)))
        self.assertEqual((self.d.get(2261500)['ja'], self.d.get(2261500)['common']), ('おばさん', 0))
        self.assertIsNone(self.d.get(1292450))  # ranked past FREQUENT
        self.assertIsNone(self.d.get(2654270))  # its only rank is the common 手's reading て
        self.assertIsNone(self.d.get(1997770))  # no rank
        self.assertEqual(sum(e['common'] > 0 for e in self.d), COMMON)
        self.assertEqual(sum(e['common'] == 0 for e in self.d), 3)
        self.assertEqual(self.d.get(2266990)['common'], 0)  # 姉 read ねえ: its own spelling, not ね's
        common_only = build_dictionary.build(WORDS, {}, {}, ({}, []), frequent=0)
        self.assertEqual(len(common_only), COMMON)

    def test_gloss_cap(self):
        senses = [{'gloss': [{'text': f'gloss {i}-{j}'} for j in range(7)]} for i in range(3)]
        out = build_dictionary.glosses(senses)
        self.assertEqual(len(out[0]), build_dictionary.MAX_FIRST)
        self.assertEqual(sum(len(s) for s in out), build_dictionary.MAX_GLOSSES)
        self.assertEqual(out[1][0], 'gloss 1-0')


class Lookups(Fixture):
    def test_conjugated_forms_find_their_entries(self):
        found = {w: (self.d.find(w) or {}).get('id') for w in
                 ('なります', 'いります', '要ります', 'いました', '確認します', '確認', 'かけています', '早かった', '速い',
                  '行けない', 'ＯＫ', '殻', 'から', 'たばこ', '〜時', 'みつからない')}
        self.assertEqual(found, {
            'なります': 1375610, 'いります': 1546640, '要ります': 1546640, 'いました': 1577980,
            '確認します': 1214970, '確認': 1214970, 'かけています': 1207610, '早かった': 1404975,
            '速い': 1404975, '行けない': 1000730, 'ＯＫ': 1031600, '殻': 1395200, 'から': 1002980,
            'たばこ': 1597150, '〜時': 2020680, 'みつからない': None})

    def test_kana_and_english_settle_homographs(self):
        self.assertEqual(self.d.find('から', 'から')['id'], 1002980)  # written beats a reading
        self.assertEqual(self.d.find('そう', 'そう', 'appearing / seeming')['id'], 1006610)
        self.assertEqual(self.d.find('そう', 'そう', 'in such manner / that way')['id'], 2137720)
        self.assertEqual(self.d.find('そう')['id'], 2137720)

    def test_a_kana_spelling_counts_as_a_reading(self):
        # ねえ is a kana spelling of ね and the reading of 姉 (an entry not marked common): rank decides, not which
        self.assertEqual(self.d.find('ねえ')['id'], 2029080)
        self.assertEqual(self.d.find('ねえ', 'ねえ', "right? / isn't it?")['id'], 2029080)
        self.assertEqual(self.d.find('ねえ', 'ねえ', 'big sister')['id'], 2266990)
        self.assertEqual([e['id'] for e in self.d.written('ねえ')], [2029080])
        self.assertEqual([e['id'] for e in self.d.reading('ねえ')], [2029080, 2266990])

    def test_written_reading_romaji_and_id(self):
        self.assertEqual([e['id'] for e in self.d.written('速い')], [1404975])
        self.assertEqual([e['id'] for e in self.d.written('から')], [1002980])  # 殻 is only read から
        self.assertEqual([e['id'] for e in self.d.reading('から')], [1002980, 1395200])
        self.assertEqual([e['id'] for e in self.d.reading('いる')], [1577980, 1546640])
        self.assertEqual([e['id'] for e in self.d.reading('タバコ')], [1597150])
        self.assertEqual(self.d.romaji('jitsuwa')[0]['ja'], '実は')
        self.assertEqual(self.d.lookup('kakunin')['ja'], '確認')
        self.assertEqual(self.d.lookup('なりました')['ja'], 'なる')
        self.assertIsNone(self.d.get(1))

    def test_english_index(self):
        glosses = list(self.d.english())
        te = [(g, n) for g, i, n in glosses if self.d.entry(i)['id'] == 1327190]
        self.assertEqual(te, [('hand', 0), ('arm', 0), ('worker, help', 1)])
        self.assertEqual(len(glosses), sum(len(s) for e in self.d for s in e['glosses']))

    def test_folding_matches_inflection(self):
        words = ['カレー', 'ＯＫ', 'ﾃｽﾄ', 'a b', 'ヴァ', 'Tōkyō']
        self.assertEqual(dictionary.fold_all(words), [inflection.fold(w) for w in words])

    def test_kinds_come_from_parts_of_speech(self):
        self.assertEqual(dictionary.kind(['v5r', 'vi'], 'いる', 'いる'), inflection.V5)
        self.assertEqual(dictionary.kind(['v1'], 'いる', 'いる'), inflection.V1)
        self.assertEqual(dictionary.kind(['n', 'vs'], '確認', 'かくにん'), inflection.NOUN)
        self.assertEqual(dictionary.kind(['adj-ix'], 'いい', 'いい'), inflection.ADJ)
        self.assertEqual(dictionary.kind(['vs-i'], 'する', 'する'), inflection.VS)
        self.assertEqual(dictionary.kind(['vz'], '信ずる', 'しんずる'), inflection.word_class('信ずる', 'しんずる'))


class Coverage(Fixture):
    def test_report_on_a_state_directory(self):
        with tempfile.TemporaryDirectory() as home:
            home = Path(home)
            state = {'words': [{'ja': ja, 'kana': kana, 'en': en} for ja, kana, en in
                               (('実は', 'じつは', 'actually'), ('そう', 'そう', 'so / that way'),
                                ('タバコ', 'たばこ', 'cigarette'), ('マジ', 'まじ', 'serious'), ('察', 'さつ', 'none'),
                                ('おばさん', 'おばさん', 'aunt'))]}
            (home / 'state.json').write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            with open(home / 'anki.csv', 'w', encoding='utf-8', newline='') as f:
                f.write('ja,en,hint,kana,interval\nタバコ,cigarette,tabako,たばこ,30\nマジ,serious,maji,まじ,20\n')
            (home / 'knowledge.json').write_text(json.dumps(
                {'unlisted': {'top': [['なります', 2], ['これは文です。', 1]]}}, ensure_ascii=False), encoding='utf-8')
            con = sqlite3.connect(home / 'interactions.sqlite')
            con.execute('create table interactions (id integer primary key, at integer, surface text, kind text, '
                        'word text, dwell_ms integer, view text, context text, position integer, uid text)')
            for w in ('なります', 'なります', '実は', '。', 'これは文です。', '関数', '再帰'):
                con.execute("insert into interactions (at, surface, kind, word, dwell_ms, view, context, position) "
                            "values (0, 't3', 'peek', ?, 900, '', '', 0)", (w,))
            con.commit()
            con.close()
            out = dictionary_coverage.report(home, self.path, Path(self.tmp.name) / 'lexicon.csv')
        self.assertEqual((out['lexicon']['resolved'], out['lexicon']['n'], out['lexicon']['other_gloss']), (2, 2, []))
        self.assertEqual((out['state']['resolved'], out['state']['n'], out['state']['misses']), (4, 6, ['マジ', '察']))
        self.assertEqual(out['state']['frequent'], ['おばさん'])  # resolved to an entry not marked common
        self.assertEqual(out['state']['entries']['そう'][2], 2137720)
        self.assertEqual((out['anki']['resolved'], out['anki']['misses']), (1, ['マジ']))
        self.assertEqual(out['log']['translations'], 1)
        self.assertEqual(out['unlisted']['sentences'], ['これは文です。'])
        self.assertEqual((out['unlisted']['resolved'], out['unlisted']['misses'], out['unlisted']['how']),
                         (2, ['再帰'], {'form': 1, 'written': 1}))
        self.assertEqual(out['unlisted']['frequent'], ['関数'])
        self.assertEqual(out['knowledge_unlisted']['sentences'], ['これは文です。'])


class Committed(unittest.TestCase):
    """The committed data/dictionary.tsv."""

    @classmethod
    def setUpClass(cls):
        cls.d = dictionary.load()

    def test_every_common_entry_in_rank_order(self):
        entries = list(self.d)
        self.assertGreater(sum(e['common'] > 0 for e in entries), 22000)
        frequent = [e for e in entries if e['common'] == 0]
        self.assertTrue(frequent and all(e['score'] <= build_dictionary.FREQUENT for e in frequent))
        self.assertEqual(len({e['id'] for e in entries}), len(entries))
        scores = [e['score'] for e in entries]
        ranked = scores[:scores.index(None)] if None in scores else scores
        self.assertEqual(ranked, sorted(ranked))
        self.assertTrue(all(s is None for s in scores[len(ranked):]))
        self.assertTrue(any('CC BY-SA 4.0' in c for c in self.d.comments))

    def test_every_lexicon_word_is_its_entry(self):
        with open(ROOT / 'data' / 'lexicon.csv', encoding='utf-8', newline='') as f:
            lexicon = list(csv.DictReader(f))
        for row in lexicon:
            e = self.d.find(row['ja'], row['kana'], row['en'])
            self.assertEqual((e['ja'], e['kana'], e['en'], e['romaji']),
                             (row['ja'], row['kana'], row['en'], row['hint']), row['ja'])

    def test_fast_folding_matches_inflection_on_every_spelling(self):
        words = [s for e in self.d for s in (e['ja'], e['kana'], *e['forms'])]
        self.assertEqual(dictionary.fold_all(words), [inflection.fold(w) for w in words])
        self.assertEqual([bool(dictionary._KANA.fullmatch(w)) for w in words], [dictionary.is_kana(w) for w in words])


if __name__ == '__main__':
    unittest.main()
