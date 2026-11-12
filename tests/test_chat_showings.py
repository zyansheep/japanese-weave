"""Offline tests for T3 messages replayed whole: messages with a word count count opened or not, by the chance their
paragraphs' stays on screen say each word was read. Run: python -m unittest tests/test_chat_showings.py"""
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
        # 120 words in a 300 px message, of which A (72 px) and B (48 px) had stays: the rest has no Japanese
        shared = {'words': 120, 'height': 300, 'stays': [stay(A, 3000), stay(B, 1000), stay(B, 1000)]}
        passage = lambda band, ms, words: {'words': words, 'height': band[1], 'active': ms}
        for y, band, ms in ((3, A, 3000), (51, A, 3000), (91, B, 2000), (115, B, 2000)):
            self.assertAlmostEqual(knowledge.stay_chance(shared, y, LINE),
                                   knowledge.read_chance(passage(band, ms, 120 * band[1] / 300), y - band[0], LINE))
        unboxed = dict(shared, height=None)  # no message height: the bands on record, 72 and 48 of 120 px
        self.assertAlmostEqual(knowledge.stay_chance(unboxed, 3, LINE),
                               knowledge.read_chance(passage(A, 3000, 72), 3, LINE))
        tiny = {'words': 10, 'height': 3000, 'stays': [stay(A, 3000)]}  # 0.24 words: at least 1
        self.assertAlmostEqual(knowledge.stay_chance(tiny, 3, LINE), knowledge.read_chance(passage(A, 3000, 1), 3, LINE))
        self.assertEqual(knowledge.stay_chance(dict(shared, words=0), 3, LINE), 0.0)  # no words to share out


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
    def test_chat_lookups_against_the_model_per_class(self):
        rows, contexts = [], {}
        message(contexts, rows, 'a', NOW - 3_600_000, stays=[stay(A, 30_000, 30)])
        message(contexts, rows, 'b', NOW - 3_000_000, stays=[stay(A, 30_000, 30), stay(B, 20_000, 20)], opened=[3])
        rows.append(dict(rows[-1], id=9, kind='peek', word='でも', position=4, dwell_ms=200))  # passing over: no lookup
        message(contexts, rows, 'old', NOW - 40 * knowledge.DAY_MS, stays=[stay(A, 30_000, 30)], notes=[['他', 0, 3]],
                opened=[0])  # an unlisted word, before the window
        shown = []
        knowledge.replay(rows, contexts, LISTED, {}, [], NOW, shown=shown)
        self.assertEqual([(m['lookups'], m['classes']['costly'][1], m['classes']['offlist'][1]) for m in shown],
                         [(1, 0.0, 1.0), (0, 0.0, 0.0), (1, 1.0, 0.0)])
        first = shown[1]  # before any evidence on these words: read chance times the prior's peek chance, all costly
        read = [knowledge.stay_chance(contexts[('t3', 'a', 'c')], y, LINE) for _w, _x, y in NOTES]
        self.assertAlmostEqual(first['classes']['costly'][0], sum(read) * knowledge.sigmoid(-1.0))
        self.assertEqual(first['classes']['free'], [0.0, 0.0])
        self.assertAlmostEqual(first['read'], knowledge.words_read('t3', contexts[('t3', 'a', 'c')]))
        report = knowledge.calibrate(rows, contexts, [], NOW, shown=shown)
        chat = report['classes']['t3']
        self.assertEqual({k: chat[k] for k in ('n', 'words', 'lookups', 'none', 'days', 'budget')},
                         {'n': 2, 'words': 240, 'lookups': 1, 'none': 1, 'days': 1, 'budget': 0.5})
        read = shown[1]['read'] + shown[2]['read']
        self.assertEqual(chat['read'], round(read))
        costly = chat['classes']['costly']
        self.assertEqual(costly['seen'], round(knowledge.per_100(1.0, read), 4))
        self.assertIsNone(costly['interval'])  # one day: too few to resample
        self.assertEqual(report['classes']['browser']['n'], 0)
        self.assertEqual(report['passages'], 0)  # chat messages stay out of the web bins
        text = knowledge.calibration_text(report)
        self.assertIn(f'\n  Chat: 2 messages (1 with no lookup) over 1 days, 240 words shown, {round(read)} read, 1 '
                      'lookups; budget 0.50:\n', text)
        self.assertIn(f'\n    costly words:    {costly["expected"]:.3f} expected, {costly["seen"]:.3f} seen: '
                      f'{costly["ratio"]:.2f} (no interval before 5 days)\n', text)
        self.assertIn('\n  Web: no passages with Japanese yet.\n', text)
        self.assertIn('    topical words:   none yet', text)
        old = {k: v for k, v in report.items() if k != 'classes'}  # knowledge.json from before
        self.assertNotIn('Lookups per 100 words read', knowledge.calibration_text(old))

    def test_a_word_offered_as_topical_in_the_hour_before_is_topical(self):
        # 問題 a listed word (a queue word offered as topical), 燃料 a dictionary word, 食べました a form of an offered
        # 食べる; 多分 and 中 listed, not offered; 他 off the list, offered too long before
        rows, contexts = [], {}
        notes = [['問題', 0, 3], ['燃料', 40, 27], ['食べました', 0, 51], ['多分', 0, 91], ['他', 0, 115]]
        at = NOW - 3_600_000
        message(contexts, rows, 'a', at, notes=notes, stays=[stay(A, 30_000, 30), stay(B, 20_000, 20)],
                opened=[1, 4])
        briefs = [{'at': at - 2 * 3_600_000, 'topical': [['他', 0.5]]},  # over the hour before: not this message's
                  {'at': at - 20 * 60_000, 'topical': [['問題', 0.2], ['燃料', 0.3], ['食べる', 0.25]]},
                  {'at': at - 10 * 60_000, 'topical': [['燃料', 0.4], 'bad', ['x', 'y']]},  # the latest price counts
                  {'at': at + 60_000, 'topical': [['多分', 0.3]]}]  # after it was shown
        offers = knowledge.Offers(briefs)
        self.assertEqual(offers.price({'燃料'}, at), ('燃料', 0.4))
        self.assertIsNone(offers.price({'他'}, at))
        self.assertIsNone(offers.price({'多分'}, at))
        self.assertTrue(offers.active(at) and not offers.active(at - 3 * 3_600_000))

        class Model:
            chance = staticmethod(lambda word, surface, _at: 0.1)

        context = contexts[('t3', 'a', 'c')]
        found = knowledge.shown_record(at, 't3', context, rows, Model(), lambda w: w if w in {'問題', '多分'} else None,
                                       offers=offers)['classes']
        read = {y: knowledge.stay_chance(context, y, LINE) for _w, _x, y in notes}
        self.assertAlmostEqual(found['topical'][0], read[3] * 0.2 + read[27] * 0.4 + read[51] * 0.25)
        self.assertEqual(found['topical'][1], 1.0)  # 燃料 opened
        self.assertEqual(found['costly'], [read[91] * 0.1, 0.0])  # 多分 alone: 問題 is topical
        self.assertEqual(found['offlist'], [read[115] * 0.1, 1.0])  # 他, opened
        without = knowledge.shown_record(at, 't3', context, rows, Model(), lambda w: w if w in {'問題', '多分'} else None)
        self.assertEqual(without['classes']['topical'], [0.0, 0.0])  # no briefs: as before
        web = dict(context, surface='browser')
        self.assertEqual(knowledge.shown_record(at, 'browser', web, rows, Model(), lambda w: None,
                                                offers=offers)['classes']['topical'], [0.0, 0.0])  # chat only
        report = knowledge.lookup_classes([knowledge.shown_record(at, 't3', context, rows, Model(),
                                                                  lambda w: w if w in {'問題', '多分'} else None,
                                                                  offers=offers)])
        text = '\n'.join(knowledge.classes_text(report, 30))
        self.assertIn('    topical words:   ', text)
        self.assertNotIn('topical words:   none yet', text)

    def test_sentence_translations_and_off_list_words_are_classes_of_their_own(self):
        rows, contexts = [], {}
        notes = NOTES[:3] + [['燃料', 0, 51], ['。', 60, 51]]
        message(contexts, rows, 'a', NOW - 3_600_000, notes=notes, stays=[stay(A, 30_000, 30)], opened=[3, 4],
                kind='peek')
        rows[-2]['dwell_ms'] = 400  # a glance at 燃料
        shown = []
        knowledge.replay(rows, contexts, LISTED, {}, [], NOW, shown=shown)
        classes = shown[0]['classes']
        self.assertEqual((classes['offlist'][1], classes['sentences']), (knowledge.GLANCE_Y, [0.0, 1.0]))
        offlist = knowledge.stay_chance(contexts[('t3', 'a', 'c')], 51, LINE) * knowledge.sigmoid(-1.0)
        self.assertAlmostEqual(classes['offlist'][0], offlist)  # priced at the prior, as words off the list are
        report = knowledge.lookup_classes(shown)['t3']['classes']
        self.assertIsNone(report['sentences']['ratio'])
        self.assertTrue(knowledge.is_sentence('。') and knowledge.is_sentence('私のおすすめは2です。'))
        self.assertFalse(knowledge.is_sentence('燃料'))
        rows.append(dict(rows[-1], id=99, word='私のおすすめは2です。'))
        self.assertEqual(knowledge.unlisted(rows, LISTED, NOW)['top'], [['燃料', 1]])  # sentences aren't words

    def test_a_sentence_costs_its_translation_lookups_per_sentence_read(self):
        rows, contexts = [], {}
        notes = NOTES[:2] + [['。', 60, 27], ['燃料', 0, 51], ['。', 60, 51]]  # two sentences in A
        for e in range(10):  # each read once (A on screen 30 s), its first sentence's translation opened 4 times in 10
            message(contexts, rows, f'v{e}', NOW - (20 - e) * 3_600_000, notes=notes, stays=[stay(A, 30_000, 30)],
                    opened=[2] if e % 5 < 2 else [], kind='peek')
        events = knowledge.sentence_events(rows, contexts, NOW)
        self.assertEqual(len(events), 20)
        read = sum(r for _at, _s, _y, r in events)
        self.assertAlmostEqual(read, 10 * sum(knowledge.stay_chance(contexts[('t3', 'v0', 'c')], y, LINE)
                                              for y in (27, 51)))
        prices = knowledge.sentence_prices(events, NOW)
        self.assertEqual(prices['browser'], {'p': knowledge.SENTENCE_PRICE, 'read': 0.0, 'opens': 0.0})  # no data
        decay = lambda at: 0.5 ** ((NOW - at) / knowledge.DAY_MS / knowledge.EVIDENCE_DAYS)
        opens = sum(decay(at) * y for at, _s, y, _r in events)
        weight = sum(decay(at) * r for at, _s, _y, r in events)
        self.assertAlmostEqual(prices['t3']['p'], (opens + 0.08 * knowledge.SENTENCE_WEIGHT) /
                               (weight + knowledge.SENTENCE_WEIGHT), places=4)
        self.assertGreater(prices['t3']['p'], knowledge.SENTENCE_PRICE)  # 4 opens in about 12 sentences read
        self.assertEqual(knowledge.sentence_prices(events, NOW - 30 * 3_600_000)['t3']['p'], knowledge.SENTENCE_PRICE)

    def test_words_read_weigh_each_word_by_its_read_chance(self):
        passage = {'words': 40, 'height': 72, 'active': 600_000}  # long enough to read it all
        self.assertAlmostEqual(knowledge.words_read('browser', passage), 40 * knowledge.READ_MAX, places=6)
        self.assertEqual(knowledge.words_read('browser', dict(passage, active=0)), 0.0)
        quick = knowledge.words_read('browser', dict(passage, active=8_000))
        self.assertLess(quick, 40 * knowledge.READ_MAX)
        self.assertGreater(quick, 40 * knowledge.read_chance(dict(passage, active=8_000), 48, LINE))  # the last line
        # A read, B never on screen: B's words count at A's mean; the 70 words with no Japanese too
        message_ = {'words': 120, 'height': 300, 'annotations': NOTES, 'stays': [stay(A, 600_000, 30)]}
        self.assertAlmostEqual(knowledge.words_read('t3', message_), 120 * knowledge.READ_MAX, places=6)
        both = dict(message_, stays=[stay(A, 600_000, 30), stay(B, 600, 20), stay((0, 80), 9_000, 30)])
        a = 30 * knowledge.READ_MAX  # the overlapping band of A is the same paragraph: counted once
        b = knowledge.words_read('browser', {'words': 20, 'active': 600})
        self.assertAlmostEqual(knowledge.words_read('t3', both), a + b + 70 * (a + b) / 50, places=6)
        self.assertEqual(knowledge.words_read('t3', dict(message_, stays=[])), 0.0)
        self.assertIsNone(knowledge.per_100(3, 0))
        # a reading model per surface (knowledge.tune): each surface reads its own
        pace, top = {'browser': 300, 't3': 300}, {'browser': 0.3, 't3': knowledge.READ_MAX}
        self.assertAlmostEqual(knowledge.words_read('browser', passage, pace, top), 40 * 0.3, places=6)
        self.assertAlmostEqual(knowledge.words_read('t3', message_, pace, top), 120 * knowledge.READ_MAX, places=6)
        self.assertAlmostEqual(knowledge.reader('browser', passage, pace, top)(48, LINE), 0.3, places=6)
        self.assertEqual(knowledge.per_100(3, 600), 0.5)

    def test_intervals_resample_days(self):
        self.assertIsNone(knowledge.day_interval([(1.0, 1.0)] * 4, knowledge.ratio))  # too few days
        self.assertEqual(knowledge.day_interval([(2.0, 1.0)] * 5, knowledge.ratio), (0.5, 0.5))
        days = [(10.0, s) for s in (5, 8, 10, 12, 15, 9, 11)]
        low, high = knowledge.day_interval(days, knowledge.ratio)
        self.assertLess(low, knowledge.ratio(days))
        self.assertGreater(high, knowledge.ratio(days))
        self.assertGreater(low, 0.5)
        self.assertLess(high, 1.5)
        self.assertEqual((low, high), knowledge.day_interval(days, knowledge.ratio))  # seeded
        self.assertIsNone(knowledge.day_interval([(0.0, 1.0)] * 5, knowledge.ratio))  # nothing expected

    def test_the_chat_budget_follows_its_level(self):
        changes = [{'surface': 'chat', 'at': 100, 'level': -2, 'until': 500},
                   {'surface': 'browser', 'at': 200, 'level': 2, 'until': 900},
                   {'surface': 'chat', 'at': 600, 'level': 3, 'until': 900}]
        self.assertEqual(knowledge.chat_budget(changes, 50), 0.5)
        self.assertEqual(knowledge.chat_budget(changes, 300), 0.15)
        self.assertEqual(knowledge.chat_budget(changes, 550), 0.5)  # ran out
        self.assertIsNone(knowledge.chat_budget(changes, 700))  # full: no budget
        self.assertEqual(knowledge.chat_budget(changes, 300, challenge=1), 0.3)  # the challenge is normal's


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
        found = feedback.record([shown] + stays + bad, 't3')
        self.assertEqual([r['item'] for r in found.pop('reasons')], [4, 5, 6, 7])  # T3: each rejected item, by index
        self.assertEqual(found, {'stored': 4, 'duplicates': 0, 'rejected': 4})
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
        chat = know['calibration']['classes']['t3']
        self.assertEqual({k: chat[k] for k in ('n', 'words', 'lookups', 'none')},
                         {'n': 1, 'words': 120, 'lookups': 0, 'none': 1})
        self.assertGreater(chat['classes']['costly']['expected'] + chat['classes']['free']['expected'], 0)
        self.assertGreater(know['words'][listed[0]]['k'], know['words'][listed[0]]['mu'])
        self.assertLess(know['offset']['t3'], 0)

    def test_refresh_counts_the_words_a_brief_offered_as_topical(self):
        import hub
        state = fwlib.load_state()
        queue = [w['ja'] for w in state['words'] if w['status'] == 'queue'][:2]
        notes = [[queue[0], 0, 3], ['燃料', 40, 27], [queue[1], 0, 51]]
        now = time.time()
        at = int(now * 1000) - 3_600_000
        items = [dict(self.where(at), kind='shown', words=120, annotations=notes)]
        items += [dict(s, annotations=notes) for s in self.stays(at + 1000)]
        items.append(dict(self.where(at + 5000), annotations=notes, kind='peek', word='燃料', position=1, dwellMs=1500,
                          uid='p-topical'))
        feedback.record(items, 't3')
        hub.keep({'kind': 'brief', 'session': 's1', 'level': 0, 'intensity': 1.0, 'offered': [], 'arms': {},
                  'known': 3, 'whole': True, 'chars': 4000, 'budget': 0.5, 'ask': 2, 'expected': 0.5,
                  'topical': [[queue[0], 0.3], ['燃料', 0.4]], 'at': at - 600_000})
        chat = knowledge.refresh(now)['calibration']['classes']['t3']['classes']
        self.assertGreater(chat['topical']['expected'], 0)
        self.assertEqual(chat['topical']['seen'], chat['all']['seen'])  # the open was 燃料's, topical
        self.assertEqual(chat['offlist'], {'expected': 0.0, 'seen': 0.0, 'ratio': None, 'interval': None})
        self.assertGreater(chat['costly']['expected'] + chat['free']['expected'], 0)  # queue[1] wasn't offered


if __name__ == '__main__':
    unittest.main()
