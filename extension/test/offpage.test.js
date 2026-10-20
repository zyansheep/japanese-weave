import { test, expect } from 'bun:test';
import { Window } from 'happy-dom';
import { validateSwaps, planSwaps } from '../src/levels.js';

// The page at the ends of the intensity scale. Full: every candidate drawn, a sentence before the words inside it and a
// word in both places it occurs. Off: the page is English again at once, new passages stay English, nothing is asked
// for or logged; the toast's meter runs from Off to Full; a heavier press brings the Japanese back.
test('a page at full, then off, then back', async () => {
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
  const words = [
    { ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'known' },
    { ja: '犬', en: 'dog', hint: 'inu', kana: 'いぬ', status: 'active' },
    { ja: '大きい', en: 'big', hint: 'ōkii', kana: 'おおきい', status: 'known' },
    { ja: '日', en: 'day', hint: 'hi', kana: 'ひ', status: 'known' },
  ];
  const texts = { a: 'It was a big day. But the dog ran home, and the dog slept.', b: 'The dog and I walked by the river all morning long.' };
  const answer = {
    [texts.a]: [
      { k: 'w', o: 'big', f: '大きい', p: '', g: '', c: 'a big day' }, // inside the sentence: the sentence wins
      { k: 's', o: 'It was a big day.', f: '大きい日でした。', p: '大きい|日|でした|。', g: 'It was a big day.', c: 'It was a big day.' },
      { k: 'w', o: 'But', f: 'でも', p: '', g: '', c: 'But the dog ran' },
      { k: 'w', o: 'dog', f: '犬', p: '', g: '', c: 'the dog ran' },
      { k: 'w', o: 'dog', f: '犬', p: '', g: '', c: 'the dog slept' },
    ],
    [texts.b]: [{ k: 'w', o: 'dog', f: '犬', p: '', g: '', c: 'The dog and' }],
  };
  const base = { settings: { enabled: true, newSitesEnabled: true, siteOverrides: {} }, level: 2, hasKey: true, paused: false,
    origins: ['<all_urls>'], stage: 3 };
  const until = Date.now() + 3 * 3600 * 1000;
  let config = { ...base, revision: 'full', intensity: { level: 3, until } };
  const sent = [];
  const listeners = [];
  const runtime = {
    id: 'fw-test',
    onMessage: { addListener: (fn) => listeners.push(fn) },
    sendMessage: async (msg) => {
      sent.push(msg);
      if (msg.type === 'config') return config;
      if (msg.type === 'swaps') {
        const results = {}, plans = {};
        for (const i of msg.items) {
          const valid = validateSwaps(2, answer[i.text] || [], words, i.text);
          ({ swaps: results[i.key], plan: plans[i.key] } = planSwaps(2, valid, i.text, words, { intensity: config.intensity.level, seed: 7 }));
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
  const broadcast = (msg) => { for (const fn of listeners) fn(msg); };
  const asked = () => sent.filter((m) => m.type === 'swaps').length;
  const logged = () => sent.filter((m) => m.type === 'interaction' || m.type === 'event').length;
  try {
    win.document.body.innerHTML = `<main>${Object.entries(texts).map(([k, t]) => `<p id="${k}">${t}</p>`).join('')}</main>`;
    await import('../src/content.js?offpage');
    await wait();
    const p = (k) => win.document.querySelector(`#${k}`);
    // Full: the sentence, then every word outside it, the dog twice.
    const top = [...p('a').querySelectorAll('.fw-word')].filter((s) => !s.classList.contains('fw-part'));
    expect(top.map((s) => s.getAttribute('data-ja'))).toEqual(['大きい日でした。', 'でも', '犬', '犬']);
    expect(p('a').textContent).toBe('大きい日でした。 でも the 犬 ran home, and the 犬 slept.');
    expect(p('b').querySelector('.fw-word')?.textContent).toBe('犬');

    // On screen and seen, so stays are open when off comes.
    const onscreen = io.findLast((o) => o.options.threshold?.length === 11);
    const sight = (el, on) => onscreen.cb([{ target: el, isIntersecting: on, intersectionRatio: on ? 1 : 0,
      intersectionRect: { height: on ? 20 : 0 }, rootBounds: { height: 800 } }]);
    win.document.dispatchEvent(user(new win.MouseEvent('pointermove', { bubbles: true })));
    sight(p('a'), true);
    await wait(20);

    // Off: English again at once.
    config = { ...base, revision: 'off', intensity: { level: -3, until } };
    broadcast({ type: 'config', config });
    expect(p('a').textContent).toBe(texts.a);
    expect(p('b').textContent).toBe(texts.b);
    expect(win.document.querySelectorAll('.fw-word')).toHaveLength(0);
    const askedBefore = asked();
    const loggedBefore = logged();
    // New passages stay English, and nothing on the page is asked for or logged.
    const added = win.document.createElement('p');
    added.textContent = 'The dog came back home late in the evening.';
    win.document.querySelector('main').append(added);
    await wait();
    sight(p('a'), true); sight(added, true);
    await wait(1200);
    sight(p('a'), false); sight(added, false);
    p('a').dispatchEvent(user(new win.MouseEvent('click', { bubbles: true, clientX: 5, clientY: 5 })));
    await wait(20);
    expect(added.querySelector('.fw-word')).toBeNull();
    expect(asked()).toBe(askedBefore);
    expect(logged()).toBe(loggedBefore);

    // The toast: seven steps from Off to Full.
    broadcast({ type: 'intensity', intensity: { level: -3, until } });
    const box = win.document.querySelector('[data-japanese-weave="intensity"]').box;
    expect(box.querySelector('.title').textContent).toBe('Japanese on pages: Off');
    expect([...box.querySelectorAll('.ends span')].map((s) => s.textContent)).toEqual(['Off', 'Full']);
    const cells = () => [...box.querySelectorAll('.meter i')].map((c) => (c.classList.contains('lit') ? 1 : 0));
    expect(cells()).toEqual([1, 1, 1, 1, 0, 0, 0]);
    expect(box.querySelector('.note').textContent).toContain('turns it back on');
    broadcast({ type: 'intensity', intensity: { level: 3, until } });
    expect(box.querySelector('.title').textContent).toBe('Japanese on pages: Full');
    expect(cells()).toEqual([0, 0, 0, 1, 1, 1, 1]);
    broadcast({ type: 'intensity', intensity: { level: 1, until } });
    expect(box.querySelector('.title').textContent).toBe('Japanese on pages: Heavier (+1)');
    expect(cells()).toEqual([0, 0, 0, 1, 1, 0, 0]);

    // Heavier from off: much lighter, and the page asks for its swaps again.
    config = { ...base, revision: 'back', intensity: { level: -2, until } };
    broadcast({ type: 'config', config });
    await wait();
    expect(asked()).toBeGreaterThan(askedBefore);
    expect(sent.filter((m) => m.type === 'swaps').at(-1).revision).toBe('back');
  } finally {
    await win.happyDOM.close();
    Object.assign(globalThis, saved);
  }
}, 30000);
