import { describe, test, expect } from 'bun:test';
import { readFileSync } from 'node:fs';
import { FIELDS, parse } from '../src/dictionary.js';

const FIXTURE = [
  '# Japanese Weave dictionary, format 1: a test fixture.',
  FIELDS.join('\t'),
  ['1320830', '実は', 'じつは', 'jitsu wa', 'actually', 'as a matter of fact;by the way', 'exp,adv', '531', '595', '473',
    '3', '', '1', ''].join('\t'),
  ['1597150', 'タバコ', 'たばこ', 'tabako', 'tobacco / cigarette', 'tobacco;cigarette|tobacco plant', 'n', '3679', '10598',
    '1277', '3', 'por', '0', '煙草,莨'].join('\t'),
  ['1115120', 'プー太郎', 'ぷーたろー', 'pūtarō', 'unemployed person', 'unemployed person', 'n', '', '', '', '2', '', '1',
    ''].join('\t'),
  '',
].join('\n');

describe('dictionary', () => {
  test('parses entries in rank order, as scripts/dictionary.py', () => {
    const [jitsu, tabako, rest] = parse(FIXTURE);
    expect(jitsu).toEqual({
      id: 1320830, ja: '実は', kana: 'じつは', romaji: 'jitsu wa', en: 'actually',
      glosses: [['as a matter of fact', 'by the way']], pos: ['exp', 'adv'], score: 531, jpdb: 595, bccwj: 473,
      common: 3, loan: '', swap: true, forms: [], rank: 1, kanji: ['実'],
    });
    expect(tabako.glosses).toEqual([['tobacco', 'cigarette'], ['tobacco plant']]);
    expect(tabako.forms).toEqual(['煙草', '莨']);
    expect([tabako.loan, tabako.swap, tabako.rank, tabako.kanji]).toEqual(['por', false, 2, []]);
    expect([rest.score, rest.jpdb, rest.rank, rest.kanji]).toEqual([null, null, 3, ['太', '郎']]);
  });

  test('refuses a file with other fields', () => {
    expect(() => parse('id\tja\n1\tx\n')).toThrow('dictionary fields');
  });

  test('reads the committed data/dictionary.tsv', () => {
    const text = readFileSync(new URL('../../data/dictionary.tsv', import.meta.url), 'utf8');
    const entries = parse(text);
    expect(entries.length).toBeGreaterThan(22000);
    expect(new Set(entries.map((e) => e.id)).size).toBe(entries.length);
    const jitsu = entries.find((e) => e.id === 1320830);
    expect([jitsu.ja, jitsu.romaji, jitsu.en]).toEqual(['実は', 'jitsu wa', 'actually']);
    const functions = entries.find((e) => e.id === 1590880);  // not marked common in JMdict: kept for its rank
    expect([functions.ja, functions.common, functions.score <= 20000]).toEqual(['関数', 0, true]);
  });
});
