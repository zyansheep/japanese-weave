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


class ReviewRound1(Home):
    """Regression tests for the first adversarial review (scratchpad review-b/py/t1.py..t10.py)."""
    TWIN = f'<ja>{P}は{SMALL}です。</ja>'
    FILLER = ' Then run the tests and read what they print, line by line, until it all passes and nothing is left.'

    def twins_of(self, text):
        lines, problems, _notes = twins.parse_authored(text)
        self.assertEqual(problems, [])
        return [(seg[1], seg[3]) for _n, segs in lines for seg in segs if seg[0] == 'twin']

    def test_t1_sentence_ends_after_any_mark(self):
        for before in ('The rate rose 5%. ', 'He said “stop.” ', 'Wait... ', 'Done ✅ ', 'It is 30°. ',
                       'Use ~~that~~. ', 'It is _fine_. '):
            with self.subTest(before):
                self.assertEqual(self.twins_of(f'{before}The {P} is small.{self.TWIN}'),
                                 [(f'The {P} is small.', None)])
        why = twins.twin_trouble('x', 0, 'Is it fine? Yes, it runs! Ship it.', '')
        self.assertEqual(why, 'its English holds more than one sentence')

    def test_t2_t7_t9_twins_that_cut_through_markdown_stay_english(self):
        cases = {f'**Bold intro. The {P} is small.**{self.TWIN}': 'leaves markdown open (**)',
                 f'*Italic intro. The {P} is small.*{self.TWIN}': 'leaves markdown open (*)',
                 f'[Read this. The {P} is small](http://x/y).{self.TWIN}': 'its English leaves markdown open ([)',
                 f'[The {P} is small.](http://x){self.TWIN}': 'holds a link',
                 f'| a | The {P} is small.{self.TWIN} |': 'in a table',
                 f'- [ ] The {P} is small.{self.TWIN}': 'in a task list',
                 f'Run `make` and the {P} is small.<ja>{P}は**{SMALL}です。</ja>': 'its Japanese leaves markdown open',
                 f'**Note:** run it. The {P} is small `x.{self.TWIN}': 'leaves markdown open (`)'}
        for text, why in cases.items():
            with self.subTest(text):
                found = self.twins_of(text)
                self.assertEqual(len(found), 1)
                self.assertIn(why, found[0][1] or '')
                checked = check_reply.finish(text + self.FILLER, plan(), TYPES, seed=1, remember=False)
                self.assertNotIn('data-jw', checked.final)  # its sentence stays English
                self.assertIn('twin dropped (its sentence stays English): ' + found[0][1], checked.text)
        for text in (f'Term: the {P} is small.{self.TWIN}', f'See [the docs](http://x) because the {P} is small.'
                     f'{self.TWIN}'.replace('See [the docs](http://x) because', 'See the docs. Then')):
            self.assertIsNone(self.twins_of(text)[0][1], text)
        self.assertIn('cuts through markdown', weave.DRAFT_RULES)

    def test_t3_t4_each_level_joins_and_propensities_match(self):
        checked = check_reply.finish(AUTHORED, plan(), TYPES, seed=3, now_ms=NOW)
        by_fp = sentence_model.records_by_fp([grammar.record(checked.final, DAY)])
        for level in twins.SHOWING:
            notes = grammar.annotation_texts(twins.render(checked.final, level))
            found = sentence_model.message_sentences({'annotations': [[a, 0, 20 * k] for k, a in enumerate(notes)],
                                                      'level': level}, by_fp)
            self.assertEqual(len(found), sum(1 for a in notes if a in grammar.MARKS))
        budgets = twins.budgets_for(check_reply.words_of(twins.render(checked.final, -3)))
        rng, n = random.Random(12345), 3000
        counts = [[0] * 5 for _ in checked.elements]
        for _ in range(n):
            on, _x = twins.assign(checked.elements, budgets, rng)
            for i in range(len(on)):
                for k, level in enumerate(twins.BUDGETED):
                    counts[i][k] += twins.shown_at(checked.elements, on, i, level)
        for i, row in enumerate(counts):
            for k in range(5):
                self.assertAlmostEqual(checked.props[i][k], row[k] / n, delta=0.05)

    def test_t5_a_sentence_ending_right_after_a_gloss_ends_there(self):
        text = f'Read the {INFO}. Then check the {LOG} and the {INFO} again. Look at the {LOG}! Fine, the {LOG}? Yes.'
        parts = twins.reply_parts(twins.parse_authored(text)[0])
        self.assertEqual([p['sid'] for p in parts if isinstance(p, dict)], [(1, 0), (1, 1), (1, 1), (1, 2), (1, 3)])

    def test_t6_a_twin_with_a_dictionary_word_or_a_later_use_shows_only_at_full(self):
        swaps = SWAPS + [{'ja': '簡単', 'title': 'かんたん · simple', 'en': 'simple', 'kana': 'かんたん', 'p': 0.05,
                          'free': False, 'dictionary': True}]
        own = check_reply.Plan(grammar.statuses({}), None, 0, None, {}, swaps, 0.08, None, None)
        easy = '<abbr title="かんたん · simple">簡単</abbr>'
        text = f'The {P} is simple.<ja>{P}は{easy}です。</ja>' + self.FILLER * 3
        for seed in range(30):
            checked = check_reply.finish(text, own, TYPES, seed=seed, remember=False)
            self.assertEqual([on for e, on in zip(checked.elements, checked.on) if e.kind == 's'], [twins.FULL])
        later = (f'Read the {INFO} first.' + self.FILLER + f' The {INFO} is small.<ja>{INFO}は{SMALL}です。</ja>')
        checked = check_reply.finish(later, plan(), TYPES, seed=1, remember=False)
        self.assertEqual([on for e, on in zip(checked.elements, checked.on) if e.kind == 's'], [twins.FULL])

    def test_t7_an_explored_twin_can_show_at_every_level(self):
        text = f'The {INFO} is small.<ja>{INFO}は{SMALL}です。</ja>' + self.FILLER
        checked = check_reply.finish(text, plan(), TYPES, seed=1, remember=False)
        twin = next(p for e, p in zip(checked.elements, checked.props) if e.kind == 's')
        self.assertTrue(all(x > 0 for x in twin), twin)

    def test_t8_a_message_first_shown_in_english_becomes_a_showing_later(self):
        db = self.home / 'log.sqlite'
        now = intensity.now_ms()
        notes = [['問題', 10, 0], ['。', 60, 0]]
        item = lambda kind, level, at, **kw: dict({'kind': kind, 'view': 'v1', 'context': 'm1', 'at': at,
                                                   'box': [0, 0, 600, 400], 'annotations': notes, 'level': level}, **kw)
        visible = dict(y=0, height=24, ms=4000, wall=4000, away=0, media=0, idle=0)
        result = feedback.record([item('shown', -3, now - 9000, words=40), item('visible', 0, now - 5000, **visible),
                                  item('pin', 0, now - 4000, word='。', position=1, dwellMs=900),
                                  item('visible', -3, now - 3000, **visible)], 't3', db, now=now)
        self.assertEqual((result['stored'], result['rejected']), (3, 1))
        rows, contexts = feedback.load(db)
        english, at0 = contexts[('t3', 'v1', 'm1@-3')], contexts[('t3', 'v1', 'm1@0')]
        self.assertEqual((english['annotations'], knowledge.is_showing(english)), ([], False))
        self.assertEqual(at0['words'], 40)
        self.assertEqual(len(knowledge.showings(rows, contexts, now + knowledge.SETTLE_MS + 1)), 1)
        feedback.record([item('shown', 0, now - 2000, words=55)], 't3', db, now=now)  # the level's own count wins
        self.assertEqual(feedback.load(db)[1][('t3', 'v1', 'm1@0')]['words'], 55)
        self.assertEqual(feedback.stats(path=db)['blankPassages'], [0, 0])  # no blank web passage

    def test_t10_a_reply_checked_under_english_only_counts_at_the_chat_level(self):
        old = check_reply.finish(AUTHORED, plan(level=0, view=-3), TYPES, seed=3, now_ms=NOW)  # a plan from before
        self.assertGreater(twins.view(old.final, level=0).count('<abbr'), 0)
        self.assertEqual(twins.view(old.final, level=-3).count('<abbr'), 0)  # chat off now: none
        state = fwlib.new_state(known_top=10)
        state.setdefault('prefs', {})[intensity.ENGLISH] = intensity.english_at(True, 't3')
        fwlib.save_state(state)
        self.assertEqual(check_reply.Plan.load(1).view, 1)  # English only is a view: the reply is read at 1
        self.assertEqual(twins.level_now(state), 0)

    def test_8_no_plan_means_send_the_english(self):
        with mock.patch.object(check_reply.Plan, 'load', side_effect=OSError('no state')), \
                mock.patch('sys.stdin', new=io.StringIO(f'The {P} is small.{self.TWIN}')), \
                mock.patch('sys.stdout', new=io.StringIO()) as out:
            import fw
            fw.main(['check-reply'])
        self.assertIn('Send your English reply as it is, with the <ja> twins taken out', out.getvalue())
        self.assertNotIn(check_reply.SEPARATOR, out.getvalue())

    def test_9_code_with_a_closing_span_in_a_twins_english(self):
        text = f'Close with `</span>` since the {P} is small.{self.TWIN}' + self.FILLER
        checked = check_reply.finish(text, plan(), TYPES, seed=1, remember=False)
        back, _owners = twins.elements(checked.final)
        self.assertEqual([b['kind'] for b in back].count('s'), 1)
        self.assertIn('`</span>`', twins.render(checked.final, -3))
        self.assertNotIn('`</span>`', twins.render(checked.final, 3))

    def test_10_japanese_quoted_in_backticks_passes(self):
        checked = check_reply.finish(f'Write it as `問題` here.' + self.FILLER, plan(), TYPES, remember=False)
        self.assertIsNotNone(checked.final)
        self.assertIn('quoted in backticks', weave.DRAFT_RULES)
        self.assertIsNone(check_reply.finish('It is 問題 here.' + self.FILLER, plan(), TYPES, remember=False).final)

    def test_11_an_item_without_a_level_is_stored_as_before(self):
        db = self.home / 'log.sqlite'
        now = intensity.now_ms()
        shown = {'kind': 'shown', 'view': 'v1', 'context': 'old1', 'at': now - 1000, 'box': [0, 0, 1, 1],
                 'annotations': [['問題', 0, 0]], 'words': 30}
        self.assertEqual(feedback.record([shown], 't3', db, now=now)['stored'], 1)
        context = feedback.load(db)[1][('t3', 'v1', 'old1')]
        self.assertEqual((context['message'], context['level'], context['words']), ('old1', None, 30))


class ReviewRound2(Home):
    """Regression tests for the second adversarial review (scratchpad review-r2a, review-r2c)."""
    FILLER = ReviewRound1.FILLER
    TWIN = ReviewRound1.TWIN
    AGAIN = '<abbr title="again / also">また</abbr>'

    def test_1_each_use_of_a_word_and_gloss_keeps_its_own_english(self):
        swaps = SWAPS + [{'ja': 'また', 'title': 'again / also', 'en': 'again', 'p': 0.02, 'free': True,
                          'before': 'Run the tests ', 'after': ' and check the result.'},
                         {'ja': 'また', 'title': 'again / also', 'en': 'also', 'p': 0.02, 'free': True,
                          'before': 'the result. It ', 'after': ' fixes the bug.'}]
        own = check_reply.Plan(grammar.statuses({}), None, 0, None, {}, swaps, 0.08, None, None)
        text = f'Run the tests {self.AGAIN} and check the result. It {self.AGAIN} fixes the bug.' + self.FILLER
        final = check_reply.finish(text, own, dict(TYPES, また={'n', 'adv'}), seed=1, remember=False).final
        self.assertEqual(re.findall(r'data-en="([^"]*)"', final), ['again', 'also'])

    def test_2_a_twins_words_the_weave_did_not_offer(self):
        words = [{'ja': '小さい', 'kana': 'ちいさい', 'status': 'active'}, {'ja': '問題', 'kana': 'もんだい',
                                                                         'status': 'known'}]
        own = check_reply.Plan(grammar.statuses({}), None, 0, None, {}, SWAPS, 0.08, None, None, words)
        text = f'The {P} is small.{self.TWIN}' + self.FILLER
        checked = check_reply.finish(text, own, TYPES, seed=1, remember=False)
        twin = next(e for e in checked.elements if e.kind == 's')
        self.assertAlmostEqual(twin.cost_w, 1 / (1 + math.e))  # 小さい: a learning word at its prior, now priced
        self.assertIsNone(twin.fixed)
        new_word = '<abbr title="あたらしい · new">新しい</abbr>'  # not in the word list: full only
        text = f'The {P} is new.<ja>{P}は{new_word}です。</ja>' + self.FILLER
        checked = check_reply.finish(text, own, TYPES, seed=1, remember=False)
        self.assertEqual([on for e, on in zip(checked.elements, checked.on) if e.kind == 's'], [twins.FULL])
        also = '<abbr title="also">も</abbr>'  # grammar costs nothing and limits nothing
        text = f'The {P} is small too.<ja>{P}{also}{SMALL}です。</ja>' + self.FILLER
        checked = check_reply.finish(text, own, TYPES, seed=1, remember=False)
        twin = next(e for e in checked.elements if e.kind == 's')
        self.assertAlmostEqual(twin.cost_w, 1 / (1 + math.e))
        self.assertIsNone(twin.fixed)

    def test_3_an_opened_twin_leaves_the_english_around_it_as_read(self):
        info = '<abbr title="じょうほう · information" data-en="information" data-on="-2">情報</abbr>'
        final = (f'Read the {info} below. <span data-jw="ja" data-on="0">{P}は{SMALL}です'
                 '<abbr title="The problem is small.">。</abbr></span><span data-jw="en" data-on="0">The problem is '
                 f'small.</span> The {info} is long.')
        by_fp = sentence_model.records_by_fp([grammar.record(final, DAY)])
        notes = [[w, 10, 0] for w in twins.shown_texts(final, 0)]
        self.assertEqual([n[0] for n in notes], ['情報', '問題', '小さい', '。', '情報'])
        context = {'surface': 't3', 'annotations': notes, 'level': 0}
        self.assertEqual(sentence_model.mark_twins({('t3', 'v', 'c'): context}, by_fp), 1)
        self.assertEqual(knowledge._in_english(notes, {3: 1.0}, context['twins']), {1, 2})  # the twin's own words
        self.assertEqual(knowledge._in_english(notes, {3: 1.0}), {0, 1, 2})  # before: the English one too
        observed = knowledge.observed('t3', context, [{'kind': 'pin', 'word': '。', 'position': 3, 'dwell_ms': 900}],
                                      {'情報', '問題', '小さい'})
        self.assertIn('情報', [o[0] for o in observed])  # read in the English sentence before it

    def test_4_a_single_letter_and_a_period_ends_no_sentence(self):
        for text in (f'Use the U.S. version, the {P} is small.{self.TWIN}', f'Meet at 9 a.m. tomorrow, the {P} is '
                     f'small.{self.TWIN}'):
            lines, _problems, _notes = twins.parse_authored(text)
            found = [seg for _n, segs in lines for seg in segs if seg[0] == 'twin']
            self.assertEqual((found[0][1], found[0][3]), (text.split('<ja>')[0], None))

    def test_5_the_clients_rule_for_malformed_markup(self):
        ja = f'<span data-jw="ja" data-on="1">{P}は{SMALL}です<abbr title="The problem is small.">。</abbr></span>'
        en = '<span data-jw="en" data-on="1">The problem is small.</span>'
        shown = lambda text, level: twins.shown_texts(text, level)
        self.assertEqual(shown(ja + ' \n' + en, 0), [])  # whitespace between: still a pair
        self.assertEqual(shown(ja + ' \n' + en, 1), ['問題', '小さい', '。'])
        self.assertEqual(shown(en.replace('The problem', P), 3), ['問題'])  # a lone English twin always shows
        self.assertEqual(twins.render(ja, 0), 'The problem is small.')  # a lone Japanese one: its translation below
        self.assertEqual(shown(ja, 1), ['問題', '小さい', '。'])
        unclosed = ja + en[:-len('</span>')]
        self.assertEqual(shown(unclosed, 3), [])  # broken (an English twin left open): every pair English
        self.assertIn('The problem is small.', twins.render(unclosed, 3))
        nested = (f'<span data-jw="ja" data-on="1">x {ja.replace("on=\"1\"", "on=\"2\"")}'
                  f'{en.replace("on=\"1\"", "on=\"2\"")}</span><span data-jw="en" data-on="1">outer</span>')
        self.assertEqual(shown(nested, 3), [])
        for bad in ('4', '+1', '1.0', ' 1', '', '-0', '-3'):  # not exactly -2..3: as written
            word = f'The <abbr title="もんだい · problem" data-en="problem" data-on="{bad}">問題</abbr> is small.'
            self.assertEqual(shown(word, -2), ['問題'], bad)
        for quoted in ("data-on='1'", 'data-on=1', 'DATA-ON="1"'):
            word = f'The <abbr title="もんだい · problem" data-en="problem" {quoted}>問題</abbr> is small.'
            self.assertEqual((shown(word, -2), shown(word, 1)), ([], ['問題']), quoted)
        empty = 'The <abbr title="もんだい · problem / issue" data-en="" data-on="1">問題</abbr> is small.'
        self.assertEqual(twins.render(empty, 0), 'The problem is small.')  # data-en "": the first meaning
        self.assertEqual(shown(f'Use `{P}` and {P} here <abbr data-on="1">今</abbr>.'.replace('here', 'here'), 1),
                         ['問題'])  # glosses in code and without a title are none
        self.assertEqual(shown('    ' + ja + en + '\n\nAfter.', 1), [])  # an indented code block
        self.assertFalse(twins.well_formed(ja))
        self.assertIsNone(grammar.record(ja + ' ' + en.replace('on="1"', 'on="2"'), DAY))  # malformed: no record

    def test_6_items_at_minus_3_have_reasons(self):
        db = self.home / 'log.sqlite'
        now = intensity.now_ms()
        base = {'view': 'v1', 'context': 'm1', 'box': [0, 0, 1, 1], 'annotations': [], 'level': -3}
        result = feedback.record([dict(base, kind='shown', at=now - 3000, words=20),
                                  dict(base, kind='visible', at=now - 2000, y=0, height=24, ms=4000, wall=4000, away=0,
                                       media=0, idle=0)], 't3', db, now=now)
        self.assertEqual((result['stored'], result['rejected']), (1, 1))
        self.assertEqual([r['item'] for r in result['reasons']], [1])
        self.assertIn('no Japanese on screen', result['reasons'][0]['error'])


class ReviewRound3(Home):
    """Regression tests for the third adversarial review (scratchpad review-r2a reply3*.txt, review-r2c/r3)."""
    FILLER = ReviewRound1.FILLER
    TWIN = ReviewRound1.TWIN
    AGAIN = ReviewRound2.AGAIN

    def test_1_an_abbreviation_before_a_capital_is_ambiguous(self):
        for before in ('It works in the U.S. The', 'Then run plan A. The', 'Pick option B. The',
                       'Ask Dr. Smith if the'):
            text = f'{before} {P} is small.{self.TWIN}'
            lines, _problems, _notes = twins.parse_authored(text)
            found = [seg for _n, segs in lines for seg in segs if seg[0] == 'twin']
            self.assertEqual(found[0][1], text.split('<ja>')[0], before)  # its English isn't cut at a guess
            self.assertIn('where its sentence starts is unclear', found[0][3])
            checked = check_reply.finish(text + self.FILLER, plan(), TYPES, seed=1, remember=False)
            self.assertNotIn('data-jw', checked.final)  # its sentence stays English, whole
            self.assertIn('where its sentence starts is unclear', checked.text)
        for text in (f'Use e.g. the cache, the {P} is small.{self.TWIN}', f'Meet at 9 a.m. tomorrow, the {P} is small.'
                     f'{self.TWIN}'):
            lines, _problems, _notes = twins.parse_authored(text)
            found = [seg for _n, segs in lines for seg in segs if seg[0] == 'twin']
            self.assertEqual((found[0][1], found[0][3]), (text.split('<ja>')[0], None))
        self.assertIsNone(twins.twin_trouble('x', 0, 'Use e.g. this or i.e. that one now.', ''))  # abbreviations

    def test_2_a_dropped_earlier_swap_leaves_the_later_ones_their_english(self):
        swaps = SWAPS + [{'ja': 'また', 'title': 'again / also', 'en': 'again', 'p': 0.02, 'free': True,
                          'before': 'Run the tests ', 'after': ' and check the result.'},
                         {'ja': 'また', 'title': 'again / also', 'en': 'also', 'p': 0.02, 'free': True,
                          'before': 'the result. It ', 'after': ' fixes the bug.'}]
        own = check_reply.Plan(grammar.statuses({}), None, 0, None, {}, swaps, 0.08, None, None)
        types = dict(TYPES, また={'n', 'adv'})
        text = f'Run the tests again and check the result. It {self.AGAIN} fixes the bug.' + self.FILLER
        final = check_reply.finish(text, own, types, seed=1, remember=False).final
        self.assertEqual(re.findall(r'data-en="([^"]*)"', final), ['also'])
        unsure = [dict(x, before='', after='') for x in swaps]  # no context to tell: the gloss's first meaning
        own = check_reply.Plan(grammar.statuses({}), None, 0, None, {}, unsure, 0.08, None, None)
        final = check_reply.finish(text, own, types, seed=1, remember=False).final
        self.assertEqual(re.findall(r'data-en="([^"]*)"', final), ['again'])

    def test_3_a_twins_unoffered_words_are_priced_as_the_weave_prices(self):
        know = {'offset': {'t3': 1.5}, 'words': {}}  # the chat offset, which the weave doesn't apply
        words = [{'ja': '小さい', 'kana': 'ちいさい', 'status': 'active', 'k': 0.5, 'free': False},
                 {'ja': '新しい', 'kana': 'あたらしい', 'status': 'active', 'k': 0.5, 'free': False,
                  'dictionary': True}]
        own = check_reply.Plan(grammar.statuses({}), None, 0, None, know, SWAPS, 0.08, None, None, words)
        checked = check_reply.finish(f'The {P} is small.{self.TWIN}' + self.FILLER, own, TYPES, seed=1, remember=False)
        twin = next(e for e in checked.elements if e.kind == 's')
        self.assertAlmostEqual(twin.cost_w, 1 / (1 + math.exp(0.5)))  # k on the web's scale, as the weave's swaps
        new_word = '<abbr title="あたらしい · new">新しい</abbr>'  # flagged dictionary as the weave flags it: full only
        checked = check_reply.finish(f'The {P} is new.<ja>{P}は{new_word}です。</ja>' + self.FILLER, own, TYPES,
                                     seed=1, remember=False)
        self.assertEqual([on for e, on in zip(checked.elements, checked.on) if e.kind == 's'], [twins.FULL])

    def test_4_broken_tags_as_the_clients_read_them(self):
        word = '<abbr title="もんだい · problem" data-en="problem" data-on="1">問題'
        pair = (f'<span data-jw="ja" data-on="1">{P}です<abbr title="It is a problem.">。</abbr></span>'
                '<span data-jw="en" data-on="1">It is a problem.</span>')
        render = lambda text: [twins.render(text, level) for level in (-3, 0, 1, 3)]
        for text in (f'The {word} is small. {pair}', f'The {word} is small.\n\nNext {pair}'):
            shown = render(text)
            self.assertTrue(all(x.startswith('The problem is small.') for x in shown), shown)  # never its Japanese
            self.assertIn('It is a problem.', shown[1])
            self.assertIn('です', shown[2])  # the rest of the block as it is
        self_closing = '<abbr title="a · b" data-en="b" data-on="1"/>after ' + pair
        self.assertEqual(render(self_closing)[0], 'after It is a problem.')
        self.assertEqual(twins.shown_texts(self_closing, 1), ['問題', '。'])  # the tag dropped, not a gloss
        lone = '<span data-jw="ja" data-on="1"/><span data-jw="en" data-on="1">It rains.</span> after.'
        self.assertEqual(render(lone), ['It rains. after.'] * 4)
        unclosed = 'Intro. <span data-jw="ja" data-on="1">今日は雨です。<span data-jw="en" data-on="1">It rains.</span> Out.'
        self.assertEqual(render(unclosed), ['Intro. It rains. Out.'] * 4)  # a Japanese twin left open: English
        for text in (f'The {word} is small. {pair}', self_closing, lone, unclosed):
            self.assertFalse(twins.well_formed(text))


class ClientParity(unittest.TestCase):
    """What T3's clients show for markup check-reply doesn't write (review-r2c/r3 corpus, desktop and mobile)."""
    PAIR = (f'<span data-jw="ja" data-on="{{on}}">{P}は{SMALL}です<abbr title="The problem is small.">。</abbr></span>'
            '<span data-jw="en" data-on="{on}">The problem is small.</span>')

    def test_an_invalid_threshold_renders_as_written_and_english_only_reads_it(self):
        for on in ('−1', '01', '4', '-3', '+1'):
            text = self.PAIR.format(on=on)
            for level in twins.SHOWING:  # as written: both spans, the Japanese glossed
                self.assertEqual(twins.shown_texts(text, level), ['問題', '小さい', '。'], (on, level))
            shown = re.sub(r'<[^>]+>', '', twins.render(text, twins.OFF))
            self.assertEqual(shown, 'The problem is small.The problem is small.', on)  # no Japanese in English only

    def test_english_only_on_old_markup_follows_the_clients(self):
        old = (f'Old <abbr title="あめ · rain">雨</abbr>です<abbr title="It is rain.">。</abbr> '
               '<abbr title="もんだい · problem" data-en="problem" data-on="1">問題</abbr>.')
        self.assertEqual(twins.render(old, twins.OFF), 'It is rain. problem.')
        self.assertEqual(twins.shown_texts(old, 0), ['雨', '。'])
        two = (f'Fine. {P}は{SMALL}です<abbr title="The problem is small.">。</abbr>Next <abbr title="いま · now">今</abbr>.')
        self.assertEqual(twins.render(two, twins.OFF), 'Fine. The problem is small.Next now.')  # as the clients

    def test_an_unclosed_level_abbr_replaces_only_its_run_of_japanese(self):
        word = '<abbr title="もんだい · problem" data-en="problem" data-on="1">'
        for after, expected in (('問題 is small.', 'The problem is small.'),
                                ('問題です is small.', 'The problem is small.'),
                                ('データー is new.', 'The problem is new.'), ('問題abc here.', 'The problemabc here.')):
            for level in (twins.OFF, 0, 3):
                self.assertEqual(twins.render(f'The {word}{after}', level), expected, (after, level))

    def test_an_abbr_inside_another_goes_with_it(self):
        nested = '<abbr title="a · x" data-en="x" data-on="1"><abbr title="b · y">雨</abbr></abbr> end.'
        self.assertEqual(twins.render(nested, 0), 'x end.')
        self.assertEqual(twins.shown_texts(nested, 0), [])


if __name__ == '__main__':
    unittest.main()
