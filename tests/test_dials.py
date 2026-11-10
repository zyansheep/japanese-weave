"""Two dials in chat (docs/chat-weave.md, "Two dials"): the sentence level as a surface of its own on the collector and
in fw.py intensity, beside the word level (the chat level from before). Run: python -m unittest tests/test_dials.py"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'
for name in ('CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ATTENDED', 'JAPANESE_WEAVE_FORCE', 'JAPANESE_WEAVE_OFF',
             'CLAUDE_CODE_SESSION_ID'):
    os.environ.pop(name, None)  # tests set these explicitly

import collector  # noqa: E402
import feedback  # noqa: E402
import fw  # noqa: E402
import fwlib  # noqa: E402
import hub  # noqa: E402
import intensity  # noqa: E402
import knowledge  # noqa: E402

NOW = 1_800_000_000_000


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fw(self, *args):
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(list(args))
        return out.getvalue()


class Surface(unittest.TestCase):
    def test_the_sentence_level_is_a_surface_with_the_same_rules(self):
        self.assertIn('sentences', intensity.SURFACES)
        prefs = {}
        for _ in range(5):
            prefs['intensity_sentences'] = intensity.step(prefs, 'sentences', 1, 't3', NOW)
        self.assertEqual(intensity.current(prefs, 'sentences', NOW)['level'], intensity.FULL)  # stops at full
        self.assertEqual(prefs['intensity_sentences']['until'] - NOW, intensity.DURATION_MS)
        self.assertEqual(intensity.chat_levels(prefs, NOW), (0, intensity.FULL))  # the word level is its own
        later = NOW + intensity.DURATION_MS
        self.assertEqual(intensity.chat_levels(prefs, later), (0, 0))  # back to normal after 3 hours
        prefs['intensity_chat'] = intensity.at_level(-1, 't3', NOW)
        self.assertEqual(intensity.chat_levels(prefs, NOW + 1), (-1, intensity.FULL))
        op = lambda value: hub.stamp({'kind': 'set', 'prefs': {'intensity_sentences': value}})
        self.assertIsNone(hub.validate(op(prefs['intensity_sentences'])))  # a pref the state takes
        self.assertIsNotNone(hub.validate(op({'level': 4})))


class Collector(Home):
    """The word state on a collector (its own state dir, on a local port), as in test_ends."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.server_home = self.home / 'collector-home'
        with fwlib.using_home(self.server_home):
            hub.submit({'kind': 'seed', 'n': 40})
        self.db = self.server_home / 'interactions.sqlite'
        handler = type('TestHandler', (collector.Handler,), {'db': self.db, 'home': self.server_home,
                                                             'log_message': lambda *args: None})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        fwlib.home().mkdir(parents=True, exist_ok=True)
        (fwlib.home() / 'collector').write_text(self.url + '\n')
        fwlib.load_state()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def press(self, step, surface):
        return feedback.request(self.url + '/v1/intensity', {'surface': surface, 'step': step, 'source': 't3'})

    def test_the_sentence_level_on_the_collector(self):
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual(chat['sentences'], {'level': 0, 'until': 0, 'at': 0})  # both dials in one request
        self.assertEqual(set(chat), {'ok', 'intensity', 'english', 'sentences'})
        self.assertNotIn('sentences', feedback.request(self.url + '/v1/intensity?surface=browser'))
        up = self.press(1, 'sentences')
        self.assertTrue(up['ok'], up)
        self.assertEqual(up['intensity']['level'], 1)
        self.assertEqual(up['intensity']['until'] - up['intensity']['at'], intensity.DURATION_MS)
        self.assertEqual(self.press(1, 'sentences')['intensity']['level'], 2)
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (0, 2))  # independent
        self.assertEqual(feedback.request(self.url + '/v1/intensity?surface=sentences')['intensity'], chat['sentences'])
        self.assertEqual(self.press(-1, 'chat')['intensity']['level'], -1)
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (-1, 2))
        presses = [(c['surface'], c['level']) for c in feedback.intensity_changes(self.db)]
        self.assertEqual(presses, [('sentences', 1), ('sentences', 2), ('chat', -1)])  # logged as presses
        self.assertEqual(self.press(0, 'sentences')['intensity']['level'], 0)
        for bad in ({'surface': 'sentence', 'step': 1, 'source': 't3'}, {'surface': 'sentences', 'step': 2,
                                                                         'source': 't3'}):
            self.assertFalse(feedback.request(self.url + '/v1/intensity', bad)['ok'])
        self.assertFalse(feedback.request(self.url + '/v1/intensity?surface=words')['ok'])
        # the hook asks for both dials at once
        live, _thread, english, sentences = hub.live_chat_dials('s1')
        self.assertEqual((live['level'], sentences['level'], english['on']), (-1, 0, False))

    def test_a_collector_without_the_sentence_level_means_one_dial(self):
        older = {'ok': True, 'intensity': {'level': 2, 'until': NOW, 'at': NOW - 1}, 'english': intensity.ENGLISH_OFF}
        with mock.patch.object(feedback, 'request', return_value=older):
            live, _thread, _english, sentences = hub.live_chat_dials('s1')
        self.assertEqual(sentences, live)

    def test_fw_intensity_shows_and_steps_the_sentence_level(self):
        self.assertIn('Chat sentences: heavier (+1) until', self.fw('intensity', 'sentences', 'up'))
        self.assertIn('Chat sentences: off until', self.fw('intensity', 'sentences', 'off'))
        both = self.fw('intensity')
        self.assertIn('Chat words: normal.', both)
        self.assertIn('Chat sentences: off until', both)
        self.assertEqual(self.fw('intensity', 'sentences', 'normal').strip(), 'Chat sentences: normal.')
        self.assertEqual(self.fw('intensity', 'chat').strip(), 'Chat words: normal.')
        changes = [c for c in feedback.intensity_changes(self.db) if c['surface'] == 'sentences']
        self.assertEqual([c['level'] for c in changes], [1, intensity.OFF, 0])


class Calibration(unittest.TestCase):
    def test_sentence_presses_are_counted_on_their_own(self):
        changes = [{'at': NOW - 7_200_000, 'surface': 'sentences', 'level': -1, 'until': NOW + 3_600_000,
                    'source': 't3'},
                   {'at': NOW - 3_600_000, 'surface': 'chat', 'level': 1, 'until': NOW + 7_200_000, 'source': 't3'}]
        report = knowledge.calibrate([], {}, changes, NOW)
        self.assertEqual((report['presses']['sentences']['lighter'], report['presses']['sentences']['heavier']), (1, 0))
        self.assertEqual((report['presses']['chat']['lighter'], report['presses']['chat']['heavier']), (0, 1))
        text = knowledge.calibration_text(report)
        self.assertIn('Chat sentences: 1 lighter and 0 heavier presses (2.0 h at -1)', text)
        self.assertIn('Chat words: 0 lighter and 1 heavier presses (1.0 h at +1)', text)
        del report['presses']['sentences']  # knowledge.json from before: no line
        self.assertNotIn('Chat sentences', knowledge.calibration_text(report))


if __name__ == '__main__':
    unittest.main()
