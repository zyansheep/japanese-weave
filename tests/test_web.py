"""What the native host gives the browser extension's web pipeline (scripts/webdata.py, fw_host.py): each word's
glosses, safe glosses and entry id; the safe glosses of every swappable entry for counting what is read; and the web's
reading counts, into the same daily store as chat's, here or on a collector.
Run: python -m unittest discover -s tests"""
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

import collector  # noqa: E402
import dictionary  # noqa: E402
import fw_host  # noqa: E402
import fwlib  # noqa: E402
import hub  # noqa: E402
import senses  # noqa: E402
import webdata  # noqa: E402


class Home(unittest.TestCase):
    """Each test gets its own state dir; the data files are the plugin's own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.root / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.root / 'no-chats')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def use(self, *words):
        """A state whose words in use are these (ja, en, kana, status, extra fields)."""
        state = fwlib.load_state()
        for ja, en, kana, status, *extra in words:
            w = fwlib.find(state, ja)
            if w is None:
                w = fwlib.blank({'ja': ja, 'en': en, 'hint': '', 'kana': kana})
                state['words'].append(w)
            w.update(en=en, kana=kana, status=status, **(extra[0] if extra else {}))
        fwlib.save_state(state)


class WordData(Home):
    def test_the_state_reply_gives_each_word_its_glosses_safe_glosses_and_entry(self):
        d = dictionary.load()
        rare = next(e for e in d if e['swap'] and e['ja'] not in ('新しい', '時'))  # as intake adds one: with its id
        self.use(('新しい', 'new', 'あたらしい', 'known'), ('時', 'time / when (hour)', 'とき', 'active'),
                 (rare['ja'], rare['en'], rare['kana'], 'active', {'id': rare['id']}))
        reply = fw_host.handle({'type': 'state'})
        words = {w['ja']: w for w in reply['words']}
        self.assertEqual((words['新しい']['glosses'], words['新しい']['safe']), (['new'], ['new']))
        self.assertEqual(words['新しい']['id'], d.find('新しい')['id'])
        self.assertEqual(words['時']['glosses'], ['time', 'when'])  # as the matcher indexes them
        self.assertEqual(words['時']['safe'], [])  # a common trap: the model checks every one
        self.assertEqual(words[rare['ja']]['id'], rare['id'])
        self.assertTrue(all(isinstance(w['glosses'], list) and set(w['safe']) <= set(w['glosses'])
                            for w in words.values()))
        self.assertEqual(reply['reading'], {'version': webdata.reading_version()})
        self.assertLess(len(json.dumps(reply, ensure_ascii=False).encode()), 1024 * 1024)  # a native message's limit

    def test_without_the_flags_file_nothing_is_safe_and_nothing_counts(self):
        self.use(('新しい', 'new', 'あたらしい', 'known'))
        with mock.patch.object(senses, 'PATH', self.root / 'missing.tsv'):
            reply = fw_host.handle({'type': 'state'})
            self.assertTrue(all(w['safe'] == [] for w in reply['words']))
            self.assertEqual(fw_host.handle({'type': 'readingIndex'}),
                             {'ok': False, 'error': 'no dictionary or sense flags here'})


class ReadingIndex(Home):
    def test_the_safe_glosses_of_every_swappable_entry_in_rank_order_versioned(self):
        reply = fw_host.handle({'type': 'readingIndex'})
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['version'], fw_host.handle({'type': 'state'})['reading']['version'])
        d = dictionary.load()
        flags = senses.load()
        rows = reply['entries']
        self.assertGreater(len(rows), 5000)
        ranks = [d.get(id_)['rank'] for id_, _, _ in rows]
        self.assertEqual(ranks, sorted(ranks))
        for id_, pos, glosses in rows[:300]:
            e = d.get(id_)
            self.assertTrue(e['swap'])
            self.assertEqual(pos, ','.join(e['pos']))
            for g in glosses.split(';'):
                self.assertEqual(senses.sense_flag(flags, {'ja': e['ja'], 'kana': e['kana']}, g), 's')
        size = len(json.dumps(reply, ensure_ascii=False).encode())
        self.assertLess(size, 600_000)  # one native message, under its 1 MB
        self.assertNotIn(d.find('する')['id'], {r[0] for r in rows})  # a verb: not swappable, never counted

    def test_the_version_follows_the_files(self):
        before = webdata.reading_version()
        copy = self.root / 'senses.tsv'
        copy.write_text(senses.PATH.read_text(encoding='utf-8'), encoding='utf-8')
        with mock.patch.object(senses, 'PATH', copy):
            self.assertNotEqual(webdata.reading_version(), before)


class Counts(Home):
    def passage(self, d='web:00ff00ff00ff00ff', day=None, **entries):
        return {'d': d, 'day': day or fwlib.today(), 'entries': entries or {'1361110': 1}}

    def test_web_counts_join_chats_in_the_daily_store_once_per_passage(self):
        fwlib.load_state()
        today, yesterday = fwlib.today(), fwlib.add_days(fwlib.today(), -1)
        future = fwlib.add_days(today, 2)
        bad = [self.passage(d='0011'), self.passage(d='web:XYZ'), self.passage(day='2026-02-30'), 'text',
               self.passage(entries={'x': 1}), dict(self.passage(), entries={'1361110': 0}),
               dict(self.passage(), entries={}), dict(self.passage(), entries={'1361110': True}),
               dict(self.passage(), entries={str(n): 1 for n in range(1, fw_host.MAX_READ_ENTRIES + 2)}),
               dict(self.passage(), entries={'²': 1}), dict(self.passage(), entries={'007': 1})]
        reply = fw_host.handle({'type': 'reading', 'passages': [
            dict(self.passage('web:aaaa0000aaaa0000'), entries={'1361110': 2, '1464900': 1}),
            dict(self.passage('web:bbbb0000bbbb0000', yesterday), entries={'1361110': 1}),
            dict(self.passage('web:cccc0000cccc0000', future), entries={'1214000': 1}),  # a clock ahead: today
        ] + bad})
        self.assertEqual(reply, {'ok': True, 'taken': 3})
        again = fw_host.handle({'type': 'reading', 'passages': [self.passage('web:aaaa0000aaaa0000')]})
        self.assertEqual(again, {'ok': True, 'taken': 1})  # sent again (a lost reply, another day): counted once
        index = fwlib.read_json(fwlib.home() / 'exposure.json')
        self.assertEqual(index['reading'], {yesterday: {'1361110': 1},
                                            today: {'1361110': 2, '1464900': 1, '1214000': 1}})
        self.assertEqual(index['readingDays'], {'1361110': 2, '1464900': 1, '1214000': 1})
        self.assertEqual(fw_host.handle({'type': 'reading', 'passages': bad}), {'ok': True, 'taken': 0})
        self.assertFalse(fw_host.handle({'type': 'reading', 'passages': 'nope'})['ok'])
        self.assertNotIn('text', json.dumps(index))  # counts and digests only


class OnTheCollector(Home):
    """The word state on a collector (its own state dir, on a local port); the browser's counts go there."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.server_home = self.root / 'collector-home'
        with fwlib.using_home(self.server_home):
            hub.submit({'kind': 'seed', 'n': 40})
        handler = type('TestHandler', (collector.Handler,), {'db': self.server_home / 'interactions.sqlite',
                                                             'home': self.server_home, 'log_message': lambda *a: None})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        fwlib.home().mkdir(parents=True, exist_ok=True)
        (fwlib.home() / 'collector').write_text(self.url + '\n')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def test_counts_wait_in_the_outbox_then_reach_the_collector_which_checks_them(self):
        today = fwlib.today()
        reply = fw_host.handle({'type': 'reading', 'passages': [
            {'d': 'web:1234abcd1234abcd', 'day': today, 'entries': {'1361110': 3}}]})
        self.assertEqual(reply, {'ok': True, 'taken': 1, 'queued': True})  # the browser never waits on the network
        self.assertEqual([op['kind'] for op in hub.pending()], ['exposure'])
        self.assertEqual(hub.sync(scan=False), (True, None))
        self.assertEqual(hub.pending(), [])
        with fwlib.using_home(self.server_home):
            served = fwlib.read_json(fwlib.home() / 'exposure.json')
        self.assertEqual(served['reading'], {today: {'1361110': 3}})
        self.assertEqual(fwlib.read_json(fwlib.home() / 'exposure.json')['readingDays'], {'1361110': 1})  # the cache
        # the collector checks what arrives, as it does chat's: a malformed op is refused there
        bad = hub.stamp({'kind': 'exposure', 'replies': [{'d': 'web:1', 'day': today, 'words': [],
                                                          'entries': {'not-an-id': 1}}]})
        result = hub.post(self.url, [bad])['results'][0]
        self.assertFalse(result['ok'])
        self.assertIn('exposure', result['error'])


if __name__ == '__main__':
    unittest.main()
