import { test, expect } from 'bun:test';
import { Window } from 'happy-dom';

// The content script with a woven sentence: a hover on one of its words is a peek at that word, a click flips the whole
// sentence to English and is logged, but tells the word state nothing about the word clicked.
test('a sentence swap: peeks are per word, a click flips the sentence whole', async () => {
  const win = new Window({ url: 'https://example.com/diary' });
  const keys = ['chrome', 'document', 'window', 'location', 'MutationObserver', 'IntersectionObserver', 'setInterval'];
  const saved = Object.fromEntries(keys.map((k) => [k, globalThis[k]]));
  class IO {
    constructor(cb, options) { this.cb = cb; this.options = options; this.els = new Set(); }
    observe(el) { this.els.add(el); if (this.options.rootMargin) queueMicrotask(() => { if (this.els.has(el)) this.cb([{ target: el, isIntersecting: true, intersectionRatio: 1 }]); }); }
    unobserve(el) { this.els.delete(el); }
    disconnect() { this.els.clear(); }
  }
  const config = { settings: { enabled: true, newSitesEnabled: true, siteOverrides: {} }, level: 2, revision: 'r1', hasKey: true,
    paused: false, origins: ['<all_urls>'], stage: 3 };
  const sentence = {
    kind: 'sentence', o: 'It was a fun day.', f: '楽しい日でした。', g: 'It was a fun day.', ctx: 'It was a fun day.',
    parts: [{ t: '楽しい', word: '楽しい', g: 'fun', h: 'tanoshii', status: 'known' }, { t: '日', word: '日', g: 'day', h: 'hi', status: 'active' },
      { t: 'でした。' }],
    words: ['楽しい', '日'], word: '日', status: 'active',
  };
  const sent = [];
  const runtime = {
    id: 'fw-test',
    onMessage: { addListener: () => {} },
    sendMessage: async (msg) => {
      sent.push(msg);
      if (msg.type === 'config') return config;
      if (msg.type === 'swaps') return { results: Object.fromEntries(msg.items.map((i) => [i.key, [sentence]])) };
      return { ok: true };
    },
  };
  Object.assign(globalThis, { window: win, document: win.document, location: win.location, MutationObserver: win.MutationObserver,
    IntersectionObserver: IO, setInterval: win.setInterval.bind(win), chrome: { runtime } });
  const wait = (ms = 400) => new Promise((done) => setTimeout(done, ms));
  const user = (e) => Object.defineProperty(e, 'isTrusted', { value: true });
  try {
    win.document.body.innerHTML = '<main><p id="a">We walked by the river all morning. It was a fun day.</p></main>';
    await import('../src/content.js?sentence');
    await wait();
    const p = win.document.querySelector('#a');
    expect(p.textContent).toBe('We walked by the river all morning. 楽しい日でした。');
    const day = [...p.querySelectorAll('.fw-part')].find((s) => s.textContent === '日');

    day.dispatchEvent(user(new win.MouseEvent('mouseover', { bubbles: true })));
    await wait(800);
    expect(day.classList.contains('fw-tip')).toBe(true);
    day.dispatchEvent(user(new win.MouseEvent('mouseout', { bubbles: true })));
    await wait(50);
    expect(sent.filter((m) => m.type === 'event').map((m) => `${m.event}:${m.word}`)).toContain('peek:日');
    const peek = sent.find((m) => m.type === 'interaction' && m.kind === 'peek');
    expect(peek).toMatchObject({ word: '日', position: 1, annotations: [['楽しい', expect.any(Number), expect.any(Number)], ['日', expect.any(Number), expect.any(Number)]] });

    day.dispatchEvent(user(new win.MouseEvent('click', { bubbles: true, cancelable: true })));
    await wait(50);
    expect(p.textContent).toBe('We walked by the river all morning. It was a fun day.');
    expect(sent.find((m) => m.type === 'interaction' && m.kind === 'flip')).toMatchObject({ word: '日' });
    expect(sent.filter((m) => m.type === 'event' && m.event === 'flip')).toEqual([]);
    p.querySelector('.fw-group').dispatchEvent(user(new win.MouseEvent('click', { bubbles: true, cancelable: true })));
    await wait(50);
    expect(p.textContent).toBe('We walked by the river all morning. 楽しい日でした。');
  } finally {
    await win.happyDOM.close();
    Object.assign(globalThis, saved);
  }
}, 30000);
