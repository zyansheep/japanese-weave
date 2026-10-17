import { describe, test, expect } from 'bun:test';
import {
  resolveLevel, L1_WORDS, L2_WORDS, validateSwaps, isGrammar, wordCount, isZeroJapanese, normalizeJa, matchCase, capSwaps, selectSwaps,
  numberTrap,
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
  });
  test('a word used in a phrase or sentence is not used again on its own in the passage', () => {
    const swaps = validateSwaps(2, [
      sentence([{ t: '楽しい', w: '楽しい' }, { t: '日', w: '日' }, { t: 'でした。', w: '' }]),
      { o: 'day', f: '日', ctx: 'a long day' },
      { o: 'store', f: '店', ctx: 'the store' },
    ], words);
    expect(capSwaps(2, swaps, 'It was a fun day. The store was busy all day long today.', words).map((s) => s.f))
      .toEqual(['楽しい日でした。', '店']);
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
    expect(pieces('大きく|なりました|。')).toBe('大きく=大きい なりました=なる 。');
    expect(pieces('よかった|です|。')).toBe('よかった=いい です 。');
    expect(pieces('店|に|行って|いました|。')).toBe('店=店 に 行って=行く いました 。');
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
    expect(kept('It was a long day at work', 'day', '日')).toBe(true);
    expect(kept('We went there together, one by one', 'together', '一緒に')).toBe(true);
  });
  test('phrases with a numeral are checked the same way; no passage, no check', () => {
    const phrase = { kind: 'phrase', o: 'Day one', f: '一日', parts: [{ t: '一', word: '一' }, { t: '日', word: '日' }] };
    expect(numberTrap(phrase, 'Day one of the trip')).toBe(true);
    expect(numberTrap({ o: 'one', f: '一つ' }, '')).toBe(false);
  });
});
