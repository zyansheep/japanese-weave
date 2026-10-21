"""Offline tests for the yardsticks (NEXT_STEPS step 3): the evaluation harness (scripts/evaluate.py), the simulated
reader (tools/sim) and the convergence check every model change must pass. Run: python -m unittest
tests/test_yardsticks.py"""
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
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
    line within 300 exposures. evaluate.CURRENT is the model knowledge.py runs."""

    def test_a_word_crosses_the_free_line_within_300_exposures(self):
        log = experiments.convergence_log(100, 300, knowledge.prior(None), 0.05, seed=3)
        q = experiments.estimates(evaluate.current(), log, 300)
        self.assertLess(statistics.median(q), fwlib.FREE_P)

    def test_one_word_among_others_crosses_it_too(self):
        # The fit learns the prior's mean from the words themselves, so a hundred words all at 5% pull it down as well.
        # Held at its prior (27%, as for one word among many others that are at 27%), each word still gets there on
        # its own evidence (between 30 and 100 exposures), and has not by 10.
        log = experiments.convergence_log(100, 300, knowledge.prior(None), 0.05, seed=3)
        self.assertEqual(evaluate.CURRENT, 'fit')
        self.assertLess(statistics.median(experiments.estimates(evaluate.Fitted(frozen=True), log, 300)), fwlib.FREE_P)
        self.assertGreater(statistics.median(experiments.estimates(evaluate.Fitted(frozen=True), log, 10)), 0.1)

    def test_without_the_pull_today_s_steps_get_there(self):
        log = experiments.convergence_log(100, 300, knowledge.prior(None), 0.05, seed=3)
        self.assertLess(statistics.median(experiments.estimates(experiments.NoPull(), log, 300)), fwlib.FREE_P)
        self.assertGreater(statistics.median(experiments.estimates(evaluate.Today(), log, 300)), 0.1)


class FreeLine(unittest.TestCase):
    """PLAN.md section 5, rule 1: a word leaves the free set only when the low end of its lookup chance passes 8%, and
    rejoins only when the high end falls below it, so one lookup doesn't flip it (knowledge.free_flag)."""
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
        fit = knowledge.Fit(log, at)
        return {ja: knowledge.free_flag(*fit.estimate(ja), before, fit.prior(ja)[0]) for ja in ('行く', '見る')}, fit

    def test_one_lookup_leaves_a_known_word_free(self):
        log, at = self.log(())
        fit = knowledge.Fit(log, at)
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
