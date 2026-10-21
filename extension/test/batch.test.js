import { test, expect } from 'bun:test';
import { hashKey } from '../src/hash.js';
import { costUsd } from '../src/usage.js';
import { buildCheckSystem } from '../src/verify.js';
import { passagesOf, checkAnswer, modelReply, userOf } from './checkmodel.js';

// The worker on its own, with the web pipeline: the swaps code is sure of come at once and the model's check after;
// passages that arrive while a call runs share the next call, a passage in a call isn't asked for again, and Claude
// Haiku is called with the key the native host passed along, in smaller calls that run at the same time. One answer
// serves every level; answers are cached by what was asked, so a new word list asks again only where it changed.
test('background: sure swaps at once, then the check; shared calls; Haiku with the host key; one answer for every level', async () => {
  const saved = Object.fromEntries(['chrome', 'fetch'].map((k) => [k, globalThis[k]]));
  const local = { settings: { enabled: true, levelOverride: 2 }, apiKey: 'synthetic-gemini' };
  const listeners = [];
  const calls = []; // { url, body, ids, user }
  const natives = []; // messages to the native host
  const opened = () => { let open; const promise = new Promise((r) => { open = r; }); return { promise, open }; };
  let gate = null; // while set, model calls wait until it opens
  let failing = 0; // calls that fail before an answer
  const listener = (arr) => ({ addListener: (fn) => arr.push(fn) });
  const area = {
    async setAccessLevel() {},
    async get(keys) { return structuredClone(keys == null ? local : Object.fromEntries((typeof keys === 'string' ? [keys] : keys).filter((k) => k in local).map((k) => [k, local[k]]))); },
    async set(values) { for (const [k, v] of Object.entries(values)) local[k] = structuredClone(v); },
    async remove(keys) { for (const k of keys) delete local[k]; },
  };
  // でも for "but" is closed-class English: always the model's to check. 犬 for "dog" is safe: code takes it.
  let words = [{ ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'known', glosses: ['but'], safe: [], id: 1008460 },
    { ja: '犬', en: 'dog', hint: 'inu', kana: 'いぬ', status: 'known', glosses: ['dog'], safe: ['dog'], id: 1166340 }];
  let version = 'b.1';
  Object.assign(globalThis, {
    chrome: {
      storage: { local: area },
      runtime: {
        id: 'fw-test', getURL: (path) => `chrome-extension://fw-test/${path}`,
        onMessage: listener(listeners), onInstalled: listener([]), onStartup: listener([]),
        sendNativeMessage: async (_host, msg) => (natives.push(msg), msg.type === 'state'
          ? { ok: true, version, stage: 3, paused: false, prefs: {}, words, anthropicKey: 'synthetic-anthropic' } : { ok: true }),
      },
      tabs: { query: async () => [], sendMessage: async () => {} },
      alarms: { create: async () => {}, clear: async () => {}, onAlarm: listener([]) },
      action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {} },
      permissions: { getAll: async () => ({ origins: ['<all_urls>'] }), onAdded: listener([]), onRemoved: listener([]) },
      scripting: { getRegisteredContentScripts: async () => [], updateContentScripts: async () => {}, unregisterContentScripts: async () => {}, registerContentScripts: async () => {} },
    },
    fetch: async (url, init) => {
      const user = userOf(url, init);
      calls.push({ url, init, body: JSON.parse(init.body), user, ids: passagesOf(user).map((p) => p.id) });
      const held = gate;
      if (held) await held.promise;
      if (failing > 0) { failing--; return { ok: false, status: 500, text: async () => '' }; }
      return modelReply(url, checkAnswer(user));
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
  const settle = () => new Promise((r) => setTimeout(r, 30));
  const text = (n) => `Passage ${n} has a dog in it, but it says very little at all.`;
  try {
    await import('../src/background.js?batch');
    expect((await dispatch({ type: 'syncWords' }, popup)).ok).toBe(true);
    expect(local.hostAnthropicKey).toBe('synthetic-anthropic');
    let { revision } = await dispatch({ type: 'config' }, tab(1));
    const item = (n) => ({ key: `${revision}:${hashKey(text(n))}`, text: text(n) });
    const ask = (id, ns, wait = true) => dispatch({ type: 'swaps', revision, items: ns.map(item), wait }, tab(id));
    // what a passage's candidates are (planSwaps' plan counts them; which it shows depends on the day's thinning)
    const cands = (r, n) => r.plans[item(n).key].candidates;
    const offered = (r, n) => (r.results[item(n).key].every((s) => ['でも', '犬', 'とても'].includes(s.f)) ? cands(r, n) : -1);

    // At once: the dog, which code is sure of; the check of "but" starts, and the page is told to ask again for it.
    gate = opened();
    const now = await ask(1, [0], false);
    expect(now.waiting).toEqual([item(0).key]);
    expect(offered(now, 0)).toBe(1); // the dog
    expect(now.results[item(0).key].every((s) => s.f === '犬')).toBe(true);
    await until(() => calls.length === 1);
    // asking again waits for that call, which isn't made twice
    const later = ask(1, [0]);
    await settle();
    expect(calls).toHaveLength(1);
    gate.open(); gate = null;
    const full = await later;
    expect(full.waiting).toBeUndefined();
    expect(offered(full, 0)).toBe(2); // and でも for "but"
    // cached: a later page gets both at once, no call
    expect(offered(await ask(2, [0], false), 0)).toBe(2);
    expect(calls).toHaveLength(1);

    // Passages from every tab wait for the running call and share the next one; one already in a call isn't asked twice.
    gate = opened();
    const first = ask(1, [1]);
    await until(() => calls.length === 2);
    const second = ask(2, [2, 3]);
    const third = ask(3, [1, 4, 5]); // 1 is in the call that is running
    await settle();
    expect(calls).toHaveLength(2);
    gate.open(); gate = null;
    const results = await Promise.all([first, second, third]);
    expect(calls).toHaveLength(3);
    expect(calls[2].ids).toHaveLength(4); // 2, 3, 4, 5 together; 1 came from the first call's answer
    for (const r of results) for (const plan of Object.values(r.plans)) expect(plan.candidates).toBe(2);
    // the check prompt, and only the doubtful span lettered: the dog is given as sure
    expect(calls[2].body.systemInstruction.parts[0].text).toBe(buildCheckSystem());
    expect(calls[2].user).toContain('[but] it says very: a でも = but');
    expect(calls[2].user).toContain('[dog] in it, but = 犬');

    // Haiku, with the key from ~/.japanese-weave/anthropic_key: light thinking, JSON schema, the prompt cached.
    await dispatch({ type: 'save', settings: { model: 'claude-haiku-5-5' } }, popup);
    const status = await dispatch({ type: 'status' }, popup);
    expect(status.hostAnthropicKey).toBe(true);
    expect(JSON.stringify(status)).not.toContain('synthetic-');
    ({ revision } = await dispatch({ type: 'config' }, tab(1)));
    expect(offered(await ask(1, [6]), 6)).toBe(2);
    const claude = calls.at(-1);
    expect(claude.url).toBe('https://api.anthropic.com/v1/messages');
    expect(claude.init.headers['x-api-key']).toBe('synthetic-anthropic');
    expect(claude.body).toMatchObject({ model: 'claude-haiku-5-5', thinking: { type: 'adaptive' }, output_config: { effort: 'low', format: { type: 'json_schema' } } });
    expect(claude.body.system).toEqual([{ type: 'text', text: buildCheckSystem(), cache_control: { type: 'ephemeral' } }]);
    const usage = (await dispatch({ type: 'status' }, popup)).usage.today;
    expect(usage.calls).toBe(4);
    expect(usage.input).toBe(4 * 3500);
    expect(usage.usd).toBeCloseTo(costUsd('claude-haiku-5-5', 3500, 400, undefined, 3000, 200) + 3 * costUsd('gemini-3.8-flash', 3500, 400), 9);

    // 14 passages from one tab go to Haiku as calls of 6, 6 and 2, all three out before any answer comes back.
    gate = opened();
    const before = calls.length;
    const many = ask(2, Array.from({ length: 14 }, (_, n) => 100 + n));
    await until(() => calls.length === before + 3);
    await settle();
    expect(calls.slice(before).map((c) => c.ids.length)).toEqual([6, 6, 2]);
    gate.open(); gate = null;
    expect(Object.keys((await many).results)).toHaveLength(14);

    // Every level picks from the same answer: no call when stepping lighter, heavier or to full; full shows every
    // candidate. The press goes to the host for the log.
    const made = calls.length;
    for (const [step, level] of [[-1, -1], [-1, -2], [1, -1], [1, 0], [1, 1], [1, 2], [1, 3]]) {
      expect((await dispatch({ type: 'intensity', step }, popup)).intensity.level).toBe(level);
      ({ revision } = await dispatch({ type: 'config' }, tab(1)));
      const r = await ask(1, [6], false);
      expect(r.waiting).toBeUndefined();
      expect(r.plans[item(6).key]).toMatchObject({ level, candidates: 2 });
      if (level === 3) expect(r.results[item(6).key].map((s) => s.f).sort()).toEqual(['でも', '犬']); // full: every candidate
    }
    expect(natives.filter((m) => m.type === 'intensity').map((m) => m.set.level)).toEqual([-1, -2, -1, 0, 1, 2, 3]);
    expect(calls.length).toBe(made);
    expect(await dispatch({ type: 'intensity', step: 2 }, popup)).toEqual({ error: 'step is 1, -1 or 0' });
    expect(await dispatch({ type: 'intensity', step: 1 }, tab(1))).toBe(undefined); // pages can't change it
    await dispatch({ type: 'intensity', step: 0 }, popup);

    // A new word list: passages whose doubtful spans are the same keep their answer; one with a new doubtful span is
    // asked again. Here "very" gets a word: every passage above says it, so each is asked again, and only those.
    version = 'b.2';
    words = [...words, { ja: 'とても', en: 'very', hint: 'totemo', kana: 'とても', status: 'known', glosses: ['very'], safe: [], id: 1008630 }];
    await dispatch({ type: 'syncWords' }, popup);
    ({ revision } = await dispatch({ type: 'config' }, tab(1)));
    const asked = calls.length;
    expect(offered(await ask(1, [6]), 6)).toBe(3);
    expect(calls.length).toBe(asked + 1);
    version = 'b.3';
    words = words.map((w) => (w.ja === '犬' ? { ...w, status: 'active' } : w)); // a new label: nothing new to ask
    await dispatch({ type: 'syncWords' }, popup);
    ({ revision } = await dispatch({ type: 'config' }, tab(1)));
    expect((await ask(1, [6], false)).waiting).toBeUndefined();
    expect(calls.length).toBe(asked + 1);

    // A failed call: the wait says so, and the sure swaps are what the page keeps; the next ask tries again.
    failing = 2; // Haiku, then its fallback Flash
    const fail = await ask(1, [200]);
    expect(fail.error).toContain('HTTP 500');
    expect(fail.results[item(200).key]).toBeUndefined();
    expect(offered(await ask(1, [200], false), 200)).toBe(1);
    expect(offered(await ask(1, [200]), 200)).toBe(3);

    // Without a key: the sure swaps, at once and final; no call.
    delete local.hostAnthropicKey;
    delete local.apiKey;
    const cfg = await dispatch({ type: 'config' }, tab(1));
    expect(cfg.hasKey).toBe(false);
    revision = cfg.revision;
    const none = calls.length;
    const bare = await ask(1, [300], false);
    expect(bare.waiting).toBeUndefined();
    expect(offered(bare, 300)).toBe(1);
    expect(calls.length).toBe(none);
  } finally {
    Object.assign(globalThis, saved);
  }
}, 30000);
