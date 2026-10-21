import { test, expect } from 'bun:test';
import { hashKey } from '../src/hash.js';
import { localDay } from '../src/hostsource.js';
import { checkAnswer, modelReply, userOf } from './checkmodel.js';

// The web's reading counts in the worker: the reading index comes from the host once per version; a passage the page
// says was read is counted once a day, its safe matches and the model's confirmed picks, from the cached answer or the
// call under way, and only counts go to the host, with the log.
test('background: reading counts, from the host\'s index and the model\'s check, counts only to the host', async () => {
  const saved = Object.fromEntries(['chrome', 'fetch'].map((k) => [k, globalThis[k]]));
  const local = { settings: { enabled: true, levelOverride: 2, model: 'claude-haiku-5-5' }, anthropicKey: 'synthetic-anthropic' };
  const listeners = [];
  const natives = [];
  const opened = () => { let open; const promise = new Promise((r) => { open = r; }); return { promise, open }; };
  let gate = null;
  let calls = 0;
  const listener = (arr) => ({ addListener: (fn) => arr.push(fn) });
  const area = {
    async setAccessLevel() {},
    async get(keys) { return structuredClone(keys == null ? local : Object.fromEntries((typeof keys === 'string' ? [keys] : keys).filter((k) => k in local).map((k) => [k, local[k]]))); },
    async set(values) { for (const [k, v] of Object.entries(values)) local[k] = structuredClone(v); },
    async remove(keys) { for (const k of keys) delete local[k]; },
  };
  const words = [{ ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'known', glosses: ['but'], safe: [], id: 1008460 },
    { ja: '犬', en: 'dog', hint: 'inu', kana: 'いぬ', status: 'known', glosses: ['dog'], safe: ['dog'], id: 1166340 }];
  // the host's index: every swappable entry's safe glosses, in rank order (公園 park, 雨 rain)
  const index = { ok: true, version: 'r1', entries: [[1214000, 'n', 'park'], [1169870, 'n', 'rain']] };
  Object.assign(globalThis, {
    chrome: {
      storage: { local: area },
      runtime: {
        id: 'fw-test', getURL: (path) => `chrome-extension://fw-test/${path}`,
        onMessage: listener(listeners), onInstalled: listener([]), onStartup: listener([]),
        sendNativeMessage: async (_host, msg) => {
          natives.push(msg);
          if (msg.type === 'state') return { ok: true, version: 'w.1', stage: 3, paused: false, prefs: {}, words, reading: { version: 'r1' } };
          if (msg.type === 'readingIndex') return index;
          return { ok: true, counted: msg.passages?.length };
        },
      },
      tabs: { query: async () => [], sendMessage: async () => {} },
      alarms: { create: async () => {}, clear: async () => {}, onAlarm: listener([]) },
      action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {} },
      permissions: { getAll: async () => ({ origins: ['<all_urls>'] }), onAdded: listener([]), onRemoved: listener([]) },
      scripting: { getRegisteredContentScripts: async () => [], updateContentScripts: async () => {}, unregisterContentScripts: async () => {}, registerContentScripts: async () => {} },
    },
    fetch: async (url, init) => {
      calls++;
      const held = gate;
      if (held) await held.promise;
      return modelReply(url, checkAnswer(userOf(url, init))); // the model confirms でも for "but"
    },
  });
  const dispatch = (msg, sender) => new Promise((done) => {
    let handled = false;
    for (const fn of listeners) if (fn(msg, sender, done)) handled = true;
    if (!handled) done(undefined);
  });
  const page = { id: 'fw-test', tab: { id: 1 }, url: 'https://example.com/a' };
  const popup = { id: 'fw-test', url: 'chrome-extension://fw-test/src/popup.html' };
  const until = async (ok) => { for (let i = 0; i < 200 && !ok(); i++) await new Promise((r) => setTimeout(r, 5)); };
  const sent = () => natives.filter((m) => m.type === 'reading').flatMap((m) => m.passages);
  try {
    await import('../src/background.js?readcounts');
    await dispatch({ type: 'syncWords' }, popup);
    expect(natives.filter((m) => m.type === 'readingIndex')).toHaveLength(1);
    await dispatch({ type: 'syncWords' }, popup);
    expect(natives.filter((m) => m.type === 'readingIndex')).toHaveLength(1); // once per version

    // Read before any answer: the call under way is waited for, and the model's でも counts with the safe matches.
    const text = 'The dog ran to the park in the rain, but it was fun anyway.';
    const { revision } = await dispatch({ type: 'config' }, page);
    gate = opened();
    const first = await dispatch({ type: 'swaps', revision, items: [{ key: `${revision}:${hashKey(text)}`, text }] }, page);
    expect(first.waiting).toHaveLength(1);
    await until(() => calls === 1);
    const counting = dispatch({ type: 'read', text }, page);
    await new Promise((r) => setTimeout(r, 20));
    gate.open(); gate = null;
    expect(await counting).toEqual({ ok: true, counted: true });
    expect(calls).toBe(1); // none asked for just to count
    await dispatch({ type: 'syncWords' }, popup); // the queue goes to the host with the log
    expect(sent()).toEqual([{ d: `web:${hashKey(text)}`, day: localDay(), entries: { 1214000: 1, 1169870: 1, 1166340: 1, 1008460: 1 } }]);
    expect(JSON.stringify(natives.filter((m) => m.type === 'reading'))).not.toContain('park in the rain');
    expect(local.pendingReading).toEqual([]);

    // Read again today: counted once. A topic kept in English, a short text, and a page on a site that is off: never.
    expect(await dispatch({ type: 'read', text }, page)).toEqual({ ok: true, counted: false });
    expect(await dispatch({ type: 'read', text: 'My doctor says the medication and rest help the dog.' }, page)).toEqual({ ok: false });
    expect(await dispatch({ type: 'read', text: 'The dog.' }, page)).toEqual({ ok: false });
    expect(await dispatch({ type: 'read', text: 'The dog ran to the park again today.' }, { ...page, url: 'https://mail.google.com/' })).toEqual({ ok: false });
    // nothing a word or the index knows: nothing to count
    expect(await dispatch({ type: 'read', text: 'Nothing here is a word anyone listed for this test.' }, page)).toEqual({ ok: true, counted: false });

    // Without a key: the safe matches alone, no call.
    delete local.anthropicKey;
    const other = 'Another dog sat in the park, but nobody saw it there.';
    expect(await dispatch({ type: 'read', text: other }, page)).toEqual({ ok: true, counted: true });
    expect(local.pendingReading).toEqual([{ d: `web:${hashKey(other)}`, day: localDay(), entries: { 1214000: 1, 1166340: 1 } }]);
    expect(calls).toBe(1);
  } finally {
    Object.assign(globalThis, saved);
  }
}, 30000);
