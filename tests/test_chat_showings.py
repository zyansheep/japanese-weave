"""Offline tests for T3 messages replayed whole: messages with a word count count opened or not, by the chance their
paragraphs' stays on screen say each word was read. Run: python -m unittest tests/test_chat_showings.py"""
import math
import os
from pathlib import Path
import sqlite3
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
import feedback  # noqa: E402
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


NOW = 1_800_000_000_000
LINE = 24
A, B = (0, 72), (88, 48)  # a message's two paragraphs (band y, height), 24 px a line: A has 3 lines, B 2
# 今 and 問題 on A's first two lines, 多分 on its last, 中 and でも in B
NOTES = [['今', 0, 3], ['問題', 40, 27], ['多分', 0, 51], ['中', 0, 91], ['でも', 0, 115]]
LISTED = {'今', '問題', '多分', '中', 'でも'}


def stay(band, ms, words=None):
    return {'y': band[0], 'height': band[1], 'ms': ms, 'words': words}


def message(contexts, rows, view, at, stays=(), notes=NOTES, words=120, shown=True, opened=(), dwell=1500, kind='pin'):
    """One T3 message as feedback.load gives it: shown at `at` (or backfilled: a word count and no shown time), its
    stays on screen, and opens of the given indexes."""
    contexts[('t3', view, 'c')] = {'surface': 't3', 'view': view, 'context': 'c', 'annotations': notes, 'words': words,
                                   'shown_at': at if shown else None, 'height': 136, 'plan': None,
                                   'active': sum(s['ms'] for s in stays), 'seen_until': at + 500 if stays else None,
                                   'stays': list(stays)}
    for i in opened:
        rows.append({'id': len(rows) + 1, 'at': at + 1000 + i, 'surface': 't3', 'kind': kind, 'word': notes[i][0],
                     'dwell_ms': dwell, 'view': view, 'context': 'c', 'position': i, 'uid': None})


class StayChance(unittest.TestCase):
    def test_a_word_is_read_by_the_stays_of_its_paragraph(self):
        c = {'words': 120, 'stays': [stay(A, 20_000, 30), stay(A, 10_000, 30)]}
        self.assertAlmostEqual(knowledge.stay_chance(c, 3, LINE), knowledge.READ_MAX, delta=0.01)
        self.assertEqual(knowledge.stay_chance(c, 91, LINE), 0.0)  # B was never on screen for a second
        self.assertGreater(knowledge.stay_chance(c, -8, LINE), 0.6)  # its top a little above the band
        self.assertEqual(knowledge.stay_chance(c, -15, LINE), 0.0)
        self.assertEqual(knowledge.stay_chance(c, 72, LINE), 0.0)  # the band's bottom is the next paragraph's
        short = {'words': 120, 'stays': [stay(A, 1500, 30), stay(A, 1500, 30)]}
        self.assertAlmostEqual(knowledge.stay_chance(short, 3, LINE),
                               knowledge.stay_chance(dict(short, stays=[stay(A, 3000, 30)]), 3, LINE))  # stays add up
        self.assertGreater(knowledge.stay_chance(short, 3, LINE), knowledge.stay_chance(short, 51, LINE))
        # the same math as a passage of the paragraph's words and height on screen as long
        self.assertAlmostEqual(knowledge.stay_chance(short, 3, LINE, 250, 0.9),
                               knowledge.read_chance({'words': 30, 'height': 72, 'active': 3000}, 3, LINE, 250, 0.9))

    def test_without_paragraph_words_the_message_words_are_shared_out_by_height(self):
        counted = {'words': 120, 'stays': [stay(A, 3000, 72), stay(B, 1000, 48), stay(B, 1000, 48)]}
        shared = {'words': 120, 'stays': [stay(A, 3000), stay(B, 1000), stay(B, 1000)]}  # 72 and 48 of 120 px
        for y in (3, 51, 91, 115):
            self.assertAlmostEqual(knowledge.stay_chance(shared, y, LINE), knowledge.stay_chance(counted, y, LINE))
        self.assertGreater(knowledge.stay_chance(shared, 3, LINE), 0)


class Replay(unittest.TestCase):
    def test_messages_count_opened_or_not(self):
        rows, contexts = [], {}
        for e in range(20):  # on screen long enough to read paragraph A, never opened; B never on screen
            message(contexts, rows, f'r{e}', NOW - 7_200_000 + e * 60_000, stays=[stay(A, 30_000, 30)])
        words, offset = knowledge.replay(rows, contexts, LISTED, {}, [], NOW)
        self.assertGreater(words['今'].k, 2.0)  # from 1.0: read and not opened
        self.assertGreater(words['多分'].k, 2.0)
        self.assertEqual(words['今'].obs, 20)
        self.assertNotIn('中', words)  # in a paragraph with no stay: no evidence
        self.assertNotIn('でも', words)
        self.assertLess(offset['t3'], 0)  # read and never opened: chat peeks less than the model said
        self.assertEqual(offset['browser'], 0.0)
        self.assertEqual(knowledge.episodes(rows, contexts, NOW), [])
        losses = []
        knowledge.replay(rows, contexts, LISTED, {}, [], NOW, losses=losses)
        self.assertEqual(losses, [])  # tuning stays on browser passages

    def test_an_opened_word_counts_as_opened(self):
        rows, contexts = [], {}
        message(contexts, rows, 'o', NOW - 3_600_000, stays=[stay(A, 30_000, 30)], opened=[3])  # 中, in B: no stay
        words, _ = knowledge.replay(rows, contexts, LISTED, {}, [], NOW)
        self.assertEqual((words['中'].opened, words['中'].obs), (1, 1))
        self.assertLess(words['中'].k, 1.0)
        self.assertEqual(words['今'].opened, 0)
        self.assertGreater(words['今'].k, 1.0)  # above the open, and its paragraph was on screen
        self.assertEqual(words['でも'].history[-1]['w'], 0.5)  # a line below the open, never on screen: the line rule
        self.assertEqual(words['中'].history[-1]['surface'], 't3')
        rows, contexts = [], {}
        message(contexts, rows, 'g', NOW - 3_600_000, opened=[0], dwell=200, kind='peek')  # the mouse passing over
        self.assertEqual(knowledge.replay(rows, contexts, LISTED, {}, [], NOW)[0], {})

    def test_backfilled_messages_are_replayed(self):
        rows, contexts = [], {}
        message(contexts, rows, 'b', NOW - 3_600_000, stays=[stay(A, 30_000), stay(B, 1000)], shown=False)
        at, key, context, _items = knowledge.showings(rows, contexts, NOW)[0]
        self.assertEqual((at, key), (NOW - 3_600_000 + 500, ('t3', 'b', 'c')))  # settled after its last stay
        words, _ = knowledge.replay(rows, contexts, LISTED, {}, [], NOW)
        self.assertGreater(words['今'].k, 1.0)
        self.assertGreater(words['今'].k, words['中'].k)  # B was on screen a second only
        message(contexts, rows, 'n', NOW - 3_000_000, shown=False)  # a word count and nothing else: no time, no evidence
        self.assertEqual(len(knowledge.showings(rows, contexts, NOW)), 1)

    def test_messages_without_a_word_count_are_episodes_as_before(self):
        rows, contexts = [], {}
        two = [['今', 0, 3], ['問題', 40, 3]]
        message(contexts, rows, 'v', NOW - 3_600_000, stays=[stay(A, 30_000, 30)], notes=two, words=None, shown=False,
                opened=[1])
        self.assertEqual(knowledge.showings(rows, contexts, NOW), [])
        self.assertEqual(len(knowledge.episodes(rows, contexts, NOW)), 1)
        words, offset = knowledge.replay(rows, contexts, {'今', '問題'}, {}, [], NOW)
        p = knowledge.sigmoid(-1.0)
        expected = p / (1 - (1 - p) ** 2)  # conditioned on at least one open
        self.assertAlmostEqual(words['今'].k, 1.0 + 1.2 * expected, delta=1e-3)  # its stay changes nothing
        self.assertAlmostEqual(words['問題'].k, 1.0 + 1.2 * (expected - 1), delta=1e-9)
        self.assertAlmostEqual(offset['t3'], knowledge.T3_RATE * (1 - 2 * expected) / 2)

    def test_the_t3_offset_moves_toward_the_data(self):
        pool = [f'w{i}' for i in range(40)]

        def reader(opens):
            rows, contexts = [], {}
            for e in range(200):
                notes = [[pool[(4 * e + j) % 40], 40 * j, 3] for j in range(4)]
                message(contexts, rows, f'v{e}', NOW - 4 * 3_600_000 + e * 60_000, stays=[stay(A, 30_000, 30)],
                        notes=notes, opened=range(4) if opens else ())
            return knowledge.replay(rows, contexts, set(pool), {}, [], NOW)[1]

        eager, never = reader(True), reader(False)
        self.assertGreater(eager['t3'], 0.2)
        self.assertLess(never['t3'], -0.1)
        self.assertLessEqual(max(abs(eager['t3']), abs(never['t3'])), knowledge.T3_LIMIT)
        self.assertEqual((eager['browser'], never['browser']), (0.0, 0.0))


class Calibration(unittest.TestCase):
    def test_chat_lookups_against_the_model(self):
        rows, contexts = [], {}
        message(contexts, rows, 'a', NOW - 3_600_000, stays=[stay(A, 30_000, 30)])
        message(contexts, rows, 'b', NOW - 3_000_000, stays=[stay(A, 30_000, 30), stay(B, 20_000, 20)], opened=[3])
        rows.append(dict(rows[-1], id=9, kind='peek', word='でも', position=4, dwell_ms=200))  # passing over: no lookup
        message(contexts, rows, 'old', NOW - 40 * knowledge.DAY_MS, stays=[stay(A, 30_000, 30)], notes=[['他', 0, 3]],
                opened=[0])  # an unlisted word, before the window
        chat = []
        knowledge.replay(rows, contexts, LISTED, {}, [], NOW, chat=chat)
        self.assertEqual([(m['lookups'], m['observed']) for m in chat], [(1, 0.0), (0, 0.0), (1, 1.0)])
        first = chat[1]  # before any evidence on these words: read chance times the prior's peek chance
        read = [knowledge.stay_chance(contexts[('t3', 'a', 'c')], y, LINE) for _w, _x, y in NOTES]
        self.assertAlmostEqual(first['expected'], sum(read) * knowledge.sigmoid(-1.0))
        report = knowledge.calibrate(rows, contexts, [], NOW, chat=chat)
        c = report['chat']
        self.assertEqual({k: c[k] for k in ('messages', 'words', 'lookups', 'per100', 'none', 'observed')},
                         {'messages': 2, 'words': 240, 'lookups': 1, 'per100': 0.42, 'none': 1, 'observed': 1.0})
        self.assertEqual(c['expected'], round(chat[1]['expected'] + chat[2]['expected'], 1))
        self.assertEqual(report['passages'], 0)  # chat messages stay out of the web bins
        self.assertIn(f'\nChat: 2 messages of 240 words, 1 lookups (0.42 per 100 words), 1 messages with none; '
                      f'listed words: {c["expected"]:.1f} lookups expected, 1.0 seen.\n',
                      knowledge.calibration_text(report))
        self.assertIn('No chat messages with a word count yet.',
                      knowledge.calibration_text(knowledge.calibrate([], {}, [], NOW)))
        old = {k: v for k, v in report.items() if k != 'chat'}  # knowledge.json from before
        self.assertNotIn('Chat:', knowledge.calibration_text(old))


class Store(Home):
    def where(self, at, context='m1'):
        return {'at': at, 'view': 'w1', 'context': context, 'box': [0, 0, 600, 136], 'annotations': NOTES}

    def stays(self, at, context='m1', words=True):
        out = []
        for i, (band, ms, count) in enumerate(((A, 30_000, 30), (A, 5000, 30), (B, 1000, 20))):
            item = dict(self.where(at + i, context), kind='visible', y=band[0], height=band[1], ms=ms, wall=ms + 500,
                        away=500, media=0, idle=0, uid=f'w1-{context}-{i}')
            out.append(dict(item, words=count) if words else item)
        return out

    def test_stays_keep_their_paragraph_words(self):
        now = int(time.time() * 1000)
        shown = dict(self.where(now - 3_600_000), kind='shown', words=120)
        stays = self.stays(now - 3_590_000)
        bad = [dict(stays[0], words=True, uid='x1'), dict(stays[0], words=-1, uid='x2'),
               dict(stays[0], words=10_001, uid='x3'), dict(stays[0], words=2.5, uid='x4')]
        self.assertEqual(feedback.record([shown] + stays + bad, 't3'), {'stored': 4, 'duplicates': 0, 'rejected': 4})
        self.assertEqual(feedback.record([dict(stays[0], words=0, uid='x5', at=now - 3_500_000)], 't3')['stored'], 1)
        visible = [r for r in feedback.recent() if r['kind'] == 'visible']
        self.assertIn({'wall': 30_500, 'away': 500, 'media': 0, 'idle': 0, 'words': 30}, [r['detail'] for r in visible])
        _, contexts = feedback.load()
        c = contexts[('t3', 'w1', 'm1')]
        self.assertEqual((c['words'], c['shown_at'], c['active']), (120, now - 3_600_000, 66_000))
        self.assertEqual(c['stays'], [stay(A, 30_000, 30), stay(A, 5000, 30), stay(B, 1000, 20), stay(A, 30_000, 0)])
        self.assertTrue(knowledge.is_showing(c))

    def test_logged_messages_feed_the_replay_and_backfills_join_it(self):
        now = int(time.time() * 1000)
        old = self.stays(now - 3_000_000, 'm2', words=False)  # before T3 sent word counts: an open, stays without
        peek = dict(self.where(now - 2_990_000, 'm2'), kind='peek', word='今', position=0, dwellMs=1500, uid='p1')
        feedback.record(old + [peek], 't3')
        rows, contexts = feedback.load()
        self.assertFalse(knowledge.is_showing(contexts[('t3', 'w1', 'm2')]))
        self.assertEqual(len(knowledge.episodes(rows, contexts, now)), 1)
        words, _ = knowledge.replay(rows, contexts, LISTED, {}, [], now)
        self.assertEqual(words['多分'].history[-1]['w'], 0.25)  # an episode: two lines below the open, the line rule
        path = feedback.default_path()
        con = sqlite3.connect(str(path))
        with con:
            con.execute("UPDATE contexts SET words = 120 WHERE context = 'm2'")  # the word count from its transcript
        con.close()
        rows, contexts = feedback.load()
        c = contexts[('t3', 'w1', 'm2')]
        self.assertEqual((c['shown_at'], [s['words'] for s in c['stays']]), (None, [None, None, None]))
        self.assertEqual((len(knowledge.showings(rows, contexts, now)), knowledge.episodes(rows, contexts, now)), (1, []))
        words, _ = knowledge.replay(rows, contexts, LISTED, {}, [], now)
        self.assertGreater(words['多分'].history[-1]['w'], 0.5)  # its paragraph was on screen 35 s
        self.assertEqual(words['今'].opened, 1)

    def test_refresh_reports_the_chat_side(self):
        state = fwlib.load_state()
        listed = [w['ja'] for w in state['words'] if w['status'] != 'queue'][:5]
        notes = [[ja, 0, y] for ja, (_w, _x, y) in zip(listed, NOTES)]
        now = time.time()
        at = int(now * 1000) - 3_600_000
        items = [dict(self.where(at), kind='shown', words=120, annotations=notes)]
        items += [dict(s, annotations=notes) for s in self.stays(at + 1000)]
        feedback.record(items, 't3')
        know = knowledge.refresh(now)
        self.assertEqual({k: know['calibration']['chat'][k] for k in ('messages', 'words', 'lookups', 'none')},
                         {'messages': 1, 'words': 120, 'lookups': 0, 'none': 1})
        self.assertGreater(know['calibration']['chat']['expected'], 0)
        self.assertGreater(know['words'][listed[0]]['k'], know['words'][listed[0]]['mu'])
        self.assertLess(know['offset']['t3'], 0)


if __name__ == '__main__':
    unittest.main()
