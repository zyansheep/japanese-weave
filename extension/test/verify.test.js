import { describe, test, expect } from 'bun:test';
import { buildIndex, matchText } from '../src/matcher.js';
import { parseSenses } from '../src/senses.js';
import { planPassage, needsCall, denseSentences, sentenceRanges, contextFor, buildCheckSystem, buildCheckContent, CHECK_SCHEMA,
  parseCheckResponse, candidatesOf, requestChecks } from '../src/verify.js';
import { API_URL } from '../src/anthropic.js';

const words = [
  { ja: '私', en: 'I / me', hint: 'watashi', kana: 'わたし', status: 'known' },
  { ja: '僕', en: 'I (male)', hint: 'boku', kana: 'ぼく', status: 'known' },
  { ja: '本当に', en: 'really / truly', hint: 'hontou ni', kana: 'ほんとうに', status: 'known' },
  { ja: '大好き', en: 'love / like very much', hint: 'daisuki', kana: 'だいすき', status: 'known' },
  { ja: 'この', en: 'this one', hint: 'kono', kana: 'この', status: 'known' },
  { ja: '映画', en: 'movie', hint: 'eiga', kana: 'えいが', status: 'known' },
  { ja: '時間', en: 'time / hours', hint: 'jikan', kana: 'じかん', status: 'active' },
  { ja: '一つ', en: 'one', hint: 'hitotsu', kana: 'ひとつ', status: 'known' },
  { ja: '日', en: 'day', hint: 'hi', kana: 'ひ', status: 'known' },
];
const flags = parseSenses('id\tja\tkana\tsafe\tdoubtful\n1\t映画\tえいが\tmovie\t\n2\t本当に\tほんとうに\treally\t\n' +
  '3\t大好き\tだいすき\t\tlove\n4\t時間\tじかん\t\ttime\n5\t日\tひ\tday\t\n');
const index = buildIndex(words);
const TEXT = 'I really love this movie. It took a lot of time. Day one was fine.';
const planOf = (text, options) => planPassage(text, matchText(index, text), flags, options);

// An answer as Haiku writes one: picks for the lettered spans, a sentence and a phrase.
const ANSWER = { id: 'p1', picks: '1a 3a 4a 6- 8a', swaps: [{ at: 's1', p: '私|は|この|映画|が|本当に|大好き|です|。' }, { at: '4-5', p: 'この|映画' }] };
const claudeReply = (passages, usage = { input_tokens: 900, cache_read_input_tokens: 1200, cache_creation_input_tokens: 0, output_tokens: 300 }) => ({
  ok: true, status: 200,
  json: async () => ({ content: [{ type: 'thinking', thinking: '', signature: 'x' }, { type: 'text', text: JSON.stringify({ passages }) }], stop_reason: 'end_turn', usage }),
});
const geminiReply = (passages) => ({ ok: true, status: 200,
  json: async () => ({ candidates: [{ content: { parts: [{ text: JSON.stringify({ passages }) }] } }], usageMetadata: { promptTokenCount: 2000, candidatesTokenCount: 200 } }) });

describe('the plan: what code takes and what goes to the model', () => {
  test('safe options are taken; closed-class English and options without a safe flag go to the model, all of them', () => {
    const plan = planOf(TEXT);
    expect(plan.spans.map((m) => [m.n, m.span.english, m.safe.map((o) => o.ja), m.doubtful.map((o) => o.ja)])).toEqual([
      [1, 'I', [], ['私', '僕']], [2, 'really', ['本当に'], []], [3, 'love', [], ['大好き']], [4, 'this', [], ['この']],
      [5, 'movie', ['映画'], []], [6, 'time', [], ['時間']], [7, 'Day', ['日'], []], [8, 'one', [], ['一つ']]]);
    expect(plan.sentences).toEqual([{ n: 1, start: 0, end: 25, text: 'I really love this movie.' }]);
    expect(needsCall(plan)).toBe(true);
  });
  test('a passage with only safe spans and no dense sentence needs no call', () => {
    const plan = planOf('A movie, then another movie.');
    expect(plan.spans.every((m) => m.safe.length)).toBe(true);
    expect(plan.sentences).toEqual([]);
    expect(needsCall(plan)).toBe(false);
  });
  test('a passage on a zero-Japanese topic gets nothing', () => {
    const plan = planOf('I really love this movie about my doctor and the hospital.');
    expect(plan).toMatchObject({ zero: true, spans: [], sentences: [] });
    expect(needsCall(plan)).toBe(false);
  });
  test('dense sentences: every word in a span or said by grammar, 3 to 14 words; none when switched off', () => {
    const spans = (t) => matchText(index, t);
    const dense = (t, o) => denseSentences(t, spans(t), o).map((s) => s.text);
    expect(dense('I really love this movie. I really love this film.')).toEqual(['I really love this movie.']);
    expect(dense('I love it. I love this movie a lot.')).toEqual(['I love it.']); // "a lot" has no word
    expect(dense('Movie.')).toEqual([]);
    expect(dense('I really love this movie.', { maxWords: 4 })).toEqual([]);
    expect(planOf(TEXT, { sentences: false }).sentences).toEqual([]);
  });
  test('sentences split as levels.js counts them: a piece of fewer than 3 words runs on', () => {
    const t = 'Hi! I went home. Dr. Smith said so';
    expect(sentenceRanges(t).map((r) => t.slice(r.start, r.end))).toEqual(['Hi! I went home.', 'Dr. Smith said so']);
  });
  test('a context occurs once in the text and holds its English once', () => {
    const t = 'I said I would. Then I said no.';
    expect(contextFor(t, 0, 1)).toBe('I'); // no stretch around the first I is unique without a second I: it can't be placed
    expect(contextFor(t, 7, 8)).toBe('I would.');
    expect(contextFor(t, 21, 22)).toBe('Then I said');
  });
});

describe('the prompt and the answer', () => {
  test('the user message: each passage, its spans in place, lettered options or the sure word, its sentences', () => {
    const content = buildCheckContent([{ id: 'p1', plan: planOf(TEXT) }]);
    expect(content.split('\n')).toEqual([
      '[p1] I really love this movie. It took a lot of time. Day one was fine.',
      '1 [I] really love this: a 私 = I / me; b 僕 = I (male)',
      '2 I [really] love this movie. = 本当に',
      '3 I really [love] this movie. It: a 大好き = love / like very much',
      '4 I really love [this] movie. It took: a この = this one',
      '5 really love this [movie]. It took = 映画',
      '6 a lot of [time]. Day one: a 時間 = time / hours',
      '7 lot of time. [Day] one was fine. = 日',
      '8 of time. Day [one] was fine.: a 一つ = one',
      's1 "I really love this movie."']);
  });
  test('the system prompt names the grammar and, unless switched off, phrases', () => {
    expect(buildCheckSystem()).toContain('PHRASES');
    expect(buildCheckSystem({ phrases: false })).not.toContain('PHRASES');
    expect(buildCheckSystem()).toContain('ている');
  });
  test('the rules cover the held-out test\'s classes: joiners, ある and いる, 全部 over time, 何でも, "one", する for "make", contractions', () => {
    const rules = buildCheckSystem();
    for (const joiner of ['"while"', '"though"', '"until"', '"because"', '"that" or "which" opening a clause']) expect(rules).toContain(joiner);
    expect(rules).toContain('ある for things, いる for people and animals');
    expect(rules).toContain('never a stretch of time');
    expect(rules).toContain('何でも only for "anything" as any at all');
    expect(rules).toContain('"one" standing for a noun');
    expect(rules).toContain('never for "make"');
    expect(rules).toContain('followed by a contracted verb');
  });
  test('picks: a letter or - per span number, in any spacing; junk ignored', () => {
    const a = parseCheckResponse(JSON.stringify({ passages: [{ id: 'p1', picks: '1a 2-  3: b,4=C x 5', swaps: [{ at: 's1', p: 'x' }, { p: 'no at' }] }, { picks: '1a' }] }));
    expect([...a.keys()]).toEqual(['p1']);
    expect([...a.get('p1').picks]).toEqual([[1, 0], [2, null], [3, 1], [4, 2]]);
    expect(a.get('p1').swaps).toEqual([{ at: 's1', p: 'x' }]);
    expect(() => parseCheckResponse('not json')).toThrow();
  });
  test('candidates: safe options and picks as word swaps, the sentence and phrase checked by validateSwaps', () => {
    const plan = planOf(TEXT);
    const answer = parseCheckResponse(JSON.stringify({ passages: [ANSWER] })).get('p1');
    const out = candidatesOf(plan, answer, words);
    const words_ = out.filter((s) => !s.parts).map((s) => `${s.o}=${s.f}`);
    // "Day one" is a label: numberTrap drops both the safe 日 and the picked 一つ
    expect(words_).toEqual(['I=私', 'really=本当に', 'love=大好き', 'this=この', 'movie=映画']);
    const sentence = out.find((s) => s.kind === 'sentence');
    expect(sentence).toMatchObject({ o: 'I really love this movie.', f: '私はこの映画が本当に大好きです。', g: 'I really love this movie.' });
    expect(sentence.words).toEqual(['私', 'この', '映画', '本当に', '大好き']);
    expect(out.find((s) => s.kind === 'phrase')).toMatchObject({ o: 'this movie', f: 'この映画' });
    // every word swap's context places it once (text.js locate)
    for (const s of out.filter((x) => !x.parts)) expect(TEXT.split(s.ctx).length).toBe(2);
  });
  test('a sentence with a word not given is dropped; one without its full stop gets it; a passage without an answer keeps its safe options', () => {
    const plan = planOf(TEXT);
    const out = candidatesOf(plan, { picks: new Map(), swaps: [{ at: 's1', p: '私|は|この|映画|が|とても|好き|です|。' }] }, words);
    expect(out.some((s) => s.kind === 'sentence')).toBe(false);
    const fixed = candidatesOf(plan, { picks: new Map(), swaps: [{ at: 's1', p: '私|は|この|映画|が|本当に|大好き|です' }] }, words);
    expect(fixed.find((s) => s.kind === 'sentence').f).toBe('私はこの映画が本当に大好きです。');
    expect(candidatesOf(plan, undefined, words).map((s) => s.f)).toEqual(['本当に', '映画']);
    // a pick of a letter the span doesn't have, an unknown sentence or a backwards range is nothing
    const odd = candidatesOf(plan, { picks: new Map([[1, 5]]), swaps: [{ at: 's9', p: '私|。' }, { at: '5-4', p: 'この|映画' }] }, words);
    expect(odd.map((s) => s.f)).toEqual(['本当に', '映画']);
  });
});

describe('the call', () => {
  const batch = [{ id: 'p1', plan: planOf(TEXT) }];
  test('Haiku: one request with the check prompt and schema, thinking as asked; candidates per passage and the usage', async () => {
    const sent = [];
    const fetchImpl = async (url, init) => { sent.push({ url, body: JSON.parse(init.body) }); return claudeReply([ANSWER]); };
    const r = await requestChecks({ keys: { anthropic: 'A' }, model: 'claude-haiku-5-5', batch, words, fetchImpl, thinking: false });
    expect(sent[0].url).toBe(API_URL);
    expect(sent[0].body.system.map((b) => b.text).join('')).toBe(buildCheckSystem());
    expect(sent[0].body.messages[0].content).toBe(buildCheckContent(batch));
    expect(sent[0].body.thinking).toEqual({ type: 'disabled' });
    expect(sent[0].body.output_config.format.schema.properties.passages.items.additionalProperties).toBe(false);
    expect(r.model).toBe('claude-haiku-5-5');
    expect(r.results.p1.some((s) => s.kind === 'sentence')).toBe(true);
    expect(r.usage).toEqual([{ model: 'claude-haiku-5-5', input: 2100, cached: 1200, written: 0, output: 300 }]);
  });
  test('Gemini: the same prompt as a system instruction, the schema as the response schema', async () => {
    const sent = [];
    const fetchImpl = async (url, init) => { sent.push({ url, body: JSON.parse(init.body) }); return geminiReply([ANSWER]); };
    const r = await requestChecks({ keys: { gemini: 'G' }, model: 'gemini-3.8-flash', batch, words, fetchImpl, phrases: false });
    expect(sent[0].url).toContain('gemini-3.8-flash:generateContent');
    expect(sent[0].body.systemInstruction).toEqual({ parts: [{ text: buildCheckSystem({ phrases: false }) }] });
    expect(sent[0].body.contents).toEqual([{ role: 'user', parts: [{ text: buildCheckContent(batch) }] }]);
    expect(sent[0].body.generationConfig).toMatchObject({ responseMimeType: 'application/json', responseSchema: CHECK_SCHEMA });
    expect(r.model).toBe('gemini-3.8-flash');
    expect(r.results.p1.filter((s) => !s.parts).map((s) => s.f)).toEqual(['私', '本当に', '大好き', 'この', '映画']);
    expect(r.usage[0]).toMatchObject({ model: 'gemini-3.8-flash', input: 2000, output: 200 });
  });
  test('a failed Haiku call falls back to Flash; a passage missing from the answer fails the call', async () => {
    const log = [];
    const fetchImpl = async (url) => {
      log.push(url.includes('anthropic') ? 'claude' : 'gemini');
      return url.includes('anthropic') ? { ok: false, status: 529, text: async () => '' } : geminiReply([ANSWER]);
    };
    const cooldown = new Map();
    const r = await requestChecks({ keys: { anthropic: 'A', gemini: 'G' }, model: 'claude-haiku-5-5', batch, words, fetchImpl, cooldown, now: () => 0 });
    expect(log).toEqual(['claude', 'gemini']);
    expect(r.model).toBe('gemini-3.8-flash');
    expect(cooldown.get('claude-haiku-5-5')).toBe(30000);
    const missing = await requestChecks({ keys: { anthropic: 'A' }, model: 'claude-haiku-5-5', batch, words, fetchImpl: async () => claudeReply([]) }).catch((e) => e);
    expect(missing.message).toContain('Incomplete model response');
    expect(missing.usage[0].output).toBe(300);
  });
});
