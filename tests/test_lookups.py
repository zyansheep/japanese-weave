"""Offline tests for looked-up words: dictionary forms, scoring words not introduced yet, and what comes in first.
Run: python -m unittest tests/test_lookups.py"""
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'
for name in ('CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ATTENDED', 'JAPANESE_WEAVE_FORCE', 'JAPANESE_WEAVE_OFF'):
    os.environ.pop(name, None)  # tests set these explicitly

import fwlib  # noqa: E402
import fw  # noqa: E402
import feedback  # noqa: E402
import hub  # noqa: E402
import inflection  # noqa: E402
import knowledge  # noqa: E402

# (ja, kana) in lexicon order: verbs of every kind, an adjective, nouns
WORDS = [('なる', 'なる'), ('消える', 'きえる'), ('待つ', 'まつ'), ('来る', 'くる'), ('出る', 'でる'), ('見える', 'みえる'),
         ('する', 'する'), ('作る', 'つくる'), ('戻る', 'もどる'), ('違う', 'ちがう'), ('いる', 'いる'), ('事', 'こと'),
         ('熱', 'ねつ'), ('遅い', 'おそい'), ('いい', 'いい'), ('分かる', 'わかる'), ('確認', 'かくにん'),
         ('大事', 'だいじ'), ('行く', 'いく'), ('読む', 'よむ'), ('書く', 'かく'), ('答え', 'こたえ'), ('答える', 'こたえる'),
         ('入る', 'はいる'), ('入れる', 'いれる'), ('変える', 'かえる'), ('帰る', 'かえる'), ('休む', 'やすむ')]


def lexicon(words=WORDS):
    return inflection.Lexicon([{'ja': ja, 'kana': kana} for ja, kana in words])


class Forms(unittest.TestCase):
    def test_forms_from_the_log_find_their_entries(self):
        entry = lexicon().entry
        found = {form: entry(form) for form in (
            'なります', '消えません', '待っています', '来ます', '出てきます', '見えなく', 'しています', '作りましょうか',
            '戻れます', '違います', 'こと', '熱')}
        self.assertEqual(found, {'なります': 'なる', '消えません': '消える', '待っています': '待つ', '来ます': '来る',
                                 '出てきます': '出る', '見えなく': '見える', 'しています': 'する', '作りましょうか': '作る',
                                 '戻れます': '戻る', '違います': '違う', 'こと': '事', '熱': '熱'})

    def test_only_real_entries_of_the_right_kind_match(self):
        entry = lexicon().entry
        for form in ('戻せます',  # 戻す, not in the list: never 戻る
                     '要ります', 'いります',  # 要る is godan; いる (to exist) is ichidan
                     '消すこと', 'つながっています', '通りました', '間違えません', '合います', '流れます',
                     '休み',  # a bare masu stem is usually a noun
                     'リスト', '燃料', '。'):
            self.assertIsNone(entry(form), form)
        self.assertEqual(lexicon(WORDS + [('消す', 'けす')]).entry('消すこと'), '消す')

    def test_the_closest_entry_wins_then_the_earliest(self):
        entry = lexicon(WORDS + [('出てくる', 'でてくる')]).entry
        self.assertEqual(entry('出てきます'), '出てくる')  # the compound when listed, though 出る comes first
        self.assertEqual(entry('入れます'), '入れる')  # not the potential of 入る
        self.assertEqual(entry('入ります'), '入る')
        self.assertEqual(entry('答え'), '答え')  # as written first, not the stem of 答える
        self.assertEqual(entry('かえります'), '帰る')  # godan: only 帰る, though 変える comes first
        self.assertEqual(entry('かえます'), '変える')
        swapped = lexicon(WORDS + [('替える', 'かえる')]).entry
        self.assertEqual(swapped('かえて'), '変える')  # two ichidan かえる: the earlier one
        self.assertEqual(lexicon([('替える', 'かえる')] + WORDS).entry('かえて'), '替える')

    def test_more_endings(self):
        entry = lexicon().entry
        found = {form: entry(form) for form in (
            'わかりました', 'よかった', 'よくない', '遅くなかった', '遅いです', '確認します', '確認しました', '大事な',
            '行った', '読んで', '書かれていません', '分かりやすい', '休みます', '来ない', 'しない', '待ってる', '見れる')}
        self.assertEqual(found, {'わかりました': '分かる', 'よかった': 'いい', 'よくない': 'いい', '遅くなかった': '遅い',
                                 '遅いです': '遅い', '確認します': '確認', '確認しました': '確認', '大事な': '大事',
                                 '行った': '行く', '読んで': '読む', '書かれていません': '書く', '分かりやすい': '分かる',
                                 '休みます': '休む', '来ない': '来る', 'しない': 'する', '待ってる': '待つ',
                                 '見れる': None})  # 見る is not in the list

    def test_lexicons_are_reused_while_the_words_stay_the_same(self):
        words = [{'ja': ja, 'kana': kana} for ja, kana in WORDS]
        self.assertIs(inflection.lexicon(words), inflection.lexicon([dict(w) for w in words]))
        self.assertIsNot(inflection.lexicon(words), inflection.lexicon(words[:-1]))

    def test_find_takes_forms_too(self):
        state = {'words': [dict(fwlib.blank({'ja': ja, 'en': 'meaning', 'hint': 'hint', 'kana': kana}))
                           for ja, kana in WORDS]}
        self.assertEqual(fwlib.find(state, 'なります')['ja'], 'なる')
        self.assertEqual(fwlib.find(state, 'わかる')['ja'], '分かる')  # a reading, as before
        self.assertIsNone(fwlib.find(state, '戻せます'))
        self.assertIsNone(fwlib.find(state, 'nope'))


class Home(unittest.TestCase):
    """Each test gets its own state dir, with a few verbs from Anki ahead of the base list."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats')})  # never yours
        self.env.start()
        (self.home / 'fw').mkdir()
        rows = ['ja,en,hint,kana'] + [f'{ja},meaning,hint,{kana}' for ja, kana in WORDS[:6]]
        (self.home / 'fw' / fwlib.ANKI_LEXICON).write_text('\n'.join(rows) + '\n', encoding='utf-8')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


def message(view, notes, opened, at):
    """A T3 message with these glossed words ([word, x, y]) and peeks on the opened indexes."""
    return [{'kind': 'peek', 'word': notes[i][0], 'at': at + 1000 * n, 'dwellMs': 1500, 'view': view, 'context': 'm',
             'position': i, 'box': [0, 0, 600, 40], 'annotations': notes, 'uid': f'{view}-{i}'}
            for n, i in enumerate(opened)]


class Scoring(Home):
    def test_t3_lookups_of_forms_and_queue_words_count(self):
        state = fwlib.load_state()
        self.assertEqual(fwlib.find(state, '熱')['status'], 'queue')
        self.assertEqual(fwlib.find(state, 'なる')['status'], 'active')
        now = time.time()
        notes = [['情報', 0, 0], ['なります', 40, 0], ['熱', 80, 0], ['リスト', 120, 0], ['。', 160, 0]]
        items = message('v1', notes, [1, 2, 3], int((now - 7200) * 1000)) + message('v2', notes, [2, 3], int((now - 3600) * 1000))
        self.assertEqual(feedback.record(items, 't3')['stored'], 5)
        know = knowledge.refresh(now)
        words = know['words']
        self.assertEqual((words['熱']['opened'], words['熱']['recent'], words['熱']['mu']), (2, 2, 1.0))  # a queue word
        self.assertEqual(words['なる']['opened'], 1)  # なります
        self.assertEqual((words['情報']['obs'], words['情報']['opened']), (2, 0))  # unopened neighbour, not introduced
        self.assertNotIn('リスト', words)
        self.assertEqual(know['unlisted'], {'days': knowledge.UNLISTED_DAYS, 'opens': 2, 'words': 1, 'top': [['リスト', 2]]})
        self.assertEqual(fwlib.looked_up(), {'熱': 2})

    def test_forms_of_one_word_make_one_observation(self):
        notes = [['しています', 0, 0], ['する', 40, 0], ['熱', 0, 20], ['燃料', 40, 20]]
        rows = [{'kind': 'peek', 'word': 'する', 'dwell_ms': 1500, 'position': 1}]
        got = knowledge.observations('t3', {'annotations': notes}, rows, lexicon().entry)
        self.assertEqual(sorted(got), [('する', 1.0, 1.0), ('熱', 0.0, 0.5)])

    def test_queue_words_never_move(self):
        state = {'words': [dict(fwlib.blank({'ja': ja, 'en': 'meaning', 'hint': 'hint', 'kana': ''}), status=status)
                           for ja, status in (('熱', 'queue'), ('今', 'known'), ('情報', 'queue'))]}
        words = {}
        for ja in ('熱', '今', '情報'):
            words[ja] = knowledge.Word(1.0)
            words[ja].k, words[ja].n, words[ja].recent = (-2.0 if ja != '情報' else 4.0), 20, [1]
        moves, veto = knowledge.plan_moves(state, words, {}, 2)
        self.assertEqual((moves, veto), ([('今', 'known', 'shaky')], []))

    def test_commands_take_forms(self):
        result = hub.run(hub.stamp({'kind': 'mark', 'words': ['待っています'], 'as': 'known'}))
        self.assertEqual(result['canonical'], ['待つ'])
        self.assertEqual(fwlib.find(fwlib.load_state(), '待つ')['status'], 'known')
        self.assertEqual([c['word'] for c in knowledge.read_commands()], ['待つ'])


class Refill(Home):
    def looked(self, counts):
        fwlib.write_json(self.home / 'fw' / knowledge.KNOWLEDGE,
                         {'words': {ja: {'k': 0.5, 'sd': 1.0, 'recent': n} for ja, n in counts.items()}})

    def test_looked_up_words_come_in_first_most_opened_first(self):
        state = fwlib.new_state('2026-10-01')
        queue = [w['ja'] for w in state['words'] if w['status'] == 'queue']
        self.looked({queue[40]: 2, queue[30]: 3, queue[50]: 2, queue[60]: 1, state['words'][0]['ja']: 9})
        fwlib.promote(state, '2026-10-02', {})
        added = {w['ja'] for w in state['words'] if w['changed'] == '2026-10-02'}
        self.assertEqual(added, {queue[30], queue[40], queue[50], queue[0], queue[1]})  # then lexicon order
        self.assertEqual(fwlib.find(state, queue[60])['status'], 'queue')  # opened in one message only: waits its turn

    def test_the_order_and_the_limits(self):
        state = {'words': [dict(fwlib.blank({'ja': f'w{i}', 'en': 'meaning', 'hint': 'hint', 'kana': ''}))
                           for i in range(30)]}
        looked = {'w9': 2, 'w7': 4, 'w8': 2}
        self.assertEqual(fwlib.refill(state, 'd1', cap=2, looked=looked), 2)
        self.assertEqual([w['ja'] for w in state['words'] if w['status'] == 'active'], ['w7', 'w8'])  # most opened first
        self.assertEqual(fwlib.refill(state, 'd1', looked=looked), fwlib.NEW_PER_DAY - 2)
        self.assertEqual([w['ja'] for w in state['words'] if w['status'] == 'active'], ['w0', 'w1', 'w7', 'w8', 'w9'])
        self.assertEqual(fwlib.refill(state, 'd1', looked={'w20': 5}), 0)  # today's new words are in
        for day in ('d2', 'd3', 'd4'):
            fwlib.refill(state, day, looked={'w20': 5})
        self.assertEqual(sum(w['status'] == 'active' for w in state['words']), fwlib.WINDOW)
        self.assertEqual(fwlib.find(state, 'w20')['status'], 'active')
        self.assertEqual(fwlib.refill(state, 'd5', looked={'w29': 5}), 0)  # the learning set is full

    def test_status_and_calibrate_show_lookups(self):
        fwlib.load_state()  # today's five new words are in
        self.looked({'熱': 3, '情報': 2, '問題': 1})
        know = fwlib.read_json(knowledge.path())
        know.update(calibration=knowledge.calibrate([], {}, [], int(time.time() * 1000)),
                    unlisted={'days': 30, 'opens': 9, 'words': 2, 'top': [['燃料', 7], ['リスト', 2]]})
        fwlib.write_json(knowledge.path(), know)
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['status'])
            fw.main(['calibrate'])
        self.assertIn('next in, as you looked them up: 熱 = fever / heat, 情報 = information / info\n', out.getvalue())
        self.assertIn('Opened but not in the word list, last 30 days (9 opens of 2 words): 燃料 7, リスト 2',
                      out.getvalue())


if __name__ == '__main__':
    unittest.main()
