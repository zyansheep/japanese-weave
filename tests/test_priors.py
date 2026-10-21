"""Offline tests for the starting values from the word itself (NEXT_STEPS step 13): the prior's word features
(knowledge.word_features), their regression in the fit, the prior of any dictionary entry (knowledge.prior_estimate,
and extension/src/prior.js on the same cases), the replay's first exposures and the simulated reader whose words start
from their features. Run: python -m unittest tests/test_priors.py"""
import json
import math
import os
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tools' / 'sim'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import dictionary  # noqa: E402
import evaluate  # noqa: E402
import knowledge  # noqa: E402
import stats  # noqa: E402
from world import World, PRIOR, DAY, HOUR  # noqa: E402

CASES = json.loads((ROOT / 'tests' / 'prior_cases.json').read_text(encoding='utf-8'))


class Features(unittest.TestCase):
    def test_the_shared_cases(self):
        # extension/test/prior.test.js checks the same cases against prior.js
        table, known = CASES['table'], {c: 1 for c in CASES['table']['kanji']}
        for case in CASES['cases']:
            k, sd = knowledge.prior_estimate({'prior': table}, case['origin'], case['interval'], case['entry'])
            self.assertAlmostEqual(k, case['k'], places=9)
            self.assertEqual(sd, case['sd'])
            if case['entry']:
                self.assertEqual(knowledge.word_features(case['entry'], known).keys(), case['features'].keys())
                for name, value in knowledge.word_features(case['entry'], known).items():
                    self.assertAlmostEqual(value, case['features'][name], places=12)

    def test_scripts_and_parts_of_speech(self):
        self.assertEqual([knowledge.script(ja) for ja in ('時間', '人々', '一ヶ月', '食べる', 'シーア派', 'ゆっくり',
                                                          'サボる', 'テレビ', 'ＣＤ')],
                         ['kanji', 'kanji', 'kanji', 'mixed', 'mixed', 'kana', 'kana', 'katakana', 'katakana'])
        self.assertEqual([knowledge.pos_group(p) for p in (['n', 'vs'], ['num'], ['v5r'], ['adj-na'], ['adv-to'],
                                                            ['exp'], [])],
                         ['noun', 'noun', 'verb', 'adjective', 'adverb', 'other', 'other'])

    def test_features_of_an_entry(self):
        d = dictionary.load()
        tokei = d.find('時計')
        self.assertEqual(knowledge.word_features(tokei, knowledge.kanji_counts(['時間'])), {
            'rank': math.log10(tokei['rank'] / 1000), 'kanji known': 0.5})  # 時 known, 計 not
        self.assertEqual(knowledge.word_features(tokei, knowledge.kanji_counts(['時間', '計画'])), {
            'rank': math.log10(tokei['rank'] / 1000), 'kanji known': 1.0, 'all kanji known': 1.0})
        self.assertEqual(knowledge.word_features(tokei, knowledge.kanji_counts(['時計']), own=True).get('kanji known'),
                         None)  # its own kanji never count for it
        jikan = knowledge.word_features(d.find('時間'), knowledge.kanji_counts(['時間', '時計']), own=True)
        self.assertEqual((jikan['kanji known'], 'all kanji known' in jikan), (0.5, False))  # 時 from 時計 only
        self.assertEqual(knowledge.word_features(d.find('アルバイト'))['loan'], 1.0)  # German
        self.assertNotIn('loan', knowledge.word_features(d.find('テレビ')))  # katakana's own baseline says it
        self.assertEqual(knowledge.word_features(d.find('関数'))['rare'], 1.0)
        self.assertEqual(knowledge.word_features({'ja': '時間', 'pos': ['n'], 'common': 3}), {})  # no rank: the
        # reference word's

    def test_only_words_of_an_origin_not_vouched_for_take_them(self):
        entry = dictionary.load().find('時間')
        self.assertIn('rank', knowledge.features(None, 'new', entry)[0])
        self.assertIn('rank', knowledge.features(30, 'learning', entry)[0])
        self.assertEqual(knowledge.features(None, 'known', entry), ({'known': 1.0}, 'none'))
        self.assertEqual(knowledge.features(None, 'new'), ({'new': 1.0}, 'none'))  # no entry: by origin alone

    def test_the_prior_of_an_entry_without_word_coefficients_is_by_origin(self):
        old = {'prior': {k: CASES['table'][k] for k in ('card', 'interval', 'none', 'sd')}}
        entry = dictionary.load().find('時間')
        self.assertEqual(knowledge.prior_estimate(old, 'new', None, entry), (1.2, 1.0))
        self.assertEqual(knowledge.prior_estimate(old, 'new', 30, entry), (0.7 + 0.64 * math.log1p(30), 0.74))


def featured_log(groups, days=10, seed=1, free=()):
    """A web log of words not introduced yet, each read 3 times a day for `days` days with read chance 0.6 and looked
    up with its group's chance: groups {name: (chance, [words])}. Returns (Log, the time after it)."""
    rng = random.Random(seed)
    t0 = evaluate.day_start('2026-09-01')
    events = []
    chance = {ja: p for p, words in groups.values() for ja in words}
    for e in range(3 * days):
        at = t0 + (e // 3) * DAY + (9 + 5 * (e % 3)) * HOUR
        group = evaluate.Group(at, 'browser', ('browser', f'v{e}', 'p'), False, [])
        for ja, p in chance.items():
            looked = rng.random() < 0.6 and rng.random() < p
            group.obs.append(evaluate.Observation(
                at=at, day=evaluate.day_of(at), surface='browser', key=group.key, episode=False, word=ja, written=ja,
                form=False, y=1.0 if looked else 0.0, weight=1.0 if looked else 0.6, near=0.0, ms=1, words=1,
                share=1.0, read=0.6, status='new', seen=e))
        events.append(group)
    words = [{'ja': ja, 'kana': '', 'status': 'queue', 'added': '', 'changed': ''} for ja in chance]
    return evaluate.Log(events, {}, words), events[-1].at + HOUR


class Regression(unittest.TestCase):
    """The word features' coefficients are fitted with everything else, held toward 0 by WORD_SD."""
    KATAKANA = ['テレビ', 'ラジオ', 'カメラ', 'ホテル', 'ニュース', 'ゲーム', 'メール', 'ペン', 'バス', 'ドア', 'コーヒー',
                'パン', 'ノート', 'テスト', 'ボール', 'ビール', 'ケーキ', 'シャツ', 'ベッド', 'タオル']
    KANJI = ['時間', '問題', '世界', '仕事', '会社', '情報', '気持ち', '最初', '意味', '部分', '必要', '自分', '今日', '関係',
             '人間', '理由', '社会', '場所', '先生', '学校']

    def test_the_fit_learns_a_coefficient_and_the_flat_start_has_none(self):
        log, at = featured_log({'katakana': (0.05, self.KATAKANA), 'kanji': (0.5, self.KANJI)})
        fit, flat = knowledge.Fit(log, at), knowledge.Fit(log, at, plain=True)
        j = knowledge.SHARED_NAMES.index('katakana')
        self.assertGreater(fit.theta[j], 1.0)  # katakana words are known: their baseline rises
        self.assertLess(fit.theta[j] - 1.645 * fit.theta_sd[j], 1.0 + 1.645 * fit.theta_sd[j])
        self.assertEqual(flat.theta[j], 0.0)
        # a katakana entry no one has seen starts better known than a kanji one of the same rank and length
        table = {'prior': knowledge.prior_table(fit)}
        d = dictionary.load()
        radio, word = d.find('ラジオ'), d.find('時間')
        same = dict(radio, ja='テレ', rank=word['rank'], pos=['n'])
        self.assertGreater(knowledge.prior_estimate(table, 'new', None, same)[0],
                           knowledge.prior_estimate(table, 'new', None, word)[0] + 1.0)
        self.assertEqual(knowledge.prior_estimate({'prior': knowledge.prior_table(flat)}, 'new', None, same),
                         knowledge.prior_estimate({'prior': knowledge.prior_table(flat)}, 'new', None, word))

    def test_kanji_count_as_known_only_from_free_words_with_evidence(self):
        log, at = featured_log({'all': (0.2, self.KANJI)}, days=3)
        unseen = '時計'  # free by its flag, but no evidence: its kanji don't count
        fit = knowledge.Fit(log, at, free={'時間': True, unseen: True})
        self.assertEqual((fit.own, set(fit.known)), ({'時間'}, {'時', '間'}))
        x = lambda ja: fit.features(ja)[0]
        self.assertNotIn('kanji known', x('時間'))  # its own kanji: no other free word has them
        self.assertEqual(x('人間')['kanji known'], 0.5)  # 間, from 時間
        self.assertEqual(knowledge.prior_table(fit)['kanji'], '時間')
        self.assertEqual(knowledge.Fit(log, at).known, {})  # no flags (a first refresh): none known

    def test_a_few_extreme_words_cannot_swing_every_unseen_word(self):
        # one word never opened against nineteen always opened: the coefficients stay small, so no unseen word of the
        # dictionary crosses into the free set by its features alone
        log, at = featured_log({'known': (0.0, self.KANJI[:1]), 'unknown': (1.0, self.KANJI[1:])})
        fit = knowledge.Fit(log, at)
        words = {name: fit.theta[knowledge.SHARED_NAMES.index(name)] for name in knowledge.WORD_FEATURES}
        self.assertLess(max(abs(v) for v in words.values()), 0.6, words)


class Replay(unittest.TestCase):
    def test_first_exposures_and_the_flat_model(self):
        log, _at = featured_log({'all': (0.2, Regression.KANJI[:3])}, days=2)
        self.assertEqual([o.seen for o in log.scored()][:4], [0, 0, 0, 1])
        member = lambda o: evaluate._member(o, 0.5, 'first')
        self.assertEqual(sum(1 for o in log.scored() if member(o)), 3 * evaluate.FIRST)
        self.assertEqual(evaluate.MODELS['flat']().name, 'flat')
        result = evaluate.evaluate(log, [evaluate.MODELS['flat'](), evaluate.current()])
        self.assertEqual(len(result['rows']), len(log.scored()))
        self.assertIn('first', evaluate.summary(result)['paired']['fit'])


class SimulatedReader(unittest.TestCase):
    def test_words_start_from_their_features(self):
        world = World(seed=2, prior=PRIOR, extra=300)
        self.assertGreater(len(world.words), 2000)
        known = knowledge.kanji_counts(w.ja for w in world.words if w.kind == 'known')
        rows = [(knowledge.word_features(world.entries[w.ja], known), w.k0) for w in world.words if w.k0 is not None]
        self.assertGreater(len(rows), 1500)
        rank = [x.get('rank', 0.0) for x, _k in rows]
        self.assertLess(stats.spearman(rank, [k for _x, k in rows]), -0.3)  # rarer words start less known
        self.assertTrue(all(w.k0 is None for w in world.words if w.kind == 'known'))
        ranks = [(world.entries.get(w.ja) or {}).get('rank') for w in world.queue]
        self.assertEqual(ranks, sorted(ranks))  # chat's topical words come up by frequency
        self.assertTrue(all(w.k0 is None for w in World(seed=2).words))  # off by default: the world as before


if __name__ == '__main__':
    unittest.main()
