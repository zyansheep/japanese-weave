"""Words from your reading (scripts/reading.py): what counts, how the counts are kept and synced, and how intake ranks
the words they nominate. Run: python -m unittest discover -s tests"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import dictionary  # noqa: E402
import evaluate  # noqa: E402
import fwlib  # noqa: E402
import hub  # noqa: E402
import knowledge  # noqa: E402
import reading  # noqa: E402

# A dictionary of a few entries, in rank order: id, ja, kana, en, glosses (senses | glosses ;), pos, common, swap
ENTRIES = [
    (101, '見る', 'みる', 'see / look', 'to see;to look', 'v1', 3, 0),
    (102, '確認', 'かくにん', 'check / confirm', 'confirmation;verification;check', 'n,vs', 3, 1),
    (103, '使う', 'つかう', 'use', 'to use', 'v5u', 3, 0),
    (104, '約', 'やく', 'about / approximately', 'about;approximately', 'adv', 3, 1),
    (105, '戻る', 'もどる', 'go back / return', 'to go back;to return', 'v5r', 3, 0),
    (106, '奥', 'おく', 'back / depths', 'back;depths', 'n', 3, 1),
    (107, '国', 'くに', 'country / state', 'country;state', 'n', 3, 1),
    (108, '中古', 'ちゅうこ', 'used / second-hand', 'used;second-hand', 'n', 3, 1),
    (109, '状態', 'じょうたい', 'state / status', 'state;status', 'n', 3, 1),
    (110, '機能', 'きのう', 'feature / function', 'feature;function', 'n,vs', 3, 1),
    (111, '更新', 'こうしん', 'update / renewal', 'update;renewal', 'n,vs', 3, 1),
    (112, '削除', 'さくじょ', 'delete / removal', 'deletion;removal', 'n,vs', 3, 1),
    (113, '二人', 'ふたり', 'two people', 'two people;pair', 'n', 3, 1),
    (114, '関数', 'かんすう', 'function', 'function', 'n', 0, 1),
    (115, '殆ど', 'ほとんど', 'almost / mostly', 'almost;mostly', 'adv', 3, 1),
    (116, '構築', 'こうちく', 'build / construction', 'construction;building', 'n,vs', 3, 1),
]


def tiny(entries=ENTRIES):
    """The text of a dictionary file with these entries."""
    rows = ['# test dictionary', '\t'.join(dictionary.FIELDS)]
    for rank, (id_, ja, kana, en, glosses, pos, common, swap) in enumerate(entries, 1):
        rows.append('\t'.join([str(id_), ja, kana, kana, en, glosses, pos, str(rank * 10), '', '', str(common), '',
                               str(swap), 'ほとんど' if ja == '殆ど' else '']))
    return '\n'.join(rows) + '\n'


def line(text, day, sidechain=False):
    return json.dumps({'type': 'assistant', 'isSidechain': sidechain, 'timestamp': day + 'T12:00:00',
                       'message': {'content': [{'type': 'text', 'text': text}]}})


class Home(unittest.TestCase):
    """Each test gets its own state dir, and the counts are of the test dictionary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'dictionary.tsv').write_text(tiny(), encoding='utf-8')
        self.patches = [mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.root / 'fw'),
                                                     'JAPANESE_WEAVE_TRANSCRIPTS': str(self.root / 'chats')}),
                        mock.patch.object(reading, 'DICTIONARY', self.root / 'dictionary.tsv'),
                        mock.patch.object(reading, 'SENSES', self.root / 'no-senses.tsv')]
        for p in self.patches:
            p.start()
        self.d = reading.load_dictionary()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def found(self, text):
        reader = reading.Reader.open()
        try:
            return {self.d.entry(i)['ja']: n for i, n in reader.found(text).items()}
        finally:
            reader.close()


class Counting(Home):
    def test_prose_leaves_out_code_glosses_and_names(self):
        text = ('Run `make build` then see https://x.test/update. The <abbr title="こうしん · update">更新</abbr> '
                'works, read_replies in fwlib.py and fetchState too.\n(更新 = update, "kōshin")\n```\nupdate()\n```')
        out = reading.prose(text)
        for gone in ('make', 'x.test', 'こうしん', 'kōshin', 'read_replies', 'fwlib', 'fetchState', 'update('):
            self.assertNotIn(gone, out)
        self.assertIn('works', out)
        self.assertEqual(reading.glossed(text), [('更新', 'こうしん', 'update')])
        self.assertEqual(reading.glossed('<abbr title="but">でも</abbr> <abbr title="It is fine.">。</abbr>'),
                         [('でも', '', 'but')])

    def test_only_spans_a_sense_check_can_trust_count(self):
        self.assertTrue(reading.ensure())
        found = self.found('I checked the back of the shelf, then went back. It was about ten. We used it and '
                           'use it. The state of the function: the build was deleted.')
        self.assertEqual(found['確認'], 1)  # "checked": its main meaning, inflected by rule
        self.assertEqual(found['奥'], 1)  # "the back of"; "went back" is 戻る's (a verb: counted, not swappable)
        self.assertEqual(found['戻る'], 1)
        self.assertNotIn('約', found)  # closed-class English never counts
        self.assertNotIn('中古', found)  # "used" is 使う's past before it is 中古 "used"
        self.assertEqual(found['使う'], 1)
        self.assertNotIn('国', found)  # "state" is 国's second meaning, and the most frequent option: none counts
        self.assertNotIn('状態', found)
        self.assertNotIn('機能', found)  # likewise "function" (機能 "feature / function" before 関数)
        self.assertNotIn('関数', found)
        self.assertEqual(found['構築'], 1)  # "build" as written, its main meaning
        self.assertEqual(found['削除'], 1)  # "deleted": a suru noun takes the verb forms of its gloss

    def test_split_first_words_and_sense_flags(self):
        reading.ensure()
        text = 'The update failed, so I checked the function and its state, then the back of it.'
        whole = self.found(text)
        with mock.patch.object(reading, 'SPLIT', 0):  # every first word stored by its second words too
            reading.build()
            self.assertEqual(self.found(text), whole)
        flags = self.root / 'senses.tsv'
        flags.write_text('flags', encoding='utf-8')
        senses = types.ModuleType('senses')
        senses.load = lambda path: {('関数', 'function'), ('状態', 'state')}
        senses.sense_flag = lambda f, entry, gloss: 's' if (entry['ja'], gloss) in f else 'd'
        with mock.patch.object(reading, 'SENSES', flags), mock.patch.dict(sys.modules, {'senses': senses}):
            self.assertIsNone(reading.Reader.open())  # new flags: out of date until built again
            reading.ensure()
            self.assertEqual(self.found(text), {'関数': 1, '状態': 1})  # only safe options count, the most frequent
        with mock.patch.object(reading, 'SENSES', flags), mock.patch.dict(sys.modules, {'senses': None}), \
                mock.patch('sys.stderr', new=io.StringIO()) as err:
            reading.build()  # a flags file that can't be read: counted without flags, and said so
            self.assertIn('sense flags unreadable', err.getvalue())
            self.assertEqual(self.found(text), whole)

    def test_a_reply_counts_its_english_and_its_japanese(self):
        reading.ensure()
        tally = reading.Tally([{'ja': '殆ど', 'kana': 'ほとんど', 'en': 'almost'}], d=self.d)
        ids = lambda counts: {self.d.get(int(k))['ja']: n for k, n in counts.items()}
        self.assertEqual(ids(tally.count('Checked: 殆ど done, the update too.', ['殆ど'])),
                         {'確認': 1, '殆ど': 1, '更新': 1})
        tagged = ('The <abbr title="こうしん · update">更新</abbr> and <abbr title="こうしん · update">更新</abbr> '
                  'passed<abbr title="It passed.">。</abbr> 殆ど')  # with <abbr> tags, the tags alone
        self.assertEqual(ids(tally.count(tagged, ['殆ど'])), {'更新': 2})
        tally.close()

    def test_counts_per_entry_and_day_are_kept_thirty_days_and_counted_once(self):
        today = fwlib.today()
        old, recent = fwlib.add_days(today, -fwlib.READING_DAYS), fwlib.add_days(today, -3)
        index = fwlib.fold({}, [{'d': 'a', 'day': today, 'words': [], 'entries': {'102': 2, '111': 1}},
                                {'d': 'b', 'day': recent, 'words': ['今'], 'entries': {'102': 1}},
                                {'d': 'c', 'day': old, 'words': [], 'entries': {'102': 1}},
                                {'d': 'a', 'day': today, 'words': [], 'entries': {'102': 2}}])  # the same reply again
        self.assertEqual(index['reading'], {recent: {'102': 1}, today: {'102': 2, '111': 1}})
        self.assertEqual(index['readingDays'], {'102': 2, '111': 1})
        self.assertEqual(set(index['read']), {'a', 'b'})
        self.assertEqual(index['words']['今']['n'], 1)
        # a reply counted for words before reading was counted (the first scan after an update reads it again)
        index = fwlib.fold({'words': {'今': {'n': 1, 'days': [today]}}, 'recent': {'e': today}},
                           [{'d': 'e', 'day': today, 'words': ['今'], 'entries': {'111': 1}}])
        self.assertEqual((index['words']['今']['n'], index['reading']), (1, {today: {'111': 1}}))
        self.assertEqual(fwlib.reading_days({'reading': {today: {'1': 1}, recent: {'1': 2}}}, recent), {'1': 1})

    def test_the_scan_counts_the_last_thirty_days_once_then_resumes(self):
        state = fwlib.load_state()
        word = next(w for w in state['words'] if w['status'] == 'active')
        chats = self.root / 'chats' / 'p'
        chats.mkdir(parents=True)
        today, old = fwlib.today(), fwlib.add_days(fwlib.today(), -10)
        chat = chats / 'a.jsonl'
        chat.write_text(line(f'The update is done, {word["ja"]}.', today) + '\n'
                        + line('An old update from before setup.', old) + '\n'
                        + line('Japanese Weave notes: the update.', today) + '\n'
                        + line('A subagent update.', today, sidechain=True) + '\n')
        state['created'] = today
        fwlib.save_state(state)
        cursor = {'offsets': {str(chat): chat.stat().st_size}}  # an install from before: everything scanned
        fwlib.write_json(fwlib.home() / fwlib.SCAN, cursor)
        with mock.patch('sys.stderr', new=io.StringIO()):
            self.assertTrue(fwlib.refresh_exposure())
        index = fwlib.read_json(fwlib.home() / 'exposure.json')
        self.assertEqual(index['reading'], {today: {'111': 1}, old: {'111': 1}})  # read again, from before setup too
        self.assertEqual(index['words'][word['ja']]['n'], 1)  # words: from setup on only
        self.assertEqual(fwlib.read_json(fwlib.home() / fwlib.SCAN)['reading'], 1)
        with open(chat, 'a') as handle:
            handle.write(line('Another update.', today) + '\n')
        fwlib.refresh_exposure()
        index = fwlib.read_json(fwlib.home() / 'exposure.json')
        self.assertEqual(index['reading'][today], {'111': 2})
        self.assertEqual(index['readingDays'], {'111': 2})
        with mock.patch.object(reading.Tally, 'count', side_effect=RuntimeError('broken')), \
                mock.patch('sys.stderr', new=io.StringIO()) as err:
            with open(chat, 'a') as handle:
                handle.write(line(f'And {word["ja"]} again.', today) + '\n')
            fwlib.refresh_exposure()  # the entries can't be counted: the words still are
        self.assertIn('reading not counted', err.getvalue())
        self.assertEqual(fwlib.read_json(fwlib.home() / 'exposure.json')['words'][word['ja']]['n'], 2)

    def test_exposure_ops_carry_entries_and_the_snapshot_their_days(self):
        replies = [{'d': 'a', 'day': fwlib.today(), 'words': [], 'entries': {'102': 2}}]
        self.assertIsNone(hub.validate(hub.stamp({'kind': 'exposure', 'replies': replies})))
        for bad in ({'102': 0}, {'x': 1}, {'102': 1.5}, ['102'], {'1' * 13: 1}):
            self.assertIsNotNone(hub.validate(hub.stamp({'kind': 'exposure', 'replies': [dict(replies[0],
                                                                                            entries=bad)]})), bad)
        fwlib.load_state()
        self.assertTrue(hub.run(hub.stamp({'kind': 'exposure', 'replies': replies}))['ok'])
        status, body = hub.snapshot()
        self.assertEqual(body['exposure']['readingDays'], {'102': 1})
        self.assertNotIn('reading', body['exposure'])  # the counts by day stay where intake runs


class Intake(Home):
    def exposure(self, days):
        """An exposure index where each entry id came up on so many of the last days."""
        reading_ = {}
        for id_, n in days.items():
            for k in range(n):
                reading_.setdefault(fwlib.add_days(fwlib.today(), -k), {})[str(id_)] = 1
        return {'reading': reading_}

    def state(self):
        word = lambda ja, status: dict(fwlib.blank({'ja': ja, 'en': 'meaning', 'hint': 'hint', 'kana': ''}),
                                       status=status, origin='new')
        return {'words': [word('確認', 'known'), word('ほとんど', 'queue'), word('今', 'active')]}

    def test_eligible_after_three_days_in_thirty_ranked_by_days_chance_and_rank(self):
        state = self.state()
        self.assertIsNone(reading.nominations(state, self.exposure({111: 2}), d=self.d))  # 2 days counted: too few
        days = {102: 9, 111: 5, 112: 5, 116: 5, 114: 2, 113: 7, 103: 9, 115: 4, 106: 3}
        ranked = reading.nominations(state, self.exposure(days), d=self.d)
        # 102 確認 is in use; 113 二人 a number; 103 使う not swappable; 114 関数 on 2 days only
        self.assertEqual([w['ja'] for w in ranked], ['更新', '削除', '構築', 'ほとんど', '奥'])
        self.assertEqual(ranked[0], {'ja': '更新', 'en': 'update / renewal', 'hint': 'こうしん', 'kana': 'こうしん',
                                     'id': 111})
        self.assertIs(ranked[3], state['words'][1])  # 殆ど is the state's ほとんど, another spelling of it
        know = {'words': {'ほとんど': {'k': 4.0}}}  # known at a glance by its estimate: labeled known, not taught
        self.assertNotIn('ほとんど', [w['ja'] for w in reading.nominations(state, self.exposure(days), know, d=self.d)])
        # the starting chance ranks words seen on as many days: nearest 25% first, in half-unit steps of log-odds
        state['words'].append(dict(state['words'][1], ja='削除'))
        know = {'words': {'削除': {'k': -1.0}}}  # chance 0.73, far from 0.25; the others at the neutral prior, 0.27
        self.assertEqual([w['ja'] for w in reading.nominations(state, self.exposure(days), know, d=self.d)][:3],
                         ['更新', '構築', '削除'])
        prior = {'none': {'new': 3.0, 'learning': 1, 'known': 2.5}, 'card': {}, 'interval': 0,
                 'sd': {'none': 1.5, 'card': 1}}  # the fitted prior of a new word: 0.05, known at a glance
        self.assertEqual(reading.nominations(state, self.exposure(days), {'prior': prior}, d=self.d), [])
        held = dict(state['words'][1], ja='更新', status='active', added=fwlib.today(),
                    arm={'arm': 'held', 'draw': 0.1, 'day': fwlib.today(), 'probe': 'x'})
        self.assertTrue(fwlib.hidden(held))
        state['words'].append(held)  # hidden by the experiment: in use, so never nominated again
        self.assertNotIn('更新', [w['ja'] for w in reading.nominations(state, self.exposure(days), d=self.d)])

    def test_intake_takes_nominated_words_in_and_adds_dictionary_words_to_the_state(self):
        state = fwlib.new_state(fwlib.today())
        state['intake'] = []
        unspent = {'browser': {'plans': 9, 'budget': 2.0, 'spent': 0.5, 'share': 0.25}}
        queue = [w['ja'] for w in state['words'] if w['status'] == 'queue']
        nominated = [{'ja': '関数', 'en': 'function', 'hint': 'kansū', 'kana': 'かんすう', 'id': 114},
                     next(w for w in state['words'] if w['ja'] == 'ほとんど')]
        log = evaluate.Log([], {}, [])
        op = knowledge.decide(state, {}, {queue[-1]: 3}, unspent, log, fwlib.today(), seed=1, nominated=nominated)
        self.assertEqual([row[0] for row in op['intake']], [queue[-1], '関数', 'ほとんど'])  # looked up first
        self.assertEqual(op['intake'][1], ['関数', *fwlib.assign(1, '関数'), 114])
        self.assertEqual(len(op['intake'][2]), 3)
        self.assertIsNone(hub.validate(hub.stamp(op)))
        self.assertIsNotNone(hub.validate(hub.stamp(dict(op, intake=[['関数', None, None, 'x']]))))
        op['intake'].append(['奥行き', None, None, 999])  # no such entry: skipped
        result = fwlib.apply_labels(state, op, fwlib.today())
        self.assertEqual(result['intake'], [queue[-1], '関数', 'ほとんど'])
        added = state['words'][-1]
        self.assertEqual({k: added[k] for k in ('ja', 'en', 'hint', 'kana', 'id', 'status', 'origin')},
                         {'ja': '関数', 'en': 'function', 'hint': 'かんすう', 'kana': 'かんすう', 'id': 114,
                          'status': 'active', 'origin': 'learning'})
        self.assertEqual(added['arm']['arm'], fwlib.assign(1, '関数')[0])
        fwlib.merge_lexicon(state)  # the list's words keep their order; a dictionary word stays, at the end
        self.assertEqual(state['words'][-1]['ja'], '関数')
        # without counts of your reading, the list's order as before
        self.assertEqual(fwlib.newcomers(state, {}, ())[0]['ja'], next(w['ja'] for w in state['words']
                                                                         if w['status'] == 'queue'))


if __name__ == '__main__':
    unittest.main()
