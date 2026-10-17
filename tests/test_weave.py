"""Offline tests for the hook, word state, native host and setup. Run: python3 -m unittest discover -s tests"""
import io
import json
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
import knowledge  # noqa: E402


def line(text, day='2026-10-09', sidechain=False):
    return json.dumps({'type': 'assistant', 'isSidechain': sidechain, 'timestamp': day + 'T12:00:00',
                       'message': {'content': [{'type': 'text', 'text': text}]}})


def word(ja, status='known'):
    return {'ja': ja, 'en': 'meaning', 'hint': 'hint', 'status': status, 'added': '2026-10-01', 'changed': '',
            'days': [], 'last_bad': ''}


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
        active = [w['ja'] for w in state['words'] if w['status'] == 'active']
        self.assertEqual(active, [w['ja'] for w in fwlib.load_lexicon()[:5]])  # 5 new a day
        self.assertEqual(fwlib.stage_for(state), 1)
        for day in ('2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05'):
            fwlib.promote(state, day, {})
        self.assertEqual(sum(w['status'] == 'active' for w in state['words']), fwlib.WINDOW)  # full at 20

    def test_graduation_needs_three_clean_days(self):
        state = fwlib.new_state('2026-10-01')
        chat = {'でも': {'n': 5, 'days': ['2026-10-01', '2026-10-02']}}
        fwlib.promote(state, '2026-10-02', chat)
        self.assertEqual(fwlib.find(state, 'でも')['status'], 'active')  # 2 days
        fwlib.apply_event(state, 'でも', 'seen', '2026-10-03')  # extension day + chat days
        fwlib.promote(state, '2026-10-03', chat)
        self.assertEqual(fwlib.find(state, 'でも')['status'], 'known')

    def test_peek_blocks_graduation_and_assumed_alone_never_graduates(self):
        state = fwlib.new_state('2026-10-01')
        chat = {'もう': {'n': 3, 'days': ['2026-10-01', '2026-10-02', '2026-10-03']}}
        fwlib.apply_event(state, 'もう', 'peek', '2026-10-02')
        fwlib.promote(state, '2026-10-03', chat)
        self.assertEqual(fwlib.find(state, 'もう')['status'], 'active')  # peek inside the last 3 days
        chat['もう']['days'] += ['2026-10-04', '2026-10-05']
        fwlib.promote(state, '2026-10-05', chat)
        self.assertEqual(fwlib.find(state, 'もう')['status'], 'known')  # last 3 days (03-05) are peek-free
        for day in ('2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'):
            fwlib.apply_event(state, '今', 'assumed', day)
        fwlib.promote(state, '2026-10-04', {})
        self.assertEqual(fwlib.find(state, '今')['status'], 'active')  # 4 assumed days, never seen
        fwlib.apply_event(state, '今', 'seen', '2026-10-05')
        fwlib.promote(state, '2026-10-05', {})
        self.assertEqual(fwlib.find(state, '今')['status'], 'known')

    def test_flip_counts_like_a_peek_day_and_shaky_recovers_after_7_clean_days(self):
        state = fwlib.new_state('2026-10-01')
        fwlib.apply_event(state, '前', 'known', '2026-10-01')
        fwlib.apply_event(state, '前', 'flip', '2026-10-02')  # one flip is often a test click
        self.assertEqual(fwlib.find(state, '前')['status'], 'known')
        fwlib.apply_event(state, '前', 'flip', '2026-10-05')
        self.assertEqual(fwlib.find(state, '前')['status'], 'shaky')
        learning = fwlib.find(state, state['words'][0]['ja'])
        fwlib.apply_event(state, learning['ja'], 'flip', '2026-10-05')
        self.assertEqual((learning['status'], learning['last_bad']), ('active', '2026-10-05'))  # restarts graduation
        fwlib.apply_event(state, '中', 'asked', '2026-10-04')
        fwlib.promote(state, '2026-10-10', {})
        self.assertEqual(fwlib.find(state, '中')['status'], 'shaky')
        fwlib.apply_event(state, '中', 'peek', '2026-10-09')  # restarts the clock
        fwlib.promote(state, '2026-10-15', {})
        self.assertEqual(fwlib.find(state, '中')['status'], 'shaky')
        fwlib.promote(state, '2026-10-16', {})
        self.assertEqual(fwlib.find(state, '中')['status'], 'known')

    def test_known_word_peeked_twice_in_a_week_becomes_shaky(self):
        state = fwlib.new_state('2026-10-01')
        fwlib.apply_event(state, '前', 'known', '2026-10-01')
        fwlib.apply_event(state, '前', 'peek', '2026-10-02')
        self.assertEqual(fwlib.find(state, '前')['status'], 'known')
        fwlib.apply_event(state, '前', 'peek', '2026-10-02')  # same day: once
        self.assertEqual(fwlib.find(state, '前')['status'], 'known')
        fwlib.apply_event(state, '前', 'peek', '2026-10-08')
        self.assertEqual(fwlib.find(state, '前')['status'], 'shaky')

    def test_refill_cap_and_shaky_words_take_learning_slots(self):
        state = fwlib.new_state('2026-10-01')
        for day in ('2026-10-02', '2026-10-03', '2026-10-04'):
            fwlib.promote(state, day, {})
        top = [w['ja'] for w in fwlib.load_lexicon()]
        for ja in top[:6]:
            fwlib.apply_event(state, ja, 'known', '2026-10-05')
        fwlib.promote(state, '2026-10-05', {})
        self.assertEqual(sum(w['status'] == 'active' for w in state['words']), fwlib.WINDOW - 1)  # 20-6+5
        for ja in top[6:11]:
            fwlib.apply_event(state, ja, 'asked', '2026-10-06')
        before = sum(w['status'] != 'queue' for w in state['words'])
        fwlib.promote(state, '2026-10-06', {})
        # 14 active + 5 shaky fill 19 of 20 slots: one new word, where 5 shaky words used to stop all new words
        self.assertEqual(sum(w['status'] != 'queue' for w in state['words']), before + 1)

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

    def test_tiers(self):
        self.assertEqual(weave.tier(word('でも'), self.seen), 'lot')
        self.assertEqual(weave.tier(word('中'), self.seen), 'some')
        self.assertEqual(weave.tier(word('最悪'), self.seen), 'rare')
        self.assertEqual(weave.tier(word('もっと'), self.seen), 'some')
        self.assertEqual(weave.tier(word('一緒に', 'active'), self.seen), 'learn')

    def test_brief_text(self):
        lines = weave.brief(self.state(), self.seen).split('\n')
        self.assertIn('about 12%', lines[0])
        self.assertIn('never over 20%', lines[0])
        self.assertIn('at most 4 in one sentence', lines[0])
        self.assertIn('never conjugated', lines[0])
        self.assertIn('never in files, code', lines[0])
        self.assertTrue(lines[1].endswith(': 結局 = meaning, "hint"; 一緒に = meaning, "hint"'))
        self.assertTrue(lines[2].endswith(': 最悪') and lines[3].endswith(': 中') and lines[4].endswith(': meaning → でも'))
        self.assertNotIn('何', '\n'.join(lines[1:]))  # queued words are not in use yet
        three = weave.brief(self.state(3), self.seen)
        self.assertIn('about 25%', three)
        self.assertNotIn('"hint"', three)
        self.assertIn('about 40%', weave.brief(self.state(4), self.seen))

    def test_recall_every_third_turn_shaky_first(self):
        state = self.state()
        picks = [weave.next_recall(state, 's1') for _ in range(7)]
        self.assertEqual([p and p['ja'] for p in picks], ['結局', None, None, '一緒に', None, None, None])
        self.assertIsNone(weave.next_recall(state, None))
        self.assertIn('Recall: 結局?', weave.brief(state, {}, picks[0]))

    def test_calibration(self):
        words = [word('でも'), word('だから'), word('今'), word('簡単', 'active'), word('問題', 'active'), word('もっと')]
        state = {'stage': 2, 'stage_pinned': True, 'words': words}
        english = 'The build passes and the cache is warm, so nothing else needs doing before the release goes out today. ' * 3
        self.assertEqual(weave.share(english, words), (0, 57, 0, 0))
        woven = 'The build passes, でも the cache is warm, だから nothing else needs 問題, 今 もっと 簡単 things wait. ' * 3
        self.assertEqual(weave.share(woven + '```でも だから```\n(問題 = problem, "mondai")\nRecall: でも?', words), (18, 54, 6, 0))
        self.assertIn('under the 12% target', weave.calibration(english, state))
        self.assertIn('over the 20% cap', weave.calibration(woven, state))
        self.assertEqual(weave.calibration('Done, でも pushed.', state), '')
        japanese = ('README.md にセクションを追加しました。空のエクスポートは行を削除しますが、テーブルは残ります。'
                    '他の問題は sync.py にあるので、直すかどうか教えてください。') * 2
        self.assertIn('slipped into Japanese sentences', weave.calibration(japanese, state))
        state3 = dict(state, stage=3)
        self.assertNotIn('slipped', weave.calibration(japanese, state3))  # Japanese sentences are the point at Stage 3
        overlap = [word('一緒'), word('一緒に'), word('今'), word('今日')]
        self.assertEqual(weave.share('Let us go 一緒に, 今日 then 一緒 it. ' * 6, overlap)[0], 18)  # 3 per sentence

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
        self.assertIn('Recall: でも?', context)
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
        recall = {'ja': 'でも', 'kana': 'でも', 'en': 'but / however', 'hint': 'demo'}
        footer, popover = weave.brief(state, {}, recall), weave.brief(state, {}, recall, markup=True)
        self.assertIn('footer line', footer)
        self.assertNotIn('<abbr', footer)
        self.assertIn('<abbr title="hiragana reading · meaning">', popover)
        self.assertNotIn('footer line', popover)
        self.assertIn('Recall: でも?', footer)
        self.assertNotIn('Recall', popover)  # popover glosses already test recall on every word
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
    for day in range(2, 7):
        fwlib.promote(state, f'2026-01-0{day}', {})
    return state


class Scale(Home):
    def test_known_band(self):
        state = grown(400)
        n, missing, extra = fwlib.known_band(state)
        known = {w['ja'] for w in state['words'] if w['status'] == 'known'}
        band = {w['ja'] for w in state['words'][:n]}
        self.assertEqual(known, (band - set(missing) - {w['ja'] for w in state['words'][:n] if w['status'] in ('active', 'shaky')}) | set(extra))

    def test_brief_and_exports_stay_small(self):
        for known in (50, 400, 1000):
            state = grown(known)
            brief = weave.brief(state, {}, {'ja': 'でも', 'en': 'but', 'hint': 'demo'})
            about, respond = export.chatgpt(state)
            self.assertLessEqual(len(about), export.CHATGPT_LIMIT)
            self.assertLessEqual(len(respond), export.CHATGPT_LIMIT)
            self.assertLess(len(export.claude_ai(state)), 6000)
            if known >= 400:
                self.assertIn('most common Japanese words', brief)
                self.assertLess(len(brief), 3400, known)

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

    def test_log_chat_day_counts_but_never_graduates_alone(self):
        state = fwlib.new_state('2026-10-01')
        for day in ('2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'):
            self.assertGreater(export.log_chat_day(state, day), 0)
            fwlib.promote(state, day, {})
        self.assertEqual(fwlib.find(state, 'でも')['status'], 'active')


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
        self.assertEqual(feedback.record([stay, trace] + bad, 't3'), {'stored': 2, 'duplicates': 0, 'rejected': 7})
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
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 4)
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
                         {'ok': True, 'stored': 1, 'duplicates': 0, 'rejected': 1})
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
                         {'ok': True, 'counts': {}, 'shownWords': [0, 0], 'readingMs': [0, 0], 'last': None, 'since': 0})
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
        self.assertEqual(fwlib.find(fwlib.load_state(), peeked)['peeks'], [fwlib.today()])  # shown here at once
        self.assertEqual(fwlib.find(self.served(), peeked)['peeks'], [])  # the browser never waits: sent by a sync
        self.assertEqual(hub.sync(), (True, None))
        self.assertEqual(fwlib.find(self.served(), peeked)['peeks'], [fwlib.today()])
        self.assertEqual(fw_host.handle({'type': 'state'})['version'], fwlib.version(self.served()))
        self.assertEqual([k for k, _ in self.ops()], ['seed', 'mark', 'set', 'events'])  # seed: its own setup
        self.assertEqual({m for _, m in self.ops()}, {socket.gethostname()[:64]})
        self.assertTrue((self.server_home / knowledge.COMMANDS).exists())  # the replay's command log lives there too
        self.assertFalse((fwlib.home() / knowledge.COMMANDS).exists())
        with self.assertRaises(fwlib.RemoteState):
            with fwlib.editing():
                pass

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
                pass  # promotion with nothing to promote: nothing rewritten
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
            fwlib.apply_event(state, 'でも', 'asked')  # shaky
            fwlib.apply_event(state, 'もう', 'seen')
        state = self.run_setup('--known-top', '150', '--sites', 'ask')
        by = {w['ja']: w for w in state['words']}
        self.assertEqual(by['でも']['status'], 'shaky')
        self.assertEqual(by['もう']['status'], 'active')
        self.assertEqual(by['もう']['days'], [fwlib.today()])
        self.assertEqual(sum(w['status'] == 'known' for w in state['words'][:150]), 145)
        self.assertEqual(fwlib.stage_for(state), 2)  # 145 known: stage 3 comes once the learning words graduate
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

    def test_episodes_split_at_gaps_and_wait_to_settle(self):
        rows, contexts = [], {}
        notes = [['今', 0, 0], ['でも', 10, 0]]
        episode(rows, contexts, 'browser', 'v1', notes, [0], self.NOW - 3_600_000)
        episode(rows, contexts, 'browser', 'v1', notes, [1], self.NOW - 1_800_000)
        episode(rows, contexts, 'browser', 'v1', notes, [0], self.NOW - 60_000)  # still being read
        self.assertEqual([len(e[2]) for e in knowledge.episodes(rows, contexts, self.NOW)], [1, 1])

    def word_entry(self, k, n, command_at=None):
        w = knowledge.Word(1.0)
        w.k, w.n, w.recent, w.command_at = k, n, [self.NOW], command_at
        return w

    def test_plan_moves(self):
        state = {'words': [word('a', 'shaky'), word('b', 'shaky'), word('c', 'known'), word('d', 'active'),
                           word('e', 'shaky'), word('f', 'known'), word('g', 'shaky')]}
        words = {'a': self.word_entry(4, 20), 'b': self.word_entry(4, 1),  # b: too little evidence
                 'c': self.word_entry(-2, 20), 'd': self.word_entry(-2, 20),
                 'e': self.word_entry(4, 20, command_at=self.NOW - knowledge.DAY_MS),  # right after `learn`
                 'f': self.word_entry(0.5, 20), 'g': self.word_entry(4, 20)}
        moves, veto = knowledge.plan_moves(state, words, {}, self.NOW)
        self.assertEqual(moves, [('a', 'shaky', 'known'), ('c', 'known', 'shaky'), ('g', 'shaky', 'known')])
        self.assertEqual(veto, ['d'])
        moves, _ = knowledge.plan_moves(state, words, {'known': 2}, self.NOW)
        self.assertEqual(moves, [('a', 'shaky', 'known'), ('c', 'known', 'shaky')])  # 3 a day per direction

    def test_vetoed_words_wait_to_graduate(self):
        state = {'words': [dict(word('今', 'active'), days=['2026-10-01', '2026-10-02', '2026-10-03'],
                                changed='2026-10-01')]}
        fwlib.promote(state, '2026-10-10', chat={}, veto={'今'})
        self.assertEqual(state['words'][0]['status'], 'active')
        fwlib.promote(state, '2026-10-10', chat={}, veto=set())
        self.assertEqual(state['words'][0]['status'], 'known')

    def test_steer(self):
        self.assertEqual(knowledge.steer(None, 2, 0.5, 100, 'd1'), {'scale': 1.0, 'day': ''})  # too little reading
        once = knowledge.steer(None, 2, 0.5, 400, 'd1')
        self.assertEqual(once, {'scale': 2.0, 'day': 'd1'})
        self.assertEqual(knowledge.steer(once, 2, 0.5, 400, 'd1'), once)  # once a day
        self.assertEqual(knowledge.steer(once, 2, 0.01, 400, 'd2')['scale'], 4.0)
        self.assertEqual(knowledge.steer(once, 0, 0.5, 400, 'd2'), once)  # dial off

    def test_refresh_moves_words_and_ignores_a_shrunk_log(self):
        with fwlib.editing() as state:
            listed = [w for w in state['words'] if w['status'] != 'queue']
            target = listed[0]['ja']
            fwlib.set_status(listed[0], 'shaky', fwlib.today())
            others = [w['ja'] for w in listed[1:]]
        now = time.time()
        items = []
        for e in range(40):  # target never opened, another word opened each time
            notes = [[target, 0, 0]] + [[w, 40 * (i + 1), 0] for i, w in enumerate(others)]
            items.append({'kind': 'peek', 'word': others[e % len(others)], 'at': int((now - 3600 - e * 60) * 1000),
                          'dwellMs': 1500, 'view': f'v{e}', 'context': 'c', 'position': 1 + e % len(others),
                          'box': [0, 0, 500, 40], 'annotations': notes, 'uid': f'u{e}'})
        feedback.record(items, 'browser')
        know = knowledge.refresh(now)  # the dial is off: only the day rules move words
        self.assertEqual((know['rows'], know['moves'], know['challenge']), (40, [], 0))
        fw_host.handle({'type': 'prefs', 'challenge': 1.5})
        fwlib.write_json(knowledge.path(), {'rows': 10_000})  # the log used to be longer: don't trust it
        know = knowledge.refresh(now)
        self.assertEqual((know['rows'], know['moves']), (40, []))
        self.assertEqual(fwlib.find(fwlib.load_state(), target)['status'], 'shaky')
        know = knowledge.refresh(now)
        self.assertEqual([(m['word'], m['to']) for m in know['moves']], [(target, 'known')])
        self.assertEqual(fwlib.find(fwlib.load_state(), target)['status'], 'known')
        self.assertLess(know['words'][target]['k'] - know['words'][target]['mu'], 3)
        self.assertGreater(know['words'][target]['k'], know['words'][others[0]]['k'])

    def test_commands_set_knowledge_and_immunity(self):
        rows, contexts = [], {}
        knowledge.log_command(['今'], 'learn', now=self.NOW / 1000 - 3600)
        episode(rows, contexts, 'browser', 'v1', [['今', 0, 0], ['でも', 10, 0]], [1], self.NOW - 1_800_000)
        words, _ = knowledge.replay(rows, contexts, {'今', 'でも'}, {'今': 400}, knowledge.read_commands(), self.NOW)
        self.assertEqual(words['今'].mu, 3.0)  # a long Anki interval
        self.assertGreater(words['今'].k, 0)  # learn set it to 0, then it went unopened next to a peek
        self.assertLess(words['今'].k, 1)
        self.assertEqual(words['今'].command_at, self.NOW - 3_600_000)

    def test_host_and_commands_carry_the_dial(self):
        self.assertEqual(fw_host.handle({'type': 'prefs', 'challenge': 1.5}), {'ok': True, 'challenge': 1.5})
        self.assertFalse(fw_host.handle({'type': 'prefs', 'challenge': True})['ok'])
        self.assertFalse(fw_host.handle({'type': 'prefs', 'challenge': 7})['ok'])
        state = fw_host.handle({'type': 'state'})
        first = state['words'][0]['ja']
        fwlib.write_json(knowledge.path(), {'updated': time.time(), 'controller': {'scale': 2.0, 'day': 'x'},
                                            'words': {first: {'k': 0.3, 'sd': 0.9}}})
        state = fw_host.handle({'type': 'state'})
        self.assertEqual((state['challenge'], state['budget']), (1.5, 3.0))
        self.assertEqual({k: state['words'][0][k] for k in ('k', 'sd')}, {'k': 0.3, 'sd': 0.9})
        self.assertNotIn('k', state['words'][1])
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['challenge', 'off'])
            fw.main(['why', first])
        self.assertIn('Adaptive difficulty: off.', out.getvalue())
        self.assertIn('no evidence in the gloss log yet', out.getvalue())  # the refresh found no log
        self.assertEqual(fw_host.handle({'type': 'state'})['challenge'], 0)
        with self.assertRaises(SystemExit):
            fw.main(['challenge', '5'])

    def test_the_extension_offers_the_same_dial(self):
        import re
        js = (ROOT / 'extension' / 'src' / 'settings.js').read_text(encoding='utf-8')
        values = re.search(r'export const CHALLENGES = \[([^\]]*)\]', js).group(1)
        self.assertEqual(tuple(float(v) for v in values.split(',')), knowledge.CHALLENGES)

    def test_chat_orders_learning_words_by_learning_value(self):
        state = {'stage': 2, 'stage_pinned': True, 'paused': False,
                 'words': [word('一緒に', 'active'), word('結局', 'shaky'), word('多分', 'active')]}
        know = {'offset': {'t3': 0.0}, 'words': {'多分': {'k': 1.1, 'sd': 0.5}, '一緒に': {'k': 4, 'sd': 0.5}}}
        line = weave.brief(state, {}, know=know).split('\n')[1]
        self.assertTrue(line.endswith(': 多分 = meaning, "hint"; 結局 = meaning, "hint"; 一緒に = meaning, "hint"'), line)


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


if __name__ == '__main__':
    unittest.main()
