"""Reading speed as evidence (NEXT_STEPS step 19; knowledge.SPEED, off by default): what a passage's or paragraph's
reading time is (knowledge.speed_units), the term in the fit (knowledge.Fit with speed, its two stages), its models in
the evaluation harness, and the simulated reader's reading time (tools/sim, cfg speed). Run: python -m unittest
discover -s tests"""
import math
import os
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tools' / 'sim'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import evaluate  # noqa: E402
import knowledge  # noqa: E402

NOW = 1_800_000_000_000
HOUR = 3_600_000


def peek(word, position, dwell, at=NOW):
    return {'kind': 'peek', 'word': word, 'position': position, 'dwell_ms': dwell, 'at': at, 'id': position}


class Units(unittest.TestCase):
    """knowledge.speed_units: one per browser passage, one per T3 paragraph, glosses' time taken off."""
    LISTED = {'今日', '天気', '行く'}

    def test_a_passage_is_its_time_on_screen_less_the_time_its_glosses_were_open(self):
        context = {'surface': 'browser', 'words': 40, 'height': 72, 'active': 60_000,
                   'annotations': [['今日', 10, 3], ['天気', 60, 27], ['ブログ', 10, 51], ['。', 90, 51]]}
        items = [peek('天気', 1, 1500), peek('。', 3, 90_000)]  # a gloss left open counts SPEED_DWELL_MS at most
        (unit,) = knowledge.speed_units('browser', context, items, self.LISTED)
        ms, words, japanese, listed, active, dwell = unit
        self.assertEqual((active, dwell, ms), (60_000, 1500 + knowledge.SPEED_DWELL_MS,
                                               60_000 - 1500 - knowledge.SPEED_DWELL_MS))
        self.assertEqual((words, japanese), (40, 3))  # the sentence end is no word
        self.assertEqual(listed, ('今日', '天気'))  # opened or not, each once; ブログ is off the list

    def test_too_little_time_left_or_no_word_count_says_nothing(self):
        context = {'surface': 'browser', 'words': 40, 'height': 24, 'active': 2_000, 'annotations': [['今日', 10, 3]]}
        self.assertEqual(knowledge.speed_units('browser', context, [peek('今日', 0, 1500)], self.LISTED), [])
        self.assertEqual(len(knowledge.speed_units('browser', context, [], self.LISTED)), 1)
        self.assertEqual(knowledge.speed_units('browser', dict(context, words=0), [], self.LISTED), [])
        self.assertEqual(knowledge.speed_units('browser', dict(context, active=0), [], self.LISTED), [])

    def test_a_t3_paragraph_adds_its_stays_and_keeps_its_own_words(self):
        context = {'surface': 't3', 'words': 120, 'height': 100,
                   'annotations': [['今日', 10, 5], ['天気', 40, 30], ['行く', 10, 75]],
                   'stays': [{'y': 0, 'height': 48, 'ms': 6_000, 'words': 30},
                             {'y': 0, 'height': 48, 'ms': 4_000, 'words': 30},  # seen twice: 10 s in all
                             {'y': 70, 'height': 24, 'ms': 3_000, 'words': 12}]}
        items = [peek('今日', 0, 200), peek('行く', 2, 1000)]  # a T3 peek under 300 ms is the mouse passing
        units = sorted(knowledge.speed_units('t3', context, items, self.LISTED), key=lambda u: -u[1])
        self.assertEqual([(u[0], u[1], u[2], u[3]) for u in units],
                         [(10_000, 30, 2, ('今日', '天気')), (2_000, 12, 1, ('行く',))])


class FitWithSpeed(unittest.TestCase):
    """The term in the fit, on a log of words with known chances whose paragraphs take longer for each unknown word."""

    def log(self, unknown, seed=1, quiet=()):
        """A log of 20 words, 10 at 5% and 10 at 50%, each looked up as its chance says over 40 showings, and 600
        paragraphs of one to three of them on 40 pages: log ms = ln(300 words) + the page's pace + unknown x (the sum
        of its words' chances) / words + Student-t noise. Words in quiet are never shown alone, only in paragraphs."""
        rng = random.Random(seed)
        chances = {f'a{i}': 0.05 for i in range(10)}
        chances.update({f'b{i}': 0.5 for i in range(10)})
        events = []
        for w, q in chances.items():
            if w in quiet:
                continue
            for i in range(40):
                at = NOW + rng.randrange(0, 72) * HOUR + i
                group = evaluate.Group(at, 'browser', ('browser', f'v{w}{i}', 'p'), False, [])
                group.obs.append(evaluate.Observation(
                    at=at, day=evaluate.day_of(at), surface='browser', key=group.key, episode=False, word=w, written=w,
                    form=False, y=1.0 if rng.random() < q else 0.0, weight=1.0, near=1.0, ms=1, words=1, share=1.0,
                    read=1.0))
                events.append(group)
        pages = [rng.gauss(0, 0.3) for _ in range(40)]
        words = list(chances) + list(quiet)
        for j in range(600):
            at = NOW + rng.randrange(0, 72) * HOUR + 1000 + j
            listed = tuple(rng.sample(words, rng.randint(1, 3)))
            count = rng.randint(10, 60)
            page = j % 40
            noise = rng.gauss(0, 0.4) / math.sqrt(rng.gammavariate(2, 0.5))  # Student-t with 4 degrees of freedom
            ms = math.exp(math.log(300 * count) + pages[page] + unknown * sum(chances.get(w, 0.5) for w in listed)
                          / count + noise)
            events.append(evaluate.Group(at, 'browser', ('browser', f'page{page}', f'p{j}'), False, [],
                                         units=((ms, count, len(listed), listed, ms, 0),)))
        events.sort(key=lambda e: e.at)
        return evaluate.Log(events, {}, [{'ja': w, 'kana': '', 'status': 'active', 'added': '', 'changed': ''}
                                         for w in words])

    def test_off_by_default_and_then_the_fit_is_as_before(self):
        self.assertFalse(knowledge.SPEED)
        log = self.log(20.0)
        until = NOW + 80 * HOUR
        plain, off = knowledge.Fit(log, until), knowledge.Fit(log, until, speed=False)
        self.assertEqual(plain.k, off.k)
        self.assertIsNone(plain.speed_summary())
        self.assertEqual(plain.names, knowledge.SHARED_NAMES)

    def test_it_finds_the_time_an_unknown_word_costs_and_none_where_there_is_none(self):
        until = NOW + 80 * HOUR
        found = knowledge.Fit(self.log(20.0), until, speed=True).speed_summary()
        b, sd = found['shared']['unknown'], found['sd']['unknown']
        self.assertLess(abs(b - 20.0), 3 * sd)
        self.assertGreater(b, 2 * sd)
        self.assertLess(abs(found['group'] - 0.3), 0.15)  # the pages' own pace
        null = knowledge.Fit(self.log(0.0), until, speed=True).speed_summary()
        self.assertLess(abs(null['shared']['unknown']), 3 * null['sd']['unknown'])

    def test_a_slow_paragraph_moves_its_words_toward_unknown_and_time_never_swamps_the_lookups(self):
        """c0 is never looked up or seen alone: only its paragraphs' times say anything about it beyond its prior. With
        its true chance 50% its paragraphs are slow, so the fit with the term puts it below where the prior has it;
        words with lookups move far less than the prior's width."""
        until = NOW + 80 * HOUR
        log = self.log(40.0, quiet=('c0',))
        off, on = knowledge.Fit(log, until, speed=False), knowledge.Fit(log, until, speed=True)
        self.assertLess(on.estimate('c0')[0], off.estimate('c0')[0] - 0.05)
        for w in ('a0', 'b0'):
            self.assertLess(abs(on.estimate(w)[0] - off.estimate(w)[0]), 0.25)
        # where time says more than the lookups and the prior (here it does: 40 words a word not known, little noise),
        # it is held to what they say (SPEED_CAP): at most half of any word's information
        cap = knowledge.SPEED_CAP / (1 + knowledge.SPEED_CAP)
        self.assertLess(on.speed_summary()["most"], cap + 0.1)

    def test_a_fit_starts_from_the_last_one(self):
        until = NOW + 80 * HOUR
        log = self.log(20.0)
        cold = knowledge.Fit(log, until, speed=True)
        warm = knowledge.Fit(log, until, speed=True, start=cold.state())
        self.assertLess(max(abs(a - b) for a, b in zip(cold.k, warm.k)), 0.02)
        self.assertAlmostEqual(cold.speed_summary()['shared']['unknown'], warm.speed_summary()['shared']['unknown'],
                               delta=0.2)


class Harness(unittest.TestCase):
    def test_the_models_with_and_without_it(self):
        self.assertEqual(evaluate.CURRENT, 'fit')
        self.assertEqual((evaluate.MODELS['speed']().name, evaluate.MODELS['nospeed']().name), ('speed', 'nospeed'))
        self.assertTrue(evaluate.MODELS['speed']().speed)
        self.assertFalse(evaluate.MODELS['nospeed']().speed)
        self.assertIsNone(evaluate.MODELS['fit']().speed)  # follows knowledge.SPEED

    def test_showings_carry_their_units_and_episodes_none(self):
        words = [{'ja': '今日', 'kana': 'きょう', 'status': 'known', 'added': '2026-01-01', 'changed': '2026-01-01'}]
        contexts = {('browser', 'v', 'p'): {'surface': 'browser', 'view': 'v', 'context': 'p', 'words': 30,
                                             'height': 24, 'active': 9_000, 'shown_at': NOW, 'seen_until': NOW,
                                             'annotations': [['今日', 10, 3]], 'plan': None},
                    ('t3', 'w', 'm'): {'surface': 't3', 'view': 'w', 'context': 'm', 'words': None, 'height': 24,
                                       'active': 0, 'shown_at': None, 'seen_until': None, 'stays': [],
                                       'annotations': [['今日', 10, 3]], 'plan': None}}
        rows = [dict(peek('今日', 0, 1000, NOW + 1000), surface='t3', view='w', context='m', uid='u1')]
        log = evaluate.extract(rows, contexts, words, {}, [], NOW + 2 * HOUR)
        units = {e.key: e.units for e in log.events if isinstance(e, evaluate.Group)}
        self.assertEqual(units[('browser', 'v', 'p')], ((9_000, 30, 1, ('今日',), 9_000, 0),))
        self.assertEqual(units[('t3', 'w', 'm')], ())


class Simulator(unittest.TestCase):
    def test_the_reading_time_changes_the_times_only(self):
        from world import World
        plain, slow = World(seed=3), World(seed=3, speed={'unknown': 10.0, 'japanese': 1.0, 'dwell': True})
        default = World(seed=3, speed=None)
        for _ in range(2):
            a, b, c = plain.simulate_day(), slow.simulate_day(), default.simulate_day()
            self.assertEqual([(m.written, m.looked) for s in a for m in s.marks],
                             [(m.written, m.looked) for s in b for m in s.marks])
            self.assertEqual([s.active for s in a], [s.active for s in c])
            web = [(x.active, y.active) for x, y in zip(a, b) if x.surface == 'browser' and x.marks]
            self.assertTrue(all(y >= x for x, y in web))
            self.assertGreater(sum(y for _x, y in web), sum(x for x, _y in web))


if __name__ == '__main__':
    unittest.main()
