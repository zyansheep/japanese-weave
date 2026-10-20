import { describe, test, expect } from 'bun:test';
import {
  resolveLevel, L1_WORDS, L2_WORDS, validateSwaps, isGrammar, wordCount, isZeroJapanese, normalizeJa, matchCase, capSwaps, selectSwaps,
  numberTrap, planSwaps, jitterFor, JITTER_MIN, misplaced, intensityLabel, intensityFactor, INTENSITY_LEVELS, INTENSITY_NAMES,
  answerLevel, INTENSITY_FULL, intensityBudget, perSentence, FREE_P, KNOWN_PRIOR_K,
} from '../src/levels.js';
import { STARTER } from '../src/starter.js';

describe('level', () => {
  test('follows the synced stage, else 1; override wins; capped at the built level', () => {
    expect(resolveLevel({})).toEqual({ stage: 0, chosen: 1, effective: 1 });
    expect(resolveLevel({}, 2)).toEqual({ stage: 2, chosen: 2, effective: 2 });
    expect(resolveLevel({ levelOverride: 1 }, 2)).toEqual({ stage: 2, chosen: 1, effective: 1 });
    expect(resolveLevel({ levelOverride: 4 }, 1)).toEqual({ stage: 1, chosen: 4, effective: 2 });
    expect(resolveLevel({ levelOverride: 9 }, 7)).toEqual({ stage: 0, chosen: 1, effective: 1 });
  });
});

describe('word lists', () => {
  test('L1 is the first 40 words of the bundled list, L2 the first 150', () => {
    expect(L1_WORDS.map((w) => w.ja)).toEqual(STARTER.slice(0, 40).map((r) => r[0]));
    expect(L2_WORDS.length).toBe(150);
    expect(L1_WORDS.slice(0, 2).map((w) => w.ja)).toEqual(['でも', 'もう']);
  });
  test('every entry has a gloss, romaji and kana, and no duplicates', () => {
    const jas = L2_WORDS.map((w) => w.ja);
    expect(new Set(jas).size).toBe(jas.length);
    for (const w of L2_WORDS) {
      expect(w.en.length).toBeGreaterThan(0);
      expect(w.hint).toMatch(/^[a-zāīūēō' ]+$/);
      expect(w.kana).toMatch(/^[ぁ-ゟ゠-ヿ]+$/);
    }
  });
});

describe('validateSwaps', () => {
  test('keeps list words, fills gloss, romaji and kana from the list', () => {
    const out = validateSwaps(1, [{ o: 'now', f: '今', g: 'x', h: 'y', ctx: 'works now' }]);
    expect(out).toEqual([{ o: 'now', f: '今', g: 'now', h: 'ima', k: 'いま', ctx: 'works now' }]);
  });
  test('drops words off the level list and malformed entries', () => {
    const l2only = L2_WORDS[100].ja;
    expect(validateSwaps(1, [{ o: 'x', f: l2only }])).toEqual([]);
    expect(validateSwaps(2, [{ o: 'x', f: l2only }])[0].f).toBe(l2only);
    expect(validateSwaps(1, [{ o: 'but' }, null, { f: 'でも' }, { o: '  ', f: 'でも' }])).toEqual([]);
    expect(validateSwaps(1, 'nope')).toEqual([]);
  });
  test('accepts the reading or katakana and shows the listed kanji', () => {
    expect(validateSwaps(1, [{ o: 'now', f: 'いま' }])[0].f).toBe('今');
    expect(validateSwaps(1, [{ o: 'problem', f: 'モンダイ' }])[0].f).toBe('問題');
    expect(validateSwaps(1, [{ o: 'but', f: ' でも ' }])[0].f).toBe('でも');
    expect(matchCase('でも', 'But')).toBe('でも');  // no letter case in Japanese
  });
  test('rejects blank replacements', () => {
    expect(validateSwaps(2, [{ o: 'and', f: '' }, { o: 'and', f: '  ' }])).toEqual([]);
  });
  test('plugin words replace the bundled list and keep the event spelling and status', () => {
    const words = [{ ja: '光', en: 'light', hint: 'hikari', kana: 'ひかり', status: 'active' }];
    expect(validateSwaps(1, [{ o: 'light', f: 'ひかり' }, { o: 'but', f: 'でも' }], words))
      .toEqual([{ o: 'light', f: '光', g: 'light', h: 'hikari', k: 'ひかり', ctx: '', word: '光', status: 'active' }]);
  });
  test('normalizeJa', () => {
    expect(normalizeJa(' タブン ')).toBe('たぶん');
    expect(normalizeJa('ﾀﾌﾞﾝ')).toBe('たぶん');
    expect(normalizeJa('多 分')).toBe('多分');
  });
});

describe('choosing swaps', () => {
  const text = 'It was a big day and a very long one, but fine.'; // 12 words, one sentence
  const words = [
    { ja: '大きい', en: 'big', hint: 'ōkii', status: 'active' },
    { ja: 'でも', en: 'but', hint: 'demo', status: 'known' },
    { ja: 'もう', en: 'already', hint: 'mō', status: 'known' },
    { ja: 'とても', en: 'very', hint: 'totemo', status: 'known' },
  ];
  const swaps = validateSwaps(1, [
    { o: 'but', f: 'でも', ctx: 'x but' }, { o: 'very', f: 'とても', ctx: 'very' }, { o: 'but', f: 'でも', ctx: 'y but' },
    { o: 'already', f: 'もう', ctx: 'already' }, { o: 'big', f: '大きい', ctx: 'a big' },
  ], words);
  const p = (k) => 1 / (1 + Math.exp(k));
  test('capSwaps: the level rule or the density, one use per word, learning words first', () => {
    expect(capSwaps(1, swaps, text, words).map((s) => s.f)).toEqual(['大きい']);
    expect(capSwaps(2, swaps, text, words).map((s) => s.f)).toEqual(['でも', '大きい']);
    expect(capSwaps(2, swaps, text, words, 25).map((s) => s.f)).toEqual(['でも', 'とても', '大きい']);
  });
  test('planSwaps: known words wherever offered at every level but off, a new active word first, the plan', () => {
    const plain = planSwaps(2, swaps, text, words);
    expect(plain.swaps.map((s) => s.f)).toEqual(['でも', 'とても', 'もう', '大きい']); // one use per word
    // budget: normal's 0.5 lookups per 100 words; expected: three known words without estimates and the active one
    expect(plain.plan).toEqual({ level: 0, intensity: 1, jitter: 1, limit: 4, candidates: 4, budget: 0.06,
      expected: Math.round((3 * p(KNOWN_PRIOR_K) + p(1)) * 1000) / 1000 });
    for (const intensity of [-2, -1, 1, 2]) {
      expect(planSwaps(2, swaps, text, words, { intensity }).swaps).toHaveLength(4); // lighter no longer thins known words
    }
    expect(planSwaps(2, swaps, text, words, { intensity: -2 }).plan).toMatchObject({ level: -2, intensity: 0.5, budget: 0.018 });
    // shown today, the active word is one more for the budget: 0.06 lookups against its 0.27
    const later = Array.from({ length: 1000 }, (_, seed) => planSwaps(2, swaps, text, words, { seed, shownToday: ['大きい'] }).swaps);
    expect(later.every((s) => ['でも', 'とても', 'もう'].every((f) => s.some((x) => x.f === f)))).toBe(true);
    expect(later.filter((s) => s.length === 4).length / later.length).toBeCloseTo(0.06 / p(1), 1);
    // the popup's density caps the total, budgeted swaps first: 25% of 12 words is 3
    expect(planSwaps(2, swaps, text, words, { density: 25 }).swaps.map((s) => s.f)).toEqual(['でも', 'とても', '大きい']);
    expect(planSwaps(2, swaps, text, words, { density: 25 }).plan.limit).toBe(3);
  });
  test('the random thinning keeps each known word with chance jitter, and scales the budget', () => {
    const one = swaps.slice(0, 1); // でも, known
    const kept = Array.from({ length: 2000 }, (_, seed) => planSwaps(2, one, text, words, { seed, jitter: 0.6 }).swaps.length);
    expect(kept.reduce((a, b) => a + b, 0) / kept.length).toBeCloseTo(0.6, 1);
    expect(planSwaps(2, one, text, words, { seed: 1, jitter: 0.6 }).plan).toMatchObject({ jitter: 0.6, budget: 0.036 });
    expect(planSwaps(2, one, text, words, { seed: 1, intensity: -2 }).swaps).toHaveLength(1); // jitter 1: no thinning
    const seeds = Array.from({ length: 2000 }, (_, i) => jitterFor(i * 7919));
    const thinned = seeds.filter((j) => j < 1);
    expect(thinned.length / seeds.length).toBeGreaterThan(0.45);
    expect(thinned.length / seeds.length).toBeLessThan(0.55);
    expect(Math.min(...thinned)).toBeGreaterThanOrEqual(JITTER_MIN);
    expect(jitterFor(42)).toBe(jitterFor(42)); // a passage repaints the same way
  });

  test('planSwaps at full: every candidate, sentences first, a word again where its English is again; off: none', () => {
    const listed = [...words, { ja: '日', en: 'day', hint: 'hi', status: 'known' }];
    const passage = 'It was a big day. It was a very long one, but fine, but late.';
    const [sentence] = validateSwaps(2, [{ k: 's', o: 'It was a big day.', f: '大きい日でした。', p: '大きい|日|でした|。',
      g: 'It was a big day.', c: 'It was a big day.' }], listed, passage);
    expect(sentence.kind).toBe('sentence');
    const offered = [...swaps, sentence, swaps[0]]; // the model's order, and one exact repeat
    const full = planSwaps(2, offered, passage, listed, { intensity: 3, density: 5, budgetPer100: 1, jitter: 0.5, seed: 3 });
    expect(full.swaps[0]).toBe(sentence); // placed before the word swaps inside it (text.js applySwaps)
    expect(full.swaps.slice(1)).toEqual(swaps); // both buts, in the model's order, the repeat dropped
    // no budget; expected: every listed word of every swap (でも twice, 大きい in the sentence and alone)
    expect(full.plan).toEqual({ level: 3, intensity: 5, jitter: 1, limit: 6, candidates: 6,
      expected: Math.round((5 * p(KNOWN_PRIOR_K) + 2 * p(1)) * 1000) / 1000 });
    expect(planSwaps(2, offered, passage, listed).swaps.length).toBeLessThan(6);
    expect(planSwaps(2, offered, passage, listed, { intensity: -3, seed: 3 }).swaps).toEqual([]);
  });
  test('the lighter levels pick from the answer at normal, the heavier ones from the full answer', () => {
    expect(INTENSITY_LEVELS.map(answerLevel)).toEqual([0, 0, 0, 0, INTENSITY_FULL, INTENSITY_FULL, INTENSITY_FULL]);
  });
  test('intensity levels -3..3: names, labels, factors, lookup budgets and the per-sentence rule', () => {
    expect(INTENSITY_LEVELS.map((l) => INTENSITY_NAMES[l])).toEqual(['off', 'much lighter', 'lighter', 'normal', 'heavier', 'much heavier', 'full']);
    expect(INTENSITY_LEVELS.map(intensityLabel)).toEqual(['off', 'much lighter (−2)', 'lighter (−1)', 'normal', 'heavier (+1)',
      'much heavier (+2)', 'full']);
    expect(INTENSITY_LEVELS.map(intensityFactor)).toEqual([0, 0.5, 0.7, 1, 1.4, 1.8, 1]); // the plan's record of the level
    expect(INTENSITY_LEVELS.map((l) => intensityBudget(l))).toEqual([0, 0.15, 0.3, 0.5, 0.8, 1.2, null]); // off none, full no limit
    expect(INTENSITY_LEVELS.map((l) => intensityBudget(l, 1))).toEqual([0, 0.3, 0.6, 1, 1.6, 2.4, null]); // a challenge of 1
    expect(intensityBudget(0, 0)).toBe(0.5); // an older host sends 0 without a challenge
    expect([-2, -1, 0, 1, 2].map(perSentence)).toEqual([1, 1, 1, 2, 2]);
  });
});

describe('the lookup budget', () => {
  // Each candidate in a sentence of its own, 20 words a sentence: "The w0 x x ... end." The words: a status and an
  // estimate (k, sd) per word.
  const passage = (n) => Array.from({ length: n }, (_, i) => `The w${i} ${'x '.repeat(17)}end.`).join(' ');
  const word = (ja, k, status = 'shaky', sd = 0.01) => ({ ja, en: 'x', hint: '', status, ...(k === undefined ? {} : { k, sd }) });
  const offer = (list, at = (i) => i) => list.map((w, i) => ({ o: `w${at(i)}`, f: w.ja, g: 'x', h: '', ctx: `The w${at(i)} x`, word: w.ja,
    status: w.status }));
  const pick = (list, opts = {}, n = list.length, at) => planSwaps(2, offer(list, at), passage(n), list, { seed: 7, ...opts });
  const p = (k) => 1 / (1 + Math.exp(k));

  test('words known at a glance are free; the rest go by learning value while their lookup chances fit', () => {
    const learning = Array.from({ length: 10 }, (_, i) => word(`l${i}`, i * 0.5)); // lookup chances 0.5 (k 0) to 0.01 (k 4.5)
    const out = pick(learning, { budgetPer100: 0.5 }).swaps.map((s) => s.word); // 1 lookup in 200 words
    expect(out).toEqual(expect.arrayContaining(['l5', 'l6', 'l7', 'l8', 'l9'])); // k 2.5 and up: under FREE_P, free
    const budgeted = out.filter((w) => !['l5', 'l6', 'l7', 'l8', 'l9'].includes(w));
    expect(budgeted).toContain('l2'); // k 1.0 is the closest to a 25% lookup chance
    const spent = budgeted.map((w) => p(learning.find((l) => l.ja === w).k));
    expect(spent.reduce((a, b) => a + b, 0)).toBeLessThan(1 + Math.max(...spent)); // only the last may go over
    expect(pick(learning, { budgetPer100: 0.5 }).swaps).toEqual(pick(learning, { budgetPer100: 0.5 }).swaps); // same passage, same day
    expect(pick(learning, { budgetPer100: 0, intensity: -2 }).swaps.map((s) => s.word)).toEqual(['l5', 'l6', 'l7', 'l8', 'l9']);
  });
  test('a known word without an estimate is free; a learning one, or one the word state lacks, is not', () => {
    const list = [word('k0', undefined, 'known'), word('s0', undefined, 'shaky'), word('a0', undefined, 'active')];
    expect(p(KNOWN_PRIOR_K)).toBeLessThan(FREE_P);
    const none = { budgetPer100: 0, shownToday: ['a0'] };
    expect(pick(list, none).swaps.map((s) => s.word)).toEqual(['k0']);
    expect(planSwaps(2, offer(list), passage(3), [], { seed: 7, ...none }).swaps).toEqual([]); // the bundled list: no statuses
    // known by its estimate whatever its status, and not known whatever its status
    expect(pick([word('s1', 3, 'shaky'), word('k1', 1, 'known')], none).swaps.map((s) => s.word)).toEqual(['s1']);
  });
  test('a phrase or sentence is free when all its words are; else it costs its words not known at a glance', () => {
    const list = [word('今日', 4, 'known'), word('行く', 1.1, 'active'), word('店', 3, 'known')];
    const text = 'Today I went to the store and it was busy all day long there.';
    const group = (f, used) => ({ kind: 'phrase', o: 'went to the store', f, g: 'x', ctx: 'I went to the store', parts: [],
      words: used, word: used[0] });
    const free = group('店に行く', ['店', '今日']);
    const due = group('店に行く', ['店', '行く']);
    expect(planSwaps(2, [free], text, list, { budgetPer100: 0 }).swaps).toEqual([free]);
    expect(planSwaps(2, [due], text, list, { budgetPer100: 0, shownToday: ['行く'] }).swaps).toEqual([]);
    const fits = planSwaps(2, [due], text, list, { budgetPer100: 100 * p(1.1) / 14 + 1e-9, shownToday: ['行く'] });
    expect(fits.swaps).toEqual([due]); // 店 costs nothing: the budget is exactly 行く's lookup chance
    expect(fits.plan.expected).toBe(Math.round((p(1.1) + p(3)) * 1000) / 1000);
  });
  test('one use per word, decided as swaps are kept: a known word alone shows where a sentence with it did not fit', () => {
    const list = [word('大きい', 4, 'known'), word('日', 1.1, 'shaky')];
    const text = 'It was a big day. The big tree fell over in the wind.';
    const [sentence] = validateSwaps(2, [{ k: 's', o: 'It was a big day.', f: '大きい日でした。', p: '大きい|日|でした|。',
      g: 'It was a big day.', c: 'It was a big day.' }], list, text);
    const alone = { o: 'big', f: '大きい', g: 'big', h: '', ctx: 'The big tree', word: '大きい', status: 'known' };
    expect(planSwaps(2, [sentence, alone], text, list, { budgetPer100: 0 }).swaps).toEqual([alone]);
    expect(planSwaps(2, [sentence, alone], text, list, { budgetPer100: 10 }).swaps).toEqual([sentence]);
    const day = { o: 'day', f: '日', g: 'day', h: '', ctx: 'big day', word: '日', status: 'shaky' }; // a learning word twice
    expect(planSwaps(2, [day, { ...day, ctx: 'a big day' }], text, list, { budgetPer100: 10, intensity: 2 }).swaps).toHaveLength(1);
    // a known word offered twice is still kept with chance jitter, not two chances
    const twice = [alone, { ...alone, ctx: 'It was a big' }];
    const kept = Array.from({ length: 2000 }, (_, seed) => planSwaps(2, twice, text, list, { seed, jitter: 0.6 }).swaps.length);
    expect(kept.every((n) => n <= 1)).toBe(true);
    expect(kept.reduce((a, b) => a + b, 0) / kept.length).toBeCloseTo(0.6, 1);
  });
  test('the budget sets how many: the first swap that does not fit gets in with probability remaining / cost', () => {
    const learning = Array.from({ length: 10 }, (_, i) => word(`l${i}`, 1.1)); // 0.25 each
    const count = (budgetPer100) => pick(learning, { budgetPer100 }).swaps.length;
    expect(count(0.25)).toBeLessThanOrEqual(3); // 0.5 lookups at 0.25: two words, maybe a third
    expect(count(0.25)).toBeGreaterThanOrEqual(2);
    expect(count(1)).toBeGreaterThanOrEqual(8);
    expect(count(3)).toBe(10);
    const halves = Array.from({ length: 10 }, (_, i) => word(`h${i}`, 0)); // 0.5 each
    const counts = Array.from({ length: 400 }, (_, seed) => pick(halves, { budgetPer100: 0.1, seed }).swaps.length); // 0.2 lookups
    expect(counts.every((n) => n <= 1)).toBe(true);
    const share = counts.filter((n) => n === 1).length / counts.length; // one word 40% of the time
    expect(share).toBeGreaterThan(0.3);
    expect(share).toBeLessThan(0.5);
    const plan = pick(halves, { budgetPer100: 0.8, intensity: 1, jitter: 0.5 }).plan; // 200 words at 0.8 per 100, halved
    expect(plan.budget).toBe(0.8);
    expect(plan.expected).toBe(plan.limit * 0.5);
    expect(plan.limit).toBeGreaterThanOrEqual(1);
    expect(plan.limit).toBeLessThanOrEqual(2);
  });
  test('at most perSentence budgeted swaps in one sentence; a full sentence passes to the next candidate', () => {
    // three learning words in the first sentence, the best ones, and a worse one in the second; a known word in the first
    const list = [word('a', 1.1), word('b', 1.0), word('c', 1.2), word('d', 2.0), word('k', 4, 'known')];
    const offered = offer(list, (i) => (i === 3 ? 1 : 0));
    const run = (intensity) => planSwaps(2, offered, passage(2), list, { budgetPer100: 10, intensity, seed: 7 }).swaps.map((s) => s.word);
    expect(run(0)).toHaveLength(3); // one of a, b, c; then d; and k, free
    expect(run(0)).toEqual(expect.arrayContaining(['d', 'k']));
    expect(run(1)).toHaveLength(4); // heavier: two a sentence
    expect(run(1)).toEqual(expect.arrayContaining(['d', 'k']));
    expect(run(-2).filter((w) => ['a', 'b', 'c'].includes(w))).toHaveLength(1);
  });
  test('the density caps the total, budgeted swaps first, then free ones', () => {
    const list = [word('k0', 4, 'known'), word('k1', 4, 'known'), word('k2', 4, 'known'), word('l0', 1.1), word('l1', 1.1)];
    const out = pick(list, { budgetPer100: 10, density: 1.5 }); // 1.5% of 100 words: 2 (rounded from 1.5)
    expect(out.swaps.map((s) => s.word)).toEqual(['l0', 'l1']);
    expect(pick(list, { budgetPer100: 10, density: 3 }).swaps.map((s) => s.word)).toEqual(['k0', 'l0', 'l1']);
    expect(pick(list, { budgetPer100: 10 }).swaps).toHaveLength(5); // no density: no other ceiling
  });
  test('one active word not yet shown today goes first, outside the budget', () => {
    const learning = [...Array.from({ length: 10 }, (_, i) => word(`l${i}`, 0.5)), word('new', undefined, 'active')];
    expect(pick(learning, { budgetPer100: 1e-6 }).swaps.map((s) => s.word)).toEqual(['new']);
    expect(pick(learning, { budgetPer100: 1e-6, shownToday: ['new'] }).swaps).toEqual([]);
    expect(pick(learning, { budgetPer100: 1e-6, density: 0.01 }).swaps).toEqual([]); // a density of none leaves none
  });
});

describe('guards', () => {
  test('wordCount', () => {
    expect(wordCount('')).toBe(0);
    expect(wordCount('  one two\n\nthree ')).toBe(3);
  });
  test('zero-Japanese topics', () => {
    expect(isZeroJapanese('if you take ibuprofen talk to your doctor first')).toBe(true);
    expect(isZeroJapanese('my lawyer says the lawsuit is fine')).toBe(true);
    expect(isZeroJapanese('call 911 in an emergency')).toBe(true);
    expect(isZeroJapanese('shipped the new onboarding flow today')).toBe(false);
    expect(isZeroJapanese('the taxi was late again')).toBe(false);
  });
});

describe('phrase and sentence swaps', () => {
  const words = [
    { ja: '楽しい', en: 'fun / enjoyable', hint: 'tanoshii', kana: 'たのしい', status: 'known' },
    { ja: '日', en: 'day', hint: 'hi', kana: 'ひ', status: 'known' },
    { ja: '母', en: 'mother', hint: 'haha', kana: 'はは', status: 'known' },
    { ja: '一緒に', en: 'together', hint: 'issho ni', kana: 'いっしょに', status: 'known' },
    { ja: '店', en: 'shop / store', hint: 'mise', kana: 'みせ', status: 'known' },
    { ja: '行く', en: 'go', hint: 'iku', kana: 'いく', status: 'active' },
    { ja: '寝る', en: 'sleep', hint: 'neru', kana: 'ねる', status: 'known' },
    { ja: '前', en: 'before / front', hint: 'mae', kana: 'まえ', status: 'shaky' },
    { ja: 'する', en: 'do / make', hint: 'suru', kana: 'する', status: 'known' },
  ];
  const sentence = (parts, extra = {}) => ({ kind: 'sentence', o: 'It was a fun day.', f: parts.map((p) => p.t).join(''), parts,
    g: 'It was a fun day.', ctx: 'It was a fun day.', ...extra });
  test('a sentence of listed words and grammar is kept, each word glossed from the list', () => {
    const [s] = validateSwaps(2, [sentence([{ t: '楽しい', w: '楽しい' }, { t: '日', w: '日' }, { t: 'でした。', w: '' }])], words);
    expect(s).toMatchObject({ kind: 'sentence', f: '楽しい日でした。', g: 'It was a fun day.', words: ['楽しい', '日'], word: '楽しい' });
    expect(s.parts[0]).toEqual({ t: '楽しい', word: '楽しい', g: 'fun / enjoyable', h: 'tanoshii', k: 'たのしい', status: 'known' });
    expect(s.parts[2]).toEqual({ t: 'でした。' });
  });
  test('conjugated forms of listed verbs count; the lead word is the first learning word', () => {
    const [s] = validateSwaps(2, [{ kind: 'sentence', o: 'Yesterday I went to the store with my mom.', f: '母と一緒に店に行きました。',
      parts: [{ t: '母', w: '母' }, { t: 'と', w: '' }, { t: '一緒に', w: '一緒に' }, { t: '店', w: '店' }, { t: 'に', w: '' },
        { t: '行きました', w: '行く' }, { t: '。', w: '' }], g: 'I went to the store with my mother.', ctx: 'with my mom' }], words);
    expect(s.word).toBe('行く');
    expect(s.status).toBe('active');
    const [p] = validateSwaps(2, [{ kind: 'phrase', o: 'before bed', f: '寝る前', parts: [{ t: '寝る', w: '寝る' }, { t: '前', w: '前' }],
      g: 'before sleeping', ctx: 'minutes before bed' }], words);
    expect(p.f).toBe('寝る前');
    expect(validateSwaps(2, [{ kind: 'phrase', o: 'did it', f: 'しました', parts: [{ t: 'しました', w: 'する' }], g: 'did', ctx: 'x' }], words))
      .toHaveLength(1);
  });
  test('fails closed: an unlisted word, a piece that is not grammar, pieces that do not spell f, or no listed word', () => {
    const drop = (s) => expect(validateSwaps(2, [s], words)).toEqual([]);
    drop(sentence([{ t: '楽しい', w: '楽しい' }, { t: '一日', w: '一日' }, { t: 'でした。', w: '' }]));
    drop(sentence([{ t: 'とても', w: '' }, { t: '楽しい', w: '楽しい' }, { t: '日', w: '日' }, { t: 'でした。', w: '' }])); // とても is a word
    drop(sentence([{ t: '楽しい', w: '楽しい' }, { t: '日', w: '日' }, { t: 'です。', w: '' }], { f: '楽しい日でした。' }));
    drop(sentence([{ t: 'でした。', w: '' }]));
    drop(sentence([{ t: '楽しく', w: '日' }, { t: 'でした。', w: '' }])); // not a form of the word it names
    drop(sentence([{ t: '楽しい', w: '楽しい' }, { t: '日', w: '日' }], { f: '楽しい日' })); // a sentence ends like one
  });
  test('without a gloss, the English it replaces is the gloss', () => {
    const [s] = validateSwaps(2, [sentence([{ t: '楽しい', w: '楽しい' }, { t: '日', w: '日' }, { t: 'でした。', w: '' }], { g: '' })], words);
    expect(s.g).toBe('It was a fun day.');
  });
  test('grammar pieces may run together; full-width and half-width punctuation match', () => {
    const [s] = validateSwaps(2, [{ kind: 'sentence', o: 'Is it the store?', f: '店ですか？', parts: [{ t: '店', w: '店' }, { t: 'ですか', w: '' },
      { t: '？', w: '' }], g: 'Is it the store?', ctx: 'Is it the store?' }], words);
    expect(s.f).toBe('店ですか？');
    expect(isGrammar('ですか？')).toBe(true);
    expect(isGrammar('とても')).toBe(false);
    expect(isGrammar('なにが')).toBe(false); // なに "what", not な + に
    expect(isGrammar('ものです')).toBe(false);
    for (const g of ['ではない', 'なかった', 'なんです', 'なんだ', 'には', 'のに']) expect(isGrammar(g)).toBe(true);
  });
  test('a word used in a phrase or sentence is not used again on its own in the passage', () => {
    const swaps = validateSwaps(2, [
      sentence([{ t: '楽しい', w: '楽しい' }, { t: '日', w: '日' }, { t: 'でした。', w: '' }]),
      { o: 'day', f: '日', ctx: 'a long day' },
      { o: 'store', f: '店', ctx: 'the store' },
    ], words);
    expect(capSwaps(2, swaps, 'It was a fun day. The store was busy all day long today.', words).map((s) => s.f))
      .toEqual(['楽しい日でした。', '店']);
  });
});

describe('the compact answer', () => {
  const words = [
    { ja: '母', en: 'mother', hint: 'haha', kana: 'はは', status: 'known' },
    { ja: '一緒に', en: 'together', hint: 'issho ni', kana: 'いっしょに', status: 'known' },
    { ja: '店', en: 'shop / store', hint: 'mise', kana: 'みせ', status: 'known' },
    { ja: '行く', en: 'go', hint: 'iku', kana: 'いく', status: 'active' },
    { ja: '見る', en: 'see / watch', hint: 'miru', kana: 'みる', status: 'known' },
    { ja: '知る', en: 'know', hint: 'shiru', kana: 'しる', status: 'known' },
    { ja: 'する', en: 'do / make', hint: 'suru', kana: 'する', status: 'known' },
    { ja: '勉強', en: 'study', hint: 'benkyō', kana: 'べんきょう', status: 'known' },
    { ja: 'いい', en: 'good', hint: 'ii', kana: 'いい', status: 'shaky' },
    { ja: 'なる', en: 'become', hint: 'naru', kana: 'なる', status: 'known' },
    { ja: '大きい', en: 'big', hint: 'ōkii', kana: 'おおきい', status: 'known' },
    { ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'known' },
    { ja: '下', en: 'below / under', hint: 'shita', kana: 'した', status: 'known' },
    { ja: 'ひらがな', en: 'hiragana', hint: 'hiragana', kana: 'ひらがな', status: 'known' },
  ];
  const group = (p, k = 's') => validateSwaps(2, [{ k, o: 'Some English.', f: p.replace(/\|/g, ''), p, g: 'Some English.', c: 'Some' }], words);
  const pieces = (p) => group(p)[0]?.parts.map((x) => (x.word ? `${x.t}=${x.word}` : x.t)).join(' ');
  test('a word swap: k, o, f and c only, glossed from the list', () => {
    expect(validateSwaps(2, [{ k: 'w', o: 'but', f: 'でも', p: '', g: '', c: 'x but y' }], words))
      .toEqual([{ o: 'but', f: 'でも', g: 'but', h: 'demo', ctx: 'x but y', word: 'でも', status: 'known' }]);
  });
  test('pieces are looked up: listed words, forms of listed verbs and adjectives, grammar', () => {
    expect(pieces('母|と|一緒に|店|に|行きました|。')).toBe('母=母 と 一緒に=一緒に 店=店 に 行きました=行く 。');
    expect(group('母|と|一緒に|店|に|行きました|。')[0]).toMatchObject({ kind: 'sentence', word: '行く', status: 'active', g: 'Some English.' });
    expect(pieces('勉強|しました|。')).toBe('勉強=勉強 しました=する 。'); // する, not 知る read しる
    expect(pieces('勉強|した|。')).toBe('勉強=勉強 した=する 。'); // nor shita "below"
    expect(pieces('下|に|行きました|。')).toBe('下=下 に 行きました=行く 。');
    expect(pieces('ヒラガナ|です|。')).toBe('ヒラガナ=ひらがな です 。'); // a listed word in another script
    expect(pieces('大きく|なりました|。')).toBe('大きく=大きい なりました=なる 。');
    expect(pieces('よかった|です|。')).toBe('よかった=いい です 。');
    expect(pieces('店|に|行って|いました|。')).toBe('店=店 に 行って=行く いました 。');
  });
  test('pieces split inside a word or an ending are put back together', () => {
    expect(pieces('母|と|店|に|行き|まし|た|。')).toBe('母=母 と 店=店 に 行きました=行く 。');
    expect(pieces('店|に|行って|い|まし|た|。')).toBe('店=店 に 行って=行く いました 。');
    expect(pieces('大き|かった|です|。')).toBe('大きかった=大きい です 。');
    expect(group('母|と|友|達|に|会いました|。')).toEqual([]); // still no unlisted words
  });
  test('fails closed: an unlisted word, a look-alike verb, a kana word that reads as grammar, no pieces', () => {
    expect(group('母|と|友達|に|会いました|。')).toEqual([]);
    expect(group('母|に|見せました|。')).toEqual([]); // 見せる is not a form of 見る
    expect(group('なに|が|いい|ですか|？')).toEqual([]);
    expect(group('店で見る', 'p')).toEqual([]); // a piece is one word, perhaps with grammar after it
    expect(validateSwaps(2, [{ k: 'p', o: 'with my mom', f: '母と', p: '', g: 'with mother', c: 'x' }], words)).toEqual([]);
  });
  test('a phrase sent as a word swap is a phrase', () => {
    const [s] = validateSwaps(2, [{ k: 'w', o: 'go to the store', f: '店に行く', p: '店|に|行く', g: '', c: 'x' }], words);
    expect(s).toMatchObject({ kind: 'phrase', f: '店に行く', g: 'go to the store', words: ['店', '行く'] });
  });
  test('a phrase ending in a verb ends its clause: no English hanging after it', () => {
    const swap = [{ k: 'p', o: 'went to the store', f: '店に行きました', p: '店|に|行きました', g: 'went to the store', c: 'x' }];
    const kept = (text) => validateSwaps(2, swap, words, text).length === 1;
    expect(kept('Yesterday I went to the store with my mom.')).toBe(false);
    expect(kept('Yesterday I went to the store.')).toBe(true);
    expect(kept('Yesterday I went to the store and bought fish.')).toBe(true);
    expect(kept('I went to the store, then home.')).toBe(true);
    const noun = [{ k: 'p', o: 'going to the store', f: '店に行くこと', p: '店|に|行く|こと', g: 'going to the store', c: 'x' }];
    expect(validateSwaps(2, noun, words, 'I like going to the store with her.').length).toBe(1);
    expect(kept('As soon as I went to the store we saw it.')).toBe(true); // a subject pronoun starts a new clause
  });
  test('one conjugated word alone that would leave English hanging after it is a word swap of the word as listed', () => {
    const swap = (o, p) => validateSwaps(2, [{ k: 'p', o, f: p.replace(/\|/g, ''), p, g: o, c: o }], words, `They ${o} the store again.`);
    expect(swap('went to', '行きました')).toEqual([{ o: 'went to', f: '行く', g: 'go', h: 'iku', k: 'いく', ctx: 'went to', word: '行く',
      status: 'active' }]);
    expect(swap('saw', '見て|いました')).toMatchObject([{ f: '見る', word: '見る' }]); // an ending split off
    expect(swap('went to', '店|に|行きました')).toEqual([]); // a phrase in Japanese order still may not
    expect(swap('I went to', '行きました')).toEqual([]); // the word swap would lose the subject
    expect(validateSwaps(2, [{ k: 'p', o: 'went', f: '行きました', p: '行きました', g: 'went', c: 'went' }], words, 'Then they went.'))
      .toMatchObject([{ kind: 'phrase', f: '行きました' }]); // nothing after it: the phrase stands
  });
  test('an empty piece, as from a bar at the end, is left out', () => {
    expect(validateSwaps(2, [{ k: 'p', o: 'x', f: '母と店に行く', p: '母|と|店|に|行く|', g: 'x', c: 'x' }], words)[0]?.f).toBe('母と店に行く');
  });
  test('f only has to say what the pieces say: bars and punctuation in f are let through', () => {
    const kept = (f, p, k = 'p') => validateSwaps(2, [{ k, o: 'x', f, p, g: 'x', c: 'x' }], words)[0]?.f;
    expect(kept('母|と|店|に|行く', '母|と|店|に|行く')).toBe('母と店に行く'); // f written with the bars
    expect(kept('母と、店に行きました。', '母|と|店|に|行きました|。', 's')).toBe('母と店に行きました。'); // a comma the pieces left out
    expect(kept('母と店に行く', '母|と|店|に|行く|。')).toBe('母と店に行く'); // a phrase keeps the English full stop
    expect(kept('母と店に行く', '母|と|店|に|見る')).toBe(undefined); // other words are still a mismatch
  });
  test('conjugations follow the verb: 行く is 行って, not 行いて', () => {
    const named = (t, w) => validateSwaps(2, [{ kind: 'phrase', o: 'x', f: t, parts: [{ t, w }], g: 'x', ctx: 'x' }], words).length;
    expect(named('行って', '行く')).toBe(1);
    expect(named('行いて', '行く')).toBe(0);
    expect(named('見ませんでした', '見る')).toBe(1);
    expect(named('見せた', '見る')).toBe(0);
    expect(named('しています', 'する')).toBe(1);
  });
});

describe('word swaps where the word cannot go', () => {
  test('mae and ato never stand in for "before" or "after"; a verb that acts on something needs something after it', () => {
    expect(misplaced({ o: 'Before', f: '前' }, 'Before you run the script, check it.')).toBe(true);
    expect(misplaced({ o: 'after', f: '後' }, 'Call me after lunch.')).toBe(true);
    expect(misplaced({ o: 'front', f: '前' }, 'Meet me in front of the store.')).toBe(false);
    expect(misplaced({ o: 'stop', f: '止める' }, 'Otherwise the program will stop with an error.')).toBe(true);
    expect(misplaced({ o: 'opened', f: '開ける' }, 'The door opened.')).toBe(true);
    expect(misplaced({ o: 'opened', f: '開ける' }, 'He opened the window.')).toBe(false);
    expect(misplaced({ o: 'stop', f: '止める' }, 'Please stop the music.')).toBe(false);
    expect(misplaced({ kind: 'phrase', o: 'before bed', f: '寝る前', parts: [] }, 'before bed')).toBe(false);
    const words = [{ ja: '前', en: 'before / front', hint: 'mae', status: 'shaky' }, { ja: '止める', en: 'stop', hint: 'tomeru', status: 'known' }];
    expect(validateSwaps(2, [{ k: 'w', o: 'Before', f: '前', p: '', g: '', c: 'Before you run' },
      { k: 'w', o: 'stop', f: '止める', p: '', g: '', c: 'will stop' }], words, 'Before you run it, the program will stop.')).toEqual([]);
  });
  test('"give (away)" never stands in for "gave me"; no swap leaves a contraction\'s tail behind', () => {
    expect(misplaced({ o: 'gave', f: 'あげる', ctx: 'gave me more' }, 'Lifting gave me more strength.')).toBe(true);
    expect(misplaced({ o: 'gave', f: 'あげる', ctx: 'gave her a book' }, 'I gave her a book.')).toBe(false);
    const text = "I've been studying Japanese. I understand more, and I'm glad.";
    expect(misplaced({ o: 'I', f: '私', ctx: "I've been studying" }, text)).toBe(true);
    expect(misplaced({ o: 'I', f: '私', ctx: 'I understand more' }, text)).toBe(false);
    expect(misplaced({ o: 'I', f: '私', ctx: '' }, text)).toBe(false); // one place it fits
    expect(misplaced({ o: 'I', f: '私', ctx: '' }, "I've seen it. I'm sure.")).toBe(true);
    expect(misplaced({ o: 'grandfather', f: 'おじいさん', ctx: '' }, "It was my grandfather's house.")).toBe(false);
  });
});

describe('number words that are labels, ranks or pronouns', () => {
  const words = [
    { ja: '一つ', en: 'one', hint: 'hitotsu', kana: 'ひとつ', status: 'active' },
    { ja: '一', en: 'one', hint: 'ichi', kana: 'いち', status: 'known' },
    { ja: '二つ', en: 'two', hint: 'futatsu', kana: 'ふたつ', status: 'known' },
    { ja: '二', en: 'two', hint: 'ni', kana: 'に', status: 'known' },
    { ja: '日', en: 'day', hint: 'hi', kana: 'ひ', status: 'known' },
    { ja: '一緒に', en: 'together', hint: 'issho ni', kana: 'いっしょに', status: 'known' },
  ];
  const kept = (text, o, f) => validateSwaps(2, [{ k: 'w', o, f, p: '', g: '', c: '' }], words, text).length === 1;
  test('labels and ranks: "Day one", "number one", "chapter two", "the first"', () => {
    expect(kept('Day one of flying a Tesla Roadster in London', 'one', '一つ')).toBe(false);
    expect(kept('Day one of flying a Tesla Roadster in London', 'Day', '日')).toBe(false);
    expect(kept('We are number one again this year', 'one', '一')).toBe(false);
    expect(kept('Chapter two was the best part', 'two', '二')).toBe(false);
    expect(kept('The first time I saw it', 'first', '一')).toBe(false);
  });
  test('pronouns and idioms: "the one", "no one", "the only one", "one day", "one-on-one"', () => {
    expect(kept('This is the one I wanted', 'one', '一つ')).toBe(false);
    expect(kept('No one came to the party', 'one', '一')).toBe(false);
    expect(kept('She was the only one there', 'one', '一')).toBe(false);
    expect(kept('One day I will visit Japan', 'One', '一')).toBe(false);
    expect(kept('I had a one-on-one with my manager', 'one', '一')).toBe(false);
  });
  test('counters count things, not hours or people; plain counts stay', () => {
    expect(kept('I waited two hours for the bus', 'two', '二つ')).toBe(false);
    expect(kept('I waited two hours for the bus', 'two', '二')).toBe(true);
    expect(kept('I have two ideas for the name', 'two', '二つ')).toBe(true);
    expect(kept('Only one of the ideas worked out', 'one', '一つ')).toBe(true);
    expect(kept('I met two of the founders today', 'two', '二つ')).toBe(false);
    expect(kept('I met two founders today', 'two', '二つ')).toBe(false);
    expect(kept('I met two of the founders today', 'two', '二')).toBe(true);
    expect(kept('It was a long day at work', 'day', '日')).toBe(true);
    expect(kept('We went there together, one by one', 'together', '一緒に')).toBe(true);
  });
  test('phrases with a numeral are checked the same way; no passage, no check', () => {
    const phrase = { kind: 'phrase', o: 'Day one', f: '一日', parts: [{ t: '一', word: '一' }, { t: '日', word: '日' }] };
    expect(numberTrap(phrase, 'Day one of the trip')).toBe(true);
    expect(numberTrap({ o: 'one', f: '一つ' }, '')).toBe(false);
  });
});
