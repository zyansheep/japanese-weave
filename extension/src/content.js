// Content script: watch readable text (tweets on X, prose elsewhere), ask the background for swaps, paint them,
// handle the delayed gloss, click/Enter flip and seen/peek/flip events, and log clicks, selections and on-screen time
// for passages with glossed words. Every async reply belongs to one config generation.
import { flatten, applySwaps, revert, toggle, isSwap, swapWord, swapOf, showsEnglish, SWAP_CLASS } from './text.js';
import { hashKey } from './hash.js';
import { siteEnabled, hasSiteAccess } from './settings.js';
import { selector, readable } from './page.js';
import { wordCount } from './levels.js';
import { MAX_BATCH } from './models.js';
import { readingState, account, newStay, paused } from './attention.js';
// Firefox has the same API as a promise-based `browser`; Chrome only has `chrome`.
const chrome = globalThis.browser ?? globalThis.chrome;

let config = null;
let observer = null;
let near = null;
let visible = null;
let timer = null;
let generation = 0;
const memo = new Map();
const keys = new WeakMap(); // element -> revision:text hash, kept off the DOM so pages can't read it
const sizes = new WeakMap(); // element -> its word count, for shown records
const shownContexts = new Set(); // passages already logged as shown in this page load
const pending = new Map();
const inflight = new Set();
const tracked = new Set();
const visibleWords = new Set();
const seen = new Set();
const failures = new Map(); // text key -> failed model attempts; retried with backoff
const MAX_RETRIES = 3;
const TIP_DELAY_MS = 700; // a beat to recall the word before the meaning appears
let tipTimer = null;
let openTip = null; // { span, at }: a gloss opened by a hover dwell, logged with its dwell when it closes
// One page load, so the host's log can tell repeat visits apart. getRandomValues also works on plain-http pages.
const VIEW = Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('');
let logged = 0; // numbers interactions within the view, so a resent one is stored once
let lastClick = 0;
let pressedAt = 0; // when the mouse button went down, so a drag selection has a duration
let lastRange = null; // the last selection logged, so the same one is logged once
const READING_MAX = 500; // clicks and selections per page load
let readingLogged = 0;
// On-screen time: one record per stay of a glossed passage on screen. Only time a reader could have spent reading it
// counts (ms); the rest is split by why not (attention.js). Stays with under STAY_MIN_MS of reading were a scroll past
// and aren't sent; longer ones than STAY_MAX_MS are sent in parts, and a stay with no reading for PAUSE_MS ends there.
const STAY_MIN_MS = 1000;
const STAY_MAX_MS = 600000;
const STAYS_MAX = 2000; // per page load
let lastInput = Date.now();
let onscreen = null;
const inView = new Set(); // passages on screen enough to be read
const stays = new Map(); // passage -> { at, last, wall, ms, away, media, idle, desc }
let stayTimer = null;
let staysLogged = 0;
// After the extension is updated or reloaded, a copy still running in an open tab is cut off from the background
// (Chrome keeps it running; Firefox mostly unloads it). It then goes quiet: no more requests or errors, woven words
// stay as they are, and reloading the page brings in the new copy.
let orphaned = false;
const alive = () => { try { return !orphaned && !!chrome.runtime?.id; } catch { return false; } };
function orphan() {
  if (orphaned) return;
  orphaned = true;
  generation++;
  observer?.disconnect(); observer = null;
  near?.disconnect(); visible?.disconnect(); onscreen?.disconnect();
  clearTimeout(timer); timer = null; clearInterval(navTimer); clearInterval(stayTimer); stayTimer = null;
  pending.clear(); inflight.clear(); stays.clear(); inView.clear(); hideTip();
}
const send = (msg) => {
  if (!alive()) { orphan(); return Promise.reject(new Error('Japanese Weave was reloaded')); }
  try {
    return chrome.runtime.sendMessage(msg).catch((e) => { if (!alive()) orphan(); throw e; });
  } catch (e) {
    orphan();
    return Promise.reject(e);
  }
};
const on = () => !orphaned && config?.hasKey && !config.paused && siteEnabled(config.settings, location.href) && hasSiteAccess(location.href, config.origins);
const targets = () => selector(location.href);
// Only real user input on spans this extension made; pages can't forge flips, peeks or seen events.
const swapFor = (e) => {
  const span = e.isTrusted && e.target?.closest?.(`span.${SWAP_CLASS}`);
  return span && isSwap(span) ? span : null;
};

function record(span, event) {
  const word = swapWord(span);
  if (!word || !on()) return;
  const id = new Date().toISOString().slice(0, 10) + ':' + event + ':' + word;
  const daily = event === 'seen' || event === 'peek';
  if (daily && seen.has(id)) return;
  if (daily) seen.add(id);
  void send({ type: 'event', word, event }).then((r) => {
    if (!r?.ok && daily) seen.delete(id);
  }).catch(() => { if (daily) seen.delete(id); });
}
// Every glossed word in a passage by position only (never page text), for the host's log.
function describe(passage) {
  const spans = [...passage.querySelectorAll(`span.${SWAP_CLASS}`)].filter((s) => isSwap(s) && swapWord(s));
  const box = passage.getBoundingClientRect();
  const annotations = spans.map((s) => {
    const r = s.getBoundingClientRect();
    return [swapWord(s), r.left - box.left, r.top - box.top];
  });
  return { spans, annotations, box: [box.left + window.scrollX, box.top + window.scrollY, box.width, box.height] };
}
// For the host's log: the peeked or flipped word and its passage, since unpeeked words next to a peek are the
// evidence that the reader knew them.
function logInteraction(span, kind, at, dwellMs) {
  const passage = span.closest(targets());
  const key = passage && keys.get(passage);
  if (!key || !on()) return;
  const { spans, annotations, box } = describe(passage);
  if (!spans.includes(span)) return;
  void send({ type: 'interaction', kind, word: swapWord(span), at, dwellMs, view: VIEW, uid: `${VIEW}-${++logged}`,
    context: key.slice(key.indexOf(':') + 1), position: spans.indexOf(span), box, annotations }).catch(() => {});
}
// The first time a passage's Japanese is on screen in this page load: its word count is the peek rate's denominator.
function logShown(span) {
  const passage = span.closest(targets());
  const key = passage && keys.get(passage);
  const context = key && key.slice(key.indexOf(':') + 1);
  if (!key || !on() || !sizes.get(passage) || document.visibilityState === 'hidden' || shownContexts.has(context)) return;
  shownContexts.add(context);
  const { annotations, box } = describe(passage);
  void send({ type: 'interaction', kind: 'shown', at: Date.now(), view: VIEW, context, words: sizes.get(passage), box,
    annotations }).catch(() => {});
}
// Clicks and selections in a passage with glossed words say where the reader was. Positions and counts only: the
// selected text never leaves the page. extra(passageRect, spans) adds the kind's own fields.
function logReading(passage, kind, at, extra) {
  const key = keys.get(passage);
  if (!key || !on() || readingLogged >= READING_MAX) return;
  const { spans, annotations, box } = describe(passage);
  if (!spans.length) return;
  readingLogged++;
  void send({ type: 'interaction', kind, at, view: VIEW, uid: `${VIEW}-${++logged}`, context: key.slice(key.indexOf(':') + 1),
    box, annotations, ...extra(passage.getBoundingClientRect(), spans) }).catch(() => {});
}
// A click on a glossed word is a flip and is logged as one; the end of a drag that selected text is a selection.
function clicked(e) {
  if (!e.isTrusted || e.button !== 0 || swapFor(e) || !on()) return;
  const passage = e.target?.closest?.(targets());
  const now = Date.now();
  if (!passage || !tracked.has(passage) || !document.getSelection()?.isCollapsed || now - lastClick < 300) return;
  lastClick = now;
  const link = e.target.closest('a[href], button, input, select, textarea, summary, label, [role="button"], [role="link"]');
  logReading(passage, 'click', now, (r) => ({ x: e.clientX - r.left, y: e.clientY - r.top, target: link ? 'link' : 'text' }));
}
function sameRange(a, b) {
  try {
    return !!b && a.compareBoundaryPoints(0, b) === 0 && a.compareBoundaryPoints(2, b) === 0; // START_TO_START, END_TO_END
  } catch {
    return false; // b's nodes left the document
  }
}
function pressed(e) {
  if (e.isTrusted && e.button === 0) pressedAt = Date.now();
}
// After a mouse drag, a double or triple click, or Shift with the arrow keys. A selection over more than three
// passages is a select-all, not reading.
function selected(e) {
  if (!e.isTrusted || !on() || (e.type === 'keyup' && !e.shiftKey && e.key !== 'Shift')) return;
  const startedAt = e.type === 'mouseup' ? pressedAt : 0;
  setTimeout(() => {
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (sameRange(range, lastRange)) return;
    const chars = selection.toString().length; // a count only
    const passages = [...tracked].filter((p) => p.isConnected && range.intersectsNode(p));
    if (!chars || !passages.length || passages.length > 3) return;
    lastRange = range.cloneRange();
    const now = Date.now();
    const rect = range.getBoundingClientRect();
    for (const passage of passages) {
      logReading(passage, 'select', now, (r, spans) => ({ x: rect.left - r.left, y: rect.top - r.top, width: rect.width,
        height: rect.height, chars, covered: spans.flatMap((s, i) => (range.intersectsNode(s) ? [i] : [])),
        ms: startedAt ? Math.max(0, now - startedAt) : 0 }));
    }
  }, 0);
}
function noteInput(e) {
  if (!e.isTrusted) return;
  lastInput = Date.now();
  if (stays.size < inView.size) for (const passage of inView) startStay(passage); // back after a pause ended them
}
// Half of it on screen, or for a passage taller than that, enough of it to fill 40% of the window.
function enoughOnScreen(e) {
  return e.isIntersecting && (e.intersectionRatio >= 0.5 || e.intersectionRect.height >= 0.4 * (e.rootBounds?.height || window.innerHeight));
}
function audible() {
  for (const m of document.querySelectorAll('video, audio')) if (!m.paused && !m.ended && !m.muted && m.volume > 0) return true;
  return false;
}
function readingNow(now) {
  const hidden = document.visibilityState === 'hidden';
  const focused = document.hasFocus();
  return readingState({ hidden, focused, playing: !hidden && focused && audible(), lastInput }, now);
}
function tickStays() {
  const now = Date.now();
  const state = readingNow(now);
  const full = [], left = [];
  for (const [passage, stay] of stays) {
    account(stay, now, state);
    if (stay.wall >= STAY_MAX_MS) full.push(passage);
    else if (paused(stay, now)) left.push(passage);
  }
  for (const passage of full) { endStay(passage); startStay(passage); }
  for (const passage of left) endStay(passage);
}
function startStay(passage) {
  if (stays.has(passage) || document.visibilityState === 'hidden' || !keys.has(passage)) return;
  const now = Date.now();
  stays.set(passage, { ...newStay(now), desc: describe(passage) });
  if (!stayTimer) stayTimer = setInterval(tickStays, 1000);
}
// Before a passage is reverted or forgotten: its glossed words are still in it, or in the stay's first description
// when it has left the page (a detached passage would describe every word at 0, 0).
function endStay(passage) {
  const stay = stays.get(passage);
  if (!stay) return;
  const now = Date.now();
  account(stay, now, readingNow(now));
  stays.delete(passage);
  if (!stays.size) { clearInterval(stayTimer); stayTimer = null; }
  const key = keys.get(passage);
  if (stay.ms < STAY_MIN_MS || staysLogged >= STAYS_MAX || !key || !on()) return;
  const { spans, annotations, box } = passage.isConnected ? describe(passage) : stay.desc;
  if (!spans.length) return;
  staysLogged++;
  void send({ type: 'interaction', kind: 'visible', at: stay.at, view: VIEW, uid: `${VIEW}-${++logged}`,
    context: key.slice(key.indexOf(':') + 1), box, annotations, y: 0, height: box[3], ms: stay.ms, wall: stay.wall,
    away: stay.away, media: stay.media, idle: stay.idle }).catch(() => {});
}
function forget(passage) {
  endStay(passage);
  inView.delete(passage);
  onscreen?.unobserve(passage);
}
function staysOnVisibility() {
  if (document.visibilityState === 'hidden') for (const passage of [...stays.keys()]) endStay(passage);
  else for (const passage of inView) startStay(passage);
}
// The gloss appears only after a short dwell; reaching it counts as a peek (the reader needed the meaning).
function hideTip() {
  clearTimeout(tipTimer); tipTimer = null;
  if (openTip) {
    const { span, at } = openTip;
    openTip = null;
    if (span.isConnected) logInteraction(span, 'peek', at, Date.now() - at);
  }
  for (const el of document.querySelectorAll(`.${SWAP_CLASS}.fw-tip`)) el.classList.remove('fw-tip');
}
function armTip(e) {
  const span = swapFor(e);
  if (!span || !on() || span.classList.contains('fw-tip')) return;
  hideTip();
  tipTimer = setTimeout(() => {
    tipTimer = null;
    if (!span.isConnected || span.classList.contains('fw-en')) return;
    span.classList.add('fw-tip');
    if (e.type === 'mouseover') { // a page can focus a span from script, so focus never counts
      record(span, 'peek');
      openTip = { span, at: Date.now() };
    }
  }, TIP_DELAY_MS);
}
function disarmTip(e) {
  const span = swapFor(e);
  if (span && !span.contains(e.relatedTarget)) hideTip();
}
function seenVisible() {
  if (document.visibilityState === 'hidden') return;
  for (const span of visibleWords) if (span.isConnected) { record(span, 'seen'); logShown(span); }
}
function paint(el, key) {
  if (!on() || keys.get(el) !== key || !readable(el, location.href)) return;
  const swaps = memo.get(key);
  if (!swaps?.length) return;
  applySwaps(el, swaps);
  for (const span of el.querySelectorAll(`.${SWAP_CLASS}`)) if (isSwap(span)) visible.observe(span);
  onscreen?.observe(el);
  observer?.takeRecords();
}
function consider(el) {
  if (!on() || !el.isConnected) return;
  forget(el);
  if (el.querySelector(`.${SWAP_CLASS}`)) revert(el);
  observer?.takeRecords();
  if (!readable(el, location.href)) { keys.delete(el); return; }
  const text = flatten(el).text;
  const key = `${config.revision}:${hashKey(text)}`;
  keys.set(el, key);
  sizes.set(el, wordCount(text));
  if (memo.has(key)) { paint(el, key); return; }
  let p = pending.get(key);
  if (!p) { p = { text, els: new Set() }; pending.set(key, p); }
  p.els.add(el);
  if (!timer) timer = setTimeout(flush, 250);
}
async function flush() {
  timer = null;
  if (!on()) return;
  const gen = generation;
  const items = [...pending].filter(([key]) => !inflight.has(key)).slice(0, MAX_BATCH).map(([key, p]) => ({ key, text: p.text }));
  if (!items.length) return;
  for (const i of items) inflight.add(i.key);
  let result;
  try { result = await send({ type: 'swaps', revision: config.revision, items }); }
  catch { result = { error: 'Extension unavailable' }; }
  if (gen !== generation || !on()) return;
  for (const i of items) {
    inflight.delete(i.key);
    const p = pending.get(i.key);
    pending.delete(i.key);
    const swaps = result?.results?.[i.key];
    if (!Array.isArray(swaps)) {
      // A failed model call (usually the free tier's rate limit) is retried instead of leaving the passage English.
      const tries = (failures.get(i.key) || 0) + 1;
      failures.set(i.key, tries);
      if (p && tries <= MAX_RETRIES) {
        const wait = Math.max(result?.retryAfterMs || 0, 5000 * tries);
        setTimeout(() => { if (gen === generation) for (const el of p.els) if (keys.get(el) === i.key) { keys.delete(el); consider(el); } }, wait);
      }
      continue;
    }
    failures.delete(i.key);
    memo.set(i.key, swaps);
    if (p) for (const el of p.els) paint(el, i.key);
  }
  if (result?.stale) { try { update(await send({ type: 'config' })); } catch { /* reloaded or gone */ } return; }
  if ([...pending.keys()].some((k) => !inflight.has(k))) timer = setTimeout(flush, 50);
}
function track(root) {
  if (root.nodeType !== 1) return;
  const elements = root.matches(targets()) ? [root, ...root.querySelectorAll(targets())] : root.querySelectorAll(targets());
  for (const el of elements) {
    if (tracked.has(el) || !readable(el, location.href)) continue;
    tracked.add(el);
    near.observe(el);
  }
}
function mutations(records) {
  const changed = new Set();
  for (const r of records) {
    for (const n of r.addedNodes || []) if (n.nodeType === 1) track(n);
    const base = r.target.nodeType === 1 ? r.target : r.target.parentElement;
    const el = base?.closest(targets());
    if (el && tracked.has(el)) changed.add(el);
    else if (el) track(el);
  }
  for (const el of changed) {
    forget(el);
    if (el.querySelector(`.${SWAP_CLASS}`)) revert(el);
    keys.delete(el);
    if (readable(el, location.href)) { near.unobserve(el); near.observe(el); }
  }
  observer?.takeRecords();
  for (const el of tracked) if (!el.isConnected) { forget(el); tracked.delete(el); near.unobserve(el); }
  for (const span of visibleWords) if (!span.isConnected) { visibleWords.delete(span); visible.unobserve(span); }
}
function start() {
  if (observer || !on()) return;
  visible = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting && e.intersectionRatio >= 0.5) visibleWords.add(e.target);
      else visibleWords.delete(e.target);
    }
    seenVisible();
  }, { threshold: [0, 0.5] });
  onscreen = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (enoughOnScreen(e)) { inView.add(e.target); startStay(e.target); } else { inView.delete(e.target); endStay(e.target); }
    }
  }, { threshold: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1] });
  near = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting && !keys.has(e.target)) consider(e.target);
  }, { rootMargin: '600px' });
  observer = new MutationObserver(mutations);
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  track(document.documentElement);
}
function stop() {
  generation++;
  observer?.disconnect(); observer = null;
  near?.disconnect(); visible?.disconnect();
  clearTimeout(timer); timer = null;
  pending.clear(); inflight.clear(); memo.clear(); failures.clear(); hideTip();
  for (const passage of [...stays.keys()]) endStay(passage);
  onscreen?.disconnect(); inView.clear();
  for (const el of tracked) { revert(el); keys.delete(el); }
  tracked.clear(); visibleWords.clear();
}
function update(next, retry = false) {
  if (!next?.settings || !next.revision) return;
  if (!retry && next.revision === config?.revision) return;
  stop(); config = next; start();
}
function reveal(e) {
  const span = swapFor(e);
  if (!span || !on()) return;
  if (e.type === 'keydown' && !['Enter', ' '].includes(e.key)) return;
  e.preventDefault(); e.stopPropagation();
  hideTip();
  // Logged before the flip, while the word is still on the page. Flipping a phrase or sentence asks for its English as a
  // whole, so it is logged (with the word clicked) but is no evidence about one word for the word state.
  if (!showsEnglish(span)) {
    if (swapOf(span) === span) record(span, 'flip');
    logInteraction(span, 'flip', Date.now(), 0);
  }
  toggle(span);
  observer?.takeRecords();
}
chrome.runtime.onMessage.addListener((msg) => {
  if (['config', 'retry'].includes(msg?.type)) update(msg.config, msg.type === 'retry');
});
document.addEventListener('click', reveal, true);
document.addEventListener('click', clicked, { capture: true, passive: true });
document.addEventListener('mousedown', pressed, { capture: true, passive: true });
document.addEventListener('mouseup', selected, { capture: true, passive: true });
document.addEventListener('keyup', selected, { capture: true, passive: true });
document.addEventListener('keydown', reveal, true);
document.addEventListener('mouseover', armTip, true);
document.addEventListener('focusin', armTip, true);
document.addEventListener('mouseout', disarmTip, true);
document.addEventListener('focusout', disarmTip, true);
window.addEventListener('scroll', hideTip, { passive: true, capture: true });
document.addEventListener('visibilitychange', seenVisible);
document.addEventListener('visibilitychange', staysOnVisibility);
window.addEventListener('pagehide', () => { for (const passage of [...stays.keys()]) endStay(passage); });
for (const type of ['pointermove', 'pointerdown', 'wheel', 'keydown', 'touchstart']) {
  document.addEventListener(type, noteInput, { capture: true, passive: true });
}
window.addEventListener('online', () => { void send({ type: 'config' }).then((c) => update(c, true)).catch(() => {}); });
// SPA navigation can reuse DOM nodes without changing their text.
let page = location.href;
const navTimer = setInterval(() => {
  if (page !== location.href) { page = location.href; if (config) update(config, true); }
}, 1000);
void send({ type: 'config' }).then(update).catch(() => {});
