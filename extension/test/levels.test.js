import { describe, test, expect } from 'bun:test';
import {
  resolveLevel, L1_WORDS, L2_WORDS, validateSwaps, isGrammar, wordCount, isZeroJapanese, normalizeJa, matchCase, selectSwaps,
  numberTrap, planSwaps, jitterFor, JITTER_MIN, misplaced, intensityLabel, intensityFactor, INTENSITY_LEVELS, INTENSITY_NAMES,
  intensityBudget, perSentence, FREE_P, KNOWN_PRIOR_K, SENTENCE_PRICE, SENTENCE_SHARE, SPACING_P,
  currentChance,
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
  const text = 'It was a big day and already a very long one, but fine.'; // 13 words, one sentence
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
  test('planSwaps: known words wherever offered at every level but off, the plan', () => {
    const plain = planSwaps(2, swaps, text, words);
    expect(plain.swaps.map((s) => s.f)).toEqual(['でも', 'とても', 'もう', '大きい']); // one use per word
    // candidates: every one the passage can place, both buts (in the one place "but" is); budget: normal's 0.5 lookups
    // per 100 words; spent: the active word, over the budget but in with chance 0.065 / 0.27; expected: three known
    // words without estimates and the active one
    expect(plain.plan).toEqual({ level: 0, intensity: 1, jitter: 1, limit: 4, candidates: 5, budget: 0.065,
      spent: Math.round(p(1) * 1000) / 1000, expected: Math.round((3 * p(KNOWN_PRIOR_K) + p(1)) * 1000) / 1000,
      sentencePrice: SENTENCE_PRICE });
    for (const intensity of [-2, -1, 1, 2]) {
      expect(planSwaps(2, swaps, text, words, { intensity }).swaps.map((s) => s.f)) // lighter no longer thins known words
        .toEqual(expect.arrayContaining(['でも', 'とても', 'もう']));
    }
    expect(planSwaps(2, swaps, text, words, { intensity: -2 }).plan).toMatchObject({ level: -2, intensity: 0.5, budget: 0.02 });
    // shown today, the active word is one more for the budget: 0.065 lookups against its 0.27
    const later = Array.from({ length: 1000 }, (_, seed) => planSwaps(2, swaps, text, words, { seed, shownToday: ['大きい'] }).swaps);
    expect(later.every((s) => ['でも', 'とても', 'もう'].every((f) => s.some((x) => x.f === f)))).toBe(true);
    expect(later.filter((s) => s.length === 4).length / later.length).toBeCloseTo(0.065 / p(1), 1);
    // a swap the passage doesn't say, or says twice with nothing to tell the places apart, can't be placed: no candidate
    const nowhere = validateSwaps(1, [{ o: 'never', f: 'もう', ctx: 'never' }, { o: 'a', f: '大きい', ctx: '' }], words);
    expect(planSwaps(2, nowhere, text, words).plan).toMatchObject({ limit: 0, candidates: 0 });
  });
  test("the host's free flag decides what is free, whatever k says: its hysteresis keeps one lookup from flipping a word", () => {
    const flagged = words.map((w) => (w.ja === 'もう' ? { ...w, k: 2.0, sd: 0.5, free: true }
      : w.ja === 'でも' ? { ...w, k: 4.0, sd: 0.5, free: false } : w));
    const one = (f) => swaps.filter((s) => s.f === f);
    // もう: a lookup chance of 0.12, over FREE_P, but free: kept at no cost, with no budget at all
    expect(planSwaps(2, one('もう'), text, flagged, { budgetPer100: 0 }).swaps.map((s) => s.f)).toEqual(['もう']);
    // でも: 0.018, under FREE_P, but not free: it costs its chance, and a budget of 0 keeps it out
    expect(planSwaps(2, one('でも'), text, flagged, { budgetPer100: 0 }).swaps).toEqual([]);
    // without the flag, k decides
    const plain = flagged.map(({ free: _free, ...w }) => w);
    expect(planSwaps(2, one('もう'), text, plain, { budgetPer100: 0 }).swaps).toEqual([]);
    expect(planSwaps(2, one('でも'), text, plain, { budgetPer100: 0 }).swaps.map((s) => s.f)).toEqual(['でも']);
  });
  test('the random thinning keeps each free word with chance jitter, from JITTER_MIN (0.2) to 1, and leaves the budget', () => {
    const one = swaps.slice(0, 1); // でも, known
    const kept = Array.from({ length: 2000 }, (_, seed) => planSwaps(2, one, text, words, { seed, jitter: 0.6 }).swaps.length);
    expect(kept.reduce((a, b) => a + b, 0) / kept.length).toBeCloseTo(0.6, 1);
    expect(planSwaps(2, one, text, words, { seed: 1, jitter: 0.6 }).plan).toMatchObject({ jitter: 0.6, budget: 0.065 });
    expect(planSwaps(2, one, text, words, { seed: 1, intensity: -2 }).swaps).toHaveLength(1); // jitter 1: no thinning
    const seeds = Array.from({ length: 4000 }, (_, i) => jitterFor(i * 7919));
    const thinned = seeds.filter((j) => j < 1);
    expect(thinned.length / seeds.length).toBeGreaterThan(0.45);
    expect(thinned.length / seeds.length).toBeLessThan(0.55);
    expect(JITTER_MIN).toBe(0.2);
    expect(Math.min(...thinned)).toBeGreaterThanOrEqual(JITTER_MIN);
    expect(Math.min(...thinned)).toBeLessThan(0.22); // the whole range, not just its top
    expect(Math.max(...thinned)).toBeGreaterThan(0.97);
    expect(thinned.reduce((a, b) => a + b, 0) / thinned.length).toBeCloseTo(0.6, 1);
    expect(jitterFor(42)).toBe(jitterFor(42)); // a passage repaints the same way
  });

  test('full is the same path with no budget: every candidate, sentences free, a word again where its English is again', () => {
    const listed = [...words, { ja: '日', en: 'day', hint: 'hi', status: 'known' }];
    const passage = 'It was a big day. It was a very long one, but fine, but late.';
    const [sentence] = validateSwaps(2, [{ k: 's', o: 'It was a big day.', f: '大きい日でした。', p: '大きい|日|でした|。',
      g: 'It was a big day.', c: 'It was a big day.' }], listed, passage);
    expect(sentence.kind).toBe('sentence');
    const [big, but, very, late] = validateSwaps(2, [{ o: 'big', f: '大きい', ctx: 'a big day' }, { o: 'but', f: 'でも', ctx: 'one, but fine' },
      { o: 'very', f: 'とても', ctx: 'a very long' }, { o: 'but', f: 'でも', ctx: 'fine, but late' }], listed, passage);
    const offered = [big, but, very, late, sentence, { ...but }]; // the model's order, and one exact repeat
    const full = planSwaps(2, offered, passage, listed, { intensity: 3, budgetPer100: 1, jitter: 0.5, seed: 3 });
    // the sentence and not big inside it; both buts; no thinning or per-sentence cap
    expect(full.swaps).toEqual([but, very, late, sentence]);
    // no budget; expected: the lookups predicted, the sentence's translation too
    expect(full.plan).toEqual({ level: 3, intensity: 5, jitter: 1, limit: 4, candidates: 5,
      expected: Math.round((4 * p(KNOWN_PRIOR_K) + p(1) + SENTENCE_PRICE) * 1000) / 1000, sentencePrice: SENTENCE_PRICE });
    const normal = planSwaps(2, offered, passage, listed, { seed: 3 }).swaps;
    expect(normal.filter((s) => s.f === 'でも')).toHaveLength(1); // one use per word below full
    expect(planSwaps(2, offered, passage, listed, { intensity: -3, seed: 3 }).swaps).toEqual([]);
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
  test('recency: a learning word read an hour ago is neither free nor picked; one unread for its half-life is', () => {
    // PLAN.md section 5, rules 1 and 2. 結局's long-term chance is 25% (k 1.1, not free); right after a read its chance
    // falls to the floor (2%) and climbs back over a week, halfway (in odds above the floor) at 3.5 days.
    const now = 1_800_000_000_000;
    const recency = { floor: Math.log(0.02), days: 7 };
    const hour = 3_600_000;
    const learning = (ago) => ({ ...word('結局', 1.1, 'active'), free: false, read: [[now - ago, 1]] });
    const known = { ...word('今', 4, 'known'), free: true, read: [[now - hour, 1]] };
    const plan = (ago) => pick([learning(ago), known], { budgetPer100: 2, now, recency, shownToday: ['結局'] });
    expect(currentChance(1.1, learning(hour).read, now, recency)).toBeLessThan(0.03); // near the floor
    expect(plan(hour).swaps.map((s) => s.word)).toEqual(['今']); // waits, though the budget has room; 今 stays free
    expect(pick([learning(hour), known], { budgetPer100: 2, now, recency }).swaps.map((s) => s.word)).toEqual(['今']); // not
    // even as an active word not shown on the web today
    expect(plan(hour).plan.expected).toBeCloseTo(p(4), 3); // the waiting word isn't shown, so costs nothing
    const half = currentChance(1.1, learning(3.5 * 24 * hour).read, now, recency);
    expect(half).toBeGreaterThan(SPACING_P);
    expect(half / (1 - half)).toBeCloseTo((0.02 + (Math.exp(-1.1) - 0.02) / 2), 2); // halfway back, in odds
    expect(plan(3.5 * 24 * hour).swaps.map((s) => s.word)).toEqual(['結局', '今']); // picked, at its current chance
    expect(plan(3.5 * 24 * hour).plan.expected).toBeCloseTo(half + p(4), 3);
    expect(currentChance(1.1, learning(8 * 24 * hour).read, now, recency)).toBeCloseTo(p(1.1), 9); // a week on: long term
    // read probably not at all, or without the host's recency: its long-term chance
    expect(currentChance(1.1, [[now - hour, 0]], now, recency)).toBeCloseTo(p(1.1), 9);
    expect(currentChance(1.1, learning(hour).read, now, null)).toBeCloseTo(p(1.1), 9);
    // a word known better than the floor has no recency; one whose long-term chance is under SPACING_P never waits
    expect(currentChance(5, [[now - hour, 1]], now, recency)).toBeCloseTo(p(5), 3);
    const near = { ...word('多分', 2.3, 'shaky'), free: false, read: [[now - hour, 1]] }; // 9%: cheap, not free
    expect(pick([near], { budgetPer100: 2, now, recency, shownToday: ['多分'] }).swaps.map((s) => s.word)).toEqual(['多分']);
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
    const plan = pick(halves, { budgetPer100: 0.8, intensity: 1, jitter: 0.5 }).plan; // 200 words at 0.8 per 100, thinned or not
    expect(plan.budget).toBe(1.6);
    expect(plan.expected).toBe(plan.limit * 0.5);
    expect(plan.limit).toBeGreaterThanOrEqual(3);
    expect(plan.limit).toBeLessThanOrEqual(4);
  });
  test('the thinning takes free words only: the budget and what it pays for are the same in thinned and untouched passages', () => {
    const learning = Array.from({ length: 6 }, (_, i) => word(`l${i}`, 0.4 + 0.3 * i)); // lookup chances 0.4 to 0.13
    const known = Array.from({ length: 6 }, (_, i) => word(`k${i}`, 4, 'known'));
    const list = [...learning, ...known];
    const budgeted = (r) => r.swaps.filter((s) => s.word.startsWith('l')).map((s) => s.word);
    let free = 0;
    for (let seed = 0; seed < 300; seed++) {
      const untouched = pick(list, { seed, budgetPer100: 0.6 });
      expect(untouched.swaps.filter((s) => s.word.startsWith('k'))).toHaveLength(6);
      for (const jitter of [0.2, 0.55]) {
        const thinned = pick(list, { seed, budgetPer100: 0.6, jitter });
        expect(thinned.plan.budget).toBe(untouched.plan.budget); // 0.6 per 100 words of 240
        expect(budgeted(thinned)).toEqual(budgeted(untouched));
        if (jitter === 0.2) free += thinned.swaps.length - budgeted(thinned).length;
      }
    }
    expect(free / (300 * 6)).toBeCloseTo(0.2, 1); // each free word kept with chance jitter
  });
  test('at most perSentence budgeted swaps in one sentence; a full sentence passes to the next candidate', () => {
    // three learning words in the first sentence, the best ones, and a worse one in the second; a known word in the first
    const list = [word('a', 1.1), word('b', 1.0), word('c', 1.2), word('d', 2.0), word('k', 4, 'known')];
    const text = `The wa wb wc wk ${'x '.repeat(14)}end. The wd ${'x '.repeat(17)}end.`;
    const offered = list.map((w) => ({ o: `w${w.ja}`, f: w.ja, g: 'x', h: '', ctx: '', word: w.ja, status: w.status }));
    const run = (intensity) => planSwaps(2, offered, text, list, { budgetPer100: 10, intensity, seed: 7 }).swaps.map((s) => s.word);
    expect(run(0)).toHaveLength(3); // one of a, b, c; then d; and k, free
    expect(run(0)).toEqual(expect.arrayContaining(['d', 'k']));
    expect(run(1)).toHaveLength(4); // heavier: two a sentence
    expect(run(1)).toEqual(expect.arrayContaining(['d', 'k']));
    expect(run(-2).filter((w) => ['a', 'b', 'c'].includes(w))).toHaveLength(1);
  });
  test('a whole sentence costs its price and is its sentence\'s one budgeted swap; when it does not fit, its words show', () => {
    const text = 'It was a big day. The big tree fell over in the wind.'; // 13 words
    const sentenceOf = (list) => validateSwaps(2, [{ k: 's', o: 'It was a big day.', f: '大きい日でした。', p: '大きい|日|でした|。',
      g: 'It was a big day.', c: 'It was a big day.' }], list, text)[0];
    const big = { o: 'big', f: '大きい', g: 'big', h: '', ctx: 'a big day', word: '大きい', status: 'known' };
    const day = (status) => ({ o: 'day', f: '日', g: 'day', h: '', ctx: 'big day', word: '日', status });
    // known words only: free alone, but the sentence costs SENTENCE_PRICE
    const known = [word('大きい', 4, 'known'), word('日', 4, 'known')];
    const sentence = sentenceOf(known);
    const offered = [sentence, big, day('known')];
    const words = Math.round(2 * p(4) * 1000) / 1000;
    expect(planSwaps(2, offered, text, known, { budgetPer100: 0 })).toEqual({ swaps: [big, day('known')], plan: { level: 0, intensity: 1,
      jitter: 1, limit: 2, candidates: 3, budget: 0, spent: 0, expected: words, sentencePrice: SENTENCE_PRICE } });
    const enough = 100 * SENTENCE_PRICE / 13 + 1e-9; // the budget is the price
    for (let seed = 0; seed < 50; seed++) { // never thinned, and the words inside it are left out
      const r = planSwaps(2, offered, text, known, { budgetPer100: enough, seed, jitter: 0.2 });
      expect(r.swaps).toEqual([sentence]);
      expect(r.plan.expected).toBe(Math.round((2 * p(4) + SENTENCE_PRICE) * 1000) / 1000);
    }
    // the host's price instead: 0.2 against a budget of 0.08, so in 40% of passages; else its words
    const priced = Array.from({ length: 1000 }, (_, seed) => planSwaps(2, offered, text, known, { budgetPer100: enough, seed,
      sentencePrice: 0.2 }));
    expect(priced.filter((r) => r.swaps[0] === sentence).length / priced.length).toBeCloseTo(0.4, 1);
    expect(priced.every((r) => r.plan.sentencePrice === 0.2 && (r.swaps[0] === sentence || r.swaps.length === 2))).toBe(true);
    // with a learning word in it, the sentence or the word in its sentence, never both, even when two a sentence fit;
    // 2.6 lookups, so the sentences' part (SENTENCE_SHARE) pays for the sentence's 0.33 and the word goes by rank
    const learning = [word('大きい', 4, 'known'), word('日', 1.1, 'shaky')];
    const withDay = [sentenceOf(learning), big, day('shaky')];
    for (const intensity of [0, 2]) {
      const picks = Array.from({ length: 200 }, (_, seed) => planSwaps(2, withDay, text, learning, { budgetPer100: 20, seed, intensity }).swaps);
      expect(picks.every((s) => s.includes(withDay[0]) !== s.includes(withDay[2]))).toBe(true);
      expect(picks.every((s) => s.includes(withDay[0]) !== s.includes(big))).toBe(true); // big shows where the sentence doesn't
      expect(picks.some((s) => s.includes(withDay[0]))).toBe(true);
      // above normal the sentence chooses first (heavier.test.js): the word shows only where the sentence doesn't fit
      if (intensity === 0) expect(picks.some((s) => s.includes(withDay[2]))).toBe(true);
    }
  });
  test('whole sentences spend up to SENTENCE_SHARE of the budget, words the rest; what one cannot use goes to the other', () => {
    // 40 sentences of 20 words: a learning word (0.25) in each of the first 20, a whole sentence of a known word to be had
    // for each of the last 20 (SENTENCE_PRICE); 0.5 lookups per 100 words of 800 is 4
    const learning = Array.from({ length: 20 }, (_, i) => word(`l${i}`, 1.1));
    const known = Array.from({ length: 20 }, (_, i) => word(`k${i}`, 4, 'known'));
    const list = [...learning, ...known];
    const sentences = known.map((k, i) => ({ kind: 'sentence', o: `The w${20 + i} ${'x '.repeat(17)}end.`,
      f: `${k.ja}です。`, g: 'x', ctx: '', words: [k.ja], word: k.ja }));
    const plan = (offered) => {
      const r = planSwaps(2, offered, passage(40), list, { budgetPer100: 0.5, seed: 7 });
      const whole = r.swaps.filter((s) => s.kind === 'sentence');
      return { words: r.swaps.length - whole.length, whole: whole.length, expected: r.plan.expected };
    };
    expect(SENTENCE_SHARE).toBe(0.18);
    expect(SENTENCE_SHARE * intensityBudget(0) / SENTENCE_PRICE).toBeCloseTo(1.125); // sentences per 100 words at normal
    const both = plan([...offer(learning), ...sentences]);
    expect(both.whole).toBe(Math.floor(4 * SENTENCE_SHARE / SENTENCE_PRICE + 1e-9)); // 0.72 buys 9
    expect(both.words).toBeGreaterThanOrEqual(13); // 3.28 buys 13, and a 14th with chance 0.03 / 0.25
    expect(both.words).toBeLessThanOrEqual(14);
    // either alone gets the whole budget: 16 words, or every sentence (20 cost 1.6 of the 4)
    expect(plan(offer(learning))).toMatchObject({ words: 16, whole: 0 });
    expect(plan(sentences)).toMatchObject({ words: 0, whole: 20 });
    // a sentence that does not fit its part waits for what words leave: with only 2 learning words, the other 3.5 pay
    // for sentences priced at 0.3, 11 of them and a 12th with chance 0.2 / 0.3
    const dear = Array.from({ length: 30 }, (_, seed) => planSwaps(2, [...offer(learning.slice(0, 2)), ...sentences],
      passage(40), list, { budgetPer100: 0.5, seed, sentencePrice: 0.3 }).swaps.filter((s) => s.kind === 'sentence').length);
    expect(Math.min(...dear)).toBe(11);
    expect(Math.max(...dear)).toBe(12);
  });
  test('no ceiling but the budget: every free word and every costly one it pays for', () => {
    const list = [word('k0', 4, 'known'), word('k1', 4, 'known'), word('k2', 4, 'known'), word('l0', 1.1), word('l1', 1.1)];
    expect(pick(list, { budgetPer100: 10 }).swaps).toHaveLength(5);
  });
  test('keep: swaps already on the page stay, first and in the budget, and the rest is planned around them', () => {
    const list = [word('k0', 4, 'known'), word('l0', 1.1), word('l1', 1.1), word('l2', 1.1)];
    const all = offer(list);
    // a budget of one learning word: kept l2 takes it, so neither l0 nor l1 gets in, whatever the seed
    for (let seed = 0; seed < 20; seed++) {
      const r = pick(list, { budgetPer100: 0.27, seed, keep: [all[3]] });
      expect(r.swaps.map((s) => s.word).sort()).toEqual(['k0', 'l2']);
      expect(r.plan.spent).toBeCloseTo(1 / (1 + Math.exp(1.1)), 3);
    }
    // kept past a budget that has shrunk since (no budget at all): still kept, and nothing else costly
    expect(pick(list, { budgetPer100: 0, keep: [all[1], all[2]] }).swaps.map((s) => s.word)).toEqual(['k0', 'l0', 'l1']);
    // a kept free word is never thinned out
    for (let seed = 0; seed < 20; seed++) {
      expect(pick(list, { budgetPer100: 0, jitter: 0.2, seed, keep: [all[0]] }).swaps.map((s) => s.word)).toEqual(['k0']);
    }
  });
  test('keep with a probe: the shown probe stays outside the budget, and no second showing or overlap is added', () => {
    const list = [{ ...word('new', undefined, 'active'), arm: 'held:probe' }, word('k0', 4, 'known')];
    const text = passage(2);
    const at = (n) => ({ o: `w${n}`, f: 'new', g: 'x', h: '', ctx: `The w${n} x`, word: 'new', status: 'active' });
    const phrase = { kind: 'phrase', o: 'w0 x', f: 'newk0', g: 'x', ctx: 'The w0 x x', words: ['new', 'k0'], word: 'new',
      parts: [{ t: 'new', word: 'new' }, { t: 'k0', word: 'k0' }] };
    const first = planSwaps(2, [at(0)], text, list, { seed: 7, budgetPer100: 1e-6 });
    expect(first.swaps.map((s) => s.ctx)).toEqual(['The w0 x']);
    expect(first.plan.spent).toBe(0);
    for (const offered of [[at(0), at(1)], [phrase, at(0)]]) {
      const again = planSwaps(2, offered, text, list, { seed: 7, budgetPer100: 1e-6, keep: first.swaps });
      expect(again.swaps).toEqual([at(0)]); // not new at w1 again, not the phrase over the shown word
      expect(again.plan).toMatchObject({ spent: 0, arms: { new: 'held:probe' } });
    }
    // seen today already (its 'seen' event in before the answer): still the probe it was, outside the budget
    const seen = planSwaps(2, [at(0), at(1)], text, list, { seed: 7, budgetPer100: 1e-6, keep: first.swaps, shownToday: ['new'] });
    expect(seen.swaps).toEqual([at(0)]);
    expect(seen.plan.spent).toBe(0);
  });
  test('a word whose delayed-start probe is due goes first, outside the budget, once a day; plans record the arms', () => {
    const learning = [...Array.from({ length: 10 }, (_, i) => word(`l${i}`, 0.5)), word('new', undefined, 'active')];
    expect(pick(learning, { budgetPer100: 1e-6 }).swaps).toEqual([]); // a new word alone no longer jumps the budget
    const probed = learning.map((w) => (w.ja === 'new' ? { ...w, arm: 'held:probe' } : w));
    const first = pick(probed, { budgetPer100: 1e-6 });
    expect(first.swaps.map((s) => s.word)).toEqual(['new']);
    expect(first.plan).toMatchObject({ spent: 0, arms: { new: 'held:probe' } }); // outside the budget
    expect(pick(probed, { budgetPer100: 1e-6, shownToday: ['new'] }).swaps).toEqual([]); // shown today: the probe is had
    // free, it still goes first: thinning can't drop it
    const free = probed.map((w) => (w.ja === 'new' ? { ...w, k: 4, sd: 0.1, free: true, arm: 'shown:probe' } : w));
    for (let seed = 0; seed < 20; seed++) {
      expect(pick(free, { budgetPer100: 1e-6, jitter: 0.2, seed }).swaps.map((s) => s.word)).toEqual(['new']);
    }
    const shown = learning.map((w) => (w.ja === 'l3' ? { ...w, arm: 'shown' } : w));
    const budgeted = pick(shown, { budgetPer100: 50 });
    expect(budgeted.plan.arms).toEqual({ l3: 'shown' }); // a word in its shown days: no priority, but recorded
    expect(pick(learning, { budgetPer100: 50 }).plan.arms).toBeUndefined();
  });
  test('a candidate with a word the host did not send is never offered: the host leaves out the words the experiment hides', () => {
    const list = [word('l0', 0.5), word('l1', 0.5)];
    const offered = offer([...list, word('held', 0.5)]);
    expect(planSwaps(2, offered, passage(3), list, { seed: 7, budgetPer100: 50 }).swaps.map((s) => s.word)).toEqual(['l0', 'l1']);
    expect(planSwaps(2, offered, passage(3), list, { seed: 7, intensity: 3 }).swaps.map((s) => s.word)).toEqual(['l0', 'l1']);
    expect(planSwaps(2, offered, passage(3), list, { seed: 7 }).plan.candidates).toBe(2);
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
    expect(planSwaps(2, swaps, 'It was a fun day. The store was busy all day long today.', words, { budgetPer100: 10 }).swaps
      .map((s) => s.f)).toEqual(['楽しい日でした。', '店']);
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
  test('"\'s" after a word whose "\'s" is a verb is a contraction\'s tail; a pronoun with its contracted verb is not one word', () => {
    expect(misplaced({ o: 'That', f: 'それ', ctx: '' }, "That's my favorite song.")).toBe(true);
    expect(misplaced({ o: 'That', f: 'それ', ctx: '' }, 'That is my favorite song.')).toBe(false);
    expect(misplaced({ o: 'What', f: 'どれ', ctx: '' }, "What's the plan?")).toBe(true);
    expect(misplaced({ o: 'Here', f: 'ここ', ctx: '' }, "Here's what I said.")).toBe(true);
    expect(misplaced({ o: "I'm", f: '私', ctx: '' }, "I'm busy today.")).toBe(true);
    expect(misplaced({ o: "it's", f: 'それ' }, '')).toBe(true);
    expect(misplaced({ o: "There's", f: 'ある', ctx: '' }, "There's a bench in the yard.")).toBe(false); // the existence verb's English
  });
  test('no word swap in front of what a joiner joins: while, though, when, until; a question\'s "when" or "where" stays', () => {
    expect(misplaced({ o: 'while', f: '間', ctx: '' }, 'We talked while we waited for the bus.')).toBe(true);
    expect(misplaced({ o: 'though', f: 'だけど', ctx: '' }, 'Even though it rained, we went out.')).toBe(true);
    expect(misplaced({ o: 'When', f: '時', ctx: '' }, 'When I got home, the lights were off.')).toBe(true);
    expect(misplaced({ o: 'When', f: 'いつ', ctx: '' }, 'When I got home, the lights were off.')).toBe(true);
    expect(misplaced({ o: 'where', f: 'どこ', ctx: '' }, 'This is the town where I grew up.')).toBe(true);
    expect(misplaced({ o: 'When', f: 'いつ', ctx: '' }, 'When did you get home?')).toBe(false);
    expect(misplaced({ o: 'Where', f: 'どこ', ctx: '' }, 'Where is the station?')).toBe(false);
    expect(misplaced({ o: 'though', f: 'だけど', ctx: '' }, 'No rush though.')).toBe(false); // at the end it joins nothing ahead
    expect(misplaced({ o: 'If', f: 'もし', ctx: '' }, 'If it rains, we stay home.')).toBe(false); // moshi comes first in Japanese too
    expect(misplaced({ o: 'between', f: '間', ctx: '' }, 'There is a gap between the two models.')).toBe(false);
  });
  test('suru and yaru only for "do"; aru and nai never for people or animals', () => {
    expect(misplaced({ o: 'made', f: 'する', ctx: '' }, 'She made a cake for the party.')).toBe(true);
    expect(misplaced({ o: 'Make', f: 'する', ctx: '' }, 'Make sure the tests pass.')).toBe(true);
    expect(misplaced({ o: 'did', f: 'する', ctx: '' }, 'I did the dishes.')).toBe(false);
    expect(misplaced({ o: 'has', f: 'ある', ctx: '' }, 'My sister has a dog and two cats.')).toBe(true);
    expect(misplaced({ o: 'have', f: 'ある', ctx: '' }, 'We have a lot of friends here.')).toBe(true);
    expect(misplaced({ o: 'There are', f: 'ある', ctx: '' }, 'There are many people in the park.')).toBe(true);
    expect(misplaced({ o: "There's", f: 'ある', ctx: '' }, "There's a puppy in the yard.")).toBe(true);
    expect(misplaced({ o: 'has', f: 'ある', ctx: '' }, 'My sister has a car and a dog.')).toBe(false); // the car
    expect(misplaced({ o: 'have', f: 'ある', ctx: '' }, "I have my mom's old car.")).toBe(false);
    expect(misplaced({ o: 'have', f: 'ある', ctx: '' }, 'I have a picture of my dog.')).toBe(false);
    expect(misplaced({ o: 'have', f: 'いる', ctx: '' }, 'We have a dog.')).toBe(false);
    expect(misplaced({ o: 'has', f: 'ある', ctx: '' }, 'The release has a bug in the parser.')).toBe(false); // software
  });
  test('no "all" over a stretch of time; free-choice "anything" only where it is free', () => {
    expect(misplaced({ o: 'all', f: '全部', ctx: '' }, 'I slept all day yesterday.')).toBe(true);
    expect(misplaced({ o: 'all', f: '全部', ctx: '' }, 'I have lived here all my life.')).toBe(true);
    expect(misplaced({ o: 'whole', f: '全部', ctx: '' }, 'I was there the whole week.')).toBe(true);
    expect(misplaced({ o: 'all', f: 'みんな', ctx: '' }, 'He knew it all along.')).toBe(true);
    expect(misplaced({ o: 'all', f: '全部', ctx: '' }, 'I ate all the cookies.')).toBe(false);
    expect(misplaced({ o: 'all', f: '全部', ctx: '' }, 'She read all of the books this year.')).toBe(false); // the books
    expect(misplaced({ o: 'anything', f: '何でも', ctx: '' }, "I didn't say anything.")).toBe(true);
    expect(misplaced({ o: 'anything', f: '何でも', ctx: '' }, 'I dont need anything else.')).toBe(true);
    expect(misplaced({ o: 'anything', f: '何でも', ctx: '' }, 'Do you need anything?')).toBe(true);
    expect(misplaced({ o: 'anything', f: '何でも', ctx: '' }, 'Before you buy anything, read the reviews.')).toBe(true);
    expect(misplaced({ o: 'anything', f: '何でも', ctx: '' }, 'I will eat anything.')).toBe(false);
    expect(misplaced({ o: 'anything', f: '何でも', ctx: '' }, 'You can ask me anything you want.')).toBe(false);
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
    expect(kept('At least one of them works', 'one', '一つ')).toBe(true);
    expect(kept('One of my friends called', 'One', '一つ')).toBe(false); // one member: a pronoun
    expect(kept('It is one of the best songs', 'one', '一')).toBe(false);
    expect(kept('I like the most recent one', 'one', '一つ')).toBe(false);
    expect(kept('I want that red one', 'one', '一つ')).toBe(false);
    expect(kept('That leaves one question', 'one', '一つ')).toBe(true);
    expect(kept('This is one idea we had', 'one', '一つ')).toBe(true);
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
