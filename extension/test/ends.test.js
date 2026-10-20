import { test, expect } from 'bun:test';
import { hashKey } from '../src/hash.js';
import { ICONS, WAIT_FRAMES } from '../src/toolbar.js';

// The worker at the ends of the intensity scale, and the toolbar button it draws per tab. Full: its own cache, a prompt
// for everything, every candidate shown. Off: no swaps, no model call (not even one already queued), nothing logged,
// every tab grey with "off"; a lighter press there keeps it off for 3 more hours, a heavier one brings Japanese back.
test('background: full and off, and the toolbar per tab (waiting, reasons, navigation, close)', async () => {
  const saved = Object.fromEntries(['chrome', 'fetch'].map((k) => [k, globalThis[k]]));
  const local = { settings: { enabled: true, levelOverride: 2 }, apiKey: 'synthetic-gemini' };
  const listeners = [];
  const tabEvents = { activated: [], updated: [], removed: [] };
  const calls = [];
  const natives = [];
  const toasts = [];
  const button = new Map(); // tab id or 'default' -> { icon, title, text, color }
  const shown = (id) => ({ ...button.get('default'), ...button.get(id) });
  const opened = () => { let open; const promise = new Promise((r) => { open = r; }); return { promise, open }; };
  let gate = null;
  const alarms = {};
  const alarmEvents = [];
  let hostPrefs = {};
  const listener = (arr) => ({ addListener: (fn) => arr.push(fn) });
  const area = {
    async setAccessLevel() {},
    async get(keys) { return structuredClone(keys == null ? local : Object.fromEntries((typeof keys === 'string' ? [keys] : keys).filter((k) => k in local).map((k) => [k, local[k]]))); },
    async set(values) { for (const [k, v] of Object.entries(values)) local[k] = structuredClone(v); },
    async remove(keys) { for (const k of keys) delete local[k]; },
  };
  const setter = (prop, field) => async (arg) => { const k = arg.tabId ?? 'default'; button.set(k, { ...button.get(k), [prop]: arg[field] }); };
  const words = [
    { ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'known' },
    { ja: '犬', en: 'dog', hint: 'inu', kana: 'いぬ', status: 'active' },
    { ja: '大きい', en: 'big', hint: 'ōkii', kana: 'おおきい', status: 'known' },
    { ja: '日', en: 'day', hint: 'hi', kana: 'ひ', status: 'known' },
  ];
  // Every candidate the passage has: a sentence, and words, one of them twice.
  const answer = (ids) => ids.map((id) => ({ id, swaps: [
    { k: 's', o: 'It was a big day.', f: '大きい日でした。', p: '大きい|日|でした|。', g: 'It was a big day.', c: 'It was a big day.' },
    { k: 'w', o: 'But', f: 'でも', p: '', g: '', c: 'But the dog ran' },
    { k: 'w', o: 'dog', f: '犬', p: '', g: '', c: 'the dog ran' },
    { k: 'w', o: 'dog', f: '犬', p: '', g: '', c: 'the dog slept' },
    { k: 'w', o: 'big', f: '大きい', p: '', g: '', c: 'a big day' },
  ] }));
  const tabs = [{ id: 1, url: 'https://example.com/1', active: true }, { id: 2, url: 'https://mail.google.com/mail/u/0/' }, { id: 3 }];
  Object.assign(globalThis, {
    chrome: {
      storage: { local: area },
      runtime: {
        id: 'fw-test', getURL: (path) => `chrome-extension://fw-test/${path}`,
        onMessage: listener(listeners), onInstalled: listener([]), onStartup: listener([]),
        sendNativeMessage: async (_host, msg) => (natives.push(msg), msg.type === 'state'
          ? { ok: true, version: 'e.1', stage: 3, paused: false, prefs: hostPrefs, words } : { ok: true }),
      },
      tabs: {
        query: async (q) => (q?.active ? tabs.filter((t) => t.active) : tabs), get: async (id) => tabs.find((t) => t.id === id),
        sendMessage: async (id, msg) => { if (msg.type === 'intensity') toasts.push([id, msg.intensity]); },
        onActivated: listener(tabEvents.activated), onUpdated: listener(tabEvents.updated), onRemoved: listener(tabEvents.removed),
      },
      alarms: { create: async (name, info) => { alarms[name] = info.when; }, clear: async (name) => { delete alarms[name]; }, onAlarm: listener(alarmEvents) },
      action: { setIcon: setter('icon', 'path'), setTitle: setter('title', 'title'), setBadgeText: setter('text', 'text'),
        setBadgeBackgroundColor: setter('color', 'color') },
      permissions: { getAll: async () => ({ origins: ['<all_urls>'] }), onAdded: listener([]), onRemoved: listener([]) },
      scripting: { getRegisteredContentScripts: async () => [], updateContentScripts: async () => {}, unregisterContentScripts: async () => {}, registerContentScripts: async () => {} },
    },
    fetch: async (url, init) => {
      const body = JSON.parse(init.body);
      const passages = JSON.parse(body.contents[0].parts[0].text.replace(/^Passages:\n/, ''));
      calls.push({ body, passages });
      const held = gate;
      if (held) await held.promise;
      const text = JSON.stringify({ passages: answer(passages.map((p) => p.id)) });
      return { ok: true, status: 200, json: async () => ({ candidates: [{ content: { parts: [{ text }] } }], usageMetadata: { promptTokenCount: 3000, candidatesTokenCount: 400 } }) };
    },
  });
  const dispatch = (msg, sender) => new Promise((done) => {
    let handled = false;
    for (const fn of listeners) if (fn(msg, sender, done)) handled = true;
    if (!handled) done(undefined);
  });
  const tab = (id) => ({ id: 'fw-test', tab: { id }, url: tabs.find((t) => t.id === id)?.url || `https://example.com/${id}` });
  const popup = { id: 'fw-test', url: 'chrome-extension://fw-test/src/popup.html' };
  const until = async (ok) => { for (let i = 0; i < 200 && !ok(); i++) await new Promise((r) => setTimeout(r, 5)); };
  const settle = () => new Promise((r) => setTimeout(r, 20));
  const passage = (n = '') => `It was a big day. But the dog ran home, and the dog slept${n}.`;
  const ask = async (text, id = 1) => {
    const cfg = await dispatch({ type: 'config' }, tab(id));
    return dispatch({ type: 'swaps', revision: cfg.revision, items: [{ key: `${cfg.revision}:${hashKey(text)}`, text }] }, tab(id));
  };
  const press = async (step, times = 1) => { let r; for (let i = 0; i < times; i++) r = await dispatch({ type: 'intensity', step }, popup); return r; };
  try {
    await import('../src/background.js?ends');
    expect((await dispatch({ type: 'syncWords' }, popup)).ok).toBe(true);

    // Normal: each tab's button says whether its page gets Japanese, and why not.
    expect(shown(1)).toMatchObject({ icon: ICONS.on, title: 'Japanese Weave', text: '' });
    expect(shown(2)).toMatchObject({ icon: ICONS.off, title: expect.stringContaining('private sites') });
    expect(shown(3)).toMatchObject({ icon: ICONS.off, title: "Japanese Weave: can't run on this page" });
    // While a passage waits for the model, its tab says so; the answer brings the level back.
    gate = opened();
    const normal = ask(passage());
    await until(() => calls.length === 1);
    await settle();
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: WAIT_FRAMES[0], title: expect.stringContaining('waiting for the model') });
    expect(shown(2).text).toBe('');
    gate.open(); gate = null;
    const plain = Object.values((await normal).plans)[0];
    expect(plain).toMatchObject({ level: 0, candidates: 3 }); // one use per word: the sentence's words aren't swapped again
    await settle();
    expect(shown(1)).toMatchObject({ text: '', title: 'Japanese Weave' });
    // A page with nothing to weave (its content script says so once the page settles) is faded and says which. Only a
    // tab's top frame is heard, and only the known states.
    expect(await dispatch({ type: 'nothing', why: 'none' }, tab(1))).toEqual({ ok: true });
    expect(shown(1)).toMatchObject({ icon: ICONS.faded, text: '', title: 'Japanese Weave: nothing on this page to weave (no English paragraphs, or only topics kept in English)' });
    expect(await dispatch({ type: 'nothing', why: 'none' }, { ...tab(3), frameId: 4 })).toEqual({ ok: false });
    expect(await dispatch({ type: 'nothing', why: 'blank' }, tab(1))).toEqual({ ok: false });
    expect(await dispatch({ type: 'nothing', why: 'unfit' }, tab(1))).toEqual({ ok: true });
    expect(shown(1).title).toBe('Japanese Weave: no Japanese fit the text on this page');
    expect(shown(3).icon).toBe(ICONS.off);

    // Full: three presses up; its own cache and prompt, and every candidate shown.
    expect((await press(1, 3)).intensity.level).toBe(3);
    expect(natives.filter((m) => m.type === 'intensity').map((m) => m.set.level)).toEqual([1, 2, 3]);
    expect(toasts.at(-1)).toEqual([1, expect.objectContaining({ level: 3 })]);
    expect(shown(1)).toMatchObject({ icon: ICONS.faded, text: 'full', title: expect.stringContaining('full until') }); // the level shows
    expect(shown(1).title).toContain('no Japanese fit');
    const full = await ask(passage());
    expect(calls).toHaveLength(2);
    expect(calls[1].body.systemInstruction.parts[0].text).toContain('The reader asked for FULL Japanese');
    expect(calls[1].passages[0].target).toBeUndefined();
    expect(calls[0].passages[0].target).toBe(4);
    const [swaps] = Object.values(full.results);
    // no budget at full; expected: the lookups predicted, 4 known words without estimates at 0.076 and the dog twice at 0.269
    expect(Object.values(full.plans)[0]).toEqual({ level: 3, intensity: 5, jitter: 1, limit: 5, candidates: 5, expected: 0.841 });
    expect(swaps.map((s) => s.f)).toEqual(['大きい日でした。', 'でも', '犬', '犬', '大きい']);
    // Heavier at full stays full, for 3 hours from this press.
    const first = local.intensity.until;
    await new Promise((r) => setTimeout(r, 5));
    expect((await press(1)).intensity.level).toBe(3);
    expect(local.intensity.until).toBeGreaterThan(first);

    // A passage queued behind a running call, then off: the queued one is never sent.
    await press(0);
    gate = opened();
    const running = ask(passage(' again'));
    await until(() => calls.length === 3);
    const queued = ask(passage(' once more'), 1);
    await settle();
    expect((await press(-1, 3)).intensity.level).toBe(-3);
    gate.open(); gate = null;
    expect((await queued).stale).toBe(true);
    await running;
    expect(calls).toHaveLength(3);
    await settle();
    // Off: grey everywhere with "off", nothing woven, called or logged.
    for (const id of [1, 2, 3, 'default']) expect(shown(id)).toMatchObject({ icon: ICONS.off, text: 'off' });
    expect(shown(1).title).toContain('off until');
    expect(shown(1).title).toContain('Alt+Shift+Period');
    const off = await dispatch({ type: 'config' }, tab(1));
    expect(off.intensity.level).toBe(-3);
    expect(await ask(passage())).toEqual({ results: {}, stale: true });
    expect(await ask(passage(' never asked'))).toEqual({ results: {}, stale: true });
    expect(calls).toHaveLength(3);
    expect(await dispatch({ type: 'event', word: '犬', event: 'seen' }, tab(1))).toEqual({ ok: false });
    const record = { type: 'interaction', kind: 'shown', at: Date.now(), view: 'v1', context: 'c1', words: 12, box: [0, 0, 1, 1], annotations: [] };
    expect(await dispatch(record, tab(1))).toEqual({ ok: false });
    expect(local.pendingLog || []).toHaveLength(0);
    // Lighter at off stays off and restarts the clock; heavier brings Japanese back, from the cache.
    const offUntil = local.intensity.until;
    await new Promise((r) => setTimeout(r, 5));
    expect((await press(-1)).intensity.level).toBe(-3);
    expect(local.intensity.until).toBeGreaterThan(offUntil);
    expect((await press(1)).intensity.level).toBe(-2);
    const back = await ask(passage());
    expect(back.stale).toBeUndefined();
    expect(Object.values(back.plans)[0]).toMatchObject({ level: -2, candidates: 3 });
    expect(calls).toHaveLength(3);
    await settle();
    expect(shown(1)).toMatchObject({ icon: ICONS.faded, text: '−2' });
    await dispatch({ type: 'nothing', why: '' }, tab(1)); // Japanese turned up
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: '−2' });
    expect(shown(2)).toMatchObject({ icon: ICONS.off, text: '−2' });
    expect(shown(3)).toMatchObject({ icon: ICONS.off, text: '−2' });

    // A wait ends when the tab loads another page, or closes, before the answer is in; so does nothing to weave.
    await press(0);
    await dispatch({ type: 'nothing', why: 'none' }, tab(1));
    gate = opened();
    const left = ask(passage(' left behind'));
    const closing = ask(passage(' closing'), 2);
    await until(() => calls.length === 4);
    await settle();
    expect(shown(1).text).toBe(WAIT_FRAMES[0]);
    expect(shown(2).icon).toBe(ICONS.off); // a private site: no wait shows (its request was stale anyway)
    tabs[0].url = 'https://example.org/next';
    for (const fn of tabEvents.updated) fn(1, { status: 'loading' }, tabs[0]);
    await settle();
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: '' });
    for (const fn of tabEvents.updated) fn(1, { title: 'Next' }, tabs[0]); // nothing to redraw
    for (const fn of tabEvents.removed) fn(2);
    tabs.splice(1, 1);
    gate.open(); gate = null;
    await Promise.all([left, closing]);
    await settle();
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: '' });
    // Switching to a tab redraws it from what it shows now.
    local.settings = { ...local.settings, siteOverrides: { 'example.org': false } };
    for (const fn of tabEvents.activated) fn({ tabId: 1 });
    await settle();
    expect(shown(1)).toMatchObject({ icon: ICONS.off, title: 'Japanese Weave: turned off on this site' });

    // Off set on another machine arrives with the word state, and its end is armed here too.
    expect(alarms.intensity).toBeUndefined(); // back at normal: no end to wait for
    const elsewhere = { level: -3, until: Date.now() + 3600000, at: Date.now() + 1000, source: 'shortcut' };
    hostPrefs = { intensity_browser: elsewhere };
    await dispatch({ type: 'syncWords' }, popup);
    expect((await dispatch({ type: 'config' }, tab(1))).intensity).toEqual({ level: -3, until: elsewhere.until });
    expect(alarms.intensity).toBe(elsewhere.until);
    expect(shown(1)).toMatchObject({ icon: ICONS.off, text: 'off' });
    hostPrefs = { intensity_browser: { ...elsewhere, until: Date.now() - 1 } }; // it ran out
    await dispatch({ type: 'syncWords' }, popup);
    for (const fn of alarmEvents) fn({ name: 'intensity' });
    await settle();
    expect(shown(1)).toMatchObject({ icon: ICONS.off, text: '', title: 'Japanese Weave: turned off on this site' });
  } finally {
    Object.assign(globalThis, saved);
  }
}, 30000);
