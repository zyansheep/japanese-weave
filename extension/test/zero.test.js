// The page guard on the zero-Japanese cases shared with the chat hook (tests/zero_japanese_cases.json: page here,
// chat in scripts/weave.py zero_japanese).
import { describe, expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { isZeroJapanese } from '../src/levels.js';

const { cases } = JSON.parse(readFileSync(new URL('../../tests/zero_japanese_cases.json', import.meta.url), 'utf8'));

describe('zero-Japanese topics', () => {
  test('the page guard agrees with the shared cases', () => {
    expect(cases.length).toBeGreaterThan(10);
    for (const c of cases) expect([c.text, isZeroJapanese(c.text)]).toEqual([c.text, c.page]);
  });
});
