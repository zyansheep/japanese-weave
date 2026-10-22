import { describe, test, expect } from 'bun:test';
import { pipelineFor, planText, answerKey, storeAnswer, loadAnswer, readingIndexOf, readingCounts } from '../src/pipeline.js';
import { parseCheckResponse, candidatesOf, buildCheckContent, CHECK_VERSION } from '../src/verify.js';
import { planSwaps, INTENSITY_LEVELS } from '../src/levels.js';

// Words as the native host sends them: glosses as the matcher indexes them, the safe ones, the entry id.
const words = [
  { ja: '私', en: 'I / me', hint: 'watashi', kana: 'わたし', status: 'known', glosses: ['i', 'me'], safe: [], id: 1311110 },
  { ja: '本当に', en: 'really / truly', hint: 'hontou ni', kana: 'ほんとうに', status: 'known', glosses: ['really', 'truly'], safe: ['really'], id: 1 },
  { ja: '大好き', en: 'love / like very much', hint: 'daisuki', kana: 'だいすき', status: 'known', glosses: ['love', 'like very much'], safe: [], id: 2 },
  { ja: 'この', en: 'this one', hint: 'kono', kana: 'この', status: 'known', glosses: ['this one'], safe: [], id: 3 },
  { ja: '映画', en: 'movie (film)', hint: 'eiga', kana: 'えいが', status: 'active', glosses: ['movie'], safe: ['movie'], id: 4 },
  { ja: '時間', en: 'time / hours', hint: 'jikan', kana: 'じかん', status: 'active', glosses: ['time', 'hours'], safe: [], id: 5 },
];
const TEXT = 'I really love this movie. It took a lot of time.';
const answerOf = (json) => parseCheckResponse(JSON.stringify({ passages: [{ id: 'p', ...json }] })).get('p');

describe('the words in use', () => {
  test('indexed by the host\'s glosses, shown to the model as written, safe where the host says so', () => {
    const pipe = pipelineFor(words);
    const plan = planText(pipe, TEXT);
    expect(plan.spans.map((m) => [m.span.english, m.safe.map((o) => o.ja), m.doubtful.map((o) => o.ja)])).toEqual([
      ['I', [], ['私']], ['really', ['本当に'], []], ['love', [], ['大好き']], ['this', [], ['この']], ['movie', ['映画'], []],
      ['time', [], ['時間']]]);
    expect(buildCheckContent([{ id: 'p', plan }])).toContain('a 時間 = time / hours'); // the gloss as written
    expect(buildCheckContent([{ id: 'p', plan }])).toContain('5 really love this [movie]. It took = 映画');
    // the bundled lists (no host): every gloss from the word's en, nothing safe, so all goes to the model
    const bare = planText(pipelineFor(words.map(({ glosses: _g, safe: _s, id: _i, ...w }) => w)), TEXT);
    expect(bare.spans.every((m) => !m.safe.length)).toBe(true);
    expect(bare.spans.map((m) => m.span.english)).toEqual(['I', 'really', 'love', 'this', 'movie', 'time']);
  });
});

describe('the answer, cached by what was asked', () => {
  const pipe = pipelineFor(words);
  const plan = planText(pipe, TEXT);
  const answer = answerOf({ picks: '1a 3a 4a 6-', swaps: [{ at: 's1', p: '私|は|この|映画|が|本当に|大好き|です|。' }, { at: '4-5', p: 'この|映画' }] });
  test('the key: the check prompt\'s version, the text, the doubtful spans with their options, the dense sentences', () => {
    const key = answerKey(plan);
    expect(key.startsWith(`${CHECK_VERSION}:`)).toBe(true);
    expect(answerKey(planText(pipelineFor(words), TEXT))).toBe(key); // the same plan, the same key
    expect(answerKey(planText(pipe, `${TEXT} `))).not.toBe(key);
    // a new safe word, or a word that turns safe, re-asks nothing: the model only reads safe spans
    const safer = words.map((w) => (w.ja === '大好き' ? { ...w, safe: ['love'] } : w));
    const lot = { ja: '沢山', en: 'a lot', kana: 'たくさん', status: 'known', glosses: ['a lot'], safe: ['a lot'] };
    expect(planText(pipelineFor([...words, lot]), TEXT).spans.find((m) => m.span.english === 'a lot of').safe).toHaveLength(1);
    expect(answerKey(planText(pipelineFor([...words, lot]), TEXT))).toBe(key);
    expect(answerKey(planText(pipelineFor(safer), TEXT))).not.toBe(key); // love leaves the doubtful spans: asked again
    // a new option on a doubtful span changes what the model would see: asked again
    expect(answerKey(planText(pipelineFor([...words, { ja: '僕', en: 'I (male)', kana: 'ぼく', status: 'known', glosses: ['i'], safe: [] }]),
      TEXT))).not.toBe(key);
  });
  test('stored by place, it comes back in another plan\'s numbers: picks, the sentence and the phrase', () => {
    const stored = storeAnswer(plan, answer);
    expect(stored).toEqual({ picks: [[0, 1, '私'], [9, 13, '大好き'], [14, 18, 'この'], [43, 47, null]],
      swaps: [{ s: [0, 25], p: '私|は|この|映画|が|本当に|大好き|です|。' }, { r: [14, 24], p: 'この|映画' }] });
    expect(JSON.parse(JSON.stringify(stored))).toEqual(stored); // as chrome.storage keeps it
    const back = loadAnswer(plan, stored);
    expect([...back.picks]).toEqual([[1, 0], [3, 0], [4, 0], [6, null]]);
    expect(back.swaps).toEqual([{ at: 's1', p: answer.swaps[0].p }, { at: '4-5', p: 'この|映画' }]);
    expect(candidatesOf(plan, back, words)).toEqual(candidatesOf(plan, answer, words));
    // a new word with a safe span before them shifts every number; the places hold
    const more = [...words, { ja: '今', en: 'now', kana: 'いま', status: 'known', glosses: ['now'], safe: ['now'] }];
    const shifted = planText(pipelineFor(more), `Now ${TEXT}`);
    const moved = storeAnswer(plan, answer);
    const offset = (n) => n + 4;
    const later = { picks: moved.picks.map(([a, b, ja]) => [offset(a), offset(b), ja]),
      swaps: [{ r: [offset(14), offset(24)], p: 'この|映画' }] };
    expect([...loadAnswer(shifted, later).picks]).toEqual([[2, 0], [4, 0], [5, 0], [7, null]]);
    expect(loadAnswer(shifted, later).swaps).toEqual([{ at: '5-6', p: 'この|映画' }]);
    expect(loadAnswer(plan, null)).toBe(null);
    expect(loadAnswer(plan, { picks: [[0, 1, '僕']], swaps: [{ s: [3, 9], p: 'x' }] })).toEqual({ picks: new Map(), swaps: [] }); // no such option or sentence
  });
});

describe('reading counts', () => {
  // the host's reading index: every swappable entry's safe glosses, in rank order
  const reading = readingIndexOf([[1416220, 'n', 'movie;film'], [1358280, 'n', 'time'], [1611260, 'adj-no', 'real'], [1, 'adv', 'really']]);
  test('safe matches of every swappable entry, the most frequent first, never closed-class English', () => {
    const counts = readingCounts({ text: 'Movies and films: the real thing, and about time.', reading, plan: null, words });
    expect(counts).toEqual({ 1416220: 2, 1611260: 1, 1358280: 1 });
  });
  test('the words in use add their safe spans and the picks the model confirmed, where nothing counted overlaps', () => {
    const plan = planText(pipelineFor(words), TEXT);
    const answer = loadAnswer(plan, storeAnswer(plan, answerOf({ picks: '1a 3a 4- 6a', swaps: [] })));
    const counts = readingCounts({ text: TEXT, reading, plan, answer, words });
    // movie and time from the reading index; really (safe), I and love (confirmed) from the words; this: none picked
    expect(counts).toEqual({ 1416220: 1, 1358280: 1, 1: 1, 1311110: 1, 2: 1 });
    // without an answer (no key, or the call failed): the safe ones alone
    expect(readingCounts({ text: TEXT, reading, plan, answer: null, words })).toEqual({ 1416220: 1, 1358280: 1, 1: 1 });
    // without the reading index (an older host): the words in use alone
    expect(readingCounts({ text: TEXT, reading: null, plan, answer, words })).toEqual({ 1: 1, 4: 1, 1311110: 1, 2: 1, 5: 1 });
  });
  test('a pick validation drops does not count', () => {
    const listed = [{ ja: '一つ', en: 'one', kana: 'ひとつ', status: 'known', glosses: ['one'], safe: [], id: 9 }];
    const text = 'Day one of the trip was long and slow.';
    const plan = planText(pipelineFor(listed), text);
    const answer = loadAnswer(plan, storeAnswer(plan, answerOf({ picks: '1a', swaps: [] })));
    expect(readingCounts({ text, reading: null, plan, answer, words: listed })).toEqual({}); // "Day one" is a label
  });
});

describe('one candidate set for every level', () => {
  const plan = planText(pipelineFor(words), TEXT);
  const answer = answerOf({ picks: '1a 3a 4a 6a', swaps: [{ at: 's1', p: '私|は|この|映画|が|本当に|大好き|です|。' }] });
  const candidates = candidatesOf(plan, answer, words);
  const pick = (list, intensity, opts = {}) => planSwaps(2, candidates, TEXT, list, { intensity, seed: 11, ...opts });
  test('off shows nothing, full every candidate, the levels between what their budget pays for', () => {
    expect(candidates.map((c) => c.f)).toEqual(['私', '本当に', '大好き', 'この', '映画', '時間', '私はこの映画が本当に大好きです。']);
    const shown = Object.fromEntries(INTENSITY_LEVELS.map((level) => [level, pick(words, level)]));
    expect(shown[-3].swaps).toEqual([]);
    // full: the sentence and time; the words inside the sentence give way to it
    expect(shown[3].swaps.map((s) => s.f)).toEqual(['時間', '私はこの映画が本当に大好きです。']);
    for (const level of [-2, -1, 0, 1, 2]) {
      expect(shown[level].plan).toMatchObject({ level, candidates: 7 });
      // the known words are free at every level (or thinned the same way); the active ones cost
      expect(shown[level].swaps.filter((s) => ['私', '本当に', '大好き', 'この'].includes(s.f)).length).toBeGreaterThan(0);
    }
    const budgets = [-2, -1, 0, 1, 2].map((level) => shown[level].plan.budget);
    expect(budgets).toEqual([...budgets].sort((a, b) => a - b));
  });
  test('a hidden word is never a candidate: the host leaves it out of the list; a due probe shows first, outside the budget', () => {
    const hidden = words.filter((w) => w.ja !== '映画'); // held back by the experiment: not sent
    const bare = planText(pipelineFor(hidden), TEXT);
    expect(bare.spans.map((m) => m.span.english)).not.toContain('movie');
    // an answer cached before it was held back still names it in the sentence: validation drops the sentence
    expect(candidatesOf(bare, loadAnswer(bare, storeAnswer(plan, answer)), hidden).map((c) => c.f)).toEqual(['私', '本当に', '大好き', 'この', '時間']);
    const probed = words.map((w) => (w.ja === '時間' ? { ...w, arm: 'held:probe', k: 0, sd: 0.1 } : w));
    for (const level of [-2, 0]) {
      const r = planSwaps(2, candidates, TEXT, probed, { intensity: level, budgetPer100: 0, seed: 3 });
      expect(r.swaps.map((s) => s.f)).toContain('時間');
      expect(r.plan).toMatchObject({ spent: 0, arms: { 時間: 'held:probe' } });
    }
  });
});
