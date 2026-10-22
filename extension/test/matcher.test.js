import { describe, test, expect } from 'bun:test';
import { readFileSync } from 'node:fs';
import { buildIndex, matchText, wordForms, entryKinds, glossAlternatives, RULES } from '../src/matcher.js';
import { ENGLISH } from '../src/english.js';
import { judgedPassage } from '../../tools/prompt-lab/oracle.mjs';

// The shared test vectors: scripts/matcher.py runs the same ones (tests/test_matcher.py).
const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const vectors = read('../../tests/matcher_cases.json');

describe('matcher, shared vectors', () => {
  for (const c of vectors.cases) {
    test(c.name + (c.without.length ? ` (without ${c.without.join(', ')})` : ''), () => {
      const spans = matchText(buildIndex(vectors.entries, { without: c.without }), c.text);
      expect(spans.map((s) => ({ start: s.start, end: s.end, english: s.english, options: s.options.map((e) => e.ja),
        glosses: s.glosses, function: s.function }))).toEqual(c.spans);
    });
  }
  test('word forms', () => {
    for (const f of vectors.forms) expect([f.word, f.kinds, wordForms(f.word, f.kinds)]).toEqual([f.word, f.kinds, f.forms]);
  });
  test('parts of speech', () => {
    for (const { kinds, ...entry } of vectors.kinds) expect([entry.ja, entryKinds(entry)]).toEqual([entry.ja, kinds]);
  });
  test('gloss alternatives', () => {
    for (const a of vectors.alternatives) expect(glossAlternatives(a.en)).toEqual(a.alternatives);
  });
  test('the vectors switch off only rules that exist', () => {
    for (const c of vectors.cases) for (const r of c.without) expect(RULES).toContain(r);
  });
});

describe('matcher', () => {
  test('options are the entries given, so a caller keeps its own fields', () => {
    const entries = [{ ja: 'あなた', en: 'you', id: 1 }, { ja: '君', en: 'you', id: 2 }];
    const [span] = matchText(buildIndex(entries), 'Thank you.');
    expect(span.options[0]).toBe(entries[0]);
    expect(span.options[1]).toBe(entries[1]);
  });
  test('nothing to match: no spans', () => {
    const index = buildIndex([{ ja: '犬', en: 'dog' }]);
    expect(matchText(index, '')).toEqual([]);
    expect(matchText(index, null)).toEqual([]);
    expect(matchText(buildIndex([]), 'A dog.')).toEqual([]);
    expect(matchText(buildIndex([{ ja: '年', en: 'counter for years' }, { ja: '空', en: '' }]), 'Two years.')).toEqual([]);
  });
  test('spans never overlap, and the longest wins', () => {
    const index = buildIndex([{ ja: '行く', en: 'go' }, { ja: '帰る', en: 'go back' }, { ja: '後ろ', en: 'back' }]);
    expect(matchText(index, 'We go back. Go! Back.').map((s) => [s.english, s.options.map((e) => e.ja)]))
      .toEqual([['go back', ['帰る']], ['Go', ['行く']], ['Back', ['後ろ']]]);
  });
});

describe('english.js', () => {
  test('is data/english.json (python3 scripts/matcher.py --js writes it)', () => {
    expect(ENGLISH).toEqual(read('../../data/english.json'));
  });
});

describe('prompt lab oracle', () => {
  test('places are whole words, a possessive counts, a contraction does not; #n picks one place', () => {
    const p = { id: 'x', text: "I can't say I can. My grandfather's dog, and I." };
    const warnings = [];
    const j = judgedPassage(p, { use: ['I#1=私', "can=出来る", 'grandfather=おじいさん'], not: ['cat=猫'] }, (w) => warnings.push(w));
    expect(j.use.map((e) => [e.key, p.text.slice(e.start, e.end)])).toEqual([['I#1', 'I'], ['can#0', 'can'], ['grandfather#0', 'grandfather']]);
    expect(j.use[0].start).toBe(p.text.indexOf('I can.'));
    expect(warnings).toEqual(['oracle: x: "cat" is not in the passage']);
    expect(judgedPassage(p, undefined)).toBeNull();
  });
});
