import { resolveLevel } from './levels.js';
import { MODELS } from './gemini.js';
import { siteEnabled, siteOn, hasSiteAccess, DENSITIES, CHALLENGES } from './settings.js';
// Firefox has the same API as a promise-based `browser`; Chrome only has `chrome`.
const chrome = globalThis.browser ?? globalThis.chrome;

const $ = (id) => document.getElementById(id);
const ALL_SITES = ['http://*/*', 'https://*/*'];
let status;
let tab;
let host = '';

const send = async (msg) => {
  const res = await chrome.runtime.sendMessage(msg);
  if (res?.error && !('ok' in res)) throw new Error(res.error);
  return res;
};

const tokens = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n));
const usd = (n) => (n >= 1 ? `$${n.toFixed(2)}` : n >= 0.01 ? `$${n.toFixed(3)}` : n > 0 ? '<$0.01' : '$0');
const usageLine = (u) => (u?.calls ? `${u.calls} calls · ${tokens(u.input + u.output)} tokens · ~${usd(u.usd)}` : 'none yet');

function ago(t) {
  if (!t) return '';
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

async function refresh() {
  status = await send({ type: 'status' });
  const c = status.config;
  const s = c.settings; // newSitesEnabled resolved: popup choice, else setup's answer, else on
  const all = await chrome.permissions.contains({ origins: ALL_SITES });
  $('enabled').checked = s.enabled;
  $('newSitesEnabled').value = String(s.newSitesEnabled);
  $('siteEnabled').checked = !!host && siteOn(s, host) && hasSiteAccess(tab.url, c.origins);
  $('siteEnabled').disabled = !host;
  $('siteName').textContent = host || 'This page (not a website)';
  $('grantSites').hidden = all || !s.newSitesEnabled;
  $('accessNote').textContent = s.newSitesEnabled
    ? (all ? 'On for every site except the ones you turn off. Webmail, AI chat and local addresses start off.' : 'Your browser needs your OK before this can run on other sites.')
    : 'Off on sites until you turn them on here.';
  const { stage, chosen, effective } = resolveLevel(s, c.stage);
  const amount = s.density ? `${s.density}% of words` : effective <= 1 ? '~1 word per 1–2 sentences' : '1–2 words per sentence';
  $('levelInfo').textContent = `L${chosen}${stage && chosen !== stage ? ` (stage ${stage})` : ''} · ${amount}`;
  $('density').value = String(s.density || 0);
  $('challenge').value = String(c.challenge || 0);
  $('challenge').disabled = !status.words; // the dial lives in the native host's word state
  const rate = status.rate;
  $('rateInfo').textContent = rate ? `${rate.per100} per 100 words (${rate.peeks} in ${rate.shownWords}, ${rate.days} days)`
    : status.words ? 'nothing logged yet' : 'needs the native host';
  $('usageToday').textContent = usageLine(status.usage?.today);
  $('usageTotal').textContent = status.usage?.total?.calls ? `${usageLine(status.usage.total)} since ${status.usage.since}` : 'none yet';
  $('levelOverride').value = String(s.levelOverride || 0);
  $('model').value = s.model;
  $('apiKey').value = '';
  $('apiKey').placeholder = status.pastedKey ? 'saved (paste to replace)' : status.hostKey ? 'from setup (paste to override)' : 'AIza…';
  $('wordInfo').textContent = status.words
    ? `${status.words} from Claude Code · ${ago(status.fetchedAt)}${c.paused ? ' · paused' : ''}`
    : 'built-in list';
  $('operation').textContent = !s.enabled ? 'Off'
    : !c.hasKey ? 'Add a Gemini key to start'
    : c.paused ? 'Paused from Claude Code'
    : host && !siteEnabled(s, tab.url) ? 'Off on this site'
    : host && !hasSiteAccess(tab.url, c.origins) ? 'This site needs your OK'
    : status.modelStatus?.error || 'On';
  const list = $('siteList');
  list.replaceChildren();
  for (const [name, enabled] of Object.entries(s.siteOverrides)) {
    const row = document.createElement('div');
    row.className = 'row';
    const label = document.createElement('span');
    label.textContent = `${name}: ${enabled ? 'on' : 'off'}`;
    const button = document.createElement('button');
    button.textContent = 'Reset';
    button.addEventListener('click', () => run(() => save({ siteOverride: { host: name, enabled: null } })));
    row.append(label, button);
    list.append(row);
  }
  if (!list.childNodes.length) list.textContent = 'None';
}

async function save(msg) {
  await send({ type: 'save', ...msg });
  await refresh();
}

async function run(fn) {
  $('notice').textContent = '';
  try { await fn(); } catch (e) { $('notice').textContent = e.message || 'Could not do that'; }
}

// The registered content script only reaches pages loaded after a grant; this covers the open tab.
async function inject() {
  if (!tab?.id || !host) return;
  try {
    await chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ['src/content.css'] });
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['src/content-loader.js'] });
  } catch { $('notice').textContent = 'Reload this page to start. Browsers block extensions on some pages.'; }
}

for (const value of DENSITIES) {
  const opt = document.createElement('option');
  opt.value = String(value);
  opt.textContent = value ? `${value}% of words` : 'Auto (by level)';
  $('density').appendChild(opt);
}

for (const value of CHALLENGES) {
  const opt = document.createElement('option');
  opt.value = String(value);
  opt.textContent = value ? `${value} peeks per 100 words` : 'Off';
  $('challenge').appendChild(opt);
}

for (const [id, label] of Object.entries(MODELS)) {
  const opt = document.createElement('option');
  opt.value = id;
  opt.textContent = label;
  $('model').appendChild(opt);
}

$('enabled').addEventListener('change', (e) => run(() => save({ settings: { enabled: e.target.checked } })));
$('newSitesEnabled').addEventListener('change', (e) => run(() => save({ settings: { newSitesEnabled: e.target.value === 'true' } })));
$('levelOverride').addEventListener('change', (e) => run(() => save({ settings: { levelOverride: Number(e.target.value) || 0 } })));
$('model').addEventListener('change', (e) => run(() => save({ settings: { model: e.target.value } })));
$('density').addEventListener('change', (e) => run(() => save({ settings: { density: Number(e.target.value) || 0 } })));
$('challenge').addEventListener('change', (e) => run(async () => {
  const res = await send({ type: 'setChallenge', challenge: Number(e.target.value) });
  await refresh();
  if (!res?.ok) $('notice').textContent = `Could not set the challenge (${res?.error || 'no native host'})`;
}));
$('apiKey').addEventListener('change', (e) => run(() => save({ apiKey: e.target.value })));
$('siteEnabled').addEventListener('change', (e) => run(async () => {
  const enabled = e.target.checked;
  if (enabled && !hasSiteAccess(tab.url, status.config.origins)) {
    // The browser asks for this one site only; requests need the click that opened this, so it happens first.
    const accepted = await chrome.permissions.request({ origins: [`${new URL(tab.url).protocol}//${host}/*`] });
    if (!accepted) { await refresh(); return; }
  }
  await save({ siteOverride: { host, enabled } });
  if (enabled) await inject();
}));
$('grantSites').addEventListener('click', () => run(async () => {
  if (await chrome.permissions.request({ origins: ALL_SITES })) { await refresh(); await inject(); }
}));
$('syncWords').addEventListener('click', () => run(async () => {
  $('wordInfo').textContent = 'syncing…';
  const res = await send({ type: 'syncWords' });
  await refresh();
  if (!res?.ok) $('notice').textContent = `No Claude Code sync (${res?.error || 'failed'})`;
}));
$('clearCache').addEventListener('click', () => run(async () => {
  const res = await send({ type: 'clearCache' });
  await refresh();
  $('operation').textContent = `Cleared ${res.removed}`;
}));

void run(async () => {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try { const u = new URL(tab?.url); if (/^https?:$/.test(u.protocol)) host = u.hostname; } catch { /* internal tab */ }
  await refresh();
});
