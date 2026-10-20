"""Offline tests for the ends of the intensity range (intensity.py): off (-3) and full (3), in the levels, the chat
brief and hook, fw.py, the native host, the collector and calibration. Run: python -m unittest tests/test_ends.py"""
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'
for name in ('CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ATTENDED', 'JAPANESE_WEAVE_FORCE', 'JAPANESE_WEAVE_OFF',
             'CLAUDE_CODE_SESSION_ID'):
    os.environ.pop(name, None)  # tests set these explicitly

import fwlib  # noqa: E402
import weave  # noqa: E402
import fw  # noqa: E402
import fw_host  # noqa: E402
import hub  # noqa: E402
import feedback  # noqa: E402
import collector  # noqa: E402
import intensity  # noqa: E402
import knowledge  # noqa: E402
import threads  # noqa: E402

JAPANESE = re.compile(r'[぀-ヿ一-鿿]')
ENGLISH = 'The build passes and the cache is warm, so nothing else needs doing before the release goes out. ' * 3


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
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats'),  # never yours
                                                'JAPANESE_WEAVE_FORCE': '1'})  # weave whatever process runs the tests
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def hook(self, prompt, session='s1', **extra):
        """The hook's additionalContext (and its whole output) for one prompt."""
        payload = dict({'session_id': session, 'prompt': prompt,
                        'transcript_path': str(self.home / 'missing.jsonl')}, **extra)
        with mock.patch('sys.stdin', new=io.StringIO(json.dumps(payload))), \
                mock.patch('sys.stdout', new=io.StringIO()) as out:
            self.assertEqual(weave.main(['--hook']), 0)
        out = json.loads(out.getvalue())
        return out['hookSpecificOutput']['additionalContext'], out

    def fw(self, *args):
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(list(args))
        return out.getvalue()

    def levels(self, db=None):
        return [(c['surface'], c['level'], c['source']) for c in feedback.intensity_changes(db or feedback.default_path())
                if c['surface'] != 'thread']


class Levels(unittest.TestCase):
    NOW = 1_800_000_000_000

    def test_presses_stop_at_off_and_full_and_restart_the_clock(self):
        for delta, end in ((-1, intensity.OFF), (1, intensity.FULL)):
            prefs = {}
            for i in range(4):  # the fourth press is past the end
                prefs['intensity_chat'] = intensity.step(prefs, 'chat', delta, 't3', self.NOW + i * 1000)
            last = self.NOW + 3000
            self.assertEqual(intensity.current(prefs, 'chat', last),
                             {'level': end, 'until': last + intensity.DURATION_MS, 'at': last})  # same level, new clock
            self.assertEqual(intensity.current(prefs, 'chat', last + intensity.DURATION_MS)['level'], 0)  # ran out
            self.assertEqual(intensity.step(prefs, 'chat', -delta, 't3', last + 1)['level'], end - delta)
            self.assertEqual(intensity.step(prefs, 'chat', 0, 't3', last + 1)['level'], 0)

    def test_every_level_validates_and_old_ones_still_do(self):
        for level in range(-3, 4):
            self.assertTrue(intensity.valid({'level': level, 'until': self.NOW, 'at': self.NOW, 'source': 't3'}), level)
        self.assertTrue(intensity.valid({'level': -2, 'until': self.NOW, 'at': self.NOW}))  # saved before the ends
        self.assertTrue(intensity.is_current({'level': 2, 'until': self.NOW, 'at': self.NOW}))
        for level in (-4, 4, True, 2.0, 2.5, '3', None):
            self.assertFalse(intensity.valid({'level': level, 'until': 0, 'at': 0}), level)

    def test_names_factors_and_descriptions(self):
        self.assertEqual((intensity.NAMES[-3], intensity.NAMES[3]), ('off', 'full'))
        self.assertEqual((intensity.factor(intensity.OFF), intensity.factor(intensity.FULL)), (0, intensity.factor(2)))
        self.assertTrue(intensity.is_off(-3) and intensity.is_full(3))
        self.assertFalse(intensity.is_off(-2) or intensity.is_full(2) or intensity.is_off(0) or intensity.is_full(0))
        until = self.NOW + intensity.DURATION_MS
        clock = time.strftime('%H:%M', time.localtime(until / 1000))
        self.assertEqual(intensity.describe({'level': -3, 'until': until}), f'off until {clock}')
        self.assertEqual(intensity.describe({'level': 3, 'until': until}), f'full (+3) until {clock}')
        self.assertEqual(intensity.describe({'level': -2, 'until': until}), f'much lighter (-2) until {clock}')
        self.assertEqual(intensity.describe({'level': 0, 'until': 0}), 'normal')


class Brief(Home):
    def state(self, stage=2):
        words = [word('でも'), word('中'), word('最悪'), word('一緒に', 'active'), word('結局', 'shaky')]
        return {'stage': stage, 'stage_pinned': True, 'paused': False, 'words': words}

    def test_full_has_no_target_and_keeps_the_rules(self):
        for stage in (1, 2, 3, 4):
            text = weave.brief(self.state(stage), {}, level=intensity.FULL)
            self.assertIn(f'Japanese Weave (Stage {stage}, turned to full for now by the user)', text)
            self.assertIn('no percentage target or cap', text)
            self.assertNotIn('%', text)
            self.assertNotIn('Under target', text)
            self.assertIn('fully in Japanese', text)
            self.assertIn('swapped in for every English word that has a word in the lists, known or learning', text)
            self.assertIn('Plain English for code, commands, paths, numbers, names, IDs and option letters', text)
            self.assertIn('zero Japanese, and no recall cue, for health, medication, legal, money', text)
            self.assertIn('never in files, code, configs, commit or PR text, messages to other agents', text)
            self.assertIn('footer line', text)
        lines = weave.brief(self.state(2), {}, level=intensity.FULL).split('\n')
        self.assertEqual(lines[1], 'Learning now (use each one wherever it fits): 結局 = meaning, "hint"; '
                                   '一緒に = meaning, "hint"')
        self.assertEqual(lines[2], 'Known (write the Japanese every time the English word has that meaning): '
                                   'meaning → でも; meaning → 中; meaning → 最悪')
        self.assertEqual(len(lines), 3)
        popover = weave.brief(self.state(3), {}, markup=True, level=intensity.FULL)
        self.assertIn('in a fully Japanese sentence wrap each word the same way', popover)
        self.assertNotIn('footer line', popover)
        recall = {'ja': '結局', 'en': 'after all', 'hint': 'kekkyoku'}
        self.assertIn('Recall: 結局?', weave.brief(self.state(), {}, recall, level=intensity.FULL))

    def test_full_with_hundreds_of_known_words_names_the_band(self):
        state = fwlib.new_state('2026-01-01')
        for w in state['words'][:fwlib.COLLAPSE_AT + 50]:
            fwlib.apply_event(state, w['ja'], 'known', '2026-01-01')
        state.update(stage=4, stage_pinned=True)
        text = weave.brief(state, {}, level=intensity.FULL)
        self.assertIn('turned to full for now by the user', text)
        self.assertIn('Known (use freely, never gloss): roughly the', text)
        self.assertNotIn('Known (write the Japanese every time', text)

    def test_full_has_no_calibration_line(self):
        state = {'stage': 2, 'stage_pinned': True, 'words': [word('でも'), word('今', 'active')]}
        self.assertIn('under the', weave.calibration(ENGLISH, state, level=2))
        self.assertEqual(weave.calibration(ENGLISH, state, level=intensity.FULL), '')

    def test_the_off_note_is_plain_english_and_fits_codex(self):
        until = int(time.time() * 1000) + intensity.DURATION_MS
        note, codex = weave.off_note(until), weave.off_note(until, codex=True)
        self.assertFalse(JAPANESE.search(note) or JAPANESE.search(codex))
        self.assertIn(f'turned Japanese off in chat replies for now, until {intensity.clock(until)}', note)
        self.assertIn('Alt+Shift+.', codex)
        self.assertIn('/japanese-weave:intensity normal', note)
        self.assertNotIn('/japanese-weave', codex)  # Codex has no plugin commands
        self.assertEqual(note.count('\n'), 0)


class Hook(Home):
    def setUp(self):
        super().setUp()
        state = fwlib.new_state(known_top=40)
        state['exported'] = {'at': time.time() - 8 * 86400, 'version': 'old', 'target': 'claude-ai'}  # a stale export
        fwlib.save_state(state)
        self.log = self.home / 'chat.jsonl'
        self.log.write_text(line(ENGLISH) + '\n')

    def test_off_sends_one_line_and_none_of_the_extras(self):
        self.assertIn('Chat replies: off until', self.fw('intensity', 'off'))
        until = fwlib.load_state()['prefs']['intensity_chat']['until']
        context, out = self.hook('Prove that the square root of 2 is irrational.', transcript_path=str(self.log))
        self.assertEqual(context, weave.off_note(until))  # no brief, recall or calibration line
        self.assertNotIn('systemMessage', out)  # and no export notice
        self.assertFalse((fwlib.home() / 'sessions.json').exists())  # nor a turn counted for the recall cue
        self.assertEqual(self.hook('Next.', session='codex-1', turn_id='t1')[0], weave.off_note(until, codex=True))
        self.assertIn('Web pages: normal.', self.fw('intensity', 'browser'))  # the browser has its own level
        self.assertEqual(hub.change_intensity('chat', 1, 't3')[0]['level'], -2)  # one press heavier: Japanese again
        context, out = self.hook('And again.', transcript_path=str(self.log))
        self.assertIn(', turned much lighter for now by the user): ', context)
        self.assertIn('under the', context)
        self.assertIn('systemMessage', out)

    def test_full_keeps_the_recall_cue_and_drops_the_calibration_line(self):
        self.assertIn('Chat replies: full (+3) until', self.fw('intensity', 'full'))
        context, out = self.hook('What is a monad?', transcript_path=str(self.log))
        self.assertIn(', turned to full for now by the user): ', context)
        self.assertIn('Recall:', context)
        self.assertNotIn('Your last reply', context)
        self.assertIn('systemMessage', out)

    def test_a_chat_switched_off_stays_off_at_any_level(self):
        self.fw('intensity', 'full')
        self.assertEqual(self.hook('no japanese for whole thread plz')[0], threads.note())
        self.assertEqual(self.hook('Next.')[0], threads.note())
        self.assertIn('turned to full for now', self.hook('Next.', session='s2')[0])
        self.fw('intensity', 'off')
        self.assertEqual(self.hook('Next.')[0], threads.note())  # the chat's own switch wins over the level
        self.assertIn('turned Japanese off in chat replies', self.hook('Next.', session='s2')[0])
        context = self.hook('japanese back on')[0]  # the chat is on again, and asking ends the off spell too
        self.assertIn('Japanese Weave (Stage', context)
        self.assertEqual(fwlib.load_state()['prefs']['intensity_chat']['level'], 0)
        self.assertNotIn('turned', self.hook('Next.', session='s2')[0])  # in all chats

    def test_off_and_full_are_set_outright_and_steps_still_step(self):
        self.assertIn('Chat replies: full (+3) until', self.fw('intensity', 'chat', 'full'))
        self.assertIn('Chat replies: much heavier (+2) until', self.fw('intensity', 'down'))
        self.assertIn('Web pages: off until', self.fw('intensity', 'browser', 'off'))
        value = fwlib.load_state()['prefs']['intensity_browser']
        self.assertEqual((value['level'], value['source'], value['until'] - value['at']),
                         (-3, 'command', intensity.DURATION_MS))
        self.assertIn('Web pages: much lighter (-2) until', self.fw('intensity', 'browser', 'up'))
        self.assertIn('Chat replies: normal.', self.fw('intensity', 'normal'))
        self.assertEqual(self.levels(), [('chat', 3, 'command'), ('chat', 2, 'command'), ('browser', -3, 'command'),
                                         ('browser', -2, 'command'), ('chat', 0, 'command')])
        with mock.patch('sys.stderr', new=io.StringIO()):
            for args in (['intensity', 'off', 'full'], ['intensity', 'chat', 'browser', 'off'], ['intensity', 'max']):
                with self.assertRaises(SystemExit, msg=args):
                    self.fw(*args)
        self.assertEqual(len(self.levels()), 5)


class Host(Home):
    def test_the_native_host_takes_the_whole_range(self):
        fwlib.save_state(fwlib.new_state(known_top=40))
        now = int(time.time() * 1000)
        ask = lambda **message: fw_host.handle(dict({'type': 'intensity', 'surface': 'browser'}, **message))
        for level in (-3, 3, -2):
            value = {'level': level, 'until': now + 60_000, 'at': now, 'source': 'shortcut'}
            self.assertEqual(ask(set=value)['intensity']['level'], level)
        self.assertEqual([ask(step=-1)['intensity']['level'] for _ in range(2)], [-3, -3])  # lighter at off stays off
        self.assertEqual(ask(step=1)['intensity']['level'], -2)
        self.assertEqual(ask(set={'level': 3, 'until': now + 60_000, 'at': now})['intensity']['level'], 3)
        self.assertEqual(ask(step=1)['intensity']['level'], 3)
        reply = ask(set={'level': 4, 'until': now + 60_000, 'at': now})
        self.assertFalse(reply['ok'])
        self.assertIn('-3..3', reply['error'])
        self.assertEqual(ask()['intensity']['level'], 3)

    def test_shown_records_at_full_are_kept(self):
        shown = {'kind': 'shown', 'at': int(time.time() * 1000) - 1000, 'view': 'v1', 'context': 'c1', 'words': 48,
                 'box': [0, 0, 500, 40], 'annotations': [['問題', 4, 0]],
                 'plan': {'level': 3, 'intensity': 1.8, 'jitter': 1, 'limit': 9, 'candidates': 9}}
        bad = dict(shown, context='c2', plan=dict(shown['plan'], level=4))
        self.assertEqual(feedback.record([shown, bad], 'browser'), {'stored': 1, 'duplicates': 0, 'rejected': 1})


class Collector(Home):
    """The word state on a collector (its own state dir, on a local port), as in test_weave's Hub tests."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.server_home = self.home / 'collector-home'
        with fwlib.using_home(self.server_home):
            hub.submit({'kind': 'seed', 'n': 40})  # the collector's own setup: the state lives there
        self.db = self.server_home / 'interactions.sqlite'
        handler = type('TestHandler', (collector.Handler,), {'db': self.db, 'home': self.server_home,
                                                             'log_message': lambda *args: None})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        fwlib.home().mkdir(parents=True, exist_ok=True)
        (fwlib.home() / 'collector').write_text(self.url + '\n')
        fwlib.load_state()  # this machine's first copy

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def press(self, step, surface='chat'):
        reply = feedback.request(self.url + '/v1/intensity', {'surface': surface, 'step': step, 'source': 't3'})
        self.assertTrue(reply['ok'], reply)
        return reply['intensity']

    def test_presses_on_the_collector_stop_at_both_ends(self):
        self.assertEqual([self.press(-1)['level'] for _ in range(4)], [-1, -2, -3, -3])
        off = feedback.request(self.url + '/v1/intensity?surface=chat')['intensity']
        self.assertEqual(off['level'], -3)
        self.assertIsNone((fwlib.load_state().get('prefs') or {}).get('intensity_chat'))  # this machine's cache is behind
        self.assertEqual(self.hook('Next.')[0], weave.off_note(off['until']))  # the hook asked the collector
        self.assertEqual([self.press(1)['level'] for _ in range(7)], [-2, -1, 0, 1, 2, 3, 3])
        self.assertIn('turned to full for now', self.hook('Next.')[0])
        self.assertEqual(self.press(0)['level'], 0)

    def test_off_and_full_from_fw_go_to_the_collector(self):
        self.assertIn('Web pages: full (+3) until', self.fw('intensity', 'browser', 'full'))
        self.assertIn('Chat replies: off until', self.fw('intensity', 'off'))
        self.assertEqual(hub.pending(), [])  # sent at once, not queued
        with fwlib.using_home(self.server_home):
            prefs = fwlib.load_state(create=False)['prefs']
        self.assertEqual((prefs['intensity_browser']['level'], prefs['intensity_chat']['level']), (3, -3))
        self.assertEqual(self.levels(self.db), [('browser', 3, 'command'), ('chat', -3, 'command')])
        self.assertEqual(self.press(1, 'browser')['level'], 3)


class Calibration(unittest.TestCase):
    NOW = 1_800_000_000_000

    def test_into_off_is_lighter_and_into_full_heavier(self):
        hour, second = 3_600_000, 1000
        chat = lambda at, level: {'at': at, 'surface': 'chat', 'level': level, 'until': at + 3 * hour if level else 0,
                                  'source': 'command', 'machine': 'm'}
        changes = [chat(self.NOW - 10 * hour, -3), chat(self.NOW - 5 * hour, 3)]  # set outright, each for 3 hours
        report = knowledge.calibrate([], {}, changes, self.NOW)
        self.assertEqual({k: report['presses']['chat'][k] for k in ('lighter', 'heavier', 'hoursAt')},
                         {'lighter': 1, 'heavier': 1, 'hoursAt': {'-3': 3.0, '3': 3.0}})
        self.assertIn('Chat replies: 1 lighter and 1 heavier presses (3.0 h off, 3.0 h at full).',
                      knowledge.calibration_text(report))
        burst = [chat(self.NOW - 2 * hour + i * second, level) for i, level in enumerate([-1, -2, -3])]
        report = knowledge.calibrate([], {}, burst, self.NOW)
        self.assertEqual((report['presses']['chat']['lighter'], report['presses']['chat']['heavier']), (1, 0))


if __name__ == '__main__':
    unittest.main()
