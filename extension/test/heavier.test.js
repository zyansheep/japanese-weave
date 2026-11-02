import { describe, test, expect } from 'bun:test';
import { readFileSync } from 'node:fs';
import * as pipeline from '../src/pipeline.js';
import * as verify from '../src/verify.js';
import * as levels from '../src/levels.js';
import { hashKey } from '../src/hash.js';
import { refreshDictionary, dictionaryOf, cleanInteraction, parseState } from '../src/hostsource.js';
import { digests } from './levelsgolden.js';
import { passagesOf, checkAnswer, modelReply, userOf } from './checkmodel.js';
import { trackTimers } from './timers.js';

const { pipelineFor, planText, heavierPlan, dictionaryWordsOf, clearOfSentences, answerKey } = pipeline;
const { candidatesOf, denseSentences } = verify;
const { planSwaps, intensityBudget, INTENSITY_DICTIONARY_SHARE } = levels;

// Words by romaji, written in escapes: inu dog, neko cat, kyou today, samui cold, kaze wind, niwa garden.
const INU = '\u72ac', NEKO = '\u732b', KYOU = '\u4eca\u65e5', SAMUI = '\u5bd2\u3044', KAZE = '\u98a8', NIWA = '\u5ead';
const MARU = '\u3002', DESU = '\u3067\u3059';
const listed = (ja, en, k, known = false) => ({ ja, en, hint: 'x', kana: '', status: known ? 'known' : 'active', glosses: [en], safe: [en], k, sd: 0, free: known });
const row = (id, ja, en, k = 1.5) => [id, ja, '', 'x', en, k, 0];

describe('normal and the lighter levels are as they were', () => {
  test('every plan, swap, model prompt and answer key on the lab passages matches the code before the change', () => {
    const before = JSON.parse(readFileSync(new URL('./fixtures/normal-levels.json', import.meta.url), 'utf8'));
    const now = digests({ ...pipeline, ...verify, ...levels, hashKey });
    expect(Object.keys(before).length).toBe(150);
    expect(now).toEqual(before);
  });
  test('dictionary words and sentences-first never reach them, whatever they are handed', () => {
    const text = 'The dog ran and the garden was quiet in the morning sun for a while.';
    const words = [listed(INU, 'dog', 1.1), { ...listed(NIWA, 'garden', 1.1), dictionary: true }];
    const swaps = [{ o: 'dog', f: INU, g: 'dog', h: '', ctx: '', word: INU }, { o: 'garden', f: NIWA, g: 'garden', h: '', ctx: '', word: NIWA }];
    for (const intensity of [-2, -1, 0]) {
      for (let seed = 0; seed < 50; seed++) {
        const { swaps: kept, plan } = planSwaps(2, swaps, text, words, { intensity, seed, budgetPer100: 100 });
        expect(kept.map((s) => s.f)).toEqual([INU]);
        expect(plan.dictionary).toBeUndefined();
      }
    }
  });
});

describe('+1 and +2 in the planner', () => {
  const sum = (xs) => xs.reduce((a, b) => a + b, 0);
  test('no thinning above normal: every free word stays, as the plan says; normal still thins', () => {
    const text = 'The dog and the cat sat near the old door for most of a long afternoon.';
    const words = [listed(INU, 'dog', 4, true), listed(NEKO, 'cat', 4, true)];
    const swaps = [{ o: 'dog', f: INU, g: 'dog', h: '', ctx: '', word: INU }, { o: 'cat', f: NEKO, g: 'cat', h: '', ctx: '', word: NEKO }];
    const count = (intensity) => sum(Array.from({ length: 200 }, (_, seed) => planSwaps(2, swaps, text, words, { intensity, seed, jitter: 0.2, budgetPer100: 1 }).swaps.length));
    expect(count(1)).toBe(400);
    expect(count(2)).toBe(400);
    expect(count(0)).toBeLessThan(150);
    expect(planSwaps(2, swaps, text, words, { intensity: 1, jitter: 0.2, budgetPer100: 1 }).plan.jitter).toBe(1);
  });
  // A learning word nearer the target than the sentence's price, in the sentence, in a passage that pays for one
  const text = 'It is cold today. Then we took the long road home past the old mill and the river in the evening light with friends.';
  const word = { o: 'today', f: KYOU, g: 'today', h: '', ctx: 'cold today', word: KYOU, status: 'active' };
  const sentence = { kind: 'sentence', o: 'It is cold today.', f: `${KYOU}${SAMUI}${DESU}${MARU}`, g: 'It is cold today.', ctx: 'It is cold today.',
    parts: [{ t: KYOU, word: KYOU }, { t: SAMUI, word: SAMUI }, { t: DESU }, { t: MARU }], words: [KYOU, SAMUI], word: KYOU };
  const words = [listed(KYOU, 'today', Math.log(3)), listed(SAMUI, 'cold', 4, true)];
  const shown = (intensity) => sum(Array.from({ length: 200 }, (_, seed) => planSwaps(2, [sentence, word], text, words, { intensity, seed,
    budgetPer100: intensityBudget(intensity, 0.5), sentencePrice: 0.1 }).swaps.filter((s) => s.kind === 'sentence').length));
  test('a sentence chooses before the words in it above normal, and more surely at +2 than at +1', () => {
    expect(shown(1)).toBeGreaterThan(shown(0));
    expect(shown(2)).toBeGreaterThan(shown(1));
  });
  // Twenty dictionary words in 120 words: more than any budget here pays for.
  const many = Array.from({ length: 20 }, (_, i) => String.fromCharCode(0x4e00 + i));
  const body = many.map((_, i) => `word${i} is here. And then there were some more of those over there, too.`).join(' ');
  const pool = many.map((ja, i) => ({ ...listed(ja, `word${i}`, 1.5), dictionary: true }));
  const offered = many.map((ja, i) => ({ o: `word${i}`, f: ja, g: `word${i}`, h: '', ctx: '', word: ja }));
  const run = (intensity, seed) => planSwaps(2, offered, body, pool, { intensity, seed, budgetPer100: intensityBudget(intensity, 0.5), sentencePrice: 0.1 });
  test('dictionary words never spend past the budget or their share of it, on average, and +2 takes more than +1', () => {
    const totals = {};
    for (const intensity of [1, 2]) {
      const plans = Array.from({ length: 200 }, (_, seed) => run(intensity, seed).plan);
      for (const plan of plans) expect(plan.spent).toBeLessThanOrEqual(plan.budget + 1e-9);
      const share = sum(plans.map((p) => p.spent)) / sum(plans.map((p) => p.budget));
      expect(share).toBeLessThanOrEqual(INTENSITY_DICTIONARY_SHARE[intensity] + 0.03);
      expect(share).toBeGreaterThan(INTENSITY_DICTIONARY_SHARE[intensity] - 0.15);
      totals[intensity] = sum(plans.map((p) => p.dictionary || 0));
    }
    expect(totals[2]).toBeGreaterThan(totals[1] * 1.5);
  });
  test("dictionary words take what the reader's own words leave: never a place or budget of theirs", () => {
    const own = listed(INU, 'dog', 1.1);
    const passage = `The dog word0 word1 word2 word3 ${'and so on '.repeat(6)}`;
    const swaps = [{ o: 'dog', f: INU, g: 'dog', h: '', ctx: '', word: INU, status: 'active' }, ...offered.slice(0, 4)];
    for (let seed = 0; seed < 100; seed++) {
      const { swaps: kept } = planSwaps(2, swaps, passage, [own, ...pool], { intensity: 2, seed, budgetPer100: 2 });
      expect(kept.map((s) => s.f)).toContain(INU);
    }
  });
  test('+1 and +2 are visibly different on the lab passages, and within budget', () => {
    const reply = { words: levels.wordsForLevel(2).map((w, i) => ({ ...w, status: i < 100 ? 'known' : 'active', glosses: w.en.split(' / '), safe: w.en.split(' / ').slice(0, 1), k: i < 100 ? 3 : 1.5, sd: 0.8, free: i < 100 })) };
    const pipe = pipelineFor(reply.words);
    const dict = pipelineFor(dictionaryWordsOf(['time / place', 'way', 'work', 'story', 'book', 'night', 'home', 'city', 'car', 'game', 'school', 'water', 'house', 'room', 'family', 'music', 'money', 'road', 'river', 'light']
      .map((en, i) => row(9000 + i, String.fromCharCode(0x5000 + i), en.replace(' / ', ';'))), reply.words));
    const passages = JSON.parse(readFileSync(new URL('../../tools/prompt-lab/fresh2.json', import.meta.url), 'utf8'));
    const seen = {};
    for (const intensity of [1, 2]) {
      let dictionary = 0, spent = 0, budget = 0;
      for (const { text } of passages) {
        const plan = heavierPlan(pipe, dict, text);
        const list = [...reply.words, ...plan.added];
        for (let seed = 1; seed <= 10; seed++) {
          const out = planSwaps(2, candidatesOf(plan, null, list, 2), text, list, { intensity, seed, budgetPer100: intensityBudget(intensity, 0.5), sentencePrice: 0.11 });
          dictionary += out.plan.dictionary || 0; spent += out.plan.spent; budget += out.plan.budget;
        }
      }
      seen[intensity] = dictionary;
      expect(spent).toBeLessThanOrEqual(budget);
    }
    expect(seen[2]).toBeGreaterThan(seen[1] * 1.5);
  });
});

describe('the plan of a passage above normal', () => {
  const words = [listed(INU, 'dog', 4, true), listed(SAMUI, 'cold', 4, true), listed(KYOU, 'today', 4, true)];
  const pipe = pipelineFor(words);
  const dictionary = pipelineFor(dictionaryWordsOf([row(7, KAZE, 'wind'), row(8, NIWA, 'garden')], words));
  test('it adds spans for dictionary words where nothing overlaps, and asks the model nothing new without a new sentence', () => {
    const text = 'I saw the dog in the garden after the rain stopped for a while.';
    const plain = planText(pipe, text);
    const plan = heavierPlan(pipe, dictionary, text);
    expect(plan.spans.map((m) => m.span.english)).toEqual(['dog', 'garden']);
    expect(plan.spans.map((m) => m.n)).toEqual([1, 2]);
    expect(plan.added.map((w) => w.ja)).toEqual([NIWA]);
    expect(plan.ask).toEqual(plain); // the same prompt and cached answer as at normal
    expect(answerKey(plan.ask)).toBe(answerKey(plain));
    expect(answerKey(plan)).toBe(answerKey(plain));
    expect(candidatesOf(plan, null, [...words, ...plan.added], 2).map((c) => c.f)).toEqual([INU, NIWA]);
  });
  test('a sentence only a dictionary word completes, or a last clause, is asked for by its own answer', () => {
    const text = 'The cold wind is here today.';
    expect(planText(pipe, text).sentences).toHaveLength(0);
    const plan = heavierPlan(pipe, dictionary, text);
    expect(plan.sentences).toHaveLength(0); // "here" is nobody's
    const cold = 'After the long and tiring week at the office, it is cold today.';
    const clause = heavierPlan(pipe, dictionary, cold);
    expect(planText(pipe, cold).sentences).toHaveLength(0);
    expect(clause.sentences.map((s) => s.text)).toEqual(['it is cold today.']);
    expect(clause.ask.sentences).toHaveLength(1);
    expect(answerKey(clause)).not.toBe(answerKey(planText(pipe, cold)));
    expect(denseSentences(cold, clause.spans.map((m) => m.span))).toHaveLength(0); // without clauses: as at normal
  });
  test('a sentence stays checked word by word: an unlisted word, or a dictionary word its passage offers no span for, drops it', () => {
    const text = 'After the long and tiring week at the office, it is cold today.';
    const plan = heavierPlan(pipe, dictionary, text);
    const list = [...words, ...plan.added];
    const answer = (p) => ({ picks: new Map(), swaps: [{ at: 's1', p }] });
    expect(candidatesOf(plan, answer(`${KYOU}|\u306f|${SAMUI}|${DESU}|${MARU}`), list, 2).filter((c) => c.kind === 'sentence')).toHaveLength(1);
    expect(candidatesOf(plan, answer(`${KYOU}|\u306f|${KAZE}|${DESU}|${MARU}`), list, 2).filter((c) => c.kind === 'sentence')).toHaveLength(0);
  });
  test('the sure painting keeps out of a sentence it asks for, so the answer can still show it', () => {
    const text = 'I saw the dog today, and it is cold today.';
    const plan = heavierPlan(pipe, dictionary, text);
    const sure = candidatesOf(plan, null, words, 2);
    const clear = clearOfSentences(plan, sure);
    expect(plan.sentences).toHaveLength(1);
    expect(sure.length).toBeGreaterThan(clear.length);
    expect(clear.map((c) => c.f)).toContain(INU);
  });
  test('a list word never comes in as a dictionary word, nor a row out of shape', () => {
    expect(dictionaryWordsOf([row(7, INU, 'dog'), row(8, NIWA, 'garden'), [1, 2]], words).map((w) => w.ja)).toEqual([NIWA]);
    expect(dictionaryWordsOf([row(8, NIWA, 'garden')], words)[0]).toMatchObject({ dictionary: true, free: false, safe: ['garden'] });
  });
});

describe('host messages', () => {
  const box = (values = {}) => ({ data: values, async get() { return this.data; }, async set(v) { Object.assign(this.data, v); } });
  test('the dictionary words are asked for once a version, and a stale version is not used', async () => {
    const storage = box();
    const asked = [];
    const send = async (m) => (asked.push(m), { ok: true, version: 'v1', entries: [row(8, NIWA, 'garden'), ['bad'], row(0, KAZE, 'wind')] });
    expect(await refreshDictionary({ storage, send, version: 'v1' })).toEqual({ changed: true, count: 1 });
    expect(await refreshDictionary({ storage, send, version: 'v1' })).toEqual({ changed: false });
    expect(asked).toEqual([{ type: 'dictionaryWords' }]);
    expect((await dictionaryOf(storage, { dictionary: 'v1' })).entries).toHaveLength(1);
    expect(await dictionaryOf(storage, { dictionary: 'v2' })).toBeNull(); // a word just held back must not come in with the old list
    expect(await dictionaryOf(storage, { dictionary: '' })).toBeNull();
    const old = await refreshDictionary({ storage: box(), send: async () => ({ ok: false, error: 'unknown message type' }), version: 'v9' });
    expect(old.changed).toBe(false);
    expect(parseState({ words: [{ ja: INU, en: 'dog' }], dictionary: { version: 'v1' } }).dictionary).toBe('v1');
    expect(parseState({ words: [{ ja: INU, en: 'dog' }] }).dictionary).toBe('');
  });
  test('a plan record says which painting it is, and how many dictionary words it kept', () => {
    const plan = { level: 2, intensity: 1.8, jitter: 1, limit: 2, candidates: 3, budget: 0.3, spent: 0.1, expected: 0.2 };
    const shown = (p) => cleanInteraction({ kind: 'shown', view: 'v', context: 'c', at: 1000, box: [0, 0, 1, 1], annotations: [], words: 10, plan: p }, 1000);
    expect(shown({ ...plan, paint: 'sure', dictionary: 1 }).plan).toMatchObject({ paint: 'sure', dictionary: 1 });
    expect(shown({ ...plan, paint: 'final' }).plan.paint).toBe('final');
    expect(shown({ ...plan, paint: 'other' })).toBeNull();
    expect(shown({ ...plan, dictionary: -1 })).toBeNull();
  });
});

// The worker: plans say first painting or final, dictionary words come in at +1 and +2 only, and stepping up asks the
// model only for a passage with a sentence normal doesn't ask for.
test('background: paint, dictionary words and calls by level', async () => {
  const saved = Object.fromEntries(['chrome', 'fetch'].map((k) => [k, globalThis[k]]));
  const stopTimers = trackTimers();
  const until = Date.now() + 3600000;
  const local = { settings: { enabled: true, levelOverride: 2 }, apiKey: 'synthetic-gemini', intensity: { level: 0, until: 0, at: 1 } };
  const listeners = [];
  const calls = [];
  const natives = [];
  const area = {
    async setAccessLevel() {},
    async get(keys) { return structuredClone(keys == null ? local : Object.fromEntries((typeof keys === 'string' ? [keys] : keys).filter((k) => k in local).map((k) => [k, local[k]]))); },
    async set(values) { for (const [k, v] of Object.entries(values)) local[k] = structuredClone(v); },
    async remove(keys) { for (const k of keys) delete local[k]; },
  };
  const listener = (arr) => ({ addListener: (fn) => arr.push(fn) });
  const words = [{ ja: '\u3067\u3082', en: 'but', hint: 'demo', kana: '\u3067\u3082', status: 'known', glosses: ['but'], safe: [], k: 4, sd: 0, free: true },
    { ja: INU, en: 'dog', hint: 'inu', kana: '', status: 'known', glosses: ['dog'], safe: ['dog'], k: 4, sd: 0, free: true }];
  Object.assign(globalThis, {
    chrome: {
      storage: { local: area },
      runtime: {
        id: 'fw-test', getURL: (path) => `chrome-extension://fw-test/${path}`,
        onMessage: listener(listeners), onInstalled: listener([]), onStartup: listener([]),
        sendNativeMessage: async (_host, msg) => (natives.push(msg), msg.type === 'state'
          ? { ok: true, version: 'h.1', stage: 3, paused: false, prefs: {}, words, anthropicKey: 'synthetic-anthropic', dictionary: { version: 'd1' } }
          : msg.type === 'dictionaryWords' ? { ok: true, version: 'd1', entries: [[8, NIWA, '', 'niwa', 'garden', 1.1, 0]] } : { ok: true }),
      },
      tabs: { query: async () => [], sendMessage: async () => {} },
      alarms: { create: async () => {}, clear: async () => {}, onAlarm: listener([]) },
      action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {} },
      permissions: { getAll: async () => ({ origins: ['<all_urls>'] }), onAdded: listener([]), onRemoved: listener([]) },
      scripting: { getRegisteredContentScripts: async () => [], updateContentScripts: async () => {}, unregisterContentScripts: async () => {}, registerContentScripts: async () => {} },
    },
    fetch: async (url, init) => {
      const user = userOf(url, init);
      calls.push(passagesOf(user).map((p) => p.id));
      return modelReply(url, checkAnswer(user));
    },
  });
  const dispatch = (msg, sender) => new Promise((done) => {
    let handled = false;
    for (const fn of listeners) if (fn(msg, sender, done)) handled = true;
    if (!handled) done(undefined);
  });
  const tab = { id: 'fw-test', tab: { id: 1 }, url: 'https://example.com/1' };
  const popup = { id: 'fw-test', url: 'chrome-extension://fw-test/src/popup.html' };
  const text = 'I saw the dog in the garden, but it says very little to us about it all.';
  try {
    await import('../src/background.js?heavier');
    expect((await dispatch({ type: 'syncWords' }, popup)).ok).toBe(true);
    expect(natives.filter((m) => m.type === 'dictionaryWords')).toHaveLength(1);
    const ask = async (wait) => {
      const { revision } = await dispatch({ type: 'config' }, tab);
      const key = `${revision}:${hashKey(text)}`;
      const r = await dispatch({ type: 'swaps', revision, items: [{ key, text }], wait }, tab);
      return { swaps: r.results[key], plan: r.plans[key], waiting: r.waiting };
    };
    const first = await ask(false);
    expect(first.waiting).toHaveLength(1);
    expect(first.plan.paint).toBe('sure');
    expect(first.swaps.map((s) => s.f)).not.toContain(NIWA);
    const final = await ask(true);
    expect(final.plan.paint).toBe('final');
    expect(final.swaps.map((s) => s.f)).not.toContain(NIWA);
    expect(calls).toHaveLength(1);
    local.intensity = { level: 2, until, at: 2 };
    const heavier = await ask(false); // the same answer, no call: the dictionary word is new but asks the model nothing
    expect(heavier.waiting).toBeUndefined();
    expect(heavier.plan.paint).toBe('final');
    expect(calls).toHaveLength(1);
    expect(heavier.plan.jitter).toBe(1);
    expect(heavier.plan.candidates).toBe(final.plan.candidates + 1); // the dictionary word, without a call
    expect(heavier.plan.level).toBe(2);
    const peek = (word) => dispatch({ type: 'interaction', kind: 'peek', word, at: Date.now(), dwellMs: 900, view: 'v', uid: `v-${word.charCodeAt(0)}`, context: 'c', position: 0, box: [0, 0, 1, 1], annotations: [] }, tab);
    expect((await peek(NIWA)).ok).toBe(true); // a dictionary word's lookup is logged
    expect((await peek(KAZE)).ok).toBe(false);
  } finally {
    stopTimers();
    Object.assign(globalThis, saved);
  }
});
