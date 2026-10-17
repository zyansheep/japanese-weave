import { describe, test, expect } from 'bun:test';
import { buildClaudeRequest, callClaude, closed, API_URL } from '../src/anthropic.js';
import { requestSwaps } from '../src/engine.js';
import { RESPONSE_SCHEMA } from '../src/prompt.js';

const claudeReply = (passages, usage = { input_tokens: 100, cache_read_input_tokens: 3000, cache_creation_input_tokens: 0, output_tokens: 500 }, extra = {}) => ({
  ok: true, status: 200,
  json: async () => ({ content: [{ type: 'text', text: JSON.stringify({ passages }) }], stop_reason: 'end_turn', usage, ...extra }),
});

describe('anthropic', () => {
  test('request: thinking off, low effort, a closed JSON schema, the system prompt in cached parts', () => {
    const body = buildClaudeRequest({ model: 'claude-haiku-5-5', system: ['rules', 'learning'], user: 'U', schema: RESPONSE_SCHEMA });
    expect(body.system).toEqual([{ type: 'text', text: 'rules', cache_control: { type: 'ephemeral' } },
      { type: 'text', text: 'learning', cache_control: { type: 'ephemeral' } }]);
    expect(body.messages).toEqual([{ role: 'user', content: 'U' }]);
    expect(body.thinking).toEqual({ type: 'disabled' });
    expect(body.output_config.effort).toBe('low');
    const schema = body.output_config.format.schema;
    expect(schema.additionalProperties).toBe(false);
    expect(schema.properties.passages.items.properties.swaps.items.additionalProperties).toBe(false);
    expect(closed({ type: 'string' })).toEqual({ type: 'string' });
    expect('temperature' in body).toBe(false);
  });
  test('posts with the key in a header; usage counts cache reads and writes into input', async () => {
    const calls = [];
    const fetchImpl = async (url, init) => { calls.push({ url, init }); return claudeReply([{ id: 'a', swaps: [] }], { input_tokens: 100, cache_read_input_tokens: 3000, cache_creation_input_tokens: 200, output_tokens: 500 }); };
    const out = await callClaude({ fetchImpl, apiKey: 'K', body: { x: 1 } });
    expect(calls[0].url).toBe(API_URL);
    expect(calls[0].init.headers['x-api-key']).toBe('K');
    expect(calls[0].init.headers['anthropic-version']).toBe('2023-06-01');
    expect(out.byId.get('a')).toEqual([]);
    expect(out.usage).toEqual({ input: 3300, cached: 3000, written: 200, output: 500 });
  });
  test('errors: retry-after, the API message, a refusal or a cut-off answer is billed and thrown', async () => {
    const limited = await callClaude({ apiKey: 'K', body: {}, fetchImpl: async () => ({ ok: false, status: 429, headers: new Headers({ 'retry-after': '12' }),
      text: async () => '{"type":"error","error":{"type":"rate_limit_error","message":"Number of requests has exceeded your rate limit"}}' }) }).catch((e) => e);
    expect(limited.status).toBe(429);
    expect(limited.retryAfterMs).toBe(12000);
    expect(limited.message).toContain('rate limit');
    const cut = await callClaude({ apiKey: 'K', body: {}, fetchImpl: async () => claudeReply([], undefined, { stop_reason: 'max_tokens' }) }).catch((e) => e);
    expect(cut.message).toContain('max_tokens');
    expect(cut.usage.output).toBe(500);
  });
});

describe('engine with two providers', () => {
  const batch = [{ id: 'a', text: 'x but y and more words here' }];
  const swaps = [{ k: 'w', o: 'but', f: 'でも', p: '', g: '', c: 'x but y' }];
  const route = (log) => async (url) => {
    log.push(url.includes('anthropic') ? 'claude' : url.split('/').pop().split(':')[0]);
    return url.includes('anthropic') ? claudeReply([{ id: 'a', swaps }])
      : { ok: true, status: 200, json: async () => ({ candidates: [{ content: { parts: [{ text: JSON.stringify({ passages: [{ id: 'a', swaps }] }) }] } }], usageMetadata: {} }) };
  };
  test('Haiku with an Anthropic key; Flash when there is only a Gemini key; neither: no call', async () => {
    const log = [];
    const r = await requestSwaps({ keys: { anthropic: 'A', gemini: 'G' }, model: 'claude-haiku-5-5', level: 1, batch, fetchImpl: route(log) });
    expect(r.model).toBe('claude-haiku-5-5');
    expect(r.results.a.map((s) => s.f)).toEqual(['でも']);
    expect(r.usage[0]).toMatchObject({ model: 'claude-haiku-5-5', cached: 3000 });
    const flash = await requestSwaps({ keys: { gemini: 'G' }, model: 'claude-haiku-5-5', level: 1, batch, fetchImpl: route(log) });
    expect(flash.model).toBe('gemini-3.8-flash');
    expect(log).toEqual(['claude', 'gemini-3.8-flash']);
    await expect(requestSwaps({ keys: {}, model: 'claude-haiku-5-5', level: 1, batch, fetchImpl: route(log) })).rejects.toThrow('No key');
    expect(log).toHaveLength(2);
  });
  test('a failed Haiku call falls back to Flash; 529 overloaded cools Haiku down', async () => {
    const log = [];
    const cooldown = new Map();
    const fetchImpl = async (url, init) => (url.includes('anthropic') ? (log.push('claude'), { ok: false, status: 529, text: async () => '' }) : route(log)(url, init));
    const r = await requestSwaps({ keys: { anthropic: 'A', gemini: 'G' }, model: 'claude-haiku-5-5', level: 1, batch, fetchImpl, cooldown, now: () => 0 });
    expect(r.model).toBe('gemini-3.8-flash');
    expect(log).toEqual(['claude', 'gemini-3.8-flash']);
    expect(cooldown.get('claude-haiku-5-5')).toBe(30000);
  });
  test('a bigger batch gets a longer timeout', async () => {
    const limits = [];
    const fetchImpl = async (url, init) => { limits.push(init.signal); return route([])(url, init); };
    const many = Array.from({ length: 16 }, (_, i) => ({ id: `p${i}`, text: 'x but y' }));
    const reply = { ok: true, status: 200, json: async () => ({ candidates: [{ content: { parts: [{ text: JSON.stringify({ passages: many.map(({ id }) => ({ id, swaps: [] })) }) }] } }] }) };
    let timeoutMs;
    const original = AbortSignal.timeout;
    AbortSignal.timeout = (ms) => { timeoutMs = ms; return original.call(AbortSignal, ms); };
    try {
      await requestSwaps({ keys: { gemini: 'G' }, model: 'gemini-3.8-flash', level: 1, batch: many, fetchImpl: async () => reply });
    } finally { AbortSignal.timeout = original; }
    expect(timeoutMs).toBe(40000);
  });
});
