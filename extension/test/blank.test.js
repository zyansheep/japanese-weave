import { test, expect } from 'bun:test';
import { Window } from 'happy-dom';
import { hashKey } from '../src/hash.js';
import { planSwaps } from '../src/levels.js';
import { cleanInteraction } from '../src/hostsource.js';

// Passages the background planned but left without Japanese (no candidates, or thinned to none) are logged as shown
// and on screen like glossed ones, the baseline calibration compares against; clicks and selections in them are not.
// Passages the topic guard keeps out, or that came back without a plan, leave no trace.
test('blank passages: a shown record and stays when planned, nothing when guarded or unplanned', async () => {
  const win = new Window({ url: 'https://example.com/blog' });
  const keys = ['chrome', 'document', 'window', 'location', 'MutationObserver', 'IntersectionObserver', 'setInterval'];
  const saved = Object.fromEntries(keys.map((k) => [k, globalThis[k]]));
  const io = [];
  class IO {
    constructor(cb, options) { this.cb = cb; this.options = options; this.els = new Set(); io.push(this); }
    observe(el) { this.els.add(el); if (this.options.rootMargin) queueMicrotask(() => { if (this.els.has(el)) this.cb([{ target: el, isIntersecting: true, intersectionRatio: 1 }]); }); }
    unobserve(el) { this.els.delete(el); }
    disconnect() { this.els.clear(); }
  }
  const config = { settings: { enabled: true, newSitesEnabled: true, siteOverrides: {} }, level: 1, revision: 'r1', hasKey: true,
    paused: false, origins: ['<all_urls>'], stage: 1 };
  const texts = {
    glossed: 'I enjoy cats and dogs every single day here.',
    none: 'We walked by the river all morning long.', // no candidates
    thinned: 'Our old cat sleeps by the warm window most afternoons.', // one candidate, thinned to none
    unplanned: 'The bus came late again this cold morning.', // a reply without a plan
    guarded: 'My doctor says medication and rest are important now.', // the topic guard: never sent
  };
  const plan = (jitter, limit, candidates) => ({ level: 0, intensity: 1, jitter, limit, candidates });
  const answers = {
    glossed: [[{ o: 'and', f: 'et', g: 'and', h: 'ay', ctx: '', word: 'et' }], plan(1, 1, 1)],
    none: [[], plan(1, 0, 0)],
    thinned: [[], plan(0.6, 0, 1)],
    unplanned: [[], null],
  };
  const name = (text) => Object.keys(texts).find((k) => texts[k] === text);
  const sent = [];
  const asked = [];
  const runtime = {
    id: 'fw-test',
    onMessage: { addListener: () => {} },
    sendMessage: async (msg) => {
      sent.push(msg);
      if (msg.type === 'config') return config;
      if (msg.type === 'swaps') {
        const results = {}, plans = {};
        for (const i of msg.items) {
          asked.push(name(i.text));
          const [swaps, p] = answers[name(i.text)];
          results[i.key] = swaps;
          if (p) plans[i.key] = p;
        }
        return { results, plans };
      }
      return { ok: true };
    },
  };
  Object.assign(globalThis, { window: win, document: win.document, location: win.location, MutationObserver: win.MutationObserver,
    IntersectionObserver: IO, setInterval: win.setInterval.bind(win), chrome: { runtime } });
  const wait = (ms = 400) => new Promise((done) => setTimeout(done, ms));
  const user = (e) => Object.defineProperty(e, 'isTrusted', { value: true });
  const logged = (kind) => sent.filter((m) => m.type === 'interaction' && m.kind === kind);
  const of = (records) => records.map((m) => Object.keys(texts).find((k) => hashKey(texts[k]) === m.context));
  try {
    win.document.body.innerHTML = `<main>${Object.entries(texts).map(([k, t]) => `<p id="${k}">${t}</p>`).join('')}</main>`;
    await import('../src/content.js?blank');
    await wait();
    expect(asked.sort()).toEqual(['glossed', 'none', 'thinned', 'unplanned']);
    const p = (k) => win.document.querySelector(`#${k}`);
    expect(p('glossed').querySelector('.fw-word')?.textContent).toBe('et');
    expect(win.document.querySelectorAll('.fw-word')).toHaveLength(1);

    const onscreen = io.findLast((o) => o.options.threshold?.length === 11);
    const sight = (k, on) => onscreen.cb([{ target: p(k), isIntersecting: on, intersectionRatio: on ? 1 : 0,
      intersectionRect: { height: on ? 20 : 0 }, rootBounds: { height: 800 } }]);
    win.document.dispatchEvent(user(new win.MouseEvent('pointermove', { bubbles: true })));
    for (const k of Object.keys(texts)) sight(k, true);
    await wait(20);
    // Shown when enough of it is on screen; a glossed passage still waits for its Japanese to be seen.
    const shown = logged('shown');
    expect(of(shown)).toEqual(['none', 'thinned']);
    expect(shown[0]).toMatchObject({ words: 8, annotations: [], plan: plan(1, 0, 0) });
    expect(shown[1]).toMatchObject({ words: 10, annotations: [], plan: plan(0.6, 0, 1) });
    expect(shown.every((m) => m.box.length === 4)).toBe(true);
    const visibility = io.findLast((o) => !o.options.rootMargin && o.options.threshold.length === 2);
    visibility.cb([{ target: p('glossed').querySelector('.fw-word'), isIntersecting: true, intersectionRatio: 1 }]);
    await wait(20);
    expect(of(logged('shown'))).toEqual(['none', 'thinned', 'glossed']);

    // Clicks and selections in a blank passage say nothing about Japanese and are not logged.
    p('none').dispatchEvent(user(new win.MouseEvent('click', { bubbles: true, clientX: 5, clientY: 5 })));
    const range = win.document.createRange();
    range.selectNodeContents(p('none'));
    win.document.getSelection().addRange(range);
    p('none').dispatchEvent(user(new win.MouseEvent('mouseup', { bubbles: true })));
    await wait(20);
    win.document.getSelection().removeAllRanges();
    expect(logged('click').length + logged('select').length).toBe(0);

    // Their stays are sent like a glossed passage's, and coming back on screen is no second shown record.
    await wait(1300);
    for (const k of Object.keys(texts)) sight(k, false);
    await wait(20);
    const stays = logged('visible');
    expect(of(stays).sort()).toEqual(['glossed', 'none', 'thinned']);
    expect(stays.find((m) => m.context === hashKey(texts.none))).toMatchObject({ annotations: [], y: 0 });
    expect(stays.every((m) => m.ms >= 1000)).toBe(true);
    sight('none', true); await wait(20); sight('none', false);
    expect(logged('shown')).toHaveLength(3);
    expect(JSON.stringify(sent.filter((m) => m.type === 'interaction'))).not.toContain('river');
  } finally {
    await win.happyDOM.close();
    Object.assign(globalThis, saved);
  }
}, 30000);

test('a blank passage\'s plan and records pass the checks on the way to the host', () => {
  const text = 'We walked by the river all morning long.';
  const none = planSwaps(1, [], text, []);
  // normal's 0.5 lookups per 100 words: 0.04 in these 8
  expect(none).toEqual({ swaps: [], plan: { level: 0, intensity: 1, jitter: 1, limit: 0, candidates: 0, budget: 0.04, expected: 0 } });
  const swap = { o: 'river', f: '川', g: 'river', h: 'kawa', ctx: '' }; // a word with no estimate: 0.27, over the budget
  const thinned = Array.from({ length: 50 }, (_, seed) => planSwaps(1, [swap], text, [], { seed, jitter: 0.5 }))
    .find((r) => !r.swaps.length);
  expect(thinned.plan).toEqual({ level: 0, intensity: 1, jitter: 0.5, limit: 0, candidates: 1, budget: 0.02, expected: 0 });
  const now = 1_800_000_000_000;
  const where = { at: now, view: 'a1b2', context: 'c0ffee', box: [0, 0, 600, 40], annotations: [] };
  for (const plan of [none.plan, thinned.plan]) {
    const shown = { ...where, kind: 'shown', words: 8, plan };
    expect(cleanInteraction(shown, now)).toEqual(shown);
  }
  const stay = { ...where, kind: 'visible', y: 0, height: 40, ms: 4200, wall: 5000, away: 800, media: 0, idle: 0, uid: 'a1b2-1' };
  expect(cleanInteraction(stay, now)).toEqual(stay);
});
