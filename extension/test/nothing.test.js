import { test, expect } from 'bun:test';
import { load } from './fixtures.js';

// The content script tells the background what a page has to weave, for the toolbar's faded icon (toolbar.js): only
// once the page has settled, so nothing flickers while it loads. On a captured Google results page whose passages get
// no Japanese: "unfit"; a hidden part of an AI answer shown and woven: '' at once; a new search in the same page with no
// passages: "none"; a result loaded later with Japanese: '' again.
test('nothing to weave: said once the page settles, and taken back when Japanese turns up', async () => {
  const win = load('google-search.html', 'https://www.google.com/search?q=how+do+rainbows+form');
  const doc = win.document;
  const keys = ['chrome', 'document', 'window', 'location', 'MutationObserver', 'IntersectionObserver', 'setInterval'];
  const saved = Object.fromEntries(keys.map((k) => [k, globalThis[k]]));
  class IO {
    constructor(cb, options) { this.cb = cb; this.options = options; this.els = new Set(); }
    observe(el) { this.els.add(el); if (this.options.rootMargin) queueMicrotask(() => { if (this.els.has(el)) this.cb([{ target: el, isIntersecting: true, intersectionRatio: 1 }]); }); }
    unobserve(el) { this.els.delete(el); }
    disconnect() { this.els.clear(); }
  }
  const config = { settings: { enabled: true, newSitesEnabled: true, siteOverrides: {} }, level: 1, revision: 'r1', hasKey: true,
    paused: false, origins: ['<all_urls>'], stage: 1 };
  const plan = { level: 0, intensity: 1, jitter: 1, limit: 0, candidates: 0 };
  const swapFor = (text) => (text.includes('Separating Colors') ? [{ o: 'Dispersion', f: 'DISPERSION', g: 'dispersion', h: '', ctx: '' }]
    : text.includes('freshly loaded') ? [{ o: 'loaded', f: 'LOADED', g: 'loaded', h: '', ctx: '' }] : []);
  const asked = [];
  const told = []; // [ms since start, why]
  const start = Date.now();
  const runtime = {
    id: 'fw-test',
    onMessage: { addListener: () => {} },
    sendMessage: async (msg) => {
      if (msg.type === 'config') return config;
      if (msg.type === 'nothing') told.push([Date.now() - start, msg.why]);
      if (msg.type === 'swaps') {
        const results = {}, plans = {};
        for (const i of msg.items) { asked.push(i.text); results[i.key] = swapFor(i.text); plans[i.key] = plan; }
        return { results, plans };
      }
      return { ok: true };
    },
  };
  Object.assign(globalThis, { window: win, document: doc, location: win.location, MutationObserver: win.MutationObserver,
    IntersectionObserver: IO, setInterval: win.setInterval.bind(win), chrome: { runtime } });
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));
  const until = async (ok, ms = 8000) => { for (const end = Date.now() + ms; !ok() && Date.now() < end;) await wait(50); };
  const whys = () => told.map(([, why]) => why);
  try {
    await import('../src/content.js?nothing');
    await wait(1500);
    expect(asked.length).toBe(7); // the passages were asked for and answered...
    expect(told).toEqual([]); // ...but the page hasn't settled yet: no word to the toolbar
    await until(() => told.length);
    expect(whys()).toEqual(['unfit']);
    expect(told[0][0]).toBeGreaterThanOrEqual(3000);

    // "Show more" on the AI overview shows a list item (Google removes aria-hidden); it gets Japanese: '' at once.
    const shown = Date.now();
    const item = [...doc.querySelectorAll('#rcnt li[aria-hidden="true"]')].find((li) => li.textContent.includes('Separating Colors'));
    item.removeAttribute('aria-hidden');
    await until(() => told.length === 2, 3000);
    expect(whys()).toEqual(['unfit', '']);
    expect(item.querySelector('.fw-word')?.textContent).toBe('DISPERSION');
    expect(Date.now() - shown).toBeLessThan(2500); // not after another quiet spell

    // A new search inside the page with no results to weave: looked at afresh, then "none".
    win.history.pushState({}, '', '/search?q=qwxzv+plmk');
    doc.querySelector('#rcnt').innerHTML = '<div id="center_col" role="main"><div id="search"><div id="rso"><div class="card-section">'
      + '<p>Your search - <em>qwxzv plmk</em> - did not match any documents.</p><ul><li>Make sure that all words are spelled correctly.</li></ul>'
      + '</div></div></div></div>';
    await until(() => told.length === 3);
    expect(whys()).toEqual(['unfit', '', 'none']);

    // Results that load later (a next page of results) with Japanese: '' at once.
    doc.querySelector('#rso').insertAdjacentHTML('beforeend', '<div class="MjjYud"><div lang="en"><div data-sncf="1">'
      + '<div class="VwiC3b">These results were freshly loaded after the first page, as you scrolled down.</div></div></div></div>');
    await until(() => told.length === 4, 3000);
    expect(whys()).toEqual(['unfit', '', 'none', '']);
    expect(doc.querySelector('#rso .VwiC3b .fw-word')?.textContent).toBe('LOADED');
  } finally {
    await win.happyDOM.close();
    Object.assign(globalThis, saved);
  }
}, 40000);
