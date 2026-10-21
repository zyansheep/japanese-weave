// Background (service worker in Chrome, event page in Firefox): word state and the model keys from the Claude Code plugin
// (native host), site access, the web pipeline for every tab's passages (code's candidates at once, one model check per
// batch of passages for the rest: pipeline.js, verify.js), results back to pages, seen/flip/peek events, the log and the
// reading counts back to the plugin.
// Keys stay here; pages only get a config without them.
import { createCache, chromeStorage } from './cache.js';
import { requestChecks, needsCall, candidatesOf, CHECK_VERSION } from './verify.js';
import { pipelineFor, planText, answerKey, storeAnswer, loadAnswer, readingIndexOf, readingCounts } from './pipeline.js';
import { candidatesFor, MAX_BATCH, BATCH, PARALLEL } from './models.js';
import { cleanSettings, newSitesDefault, offReason, globalOffReason } from './settings.js';
import { resolveLevel, exactJa, isZeroJapanese, wordCount, planSwaps, jitterFor, intensityBudget, isSentenceMark, wordsForLevel,
  INTENSITY_OFF, INTENSITY_FULL } from './levels.js';
import { hashKey } from './hash.js';
import { HOST, refresh, readCached, queueEvents, flushEvents, shownToday, cleanInteraction, queueLog, queuedLog, flushLog, localDay,
  cleanIntensity, refreshReadingIndex, queueReading, readToday, flushReading, INDEX_KEY } from './hostsource.js';
import { DAILY_MODEL_CALLS, callsToday, countCall } from './quota.js';
import { recordUsage, readUsage } from './usage.js';
import { createToolbar } from './toolbar.js';
// Firefox has the same API as a promise-based `browser`; Chrome only has `chrome`.
const chrome = globalThis.browser ?? globalThis.chrome;

const store = chrome.storage.local;
const cache = createCache(chromeStorage);
const send = (message) => chrome.runtime.sendNativeMessage(HOST, message);
const ALARM = 'sync';
const INTENSITY_ALARM = 'intensity'; // when a level away from normal ends
const INTENSITY_MS = 3 * 3600 * 1000; // as ../scripts/intensity.py DURATION_MS
const SYNC_MINUTES = 30; // word statuses change at most a few times a day
const FLUSH_DELAY_MS = 10000;
const FLUSH_AT = 200; // queued log items sent at once, before a long scroll fills the queue (hostsource LOG_MAX)
const cooldown = new Map(); // model -> rate-limit cooldown end; worker memory is enough
const KEY_NAMES = ['apiKey', 'hostKey', 'anthropicKey', 'hostAnthropicKey'];
// Pasted keys win over the ones setup stored.
const keysOf = (data) => ({ gemini: data.apiKey || data.hostKey || '', anthropic: data.anthropicKey || data.hostAnthropicKey || '' });

const serial = () => {
  let chain = Promise.resolve();
  return (fn) => { const p = chain.then(fn); chain = p.catch(() => {}); return p; };
};
const locked = serial(); // storage read-modify-writes one at a time, or concurrent ones drop data
// The cache's format: 2 since the check pipeline, a model's answer per passage keyed by what was asked (pipeline.js
// answerKey); before, every level's validated swaps per revision, which nothing reads now.
const CACHE_FORMAT = 2;

const ready = (async () => {
  // Content scripts run on every enabled site, so stored keys are kept out of their reach.
  if (store.setAccessLevel) await store.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  const { settings, cacheFormat } = await store.get(['settings', 'cacheFormat']);
  if (settings?.apiKey) await store.set({ apiKey: settings.apiKey }); // 0.1 kept a pasted key in settings
  await store.set({ settings: cleanSettings(settings) });
  if (cacheFormat !== CACHE_FORMAT) {
    await cache.clear();
    await store.set({ cacheFormat: CACHE_FORMAT });
  }
})();

// Intensity (heavier or lighter for a while, ../scripts/intensity.py): the newer of what this browser set and what the
// host last sent (set on another machine or with fw.py); an expired one is normal.
function intensityOf(data, now = Date.now()) {
  const latest = [cleanIntensity(data.intensity), data.wordState?.intensity].filter(Boolean).sort((a, b) => b.at - a.at)[0];
  return latest?.level && latest.until > now ? latest : { level: 0, until: 0, at: latest?.at || 0 };
}

// What pages get. The revision changes whenever anything that affects swaps does, so stale replies are dropped and pages
// repaint. The answers cache is keyed by what each passage asked (pipeline.js answerKey), not by the revision: a new
// word list, level, intensity or budget only changes which candidates planSwaps picks, and asks the model again only
// for passages whose doubtful spans changed. Off (-3) changes the revision too, so pages go back to English at once
// (settings.js offReason). hasKey: the chosen model or its fallback has a key; without one, pages get the swaps code
// is sure of alone.
async function context() {
  await ready;
  const data = await store.get(['settings', 'wordState', 'epoch', 'intensity', ...KEY_NAMES]);
  const state = data.wordState?.words?.length ? data.wordState : null;
  const saved = cleanSettings(data.settings);
  const settings = { ...saved, newSitesEnabled: newSitesDefault(saved, state?.sites) };
  const level = resolveLevel(saved, state?.stage).effective;
  const hasKey = candidatesFor(settings.model, keysOf(data)).length > 0;
  const paused = !!state?.paused;
  const { origins = [] } = await chrome.permissions.getAll();
  const intensity = intensityOf(data);
  const challenge = state?.challenge || 0;
  const revision = hashKey(JSON.stringify(['checks', CHECK_VERSION, data.epoch || '', settings, level, state?.version || '', paused,
    hasKey, origins, challenge, intensity.level]));
  return { settings, level, revision, challenge, hasKey, paused, origins, stage: state?.stage || 0,
    intensity: { level: intensity.level, until: intensity.until } };
}

const activeOn = (ctx, url) => !offReason(ctx, url);

async function broadcast(type = 'config') {
  const config = await context();
  const tabs = await chrome.tabs.query({});
  await Promise.allSettled(tabs.map((t) => chrome.tabs.sendMessage(t.id, { type, config })));
  await armIntensity(config.intensity);
  await showToolbar(config, tabs);
}
// The end of a level away from normal redraws every page, wherever it was set: here, or on another machine (the host's
// word state brings it).
const armIntensity = async ({ level, until }) => { if (level) await chrome.alarms.create(INTENSITY_ALARM, { when: until }); };

// The toolbar button (toolbar.js): grey with the reason where a tab gets no Japanese, "···" while a model works on a
// tab's passages, faded where its page has nothing to weave (the content script says so once the page settles), else
// the level. Redrawn for every tab whenever settings, the word state, the level or site access change (broadcast), and
// per tab when it is switched to, loads a page or closes.
const toolbar = createToolbar(chrome.action);
async function showToolbar(ctx, tabs) {
  ctx ??= await context();
  tabs ??= await chrome.tabs.query({});
  await toolbar.show({ ...ctx.intensity, reason: globalOffReason(ctx) }, tabs.map((t) => [t.id, offReason(ctx, t.url)]));
}
async function showTab(tabId, url, how) {
  const reason = offReason(await context(), url);
  if (how) await toolbar.navigated(tabId, reason, how);
  else await toolbar.tab(tabId, reason);
}
chrome.tabs.onActivated?.addListener(({ tabId }) => {
  void Promise.resolve(chrome.tabs.get?.(tabId)).then((tab) => showTab(tabId, tab?.url)).catch(() => {});
});
chrome.tabs.onUpdated?.addListener((tabId, change, tab) => {
  if (!change.url && !change.status) return;
  void showTab(tabId, tab?.url, { loading: change.status === 'loading' || !!change.url }).catch(() => {});
});
chrome.tabs.onRemoved?.addListener((tabId) => toolbar.closed(tabId));
// From the content script of a tab's top frame: '' (it has something to weave), 'none' (no passage it may weave) or
// 'unfit' (no Japanese fit the passages the model saw).
async function nothing(msg, sender) {
  if (sender.frameId || sender.tab?.id == null || !['', 'none', 'unfit'].includes(msg.why)) return { ok: false };
  await toolbar.nothing(sender.tab.id, msg.why);
  return { ok: true };
}

// The matcher's index and flags for the words in use (the host's, else the bundled list for the level), built again
// when a sync brings a new word state. words: what validation checks candidates against (levels.js validateSwaps;
// undefined for the bundled lists, which it knows by level).
let piped = null;
async function pipelineOf(ctx) {
  const { wordState } = await store.get(['wordState']);
  const own = wordState?.words?.length ? wordState.words : null;
  const key = own ? `state:${wordState.version}:${wordState.fetchedAt || 0}` : `level:${ctx.level}`;
  if (piped?.key !== key) piped = { key, pipe: pipelineFor(own || wordsForLevel(ctx.level)), words: own || undefined };
  return piped;
}

// A page's passages: each one's plan (pipeline.js), its candidates (verify.js candidatesOf) and the level's pick of them
// (planSwaps). Code's candidates need no model: the safe options, and with the model's cached answer its picks,
// sentences and phrases too. A passage that needs the model (needsCall) and has no cached answer gets its safe swaps
// at once, listed in `waiting`, and its call starts now; the page asks again with wait: true for what the answer adds,
// and that reply waits for the call. An item's keep (the swaps the page already shows) also gets `appended`: the plan
// redone around them, for a passage the reader has had on screen (content.js). Without a key no call is made: the safe
// swaps are all. A failed call leaves the safe swaps, with the error.
async function swaps(msg, sender) {
  const ctx = await context();
  if (msg.revision !== ctx.revision || !activeOn(ctx, sender.url)) return { results: {}, stale: true };
  if (!Array.isArray(msg.items) || msg.items.length > MAX_BATCH) throw new Error('Invalid text batch');
  const items = msg.items.filter((i) => i && typeof i.text === 'string' && i.text.length <= 2000 &&
    wordCount(i.text) >= 6 && !isZeroJapanese(i.text) && i.key === `${ctx.revision}:${hashKey(i.text)}`);
  const { pipe, words } = await pipelineOf(ctx);
  const planned = items.map((i) => {
    const plan = planText(pipe, i.text);
    const keep = Array.isArray(i.keep) ? i.keep.filter((x) => x && typeof x === 'object').slice(0, 100) : [];
    return { key: i.key, text: i.text, plan, keep, id: ctx.hasKey && needsCall(plan) ? answerKey(plan) : null };
  });
  const asked = planned.filter((p) => p.id);
  const stored = await cache.getMany(asked.map((p) => p.id));
  const choose = await chooser(ctx);
  const out = { results: {}, plans: {} };
  const pick = (p, answer) => {
    const candidates = candidatesOf(p.plan, answer, words, ctx.level);
    ({ swaps: out.results[p.key], plan: out.plans[p.key] } = choose(p.text, candidates));
    if (p.keep.length) (out.appended ||= {})[p.key] = choose(p.text, candidates, p.keep);
  };
  const misses = asked.filter((p) => !(p.id in stored));
  for (const p of planned) if (!misses.includes(p)) pick(p, p.id ? loadAnswer(p.plan, stored[p.id]) : null);
  if (!misses.length) return out;
  const batch = misses.map((p) => ({ id: p.id, plan: p.plan }));
  if (!msg.wait) {
    for (const p of misses) pick(p, null);
    out.waiting = misses.map((p) => p.key);
    void gather(ctx, batch).catch(() => {}); // the wait request hears how it went
    return out;
  }
  const done = toolbar.wait(sender.tab?.id); // the tab's button says a model is at work until this answer is in
  try {
    const fresh = await gather(ctx, batch);
    const now = await context();
    if (!fresh || now.revision !== ctx.revision) return { results: {}, stale: true };
    for (const p of misses) pick(p, loadAnswer(p.plan, fresh[p.id]));
    return out;
  } catch (e) {
    return { ...out, error: e?.message || 'Model request failed', retryAfterMs: e?.retryAfterMs };
  } finally {
    done();
  }
}

// Passages to check from every tab wait here and go to the model together, up to the model's batch size a call
// (models.js: BATCH, else MAX_BATCH) and one call at a time (PARALLEL for Haiku), so the passages that turn up while a
// call runs (scrolling, other tabs) share the next call and its prompt. A passage already waiting or in a call isn't
// asked for twice: its id is its answer's cache key. Resolves with { id: stored answer } for the batch, or null when the
// reader turned the Japanese off meanwhile.
const pending = new Map(); // passage id -> { ctx, p: { id, plan }, promise, resolve, reject }
const queue = []; // ids of pending passages not in a call yet
let active = 0;
function gather(ctx, batch) {
  for (const p of batch) {
    if (pending.has(p.id)) continue;
    const entry = { ctx, p };
    entry.promise = new Promise((resolve, reject) => Object.assign(entry, { resolve, reject }));
    entry.promise.catch(() => {}); // heard by whoever awaits it
    pending.set(p.id, entry);
    queue.push(p.id);
  }
  const answers = batch.map((p) => pending.get(p.id).promise);
  drain();
  return Promise.all(answers).then((all) => (all.includes(null) ? null : Object.fromEntries(batch.map((p, i) => [p.id, all[i]]))));
}
function drain() {
  while (queue.length) {
    const { ctx } = pending.get(queue[0]);
    const model = ctx.settings.model;
    if (active >= (PARALLEL[model] || 1)) return;
    const size = BATCH[model] || MAX_BATCH;
    const group = [];
    for (let i = 0; i < queue.length && group.length < size;) {
      const entry = pending.get(queue[i]);
      if (entry.ctx.settings.model !== model) { i++; continue; }
      queue.splice(i, 1);
      group.push(entry);
    }
    active++;
    void answer(ctx, group).finally(() => { active--; drain(); });
  }
}
// Each passage's promise gets its stored answer, or null if the call was dropped.
async function answer(ctx, group) {
  try {
    // A passage may have come back in an earlier call (another tab, the same text twice).
    const done = await cache.getMany(group.map((e) => e.p.id));
    const todo = group.filter((e) => !(e.p.id in done)).map((e) => e.p);
    const fresh = todo.length ? await callModel(ctx, todo) : {};
    for (const e of group) e.resolve(fresh && (done[e.p.id] ?? fresh[e.p.id]));
  } catch (err) {
    for (const e of group) e.reject(err);
  } finally {
    for (const e of group) pending.delete(e.p.id);
  }
}

// One model call for a batch; the answers go into the cache, keyed by what was asked, and the tokens into usage. None
// once the reader turned the Japanese off, paused it, or the key went.
async function callModel(ctx, batch) {
  const current = await context();
  if (globalOffReason(current) || !current.hasKey) return null;
  let fresh;
  try {
    if (await callsToday(chromeStorage) >= DAILY_MODEL_CALLS) throw new Error(`Daily limit of ${DAILY_MODEL_CALLS} model calls reached; Japanese resumes tomorrow`);
    const data = await store.get(KEY_NAMES);
    const fetchImpl = async (...args) => { await countCall(chromeStorage); return fetch(...args); };
    const { words } = await pipelineOf(current);
    fresh = await requestChecks({ keys: keysOf(data), model: ctx.settings.model, batch, words, level: current.level, fetchImpl, cooldown });
  } catch (e) {
    await locked(async () => {
      await recordSpent(e?.usage);
      await store.set({ modelStatus: { at: Date.now(), error: e?.message || 'Model request failed' } });
    });
    throw e;
  }
  return locked(async () => {
    await recordSpent(fresh.usage);
    const stored = Object.fromEntries(batch.map((p) => [p.id, storeAnswer(p.plan, fresh.answers[p.id])]));
    await cache.setMany(stored);
    await store.set({ modelStatus: { at: Date.now(), error: '', model: fresh.model } });
    return stored;
  });
}

// Picks each passage's swaps from its candidates (planSwaps): the level's lookup budget (normal's from the host: the
// challenge when set, else intensity.py's), the host's per-word estimates and price of a whole sentence (else
// levels.js SENTENCE_PRICE), the intensity and the passage's random thinning of free words, seeded by the passage and
// the day so a passage repaints the same way; keep: swaps the page already shows. Returns { swaps, plan }.
async function chooser(ctx) {
  const { wordState } = await store.get(['wordState']);
  const day = localDay();
  const shown = await shownToday(chromeStorage, day);
  const budgetPer100 = intensityBudget(ctx.intensity.level, wordState?.budget);
  return (text, swaps, keep = []) => {
    const seed = parseInt(hashKey(`${day}:${text}`).slice(-8), 16);
    return planSwaps(ctx.level, swaps, text, wordState?.words, { budgetPer100, sentencePrice: wordState?.sentencePrice,
      shownToday: shown, seed, intensity: ctx.intensity.level, jitter: jitterFor(seed), now: Date.now(), recency: wordState?.recency, keep });
  };
}

// The reading index (hostsource.js refreshReadingIndex) as a matcher index, built once per version a worker sees.
let reader = null;
async function readingIndex() {
  const { wordState } = await store.get(['wordState']);
  if (reader && reader.version === wordState?.reading) return reader.index;
  const stored = (await store.get([INDEX_KEY]))[INDEX_KEY];
  if (!stored?.entries?.length) return null;
  reader = { version: stored.version, index: readingIndexOf(stored.entries) };
  return reader.index;
}

// A passage the page says was read (content.js: a stay on screen long enough to log), counted once a day: the
// dictionary entries its safe matches show and the doubtful ones the model confirmed (pipeline.js readingCounts), from
// the cached answer or the call under way; none asked for just to count. Counts only: queued for the host, never text.
async function read(msg, sender) {
  const ctx = await context();
  const text = msg.text;
  if (!activeOn(ctx, sender.url) || typeof text !== 'string' || text.length > 2000 || wordCount(text) < 6 || isZeroJapanese(text)) {
    return { ok: false };
  }
  const day = localDay();
  const d = `web:${hashKey(text)}`;
  if (await readToday(chromeStorage, d, day)) return { ok: true, counted: false };
  const { pipe, words } = await pipelineOf(ctx);
  const plan = planText(pipe, text);
  let answer = null;
  if (ctx.hasKey && needsCall(plan)) {
    const id = answerKey(plan);
    const stored = (await cache.getMany([id]))[id] ?? (pending.has(id) ? await pending.get(id).promise.catch(() => null) : null);
    answer = loadAnswer(plan, stored);
  }
  const entries = readingCounts({ text, reading: await readingIndex(), plan, answer, words, level: ctx.level });
  if (!Object.keys(entries).length) return { ok: true, counted: false };
  const counted = await locked(() => queueReading(chromeStorage, { d, day, entries }));
  if (counted) scheduleFlush();
  return { ok: true, counted };
}

// Every billed attempt counts toward usage, including an answer that was unusable before the fallback.
async function recordSpent(spent) {
  for (const { model, ...tokens } of spent || []) await recordUsage(chromeStorage, model, tokens);
}

const flush = () => locked(async () => {
  const result = await flushEvents({ storage: chromeStorage, send });
  await flushLog({ storage: chromeStorage, send });
  await flushReading({ storage: chromeStorage, send });
  return result;
});
let flushTimer = null;
function scheduleFlush() {
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flush, FLUSH_DELAY_MS);
}

// Events only for words in the plugin's word state; queueEvents keeps seen and peek to once per word per day.
async function record(msg, sender) {
  const ctx = await context();
  if (!activeOn(ctx, sender.url) || !['seen', 'flip', 'peek'].includes(msg.event) || typeof msg.word !== 'string') return { ok: false };
  const word = (await readCached(chromeStorage))?.words.find((w) => exactJa(w.ja) === exactJa(msg.word));
  if (!word) return { ok: false };
  await locked(() => queueEvents(chromeStorage, [{ word: word.ja, kind: msg.event }]));
  scheduleFlush();
  return { ok: true };
}

// Gloss interactions for the host's log (see cleanInteraction), only for words in the plugin's word state and a
// sentence's closing mark (its translation opened).
async function logInteraction(msg, sender) {
  const ctx = await context();
  const item = activeOn(ctx, sender.url) ? cleanInteraction(msg) : null;
  const listed = (ja) => readCached(chromeStorage).then((s) => !!s?.words.some((w) => exactJa(w.ja) === exactJa(ja)));
  if (!item || (item.word !== undefined && !isSentenceMark(item.word) && !(await listed(item.word)))) return { ok: false };
  const queued = await locked(() => queueLog(chromeStorage, [item]));
  if (queued >= FLUSH_AT) void flush();
  else scheduleFlush();
  return { ok: true };
}

// A new word state repaints pages (its version is in the revision); the answers cache stays, since each answer is keyed
// by what its passage asked. Then the reading index, when the host has a new one.
async function syncWords() {
  await flush();
  const res = await refresh({ storage: chromeStorage, send });
  await broadcast();
  if (res.state?.reading) await refreshReadingIndex({ storage: chromeStorage, send, version: res.state.reading });
  return { ok: !res.error, error: res.error, count: res.state?.words?.length || 0, stage: res.state?.stage || 0 };
}

// The challenge (normal's lookups per 100 words shown, 0: intensity.py's default budget) lives in the word state, shared
// with chat.
async function setChallenge(msg) {
  const reply = await send({ type: 'prefs', challenge: msg.challenge });
  if (!reply?.ok) return { ok: false, error: reply?.error || 'the Japanese Weave host refused the setting' };
  return syncWords();
}

async function clearCache() {
  const removed = await locked(async () => {
    await store.set({ epoch: crypto.randomUUID() });
    return cache.clear();
  });
  await broadcast('retry');
  return { ok: true, removed };
}

// msg.settings: a partial patch; msg.siteOverride: { host, enabled: true | false | null (reset) }; msg.apiKey (Gemini),
// msg.anthropicKey.
async function save(msg) {
  await locked(async () => {
    const saved = await store.get('settings');
    const merged = { ...saved.settings, ...msg.settings };
    if (msg.siteOverride && typeof msg.siteOverride.host === 'string') {
      merged.siteOverrides = { ...saved.settings?.siteOverrides };
      if (msg.siteOverride.enabled === null) delete merged.siteOverrides[msg.siteOverride.host];
      else merged.siteOverrides[msg.siteOverride.host] = msg.siteOverride.enabled;
    }
    const patch = { settings: cleanSettings(merged) };
    for (const name of ['apiKey', 'anthropicKey']) {
      if (typeof msg[name] !== 'string') continue;
      if (msg[name].length > 500) throw new Error('Key is too long');
      patch[name] = msg[name].trim();
    }
    await store.set(patch);
  });
  await broadcast();
  return { ok: true };
}

// How much the host's log holds (on the collector when one is set), for the popup; a missing or old host says so.
async function stats() {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const queued = await queuedLog(chromeStorage);
  try {
    return { ...(await send({ type: 'stats', since: midnight.getTime() })), queued };
  } catch (e) {
    return { ok: false, error: String(e?.message || e), queued };
  }
}

// One press of the heavier/lighter shortcut (or the popup): step 1 heavier, -1 lighter, 0 back to normal, for
// INTENSITY_MS from now; at off (-3) or full (3) a press further that way keeps the level and restarts the clock.
// Takes effect here at once (every level picks from the same candidates and cached answers; off: pages back to English,
// no model calls), and goes to the host so the change is logged with the word state (calibration).
async function stepIntensity(step, source) {
  if (![-1, 0, 1].includes(step)) throw new Error('step is 1, -1 or 0');
  const now = Date.now();
  const current = intensityOf(await store.get(['intensity', 'wordState']), now);
  const level = step ? Math.max(INTENSITY_OFF, Math.min(INTENSITY_FULL, current.level + step)) : 0;
  const value = { level, until: level ? now + INTENSITY_MS : 0, at: now };
  await store.set({ intensity: value });
  if (!level) await chrome.alarms.clear(INTENSITY_ALARM); // else broadcast sets it for the new end
  void send({ type: 'intensity', surface: 'browser', set: { ...value, source } }).catch(() => {});
  await showToast(value);
  await broadcast();
  return { ok: true, intensity: { level, until: value.until } };
}
// A press shows a toast on the page in front; the toolbar badge (broadcast) says the level while it lasts.
async function showToast({ level, until }) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id != null) await chrome.tabs.sendMessage(tab.id, { type: 'intensity', intensity: { level, until } }).catch(() => {});
}
chrome.commands?.onCommand.addListener((command) => {
  const step = { 'intensity-up': 1, 'intensity-down': -1 }[command];
  if (step) void ready.then(() => stepIntensity(step, 'shortcut')).catch(() => {});
});
// After a restart the level may still run: its end comes back, and every tab's button is drawn afresh, which also
// clears a "···" left from a worker that stopped during a call.
void ready.then(async () => {
  await armIntensity(intensityOf(await store.get(['intensity', 'wordState'])));
  await showToolbar();
}).catch(() => {});

async function status() {
  const config = await context();
  const data = await store.get(['wordState', 'modelStatus', ...KEY_NAMES]);
  return { config, words: data.wordState?.words?.length || 0, fetchedAt: data.wordState?.fetchedAt || 0,
    budget: data.wordState?.budget || 0,
    pastedKey: !!data.apiKey, hostKey: !!data.hostKey, pastedAnthropicKey: !!data.anthropicKey,
    hostAnthropicKey: !!data.hostAnthropicKey, modelStatus: data.modelStatus || null,
    usage: await readUsage(chromeStorage), intensity: config.intensity };
}

// Pages may only ask for config and swaps, and send events, interactions, passages read and what they have to weave;
// everything else is popup-only.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || sender.id !== chrome.runtime.id) return false;
  const popup = sender.url === chrome.runtime.getURL('src/popup.html');
  const page = sender.tab && /^https?:\/\//.test(sender.url || '');
  const handlers = {
    config: context,
    swaps: () => swaps(msg, sender),
    event: () => record(msg, sender),
    interaction: () => logInteraction(msg, sender),
    nothing: () => nothing(msg, sender),
    read: () => read(msg, sender),
    status, stats, syncWords, clearCache,
    save: () => save(msg),
    setChallenge: () => setChallenge(msg),
    intensity: () => stepIntensity(msg.step, 'popup'),
  };
  if (!handlers[msg.type] || (!popup && !(page && ['config', 'swaps', 'event', 'interaction', 'nothing', 'read'].includes(msg.type)))) return false;
  ready.then(handlers[msg.type]).then(reply, (e) => reply({ error: String(e?.message || e) }));
  return true;
});

// Granted origins (all sites, or one at a time from the popup) decide where the content script is injected.
// X is in the manifest, so it works without a grant.
let registrations = Promise.resolve();
function registerSites() {
  const p = registrations.then(async () => {
    const { origins = [] } = await chrome.permissions.getAll();
    const matches = [...new Set(origins.flatMap((o) => o === '<all_urls>' ? ['http://*/*', 'https://*/*'] : [o]))]
      .filter((o) => /^(https?|\*):\/\//.test(o) && !o.includes('googleapis.com') && !o.includes('api.anthropic.com'));
    const existing = await chrome.scripting.getRegisteredContentScripts({ ids: ['weave-sites'] });
    if (!matches.length) {
      if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: ['weave-sites'] });
      return;
    }
    const script = { id: 'weave-sites', matches, js: ['src/content-loader.js'], css: ['src/content.css'], runAt: 'document_idle',
      excludeMatches: ['https://x.com/*', 'https://twitter.com/*'] };
    if (existing.length) await chrome.scripting.updateContentScripts([script]);
    else await chrome.scripting.registerContentScripts([script]);
  });
  registrations = p.catch(() => {});
  return p;
}
const permissionsChanged = () => { void registerSites().then(() => broadcast('retry')).catch(() => {}); };
chrome.permissions.onAdded.addListener(permissionsChanged);
chrome.permissions.onRemoved.addListener(permissionsChanged);

// create() replaces an alarm of the same name, so calling it on both events is safe. Startup matters
// because disabling and re-enabling the extension clears alarms without firing onInstalled.
function arm() {
  chrome.alarms.create(ALARM, { periodInMinutes: SYNC_MINUTES });
  void syncWords().catch(() => {});
}
chrome.runtime.onInstalled.addListener(arm);
chrome.runtime.onStartup.addListener(arm);
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === ALARM) void syncWords().catch(() => {});
  if (a.name === INTENSITY_ALARM) void broadcast().catch(() => {}); // the level ran out: back to normal everywhere
});
void ready.then(registerSites).catch(() => {});
