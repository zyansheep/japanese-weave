import { describe, test, expect } from 'bun:test';
import { buildClaudeRequest, callClaude, closed, API_URL } from '../src/anthropic.js';
import { requestChecks, CHECK_SCHEMA, parseCheckResponse } from '../src/verify.js';

const claudeReply = (passages, usage = { input_tokens: 100, cache_read_input_tokens: 3000, cache_creation_input_tokens: 0, output_tokens: 500 }, extra = {}) => ({
  ok: true, status: 200,
  json: async () => ({ content: [{ type: 'text', text: JSON.stringify({ passages }) }], stop_reason: 'end_turn', usage, ...extra }),
});

describe('anthropic', () => {
  test('request: light thinking, low effort, a closed JSON schema, the system prompt in cached parts', () => {
    const body = buildClaudeRequest({ model: 'claude-haiku-5-5', system: ['rules', 'learning'], user: 'U', schema: CHECK_SCHEMA });
    expect(body.system).toEqual([{ type: 'text', text: 'rules', cache_control: { type: 'ephemeral' } },
      { type: 'text', text: 'learning', cache_control: { type: 'ephemeral' } }]);
    expect(body.messages).toEqual([{ role: 'user', content: 'U' }]);
    expect(body.thinking).toEqual({ type: 'adaptive' });
    expect(body.output_config.effort).toBe('low');
    const schema = body.output_config.format.schema;
    expect(schema.additionalProperties).toBe(false);
    expect(schema.properties.passages.items.properties.swaps.items.additionalProperties).toBe(false);
    expect(closed({ type: 'string' })).toEqual({ type: 'string' });
    expect('temperature' in body).toBe(false);
  });
  test('posts with the key in a header; usage counts cache reads and writes into input', async () => {
    const calls = [];
    const fetchImpl = async (url, init) => { calls.push({ url, init }); return claudeReply([{ id: 'a', picks: '', swaps: [] }], { input_tokens: 100, cache_read_input_tokens: 3000, cache_creation_input_tokens: 200, output_tokens: 500 }); };
    const out = await callClaude({ fetchImpl, apiKey: 'K', body: { x: 1 }, parse: parseCheckResponse });
    expect(calls[0].url).toBe(API_URL);
    expect(calls[0].init.headers['x-api-key']).toBe('K');
    expect(calls[0].init.headers['anthropic-version']).toBe('2023-06-01');
    expect(out.byId.get('a')).toEqual({ picks: new Map(), swaps: [] });
    expect(out.usage).toEqual({ input: 3300, cached: 3000, written: 200, output: 500 });
  });
  test('errors: retry-after, the API message, a refusal or a cut-off answer is billed and thrown', async () => {
    const limited = await callClaude({ apiKey: 'K', body: {}, parse: parseCheckResponse, fetchImpl: async () => ({ ok: false, status: 429, headers: new Headers({ 'retry-after': '12' }),
      text: async () => '{"type":"error","error":{"type":"rate_limit_error","message":"Number of requests has exceeded your rate limit"}}' }) }).catch((e) => e);
    expect(limited.status).toBe(429);
    expect(limited.retryAfterMs).toBe(12000);
    expect(limited.message).toContain('rate limit');
    const cut = await callClaude({ apiKey: 'K', body: {}, parse: parseCheckResponse, fetchImpl: async () => claudeReply([], undefined, { stop_reason: 'max_tokens' }) }).catch((e) => e);
    expect(cut.message).toContain('max_tokens');
    expect(cut.usage.output).toBe(500);
  });
});

describe('the check call with two providers', () => {
  const plan = { text: 'x but y', spans: [{ n: 1, span: { start: 2, end: 5, english: 'but' }, safe: [], doubtful: [{ ja: 'でも', en: 'but' }] }], sentences: [] };
  const batch = [{ id: 'a', plan }];
  const answer = [{ id: 'a', picks: '1a', swaps: [] }];
  const route = (log) => async (url) => {
    log.push(url.includes('anthropic') ? 'claude' : url.split('/').pop().split(':')[0]);
    return url.includes('anthropic') ? claudeReply(answer)
      : { ok: true, status: 200, json: async () => ({ candidates: [{ content: { parts: [{ text: JSON.stringify({ passages: answer }) }] } }], usageMetadata: {} }) };
  };
  const words = [{ ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'known' }];
  test('Haiku with an Anthropic key; Flash when there is only a Gemini key; neither: no call', async () => {
    const log = [];
    const r = await requestChecks({ keys: { anthropic: 'A', gemini: 'G' }, model: 'claude-haiku-5-5', batch, words, fetchImpl: route(log) });
    expect(r.model).toBe('claude-haiku-5-5');
    expect(r.results.a.map((s) => s.f)).toEqual(['でも']);
    expect(r.usage[0]).toMatchObject({ model: 'claude-haiku-5-5', cached: 3000 });
    const flash = await requestChecks({ keys: { gemini: 'G' }, model: 'claude-haiku-5-5', batch, words, fetchImpl: route(log) });
    expect(flash.model).toBe('gemini-3.8-flash');
    expect(log).toEqual(['claude', 'gemini-3.8-flash']);
    await expect(requestChecks({ keys: {}, model: 'claude-haiku-5-5', batch, words, fetchImpl: route(log) })).rejects.toThrow('No key');
    expect(log).toHaveLength(2);
  });
  test('a model cooling down is skipped; when all are, the error says when to retry', async () => {
    const cooldown = new Map([['claude-haiku-5-5', 20000]]);
    const log = [];
    const r = await requestChecks({ keys: { anthropic: 'A', gemini: 'G' }, model: 'claude-haiku-5-5', batch, words, fetchImpl: route(log), cooldown, now: () => 0 });
    expect(r.model).toBe('gemini-3.8-flash');
    expect(log).toEqual(['gemini-3.8-flash']);
    const waiting = await requestChecks({ keys: { anthropic: 'A' }, model: 'claude-haiku-5-5', batch, words, fetchImpl: route(log), cooldown, now: () => 5000 }).catch((e) => e);
    expect(waiting.retryAfterMs).toBe(15000);
  });
  test('a bigger batch gets a longer timeout', async () => {
    const many = Array.from({ length: 16 }, (_, i) => ({ id: `p${i}`, plan }));
    const reply = { ok: true, status: 200, json: async () => ({ candidates: [{ content: { parts: [{ text: JSON.stringify({ passages: many.map(({ id }) => ({ id, picks: '', swaps: [] })) }) }] } }] }) };
    let timeoutMs;
    const original = AbortSignal.timeout;
    AbortSignal.timeout = (ms) => { timeoutMs = ms; return original.call(AbortSignal, ms); };
    try {
      await requestChecks({ keys: { gemini: 'G' }, model: 'gemini-3.8-flash', batch: many, words, fetchImpl: async () => reply });
    } finally { AbortSignal.timeout = original; }
    expect(timeoutMs).toBe(40000);
  });
});
