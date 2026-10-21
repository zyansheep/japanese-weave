"""Offline tests for the yardsticks (NEXT_STEPS step 3): the evaluation harness (scripts/evaluate.py), the simulated
reader (tools/sim) and the convergence check every model change must pass. Run: python -m unittest
tests/test_yardsticks.py"""
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tools' / 'sim'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import evaluate  # noqa: E402
import feedback  # noqa: E402
import fwlib  # noqa: E402
import inflection  # noqa: E402
import knowledge  # noqa: E402
import experiments  # noqa: E402
import logs  # noqa: E402
import stats  # noqa: E402
from world import World, DAY, HOUR  # noqa: E402


class Home(unittest.TestCase):
    """Each test gets its own state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'fw'
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(Path(self.tmp.name) / 'no-chats')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Convergence(unittest.TestCase):
    """PLAN.md section 10: a word starting at 27% whose true chance is 5%, read 3 times a day, must cross the 8% free
    line within 300 exposures. evaluate.CURRENT is the model knowledge.py runs. Its estimate is the long-term chance;
    the words' true chance here is the same at every gap, which the fit learns: the floor goes above them."""

    def test_a_word_crosses_the_free_line_within_300_exposures(self):
        log = experiments.convergence_log(100, 300, knowledge.prior(None), 0.05, seed=3)
        model = evaluate.current()
        q = experiments.estimates(model, log, 300)
        self.assertLess(statistics.median(q), fwlib.FREE_P)
        self.assertGreater(model.last.q('w0', 'browser'), 0.02)  # no recency: the floor is above them
        self.assertGreater(model.last.theta[knowledge.FLOOR], knowledge.logit(0.05))

    def test_one_word_among_others_crosses_it_too(self):
        # The fit learns the prior's mean from the words themselves, so a hundred words all at 5% pull it down as well.
        # Held at its prior (27%, as for one word among many others that are at 27%), each word still gets there on
        # its own evidence (between 30 and 100 exposures), and has not by 10. This tests the prior's pull, so recency
        # is off: held at its prior too, the floor (2%) would say these words, at 5% when read 5 hours after the last
        # time, are at about 25% a week after, which is right in a world with that floor and isn't this one.
        log = experiments.convergence_log(100, 300, knowledge.prior(None), 0.05, seed=3)
        self.assertEqual(evaluate.CURRENT, 'fit')
        held = lambda: evaluate.Fitted(frozen=True, recency=False)
        self.assertLess(statistics.median(experiments.estimates(held(), log, 300)), fwlib.FREE_P)
        self.assertGreater(statistics.median(experiments.estimates(held(), log, 10)), 0.1)

    def test_without_the_pull_today_s_steps_get_there(self):
        log = experiments.convergence_log(100, 300, knowledge.prior(None), 0.05, seed=3)
        self.assertLess(statistics.median(experiments.estimates(experiments.NoPull(), log, 300)), fwlib.FREE_P)
        self.assertGreater(statistics.median(experiments.estimates(evaluate.Today(), log, 300)), 0.1)


class FreeLine(unittest.TestCase):
    """PLAN.md section 5, rule 1: a word leaves the free set only when the low end of its lookup chance passes 8%, and
    rejoins only when the high end falls below it, so one lookup doesn't flip it (knowledge.free_flag). On the fit
    without recency: in this log every read is 5 hours after the last, and only two words are ever looked up, so with
    recency the floor would be fitted from those two alone (the Recency tests check the free line with it)."""
    START = '2026-09-01'

    def log(self, opens):
        """60 known words with Anki intervals of 21 to 120 days, each read 30 times over 10 days and never opened, and
        two more: 行く (known, no Anki card) and 見る (an Anki interval of 21 days), read 30 times and opened at the
        exposures `opens`. Returns (Log, the time after it)."""
        t0 = evaluate.day_start(self.START)
        names = [f'w{i}' for i in range(60)]
        intervals = dict({ja: 21 + (i * 99) // 59 for i, ja in enumerate(names)}, **{'見る': 21})
        events = []
        for e in range(30):
            at = t0 + (e // 3) * DAY + (9 + 5 * (e % 3)) * HOUR
            group = evaluate.Group(at, 'browser', ('browser', f'v{e}', 'p'), False, [])
            for ja in names + ['行く', '見る']:
                looked = ja in ('行く', '見る') and e in opens
                group.obs.append(evaluate.Observation(
                    at=at, day=evaluate.day_of(at), surface='browser', key=group.key, episode=False, word=ja,
                    written=ja, form=False, y=1.0 if looked else 0.0, weight=1.0 if looked else 0.6, near=0.0,
                    ms=1, words=1, share=1.0, read=0.6, status='known'))
            events.append(group)
        words = [{'ja': ja, 'kana': '', 'status': 'known', 'added': self.START, 'changed': self.START}
                 for ja in names + ['行く', '見る']]
        return evaluate.Log(events, intervals, words), events[-1].at + HOUR

    def flags(self, opens, before):
        log, at = self.log(opens)
        fit = knowledge.Fit(log, at, recency=False)
        return {ja: knowledge.free_flag(*fit.estimate(ja), before, fit.prior(ja)[0]) for ja in ('行く', '見る')}, fit

    def test_one_lookup_leaves_a_known_word_free(self):
        log, at = self.log(())
        fit = knowledge.Fit(log, at, recency=False)
        for ja in ('行く', '見る'):  # free by their priors (no history) and by their evidence
            self.assertLess(knowledge.sigmoid(-fit.prior(ja)[0]), fwlib.FREE_P, ja)
            self.assertTrue(knowledge.free_flag(*fit.estimate(ja), None, fit.prior(ja)[0]), ja)
        flags, after = self.flags([29], True)  # one lookup, at the last exposure
        self.assertEqual(flags, {'行く': True, '見る': True})
        for ja in ('行く', '見る'):  # though it did raise their chances
            self.assertGreater(after.estimate(ja)[1], 0)
            self.assertLess(after.estimate(ja)[0], fit.estimate(ja)[0])
        self.assertEqual(self.flags([5, 11, 17, 23, 29], True)[0], {'行く': False, '見る': True})  # 1 in 6: 見る's card
        # holds it until the evidence is clearer
        self.assertEqual(self.flags(range(2, 30, 3), True)[0], {'行く': False, '見る': False})  # 1 in 3: they leave
        # the same evidence as one lookup, for words that were out: 見る's high end is under the line, so it rejoins;
        # 行く's chance is under it too (6.5%), but not its high end, so it stays out
        self.assertEqual(self.flags([29], False)[0], {'行く': False, '見る': True})
        self.assertLess(knowledge.sigmoid(-after.estimate('行く')[0]), fwlib.FREE_P)

    def test_the_line_has_hysteresis(self):
        line = knowledge.logit(1 - fwlib.FREE_P)  # k where the chance is FREE_P
        for before in (True, False):  # far from the line: the evidence decides
            self.assertTrue(knowledge.free_flag(line + 1.0, 0.3, before, 0.0))
            self.assertFalse(knowledge.free_flag(line - 1.0, 0.3, before, 0.0))
        self.assertTrue(knowledge.free_flag(line - 0.3, 0.3, True, 0.0))  # its low end is still under the line
        self.assertFalse(knowledge.free_flag(line + 0.3, 0.3, False, 0.0))  # its high end is still over it
        self.assertTrue(knowledge.free_flag(line + 0.1, 1.0, None, line + 0.5))  # no history: the prior's side
        self.assertFalse(knowledge.free_flag(line + 0.1, 1.0, None, line - 0.5))


def simulated(home, days=3, seed=5):
    """A small simulated log in `home`, with an episode (a T3 message without a word count) and a command added."""
    world, _ = logs.simulate(home, days, seed=seed)
    con = feedback.connect(Path(home) / feedback.DB_NAME)
    known = next(w.ja for w in world.words if w.status == 'known')
    at = world.time() - 12 * HOUR
    with con:
        notes = json.dumps([[known, 0, 3], [world.words[-1].ja, 0, 27]], ensure_ascii=False)
        con.execute("INSERT INTO contexts (surface, view, context, annotations, height) "
                    "VALUES ('t3', 'old', 'm', ?, 50)", (notes,))
        con.execute("INSERT INTO interactions (at, surface, kind, word, dwell_ms, view, context, position) "
                    "VALUES (?, 't3', 'peek', ?, 1500, 'old', 'm', 1)", (at, world.words[-1].ja))
    con.close()
    commands = [{'at': at - HOUR, 'word': known, 'kind': 'learn'}]
    return world, commands, world.time() + DAY


class Harness(Home):
    def test_extraction_is_what_replay_learns_from(self):
        world, commands, now = simulated(self.home)
        rows, contexts = feedback.load(self.home / feedback.DB_NAME)
        state = json.loads((self.home / 'state.json').read_text())
        with fwlib.using_home(self.home):
            intervals = knowledge.anki_intervals()
        listed = inflection.lexicon(state['words']).entry
        losses = []
        words, offset = knowledge.replay(rows, contexts, listed, intervals, commands, now, losses=losses)
        log = evaluate.extract(rows, contexts, state['words'], intervals, commands, now)
        groups = [e for e in log.events if isinstance(e, evaluate.Group)]
        self.assertEqual(sum(len(g.obs) for g in groups), sum(w.obs for w in words.values()))
        self.assertEqual(sum(1 for o in log.scored() if o.surface == 'browser'), len(losses))
        self.assertEqual([len(g.obs) for g in groups if g.episode], [2])
        self.assertEqual(sum(isinstance(e, evaluate.Command) for e in log.events), 1)
        today = evaluate.Today()
        today.fit(log, now + 1)
        today.model.finish(now)
        self.assertEqual(today.model.offset, offset)
        self.assertEqual({ja: (w.k, w.n) for ja, w in today.model.words.items()},
                         {ja: (w.k, w.n) for ja, w in words.items()})  # exactly replay's
        o = next(o for o in log.scored() if o.y == 0 and o.read > 0)
        self.assertAlmostEqual(o.read, knowledge._chance(o.ms, o.words, o.share, log.pace, log.top))
        self.assertGreaterEqual(o.weight, max(o.read, o.near))

    def test_statuses_and_forms(self):
        word = {'ja': '行く', 'kana': 'いく', 'status': 'known', 'added': '2026-09-03', 'changed': '2026-09-20'}
        self.assertEqual([evaluate.status_at(word, d) for d in ('2026-09-01', '2026-09-03', '2026-09-20')],
                         ['new', 'learning', 'known'])  # a known word graduated
        self.assertEqual(evaluate.status_at(dict(word, status='shaky'), '2026-09-10'), 'known')
        self.assertEqual(evaluate.status_at(dict(word, status='shaky'), '2026-09-21'), 'learning')
        self.assertEqual(evaluate.status_at(dict(word, status='queue'), '2026-09-21'), 'new')
        self.assertIsNone(evaluate.status_at(None, '2026-09-21'))
        self.assertTrue(evaluate.is_form('行きます', '行く', word))
        self.assertFalse(evaluate.is_form('いく', '行く', word))  # its reading is the word, not a form
        self.assertFalse(evaluate.is_form('行く', '行く'))

    def test_scores_by_day_and_class_from_earlier_days_only(self):
        world, commands, now = simulated(self.home, days=4)
        log = evaluate.load(self.home, now)

        class Spy(evaluate.Constant):
            name = 'spy'
            seen = []

            def fit(self, log, until):
                self.seen.append((until, max([e.at for e in log.events] or [0])))
                super().fit(log, until)

        result = evaluate.evaluate(log, [evaluate.Today(), Spy()], evaluate.load_truth(self.home / logs.TRUTH))
        self.assertEqual(len(result['days']), 4)
        self.assertTrue(all(last < until for until, last in Spy.seen))  # nothing from the day it predicts
        self.assertEqual(len(result['rows']), len(log.scored()))
        s = evaluate.summary(result)
        today = s['models']['today']
        self.assertEqual(today['browser']['n'] + today['t3']['n'], today['all']['n'])
        self.assertEqual(sum(today[c]['n'] for c in ('known', 'learning', 'new', 'form')), today['all']['n'])
        self.assertEqual(today['free']['n'] + today['costly']['n'], today['all']['n'])
        self.assertGreater(today['form']['n'], 0)
        self.assertLess(s['truth']['all']['loss'], today['all']['loss'])  # the floor
        text = evaluate.report_text(result, log)
        self.assertIn('spy against today: mean log loss difference', text)
        self.assertIn('  truth', text.splitlines()[3])

    def test_the_paired_interval_resamples_days(self):
        class Fixed:
            def __init__(self, name, q):
                self.name, self.q = name, q

            def fit(self, log, until):
                pass

            def predict(self, obs):
                return self.q

        days = [f'2026-09-{d:02d}' for d in range(1, 9)]
        events = []
        for i, day in enumerate(days):
            at = evaluate.day_start(day) + 10 * HOUR
            group = evaluate.Group(at, 'browser', ('browser', f'v{i}', 'p'), False, [])
            for j in range(40):
                group.obs.append(evaluate.Observation(at=at, day=day, surface='browser', word=f'w{j}', written=f'w{j}',
                                                      form=False, y=1.0 if j < 4 + i % 3 else 0.0, read=1.0,
                                                      status='known'))
            events.append(group)
        log = evaluate.Log(events, {}, [])
        result = evaluate.evaluate(log, [Fixed('far', 0.5), Fixed('near', 0.12), Fixed('same', 0.5)])
        paired = evaluate.summary(result)['paired']
        near, same = paired['near']['all'], paired['same']['all']
        self.assertLess(near['interval'][1], 0)  # better on every day, and the interval says so
        self.assertLess(near['interval'][0], near['diff'])
        self.assertEqual((near['better'], near['days']), (8, 8))
        self.assertEqual((same['diff'], same['interval'], same['better']), (0.0, [0.0, 0.0], 0))
        few = evaluate.evaluate(evaluate.Log(events[:3], {}, []), [Fixed('far', 0.5), Fixed('near', 0.12)])
        self.assertIsNone(evaluate.summary(few)['paired']['near']['all']['interval'])  # too few days

    def test_the_command_line_scores_a_state_dir(self):
        _world, _commands, now = simulated(self.home)
        with mock.patch('sys.stdout') as out:
            evaluate.main(['--home', str(self.home), '--now', str(now / 1000), '--models', 'constant,today'])
        text = ''.join(c.args[0] for c in out.write.call_args_list)
        self.assertIn('today against constant', text)
        with self.assertRaises(SystemExit):
            evaluate.main(['--home', str(self.home), '--models', 'nonesuch'])


class Simulator(Home):
    def test_a_seeded_world_repeats(self):
        def days(seed):
            world = World(seed=seed)
            return [(s.at, s.surface, [(m.written, m.looked, round(m.q, 9), m.was_read) for m in s.marks])
                    for _ in range(2) for s in world.simulate_day()]

        self.assertEqual(days(4), days(4))
        self.assertNotEqual(days(4), days(5))

    def test_logs_load_through_the_real_code(self):
        world, _ = logs.simulate(self.home, 3, seed=2)
        rows, contexts = feedback.load(self.home / feedback.DB_NAME)
        self.assertEqual(len(contexts), 3 * (world.cfg['chat_sessions'] * world.cfg['chat_replies'] +
                                             world.cfg['web_sessions'] * world.cfg['web_passages']))
        self.assertTrue(all(knowledge.is_showing(c) for c in contexts.values()))
        self.assertTrue(any(c['stays'] and all(s['words'] for s in c['stays']) for c in contexts.values()
                            if c['surface'] == 't3'))
        truth = [json.loads(line) for line in (self.home / logs.TRUTH).read_text().splitlines()]
        self.assertEqual(len(truth), sum(len(c['annotations']) for c in contexts.values()))
        opens = sum(1 for r in rows if r['dwell_ms'] >= knowledge.T3_NOISE_MS)  # the rest: the mouse passing over
        self.assertEqual(sum(t['looked'] for t in truth), opens)
        with fwlib.using_home(self.home):
            know = knowledge.refresh(world.time() / 1000 + 86_400)
        classes = know['calibration']['classes']
        self.assertGreater(classes['t3']['classes']['sentences']['seen'], 0)
        self.assertGreater(classes['browser']['n'], 0)
        log = evaluate.load(self.home, world.time() + DAY)
        known = {(t['surface'], t['view'], t['context'], t['word']) for t in truth}
        self.assertTrue(all((o.key[0], o.key[1], o.key[2], o.word) in known for o in log.scored()))
        for o in log.scored():  # the simulated reader reads by the replay's own formula
            t = next(t for t in truth if (t['view'], t['context'], t['word']) == (o.key[1], o.key[2], o.word))
            self.assertAlmostEqual(o.read, t['read'], places=5)
            break

    def test_the_truth_is_calibrated(self):
        world = World(seed=3)
        expected = seen = 0.0
        for _ in range(5):
            for s in world.simulate_day():
                for m in s.marks:
                    expected += m.read * m.q
                    seen += m.looked
        self.assertLess(abs(seen - expected), 3 * expected ** 0.5)


class Refresh(Home):
    def test_a_refresh_fits_once_and_reuses_the_days_before(self):
        world, _ = logs.simulate(self.home, 3, seed=5)
        now = world.time() / 1000 + 3600  # an hour into the next day: nothing shown yet today
        fits = []
        real = knowledge.Fit

        class Counted(real):
            def __init__(self, log, until, *args, **kwargs):
                fits.append(until)
                super().__init__(log, until, *args, **kwargs)

        with fwlib.using_home(self.home), mock.patch.object(knowledge, 'Fit', Counted):
            first = knowledge.refresh(now)
            grid = len(first['reading']['grid'])
            self.assertEqual(len(fits), grid + 1 + 3)  # the reading model's grid, the fit, one for each day before
            self.assertEqual(sorted(first['daily']), sorted({evaluate.day_of(at) for at in fits[grid + 1:]}))
            fits.clear()
            second = knowledge.refresh(now + 60)
        self.assertEqual(fits, [int((now + 60) * 1000)])  # only the fit itself: the days before are summed already
        self.assertEqual(second['calibration']['classes'], first['calibration']['classes'])
        self.assertEqual(second['dailyFree']['day'], max(first['daily']))
        words = second['words']
        self.assertEqual(len(words), len(world.state()['words']))  # every word, with its fitted prior if nothing else
        self.assertTrue(all(isinstance(e['free'], bool) and e['sd'] > 0 for e in words.values()))
        self.assertEqual(second['offset']['browser'], 0.0)
        self.assertEqual(set(second['prior']), {'card', 'interval', 'none', 'sd'})
        for status in ('active', 'known', 'queue'):
            k, sd = knowledge.prior_estimate(second, status)
            self.assertEqual((k, sd), (second['prior']['none'][{'active': 'learning', 'queue': 'new'}.get(status, status)],
                                       second['prior']['sd']['none']))
        self.assertIsNone(knowledge.prior_estimate({}, 'known'))
        chat = second['calibration']['classes']['t3']['classes']['sentences']
        self.assertGreater(chat['expected'], 0)  # whole sentences are priced
        self.assertTrue(0 < second['sentencePrice']['t3'] < 1)


class Recency(Home):
    """NEXT_STEPS step 10: the shared recency term (knowledge.recency_log_odds) on a clock that weighs each showing by
    the chance it was read (knowledge.last_reads, read_clocks); free and known judged on the long-term chance, cost on
    the current one, and the spacing wait (PLAN.md section 5, rules 1 and 2)."""
    NOW = 1_800_000_000_000
    FLOOR = knowledge.logit(0.02)

    def test_the_clock_weighs_each_showing_by_the_chance_it_was_read(self):
        at, view = self.NOW, ('t3', 'c')
        history = [(at - 3 * DAY, 1.0, ('browser', 'a')), (at - 2 * HOUR, 0.5, ('t3', 'b')),
                   (at - HOUR // 2, 0.2, view)]
        reads, rest = knowledge.last_reads(history, at, view)
        self.assertEqual([round(chance, 3) for _shown, chance, _same in reads], [0.2, 0.4, 0.4])  # 0.8 0.5, 0.8 0.5 1
        self.assertEqual(([same for *_x, same in reads], rest), ([True, False, False], 0.0))
        u, same = knowledge.since_read(reads, rest, at)
        days = knowledge.LONG_DAYS * 24
        self.assertAlmostEqual(u, 0.2 * 0.5 / days + 0.4 * 2 / days + 0.4 * 72 / days)
        self.assertAlmostEqual(same, 0.2)
        # a showing surely skipped doesn't reset the clock; one a week old or more is as if never read
        skipped = [(at - 3 * DAY, 1.0, ('browser', 'a')), (at - HOUR, 0.0, view)]
        self.assertAlmostEqual(knowledge.since_read(*knowledge.last_reads(skipped, at), at)[0], 3 / 7)
        self.assertEqual(knowledge.since_read(*knowledge.last_reads([(at - 8 * DAY, 1.0, view)], at), at), (1.0, 0.0))

    def test_an_unopened_showing_of_a_word_not_known_was_most_likely_skipped(self):
        at, key = self.NOW, ('browser', 'v', 'p')
        events = []
        for i, (y, read) in enumerate(((0.0, 0.6), (0.0, 0.6))):
            group = evaluate.Group(at + i * HOUR, 'browser', key[:1] + (f'v{i}', 'p'), False, [])
            group.obs.append(evaluate.Observation(at=group.at, day=evaluate.day_of(group.at), surface='browser',
                                                  key=group.key, episode=False, word='w', written='w', form=False, y=y,
                                                  weight=read, near=0.0, ms=1, words=1, share=1.0, read=read))
            events.append(group)
        log = evaluate.Log(events, {}, [{'ja': 'w', 'kana': '', 'status': 'active', 'added': '', 'changed': ''}])
        second = events[1].obs[0]
        for q, u in ((0.0, 0.6 / 168 + 0.4), (0.9, None)):  # known: read with its read chance; not: barely at all
            clock = knowledge.read_clocks(log, log.pace, log.top, lambda o, u, s: q)[id(second)]
            r = 0.6 * (1 - q) / (1 - 0.6 * q)
            self.assertAlmostEqual(clock[0], r / 168 + (1 - r))
        self.assertGreater(knowledge.read_clocks(log, log.pace, log.top, lambda o, u, s: 0.9)[id(second)][0], 0.85)

    def test_right_after_a_read_the_chance_falls_to_the_floor(self):
        self.assertEqual(knowledge.recency_log_odds(1.1, 1.0, self.FLOOR), -1.1)  # a week on: the long term
        self.assertAlmostEqual(knowledge.recency_log_odds(1.1, 0.0, self.FLOOR), self.FLOOR, places=4)
        halfway = knowledge.recency_log_odds(1.1, 0.5, self.FLOOR)
        self.assertAlmostEqual(math.exp(halfway), (0.02 + math.exp(-1.1)) / 2 * (1 - 1e-4), places=3)
        self.assertAlmostEqual(knowledge.recency_log_odds(6.0, 0.0, self.FLOOR), -6.0, places=3)  # known better than it

    def test_the_fit_recovers_a_floor_and_the_long_term_chance(self):
        """80 words at 30% a week after a read and 2% (in odds) right after, read each time they are shown at
        exponential gaps (a mean of 12 hours): the fit's floor and long-term chances land near the truth."""
        import random
        rng = random.Random(4)
        t0, events, words = self.NOW, [], []
        long_term = 0.3 / 0.7
        for w in range(80):
            at, last = t0, None
            for i in range(40):
                at += int(rng.expovariate(1 / 12) * HOUR)
                u = 1.0 if last is None else min(1.0, (at - last) / (7 * DAY))
                odds = 0.02 + (long_term - 0.02) * u
                y = 1.0 if rng.random() < odds / (1 + odds) else 0.0
                group = evaluate.Group(at, 'browser', ('browser', f'v{w}-{i}', 'p'), False, [])
                group.obs.append(evaluate.Observation(at=at, day=evaluate.day_of(at), surface='browser', key=group.key,
                                                      episode=False, word=f'w{w}', written=f'w{w}', form=False, y=y,
                                                      weight=1.0, near=1.0, ms=1, words=1, share=1.0, read=0.7))
                events.append(group)
                last = at
            words.append({'ja': f'w{w}', 'kana': '', 'status': 'active', 'added': '', 'changed': ''})
        events.sort(key=lambda e: e.at)
        until = events[-1].at + HOUR
        fit = knowledge.Fit(evaluate.Log(events, {}, words), until)
        self.assertLess(abs(fit.theta[knowledge.FLOOR] - math.log(0.02)), math.log(2))
        self.assertLess(abs(statistics.median(fit.estimate(f'w{w}')[0] for w in range(80)) + math.log(long_term)),
                        math.log(1.5))
        stale = knowledge.Fit(evaluate.Log(events, {}, words), until, recency=False)  # without it: the mean over gaps
        self.assertGreater(statistics.median(stale.estimate(f'w{w}')[0] for w in range(80)), -math.log(0.2))

    def know(self, now):
        hour, recency = HOUR, {'floor': self.FLOOR, 'days': knowledge.LONG_DAYS}
        entry = lambda k, free, ago: {'k': k, 'sd': 0.3, 'free': free, 'obs': 5, 'read': [[now - ago, 1.0]]}
        return {'offset': {'browser': 0.0, 't3': 0.0}, 'recency': recency,
                'words': {'結局': entry(1.1, False, hour), '多分': entry(1.1, False, 84 * hour),
                          '今': entry(4.0, True, hour), '中': entry(2.4, False, hour), '一緒に': entry(2.0, False, hour)}}

    def test_a_learning_word_read_an_hour_ago_is_neither_free_nor_in_the_brief(self):
        import weave
        now = int(time.time() * 1000)
        know = self.know(now)
        state = {'stage': 3, 'stage_pinned': True, 'paused': False,
                 'words': [dict(word_entry(ja, status), added='2026-10-01') for ja, status in
                           (('結局', 'active'), ('多分', 'active'), ('今', 'known'), ('中', 'shaky'),
                            ('一緒に', 'active'))]}
        free, learning, p = weave.lists(state, {}, know, now_ms=now)
        self.assertEqual(([w['ja'] for w in free], [w['ja'] for w in learning]), (['今'], ['多分', '中']))
        # 結局 waits: an hour after a read it is near the floor, though its long-term chance is 25%; free is judged on
        # that, so it isn't free either. 一緒に (12% long-term) waits too, until it is back over SPACING_P (5.5 days).
        # 多分, read 3.5 days ago, is halfway back (in odds above the floor). 中 isn't free (8.3% long-term, its flag
        # still out) but is cheap all along: it never waits, else a word in the free line's hysteresis would never be
        # shown again
        self.assertLess(knowledge.current_chance(know['words']['結局'], know['recency'], now), 0.03)
        self.assertEqual(knowledge.word_estimate(know, '結局', 't3', now)[0], fwlib.FREE_P)
        self.assertAlmostEqual(knowledge.word_estimate(know, '結局', 't3')[0], knowledge.sigmoid(-1.1))
        halfway = p['多分'] / (1 - p['多分'])
        self.assertAlmostEqual(halfway, 0.02 + (math.exp(-1.1) - 0.02) / 2, places=3)
        self.assertGreater(p['多分'], knowledge.SPACING_P)
        self.assertNotIn('結局', weave.brief(state, {}, know=know))
        self.assertIn('多分', weave.brief(state, {}, know=know))
        # the next turn's line on the last reply still counts 結局 as a learning word, not as off the lists
        self.assertIn('結局', [w['ja'] for w in weave.lists(state, {}, know, now_ms=now, wait=False)[1]])

    def test_knowledge_json_says_when_each_word_was_last_read(self):
        world, _ = logs.simulate(self.home, 3, seed=5)
        now = world.time() / 1000 + 3600
        with fwlib.using_home(self.home):
            know = knowledge.refresh(now)
        self.assertTrue(know['fit']['cold'])  # the day's first fit starts from nothing, from two floors
        self.assertEqual(set(know['recency']), {'floor', 'context', 'days', 'softMin', 'spacing'})
        read = {ja: e for ja, e in know['words'].items() if e.get('read')}
        self.assertGreater(len(read), 50)
        for entry in read.values():
            self.assertTrue(all(at < now * 1000 and 0 < chance <= 1 for at, chance in entry['read']))
            self.assertLessEqual(sum(chance for _at, chance in entry['read']), 1.002)  # each rounded to 0.001
            current = knowledge.current_chance(entry, know['recency'], int(now * 1000))
            self.assertLessEqual(current, knowledge.sigmoid(-entry['k']) + 1e-9)  # under the long term, or at it
        import fw_host
        with fwlib.using_home(self.home):
            words = [dict(w) for w in world.state()['words']]
            reply = fw_host.adaptive(world.state(), words)
        self.assertEqual(reply['recency'], {'floor': know['recency']['floor'], 'days': knowledge.LONG_DAYS})
        self.assertEqual({w['ja'] for w in words if 'read' in w}, set(read))


def word_entry(ja, status):
    return {'ja': ja, 'en': 'meaning', 'hint': 'hint', 'status': status, 'changed': '', 'days': [], 'last_bad': ''}


class Stats(unittest.TestCase):
    def test_rank_correlation_and_logistic_regression(self):
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4], [10, 20, 30, 45]), 1.0)
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertEqual(stats.ranks([3, 1, 3]), [2.5, 1.0, 2.5])
        ys = [0, 0, 1, 0, 1, 1, 0, 1, 1, 1] * 20
        xs = [(0.0,)] * 5 + [(1.0,)] * 5
        beta, se = stats.logistic(ys, xs * 20)
        self.assertAlmostEqual(beta[0], stats.logit(0.4), places=6)
        self.assertAlmostEqual(beta[1], stats.logit(0.8) - stats.logit(0.4), places=6)
        self.assertGreater(se[1], 0)
        self.assertIsNone(stats.logistic([0, 0, 1, 1], [(0.0,), (0.0,), (1.0,), (1.0,)]))  # separated


if __name__ == '__main__':
    unittest.main()
