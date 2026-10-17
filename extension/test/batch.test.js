import { test, expect } from 'bun:test';
import { hashKey } from '../src/hash.js';
import { costUsd } from '../src/usage.js';

// The worker on its own: passages that arrive while a model call runs share the next call, a passage that came back
// meanwhile isn't asked for again, and Claude Haiku is called with the key the native host passed along.
test('background: misses from every tab share one call; Haiku with the host key', async () => {
  const saved = Object.fromEntries(['chrome', 'fetch'].map((k) => [k, globalThis[k]]));
  const local = { settings: { enabled: true, levelOverride: 2 }, apiKey: 'synthetic-gemini' };
  const listeners = [];
  const calls = []; // { url, init, ids }
  let hold = null; // resolves the held model call
  const listener = (arr) => ({ addListener: (fn) => arr.push(fn) });
  const area = {
    async setAccessLevel() {},
    async get(keys) { return structuredClone(keys == null ? local : Object.fromEntries((typeof keys === 'string' ? [keys] : keys).filter((k) => k in local).map((k) => [k, local[k]]))); },
    async set(values) { for (const [k, v] of Object.entries(values)) local[k] = structuredClone(v); },
    async remove(keys) { for (const k of keys) delete local[k]; },
  };
  const words = [{ ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'active' }, { ja: '犬', en: 'dog', hint: 'inu', kana: 'いぬ', status: 'known' }];
  const answer = (ids) => ids.map((id) => ({ id, swaps: [{ k: 'w', o: 'but', f: 'でも', p: '', g: '', c: 'but' }] }));
  Object.assign(globalThis, {
    chrome: {
      storage: { local: area },
      runtime: {
        id: 'fw-test', getURL: (path) => `chrome-extension://fw-test/${path}`,
        onMessage: listener(listeners), onInstalled: listener([]), onStartup: listener([]),
        sendNativeMessage: async (_host, msg) => (msg.type === 'state'
          ? { ok: true, version: 'b.1', stage: 3, paused: false, prefs: {}, words, anthropicKey: 'synthetic-anthropic' } : { ok: true }),
      },
      tabs: { query: async () => [], sendMessage: async () => {} },
      alarms: { create: async () => {}, onAlarm: listener([]) },
      permissions: { getAll: async () => ({ origins: ['<all_urls>'] }), onAdded: listener([]), onRemoved: listener([]) },
      scripting: { getRegisteredContentScripts: async () => [], updateContentScripts: async () => {}, unregisterContentScripts: async () => {}, registerContentScripts: async () => {} },
    },
    fetch: async (url, init) => {
      const body = JSON.parse(init.body);
      const user = url.includes('anthropic') ? body.messages[0].content : body.contents[0].parts[0].text;
      const ids = JSON.parse(user.replace(/^Passages:\n/, '')).map((p) => p.id);
      calls.push({ url, init, body, ids });
      if (calls.length === 1) await new Promise((done) => { hold = done; });
      const text = JSON.stringify({ passages: answer(ids) });
      if (url.includes('anthropic')) {
        return { ok: true, status: 200, json: async () => ({ content: [{ type: 'text', text }], stop_reason: 'end_turn',
          usage: { input_tokens: 300, cache_read_input_tokens: 3000, cache_creation_input_tokens: 200, output_tokens: 400 } }) };
      }
      return { ok: true, status: 200, json: async () => ({ candidates: [{ content: { parts: [{ text }] } }], usageMetadata: { promptTokenCount: 3500, candidatesTokenCount: 500 } }) };
    },
  });
  const dispatch = (msg, sender) => new Promise((done) => {
    let handled = false;
    for (const fn of listeners) if (fn(msg, sender, done)) handled = true;
    if (!handled) done(undefined);
  });
  const tab = (id) => ({ id: 'fw-test', tab: { id }, url: `https://example.com/${id}` });
  const popup = { id: 'fw-test', url: 'chrome-extension://fw-test/src/popup.html' };
  const until = async (ok) => { for (let i = 0; i < 200 && !ok(); i++) await new Promise((r) => setTimeout(r, 5)); };
  try {
    await import('../src/background.js?batch');
    expect((await dispatch({ type: 'syncWords' }, popup)).ok).toBe(true);
    expect(local.hostAnthropicKey).toBe('synthetic-anthropic');
    const { revision } = await dispatch({ type: 'config' }, tab(1));
    const item = (n) => { const text = `Passage ${n} is long enough, but it says very little at all.`; return { key: `${revision}:${hashKey(text)}`, text }; };
    const ask = (id, ns) => dispatch({ type: 'swaps', revision, items: ns.map(item) }, tab(id));

    const first = ask(1, [1]);
    await until(() => hold);
    const second = ask(2, [2, 3]);
    const third = ask(3, [1, 4, 5]); // 1 is in the call that is running
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toHaveLength(1);
    hold();
    const results = await Promise.all([first, second, third]);
    expect(calls).toHaveLength(2);
    expect(calls[1].ids).toHaveLength(4); // 2, 3, 4, 5 together; 1 came from the first call's answer
    for (const r of results) for (const swaps of Object.values(r.results)) expect(swaps.map((s) => s.f)).toEqual(['でも']);
    expect(Object.keys(results[2].results)).toHaveLength(3);

    // Haiku, with the key from ~/.japanese-weave/anthropic_key: thinking off, JSON schema, the prompt in two cached parts.
    await dispatch({ type: 'save', settings: { model: 'claude-haiku-5-5' } }, popup);
    const status = await dispatch({ type: 'status' }, popup);
    expect(status.hostAnthropicKey).toBe(true);
    expect(JSON.stringify(status)).not.toContain('synthetic-');
    const fresh = await dispatch({ type: 'config' }, tab(1));
    const text = 'Another passage, but this one is for Claude to read today.';
    const r = await dispatch({ type: 'swaps', revision: fresh.revision, items: [{ key: `${fresh.revision}:${hashKey(text)}`, text }] }, tab(1));
    expect(Object.values(r.results)[0].map((s) => s.f)).toEqual(['でも']);
    const claude = calls.at(-1);
    expect(claude.url).toBe('https://api.anthropic.com/v1/messages');
    expect(claude.init.headers['x-api-key']).toBe('synthetic-anthropic');
    expect(claude.body).toMatchObject({ model: 'claude-haiku-5-5', thinking: { type: 'disabled' }, output_config: { effort: 'low', format: { type: 'json_schema' } } });
    expect(claude.body.system).toHaveLength(2);
    expect(claude.body.system.every((b) => b.cache_control?.type === 'ephemeral')).toBe(true);
    expect(claude.body.output_config.format.schema.properties.passages.items.additionalProperties).toBe(false);
    const usage = (await dispatch({ type: 'status' }, popup)).usage.today;
    expect(usage.calls).toBe(3);
    // Haiku: 3500 in, of which 3000 read from the cache and 200 written to it
    expect(usage.input).toBe(3 * 3500);
    expect(usage.usd).toBeCloseTo(costUsd('claude-haiku-5-5', 3500, 400, undefined, 3000, 200) + 2 * costUsd('gemini-3.8-flash', 3500, 500), 9);
  } finally {
    Object.assign(globalThis, saved);
  }
}, 30000);
