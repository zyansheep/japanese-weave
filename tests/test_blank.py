"""Offline tests for blank passages: web passages planned to get Japanese that got none (no candidates, or thinned to
none), logged as shown and on screen. Run: python -m unittest tests/test_blank.py"""
import io
import math
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
import fw_host  # noqa: E402
import fw  # noqa: E402
import feedback  # noqa: E402
import collector  # noqa: E402
import knowledge  # noqa: E402


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


def plan(jitter=1, limit=0, candidates=0, level=0):
    return {'level': level, 'intensity': 1, 'jitter': jitter, 'limit': limit, 'candidates': candidates}


def records(context, notes, at, plan_, words=40, stays=(1500,)):
    """What the extension sends for one passage on screen: its shown record, then one visible record per stay."""
    where = {'at': at, 'view': 'v1', 'context': context, 'box': [0, 0, 500, 40], 'annotations': notes}
    shown = dict(where, kind='shown', words=words, plan=plan_)
    return [shown] + [dict(where, kind='visible', y=0, height=40, ms=ms, wall=ms + 500, away=500, media=0, idle=0,
                           uid=f'v1-{context}-{i}') for i, ms in enumerate(stays)]


class BlankStore(Home):
    def test_the_host_stores_blank_passages_and_counts_them_apart(self):
        now = int(time.time() * 1000)
        items = (records('glossed', [['問題', 4, 0]], now - 5000, plan(1, 1, 1), words=40, stays=(2000,))
                 + records('none', [], now - 4000, plan(), words=30, stays=(3000, 1500))
                 + records('thinned', [], now - 3000, plan(0.6, 0, 1), words=20, stays=(60_000,)))
        self.assertEqual(fw_host.handle({'type': 'log', 'items': items}),
                         {'ok': True, 'stored': 7, 'duplicates': 0, 'rejected': 0})
        rows, contexts = feedback.load()
        self.assertEqual(rows, [])  # nothing opened
        none, thinned = contexts[('browser', 'v1', 'none')], contexts[('browser', 'v1', 'thinned')]
        self.assertEqual((none['annotations'], none['words'], none['plan'], none['active']), ([], 30, plan(), 4500))
        self.assertEqual((thinned['plan']['jitter'], thinned['plan']['candidates'], thinned['active']), (0.6, 1, 60_000))
        self.assertTrue(knowledge.is_showing(none))
        # The popup's and `fw.py log`'s totals: words shown and reading time in passages with Japanese, blank ones apart.
        totals = fw_host.handle({'type': 'stats', 'since': now - 60_000})
        self.assertEqual((totals['shownWords'], totals['blankPassages'], totals['readingMs']),
                         ([40, 40], [2, 2], [2000, 2000]))
        self.assertEqual(totals['counts'], {'browser:visible': [4, 4]})
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(['log'])
        self.assertIn('40 words shown on pages (40 today), and 2 passages without Japanese (2 today); '
                      '0 min reading on screen (0 today).', out.getvalue())
        self.assertIn('on screen 60s of 60s, a paragraph with no Japanese', out.getvalue())
        self.assertIn('on screen 2s of 2s, a paragraph with 1 glossed word', out.getvalue())

    def test_a_blank_shown_record_still_needs_a_good_plan_and_word_count(self):
        now = int(time.time() * 1000)
        shown = records('none', [], now - 1000, plan())[0]
        bad = [dict(shown, words=0), dict(shown, plan=dict(plan(), jitter=0)), dict(shown, plan=dict(plan(), limit=-1)),
               dict(shown, plan=dict(plan(), text='page text'))]
        self.assertEqual(feedback.record([shown] + bad, 'browser'), {'stored': 1, 'duplicates': 0, 'rejected': 4})


class BlankCollector(Home):
    """A collector on a random local port; this machine's native host forwards to it."""

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

    def test_the_collector_takes_blank_passages_from_the_host(self):
        fwlib.home().mkdir(parents=True, exist_ok=True)
        (fwlib.home() / 'collector').write_text(self.url + '\n')
        now = int(time.time() * 1000)
        items = records('thinned', [], now - 2000, plan(0.5, 0, 1), words=25, stays=(1200,))
        self.assertEqual(fw_host.handle({'type': 'log', 'items': items}),
                         {'ok': True, 'stored': 2, 'duplicates': 0, 'rejected': 0})
        _, contexts = feedback.load(self.db)
        c = contexts[('browser', 'v1', 'thinned')]
        self.assertEqual((c['annotations'], c['plan'], c['active']), ([], plan(0.5, 0, 1), 1200))
        totals = fw_host.handle({'type': 'stats', 'since': now - 60_000})
        self.assertEqual((totals['ok'], totals['shownWords'], totals['blankPassages'], totals['readingMs']),
                         (True, [0, 0], [1, 1], [0, 0]))
        self.assertFalse(feedback.default_path().exists())  # nothing stored on this machine


class BlankKnowledge(Home):
    NOW = 1_800_000_000_000

    def showing(self, contexts, name, japanese, active, words=60, plan_=None, at=None):
        """A browser passage shown with `japanese` glossed words in it and `active` ms of reading time on screen."""
        contexts[('browser', name, 'c')] = {
            'surface': 'browser', 'view': name, 'context': 'c', 'annotations': [[f'w{i}', 0, 10 * i] for i in range(japanese)],
            'words': words, 'shown_at': at or self.NOW - 3_600_000, 'height': 80, 'active': active,
            'seen_until': at or self.NOW - 3_600_000, 'plan': plan_}

    def thinning(self, offered_share, thinned_share, count=30):
        """count untouched and count thinned passages with candidates, at these shares of 60 words, plus blank ones
        without candidates; the reader slows 2% per point of Japanese."""
        contexts = {}
        for e in range(count):
            for name, share, jitter in (('u', offered_share, 1), ('t', thinned_share, 0.5)):
                japanese = round(share * 60)
                ms_per_word = 300 * math.exp(2 * japanese / 60)
                self.showing(contexts, f'{name}{e}', japanese, ms_per_word * 60, plan_=plan(jitter, japanese, 3))
            self.showing(contexts, f'n{e}', 0, 300 * 60, plan_=plan(1, 0, 0))
        return contexts

    def test_blank_passages_get_their_own_bin(self):
        contexts = {}
        for e in range(5):
            self.showing(contexts, f'none{e}', 0, 18_000, plan_=plan())
            self.showing(contexts, f'thin{e}', 0, 18_000, plan_=plan(0.6, 0, 1))  # thinned to none
            self.showing(contexts, f'one{e}', 1, 20_000, plan_=plan(1, 1, 1))  # 1 in 60: 1.7%
            self.showing(contexts, f'four{e}', 4, 24_000, plan_=plan(1, 4, 4))  # 6.7%
        report = knowledge.calibrate([], contexts, [], self.NOW)
        bins = report['bins']
        self.assertEqual((bins[0]['low'], bins[0]['high'], bins[0]['n'], bins[0]['share']), (0.0, 0.0, 10, 0.0))
        self.assertEqual((bins[1]['low'], bins[1]['high'], bins[1]['n']), (0.0, 0.03, 5))  # no blank ones in it
        self.assertEqual((bins[0]['msPerWord'], bins[1]['msPerWord']), (300, 333))
        self.assertEqual([b['n'] for b in bins[2:]], [0, 5, 0, 0])
        self.assertEqual(sum(b['n'] for b in bins), report['passages'])
        text = knowledge.calibration_text(report)
        self.assertIn('\n         no Japanese:   10 passages,  300 ms on screen per word', text)
        self.assertIn('\n     0-  3% Japanese:    5 passages,  333 ms on screen per word', text)

    def test_thinning_is_compared_among_passages_with_candidates(self):
        contexts = self.thinning(0.05, 0.02)
        for e in range(10):  # thinned all the way to none: now on record, in the thinned group
            self.showing(contexts, f'z{e}', 0, 300 * 60, plan_=plan(0.5, 0, 1))
        report = knowledge.calibrate([], contexts, [], self.NOW)
        j = report['jitter']
        self.assertEqual((j['thinned']['n'], j['untouched']['n']), (40, 30))  # the 30 without candidates left out
        self.assertAlmostEqual(j['gap'], 0.05 - 0.75 * 0.0167, delta=0.001)
        self.assertAlmostEqual(j['effect'], 2.0, delta=0.05)
        self.assertIn('Each extra point of Japanese changes screen time per word by about +2.0%.',
                      knowledge.calibration_text(report))
        self.assertEqual((report['passages'], report['bins'][0]['n']), (100, 40))  # still in the bins

    def test_calibration_text_says_when_the_gap_is_too_small(self):
        contexts = self.thinning(0.05, 0.05, count=25)
        for e in range(2):  # two of the 25 thinned passages lost a word: 0.13 points less Japanese on average
            self.showing(contexts, f't{e}', 2, 300 * math.exp(4 / 60) * 60, plan_=plan(0.5, 2, 3))
        report = knowledge.calibrate([], contexts, [], self.NOW)
        self.assertEqual((report['jitter']['gap'], report['jitter']['effect']), (0.0013, None))
        text = knowledge.calibration_text(report)
        self.assertNotIn('Not enough', text)
        self.assertIn('\n  Thinned passages have only 0.1 points less Japanese than untouched ones: too close to measure '
                      'the effect (0.5 needed).\n', text)
        report = knowledge.calibrate([], self.thinning(0.05, 0.0667, count=25), [], self.NOW)
        self.assertIn("\n  Thinned passages have no less Japanese than untouched ones (1.7 points more): the effect can't "
                      'be measured yet.\n', knowledge.calibration_text(report))
        few = knowledge.calibrate([], self.thinning(0.05, 0.02, count=10), [], self.NOW)
        self.assertEqual(few['jitter']['gap'], None)
        self.assertIn('  Not enough of either yet to measure the effect (20 each).', knowledge.calibration_text(few))
        old = dict(few, jitter={k: v for k, v in few['jitter'].items() if k != 'gap'})  # knowledge.json from before
        self.assertIn('Not enough of either yet', knowledge.calibration_text(old))

    def test_blank_passages_leave_the_dial_and_the_word_estimates_alone(self):
        contexts, rows = {}, []
        for e in range(4):
            self.showing(contexts, f'g{e}', 2, 30_000, words=50, at=self.NOW - 7_200_000 + e * 60_000)
        rows.append({'id': 1, 'at': self.NOW - 7_000_000, 'surface': 'browser', 'kind': 'peek', 'word': 'w0',
                     'dwell_ms': 1500, 'view': 'g0', 'context': 'c', 'position': 0, 'uid': None})
        listed = {'w0', 'w1'}
        before = (knowledge.peek_rate(rows, contexts, self.NOW), knowledge.replay(rows, contexts, listed, {}, [], self.NOW))
        losses = []
        knowledge.replay(rows, contexts, listed, {}, [], self.NOW, losses=losses)
        for e in range(20):
            self.showing(contexts, f'b{e}', 0, 30_000, words=80, at=self.NOW - 7_100_000 + e * 60_000, plan_=plan())
        self.assertEqual(knowledge.peek_rate(rows, contexts, self.NOW), before[0])
        self.assertEqual(before[0], (0.5, 200, 1))
        words, offset = knowledge.replay(rows, contexts, listed, {}, [], self.NOW)
        self.assertEqual({ja: (w.k, w.n, w.obs) for ja, w in words.items()},
                         {ja: (w.k, w.n, w.obs) for ja, w in before[1][0].items()})
        self.assertEqual(offset, before[1][1])
        blank_showings = [s for s in knowledge.showings(rows, contexts, self.NOW) if not s[2]['annotations']]
        self.assertEqual(len(blank_showings), 20)
        self.assertEqual([knowledge.observations('browser', s[2], s[3], listed, lambda y, line: 1.0)
                          for s in blank_showings], [[]] * 20)  # no words there: no observations
        after = []
        knowledge.replay(rows, contexts, listed, {}, [], self.NOW, losses=after)
        self.assertEqual(after, losses)
        self.assertEqual(knowledge.tune(rows, contexts, listed, {}, [], self.NOW)['words'], len(losses))


if __name__ == '__main__':
    unittest.main()
