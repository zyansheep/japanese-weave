"""Labels from the fitted model, the replay that compares them with the day-count rules' before the switch, and the
delayed-start experiment (NEXT_STEPS steps 6 and 8). Run: python3 -m unittest discover -s tests"""
import io
import json
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

import feedback  # noqa: E402
import fw  # noqa: E402
import fwlib  # noqa: E402
import hub  # noqa: E402
import knowledge  # noqa: E402
import relabel  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
