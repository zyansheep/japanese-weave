"""Offline tests for the review cards (scripts/review.py, the review wedge): the collector's POST /v1/review/next (the
card's shape, exclude, words the delayed-start experiment hides never served, nothing to review), review items in
POST /v1/interactions (stored once on their card, malformed ones rejected, the mirror gets them), the card choice
(the mix, learning words behind first, made-up words reused least recently), the mixture of reveal times in the fit
(two clear components separate, overlapping ones report a weak separation, made-up words pin the "not known" side),
a review as a read (the recency clock, the experiment's exposure) and the probe's rolling estimate on a simulated
reader. Run: python3 -m unittest tests/test_review.py"""
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
import dictionary  # noqa: E402
import evaluate  # noqa: E402
import experiment  # noqa: E402
import feedback  # noqa: E402
import fwlib  # noqa: E402
import knowledge  # noqa: E402
import probe  # noqa: E402
import review  # noqa: E402

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


def reviewed(card, at, reveal, shown=None, uid=None, source='desktop', word=None):
    """A review item as the T3 Code fork sends it."""
    item = {'kind': 'review', 'at': at, 'card': card, 'word': word, 'revealMs': reveal,
            'shownMs': shown if shown is not None else (reveal or 0) + 900, 'source': source}
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
        self.assertEqual(set(card), {'id', 'ja', 'kana', 'gloss'})
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
                     {'source': 'desktop', 'exclude': ['a b']}, {'source': 'desktop', 'exclude': ['x'] * 21}, []):
            status, reply = self.post('/v1/review/next', body)
            self.assertEqual((status, reply['ok']), (400, False), body)
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop', 'exclude': ['x'] * 20})[0], 200)
        (fwlib.home() / 'state.json').rename(fwlib.home() / 'elsewhere.json')
        status, reply = self.post('/v1/review/next', {'source': 'desktop'})
        self.assertEqual((status, reply['ok']), (409, False))

    def test_paused_means_nothing_to_review(self):
        state = fwlib.load_state()
        state['paused'] = True
        fwlib.save_state(state)
        self.assertEqual(self.post('/v1/review/next', {'source': 'desktop'}), (200, {'ok': True, 'card': None}))
        self.assertEqual(feedback.cards(self.db), [])

    def test_no_word_to_offer_means_nothing_to_review(self):
        state = fwlib.load_state()
        for w in state['words']:
            if w['ja'] in self.active:
                w['status'] = 'known'
        fwlib.save_state(state)
        with mock.patch.object(dictionary, 'PATH', self.home / 'no-dictionary.tsv'):
            for _ in range(20):  # a made-up word alone is never a card
                self.assertEqual(self.post('/v1/review/next', {'source': 'desktop'})[1], {'ok': True, 'card': None})

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
        with mock.patch.object(review, 'MIX', (('learning', 0.4), ('probe', 0.5), ('fake', 0.1))):
            for i in range(300):
                card = review.next_card('desktop', (), self.db, now - (300 - i) * 5 * 60_000, rng)
                self.assertNotIn(fwlib.key(card['ja']), hidden)
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
            reviewed(card['id'], now - 2000, 800, uid='r1', word=card['ja'])]})
        self.assertEqual(stored[1], {'ok': True, 'stored': 1, 'duplicates': 0, 'rejected': 0})
        old = feedback.export(0, 0, 10, self.db)
        self.assertNotIn('cards', old)  # a mirror from before cards asks for none
        mirror = self.home / 'mirror.sqlite'
        self.assertEqual(feedback.sync_mirror(self.url, mirror), (True, None))
        [row] = feedback.reviews(mirror)
        self.assertEqual((row['card'], row['word'], row['reveal'], row['source']), (card['id'], card['ja'], 800,
                                                                                    'desktop'))
        self.assertEqual(feedback.stats(0, self.db)['counts']['t3:review'], [1, 1])


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
               dict(good, word=''), dict(good, at=self.NOW - 8 * DAY), dict(good, at=self.NOW + HOUR),
               {k: v for k, v in good.items() if k != 'card'}, {k: v for k, v in good.items() if k != 'shownMs'}]
        self.assertEqual(self.record(*bad), {'stored': 0, 'duplicates': 0, 'rejected': len(bad)})
        self.assertEqual(self.record(good, surface='browser'), {'stored': 0, 'duplicates': 0, 'rejected': 1})
        self.assertEqual(feedback.reviews(self.db), [])
        self.assertEqual(self.record(good, dict(good, revealMs=None)), {'stored': 1, 'duplicates': 1, 'rejected': 0})
        self.assertEqual(feedback.count(self.db), 0)  # no gloss interaction

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
    """review.choose, the card choice, on a word state alone."""
    NOW = int(time.time() * 1000)

    def state(self, n=8):
        today = knowledge.day_of(self.NOW)
        state = fresh_state(today)
        self.words = queue_words(state, n)
        return learning(state, self.words, fwlib.add_days(today, -30))

    def test_the_mix_is_about_seven_two_one(self):
        state, rng, counts = self.state(), random.Random(1), {}
        for _ in range(3000):
            card = review.choose(state, {}, [], (), self.NOW, rng, FRAME)
            counts[card['kind']] = counts.get(card['kind'], 0) + 1
        self.assertAlmostEqual(counts['learning'] / 3000, 0.7, delta=0.03)
        self.assertAlmostEqual(counts['probe'] / 3000, 0.2, delta=0.03)
        self.assertAlmostEqual(counts['fake'] / 3000, 0.1, delta=0.02)

    def test_behind_first_then_the_least_certain_and_never_a_recent_or_excluded_word(self):
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
        served = []
        with mock.patch.object(review, 'MIX', (('learning', 1.0), ('probe', 0.0), ('fake', 0.0))):
            for i in range(4):
                card = review.choose(state, know, served, (), self.NOW, random.Random(i), FRAME)
                served.append({'id': f'x{i}', 'served_at': self.NOW - i * HOUR, 'word': card['word'],
                               'kind': card['kind'], 'at': None})
            order = [s['word'] for s in served]
            self.assertEqual(order, [a, c, b, d])  # a is behind (0 showings in 3 days); b had 4, then by sd
            card = review.choose(state, know, served, (), self.NOW, random.Random(9), FRAME)
            self.assertEqual(card['kind'], 'probe')  # every word recent: the other real kind
            served[0]['served_at'] = served[1]['served_at'] = self.NOW - 5 * HOUR  # a and c no longer recent
            self.assertEqual(review.choose(state, know, served, (), self.NOW, random.Random(9), FRAME)['word'], a)
            self.assertEqual(review.choose(state, know, served, ('x0',), self.NOW, random.Random(9), FRAME)['word'],
                             c)  # a's card is held by the client

    def test_a_review_counts_as_a_showing_while_behind(self):
        state = self.state(1)
        [a] = self.words
        started = self.NOW - 2 * DAY
        for w in state['words']:
            if w['ja'] == a:
                w['arm'] = {'arm': 'shown', 'draw': 0.9, 'day': knowledge.day_of(started), 'at': started,
                            'probe': fwlib.add_days(knowledge.day_of(started), 21)}
        served = [{'id': f'r{i}', 'served_at': self.NOW - 6 * HOUR, 'word': a, 'kind': 'learning',
                   'at': self.NOW - 6 * HOUR} for i in range(3)]
        rng = random.Random(1)
        behind = review._learning(state, {}, [], set(), knowledge.day_of(self.NOW), self.NOW, rng)
        self.assertEqual(behind['word'], a)
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


def simulate_stream(seed, days=30, per_day=20, alike=False, rates=(0.9, 0.7, 0.5, 0.3, 0.15, 0.05)):
    """A simulated reader's review cards over `days` days: per_day cards a day, 70% learning, 20% probe and 10% made
    up (review.MIX), each probe word known or not once for all (by its band's rate), with outcomes as outcome()
    draws them (alike: the reader gives up on unknown words as fast as they reveal known ones). Returns (reviews, the
    true goal: the weighted share of the population known, now)."""
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
                        'weight': it['weight'], 'reveal': reveal, 'shown': shown})
        else:
            reveal, shown = outcome(rng, kind == 'learning' and rng.random() < 0.4, **kinds)
            out.append({'card': f'c{i}', 'at': at, 'word': f'w{i}', 'kind': kind, 'reveal': reveal, 'shown': shown})
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
