import { describe, test, expect } from 'bun:test';
import { readFileSync, existsSync } from 'node:fs';
import { parseSenses, senseFlag, safeOptions, functionGloss, formatSenses, SENSES_FIELDS } from '../src/senses.js';

// The shared test vectors: scripts/senses.py runs the same ones (tests/test_senses.py).
const vectors = JSON.parse(readFileSync(new URL('../../tests/senses_cases.json', import.meta.url), 'utf8'));
const flags = parseSenses(vectors.file);

describe('sense flags, shared vectors', () => {
  test('a pair is safe, doubtful or not in the file; kana tells homographs apart', () => {
    for (const c of vectors.flags) expect([c.entry, c.gloss, senseFlag(flags, c.entry, c.gloss)]).toEqual([c.entry, c.gloss, c.flag]);
  });
  test('closed-class glosses', () => {
    for (const c of vectors.function) expect([c.gloss, functionGloss(c.gloss)]).toEqual([c.gloss, c.function]);
  });
  test("a span's options", () => {
    for (const c of vectors.spans) expect(safeOptions(flags, c.span)).toEqual(c.safe);
  });
  test('a file with other fields is refused', () => {
    expect(() => parseSenses('# x\nid\tja\tkana\tgloss\tflag\n')).toThrow();
  });
});

describe('the flags file', () => {
  test('formatSenses writes what parseSenses reads, in rank order, words without an entry last', () => {
    const rows = [{ id: null, ja: '路', kana: 'ろ', gloss: 'road', flag: 's' }, { id: 2, ja: '猫', kana: 'ねこ', gloss: 'cat', flag: 's', rank: 9 },
      { id: 1, ja: '犬', kana: 'いぬ', gloss: 'spy', flag: 'd', rank: 3 }, { id: 1, ja: '犬', kana: 'いぬ', gloss: 'dog', flag: 's', rank: 3 }];
    const text = formatSenses(rows, { version: 2, model: 'claude-haiku-5-5', samples: 2 });
    expect(text).toContain('prompt 2, claude-haiku-5-5, safe in all of 2 samples');
    expect(text).toContain('4 pairs of 3 entries, 3 safe.');
    const body = text.split('\n').filter((l) => l && !l.startsWith('#'));
    expect(body).toEqual([SENSES_FIELDS.join('\t'), '1\t犬\tいぬ\tdog\tspy', '2\t猫\tねこ\tcat\t', '\t路\tろ\troad\t']);
    const back = parseSenses(text);
    expect(senseFlag(back, { ja: '猫' }, 'cat')).toBe('s');
    expect(senseFlag(back, { ja: '犬', kana: 'いぬ' }, 'spy')).toBe('d');
  });
  const path = new URL('../../data/senses.tsv', import.meta.url);
  test.skipIf(!existsSync(path))('the committed file parses, and holds no closed-class gloss', () => {
    const committed = parseSenses(readFileSync(path, 'utf8'));
    expect(committed.size).toBeGreaterThan(1000);
    for (const key of committed.keys()) expect(functionGloss(key.split('\t')[1])).toBe(false);
  });
});
