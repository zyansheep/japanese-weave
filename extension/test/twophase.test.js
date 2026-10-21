import { test, expect } from 'bun:test';
import { Window } from 'happy-dom';
import { hashKey } from '../src/hash.js';

// The page's two paintings (content.js): the swaps code is sure of at once, the model's additions when its answer
// comes. A passage the reader hasn't had on screen is painted afresh from the full plan; one they have keeps the very
// spans it shows and only gains; one with nothing sure is painted when the answer comes. Until its last painting a
// passage logs no shown record and no stays.
test('two paintings: sure swaps at once, the answer without moving what the reader saw; the log waits for the last', async () => {
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
  const config = { settings: { enabled: true, newSitesEnabled: true, siteOverrides: {} }, level: 2, revision: 'r1', hasKey: true,
    paused: false, origins: ['<all_urls>'], stage: 3, intensity: { level: 0, until: 0 } };
  const texts = {
    away: 'The old dog sleeps by the warm window, but only in the morning.', // a sure swap; off screen when the answer comes
    seen: 'Our new dog runs to the park, but he never stays there long.', // a sure swap; on screen when the answer comes
    bare: 'I always forget my umbrella when it rains in the evening.', // nothing sure
  };
  const word = (o, f, ctx) => ({ o, f, g: o, h: '', ctx, word: f, status: 'known' });
  const plan = (limit, candidates) => ({ level: 0, intensity: 1, jitter: 1, limit, candidates, budget: 0.06, spent: 0, expected: 0.1, sentencePrice: 0.08 });
  const sure = { away: [word('dog', '犬', 'old dog sleeps')], seen: [word('dog', '犬', 'new dog runs')], bare: [] };
  // the full plans: away's day thins the dog out and takes others; seen's appended plan keeps its dog
  const full = {
    away: { swaps: [word('old', '古い', 'The old dog'), word('but', 'でも', 'window, but only')], plan: plan(2, 3) },
    seen: { swaps: [word('new', '新しい', 'Our new dog'), word('but', 'でも', 'park, but he')], plan: plan(2, 3),
      appended: { swaps: [sure.seen[0], word('but', 'でも', 'park, but he')], plan: plan(2, 3) } },
    bare: { swaps: [word('umbrella', '傘', 'my umbrella when')], plan: plan(1, 1) },
  };
  const name = (text) => Object.keys(texts).find((k) => texts[k] === text);
  const sent = [];
  let answer; // resolves the page's wait request
  const runtime = {
    id: 'fw-test',
    onMessage: { addListener: () => {} },
    sendMessage: async (msg) => {
      sent.push(msg);
      if (msg.type === 'config') return config;
      if (msg.type === 'swaps' && !msg.wait) {
        const results = {}, plans = {};
        for (const i of msg.items) { results[i.key] = sure[name(i.text)]; if (sure[name(i.text)].length) plans[i.key] = plan(1, 1); }
        return { results, plans, waiting: msg.items.map((i) => i.key) };
      }
      if (msg.type === 'swaps') {
        await new Promise((r) => { answer = r; });
        const out = { results: {}, plans: {}, appended: {} };
        for (const i of msg.items) {
          const f = full[name(i.text)];
          out.results[i.key] = f.swaps;
          out.plans[i.key] = f.plan;
          if (i.keep.length && f.appended) out.appended[i.key] = f.appended;
        }
        return out;
      }
      return { ok: true };
    },
  };
  Object.assign(globalThis, { window: win, document: win.document, location: win.location, MutationObserver: win.MutationObserver,
    IntersectionObserver: IO, setInterval: win.setInterval.bind(win), chrome: { runtime } });
  const wait = (ms = 300) => new Promise((done) => setTimeout(done, ms));
  const user = (e) => Object.defineProperty(e, 'isTrusted', { value: true });
  const logged = (kind) => sent.filter((m) => m.type === 'interaction' && m.kind === kind);
  const of = (records) => records.map((m) => Object.keys(texts).find((k) => hashKey(texts[k]) === m.context));
  try {
    win.document.body.innerHTML = `<main>${Object.entries(texts).map(([k, t]) => `<p id="${k}">${t}</p>`).join('')}</main>`;
    const t0 = Date.now();
    await import('../src/content.js?twophase');
    await wait();
    const p = (k) => win.document.querySelector(`#${k}`);
    const ja = (k) => [...p(k).querySelectorAll('.fw-word')].map((s) => s.textContent);
    // At once: the sure swaps; the page asks again for the rest, with what it shows
    expect([ja('away'), ja('seen'), ja('bare')]).toEqual([['犬'], ['犬'], []]);
    const asked = sent.find((m) => m.type === 'swaps' && m.wait);
    expect(asked.items.map((i) => [name(i.text), i.keep.map((s) => s.f)])).toEqual([['away', ['犬']], ['seen', ['犬']], ['bare', []]]);
    expect(Date.now() - t0).toBeLessThan(1000);

    // The reader has "seen" on screen, reads it, and its dog comes into view: no shown record, no stay yet.
    const onscreen = io.findLast((o) => o.options.threshold?.length === 11);
    const sight = (k, on) => onscreen.cb([{ target: p(k), isIntersecting: on, intersectionRatio: on ? 1 : 0,
      intersectionRect: { height: on ? 20 : 0 }, rootBounds: { height: 800 } }]);
    const visibility = io.findLast((o) => !o.options.rootMargin && o.options.threshold.length === 2);
    win.document.dispatchEvent(user(new win.MouseEvent('pointermove', { bubbles: true })));
    sight('seen', true);
    const dog = p('seen').querySelector('.fw-word');
    visibility.cb([{ target: dog, isIntersecting: true, intersectionRatio: 1 }]);
    await wait(1200);
    expect(logged('shown')).toHaveLength(0);
    // a peek while awaiting is logged as it happens
    dog.dispatchEvent(user(new win.MouseEvent('mouseover', { bubbles: true })));
    await wait(800);
    dog.dispatchEvent(user(new win.MouseEvent('mouseout', { bubbles: true })));
    expect(logged('peek').map((m) => m.word)).toEqual(['犬']);

    // The answer comes.
    answer();
    await wait(50);
    // away: painted afresh from the full plan (its dog thinned out), never seen
    expect(ja('away')).toEqual(['古い', 'でも']);
    // seen: the very dog span it showed, plus the answer's but; no new for "new", which the appended plan didn't keep
    expect(ja('seen')).toEqual(['犬', 'でも']);
    expect(p('seen').querySelector('.fw-word')).toBe(dog);
    // bare: painted now
    expect(ja('bare')).toEqual(['傘']);
    expect(p('seen').textContent).toBe('Our new 犬 runs to the park, でも he never stays there long.');
    // its shown record now, with the plan of what is on the page; the stay before the answer is not logged
    expect(of(logged('shown'))).toEqual(['seen']);
    expect(logged('shown')[0]).toMatchObject({ plan: full.seen.appended.plan, annotations: [['犬', 0, 0], ['でも', 0, 0]] });
    expect(logged('visible')).toHaveLength(0);
    // the stay since the answer is the last painting's
    await wait(1100);
    sight('seen', false);
    await wait(20);
    expect(of(logged('visible'))).toEqual(['seen']);
    expect(logged('visible')[0].annotations).toEqual([['犬', 0, 0], ['でも', 0, 0]]);
    expect(JSON.stringify(sent.filter((m) => m.type === 'interaction'))).not.toContain('umbrella');
  } finally {
    await win.happyDOM.close();
    Object.assign(globalThis, saved);
  }
}, 30000);
