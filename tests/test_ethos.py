"""ETHOS.md in code: single kana sounds are never words to swap in, no Japanese word touches a number, the check mends
what it can see instead of dropping a twin, the weave asks for no twins while the sentence level is off (and the brief
leaves out how to write them), a known word goes in wherever it fits, a verb stays in an English sentence
conjugated to fit, a twin can carry a middle step between its Japanese and its English, and fw.py backtest replays the
chats."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import backtest  # noqa: E402
import chatweave  # noqa: E402
import check_reply  # noqa: E402
import fwlib  # noqa: E402
import grammar  # noqa: E402
import twins  # noqa: E402
import weave  # noqa: E402
from test_chatweave import RUNTIME, run_mjs, word  # noqa: E402
from test_twins import INFO, LOG, P, SMALL, TYPES, plan  # noqa: E402


class Letters(unittest.TestCase):
    def test_a_single_kana_sound_is_not_a_word(self):
        for ja in ('ざ', 'む', 'てぃ', 'しゅ', 'に', 'ア'):
            self.assertTrue(fwlib.letter(ja), ja)
        for ja in ('はい', 'それ', 'ねえ', '木', '問題', 'マジ', ''):
            self.assertFalse(fwlib.letter(ja), ja)

    def test_no_surface_uses_one_and_marking_one_known_does_nothing(self):
        state = {'words': [{'ja': 'ざ', 'status': 'known', 'added': '2026-11-10'},
                           {'ja': '問題', 'status': 'known', 'added': '2026-11-10'},
                           {'ja': 'む', 'status': 'queue', 'added': ''}, {'ja': '犬', 'status': 'queue', 'added': ''}]}
        self.assertEqual([w['ja'] for w in fwlib.introduced(state, '2026-11-15')], ['問題'])
        self.assertEqual([e['ja'] for e in fwlib.mark_known(state, ['む', '犬'], '2026-11-15')], ['犬'])
        self.assertEqual(state['words'][2]['status'], 'queue')
        self.assertEqual(fwlib.seed_known(state, 4, '2026-11-15'), 0)  # 犬 is known now; む stays out


class Numbers(unittest.TestCase):
    def test_no_swap_in_or_beside_a_number(self):
        two = {'ja': '二', 'title': 'に · two', 'en': '2', 'free': True, 'before': 'swaps per 100: 4.', 'after': ' then'}
        self.assertFalse(check_reply.may_stay(two, {'二': {'num'}}))
        word_ = {'ja': '問題', 'title': 'もんだい · problem', 'en': 'problem', 'free': True}
        self.assertFalse(check_reply.may_stay(dict(word_, before='about 4.', after=' left'), TYPES))
        self.assertFalse(check_reply.may_stay(dict(word_, before='the ', after='5%'), TYPES))
        self.assertTrue(check_reply.may_stay(dict(word_, before='Line 13: the ', after=' is here'), TYPES))
        self.assertTrue(check_reply.may_stay(dict(word_, before='in 2026, the ', after=' is here'), TYPES))

    @unittest.skipUnless(RUNTIME, 'no node or bun')
    def test_the_weave_leaves_numbers_alone(self):
        numeral = lambda ja, kana, en, digit: dict(word(ja, kana, f'{en} / {digit}', True, 3.0), glosses=[en, digit],
                                                   safe=[en, digit])
        words = [numeral('二', 'に', 'two', '2'), numeral('五', 'ご', 'five', '5'), word('問題', 'もんだい', 'problem', True, 3.5)]
        out = run_mjs({'draft': 'It went from 4.2 to 5.3, and 5% of the problem is two problems.', 'words': words})
        self.assertNotIn('二</abbr>.', out['text'])
        self.assertIn('4.2 to 5.3, and 5%', out['text'])
        self.assertEqual([s['en'] for s in out['swaps'] if s['ja'] == '二'], ['two'])


@unittest.skipUnless(RUNTIME, 'no node or bun')
class KnownWords(unittest.TestCase):
    def test_a_known_word_goes_in_each_place_it_fits(self):
        words = [word('問題', 'もんだい', 'problem', True, 3.5)]
        out = run_mjs({'draft': 'One problem leads to another problem, and that problem to a third.', 'words': words})
        self.assertEqual(out['text'].count('問題</abbr>'), 3)


class Mend(unittest.TestCase):
    def test_bare_grammar_words_get_their_gloss(self):
        text, notes = check_reply.mend(f'It is small.<ja>{P}も{SMALL}です。</ja> The file is here.<ja>the file はここに'
                                       'あります。</ja>')
        self.assertIn('<abbr title="also / even">も</abbr>', text)
        self.assertIn('<abbr title="here">ここ</abbr>に<abbr title="there is / has">あります</abbr>', text)
        self.assertIn('<ja>file は', text)  # the article dropped
        self.assertEqual(len(notes), 3)

    def test_what_code_cannot_read_stays_as_written(self):
        text = f'It stays.<ja>{P}は残ります。</ja>'
        self.assertEqual(check_reply.mend(text), (text, []))
        self.assertEqual(check_reply.mend('Run `これも` now.')[0], 'Run `これも` now.')  # code is code

    def test_an_before_a_japanese_word(self):
        text, notes = check_reply.mend(f'This is an {P}. An {P} again, and an apple.')
        self.assertEqual(text, f'This is a {P}. A {P} again, and an apple.')
        self.assertEqual(len(notes), 1)

    def test_the_check_keeps_a_mended_twin(self):
        checked = check_reply.finish(f'The problem is small too.<ja>{P}も{SMALL}です。</ja>', plan(), TYPES, seed=1,
                                     remember=False)
        self.assertIn('data-jw="ja"', checked.final)
        self.assertIn('- mended: glossed も', checked.text)
        self.assertNotIn('twin dropped', checked.text)


class SentencesOff(unittest.TestCase):
    WOVEN = {'swaps': [{'ja': '問題', 'title': 'もんだい · problem', 'en': 'problem', 'free': True}], 'parts': []}
    PLAN = {'maxChars': 12, 'grammar': {'known': ['は (topic)'], 'learning': None}}

    def test_the_first_line_asks_for_a_firm_count_or_none(self):
        on = chatweave.first_line(self.WOVEN, self.PLAN, 9, 6, least=4)
        self.assertIn('Twins: write 4 twins,', on)
        self.assertNotIn('up to', on)
        off = chatweave.first_line(self.WOVEN, self.PLAN, 9, 6, least=4, twins_off=True)
        self.assertIn('write no <ja> twins', off)
        self.assertNotIn('grammar patterns', off)

    def test_the_brief_leaves_out_how_to_write_twins(self):
        state = {'words': [], 'stage': 2}
        with mock.patch.object(fwlib, 'stage_for', return_value=2):
            on, after_on, _ = weave.compose_draft(state, 0, command='fw')
            off, after_off, _ = weave.compose_draft(state, 0, command='fw', sentences=-3, twins_off=True)
            back, _, plan_back = weave.compose_draft(state, 0, had=after_off, command='fw')
        self.assertIn('   - Twins.', on)
        self.assertNotIn('   - Twins.', off)
        self.assertNotIn('   - Grammar and length.', off)
        self.assertIn('   - Senses.', off)
        self.assertIn(weave.NO_TWINS_LINE, off)
        self.assertLess(len(off), len(on) - 1500)
        self.assertTrue(plan_back['whole'])  # sentences back on: the whole steps, twins included, again
        self.assertIn('   - Twins.', back)


class Backtest(unittest.TestCase):
    def test_replies_from_the_transcripts(self):
        def use(i, command):
            return {'message': {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': i, 'name': 'Bash', 'input': {'command': command}}]}, 'timestamp': i}

        def result(i, text):
            return {'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': i, 'content': [{'type': 'text', 'text': text}]}]}}
        rows = [use('2026-11-13a', 'sh run fw.py" weave --level 0 <<\'DRAFT\'\nThe draft.\nDRAFT'),
                result('2026-11-13a', 'jw-weave: ...\n-----\nThe draft.'),
                use('2026-11-13b', 'sh run fw.py" check-reply --level 0 <<\'REPLY\'\nFirst try.\nREPLY'),
                use('2026-11-13c', 'sh run fw.py" check-reply --level 0 <<\'REPLY\'\nThe reply.<ja>x。</ja>\nREPLY'),
                result('2026-11-13c', 'jw-check: ...\n=====\nFinal.')]
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('a.jsonl', 'copy.jsonl'):  # a resumed chat copies its transcript: once
                (Path(tmp) / name).write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
            found = backtest.replies([tmp])
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]['draft'], found[0]['reply'], found[0]['sent']),
                         ('The draft.', 'The reply.<ja>x。</ja>', 'jw-check: ...\n=====\nFinal.'))

    def test_the_numbers_of_a_check_and_a_weave(self):
        out = ('jw-check: 2 twins, 3 put back:\n- mended: glossed も\n- line 3, twin dropped (its sentence stays '
               'English): too long. "A."\n=====\n<span data-jw="ja" data-on="0">x</span><span data-jw="en" '
               'data-on="0">The <abbr title="a" data-en="a" data-on="-2">a</abbr>.</span> <abbr title="b" data-en="b" '
               'data-on="1">b</abbr> <span data-jw="ja" data-on="2">y</span>')
        got = backtest.parse_check(out)
        self.assertEqual((got['words_full'], got['words_normal'], got['twins'], got['twins_normal']), (2, 1, 2, 1))
        self.assertEqual((got['dropped'], got['put_back'], got['mended'], got['reasons']), (1, 3, 1, {'too long': 1}))
        self.assertIsNone(backtest.parse_check('jw-check: 2 things to fix before there is anything to send:'))
        woven = backtest.parse_weave('jw-weave: x. For twins only (they can\'t): a (b); c (d). Twins: write 3 twins, '
                                     'each\n-----\nA <abbr title="x">x</abbr> {{y→<abbr title="y">y</abbr>}}.')
        self.assertEqual(woven, {'offered': 2, 'unsure': 1, 'twin_only': 2, 'asked': 3})
        self.assertEqual(backtest.english_words('Run `make all` now.\n```\ncode here\n```'), 2)



class Verbs(unittest.TestCase):
    def test_a_verb_in_the_form_of_its_english(self):
        import inflection as inf
        cases = {('読む', 'よむ'): ('読む', '読んだ', '読んでいる'), ('開く', 'ひらく'): ('開く', '開いた', '開いている'),
                 ('行く', 'いく'): ('行く', '行った', '行っている'), ('始める', 'はじめる'): ('始める', '始めた', '始めている'),
                 ('確認する', 'かくにんする'): ('確認する', '確認した', '確認している'),
                 ('来る', 'くる'): ('来る', '来た', '来ている'), ('走る', 'はしる'): ('走る', '走った', '走っている'),
                 ('待つ', 'まつ'): ('待つ', '待った', '待っている'), ('泳ぐ', 'およぐ'): ('泳ぐ', '泳いだ', '泳いでいる')}
        for (ja, kana), forms in cases.items():
            got = tuple(inf.conjugate(ja, kana, f)[0] for f in (inf.PLAIN, inf.PAST, inf.ONGOING))
            self.assertEqual(got, forms, ja)
        self.assertEqual(inf.conjugate('読む', 'よむ', inf.PAST)[1], 'よんだ')
        self.assertIsNone(inf.conjugate('古い', 'ふるい', inf.PAST))  # not a verb
        forms = {w: inf.english_form(w) for w in ('reads', 'read', 'opened', 'running', 'wrote', 'need', 'agreed',
                                                  'written', 'set')}
        self.assertEqual(forms, {'reads': 'plain', 'read': 'plain', 'opened': 'past', 'running': 'ongoing',
                                 'wrote': 'past', 'need': 'plain', 'agreed': 'past', 'written': 'past', 'set': 'plain'})

    def test_the_weave_bends_its_verb_swaps(self):
        swaps = [{'ja': '開く', 'kana': 'ひらく', 'title': 'ひらく · open', 'en': 'opened'},
                 {'ja': '読む', 'kana': 'よむ', 'title': 'よむ · read', 'en': 'reads'},
                 {'ja': '問題', 'kana': 'もんだい', 'title': 'もんだい · problem', 'en': 'problems'}]
        chatweave.fit_verbs(swaps, {'開く': {'v5k'}, '読む': {'v5m'}, '問題': {'n'}})
        self.assertEqual((swaps[0]['ja'], swaps[0]['title'], swaps[0]['lemma']), ('開いた', 'ひらいた · open (past)', '開く'))
        self.assertEqual(swaps[1]['ja'], '読む')
        self.assertNotIn('lemma', swaps[1])
        self.assertEqual(swaps[2]['ja'], '問題')

    def test_only_where_the_english_is_a_verb(self):
        cases = {('The whole ', 'run', ' took'): False, ('Gmail ', 'reading', ' went'): False,
                 ('', 'Moving', ' everything'): False, ('The ', 'deciding', ' question'): False,
                 ("so it won't ", 'show', ' up'): False, ('It is ', 'reading', ' the log'): True,
                 ('I ', 'opened', ' the door'): True, ('', 'Bring', ' the world'): True}
        for (before, en, after), want in cases.items():
            self.assertEqual(check_reply.verb_slot(before, en, after), want, en)
        self.assertFalse(check_reply.may_stay({'ja': '走る', 'en': 'run', 'before': 'The whole ', 'after': ' took'},
                                              {'走る': {'v5r'}}))
        import inflection
        self.assertEqual(inflection.english_form('Bring'), 'plain')

    def test_a_conjugated_verb_may_stay_in_english(self):
        types = {'読む': {'v5m'}, '問題': {'n'}}
        self.assertEqual(check_reply.pos_of('読んでいる', types), {'v5m'})
        self.assertIsNone(check_reply.pos_of('読書', types))
        self.assertTrue(check_reply.may_stay({'ja': '読んだ', 'en': 'read', 'before': 'I ', 'after': ' it'}, types))
        found = check_reply.problems('It <abbr title="よんでいる · read (ongoing)">読んでいる</abbr> the log.', types)
        self.assertEqual(found, [])

if __name__ == '__main__':
    unittest.main()


REMAIN = '<abbr title="のこります · remain">残ります</abbr>'
MARK = '<abbr title="The information is in the log.">。</abbr>'
LADDER = (f'<span data-jw="ja" data-on="1">{INFO}は{LOG}に{REMAIN}{MARK}</span>'
          f'<span data-jw="mid" data-on="0">information は{LOG}に{REMAIN}{MARK}</span>'
          '<span data-jw="en" data-on="1">The <abbr title="じょうほう · information" data-en="information" '
          'data-on="-1">情報</abbr> is in the log.</span> Done.')


class Steps(unittest.TestCase):
    def test_each_sentence_level_shows_its_step(self):
        shown = lambda s: twins.shown_texts(LADDER, (0, s))
        self.assertEqual(shown(1), ['情報', 'ログ', '残ります', '。'])
        self.assertEqual(shown(3), shown(1))
        self.assertEqual(shown(0), ['ログ', '残ります', '。'])
        self.assertIn('information は', twins.render(LADDER, (0, 0)))
        self.assertEqual(shown(-1), ['情報'])  # the weave: the English with its own swap
        self.assertEqual(twins.shown_texts(LADDER, (-2, -1)), [])
        self.assertEqual(twins.render(LADDER, (-3, -3)), 'The information is in the log. Done.')

    def test_the_elements_account_for_every_pair(self):
        self.assertTrue(twins.well_formed(LADDER))
        els, owners = twins.elements(LADDER)
        self.assertEqual([(e['kind'], e['on'], e['parent']) for e in els], [('s', 1, None), ('w', -1, 0), ('m', 0, 0)])
        self.assertEqual(els[0]['mid'], 0)
        texts = twins.annotation_texts(LADDER)
        for pair in twins.PAIRS:
            self.assertEqual(twins.shown_texts(LADDER, pair),
                             [texts[j] for j, o in enumerate(owners) if twins.visible(els, o, pair)], pair)

    def test_a_step_that_cannot_be_one_never_shows(self):
        bad = LADDER.replace('data-jw="mid" data-on="0"', 'data-jw="mid" data-on="2"')
        self.assertFalse(twins.well_formed(bad))
        self.assertNotIn('information は', ''.join(twins.render(bad, (0, s)) for s in range(-3, 4)))
        self.assertEqual(twins.shown_texts(bad, (0, 1)), ['情報', 'ログ', '残ります', '。'])
        self.assertEqual(twins.shown_texts(bad, (0, 0)), ['情報'])  # the pair, as without it
        orphan = f'Before. <span data-jw="mid" data-on="0">{LOG}{MARK}</span> After.'
        self.assertEqual(twins.render(orphan, (0, 0)), 'Before.  After.')
        broken = LADDER.replace('</span> Done.', ' Done.')
        self.assertNotIn('information は', ''.join(twins.render(broken, (0, s)) for s in range(-3, 4)))

    def test_without_a_step_nothing_changes(self):
        plain = LADDER.replace(LADDER[LADDER.index('<span data-jw="mid"'):LADDER.index('<span data-jw="en"')], '')
        self.assertEqual(twins.shown_texts(plain, (0, 0)), ['情報'])
        self.assertEqual([e['kind'] for e in twins.elements(plain)[0]], ['s', 'w'])

    def test_the_middle_step_keeps_grammar_and_known_words(self):
        glance = lambda word: word == 'ログ'
        english_of = lambda word: {'情報': ('information', '情報'), '確認しました': ('check', '確認')}.get(word, (None, None))
        check = '<abbr title="かくにんしました · checked">確認しました</abbr>'
        step = check_reply.middle_step(f'{INFO}は{LOG}を{check}{MARK}', english_of, glance)
        self.assertEqual(step, f'information は{LOG}を check しました{MARK}')
        self.assertEqual(check_reply.middle_step(f'{INFO}は{REMAIN}{MARK}', english_of, glance),
                         f'information は remain{MARK}')  # no English it stands for: its gloss's meaning
        self.assertIsNone(check_reply.middle_step(f'{LOG}は{LOG}{MARK}', english_of, glance))

    def test_the_check_adds_steps_only_when_switched_on(self):
        reply = f'The {INFO} is in the log.<ja>{INFO}は{LOG}に{REMAIN}。</ja> After that, run the tests and read them.'
        off = check_reply.finish(reply, plan(), TYPES, seed=1, remember=False)
        self.assertNotIn('data-jw="mid"', off.final)
        on = plan()
        on.steps = True
        checked = check_reply.finish(reply, on, TYPES, seed=1, remember=False)
        self.assertNotIn('code (', checked.text)
        els, _owners = twins.elements(checked.final)
        twin = next(e for e in els if e['kind'] == 's')
        self.assertEqual(twin['on'], 3)  # this seed's draw
        self.assertEqual(twin['mid'], 2)
        self.assertIn('information は log に remain', checked.final)
        self.assertTrue(twins.well_formed(checked.final))
        record = grammar.record_levels(checked.final, '2026-11-15')
        self.assertTrue(grammar.valid_record(record))
        self.assertEqual(record['e'][-1][0], 'm')

    def test_the_switch_is_a_pref(self):
        self.assertFalse(fwlib.sentence_steps({}))
        self.assertTrue(fwlib.sentence_steps({'prefs': {fwlib.STEPS_PREF: True}}))
