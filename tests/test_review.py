"""Offline tests for the review cards (scripts/review.py, scheduled as Anki with FSRS, scripts/srs.py): the collector's
POST /v1/review/next (the card's shape with its queue, intervals and today's counts, exclude, more, peek: the counts
alone with nothing served or held back, release: cards given back unshown, words the delayed-start experiment hides
never served, nothing to review) and GET /v1/review/stats and revlog, review items in POST /v1/interactions (stored once
on their card with their grade, malformed ones rejected, an old database migrated, a card left then graded, a rejected
review's reason, the mirror gets them), the queue (new cards through their learning steps, Anki's order, the day's
new-card limit and Review more, held while a grade is in flight, ties fixed for the day, the buttons' intervals being
the ones a grade past 4am gets, a schedule kept under the entry id, the learn-ahead window, a word marked known leaving
the deck while the model's known keeps its schedule, pending and excluded words waiting, a left card served again, the
schedules' cache, probe and made-up cards interleaved as new cards), the card choice's helpers (learning words behind
first, made-up words reused least recently, probe words not repeated), graded cards as explicit answers in the fit (not
a learning step's, one a word a day; the graded flow kept out of the mixture) and the mixture of ungraded reveal times
(two clear components separate, overlapping ones report a weak separation, made-up words pin the "not known" side), a
review as a read (the recency clock, the experiment's exposure), fw.py review's numbers and review log, and the probe's
rolling estimate on a simulated reader (and on the graded panel's stream). Run: python3 -m unittest
tests/test_review.py"""
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'

import collector  # noqa: E402
import contextlib  # noqa: E402
import dictionary  # noqa: E402
import evaluate  # noqa: E402
import experiment  # noqa: E402
import feedback  # noqa: E402
import fwlib  # noqa: E402
import knowledge  # noqa: E402
import probe  # noqa: E402
import review  # noqa: E402
import srs  # noqa: E402
import fw  # noqa: E402
import io  # noqa: E402
import sqlite3  # noqa: E402

DAY = 86_400_000
HOUR = 3_600_000
FRAME = probe.frequency_frame()


class Home(unittest.TestCase):
    """Each test gets its own state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'JAPANESE_WEAVE_HOME': str(self.home / 'fw'),
                                                'JAPANESE_WEAVE_TRANSCRIPTS': str(self.home / 'no-chats')})
        self.env.start()
        self.db = self.home / 'fw' / feedback.DB_NAME

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


def learning(state, words, day, arm=None, started=None):
    """Makes words of the state learning words since `day`, in an experiment arm ('held' or 'shown') if given."""
    by = {w['ja']: w for w in state['words']}
    for ja in words:
        w = by[ja]
        w['status'], w['changed'], w['origin'] = 'active', day, 'learning'
        if arm:
            w['arm'] = {'arm': arm, 'draw': 0.1 if arm == 'held' else 0.9, 'day': day,
                        'probe': fwlib.add_days(day, fwlib.PROBE_DAY)}
            if started is not None:
                w['arm']['at'] = started
    return state


def queue_words(state, n, skip=0):
    return [w['ja'] for w in state['words'] if w['status'] == 'queue'][skip:skip + n]


def fresh_state(day):
    """A new word state with 40 known words and no learning word yet (new_state takes some in: back to the queue)."""
    state = fwlib.new_state(day, known_top=40)
    for w in state['words']:
        if w['status'] == 'active':
            w['status'] = 'queue'
    return state


def reviewed(card, at, reveal, shown=None, uid=None, source='desktop', word=None, grade=None, graded=None):
    """A review item as the T3 Code fork sends it; with a grade, gradeMs (default: 400 ms after the reveal)."""
    item = {'kind': 'review', 'at': at, 'card': card, 'word': word, 'revealMs': reveal,
            'shownMs': shown if shown is not None else (reveal or 0) + 900, 'source': source}
    if grade is not None:
        item = dict(item, grade=grade, gradeMs=graded if graded is not None else reveal + 400)
    return dict(item, uid=uid) if uid else item


class Endpoint(Home):
    """POST /v1/review/next on a collector serving its own state dir."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.today = fwlib.today()
        state = fresh_state(self.today)
        self.active = queue_words(state, 6)
        self.held = queue_words(state, 6, skip=6)
        learning(state, self.active, fwlib.add_days(self.today, -3), 'shown')
        learning(state, self.held[:2], fwlib.add_days(self.today, -3), 'held')
        learning(state, self.held[2:4], fwlib.add_days(self.today, -16), 'shown')  # paused
        learning(state, self.held[4:], fwlib.add_days(self.today, -22), 'held')  # its probe is due
        fwlib.save_state(state)
        self.state = state
        quiet = {'db': self.db, 'home': fwlib.home(), 'log_message': lambda *args: None}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), type('TestHandler', (collector.Handler,), quiet))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def post(self, path, body):
        data = json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_a_card_is_a_word_alone_and_its_kind_stays_here(self):
        status, body = self.post('/v1/review/next', {'source': 'desktop'})
        self.assertEqual((status, body['ok']), (200, True))
        card = body['card']
        self.assertEqual(set(body), {'ok', 'card', 'counts', 'newPerDay'})
        self.assertEqual(set(card), {'id', 'ja', 'kana', 'gloss', 'queue', 'intervals'})
        self.assertEqual(card['queue'], 'new')  # nothing graded yet: a new card (or a probe or made-up one, alike)
        served = feedback.cards(self.db)[0]['served_at']  # a step past 4am counts in days: as the server had it then
        self.assertEqual(card['intervals'], srs.intervals(srs.NEW_CARD, served, srs.card_id(card['ja'])))
        if srs.day_number(served + 600_000) == srs.day_number(served):
            self.assertEqual({k: card['intervals'][k] for k in ('again', 'hard', 'good')},
                             {'again': 60, 'hard': 330, 'good': 600})
        self.assertEqual(card['intervals']['easy'] % 86_400, 0)
        self.assertEqual(body['counts'], {'new': len(self.active), 'learning': 0, 'review': 0})
        self.assertTrue(1 <= len(card['id']) <= 64 and 1 <= len(card['ja']) <= 40)
        self.assertTrue(len(card['kana']) <= 40 and len(card['gloss']) <= 200)
        row = feedback.cards(self.db)[0]
        self.assertEqual((row['id'], row['word'], row['source'], row['at']), (card['id'], card['ja'], 'desktop', None))
        self.assertIn(row['kind'], feedback.CARD_KINDS)
        if row['kind'] == 'fake':
            self.assertEqual(card['gloss'], review.MADE_UP)
        status, again = self.post('/v1/review/next', {'source': 'mobile', 'exclude': [card['id']]})
        self.assertEqual(status, 200)
        self.assertNotEqual(again['card']['ja'], card['ja'])

    def test_bad_requests_and_no_state(self):
        for body in ({}, {'source': 'web'}, {'source': 'desktop', 'exclude': 'x'},
                     {'source': 'desktop', 'exclude': ['a b']}, {'source': 'desktop', 'exclude': ['x'] * 21}, [],
                     {'source': 'desktop', 'more': 'yes'}, {'source': 'desktop', 'more': 1},
                     {'source': 'desktop', 'peek': 'yes'}, {'source': 'desktop', 'peek': 0},
                     {'source': 'desktop', 'release': 'x'}, {'source': 'desktop', 'release': ['a b']},
                     {'source': 'desktop', 'release': ['x'] * 21}, {'source': 'desktop', 'newPerDay': -1},
                     {'source': 'desktop', 'newPerDay': 201}, {'source': 'desktop', 'newPerDay': 2.5},
                     {'source': 'desktop', 'newPerDay': True}, {'source': 'desktop', 'newPerDay': '5'}):
            status, reply = self.post('/v1/review/next', body)
            self.assertEqual((status, reply['ok']), (400, False), body)
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop', 'exclude': ['x'] * 20})[0], 200)
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop', 'more': True})[0], 200)

    def test_the_review_menu_sets_the_new_cards_a_day(self):
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop', 'peek': True})[1]['newPerDay'],
                         fwlib.REVIEW_NEW_DEFAULT)
        status, body = self.post('/v1/review/next', {'source': 'mobile', 'peek': True, 'newPerDay': 2})
        self.assertEqual((status, body['newPerDay'], body['counts']['new']), (200, 2, 2))
        self.assertEqual(fwlib.load_state()['prefs'][fwlib.REVIEW_NEW_PREF], 2)  # kept, as a set op
        self.assertEqual(feedback.cards(self.db), [])  # a peek still serves nothing
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop'})[1]['counts']['new'], 2)
        status, body = self.post('/v1/review/next', {'source': 'desktop', 'peek': True, 'newPerDay': 0})
        self.assertEqual((body['newPerDay'], body['counts']['new'], body['card']), (0, 0, None))
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop', 'peek': False})[0], 200)
        status, reply = self.post('/v1/review/next', {'source': 'desktop', 'peek': True, 'release': ['x'] * 20})
        self.assertEqual((status, reply['card'], set(reply['counts'])), (200, None, {'new', 'learning', 'review'}))
        (fwlib.home() / 'state.json').rename(fwlib.home() / 'elsewhere.json')
        status, reply = self.post('/v1/review/next', {'source': 'desktop'})
        self.assertEqual((status, reply['ok']), (409, False))

    def test_paused_means_nothing_to_review(self):
        state = fwlib.load_state()
        state['paused'] = True
        fwlib.save_state(state)
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop'}),
                         (200, {'ok': True, 'card': None, 'counts': {'new': 0, 'learning': 0, 'review': 0}, 'newPerDay': 10}))
        self.assertEqual(feedback.cards(self.db), [])
        self.assertEqual(self.post('/v1/review/next', {'source': 'mobile', 'peek': True}),
                         (200, {'ok': True, 'card': None, 'counts': {'new': 0, 'learning': 0, 'review': 0}, 'newPerDay': 10}))

    def test_a_peek_answers_the_counts_and_serves_nothing(self):
        for _ in range(3):  # a badge polling
            self.assertEqual(self.post('/v1/review/next', {'source': 'desktop', 'peek': True}),
                             (200, {'ok': True, 'card': None,
                                    'counts': {'new': len(self.active), 'learning': 0, 'review': 0},
                                    'newPerDay': 10}))
        self.assertEqual(feedback.cards(self.db), [])  # no cards row: nothing pending, no probe or made-up word used
        _status, body = self.post('/v1/review/next', {'source': 'desktop'})
        self.assertIsNotNone(body['card'])
        self.assertEqual(len(feedback.cards(self.db)), 1)

    def test_no_word_to_offer_means_nothing_to_review(self):
        state = fwlib.load_state()
        for w in state['words']:
            if w['ja'] in self.active:
                w['status'] = 'known'
        fwlib.save_state(state)
        with mock.patch.object(dictionary, 'PATH', self.home / 'no-dictionary.tsv'):
            for _ in range(20):  # a made-up word alone is never a card
                self.assertEqual(self.post('/v1/review/next', {'source': 'desktop', 'more': True})[1],
                                 {'ok': True, 'card': None, 'counts': {'new': 0, 'learning': 0, 'review': 0}, 'newPerDay': 10})

    def test_hidden_words_are_never_served(self):
        """Over many cards, hours apart (so nothing is held back for being recent), no word the experiment hides or is
        about to probe comes up, as a learning word or as a probe word: the band-0 words are hidden too here."""
        state = fwlib.load_state()
        by = {fwlib.key(w['ja']): w for w in state['words']}
        top = [it['ja'] for it in FRAME.bands[0] if fwlib.key(it['ja']) in by and by[fwlib.key(it['ja'])]['ja']
               not in self.active and by[fwlib.key(it['ja'])]['ja'] not in self.held][:12]
        top = [by[fwlib.key(ja)]['ja'] for ja in top]
        learning(state, top, fwlib.add_days(self.today, -2), 'held')
        fwlib.save_state(state)
        hidden = {fwlib.key(w['ja']) for w in state['words'] if fwlib.phase(w) in review.HIDDEN}
        hidden |= {fwlib.key(w['kana']) for w in state['words'] if fwlib.phase(w) in review.HIDDEN and w.get('kana')}
        self.assertGreaterEqual(len(hidden), 12)
        now, rng, kinds = int(time.time() * 1000), random.Random(5), {}
        with mock.patch.object(review, 'PROBE_SHARE', 0.5):
            for i in range(300):
                at = now - (300 - i) * 5 * 60_000
                pending = {r['word'] for r in feedback.cards(self.db) if r['kind'] == 'learning'  # in flight today
                           and r['at'] is None and r['served_at'] >= at - review.PENDING_MS
                           and srs.day_number(r['served_at']) == srs.day_number(at)}
                card, counts = review.next_card('desktop', (), self.db, at, rng)
                self.assertNotIn(fwlib.key(card['ja']), hidden)
                if i % 2:  # every other card left (the rest never reported): the words go round
                    feedback.record([reviewed(card['id'], at, None, 2000, word=card['ja'])], 't3', self.db, now=at)
                self.assertEqual(counts['new'] + len(pending), len(self.active))  # hidden words aren't counted either
        for row in feedback.cards(self.db):
            kinds[row['kind']] = kinds.get(row['kind'], 0) + 1
        self.assertGreater(kinds['probe'], 100)
        served = {row['word'] for row in feedback.cards(self.db) if row['kind'] == 'learning'}
        self.assertEqual(served, set(self.active))

    def test_the_mirror_gets_the_cards_and_their_reviews(self):
        _status, body = self.post('/v1/review/next', {'source': 'desktop'})
        card = body['card']
        now = int(time.time() * 1000)
        stored = self.post('/v1/interactions', {'surface': 't3', 'items': [
            reviewed(card['id'], now - 2000, 800, uid='r1', word=card['ja'], grade=3, graded=1300)]})
        self.assertEqual(stored[1], {'ok': True, 'stored': 1, 'duplicates': 0, 'rejected': 0})
        old = feedback.export(0, 0, 10, self.db)
        self.assertNotIn('cards', old)  # a mirror from before cards asks for none
        mirror = self.home / 'mirror.sqlite'
        self.assertEqual(feedback.sync_mirror(self.url, mirror), (True, None))
        [row] = feedback.reviews(mirror)
        self.assertEqual((row['card'], row['word'], row['reveal'], row['source'], row['grade'], row['grade_ms']),
                         (card['id'], card['ja'], 800, 'desktop', 3, 1300))
        self.assertEqual(feedback.stats(0, self.db)['counts']['t3:review'], [1, 1])


    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=10) as reply:
            return json.loads(reply.read())

    def test_the_schedules_numbers_and_review_log_over_http(self):
        _status, body = self.post('/v1/review/next', {'source': 'desktop'})
        card = body['card']
        now = int(time.time() * 1000)
        self.post('/v1/interactions', {'surface': 't3', 'items': [
            reviewed(card['id'], now - 3000, 700, 2500, word=card['ja'], grade=4, graded=1100)]})
        found = self.get('/v1/review/stats')
        self.assertTrue(found['ok'])
        self.assertEqual(found['review']['today'], 1)
        rows = self.get('/v1/review/revlog')
        self.assertEqual(rows['columns'], list(review.REVLOG_COLUMNS))
        learning_card = feedback.cards(self.db)[0]['kind'] == 'learning'
        self.assertEqual(len(rows['rows']), 1 if learning_card else 0)  # a probe or made-up card schedules nothing
        if learning_card:
            self.assertEqual(rows['rows'][0], [srs.card_id(card['ja']), now - 3000 + 1100, 4, 0, 1100])
            self.assertEqual(found['review']['deck'], 1)


class Events(Home):
    """Review items through feedback.record, as POST /v1/interactions stores them."""
    NOW = 1_800_000_000_000

    def setUp(self):
        super().setUp()
        for i, (word, kind) in enumerate((('問題', 'learning'), ('時間', 'probe'), ('悪売', 'fake'))):
            feedback.serve_card({'id': f'c{i}', 'served_at': self.NOW - HOUR, 'source': 'desktop', 'word': word,
                                 'kind': kind}, self.db)

    def record(self, *items, surface='t3'):
        return feedback.record(list(items), surface, self.db, now=self.NOW)

    def test_a_review_is_stored_on_its_card_once(self):
        item = reviewed('c0', self.NOW - 60_000, 1200, 3000, uid='u-1', word='問題')
        self.assertEqual(self.record(item), {'stored': 1, 'duplicates': 0, 'rejected': 0})
        self.assertEqual(self.record(item), {'stored': 0, 'duplicates': 1, 'rejected': 0})  # a resend
        self.assertEqual(self.record(dict(item, uid='u-2')), {'stored': 0, 'duplicates': 1, 'rejected': 0})
        left = reviewed('c1', self.NOW - 50_000, None, 1500, uid='u-3', word='時間', source='mobile')
        self.assertEqual(self.record(left, dict(left, uid=None)), {'stored': 1, 'duplicates': 1, 'rejected': 0})
        found = feedback.reviews(self.db)
        self.assertEqual([(r['card'], r['kind'], r['reveal'], r['shown'], r['source']) for r in found],
                         [('c0', 'learning', 1200, 3000, 'desktop'), ('c1', 'probe', None, 1500, 'mobile')])
        self.assertEqual(found[0]['at'], self.NOW - 60_000)

    def test_malformed_unknown_or_mismatched_reviews_are_rejected(self):
        good = reviewed('c2', self.NOW - 1000, 700, 2000, word='悪売')
        bad = [dict(good, card='c9'),  # never served here
               dict(good, word='問題'),  # not the card's word
               dict(good, revealMs=2500),  # revealed after it left
               dict(good, revealMs=-1), dict(good, revealMs=True), dict(good, revealMs='700'),
               dict(good, shownMs=None), dict(good, shownMs=feedback.MAX_REVIEW_MS + 1),
               dict(good, source='web'), dict(good, uid='a b'), dict(good, card=''), dict(good, card='x' * 65),
               dict(good, word=''), dict(good, at=self.NOW - 8 * DAY), dict(good, at=self.NOW + HOUR + 1),
               {k: v for k, v in good.items() if k != 'card'}, {k: v for k, v in good.items() if k != 'shownMs'}]
        found = self.record(*bad)
        self.assertEqual(len(found.pop('reasons')), len(bad))  # each with why
        self.assertEqual(found, {'stored': 0, 'duplicates': 0, 'rejected': len(bad)})
        self.assertEqual(self.record(good, surface='browser')['rejected'], 1)
        self.assertEqual(feedback.reviews(self.db), [])
        self.assertEqual(self.record(good, dict(good, revealMs=None)), {'stored': 1, 'duplicates': 1, 'rejected': 0})
        self.assertEqual(feedback.count(self.db), 0)  # no gloss interaction

    def test_a_grade_needs_a_reveal_and_its_time_between_the_reveal_and_the_end(self):
        good = reviewed('c0', self.NOW - 1000, 700, 2000, word='問題', grade=3, graded=1200)
        bad = [dict(good, grade=0), dict(good, grade=5), dict(good, grade=True), dict(good, grade='3'),
               dict(good, grade=2.5), dict(good, revealMs=None),  # no reveal
               dict(good, gradeMs=600), dict(good, gradeMs=2100), dict(good, gradeMs=None),  # outside reveal..shown
               {k: v for k, v in good.items() if k != 'gradeMs'}, {k: v for k, v in good.items() if k != 'grade'}]
        found = self.record(*bad)
        self.assertEqual([r['item'] for r in found.pop('reasons')], list(range(len(bad))))
        self.assertEqual(found, {'stored': 0, 'duplicates': 0, 'rejected': len(bad)})
        self.assertEqual(self.record(good, dict(good, grade=1)), {'stored': 1, 'duplicates': 1, 'rejected': 0})
        self.assertEqual(self.record(reviewed('c1', self.NOW - 900, 700, 2000, word='時間', grade=1, graded=700)),
                         {'stored': 1, 'duplicates': 0, 'rejected': 0})  # graded as it was revealed
        left = reviewed('c2', self.NOW - 800, None, 1500, word='悪売')
        self.assertEqual(self.record(left), {'stored': 1, 'duplicates': 0, 'rejected': 0})  # an old client's item
        found = {r['card']: (r['grade'], r['grade_ms']) for r in feedback.reviews(self.db)}
        self.assertEqual(found, {'c0': (3, 1200), 'c1': (1, 700), 'c2': (None, None)})
        self.assertEqual([r['card'] for r in feedback.graded(self.db)], ['c0'])  # learning cards alone schedule

    def test_a_card_left_then_graded_takes_the_grade_once(self):
        left = reviewed('c0', self.NOW - 60_000, None, 3000, uid='l-1', word='問題')
        self.assertEqual(self.record(left)['stored'], 1)
        late = reviewed('c0', self.NOW - 59_000, 2000, 50_000, uid='g-1', word='問題', grade=3, graded=40_000)
        self.assertEqual(self.record(late), {'stored': 1, 'duplicates': 0, 'rejected': 0})
        [row] = feedback.cards(self.db)[:1]
        self.assertEqual((row['at'], row['reveal_ms'], row['grade'], row['grade_ms'], row['shown_ms'], row['uid']),
                         (self.NOW - 60_000, 3000, 3, 41_000, 51_000, 'l-1'))  # first shown kept, times from it
        self.assertEqual(self.record(dict(late, grade=1, uid='g-2')), {'stored': 0, 'duplicates': 1, 'rejected': 0})
        self.assertEqual(self.record(late), {'stored': 0, 'duplicates': 1, 'rejected': 0})
        self.assertEqual([r['card'] for r in feedback.graded(self.db)], ['c0'])

    def test_a_client_clock_ahead_is_taken_as_now(self):
        ahead = reviewed('c0', self.NOW + 30 * 60_000, 700, 2000, word='問題', grade=3, graded=1200)
        self.assertEqual(self.record(ahead)['stored'], 1)
        self.assertEqual(feedback.reviews(self.db)[0]['at'], self.NOW)
        found = self.record(reviewed('c1', self.NOW + HOUR + 1, 700, word='時間'))
        self.assertEqual(found['reasons'], [{'item': 0, 'error': 'at: a time within the last 7 days'}])

    def test_every_rejected_item_has_its_reason(self):
        """A batch of 50 rejected items (as T3 Code sends them): one reason each, by index, so each can be settled."""
        items = [reviewed('c9', self.NOW - 1000, 700, word='問題') for _ in range(25)]
        items += [{'kind': 'peek', 'view': 'v', 'context': 'c'} for _ in range(24)]  # malformed interactions too
        items.append(reviewed('c0', self.NOW - 1000, 700, word='問題'))  # stored
        found = self.record(*items)
        self.assertEqual((found['stored'], found['rejected']), (1, 49))
        self.assertEqual([r['item'] for r in found['reasons']], list(range(49)))
        self.assertEqual(found['reasons'][30]['error'], 'malformed or too old')

    def test_items_past_the_batch_limit_are_rejected_with_a_reason(self):
        items = [{'kind': 'peek'}] * feedback.MAX_ITEMS + [reviewed('c0', self.NOW - 1000, 700, word='問題')] * 2
        found = self.record(*items)
        self.assertEqual((found['stored'], found['rejected']), (0, feedback.MAX_ITEMS + 2))
        self.assertEqual(len(found['reasons']), feedback.MAX_ITEMS + 2)
        self.assertEqual(found['reasons'][-1], {'item': feedback.MAX_ITEMS + 1, 'error': 'over 500 in one batch'})

    def test_a_long_left_card_completed_keeps_its_times_in_order(self):
        self.record(reviewed('c0', self.NOW - 3 * HOUR, None, 2000, word='問題'))
        late = reviewed('c0', self.NOW - 1000, 700, 1500, word='問題', grade=3, graded=1200)
        self.assertEqual(self.record(late)['stored'], 1)
        row = feedback.cards(self.db)[0]
        self.assertTrue(row['reveal_ms'] <= row['grade_ms'] <= row['shown_ms'] <= feedback.MAX_REVIEW_MS, row)
        self.record(reviewed('c1', self.NOW - 3 * DAY, None, 2000, word='時間'))  # left days ago
        self.record(reviewed('c1', self.NOW - 1000, 700, 1500, word='時間', grade=1, graded=1200))
        row = [r for r in feedback.cards(self.db) if r['id'] == 'c1'][0]
        self.assertEqual((row['reveal_ms'], row['grade_ms'], row['shown_ms']), (feedback.MAX_REVIEW_MS,) * 3)

    def test_a_rejected_review_says_why(self):
        long = reviewed('c0', self.NOW - 1000, 700, feedback.MAX_REVIEW_MS + 1, word='問題')
        found = self.record(long, reviewed('c9', self.NOW - 1000, 700, word='問題'))
        self.assertEqual(found['rejected'], 2)
        self.assertEqual(found['reasons'], [{'item': 0, 'error': 'shownMs: 0 to 86400000 ms'},
                                            {'item': 1, 'error': 'no card served here with this id and word'}])
        self.assertEqual(self.record(dict(long, shownMs=feedback.MAX_REVIEW_MS)),
                         {'stored': 1, 'duplicates': 0, 'rejected': 0})  # clamped by the client: stored, no reasons

    def test_an_old_database_gets_the_grade_columns(self):
        path = self.home / 'old.sqlite'
        con = sqlite3.connect(str(path))
        con.executescript('''CREATE TABLE cards (id TEXT PRIMARY KEY, served_at INTEGER NOT NULL, source TEXT NOT NULL,
            word TEXT NOT NULL, kind TEXT NOT NULL, entry INTEGER, band INTEGER, weight REAL, at INTEGER,
            reveal_ms INTEGER, shown_ms INTEGER, via TEXT, uid TEXT, seq INTEGER NOT NULL DEFAULT 0);
            PRAGMA user_version = 6;''')
        con.execute("INSERT INTO cards VALUES ('old', 1, 'desktop', 'w', 'learning', NULL, NULL, NULL, 2, 900, 1500, "
                    "'desktop', NULL, 1)")
        con.commit()
        con.close()
        con = feedback.connect(path)
        try:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], feedback.SCHEMA_VERSION)
        finally:
            con.close()
        [row] = feedback.cards(path)
        self.assertEqual((row['id'], row['reveal_ms'], row['grade'], row['grade_ms']), ('old', 900, None, None))
        feedback.serve_card({'id': 'new', 'served_at': self.NOW - HOUR, 'source': 'mobile', 'word': '問題',
                             'kind': 'learning'}, path)
        stored = feedback.record([reviewed('new', self.NOW - 1000, 600, 1800, word='問題', grade=2, graded=900)], 't3',
                                 path, now=self.NOW)
        self.assertEqual(stored['stored'], 1)
        self.assertEqual(feedback.graded(path)[0]['grade'], 2)

    def test_reviews_leave_the_gloss_log_alone(self):
        self.record(reviewed('c0', self.NOW - 1000, 700, 2000, word='問題'))
        rows, contexts = feedback.load(self.db)
        self.assertEqual((rows, contexts), ([], {}))


def outcome(rng, known, k_ms=900, k_sd=0.4, k_skip=0.3, u_ms=3500, u_sd=0.6, u_skip=0.1):
    """A simulated card's (reveal ms or None, shown ms)."""
    if known:
        reveal = None if rng.random() < k_skip else math.exp(rng.gauss(math.log(k_ms), k_sd))
    else:
        reveal = None if rng.random() < u_skip else math.exp(rng.gauss(math.log(u_ms), u_sd))
    return reveal, (reveal or 0) + 500 + rng.random() * 1500


def em(real, fakes, steps=400):
    """Components fitted by EM on real cards [(x, P(known) a priori)] and made-up ones [x]."""
    parts = knowledge.Components()
    for _ in range(steps):
        cards = []
        for x, z in real:
            la, lb = parts.logs(x)
            cards.append((x, 1.0, knowledge.sigmoid(knowledge.logit(z) + la - lb)))
        if parts.update(cards, [(x, 1.0) for x in fakes]) < 1e-7:
            break
    return parts


def x_of(reveal, shown):
    return knowledge.review_outcome(reveal, shown)


class Mixture(unittest.TestCase):
    def cards(self, n, share, seed, **kinds):
        rng = random.Random(seed)
        return [(x_of(*outcome(rng, rng.random() < share, **kinds)), share) for _ in range(n)]

    def test_outcomes_over_a_minute_or_left_at_once_are_dropped(self):
        self.assertIs(knowledge.review_outcome(61_000, 61_000), False)
        self.assertIs(knowledge.review_outcome(None, 61_000), False)
        self.assertIs(knowledge.review_outcome(None, 100), False)
        self.assertIsNone(knowledge.review_outcome(None, 2000))
        self.assertEqual(knowledge.review_outcome(20, 900), math.log(knowledge.REVIEW_MIN_MS))

    def test_two_clear_components_separate(self):
        rng = random.Random(1)
        fakes = [x_of(*outcome(rng, False)) for _ in range(60)]
        parts = em(self.cards(600, 0.5, 2), fakes)
        self.assertAlmostEqual(math.exp(parts.mu[0]), 900, delta=120)
        self.assertAlmostEqual(math.exp(parts.mu[1]), 3500, delta=500)
        self.assertAlmostEqual(parts.a[0], 0.3, delta=0.07)
        self.assertAlmostEqual(parts.a[1], 0.1, delta=0.05)
        found = parts.separation()
        self.assertFalse(found['weak'])
        self.assertGreater(found['auc'], 0.9)
        self.assertGreater(found['worth'], 0.4)
        self.assertLessEqual(parts.info(0.3), 0.3 * 0.7)

    def test_a_reader_who_gives_up_as_fast_as_they_know_shows_a_weak_separation(self):
        alike = dict(u_ms=1000, u_sd=0.45, u_skip=0.25)
        rng = random.Random(3)
        fakes = [x_of(*outcome(rng, False, **alike)) for _ in range(60)]
        parts = em(self.cards(600, 0.5, 4, **alike), fakes)
        found = parts.separation()
        self.assertTrue(found['weak'])
        self.assertLess(found['auc'], 0.7)
        self.assertLess(found['worth'], knowledge.REVIEW_WEAK)

    def test_made_up_words_pin_the_unknown_component(self):
        rng = random.Random(6)
        fakes = [x_of(*outcome(rng, False, u_ms=6000, u_sd=0.3)) for _ in range(80)]
        real = self.cards(80, 0.9, 7, u_ms=6000, u_sd=0.3)  # mostly known: little to learn "not known" from
        pinned, alone = em(real, fakes), em(real, [])
        self.assertAlmostEqual(math.exp(pinned.mu[1]), 6000, delta=700)
        self.assertLess(abs(pinned.mu[1] - math.log(6000)), abs(alone.mu[1] - math.log(6000)))
        self.assertLess(pinned.s[1], 0.45)

    def test_a_handful_of_cards_cannot_swing_the_components(self):
        parts = em([(math.log(20_000), 0.5)] * 3, [math.log(150)] * 3)
        prior = knowledge.REVIEW_PRIOR
        self.assertLess(abs(parts.mu[0] - prior['known'][0]), 0.6)
        self.assertLess(abs(parts.mu[1] - prior['unknown'][0]), 1.0)


class InTheFit(unittest.TestCase):
    """Review cards in knowledge.Fit: their own offset, components fitted with the words."""
    WORDS = ['時間', '問題', '世界', '仕事', '会社', '情報']

    def log(self, reviews):
        words = [{'ja': ja, 'kana': '', 'status': 'active', 'added': '2026-09-01', 'changed': '2026-09-01'}
                 for ja in self.WORDS]
        log = evaluate.Log([], {}, words, reviews=reviews)
        d = dictionary.load()
        log.entries = {ja: d.find(ja) for ja in self.WORDS}
        return log

    def reviews(self, until, known, n=12, seed=1, fakes=20, **kinds):
        rng, out = random.Random(seed), []
        at = until - 5 * DAY
        for i in range(n):
            for ja in self.WORDS:
                reveal, shown = outcome(rng, ja in known, **kinds)
                out.append({'card': f'{ja}{i}', 'at': at, 'word': ja, 'kind': 'learning', 'entry': None,
                            'reveal': reveal, 'shown': shown})
                at += 60_000
        for i in range(fakes):
            reveal, shown = outcome(rng, False, **kinds)
            out.append({'card': f'f{i}', 'at': at, 'word': '悪売', 'kind': 'fake', 'reveal': reveal, 'shown': shown})
            at += 60_000
        return out

    def test_fast_reveals_raise_a_word_and_slow_ones_lower_it(self):
        until = evaluate.day_start('2026-10-01')
        known = {'時間', '問題', '世界'}
        fit = knowledge.Fit(self.log(self.reviews(until, known)), until)
        base = knowledge.Fit(self.log([]), until)
        for ja in self.WORDS:
            moved = fit.estimate(ja)[0] - base.estimate(ja)[0]
            self.assertGreater(moved, 0.3) if ja in known else self.assertLess(moved, -0.3)
        summary = fit.review_summary()
        self.assertEqual((summary['cards'], summary['fakes']), (72, 20))
        self.assertFalse(summary['weak'])
        self.assertAlmostEqual(summary['known']['ms'], 900, delta=200)
        self.assertLess(summary['offsetSd'], 1.0)
        without = knowledge.Fit(self.log(self.reviews(until, known)), until, reviews=False)
        self.assertEqual([round(x, 6) for x in without.k], [round(x, 6) for x in base.k])
        self.assertIsNone(without.review_summary())
        self.assertIn('review', fit.state())
        again = knowledge.Fit(self.log(self.reviews(until, known)), until, start=fit.state())
        self.assertAlmostEqual(again.estimate('時間')[0], fit.estimate('時間')[0], delta=0.05)

    def test_graded_cards_are_explicit_answers(self):
        """Every card revealed alike (900 ms): alone the reveal times can't tell the words apart, the grades do (a card
        a day each, so every grade is an answer)."""
        until = evaluate.day_start('2026-10-01')
        known = {'時間', '問題', '世界'}
        rows = []
        for i in range(6):
            at = until - 7 * DAY + i * DAY
            for ja in self.WORDS:
                rows.append({'card': f'{ja}{i}', 'at': at, 'word': ja, 'kind': 'learning', 'entry': None,
                             'reveal': 900, 'shown': 2000, 'grade': 3 if ja in known else 1, 'grade_ms': 1300})
                at += 60_000
        rows.append({'card': 'f0', 'at': at, 'word': '悪売', 'kind': 'fake', 'reveal': 3000, 'shown': 4000,
                     'grade': 3, 'grade_ms': 3500})
        fit = knowledge.Fit(self.log(rows), until)
        base = knowledge.Fit(self.log([]), until)
        for ja in self.WORDS:
            moved = fit.estimate(ja)[0] - base.estimate(ja)[0]
            self.assertGreater(moved, 0.5) if ja in known else self.assertLess(moved, -0.5)
        summary = fit.review_summary()
        self.assertEqual((summary['graded'], summary['cards'], summary['fakes']), (36, 0, 0))  # the made-up card is
        # of the graded flow: it claims nothing and stays out of the mixture
        ungraded = knowledge.Fit(self.log([dict(r, grade=None) for r in rows]), until)
        spread = lambda f: f.estimate('時間')[0] - f.estimate('仕事')[0]
        self.assertGreater(spread(fit), spread(ungraded) + 1.0)
        answers, _fakes, _extra, shown, graded = knowledge.review_answers(rows, self.log([]).entries)
        self.assertEqual((len(answers), len(shown), len(graded)), (0, 36, 36))  # still reads for the clock
        self.assertIn('世界', knowledge.evidenced(self.log(rows), until))

    def card(self, ja, at, grade, kind='learning', reveal=2500, shown=4000):
        return {'card': f'{ja}{at}{kind}', 'at': at, 'served': at, 'word': ja, 'kind': kind, 'entry': None,
                'reveal': reveal, 'shown': shown, 'grade': grade, 'grade_ms': None if grade is None else 3000,
                'source': 'desktop', 'band': 0, 'weight': 1.0}

    def test_learning_steps_are_not_answers(self):
        """Again, then Good after the 1-minute step and Good after the 10-minute one: one answer (not known), as one
        Again alone is; a step the next day is an answer again; and at most one answer per word a day."""
        until = evaluate.day_start('2026-10-01')
        at, minute = until - 5 * DAY, 60_000
        rows = [self.card('時間', at, 1), self.card('時間', at + minute, 3), self.card('時間', at + 12 * minute, 3),
                self.card('問題', at, 1)]
        fit, base = knowledge.Fit(self.log(rows), until), knowledge.Fit(self.log([]), until)
        moved = [fit.estimate(ja)[0] - base.estimate(ja)[0] for ja in ('時間', '問題')]
        self.assertLess(moved[0], -0.5)
        self.assertAlmostEqual(moved[0], moved[1], delta=0.05)
        _a, _f, _e, shown, graded = knowledge.review_answers(rows, self.log([]).entries)
        self.assertEqual([(ja, y) for _at, ja, y in graded], [('時間', 1), ('問題', 1)])
        self.assertEqual(len(shown), 4)  # every card still a read
        rows += [self.card('世界', at, 1), self.card('世界', at + DAY, 3),  # a step overnight: an answer
                 self.card('仕事', at, 3, 'probe'), self.card('仕事', at + HOUR, 1, 'probe')]  # one a day
        graded = knowledge.review_answers(rows, self.log([]).entries)[4]
        self.assertEqual([(ja, y) for _at, ja, y in graded if ja in ('世界', '仕事')],
                         [('世界', 1), ('仕事', 0), ('世界', 0)])

    def test_the_graded_flow_keeps_out_of_the_mixture(self):
        """Once a client grades, a card it closes unrevealed is no evidence (it read as a strong known before), and its
        made-up cards, always revealed to be graded, don't bend the components' chance of no reveal."""
        until = evaluate.day_start('2026-10-01')
        at = until - 5 * DAY
        rows = [self.card('悪売', at + i * 60_000, 1, 'fake') for i in range(30)]
        rows += [self.card('問題', at + i * DAY // 30, 3) for i in range(30)]
        rows += [self.card('時間', at + DAY + i * HOUR, None, reveal=None, shown=5000) for i in range(3)]
        fit = knowledge.Fit(self.log(rows), until)
        alone = knowledge.Fit(self.log([r for r in rows if r['word'] != '時間']), until)
        self.assertAlmostEqual(fit.estimate('時間')[0], alone.estimate('時間')[0], delta=1e-6)
        summary = fit.review_summary()
        self.assertEqual((summary['cards'], summary['fakes']), (0, 0))
        self.assertEqual(summary['unknown']['skip'], knowledge.REVIEW_PRIOR['unknown'][2])
        wedge = [dict(r, source='mobile', grade=None, grade_ms=None, at=r['at'] - 3 * DAY) for r in rows]
        found = knowledge.review_answers(wedge + rows, self.log([]).entries)  # another client, before it graded
        self.assertEqual((len(found[0]), len(found[1])), (33, 30))  # the reveal flow's cards, read as before

    def test_overlapping_components_tell_the_fit_little(self):
        until = evaluate.day_start('2026-10-01')
        known = {'時間', '問題', '世界'}
        alike = dict(u_ms=950, u_sd=0.4, u_skip=0.3)
        fit = knowledge.Fit(self.log(self.reviews(until, known, **alike)), until)
        base = knowledge.Fit(self.log([]), until)
        self.assertTrue(fit.review_summary()['weak'])
        clear = knowledge.Fit(self.log(self.reviews(until, known)), until)
        spread = lambda f: f.estimate('時間')[0] - f.estimate('仕事')[0]
        self.assertLess(abs(spread(fit) - spread(base)), abs(spread(clear) - spread(base)) / 2)


class AsARead(unittest.TestCase):
    """A review card is a showing of its word: a read for the recency clock and for the experiment's exposure."""

    def test_a_reading_right_after_a_review_is_near_the_floor(self):
        at = evaluate.day_start('2026-10-01') + 10 * HOUR
        group = evaluate.Group(at + HOUR, 'browser', ('browser', 'v1', 'p'), False, [])
        group.obs.append(evaluate.Observation(at=at + HOUR, day='2026-10-01', surface='browser', key=group.key,
                                              episode=False, word='問題', written='問題', form=False, y=0.0,
                                              weight=0.6, near=0.0, ms=1, words=1, share=1.0, read=0.6,
                                              status='learning', seen=0))
        words = [{'ja': '問題', 'kana': 'もんだい', 'status': 'active', 'added': '', 'changed': ''}]
        card = {'card': 'c1', 'at': at, 'word': '問題', 'kind': 'learning', 'reveal': 900, 'shown': 2000}
        chance = lambda o, u, s: 0.2
        for reviews, u in (([], 1.0), ([card], HOUR / (knowledge.LONG_DAYS * DAY))):
            log = evaluate.Log([group], {}, words, reviews=reviews)
            log.entries = {'問題': dictionary.load().find('問題')}
            found = knowledge.read_clocks(log, knowledge.PACE_MS, knowledge.READ_MAX, chance)
            self.assertAlmostEqual(found[id(group.obs[0])][0], u)

    def test_the_experiment_counts_a_review_of_a_shown_word(self):
        day = '2026-10-01'
        found = [{'ja': '問題', 'arm': 'shown', 'day': day, 'at': None, 'probe': fwlib.add_days(day, 21)}]
        words = [{'ja': '問題', 'kana': '', 'status': 'active', 'added': day, 'changed': day}]
        at = evaluate.day_start(day) + 2 * DAY
        log = evaluate.Log([], {}, words, reviews=[{'card': 'c', 'at': at, 'word': '問題', 'kind': 'learning',
                                                    'reveal': None, 'shown': 900}])
        log.entries = {'問題': dictionary.load().find('問題')}
        per_day, read, leaks = experiment.exposure(found, log)
        self.assertGreater(per_day, 0)
        self.assertEqual(leaks, {'held': 0, 'shown': 0})


class Choice(unittest.TestCase):
    """review.py's helpers on a word state alone: the new cards' order, made-up and probe words."""
    NOW = int(time.time() * 1000)

    def state(self, n=8):
        today = knowledge.day_of(self.NOW)
        state = fresh_state(today)
        self.words = queue_words(state, n)
        return learning(state, self.words, fwlib.add_days(today, -30))

    def test_behind_first_then_the_least_certain(self):
        state = self.state(4)
        a, b, c, d = self.words
        started = self.NOW - 3 * DAY
        for w in state['words']:
            if w['ja'] in (a, b):
                w['arm'] = {'arm': 'shown', 'draw': 0.9, 'day': knowledge.day_of(started), 'at': started,
                            'probe': fwlib.add_days(knowledge.day_of(started), 21)}
        shown = [{'at': self.NOW - DAY + i, 'surface': 't3', 'y': 0.0, 'w': 1.0} for i in range(4)]
        know = {'words': {a: {'k': 0.5, 'sd': 0.2}, b: {'k': 0.5, 'sd': 0.9, 'history': shown},
                          c: {'k': 0.5, 'sd': 1.1}, d: {'k': 0.5, 'sd': 0.4}}}
        today = knowledge.day_of(self.NOW)
        order = [w['ja'] for w in review._new_order(state, know, [], set(), today, self.NOW, random.Random(1))]
        self.assertEqual(order, [a, c, b, d])  # a is behind (0 showings in 3 days); b had 4, then by sd
        order = [w['ja'] for w in review._new_order(state, know, [], {a}, today, self.NOW, random.Random(1))]
        self.assertEqual(order, [c, b, d])  # a word with a schedule (or marked known) is no new card

    def test_the_words_that_come_up_most_in_reading_first(self):
        state = self.state(4)
        a, b, c, d = self.words
        know = {'words': {a: {'k': 0.5, 'sd': 1.5, 'id': 1}, b: {'k': 0.5, 'sd': 0.2, 'id': 2},
                          c: {'k': 0.5, 'sd': 0.9, 'id': 3}, d: {'k': 0.5, 'sd': 0.4}}}
        today = knowledge.day_of(self.NOW)
        order = lambda reading: [w['ja'] for w in review._new_order(state, know, [], set(), today, self.NOW, 0,
                                                                     reading)]
        self.assertEqual(order(None), [a, c, d, b])  # no counts: the least certain first, as before
        self.assertEqual(order({'2': 40, '3': 7, '9': 100}), [b, c, a, d])  # by times read, then as before
        for w in state['words']:
            if w['ja'] == b:
                w['ja'], w['en'] = '六', 'six'
        know['words']['六'] = know['words'].pop(b)
        self.assertEqual(order({'2': 40, '3': 7}), [c, a, d, '六'])  # a number's count is the digits read: none

    def test_reading_counts_are_the_last_30_days(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'exposure.json'
        today = fwlib.today()
        fwlib.write_json(path, {'reading': {today: {'2': 3, '5': 1}, fwlib.add_days(today, -3): {'2': 4, 'x': 'no'},
                                            fwlib.add_days(today, -fwlib.READING_DAYS): {'5': 50}}})
        self.assertEqual(review.reading_counts(path), {'2': 7, '5': 1})
        self.assertEqual(review.reading_counts(path.with_name('none.json')), {})

    def test_a_review_counts_as_a_showing_while_behind(self):
        state = self.state(2)
        a, b = self.words
        started = self.NOW - 2 * DAY
        for w in state['words']:
            if w['ja'] in (a, b):
                w['arm'] = {'arm': 'shown', 'draw': 0.9, 'day': knowledge.day_of(started), 'at': started,
                            'probe': fwlib.add_days(knowledge.day_of(started), 21)}
        served = [{'id': f'r{i}', 'served_at': self.NOW - 6 * HOUR, 'word': a, 'kind': 'learning',
                   'at': self.NOW - 6 * HOUR} for i in range(3)]
        today = knowledge.day_of(self.NOW)
        self.assertEqual(review._new_order(state, {}, served, set(), today, self.NOW, random.Random(1))[0]['ja'], b)
        self.assertEqual(review.learning_start(next(w for w in state['words'] if w['ja'] == a)), started)

    def test_made_up_words_come_back_least_recently_used(self):
        fakes = probe.read_fakes()
        served = [{'id': f'f{i}', 'served_at': self.NOW - DAY - i * HOUR, 'word': ja, 'kind': 'fake', 'at': None}
                  for i, (ja, _kana) in enumerate(fakes)]
        card = review._fake(served, set(), self.NOW, random.Random(1))
        self.assertEqual(card['word'], fakes[-1][0])  # all used within FAKE_REUSE_DAYS: the least recent
        served[3]['served_at'] = self.NOW - (review.FAKE_REUSE_DAYS + 1) * DAY
        self.assertEqual(review._fake(served, set(), self.NOW, random.Random(1))['word'], fakes[3][0])
        self.assertEqual(card['gloss'], review.MADE_UP)

    def test_probe_words_are_not_asked_again_within_the_repeat_days(self):
        state, rng, seen = self.state(0), random.Random(2), set()
        served = []
        for i in range(60):
            card = review._probe(state, served, set(), [], {}, knowledge.day_of(self.NOW), rng, FRAME)
            self.assertNotIn(card['word'], seen)
            seen.add(card['word'])
            served.append({'id': f'p{i}', 'served_at': self.NOW - 6 * HOUR, 'word': card['word'], 'kind': 'probe',
                           'at': None})
            self.assertEqual(FRAME.bands[card['band']][0]['weight'] >= card['weight'], True)


class Deck(Home):
    """A state dir with 30 learning words in a known order (by sd), on a fixed clock, and the probe and made-up cards
    off unless a test turns them on."""
    T0 = int(time.mktime((2026, 6, 10, 12, 0, 0, 0, 0, -1)) * 1000)  # noon local, a day without a clock change

    def setUp(self):
        super().setUp()
        state = fresh_state('2026-05-01')
        self.words = queue_words(state, 30)
        learning(state, self.words, '2026-05-01')  # learning for weeks: none behind, so the order is by sd
        fwlib.save_state(state)
        fwlib.write_json(fwlib.home() / knowledge.KNOWLEDGE, {'words': {
            ja: {'k': 0.0, 'sd': 2.0 - i / 100} for i, ja in enumerate(self.words)}})  # today's order: as listed
        self.quiet = mock.patch.multiple(review, PROBE_SHARE=0.0, FAKE_SHARE=0.0)
        self.quiet.start()

    def tearDown(self):
        self.quiet.stop()
        super().tearDown()

    def next(self, at, exclude=(), more=False, seed=1):
        return review.next_card('desktop', exclude, self.db, at, random.Random(seed), more)

    def grade(self, card, at, grade, reveal=700):
        """The review item of a card shown at `at`, graded `grade` 400 ms after its reveal (None: left)."""
        item = reviewed(card['id'], at, reveal, reveal + 1500, word=card['ja'], grade=grade)
        self.assertEqual(feedback.record([item], 't3', self.db, now=at + 5000)['stored'], 1)

    def deck(self):
        con = feedback.connect(self.db)
        try:
            return review.schedules(con, self.db)
        finally:
            con.close()

class Queue(Deck):
    """The queue through review.next_card, with grades stored as POST /v1/interactions stores them: new cards through
    their learning steps, Anki's order, today's limit, Review more, the learn-ahead window, known words, pending and
    left cards, the cache, and the probe and made-up cards in between."""

    def test_new_cards_go_through_their_steps_and_graduate(self):
        w0, w1, w2 = self.words[:3]
        card, counts = self.next(self.T0)
        self.assertEqual((card['ja'], card['queue'], counts), (w0, 'new', {'new': review.NEW_PER_DAY, 'learning': 0, 'review': 0}))
        self.grade(card, self.T0, srs.GOOD)  # to the 10-minute step
        card, counts = self.next(self.T0 + 5000)
        self.assertEqual((card['ja'], card['queue'], counts), (w1, 'new', {'new': review.NEW_PER_DAY - 1, 'learning': 1, 'review': 0}))
        self.grade(card, self.T0 + 5000, srs.AGAIN)  # to the 1-minute step
        card, _counts = self.next(self.T0 + 10_000)
        self.assertEqual(card['ja'], w2)
        self.grade(card, self.T0 + 10_000, None)  # left: schedules nothing, still new
        card, counts = self.next(self.T0 + 120_000)  # w1 is due: learning before new
        self.assertEqual((card['ja'], card['queue']), (w1, 'learning'))
        self.assertEqual(card['intervals'], {'again': 60, 'hard': 330, 'good': 600,
                                             'easy': card['intervals']['easy']})
        self.assertEqual(counts, {'new': review.NEW_PER_DAY - 2, 'learning': 2, 'review': 0})
        self.grade(card, self.T0 + 120_000, srs.GOOD)
        card, _counts = self.next(self.T0 + 125_000)
        self.assertEqual((card['ja'], card['queue']), (self.words[3], 'new'))  # the card left waits behind the others
        card, _counts = self.next(self.T0 + 125_000, exclude=[card['id']])
        self.assertEqual((card['ja'], card['queue']), (self.words[4], 'new'))
        self.grade(card, self.T0 + 125_000, srs.EASY)  # graduates at once
        at = self.T0 + 15 * 60_000  # w0 due (10 minutes, fuzzed by up to 2.5)
        card, _counts = self.next(at)
        self.assertEqual((card['ja'], card['queue'], card['intervals']['good']), (w0, 'learning', 2 * 86_400))
        self.grade(card, at, srs.GOOD)
        w0_card = self.deck()[w0][0]
        self.assertEqual((w0_card.state, w0_card.interval), ('review', 2))  # S 2.3: no fuzz under 2.5 days
        self.assertEqual(w0_card.due, srs.day_start(srs.day_number(at) + 2))
        two_days = self.T0 + 2 * DAY + HOUR
        card, counts = self.next(two_days)
        self.assertEqual((card['ja'], card['queue']), (w1, 'learning'))  # its 10-minute step, never graded: first
        card, counts = self.next(two_days, exclude=[card['id']])
        self.assertEqual((card['ja'], card['queue']), (w0, 'review'))  # due today: reviews before new cards
        self.assertTrue(card['intervals']['hard'] < card['intervals']['good'] < card['intervals']['easy'])
        self.assertEqual(counts['review'], 1)
        self.assertEqual(counts['new'], review.NEW_PER_DAY)  # a new day, a new limit

    def test_the_days_new_card_limit_and_review_more(self):
        at = self.T0
        for i in range(review.NEW_PER_DAY):
            card, counts = self.next(at)
            self.assertEqual((card['ja'], counts['new']), (self.words[i], review.NEW_PER_DAY - i))
            self.grade(card, at, srs.EASY)  # graduates: due in days
            at += 20_000
        card, counts = self.next(at)
        self.assertEqual((card, counts), (None, {'new': 0, 'learning': 0, 'review': 0}))  # done for today
        card, counts = self.next(at, more=True)
        n = review.NEW_PER_DAY
        self.assertEqual((card['ja'], card['queue'], counts['new']), (self.words[n], 'new', len(self.words) - n))
        tomorrow = srs.day_start(srs.day_number(at) + 1) + 60_000
        self.assertEqual(self.next(tomorrow)[1]['new'], n)  # a new day's limit; the one served, not graded, too

    def test_anki_order_learning_then_review_then_new_then_learn_ahead(self):
        a, b, c = self.words[:3]
        days_ago = self.T0 - 10 * DAY
        card, _ = self.next(days_ago)
        self.assertEqual(card['ja'], a)
        self.grade(card, days_ago, srs.EASY)  # due in 6 to 10 days: a review card due now
        held = lambda *words: [r['id'] for r in feedback.cards(self.db) if r['word'] in words]
        card, _ = self.next(self.T0 - 3 * 60_000, exclude=held(a))  # a is due already
        self.assertEqual(card['ja'], b)
        self.grade(card, self.T0 - 3 * 60_000, srs.AGAIN)  # due in a minute or so: learning, due now
        card, _ = self.next(self.T0 - 60_000, exclude=held(a, b))
        self.assertEqual(card['ja'], c)
        self.grade(card, self.T0 - 60_000, srs.GOOD)  # due in 10 minutes: within the learn-ahead window
        card, counts = self.next(self.T0)
        self.assertEqual(counts, {'new': review.NEW_PER_DAY - 2, 'learning': 2, 'review': 1})  # a came 10 days ago
        self.assertEqual((card['ja'], card['queue']), (b, 'learning'))
        card, _ = self.next(self.T0, exclude=held(b))
        self.assertEqual((card['ja'], card['queue']), (a, 'review'))
        card, _ = self.next(self.T0, exclude=held(a, b))
        self.assertEqual((card['ja'], card['queue']), (self.words[3], 'new'))
        state = fwlib.load_state()
        for w in state['words']:
            if w['ja'] in self.words[3:]:
                w['status'] = 'queue'  # no new card left
        fwlib.save_state(state)
        card, counts = self.next(self.T0, exclude=held(a, b))
        self.assertEqual((card['ja'], card['queue']), (c, 'learning'))  # nothing else: a learning card early
        self.assertEqual(counts, {'new': 0, 'learning': 2, 'review': 1})

    def test_marked_known_leaves_the_deck_and_the_models_known_keeps_its_schedule(self):
        a, b = self.words[:2]
        for ja in (a, b):
            card, _ = self.next(self.T0 - 20 * DAY)
            self.assertEqual(card['ja'], ja)
            self.grade(card, self.T0 - 20 * DAY, srs.EASY)
        state = fwlib.load_state()
        for w in state['words']:
            if w['ja'] in (a, b):
                w['status'] = 'known'  # a by the reader's command, b by the model
        fwlib.save_state(state)
        knowledge.log_command([a], 'known', now=(self.T0 - DAY) / 1000)
        card, counts = self.next(self.T0)
        self.assertEqual((card['ja'], card['queue'], counts['review']), (b, 'review', 1))
        knowledge.log_command([a], 'learn', now=(self.T0 - HOUR) / 1000)
        self.assertEqual(self.next(self.T0, exclude=[card['id']])[1]['review'], 2)  # back, with its schedule

    def test_hidden_deck_words_are_never_served_nor_counted(self):
        a = self.words[0]
        card, _ = self.next(self.T0 - 20 * DAY)
        self.grade(card, self.T0 - 20 * DAY, srs.EASY)
        state = fwlib.load_state()
        for w in state['words']:
            if w['ja'] == a:
                w['arm'] = {'arm': 'held', 'draw': 0.1, 'day': '2026-06-05', 'probe': fwlib.add_days('2026-06-05', 21)}
        fwlib.save_state(state)
        card, counts = self.next(self.T0)
        self.assertNotEqual(card['ja'], a)
        self.assertEqual(counts['review'], 0)

    def test_a_client_that_never_grades_moves_through_the_words(self):
        """A client from before grades leaves every card: the words left go behind the others, longest left first,
        so it sees the day's new cards in turn, then the first again, never the same card twice running."""
        seen, at = [], self.T0
        for _ in range(review.NEW_PER_DAY + 2):
            card, counts = self.next(at)
            self.assertEqual(counts['new'], review.NEW_PER_DAY)  # nothing graded: nothing introduced
            self.grade(card, at, None)
            seen.append(card['ja'])
            at += 30_000
        self.assertEqual(seen, self.words[:review.NEW_PER_DAY] + self.words[:2])
        self.assertEqual(self.next(at + review.LEFT_HOURS * HOUR)[0]['ja'], self.words[0])  # waited long enough

    def test_a_peek_holds_no_word_back(self):
        """A badge's peeks between cards: the counts the real request would give, and the same card after them."""
        card, _ = self.next(self.T0)
        self.grade(card, self.T0, srs.GOOD)
        for i in range(5):
            self.assertEqual(review.next_card('mobile', (), self.db, self.T0 + 1000 + i, peek=True),
                             (None, {'new': review.NEW_PER_DAY - 1, 'learning': 1, 'review': 0}))
        self.assertEqual(len(feedback.cards(self.db)), 1)
        card, counts = self.next(self.T0 + 2000)
        self.assertEqual((card['ja'], counts), (self.words[1], {'new': review.NEW_PER_DAY - 1, 'learning': 1, 'review': 0}))
        self.assertEqual(review.next_card('mobile', (), self.db, self.T0 + 3000, peek=True, more=True)[1]['new'],
                         28)  # 29 left, one of them served and pending

    def test_released_cards_are_as_if_never_served(self):
        first, _ = self.next(self.T0)
        prefetched, _ = self.next(self.T0 + 1000)  # the next card fetched ahead, then dropped unshown
        self.assertEqual((first['ja'], prefetched['ja']), (self.words[0], self.words[1]))
        self.grade(first, self.T0, None)
        self.assertEqual(review.next_card('mobile', (), self.db, self.T0 + 2000, release=[prefetched['id']],
                                          peek=True)[1]['new'], review.NEW_PER_DAY - 1)  # another client's: still pending
        card, counts = review.next_card('desktop', (), self.db, self.T0 + 2000, release=[prefetched['id']],
                                        peek=True)
        self.assertEqual((card, counts['new']), (None, review.NEW_PER_DAY))  # released along with a peek
        self.assertEqual(self.next(self.T0 + 3000)[0]['ja'], self.words[1])  # no longer pending: served at once
        stored = feedback.record([reviewed(prefetched['id'], self.T0 + 1000, 700, word=prefetched['ja'], grade=3)],
                                 't3', self.db, now=self.T0 + 5000)
        self.assertEqual((stored['rejected'], stored['reasons'][0]['error']), (1, 'card released unshown'))
        self.assertNotIn(prefetched['ja'], review._left(feedback.cards(self.db), self.T0 + 6000))

    def test_the_new_card_limit_holds_while_a_grade_is_in_flight(self):
        """No sd yet (every new word ties): the order stays the same between requests, and the 20th new card's grade,
        not back yet, keeps a 21st out."""
        fwlib.write_json(fwlib.home() / knowledge.KNOWLEDGE, {'words': {}})
        at, seen = self.T0, []
        for i in range(review.NEW_PER_DAY - 1):
            card, _ = self.next(at, seed=i)
            seen.append(card['ja'])
            self.grade(card, at, srs.EASY)
            at += 20_000
        twentieth, counts = self.next(at, seed=100)
        self.assertEqual((twentieth['queue'], counts['new']), ('new', 1))
        card, counts = self.next(at + 3000, seed=101)  # before its grade arrives
        self.assertEqual((card, counts), (None, {'new': 0, 'learning': 0, 'review': 0}))
        self.grade(twentieth, at, srs.EASY)
        q = review.queues(fwlib.load_state(), knowledge.load(), feedback.cards(self.db), self.deck(), at + 9000)
        self.assertEqual(q['introduced'], review.NEW_PER_DAY)
        order = lambda seed: [w['ja'] for w in review.queues(fwlib.load_state(), knowledge.load(), [], {}, at,
                                                              random.Random(seed), more=True)['new']]
        self.assertEqual(order(1), order(2))  # ties broken the same way all day

    def test_the_buttons_say_what_the_grade_gets_past_4am(self):
        w = self.words[0]
        start = self.T0 - 12 * DAY
        card, _ = self.next(start)
        self.grade(card, start, srs.EASY)
        due = srs.day_number(self.deck()[w][0].due)
        late = srs.day_start(due + 1) - 5 * 60_000  # 3:55, the card due
        card, _ = self.next(late)
        self.assertEqual((card['ja'], card['queue']), (w, 'review'))
        item = reviewed(card['id'], late, 2000, 11 * 60_000, word=w, grade=srs.GOOD, graded=10 * 60_000)  # 4:05
        self.assertEqual(feedback.record([item], 't3', self.db, now=late + 11 * 60_000)['stored'], 1)
        after = self.deck()[w][0]
        self.assertEqual(after.interval * 86_400, card['intervals']['good'])
        self.assertEqual(after.due, srs.day_start(srs.day_number(late + 10 * 60_000) + after.interval))  # from 4:05

    def test_a_schedule_follows_the_entry_id(self):
        """A word served before it had an entry id, then with one: one schedule, under the id, found by either."""
        w0 = self.words[0]
        card, _ = self.next(self.T0)
        self.grade(card, self.T0, srs.GOOD)
        know = fwlib.read_json(fwlib.home() / knowledge.KNOWLEDGE)
        know['words'][w0]['id'] = 1234567
        fwlib.write_json(fwlib.home() / knowledge.KNOWLEDGE, know)
        card, _ = self.next(self.T0 + 15 * 60_000)
        self.assertEqual((card['ja'], card['queue']), (w0, 'learning'))
        self.grade(card, self.T0 + 15 * 60_000, srs.GOOD)
        deck = self.deck()
        self.assertEqual(list(deck), ['entry:1234567'])
        self.assertEqual((deck['entry:1234567'][0].state, deck['entry:1234567'][0].reps), ('review', 2))
        self.assertEqual(review.revlog(self.db)[0][0], srs.card_id(w0))  # the fuzz's id: unchanged by the id

    def test_two_spellings_of_one_entry_are_one_card(self):
        """Two learning words with one dictionary entry (a kanji spelling and a kana one): one new card, one schedule;
        the other spelling never comes up as new, and the day's limit counts the word once."""
        w0, w1 = self.words[:2]
        know = fwlib.read_json(fwlib.home() / knowledge.KNOWLEDGE)
        know['words'][w0]['id'] = know['words'][w1]['id'] = 4242
        fwlib.write_json(fwlib.home() / knowledge.KNOWLEDGE, know)
        card, counts = self.next(self.T0)
        self.assertEqual((card['ja'], counts['new']), (w0, review.NEW_PER_DAY))
        self.grade(card, self.T0, srs.GOOD)
        seen = []
        for i in range(25):
            card, counts = self.next(self.T0 + (i + 1) * 30_000)
            if card is None:
                break
            seen.append(card['ja'])
            self.grade(card, self.T0 + (i + 1) * 30_000, srs.EASY)
        self.assertNotIn(w1, seen)
        self.assertEqual(len([k for k in self.deck() if k == 'entry:4242']), 1)
        self.assertEqual(len(self.deck()), review.NEW_PER_DAY)  # the shared word once among the day's cards

    def test_a_word_whose_entry_id_changes_keeps_one_schedule(self):
        rows = [{'card': 'a', 'word': 'x', 'entry': None}, {'card': 'b', 'word': 'x', 'entry': 7},
                {'card': 'c', 'word': 'x', 'entry': 8}, {'card': 'd', 'word': 'y', 'entry': 8},
                {'card': 'e', 'word': 'z', 'entry': None}]
        groups = feedback.deck_groups(rows)
        self.assertEqual({k: [r['card'] for r in v] for k, v in groups.items()},
                         {'entry:8': ['a', 'b', 'c', 'd'], 'z': ['e']})

    def test_an_id_change_moves_its_spelling_and_chains_nothing(self):
        """kata had id 1; ho had 1, then 2; hou has 2: ho moves to 2 with hou (one entry, two spellings), kata stays."""
        rows = [{'card': 'a', 'word': 'kata', 'entry': 1}, {'card': 'b', 'word': 'ho', 'entry': 1},
                {'card': 'c', 'word': 'ho', 'entry': 2}, {'card': 'd', 'word': 'hou', 'entry': 2},
                {'card': 'e', 'word': 'ho', 'entry': None}]
        groups = feedback.deck_groups(rows)
        self.assertEqual({k: [r['card'] for r in v] for k, v in groups.items()},
                         {'entry:1': ['a'], 'entry:2': ['b', 'c', 'd', 'e']})

    def test_a_schedule_shows_its_latest_graded_spelling_or_any_it_can(self):
        w0, w1, w2 = self.words[:3]
        know = fwlib.read_json(fwlib.home() / knowledge.KNOWLEDGE)
        know['words'][w0]['id'] = know['words'][w1]['id'] = 4242  # w0 first in the state: by entry id, w0
        fwlib.write_json(fwlib.home() / knowledge.KNOWLEDGE, know)
        due = srs.Card(state=srs.REVIEW, stability=5.0, difficulty=5.0, last=self.T0 - 9 * DAY,
                       due=self.T0 - DAY, interval=8, reps=3)
        log = [(self.T0 - 9 * DAY, 3, srs.REVIEW, 1000)]
        decks = {'entry:4242': (due, log, w1, 1, frozenset({w0, w1})),  # last graded as w1: shown as w1
                 'entry:9': (due, log, 'gone', 2, frozenset({'gone', w2}))}  # neither id nor latest in the state
        q = review.queues(fwlib.load_state(), know, [], decks, self.T0)
        self.assertEqual(sorted(w['ja'] for _card, w, _cid in q['review']), sorted([w1, w2]))
        self.assertNotIn(w0, [w['ja'] for w in q['new']])  # nor is the other spelling a new card

    def test_text_is_cut_as_t3_counts_it(self):
        wide = chr(0x20000)  # a character beyond the BMP: two UTF-16 code units
        self.assertEqual(review.utf16('a' + wide), 3)
        self.assertEqual(review.cut('a' * 199 + wide, 200), 'a' * 199)  # not split, and within 200 code units
        self.assertEqual(review.cut(wide * 100, 200), wide * 100)
        state = fwlib.load_state()
        for w in state['words']:
            if w['ja'] == self.words[0]:
                w['en'] = 'b' * 199 + wide + 'c'
        fwlib.save_state(state)
        card, _ = self.next(self.T0)
        self.assertEqual((card['ja'], review.utf16(card['gloss'])), (self.words[0], 199))

    def test_pending_and_excluded_words_wait(self):
        w0, w1, w2 = self.words[:3]
        first, _ = self.next(self.T0)
        self.assertEqual(first['ja'], w0)
        self.assertEqual(self.next(self.T0 + 1000)[0]['ja'], w1)  # w0's grade may still be on its way
        self.assertEqual(self.next(self.T0 + 2000, exclude=[first['id']])[0]['ja'], w2)
        self.assertEqual(self.next(self.T0 + review.PENDING_MS + 1)[0]['ja'], w0)  # never reported: served again

    def test_the_schedules_are_cached_until_a_grade_is_stored(self):
        card, _ = self.next(self.T0)
        before = self.deck()
        self.assertIs(self.deck(), before)
        self.grade(card, self.T0, None)  # left: nothing to replay
        self.assertIs(self.deck(), before)
        card, _ = self.next(self.T0 + 1000)
        self.grade(card, self.T0 + 1000, srs.HARD)
        after = self.deck()
        self.assertIsNot(after, before)
        self.assertEqual(list(after), [card['ja']])
        with mock.patch.object(srs, 'PARAMETERS', srs.PARAMETERS[:20] + (0.2,)):  # another decay: recomputed
            self.assertIsNot(self.deck(), after)

    def test_probe_and_made_up_cards_come_in_between_as_new_cards(self):
        self.quiet.stop()
        shares = review.PROBE_SHARE, review.FAKE_SHARE
        rng, kinds = random.Random(4), {}
        state = fwlib.load_state()  # room for the ten real cards a 10-minute pending window holds at one a minute
        state.setdefault('prefs', {})[fwlib.REVIEW_NEW_PREF] = 20
        fwlib.save_state(state)
        try:
            for i in range(400):
                at = self.T0 + i * 60_000
                pending = sum(1 for r in feedback.cards(self.db) if r['kind'] == 'learning'
                              and r['served_at'] >= at - review.PENDING_MS)  # real new cards in flight count
                card, counts = review.next_card('desktop', (), self.db, at, rng)
                self.assertEqual(card['queue'], 'new')
                self.assertEqual(counts, {'new': 20 - pending, 'learning': 0, 'review': 0})  # probe, fake: never
                self.assertEqual({k: card['intervals'][k] for k in ('again', 'hard', 'good')},
                                 {'again': 60, 'hard': 330, 'good': 600})
        finally:
            self.quiet.start()
        for row in feedback.cards(self.db):
            kinds[row['kind']] = kinds.get(row['kind'], 0) + 1
        self.assertEqual(shares, (0.10, 0.05))
        self.assertAlmostEqual(kinds.get('probe', 0) / 400, shares[0], delta=0.04)
        self.assertAlmostEqual(kinds.get('fake', 0) / 400, shares[1], delta=0.03)
        made_up = [r for r in feedback.cards(self.db) if r['kind'] == 'fake']
        self.grade({'id': made_up[0]['id'], 'ja': made_up[0]['word']}, made_up[0]['served_at'], srs.GOOD)
        self.assertEqual(self.deck(), {})  # graded, but no schedule


class Summary(Deck):
    """fw.py review: the schedule's numbers and the review log in the FSRS optimizer's layout."""

    def test_the_numbers_and_the_review_log(self):
        a, b = self.words[:2]
        for ja in (a, b):
            card, _ = self.next(self.T0 - 20 * DAY)
            self.grade(card, self.T0 - 20 * DAY, srs.EASY)
        for at, grade in ((self.T0 - HOUR, srs.GOOD), (self.T0 - HOUR + 60_000, srs.AGAIN)):  # both due: one kept,
            card, _ = self.next(at)                                                          # one lapsed
            self.assertEqual(card['queue'], 'review')
            self.grade(card, at, grade)
        found = review.summary(self.db, self.T0)
        self.assertEqual(found['retention'], {'month': [1, 2], 'all': [1, 2]})
        self.assertEqual((found['today'], found['month'], found['graded'], found['deck']), (2, 4, 4, 2))
        self.assertEqual(found['counts'], {'new': review.NEW_PER_DAY, 'learning': 1, 'review': 0})
        text = review.summary_text(found)
        self.assertIn('50% of 2 in the last 30 days', text)
        self.assertIn('on the order of 1,000 graded reviews', text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fw.main(['review', 'revlog'])
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], 'card_id,review_time,review_rating,review_state,review_duration')
        rows = [line.split(',') for line in lines[1:]]
        self.assertEqual([(int(r[2]), int(r[3])) for r in rows], [(4, 0), (4, 0), (3, 2), (1, 2)])
        self.assertEqual({int(r[0]) for r in rows}, {srs.card_id(a), srs.card_id(b)})
        self.assertEqual(int(rows[0][1]), self.T0 - 20 * DAY + 1100)  # graded 1.1 s after it was shown
        self.assertEqual(rows[0][4], '1100')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fw.main(['review'])  # no collector here: computed on this state dir
        printed = out.getvalue()  # at the real clock, months after these reviews: only what doesn't depend on it
        self.assertTrue(printed.startswith(f'Due today: {review.NEW_PER_DAY} new, 1 learning, '))
        self.assertIn('2 words have a schedule', printed)
        self.assertIn('FSRS reviews (graded learning cards): 4.', printed)


def simulate_stream(seed, days=30, per_day=20, alike=False, rates=(0.9, 0.7, 0.5, 0.3, 0.15, 0.05), graded=False):
    """A simulated reader's review cards over `days` days: per_day cards a day, 70% learning, 20% probe and 10% made
    up (the wedge's mix), each probe word known or not once for all (by its band's rate), with outcomes as outcome()
    draws them (alike: the reader gives up on unknown words as fast as they reveal known ones). graded: the review
    panel instead, every card revealed and graded (a probe card Good when its word is known, else Again). Returns
    (reviews, the true goal: the weighted share of the population known, now)."""
    rng = random.Random(seed)
    knows = {}
    for h, band in enumerate(FRAME.bands):
        for it in band:
            knows[it['ja']] = rng.random() < rates[h]
    truth = sum(share * sum(it['weight'] for it in band if knows[it['ja']]) / sum(it['weight'] for it in band)
                for share, band in zip(FRAME.shares, FRAME.bands))
    kinds = dict(u_ms=1000, u_sd=0.45, u_skip=0.25) if alike else {}
    now, out = 1_800_000_000_000, []
    for i in range(days * per_day):
        at = now - days * DAY + i * (DAY // per_day)
        kind = rng.choices(['learning', 'probe', 'fake'], [0.7, 0.2, 0.1])[0]
        if kind == 'probe':
            h = rng.choices(range(len(FRAME.bands)), FRAME.shares)[0]
            it = rng.choice(FRAME.bands[h])
            reveal, shown = outcome(rng, knows[it['ja']], **kinds)
            out.append({'card': f'c{i}', 'at': at, 'word': it['ja'], 'kind': 'probe', 'band': h,
                        'weight': it['weight'], 'reveal': reveal, 'shown': shown,
                        'grade': (3 if knows[it['ja']] else 1) if graded else None})
        else:
            reveal, shown = outcome(rng, kind == 'learning' and rng.random() < 0.4, **kinds)
            out.append({'card': f'c{i}', 'at': at, 'word': f'w{i}', 'kind': kind, 'reveal': reveal, 'shown': shown,
                        'grade': 1 if graded else None})
        if graded:
            out[-1].update(source='desktop', reveal=out[-1]['reveal'] or 800, shown=max(out[-1]['shown'], 2000),
                           grade_ms=1500)
    return out, truth, now


class Rolling(unittest.TestCase):
    """probe.rolling, the probe's estimate from the review stream, on a simulated reader."""

    def test_close_to_the_truth_with_its_interval_covering_it(self):
        """20 simulated readers of about 120 probe cards each: on 2026-11-02 the error was +0.024 on average with a
        root mean square of 0.070, the standard error about 0.08, and the 90% interval covered the truth 17 times."""
        covered, errors = 0, []
        for seed in range(20):
            reviews, truth, now = simulate_stream(seed)
            est = probe.rolling(reviews, now, FRAME)
            errors.append(est['known'] - truth)
            covered += est['low'] <= truth <= est['high']
            self.assertFalse(est['separation']['weak'])
            self.assertGreater(est['n'], 80)
        self.assertGreaterEqual(covered, 15)
        self.assertLess(abs(sum(errors) / len(errors)), 0.05)
        self.assertLess(math.sqrt(sum(e * e for e in errors) / len(errors)), 0.1)

    def test_a_graded_probe_card_is_a_sure_answer_and_one_closed_unrevealed_says_nothing(self):
        """The review panel's stream: the estimate from the probe cards' grades, close to the truth; cards closed
        without a reveal or a grade (here every unknown word's, once more) don't move it, where read as reveal times
        they would look known."""
        reviews, truth, now = simulate_stream(7, graded=True)
        est = probe.rolling(reviews, now, FRAME)
        self.assertAlmostEqual(est['known'], truth, delta=0.1)
        self.assertEqual((est['fakes'], est['learning']), (0, 0))  # out of the components: they claim nothing
        closed = [dict(r, card=r['card'] + 'x', at=r['at'] + 1, reveal=None, shown=3000, grade=None, grade_ms=None)
                  for r in reviews if r['kind'] == 'probe' and r['grade'] == 1]
        again = probe.rolling(reviews + closed, now, FRAME)
        self.assertAlmostEqual(again['known'], est['known'], delta=0.03)  # each word's latest card: the graded one
        self.assertLessEqual(again['n'], est['n'])

    def test_overlapping_components_widen_the_interval_and_say_so(self):
        reviews, _truth, now = simulate_stream(1)
        clear = probe.rolling(reviews, now, FRAME)
        reviews, _truth, now = simulate_stream(1, alike=True)
        alike = probe.rolling(reviews, now, FRAME)
        self.assertTrue(alike['separation']['weak'])
        self.assertGreater(alike['high'] - alike['low'], 1.5 * (clear['high'] - clear['low']))
        self.assertIn('separate weakly', probe.rolling_text({'review': {'probe': alike}}))

    def test_the_window_and_each_words_latest_card(self):
        reviews, _truth, now = simulate_stream(2, days=60)
        est = probe.rolling(reviews, now, FRAME)
        recent = [r for r in reviews if r['at'] >= now - probe.ROLLING_DAYS * DAY]
        words = {r['word'] for r in recent if r['kind'] == 'probe'
                 and knowledge.review_outcome(r['reveal'], r['shown']) is not False}
        self.assertEqual(est['n'], len(words))
        self.assertEqual(est['fakes'], sum(1 for r in recent if r['kind'] == 'fake'
                                           and knowledge.review_outcome(r['reveal'], r['shown']) is not False))
        self.assertIsNone(probe.rolling([r for r in reviews if r['kind'] != 'probe'], now, FRAME))
        self.assertIsNone(probe.rolling(reviews, now - 61 * DAY, FRAME))

    def test_status_says_the_estimate_and_what_it_rests_on(self):
        reviews, _truth, now = simulate_stream(3)
        est = probe.rolling(reviews, now, FRAME)
        line = probe.status_line({'review': {'probe': est}})
        self.assertIn(f'from {est["n"]} probe cards and {est["fakes"]} made-up ones', line)
        self.assertIn('separate well', line)
        self.assertIn('no probe cards reviewed', probe.status_line({}))


if __name__ == '__main__':
    unittest.main()
