"""Draft and weave in chat: the weave (chatweave.py, chatweave.mjs), the reply check (check_reply.py), the brief that
asks for them (weave.compose_draft) and the style choice (weave.chat_style)."""
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import chatweave  # noqa: E402
import check_reply  # noqa: E402
import fw  # noqa: E402
import hub  # noqa: E402
import intensity  # noqa: E402
import weave  # noqa: E402

RUNTIME = chatweave.runtime()


def word(ja, kana, gloss, free, k, status='known', safe=True):
    return {'ja': ja, 'en': gloss, 'hint': '', 'kana': kana, 'status': status, 'glosses': [gloss],
            'safe': [gloss] if safe else [], 'k': k, 'sd': 0.3, 'free': free}


WORDS = [word('問題', 'もんだい', 'problem', True, 3.5), word('簡単', 'かんたん', 'easy', True, 3.2),
         word('情報', 'じょうほう', 'information', False, 1.2, status='active'),
         word('今', 'いま', 'now', True, 3.0, safe=False)]
DRAFT = """The problem is easy to fix now. Read the information below before you start.

```
problem = now()  # code stays as it is
```

Run `fix problem` and the problem goes away. The information you need is in the log, and it is easy to read."""


def run_mjs(payload):
    run = subprocess.run([RUNTIME, str(chatweave.SCRIPT)], input=json.dumps(payload, ensure_ascii=False),
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@unittest.skipUnless(RUNTIME, 'no node or bun')
class Weave(unittest.TestCase):
    def test_every_candidate_with_its_price_and_unsure_marks(self):
        out = run_mjs({'draft': DRAFT, 'words': WORDS})
        rest, swaps = out['text'], out['swaps']
        self.assertIn('<abbr title="もんだい · problem">問題</abbr>', rest)  # safe: placed outright
        self.assertIn('{{now→<abbr title="いま · now">今</abbr>}}', rest)  # no safe sense: marked for the writer
        self.assertIn('情報', rest)  # a learning word, at every level's candidates
        self.assertEqual(rest.count('<abbr title="じょうほう · information">情報</abbr>'), 2)  # as at full: each place
        self.assertIn('problem = now()  # code stays as it is', rest)  # code fences untouched
        self.assertIn('`fix problem`', rest)  # inline code too
        info = next(s for s in swaps if s['ja'] == '情報')
        self.assertEqual((info['en'], info['title'], info['free'], info['dictionary']),
                         ('information', 'じょうほう · information', False, False))
        self.assertAlmostEqual(info['p'], 1 / (1 + math.exp(1.2)), places=3)  # its chance from k, as costOf prices it
        self.assertTrue(next(s for s in swaps if s['ja'] == '今')['unsure'])
        self.assertTrue(next(s for s in swaps if s['ja'] == '問題')['free'])
        self.assertTrue(30 < out['words'] < len(DRAFT.split()))  # the prose's words, the fence's left out
        self.assertIn('Twins. After an English sentence', weave.DRAFT_RULES)

    def test_dictionary_words_are_few_and_flagged(self):
        pool = [dict(word(ja, kana, gloss, False, k, status='active'), dictionary=True)
                for ja, kana, gloss, k in (('記録', 'きろく', 'log', 0.8), ('読む', 'よむ', 'read', 1.1),
                                           ('始める', 'はじめる', 'start', 1.0), ('下', 'した', 'below', 1.3),
                                           ('必要', 'ひつよう', 'need', 0.9), ('直す', 'なおす', 'fix', 1.2),
                                           ('前', 'まえ', 'before', 1.4), ('行く', 'いく', 'go', 2.0))]
        out = run_mjs({'draft': DRAFT * 3, 'words': WORDS + pool})
        found = [s for s in out['swaps'] if s['dictionary']]
        self.assertTrue(found)
        self.assertLessEqual(len(found), 6)  # DICTIONARY_MAX: they show only at full

    def test_levels_from_python(self):
        state = {'version': 'v1', 'prefs': {}, 'words': []}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': home}), \
                mock.patch.object(chatweave, 'words_for', return_value=([dict(w) for w in WORDS], {'recency': None})), \
                mock.patch.object(chatweave.knowledge, 'load', return_value={}), \
                mock.patch.object(chatweave, 'pool', return_value=[]) as pool:
            short = chatweave.weave(DRAFT, 0, state, session='s1')
            long = chatweave.weave('\n\n'.join([DRAFT.split('```')[0].strip()] * 12), 2, state, session='s2')
            import twins
            kept = twins.swaps_for('s1')
        head, rest = short.split('\n-----\n', 1)
        self.assertTrue(head.startswith('jw-weave: '))
        self.assertIn('情報: keep each whose sense fits', head)  # the learning words named
        self.assertIn('Twins: right after each sentence you can say in Japanese', head)
        self.assertIn(f'up to {chatweave.TWIN_MIN} twins', head)  # a short reply: the least
        cap = lambda out: int(out.split('up to ')[1].split(' ')[0])
        self.assertGreater(cap(long), cap(short))  # the cap grows with the reply
        self.assertIn('<abbr title="じょうほう · information">情報</abbr>', rest)
        self.assertEqual(pool.call_count, 2)  # the dictionary words: on every weave now
        self.assertIn('情報', [s['ja'] for s in kept])  # kept for the check step, per session
        self.assertEqual(next(s for s in kept if s['ja'] == '情報')['en'], 'information')

    def test_the_cap_follows_the_heaviest_budgeted_level(self):
        cap, heaviest = chatweave.twin_cap(300)
        self.assertAlmostEqual(heaviest, intensity.SENTENCE_SHARE * intensity.BUDGETS[2] * 3 / 0.08)
        self.assertEqual(cap, math.ceil(chatweave.TWIN_SLACK * heaviest))
        self.assertEqual(chatweave.twin_cap(20)[0], chatweave.TWIN_MIN)
        self.assertGreater(chatweave.twin_cap(300, normal=1.0)[0], cap)  # a higher challenge pays for more


class Check(unittest.TestCase):
    TYPES = {'簡単': {'adj-na', 'n'}, '問題': {'n'}, '使う': {'v5u'}, '見る': {'v1'}, '小さい': {'adj-i'},
             '前': {'n', 'n-adv'}, 'もし': {'adv'}, 'そして': {'conj'}, '今': {'n', 'adv'}, '古い': {'adj-i'}}

    def found(self, text):
        return [what for _, what, _ in check_reply.problems(text, self.TYPES)]

    def has(self, text, phrase):
        self.assertTrue(any(phrase in w for w in self.found(text)), self.found(text))

    def test_clean_reply(self):
        text = ('The <abbr title="もんだい · problem">問題</abbr> is in the log.\n'
                '<abbr title="this">これ</abbr>は<abbr title="かんたん · easy">簡単</abbr>です'
                '<abbr title="This is easy.">。</abbr>')
        self.assertEqual(self.found(text), [])

    def test_each_rule(self):
        self.has('これは<abbr title="かんたん · easy">簡単</abbr>な<abbr title="もんだい · problem">問題</abbr>です。',
                 'no translation')
        self.has('<abbr title="if">もし</abbr> it fails, retry.', 'conditional ending')
        self.has('Then <abbr title="you">あなた</abbr> retry.', 'pronoun')
        self.has('Do it <abbr title="まえ · before">前</abbr> lunch.', 'preposition')
        self.has('Wait 2 <abbr title="いま · now">今</abbr>.', 'next to a number')
        self.has('You <abbr title="つかう · use">使う</abbr> it daily.', 'stays English')
        self.has('Packets arrive します in order<abbr title="Packets arrive in order.">。</abbr>', 'する on an English word')
        self.has('It is an <abbr title="ふるい · old">古い</abbr> one.', '"an"')
        self.has('<abbr title="and then">そして</abbr>、<abbr title="もんだい · problem">問題</abbr>は'
                 '<abbr title="かんたん · easy">簡単</abbr>です<abbr title="And the problem is easy.">。</abbr>', 'そして')
        self.has('Packet が届くから<abbr title="Because the packet arrives.">。</abbr>', 'outside the <abbr>')
        self.has('Use it. ' + ' '.join(['<abbr title="もんだい · problem">問題</abbr>'] * 5), 'glossed 5 times')

    def test_code_is_skipped(self):
        self.assertEqual(self.found('```\nあなた もし\n```\nRun `もし` here.'), [])

    def test_report(self):
        self.assertEqual(check_reply.report('Plain English.', self.TYPES), 'jw-check: no problems found.')
        report = check_reply.report('Then <abbr title="you">あなた</abbr> retry.', self.TYPES)
        self.assertTrue(report.startswith('jw-check: 2 things to fix'))  # a pronoun, and not a noun type it knows
        self.assertIn('- line 1: the pronoun あなた', report)

    def test_dictionary_types(self):
        types = check_reply.word_types()
        self.assertIn('n', types['問題'])
        self.assertTrue(any(p.startswith('v') for p in types['使う']))

    def test_fw_check_reply_reads_stdin(self):
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': home, 'JAPANESE_WEAVE_NO_REFRESH': '1'}), \
                mock.patch.object(sys, 'stdin', io.StringIO('Plain English.')), \
                mock.patch.object(sys, 'stdout', new_callable=io.StringIO) as out:
            fw.main(['check-reply'])  # never this machine's own state
        head, final = out.getvalue().split('\n' + check_reply.SEPARATOR + '\n')
        self.assertTrue(head.startswith('jw-check: 0 twins and 0 word swaps'), head)
        self.assertEqual(final.strip(), 'Plain English.')


class Brief(unittest.TestCase):
    STATE = {'prefs': {}, 'words': [{'status': 'known'}] * 200}

    def test_style(self):
        self.assertEqual(weave.chat_style(self.STATE, markup=True, runtime='/bin/node'), 'draft')
        self.assertEqual(weave.chat_style(self.STATE, markup=False, runtime='/bin/node'), 'classic')  # no glosses
        self.assertEqual(weave.chat_style(self.STATE, codex=True, markup=True, runtime='/bin/node'), 'classic')
        self.assertEqual(weave.chat_style(self.STATE, markup=True, runtime=''), 'classic')  # nothing to run it
        chosen = dict(self.STATE, prefs={'chat_style': 'classic'})
        self.assertEqual(weave.chat_style(chosen, markup=True, runtime='/bin/node'), 'classic')

    def test_whole_then_digest(self):
        plan = {'level': 0, 'intensity': 1.0, 'offered': [], 'arms': {}, 'known': 3, 'whole': True}
        text, after, record = weave.compose_draft(self.STATE, 2, None, 'fw', plan)
        self.assertIn("fw weave --level 2 <<'DRAFT'", text)
        self.assertIn("fw check-reply --level 2 <<'REPLY'", text)
        self.assertIn('穏やか', text)  # the trap list
        self.assertIn('<ja>...</ja>', text)
        self.assertIn('Send everything below the ===== line exactly as it is', text)
        self.assertLess(len(text), weave.BRIEF_MAX)
        self.assertEqual((after['rules'], record['mode'], record['whole']), (weave.RULES, 'draft', True))
        later, after2, record2 = weave.compose_draft(self.STATE, 0, dict(after, known=[]), 'fw', plan)
        self.assertIn('fw weave --level 0', later)
        self.assertLess(len(later), len(text) / 5)
        self.assertFalse(record2['whole'])
        switched, _, _ = weave.compose_draft(self.STATE, 0, {'rules': 'classic', 'known': ['今']}, 'fw', plan)
        self.assertIn("<<'DRAFT'", switched)  # the steps in full after a classic turn
        before, _, _ = weave.compose_draft(self.STATE, 0, {'rules': 'draft', 'known': []}, 'fw', plan)
        self.assertIn('<ja>...</ja>', before)  # and after the steps from before twins
        english, _, _ = weave.compose_draft(self.STATE, 0, dict(after, known=[]), 'fw', plan, english=2_000_000_000_000)
        self.assertTrue(english.startswith(later))
        self.assertIn('English only is on until', english)  # the twins all the same

    def test_record_and_pref_are_valid(self):
        good = hub.stamp({'kind': 'brief', 'session': 's1', 'level': 0, 'intensity': 1.0, 'offered': [], 'arms': {},
                          'known': 3, 'whole': True, 'chars': 4000, 'mode': 'draft'})
        self.assertIsNone(hub.validate(good))
        self.assertIsNotNone(hub.validate(dict(good, mode='other')))
        self.assertIsNone(hub.validate(hub.stamp({'kind': 'set', 'prefs': {'chat_style': 'classic'}})))
        self.assertIsNotNone(hub.validate(hub.stamp({'kind': 'set', 'prefs': {'chat_style': 'loud'}})))

    def test_command_runs_from_this_copy(self):
        self.assertIn(str(ROOT / 'scripts' / 'fw.py'), weave.fw_command())


if __name__ == '__main__':
    unittest.main()
