"""Offline tests for the hook, word state, native host and setup. Run: python3 -m unittest discover -s tests"""
import copy
import io
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
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
import weave  # noqa: E402
import fw_host  # noqa: E402
import fw  # noqa: E402
import hub  # noqa: E402
import export  # noqa: E402
import anki  # noqa: E402
import feedback  # noqa: E402
import collector  # noqa: E402
import kana  # noqa: E402
import intensity  # noqa: E402
import knowledge  # noqa: E402


def line(text, day='2026-10-09', sidechain=False):
    return json.dumps({'type': 'assistant', 'isSidechain': sidechain, 'timestamp': day + 'T12:00:00',
                       'message': {'content': [{'type': 'text', 'text': text}]}})


def word(ja, status='known'):
    return {'ja': ja, 'en': 'meaning', 'hint': 'hint', 'status': status, 'added': '2026-10-01', 'changed': '',
            'days': [], 'last_bad': ''}  # with the day-count fields, as before the switch


class Home(unittest.TestCase):
    """Each test gets its own state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats')})  # never yours
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Matching(unittest.TestCase):
    def hits(self, ja, text, reading=''):
        return bool(fwlib.pattern(ja, reading).search(fwlib.norm(text)))

    def test_word_matching(self):
        self.assertTrue(self.hits('今', 'it works 今, really'))
        self.assertFalse(self.hits('今', '今日 is fine'))  # inside a longer kanji word
        self.assertTrue(self.hits('今日', '今日 is fine'))
        self.assertTrue(self.hits('問題', 'この問題は簡単'))  # kana neighbors are particles, still the word
        self.assertFalse(self.hits('問題', '問題点'))
        self.assertTrue(self.hits('でも', 'でも the cache'))
        self.assertFalse(self.hits('でも', 'どうでもいい'))  # inside a kana run
        self.assertTrue(self.hits('多分', 'たぶん it works', 'たぶん'))  # the reading counts when 3+ kana
        self.assertFalse(self.hits('今', 'います', 'いま'))  # short readings never count
        self.assertTrue(self.hits('問題', 'Ｔhe 問題 is'))

    def test_romaji(self):
        cases = {'とうきょう': 'tōkyō', 'がっこう': 'gakkō', 'しゃしん': 'shashin', 'まっちゃ': 'matcha', 'きんえん': "kin'en",
                 'せんせい': 'sensei', 'ジュース': 'jūsu', 'ちょっと': 'chotto', 'じゅうぶん': 'jūbun', 'もう': 'mō'}
        for reading, roman in cases.items():
            self.assertEqual(kana.romaji(reading), roman)
        self.assertEqual(kana.romaji('おもう', verb_u=True), 'omou')
        self.assertEqual(fwlib.roman_key('Tōkyō'), fwlib.roman_key('toukyou'))
        self.assertEqual(fwlib.roman_key("kin'en"), fwlib.roman_key('kinen'))


class Scan(unittest.TestCase):
    def test_scan_dedupes_and_skips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chat = root / 'proj' / 'a.jsonl'
            chat.parent.mkdir()
            chat.write_text('\n'.join([
                line('Done, 今 the build passes.'),
                line('Done, 今 the build passes.'),  # forked copy: counted once
                line('今 inside a subagent', sidechain=True),
                line('Run `git add 今` then check https://x.test/今'),
                line('Japanese Weave notes: 今, でも'),
                line('Old reply, 今 it predates install.', day='2026-09-01'),
            ]) + '\n' + line('partial でも'))  # no newline: half-written
            (root / 'proj' / 's' / 'subagents').mkdir(parents=True)
            (root / 'proj' / 's' / 'subagents' / 'b.jsonl').write_text(line('今 でも') + '\n')
            words = ['今', 'でも', {'ja': '多分', 'kana': 'たぶん'}]
            index = fwlib.scan(words, {}, root, start='2026-09-19')
            self.assertEqual(index['words']['今'], {'n': 1, 'days': ['2026-10-09']})
            self.assertEqual(index['words']['でも'], {'n': 0, 'days': []})
            with open(chat, 'a') as file:
                file.write('\n' + line('Again 今, 今 でも, たぶん.', day='2026-10-11') + '\n')
            index = fwlib.scan(words, index, root, start='2026-09-19')
            self.assertEqual(index['words']['今'], {'n': 2, 'days': ['2026-10-09', '2026-10-11']})
            self.assertEqual(index['words']['でも']['n'], 2)  # the finished partial line and the new one
            self.assertEqual(index['words']['多分']['n'], 1)


class Rules(Home):
    def test_lexicon_is_well_formed(self):
        words = fwlib.load_lexicon()
        self.assertEqual(len({fwlib.key(w['ja']) for w in words}), len(words))
        for w in words:
            self.assertTrue(w['en'] and w['hint'] and w['hint'] == w['hint'].lower(), w)
            self.assertTrue(kana.is_kana(w['kana']) and fwlib.JAPANESE.search(w['ja']), w)

    def test_new_state_starts_small(self):
        state = fwlib.new_state('2026-10-01')
        active = [w for w in state['words'] if w['status'] == 'active']
        self.assertEqual([w['ja'] for w in active], [w['ja'] for w in fwlib.load_lexicon()[:5]])  # 5 new a day
        self.assertTrue(all(w['origin'] == 'learning' and 'arm' not in w for w in active))  # every first word on show
        self.assertEqual(fwlib.taken_today(state, '2026-10-01'), fwlib.NEW_PER_DAY)
        self.assertEqual(state['labels'], {'from': 'model', 'since': '2026-10-01'})
        self.assertEqual(fwlib.stage_for(state), 1)
        self.assertEqual(fwlib.blank({'ja': 'x'}), {'ja': 'x', 'status': 'queue', 'added': '', 'changed': ''})

    def test_labels_come_from_the_model(self):
        state = fwlib.new_state('2026-10-01')
        learning, queued = state['words'][0], state['words'][40]
        self.assertEqual([fwlib.label(learning, f) for f in (True, False)], ['known', 'active'])
        self.assertEqual([fwlib.label(queued, f) for f in (True, False)], ['known', 'queue'])  # free: known, not new
        fwlib.apply_event(state, learning['ja'], 'known', '2026-10-02')  # a command since the refresh decided
        done = fwlib.relabel(state, [[learning['ja'], 'active', 'known'], [queued['ja'], 'queue', 'known'],
                                     [state['words'][1]['ja'], 'queue', 'known']], '2026-10-03')
        self.assertEqual(done, [[queued['ja'], 'queue', 'known']])  # only where the label is still the one decided on
        self.assertEqual((queued['status'], queued['added'], queued['origin']), ('known', '2026-10-03', 'new'))
        self.assertEqual(fwlib.label(queued, False), 'active')  # in use since: learning once it isn't free

    def test_commands_set_the_label_at_once_and_events_change_nothing(self):
        state = fwlib.new_state('2026-10-01')
        before = json.dumps(state, sort_keys=True)
        for kind in ('seen', 'peek', 'flip', 'assumed'):
            self.assertTrue(fwlib.apply_event(state, '前', kind, '2026-10-02'))
        self.assertEqual(json.dumps(state, sort_keys=True), before)  # no day counts: the ops log keeps them
        fwlib.apply_event(state, '前', 'known', '2026-10-02')
        self.assertEqual(fwlib.find(state, '前')['status'], 'known')
        fwlib.apply_event(state, '前', 'asked', '2026-10-03')
        self.assertEqual(fwlib.find(state, '前')['status'], 'active')
        self.assertFalse(fwlib.apply_event(state, 'nope', 'known'))
        self.assertFalse(fwlib.apply_event(state, '前', 'jump'))

    def test_intake_order_and_the_days_count(self):
        state = fwlib.new_state('2026-10-01')
        queue = [w['ja'] for w in state['words'] if w['status'] == 'queue']
        order = [w['ja'] for w in fwlib.newcomers(state, {queue[30]: 2, queue[9]: 5}, free={queue[0]})]
        self.assertEqual(order[:4], [queue[9], queue[30], queue[1], queue[2]])  # looked up first; never a free word
        self.assertEqual(fwlib.take_in(state, [[queue[9], 'held', 0.25], [queue[1], None, None], [queue[0], None, None],
                                               [state['words'][0]['ja'], None, None]], '2026-10-02'),
                         [queue[9], queue[1], queue[0]])  # a word in use already isn't taken again
        self.assertEqual(fwlib.taken_today(state, '2026-10-02'), 3)
        self.assertEqual(fwlib.taken_today(state, '2026-10-03'), 0)
        held = fwlib.find(state, queue[9])
        self.assertEqual((held['status'], held['origin'], held['arm']),
                         ('active', 'learning', {'arm': 'held', 'draw': 0.25, 'day': '2026-10-02', 'probe': '2026-10-23'}))
        self.assertNotIn('arm', fwlib.find(state, queue[1]))

    def test_the_switch_keeps_the_old_fields_a_month_and_can_be_undone(self):
        words = [dict(word('今'), changed='2026-10-01', days=['2026-10-01'], peeks=['2026-10-02']),
                 dict(word('前', 'shaky'), last_bad='2026-10-03'), dict(word('中', 'active')), dict(word('何', 'queue'), added=''),
                 dict(word('多分', 'queue'), added='')]
        state = {'stage': 1, 'stage_pinned': False, 'paused': False, 'words': words, 'refilled': ['2026-10-04', 2]}
        result = fwlib.apply_labels(state, {'labels': [['前', 'shaky', 'active'], ['今', 'known', 'known']],
                                            'intake': [['何', 'shown', 0.75]], 'experiment': {'seed': 7}}, '2026-10-05')
        self.assertTrue(result['switched'])
        self.assertEqual((result['labels'], result['intake']), ([['前', 'shaky', 'active']], ['何']))
        by = {w['ja']: w for w in state['words']}
        self.assertEqual([by[ja]['origin'] for ja in ('今', '前', '中', '何', '多分')],
                         ['known', 'known', 'learning', 'learning', 'new'])  # the prior's feature, never the label
        self.assertEqual(by['前']['legacy'], {'status': 'shaky', 'added': '2026-10-01', 'changed': '', 'days': [],
                                             'last_bad': '2026-10-03'})
        self.assertNotIn('peeks', by['今'])
        self.assertEqual((state['legacy'], state['experiment']),
                         ({'refilled': ['2026-10-04', 2]}, {'name': 'delayed-start', 'seed': 7, 'since': '2026-10-05'}))
        self.assertFalse(fwlib.apply_labels(state, {}, '2026-10-06')['switched'])  # once
        undone = copy.deepcopy(state)
        self.assertEqual(fwlib.restore(undone, '2026-10-07'), 5)
        back = {w['ja']: w for w in undone['words']}
        self.assertEqual((back['前']['status'], back['前']['last_bad'], back['今']['peeks']), ('shaky', '2026-10-03',
                                                                                            ['2026-10-02']))
        self.assertEqual((back['何']['status'], back['何']['days']), ('active', []))  # taken in since: still learning
        self.assertEqual((undone['labels']['from'], undone['refilled']), ('rules', ['2026-10-04', 2]))
        self.assertEqual(fwlib.apply_labels(undone, {'labels': [['前', 'shaky', 'known']]}, '2026-10-08')['labels'], [])
        self.assertIsNone(fwlib.restore(undone, '2026-10-08'))  # nothing left to restore
        fwlib.expire_legacy(state, '2026-11-03')
        self.assertIn('legacy', by['今'])  # 29 days on
        fwlib.expire_legacy(state, '2026-11-04')
        self.assertFalse(any('legacy' in w for w in state['words']) or 'legacy' in state)
        self.assertIsNone(fwlib.restore(state, '2026-11-04'))  # too late to undo

    def test_stage_auto_and_pinned(self):
        state = fwlib.new_state('2026-10-01')
        stages = []
        for i, w in enumerate(state['words'][:400], 1):
            fwlib.apply_event(state, w['ja'], 'known', '2026-10-01')
            if i in (39, 40, 149, 150, 399, 400):
                stages.append(fwlib.stage_for(state))
        self.assertEqual(stages, [1, 2, 2, 3, 3, 4])
        state['stage'], state['stage_pinned'] = 1, True
        self.assertEqual(fwlib.stage_for(state), 1)

    def test_find_accepts_kana_and_romaji(self):
        state = fwlib.new_state('2026-10-01')
        self.assertEqual(fwlib.find(state, 'たぶん')['ja'], '多分')
        self.assertEqual(fwlib.find(state, 'Tabun')['ja'], '多分')
        self.assertEqual(fwlib.find(state, 'jitsuwa')['ja'], '実は')
        self.assertEqual(fwlib.find(state, 'jitsu wa')['ja'], '実は')
        self.assertEqual(fwlib.find(state, 'mondai')['ja'], '問題')
        self.assertIsNone(fwlib.find(state, 'nope'))

    def test_merge_reorders_and_keeps_progress(self):
        state = fwlib.new_state('2026-10-01')
        fwlib.apply_event(state, '今', 'known', '2026-10-01')
        lexicon = fwlib.load_lexicon(fwlib.LEXICON)
        moved = [lexicon[100], lexicon[0]] + [w for i, w in enumerate(lexicon) if i not in (0, 100)]
        fwlib.merge_lexicon(state, moved)
        self.assertEqual([w['ja'] for w in state['words'][:2]], [lexicon[100]['ja'], lexicon[0]['ja']])
        self.assertEqual(fwlib.find(state, '今')['status'], 'known')
        self.assertEqual(len(state['words']), len(lexicon))


class Brief(Home):
    seen = {'でも': {'n': 40, 'days': [f'2026-10-0{i}' for i in range(1, 8)]},
            '中': {'n': 400, 'days': ['2026-10-08', '2026-10-09', '2026-10-10']},
            '最悪': {'n': 0, 'days': []}}

    def state(self, stage=2):
        words = [word('でも'), word('中'), word('最悪'), word('一緒に', 'active'), word('結局', 'shaky'), word('何', 'queue')]
        return {'stage': stage, 'stage_pinned': True, 'paused': False, 'words': words}

    def test_known_words_by_name_in_list_order_connectors_as_swaps(self):
        words = [word('犬'), word('イタリア'), word('二'), word('四つ'), word('〜時'), word('最悪'), word('中'), word('もっと'),
                 word('でも')]
        words[4]['en'] = 'hour / o\'clock'
        named = weave.named_words(words)  # the base list's order; Anki words (犬) after it; no numbers or katakana
        self.assertEqual([w['ja'] for w in named], ['でも', '中', 'もっと', '最悪', '犬'])
        self.assertEqual([w['ja'] for w in weave.default_swaps(named)], ['でも', 'もっと'])  # tier A only
        many = fwlib.new_state('2026-10-01')['words'][:400]
        swaps = weave.default_swaps(weave.named_words(many))
        self.assertEqual(len(swaps), weave.DEFAULT_SWAPS)
        self.assertEqual([w['ja'] for w in swaps[:6]], ['でも', 'もう', '今', '前', 'そして', 'だから'])

    def test_brief_text(self):
        lines = weave.brief(self.state(), self.seen).split('\n')
        self.assertNotIn('%', lines[0])  # a lookup budget, not a share of the words
        self.assertIn('Known words cost the user nothing', lines[0])
        # normal's 0.5: 0.09 for whole sentences (intensity.SENTENCE_SHARE), 0.41 for learning words at 0.27 each
        self.assertIn('about 1.5 learning words per 100 words (5 in a 300-word reply), at most one in a sentence',
                      lines[0])
        self.assertIn('about 1 whole sentence per 100 words (3 in a 300-word reply) fully in Japanese', lines[0])
        self.assertIn('never conjugated', lines[0])
        self.assertIn('No other Japanese anywhere, not even a common word', lines[0])
        self.assertIn('never in files, code', lines[0])
        # no evidence yet: learning words at the neutral prior (about 1 in 4), known words free
        self.assertEqual(lines[1], 'Learning now, by how often the user looks one up: often, about 1 in 4: '
                                   '結局 = meaning, "hint"; 一緒に = meaning, "hint"')
        self.assertEqual(lines[2], 'Default swaps (write the Japanese every time the English word has that meaning): '
                                   'meaning → でも')
        self.assertEqual(lines[3], 'Known, besides the default swaps (each Japanese word, then its meaning; write the '
                                   'Japanese every time the English has that meaning): 中 meaning; 最悪 meaning')
        self.assertEqual(len(lines), 4)
        self.assertNotIn('何', '\n'.join(lines[1:]))  # queued words are not in use yet
        three = weave.brief(self.state(3), self.seen)
        self.assertIn('about 1 whole sentence per 100 words (3 in a 300-word reply) fully in Japanese', three)
        self.assertIn('sentences of known words, particles and これ/それ only', three)
        self.assertIn('No other Japanese anywhere, not even a common word', three)
        self.assertNotIn('never conjugated', three)
        self.assertNotIn('"hint"', three)
        self.assertNotIn('%', three)
        # the stage no longer sets how many sentences: the budget does, at every stage
        for stage in (1, 4):
            self.assertIn('about 1 whole sentence per 100 words', weave.brief(self.state(stage), self.seen))

    def test_no_recall_cue_and_turns_still_count(self):
        state = self.state()
        fwlib.write_json(fwlib.home() / 'sessions.json', {'s1': {'n': 3, 'recalled': ['結局']}})  # from before it went
        build = lambda had: ('brief', {'known': [], 'rules': ''}, {})
        self.assertEqual([weave.chat_turn(state, s, build)[0] for s in ('s1', 's2', 's2')], ['brief'] * 3)
        sessions = fwlib.read_json(fwlib.home() / 'sessions.json')
        self.assertEqual((sessions['s1']['n'], sessions['s2']['n'], 'recalled' in sessions['s1']), (4, 2, False))
        self.assertNotIn('Recall', weave.brief(state, {}))

    def test_calibration(self):
        words = [word('でも'), word('だから'), word('今'), word('簡単', 'active'), word('問題', 'active'), word('もっと')]
        state = {'stage': 2, 'stage_pinned': True, 'words': words}
        english = 'The build passes and the cache is warm, so nothing else needs doing before the release goes out today. ' * 3
        self.assertEqual(weave.share(english, words), (0, 57, 0, 0))
        woven = 'The build passes, でも the cache is warm, だから nothing else needs 問題, 今 もっと 簡単 things wait. ' * 3
        self.assertEqual(weave.share(woven + '```でも だから```\n(問題 = problem, "mondai")\nRecall: でも?', words), (18, 54, 6, 0))
        self.assertIn('under budget, so use more learning words', weave.calibration(english, state))
        # 6 learning words at the neutral prior's 0.27 in 54 words: about 3 lookups per 100 words, budget 0.5
        line = weave.calibration(woven, state)
        self.assertIn('had 18 Japanese words in 54 (12 known, 6 learning, 0 off the lists) and 0 whole Japanese '
                      'sentences: about 2.99 expected lookups per 100 words against a budget of 0.5 (known words are '
                      'free, a whole sentence costs 0.08): over budget, so use fewer learning words this time.', line)
        self.assertEqual(weave.calibration('Done, でも pushed.', state), '')
        japanese = ('README.md にセクションを追加しました。空のエクスポートは行を削除しますが、テーブルは残ります。'
                    '他の問題は sync.py にあるので、直すかどうか教えてください。') * 2
        self.assertIn('so this time use no Japanese outside the lists', weave.calibration(japanese, state))
        state3 = dict(state, stage=3)
        self.assertIn('outside the lists', weave.calibration(japanese, state3))  # off-list words at any stage
        listed = ('The cache was the cause. 問題は簡単です。 The rest of the build still runs the way it did before, '
                  'and nothing else in the release needs a change from you today. ') * 2
        # particles and です in a whole sentence, at any stage; a whole sentence costs its price: (4 * 0.27 + 2 * 0.08)
        # lookups in 65 words, past both parts of the budget
        for each in (state3, state):
            self.assertIn('(0 known, 4 learning, 0 off the lists) and 2 whole Japanese sentences: about 1.90 expected '
                          'lookups per 100 words against a budget of 0.5 (known words are free, a whole sentence costs '
                          '0.08): over budget, so use fewer learning words and whole Japanese sentences this time.',
                          weave.calibration(listed, each))
        grammar = listed.replace('問題は簡単です。', 'The 問題は簡単です now.')
        self.assertIn('outside the lists', weave.calibration(grammar, state))  # no grammar in English below Stage 3
        self.assertNotIn('outside the lists', weave.calibration(grammar, state3))
        # a challenge of 3: sentences may take 0.24 (3 per 100 words at most), learning words 1.35 (5 at 0.27); two
        # sentences of a known word, 0.16 in 62 words, spend the sentences' part, so the line asks for learning words
        line = weave.calibration(listed.replace('問題は簡単です。', '今です。'), dict(state3, prefs={'challenge': 3}))
        self.assertIn('(2 known, 0 learning, 0 off the lists) and 2 whole Japanese sentences: about 0.26 expected', line)
        self.assertTrue(line.endswith('under budget, so use more learning words this time unless the topic needs '
                                      'zero Japanese or the user asked for none.'), line)
        overlap = [word('一緒'), word('一緒に'), word('今'), word('今日')]
        self.assertEqual(weave.share('Let us go 一緒に, 今日 then 一緒 it. ' * 6, overlap)[0], 18)  # 3 per sentence

    def test_the_feedback_line_ignores_code(self):
        state = {'stage': 2, 'stage_pinned': True, 'words': [word('でも'), word('簡単', 'active')]}
        filler = 'The build passes and the cache is warm so nothing else needs doing before the release goes out. ' * 5
        quoted = ['\n```json\n{"ja": "設定"}\n```\n', 'The row is `設定,せってい` now.', '\n~~~\n設定 問題\n~~~\n',
                  '\n````md\n```\n設定\n```\n````\n', 'Use ``設定`s`` here.', '\n  ```\n  設定\n  ```\n',
                  'The fix: ```設定 = 1``` there.', '\n```\n設定 問題 状況\n']  # the last one never closed
        for code in quoted:  # Japanese quoted as data is not woven
            self.assertIn('(0 known, 0 learning, 0 off the lists)', weave.calibration(filler + code, state), code)
        self.assertIn('(1 known, 1 learning, 1 off the lists)',
                      weave.calibration(filler + 'Then `設定` and 設定 and でも and 簡単 stay. ``x`` ~~~', state))
        self.assertEqual(fwlib.strip_code('a ``b` c`` d ``e'), 'a   d ``e')  # same-length runs pair; a lone one is text

    def test_last_reply(self):
        log = self.home / 'chat.jsonl'
        log.write_text('\n'.join([line('old reply, 今 more'), line('sub', sidechain=True),
                                  json.dumps({'type': 'user', 'message': {'content': 'next'}}),
                                  line('latest reply, でも done'), line('sub2', sidechain=True)]) + '\n')
        self.assertEqual(weave.last_reply(log), 'latest reply, でも done')
        with mock.patch.object(weave, 'TAIL', len(log.read_bytes()) // 2):
            self.assertEqual(weave.last_reply(log), 'latest reply, でも done')

    def run_hook(self, payload, **env):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'weave.py'), '--hook'], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=30, env=dict(os.environ, **env))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_hook_output_and_pause(self):
        out = json.loads(self.run_hook({'session_id': 'x', 'transcript_path': str(self.home / 'missing.jsonl')}))
        context = out['hookSpecificOutput']['additionalContext']
        self.assertEqual(out['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        self.assertIn('Japanese Weave (Stage 1)', context)
        self.assertNotIn('Recall', context)
        self.assertLess(len(context), 3200)
        fw.main(['pause'])
        self.assertEqual(self.run_hook({'session_id': 'x'}), '')
        fw.main(['resume'])
        self.assertIn('Japanese Weave', self.run_hook({}))

    def test_no_japanese_in_scripted_runs(self):
        self.assertIn('Japanese Weave', self.run_hook({}))  # no claude parent here: weave by default
        self.assertEqual(self.run_hook({}, JAPANESE_WEAVE_OFF='1'), '')
        # Agent SDK scripts stay silent, except under T3 Code (which these tests may themselves run under).
        self.assertEqual(self.run_hook({}, CLAUDE_CODE_ENTRYPOINT='sdk-py') != '', weave.t3_host())
        self.assertIn('Japanese Weave', self.run_hook({}, CLAUDE_CODE_ENTRYPOINT='sdk-cli', CLAUDE_CODE_SESSION_ATTENDED='0'))
        one_shot = ['claude', '-p', '--model', 'haiku', 'Reply OK']
        remote = ['claude', '--print', '--sdk-url', 'https://example.invalid/v1/code/sessions/x', '--session-id', 'x',
                  '--input-format', 'stream-json', '--output-format', 'stream-json']
        self.assertTrue(weave.one_shot(one_shot))
        self.assertTrue(weave.is_claude(['/home/u/.local/share/claude/versions/2.1.296', '--print']))
        self.assertTrue(weave.is_claude(['claude', '-p']) and not weave.is_claude(['/bin/sh', '-c', 'x']))
        self.assertTrue(weave.one_shot(['claude', '--print', '--output-format', 'json', 'x']))
        self.assertFalse(weave.one_shot(remote))
        self.assertFalse(weave.one_shot(['claude', '--print', '--input-format=stream-json']))
        self.assertFalse(weave.one_shot(['claude', '--model', 'opus']))
        self.assertFalse(weave.interactive({}, args_check=lambda: one_shot))
        self.assertTrue(weave.interactive({}, args_check=lambda: remote))
        self.assertTrue(weave.interactive({'CLAUDE_CODE_ENTRYPOINT': 'sdk-cli'}, args_check=lambda: remote))
        self.assertTrue(weave.interactive({'JAPANESE_WEAVE_FORCE': '1'}, args_check=lambda: one_shot))
        self.assertFalse(weave.interactive({'CLAUDE_CODE_ENTRYPOINT': 'sdk-ts'}, args_check=lambda: [], host_check=lambda: False))
        self.assertTrue(weave.interactive({'CLAUDE_CODE_ENTRYPOINT': 'sdk-ts'}, args_check=lambda: [], host_check=lambda: True))

    def test_t3_code_threads_weave(self):
        inner = f'import sys, os; sys.path.insert(0, {str(ROOT / "scripts")!r}); import weave; print(weave.t3_host(os.getpid()))'
        outer = f'import subprocess; subprocess.run(["claude", "-c", {inner!r}], executable={sys.executable!r})'
        t3 = subprocess.run(['t3', '-c', outer, 'serve'], executable=sys.executable, capture_output=True, text=True)
        self.assertEqual(t3.stdout.strip(), 'True', t3.stderr)  # claude started by `t3 serve`
        tree = {30: (['/bin/sh', '-c', 'hook'], 20), 20: (['claude', '--input-format', 'stream-json'], 10),
                10: (['/nix/store/x-t3-cli/lib/t3/t3', 'serve', '--port', '3773'], 1)}
        self.assertTrue(weave.t3_host(30, lookup=lambda pid: tree.get(pid, ([], 0))))
        app = '/nix/store/x-t3code/T3 Code'  # the desktop app runs its server on its own Electron binary
        tree[10] = ([f'{app}/t3code', f'{app}/resources/app.asar/apps/server/dist/bin.mjs', '--bootstrap-fd', '3'], 1)
        self.assertTrue(weave.t3_host(30, lookup=lambda pid: tree.get(pid, ([], 0))))
        tree[10] = ([f'{app}/t3code'], 1)  # the app's window process never starts claude itself
        self.assertFalse(weave.t3_host(30, lookup=lambda pid: tree.get(pid, ([], 0))))
        tree[10] = (['node', 'agent-script.mjs'], 5)  # an SDK script, even one run from a T3 terminal
        tree[5] = (['/nix/store/x-t3-cli/lib/t3/t3', 'serve'], 1)
        self.assertFalse(weave.t3_host(30, lookup=lambda pid: tree.get(pid, ([], 0))))

    def test_t3_glosses_in_abbr_popovers(self):
        state = fwlib.new_state('2026-10-01')
        self.assertFalse(weave.abbr_glosses(state, host_check=lambda: True))  # off until the user opts in
        state['prefs'] = {'chat_glosses': 'abbr'}
        self.assertTrue(weave.abbr_glosses(state, host_check=lambda: True))
        self.assertFalse(weave.abbr_glosses(state, host_check=lambda: False))  # a terminal would show raw tags
        footer, popover = weave.brief(state, {}), weave.brief(state, {}, markup=True)
        self.assertIn('footer line', footer)
        self.assertNotIn('<abbr', footer)
        self.assertIn('<abbr title="hiragana reading · meaning">', popover)
        self.assertNotIn('footer line', popover)
        with mock.patch('sys.stdout', new=io.StringIO()):
            fw.main(['glosses', 'abbr'])
        self.assertEqual(fwlib.load_state()['prefs']['chat_glosses'], 'abbr')

    def test_share_counts_abbr_glossed_words_once(self):
        words = [word('でも'), word('問題', 'active')]
        plain = 'The build passes, でも the cache has a 問題 that needs a closer look before we ship it today. ' * 3
        glossed = plain.replace('でも', '<abbr title="demo · but / however">でも</abbr>').replace(
            '問題', '<abbr title="mondai · problem">問題</abbr>')
        self.assertEqual(weave.share(glossed, words), weave.share(plain, words))


def grown(known, seed=1):
    """A state with about `known` known words, mostly in rank order, a few gaps and a few beyond."""
    import random
    rng = random.Random(seed)
    state = fwlib.new_state('2026-01-01')
    words = state['words']
    chosen = set(range(known))
    for i in rng.sample(sorted(chosen), known // 20):
        chosen.discard(i)
    chosen.update(range(known, min(len(words), known + known // 20)))
    for i in chosen:
        fwlib.apply_event(state, words[i]['ja'], 'known', '2026-01-01')
    for day in range(2, 7):  # up to 20 learning words, 5 a day
        room = min(fwlib.NEW_PER_DAY, 20 - sum(w['status'] == 'active' for w in words))
        fwlib.take_in(state, [[w['ja'], None, None] for w in fwlib.newcomers(state, {})[:room]], f'2026-01-0{day}')
    return state


class Scale(Home):
    def test_known_band(self):
        state = grown(400)
        n, missing, extra = fwlib.known_band(state)
        known = {w['ja'] for w in state['words'] if w['status'] == 'known'}
        band = {w['ja'] for w in state['words'][:n]}
        self.assertEqual(known, (band - set(missing) - {w['ja'] for w in state['words'][:n] if w['status'] in ('active', 'shaky')}) | set(extra))

    def test_brief_and_exports_stay_small(self):
        later = {}
        for known in (50, 400, 1000):
            state = grown(known)
            first, had, _ = weave.compose(state, {}, tail='x' * 300)
            about, respond = export.chatgpt(state)
            self.assertLessEqual(len(about), export.CHATGPT_LIMIT)
            self.assertLessEqual(len(respond), export.CHATGPT_LIMIT)
            self.assertLess(len(export.claude_ai(state)), 6000)
            self.assertLessEqual(len(first) + 300, weave.BRIEF_MAX)  # Claude Code files away hook context over 10,000
            self.assertNotIn('most common', first)
            later[known] = weave.compose(state, {}, had=had)[0]
            if known >= 400:  # the most useful named on the first turn, the rest never: a later turn has no list
                self.assertIn('more the user knows, not named here: keep the English for any word not named', first)
                self.assertNotIn('also known', later[known])
                self.assertLess(len(later[known]), 2400, known)
                self.assertIn('Known: as listed earlier in this chat. If you can no longer see that list', later[known])

    def test_the_first_turn_fits_claude_codes_limit_at_1500_known_words(self):
        """Claude Code hands Claude 10,000 characters of a hook's text and files away the rest, so a chat's first turn
        stays under 9,500 at any number of known words: the known list names the most useful ones (weave.priority) in
        KNOWN_ROOM characters. Measured through the hook on the 2026-10-20 state with <abbr> glosses and the feedback
        line: 8,128 characters at 293 known words (all 255 named), 8,737 at 500 (285 named) and 8,717 at 1,500 (255
        named)."""
        state = fwlib.new_state('2026-01-01')
        lexicon = state['words']
        anki = [dict(word(f'語{i}'), en='it is i who should say so / i feel the same way') for i in range(30)]
        for i, w in enumerate(lexicon):  # 24 learning words, then every other base-list word known
            w.update(status='active' if i < 24 else 'known', added='2026-01-01')
        state['words'] = anki + lexicon  # an Anki sync puts its words first
        known = [w for w in state['words'] if w['status'] == 'known']
        self.assertEqual(len(known), 1506)
        for w in known[-6:]:
            w['status'] = 'queue'
        self.assertEqual(fwlib.stage_for(state), 4)
        rare = known[-10]  # a word far down the base list that keeps coming up in the user's chats
        seen = {rare['ja']: {'n': 5, 'days': ['2026-10-18', '2026-10-19', '2026-10-20']}}
        tail = '\nYour last reply in this chat had ' + 'x' * 420  # about the longest feedback line
        for markup in (False, True):
            for level in (0, -2, 2, intensity.FULL):
                first, _, plan = weave.compose(state, seen, markup=markup, level=level, tail=tail)
                self.assertLess(len(first) + len(tail), 9500, (markup, level))
                self.assertEqual(plan['known'], 1500)
                lines = first.split('\n')
                known_line = next(line for line in lines if line.startswith('Known, besides'))
                named = known_line.split('has that meaning): ', 1)[1].split('; ')
                left = int(named.pop().split(' ')[1].replace(',', ''))
                self.assertEqual(named[0], weave.entry(rare))  # it came up in chat: named first
                self.assertFalse(any(e.startswith('語') for e in named))  # Anki words, odd meanings: last, not named
                self.assertGreater(len(named), 240)
                self.assertEqual(len(named) + left + weave.DEFAULT_SWAPS, len(weave.named_words(
                    [w for w in state['words'] if w['status'] == 'known'])))
        # unnamed known words are still known: a reply that uses one is on budget, not off the lists
        unnamed = next(w for w in reversed(lexicon) if w['status'] == 'known' and not weave.NUMBER.match(w['ja'])
                       and not weave.KATAKANA.match(w['ja']) and w['ja'] not in first)
        reply = 'The build passes and the cache is warm so nothing else needs doing before the release goes out. ' * 4
        self.assertIn('(1 known, 0 learning, 0 off the lists)', weave.calibration(reply + unnamed['ja'], state))

    def test_exports_describe_known_words_in_the_base_lists_order(self):
        """An Anki sync puts its words first in the state; the exports' band still counts ranks in the base list, so
        "up to about the N-th most frequent" means the most frequent words, and the Anki words come after it."""
        state = grown(400)
        band = export.band_text(state)
        anki = [dict(word(f'語{i}'), en='card meaning') for i in range(40)]
        moved = [w for w in state['words'] if w['status'] == 'known'][100:200]  # reviewed on cards, so moved up too
        state['words'] = anki + moved[::-1] + [w for w in state['words'] if w not in moved]
        self.assertNotEqual(fwlib.known_band(state)[0], fwlib.known_band(grown(400))[0])  # the state's order misleads
        n, missing, extra = fwlib.known_band({'words': export.by_lexicon(state)})
        self.assertEqual(n, fwlib.known_band(grown(400))[0])
        self.assertEqual(extra[-40:], [w['ja'] for w in anki])  # beyond the band, after the base list's words
        self.assertTrue(export.band_text(state).startswith(band.split('; also: ')[0]))
        everyone = export.band_text(state, extras=len(extra))
        self.assertTrue(everyone.endswith(', '.join(extra)))
        block = export.claude_ai(state, now=0)
        self.assertLessEqual(len(block), export.CLAUDE_AI_MAX)
        self.assertIn(f'up to about the {export.ordinal(n)} most frequent', block)
        self.assertIn('語0', block)  # as many past the band named as fit
        about, respond = export.chatgpt(state, now=0)
        self.assertLessEqual(max(len(about), len(respond)), export.CHATGPT_LIMIT)
        self.assertIn(f'; also: {extra[0]}, ', about)
        few = fwlib.new_state('2026-01-01')  # under KNOWN_LIST_MAX, named one by one: the base list's order too
        for w in few['words'][:20] + few['words'][40:50]:
            w.update(status='known', added='2026-01-01')
        few['words'] = few['words'][40:50] + anki[:3] + few['words'][:40] + few['words'][50:]
        listed = export.state_line(few, now=0).split('Known (use freely; never gloss): ')[1].split(' | ')[0]
        self.assertEqual(listed.split(', '), [w['ja'] for w in export.by_lexicon(few) if w['status'] == 'known'])
        trimmed = export.state_line(few, now=0, known_max=10).split('never gloss): ')[1].split(' | ')[0]
        self.assertTrue(trimmed.endswith(' and 23 more not named here'), trimmed)  # not the most common: unnamed

    def test_exports_describe_known_words_in_the_base_lists_order(self):
        """An Anki sync puts its words first in the state; the exports' band still counts ranks in the base list, so
        "up to about the N-th most frequent" means the most frequent words, and the Anki words come after it."""
        state = grown(400)
        band = export.band_text(state)
        anki = [dict(word(f'語{i}'), en='card meaning') for i in range(40)]
        moved = [w for w in state['words'] if w['status'] == 'known'][100:200]  # reviewed on cards, so moved up too
        state['words'] = anki + moved[::-1] + [w for w in state['words'] if w not in moved]
        self.assertNotEqual(fwlib.known_band(state)[0], fwlib.known_band(grown(400))[0])  # the state's order misleads
        n, missing, extra = fwlib.known_band({'words': export.by_lexicon(state)})
        self.assertEqual(n, fwlib.known_band(grown(400))[0])
        self.assertEqual(extra[-40:], [w['ja'] for w in anki])  # beyond the band, after the base list's words
        self.assertTrue(export.band_text(state).startswith(band.split('; also: ')[0]))
        everyone = export.band_text(state, extras=len(extra))
        self.assertTrue(everyone.endswith(', '.join(extra)))
        block = export.claude_ai(state, now=0)
        self.assertLessEqual(len(block), export.CLAUDE_AI_MAX)
        self.assertIn(f'up to about the {export.ordinal(n)} most frequent', block)
        self.assertIn('語0', block)  # as many past the band named as fit
        about, respond = export.chatgpt(state, now=0)
        self.assertLessEqual(max(len(about), len(respond)), export.CHATGPT_LIMIT)
        self.assertIn(f'; also: {extra[0]}, ', about)
        few = fwlib.new_state('2026-01-01')  # under KNOWN_LIST_MAX, named one by one: the base list's order too
        for w in few['words'][:20] + few['words'][40:50]:
            w.update(status='known', added='2026-01-01')
        few['words'] = few['words'][40:50] + anki[:3] + few['words'][:40] + few['words'][50:]
        listed = export.state_line(few, now=0).split('Known (use freely; never gloss): ')[1].split(' | ')[0]
        self.assertEqual(listed.split(', '), [w['ja'] for w in export.by_lexicon(few) if w['status'] == 'known'])
        trimmed = export.state_line(few, now=0, known_max=10).split('never gloss): ')[1].split(' | ')[0]
        self.assertTrue(trimmed.endswith(' and 23 more not named here'), trimmed)  # not the most common: unnamed

    def test_state_line_round_trips(self):
        for known in (30, 400):
            state = grown(known)
            line = export.state_line(state, now=0)
            parsed = export.parse_state_line(line)
            self.assertEqual(parsed['stage'], fwlib.stage_for(state))
            self.assertEqual(parsed['synced'], '1970-01-01T00:00Z')
            active = sorted((w for w in state['words'] if w['status'] == 'active'), key=lambda w: w['added'])
            self.assertEqual(parsed['active'], [(w['ja'], w['en'], w['hint']) for w in active])
            shaky = [w for w in state['words'] if w['status'] == 'shaky']
            self.assertEqual(len(parsed['shaky']), len(shaky))
            if known < export.KNOWN_LIST_MAX:
                self.assertEqual(parsed['known'], [w['ja'] for w in state['words'] if w['status'] == 'known'])
            else:
                n = fwlib.known_band(state)[0]
                self.assertIn(f'up to about the {export.ordinal(n)} most frequent', parsed['known'])

    def test_export_notice_once_after_a_week(self):
        state = fwlib.load_state()
        with fwlib.editing() as state:
            export.mark_exported(state, 'claude-ai')
        self.assertIsNone(weave.export_notice(state))  # same version
        with fwlib.editing() as state:
            fwlib.apply_event(state, 'でも', 'known')
        self.assertIsNone(weave.export_notice(state, now=time.time() + 86400))  # changed, but fresh
        self.assertIn('/japanese-weave:export', weave.export_notice(state, now=time.time() + 8 * 86400))
        self.assertIsNone(weave.export_notice(fwlib.load_state(), now=time.time() + 9 * 86400))  # only once

    def test_day_count_ops_from_before_change_nothing(self):
        state = fwlib.new_state('2026-10-01')
        before = json.dumps(state, sort_keys=True)
        self.assertEqual(hub.apply(state, hub.stamp({'kind': 'chat_day'})), {'logged': 0})
        self.assertEqual(json.dumps(state, sort_keys=True), before)
        self.assertEqual(hub.apply(state, hub.stamp({'kind': 'exported', 'target': 'chatgpt', 'log_day': True})),
                         {'logged': 0})
        self.assertEqual(state['exported']['target'], 'chatgpt')


class Blocks(unittest.TestCase):
    def test_blocks_are_current_and_fit(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'make_blocks.py'), '--check'],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class Codex(Home):
    def test_merge_keeps_other_hooks_and_is_idempotent(self):
        theirs = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'x.sh'}]}],
                            'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'other.py'}]}]}}
        once = fw.merge_codex_hooks(json.loads(json.dumps(theirs)), fw.codex_command())
        twice = fw.merge_codex_hooks(json.loads(json.dumps(once)), fw.codex_command())
        self.assertEqual(once, twice)
        self.assertEqual(once['hooks']['Stop'], theirs['hooks']['Stop'])
        self.assertEqual(len(once['hooks']['UserPromptSubmit']), 2)
        self.assertEqual(once['hooks']['UserPromptSubmit'][0], theirs['hooks']['UserPromptSubmit'][0])

    def test_feature_flag(self):
        self.assertEqual(fw.enable_codex_hooks(''), '[features]\nhooks = true\n')
        self.assertEqual(fw.enable_codex_hooks('model = "x"\n[features]\nhooks = false\nother = 1\n[tui]\n'),
                         'model = "x"\n[features]\nhooks = true\nother = 1\n[tui]\n')
        self.assertEqual(fw.enable_codex_hooks('[features]\nmemories = true\n'), '[features]\nhooks = true\nmemories = true\n')
        self.assertIn('[features]\nhooks = true', fw.enable_codex_hooks('model = "x"\n[tui]\na = 1\n'))

    def test_codex_exec_is_scripted_tui_is_not(self):
        data = {'turn_id': 't', 'session_id': 's'}
        self.assertFalse(weave.interactive({}, data, exec_check=lambda: True))
        self.assertTrue(weave.interactive({}, data, exec_check=lambda: False))
        self.assertTrue(weave.interactive({'JAPANESE_WEAVE_FORCE': '1'}, data, exec_check=lambda: True))
        code = f'import sys, os; sys.path.insert(0, {str(ROOT / "scripts")!r}); import weave; print(weave.codex_exec(os.getpid()))'
        sh = subprocess.run(['codex', '-c', code, 'exec'], executable=sys.executable, capture_output=True, text=True)
        self.assertEqual(sh.stdout.strip(), 'True', sh.stderr)

    def test_codex_transcript_replies(self):
        lines = [json.dumps({'type': 'response_item', 'timestamp': '2026-10-09T10:00:00Z',
                             'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Fixed, だから it ships.'}]}}),
                 json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '今'}]}})]
        self.assertEqual([t for _, t in fwlib.replies(lines)], ['Fixed, だから it ships.'])


class Host(Home):
    def converse(self, *messages):
        raw = b''.join(struct.pack('=I', len(m)) + m for m in (json.dumps(x).encode() for x in messages))
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'fw_host.py')], input=raw, capture_output=True,
                                timeout=30, env=dict(os.environ))
        out, replies = result.stdout, []
        while out:
            size = struct.unpack('=I', out[:4])[0]
            replies.append(json.loads(out[4:4 + size]))
            out = out[4 + size:]
        return replies

    def test_state_carries_the_key_files(self):
        home = self.home / 'fw'
        home.mkdir(parents=True, exist_ok=True)
        (home / 'gemini_key').write_text('g-key\n')
        (home / 'anthropic_key').write_text(' a-key \n')
        state, = self.converse({'type': 'state'})
        self.assertEqual((state['geminiKey'], state['anthropicKey']), ('g-key', 'a-key'))

    def test_protocol(self):
        ping, state, events, after, bad = self.converse(
            {'type': 'ping'}, {'type': 'state'},
            {'type': 'events', 'events': [{'word': 'もう', 'kind': 'flip'}, {'word': 'でも', 'kind': 'seen'},
                                          {'word': 'zzz', 'kind': 'seen'}, {'word': 'でも', 'kind': 'typed'}]},
            {'type': 'state'}, {'type': 'nope'})
        self.assertEqual(ping, {'ok': True, 'host': 1})
        self.assertEqual(state['stage'], 1)
        self.assertEqual(len(state['words']), fwlib.NEW_PER_DAY)
        self.assertNotIn('geminiKey', state)
        self.assertNotIn('anthropicKey', state)
        self.assertEqual(state['prefs'], {})
        self.assertEqual(events, {'ok': True, 'applied': 2})
        mou = next(w for w in after['words'] if w['ja'] == 'もう')  # a flip restarts a learning word's count
        self.assertEqual({k: mou[k] for k in ('ja', 'en', 'hint', 'kana', 'status')},
                         {'ja': 'もう', 'en': 'already / anymore', 'hint': 'mō', 'kana': 'もう', 'status': 'active'})
        self.assertEqual(after['version'], state['version'])  # no status changed: the extension keeps its cache
        self.assertFalse(bad['ok'])

    def test_state_reply_carries_prefs(self):
        with fwlib.editing() as state:
            state['prefs'] = {'extension_sites': 'ask'}
        reply, = self.converse({'type': 'state'})
        self.assertEqual(reply['prefs'], {'extension_sites': 'ask'})

    def test_message_framing(self):
        stream = io.BytesIO()
        fw_host.write_message(stream, {'a': 'é'})
        stream.seek(0)
        self.assertEqual(fw_host.read_message(stream), {'a': 'é'})
        self.assertIsNone(fw_host.read_message(io.BytesIO(b'')))


FAKE_KEY = 'AIzaTEST_placeholder_not_a_real_key_0000'


class Feedback(Home):
    def test_log_stores_gloss_interactions(self):
        now = int(time.time() * 1000)
        item = {'kind': 'peek', 'word': '問題', 'at': now - 5000, 'dwellMs': 1200, 'view': 'ab12', 'context': 'c0ffee',
                'position': 1, 'box': [10, 400, 600, 80], 'annotations': [['今', 12, 0], ['問題', 140, 0], ['でも', 300, 20]]}
        bad = [dict(item, kind='seen'), dict(item, annotations=[['今', 'left', 0]]), dict(item, at=now + 3_600_000),
               dict(item, view='has spaces'), 'not an item']
        reply = fw_host.handle({'type': 'log', 'items': [item, dict(item, kind='flip', dwellMs=0)] + bad})
        self.assertEqual(reply, {'ok': True, 'stored': 2, 'duplicates': 0, 'rejected': 5})
        self.assertFalse(fw_host.handle({'type': 'log', 'items': 'nope'})['ok'])
        rows = feedback.recent()
        self.assertEqual([(r['kind'], r['word'], r['position'], r['dwellMs']) for r in rows],
                         [('flip', '問題', 1, 0), ('peek', '問題', 1, 1200)])
        self.assertEqual(rows[1]['annotations'], [['今', 12, 0], ['問題', 140, 0], ['でも', 300, 20]])
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['log'])
        self.assertIn('peek 問題  open 1.2s, 2 other glossed words in the passage', out.getvalue())


class FeedbackStore(Home):
    def item(self, **extra):
        return dict({'kind': 'peek', 'word': '問題', 'at': int(time.time() * 1000) - 1000, 'dwellMs': 900, 'view': 'v1',
                     'context': 'c1', 'position': 0, 'box': [0, 0, 500, 40], 'annotations': [['問題', 4, 0]]}, **extra)

    def test_resends_are_stored_once_and_shown_records_describe_contexts(self):
        first = feedback.record([self.item(uid='u1'), self.item(uid='u2')], 'browser')
        again = feedback.record([self.item(uid='u1')], 'browser')  # the reply was lost, the extension resent it
        self.assertEqual((first['stored'], again['stored'], again['duplicates']), (2, 0, 1))
        shown = dict(self.item(), kind='shown', words=48)
        del shown['word'], shown['position'], shown['dwellMs']
        self.assertEqual(feedback.record([shown, dict(shown, words=0)], 'browser'), {'stored': 1, 'duplicates': 0, 'rejected': 1})
        week_old = self.item(at=int(time.time() * 1000) - 6 * feedback.DAY_MS)  # an offline laptop's queue
        self.assertEqual(feedback.record([week_old], 'browser')['stored'], 1)
        rows, contexts = feedback.load()
        self.assertEqual(len(rows), 3)
        self.assertEqual((contexts[('browser', 'v1', 'c1')]['words'], bool(contexts[('browser', 'v1', 'c1')]['shown_at'])), (48, True))

    def test_shown_records_keep_their_plan_and_contexts_their_time_on_screen(self):
        shown = dict(self.item(), kind='shown', words=48, plan={'level': -1, 'intensity': 0.7, 'jitter': 0.62,
                                                                  'limit': 3, 'candidates': 5})
        del shown['word'], shown['position'], shown['dwellMs']
        bad = [dict(shown, context='c2', plan={'level': 4, 'intensity': 1, 'jitter': 1, 'limit': 1, 'candidates': 1}),
               dict(shown, context='c3', plan={'level': 0, 'intensity': 1, 'jitter': 1.5, 'limit': 1, 'candidates': 1}),
               dict(shown, context='c4', plan={'level': 0, 'intensity': 1, 'jitter': 1, 'limit': 1, 'candidates': 1,
                                               'text': 'page text'})]
        self.assertEqual(feedback.record([shown] + bad, 'browser'), {'stored': 1, 'duplicates': 0, 'rejected': 3})
        # Since levels meant a lookup budget (levels.js planSwaps): the passage's budget and the lookups expected, both
        # optional (full has no budget, older plans neither), as hostsource.js cleanPlan checks them.
        num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
        new = dict(shown['plan'], budget=0.186, expected=0.41)
        full = {'level': 3, 'intensity': 5, 'jitter': 1, 'limit': 9, 'candidates': 9, 'expected': 2.5}
        # since whole sentences cost a price (and the thinning no longer scales the budget): the price used
        priced = dict(new, sentencePrice=0.08)
        for plan in (new, full, shown['plan'], dict(new, budget=0, expected=0), priced, dict(full, sentencePrice=0.08)):
            self.assertTrue(feedback._plan_ok(plan, num), plan)
        for plan in (dict(new, budget=-0.1), dict(new, expected=1001), dict(new, budget=float('nan')), dict(new, budget='0.5'),
                     dict(new, expected=None), dict(new, budget=True), dict(priced, sentencePrice=1.5),
                     dict(priced, sentencePrice=-0.1), dict(priced, sentencePrice='0.08')):
            self.assertFalse(feedback._plan_ok(plan, num), plan)
        stays = [dict(self.item(), kind='visible', y=0, height=40, ms=ms, wall=ms + 500, away=500, media=0, idle=0,
                      uid=f'v1-{ms}') for ms in (1500, 2500)]
        feedback.record(stays, 'browser')
        _, contexts = feedback.load()
        c = contexts[('browser', 'v1', 'c1')]
        self.assertEqual((c['plan']['jitter'], c['active']), (0.62, 4000))
        self.assertEqual(c['seen_until'], stays[0]['at'])

    def test_clicks_and_selections_are_kept_by_position_and_counted(self):
        where = {'at': int(time.time() * 1000) - 1000, 'view': 'v1', 'context': 'c2', 'box': [0, 0, 500, 40],
                 'annotations': [['問題', 4, 0], ['今', 90, 0]]}
        click = dict(where, kind='click', x=30.4, y=8, target='link', uid='v1-1')
        select = dict(where, kind='select', x=0, y=0, width=120, height=18, chars=31, covered=[1, 0, 1], ms=640,
                      uid='v1-2', text='page text')
        bad = [dict(click, target='image'), dict(select, covered=[2]), dict(select, chars=0), dict(click, uid='a b'),
               dict(select, ms=-1), dict(click, x=float('nan'))]
        self.assertEqual(feedback.record([click, select, click] + bad, 'browser'),
                         {'stored': 2, 'duplicates': 1, 'rejected': 6})
        feedback.record([self.item(uid='u1'), self.item(kind='flip', dwellMs=0, uid='u2')], 'browser')
        rows, _ = feedback.load()
        self.assertEqual([r['kind'] for r in rows], ['peek', 'flip'])  # the knowledge model reads gloss opens only
        recent = feedback.recent()
        detail = {r['kind']: r.get('detail') for r in recent}
        self.assertEqual((detail['click'], detail['select']), ({'target': 'link'}, {'chars': 31, 'covered': [0, 1]}))
        self.assertEqual({r['kind']: r.get('ms') for r in recent}['select'], 640)
        self.assertNotIn('page text', json.dumps(recent, ensure_ascii=False))
        stats = feedback.stats(since=int(time.time() * 1000) + 60_000)
        self.assertEqual(stats['counts'], {'browser:peek': [1, 0], 'browser:flip': [1, 0], 'browser:click': [1, 0],
                                           'browser:select': [1, 0]})
        self.assertEqual(feedback.stats()['counts']['browser:select'], [1, 1])
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['log'])
        self.assertIn('Collected: 1 peek (1 today), 1 flip (1 today), 1 click (1 today), 1 selection (1 today), '
                      '0 mouse paths (0 today); 0 words shown on pages (0 today); 0 min reading on screen (0 today).',
                      out.getvalue())
        self.assertIn('select 31 characters, 2 of the passage\'s 2 glossed words', out.getvalue())
        self.assertIn('click  on link at 30,8 in a passage with 2 glossed words', out.getvalue())

    def test_on_screen_time_and_mouse_paths_are_kept(self):
        where = {'at': int(time.time() * 1000) - 60_000, 'view': 'v1', 'context': 'c3', 'box': [0, 0, 500, 400],
                 'annotations': [['問題', 4, 0], ['今', 90, 200]]}
        stay = dict(where, kind='visible', y=180, height=60, ms=42_000, wall=50_000, away=5000, media=0, idle=3000)
        trace = dict(where, kind='trace', points=[[0, 10, 4], [120, 60.4, 5], [480, 120, 30]])
        bad = [dict(stay, ms=60_000), dict(stay, idle=-1), dict(stay, wall=4_000_000), dict(trace, points=[]),
               dict(trace, points=[[100, 1, 1], [50, 2, 2]]), dict(trace, points=[[0, 1, 1]] * 201), dict(trace, points=[[0, 1]])]
        found = feedback.record([stay, trace] + bad, 't3')
        self.assertEqual([r['item'] for r in found.pop('reasons')], list(range(2, 9)))
        self.assertEqual(found, {'stored': 2, 'duplicates': 0, 'rejected': 7})
        rows = {r['kind']: r for r in feedback.recent()}
        self.assertEqual((rows['visible']['ms'], rows['visible']['detail']),
                         (42_000, {'wall': 50_000, 'away': 5000, 'media': 0, 'idle': 3000}))
        self.assertEqual((rows['trace']['ms'], rows['trace']['detail']['points'][1]), (480, [120, 60, 5]))
        totals = feedback.stats(since=int(time.time() * 1000) - 3_600_000)
        self.assertEqual((totals['readingMs'], totals['counts']['t3:trace']), ([42_000, 42_000], [1, 1]))
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['log'])
        self.assertIn('on screen 42s of 50s (5s away, 3s idle), a paragraph with 2 glossed words', out.getvalue())
        self.assertIn('mouse path 3 points over 0.5s, a message with 2 glossed words', out.getvalue())
        self.assertIn('1 min reading on screen (1 today)', out.getvalue())

    def test_schema_3_reading_rows_move_to_schema_4(self):
        import sqlite3
        path = self.home / 'v3.sqlite'
        feedback.connect(path).close()
        con = sqlite3.connect(str(path))
        con.executescript("""DROP TABLE reading;
            CREATE TABLE reading (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, surface TEXT NOT NULL, kind TEXT NOT NULL,
              view TEXT NOT NULL, context TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL, width REAL, height REAL,
              detail TEXT NOT NULL, uid TEXT);
            CREATE UNIQUE INDEX reading_uid ON reading (uid);
            INSERT INTO contexts (surface, view, context, annotations) VALUES ('browser', 'v0', 'c0', '[]');
            INSERT INTO reading VALUES (1, 5, 'browser', 'select', 'v0', 'c0', 1, 2, 30, 18,
              '{"chars": 31, "covered": [0], "ms": 640}', 'v0-1');
            PRAGMA user_version = 3;""")
        con.close()
        row = feedback.recent(path=path)[0]
        self.assertEqual((row['kind'], row['ms'], row['detail']), ('select', 640, {'chars': 31, 'covered': [0]}))
        stay = dict(self.item(), kind='visible', y=0, height=40, ms=1500, wall=1500, away=0, media=0, idle=0, uid='v0-1')
        self.assertEqual(feedback.record([stay], 'browser', path)['duplicates'], 1)  # the uid index came along
        con = sqlite3.connect(str(path))
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], feedback.SCHEMA_VERSION)
        self.assertEqual(con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'reading_3'").fetchone()[0], 0)
        con.close()

    def test_schema_1_databases_are_migrated_in_place(self):
        import sqlite3
        path = self.home / 'old.sqlite'
        con = sqlite3.connect(str(path))
        con.executescript("""CREATE TABLE contexts (surface TEXT NOT NULL, view TEXT NOT NULL, context TEXT NOT NULL,
            left REAL, top REAL, width REAL, height REAL, annotations TEXT NOT NULL, PRIMARY KEY (surface, view, context));
            CREATE TABLE interactions (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, surface TEXT NOT NULL, kind TEXT NOT NULL,
            word TEXT NOT NULL, dwell_ms INTEGER NOT NULL, view TEXT NOT NULL, context TEXT NOT NULL, position INTEGER NOT NULL);
            INSERT INTO contexts VALUES ('browser', 'v0', 'c0', 0, 0, 1, 1, '[]');
            INSERT INTO interactions VALUES (1, 5, 'browser', 'peek', 'x', 9, 'v0', 'c0', 0);
            PRAGMA user_version = 1;""")
        con.close()
        self.assertEqual(feedback.record([self.item(uid='u9')], 'browser', path)['stored'], 1)
        rows, contexts = feedback.load(path)
        self.assertEqual([r['uid'] for r in rows], [None, 'u9'])
        self.assertEqual(sorted(c['seq'] for c in contexts.values()), [1, 2])
        click = dict(self.item(), kind='click', x=1, y=2, target='text')
        self.assertEqual(feedback.record([click], 'browser', path)['stored'], 1)  # the reading table came with it


class Collector(Home):
    """A collector on a random local port, as on the always-on machine; this machine's native host forwards to it."""

    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        self.db = self.home / 'collector' / 'interactions.sqlite'
        handler = type('TestHandler', (collector.Handler,), {'db': self.db, 'log_message': lambda *args: None})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def item(self, kind='peek'):
        return {'kind': kind, 'word': '問題', 'at': int(time.time() * 1000) - 1000, 'dwellMs': 800, 'view': 'v1',
                'context': 'm1', 'position': 0, 'box': [0, 0, 500, 40], 'annotations': [['問題', 4, 0], ['今', 90, 0]]}

    def test_collector_stores_json_and_refuses_page_style_posts(self):
        import urllib.request
        self.assertEqual(feedback.forward(self.url, [self.item('pin'), {'kind': 'bad'}], 't3'),
                         {'ok': True, 'stored': 1, 'duplicates': 0, 'rejected': 1,
                          'reasons': [{'item': 1, 'error': 'malformed or too old'}]})
        # A web page can only send a "simple" cross-site POST (text/plain, forms) without a preflight: refused.
        form = urllib.request.Request(self.url + '/v1/interactions', data=b'{"surface": "t3", "items": []}',
                                      headers={'Content-Type': 'text/plain'})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(form, timeout=5)
        self.assertEqual(refused.exception.code, 415)
        refused.exception.close()
        self.assertEqual(feedback.forward(self.url, [self.item()], 'phone')['ok'], False)
        health = feedback.remote_health(self.url)
        self.assertEqual((health['ok'], health['interactions'], health['state']), (True, 1, False))
        self.assertEqual([(r['surface'], r['kind']) for r in feedback.remote_recent(self.url)], [('t3', 'pin')])

    def test_mirror_follows_the_collector_and_starts_over_after_a_reset(self):
        feedback.forward(self.url, [self.item(), self.item('pin')], 't3')
        mirror = self.home / 'mirror.sqlite'
        self.assertEqual(feedback.sync_mirror(self.url, mirror), (True, None))
        feedback.forward(self.url, [self.item('flip')], 'browser')
        feedback.sync_mirror(self.url, mirror)
        rows, contexts = feedback.load(mirror)
        self.assertEqual([r['kind'] for r in rows], ['peek', 'pin', 'flip'])
        self.assertEqual(set(contexts), {('t3', 'v1', 'm1'), ('browser', 'v1', 'm1')})
        self.db.unlink()  # the collector's database was reset and holds less than the mirror
        for suffix in ('-wal', '-shm'):
            Path(str(self.db) + suffix).unlink(missing_ok=True)
        feedback.forward(self.url, [self.item()], 't3')
        self.assertEqual(feedback.sync_mirror(self.url, mirror)[0], True)
        self.assertEqual([r['kind'] for r in feedback.load(mirror)[0]], ['peek'])
        self.assertFalse(feedback.sync_mirror('http://127.0.0.1:9', mirror)[0])  # down: the mirror stays as it was

    def test_the_popup_counts_come_from_the_collector(self):
        state_dir = fwlib.home()
        state_dir.mkdir(parents=True, exist_ok=True)
        self.assertEqual(fw_host.handle({'type': 'stats', 'since': 0}),  # no log here yet, and none created
                         {'ok': True, 'counts': {}, 'shownWords': [0, 0], 'blankPassages': [0, 0], 'readingMs': [0, 0],
                          'last': None, 'since': 0})
        self.assertFalse(feedback.default_path().exists())
        (state_dir / 'collector').write_text(self.url + '\n')
        feedback.forward(self.url, [self.item(), self.item('pin')], 't3')
        shown = dict(self.item(), kind='shown', words=48)
        fw_host.handle({'type': 'log', 'items': [self.item(), shown]})
        reply = fw_host.handle({'type': 'stats', 'since': int(time.time() * 1000) - 60_000})
        self.assertEqual((reply['ok'], reply['counts'], reply['shownWords']),
                         (True, {'t3:peek': [1, 1], 't3:pin': [1, 1], 'browser:peek': [1, 1]}, [48, 48]))
        self.assertFalse(fw_host.handle({'type': 'stats', 'since': 'today'})['ok'])
        self.assertEqual(feedback.request(self.url + '/v1/stats')['counts']['t3:peek'], [1, 1])  # since: today there
        (state_dir / 'collector').write_text('http://127.0.0.1:9\n')
        self.assertIn('unreachable', fw_host.handle({'type': 'stats', 'since': 0})['error'])

    def test_requests_for_another_host_name_are_refused(self):
        import urllib.request
        rebound = urllib.request.Request(self.url + '/v1/export', headers={'Host': 'attacker.example:7853'})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(rebound, timeout=5)
        self.assertEqual(refused.exception.code, 421)
        refused.exception.close()

    def test_native_host_forwards_to_the_collector_and_keeps_items_when_it_is_down(self):
        state_dir = fwlib.home()
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'collector').write_text(self.url + '\n')
        self.assertEqual(fw_host.handle({'type': 'log', 'items': [self.item()]})['stored'], 1)
        self.assertFalse(feedback.default_path().exists())  # nothing kept locally
        self.assertEqual(feedback.count(self.db), 1)
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['log'])
        self.assertIn('browser peek 問題  open 0.8s, 1 other glossed word in the passage', out.getvalue())
        (state_dir / 'collector').write_text('http://127.0.0.1:9\n')  # nothing listens there
        reply = fw_host.handle({'type': 'log', 'items': [self.item()]})
        self.assertFalse(reply['ok'])  # so the extension keeps them queued and retries
        self.assertIn('unreachable', reply['error'])


class Hub(Home):
    """The word state on a collector (its own state dir, on a local port) and this machine keeping a cache of it."""

    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        self.server_home = self.home / 'collector-home'
        with fwlib.using_home(self.server_home):
            hub.submit({'kind': 'seed', 'n': 40})  # the collector's own setup: the state lives there
        handler = type('TestHandler', (collector.Handler,), {'db': self.server_home / 'interactions.sqlite',
                                                             'home': self.server_home, 'log_message': lambda *args: None})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.join(self.url)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def join(self, url):
        fwlib.home().mkdir(parents=True, exist_ok=True)
        (fwlib.home() / 'collector').write_text(url + '\n')

    def served(self):
        with fwlib.using_home(self.server_home):
            return fwlib.load_state(create=False)

    def ops(self):
        import sqlite3
        con = sqlite3.connect(str(self.server_home / 'interactions.sqlite'))
        try:
            return con.execute('SELECT kind, machine FROM ops ORDER BY rowid').fetchall()
        finally:
            con.close()

    def learning(self, state):
        return [w['ja'] for w in state['words'] if w['status'] == 'active']

    def test_a_damaged_state_on_the_collector_goes_back_one_change_not_to_nothing(self):
        word = self.learning(fwlib.load_state())[0]
        with mock.patch('sys.stdout', new=io.StringIO()):
            fw.main(['known', word])
        self.assertEqual(fwlib.find(self.served(), word)['status'], 'known')
        (self.server_home / 'state.json').write_text('{"words": [', encoding='utf-8')  # cut short
        with mock.patch('sys.stderr', new=io.StringIO()) as err:
            served = self.served()
        self.assertIn('kept it as state.json.bad-', err.getvalue())
        self.assertEqual(fwlib.find(served, word)['status'], 'active')  # the state from before that change
        self.assertEqual(len(served['words']), len(fwlib.load_state()['words']))  # not a new, empty state
        self.assertEqual([p.read_text() for p in self.server_home.glob('state.json.bad-*')], ['{"words": ['])

    def test_changes_go_to_the_collector_and_the_cache_follows(self):
        cache = fwlib.load_state()  # no copy yet: fetched
        self.assertEqual(cache, self.served())
        word = self.learning(cache)[0]
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['known', word])
            fw.main(['challenge', '1.5'])
        self.assertIn(f'Known: {word}', out.getvalue())
        self.assertNotIn('Saved here', out.getvalue())
        self.assertEqual(fwlib.find(self.served(), word)['status'], 'known')
        self.assertEqual(self.served()['prefs'], {'challenge': 1.5})
        self.assertEqual(fwlib.load_state(), self.served())  # the cache was refreshed after each change
        peeked = self.learning(cache)[1]
        self.assertEqual(fw_host.handle({'type': 'events', 'events': [{'word': peeked, 'kind': 'peek'}]})['applied'], 1)
        self.assertEqual([op['kind'] for op in hub.pending()], ['events'])  # the browser never waits: sent by a sync
        self.assertEqual(hub.sync(), (True, None))
        self.assertEqual(hub.pending(), [])
        self.assertEqual(fw_host.handle({'type': 'state'})['version'], fwlib.version(self.served()))
        self.assertEqual([k for k, _ in self.ops()], ['seed', 'mark', 'set', 'events'])  # seed: its own setup
        self.assertEqual({m for _, m in self.ops()}, {socket.gethostname()[:64]})
        self.assertTrue((self.server_home / knowledge.COMMANDS).exists())  # the replay's command log lives there too
        self.assertFalse((fwlib.home() / knowledge.COMMANDS).exists())
        with self.assertRaises(fwlib.RemoteState):
            with fwlib.editing():
                pass

    def test_intensity_presses_go_to_the_collector_and_the_hook_asks_it(self):
        self.assertEqual(hub.change_intensity('chat', 1, 't3')[0]['level'], 1)
        value, error = hub.change_intensity('chat', 1, 't3')
        self.assertEqual((value['level'], error), (2, None))
        self.assertEqual(hub.change_intensity('chat', 1, 't3')[0]['level'], 3)  # full
        self.assertEqual(hub.change_intensity('chat', 1, 't3')[0]['level'], 3)  # at most full
        self.assertEqual(intensity.current(self.served()['prefs'], 'chat')['level'], 3)
        self.assertEqual(hub.live_intensity('chat')['level'], 3)  # asked of the collector, not the cache
        self.assertEqual(hub.live_intensity('browser'), dict(intensity.NORMAL))
        reply = feedback.request(self.url + '/v1/intensity', {'surface': 'chat', 'step': 2, 'source': 't3'})
        self.assertEqual(reply['ok'], False)
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['intensity', 'normal'])
            fw.main(['intensity', 'browser', 'down'])
        self.assertIn('Chat replies: normal.', out.getvalue())
        self.assertIn('Web pages: lighter (-1) until', out.getvalue())
        changes = feedback.intensity_changes(self.server_home / 'interactions.sqlite')
        self.assertEqual([(c['surface'], c['level'], c['source']) for c in changes],
                         [('chat', 1, 't3'), ('chat', 2, 't3'), ('chat', 3, 't3'), ('chat', 3, 't3'),
                          ('chat', 0, 'command'), ('browser', -1, 'command')])
        brief = []
        compose = lambda *a, **k: brief.append(k['level']) or ('', {'known': [], 'rules': ''}, {})
        with mock.patch.object(weave, 'compose', side_effect=compose):
            with mock.patch('sys.stdout', new=io.StringIO()):
                weave.main([])
        self.assertEqual(brief, [0])
        hub.change_intensity('chat', -1, 't3')
        with mock.patch.object(weave, 'compose', side_effect=compose):
            with mock.patch('sys.stdout', new=io.StringIO()):
                weave.main([])
        self.assertEqual(brief, [0, -1])

    def test_brief_records_wait_in_the_outbox_and_reach_the_collector(self):
        fwlib.load_state()
        payload = json.dumps({'session_id': 's1', 'prompt': 'Hi', 'transcript_path': str(self.home / 'none.jsonl')})
        with mock.patch.object(weave, 'interactive', return_value=True), \
                mock.patch('sys.stdin', new=io.StringIO(payload)), mock.patch('sys.stdout', new=io.StringIO()) as out:
            weave.main(['--hook'])
        context = json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']
        self.assertEqual([op['kind'] for op in hub.pending()], ['brief'])  # the hook never waits on the network
        self.assertEqual(hub.sync(scan=False), (True, None))
        self.assertEqual(hub.pending(), [])
        briefs = feedback.chat_briefs(self.server_home / 'interactions.sqlite')
        self.assertEqual([(b['session'], b['whole'], b['chars']) for b in briefs], [('s1', True, len(context))])
        self.assertEqual([k for k, _ in self.ops()], ['seed', 'brief'])
        self.assertEqual(self.served()['words'], fwlib.load_state()['words'])  # it changed nothing
        hub.queue(hub.stamp({'kind': 'brief', 'session': 's1'}))  # refused there (as by an older collector): dropped
        with mock.patch('sys.stderr', new=io.StringIO()) as err:
            self.assertEqual(hub.flush(), (True, 1))
        self.assertEqual(err.getvalue(), '')

    def test_changes_wait_here_while_the_collector_is_down(self):
        fwlib.load_state()
        word = self.learning(self.served())[0]
        self.join('http://127.0.0.1:9')  # nothing listens there
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['known', word])
            fw.main(['pause'])
            fw.main(['status'])
        self.assertIn('Saved here', out.getvalue())
        self.assertIn('Showing the copy here', out.getvalue())
        self.assertEqual(fwlib.find(fwlib.load_state(), word)['status'], 'known')  # shows at once
        self.assertTrue(fwlib.load_state()['paused'])
        self.assertEqual(fwlib.find(self.served(), word)['status'], 'active')
        self.assertEqual(len(hub.pending()), 2)
        self.assertEqual(fw_host.handle({'type': 'state'})['paused'], True)  # the browser still gets an answer
        self.join(self.url)
        self.assertEqual(hub.sync(scan=False), (True, None))
        self.assertEqual(hub.pending(), [])
        self.assertEqual((fwlib.find(self.served(), word)['status'], self.served()['paused']), ('known', True))
        self.assertEqual(fwlib.load_state(), self.served())
        resent = hub.stamp({'kind': 'set', 'paused': False})
        self.assertEqual(hub.post(self.url, [resent])['results'][0], {'ok': True})
        self.assertEqual(hub.post(self.url, [resent])['results'][0], {'ok': True, 'duplicate': True})  # a lost reply

    def test_the_hook_reads_the_cache_and_never_waits(self):
        fwlib.load_state()
        self.join('http://127.0.0.1:9')
        with mock.patch.object(weave, 'interactive', return_value=True), \
                mock.patch('sys.stdin', new=io.StringIO('{}')), mock.patch('sys.stdout', new=io.StringIO()) as out:
            weave.main(['--hook'])
        self.assertIn('Japanese Weave', json.loads(out.getvalue())['hookSpecificOutput']['additionalContext'])
        fwlib.write_json(fwlib.home() / 'state.json', dict(fwlib.load_state(), exported={'at': 1, 'version': 'old'}))
        self.assertIn('over a week behind', weave.export_notice(fwlib.load_state()))
        self.assertEqual([op['kind'] for op in hub.pending()], ['noticed'])  # queued, not waited on
        self.assertIsNone(weave.export_notice(fwlib.load_state()))  # shown once

    def test_chat_replies_from_two_machines_count_once(self):
        state = fwlib.load_state()
        word = self.learning(state)[0]
        chats = self.home / 'chats'
        (chats / 'p').mkdir(parents=True)
        (chats / 'p' / 'a.jsonl').write_text(line(f'Done, {word} works.', day=fwlib.today()) + '\n')
        with mock.patch.dict(os.environ, {'JAPANESE_WEAVE_TRANSCRIPTS': str(chats)}):
            self.assertTrue(fwlib.refresh_exposure())
            self.assertTrue(fwlib.refresh_exposure())  # nothing new
            with mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'other')}):
                self.join(self.url)  # another machine with a copy of the same chat (a forked session)
                self.assertTrue(fwlib.refresh_exposure())
        with fwlib.using_home(self.server_home):
            counts = fwlib.read_json(fwlib.home() / 'exposure.json')['words']
        self.assertEqual(counts[word], {'n': 1, 'days': [fwlib.today()]})
        self.assertEqual(counts[self.learning(state)[1]], {'n': 0, 'days': []})
        self.assertIn(str(chats / 'p' / 'a.jsonl'), fwlib.read_json(fwlib.home() / fwlib.SCAN)['offsets'])
        hub.pull()
        self.assertEqual(fwlib.read_json(fwlib.home() / 'exposure.json')['words'][word]['n'], 1)

    def test_anki_words_go_to_the_front_on_the_collector(self):
        fwlib.load_state()
        words = [{'ja': '猫', 'en': 'cat', 'hint': 'neko', 'kana': 'ねこ', 'interval': 40, 'mature': True},
                 {'ja': '机', 'en': 'desk', 'hint': 'tsukue', 'kana': 'つくえ', 'interval': 2, 'mature': False}]
        result = hub.submit({'kind': 'anki', 'words': words, 'known': ['猫']})
        self.assertEqual((result['ok'], result['known']), (True, 1))
        self.assertTrue((self.server_home / fwlib.ANKI_LEXICON).exists())
        self.assertFalse((fwlib.home() / fwlib.ANKI_LEXICON).exists())
        self.assertEqual([w['ja'] for w in fwlib.load_state()['words'][:2]], ['猫', '机'])
        self.assertEqual(fwlib.find(fwlib.load_state(), '猫')['status'], 'known')

    def test_a_machine_with_its_own_state_keeps_it_when_the_first_pull_replaces_it(self):
        with fwlib.using_home(self.home / 'fw'):  # this machine had its own list before the collector file appeared
            (fwlib.home() / 'collector').unlink()
            own = fwlib.load_state()
            fwlib.write_json(fwlib.home() / 'exposure.json', {'words': {}, 'offsets': {'/chat.jsonl': 42}})
        self.join(self.url)
        self.assertEqual(hub.sync(scan=False), (True, None))  # e.g. the hook's background sync, before setup ran
        self.assertEqual(fwlib.read_json(fwlib.home() / hub.BEFORE / 'state.json'), own)
        self.assertEqual(fwlib.read_json(fwlib.home() / fwlib.SCAN), {'offsets': {'/chat.jsonl': 42}})
        self.assertEqual(fwlib.load_state(), self.served())

    def test_a_cache_edited_behind_its_back_is_fetched_whole(self):
        fwlib.load_state()
        fwlib.write_json(fwlib.home() / 'state.json', dict(fwlib.load_state(), paused=True))  # e.g. an old copy's code
        self.assertEqual(hub.pull(), (True, None))
        self.assertEqual(fwlib.load_state(), self.served())
        hub.submit({'kind': 'set', 'stage': 2}, wait=False)  # a queued change is not an outside edit
        hub.pull()
        self.assertEqual(fwlib.load_state()['stage'], 2)

    def test_one_failing_op_never_holds_up_the_rest(self):
        fwlib.load_state()
        self.assertIn('day', hub.validate(hub.stamp({'kind': 'noticed', 'day': '2025-02-30'})))
        self.assertIn('at', hub.validate(hub.stamp({'kind': 'noticed', 'at': 'soon'})))
        self.assertFalse(fw_host.handle({'type': 'events', 'events': [{'word': 'x', 'kind': 'peek', 'day': '2025-02-30'}]})
                         ['applied'])  # an impossible day becomes today, never stored
        self.assertEqual(hub.pending()[0]['events'][0]['day'], fwlib.today())
        real = hub._apply_here
        calls = []

        def flaky(op):
            calls.append(op['kind'])
            if len(calls) == 1:
                raise KeyError('boom')
            return real(op)
        with mock.patch.object(hub, '_apply_here', flaky), mock.patch('sys.stderr', new=io.StringIO()):
            hub.submit({'kind': 'set', 'paused': True}, wait=False)
            self.assertEqual(hub.sync(scan=False), (True, None))
        self.assertEqual(calls, ['events', 'set'])
        self.assertEqual((hub.pending(), self.served()['paused']), ([], True))

    def test_the_collector_serves_only_its_own_state_and_name(self):
        import urllib.request
        lookalike = urllib.request.Request(self.url + '/v1/state', headers={'Host': socket.gethostname() + '.evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(lookalike, timeout=5)
        self.assertEqual(refused.exception.code, 421)
        refused.exception.close()
        with fwlib.using_home(self.server_home):
            (fwlib.home() / 'collector').write_text('http://elsewhere:7853\n')
            with self.assertRaises(SystemExit) as stopped:
                collector.main(['--listen', '127.0.0.1:0'])
        self.assertIn('remove its "collector" file', str(stopped.exception.code))

    def test_bad_ops_are_refused_one_by_one(self):
        good = hub.stamp({'kind': 'set', 'paused': True})
        reply = hub.post(self.url, [hub.stamp({'kind': 'drop_tables'}), good,
                                    hub.stamp({'kind': 'events', 'events': [{'word': 'x', 'kind': 'known'}]}),
                                    hub.stamp({'kind': 'set', 'prefs': {'challenge': 7}}), {'kind': 'set'}])
        self.assertEqual([r['ok'] for r in reply['results']], [False, True, False, False, False])
        self.assertTrue(self.served()['paused'])
        with self.assertRaises(ValueError):
            hub.submit({'kind': 'mark', 'words': [], 'as': 'forgotten'})

    def test_an_unchanged_state_is_not_sent_again(self):
        fwlib.load_state()
        rev = fwlib.read_json(fwlib.home() / hub.SYNCED)['rev']
        with fwlib.using_home(self.server_home):
            with fwlib.editing():
                pass  # nothing to change: nothing rewritten
            self.assertEqual(hub.snapshot(rev), (200, {'ok': True, 'rev': rev, 'unchanged': True}))
            hub.submit({'kind': 'set', 'stage': 2})
            self.assertNotIn('unchanged', hub.snapshot(rev)[1])

    def test_moving_this_machines_state_to_a_new_collector(self):
        empty = self.home / 'empty-collector'
        handler = type('EmptyHandler', (collector.Handler,), {'db': empty / 'interactions.sqlite', 'home': empty,
                                                              'log_message': lambda *args: None})
        from http.server import ThreadingHTTPServer
        import threading
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            (fwlib.home() / 'collector').unlink()
            with mock.patch.object(Path, 'home', return_value=self.home), mock.patch.dict(os.environ, {
                    'XDG_CONFIG_HOME': '', 'CODEX_HOME': str(self.home / 'codex')}), mock.patch('sys.stdout', new=io.StringIO()):
                fw.main(['setup', '--known-top', '10'])
                mine = fwlib.load_state()
                with self.assertRaises(SystemExit) as refused:
                    fw.main(['setup', '--collector', url])
                self.assertIn('has no word state yet', str(refused.exception.code))
                self.assertIn('state.json', str(refused.exception.code))
                self.assertIsNone(fwlib.collector_url())  # left as it was
                self.assertEqual(feedback.request(url + '/v1/state')['ok'], False)
                import shutil
                empty.mkdir(exist_ok=True)
                shutil.copy2(fwlib.home() / 'state.json', empty / 'state.json')  # the documented move
                fw.main(['setup', '--collector', url])
            self.assertEqual(fwlib.collector_url(), url)
            self.assertEqual(fwlib.load_state(), mine)
            self.assertEqual(fwlib.read_json(fwlib.home() / 'before-collector' / 'state.json'), mine)
        finally:
            server.shutdown()
            server.server_close()


class LocalExposure(Home):
    def test_scans_resume_and_older_offsets_carry_over(self):
        state = fwlib.load_state()
        word = [w['ja'] for w in state['words'] if w['status'] == 'active'][0]
        chats = self.home / 'chats'
        (chats / 'p').mkdir(parents=True)
        chat = chats / 'p' / 'a.jsonl'
        chat.write_text(line(f'Done, {word} works.', day=fwlib.today()) + '\n')
        with mock.patch.dict(os.environ, {'JAPANESE_WEAVE_TRANSCRIPTS': str(chats)}):
            fwlib.refresh_exposure()
            fwlib.refresh_exposure()
            self.assertEqual(fwlib.read_json(fwlib.home() / 'exposure.json')['words'][word]['n'], 1)
            (fwlib.home() / fwlib.SCAN).unlink()  # an older install: offsets in exposure.json
            index = fwlib.read_json(fwlib.home() / 'exposure.json')
            fwlib.write_json(fwlib.home() / 'exposure.json', dict(index, offsets={str(chat): chat.stat().st_size}))
            with open(chat, 'a') as handle:
                handle.write(line(f'Again {word}.', day=fwlib.today()) + '\n')
            fwlib.refresh_exposure()
        self.assertEqual(fwlib.read_json(fwlib.home() / 'exposure.json')['words'][word]['n'], 2)
        self.assertNotIn('offsets', fwlib.read_json(fwlib.home() / 'exposure.json'))
        self.assertEqual(self.ops_kinds(), ['exposure', 'exposure'])

    def ops_kinds(self):
        import sqlite3
        con = sqlite3.connect(str(feedback.default_path()))
        try:
            return [k for k, in con.execute('SELECT kind FROM ops ORDER BY rowid')]
        finally:
            con.close()


class Setup(Home):
    def test_setup_installs_extension_host_and_key(self):
        key = self.home / 'key.txt'
        key.write_text(f'  {FAKE_KEY} \n')
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': ''}), mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['setup', '--dry-run', '--gemini-key-file', str(key)])
            self.assertFalse((self.home / 'fw').exists())
            fw.main(['setup', '--gemini-key-file', str(key)])
        home = self.home / 'fw'
        self.assertIn('Load unpacked', out.getvalue())
        self.assertIn('Load Temporary Add-on', out.getvalue())
        firefox = json.loads((home / 'extension-firefox' / 'manifest.json').read_text())
        self.assertEqual(firefox['browser_specific_settings']['gecko']['id'], fw.GECKO_ID)
        self.assertEqual(firefox['background'], {'scripts': ['src/background.js'], 'type': 'module'})
        self.assertNotIn('key', firefox)
        import zipfile
        with zipfile.ZipFile(home / 'japanese-weave.xpi') as xpi:  # the permanent install, same files at the root
            self.assertEqual(json.loads(xpi.read('manifest.json')), firefox)
            self.assertIn('src/background.js', xpi.namelist())
        mozilla = self.home / ('Library/Application Support/Mozilla/NativeMessagingHosts' if sys.platform == 'darwin'
                               else '.mozilla/native-messaging-hosts')
        self.assertEqual(json.loads((mozilla / f'{fw.HOST_NAME}.json').read_text())['allowed_extensions'], [fw.GECKO_ID])
        self.assertTrue((home / 'extension' / 'manifest.json').exists())
        self.assertFalse((home / 'extension' / 'test').exists())
        self.assertEqual((home / 'gemini_key').read_text().strip(), FAKE_KEY)
        self.assertEqual(oct((home / 'gemini_key').stat().st_mode & 0o777), '0o600')
        base = self.home / ('Library/Application Support/Google/Chrome' if sys.platform == 'darwin' else '.config/google-chrome')
        manifest = json.loads((base / 'NativeMessagingHosts' / f'{fw.HOST_NAME}.json').read_text())
        self.assertEqual(manifest['allowed_origins'], [f'chrome-extension://{fw.EXTENSION_ID}/'])
        launcher = Path(manifest['path'])
        self.assertTrue(os.access(launcher, os.X_OK))
        # The installed copy answers on its own, without the plugin folder.
        msg = json.dumps({'type': 'state'}).encode()
        result = subprocess.run([str(launcher)], input=struct.pack('=I', len(msg)) + msg, capture_output=True, timeout=30,
                                env=dict(os.environ))
        reply = json.loads(result.stdout[4:])
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['geminiKey'], FAKE_KEY)

    def test_key_file_problems_never_echo_the_key_or_path(self):
        def fails(arg):
            with mock.patch.object(Path, 'home', return_value=self.home), mock.patch('sys.stdout', new=io.StringIO()) as out, \
                    self.assertRaises(SystemExit) as caught:
                fw.main(['setup', '--dry-run', '--gemini-key-file', arg])
            return str(caught.exception.code) + out.getvalue()
        self.assertIn('looks like a key', fails(FAKE_KEY))  # the key itself, given as the path
        self.assertNotIn(FAKE_KEY, fails(FAKE_KEY))
        self.assertIn('looks like a key', fails('x' * 40))
        missing = str(self.home / 'secret-dir' / 'no-such-file')
        self.assertNotIn('secret-dir', fails(missing))
        bad = self.home / 'notes.txt'
        bad.write_text(f'my key is {FAKE_KEY}\n')
        message = fails(str(bad))
        self.assertIn('only the key', message)
        self.assertNotIn(FAKE_KEY, message)
        self.assertFalse((self.home / 'fw').exists())

    def run_setup(self, *flags):
        with mock.patch.object(Path, 'home', return_value=self.home), mock.patch.dict(os.environ, {
                'XDG_CONFIG_HOME': '', 'CODEX_HOME': str(self.home / 'codex')}), mock.patch('sys.stdout', new=io.StringIO()):
            fw.main(['setup', *flags])
        return fwlib.load_state(create=False)

    def test_collector_flag_sets_and_clears_the_collector(self):
        with self.assertRaises(SystemExit) as refused:  # no state here, and the collector can't be reached
            self.run_setup('--collector', 'http://127.0.0.1:9/')
        self.assertIn('did not answer', str(refused.exception.code))
        self.assertIsNone(fwlib.collector_url())  # left as it was
        own = self.run_setup()
        with self.assertRaises(SystemExit) as refused:  # this machine has its own list: only for a confirmed state
            self.run_setup('--collector', 'http://127.0.0.1:9/')
        self.assertIn('nothing changed', str(refused.exception.code))
        self.assertIsNone(fwlib.collector_url())
        self.assertEqual(fwlib.load_state(), own)
        (fwlib.home() / 'collector').write_text('http://127.0.0.1:9\n')
        self.run_setup('--collector', 'off')
        self.assertIsNone(fwlib.collector_url())
        with self.assertRaises(SystemExit), mock.patch('sys.stderr', new=io.StringIO()):
            self.run_setup('--collector', 'functor:7853')

    def test_known_top_seeds_before_first_learning_words(self):
        state = self.run_setup('--known-top', '40')
        statuses = [w['status'] for w in state['words']]
        self.assertEqual(statuses[:40], ['known'] * 40)
        self.assertEqual(statuses[40:40 + fwlib.NEW_PER_DAY], ['active'] * fwlib.NEW_PER_DAY)
        self.assertEqual(fwlib.stage_for(state), 2)
        self.assertFalse(state['stage_pinned'])
        self.assertNotIn('prefs', state)

    def test_rerun_never_resets_or_downgrades(self):
        self.run_setup()  # top 5 become learning words
        with fwlib.editing() as state:
            fwlib.apply_event(state, 'でも', 'known')
            fwlib.apply_event(state, 'でも', 'asked')  # back to learning
        state = self.run_setup('--known-top', '150', '--sites', 'ask')
        by = {w['ja']: w for w in state['words']}
        self.assertEqual(by['でも']['status'], 'active')
        self.assertEqual(by['もう']['status'], 'active')
        self.assertEqual(sum(w['status'] == 'known' for w in state['words'][:150]), 145)
        self.assertEqual(fwlib.stage_for(state), 2)  # 145 known: stage 3 once the model labels learning words known
        self.assertEqual(state['prefs'], {'extension_sites': 'ask'})
        before = json.dumps(state)
        state = self.run_setup('--known-top', '40')  # a lower answer later changes nothing
        self.assertEqual(json.dumps(state), before)
        self.assertEqual(state['prefs'], {'extension_sites': 'ask'})  # --sites left out keeps the choice
        self.assertEqual(self.run_setup('--sites', 'all')['prefs'], {'extension_sites': 'all'})

    def test_dry_run_reports_answers_and_writes_nothing(self):
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': ''}), mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['setup', '--dry-run', '--known-top', '40', '--sites', 'ask'])
        info = json.loads(out.getvalue())
        self.assertEqual((info['known_top'], info['sites']), (40, 'ask'))
        self.assertIn('codex_cli', info)
        self.assertFalse((self.home / 'fw').exists())
        with self.assertRaises(SystemExit), mock.patch('sys.stderr', new=io.StringIO()):
            fw.main(['setup', '--dry-run', '--sites', 'some'])

    def test_remote_setup_proxies_over_ssh_and_keeps_no_state(self):
        self.run_setup('--remote', 'functor')
        launcher = (self.home / 'fw' / 'app' / 'fw-host').read_text()
        self.assertIn('ssh -T -o BatchMode=yes', launcher)
        self.assertIn('functor .japanese-weave/app/fw-host', launcher)
        self.assertFalse((self.home / 'fw' / 'state.json').exists())

    def test_extension_id_matches_manifest_key(self):
        import base64
        import hashlib
        key = json.loads((ROOT / 'extension' / 'manifest.json').read_text())['key']
        digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
        self.assertEqual(''.join(chr(ord('a') + int(c, 16)) for c in digest), fw.EXTENSION_ID)


def collection(path, cards, types=None):
    """A minimal Anki collection (current schema, with Anki's unicase collation): one note type per field layout,
    notes and cards as given."""
    import sqlite3
    con = sqlite3.connect(str(path))
    con.create_collation('unicase', lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
    con.executescript('create table fields (ntid integer, ord integer, name text collate unicase,'
                      ' primary key (ntid, ord)) without rowid;'
                      'create unique index idx_fields_name_ntid on fields (name, ntid);'
                      'create table decks (id integer, name text collate unicase);'
                      'create table notes (id integer, mid integer, flds text);'
                      'create table cards (id integer, nid integer, did integer, type integer, queue integer, ivl integer);')
    con.execute('insert into decks values (1, ?)', ('Japanese\x1fCore 2k',))
    con.execute('insert into decks values (2, ?)', ('Spanish',))
    types = types or {10: ['Vocabulary-Kanji', 'Vocabulary-Furigana', 'Vocabulary-English'], 20: ['Front', 'Back']}
    for ntid, names in types.items():
        for i, name in enumerate(names):
            con.execute('insert into fields values (?, ?, ?)', (ntid, i, name))
    for nid, (mid, fields, did, ctype, queue, ivl) in enumerate(cards, 1):
        con.execute('insert into notes values (?, ?, ?)', (nid, mid, '\x1f'.join(fields)))
        con.execute('insert into cards values (?, ?, ?, ?, ?, ?)', (nid, nid, did, ctype, queue, ivl))
    con.commit()
    con.close()


class Anki(Home):
    CARDS = [
        (10, ['問題', '問題[もんだい]', 'problem, question'], 1, 2, 2, 40),     # mature review
        (10, ['食べる', '食[た]べる', 'to eat'], 1, 2, 2, 5),                  # young review, not in the base list
        (10, ['猫', '猫[ねこ]', '<b>cat</b>'], 1, 1, 1, -600),                  # learning
        (10, ['犬', '犬[いぬ]', 'dog'], 1, 0, 0, 0),                            # new: skipped
        (10, ['鳥', '鳥[とり]', 'bird'], 1, 2, -1, 30),                         # suspended: skipped
        (20, ['これはペンです。', 'This is a pen.'], 1, 2, 2, 10),              # a sentence: skipped
        (20, ['勉強', 'study'], 1, 2, 2, 25),                                   # Front/Back, reading from the base list
    ]

    def test_review_words_order_and_fields(self):
        path = self.home / 'collection.anki2'
        collection(path, self.CARDS)
        words, report = anki.review_words(path, base=fwlib.read_lexicon(fwlib.LEXICON))
        self.assertEqual([w['ja'] for w in words], ['問題', '勉強', '食べる', '猫'])
        eat = words[2]
        self.assertEqual((eat['kana'], eat['hint'], eat['en'], eat['mature']), ('たべる', 'taberu', 'eat', False))
        self.assertEqual(words[3]['en'], 'cat')
        self.assertTrue(words[0]['mature'] and words[1]['mature'])
        self.assertEqual(report['skipped'], {'sentence or not a word': 1})
        self.assertEqual(anki.review_words(path, deck='spanish')[0], [])

    def test_skips_kana_charts_conjugations_and_explanations(self):
        path = self.home / 'collection.anki2'
        yomitan = ('<div class="yomitan-glossary"><i>(★, Jitendex.org [2025-08-01])</i> <span><div data-sc-content='
                   '"sense-group"><span data-sc-class="tag" data-sc-content="part-of-speech-info">noun</span>'
                   '<span data-sc-class="tag" data-sc-content="misc-info">kana</span><div data-sc-content="sense">'
                   '<ul data-sc-content="glossary"><li>Italy</li></ul></div></div></span>'
                   '<style>.yomitan-glossary { color: red; }</style></div>')
        types = {30: ['Word', 'Furigana', 'meaning'], 40: ['id', 'hiragana', 'katakana', 'Rōmaji'], 20: ['Front', 'Back'],
                 50: ['Conjugation', 'Infinitive', 'Base'], 60: ['grammatical concept', 'usage']}
        collection(path, [
            (30, ['イタリア', 'イタリア', yomitan], 1, 2, 2, 40),               # Yomitan glossary: no tags
            (40, ['1', 'ぎゃ', 'ギャ', 'gya'], 1, 2, 2, 40),                   # kana chart: romaji is no meaning
            (20, ['ぎゅ', 'gyu'], 1, 2, 2, 40),                                 # kana drill on Front/Back
            (50, ['書[か]かない', 'to write', '書[か]く'], 1, 2, 2, 40),          # conjugated form
            (50, ['書[か]く', 'to write', '書[か]く'], 1, 2, 2, 30),             # its dictionary form
            (60, ['まで', 'A particle which indicates the extent or degree to which something reaches, such as a '
                         'place, time or inanimate object.'], 1, 2, 2, 40),    # grammar explanation
        ], types)
        words, report = anki.review_words(path)
        self.assertEqual([(w['ja'], w['en']) for w in words], [('イタリア', 'italy'), ('書く', 'write')])
        self.assertEqual(report['skipped'], {'no English meaning': 2, 'romaji, not a meaning': 1,
                                             'conjugated form': 1})

    def test_sync_puts_anki_words_first_and_marks_mature_known(self):
        path = self.home / 'collection.anki2'
        collection(path, self.CARDS)
        with mock.patch('sys.stdout', new=io.StringIO()):
            fw.main(['anki', str(path)])
        state = fwlib.load_state()
        self.assertEqual([w['ja'] for w in state['words'][:4]], ['問題', '勉強', '食べる', '猫'])
        self.assertEqual([w['status'] for w in state['words'][:2]], ['known', 'known'])
        self.assertEqual(fwlib.find(state, '食べる')['status'], 'active')  # first in line for learning
        self.assertEqual(fwlib.find(state, 'taberu')['ja'], '食べる')


def episode(rows, contexts, surface, view, notes, opened, at, dwell=1500, kind='peek'):
    """One reading episode: a context with these glossed words ([word, x, y]) and peeks on the opened indexes."""
    contexts[(surface, view, 'c')] = {'surface': surface, 'view': view, 'context': 'c', 'annotations': notes,
                                     'words': None, 'shown_at': None}
    for i in opened:
        rows.append({'id': len(rows) + 1, 'at': at + i, 'surface': surface, 'kind': kind, 'word': notes[i][0],
                     'dwell_ms': dwell, 'view': view, 'context': 'c', 'position': i, 'uid': None})


def simulated_reader(seed=1, count=600):
    """Peeks drawn from known chances: 30 known words (p 0.05), 10 learning (0.5), 10 in between (0.25); each episode
    glosses 3 to 12 of them. Only episodes with a peek are logged, as in real use."""
    import random
    rng = random.Random(seed)
    truth = dict([(f'k{i}', 3.0) for i in range(30)] + [(f'l{i}', 0.0) for i in range(10)] + [(f'm{i}', 1.1) for i in range(10)])
    rows, contexts, at = [], {}, 0
    for e in range(count):
        at += 60_000
        picked = rng.sample(list(truth), rng.randint(3, 12))
        opened = [i for i, w in enumerate(picked) if rng.random() < knowledge.sigmoid(-truth[w])]
        if opened:
            episode(rows, contexts, 'browser', f'v{e}', [[w, 10 * i, 0] for i, w in enumerate(picked)], opened, at)
    return truth, rows, contexts, at + knowledge.SETTLE_MS + 1


class Intensity(Home):
    NOW = 1_800_000_000_000

    def test_steps_clamp_expire_and_go_back_to_normal(self):
        prefs = {}
        for _ in range(4):
            prefs['intensity_chat'] = intensity.step(prefs, 'chat', 1, 't3', self.NOW)
        self.assertEqual(intensity.current(prefs, 'chat', self.NOW), {'level': 3, 'until': self.NOW + intensity.DURATION_MS,
                                                                      'at': self.NOW})
        self.assertEqual(intensity.current(prefs, 'browser', self.NOW)['level'], 0)
        self.assertEqual(intensity.current(prefs, 'chat', self.NOW + intensity.DURATION_MS)['level'], 0)  # expired
        later = self.NOW + intensity.DURATION_MS + 1
        self.assertEqual(intensity.step(prefs, 'chat', -1, 't3', later)['level'], -1)  # from normal again
        self.assertEqual(intensity.step(prefs, 'chat', 0, 't3', self.NOW), {'level': 0, 'until': 0, 'at': self.NOW,
                                                                             'source': 't3'})
        self.assertFalse(intensity.valid({'level': 4, 'until': 0, 'at': 0}))
        self.assertFalse(intensity.valid({'level': 1, 'until': 0, 'at': 0, 'note': 'x'}))
        self.assertEqual(intensity.describe({'level': 0}), 'normal')

    def test_the_chat_brief_follows_the_level(self):
        state = {'stage': 3, 'stage_pinned': True, 'paused': False, 'words': [word('でも'), word('今', 'active')]}
        plain, light, heavy = (weave.brief(state, {}, level=level) for level in (0, -1, 1))
        # whole sentences take intensity.SENTENCE_SHARE of the budget at 0.08 each, the learning word (0.27) the rest
        self.assertIn('about 1.5 learning words per 100 words', plain)  # 0.41 / 0.27
        self.assertIn('about 1 whole sentence per 100 words', plain)  # 0.09 / 0.08
        self.assertIn('(Stage 3, turned lighter for now by the user)', light)
        self.assertIn('about 1 learning word per 100 words (3 in a 300-word reply), at most one in a sentence', light)
        self.assertIn('about 0.5 whole sentences per 100 words (2 in a 300-word reply)', light)  # 0.054 / 0.08
        self.assertIn('about 2 learning words per 100 words (6 in a 300-word reply), at most 2 in a sentence', heavy)
        self.assertIn('about 2 whole sentences per 100 words', heavy)  # 0.144 / 0.08
        for brief in (plain, light, heavy):
            self.assertIn('Default swaps (write the Japanese every time the English word has that meaning): '
                          'meaning → でも', brief)

    def test_the_native_host_saves_and_reads_the_browser_level(self):
        fwlib.save_state(fwlib.new_state(known_top=40))
        value = {'level': 1, 'until': int(time.time() * 1000) + 60_000, 'at': int(time.time() * 1000), 'source': 'shortcut'}
        reply = fw_host.handle({'type': 'intensity', 'surface': 'browser', 'set': value})
        self.assertEqual(reply['intensity']['level'], 1)
        self.assertEqual(fwlib.load_state()['prefs']['intensity_browser'], value)
        self.assertEqual(fw_host.handle({'type': 'intensity', 'surface': 'browser'})['intensity']['level'], 1)
        self.assertEqual(fw_host.handle({'type': 'intensity', 'surface': 'browser', 'step': -1})['intensity']['level'], 0)
        self.assertFalse(fw_host.handle({'type': 'intensity', 'surface': 'page', 'step': 1})['ok'])
        self.assertFalse(fw_host.handle({'type': 'intensity', 'surface': 'browser', 'set': dict(value, level=5)})['ok'])
        self.assertEqual(fw_host.handle({'type': 'state'})['prefs']['intensity_browser']['level'], 0)


class Knowledge(Home):
    NOW = 1_800_000_000_000

    def test_one_word_episodes_carry_no_evidence(self):
        rows, contexts = [], {}
        episode(rows, contexts, 'browser', 'v1', [['今', 0, 0]], [0], self.NOW - 3_600_000)
        words, _ = knowledge.replay(rows, contexts, {'今'}, {}, [], self.NOW)
        self.assertEqual((words['今'].k, words['今'].obs), (1.0, 1))  # logged only because it was peeked: says nothing

    def test_replay_recovers_the_true_peek_chances(self):
        import statistics
        truth, rows, contexts, now = simulated_reader()
        words, _ = knowledge.replay(rows, contexts, set(truth), {}, [], now)
        p = {g: knowledge.sigmoid(-statistics.median(words[w].k for w in truth if w[0] == g)) for g in 'klm'}
        self.assertLess(p['k'], 0.1)
        self.assertTrue(0.38 < p['l'] < 0.62, p)
        self.assertTrue(0.18 < p['m'] < 0.36, p)

    def test_observations(self):
        notes = [['今', 0, 0], ['問題', 50, 0], ['でも', 0, 20], ['多分', 0, 40], ['中', 0, 60], ['。', 80, 60]]
        listed = {'今', '問題', 'でも', '多分', '中'}
        row = lambda i, dwell=1500, kind='peek': {'kind': kind, 'word': notes[i][0], 'dwell_ms': dwell, 'position': i}
        context = {'annotations': notes}
        got = dict((w, (y, weight)) for w, y, weight in knowledge.observations('browser', context, [row(2, 400)], listed))
        self.assertEqual(got['でも'], (0.6, 1.0))  # a glance: soft evidence
        self.assertEqual((got['今'], got['問題']), ((0.0, 1.0), (0.0, 1.0)))  # above the peek: read
        self.assertEqual((got['多分'], got['中']), ((0.0, 0.5), (0.0, 0.25)))  # below it: maybe not
        self.assertEqual(knowledge.observations('t3', context, [row(2, 200)], listed), [])  # the mouse passing over
        got = dict((w, y) for w, y, _ in knowledge.observations('t3', context, [row(5, 900, 'pin')], listed))
        self.assertEqual(got, {})  # the whole sentence was read in English
        two = [['今', 0, 0], ['。', 10, 0], ['でも', 0, 20], ['？', 10, 20]]
        got = dict((w, y) for w, y, _ in knowledge.observations('t3', {'annotations': two}, [{'kind': 'pin', 'word': '？',
                                                                'dwell_ms': 900, 'position': 3}], {'今', 'でも'}))
        self.assertEqual(got, {'今': 0.0})  # only the translated sentence is excluded
        got = dict((w, y) for w, y, _ in knowledge.observations('t3', context, [row(1), row(5, 900, 'pin')], listed))
        self.assertEqual(got, {'問題': 1.0})  # a word opened in it still counts; its other words were read in English

    def test_episodes_split_at_gaps_and_wait_to_settle(self):
        rows, contexts = [], {}
        notes = [['今', 0, 0], ['でも', 10, 0]]
        episode(rows, contexts, 'browser', 'v1', notes, [0], self.NOW - 3_600_000)
        episode(rows, contexts, 'browser', 'v1', notes, [1], self.NOW - 1_800_000)
        episode(rows, contexts, 'browser', 'v1', notes, [0], self.NOW - 60_000)  # still being read
        self.assertEqual([len(e[2]) for e in knowledge.episodes(rows, contexts, self.NOW)], [1, 1])

    def test_refresh_labels_words_from_the_model_and_ignores_a_shrunk_log(self):
        with fwlib.editing() as state:
            listed = [w for w in state['words'] if w['status'] != 'queue']
            target = listed[0]['ja']
            others = [w['ja'] for w in listed[1:]]
        now = time.time()
        items = []
        for e in range(40):  # target never opened, every other word opened each time
            notes = [[target, 0, 0]] + [[w, 40 * (i + 1), 0] for i, w in enumerate(others)]
            items += [{'kind': 'peek', 'word': w, 'at': int((now - 3600 - e * 60 + i) * 1000), 'dwellMs': 1500,
                       'view': f'v{e}', 'context': 'c', 'position': 1 + i, 'box': [0, 0, 500, 40],
                       'annotations': notes, 'uid': f'u{e}-{i}'} for i, w in enumerate(others)]
        feedback.record(items, 'browser')
        fwlib.write_json(knowledge.path(), {'rows': 10_000})  # the log used to be longer: don't trust it
        know = knowledge.refresh(now)
        self.assertEqual((know['rows'], know['moves']), (40 * len(others), []))
        self.assertEqual(fwlib.find(fwlib.load_state(), target)['status'], 'active')
        know = knowledge.refresh(now)  # whatever the challenge: labels always come from the model
        self.assertEqual(know['challenge'], 0)
        self.assertEqual([(m['word'], m['from'], m['to']) for m in know['moves']], [(target, 'active', 'known')])
        state = fwlib.load_state()
        self.assertEqual(fwlib.find(state, target)['status'], 'known')
        self.assertTrue(know['words'][target]['free'])
        self.assertEqual([fwlib.find(state, ja)['status'] for ja in others], ['active'] * len(others))
        self.assertEqual(fwlib.find(state, target)['origin'], 'learning')  # the label never moves the prior
        self.assertGreater(know['words'][target]['k'], know['words'][others[0]]['k'])
        con = feedback.connect()
        ops = [json.loads(body) for (body,) in con.execute("SELECT body FROM ops WHERE kind = 'labels'")]
        con.close()
        self.assertEqual([op['labels'] for op in ops], [[[target, 'active', 'known']]])  # the ops log keeps it
        self.assertEqual(knowledge.refresh(now)['moves'], know['moves'])  # nothing new to change

    def showing(self, contexts, rows, view, notes, at, active, words=60, opened=(), jitter=None, level=0):
        contexts[('browser', view, 'c')] = {'surface': 'browser', 'view': view, 'context': 'c', 'annotations': notes,
                                           'words': words, 'shown_at': at, 'height': 80, 'active': active,
                                           'seen_until': at,
                                           'plan': None if jitter is None else {'level': level, 'jitter': jitter,
                                                                                 'candidates': 6,
                                                                                 'sentencePrice': 0.08}}
        for i in opened:
            rows.append({'id': len(rows) + 1, 'at': at + 1000 + i, 'surface': 'browser', 'kind': 'peek',
                         'word': notes[i][0], 'dwell_ms': 1500, 'view': view, 'context': 'c', 'position': i, 'uid': None})

    def test_read_chance_grows_with_time_on_screen_and_falls_further_down(self):
        c = {'words': 60, 'height': 80, 'active': 0}
        self.assertEqual(knowledge.read_chance(c, 0, 20), 0.0)  # never on screen while a reader could read
        c['active'] = 9000
        top, bottom = knowledge.read_chance(c, 0, 20), knowledge.read_chance(c, 60, 20)
        self.assertGreater(top, bottom)
        self.assertLessEqual(top, knowledge.READ_MAX)
        self.assertGreater(knowledge.read_chance(dict(c, active=60_000), 60, 20), bottom)

    def test_every_shown_passage_counts_by_the_chance_it_was_read(self):
        rows, contexts = [], {}
        for e in range(20):  # 今 shown and never opened, on screen long enough to read
            self.showing(contexts, rows, f'r{e}', [['今', 0, 0]], self.NOW - 7_200_000 + e * 60_000, active=30_000)
        for e in range(20):  # 中 shown just as often but never on screen while a reader could read
            self.showing(contexts, rows, f'u{e}', [['中', 0, 0]], self.NOW - 7_200_000 + e * 60_000, active=0)
        words, _ = knowledge.replay(rows, contexts, {'今', '中'}, {}, [], self.NOW)
        self.assertGreater(words['今'].k, 2.0)  # from 1.0: read and not opened
        self.assertNotIn('中', words)  # no chance it was read: no evidence
        self.showing(contexts, rows, 'p', [['今', 0, 0]], self.NOW - 900_000, active=30_000, opened=[0])
        before, _ = knowledge.replay(rows, contexts, {'今'}, {}, [], self.NOW - 400_000)  # not settled yet
        after, _ = knowledge.replay(rows, contexts, {'今'}, {}, [], self.NOW)
        self.assertEqual(before['今'].opened, 0)
        self.assertEqual(after['今'].opened, 1)
        self.assertLess(after['今'].k, before['今'].k)
        losses = []
        knowledge.replay(rows, contexts, {'今'}, {}, [], self.NOW, losses=losses)
        self.assertEqual((len(losses), sum(o for o, _ in losses)), (21, 1))
        import evaluate
        words = [{'ja': '今', 'kana': 'いま', 'status': 'queue', 'added': '', 'changed': ''}]
        log = evaluate.extract(rows, contexts, words, {}, [], self.NOW)
        fit = knowledge.Fit(log, self.NOW)
        self.assertGreater(fit.estimate('今')[0], fit.prior('今')[0])  # read and not opened, 20 times to 1
        tuned = knowledge.tune(log, self.NOW)  # one open: too few to leave the defaults
        both = lambda x: {surface: x for surface in knowledge.SURFACES}
        self.assertEqual((tuned['pace'], tuned['top'], tuned['tuned'], tuned['opens']),
                         (both(knowledge.PACE_MS), both(knowledge.READ_MAX), False, {'browser': 1.0, 't3': 0}))
        for surface in knowledge.SURFACES:  # each surface's grid, the defaults scored
            self.assertIn({'surface': surface, 'pace': knowledge.PACE_MS, 'top': knowledge.READ_MAX},
                          [{k: g[k] for k in ('surface', 'pace', 'top')} for g in tuned['grid']])
        # a reading model per surface: the web's alone changes the web's read chances, and the same numbers per
        # surface are the shared model
        slow = ({'browser': 1500, 't3': knowledge.PACE_MS}, {'browser': 0.3, 't3': knowledge.READ_MAX})
        alike = evaluate.extract(rows, contexts, words, {}, [], self.NOW, *(both(x) for x in (300, 0.7)))
        self.assertEqual([round(o.read, 9) for o in alike.scored()], [round(o.read, 9) for o in log.scored()])
        self.assertAlmostEqual(knowledge.Fit(alike, self.NOW).estimate('今')[0], fit.estimate('今')[0], places=6)
        apart = evaluate.extract(rows, contexts, words, {}, [], self.NOW, *slow)
        self.assertTrue(all(o.read <= 0.3 for o in apart.scored()))
        self.assertLess(sum(o.read for o in apart.scored()), sum(o.read for o in log.scored()))
        self.assertIn('web 1500 ms a word, cap 0.3; chat 300 ms a word, cap 0.7', knowledge.reading_text(*slow))

    def test_calibrate_compares_thinned_passages_and_counts_presses(self):
        import random
        rng = random.Random(3)
        rows, contexts = [], {}
        import math
        for e in range(80):  # a reader who slows down 2% per point of Japanese
            jitter = 1.0 if e % 2 else 0.5
            count = 6 if jitter == 1 else 3
            notes = [[f'w{i}', 0, 10 * i] for i in range(count)]
            ms_per_word = 300 * math.exp(2 * count / 60) * rng.uniform(0.98, 1.02)
            self.showing(contexts, rows, f'v{e}', notes, self.NOW - 3_600_000, active=ms_per_word * 60, jitter=jitter,
                         opened=[0] if e % 10 == 0 else [])
        hour = 3_600_000
        changes = [{'at': self.NOW - 5 * hour, 'surface': 'browser', 'level': -1, 'until': self.NOW - 2 * hour,
                    'source': 'shortcut', 'machine': 'm'},
                   {'at': self.NOW - 4 * hour, 'surface': 'browser', 'level': -2, 'until': self.NOW - hour,
                    'source': 'shortcut', 'machine': 'm'},
                   {'at': self.NOW - 3 * hour, 'surface': 'chat', 'level': 1, 'until': self.NOW, 'source': 't3',
                    'machine': 'm'}]
        report = knowledge.calibrate(rows, contexts, changes, self.NOW)
        self.assertEqual(report['passages'], 80)
        self.assertEqual(report['jitter']['thinned']['n'], 40)
        self.assertAlmostEqual(report['jitter']['effect'], 2.0, delta=0.4)
        self.assertEqual(sum(b['n'] for b in report['bins']), 80)
        self.assertEqual(report['presses']['browser'], {'lighter': 2, 'heavier': 0, 'hoursAt': {'-2': 3.0, '-1': 1.0},
                                                        'verdict': None})
        self.assertEqual(report['presses']['chat']['hoursAt'], {'1': 3.0})
        text = knowledge.calibration_text(report)
        self.assertIn('Randomly thinned passages (thinning free words only, since 2026-10-20): 40', text)
        self.assertIn('Web pages: 2 lighter and 0 heavier presses', text)

    def test_commands_set_knowledge_and_immunity(self):
        rows, contexts = [], {}
        knowledge.log_command(['今'], 'learn', now=self.NOW / 1000 - 3600)
        episode(rows, contexts, 'browser', 'v1', [['今', 0, 0], ['でも', 10, 0]], [1], self.NOW - 1_800_000)
        words, _ = knowledge.replay(rows, contexts, {'今', 'でも'}, {'今': 400}, knowledge.read_commands(), self.NOW)
        self.assertEqual(words['今'].mu, 3.0)  # a long Anki interval
        self.assertGreater(words['今'].k, 0)  # learn set it to 0, then it went unopened next to a peek
        self.assertLess(words['今'].k, 1)
        self.assertEqual(words['今'].command_at, self.NOW - 3_600_000)

    def test_host_and_commands_carry_the_challenge(self):
        self.assertEqual(fw_host.handle({'type': 'prefs', 'challenge': 1.5}), {'ok': True, 'challenge': 1.5})
        self.assertFalse(fw_host.handle({'type': 'prefs', 'challenge': True})['ok'])
        self.assertFalse(fw_host.handle({'type': 'prefs', 'challenge': 7})['ok'])
        state = fw_host.handle({'type': 'state'})
        first, second, third = (w['ja'] for w in state['words'][:3])
        fwlib.write_json(knowledge.path(), {'updated': time.time(), 'controller': {'scale': 2.0, 'day': 'x'},
                                            'words': {first: {'k': 0.3, 'sd': 0.9},
                                                      second: {'k': 2.0, 'sd': 0.4, 'free': True}}})
        state = fw_host.handle({'type': 'state'})
        self.assertEqual((state['challenge'], state['budget']), (1.5, 1.5))  # the challenge is normal's budget
        self.assertNotIn('rate', state)
        self.assertEqual({k: state['words'][0][k] for k in ('k', 'sd')}, {'k': 0.3, 'sd': 0.9})
        self.assertNotIn('free', state['words'][0])  # no flag: the extension compares p with FREE_P
        self.assertEqual({k: state['words'][1][k] for k in ('k', 'sd', 'free')}, {'k': 2.0, 'sd': 0.4, 'free': True})
        self.assertNotIn('k', state['words'][2])
        prior = {'card': {'new': 0.5, 'learning': 0.5, 'known': 0.5}, 'interval': 0.8,
                 'none': {'new': 1.0, 'learning': 1.2, 'known': 2.8}, 'sd': {'card': 0.8, 'none': 1.1}}
        fwlib.write_json(knowledge.path(), {'updated': time.time(), 'prior': prior, 'words': {}})
        words = fw_host.handle({'type': 'state'})['words']  # no estimates yet: the fitted prior for the word's status
        self.assertEqual({k: words[2][k] for k in ('k', 'sd', 'free')},
                         {'k': 2.8 if words[2]['status'] == 'known' else 1.0 if words[2]['status'] == 'queue'
                          else 1.2, 'sd': 1.1, 'free': words[2]['status'] == 'known'})
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['challenge', 'off'])
            fw.main(['why', first])
        self.assertIn('Lookup budget at normal: the default, 0.5 per 100 words', out.getvalue())
        self.assertIn('no evidence in the gloss log yet', out.getvalue())  # the refresh found no log
        fwlib.write_json(knowledge.path(), {'updated': time.time(), 'controller': {'scale': 2.0, 'day': 'x'}})
        state = fw_host.handle({'type': 'state'})
        # without a challenge, normal's own budget; a controller's scale from before is ignored
        self.assertEqual((state['challenge'], state['budget']), (0, intensity.BUDGETS[0]))
        self.assertNotIn('sentencePrice', state)  # none fitted yet: the extension uses its own default
        # the web's price of a whole sentence, once the fit puts one per surface in knowledge.json
        for price, sent in ((0.11, 0.11), (2, None), ('0.1', None), (True, None)):
            fwlib.write_json(knowledge.path(), {'updated': time.time(), 'sentencePrice': {'browser': price, 't3': 0.09}})
            self.assertEqual(fw_host.handle({'type': 'state'}).get('sentencePrice'), sent, price)
        with self.assertRaises(SystemExit):
            fw.main(['challenge', '5'])

    def test_the_extension_means_the_same_by_a_level(self):
        import re
        js = (ROOT / 'extension' / 'src' / 'levels.js').read_text(encoding='utf-8')
        table = lambda name: {int(k.strip("' ")): float(v) for k, v in
                              re.findall(r"('?-?\d'?): ([\d.]+)", re.search(rf'export const {name} = {{([^}}]*)}}', js).group(1))}
        constant = lambda name: float(re.search(rf'export const {name} = ([\d.]+);', js).group(1))
        self.assertEqual(table('INTENSITY_BUDGETS'), intensity.BUDGETS)
        self.assertEqual(table('INTENSITY_PER_SENTENCE'), intensity.PER_SENTENCE)
        self.assertEqual((constant('FREE_P'), constant('KNOWN_PRIOR_K')), (fwlib.FREE_P, fwlib.KNOWN_PRIOR_K))
        self.assertEqual((constant('SENTENCE_PRICE'), constant('SENTENCE_SHARE')),
                         (intensity.SENTENCE_PRICE, intensity.SENTENCE_SHARE))  # one rule for both surfaces

    def test_the_extension_offers_the_same_dial(self):
        import re
        js = (ROOT / 'extension' / 'src' / 'settings.js').read_text(encoding='utf-8')
        values = re.search(r'export const CHALLENGES = \[([^\]]*)\]', js).group(1)
        self.assertEqual(tuple(float(v) for v in values.split(',')), knowledge.CHALLENGES)

    def test_chat_orders_learning_words_by_learning_value(self):
        state = {'stage': 2, 'stage_pinned': True, 'paused': False,
                 'words': [word('一緒に', 'active'), word('結局', 'shaky'), word('多分', 'active')]}
        know = {'offset': {'t3': 0.0}, 'words': {'多分': {'k': 1.1, 'sd': 0.5}, '一緒に': {'k': 4, 'sd': 0.5}}}
        lines = weave.brief(state, {}, know=know).split('\n')
        self.assertTrue(lines[1].endswith(': 多分 = meaning, "hint"; 結局 = meaning, "hint"'), lines[1])
        self.assertTrue(lines[2].endswith(': meaning → 一緒に'), lines[2])  # known at a glance, whatever its status


class Budget(Home):
    """Below full a level is a lookup budget: words known at a glance are free, learning words spend it."""

    def know(self, ks, t3=0.0, browser=0.0):
        return {'offset': {'t3': t3, 'browser': browser}, 'words': {ja: {'k': k, 'sd': 0.5} for ja, k in ks.items()}}

    def test_known_at_a_glance_and_learning_split_by_lookup_chance(self):
        words = [word('でも'), word('中'), word('最悪'), word('一緒に', 'active'), word('結局', 'shaky'), word('多分', 'active')]
        state = {'stage': 2, 'stage_pinned': True, 'paused': False, 'words': words}
        # 中: known but looked up often; 結局: shaky but known at a glance; 最悪 and 一緒に: no evidence (their status)
        know = self.know({'でも': 4.0, '中': 0.5, '結局': 3.0, '多分': 1.5})
        free, learning, p = weave.lists(state, {}, know)
        self.assertEqual([w['ja'] for w in free], ['でも', '最悪', '結局'])
        self.assertEqual([w['ja'] for w in learning], ['一緒に', '多分', '中'])  # p 0.27, 0.18, 0.38: nearest 0.25 first
        self.assertAlmostEqual(p['最悪'], knowledge.sigmoid(-fwlib.KNOWN_PRIOR_K))
        self.assertAlmostEqual(p['一緒に'], knowledge.sigmoid(-knowledge.prior(None)))
        self.assertAlmostEqual(weave.chance(word('中'), know), knowledge.sigmoid(-0.5))
        self.assertAlmostEqual(weave.chance(word('最悪'), self.know({}, t3=0.5)), knowledge.sigmoid(0.5 - 2.5))  # T3
        lines = weave.brief(state, {}, know=know).split('\n')
        self.assertEqual(lines[1], 'Learning now, by how often the user looks one up: often, about 1 in 3: '
                                   '一緒に = meaning, "hint"; 中 = meaning, "hint" | sometimes, about 1 in 5: '
                                   '多分 = meaning, "hint"')
        self.assertEqual(lines[2], 'Default swaps (write the Japanese every time the English word has that meaning): '
                                   'meaning → でも; meaning → 結局')
        self.assertTrue(lines[3].endswith('has that meaning): 最悪 meaning'), lines[3])
        # mean chance (0.27 + 0.38 + 0.18) / 3: 0.41 (the budget less the sentences' 0.09) / 0.28 is 1.5 per 100 words
        self.assertIn('about 1.5 learning words per 100 words (5 in a 300-word reply)', lines[0])
        none = weave.brief(state, {}, know=self.know({ja: 5.0 for ja in ('でも', '中', '最悪', '一緒に', '結局', '多分')}))
        # no learning words: whole sentences take the budget, up to their most (weave.SENTENCES_MAX)
        self.assertIn('no learning words right now, only known ones; and about 3 whole sentences per 100 words', none)
        self.assertIn('Learning now, by how often the user looks one up: none\n', none)

    def test_the_brief_names_every_word_known_at_a_glance(self):
        state = fwlib.new_state('2026-01-01', known_top=200)  # and the next 5 words come in as learning words
        state.update(stage=3, stage_pinned=True)
        often = [w['ja'] for w in state['words'][10:40]]  # known, but the gloss log says they are looked up a lot
        know = self.know({ja: 1.0 for ja in often})
        lines = weave.brief(state, {}, know=know).split('\n')
        free, learning, _ = weave.lists(state, {}, know)
        self.assertEqual(len(free), 170)
        self.assertEqual(len(learning), weave.LEARN_MAX)
        self.assertTrue(lines[2].startswith('Default swaps (write the Japanese every time the English word has that '
                                            'meaning): '), lines[2])
        swaps = [pair.split(' → ')[1] for pair in lines[2].split(': ', 1)[1].split('; ')]
        self.assertTrue(lines[3].startswith('Known, besides the default swaps (each Japanese word, then its meaning; '), lines[3])
        named = [entry.split(' ')[0] for entry in lines[3].split('meaning): ', 1)[1].split('; ')]
        expected = [w['ja'] for w in weave.named_words(free)]
        self.assertEqual(sorted(swaps + named), sorted(expected))  # every one, each once, in the swaps or the list
        self.assertEqual(len(expected), 170 - sum(bool(weave.NUMBER.match(w['ja']) or weave.KATAKANA.match(w['ja']))
                                                  for w in free))
        self.assertEqual(swaps, [w['ja'] for w in weave.named_words(free)
                                 if weave.base_list()[fwlib.key(w['ja'])][1] == 'A'][:weave.DEFAULT_SWAPS])
        self.assertFalse(set(often) & set(swaps + named))  # looked up a lot: not named as known
        self.assertTrue(all(ja in lines[1] for ja in often if ja in {w['ja'] for w in learning}))
        self.assertNotIn('most common', '\n'.join(lines))
        self.assertLess(len('\n'.join(lines)), weave.BRIEF_MAX)
        self.assertEqual(fwlib.known_band(state), (205, [], []))  # the exports' band still describes status known

    def test_learning_words_per_100_words_spend_the_budget(self):
        self.assertIsNone(weave.per_100(0.5, []))
        self.assertEqual(weave.per_100(0.5, [0.25] * 4), 2)
        self.assertEqual(weave.per_100(0.5, [0.12]), 4)  # 4.2
        self.assertEqual(weave.per_100(0.3, [0.27]), 1)  # 1.1: halves under 2
        self.assertEqual(weave.per_100(0.15, [0.27]), 0.5)  # 0.56: never none while there are learning words
        self.assertEqual(weave.per_100(0.15, [0.9]), 0.5)
        self.assertEqual(weave.per_100(0.5, [0.3, 0.4]), 1.5)  # 1.43
        self.assertEqual(weave.per_100(1.2, [0.1]), weave.PER_100[1])  # 12, at most 8
        self.assertEqual([weave.most_per_100(level) for level in (-2, -1, 0, 1, 2)], [5, 5, 5, 8, 8])  # per sentence
        self.assertEqual(weave.per_100(1.5, [0.1], weave.most_per_100(0)), 5)  # 15, but one a sentence
        state = {'stage': 2, 'stage_pinned': True, 'paused': False, 'words': [word('でも'), word('今', 'active')]}
        self.assertIn('about 0.5 learning words per 100 words (2 in a 300-word reply), at most one in a sentence',
                      weave.brief(state, {}, level=-2))
        self.assertIn('about 4 learning words per 100 words (12 in a 300-word reply), at most 2 in a sentence',
                      weave.brief(state, {}, level=2))  # (1.2 - 0.216 for sentences) / 0.27
        self.assertEqual(weave.lookup_budget(state), intensity.BUDGETS[0])
        state['prefs'] = {'challenge': 1.5}  # the popup's challenge is normal's budget and scales the others
        self.assertEqual(weave.lookup_budget(state), 1.5)
        self.assertAlmostEqual(weave.lookup_budget(state, -1), 0.9)
        # sentences take 0.24, their most (3 per 100 words); learning words the other 1.26: 4.7 at 0.27
        self.assertIn('about 5 learning words per 100 words', weave.brief(state, {}))
        self.assertIn('about 3 whole sentences per 100 words', weave.brief(state, {}))
        state['prefs'] = {'challenge': 3}
        reply = 'The build passes and the cache is warm, so we ship it 今. ' * 3 + 'Nothing else needs doing today. ' * 9
        line = weave.calibration(reply, state)  # 3 learning words in 84
        self.assertIn('about 0.96 expected lookups per 100 words against a budget of 3', line)
        self.assertTrue(line.endswith(': on budget.'), line)  # all it asks for would spend 1.35 + 0.24
        self.assertIsNone(weave.lookup_budget(state, intensity.FULL))

    def test_whole_sentences_take_a_share_of_the_budget(self):
        split = lambda *args: tuple(round(x, 4) for x in intensity.split(*args))
        self.assertEqual(split(0.5, None, None), (0.41, 0.09))  # SENTENCE_SHARE of normal's 0.5
        self.assertEqual(split(0.5, 0.2, None), (0.2, 0.3))  # what learning words can't use goes to sentences
        self.assertEqual(split(0.5, None, 0.04), (0.46, 0.04))  # and the other way
        self.assertEqual(split(3, 1.35, 0.24), (1.35, 0.24))  # neither can use it all
        self.assertEqual(split(0.5, 0, 0), (0, 0))
        # normal asks for the sentences the Stage 3 brief asked for before they were priced: one in five, about 1.1 per
        # 100 words
        self.assertAlmostEqual(intensity.SENTENCE_SHARE * intensity.BUDGETS[0] / intensity.SENTENCE_PRICE, 1.125)
        rounded = lambda result: tuple(round(x, 4) if isinstance(x, float) else x for x in result)
        self.assertEqual(rounded(weave.amounts(0.5, [0.25] * 4, 0.08, 0)), (1.5, 1, 0.41, 0.09))
        self.assertEqual(rounded(weave.amounts(0.5, [], 0.08, 0)), (None, 3, 0, 0.24))  # sentences at their most
        self.assertEqual(rounded(weave.amounts(0.5, [0.25], 0.08, 0, sentences=False)), (2, 0, 0.5, 0))  # none known
        self.assertEqual(rounded(weave.amounts(0.15, [0.25], 0.08, -2)), (0.5, 0.5, 0.123, 0.027))
        self.assertEqual(rounded(weave.amounts(1.2, [0.25], 0.08, 2)), (4, 3, 0.984, 0.216))  # 2.7 sentences
        # the price: knowledge.json's for chat (t3) once a fit makes one, else the default the extension also uses
        self.assertEqual(intensity.sentence_price({'sentencePrice': {'t3': 0.045, 'browser': 0.2}}), 0.045)
        for bad in ({}, None, {'sentencePrice': {'browser': 0.2}}, {'sentencePrice': {'t3': 0}},
                    {'sentencePrice': {'t3': 1.5}}, {'sentencePrice': {'t3': True}}, {'sentencePrice': [0.1]}):
            self.assertEqual(intensity.sentence_price(bad), intensity.SENTENCE_PRICE, bad)
        state = {'stage': 3, 'stage_pinned': True, 'paused': False, 'words': [word('でも'), word('今', 'active')]}
        cheap = dict(self.know({}), sentencePrice={'t3': 0.045})  # 0.09 buys 2 sentences per 100 words
        text, _, plan = weave.compose(state, {}, know=cheap)
        self.assertIn('about 2 whole sentences per 100 words (6 in a 300-word reply)', text)
        self.assertEqual((plan['ask'], plan['sentences'], plan['sentencePrice']), (1.5, 2, 0.045))
        self.assertAlmostEqual(plan['expected'], 1.5 * knowledge.sigmoid(-knowledge.prior(None)) + 2 * 0.045, places=3)

    def test_feedback_line_compares_expected_lookups_with_the_budget(self):
        words = [word('でも'), word('だから'), word('今'), word('簡単', 'active'), word('問題', 'active')]
        state = {'stage': 2, 'stage_pinned': True, 'words': words}
        filler = 'The build passes and the cache is warm so nothing else needs doing before the release goes out. '
        one = filler * 5 + 'Then the 問題 goes away. '  # 1 learning word, at the neutral prior's 0.27, in 95
        line = weave.calibration(one, state)
        self.assertIn('had 1 Japanese words in 95 (0 known, 1 learning, 0 off the lists) and 0 whole Japanese '
                      'sentences: about 0.28 expected lookups per 100 words', line)
        self.assertIn('against a budget of 0.5 (known words are free, a whole sentence costs 0.08): on budget.', line)
        # 0.28 against 0.15, the learning words' part 0.12
        self.assertIn('over budget, so use fewer learning words this time', weave.calibration(one, state, level=-2))
        self.assertIn('under budget, so use more learning words and whole Japanese sentences this time',
                      weave.calibration(one, state, level=2))  # against 1.2: 0.98 for words, 0.22 for sentences
        known = filler.replace('so', 'だから') * 5  # known words are free, however many
        self.assertIn('(5 known, 0 learning, 0 off the lists) and 0 whole Japanese sentences: about 0.00 expected '
                      'lookups', weave.calibration(known, state))
        settled = self.know({'簡単': 4.0, '問題': 4.0})  # no learning words to use more of: sentences get the budget
        self.assertTrue(weave.calibration(filler * 5, state, know=settled).endswith(
            'under budget, so use more whole Japanese sentences this time unless the topic needs zero Japanese or the '
            'user asked for none.'))
        none = {'stage': 2, 'stage_pinned': True, 'words': [word('中', 'queue')]}  # nor known words to write them with
        self.assertTrue(weave.calibration(filler * 5, none).endswith(': on budget.'))
        # off the lists: a word not in use yet, a katakana loanword, or a listed word conjugated
        for reply in (filler * 5 + 'Then the 設定 goes away.', filler * 5 + 'Then the キャッシュ goes away.',
                      filler * 5 + 'Then it is 簡単です now.'):
            line = weave.calibration(reply, state)
            self.assertIn('1 off the lists', line)
            self.assertTrue(line.endswith('so this time use no Japanese outside the lists.'), line)
        self.assertIn('1 off the lists', weave.calibration(filler * 5 + 'Then the 中 goes away.',
                                                           dict(state, words=words + [word('中', 'queue')])))
        self.assertEqual(weave.calibration(filler * 5, state, level=intensity.FULL), '')
        self.assertEqual(weave.calibration('Done, でも pushed.', state), '')

    def test_the_free_flag_decides_what_is_known_at_a_glance(self):
        know = self.know({'でも': 1.5, '中': 4.0, '多分': 1.5})
        know['words']['でも']['free'] = True  # p 0.18 but free: one lookup didn't take it out
        know['words']['中']['free'] = False  # p 0.018 but not free: not yet clearly known again
        self.assertEqual({ja: fwlib.is_free(e) for ja, e in know['words'].items()},
                         {'でも': True, '中': False, '多分': False})
        self.assertLess(knowledge.word_estimate(know, 'でも', 't3')[0], fwlib.FREE_P)
        self.assertEqual(knowledge.word_estimate(know, '中', 't3')[0], fwlib.FREE_P)
        self.assertAlmostEqual(knowledge.word_estimate(know, '多分', 't3')[0], knowledge.sigmoid(-1.5))  # no flag
        state = {'stage': 2, 'stage_pinned': True, 'paused': False,
                 'words': [word('でも'), word('中'), word('多分', 'active')]}
        free, learning, _p = weave.lists(state, {}, know)
        self.assertEqual(([w['ja'] for w in free], [w['ja'] for w in learning]), (['でも'], ['多分', '中']))

    def test_new_words_come_in_while_the_budget_goes_unspent(self):
        now, hour = 1_800_000_000_000, 3_600_000
        web = lambda i, plan, ago=hour: (('browser', f'v{i}', 'c'), {'surface': 'browser', 'shown_at': now - ago,
                                                                       'plan': plan})
        contexts = dict(web(i, {'budget': 0.2, 'spent': 0.05, 'expected': 0.3}) for i in range(5))
        contexts.update([web(9, {'budget': 0.2, 'spent': 0}, 49 * hour), web(10, {'level': 3}),
                         (('t3', 'm', 'c'), {'surface': 't3', 'shown_at': now, 'plan': None})])
        briefs = [{'at': now - hour, 'budget': 0.5, 'expected': 0.45}] * 5 + [{'at': now, 'budget': None}]
        spend = knowledge.spending(contexts, briefs, now)
        self.assertEqual(spend, {'browser': {'plans': 5, 'budget': 1.0, 'spent': 0.25, 'share': 0.25},
                                 't3': {'plans': 5, 'budget': 2.5, 'spent': 2.25, 'share': 0.9}})
        self.assertTrue(knowledge.unspent(spend))  # the web's plans left three quarters of its budget
        state = fwlib.new_state('2026-10-01')
        self.assertEqual(knowledge.intake_room(state, spend, '2026-10-01'), 0)  # today's 5 are in
        self.assertEqual(knowledge.intake_room(state, spend, '2026-10-02'), fwlib.NEW_PER_DAY)
        spent = {'browser': dict(spend['browser'], share=0.85), 't3': spend['t3']}
        self.assertEqual(knowledge.intake_room(state, spent, '2026-10-02'), 0)  # both spend 80% or more
        few = {'browser': dict(spend['browser'], plans=4), 't3': {'plans': 0, 'budget': 0, 'spent': 0, 'share': None}}
        self.assertEqual(knowledge.intake_room(state, few, '2026-10-02'), 0)  # too little reading to tell
        old = dict(web(i, {'budget': 0.2, 'expected': 0.04}) for i in range(5))  # plans before spent was logged
        self.assertEqual(knowledge.spending(old, [], now)['browser']['share'], 0.2)

class DamagedState(Home):
    """A state.json cut short or edited by hand is kept aside and the copy from before the last save comes back."""

    def path(self):
        return fwlib.home() / 'state.json'

    def test_it_goes_back_to_the_copy_from_before_the_last_save(self):
        before = fwlib.load_state()
        fwlib.save_state(dict(before, paused=True))
        self.assertTrue(fwlib.load_state()['paused'])
        self.path().write_text('{"words": [', encoding='utf-8')
        with mock.patch('sys.stderr', new=io.StringIO()) as err:
            self.assertEqual(fwlib.load_state(), before)
        self.assertIn('went back to the copy from before the last change', err.getvalue())
        self.assertEqual([p.read_text() for p in fwlib.home().glob('state.json.bad-*')], ['{"words": ['])

    def test_without_a_good_copy_it_starts_over_and_keeps_the_damaged_file(self):
        self.path().parent.mkdir(parents=True)
        self.path().write_text('[]', encoding='utf-8')  # valid JSON, but not a word state
        with mock.patch('sys.stderr', new=io.StringIO()):
            self.assertIsNone(fwlib.load_state(create=False))
        self.assertTrue(fwlib.usable(fwlib.load_state()))
        self.assertEqual([p.read_text() for p in fwlib.home().glob('state.json.bad-*')], ['[]'])

    def test_a_file_it_cannot_open_is_left_alone(self):
        self.path().mkdir(parents=True)  # opening it fails with an OSError: nothing to say it is damaged
        self.assertIsNone(fwlib.load_state(create=False))
        self.assertTrue(self.path().is_dir())
        self.assertEqual(list(fwlib.home().glob('state.json.bad-*')), [])

    def test_a_damaged_file_never_becomes_the_copy_to_go_back_to(self):
        before = fwlib.load_state()
        fwlib.save_state(before)
        self.path().write_text('nope', encoding='utf-8')
        fwlib.save_state(dict(before, paused=True))  # a save over a damaged file keeps the older good copy
        self.assertEqual(fwlib.read_json(fwlib.home() / fwlib.PREVIOUS), before)


class OncePerChat(Home):
    """A chat gets the whole brief once, with the known words most useful in chat by name, and later only a digest and
    the changes."""

    def setUp(self):
        super().setUp()
        state = grown(300)
        state.update(stage=3, stage_pinned=True)
        fwlib.save_state(state)
        # a few known words looked up often enough to be learning words, so the learning list is full
        learn = [w['ja'] for w in state['words'] if w['status'] == 'known'][40:70]
        fwlib.write_json(fwlib.home() / 'knowledge.json', {'offset': {'t3': 0.0}, 'words': {
            ja: {'k': 1.0 + i / 20, 'sd': 0.5} for i, ja in enumerate(learn)}})
        self.log = self.home / 'chat.jsonl'
        self.log.write_text(line('The build passes and the cache is warm, so nothing else needs doing today. ' * 4) + '\n')

    def hook(self, session='s1', **extra):
        payload = dict({'session_id': session, 'prompt': 'Next.', 'transcript_path': str(self.log)}, **extra)
        with mock.patch.object(weave, 'interactive', return_value=True), \
                mock.patch('sys.stdin', new=io.StringIO(json.dumps(payload))), \
                mock.patch('sys.stdout', new=io.StringIO()) as out:
            self.assertEqual(weave.main(['--hook']), 0)
        return json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']

    def named(self, context):
        """The known words a brief names: its default swaps and its known list (or the words it adds)."""
        lines = context.split('\n')
        swaps = [pair.split(' → ')[1] for pair in lines[2].split('meaning): ', 1)[1].split('; ')]
        known = lines[3]
        listed = (known.split('has that meaning): ', 1)[1] if known.startswith('Known, besides') else
                  known.split('; also known: ', 1)[1].split('. If you', 1)[0] if '; also known: ' in known else '')
        return swaps, [entry.split(' ')[0] for entry in listed.split('; ') if entry and not entry.startswith('and ')]

    def test_the_known_list_goes_once_then_only_its_changes(self):
        first = self.hook()
        state, know = fwlib.load_state(), knowledge.load()
        free = weave.lists(state, {}, know)[0]
        swaps, listed = self.named(first)
        words = weave.named_words(free)
        self.assertEqual(swaps, [w['ja'] for w in weave.default_swaps(words)])
        self.assertEqual(listed, [w['ja'] for w in weave.capped([w for w in words if w['ja'] not in swaps])])
        left = len(words) - len(swaps) - len(listed)  # the most useful in chat named, as many as fit KNOWN_ROOM
        self.assertGreater(left, 0)
        self.assertIn(f'; and {left} more the user knows, not named here: keep the English for any word not named',
                      first)
        self.assertIn('the user is a native English speaker', first)
        self.assertIn('Your last reply in this chat had', first)
        second = self.hook()
        self.assertIn('Known: as listed earlier in this chat. If you can no longer see that list', second)
        self.assertIn('the Japanese Weave brief earlier in this chat still holds', second)
        self.assertNotIn('the user is a native English speaker', second)
        self.assertEqual(self.named(second), (swaps, []))  # the swaps every turn, the list once
        self.assertIn('Your last reply in this chat had', second)
        self.assertLess(len(second), len(first) / 3)
        # a word becomes known at a glance, another stops being one: only they are named, the new one as it came up
        # in the user's chat replies on two days, so it is among the most useful
        newly = [w for w in state['words'] if w['status'] == 'queue'][-1]
        with fwlib.editing() as edited:
            fwlib.apply_event(edited, newly['ja'], 'known')
        fwlib.write_json(fwlib.home() / 'knowledge.json', dict(know, words=dict(know['words'], **{listed[0]: {'k': 1.1, 'sd': 0.5}})))
        fwlib.write_json(fwlib.home() / 'exposure.json', {'words': {newly['ja']: {'n': 3, 'days': ['2026-10-19',
                                                                                                 '2026-10-20']}}})
        third = self.hook()
        # first, as it came up in chat; and the next word by priority, in the place the dropped one left in the list
        self.assertIn(f'; also known: {weave.entry(newly)}; ', third)
        self.assertEqual(len(third.split('; also known: ')[1].split('. If you')[0].split('; ')), 2)
        self.assertIn(f'; no longer known at a glance, so keep the English unless listed as learning: {listed[0]};', third)
        self.assertIn(f'{listed[0]} = ', third.split('\n')[1])  # it is a learning word now
        fourth = self.hook()
        self.assertNotIn(newly['ja'], fourth.split('\n')[3])  # said once
        self.assertNotIn('no longer known', fourth)
        self.assertIn('the user is a native English speaker', self.hook(session='s2'))  # another chat: its own list

    def test_a_compaction_or_twenty_turns_bring_the_whole_brief_back(self):
        whole = lambda context: context.split('\n')[3].startswith('Known, besides')
        self.assertTrue(whole(self.hook()))
        self.assertFalse(whole(self.hook()))
        boundary = {'type': 'system', 'subtype': 'compact_boundary', 'uuid': 'c-1', 'isSidechain': False,
                    'timestamp': '2026-10-20T12:00:00Z', 'content': 'Conversation compacted'}
        with open(self.log, 'a') as file:
            file.write(json.dumps(boundary) + '\n')
        self.assertTrue(whole(self.hook()))  # what came before it, the earlier brief too, is gone
        self.assertFalse(whole(self.hook()))  # the same compaction counts once
        codex = {'timestamp': '2026-10-20T13:00:00Z', 'type': 'compacted', 'payload': {'message': '', 'window_id': 'w2'}}
        with open(self.log, 'a') as file:
            file.write(json.dumps(codex) + '\n')
        self.assertTrue(whole(self.hook()))
        turns = [whole(self.hook()) for _ in range(weave.FULL_EVERY)]
        self.assertEqual(turns, [False] * (weave.FULL_EVERY - 1) + [True])  # in case the context lost it unseen
        self.assertEqual(weave.compaction([json.dumps(boundary), 'not json "compacted"', line('"compact_boundary"')]),
                         'c-1')
        self.assertIsNone(weave.compaction([line('we talked about "compacted" logs')]))

    def test_codex_and_chats_without_memory_still_get_a_brief(self):
        first, second = self.hook('codex-1', turn_id='t1'), self.hook('codex-1', turn_id='t2')
        self.assertIn('Known, besides', first)
        self.assertIn('Known: as listed earlier in this chat', second)
        self.assertIn('Known, besides', self.hook(None))  # no session id: whole every time
        (fwlib.home() / 'sessions.json').write_text('{"s1": {"n": "x", "brief": [1]}, "s3": 5', encoding='utf-8')
        self.assertIn('Known, besides', self.hook())  # a damaged memory: whole, as on a first turn
        self.assertIn('Known: as listed', self.hook())  # and kept from then on
        with mock.patch.object(weave, 'chat_turn', side_effect=OSError('read-only')):
            self.assertIn('Known, besides', self.hook())
        sessions = json.loads((fwlib.home() / 'sessions.json').read_text(encoding='utf-8'))
        self.assertIsInstance(sessions['s1']['brief']['known'], str)  # one line per chat in the file
        sessions['s1']['t'] = 'soon'
        fwlib.write_json(fwlib.home() / 'sessions.json', sessions)
        self.assertIn('Known: as listed', self.hook())  # a damaged time doesn't cost the chat its memory

    def test_a_new_user_with_no_known_words(self):
        fwlib.save_state(fwlib.new_state('2026-10-01'))
        fwlib.write_json(fwlib.home() / 'knowledge.json', {})
        first, second = self.hook(), self.hook()
        self.assertIn('\nKnown (each Japanese word, then its meaning; write the Japanese every time the English has '
                      'that meaning): none', first)
        self.assertNotIn('Default swaps', first)
        self.assertNotIn('no longer known', second)
        self.assertIn('\nKnown: as listed earlier in this chat. If', second)

    def test_later_turns_stay_shorter_than_the_old_brief(self):
        """Measured through the hook on a copy of the 2026-10-20 state (Stage 3, 293 words known at a glance, 24
        learning words, <abbr> glosses as the user has them, with the feedback line): the old brief was 3,993
        characters on every turn; now the first turn is 8,128 and later ones 2,294 (at full: 3,813 against 7,597 and
        1,786). The old brief was the rules and the learning words plus a band or a list of known words, so a later
        turn must stay under the first two alone, whatever the number of known words."""
        for known in (150, 300, 800):
            state = grown(known)
            state.update(stage=3, stage_pinned=True)
            for markup in (False, True):
                for level in (0, -1, 1, intensity.FULL):
                    _, had, _ = weave.compose(state, {}, markup=markup, level=level)
                    while True:
                        later, had, plan = weave.compose(state, {}, markup=markup, level=level, had=had)
                        if 'also known' not in later:
                            break
                    head, learning = later.split('\n')[:2]
                    old = weave.rules(3, level, markup, 'like "a" or "b"', plan.get('ask'), plan.get('sentences'))
                    self.assertLess(len(later), len(old) + len(learning), (known, markup, level))
                    self.assertLess(len(later) - len(learning), 1500, (known, markup, level))
                    self.assertFalse(plan['whole'])


class BriefRecords(Home):
    """Each chat reply's brief is kept as a record of what it was offered, like a web passage's plan."""

    def hook(self, session='s1', **extra):
        payload = dict({'session_id': session, 'prompt': 'Next.', 'transcript_path': str(self.home / 'none.jsonl')},
                       **extra)
        with mock.patch.object(weave, 'interactive', return_value=True), \
                mock.patch('sys.stdin', new=io.StringIO(json.dumps(payload))), \
                mock.patch('sys.stdout', new=io.StringIO()) as out:
            weave.main(['--hook'])
        return json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']

    def test_every_reply_has_a_record_joined_by_chat_and_time(self):
        fwlib.save_state(fwlib.new_state('2026-10-01', known_top=60))
        before = int(time.time() * 1000)
        first, second = self.hook(), self.hook()
        self.hook('codex-1', turn_id='turn-9')
        briefs = feedback.chat_briefs()
        self.assertEqual([(b['session'], b['whole'], b['chars']) for b in briefs],
                         [('s1', True, len(first)), ('s1', False, len(second)), ('codex-1', True, briefs[2]['chars'])])
        b = briefs[0]
        learning = [w['ja'] for w in fwlib.introduced(fwlib.load_state()) if w['status'] == 'active']
        self.assertEqual(sorted(ja for ja, _ in b['offered']), sorted(learning))
        self.assertTrue(all(abs(p - knowledge.sigmoid(-knowledge.prior(None))) < 0.001 for _, p in b['offered']))
        self.assertEqual((b['level'], b['intensity'], b['budget'], b['arms'], b['known']), (0, 1.0, 0.5, {}, 60))
        # the learning words asked for at their lookup chance and the whole sentences at their price
        self.assertEqual((b['sentences'], b['sentencePrice']), (1, intensity.SENTENCE_PRICE))
        self.assertAlmostEqual(b['expected'], b['ask'] * b['offered'][0][1] + b['sentences'] * b['sentencePrice'],
                               places=2)
        self.assertEqual(briefs[2]['turn'], 'turn-9')
        self.assertEqual([b['session'] for b in feedback.chat_briefs(session='codex-1')], ['codex-1'])
        self.assertEqual(feedback.brief_for(briefs, 's1', briefs[1]['at'] + 5000), briefs[1])  # the latest before it
        self.assertEqual(feedback.brief_for(briefs, 's1', briefs[0]['at']), briefs[0])
        self.assertIsNone(feedback.brief_for(briefs, 's1', before - 1))
        self.assertIsNone(feedback.brief_for(briefs, 's9', briefs[1]['at']))
        self.assertEqual(weave.compose(fwlib.load_state(), {}, level=intensity.FULL)[2].keys()
                         & {'budget', 'ask', 'sentences', 'expected'}, set())  # full has no budget
        self.assertTrue(intensity.LEVELS)

    def test_bad_records_are_refused(self):
        good = hub.stamp({'kind': 'brief', 'session': 's1', 'level': 0, 'intensity': 1.0, 'offered': [['問題', 0.27]],
                          'arms': {}, 'known': 3, 'whole': True, 'chars': 4000, 'budget': 0.5, 'ask': 2, 'expected': 0.54})
        self.assertIsNone(hub.validate(good))
        self.assertIsNone(hub.validate(dict(good, level=3, arms={'問題': 'held'}, turn='t-1')))
        self.assertIsNone(hub.validate(dict(good, sentences=1, sentencePrice=0.08)))  # since sentences were priced
        for bad in (dict(good, level=True), dict(good, level=4), dict(good, offered=[['問題', 1.5]]),
                    dict(good, offered=[['問題']]), dict(good, session='a b'), dict(good, arms=[]),
                    dict(good, whole='yes'), dict(good, chars=-1), dict(good, budget=float('nan')), dict(good, note='x'),
                    dict(good, intensity=0), {k: v for k, v in good.items() if k != 'known'},
                    dict(good, sentencePrice=0), dict(good, sentencePrice=1.5), dict(good, sentences=-1)):
            self.assertIsNotNone(hub.validate(bad), bad)
        self.assertEqual(hub.run(good), {'ok': True})
        self.assertEqual(hub.run(good), {'ok': True, 'duplicate': True})  # a resend is kept once
        self.assertFalse(hub.run(dict(good, uid='other', level=9))['ok'])
        self.assertFalse((fwlib.home() / 'state.json').exists())  # a record changes no word state


class Examples(unittest.TestCase):
    def test_example_sentences_use_words_known_at_a_glance(self):
        known = {'仕事': 0.02, '終わる': 0.03, '簡単': 0.05, '今日': 0.02, '忙しい': 0.3, '問題': 0.27}
        self.assertEqual(weave.examples(known), 'like "仕事は終わりました。" or "これは簡単です。"')

    def test_without_known_examples_the_brief_still_has_two(self):
        self.assertEqual(weave.examples({}), 'like "問題は設定です。" or "これは簡単です。"')


class ZeroJapanese(unittest.TestCase):
    def test_the_shared_cases(self):
        # extension/test/zero.test.js checks the page guard on the same cases
        cases = json.loads((Path(__file__).resolve().parent / 'zero_japanese_cases.json').read_text(encoding='utf-8'))
        self.assertGreater(len(cases['cases']), 10)
        for case in cases['cases']:
            self.assertEqual((case['text'], weave.zero_japanese(case['text'])), (case['text'], case['chat']))

    def test_one_strong_term_or_three_families_of_weak_ones(self):
        self.assertTrue(weave.zero_japanese('my doctor said so'))
        self.assertFalse(weave.zero_japanese('let me diagnose it: it was diagnosed as a race'))  # one family
        self.assertFalse(weave.zero_japanese('the symptom and the diagnosis'))  # two: the talk of a bug, often
        self.assertTrue(weave.zero_japanese('the symptom, the pain and the diagnosis'))
        self.assertTrue(weave.zero_japanese('the IRS sent a letter'))
        self.assertFalse(weave.zero_japanese('lower the IRs to bytecode'))  # a compiler's, not the tax office


if __name__ == '__main__':
    unittest.main()
