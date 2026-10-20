import { describe, test, expect } from 'bun:test';
import { buildSystemPrompt, buildSystemParts, buildUserContent, RESPONSE_SCHEMA } from '../src/prompt.js';
import { L1_WORDS, L2_WORDS } from '../src/levels.js';

const katakana = (ja) => /^[゠-ヿ〜]+$/.test(ja);

describe('prompt', () => {
  test('L1 lists its words with glosses, the three kinds of swap and the meaning rules', () => {
    const p = buildSystemPrompt(1);
    expect(p).toContain('LEARNING words (Level 1)');
    for (const w of L1_WORDS) if (!katakana(w.ja)) expect(p).toContain(`${w.ja} = ${w.en}`);
    expect(p).toContain('THREE KINDS OF SWAP');
    expect(p).toContain('"so" as an intensifier');
    expect(p).toContain('Zero Japanese in passages that give or ask for health');
    expect(p).toContain('A passing mention (a parked car');
    expect(p).toContain('Default is to swap');
    expect(p).toContain('No sentence swaps yet');
  });
  test('number words that name a rank, an order or a label are never swapped; counters count things only', () => {
    const p = buildSystemPrompt(2);
    expect(p).toContain('"number one", "day one"');
    expect(p).toContain('never people, days, years, minutes, money');
  });
  test('L2 lists the longer list; L3/L4 fall back to L2 for now', () => {
    const p = buildSystemPrompt(2);
    expect(p).toContain('Level 2');
    for (const w of L2_WORDS) if (!katakana(w.ja)) expect(p).toContain(w.ja);
    expect(buildSystemPrompt(4)).toBe(p);
    expect(p).toContain('about one sentence in ten');
  });
  test('user content and schema shape', () => {
    expect(buildUserContent([{ id: 'a', text: 'hi', extra: 1 }])).toBe('Passages:\n[{"id":"a","text":"hi","target":2}]');
    const twenty = Array(20).fill('word').join(' ');
    expect(buildUserContent([{ id: 'a', text: twenty }], 15)).toContain('"target":3');
    expect(buildSystemPrompt(2, undefined, [], 15)).toContain('about 15% of the words');
    expect(buildSystemPrompt(2)).toContain('Each passage comes with a "target"');
    expect(RESPONSE_SCHEMA.properties.passages.items.required).toEqual(['id', 'swaps']);
    const swap = RESPONSE_SCHEMA.properties.passages.items.properties.swaps.items;
    expect(swap.required).toEqual(['k', 'o', 'f', 'p', 'g', 'c']);
    expect(swap.properties.k.enum).toEqual(['w', 'p', 's']);
  });
  test('shared state: learning words first (unshown today ahead), known words with glosses, no katakana loanwords', () => {
    const words = [
      { ja: '大きい', en: 'big', hint: 'ōkii', status: 'active' },
      { ja: '小さい', en: 'small', hint: 'chiisai', status: 'active' },
      { ja: '本当', en: 'true', hint: 'hontō', status: 'shaky' },
      { ja: 'でも', en: 'but', hint: 'demo', status: 'known' },
      { ja: 'バス', en: 'bus', hint: 'basu', status: 'known' },
    ];
    const p = buildSystemPrompt(2, words, ['大きい']);
    expect(p).toContain('LEARNING words');
    expect(p).toContain('小さい = small; 本当 = true; 大きい = big');
    expect(p).toContain('KNOWN words:\nでも = but\n');
    expect(p).not.toContain('バス');
  });
  test('two parts: everything through the known words, then the learning words that change during the day', () => {
    const words = [{ ja: '大きい', en: 'big', hint: 'ōkii', status: 'active' }, { ja: 'でも', en: 'but', hint: 'demo', status: 'known' }];
    const [stable, learning] = buildSystemParts(2, words, [], 0, 3);
    expect(stable).toContain('THREE KINDS OF SWAP');
    expect(stable.endsWith('KNOWN words:\nでも = but\n')).toBe(true);
    expect(learning).toBe('LEARNING words (the reason this extension exists; most needed first):\n大きい = big');
    expect(buildSystemPrompt(2, words, [], 0, 3)).toBe(stable + learning);
    expect(buildSystemParts(2, words, ['大きい'], 0, 3)[0]).toBe(stable);
  });
  test('a heavier intensity asks for more sentences and swaps; normal is unchanged', () => {
    const words = [{ ja: 'でも', en: 'but', hint: 'demo', status: 'known' }];
    expect(buildSystemPrompt(2, words, [], 0, 3, 0)).toBe(buildSystemPrompt(2, words, [], 0, 3));
    expect(buildSystemPrompt(2, words, [], 0, 3, 1)).toContain('about one sentence in three');
    expect(buildSystemPrompt(2, words, [], 0, 3, 2)).toContain('about two sentences in five');
  });
  test('every passage gets a target: the most swaps planSwaps shows, by sentences or density, raised by a heavier intensity', () => {
    const target = (text, ...args) => JSON.parse(buildUserContent([{ id: 'a', text }], ...args).slice('Passages:\n'.length))[0].target;
    const two = 'The first sentence is here today. The second one follows it now.';
    expect(target(two)).toBe(4); // Level 2: two per sentence
    expect(target(two, 0, { level: 1 })).toBe(2);
    expect(target(two, 0, { heavy: 1 })).toBe(6); // 4 x 1.4
    expect(target(two, 0, { heavy: 2 })).toBe(7); // 4 x 1.8
    expect(target(two, 25)).toBe(3); // a density: its share of the 12 words
  });
  test('each passage names the listed words its English says: learning words, then known ones as leads', () => {
    const words = [
      { ja: 'ある', en: 'have / exist', status: 'shaky' },
      { ja: '入る', en: 'enter', status: 'active' },
      { ja: '犬', en: 'dog', status: 'known' },
      { ja: 'そして', en: 'and then / and', status: 'known' },
      { ja: 'バス', en: 'bus', status: 'known' },
    ];
    const [p] = JSON.parse(buildUserContent([{ id: 'a', text: 'We entered and the dogs had a bus. I have been there.' }], 0, { words })
      .slice('Passages:\n'.length));
    expect(p.learning).toEqual(['ある = have / exist: had', '入る = enter: entered']);
    expect(p.listed).toEqual(['犬 = dog: dogs']); // not "and" for soshite, nor a katakana loanword
    expect(buildUserContent([{ id: 'a', text: 'Nothing listed here at all.' }], 0, { words })).not.toContain('learning');
    // without the plugin's word state, the level's bundled list
    expect(buildUserContent([{ id: 'a', text: 'But the rain stopped.' }], 0, { level: 1 })).toContain('"learning":["でも = but');
    expect(buildSystemPrompt(2, words)).toContain('"listed" names the KNOWN words');
  });
  test('the stage sets how many sentences go fully into Japanese', () => {
    const words = [{ ja: 'でも', en: 'but', hint: 'demo', status: 'known' }];
    expect(buildSystemPrompt(2, words, [], 0, 3)).toContain('about one sentence in five');
    expect(buildSystemPrompt(2, words, [], 0, 2)).toContain('about one sentence in ten');
    expect(buildSystemPrompt(1, words, [], 0, 1)).toContain('No sentence swaps yet');
    expect(buildSystemPrompt(2, words, [], 15, 3)).toContain('Put about one sentence in five fully into Japanese');
  });
});
