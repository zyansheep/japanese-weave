"""Render-time levels in chat replies (twins.py, docs/chat-weave.md "Levels after the fact"): the authoring syntax
(English sentences with <ja> twins), thresholds and propensities, and check-reply's final markup and its round trip.
Run: python -m unittest tests/test_twins.py"""
import io
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import check_reply  # noqa: E402
import feedback  # noqa: E402
import fwlib  # noqa: E402
import grammar  # noqa: E402
import intensity  # noqa: E402
import knowledge  # noqa: E402
import sentence_model  # noqa: E402
import twins  # noqa: E402
import weave  # noqa: E402

NOW = 1_800_000_000_000
DAY = __import__('time').strftime('%Y-%m-%d', __import__('time').localtime(NOW / 1000))
TYPES = {'問題': {'n'}, '情報': {'n'}, '簡単': {'adj-na', 'n'}, '使う': {'v5u'}, '小さい': {'adj-i'}, 'ログ': {'n'},
         '今': {'n', 'adv'}, '読む': {'v5m'}, 'あなた': {'pn'}}
SWAPS = [{'ja': '問題', 'title': 'もんだい · problem', 'en': 'problem', 'kana': 'もんだい', 'p': 0.02, 'free': True},
         {'ja': '情報', 'title': 'じょうほう · information', 'en': 'information', 'kana': 'じょうほう', 'p': 0.3,
          'free': False},
         {'ja': 'ログ', 'title': 'log', 'en': 'log', 'kana': 'ろぐ', 'p': 0.25, 'free': False},
         {'ja': '使う', 'title': 'つかう · use', 'en': 'use', 'kana': 'つかう', 'p': 0.25, 'free': False}]
P = '<abbr title="もんだい · problem">問題</abbr>'
INFO = '<abbr title="じょうほう · information">情報</abbr>'
SMALL = '<abbr title="ちいさい · small">小さい</abbr>'
LOG = '<abbr title="log">ログ</abbr>'
PARAGRAPH = (f'The {P} is small.<ja>{P}は{SMALL}です。</ja> Read the {INFO} below before you start, and check the '
             f'whole thing twice.\nThe {INFO} is in the log.<ja>{INFO}は{LOG}にあります。</ja> After that, run the '
             'tests and look at what they print, line by line, until it all passes.')
AUTHORED = '\n\n'.join([PARAGRAPH] * 4)
# A final markup written by hand, for the readers: a twin from +1, a free word in its English, a learning word from 0
FINAL = ('<span data-jw="ja" data-on="1">' + P + 'は' + SMALL + 'です<abbr title="The problem is small.">。</abbr></span>'
         '<span data-jw="en" data-on="1">The <abbr title="もんだい · problem" data-en="problem" data-on="-2">問題</abbr>'
         ' is small.</span> Read the <abbr title="じょうほう · information" data-en="information" data-on="0">情報</abbr>'
         ' below before you start.')


def plan(level=0, view=None, model=None, statuses=None, learning=None):
    return check_reply.Plan(statuses or grammar.statuses({}), model, level, None, {}, SWAPS, 0.08, learning, view)


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'chats')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Authoring(unittest.TestCase):
    def test_the_english_sentence_before_each_twin(self):
        text = ('- **Short version:** the cache is stale.<ja>A。</ja> Read the docs, e.g. the README, first. Then '
                f'restart it.<ja>B</ja>\nRun `make <ja>x</ja>` and the {P} goes away. <ja>C。</ja>')
        lines, problems, notes = twins.parse_authored(text)
        self.assertEqual(problems, [])
        twinned = [(seg[1], seg[2]) for _n, segs in lines for seg in segs if seg[0] == 'twin']
        self.assertEqual(twinned, [('the cache is stale.', 'A。'), ('Then restart it.', 'B'),
                                   (f'Run `make <ja>x</ja>` and the {P} goes away.', 'C。')])
        self.assertEqual(notes, [])

    def test_what_stops_the_check_and_what_code_fixes(self):
        for text, what in (('No close <ja>A。', 'no </ja>'), ('<ja>A。</ja> at the start.', 'no English sentence'),
                           ('Stray.</ja>', 'no <ja> before'), ('One.<ja>A<ja>B</ja></ja>', 'inside another')):
            with self.subTest(text):
                problems = twins.parse_authored(text)[1]
                self.assertTrue(problems and what in problems[0][1], problems)
        lines, problems, notes = twins.parse_authored('Fix it {{now→<abbr title="いま · now">今</abbr>}}.')
        self.assertEqual(lines[0][1], [('raw', 'Fix it now.')])  # an unsure swap left: back in English
        self.assertIn('unsure', notes[0][1])
        self.assertEqual(twins.parse_authored('```\nA.<ja>x\n```')[1], [])  # code is code

    def test_the_closing_marks_take_the_english(self):
        marked = twins.japanese_twin(f'{P}です。{P}ですか？', 'Is it "the" problem?')
        self.assertEqual(marked.count('<abbr title="Is it &quot;the&quot; problem?">'), 2)
        self.assertTrue(twins.japanese_twin(f'{P}です', 'It is.').endswith('<abbr title="It is.">。</abbr>'))
        self.assertTrue(twins.japanese_twin(f'{P}です', 'Is it?').endswith('？</abbr>'))
        self.assertIn('。</abbr>', twins.japanese_twin(f'{P}です！', 'It is!'))


def element(kind, cost, value=1.0, parent=None, sid=0, fixed=None):
    if kind == 's':
        return twins.Element('s', cost_s=cost, opens=cost, value=value)
    return twins.Element('w', cost_w=cost, opens=cost, value=value, parent=parent, sid=sid, fixed=fixed)


class Thresholds(unittest.TestCase):
    def elements(self):
        els = [element('s', 0.08, 3.0), element('w', 0.25, parent=0, sid='t0'), element('w', 0.0, parent=0,
                                                                                           sid='t0', fixed=-2),
               element('s', 0.08, 1.0), element('s', 0.16, 2.0), element('w', 0.3, sid=1), element('w', 0.2, sid=2),
               element('w', 0.25, sid=3), element('w', 0.4, sid=4, fixed=twins.FULL)]
        return twins.link(els)

    def test_nested_within_budget_and_sentences_peel_first(self):
        els = self.elements()
        budgets = twins.budgets_for(300)
        for seed in range(200):
            on, explored = twins.assign(els, budgets, random.Random(seed))
            self.assertTrue(all(x in twins.SHOWING for x in on))
            for level in twins.BUDGETED:
                words_part, sentences_part = budgets[level]
                shown = [i for i in range(len(els)) if twins.shown_at(els, on, i, level)]
                below = [i for i in range(len(els)) if on[i] <= level - 1]
                self.assertTrue(set(below) <= {i for i in range(len(els)) if on[i] <= level})  # nested
                spent_s = sum(els[i].cost_s for i in shown)
                spent_w = sum(els[i].cost_w for i in shown)
                self.assertLessEqual(spent_s, sentences_part + max(e.cost_s for e in els))  # one at the edge at most
                self.assertLessEqual(spent_w, words_part + max(e.cost_w for e in els))
            for i, e in enumerate(els):
                if e.parent is not None:  # a word in a twin's English never outlasts the twin going down
                    self.assertLessEqual(on[i], on[e.parent])
            self.assertEqual(on[2], -2)  # known at a glance: from -2 (while its twin is English)
            self.assertEqual(on[8], twins.FULL)  # fixed at full: a dictionary word, a later use
        self.assertEqual(twins.assign(els, {lv: (0, 0) for lv in twins.BUDGETED}, random.Random(1))[0][0], twins.FULL)

    def test_value_per_expected_open_orders_them(self):
        els = twins.link([element('s', 0.08, 1.0), element('s', 0.08, 3.0), element('s', 0.16, 2.0)])
        budgets = {lv: (10.0, 0.08) for lv in twins.BUDGETED}  # room for one twin of 0.08 at every level
        on, _x = twins.assign(els, budgets, random.Random(0), explore=0)
        self.assertEqual(on[1], -2)  # 3.0 / 0.08 first
        self.assertEqual(on[0], twins.FULL)

    def test_propensities_match_the_randomization(self):
        els = twins.link([element('s', 0.08, v) for v in (4.0, 3.0, 2.0, 1.0)])
        budgets = {lv: (10.0, 0.08 * (k + 1)) for k, lv in enumerate(twins.BUDGETED)}  # 1, 2, 3, 4, 5 twins
        props = twins.propensities(els, budgets, draws=2000, seed=3)
        # exact: k twins fit at level k-th; the best ranked keeps its place unless explored (EXPLORE) or pushed
        rng, counts, n = random.Random(99), [[0] * 5 for _ in els], 4000
        for _ in range(n):
            on, _x = twins.assign(els, budgets, rng)
            for i in range(len(els)):
                for k, level in enumerate(twins.BUDGETED):
                    counts[i][k] += on[i] <= level
        for i in range(len(els)):
            for k in range(5):
                self.assertAlmostEqual(props[i][k], counts[i][k] / n, delta=0.05)
        self.assertEqual([round(p, 2) for p in props[0][3:]], [1.0, 1.0])  # room for all four from +1
        self.assertTrue(0.6 < props[0][0] < 1.0)  # chosen first, but not always: its place may be drawn
        self.assertTrue(0.0 < props[3][0] < 0.2)  # last, but never never
        self.assertEqual(twins.propensities(els, budgets, draws=300, seed=3),
                         twins.propensities(els, budgets, draws=300, seed=3))  # seeded: a re-check repeats them


class Final(Home):
    def test_round_trip_levels_and_plan(self):
        checked = check_reply.finish(AUTHORED, plan(), TYPES, seed=4, now_ms=NOW)
        self.assertIsNotNone(checked.final, checked.text)
        head, final = checked.text.split('\n' + check_reply.SEPARATOR + '\n')
        self.assertEqual(final, checked.final)
        self.assertIn('Send everything below the ===== line exactly as it is', head)
        self.assertIn(' 0 normal (now)', head)
        back, owners = twins.elements(final)
        self.assertEqual([(b['kind'], b['on'], b['parent']) for b in back],
                         [(e.kind, on, e.parent) for e, on in zip(checked.elements, checked.on)])
        texts = twins.annotation_texts(final)
        for level in (twins.OFF,) + twins.SHOWING:
            shown = twins.render(final, level)
            self.assertNotRegex(shown, 'data-on|data-jw|<ja>')
            self.assertEqual(grammar.annotation_texts(shown),
                             [texts[j] for j, o in enumerate(owners) if twins.visible(back, o, level)])
        self.assertFalse(re.search('[぀-ヿ一-鿿]', twins.render(final, twins.OFF)))  # off: English only
        full = twins.render(final, twins.FULL)
        self.assertEqual(full.count('です<abbr title="The problem is small.">。</abbr>'), 4)  # every twin at full
        self.assertNotIn('The problem is small', re.sub(r'<abbr[^>]*>', '', full))
        self.assertIn('the information is in the log.', head.lower() + twins.render(final, -2).lower())
        # what was dropped and put back: the second twin has bare あります; nothing else
        self.assertIn('twin dropped (its sentence stays English): Japanese outside the <abbr> tags', head)
        kept = twins.plan_for(final)
        self.assertEqual(len(kept['e']), len(back))
        self.assertEqual([e[1] for e in kept['e']], checked.on)
        self.assertEqual((kept['level'], kept['view']), (0, 0))
        self.assertEqual(len(kept['d']), 4)  # the dropped twins' patterns, for intake's counts
        self.assertTrue(all(len(e[2]) == 5 for e in kept['e']))

    def test_limits_and_english_rules(self):
        long = '<abbr title="a very very long sentence">とてもとてもとても長い長い長い文</abbr>です'
        text = (f'This one is long.<ja>{P}は{long}。</ja> We {"<abbr title=\"つかう · use\">使う</abbr>"} the {INFO}.'
                f' You see.<ja><abbr title="you">あなた</abbr>は{P}です。</ja> Three.<ja>{P}。{P}。{P}。</ja>')
        checked = check_reply.finish(text, plan(), TYPES, seed=1, remember=False)
        self.assertIn('too long', checked.text)
        self.assertIn('the pronoun あなた', checked.text)
        self.assertIn('3 Japanese sentences, at most 2', checked.text)
        self.assertIn('swap put back in English: 使う (v5u)', checked.text)
        self.assertIn('We use the', checked.final)  # its English, as the weave had it
        self.assertNotIn('data-jw', checked.final)  # every twin dropped
        blocked = check_reply.finish('It is です here.', plan(), TYPES, remember=False)
        self.assertIsNone(blocked.final)
        self.assertIn('Japanese outside a <ja> twin', blocked.text)
        old_style = check_reply.finish(f'{P}です<abbr title="It is.">。</abbr>', plan(), TYPES, remember=False)
        self.assertIn('a Japanese sentence outside <ja>', old_style.text)

    def test_more_budget_shows_more_and_a_learning_pattern_adds_value(self):
        statuses = dict(grammar.statuses({}), teiru='learning')
        text = ' '.join([f'The {P} waits.<ja>{P}は<abbr title="まっています · is waiting">待っています</abbr>。</ja> '
                         f'The {P} is small.<ja>{P}は{SMALL}です。</ja> And then some more English words here.'] * 5)
        checked = check_reply.finish(text, plan(statuses=statuses, learning='teiru'), TYPES, seed=0, remember=False)
        twin_props = [p for e, p in zip(checked.elements, checked.props) if e.kind == 's']
        waits, small = twin_props[0::2], twin_props[1::2]
        for k in range(5):  # the learning pattern's twins come first, all else equal
            self.assertGreater(sum(p[k] for p in waits) + 1e-9, sum(p[k] for p in small))
        totals = [sum(p[k] for p in twin_props) for k in range(5)]
        self.assertEqual(totals, sorted(totals))  # more at each heavier level
        levels = checked.summary['levels']
        self.assertEqual(levels[3]['twins'], 10)


if __name__ == '__main__':
    unittest.main()
