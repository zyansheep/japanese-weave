"""Offline tests for the probe (scripts/probe.py, NEXT_STEPS step 7): its population and sample, the estimate and the
simulated readers that check it (tools/probe_sim.py), the made-up words, the lookup habit, the probe op on its way to
the collector, and fw.py probe. Run: python3 -m unittest tests/test_probe.py"""
import datetime as dt
import io
import json
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tools'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import collector  # noqa: E402
import dictionary  # noqa: E402
import feedback  # noqa: E402
import fw  # noqa: E402
import fwlib  # noqa: E402
import hub  # noqa: E402
import probe  # noqa: E402
import probe_sim  # noqa: E402

FRAME = probe.Frame()


class Home(unittest.TestCase):
    """Each test gets its own state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fw(self, *argv, stdin=None):
        with mock.patch('sys.stdout', new=io.StringIO()) as out, \
                mock.patch('sys.stdin', new=io.StringIO(stdin or '')):
            fw.main(list(argv))
        return out.getvalue()


def answered(p, knows=lambda ja: False):
    """Answers to a probe: yes to the words knows() says, no to the rest and to every made-up word."""
    return [0 if q.get('fake') else int(knows(q['ja'])) for q in p['questions']]


class Population(unittest.TestCase):
    def test_swappable_words_of_the_top_ranks_weighted_by_frequency_one_item_a_spelling(self):
        d = dictionary.load()
        items = {it['ja']: it for it in FRAME.items}
        self.assertGreater(len(items), 6000)
        for it in FRAME.items[:200] + FRAME.items[-200:]:
            entries = [d.get(i) for i in it['ids']]
            self.assertTrue(all(e['swap'] and e['rank'] <= probe.TOP and e['ja'] == it['ja'] for e in entries))
            self.assertAlmostEqual(it['weight'], sum(1 / e['score'] for e in entries))
        self.assertEqual(len(items['方']['ids']), 2)  # ほう and かた: the probe shows only the spelling
        self.assertNotIn('二人', items)  # a number and a counter
        self.assertNotIn('三時', items)
        self.assertIn('一緒', items)  # a numeral inside a word that means something else
        self.assertIn('十分', items)
        self.assertFalse(any(re.fullmatch('[ァ-ヺー]+', ja) for ja in items))  # katakana words aren't swappable
        weights = [it['weight'] for it in FRAME.items]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_reading_counts_replace_general_frequency(self):
        d = dictionary.load()
        rare = next(e for e in d if e['swap'] and e['rank'] > probe.TOP)  # out by rank, in by your reading
        common = d.find('問題')
        frame = probe.Frame(d, counts={rare['id']: 3, common['id']: 9, d.find('二人')['id']: 50})
        self.assertEqual([(it['ja'], it['weight']) for it in frame.items], [('問題', 9), (rare['ja'], 3)])
        self.assertEqual((frame.weights, frame.top), ('reading', 0))

    def test_bands_hold_equal_weight_and_the_first_gives_at_most_a_quarter(self):
        self.assertEqual(len(FRAME.bands), probe.BANDS)
        for share in FRAME.shares:
            self.assertAlmostEqual(share, 1 / probe.BANDS, delta=0.01)
        sizes = [len(b) for b in FRAME.bands]
        self.assertEqual(sizes, sorted(sizes))
        counts = probe.allocate(sizes, FRAME.shares)
        self.assertEqual(sum(counts), probe.WORDS)
        self.assertEqual(counts[0], sizes[0] // probe.SHARE)
        self.assertTrue(all(10 <= n <= 11 for n in counts[1:]))

    def test_allocation_caps_and_spreads(self):
        self.assertEqual(probe.allocate([100, 100, 100], [0.5, 0.25, 0.25], 20), [10, 5, 5])
        self.assertEqual(probe.allocate([8, 100, 100], [0.5, 0.25, 0.25], 20), [2, 9, 9])
        self.assertEqual(probe.allocate([1, 2, 400], [0.4, 0.4, 0.2], 10), [1, 1, 8])  # at least one a band
        self.assertEqual(probe.allocate([4, 4], [0.5, 0.5], 10), [1, 1])  # no more than the bands can give
        bands = probe.cut([{'ja': 'a', 'weight': 9.0}] + [{'ja': str(i), 'weight': 1.0} for i in range(9)], 3)
        self.assertEqual([len(b) for b in bands], [1, 3, 6])  # a dominant item sits in the band of its middle


class Sampling(unittest.TestCase):
    def test_a_probe_asks_60_words_and_6_made_up_in_random_order(self):
        p = probe.sample(rng=random.Random(1), frame=FRAME)
        words = [q for q in p['questions'] if not q.get('fake')]
        fakes = [q for q in p['questions'] if q.get('fake')]
        self.assertEqual((len(words), len(fakes)), (probe.WORDS, probe.FAKES))
        self.assertEqual(len({q['ja'] for q in p['questions']}), len(p['questions']))
        self.assertEqual([sum(q['band'] == h for q in words) for h in range(probe.BANDS)],
                         probe.allocate([len(b) for b in FRAME.bands], FRAME.shares))
        self.assertNotEqual([q.get('fake', False) for q in p['questions']], [False] * 60 + [True] * 6)
        self.assertIsNone(p['listed'])  # no word state to compare with
        self.assertEqual([s for s, _n in p['bands']], [round(s, 6) for s in FRAME.shares])

    def test_no_word_comes_back_within_90_days_nor_a_made_up_one_within_a_year(self):
        state, rng, day = {'probes': []}, random.Random(2), '2026-01-01'
        for m in range(4):  # monthly
            p = probe.sample(state, rng=rng, today=day, frame=FRAME)
            op = probe.record(p, answered(p), 'chat', 100)
            probe.keep(state, dict(op, uid=str(m), day=day, at=0))
            day = dt.date.fromisoformat(day).replace(month=m + 2).isoformat()
        self.assertEqual(probe_sim.repeats(state), 0)
        first = {w[0] for w in state['probes'][0]['words']}
        later = {w[0] for p in state['probes'][1:3] for w in p['words']}
        self.assertFalse(first & later)
        fakes = [f[0] for p in state['probes'] for f in p['fakes']]
        self.assertEqual(len(fakes), len(set(fakes)))
        # the fourth probe, 90 days after the first, may draw the first's words again; the first band needs some
        self.assertTrue({w[0] for w in state['probes'][3]['words'] if w[2] == 0} & first)

    def test_too_few_fresh_words_take_the_least_recently_probed(self):
        last = {'a': '2026-03-01', 'b': '2026-02-01', 'c': '2026-03-10'}
        picked = probe.pick(['a', 'b', 'c', 'd'], 3, last, '2026-03-20', 90, random.Random(1))
        self.assertEqual(picked, ['d', 'b', 'a'])


class Estimate(unittest.TestCase):
    bands = [[0.5, 20], [0.5, 200]]

    def test_the_weighted_share_known_per_band(self):
        words = [(0, 2.0, 1), (0, 1.0, 0), (1, 1.0, 1), (1, 1.0, 0), (1, 1.0, 0), (1, 1.0, None)]
        found = probe.estimate(words, self.bands)
        tin = 2 / 3 * (1 + (1 - 2 / 20) / 2 * (1.0 / (1.5 * 1.0) - 0.5 / 2.25))  # Tin's correction of 2/3
        self.assertAlmostEqual(found['known'], round(0.5 * tin + 0.5 / 3, 4))
        self.assertEqual(found['n'], 5)  # the skipped word doesn't count
        self.assertLess(found['low'], found['known'])
        self.assertGreater(found['high'], found['known'])

    def test_all_or_nothing_still_has_an_interval(self):
        every = probe.estimate([(h, 1.0, 1) for h in (0, 1) for _ in range(10)], self.bands)
        self.assertEqual(every['known'], 1.0)
        self.assertTrue(0.8 < every['low'] < 1.0 and every['high'] == 1.0)
        none = probe.estimate([(h, 1.0, 0) for h in (0, 1) for _ in range(10)], self.bands)
        self.assertEqual((none['known'], none['low']), (0.0, 0.0))
        self.assertGreater(none['high'], 0.0)
        self.assertIsNone(probe.estimate([(0, 1.0, None)], self.bands))

    def test_a_band_without_answers_leaves_its_weight_out(self):
        found = probe.estimate([(1, 1.0, 1), (1, 1.0, 0)], self.bands)
        self.assertEqual(found['known'], 0.5)

    def test_correction_for_guessing(self):
        found = {'known': 0.6, 'se': 0.04, 'n': 60, 'low': 0.53, 'high': 0.66}
        same = probe.corrected(found, 0, 6)
        self.assertEqual((same['known'], same['high']), (0.6, 0.66))  # never above the share claimed
        self.assertLess(same['low'], 0.53)  # 0 of 6 can't rule out some guessing
        one = probe.corrected(found, 1, 6)
        self.assertAlmostEqual(one['known'], (0.6 - 1 / 6) / (5 / 6), places=4)
        self.assertLessEqual(one['high'], 0.66)
        year = probe.corrected(found, 6, 36)  # the same rate over six probes: a narrower interval
        self.assertEqual(year['known'], one['known'])
        self.assertGreater(year['low'], one['low'])
        self.assertIsNone(probe.corrected(found, 6, 6))
        self.assertIsNone(probe.corrected(found, 0, 0))

    def test_made_up_words_pool_over_the_last_year(self):
        probes = [{'day': '2025-10-01', 'fakes': [['悪売', 1]] * 6},  # over a year ago
                  {'day': '2026-01-01', 'fakes': [['悪売', 1]] + [['暗計', 0]] * 5},
                  {'day': '2026-09-01', 'fakes': [['円説', 0]] * 5 + [['王会', None]]}]
        self.assertEqual(probe.claims(probes, '2026-10-20'), (1, 11))
        p = probe.sample(rng=random.Random(1), frame=FRAME)
        est = probe.record(p, answered(p), 'chat', 60, (1, 11))['estimate']
        self.assertEqual((est['claimed'], est['fakes'], est['claimed_year'], est['fakes_year']), (0, 6, 1, 17))
        self.assertAlmostEqual(est['corrected'], max(0, (est['known'] - 1 / 17) / (16 / 17)), places=3)
        self.assertIsNone(hub.validate(hub.stamp(probe.record(p, answered(p), 'chat', 60, (1, 11)))))


class Simulated(unittest.TestCase):
    """tools/probe_sim.py at a few hundred readers: the numbers PLAN.md section 1 promises."""

    def test_unbiased_with_about_the_promised_standard_error_and_its_coverage(self):
        s = probe_sim.profile(FRAME, 1000, 0.0, 400, 7)
        self.assertLess(abs(s['bias']), 0.01)
        self.assertLess(s['sd'], 0.06)  # PLAN.md: about 6 points
        self.assertLess(abs(s['se'] - s['sd']), 0.015)  # the reported standard error is about right
        self.assertGreater(s['coverage'], 0.85)
        self.assertLess(s['deff'], 1.0)  # stratifying by frequency beats a simple sample

    def test_made_up_words_catch_over_claiming(self):
        s = probe_sim.profile(FRAME, 1000, 0.1, 400, 8)
        self.assertGreater(s['bias'], 0.025)  # saying yes to 1 in 10 unknown words inflates the share
        self.assertLess(abs(s['corrected_bias']), 0.02)
        self.assertGreater(s['corrected_coverage'], 0.85)

    def test_leaving_out_recent_words_biases_no_later_probe(self):
        months = probe_sim.months(FRAME, 1000, 0.0, 40, 5, 9)
        self.assertLess(abs(sum(m['bias'] for m in months) / len(months)), 0.012)
        self.assertGreater(min(m['coverage'] for m in months), 0.75)


class MadeUp(unittest.TestCase):
    def test_three_hundred_two_kanji_fakes_no_dictionary_word(self):
        fakes = probe.read_fakes()
        self.assertEqual(len(fakes), 300)
        self.assertEqual(len({ja for ja, _ in fakes}), 300)
        d = dictionary.load()
        for ja, kana in fakes:
            self.assertTrue(len(ja) == 2 and all(dictionary.KANJI.match(c) for c in ja), ja)
            self.assertTrue(dictionary.is_kana(kana), kana)
            self.assertEqual(d.written(ja), [], ja)
            self.assertEqual(d.reading(kana), [], kana)
            self.assertEqual(d.written(ja[::-1]), [], ja)


class Habit(Home):
    def log(self, at):
        """A web passage with 経済 (peeked) and 問題 (not, on the same line) on screen for a minute, and one with 問題
        alone, never on screen (too unlikely read to count)."""
        where = {'view': 'v1', 'box': [0, 0, 600, 40], 'at': at}
        first = dict(where, context='p1', annotations=[['経済', 10, 0], ['問題', 300, 0]])
        second = dict(where, context='p2', annotations=[['問題', 10, 0]])
        items = [dict(first, kind='shown', words=40), dict(first, kind='peek', word='経済', position=0, dwellMs=900,
                                                           uid='k1'),
                 dict(first, kind='visible', y=0, height=40, ms=60_000, wall=60_000, away=0, media=0, idle=0, uid='s1'),
                 dict(second, kind='shown', words=40)]
        feedback.record(items, 'browser')

    def test_lookups_over_read_chances_of_the_words_answered_unknown(self):
        now = int(time.time() * 1000)
        self.log(now - 3_600_000)
        rows, contexts = feedback.load()
        found = probe.lookup_habit(rows, contexts, ['経済', '問題', '時間'], now)
        by = {w[0]: w for w in found['words']}
        self.assertEqual(set(by), {'経済', '問題'})  # 時間 was never shown
        self.assertEqual(by['経済'][1:], [1.0, 1.0, 1])
        self.assertEqual(by['問題'][1], 0.0)
        self.assertEqual(by['問題'][2:], [1.0, 1])  # on the line of a word opened, so read; the unread one left out
        summary = probe.habit_summary([found])
        self.assertAlmostEqual(summary['c'], 1 / (1 + by['問題'][2]), places=2)
        self.assertEqual((summary['words'], summary['showings']), (2, 2))
        self.assertLess(summary['low'], summary['c'])
        self.assertEqual(probe.lookup_habit(rows, contexts, ['経済'], now - 2 * 3_600_000)['words'], [])  # before it
        self.assertIsNone(probe.habit_summary([{'words': []}, None]))


class Op(Home):
    def op(self):
        p = probe.sample(rng=random.Random(3), frame=FRAME)
        return hub.stamp(probe.record(p, answered(p, lambda ja: ja in {it['ja'] for it in FRAME.bands[0]}), 'chat', 95))

    def test_well_formed_probes_only(self):
        good = self.op()
        self.assertIsNone(hub.validate(good))
        self.assertIsNone(hub.validate(dict(good, via='terminal', estimate={})))  # no answers, no estimate
        for bad in (dict(good, via='phone'), dict(good, seconds=-1), dict(good, weights='other'),
                    dict(good, words=[['問題', 1, 0, 0.1, 2]]), dict(good, words=[['問題', 1, 9, 0.1, 1]]),
                    dict(good, words=[['問題', 1, 0, 0.0, 1]]), dict(good, fakes=[['悪売', True]]),
                    dict(good, bands=[]), dict(good, bands=[[1.5, 10]]), dict(good, estimate={'known': 1.2}),
                    dict(good, estimate={'known': float('nan')}), dict(good, estimate={'note': 'x'}),
                    dict(good, note='x'), {k: v for k, v in good.items() if k != 'fakes'}):
            self.assertIsNotNone(hub.validate(bad), {k: v for k, v in bad.items() if k not in ('words', 'fakes')})

    def test_kept_in_the_state_once_with_the_lookup_habit_where_the_log_is(self):
        fwlib.save_state(fwlib.new_state('2026-10-01', known_top=40))
        op = self.op()
        Habit.log(self, op['at'] - 3_600_000)
        unknown = {w[0] for w in op['words'] if w[4] == 0}
        with mock.patch.object(probe, 'lookup_habit', wraps=probe.lookup_habit) as habit:
            result = hub.run(op)
        self.assertEqual(set(habit.call_args.args[2]), unknown)
        self.assertTrue(result['ok'] and result['kept'])
        kept = fwlib.load_state()['probes']
        self.assertEqual(len(kept), 1)
        self.assertEqual({k: kept[0][k] for k in probe.RECORD}, {k: op[k] for k in probe.RECORD})
        self.assertIn('habit', kept[0])
        self.assertEqual(hub.run(op), {'ok': True, 'duplicate': True})
        state = fwlib.load_state()
        self.assertEqual(probe.keep(state, op), {'kept': False})  # once, even applied again to a cache
        self.assertEqual(len(state['probes']), 1)


class Collector(Home):
    """A probe from a machine whose word state lives on a collector reaches it, is checked there, and comes back in the
    cache."""

    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        self.server_home = self.home / 'collector-home'
        with fwlib.using_home(self.server_home):
            hub.submit({'kind': 'seed', 'n': 40})
        quiet = {'db': self.server_home / 'interactions.sqlite', 'home': self.server_home,
                 'log_message': lambda *args: None}
        handler = type('TestHandler', (collector.Handler,), quiet)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        fwlib.home().mkdir(parents=True, exist_ok=True)
        (fwlib.home() / 'collector').write_text(f'http://127.0.0.1:{self.server.server_address[1]}\n')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def test_the_probe_goes_to_the_collector_and_its_history_comes_back(self):
        listed = self.fw('probe', '--list', '--seed', '4')
        self.assertIn(' 66  ', listed)
        out = self.fw('probe', '--answers', '1-20')
        self.assertIn('You would read', out)
        self.assertNotIn('Kept here', out)
        with fwlib.using_home(self.server_home):
            served = fwlib.load_state(create=False)['probes']
            import sqlite3
            con = sqlite3.connect(str(self.server_home / 'interactions.sqlite'))
            kinds = [k for (k,) in con.execute('SELECT kind FROM ops ORDER BY rowid')]
            con.close()
        self.assertEqual(kinds, ['seed', 'probe'])
        self.assertEqual(fwlib.load_state()['probes'], served)  # the cache was refreshed
        self.assertEqual(len(served[0]['words']) + len(served[0]['fakes']), 66)
        refused = hub.post(fwlib.collector_url(), [hub.stamp({'kind': 'probe', 'via': 'chat', 'words': 'all'})])
        self.assertFalse(refused['results'][0]['ok'])
        self.assertIn('probe:', refused['results'][0]['error'])

    def test_a_probe_made_while_the_collector_is_away_shows_at_once_and_goes_with_the_next_sync(self):
        url = fwlib.collector_url()
        self.fw('probe', '--list', '--seed', '5')  # fetches the first copy of the state
        (fwlib.home() / 'collector').write_text('http://127.0.0.1:9\n')  # nothing listens there
        out = self.fw('probe', '--answers', 'none')
        self.assertIn('Kept here', out)
        self.assertEqual(len(fwlib.load_state()['probes']), 1)  # in the cache at once
        self.assertIn('last probe', self.fw('status'))
        (fwlib.home() / 'collector').write_text(url + '\n')
        self.assertEqual(hub.sync(), (True, None))
        with fwlib.using_home(self.server_home):
            self.assertEqual(len(fwlib.load_state(create=False)['probes']), 1)
        self.assertEqual(len(fwlib.load_state()['probes']), 1)  # the collector's copy, not a second one


class Command(Home):
    def test_list_then_answers_then_history_and_status(self):
        fwlib.save_state(fwlib.new_state('2026-10-01', known_top=150))
        self.assertIn('probe due: none yet', self.fw('status'))
        listed = self.fw('probe', '--list')
        again = self.fw('probe', '--list')
        self.assertEqual(listed, again)  # the same list until answered
        words = re.findall(r'^\s*(\d+)  (\S+)$', listed, re.M)
        self.assertEqual(len(words), 66)
        pending = json.loads((fwlib.home() / probe.PENDING).read_text())
        fakes = {q['ja'] for q in pending['questions'] if q.get('fake')}
        state = fwlib.load_state()
        known = {dictionary.load().find(w['ja'], w['kana'], w['en'])['ja'] for w in state['words']
                 if w['status'] == 'known'}
        yes = [n for n, ja in words if ja in known or ja == sorted(fakes)[0]]  # one made-up word claimed
        out = self.fw('probe', '--answers', ' '.join(yes))
        self.assertIn(f'Made-up words you marked known: 1 of 6 ({sorted(fakes)[0]})', out)
        self.assertIn('Corrected for guessing', out)
        self.assertIn('Your word list marks', out)
        self.assertIn('Lookup habit: none of the words', out)  # no gloss log here
        self.assertIn('Made up: ', out)
        self.assertFalse((fwlib.home() / probe.PENDING).exists())
        kept = fwlib.load_state()['probes'][-1]
        self.assertEqual(kept['via'], 'chat')
        self.assertEqual(sum(w[4] for w in kept['words']), len(yes) - 1)
        self.assertIn('last probe ' + fwlib.today(), self.fw('status'))
        history = self.fw('probe', '--history')
        self.assertIn(f'{fwlib.today()}  known ', history)
        self.assertIn('made up 1/6', history)
        with self.assertRaises(SystemExit):
            self.fw('probe', '--answers', '1 2')  # nothing waits any more

    def test_answers_are_numbers_ranges_or_all_but(self):
        self.assertEqual(probe.parse_answers('1 4 7-9', 10), {1, 4, 7, 8, 9})
        self.assertEqual(probe.parse_answers('1,2, 3', 10), {1, 2, 3})
        self.assertEqual(probe.parse_answers('none', 5), set())
        self.assertEqual(probe.parse_answers('all', 3), {1, 2, 3})
        self.assertEqual(probe.parse_answers('All but 2', 3), {1, 3})
        self.assertEqual(probe.parse_answers('all except 1-2', 3), {3})
        for bad in ('0', '11', '3-1', 'x', 'all 3', '5-12'):
            with self.assertRaises(ValueError):
                probe.parse_answers(bad, 10)

    def test_one_word_at_a_time_in_a_terminal(self):
        fwlib.save_state(fwlib.new_state('2026-10-01', known_top=40))
        keys = 'y\nn\nb\ny\ns\nx\n' + 'n\n' * 63  # b: back over the "n"; x: not a key, asked again
        out = self.fw('probe', '--seed', '5', stdin=keys)
        self.assertIn('  1/66  ', out)
        self.assertIn('(back)', out)
        kept = fwlib.load_state()['probes'][0]
        answers = [w[4] for w in kept['words']] + [f[1] for f in kept['fakes']]
        self.assertEqual((answers.count(1), answers.count(None), len(answers)), (2, 1, 66))
        self.assertEqual(kept['via'], 'terminal')
        out = self.fw('probe', '--seed', '6', stdin='y\nq\n')
        self.assertIn('Stopped: nothing kept.', out)
        self.assertEqual(len(fwlib.load_state()['probes']), 1)

    def test_due_after_30_days(self):
        state = {'probes': [{'day': '2026-09-01', 'estimate': {'known': 0.42}}]}
        self.assertIn('next due 2026-10-01', probe.status_line(state, '2026-09-20'))
        self.assertIn('probe due: the last was 30 days ago (2026-09-01, 42% known)',
                      probe.status_line(state, '2026-10-01'))


if __name__ == '__main__':
    unittest.main()
