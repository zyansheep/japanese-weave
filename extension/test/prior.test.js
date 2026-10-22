import { describe, test, expect } from 'bun:test';
import { readFileSync } from 'node:fs';
import { parse } from '../src/dictionary.js';
import { priorOf, wordFeatures, script, posGroup } from '../src/prior.js';

const cases = JSON.parse(readFileSync(new URL('../../tests/prior_cases.json', import.meta.url), 'utf8'));

const close = (a, b) => Object.keys(a).length === Object.keys(b).length
  && Object.entries(a).every(([name, value]) => Math.abs(value - b[name]) < 1e-9);

describe('prior', () => {
  test('agrees with scripts/knowledge.py on the shared cases (tests/prior_cases.json)', () => {
    for (const c of cases.cases) {
      const got = priorOf(cases.table, c.entry, { origin: c.origin, interval: c.interval });
      expect(Math.abs(got.k - c.k)).toBeLessThan(1e-9);
      expect(got.sd).toBe(c.sd);
      if (c.entry) expect(close(wordFeatures(c.entry, cases.table.kanji, cases.table.centre), c.features)).toBe(true);
    }
  });

  test('scripts and parts of speech', () => {
    expect(['時間', '人々', '一ヶ月', '食べる', 'シーア派', 'ゆっくり', 'サボる', 'テレビ', 'ＣＤ'].map(script))
      .toEqual(['kanji', 'kanji', 'kanji', 'mixed', 'mixed', 'kana', 'kana', 'katakana', 'katakana']);
    expect([['n', 'vs'], ['num'], ['v5r'], ['adj-na'], ['adv-to'], ['exp'], []].map(posGroup))
      .toEqual(['noun', 'noun', 'verb', 'adjective', 'adverb', 'other', 'other']);
  });

  test('any entry of the committed dictionary has a prior, rarer words lower', () => {
    const entries = parse(readFileSync(new URL('../../data/dictionary.tsv', import.meta.url), 'utf8'));
    const ks = entries.map((e) => priorOf(cases.table, e)?.k);
    expect(ks.every(Number.isFinite)).toBe(true);
    const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
    expect(mean(ks.slice(0, 1000))).toBeGreaterThan(mean(ks.slice(-1000)));
  });

  test('no table, no prior; a table without word coefficients gives the prior by origin', () => {
    expect(priorOf(null, cases.cases[0].entry)).toBeNull();
    expect(priorOf({ none: { new: 1 } }, cases.cases[0].entry)).toBeNull();  // no sd
    const old = { card: cases.table.card, interval: 0.64, none: cases.table.none, sd: cases.table.sd };
    expect(priorOf(old, cases.cases[0].entry)).toEqual({ k: 1.2, sd: 1.0 });
  });
});
