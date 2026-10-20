// Background (service worker in Chrome, event page in Firefox): word state and the model keys from the Claude Code plugin
// (native host), site access, shared model calls for every tab's passages together, results back to pages,
// seen/flip/peek events back to the plugin.
// Keys stay here; pages only get a config without them.
import { createCache, chromeStorage } from './cache.js';
import { requestSwaps } from './engine.js';
import { PROMPT_VERSION } from './prompt.js';
import { candidatesFor, MAX_BATCH, BATCH, PARALLEL } from './models.js';
import { cleanSettings, newSitesDefault, offReason, globalOffReason } from './settings.js';
import { resolveLevel, exactJa, isZeroJapanese, wordCount, planSwaps, jitterFor, INTENSITY_OFF, INTENSITY_FULL } from './levels.js';
import { hashKey } from './hash.js';
import { HOST, refresh, readCached, queueEvents, flushEvents, shownToday, cleanInteraction, queueLog, queuedLog, flushLog, localDay, cleanIntensity } from './hostsource.js';
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

const ready = (async () => {
  // Content scripts run on every enabled site, so stored keys are kept out of their reach.
  if (store.setAccessLevel) await store.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  const { settings } = await store.get('settings');
  if (settings?.apiKey) await store.set({ apiKey: settings.apiKey }); // 0.1 kept a pasted key in settings
  await store.set({ settings: cleanSettings(settings) });
})();

// Intensity (heavier or lighter for a while, ../scripts/intensity.py): the newer of what this browser set and what the
// host last sent (set on another machine or with fw.py); an expired one is normal.
function intensityOf(data, now = Date.now()) {
  const latest = [cleanIntensity(data.intensity), data.wordState?.intensity].filter(Boolean).sort((a, b) => b.at - a.at)[0];
  return latest?.level && latest.until > now ? latest : { level: 0, until: 0, at: latest?.at || 0 };
}

// What pages get. The revision changes whenever anything that affects swaps does, so stale replies are dropped. The
// cache is keyed by cacheRevision, which leaves out the adaptive-difficulty dial and a lighter intensity: the cache
// holds every validated candidate and those only change which of them planSwaps picks, so they never cost a model
// call. A heavier intensity, and full, ask the model for more, so each has its own cache. Off (-3) changes the revision
// too, so pages go back to English at once (settings.js offReason).
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
  const heavy = Math.max(0, intensity.level);
  const cacheRevision = hashKey(JSON.stringify(['candidates', PROMPT_VERSION, data.epoch || '', settings, level, state?.version || '', paused, hasKey, origins, ...(heavy ? [heavy] : [])]));
  const challenge = state?.challenge || 0;
  const revision = hashKey(JSON.stringify([cacheRevision, challenge, intensity.level]));
  return { settings, level, revision, cacheRevision, challenge, hasKey, paused, origins, stage: state?.stage || 0,
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
// tab's passages, else the level. Redrawn for every tab whenever settings, the word state, the level or site access
// change (broadcast), and per tab when it is switched to, loads a page or closes.
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

async function swaps(msg, sender) {
  const ctx = await context();
  if (msg.revision !== ctx.revision || !activeOn(ctx, sender.url)) return { results: {}, stale: true };
  if (!Array.isArray(msg.items) || msg.items.length > MAX_BATCH) throw new Error('Invalid text batch');
  const items = msg.items.filter((i) => i && typeof i.text === 'string' && i.text.length <= 2000 &&
    wordCount(i.text) >= 6 && !isZeroJapanese(i.text) && i.key === `${ctx.revision}:${hashKey(i.text)}`);
  const cacheKey = (i) => `${ctx.cacheRevision}:${hashKey(i.text)}`;
  const cached = await cache.getMany(items.map(cacheKey));
  const choose = await chooser(ctx);
  // { results: { key: swaps }, plans: { key: plan } } for the items with candidates
  const picked = (candidates) => {
    const out = { results: {}, plans: {} };
    for (const i of items.filter((x) => cacheKey(x) in candidates)) {
      const { swaps: chosen, plan } = choose(i.text, candidates[cacheKey(i)]);
      out.results[i.key] = chosen;
      out.plans[i.key] = plan;
    }
    return out;
  };
  const misses = items.filter((i) => !(cacheKey(i) in cached));
  if (!misses.length) return picked(cached);
  const done = toolbar.wait(sender.tab?.id); // the tab's button says a model is at work until this answer is in
  try {
    const fresh = await gather(ctx, misses.map((i) => ({ id: cacheKey(i), text: i.text })));
    const now = await context();
    if (!fresh || now.revision !== ctx.revision) return { results: {}, stale: true };
    return picked({ ...cached, ...fresh });
  } catch (e) {
    return { ...picked(cached), error: e?.message || 'Model request failed', retryAfterMs: e?.retryAfterMs };
  } finally {
    done();
  }
}

// Cache misses from every tab wait here and go to the model together, up to the model's batch size a call (models.js:
// BATCH, else MAX_BATCH) and one call at a time (PARALLEL for Haiku), so the passages that turn up while a call runs
// (scrolling, other tabs) share the next call and its prompt. A passage already waiting or in a call isn't asked for
// twice. Resolves with { cacheKey: swaps } for the batch, or null when the word state or settings changed meanwhile.
// Passage ids are cache keys, which start with the cacheRevision, so one id never mixes two revisions.
const pending = new Map(); // passage id -> { ctx, p: { id, text }, promise, resolve, reject }
const queue = []; // ids of pending passages not in a call yet
let active = 0;
function gather(ctx, batch) {
  for (const p of batch) {
    if (pending.has(p.id)) continue;
    const entry = { ctx, p };
    entry.promise = new Promise((resolve, reject) => Object.assign(entry, { resolve, reject }));
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
      if (entry.ctx.cacheRevision !== ctx.cacheRevision) { i++; continue; }
      queue.splice(i, 1);
      group.push(entry);
    }
    active++;
    void answer(ctx, group).finally(() => { active--; drain(); });
  }
}
// Each passage's promise gets its swaps, or null if the call's answer is stale.
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

// One model call for a batch; the swaps go into the cache and the tokens into usage. None once the reader turned the
// Japanese off, which a lighter level's cacheRevision doesn't show.
async function callModel(ctx, batch) {
  const current = await context();
  if (current.cacheRevision !== ctx.cacheRevision || current.intensity.level === INTENSITY_OFF) return null;
  let fresh;
  try {
    if (await callsToday(chromeStorage) >= DAILY_MODEL_CALLS) throw new Error(`Daily limit of ${DAILY_MODEL_CALLS} model calls reached; Japanese resumes tomorrow`);
    const data = await store.get(['wordState', ...KEY_NAMES]);
    const fetchImpl = async (...args) => { await countCall(chromeStorage); return fetch(...args); };
    fresh = await requestSwaps({ keys: keysOf(data), model: ctx.settings.model, level: ctx.level, stage: data.wordState?.stage,
      words: data.wordState?.words, fetchImpl, shownToday: await shownToday(chromeStorage), cooldown, batch, density: ctx.settings.density,
      heavy: Math.max(0, ctx.intensity.level) });
  } catch (e) {
    await locked(async () => {
      await recordSpent(e?.usage);
      await store.set({ modelStatus: { at: Date.now(), error: e?.message || 'Model request failed' } });
    });
    throw e;
  }
  return locked(async () => {
    await recordSpent(fresh.usage);
    if ((await context()).cacheRevision !== ctx.cacheRevision) return null;
    await cache.setMany(fresh.results); // still good candidates if only the dial moved meanwhile
    await store.set({ modelStatus: { at: Date.now(), error: '', model: fresh.model } });
    return fresh.results;
  });
}

// Picks each passage's swaps from its cached candidates (planSwaps): the dial's budget, the host's per-word estimates,
// the intensity and the passage's random thinning, seeded by the passage and the day so a passage repaints the same
// way. Returns { swaps, plan }.
async function chooser(ctx) {
  const { wordState } = await store.get(['wordState']);
  const day = localDay();
  const shown = await shownToday(chromeStorage, day);
  const budgetPer100 = ctx.challenge ? wordState?.budget || ctx.challenge : 0;
  return (text, swaps) => {
    const seed = parseInt(hashKey(`${day}:${text}`).slice(-8), 16);
    return planSwaps(ctx.level, swaps, text, wordState?.words, { density: ctx.settings.density, budgetPer100,
      shownToday: shown, seed, intensity: ctx.intensity.level, jitter: jitterFor(seed) });
  };
}

// Every billed attempt counts toward usage, including an answer that was unusable before the fallback.
async function recordSpent(spent) {
  for (const { model, ...tokens } of spent || []) await recordUsage(chromeStorage, model, tokens);
}

const flush = () => locked(async () => {
  const result = await flushEvents({ storage: chromeStorage, send });
  await flushLog({ storage: chromeStorage, send });
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

// Gloss interactions for the host's log (see cleanInteraction), only for words in the plugin's word state.
async function logInteraction(msg, sender) {
  const ctx = await context();
  const item = activeOn(ctx, sender.url) ? cleanInteraction(msg) : null;
  const listed = (ja) => readCached(chromeStorage).then((s) => !!s?.words.some((w) => exactJa(w.ja) === exactJa(ja)));
  if (!item || (item.word !== undefined && !(await listed(item.word)))) return { ok: false };
  const queued = await locked(() => queueLog(chromeStorage, [item]));
  if (queued >= FLUSH_AT) void flush();
  else scheduleFlush();
  return { ok: true };
}

// A new word-state version clears the decision cache, because cached swaps were chosen against the old list.
async function syncWords() {
  await flush();
  const res = await refresh({ storage: chromeStorage, send });
  if (res.changed) await locked(() => cache.clear());
  await broadcast();
  return { ok: !res.error, error: res.error, count: res.state?.words?.length || 0, stage: res.state?.stage || 0 };
}

// The adaptive-difficulty dial (peeks per 100 words shown, 0: off) lives in the word state, shared with chat.
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
// Takes effect here at once (lighter: from the cached candidates; heavier and full: a model call per passage; off:
// pages back to English, no model calls), and goes to the host so the change is logged with the word state
// (calibration).
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
    budget: data.wordState?.budget || 0, rate: data.wordState?.rate || null,
    pastedKey: !!data.apiKey, hostKey: !!data.hostKey, pastedAnthropicKey: !!data.anthropicKey,
    hostAnthropicKey: !!data.hostAnthropicKey, modelStatus: data.modelStatus || null,
    usage: await readUsage(chromeStorage), intensity: config.intensity };
}

// Pages may only ask for config, swaps, events and interactions; everything else is popup-only.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || sender.id !== chrome.runtime.id) return false;
  const popup = sender.url === chrome.runtime.getURL('src/popup.html');
  const page = sender.tab && /^https?:\/\//.test(sender.url || '');
  const handlers = {
    config: context,
    swaps: () => swaps(msg, sender),
    event: () => record(msg, sender),
    interaction: () => logInteraction(msg, sender),
    status, stats, syncWords, clearCache,
    save: () => save(msg),
    setChallenge: () => setChallenge(msg),
    intensity: () => stepIntensity(msg.step, 'popup'),
  };
  if (!handlers[msg.type] || (!popup && !(page && ['config', 'swaps', 'event', 'interaction'].includes(msg.type)))) return false;
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
