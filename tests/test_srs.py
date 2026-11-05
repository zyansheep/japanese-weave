"""Offline tests for scripts/srs.py, FSRS-6 scheduled as Anki schedules with it: worked cases whose memory states and
unfuzzed intervals were computed with py-fsrs 6.3.2's own Scheduler (fsrs/scheduler.py, enable_fuzzing=False) on
2026-11-05, Anki's own test vectors for its fuzz, learning steps and minimum review interval (rslib/src/scheduler/
states/fuzz.rs and steps.rs), Anki's 4am day boundary, the order of Hard, Good and Easy, and a deterministic fuzz.
The dates are in June and July, local time, so no daylight saving change falls between two reviews (py-fsrs counts
24-hour periods, Anki and srs.py day numbers; they agree when the time of day only grows). Run: python3 -m unittest
tests/test_srs.py"""
import datetime as dt
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import srs  # noqa: E402


def at(month, day, hour, minute=0, year=2026):
    """A local time in ms."""
    return int(dt.datetime(year, month, day, hour, minute).timestamp() * 1000)


# (time, grade) -> (state, step, stability, difficulty, delay: ('secs', n) or ('days', n)), from py-fsrs 6.3.2
FIRST = {1: ('learning', 0, 0.212, 6.4133, ('secs', 60)),
         2: ('learning', 0, 1.2931, 5.112170705601056, ('secs', 330)),
         3: ('learning', 1, 2.3065, 2.118103970459016, ('secs', 600)),
         4: ('review', 0, 8.2956, 1.0, ('days', 8))}
LEARN_THEN_REVIEW = [
    ((6, 1, 10, 0), 3, 'learning', 2.3065, 2.118103970459016, ('secs', 600)),
    ((6, 1, 10, 10), 3, 'review', 2.3065, 2.111214235785395, ('days', 2)),        # same day: the factor floored at 1
    ((6, 4, 11, 0), 3, 'review', 13.835840133660218, 2.1043313908464483, ('days', 14)),  # recall after 3 days
    ((6, 14, 12, 0), 1, 'relearning', 1.666890180902645, 7.389975788014609, ('secs', 600)),  # a lapse
    ((6, 14, 12, 10), 3, 'review', 1.6935290949964625, 7.377814181523433, ('days', 2)),
    ((6, 20, 13, 0), 2, 'review', 5.614403948381989, 8.244499229132952, ('days', 6)),
    ((7, 10, 14, 0), 4, 'review', 30.9038704145765, 7.643121047141945, ('days', 31))]
HARD_FIRST = [
    ((6, 1, 10, 0), 2, 'learning', 1.2931, 5.112170705601056, ('secs', 330)),
    ((6, 1, 10, 6), 2, 'learning', 1.2931, 6.7404595108297, ('secs', 330)),
    ((6, 1, 10, 20), 3, 'learning', 1.3358997622047517, 6.728947420615708, ('secs', 600)),
    ((6, 1, 10, 31), 3, 'review', 1.3771622371865915, 6.717446842491931, ('days', 1))]
# R(10 days, S) after the first review, by grade (py-fsrs get_card_retrievability)
R10 = {1: 0.5518454978750337, 2: 0.7178696551429408, 3: 0.7743669167614039, 4: 0.8866572196242765}


class Reference(unittest.TestCase):
    """Worked cases against py-fsrs: the same memory states, delays and review intervals (no fuzz)."""

    def check(self, steps):
        card = srs.NEW_CARD
        for (month, day, hour, minute), grade, state, s, d, (unit, n) in steps:
            when = at(month, day, hour, minute)
            card = srs.answer(card, grade, when, cid=1, fuzz=False)
            self.assertEqual(card.state, state)
            self.assertAlmostEqual(card.stability, s, places=10)
            self.assertAlmostEqual(card.difficulty, d, places=10)
            if unit == 'secs':
                self.assertEqual(card.due - when, n * 1000)
            else:
                self.assertEqual(card.interval, n)
                self.assertEqual(card.due, srs.day_start(srs.day_number(when) + n))

    def test_the_first_review(self):
        for grade, (state, step, s, d, delay) in FIRST.items():
            card = srs.answer(srs.NEW_CARD, grade, at(6, 1, 10), cid=1, fuzz=False)
            self.assertEqual((card.state, card.step), (state, step))
            self.assertAlmostEqual(card.stability, s, places=12)
            self.assertAlmostEqual(card.difficulty, d, places=12)
            self.assertAlmostEqual(srs.retrievability(10, card.stability), R10[grade], places=12)
            self.check([((6, 1, 10, 0), grade, state, s, d, delay)])

    def test_learning_then_reviews_a_lapse_and_relearning(self):
        self.check(LEARN_THEN_REVIEW)

    def test_hard_repeats_a_step_and_same_day_reviews_grow_stability_slowly(self):
        self.check(HARD_FIRST)

    def test_the_parameters_and_the_curve(self):
        self.assertEqual(len(srs.PARAMETERS), 21)
        self.assertEqual(srs.PARAMETERS[20], 0.1542)
        self.assertAlmostEqual(srs.retrievability(7.5, 7.5), 0.9, places=12)  # R(S, S) = 0.9
        self.assertAlmostEqual(srs.next_interval(7.5), 7.5, places=9)  # at the desired 0.9, I = S
        self.assertGreater(srs.next_interval(7.5, 0.8), 7.5)
        self.assertEqual(srs.retrievability(0, 3.0), 1.0)


class AnkiRules(unittest.TestCase):
    """What Anki does around the memory model."""

    def test_fuzz_as_ankis_own_tests(self):
        for interval, low, high, *expected in [
                (1.0, 1, 1000, 1, 1, 1), (2.49, 1, 1000, 2, 2, 2), (2.5, 1, 1000, 2, 3, 4), (7.0, 1, 1000, 5, 7, 9),
                (17.0, 1, 1000, 14, 17, 20), (37.0, 1, 1000, 33, 37, 41), (2.0, 2, 1000, 2, 2, 2),
                (2.0, 3, 1000, 3, 4, 4), (2.0, 3, 3, 3, 3, 3), (6.9, 3, 1000, 5, 7, 9), (7.1, 3, 1000, 5, 7, 9),
                (19.9, 3, 1000, 17, 20, 23), (20.1, 3, 1000, 17, 20, 23), (100.0, 101, 1000, 101, 105, 108),
                (100.0, 1, 99, 92, 96, 99), (100.0, 97, 103, 97, 100, 103)]:
            self.assertEqual([srs.fuzzed(u, interval, low, high) for u in (0.0, 0.5, 0.99)], expected, interval)
        self.assertEqual([srs.fuzzed(None, x, 1, 100) for x in (1.5, 0.1, 101.0)], [2, 1, 100])  # half away from 0
        self.assertEqual([srs.least_review_interval(2.7269483, 4), srs.least_review_interval(2.7269483, 5),
                          srs.least_review_interval(4.591988, 4)], [4, 0, 5])

    def test_learning_steps_as_ankis_own_tests(self):
        self.assertEqual(srs.hard_delay((60, 600), 0), 330)
        self.assertEqual(srs.hard_delay((60, 600), 1), 600)
        self.assertEqual(srs.hard_delay((600,), 0), 900)
        self.assertEqual(srs.hard_delay((60, 600, 6000), 1), 600)
        self.assertEqual(srs.hard_delay((3 * 1440 * 60,), 0), 4 * srs.DAY_S)  # at most a day more than one step

    def test_a_day_starts_at_four(self):
        self.assertEqual(srs.day_number(at(6, 2, 3, 59)), srs.day_number(at(6, 1, 12)))
        self.assertEqual(srs.day_number(at(6, 2, 4, 0)), srs.day_number(at(6, 1, 12)) + 1)
        self.assertEqual(srs.day_start(srs.day_number(at(6, 2, 9))), at(6, 2, 4))
        # 03:50 and 04:10 are a day apart: the second review is a recall, not a same-day review
        card = srs.answer(srs.answer(srs.NEW_CARD, 3, at(6, 2, 3, 50), 1, False), 3, at(6, 2, 4, 10), 1, False)
        recall = srs.recall_stability(srs.initial_difficulty(3), 2.3065, srs.retrievability(1, 2.3065), 3)
        self.assertAlmostEqual(card.stability, recall, places=12)
        self.assertGreater(card.stability, 2.3065)

    def test_a_step_past_the_day_boundary_counts_in_days(self):
        late = at(6, 2, 3, 55)
        card = srs.answer(srs.NEW_CARD, 3, late, 1)
        self.assertEqual(card.due, at(6, 2, 4))  # 10 minutes would pass 4am: due at that day's start
        self.assertEqual(srs.intervals(srs.NEW_CARD, late, 1)['good'], srs.DAY_S)
        self.assertEqual(srs.intervals(srs.NEW_CARD, late, 1)['again'], 60)

    def test_a_new_cards_buttons(self):
        found = srs.intervals(srs.NEW_CARD, at(6, 1, 12), 1)
        self.assertEqual((found['again'], found['hard'], found['good']), (60, 330, 600))
        self.assertIn(found['easy'] // srs.DAY_S, range(6, 11))  # 8.3 days, fuzzed: 6..10
        self.assertEqual(found['easy'] % srs.DAY_S, 0)
        self.assertEqual(srs.intervals(srs.NEW_CARD, at(6, 1, 12), 1, fuzz=False)['easy'], 8 * srs.DAY_S)

    def test_learning_fuzz_adds_at_most_a_quarter_and_five_minutes(self):
        for cid in range(200):
            card = srs.answer(srs.NEW_CARD, 3, at(6, 1, 12), cid)
            self.assertTrue(600_000 <= card.due - at(6, 1, 12) < 750_000)
        self.assertEqual(srs.learn_fuzz(0.99, 3600), 3600 + 297)
        self.assertEqual(srs.learn_fuzz(None, 3600), 3600)

    def test_hard_good_easy_in_order_and_what_the_buttons_say_is_what_happens(self):
        rng = random.Random(3)
        for cid in range(300):
            card, when = srs.NEW_CARD, at(6, 1, 9)
            for _ in range(rng.randint(2, 9)):
                when += rng.choice([600_000, srs.DAY_MS, 3 * srs.DAY_MS, 11 * srs.DAY_MS, 40 * srs.DAY_MS])
                shown = srs.intervals(card, when, cid)
                if card.state == srs.REVIEW:
                    self.assertTrue(shown['hard'] < shown['good'] < shown['easy'], shown)
                grade = rng.choice([1, 2, 3, 3, 4])
                after = srs.answer(card, grade, when, cid)
                if after.state == srs.REVIEW:
                    self.assertEqual(after.due, srs.day_start(srs.day_number(when) + shown[srs.NAMES[grade]]
                                                              // srs.DAY_S))
                card = after
                self.assertLessEqual(card.interval, srs.MAXIMUM_INTERVAL)

    def test_replay_is_deterministic_and_ordered_by_time(self):
        reviews = [(at(6, 1, 10), 3), (at(6, 1, 10, 10), 3), (at(6, 4, 11), 3), (at(6, 20, 9), 3)]
        one, log = srs.replay(reviews, 77)
        two, _ = srs.replay(list(reversed(reviews)), 77)
        self.assertEqual(one, two)
        self.assertEqual([state for _at, _g, state in log], ['new', 'learning', 'review', 'review'])
        self.assertEqual(srs.replay([], 77), (srs.NEW_CARD, []))
        fuzzed = {srs.replay(reviews, cid)[0].interval for cid in range(60)}
        self.assertGreater(len(fuzzed), 2)  # the fuzz spreads cards with the same history over a few days

    def test_a_lapse_counts_and_relearns(self):
        card = srs.replay([(at(6, 1, 10), 4), (at(6, 12, 10), 1)], 5)[0]
        self.assertEqual((card.state, card.lapses, card.reps), ('relearning', 1, 2))
        self.assertEqual(srs.queue_of(card), 'learning')
        self.assertEqual(srs.intervals(card, at(6, 12, 10, 10), 5)['hard'], 900)  # 1.5 times the single step
        self.assertTrue(srs.is_due(card, at(6, 12, 10, 12)))
        self.assertFalse(srs.is_due(card, at(6, 12, 10, 1)))
        self.assertTrue(srs.is_due(card, at(6, 12, 10, 1), srs.LEARN_AHEAD_S))


if __name__ == '__main__':
    unittest.main()
