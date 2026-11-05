"""Render-time levels in chat replies (twins.py, docs/chat-weave.md "Levels after the fact"): the authoring syntax
(English sentences with <ja> twins), thresholds and propensities, check-reply's final markup and its round trip, the
sentence record with levels and its join to T3 messages shown at a level (grammar.record_levels, sentence_model,
feedback's per-level showings), exploration in calibrate, and the transcript readers counting only what was shown.
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


class Join(Home):
    def contexts(self, final, levels):
        out = {}
        for level in levels:
            words = grammar.annotation_texts(twins.render(final, level))
            notes = [[w, 10, 24 * i] for i, w in enumerate(words)]
            key = ('t3', 'v1', feedback.level_context('m1', level))
            out[key] = {'surface': 't3', 'view': 'v1', 'context': key[2], 'annotations': notes, 'words': 80,
                        'shown_at': NOW - 3_600_000 + level, 'height': 24 * len(notes), 'plan': None,
                        'active': 60_000, 'seen_until': NOW - 3_500_000, 'message': 'm1', 'level': level,
                        'stays': [{'y': 0, 'height': 24 * max(1, len(notes)), 'ms': 60_000, 'words': 80}]}
        return out

    def test_each_level_joins_the_sentences_it_shows(self):
        checked = check_reply.finish(AUTHORED, plan(), TYPES, seed=4, now_ms=NOW)
        record = grammar.record(checked.final, '2026-11-05')
        self.assertTrue(grammar.valid_record(record))
        self.assertFalse(re.search('[ぁ-ん一-鿿]', json.dumps(record, ensure_ascii=False)))  # never text
        self.assertEqual(record['e'][0][3], checked.props[0])  # the plan's propensities
        by_fp = sentence_model.records_by_fp([record])
        contexts = self.contexts(checked.final, twins.SHOWING)
        for (_s, _v, key), context in contexts.items():
            level = context['level']
            shown = twins.render(checked.final, level)
            expected = [(s['index'], s['jchars']) for s in grammar.sentences(shown)]
            found = sentence_model.message_sentences(context, by_fp)
            self.assertEqual([(i, chars) for i, chars, _ids, _meta in found], expected, level)
            for _i, _c, _ids, meta in found:
                self.assertEqual(meta[0], level)
                self.assertLessEqual(meta[1], level)  # shown: its threshold is at most the level
                self.assertTrue(meta[2] is None or 0 < meta[2] <= 1)
        rows = []
        units = sentence_model.units(knowledge.showings(rows, contexts, NOW), by_fp)
        per_level = {}
        for u in units:
            per_level[u.level] = per_level.get(u.level, 0) + 1
        self.assertEqual(per_level.get(twins.FULL), 4)  # every twin at full; each level a showing of its own
        self.assertEqual(sum(per_level.values()),
                         sum(len(grammar.sentences(twins.render(checked.final, lv))) for lv in twins.SHOWING))
        report = sentence_model.exploration(units)
        self.assertEqual(report['explored']['units'] + report['ranked']['units'], len(units))
        if report['explored']['units']:
            self.assertIn('Chat twins placed at random (20% of places)', sentence_model.exploration_text(report))

    def test_old_records_and_a_client_without_levels(self):
        old = grammar.record(f'{P}は{SMALL}です<abbr title="It is small.">。</abbr>', '2026-11-05')
        self.assertNotIn('e', old)
        self.assertTrue(grammar.valid_record(old))
        checked = check_reply.finish(AUTHORED, plan(), TYPES, seed=4, now_ms=NOW)
        record = grammar.record(checked.final, '2026-11-05')
        by_fp = sentence_model.records_by_fp([old, record])
        everything = grammar.annotation_texts(re.sub(r'</?span[^>]*>', '', checked.final))
        context = {'annotations': [[w, 0, 24 * i] for i, w in enumerate(everything)]}  # both spans on screen
        found = sentence_model.message_sentences(context, by_fp)
        self.assertEqual(len(found), 4)
        self.assertTrue(all(meta[0] is None for *_rest, meta in found))
        for bad in (dict(record, e=record['e'] + [['x', 0, -1, [], None, None]]), dict(record, fps=record['fps'][:5]),
                    dict(record, lv=7), dict(old, lv=0), dict(record, a=[len(record['e'])])):
            self.assertFalse(grammar.valid_record(bad))

    def test_frequency_counts_the_written_twins_and_the_dropped_ones(self):
        checked = check_reply.finish(AUTHORED, plan(), TYPES, seed=4, now_ms=NOW)
        record = grammar.record(checked.final, DAY)
        self.assertEqual(len(record['d']), 4)
        counts = sentence_model.frequency([record], NOW)
        self.assertEqual(counts['wa'], 8)  # 4 twins kept and 4 dropped, all with は

    def test_a_press_names_the_message_as_shown_at_the_level_then(self):
        checked = check_reply.finish(AUTHORED, plan(), TYPES, seed=4, now_ms=NOW)
        by_fp = sentence_model.records_by_fp([grammar.record(checked.final, '2026-11-05')])
        contexts = self.contexts(checked.final, (-2, twins.FULL))
        changes = [{'at': NOW - 7_200_000, 'surface': 'chat', 'level': 3, 'until': NOW + 3_600_000}]
        press = {'at': NOW - 1_800_000, 'on': True, 'contexts': [{'view': 'v1', 'context': 'm1'}]}
        found = sentence_model.units([], by_fp, presses=[press], contexts=contexts, changes=changes)
        self.assertEqual(len(found), 4)  # at full: every twin was on screen
        self.assertTrue(all(u.pseudo and u.level == twins.FULL for u in found))
        lighter = [dict(changes[0], level=-2)]
        found = sentence_model.units([], by_fp, presses=[press], contexts=contexts, changes=lighter)
        self.assertEqual(len(found), len(grammar.sentences(twins.render(checked.final, -2))))


class Showings(Home):
    def item(self, kind, level, at, **extra):
        notes = [['問題', 10, 0], ['。', 60, 0]] if level is not None and level >= 1 else [['問題', 10, 0]]
        base = {'kind': kind, 'view': 'v1', 'context': 'm1', 'at': at, 'box': [0, 0, 400, 60], 'annotations': notes}
        if level is not None:
            base['level'] = level
        return dict(base, **extra)

    def test_a_level_change_splits_the_showings(self):
        db = self.home / 'log.sqlite'
        now = intensity.now_ms()
        visible = dict(y=0, height=24, ms=4000, wall=4000, away=0, media=0, idle=0)
        result = feedback.record([self.item('shown', 0, now - 9000, words=40),
                                  self.item('pin', 0, now - 8000, word='問題', position=0, dwellMs=900),
                                  self.item('visible', 2, now - 5000, **visible),
                                  self.item('pin', 2, now - 4000, word='。', position=1, dwellMs=900),
                                  self.item('visible', -3, now - 3000, **visible),
                                  self.item('visible', 9, now - 2000, **visible)], 't3', db, now=now,
                                 hide=(now - 60_000, now))
        self.assertEqual((result['stored'], result['rejected']), (4, 2))  # -3: no Japanese; 9: no level
        rows, contexts = feedback.load(db)
        keys = sorted(k[2] for k in contexts)
        self.assertEqual(keys, ['m1@0', 'm1@2'])
        at2 = contexts[('t3', 'v1', 'm1@2')]
        self.assertEqual((at2['message'], at2['level'], at2['words']), ('m1', 2, 40))  # words from its other level
        self.assertEqual(at2['annotations'], [['問題', 10, 0], ['。', 60, 0]])
        self.assertEqual(sorted(r['context'] for r in rows), ['m1@0', 'm1@2'])
        self.assertEqual(len(knowledge.showings(rows, contexts, now + knowledge.SETTLE_MS + 1)), 2)  # two showings
        self.assertEqual(knowledge.without_english(contexts, [{'at': now - 60_000, 'on': True, 'until': now + 1}],
                                                   now), contexts)  # they say what was shown
        old = feedback.record([self.item('shown', None, now - 1000, words=40, context='m2')], 't3', db, now=now,
                              hide=(now - 60_000, now))
        self.assertEqual(old['rejected'], 1)  # a client without levels during English only: as before
        self.assertEqual(feedback.split_context('m1@-2'), ('m1', -2))
        self.assertEqual(feedback.split_context('m1'), ('m1', None))


class Readers(Home):
    WORDS = [{'ja': '問題', 'kana': 'もんだい'}, {'ja': '小さい', 'kana': 'ちいさい'}, {'ja': '情報', 'kana': 'じょうほう'}]

    def transcript(self, text):
        chats = self.home / 'chats' / 'p'
        chats.mkdir(parents=True, exist_ok=True)
        line = json.dumps({'type': 'assistant', 'timestamp': fwlib.today() + 'T12:00:00',
                           'message': {'content': [{'type': 'text', 'text': text}]}}, ensure_ascii=False)
        (chats / 'a.jsonl').write_text(line + '\n')
        return [line]

    def test_render_shows_each_level(self):
        self.assertEqual(twins.render(FINAL, -3), 'The problem is small. Read the information below before you start.')
        self.assertIn('The <abbr title="もんだい · problem">問題</abbr> is small.', twins.render(FINAL, -1))
        self.assertIn('Read the information below', twins.render(FINAL, -1))
        self.assertIn('Read the <abbr title="じょうほう · information">情報</abbr>', twins.render(FINAL, 0))
        self.assertNotIn('is small', re.sub(r'<[^>]+>', '', twins.render(FINAL, 1)))  # the English twin hidden
        self.assertEqual(twins.render('No levels <abbr title="x">今</abbr>.', 0), 'No levels <abbr title="x">今</abbr>.')

    def test_the_exposure_scan_counts_what_was_shown(self):
        self.transcript(FINAL)

        class Tally:
            texts = []

            def count(self, text, used):
                self.texts.append(text)
                return {'1': 1}

        used = {}
        for level in (-3, -1, 0, 1):
            found = fwlib.read_replies(self.WORDS, {}, start='0000-00-00', level=level)
            used[level] = found[0]['words'] if found else []
        self.assertEqual(used, {-3: [], -1: ['問題'], 0: ['問題', '情報'], 1: ['問題', '小さい', '情報']})
        tally = Tally()
        fwlib.read_replies(self.WORDS, {}, start='0000-00-00', tally=tally, level=-1)
        self.assertNotIn('小さい', tally.texts[-1])  # the hidden twin isn't read
        twins.remember_plan(grammar.fingerprint(twins.annotation_texts(FINAL)), {'view': 1, 'level': 1})
        found = fwlib.read_replies(self.WORDS, {}, start='0000-00-00', level=-3)
        self.assertEqual(found[0]['words'], ['問題', '小さい', '情報'])  # as it was shown when sent

    def test_the_hook_reads_its_last_reply_and_turns_as_shown(self):
        lines = self.transcript(FINAL)
        self.assertEqual(twins.view(weave.last_reply(None, lines), -3), twins.render(FINAL, -3))
        turns = weave.recent_turns(lines, 'Next question.', level=-1)
        self.assertIn('The <abbr title="もんだい · problem">問題</abbr> is small.', turns)
        self.assertNotIn('小さい', turns)

    def test_the_backfill_sends_records_with_levels(self):
        checked = check_reply.finish(AUTHORED, plan(), TYPES, seed=4)
        self.transcript(checked.final)
        records = fwlib.read_sentences(str(self.home / 'chats'))
        self.assertEqual(len(records), 1)
        self.assertIn('fps', records[0])
        self.assertEqual(records[0]['lv'], 0)


class Calibration(unittest.TestCase):
    def test_the_twins_line(self):
        meta = lambda x, p=0.05, prop=0.5: (0, 0, prop, p, x)
        found = [sentence_model.Unit(NOW, 8, ('wa',), 1.0, 1.0, meta=meta(1)),
                 sentence_model.Unit(NOW, 8, ('wa',), 0.0, 0.5, meta=meta(1)),
                 sentence_model.Unit(NOW, 8, ('wa',), 0.0, 1.0, meta=meta(0, 0.02, 0.9)),
                 sentence_model.Unit(NOW, 8, ('wa',), 1.0, 1.0, pseudo=True, meta=meta(0)),
                 sentence_model.Unit(NOW, 8, ('wa',), 0.0, 1.0)]  # a reply from before levels
        report = sentence_model.exploration(found)
        self.assertEqual(report['explored'], {'units': 2, 'read': 1.5, 'expected': 0.075, 'seen': 1.0})
        self.assertEqual(report['ranked'], {'units': 1, 'read': 1.0, 'expected': 0.02, 'seen': 0.0})
        self.assertIsNone(sentence_model.exploration(found[3:]))
        calibration = dict(knowledge.calibrate([], {}, [], NOW), twins=report)
        text = knowledge.calibration_text(calibration)
        self.assertIn('Chat twins placed at random (20% of places): 0.07 opens expected and 1 seen over 1.5 read', text)


if __name__ == '__main__':
    unittest.main()
