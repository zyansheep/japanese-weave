import { test, expect } from 'bun:test';
import { load, fixture } from './fixtures.js';
import { flatten } from '../src/text.js';

// The content script on a captured YouTube video page (test/fixtures): the description and comments woven; a comment
// YouTube renders again, with new nodes or with new text in its old text node, woven again once; the description
// opened; and a move to another video inside the page (YouTube is a single-page app), which is woven afresh.
test('YouTube: woven, re-rendered, opened and navigated', async () => {
  const win = load('youtube-watch.html', 'https://www.youtube.com/watch?v=24GfgNtnjXc');
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
  // The fake model: the longest word that occurs once in a passage, in capitals.
  const pick = (text) => {
    const words = text.match(/\p{L}{4,}/gu) || [];
    const once = words.filter((w) => text.split(new RegExp(`(?<!\\p{L})${w}(?!\\p{L})`, 'u')).length === 2);
    const w = once.sort((a, b) => b.length - a.length)[0];
    return w ? [{ o: w, f: w.toUpperCase(), g: w, h: '', ctx: '' }] : [];
  };
  const asked = [];
  const sent = [];
  const runtime = {
    id: 'fw-test',
    onMessage: { addListener: () => {} },
    sendMessage: async (msg) => {
      sent.push(msg);
      if (msg.type === 'config') return config;
      if (msg.type === 'swaps') {
        const results = {};
        for (const i of msg.items) { asked.push(i.text); results[i.key] = pick(i.text); }
        return { results };
      }
      return { ok: true };
    },
  };
  Object.assign(globalThis, { window: win, document: doc, location: win.location, MutationObserver: win.MutationObserver,
    IntersectionObserver: IO, setInterval: win.setInterval.bind(win), chrome: { runtime } });
  const wait = (ms = 800) => new Promise((done) => setTimeout(done, ms));
  const words = (el) => [...el.querySelectorAll('.fw-word')];
  const comments = () => [...doc.querySelectorAll('#content-text')];
  try {
    await import('../src/content.js?youtube');
    await wait();
    // Two runs of the collapsed description and three comments, each with its word; nothing else.
    const runs = [...doc.querySelectorAll('#attributed-snippet-text > span > span')];
    expect(runs.map((r) => words(r).length)).toEqual([1, 0, 1]); // the link between them stays as it is
    expect(comments().map((c) => words(c).length)).toEqual([1, 1, 1]);
    expect(doc.querySelectorAll('.fw-word')).toHaveLength(5);
    expect(words(comments()[0])[0].textContent).toBe('RAINBOWS');
    expect(doc.querySelector('ytd-watch-metadata h1 .fw-word, #author-text .fw-word, ytd-watch-next-secondary-results-renderer .fw-word')).toBeNull();

    // YouTube renders a comment again with new nodes: the new text is woven, once.
    comments()[1].innerHTML = '<span class="ytAttributedStringHost" role="text">Light bends twice inside every single raindrop on its way</span>';
    await wait();
    expect(words(comments()[1]).map((w) => w.textContent)).toEqual(['RAINDROP']);
    // Or writes new text into the old text node, which the woven word split: no leftovers of the old text.
    const host = comments()[0].querySelector('span');
    host.firstChild.data = 'Every rainbow is really a full circle seen from the ground';
    await wait();
    expect(flatten(comments()[0]).text).toBe('Every rainbow is really a full circle seen from the ground');
    expect(words(comments()[0]).map((w) => w.textContent)).toEqual(['RAINBOW']);

    // "...more": YouTube hides the snippet and fills the expanded description, which is woven run by run.
    const expanded = doc.createElement('div');
    expanded.innerHTML = fixture('youtube-description-expanded.html');
    doc.querySelector('#snippet-text').setAttribute('hidden', '');
    doc.querySelector('#description-inline-expander').setAttribute('is-expanded', '');
    doc.querySelector('#expanded yt-attributed-string').append(expanded.querySelector('#expanded yt-attributed-string > span'));
    await wait(1000);
    const open = [...doc.querySelectorAll('#expanded yt-attributed-string > span > span')];
    expect(open.filter((r) => words(r).length).length).toBe(10);
    expect(words(open[0]).map((w) => w.textContent)).toEqual(words(runs[0]).map((w) => w.textContent)); // same text, same word

    // Another video, in the same page: YouTube swaps the text in place. All of it is woven afresh.
    win.history.pushState({}, '', '/watch?v=wt4p2oalmRY');
    expect(win.location.href).toContain('wt4p2oalmRY');
    doc.querySelector('#expanded yt-attributed-string').replaceChildren();
    doc.querySelector('#snippet-text').removeAttribute('hidden');
    doc.querySelector('#description-inline-expander').removeAttribute('is-expanded');
    doc.querySelector('#attributed-snippet-text').innerHTML = '<span class="ytAttributedStringHost">Spider silk is tougher than steel for its weight, so we tried swinging from it.</span>';
    const threads = doc.querySelector('ytd-comments #contents');
    for (const t of [...threads.children].slice(1)) t.remove();
    comments()[0].innerHTML = '<span class="ytAttributedStringHost" role="text">I never knew spiders could make something this strong</span>';
    asked.length = 0;
    await wait(2000); // the script looks for a new URL every second
    expect(words(doc.querySelector('#attributed-snippet-text')).map((w) => w.textContent)).toEqual(['SWINGING']);
    expect(words(comments()[0]).map((w) => w.textContent)).toEqual(['SOMETHING']);
    expect(doc.querySelectorAll('.fw-word')).toHaveLength(2);
    expect(asked.some((t) => t.includes('Spider silk'))).toBe(true);
    // Something was woven all along, so the toolbar never heard "nothing to weave".
    expect(sent.filter((m) => m.type === 'nothing' && m.why)).toEqual([]);
  } finally {
    await win.happyDOM.close();
    Object.assign(globalThis, saved);
  }
}, 30000);
