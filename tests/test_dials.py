"""Two dials in chat (docs/chat-weave.md, "Two dials"): the sentence level as a surface of its own on the collector and
in fw.py intensity, beside the word level (the chat level from before). Run: python -m unittest tests/test_dials.py"""
import io
import json
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
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
import twins  # noqa: E402
import weave  # noqa: E402
import test_twins as tt  # noqa: E402  (its reply, swaps and word types)

NOW = 1_800_000_000_000


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fw(self, *args):
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            fw.main(list(args))
        return out.getvalue()


class Surface(unittest.TestCase):
    def test_the_sentence_level_is_a_surface_with_the_same_rules(self):
        self.assertIn('sentences', intensity.SURFACES)
        prefs = {}
        for _ in range(5):
            prefs['intensity_sentences'] = intensity.step(prefs, 'sentences', 1, 't3', NOW)
        self.assertEqual(intensity.current(prefs, 'sentences', NOW)['level'], intensity.FULL)  # stops at full
        self.assertEqual(prefs['intensity_sentences']['until'] - NOW, intensity.DURATION_MS)
        self.assertEqual(intensity.chat_levels(prefs, NOW), (0, intensity.FULL))  # the word level is its own
        later = NOW + intensity.DURATION_MS
        self.assertEqual(intensity.chat_levels(prefs, later), (0, 0))  # back to normal after 3 hours
        prefs['intensity_chat'] = intensity.at_level(-1, 't3', NOW)
        self.assertEqual(intensity.chat_levels(prefs, NOW + 1), (-1, intensity.FULL))
        op = lambda value: hub.stamp({'kind': 'set', 'prefs': {'intensity_sentences': value}})
        self.assertIsNone(hub.validate(op(prefs['intensity_sentences'])))  # a pref the state takes
        self.assertIsNotNone(hub.validate(op({'level': 4})))


class Server(Home):
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

    def press(self, step, surface):
        return feedback.request(self.url + '/v1/intensity', {'surface': surface, 'step': step, 'source': 't3'})


class Collector(Server):

    def test_the_sentence_level_on_the_collector(self):
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual(chat['sentences'], {'level': 0, 'until': 0, 'at': 0})  # both dials in one request
        self.assertEqual(set(chat), {'ok', 'intensity', 'english', 'sentences'})
        self.assertNotIn('sentences', feedback.request(self.url + '/v1/intensity?surface=browser'))
        up = self.press(1, 'sentences')
        self.assertTrue(up['ok'], up)
        self.assertEqual(up['intensity']['level'], 1)
        self.assertEqual(up['intensity']['until'] - up['intensity']['at'], intensity.DURATION_MS)
        self.assertEqual(self.press(1, 'sentences')['intensity']['level'], 2)
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (0, 2))  # independent
        self.assertEqual(feedback.request(self.url + '/v1/intensity?surface=sentences')['intensity'], chat['sentences'])
        self.assertEqual(self.press(-1, 'chat')['intensity']['level'], -1)
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (-1, 2))
        presses = [(c['surface'], c['level']) for c in feedback.intensity_changes(self.db)]
        self.assertEqual(presses, [('sentences', 1), ('sentences', 2), ('chat', -1)])  # logged as presses
        self.assertEqual(self.press(0, 'sentences')['intensity']['level'], 0)
        for bad in ({'surface': 'sentence', 'step': 1, 'source': 't3'}, {'surface': 'sentences', 'step': 2,
                                                                         'source': 't3'}):
            self.assertFalse(feedback.request(self.url + '/v1/intensity', bad)['ok'])
        self.assertFalse(feedback.request(self.url + '/v1/intensity?surface=words')['ok'])
        # the hook asks for both dials at once
        live, _thread, english, sentences = hub.live_chat_dials('s1')
        self.assertEqual((live['level'], sentences['level'], english['on']), (-1, 0, False))

    def test_the_sentence_level_follows_the_words_until_set_apart(self):
        self.assertEqual(self.press(-1, 'chat')['intensity']['level'], -1)
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual(chat['sentences'], chat['intensity'])  # one dial: no sentences pref yet
        self.assertEqual(feedback.request(self.url + '/v1/intensity?surface=sentences')['intensity'],
                         chat['intensity'])
        self.assertEqual(hub.live_chat_dials('s1')[3]['level'], -1)
        self.assertEqual(self.press(1, 'sentences')['intensity']['level'], 0)  # a step from where it followed
        self.assertEqual(self.press(-1, 'chat')['intensity']['level'], -2)
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (-2, 0))  # apart now
        prefs = {'intensity_chat': intensity.at_level(-3, 't3', NOW)}  # an off spell from before the deploy
        self.assertEqual(intensity.chat_levels(prefs, NOW + 1), (-3, -3))
        self.assertFalse(intensity.apart(prefs))

    def test_a_collector_without_the_sentence_level_means_one_dial(self):
        older = {'ok': True, 'intensity': {'level': 2, 'until': NOW, 'at': NOW - 1}, 'english': intensity.ENGLISH_OFF}
        with mock.patch.object(feedback, 'request', return_value=older):
            live, _thread, _english, sentences = hub.live_chat_dials('s1')
        self.assertEqual(sentences, live)

    def test_fw_intensity_shows_and_steps_the_sentence_level(self):
        self.assertIn('Chat sentences: heavier (+1) until', self.fw('intensity', 'sentences', 'up'))
        self.assertIn('Chat sentences: off until', self.fw('intensity', 'sentences', 'off'))
        both = self.fw('intensity')
        self.assertIn('Chat words: normal.', both)
        self.assertIn('Chat sentences: off until', both)
        self.assertEqual(self.fw('intensity', 'sentences', 'normal').strip(), 'Chat sentences: normal.')
        self.assertEqual(self.fw('intensity', 'chat').strip(), 'Chat words: normal.')
        changes = [c for c in feedback.intensity_changes(self.db) if c['surface'] == 'sentences']
        self.assertEqual([c['level'] for c in changes], [1, intensity.OFF, 0])


class Hook(Server):
    def hook(self, prompt, session='s1'):
        payload = {'session_id': session, 'prompt': prompt, 'transcript_path': str(self.home / 'missing.jsonl')}
        with mock.patch.dict(os.environ, {'JAPANESE_WEAVE_FORCE': '1'}), \
                mock.patch('sys.stdin', new=io.StringIO(json.dumps(payload))), \
                mock.patch('sys.stdout', new=io.StringIO()) as out:
            self.assertEqual(weave.main(['--hook']), 0)
        return json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']

    def test_words_off_still_writes_the_twins_in_draft_style(self):
        hub.submit({'kind': 'set', 'prefs': {'chat_glosses': 'abbr'}})
        self.fw('intensity', 'sentences', 'normal')  # the sentence dial set apart: it no longer follows the words
        self.fw('intensity', 'chat', 'off')
        with mock.patch.object(weave, 't3_host', return_value=True), \
                mock.patch.object(chatweave, 'runtime', return_value='/bin/node'):
            context = self.hook('What is a monad?')
            self.assertIn('<ja>...</ja>', context)  # the sentences still show: the twins are written
            self.assertIn('words turned off and sentences turned normal for now by the user', context)
            self.assertIn("weave --level -3 --sentences 0 <<'DRAFT'", context)  # the live levels, for the commands
            self.assertIn("check-reply --level -3 --sentences 0 <<'REPLY'", context)
            with mock.patch.object(chatweave, 'weave', return_value='jw-weave: woven'), \
                    mock.patch('sys.stdin', new=io.StringIO('The draft.')):
                self.assertIn('jw-weave: woven', self.fw('weave', '--level', '-3', '--sentences', '0'))
            self.fw('intensity', 'sentences', 'off')
            off = self.hook('And a functor?')
        briefs = feedback.chat_briefs(self.db)
        self.assertEqual([(r['level'], r['intensity']) for r in briefs], [(intensity.OFF, 0.0)])  # recorded
        self.assertTrue(off.startswith('Japanese Weave'), off)
        self.assertNotIn('<ja>', off)  # both dials off: plain English
        for args in (['weave'], ['weave', '--level', '-3'], ['weave', '--level', '-3', '--sentences', '-3']):
            with mock.patch('sys.stdin', new=io.StringIO('The draft.')), self.assertRaises(SystemExit):
                fw.main(args)  # both off (--level alone: both dials at it, as older briefs say)

    def test_off_means_no_japanese_and_back_on_means_both(self):
        hub.submit({'kind': 'set', 'prefs': {'chat_glosses': 'abbr'}})
        with mock.patch.object(weave, 't3_host', return_value=True), \
                mock.patch.object(chatweave, 'runtime', return_value='/bin/node'):
            out = self.fw('intensity', 'off')  # one dial so far: the sentences follow the words
            self.assertIn('Chat words: off until', out)
            self.assertIn('Chat sentences: off until', out)
            self.assertFalse(intensity.apart(feedback.request(self.url + '/v1/state')['state'].get('prefs')))
            self.assertTrue(self.hook('What is a monad?').startswith('Japanese Weave: the user turned Japanese off'))
            self.hook('japanese back on')
            chat = feedback.request(self.url + '/v1/intensity?surface=chat')
            self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (0, 0))
            self.fw('intensity', 'sentences', 'up')  # set apart now
            out = self.fw('intensity', 'off')  # off is both dials, whichever way they stand
            self.assertIn('Chat sentences: off until', out)
            chat = feedback.request(self.url + '/v1/intensity?surface=chat')
            self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (-3, -3))
            self.hook('japanese back on')
            chat = feedback.request(self.url + '/v1/intensity?surface=chat')
            self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (0, 0))
        self.assertIn('Chat words: full', self.fw('intensity', 'full'))
        self.assertIn('Chat sentences: full', self.fw('intensity'))
        self.assertIn('Chat sentences: normal', self.fw('intensity', 'normal'))
        self.fw('intensity', 'chat', 'off')  # a words-only off stays available
        chat = feedback.request(self.url + '/v1/intensity?surface=chat')
        self.assertEqual((chat['intensity']['level'], chat['sentences']['level']), (-3, 0))

    def test_the_classic_brief_follows_the_word_level(self):
        self.fw('intensity', 'chat', 'off')
        self.assertNotIn('<ja>', self.hook('What is a monad?'))
        self.assertEqual(weave.turned_dials(1), weave.turned(1))
        self.assertEqual(weave.turned_dials(-1, -1), weave.turned(-1))
        self.assertEqual(weave.turned_dials(0, 3), ', words turned normal and sentences turned to full for now by '
                                                   'the user')


class Commands(Home):
    """fw.py weave and check-reply take the live levels the hook passes (--level W --sentences S)."""

    def test_the_levels_the_commands_use(self):
        fwlib.save_state(dict(fwlib.new_state(known_top=10), prefs={'intensity_chat': intensity.at_level(2, 't3')}))
        args = lambda **kw: __import__('argparse').Namespace(**dict({'level': None, 'sentences': None}, **kw))
        self.assertEqual(fw.command_levels(args()), (2, 2))  # the cache: the sentence level following the words
        self.assertEqual(fw.command_levels(args(level=1)), (1, 1))  # --level alone: both dials, as older briefs
        self.assertEqual(fw.command_levels(args(level=-3, sentences=1)), (-3, 1))
        plan = check_reply.Plan.load(-1, 2)
        self.assertEqual((plan.level, plan.sentences, plan.view, plan.sview), (-1, 2, -1, 2))
        plan = check_reply.Plan.load(1)
        self.assertEqual((plan.sentences, plan.sview), (1, 1))
        self.assertEqual(weave.level_flags(1), '--level 1')
        self.assertEqual(weave.level_flags(1, 1), '--level 1')
        self.assertEqual(weave.level_flags(-3, 0), '--level -3 --sentences 0')
        with mock.patch.object(check_reply, 'finish', return_value=check_reply.Checked('ok')) as finish, \
                mock.patch('sys.stdin', new=io.StringIO('A reply.')):
            self.fw('check-reply', '--level', '0', '--sentences', '-2')
        self.assertEqual((finish.call_args[0][1].level, finish.call_args[0][1].sentences), (0, -2))


class Reports(Home):
    """T3's report items with a sentenceLevel: a showing per (word level, sentence level)."""

    def item(self, kind, at, level=None, sentences=None, context='m1', **extra):
        base = {'kind': kind, 'view': 'v1', 'context': context, 'at': at, 'box': [0, 0, 400, 60],
                'annotations': [['問題', 10, 0]]}
        if level is not None:
            base['level'] = level
        if sentences is not None:
            base['sentenceLevel'] = sentences
        return dict(base, **extra)

    def test_a_showing_per_pair_of_levels(self):
        db = self.home / 'log.sqlite'
        now = intensity.now_ms()
        visible = dict(y=0, height=24, ms=4000, wall=4000, away=0, media=0, idle=0)
        pin = dict(word='問題', position=0, dwellMs=900)
        result = feedback.record([
            self.item('shown', now - 9000, 1, 1, words=40),           # equal: stored as one dial is
            self.item('pin', now - 8500, 1, None, **pin),              # no sentenceLevel: the same pair
            self.item('pin', now - 8000, 1, 2, **pin),                 # another pair: its own showing
            self.item('visible', now - 7000, -3, 2, **visible),        # words off, sentences shown: kept
            self.item('visible', now - 6000, 2, -3, **visible),        # and the other way round
            self.item('visible', now - 5000, -3, -3, **visible),       # nothing Japanese: dropped
            self.item('shown', now - 4000, -3, -3, words=40, context='m2'),  # a word count only
            self.item('visible', now - 3000, 1, 4, **visible),         # not a level: malformed
            self.item('shown', now - 2000, None, 2, words=30, context='m3'),  # no level: as before
        ], 't3', db, now=now)
        self.assertEqual((result['stored'], result['rejected']), (7, 2))
        self.assertEqual([r['item'] for r in result['reasons']], [5, 7])
        self.assertIn('no Japanese on screen', result['reasons'][0]['error'])
        rows, contexts = feedback.load(db)
        keys = sorted(k[2] for k in contexts)
        self.assertEqual(keys, ['m1@-3/2', 'm1@1', 'm1@1/2', 'm1@2/-3', 'm2@-3', 'm3'])
        self.assertEqual(sorted(r['context'] for r in rows), ['m1@1', 'm1@1/2'])
        pair = contexts[('t3', 'v1', 'm1@1/2')]
        self.assertEqual((pair['message'], pair['level'], pair['sentences'], pair['words']), ('m1', 1, 2, 40))
        self.assertEqual(contexts[('t3', 'v1', 'm1@1')]['sentences'], 1)
        self.assertEqual((contexts[('t3', 'v1', 'm3')]['level'], contexts[('t3', 'v1', 'm3')]['sentences']),
                         (None, None))
        english = contexts[('t3', 'v1', 'm2@-3')]
        self.assertEqual((english['annotations'], english['words']), ([], 40))
        self.assertFalse(knowledge.is_showing(english))
        self.assertTrue(all(knowledge.is_showing(contexts[('t3', 'v1', k)])
                            for k in ('m1@-3/2', 'm1@2/-3', 'm1@1/2', 'm1@1')))
        self.assertEqual(len(knowledge.showings(rows, contexts, now + knowledge.SETTLE_MS + 1)), 5)  # m2 aside

    def test_context_ids(self):
        self.assertEqual(feedback.level_context('m1', 2), 'm1@2')
        self.assertEqual(feedback.level_context('m1', 2, 2), 'm1@2')
        self.assertEqual(feedback.level_context('m1', -1, 3), 'm1@-1/3')
        for context, split in (('m1@-1/3', ('m1', -1, 3)), ('m1@2', ('m1', 2, 2)), ('m1', ('m1', None, None)),
                               ('m1@2/9', ('m1@2/9', None, None)), ('m1@x', ('m1@x', None, None)),
                               ('m1@-3/-3', ('m1', -3, -3))):
            self.assertEqual(feedback.split_levels(context), split, context)
        self.assertEqual(feedback.split_context('m1@-1/3'), ('m1', -1))

    def test_a_messages_budget_at_a_pair(self):
        self.assertEqual(knowledge.chat_budget([], NOW, level=1), intensity.BUDGETS[1])
        self.assertEqual(knowledge.chat_budget([], NOW, level=1, sentences=1), intensity.BUDGETS[1])
        both = (1 - intensity.SENTENCE_SHARE) * intensity.BUDGETS[-1] + intensity.SENTENCE_SHARE * intensity.BUDGETS[2]
        self.assertAlmostEqual(knowledge.chat_budget([], NOW, level=-1, sentences=2), both)
        self.assertIsNone(knowledge.chat_budget([], NOW, level=0, sentences=intensity.FULL))


def strip(text):
    return re.sub(r'<[^>]+>', '', text)


class Rendering(unittest.TestCase):
    """Every level abbr by the word level, every twin by the sentence level (tt.FINAL: a twin from +1, a free word in
    its English from -2, a learning word from 0)."""

    def test_each_kind_by_its_own_dial(self):
        self.assertEqual(twins.render(tt.FINAL, 1), twins.render(tt.FINAL, 1, 1))  # one level: both dials
        self.assertEqual(twins.render(tt.FINAL, (1, -1)), twins.render(tt.FINAL, 1, -1))  # or a pair
        words_only = twins.render(tt.FINAL, 0, -3)  # the twin English, the words Japanese
        self.assertIn('The <abbr title="もんだい · problem">問題</abbr> is small.', words_only)
        self.assertIn('Read the <abbr title="じょうほう · information">情報</abbr>', words_only)
        sentences_only = twins.render(tt.FINAL, -3, 1)  # the twin Japanese, the words English
        self.assertIn('です<abbr title="The problem is small.">。</abbr>', sentences_only)
        self.assertIn('Read the information below', sentences_only)
        self.assertNotIn('is small', strip(sentences_only))
        self.assertEqual(twins.render(tt.FINAL, -3, -3), twins.render(tt.FINAL, -3))  # English only: as before
        self.assertFalse(re.search('[぀-ヿ一-鿿]', twins.render(tt.FINAL, -3, -3)))
        # what words show doesn't depend on the sentence level, and the reverse
        for words in (-2, 0, 2):
            shown = {re.sub(r'.*?Read', 'Read', twins.render(tt.FINAL, words, s), flags=re.S)
                     for s in intensity.LEVELS if s > -3}
            self.assertEqual(len(shown), 1, words)
        for sentences in (-2, 1, 3):
            twin = {('です' in twins.render(tt.FINAL, w, sentences)) for w in intensity.LEVELS}
            self.assertEqual(twin, {sentences >= 1})

    def test_an_older_message_renders_as_before(self):
        old = 'No levels <abbr title="x · meaning">今</abbr>.'
        for pair in ((0, 0), (-3, 2), (2, -3), (3, 3)):
            self.assertEqual(twins.render(old, pair), old)
        self.assertEqual(twins.render(old, -3, -3), 'No levels meaning.')  # English only

    def test_a_gloss_without_a_threshold_in_a_twins_english_is_not_well_formed(self):
        self.assertTrue(twins.well_formed(tt.FINAL))
        loose = tt.FINAL.replace(' data-en="problem" data-on="-2"', '')
        self.assertFalse(twins.well_formed(loose))
        self.assertIsNone(grammar.record(loose, tt.DAY))  # no record for it

    def test_visible_agrees_with_render_at_every_pair(self):
        checked = check_reply.finish(tt.AUTHORED, tt.plan(), tt.TYPES, seed=4, now_ms=NOW, remember=False)
        back, owners = twins.elements(checked.final)
        texts = twins.annotation_texts(checked.final)
        for pair in twins.PAIRS:
            self.assertEqual(twins.shown_texts(checked.final, pair),
                             [texts[j] for j, o in enumerate(owners) if twins.visible(back, o, pair)], pair)


class Join(tt.Home):
    """The sentence record's pair fingerprints and the join at any pair of levels."""

    def contexts(self, final, pairs, message='m1'):
        out = {}
        for k, (words, sentences) in enumerate(pairs):
            shown = grammar.annotation_texts(twins.render(final, words, sentences))
            notes = [[w, 10, 24 * i] for i, w in enumerate(shown)]
            key = ('t3', 'v1', feedback.level_context(message, words, sentences))
            out[key] = {'surface': 't3', 'view': 'v1', 'context': key[2], 'annotations': notes, 'words': 80,
                        'shown_at': NOW - 3_600_000 + k, 'height': 24 * len(notes), 'plan': None,
                        'active': 60_000, 'seen_until': NOW - 3_500_000, 'message': message, 'level': words,
                        'sentences': sentences,
                        'stays': [{'y': 0, 'height': 24 * max(1, len(notes)), 'ms': 60_000, 'words': 80}]}
        return out

    def test_every_pair_joins_the_sentences_it_shows(self):
        checked = check_reply.finish(tt.AUTHORED, tt.plan(), tt.TYPES, seed=4, now_ms=NOW)
        record = grammar.record(checked.final, tt.DAY)
        self.assertTrue(grammar.valid_record(record))
        self.assertEqual(len(record['pf']), 49)
        self.assertNotIn('fps', record)
        by_fp = sentence_model.records_by_fp([record])
        pairs = [p for p in twins.PAIRS if p != (-3, -3)]
        contexts = self.contexts(checked.final, pairs)
        twin_props = [p for e, p in zip(checked.elements, checked.props) if e.kind == 's']
        for context in contexts.values():
            pair = (context['level'], context['sentences'])
            shown = twins.render(checked.final, pair)
            expected = [(s['index'], s['jchars']) for s in grammar.sentences(shown)]
            found = sentence_model.message_sentences(context, by_fp)
            self.assertEqual([(i, chars) for i, chars, _ids, _meta in found], expected, pair)
            for _i, _c, _ids, meta in found:
                self.assertEqual(meta[0], pair[1])  # a twin's level is the sentence level
                self.assertLessEqual(meta[1], pair[1])
                if pair[1] in twins.BUDGETED:
                    self.assertIn(meta[2], [p[twins.BUDGETED.index(pair[1])] for p in twin_props])
        units = sentence_model.units(knowledge.showings([], contexts, NOW), by_fp)
        self.assertEqual(len(units), sum(len(grammar.sentences(twins.render(checked.final, p))) for p in pairs))
        self.assertTrue(any(u.level == twins.FULL for u in units))

    def test_a_record_from_before_joins_its_equal_pairs(self):
        checked = check_reply.finish(tt.AUTHORED, tt.plan(), tt.TYPES, seed=4, now_ms=NOW)
        record = grammar.record(checked.final, tt.DAY)
        before = {k: v for k, v in record.items() if k not in ('pf', 'ls')}
        before['fps'] = [record['pf'][twins.PAIRS.index((lv, lv))] for lv in twins.SHOWING]
        by_fp = sentence_model.records_by_fp([before])
        for level in twins.SHOWING:
            context = list(self.contexts(checked.final, [(level, level)]).values())[0]
            expected = [s['index'] for s in grammar.sentences(twins.render(checked.final, level))]
            self.assertEqual([i for i, *_rest in sentence_model.message_sentences(context, by_fp)], expected)

    def test_a_press_names_the_message_as_shown_at_both_levels_then(self):
        checked = check_reply.finish(tt.AUTHORED, tt.plan(), tt.TYPES, seed=4, now_ms=NOW)
        by_fp = sentence_model.records_by_fp([grammar.record(checked.final, tt.DAY)])
        contexts = self.contexts(checked.final, ((3, -2), (3, 3), (-2, -2)))
        press = {'at': NOW - 1_800_000, 'on': True, 'contexts': [{'view': 'v1', 'context': 'm1'}]}
        words_full = [{'at': NOW - 7_200_000, 'surface': 'chat', 'level': 3, 'until': NOW + 3_600_000}]
        both = words_full + [{'at': NOW - 7_000_000, 'surface': 'sentences', 'level': -2, 'until': NOW + 3_600_000}]
        found = sentence_model.units([], by_fp, presses=[press], contexts=contexts, changes=both)
        self.assertEqual(len(found), len(grammar.sentences(twins.render(checked.final, 3, -2))))
        self.assertTrue(all(u.pseudo and u.level == -2 for u in found))
        found = sentence_model.units([], by_fp, presses=[press], contexts=contexts, changes=words_full)
        self.assertEqual(len(found), 4)  # no sentence level in the log yet: one dial, at full
        self.assertEqual(knowledge.chat_levels(both, NOW), (3, -2))
        self.assertEqual(knowledge.chat_levels(words_full, NOW), (3, None))


class Readers(tt.Home):
    def test_the_readers_count_what_each_dial_showed(self):
        lines = tt.Readers.transcript(self, tt.FINAL)
        words = tt.Readers.WORDS
        found = fwlib.read_replies(words, {}, start='0000-00-00', level=(0, -3))
        self.assertEqual(found[0]['words'], ['問題', '情報'])
        found = fwlib.read_replies(words, {}, start='0000-00-00', level=(-3, 1))
        self.assertEqual(found[0]['words'], ['問題', '小さい'])  # the twin's Japanese, no word swaps
        self.assertEqual(twins.view(weave.last_reply(None, lines), (-3, 1)), twins.render(tt.FINAL, -3, 1))
        twins.remember_plan(grammar.fingerprint(twins.annotation_texts(tt.FINAL)),
                            {'view': -3, 'sview': 1, 'level': -3, 'sentences': 1})
        plans = lambda: fwlib.read_json(fwlib.home() / twins.PLANS, {})  # as written (plans_now caches by mtime)
        self.assertEqual(twins.view(tt.FINAL, (0, 0), plans()), twins.render(tt.FINAL, -3, 1))  # as shown when sent
        twins.remember_plan(grammar.fingerprint(twins.annotation_texts(tt.FINAL)), {'view': 1, 'level': 1})
        self.assertEqual(twins.view(tt.FINAL, (0, 0), plans()), twins.render(tt.FINAL, 1, 1))  # from before: one dial
        state = fwlib.new_state(known_top=10)
        state['prefs'] = {'intensity_sentences': intensity.at_level(2, 't3')}
        self.assertEqual(twins.levels_now(state), (0, 2))
        plan = check_reply.Plan(grammar.statuses({}), None, 1, sentences=-1)
        self.assertEqual((plan.view, plan.sview), (1, -1))
        self.assertEqual((tt.plan(1).view, tt.plan(1).sview), (1, 1))


class Thresholds(unittest.TestCase):
    """check-reply's thresholds with two dials: each kind from its own part of each level's budget."""

    def test_each_kind_spends_its_own_part(self):
        budgets = twins.budgets_for(200)
        for level, (words_part, sentences_part) in budgets.items():
            total = intensity.budget(level) * 2
            self.assertAlmostEqual(sentences_part, intensity.SENTENCE_SHARE * total)
            self.assertAlmostEqual(words_part + sentences_part, total)  # words no longer spend the whole budget
        # a twin's costly words are part of its cost: a twin that fits the sentences' part alone no longer does
        twin = twins.Element('s', cost_s=0.08, cost_w=0.3, opens=0.38, value=2.0)
        word = twins.Element('w', cost_w=0.3, opens=0.3, value=1.0, sid=1)
        els = twins.link([twin, word])
        room = {lv: (0.3, 0.1) for lv in twins.BUDGETED}
        on, _x = twins.assign(els, room, random.Random(0), explore=0)
        self.assertEqual(on, [twins.FULL, -2])  # the twin is over its part; the word fits the words' part
        room = {lv: (0.0, 0.4) for lv in twins.BUDGETED}
        on, _x = twins.assign(els, room, random.Random(0), explore=0)
        self.assertEqual(on, [-2, twins.FULL])  # and the reverse: neither kind spends the other's part

    def test_words_never_depend_on_their_twin(self):
        # a costly word in a twin's English takes its threshold from the words' order alone, not below its twin's
        twin = twins.Element('s', cost_s=0.08, opens=0.08, value=3.0)
        inside = twins.Element('w', cost_w=0.3, opens=0.3, value=1.0, parent=0, sid=('twin', 0))
        els = twins.link([twin, inside])
        on, _x = twins.assign(els, {lv: (0.3, 0.08) for lv in twins.BUDGETED}, random.Random(0), explore=0)
        self.assertEqual(on, [-2, -2])  # both from -2: the word shows at any word level while its twin is English
        props = twins.propensities(els, {lv: (0.3, 0.08) for lv in twins.BUDGETED}, draws=200, seed=1)
        self.assertEqual(props[1], [1.0] * 5)  # at its own kind's levels, never hidden by the twin's

    def test_the_final_markup_carries_each_chance(self):
        checked = check_reply.finish(tt.AUTHORED, tt.plan(), tt.TYPES, seed=4, now_ms=NOW, remember=False)
        final = checked.final
        for m in re.finditer(r'<abbr[^>]*data-on="[^"]*"[^>]*>', final):  # every level abbr
            self.assertRegex(m.group(), r' data-p="(0|0\.02|0\.3)"')
        for m in re.finditer(r'<span data-jw="ja"[^>]*>', final):  # every twin's Japanese span
            self.assertRegex(m.group(), r' data-p="0\.\d{1,2}"')
        self.assertNotRegex(final, r'<span data-jw="en"[^>]*data-p')  # English spans have none
        self.assertIn('<abbr title="もんだい · problem" data-p="0.02">問題</abbr>は', final)  # a twin's word with a chance
        self.assertIn('<abbr title="ちいさい · small">小さい</abbr>', final)  # no chance known: none
        self.assertNotRegex(final, r'data-p="[^"]*">[。？]<')  # a closing mark has none
        back, _owners = twins.elements(final)
        twin_ps = [x for x in checked.elements if x.kind == 's']
        self.assertTrue(all(b['p'] is not None for b in back))
        self.assertEqual([b['p'] for b in back if b['kind'] == 's'],  # one sentence each, no costly word
                         [twins.p_value(twins.p_text(e.opens)) for e in twin_ps])
        self.assertEqual(twins.twin_word_chances(final), [0.02, None] * 4)

    def test_chances_as_written(self):
        for p, text in ((0.07, '0.07'), (0.5, '0.5'), (1, '1'), (0, '0'), (0.0049, '0'), (1.7, '1'), (-1, '0'),
                        (0.123, '0.12')):
            self.assertEqual(twins.p_text(p), text)
        for value, read in (('0.07', 0.07), ('1', 1.0), ('1.00', 1.0), ('0', 0.0), ('.5', None), ('0.071', None),
                            ('1.5', None), ('-0.1', None), ('', None), (None, None), ('0.5 ', None)):
            self.assertEqual(twins.p_value(value), read, value)
        self.assertAlmostEqual(twins.twin_chance([0.1, 0.2]), 1 - 0.9 * 0.8)

    def test_the_summary_says_which_dial_is_where(self):
        checked = check_reply.finish(tt.AUTHORED, check_reply.Plan(grammar.statuses({}), None, 1, None, {}, tt.SWAPS,
                                                                   0.08, sentences=-1), tt.TYPES, seed=4,
                                     remember=False)
        self.assertIn('+1 heavier (words now):', checked.text)
        self.assertIn('-1 lighter (sentences now):', checked.text)
        same = check_reply.finish(tt.AUTHORED, tt.plan(0), tt.TYPES, seed=4, remember=False)
        self.assertIn(' 0 normal (now):', same.text)


class KnownAdverbs(unittest.TestCase):
    """An English sentence may keep a swapped adverb the reader knows at a glance; verbs, pronouns, connectives and
    adverbs still being learned stay out."""
    TYPES = dict(tt.TYPES, もう={'adv'}, 多分={'adv'}, もし={'adv'}, また={'adv', 'conj'}, そして={'conj'})

    def swap(self, ja, title, en, p, free):
        return {'ja': ja, 'title': title, 'en': en, 'kana': ja, 'p': p, 'free': free}

    def test_which_swaps_stay_in_english_sentences(self):
        swaps = tt.SWAPS + [self.swap('もう', 'already', 'already', 0.02, True),
                            self.swap('多分', 'probably', 'probably', 0.3, False),
                            self.swap('もし', 'if', 'if', 0.02, True),
                            self.swap('また', 'again', 'again', 0.02, True),
                            self.swap('また', 'and', 'and', 0.02, True),
                            self.swap('使う', 'つかう · use', 'use', 0.02, True)]
        a = lambda ja, title: f'<abbr title="{title}">{ja}</abbr>'
        text = (f'The build is {a("もう", "already")} done. It will {a("多分", "probably")} be ready. Ask {a("もし", "if")} '
                f'it fails. Run it {a("また", "again")} tomorrow. Fix it, {a("また", "and")} ship it. We '
                f'{a("使う", "つかう · use")} the {tt.INFO} today.')
        plan = check_reply.Plan(grammar.statuses({}), None, 0, None, {}, swaps, 0.08)
        checked = check_reply.finish(text, plan, self.TYPES, seed=1, remember=False)
        back = [line for line in checked.text.split('\n') if line.startswith('- line') and 'put back' in line]
        self.assertEqual(len(back), 4, back)
        for ja in ('多分', 'もし', '使う'):  # learning, a connective, a verb
            self.assertTrue(any(ja in line for line in back), ja)
        self.assertTrue(any('また' in line for line in back))  # as "and"
        self.assertIn('data-en="already"', checked.final)  # a known adverb stays
        self.assertIn('data-en="again"', checked.final)
        self.assertIn('adverbs known at a glance', back[0])
        # without known words (the old check), an adverb is never a noun
        sent = check_reply.sentences(f'It is {a("もう", "#0")} done.')[0]
        self.assertTrue(check_reply.sentence_problems(sent, self.TYPES, english=True))
        self.assertFalse(check_reply.sentence_problems(sent, self.TYPES, english=True, known=lambda t: True))

    def test_connectives_and_question_words_stay_out_whatever_their_types(self):
        types = check_reply.word_types()  # the real dictionary: most of these are nouns or adverbs by type too
        cases = [('さらに', 'moreover'), ('さらに', 'also'), ('あるいは', 'alternatively'), ('なお', 'furthermore'),
                 ('ただ', 'however'), ('どう', 'how'), ('なぜ', 'why'), ('かつ', 'moreover'), ('もしも', 'in case'),
                 ('つまり', 'in other words'), ('また', 'also'), ('もし', 'if'), ('でも', 'but')]
        kept = [('また', 'again'), ('もう', 'already'), ('とても', 'so')]  # とても: an intensifier, typed adv only
        swaps = [{'ja': ja, 'title': en, 'en': en, 'kana': ja, 'p': 0.02, 'free': True} for ja, en in cases + kept]
        a = lambda ja, en: f'<abbr title="{en}">{ja}</abbr>'
        text = ' '.join(f'Line {i}: {a(ja, en)}, we run the tests now.' for i, (ja, en) in enumerate(cases + kept))
        plan = check_reply.Plan(grammar.statuses({}), None, 0, None, {}, swaps, 0.08)
        final = check_reply.finish(text, plan, types, seed=1, remember=False).final
        for ja, en in cases:
            self.assertNotIn(f'data-en="{en}"', final, (ja, en))
        for ja, en in kept:
            self.assertIn(f'data-en="{en}"', final, (ja, en))
        self.assertTrue(check_reply.connective('どこ', {'pn'}))  # a question word by spelling, English unknown
        self.assertTrue(check_reply.connective('ただ', {'adv', 'conj', 'n'}))  # conj-typed, English unknown
        self.assertFalse(check_reply.connective('また', {'adv', 'conj'}, 'Again'))
        self.assertTrue(check_reply.connective('とても', {'adv', 'conj'}, 'so'))  # "so" with a conj type: the connective

    def test_the_brief_says_so(self):
        self.assertIn('adverbs among the known-word swaps', weave.DRAFT_RULES)
        self.assertIn('rather than one that drops the swap', weave.DRAFT_RULES)
        self.assertEqual(weave.RULES, 'dials')
        state = fwlib.new_state(known_top=10)
        plan = {'level': 0, 'intensity': 1.0, 'offered': [], 'arms': {}, 'known': 3, 'whole': True}
        again, _after, record = weave.compose_draft(state, 0, {'rules': 'twins', 'known': []}, 'fw', plan)
        self.assertTrue(record['whole'])  # a chat that had the steps before gets them again
        self.assertIn('adverbs among the known-word swaps', again)


class Calibration(unittest.TestCase):
    def test_sentence_presses_are_counted_on_their_own(self):
        changes = [{'at': NOW - 7_200_000, 'surface': 'sentences', 'level': -1, 'until': NOW + 3_600_000,
                    'source': 't3'},
                   {'at': NOW - 3_600_000, 'surface': 'chat', 'level': 1, 'until': NOW + 7_200_000, 'source': 't3'}]
        report = knowledge.calibrate([], {}, changes, NOW)
        self.assertEqual((report['presses']['sentences']['lighter'], report['presses']['sentences']['heavier']), (1, 0))
        self.assertEqual((report['presses']['chat']['lighter'], report['presses']['chat']['heavier']), (0, 1))
        text = knowledge.calibration_text(report)
        self.assertIn('Chat sentences: 1 lighter and 0 heavier presses (2.0 h at -1)', text)
        self.assertIn('Chat words: 0 lighter and 1 heavier presses (1.0 h at +1)', text)
        del report['presses']['sentences']  # knowledge.json from before: no line
        self.assertNotIn('Chat sentences', knowledge.calibration_text(report))


if __name__ == '__main__':
    unittest.main()
