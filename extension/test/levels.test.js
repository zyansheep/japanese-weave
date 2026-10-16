import { describe, test, expect } from 'bun:test';
import {
  resolveLevel, L1_WORDS, L2_WORDS, validateSwaps, wordCount, isZeroJapanese, normalizeJa, matchCase, capSwaps, selectSwaps,
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
  test('capSwaps: the level rule or the density, one use per word, learning words first', () => {
    expect(capSwaps(1, swaps, text, words).map((s) => s.f)).toEqual(['大きい']);
    expect(capSwaps(2, swaps, text, words).map((s) => s.f)).toEqual(['でも', '大きい']);
    expect(capSwaps(2, swaps, text, words, 25).map((s) => s.f)).toEqual(['でも', 'とても', '大きい']);
    expect(selectSwaps(2, swaps, text, words)).toEqual(capSwaps(2, swaps, text, words)); // the dial off
  });

  // 200 words, 10 learning candidates and 10 known ones, room for all of them.
  const long = Array(200).fill('word').join(' ');
  const pool = (k, sd = 0.01, status = 'shaky') => Array.from({ length: 10 }, (_, i) => ({ ja: `l${i}`, en: 'x', hint: '', status, k: k(i), sd }));
  const known = Array.from({ length: 10 }, (_, i) => ({ ja: `k${i}`, en: 'x', hint: '', status: 'known' }));
  const candidates = (list) => list.map((w) => ({ o: 'x', f: w.ja, g: 'x', h: '', ctx: '', word: w.ja, status: w.status }));
  const pick = (list, opts) => selectSwaps(2, candidates(list), long, list, { density: 20, seed: 7, ...opts }).map((s) => s.word);

  test('learning words go by learning value and fit the budget; known words fill the rest', () => {
    const learning = pool((i) => i * 0.5); // peek chances from 0.5 (k 0) down to 0.01 (k 4.5)
    const out = pick([...learning, ...known], { budgetPer100: 0.5 }); // 1 expected peek in 200 words
    const chosen = out.filter((w) => w.startsWith('l'));
    expect(chosen).toContain('l2'); // k 1.0 is the closest to a 25% peek chance
    expect(chosen).not.toContain('l9');
    const spent = chosen.map((w) => 1 / (1 + Math.exp(learning.find((l) => l.ja === w).k))).reduce((a, b) => a + b, 0);
    expect(spent).toBeLessThan(1 + 0.5);
    expect(out.filter((w) => w.startsWith('k'))).toHaveLength(10);
    expect(pick([...learning, ...known], { budgetPer100: 0.5 })).toEqual(out); // same passage, same day: same words
  });
  test('the budget scales the number of learning words', () => {
    const learning = pool(() => 1.1);
    const count = (budgetPer100) => pick(learning, { budgetPer100 }).length;
    expect(count(0.25)).toBeLessThanOrEqual(3); // 0.5 expected peeks at p 0.25: two words, maybe a third
    expect(count(1)).toBeGreaterThanOrEqual(8);
    expect(count(3)).toBe(10);
  });
  test('the first word that does not fit still gets in, with probability remaining / p', () => {
    const learning = pool(() => 0); // p 0.5 each
    const counts = Array.from({ length: 400 }, (_, seed) => selectSwaps(2, candidates(learning), Array(20).fill('w').join(' '), learning,
      { density: 50, budgetPer100: 1, seed }).length); // 0.2 expected peeks: one word 40% of the time
    const share = counts.filter((n) => n === 1).length / counts.length;
    expect(counts.every((n) => n <= 1)).toBe(true);
    expect(share).toBeGreaterThan(0.3);
    expect(share).toBeLessThan(0.5);
  });
  test('one active word not yet shown today goes first, outside the budget', () => {
    const learning = [...pool(() => 4), { ja: 'new', en: 'x', hint: '', status: 'active' }];
    expect(pick(learning, { budgetPer100: 1e-6 })).toEqual(['new']);
    expect(pick(learning, { budgetPer100: 1e-6, shownToday: ['new'] })).toEqual([]);
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
