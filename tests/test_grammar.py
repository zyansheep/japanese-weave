"""Offline tests for sentence-difficulty calibration (docs/grammar.md): the grammar scanner (grammar.py), the grammar
section of the word state and its op, a reply's sentences and the fingerprint that joins them to the gloss log, the
sentence-open model and intake (sentence_model.py), the weave's first line, check-reply's sentence scores, and English
only in chat (intensity.py, the collector's POST /v1/english, the hook, calibration).
Run: python -m unittest tests/test_grammar.py"""
import io
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'
for name in ('CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ATTENDED', 'JAPANESE_WEAVE_FORCE', 'JAPANESE_WEAVE_OFF',
             'CLAUDE_CODE_SESSION_ID'):
    os.environ.pop(name, None)  # tests set these explicitly

import chatweave  # noqa: E402
import check_reply  # noqa: E402
import collector  # noqa: E402
import feedback  # noqa: E402
import fw  # noqa: E402
import fwlib  # noqa: E402
import grammar  # noqa: E402
import hub  # noqa: E402
import intensity  # noqa: E402
import knowledge  # noqa: E402
import sentence_model  # noqa: E402
import weave  # noqa: E402

JAPANESE = re.compile(r'[぀-ヿ一-鿿]')
NOW = 1_800_000_000_000


def seg(text):
    """Segments from a sentence written with its glossed words in brackets: '[これ]は[問題]です'."""
    return [('w', p[1:-1]) if p.startswith('[') else ('t', p) for p in re.findall(r'\[[^\]]*\]|[^\[]+', text)]


def reply(text):
    """A reply from the same notation, its closing marks glossed with a translation: '[これ]は[問題]です[。]'."""
    return re.sub(r'\[([^\]]*)\]', lambda m: f'<abbr title="{"A translation." if m.group(1) in "。？" else "gloss"}">'
                  f'{m.group(1)}</abbr>', text)


def line(text, day='2026-10-09', sidechain=False):
    return json.dumps({'type': 'assistant', 'isSidechain': sidechain, 'timestamp': day + 'T12:00:00',
                       'message': {'content': [{'type': 'text', 'text': text}]}})


# Each pattern on a sentence that has it and one that doesn't (often a neighbour it could be taken for).
CASES = {
    'wa': ('[これ]は[問題]です', '[これ]が[問題]です'),
    'ga': ('[時間]が[かかります]', '[高い]ですが、[いい]です'),
    'wo': ('[ログ]を[読みます]', '[ログ]は[長い]です'),
    'ni': ('[三時]に[始めます]', '[簡単]に[できます]'),
    'de': ('[テスト]で[分かります]', '[問題]ではありません'),
    'e': ('[東京]へ[行きます]', '[東京]に[行きます]'),
    'no': ('[今日]の[仕事]です', '[読む]のが[好き]です'),
    'desu': ('[大丈夫]です', '[待ちます]'),
    'masu': ('[待ちます]', '[待ちません]'),
    'masen': ('[分かりません]', '[分かります]'),
    'mashita': ('[終わりました]', '[終わります]'),
    'mashou': ('[始めましょう]', '[始めます]'),
    'kudasai': ('[待ってください]', '[待っています]'),
    'kosoado': ('[それ]は[本当]です', '[問題]は[本当]です'),
    'mo': ('[これ]も[大切]です', '[遅くても][大丈夫]です'),
    'ka': ('[分かりますか]', '[分かります]'),
    'to': ('[犬]と[猫]です', '[押す]と[動きます]'),
    'ne-yo': ('[いい]ですね', '[いい]です'),
    'adj-noun': ('[新しい][問題]です', '[問題]は[新しい]です'),
    'teiru': ('[待っています]', '[待ちます]'),
    'dake': ('[一つ][だけ]です', '[一つ]です'),
    'suru-verb': ('[確認します]', '[直します]'),
    'naru': ('[速くなります]', '[速い]です'),
    'relative': ('[昨日][読んだ][本]です', '[本]を[読みました]'),
    'kara-from': ('[東京]から[来ました]', '[遅い]から[待ちます]'),
    'potential': ('[読めます]', '[読みます]'),
    'plain-neg': ('[分からない]', '[分かりません]'),
    'cleft': ('[大切]なのは[時間]です', '[本]の[話]です'),
    'conjecture': ('[雨][かもしれません]', '[雨]です'),
    'kedo': ('[高い]ですが、[いい]です', '[高い]です'),
    'nara': ('[明日]なら[大丈夫]です', '[明日]は[大丈夫]です'),
    'quote': ('[いい]と[思います]', '[犬]と[猫]です'),
    'reason': ('[遅い]ので、[待ちます]', '[東京]から[来ました]'),
    'plain-past': ('[終わった]', '[終わりました]'),
    'nominal': ('[読む]のが[好き]です', '[本]が[好き]です'),
    'te-link': ('[読んで]、[書きます]', '[読んでいます]'),
    'to-when': ('[押す]と[動きます]', '[犬]と[猫]です'),
    'passive': ('[使われます]', '[使います]'),
    'made': ('[五時]まで[待ちます]', '[五時]に[待ちます]'),
    'tara': ('[終わったら]、[教えてください]', '[終わって]、[教えてください]'),
    'da': ('[問題]だ', '[問題]です'),
    'ba': ('[読めば][分かります]', '[例えば]、[これ]です'),
    'yori': ('[昨日]より[速い]です', '[昨日]は[速かった]です'),
    'tai': ('[見たい]です', '[見ます]'),
    'to-iu': ('「[速い]」という[意味]です', '[速い][意味]です'),
    'temo': ('[遅くても][大丈夫]です', '[遅い]けど[大丈夫]です'),
    'ya': ('[本]や[ノート]です', '[本]と[ノート]です'),
    'neg-potential': ('[読めません]', '[読めます]'),
    'mada': ('[まだ][終わっていません]', '[もう][終わりました]'),
    'stack': ('[ここ]には[何も]ありません', '[ここ]に[あります]'),
}


class Scanner(unittest.TestCase):
    def test_every_pattern_on_a_sentence_with_it_and_one_without(self):
        self.assertEqual(set(CASES), set(grammar.IDS))
        for pid, (with_it, without) in CASES.items():
            with self.subTest(pid):
                self.assertIn(pid, grammar.scan_ids(seg(with_it)), grammar.tokens(seg(with_it)))
                self.assertNotIn(pid, grammar.scan_ids(seg(without)), grammar.tokens(seg(without)))

    def test_inventory(self):
        self.assertGreaterEqual(len(grammar.PATTERNS), 40)
        self.assertEqual(len(set(grammar.IDS)), len(grammar.IDS))
        for p in grammar.PATTERNS:
            self.assertTrue(p.name and p.explain and p.example and JAPANESE.search(p.example), p.id)
        for basic in ('wa', 'ga', 'wo', 'no', 'desu', 'masu', 'masen', 'kosoado', 'mo', 'ka', 'to'):
            self.assertIn(basic, grammar.BASIC)
        for hard in ('plain-past', 'plain-neg', 'quote', 'to-iu', 'kara-from', 'reason', 'nominal', 'cleft',
                     'neg-potential', 'mada', 'stack'):
            self.assertNotIn(hard, grammar.BASIC)

    def test_english_runs_are_nouns_and_glossed_words_split_into_known_pieces(self):
        self.assertEqual(grammar.tokens(seg('Review wedge は [loop]の[外]からの check です')),
                         ['Ｘ', 'は', 'Ｘ', 'の', '外', 'から', 'の', 'Ｘ', 'です'])
        self.assertEqual(grammar.tokens(seg('[あるべきだと思います]')), ['ある', 'べき', 'だ', 'と', '思います'])
        self.assertEqual(grammar.tokens(seg('[アップデートできます]')), ['アップデートできます'])  # a katakana word whole
        self.assertIn('stack', grammar.scan_ids(seg('Review wedge は [loop]の[外]からの check です')))

    def test_sentences_of_a_reply(self):
        text = ('Intro line in English.\n'
                + reply('Packet は[小さい]です[。] Then English. [これ]は[大切]です[。]') + '\n'
                + '```\n' + reply('[コード]です[。]') + '\n```\n'
                + reply('- [本当]ですか[？]'))
        found = grammar.sentences(text)
        self.assertEqual([s['text'] for s in found], ['Packet は小さいです。', 'これは大切です。', '本当ですか？'])
        self.assertEqual([s['index'] for s in found], [1, 4, 6])  # the mark among the reply's annotations, code out
        self.assertEqual([(s['jchars'], s['en']) for s in found], [(6, 1), (7, 0), (5, 0)])
        record = grammar.record(text, '2026-11-04')
        self.assertTrue(grammar.valid_record(record))
        self.assertEqual(record['s'][0], [1, 6, 1, ['wa', 'desu']])
        self.assertEqual(record['s'][1][3], ['wa', 'desu', 'kosoado'])
        self.assertNotIn('大切', json.dumps(record, ensure_ascii=False))  # no text: counts and ids only
        self.assertIsNone(grammar.record('Plain English, <abbr title="x">今</abbr> only.', '2026-11-04'))


class Statuses(unittest.TestCase):
    def test_defaults_ops_and_one_learning_pattern(self):
        state = {'words': []}
        statuses = grammar.statuses(state)
        self.assertEqual({p for p, s in statuses.items() if s == 'known'}, set(grammar.BASIC))
        self.assertNotIn('learning', statuses.values())
        good = hub.stamp({'kind': 'grammar', 'set': [['teiru', 'learning']], 'source': 'intake'})
        self.assertIsNone(hub.validate(good))
        for bad in ({'set': [['nope', 'known']]}, {'set': [['teiru', 'sure']]}, {'set': []},
                    {'set': [['teiru', 'learning'], ['dake', 'learning']]}, {'set': [['teiru', 'known']], 'source': ''},
                    {'set': [['teiru', 'known']], 'source': 'x', 'extra': 1}):
            self.assertIsNotNone(hub.validate(hub.stamp(dict({'kind': 'grammar', 'source': 'intake'}, **bad))), bad)
        hub.apply(state, good)
        self.assertEqual(grammar.statuses(state)['teiru'], 'learning')
        self.assertEqual(grammar.learning(state)[0].id, 'teiru')
        hub.apply(state, hub.stamp({'kind': 'grammar', 'set': [['dake', 'learning']], 'source': 'command'}))
        self.assertEqual((grammar.statuses(state)['teiru'], grammar.statuses(state)['dake']), ('new', 'learning'))
        hub.apply(state, hub.stamp({'kind': 'grammar', 'set': [['dake', 'known'], ['wa', 'new']], 'source': 'x'}))
        self.assertEqual((grammar.statuses(state)['dake'], grammar.statuses(state)['wa']), ('known', 'new'))
        self.assertEqual(grammar.learning(state), (None, None))


def t3_message(contexts, rows, view, text, at, opened=(), stays_ms=60_000, kind='pin'):
    """A T3 message as feedback.load gives it, its annotations those of the reply (one line each, 24 px apart), read
    for stays_ms, with opens (pins) of the given annotation indexes."""
    words = grammar.annotation_texts(text)
    notes = [[w, 10, 24 * i] for i, w in enumerate(words)]
    height = 24 * len(words)
    contexts[('t3', view, 'c')] = {'surface': 't3', 'view': view, 'context': 'c', 'annotations': notes, 'words': 60,
                                   'shown_at': at, 'height': height, 'plan': None, 'active': stays_ms,
                                   'seen_until': at + 500,
                                   'stays': [{'y': 0, 'height': height, 'ms': stays_ms, 'words': 60}]}
    for i in opened:
        rows.append({'id': len(rows) + 1, 'at': at + 1000 + i, 'surface': 't3', 'kind': kind, 'word': notes[i][0],
                     'dwell_ms': 1500, 'view': view, 'context': 'c', 'position': i, 'uid': None})


TEXT = reply('Packet は[小さい]です[。] And English. [これ]は[大切]です[。]')


class Join(unittest.TestCase):
    def test_the_fingerprint_joins_a_reply_to_its_message(self):
        record = grammar.record(TEXT, '2026-11-04')
        contexts, rows = {}, []
        t3_message(contexts, rows, 'v1', TEXT, NOW - 3_600_000, opened=[4])
        t3_message(contexts, rows, 'v2', reply('[他]の[文]です[。]'), NOW - 3_600_000)  # no record: left out
        by_fp = sentence_model.records_by_fp([record])
        self.assertEqual(sentence_model.context_fp(contexts[('t3', 'v1', 'c')]), record['fp'])
        self.assertEqual(sentence_model.message_sentences(contexts[('t3', 'v1', 'c')], by_fp),
                         [(1, 6, ('wa', 'desu')), (4, 7, ('wa', 'desu', 'kosoado'))])
        found = sentence_model.units(knowledge.showings(rows, contexts, NOW), by_fp)
        self.assertEqual([(u.chars, u.y, round(u.w, 2)) for u in found], [(6, 0.0, 1.0), (7, 1.0, 1.0)])
        # a record that names a place that isn't a mark (another reply with the same words) is dropped
        bad = dict(record, s=[[0, 3, 0, []]])
        self.assertEqual(sentence_model.message_sentences(contexts[('t3', 'v1', 'c')],
                                                          sentence_model.records_by_fp([bad])), [])
        # a newer scanner's record wins over an older one's
        newer = dict(record, v=record['v'] + 1, s=[[4, 9, 0, ['wa']]])
        self.assertEqual(sentence_model.records_by_fp([newer, record])[record['fp']], {4: (9, 0, ('wa',))})

    def test_a_press_of_english_only_makes_weak_opens(self):
        contexts, rows = {}, []
        t3_message(contexts, rows, 'v1', TEXT, NOW - 3_600_000)
        by_fp = sentence_model.records_by_fp([grammar.record(TEXT, '2026-11-04')])
        press = {'at': NOW - 1_800_000, 'on': True, 'contexts': [{'view': 'v1', 'context': 'c'},
                                                                 {'view': 'gone', 'context': 'c'}]}
        found = sentence_model.units([], by_fp, presses=[press], contexts=contexts)
        self.assertEqual([(u.y, u.w, u.pseudo) for u in found], [(1.0, sentence_model.PRESS_WEIGHT, True)] * 2)
        model = sentence_model.fit(found, grammar.statuses({}), NOW)
        self.assertEqual((model['read'], model['opens'], model['pressed']), (0, 0, 2))  # not in the counts


def simulate(n=3000, a=-4.0, b=1.1, hard=1.5, seed=7):
    """Sentences read by a simulated reader: open chance from the model with a hard pattern (quote) and an easy one
    (teiru, difficulty 0), all at NOW."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        chars = rng.randint(3, 25)
        ids = ['wa', 'desu'] + (['quote'] if rng.random() < 0.15 else []) + (['teiru'] if rng.random() < 0.2 else [])
        p = knowledge.sigmoid(a + b * chars / 10 + (hard if 'quote' in ids else 0))
        out.append(sentence_model.Unit(NOW, chars, ids, 1.0 if rng.random() < p else 0.0, 1.0))
    return out


class Fit(unittest.TestCase):
    def test_recovers_the_length_slope_and_a_hard_patterns_difficulty(self):
        model = sentence_model.fit(simulate(), grammar.statuses({}), NOW)
        self.assertTrue(model['ready'])
        self.assertLess(abs(model['b'] - 1.1), 0.3, model)
        self.assertLess(abs(model['a'] + 4.0), 0.5, model)
        quote, teiru = model['patterns']['quote'], model['patterns']['teiru']
        self.assertLess(quote['low'], 1.5)
        self.assertGreater(quote['high'], 1.5)
        self.assertGreater(quote['low'], 0.5)  # clearly hard
        self.assertLess(teiru['low'], 0)
        self.assertGreater(teiru['high'], 0)  # no harder than known grammar
        self.assertNotIn('wa', model['patterns'])  # known patterns add nothing
        # the limit: where known patterns alone reach the target
        chars = 10 * (math.log(0.05 / 0.95) - model['a']) / model['b']
        self.assertEqual(sentence_model.max_chars(model), int(max(12, min(24, math.floor(chars)))))
        self.assertLess(sentence_model.typical(model)[1], sentence_model.TARGET)
        statuses = grammar.statuses({})
        self.assertGreater(sentence_model.predict(model, 10, ['quote'], statuses),
                           sentence_model.predict(model, 10, ['wa'], statuses))

    def test_reads_weigh_by_their_chance_and_age(self):
        found = [sentence_model.Unit(NOW, 10, ['wa'], 0.0, 0.5), sentence_model.Unit(NOW - 60 * 86_400_000, 10, ['wa'],
                                                                                          1.0, 1.0)]
        model = sentence_model.fit(found, grammar.statuses({}), NOW)
        self.assertEqual((model['read'], model['opens'], model['ready']), (1.5, 1.0, False))
        self.assertEqual(sentence_model.max_chars(model), sentence_model.LENGTH_START)  # not ready: the start
        self.assertIsNone(sentence_model.typical(model))


class Intake(unittest.TestCase):
    def test_one_pattern_at_a_time(self):
        found = simulate(n=400)
        state = {'words': []}
        model = sentence_model.fit(found, grammar.statuses(state), NOW)
        counts = {'teiru': 30, 'quote': 50}
        quiet = [sentence_model.Unit(u.at, u.chars, u.ids, 0.0, u.w) for u in found]  # under the target
        op = sentence_model.intake(state, model, quiet, counts, NOW)
        self.assertEqual(op, {'kind': 'grammar', 'set': [['quote', 'learning']], 'source': 'intake'})  # most used
        busy = [sentence_model.Unit(u.at, u.chars, u.ids, 1.0, u.w) for u in found]  # far over the target
        self.assertIsNone(sentence_model.intake(state, model, busy, counts, NOW))
        self.assertIsNotNone(sentence_model.intake(state, model, [], counts, NOW))  # too few read: no evidence against
        # learning teiru, which is easy: known once it has the evidence since it began, and the next one comes in
        grammar.apply(state, {'set': [['teiru', 'learning']]}, '2026-11-01')
        model = sentence_model.fit(found, grammar.statuses(state), NOW)
        self.assertGreater(model['patterns']['teiru']['read'], sentence_model.KNOWN_EVIDENCE)
        state['grammar']['teiru']['since'] = time.strftime('%Y-%m-%d', time.localtime(NOW / 1000 + 86_400))
        self.assertIsNone(sentence_model.intake(state, model, quiet, counts, NOW))  # nothing read since it began
        self.assertEqual(sentence_model.summary(model, quiet, counts, NOW, state)['learning']['read'], 0)
        state['grammar']['teiru']['since'] = '2026-11-01'
        op = sentence_model.intake(state, model, quiet, counts, NOW)
        self.assertEqual(op['set'], [['teiru', 'known'], ['quote', 'learning']])
        # learning quote, which is hard: it stays
        grammar.apply(state, op, '2026-11-02')
        model = sentence_model.fit(found, grammar.statuses(state), NOW)
        self.assertIsNone(sentence_model.intake(state, model, quiet, counts, NOW))
        self.assertIsNone(hub.validate(hub.stamp(dict(op))))


class FirstLine(unittest.TestCase):
    def test_limit_grammar_and_learning_pattern(self):
        teiru = grammar.BY_ID['teiru']
        plan = {'maxChars': 11, 'grammar': {'known': ['は (topic)', 'です (is)'],
                                            'learning': {'name': teiru.name, 'explain': teiru.explain,
                                                         'example': teiru.example}}}
        woven = {'swaps': [{'ja': '情報', 'free': False, 'dictionary': False, 'unsure': False},
                           {'ja': '記録', 'free': False, 'dictionary': True, 'unsure': True}]}
        head = chatweave.first_line(woven, plan, 9, 6.2)
        self.assertIn('up to 9 twins (much heavier shows about 6', head)
        self.assertIn('at most 11 Japanese characters', head)
        self.assertIn('grammar patterns: は (topic), です (is); plus at most one learning pattern', head)
        self.assertIn(f"This week's learning pattern is {teiru.name}: {teiru.explain}, as in {teiru.example}", head)
        self.assertIn('Any other grammar stays out', head)
        self.assertIn('1 dictionary words that show only at full (記録)', head)
        self.assertIn('No learning pattern this week', chatweave.first_line(
            woven, dict(plan, grammar={'known': [], 'learning': None}), 3, 1))

    def test_the_count_never_exceeds_the_old_price_rule(self):
        import twins
        rng = random.Random(5)
        for budget_words in (40, 150, 400):
            for price in (0.08, 0.11):
                pairs = [(rng.choice((0.005, 0.034, 0.08, 0.5)), rng.choice((1, 1, 2))) for _ in range(12)]
                els = twins.link([twins.Element('s', cost_s=max(p, price) * n, opens=p * n, value=1.0 + rng.random())
                                  for p, n in pairs])
                sentences = [n for _p, n in pairs]
                budgets = twins.budgets_for(budget_words)
                props = twins.propensities(els, budgets, draws=400, seed=1)
                for k, level in enumerate(twins.BUDGETED):
                    paid = budgets[level][1] / price  # what the old rule's count was before rounding
                    expected = sum(pr[k] * n for pr, n in zip(props, sentences))
                    self.assertLessEqual(expected, paid + 0.15, (budget_words, price, level))
                    for seed in range(20):  # and no draw shows more than one past it
                        on, _x = twins.assign(els, budgets, random.Random(seed))
                        shown = sum(n for e, n, t in zip(els, sentences, on) if t <= level)
                        self.assertLessEqual(shown, math.floor(paid + 1e-9) + 2, (budget_words, price, level))

    def test_the_plan_from_the_state_and_the_fit(self):
        state = {'words': [], 'grammar': {'teiru': {'status': 'learning', 'since': '2026-11-01'}}}
        model = sentence_model.summary(sentence_model.fit(simulate(), grammar.statuses(state), NOW), [], {}, NOW)
        plan = chatweave.sentence_plan(state, {'grammar': model})
        self.assertEqual(plan['maxChars'], sentence_model.max_chars(model))
        self.assertAlmostEqual(plan['sentenceChance'], sentence_model.typical(model)[1], places=3)
        self.assertEqual(plan['grammar']['learning']['name'], grammar.BY_ID['teiru'].name)
        self.assertIn('は (topic)', plan['grammar']['known'])
        self.assertNotIn(grammar.BY_ID['teiru'].name, plan['grammar']['known'])
        empty = chatweave.sentence_plan({'words': []}, {})
        self.assertEqual((empty['sentenceChance'], empty['maxChars'], empty['grammar']['learning']),
                         (None, sentence_model.LENGTH_START, None))


class Scorer(unittest.TestCase):
    def plan(self, ready=True):
        model = sentence_model.fit(simulate(), grammar.statuses({}), NOW) if ready else None
        statuses = dict(grammar.statuses({}), teiru='learning', dake='learning')
        return check_reply.Plan(statuses, model, level=0)

    def test_flags_and_summary(self):
        plan = self.plan()
        text = '\n'.join([
            'The cache is warm.',
            reply('[これ]は[大切]です[。]'),
            reply('[昨日][読んだ][本]は[とても][面白い][本]で[大切]です[。]'),
            reply('[待っています]が、[一つ][だけ]です[。]'),
            reply('[いい]と[思います][。]')])
        scored = check_reply.score(text, plan)
        self.assertEqual([s['line'] for s in scored], [2, 3, 4, 5])
        self.assertEqual(scored[0]['flags'], [])
        self.assertTrue(any(f.startswith('too long') for f in scored[1]['flags']), scored[1])
        self.assertTrue(any('not on the list' in f and 'relative clause' in f for f in scored[1]['flags']))
        self.assertTrue(any('more than one learning pattern' in f for f in scored[2]['flags']), scored[2])
        self.assertTrue(any('not on the list' in f and 'と思う' in f for f in scored[3]['flags']), scored[3])
        self.assertTrue(any('likely to be opened' in f for s in scored for f in s['flags']))
        report = check_reply.report(text, {}, plan)
        self.assertTrue(report.startswith('jw-check: '))
        self.assertIn('FIX (split it, simplify it, or leave it English)', report)
        expected = sum(s['p'] for s in scored)
        self.assertIn(f'Expected sentence opens: {expected:.2f} for 4 sentences, against a budget of', report)
        clean = check_reply.report(reply('[これ]は[大切]です[。]'), {}, plan)
        self.assertTrue(clean.startswith('jw-check: no problems found.\n'), clean)
        self.assertEqual(check_reply.report('Plain English.', {}, plan), 'jw-check: no problems found.')
        full = check_reply.report(reply('[これ]は[大切]です[。]'), {}, check_reply.Plan(plan.statuses, plan.model, 3))
        self.assertIn('(no budget at full)', full)

    def test_before_the_model_is_ready_only_length_and_grammar_flag(self):
        plan = self.plan(ready=False)
        self.assertEqual(plan.limit, sentence_model.LENGTH_START)
        scored = check_reply.score(reply('[これ]は[とても][大切]な[問題]です[。]'), plan)
        self.assertEqual(scored[0]['flags'], [])


class Home(unittest.TestCase):
    """Each test gets its own state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats'),
                                                'JAPANESE_WEAVE_FORCE': '1'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def hook(self, prompt, session='s1'):
        payload = {'session_id': session, 'prompt': prompt, 'transcript_path': str(self.home / 'missing.jsonl')}
        with mock.patch('sys.stdin', new=io.StringIO(json.dumps(payload))), \
                mock.patch('sys.stdout', new=io.StringIO()) as out:
            self.assertEqual(weave.main(['--hook']), 0)
        return json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']

    def fw(self, *args):
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(list(args))
        return out.getvalue()


class Backfill(Home):
    def test_the_scan_sends_each_replys_sentences_once_and_backfills_once(self):
        fwlib.save_state(fwlib.new_state(known_top=40))
        chats = self.home / 'chats'
        (chats / 'p').mkdir(parents=True)
        log = chats / 'p' / 'a.jsonl'
        log.write_text(line(TEXT, day=fwlib.today()) + '\n' + line('Plain English only.', day=fwlib.today()) + '\n')
        with mock.patch.dict(os.environ, {'JAPANESE_WEAVE_TRANSCRIPTS': str(chats)}):
            self.assertTrue(fwlib.refresh_exposure())  # the backfill: every reply so far
            self.assertEqual(len(feedback.sentence_records()), 1)
            self.assertEqual(fwlib.read_json(fwlib.home() / fwlib.SCAN)['sentences'], grammar.VERSION)
            self.assertTrue(fwlib.refresh_exposure())  # nothing new
            self.assertEqual(len(feedback.sentence_records()), 1)
            with open(log, 'a') as f:
                f.write(line(reply('[次]は[明日]です[。]'), day=fwlib.today()) + '\n')
            self.assertTrue(fwlib.refresh_exposure())  # the new reply only
        records = feedback.sentence_records()
        self.assertEqual([len(r['s']) for r in records], [2, 1])
        self.assertEqual(records[0]['fp'], grammar.fingerprint(grammar.annotation_texts(TEXT)))
        body = json.dumps(records, ensure_ascii=False)
        self.assertFalse(re.search('[ぁ-ん一-鿿]', body))  # never text


class Collector(Home):
    """The word state on a collector (its own state dir, on a local port), as in test_ends."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.server_home = self.home / 'collector-home'
        with fwlib.using_home(self.server_home):
            hub.submit({'kind': 'seed', 'n': 40})
        self.db = self.server_home / 'interactions.sqlite'
        handler = type('TestHandler', (collector.Handler,), {'db': self.db, 'home': self.server_home,
                                                             'log_message': lambda *args: None})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        fwlib.home().mkdir(parents=True, exist_ok=True)
        (fwlib.home() / 'collector').write_text(self.url + '\n')
        fwlib.load_state()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def english(self, body):
        return feedback.request(self.url + '/v1/english', body)

    def test_the_english_endpoint(self):
        before = intensity.now_ms()
        asked = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual(asked['english'], {'on': False, 'until': 0, 'at': 0})
        self.assertNotIn('english', feedback.request(self.url + '/v1/intensity?surface=browser'))
        screen = [{'view': 'v1', 'context': 'c1'}, {'view': 'v1', 'context': 'c2'}]
        on = self.english({'on': True, 'source': 't3', 'contexts': screen})
        self.assertEqual(set(on), {'ok', 'english'})
        self.assertTrue(on['ok'] and on['english']['on'])
        self.assertEqual(on['english']['until'] - on['english']['at'], intensity.ENGLISH_MS)
        self.assertGreaterEqual(on['english']['at'], before)
        self.assertEqual(feedback.request(self.url + '/v1/intensity?surface=chat')['english'], on['english'])
        again = self.english({'on': True, 'source': 't3', 'contexts': []})  # a press while on starts the hour again
        self.assertGreaterEqual(again['english']['until'], on['english']['until'])
        off = self.english({'on': False, 'source': 't3', 'contexts': []})
        self.assertEqual((off['english']['on'], off['english']['until']), (False, 0))
        self.assertGreaterEqual(off['english']['at'], again['english']['at'])
        self.assertEqual(feedback.request(self.url + '/v1/intensity?surface=chat')['english'], off['english'])
        presses = [c for c in feedback.intensity_changes(self.db) if c['surface'] == 'english']
        self.assertEqual([(c['on'], c['source'], c['contexts']) for c in presses],
                         [(True, 't3', screen), (True, 't3', []), (False, 't3', [])])
        for bad in ({}, {'on': 'yes', 'source': 't3'}, {'on': True, 'source': ''}, {'on': True, 'source': 'x' * 21},
                    {'on': True, 'source': 't3', 'contexts': 'v1'},
                    {'on': True, 'source': 't3', 'contexts': [{'view': 'v1'}]},
                    {'on': True, 'source': 't3', 'contexts': [{'view': 'v 1', 'context': 'c'}]},
                    {'on': True, 'source': 't3', 'contexts': [{'view': 'v', 'context': 'c'}] * 31}, [True]):
            reply_ = self.english(bad)
            self.assertFalse(reply_['ok'], bad)
            self.assertTrue(reply_['error'], bad)
        self.assertEqual(len([c for c in feedback.intensity_changes(self.db) if c['surface'] == 'english']), 3)

    def test_the_hook_writes_english_while_it_is_on(self):
        self.assertIn('Japanese Weave (Stage', self.hook('What is a monad?'))
        on = self.english({'on': True, 'source': 't3', 'contexts': []})['english']
        self.assertIsNone(((fwlib.load_state().get('prefs') or {}).get(intensity.ENGLISH)))  # the cache is behind
        context = self.hook('What is a monad?')
        self.assertEqual(context, weave.english_note(on['until']))  # asked of the collector
        self.assertFalse(JAPANESE.search(context))
        self.assertIn(f'until {intensity.clock(on["until"])}', context)
        self.assertIn('the English-only shortcut or the Japanese Weave panel in T3 Code', context)
        with mock.patch.object(chatweave, 'weave', return_value='jw-weave: woven'), \
                mock.patch('sys.stdin', new=io.StringIO('The draft.')):
            self.assertIn('jw-weave: woven', self.fw('weave', '--level', '0'))  # the weave runs: twins all the same
        self.assertIn('English only is off', self.fw('intensity', 'english', 'off'))
        self.assertIn('Japanese Weave (Stage', self.hook('Next.'))
        self.assertIn('English only until', self.fw('intensity', 'english', 'on'))  # from fw too
        self.assertTrue(self.hook('Next.').startswith('Japanese Weave: the user switched chat to English only'))

    def test_in_draft_style_the_hook_keeps_the_twins_while_english_is_on(self):
        hub.submit({'kind': 'set', 'prefs': {'chat_glosses': 'abbr'}})
        with mock.patch.object(weave, 't3_host', return_value=True), \
                mock.patch.object(chatweave, 'runtime', return_value='/bin/node'):
            self.assertIn('<ja>...</ja>', self.hook('What is a monad?'))
            on = self.english({'on': True, 'source': 't3', 'contexts': []})['english']
            context = self.hook('And a functor?')
        self.assertIn('draft and weave', context)  # the brief, not the plain-English note
        self.assertIn(f'English only is on until {intensity.clock(on["until"])}', context)

    def test_t3_messages_shown_while_english_is_on_are_dropped(self):
        shown = {'kind': 'shown', 'at': intensity.now_ms(), 'view': 'v1', 'context': 'c1', 'words': 40,
                 'box': [0, 0, 500, 40], 'annotations': [['問題', 4, 0]]}
        body = lambda context: {'surface': 't3', 'items': [dict(shown, context=context, at=intensity.now_ms())]}
        self.assertEqual(feedback.request(self.url + '/v1/interactions', body('c1'))['stored'], 1)
        self.english({'on': True, 'source': 't3', 'contexts': []})
        self.assertEqual(feedback.request(self.url + '/v1/interactions', body('c2'))['rejected'], 1)
        self.english({'on': False, 'source': 't3', 'contexts': []})
        self.assertEqual(feedback.request(self.url + '/v1/interactions', body('c3'))['stored'], 1)


class English(unittest.TestCase):
    def test_current_and_windows(self):
        hour = intensity.ENGLISH_MS
        value = intensity.english_at(True, 't3', NOW)
        self.assertEqual(value, {'on': True, 'until': NOW + hour, 'at': NOW, 'source': 't3'})
        prefs = {intensity.ENGLISH: value}
        self.assertEqual(intensity.english_current(prefs, NOW + 10), {'on': True, 'until': NOW + hour, 'at': NOW})
        self.assertEqual(intensity.english_current(prefs, NOW + hour), {'on': False, 'until': 0, 'at': NOW})
        self.assertEqual(intensity.english_current({}, NOW), {'on': False, 'until': 0, 'at': 0})
        changes = [{'at': NOW, 'on': True, 'until': NOW + hour}, {'at': NOW + 600_000, 'on': False, 'until': 0},
                   {'at': NOW + 2 * hour, 'on': True, 'until': NOW + 3 * hour}]
        self.assertEqual(intensity.english_windows(changes, NOW + 10 * hour),
                         [(NOW, NOW + 600_000), (NOW + 2 * hour, NOW + 3 * hour)])
        contexts = {('t3', 'v', c): {'surface': 't3', 'shown_at': at} for c, at in
                    (('in', NOW + 60_000), ('out', NOW + 700_000))}
        contexts[('browser', 'v', 'in')] = {'surface': 'browser', 'shown_at': NOW + 60_000}
        kept = knowledge.without_english(contexts, [dict(c, surface='english') for c in changes], NOW + 10 * hour)
        self.assertEqual(sorted(kept), [('browser', 'v', 'in'), ('t3', 'v', 'out')])

    def test_each_press_on_is_a_lighter_press_in_calibration(self):
        changes = [{'at': NOW - 3_600_000 * k, 'machine': 'm', 'surface': 'english', 'on': on, 'until': 0,
                    'source': 't3', 'contexts': []} for k, on in ((5, True), (4, False), (3, True), (2, True))]
        report = knowledge.calibrate([], {}, changes, NOW)
        chat = report['presses']['chat']
        self.assertEqual((chat['lighter'], chat['heavier'], chat['englishOn']), (3, 0, 3))
        self.assertIn('counting 3 presses of English only', knowledge.calibration_text(report))
        self.assertEqual(report['presses']['browser']['lighter'], 0)

    def test_the_notes_say_how_to_turn_it_back(self):
        until = NOW + intensity.ENGLISH_MS
        for note in (weave.english_note(until), weave.english_note(until, codex=True), weave.off_note(until)):
            self.assertFalse(JAPANESE.search(note))
            self.assertNotIn('Alt+Shift', note)
            self.assertEqual(note.count('\n'), 0)
        self.assertNotIn('/japanese-weave', weave.english_note(until, codex=True))


if __name__ == '__main__':
    unittest.main()
