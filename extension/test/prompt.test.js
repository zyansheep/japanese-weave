import { describe, test, expect } from 'bun:test';
import { buildSystemPrompt, buildUserContent, RESPONSE_SCHEMA } from '../src/prompt.js';
import { L1_WORDS, L2_WORDS } from '../src/levels.js';

describe('prompt', () => {
  test('L1 lists its words with glosses and the sense rules', () => {
    const p = buildSystemPrompt(1);
    expect(p).toContain('Level 1');
    for (const w of L1_WORDS) expect(p).toContain(`${w.ja} = ${w.en}`);
    expect(p).toContain('"so" as an intensifier is NOT だから');
    expect(p).toContain('never add particles');
    expect(p).toContain('Zero Japanese in passages about health');
    expect(p).toContain('Default is to swap');
  });
  test('L2 lists the longer list; L3/L4 fall back to L2 for now', () => {
    const p = buildSystemPrompt(2);
    expect(p).toContain('Level 2');
    for (const w of L2_WORDS) expect(p).toContain(w.ja);
    expect(buildSystemPrompt(4)).toBe(p);
  });
  test('user content and schema shape', () => {
    expect(buildUserContent([{ id: 'a', text: 'hi', extra: 1 }])).toBe('Passages:\n[{"id":"a","text":"hi"}]');
    expect(RESPONSE_SCHEMA.properties.passages.items.required).toEqual(['id', 'swaps']);
    expect(RESPONSE_SCHEMA.properties.passages.items.properties.swaps.items.required).toEqual(['o', 'f', 'g', 'h', 'ctx']);
  });
  test('shared state: learning words are the target, unshown ones first; known words are filler; no fixed examples', () => {
    const words = [
      { ja: '大きい', en: 'big', hint: 'ōkii', status: 'active' },
      { ja: '小さい', en: 'small', hint: 'chiisai', status: 'active' },
      { ja: '本当', en: 'true', hint: 'hontō', status: 'shaky' },
      { ja: 'でも', en: 'but', hint: 'demo', status: 'known' },
    ];
    const p = buildSystemPrompt(2, words, ['大きい']);
    expect(p).toContain('LEARNING words');
    expect(p).toContain('小さい = small; 本当 = true; 大きい = big');
    expect(p).toContain('KNOWN words (filler only):\nでも');
    expect(p).not.toContain('Typical swaps');
    expect(p).toContain('four or more swaps');
    expect(buildSystemPrompt(1, words)).toContain('two or more swaps');
  });
});
