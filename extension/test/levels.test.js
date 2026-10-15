import { describe, test, expect } from 'bun:test';
import {
  resolveLevel, L1_WORDS, L2_WORDS, validateSwaps, wordCount, isZeroJapanese, normalizeJa, matchCase,
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
