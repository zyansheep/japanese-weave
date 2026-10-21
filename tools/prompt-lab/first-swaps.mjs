// How long a page waits for its first Japanese: the extension's content script and worker together in happy-dom (as
// extension/test/runtime.test.js runs them), from the content script's start on a page of passages to the first swap
// painted, with a model call under way that never answers, so only the swaps code is sure of can show. Run with a
// worker that has just started (it builds the matcher's index on the first request) and with one already warm.
//
//   python tools/prompt-lab/words.py <copy of state.json> > /tmp/words.json
//   WORDS=/tmp/words.json bun tools/prompt-lab/first-swaps.mjs tools/prompt-lab/fresh2.json
//
// RUNS: page loads per worker (3). The time includes the content script's 250 ms wait for passages to arrive together
// (content.js consider), which a real page pays too; the browser's own message passing is not in it.
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

const env = process.env;
const reply = JSON.parse(readFileSync(env.WORDS, 'utf8'));
const passages = process.argv.slice(2).flatMap((f) => JSON.parse(readFileSync(f, 'utf8')));
const runs = Number(env.RUNS || 3);
const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const html = `<main>${passages.map((p) => `<p>${escape(p.text)}</p>`).join('')}</main>`;

const local = { settings: { enabled: true, newSitesEnabled: true, model: 'claude-haiku-5-5' } };
const workerListeners = [];
const pageListeners = [];
const listener = (arr) => ({ addListener: (fn) => arr.push(fn) });
let win;
const dispatch = (msg, sender) => new Promise((done) => {
  let handled = false;
  for (const fn of workerListeners) if (fn(msg, sender, done)) handled = true;
  if (!handled) done(undefined);
});
class IO {
  constructor(cb, options) { this.cb = cb; this.options = options; this.els = new Set(); }
  observe(el) { this.els.add(el); if (this.options.rootMargin) queueMicrotask(() => { if (this.els.has(el)) this.cb([{ target: el, isIntersecting: true, intersectionRatio: 1 }]); }); }
  unobserve(el) { this.els.delete(el); }
  disconnect() { this.els.clear(); }
}
globalThis.chrome = {
  storage: { local: {
    async setAccessLevel() {},
    async get(keys) { return structuredClone(keys == null ? local : Object.fromEntries((typeof keys === 'string' ? [keys] : keys).filter((k) => k in local).map((k) => [k, local[k]]))); },
    async set(values) { for (const [k, v] of Object.entries(values)) local[k] = structuredClone(v); },
    async remove(keys) { for (const k of keys) delete local[k]; },
  } },
  runtime: { id: 'lab', getURL: (path) => `chrome-extension://lab/${path}`, onMessage: listener(workerListeners), onInstalled: listener([]),
    onStartup: listener([]),
    sendMessage: (msg) => dispatch(msg, { id: 'lab', tab: { id: 1 }, url: win.location.href }),
    sendNativeMessage: async (_host, msg) => (msg.type === 'state' ? { ...reply, ok: true, anthropicKey: 'not-a-key' } : { ok: true }) },
  tabs: { query: async () => [], sendMessage: async () => {} },
  alarms: { create: async () => {}, clear: async () => {}, onAlarm: listener([]) },
  action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {}, setIcon: async () => {}, setTitle: async () => {} },
  permissions: { getAll: async () => ({ origins: ['<all_urls>'] }), onAdded: listener([]), onRemoved: listener([]) },
  scripting: { getRegisteredContentScripts: async () => [], updateContentScripts: async () => {}, unregisterContentScripts: async () => {},
    registerContentScripts: async () => {} },
};
globalThis.fetch = () => new Promise(() => {}); // the model's answer never comes

// One page load: a fresh page and a fresh copy of the content script; ms until the first swap is painted, and how many
// passages have one by the time every passage's first answer is in.
async function load(n) {
  win = new Window({ url: `https://example.com/page${n}` });
  Object.assign(globalThis, { window: win, document: win.document, location: win.location, MutationObserver: win.MutationObserver,
    IntersectionObserver: IO, setInterval: win.setInterval.bind(win) });
  win.document.body.innerHTML = html;
  const before = workerListeners.length;
  const t0 = performance.now();
  await import(`../../extension/src/content.js?load${n}`);
  pageListeners.push(...workerListeners.splice(before)); // the page's own listener hears the worker's broadcasts, not pages
  let first = null;
  while (performance.now() - t0 < 5000) {
    if (first === null && win.document.querySelector('.fw-word')) first = performance.now() - t0;
    if (first !== null && performance.now() - t0 > first + 300) break;
    await new Promise((r) => setTimeout(r, 1));
  }
  const woven = [...win.document.querySelectorAll('p')].filter((p) => p.querySelector('.fw-word')).length;
  await win.happyDOM.abort();
  return { first, woven };
}

const t = performance.now();
await import('../../extension/src/background.js');
await dispatch({ type: 'syncWords' }, { id: 'lab', url: 'chrome-extension://lab/src/popup.html' });
const started = performance.now() - t;
const results = [];
for (let n = 0; n < runs; n++) results.push(await load(n));
console.log(`${passages.length} passages, ${reply.words.length} words; the worker started and synced in ${started.toFixed(0)} ms`);
results.forEach((r, n) => console.log(`page load ${n + 1}${n ? ' (worker warm)' : ' (worker just started)'}: first swap after ${r.first?.toFixed(0)} ms; ` +
  `${r.woven} of ${passages.length} passages with sure swaps`));
process.exit(0);
