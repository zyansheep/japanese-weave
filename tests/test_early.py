"""Offline tests for a word's first sightings (knowledge.early_shifts, early_of): the shift a word's chance takes while
it has been seen fewer than EARLY_FIRST times, fitted per surface at each refresh and applied wherever a chance is
priced. Run: python -m unittest tests/test_early.py"""
import math
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import check_reply  # noqa: E402
import evaluate  # noqa: E402
import knowledge  # noqa: E402

logit = lambda p: math.log(p / (1 - p))  # noqa: E731


class Shift(unittest.TestCase):
    def test_recovers_the_shift_that_made_the_opens(self):
        # 10,000 first sightings predicted at 10%, read half the time, opened as a 20% chance would be: a shift of
        # logit(0.2) - logit(0.1) = 0.81, held a little toward none by its prior
        n, r, z = 10_000, 0.5, logit(0.1)
        opened = round(n * r * 0.2)
        rows = [(1.0, r, z)] * opened + [(0.0, r, z)] * (n - opened)
        self.assertAlmostEqual(knowledge._shift(rows), logit(0.2) - logit(0.1), delta=0.02)
        self.assertEqual(knowledge._shift([]), 0.0)  # nothing to go on: none

    def test_fitted_per_surface_from_first_sightings_only(self):
        class Fit:
            pace, top, recency = 300, 1.0, False  # read for certain after long enough on screen
            prior = staticmethod(lambda ja: (logit(0.9), 1.0))  # every word's prior: a 10% chance
            q_at = staticmethod(lambda k, surface, form=False, u=1.0, s=0.0: knowledge.sigmoid(-k))

        def obs(surface, seen, y):
            return evaluate.Observation(surface=surface, word='w', form=False, y=y, seen=seen, ms=10 ** 9, words=10,
                                        share=1.0)

        class Log:
            def scored(self):
                chat = [obs('t3', 0, 1.0 if i < 30 else 0.0) for i in range(100)]  # 30% opened at first sight
                later = [obs('t3', 5, 0.0) for _ in range(500)]  # past the first sightings: never counted
                web = [obs('browser', 1, 1.0 if i < 10 else 0.0) for i in range(100)]  # 10%: as predicted
                forms = [evaluate.Observation(surface='browser', word='w', form=True, y=1.0, seen=0, ms=10 ** 9,
                                              words=10, share=1.0)] * 50  # a conjugated form has its own offset
                return chat + later + web + forms

        with mock.patch.object(knowledge, 'EARLY_SURFACES', ('t3', 'browser')):
            shifts = knowledge.early_shifts(Fit(), Log())
        self.assertGreater(shifts['t3'], 1.0)
        self.assertLess(shifts['t3'], logit(0.3) - logit(0.1))
        self.assertAlmostEqual(shifts['browser'], 0.0, delta=0.01)  # as predicted, forms left out
        self.assertEqual(knowledge.early_shifts(Fit(), Log())['browser'], 0.0)  # the web takes none


class Applied(unittest.TestCase):
    KNOW = {'early': {'browser': 0.2, 't3': 0.8, 'first': 3}, 'offset': {'t3': 0.0},
            'words': {'新': {'k': 1.0, 'sd': 0.8, 'free': False, 'obs': 2},
                      '旧': {'k': 1.0, 'sd': 0.4, 'free': False, 'obs': 3},
                      '今': {'k': 4.0, 'sd': 0.3, 'free': True, 'obs': 0}}}

    def test_only_while_a_word_has_fewer_than_its_first_sightings(self):
        early = lambda ja, surface='t3': knowledge.early_of(self.KNOW, self.KNOW['words'].get(ja), surface)  # noqa
        self.assertEqual(early('新'), 0.8)
        self.assertEqual(early('新', 'browser'), 0.2)
        self.assertEqual(early('旧'), 0.0)  # its third sighting is behind it
        self.assertEqual(early('無'), 0.8)  # no entry: none seen yet
        self.assertEqual(knowledge.early_of({}, None, 't3'), 0.0)  # an older knowledge.json: no shift

    def test_the_estimate_and_the_weave_price_take_it(self):
        p = lambda ja: knowledge.word_estimate(self.KNOW, ja, 't3')[0]  # noqa: E731
        self.assertAlmostEqual(p('新'), knowledge.sigmoid(0.8 - 1.0))
        self.assertAlmostEqual(p('旧'), knowledge.sigmoid(-1.0))
        self.assertLess(p('今'), 0.08)  # known at a glance: the shift doesn't touch it
        word = {'ja': '新', 'k': 1.0, 'free': False}
        self.assertAlmostEqual(check_reply.weave_price(dict(word, early=0.8), None, None)[0], knowledge.sigmoid(-0.2))
        self.assertAlmostEqual(check_reply.weave_price(word, None, None)[0], knowledge.sigmoid(-1.0))


if __name__ == '__main__':
    unittest.main()
