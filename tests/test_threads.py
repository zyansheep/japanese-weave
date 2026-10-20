"""Offline tests for one chat without Japanese (threads.py): asking in the prompt, the command, the thread op on the
collector and in the cache, and calibration. Run: python -m unittest tests/test_threads.py"""
import io
import json
import os
from pathlib import Path
import re
import socket
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
import hub  # noqa: E402
import feedback  # noqa: E402
import collector  # noqa: E402
import knowledge  # noqa: E402
import threads  # noqa: E402


def line(text, day='2026-10-09', sidechain=False):
    return json.dumps({'type': 'assistant', 'isSidechain': sidechain, 'timestamp': day + 'T12:00:00',
                       'message': {'content': [{'type': 'text', 'text': text}]}})


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

    def thread_ops(self, db=None):
        return [(c['session'], c['off'], c['source']) for c in feedback.intensity_changes(db or feedback.default_path())
                if c['surface'] == 'thread']


# Prompts that turn Japanese off in the chat, bring it back, and leave it alone.
OFF = ['no japanese for whole thread plz', 'no japanese please', 'english only', 'english only from now on',
       'stop using japanese', 'Write no Japanese in what you say to me', 'No Japanese.', 'no more japanese in this chat',
       'can you stop using japanese?', 'please reply only in English', 'thanks, no japanese please', 'make it english only',
       'turn off the japanese', 'japanese off', 'Drop the Japanese for the rest of this thread', 'I said no japanese',
       'plz no more jp', "don't use japanese here", 'can we drop the japanese in this chat?', 'enough with the japanese',
       'Please keep Japanese out of this thread', 'reply without japanese from now on', "no japanese, I'm reading math",
       'No Japanese for this whole thread please. Now: prove that every bounded monotone sequence converges, with the '
       'epsilon-N argument spelled out.',
       'Write no Japanese in what you say to me. I will paste a long stack trace and need the bug in the parser. It '
       'fails on nested brackets. Here it is.\nTraceback: ...\nThanks.']
ON = ['japanese back on', 'you can use japanese again', 'turn japanese back on', 'Turn the Japanese back on please',
      'ok, bring back the japanese', 'japanese is fine again', 'start using japanese again']
NEITHER = [
    'no Japanese in commit messages', 'the hook should put no Japanese in configs',
    'How does the weave brief decide how much Japanese to use? I noticed it says to use more Japanese when the last '
    'reply was 0% Japanese. Should it stop using Japanese when a thread is about math? Explain the design.',
    'is there no japanese version of this library?', 'the page has no japanese', "There's no Japanese in the reply",
    'I do not use japanese at work', "I don't speak Japanese", 'Why did you stop using Japanese?',
    "No Japanese in prose you write into files (comments, docs, commit messages, the hook's injected text).",
    'write the docs in english only', 'Make the extension english only', 'the site is english only',
    "Add a test: when the user types 'no japanese please', the hook turns it off.",
    'The user typed "no japanese for whole thread plz" and Claude complied.', 'no japanese support in this font?',
    'stop using japanese characters in filenames', 'translate this to english only', 'explain it without japanese',
    'Japanese only as test data if needed.', '/japanese-weave:known tabun', 'keep the japanese words in the lexicon',
    'Can you check why the japanese stopped?', 'we have no japanese locale installed',
    'Compute the integral of x^2 from 0 to 1 with no japanese and show the steps carefully now',
    'If they say english only, turn it off.', 'Should there be no Japanese in the export block?',
    'use `no japanese` as the trigger', 'The README must say: english only.', 'japanese back on in the docs',
    'Bring back the Japanese words in the extension popup', "what's the japanese for 'back on'?", '', None]
COMMANDS = {'/japanese-weave:intensity off here': (True, 'command'),
            '/japanese-weave:intensity normal here': (False, 'command'), '/intensity off here': (True, 'command'),
            '<command-message>japanese-weave:intensity</command-message>\n<command-name>/japanese-weave:intensity'
            '</command-name>\n<command-args>off here</command-args>': (True, 'command'),
            '/japanese-weave:intensity down': None, '/japanese-weave:intensity off': None,
            '/japanese-weave:status no japanese please': None}


class Asking(unittest.TestCase):
    def test_plain_requests_turn_it_off_and_back_on(self):
        for prompt in OFF:
            self.assertEqual(threads.request(prompt), (True, 'chat'), prompt)
        for prompt in ON:
            self.assertEqual(threads.request(prompt), (False, 'chat'), prompt)

    def test_talk_about_japanese_files_or_the_feature_changes_nothing(self):
        for prompt in NEITHER:
            self.assertIsNone(threads.request(prompt), prompt)

    def test_the_command_form(self):
        for prompt, want in COMMANDS.items():
            self.assertEqual(threads.request(prompt), want, prompt)

    def test_the_last_request_wins_and_long_prompts_need_a_scope(self):
        self.assertEqual(threads.request('no japanese please.\nActually, japanese back on.'), (False, 'chat'))
        filler = 'The proof uses induction on the length of the word and a careful case split. ' * 3
        self.assertIsNone(threads.request('English only. ' + filler))  # a long prompt that doesn't name the chat
        self.assertEqual(threads.request('English only from now on. ' + filler), (True, 'chat'))
        middle = filler + 'no japanese please. ' + filler + filler
        self.assertIsNone(threads.request(middle))  # only the first and last sentences are read


class Switch(unittest.TestCase):
    NOW = 1_800_000_000_000

    def test_later_change_wins_and_old_chats_are_forgotten(self):
        state = {}
        self.assertTrue(threads.merge(state, 'a', {'off': True, 'at': self.NOW, 'source': 'chat'}, self.NOW))
        self.assertTrue(threads.merge(state, 'a', {'off': False, 'at': self.NOW - 1, 'source': 'chat'}, self.NOW))
        self.assertFalse(threads.merge(state, 'a', {'off': False, 'at': self.NOW + 1, 'source': 'command'}, self.NOW))
        threads.merge(state, 'b', {'off': True, 'at': self.NOW, 'source': 'chat'}, self.NOW)  # another chat stays
        self.assertEqual(set(state['threads']), {'a', 'b'})
        old = self.NOW - threads.KEEP_DAYS * threads.DAY_MS - 1
        state['threads']['old'] = {'off': True, 'at': old, 'source': 'chat'}
        state['threads']['bad'] = {'off': 'yes', 'at': self.NOW, 'source': 'chat'}
        threads.merge(state, 'c', {'off': True, 'at': self.NOW, 'source': 'chat'}, self.NOW)
        self.assertEqual(set(state['threads']), {'a', 'b', 'c'})
        many = {}
        for i in range(threads.MOST + 5):
            threads.merge(many, f's{i}', {'off': True, 'at': self.NOW + i, 'source': 'chat'}, self.NOW)
        self.assertEqual(len(many['threads']), threads.MOST)
        self.assertNotIn('s4', many['threads'])
        self.assertIn('s5', many['threads'])
        self.assertEqual(threads.later(None, {'off': True, 'at': 2, 'source': 'x'}, {'off': False, 'at': 1, 'source': 'x'}),
                         {'off': True, 'at': 2, 'source': 'x'})
        self.assertIsNone(threads.entry(state, 'missing'))
        self.assertIsNone(threads.entry(state, 'a b'))

    def test_the_note_is_plain_english_and_fits_codex(self):
        japanese = re.compile(f'[{fwlib.KANJI}{fwlib.KANA}]')
        self.assertFalse(japanese.search(threads.note()) or japanese.search(threads.note(codex=True)))
        self.assertIn('/japanese-weave:intensity normal here', threads.note())
        self.assertNotIn('/japanese-weave', threads.note(codex=True))  # Codex has no plugin commands
        self.assertIn('"japanese back on"', threads.note(codex=True))

    def test_thread_ops_are_checked(self):
        good = hub.stamp({'kind': 'thread', 'session': 'a1-b2', 'off': True, 'source': 'chat'})
        self.assertIsNone(hub.validate(good))
        for bad in (dict(good, session=''), dict(good, session='has space'), dict(good, session='x' * 101),
                    dict(good, session='a1\n'),
                    dict(good, off=1), dict(good, source='x' * 21), {k: v for k, v in good.items() if k != 'off'}):
            self.assertIn('thread:', hub.validate(bad))


class Hook(Home):
    def setUp(self):
        super().setUp()
        state = fwlib.new_state(known_top=40)
        state['exported'] = {'at': time.time() - 8 * 86400, 'version': 'old', 'target': 'claude-ai'}  # a stale export
        fwlib.save_state(state)

    def test_asking_turns_this_chat_off_at_once_and_back_on(self):
        english = 'The build passes and the cache is warm, so nothing else needs doing before the release goes out. ' * 3
        log = self.home / 'chat.jsonl'
        log.write_text(line(english) + '\n')
        context, out = self.hook('no japanese for whole thread plz', transcript_path=str(log))
        self.assertEqual(context, threads.note())  # this very turn: no brief, no recall, no calibration
        self.assertNotIn('systemMessage', out)  # and no export notice
        context, _ = self.hook('Prove that the square root of 2 is irrational.', transcript_path=str(log))
        self.assertEqual(context, threads.note())
        context, out = self.hook('Prove it again.', session='s2', transcript_path=str(log))  # another chat
        self.assertIn('Japanese Weave (Stage', context)
        self.assertIn('under budget', context)
        self.assertIn('systemMessage', out)
        self.assertEqual(self.hook('no japanese please')[0], threads.note())  # already off: nothing new to log
        context, _ = self.hook('japanese back on', transcript_path=str(log))
        self.assertIn('Japanese Weave (Stage', context)
        self.assertIn('Recall:', context)
        self.assertEqual(self.thread_ops(), [('s1', True, 'chat'), ('s1', False, 'chat')])
        self.assertEqual(threads.entry(fwlib.load_state(), 's1')['off'], False)

    def test_the_command_through_the_hook_and_fw(self):
        self.assertEqual(self.hook('/japanese-weave:intensity off here')[0], threads.note())
        out = self.fw('intensity', 'off', 'here', '--session', 's1')  # the command's own run: already off, no new op
        self.assertIn('This chat: no Japanese until you turn it back on', out)
        self.assertEqual(self.thread_ops(), [('s1', True, 'command')])
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 's1'}):
            self.assertIn('Japanese on, like other chats (chat replies: normal)', self.fw('intensity', 'normal', 'here'))
            self.assertIn('This chat: Japanese on', self.fw('intensity', 'here'))
        self.assertIn('Japanese Weave (Stage', self.hook('Next question.')[0])
        self.assertIn('no Japanese until', self.fw('intensity', 'off', 'here', '--session', 's9'))
        self.assertEqual(self.hook('Next question.', session='s9')[0], threads.note())
        self.assertEqual(self.thread_ops(), [('s1', True, 'command'), ('s1', False, 'command'), ('s9', True, 'command')])
        with mock.patch('sys.stderr', new=io.StringIO()):
            for args in (['intensity', 'off', 'here'], ['intensity', 'off', 'normal', 'here', '--session', 's1'],
                         ['intensity', 'browser', 'off', 'here', '--session', 's1'], ['intensity', 'off', 'full'],
                         ['intensity', 'full', 'here', '--session', 's1']):
                with self.assertRaises(SystemExit, msg=args):
                    self.fw(*args)

    def test_codex_gets_the_note_without_the_command(self):
        context, _ = self.hook('english only from now on', session='codex-thread-1', turn_id='t1')
        self.assertEqual(context, threads.note(codex=True))

    def test_without_a_session_nothing_is_kept(self):
        context, _ = self.hook('no japanese please', session=None)
        self.assertIn('Japanese Weave (Stage', context)
        self.assertEqual(self.thread_ops(), [])


class Unreachable(Home):
    """A collector that is down: the change waits in the outbox, and this machine honors it at once."""

    def test_a_change_here_holds_while_the_collector_is_down(self):
        fwlib.save_state(fwlib.new_state(known_top=40))  # the cache from an earlier sync
        (fwlib.home() / 'collector').write_text('http://127.0.0.1:9\n')  # nothing listens there
        self.assertEqual(self.hook('english only from now on')[0], threads.note())
        self.assertEqual([(op['kind'], op['session'], op['off']) for op in hub.pending()], [('thread', 's1', True)])
        started = time.time()
        self.assertEqual(self.hook('What is a Banach space?')[0], threads.note())
        self.assertLess(time.time() - started, 3)
        out = self.fw('intensity', 'normal', 'here', '--session', 's1')
        self.assertIn('Note: collector unreachable; queued.', out)
        self.assertIn('Japanese Weave (Stage', self.hook('And a Hilbert space?')[0])
        self.assertEqual([op['off'] for op in hub.pending()], [True, False])


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

    def served(self, session):
        with fwlib.using_home(self.server_home):
            return threads.entry(fwlib.load_state(create=False), session)

    def elsewhere(self, session, off, at):
        """A change to a chat sent by another machine."""
        op = hub.stamp({'kind': 'thread', 'session': session, 'off': off, 'source': 'chat', 'at': at,
                        'machine': 'laptop'})
        reply = feedback.request(self.url + '/v1/ops', {'machine': 'laptop', 'ops': [op]})
        self.assertTrue(reply['results'][0]['ok'], reply)

    def test_the_collector_keeps_every_change_merged_per_chat(self):
        self.assertEqual(self.hook('no japanese for whole thread plz')[0], threads.note())
        self.assertIsNone(self.served('s1'))  # the hook never waits: queued, sent by the next sync
        self.assertEqual(self.hook('Next.')[0], threads.note())  # the collector has nothing newer: the cache holds
        self.assertEqual(hub.sync(), (True, None))
        self.assertTrue(self.served('s1')['off'])
        reply = feedback.request(self.url + '/v1/intensity?surface=chat&session=s1')
        self.assertEqual((reply['intensity']['level'], reply['thread']['off']), (0, True))
        self.assertNotIn('thread', feedback.request(self.url + '/v1/intensity?surface=chat'))
        self.elsewhere('s1', False, int(time.time() * 1000) + 1000)  # turned back on from another machine
        self.assertIn('Japanese Weave (Stage', self.hook('Next.')[0])  # asked of the collector: the cache is behind
        self.elsewhere('s1', True, int(time.time() * 1000) - 60_000)  # an older change arriving late changes nothing
        self.elsewhere('s7', True, int(time.time() * 1000))  # another chat, another machine
        self.assertFalse(self.served('s1')['off'])
        self.assertTrue(self.served('s7')['off'])
        self.assertEqual(self.hook('Next.', session='s7')[0], threads.note())
        changes = [c for c in feedback.intensity_changes(self.db) if c['surface'] == 'thread']
        self.assertEqual([(c['session'], c['off'], c['machine']) for c in changes],
                         [('s1', True, 'laptop'), ('s1', True, socket.gethostname()[:64]), ('s7', True, 'laptop'),
                          ('s1', False, 'laptop')])
        report = knowledge.calibrate([], {}, feedback.intensity_changes(self.db), int(time.time() * 1000) + 5000)
        self.assertEqual((report['presses']['chat']['lighter'], report['presses']['chat']['threadsOff']), (2, 2))

    def test_the_command_sends_at_once(self):
        self.assertIn('no Japanese until', self.fw('intensity', 'off', 'here', '--session', 's3'))
        self.assertTrue(self.served('s3')['off'])
        self.assertEqual(hub.pending(), [])
        self.assertEqual(self.hook('Next.', session='s3')[0], threads.note())


class Calibration(unittest.TestCase):
    NOW = 1_800_000_000_000

    def test_a_burst_of_presses_is_one_decision(self):
        hour, second = 3_600_000, 1000
        chat = lambda at, level: {'at': at, 'surface': 'chat', 'level': level, 'until': at + 3 * hour if level else 0,
                                  'source': 't3', 'machine': 'm'}
        trying = [chat(self.NOW - 5 * hour + i * second, level) for i, level in enumerate([1, 0, 1, 2, 2, 1, 0])]
        overshoot = [chat(self.NOW - 2 * hour, -1), chat(self.NOW - 2 * hour + 5 * second, -2),
                     chat(self.NOW - 2 * hour + 9 * second, -1)]
        report = knowledge.calibrate([], {}, trying + overshoot, self.NOW)
        self.assertEqual((report['presses']['chat']['lighter'], report['presses']['chat']['heavier']), (1, 0))
        expired = [chat(self.NOW - 10 * hour, 1), chat(self.NOW - hour, 1)]  # the first ran out: up again from normal
        report = knowledge.calibrate([], {}, expired, self.NOW)
        self.assertEqual(report['presses']['chat']['heavier'], 2)

    def test_chats_turned_off_count_as_lighter_presses(self):
        hour = 3_600_000
        chat = lambda at, level: {'at': at, 'surface': 'chat', 'level': level, 'until': at + 3 * hour, 'source': 't3',
                                  'machine': 'm'}
        thread = lambda at, session, off: {'at': at, 'surface': 'thread', 'session': session, 'off': off,
                                           'source': 'chat', 'machine': 'm'}
        changes = [thread(self.NOW - 40 * 24 * hour, 'old', True),  # before the window
                   chat(self.NOW - 5 * hour, -1), thread(self.NOW - 4 * hour, 'a', True),
                   thread(self.NOW - 3 * hour, 'a', False), thread(self.NOW - 2 * hour, 'a', True),
                   thread(self.NOW - hour, 'b', True), thread(self.NOW - hour, 'c', False)]
        report = knowledge.calibrate([], {}, changes, self.NOW)
        self.assertEqual({k: report['presses']['chat'][k] for k in ('lighter', 'heavier', 'threadsOff')},
                         {'lighter': 3, 'heavier': 0, 'threadsOff': 2})
        self.assertEqual(report['presses']['browser']['lighter'], 0)
        self.assertNotIn('threadsOff', report['presses']['browser'])
        text = knowledge.calibration_text(report)
        self.assertIn('Chat replies: 3 lighter and 0 heavier presses (3.0 h at -1), counting 2 chats with Japanese '
                      'turned off.', text)
        self.assertNotIn('turned off', text.split('Web pages')[1])


if __name__ == '__main__':
    unittest.main()
