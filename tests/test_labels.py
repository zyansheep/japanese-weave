"""Labels from the fitted model, the replay that compares them with the day-count rules' before the switch, and the
delayed-start experiment (NEXT_STEPS steps 6 and 8). Run: python3 -m unittest discover -s tests"""
import io
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

import evaluate  # noqa: E402
import experiment  # noqa: E402
import export  # noqa: E402
import feedback  # noqa: E402
import fw  # noqa: E402
import fw_host  # noqa: E402
import fwlib  # noqa: E402
import hub  # noqa: E402
import knowledge  # noqa: E402
import relabel  # noqa: E402
import weave  # noqa: E402


class Home(unittest.TestCase):
    """Each test gets its own state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'fw'
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(Path(self.tmp.name) / 'none')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fw(self, *argv):
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(list(argv))
        return out.getvalue()


def peeks(opened, unopened, now, n=40):
    """n episodes, each with every word of `opened` opened and every word of `unopened` left alone."""
    notes = [[w, 40 * i, 0] for i, w in enumerate(unopened + opened)]
    return [{'kind': 'peek', 'word': w, 'at': int((now - 3600 - e * 60 + i) * 1000), 'dwellMs': 1500, 'view': f'v{e}',
             'context': 'c', 'position': len(unopened) + i, 'box': [0, 0, 500, 40], 'annotations': notes,
             'uid': f'u{e}-{i}'} for e in range(n) for i, w in enumerate(opened)]


def before_the_switch(day):
    """A state as the day-count rules left it: no labels from the model yet, no origins, the old fields."""
    state = fwlib.new_state(day)
    del state['labels']
    for w in state['words']:
        w.pop('origin', None)
        w.update(days=[], assumed=[], peeks=[], last_bad='')
    return state


class Relabel(Home):
    def test_the_replay_lists_the_words_the_model_would_label_otherwise_and_writes_nothing(self):
        now = time.time()
        state = before_the_switch(fwlib.today())
        learning = [w['ja'] for w in state['words'] if w['status'] == 'active']
        shaky, known = state['words'][10], state['words'][11]
        fwlib.set_status(shaky, 'shaky', fwlib.today())
        fwlib.set_status(known, 'known', fwlib.today())
        fwlib.save_state(state)
        feedback.record(peeks([known['ja']] + learning[1:], [learning[0], shaky['ja']], now), 'browser')
        before = (self.home / 'state.json').read_bytes()
        found = relabel.preview(now=now)
        self.assertEqual((self.home / 'state.json').read_bytes(), before)
        self.assertFalse((self.home / knowledge.KNOWLEDGE).exists())
        by = {w['ja']: w for w in found['words']}
        self.assertEqual((by[shaky['ja']]['old'], by[shaky['ja']]['new']), ('shaky', 'known'))
        self.assertEqual((by[known['ja']]['old'], by[known['ja']]['new']), ('known', 'learning'))
        self.assertEqual((by[learning[0]]['old'], by[learning[0]]['new']), ('learning', 'known'))
        self.assertEqual({by[ja]['new'] for ja in learning[1:]}, {'learning'})
        text = relabel.summary(found, examples=2)
        self.assertIn('shaky -> known: 1 (shaky by the day rules, known at a glance by the model; 1 with evidence', text)
        self.assertIn(f'  {known["ja"]} ({known["en"]}): lookup chance', text)
        self.assertIn('opened 40 of 40 times shown', text)
        out = self.fw('relabel', '--dry-run')
        self.assertIn('differ. Nothing was changed.', out)
        self.assertEqual((self.home / 'state.json').read_bytes(), before)

    def test_restore_undoes_the_switch_where_the_state_lives(self):
        fwlib.save_state(before_the_switch(fwlib.today()))
        with self.assertRaises(SystemExit):  # nothing to restore before the switch
            self.fw('relabel', '--restore')
        hub.run(hub.stamp({'kind': 'labels', 'labels': [[fwlib.load_state()['words'][0]['ja'], 'active', 'known']]}))
        state = fwlib.load_state()
        self.assertEqual((state['labels']['from'], state['words'][0]['status']), ('model', 'known'))
        self.assertIn('Restored', self.fw('relabel', '--restore'))
        state = fwlib.load_state()
        self.assertEqual((state['labels']['from'], state['words'][0]['status'], state['words'][0]['last_bad']),
                         ('rules', 'active', ''))
        self.assertEqual(knowledge.decide(state, {state['words'][0]['ja']: True}, {}, {}, None, fwlib.today()),
                         {'kind': 'labels', 'labels': [], 'intake': [], 'probes': []})  # refresh leaves it alone
        self.assertIsNotNone(hub.validate(hub.stamp({'kind': 'restore', 'words': []})))



def in_arm(state, ja, arm, days_ago, **more):
    """A word taken in days_ago days ago into an arm of the delayed-start experiment."""
    day = fwlib.add_days(fwlib.today(), -days_ago)
    w = fwlib.find(state, ja)
    w.update(status='active', added=day, changed=day, origin='learning')
    w['arm'] = dict({'arm': arm, 'draw': 0.25 if arm == 'held' else 0.75, 'day': day,
                     'probe': fwlib.add_days(day, fwlib.PROBE_DAY)}, **more)
    return w


class Arms(Home):
    def test_phases_and_the_draw(self):
        w = {'ja': 'x', 'arm': {'arm': 'held', 'draw': 0.1, 'day': '2026-10-01', 'probe': '2026-10-22'}}
        phase = lambda word, day: fwlib.phase(word, day)
        self.assertEqual([phase(w, d) for d in ('2026-10-01', '2026-10-21', '2026-10-22', '2026-11-30')],
                         ['held', 'held', 'probe', 'probe'])
        shown = dict(w, arm=dict(w['arm'], arm='shown'))
        self.assertEqual([phase(shown, d) for d in ('2026-10-01', '2026-10-14', '2026-10-15', '2026-10-21', '2026-10-22')],
                         ['shown', 'shown', 'paused', 'paused', 'probe'])
        self.assertEqual([fwlib.hidden(shown, d) for d in ('2026-10-14', '2026-10-15', '2026-10-22')], [False, True, False])
        self.assertEqual(phase(dict(w, arm=dict(w['arm'], outcome={'y': 0})), '2026-10-23'), 'done')
        self.assertEqual(phase(dict(w, arm=dict(w['arm'], missed='2026-11-05')), '2026-11-05'), 'done')
        self.assertIsNone(phase({'ja': 'y'}, '2026-10-01'))
        self.assertEqual([fwlib.arm_label(x, '2026-10-05') for x in (w, shown)], [None, 'shown'])
        self.assertEqual(fwlib.arm_label(w, '2026-10-22'), 'held:probe')
        # the draw comes from the seed and the word alone, so anyone can check it; about half go to each arm
        self.assertEqual(fwlib.assign(7, '今'), fwlib.assign(7, '今'))
        self.assertNotEqual([fwlib.assign(7, str(i)) for i in range(20)], [fwlib.assign(8, str(i)) for i in range(20)])
        held = sum(fwlib.assign(7, str(i))[0] == 'held' for i in range(2000))
        self.assertTrue(900 < held < 1100, held)
        self.assertEqual(fwlib.assign(7, '今')[1], round(fwlib.draw(7, '今'), 6))

    def test_hidden_words_appear_on_no_surface(self):
        state = fwlib.load_state()  # a new state: five learning words
        words = [w['ja'] for w in state['words'] if w['status'] == 'active']
        queue = [w['ja'] for w in state['words'] if w['status'] == 'queue' and w['ja'] not in fwlib.IN_THE_RULES]
        held, paused, shown, probe = (in_arm(state, queue[0], 'held', 3), in_arm(state, queue[1], 'shown', 15),
                                      in_arm(state, queue[2], 'shown', 2), in_arm(state, queue[3], 'held', 21))
        fwlib.find(state, queue[4]).update(status='known', added='2026-10-01', changed='2026-10-01')
        in_arm(state, queue[4], 'held', 1)['status'] = 'known'  # held while known at a glance: hidden all the same
        fwlib.save_state(state)
        hidden = {held['ja'], paused['ja'], queue[4]}
        self.assertEqual({ja for ja in hidden if fwlib.hidden(fwlib.find(state, ja))}, hidden)
        # the chat brief: no list, no record
        text, _after, plan = weave.compose(state, {})
        free, learning, _p = weave.lists(state, {})
        self.assertFalse(hidden & {w['ja'] for w in free + learning})
        self.assertFalse(hidden & ({o[0] for o in plan['offered']} | set(plan['arms'])))
        self.assertEqual(plan['arms'], {shown['ja']: 'shown', probe['ja']: 'held:probe'})
        self.assertIn(f'Use each of these once in this reply where it fits naturally, like a learning word and on top '
                      f'of the amounts above: {probe["ja"]} = {probe["en"]}', text)
        for ja in hidden:
            self.assertNotIn(f'{ja} = ', text)
            self.assertNotIn(f'; {ja} ', text)
        # the browser: the words the host sends, so the planner's candidates and the web model's word list
        sent = fw_host.handle({'type': 'state'})
        self.assertFalse(hidden & {w['ja'] for w in sent['words']})
        arms = {w['ja']: w.get('arm') for w in sent['words']}
        self.assertEqual((arms[shown['ja']], arms[probe['ja']], arms[words[0]]), ('shown', 'held:probe', None))
        # the claude.ai and ChatGPT blocks: their word lists (their fixed rules name only fwlib.IN_THE_RULES)
        for block in (export.state_line(state), export.chatgpt(state)[0]):
            self.assertFalse([ja for ja in hidden if ja in block])
        # a reply that uses a hidden word anyway: off the lists
        reply = 'The build passes and the cache is warm so nothing else needs doing before release. ' * 4
        self.assertIn('(0 known, 0 learning, 1 off the lists)', weave.calibration(reply + held['ja'], state))
        # fw.py status counts them and names none
        out = self.fw('status')
        self.assertIn('and 3 words held back for the delayed-start experiment (not named', out)
        self.assertFalse([ja for ja in hidden if f' {ja} = ' in out])
        self.assertIn(f'{shown["ja"]} = {shown["en"]}', out)
        # a word whose shown days end is hidden from the next day: the version changes with what is offered
        before = fwlib.version(state)
        in_arm(state, shown['ja'], 'shown', fwlib.SHOW_DAYS)
        self.assertNotEqual(fwlib.version(state), before)
        self.assertNotIn(shown['ja'], {w['ja'] for w in fwlib.public_state(state)['words']})

    def test_probe_outcomes_are_the_first_showing_read_on_or_after_the_probe_day(self):
        state = fwlib.new_state(fwlib.today())
        queue = [w['ja'] for w in state['words'] if w['status'] == 'queue']
        a, b, c = in_arm(state, queue[0], 'held', 21), in_arm(state, queue[1], 'shown', 21), \
            in_arm(state, queue[2], 'held', 21 + fwlib.PROBE_WAIT)
        due = knowledge.day_start(fwlib.today())
        obs = lambda ja, y, read: evaluate.Observation(word=ja, y=y, read=read, weight=max(read, y), surface='browser')
        group = lambda at, *o, episode=False: evaluate.Group(at, 'browser', ('browser', 'v', str(at)), episode, list(o))
        log = evaluate.Log([group(due - 3_600_000, obs(a['ja'], 1, 0.5)),  # before its probe day
                            group(due + 1000, obs(a['ja'], 1, 0.0), episode=True),  # an episode: it only exists opened
                            group(due + 2000, obs(a['ja'], 0, 0.05)),  # hardly on screen: not read
                            group(due + 3000, obs(a['ja'], 0, 0.4), obs(b['ja'], 0.6, 0.0)),
                            group(due + 4000, obs(a['ja'], 1, 0.9))], {}, state['words'])
        found = dict(knowledge.probe_outcomes(state, log, fwlib.today()))
        self.assertEqual(found[a['ja']], {'at': due + 3000, 'surface': 'browser', 'y': 0, 'read': 0.4})
        self.assertEqual(found[b['ja']], {'at': due + 3000, 'surface': 'browser', 'y': 0.6, 'read': 0.05})  # a glance
        self.assertEqual(found[c['ja']], 'missed')  # PROBE_WAIT days with no showing read
        op = hub.stamp({'kind': 'labels', 'probes': [[ja, v] for ja, v in found.items()]})
        self.assertIsNone(hub.validate(op))
        self.assertEqual(hub.apply(state, op)['probes'], 3)
        self.assertEqual([fwlib.phase(w) for w in (a, b, c)], ['done'] * 3)
        self.assertEqual(c['arm']['missed'], fwlib.today())
        self.assertEqual(hub.apply(state, op)['probes'], 0)  # once

    def test_words_the_fixed_texts_name_come_in_outside_the_experiment(self):
        texts = [weave.rules(stage, level, markup, weave.examples({}), 1, 1) for stage in (1, 2, 3, 4)
                 for level in (0, 3) for markup in (False, True)]
        texts += [weave.digest(stage, level, markup, 1, 1) for stage in (1, 3) for level in (0, 3) for markup in (0, 1)]
        texts += [export.rules(), export.rules(short=True)]
        blob = '\n'.join(texts)
        lexicon = fwlib.load_lexicon()
        named, _rest = weave.uses(blob, [w for w in lexicon if w['ja'] in blob])
        examples = {ja for _sentence, needs in weave.EXAMPLES for ja in needs}
        self.assertLessEqual(set(named) | examples, fwlib.IN_THE_RULES)
        state = fwlib.new_state(fwlib.today())
        for w in state['words']:
            if w['ja'] in fwlib.IN_THE_RULES and w['status'] != 'queue':
                w.update(status='queue', added='')
        unspent = {'browser': {'plans': 9, 'budget': 2.0, 'spent': 0.5, 'share': 0.25}}
        state['intake'] = []
        other = next(w['ja'] for w in state['words'][100:] if w['status'] == 'queue')
        op = knowledge.decide(state, {}, {'だから': 9, '今日': 9, other: 5}, unspent, evaluate.Log([], {}, []),
                              fwlib.today(), seed=1)
        self.assertEqual(op['intake'][:3], [['だから', None, None], ['今日', None, None], [other, *fwlib.assign(1, other)]])
        self.assertTrue(all((arm is None) == (ja in fwlib.IN_THE_RULES) for ja, arm, _u in op['intake']))

    def test_labels_ops_are_checked(self):
        good = {'kind': 'labels', 'labels': [['今', 'shaky', 'known']], 'intake': [['前', 'held', 0.4], ['後', None, None]],
                'probes': [['中', 'missed'], ['上', {'at': 1, 'surface': 't3', 'y': 1, 'read': 1}]],
                'experiment': {'seed': 3}}
        self.assertIsNone(hub.validate(hub.stamp(good)))
        for bad in ({'labels': [['今', 'shaky', 'shaky']]}, {'labels': [['今', 'known']]}, {'intake': [['前', 'held', 1.0]]},
                    {'intake': [['前', 'held', None]]}, {'intake': [['前', 'odd', 0.1]]},
                    {'intake': [[str(i), None, None] for i in range(fwlib.NEW_PER_DAY + 1)]},
                    {'probes': [['中', {'at': 1, 'surface': 't3', 'y': 2, 'read': 1}]]}, {'probes': [['中', 'gone']]},
                    {'experiment': {'seed': -1}}, {'experiment': {'seed': 3, 'arms': 2}}, {'words': []}):
            self.assertIsNotNone(hub.validate(hub.stamp(dict(good, **bad))), bad)
        plan = {'level': 0, 'intensity': 1, 'jitter': 1, 'limit': 1, 'candidates': 1, 'budget': 0.1, 'spent': 0.05,
                'arms': {'今': 'held:probe'}}
        num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
        self.assertTrue(feedback._plan_ok(plan, num))
        for bad in ({'arms': {'今': 3}}, {'arms': ['今']}, {'spent': -1}):
            self.assertFalse(feedback._plan_ok(dict(plan, **bad), num), bad)

    def test_the_analysis_reads_the_outcome_back(self):
        state = fwlib.new_state(fwlib.today())
        state['experiment'] = {'name': 'delayed-start', 'seed': 5, 'since': fwlib.add_days(fwlib.today(), -40)}
        queue = [w['ja'] for w in state['words'] if w['status'] == 'queue']
        import random
        rng = random.Random(3)
        for i in range(60):  # held looked up 60% of the time, shown 25%, read chances 0.3 to 0.9
            arm = 'held' if i % 2 else 'shown'
            read = 0.3 + 0.6 * rng.random()
            looked = rng.random() < (0.6 if arm == 'held' else 0.25)
            in_arm(state, queue[i], arm, 25, outcome={'at': 1, 'surface': 'browser', 'y': 1 if looked else 0,
                                                       'read': round(read, 3)})
        for i in range(60, 64):
            in_arm(state, queue[i], 'held' if i % 2 else 'shown', 3)
        found = experiment.arms(state, fwlib.today())
        result = experiment.analysis(found)
        (k1, n1), (k2, n2) = result['held'], result['shown']
        self.assertEqual((n1, n2), (30, 30))
        diff, se, z, p = result['test']
        self.assertAlmostEqual(diff, k2 / n2 - k1 / n1)
        self.assertLess(p, 0.05)
        arm, se, p, _read = result['fit']
        self.assertLess(arm, 0)  # shown words looked up less: exposure taught
        text = experiment.report(state, None, fwlib.today())
        self.assertIn('held: 32 (2 held, 30 probed)', text)
        self.assertIn(f'Outcome, looked up at the probe: held {k1}/30', text)
        self.assertIn('logistic regression with the read chance: shown -', text)
        self.assertIn('words a side due their probe in about', text)
        self.assertNotIn(queue[60], text)  # held words are never named
        self.assertEqual(experiment.two_proportions(0, 0, 1, 2), None)
        self.assertIsNone(experiment.logistic([1, 0], [(0.0,), (1.0,)]))  # too few to fit
        self.assertEqual(experiment.weeks_to([], 80, 0, fwlib.today()), None)
        self.assertAlmostEqual(experiment.weeks_to([], 80, 5, fwlib.today()), (80 / 2.5 + fwlib.PROBE_DAY) / 7)


if __name__ == '__main__':
    unittest.main()
