"""Offline tests for gloss opens from the phone app: it knows the word tapped and nothing about lines or read depth, so
each open must count as a lookup of that word and say nothing about any other word. Run:
python -m unittest tests/test_phone.py"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import evaluate  # noqa: E402
import feedback  # noqa: E402
import knowledge  # noqa: E402

DAY = 86_400_000
NOW = 1_800_000_000_000
LISTED = ('mondai', 'moshi', 'tabun')
WORDS = [{'ja': ja, 'kana': '', 'status': 'active', 'added': '2026-01-01', 'changed': '2026-01-01'} for ja in LISTED]
SENTENCE_END = '\u3002'


def opened(word, at, uid, view=None, context='m1'):
    """As apps/mobile/src/lib/glossOpens.ts in the T3 Code fork sends it."""
    return {'kind': 'pin', 'word': word, 'at': at, 'dwellMs': 0, 'view': view or f'phone-s1-{word.encode().hex()[:8]}',
            'context': context, 'position': 0, 'box': [0, 0, 0, 0], 'annotations': [[word, 0, 0]], 'uid': uid}


class PhoneOpens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': self.tmp.name,
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(Path(self.tmp.name) / 'no-chats')})
        self.env.start()
        self.db = Path(self.tmp.name) / 'log.sqlite'

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def record(self, *items):
        return feedback.record(list(items), 't3', self.db, now=NOW)

    def extract(self):
        rows, contexts = feedback.load(self.db)
        return rows, contexts, evaluate.extract(rows, contexts, WORDS, {}, [], NOW)

    def test_stored_once_however_often_it_is_sent(self):
        item = opened('mondai', NOW - DAY, 'phone-s1-1')
        self.assertEqual(self.record(item), {'stored': 1, 'duplicates': 0, 'rejected': 0})
        self.assertEqual(self.record(item), {'stored': 0, 'duplicates': 1, 'rejected': 0})
        self.assertEqual(feedback.count(self.db), 1)

    def test_an_open_is_an_episode_and_a_lookup_of_its_word_alone(self):
        # Two words of one message, then one of them again past the gap that ends an episode.
        self.record(opened('mondai', NOW - DAY, 'phone-s1-1'), opened('moshi', NOW - DAY + 60_000, 'phone-s1-2'),
                    opened('mondai', NOW - DAY + 3_600_000, 'phone-s1-3'))
        rows, contexts, log = self.extract()
        self.assertEqual(knowledge.showings(rows, contexts, NOW), [])
        episodes = knowledge.episodes(rows, contexts, NOW)
        self.assertEqual(len(episodes), 3)
        for (surface, _view, _context), context, items in episodes:
            found = knowledge.observed(surface, context, items, set(LISTED))
            self.assertEqual([(w, y, weight) for w, y, weight, _i, _n in found], [(items[0]['word'], 1.0, 1.0)])
        observations = [o for e in log.events for o in e.obs]
        self.assertEqual(sorted(o.word for o in observations), ['mondai', 'mondai', 'moshi'])
        self.assertTrue(all(o.y == 1.0 and o.episode for o in observations))

    def test_a_list_shared_by_a_view_would_be_negative_evidence(self):
        """Why each word has its own view: a context keeps its latest list, so in the first episode the word opened
        later would read as seen and not looked up."""
        shared = dict(opened('moshi', NOW - DAY + 3_600_000, 'phone-s1-2', view='phone-s1'),
                      annotations=[['mondai', 0, 0], ['moshi', 0, 0]], position=1)
        self.record(opened('mondai', NOW - DAY, 'phone-s1-1', view='phone-s1'), shared)
        _rows, _contexts, log = self.extract()
        self.assertIn(('moshi', 0.0), [(o.word, o.y) for e in log.events for o in e.obs])

    def test_the_models_take_it_and_leave_the_word_where_it_was(self):
        """An episode's likelihood is conditioned on at least one open, and with one word listed that open is certain,
        so opens from the phone never move k, in the fit or online. They do count as the word being read (its clock)."""
        self.record(*[opened(LISTED[i % 3], NOW - 3 * DAY + i * 3_600_000, f'phone-s1-{i}', context=f'm{i}')
                      for i in range(30)])
        rows, contexts, log = self.extract()
        fit = knowledge.Fit(log, NOW)
        self.assertAlmostEqual(fit.estimate('mondai')[0], fit.prior('mondai')[0], places=6)
        words, offset = knowledge.replay(rows, contexts, set(LISTED), {}, [], NOW)
        self.assertEqual(set(words), set(LISTED))
        self.assertEqual(words['mondai'].opened, 10)
        self.assertEqual(offset['t3'], 0.0)
        self.assertLess(knowledge.since_read(*knowledge.last_reads([(NOW - DAY, 1.0, ('t3', 'v'))], NOW), NOW)[0], 1.0)

    def test_it_leaves_the_views_of_the_log_whole(self):
        self.record(opened('mondai', NOW - DAY, 'phone-s1-1'), opened(SENTENCE_END, NOW - DAY + 1000, 'phone-s1-2'),
                    opened('offlist', NOW - DAY + 2000, 'phone-s1-3'))
        rows, contexts, log = self.extract()
        self.assertEqual([o.word for e in log.events for o in e.obs], ['mondai'])
        report = knowledge.calibrate(rows, contexts, [], NOW, totals=None)
        self.assertEqual(report['classes']['t3']['n'], 0)  # no message counted as read
        self.assertEqual(knowledge.unlisted(rows, set(LISTED), NOW)['top'], [['offlist', 1]])
        stats = feedback.stats(0, self.db)
        self.assertEqual(stats['counts']['t3:pin'][0], 3)
        self.assertEqual(stats['shownWords'], [0, 0])
        self.assertEqual(stats['blankPassages'], [0, 0])
        self.assertEqual(stats['readingMs'], [0, 0])
        self.assertEqual({r['word'] for r in feedback.recent(10, self.db)}, {'mondai', SENTENCE_END, 'offlist'})
        self.assertEqual(len(feedback.export(0, 0, 10, self.db)['interactions']), 3)


if __name__ == '__main__':
    unittest.main()
