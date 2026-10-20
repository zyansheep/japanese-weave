// The toolbar button, per tab: its icon (grey where the page gets no Japanese, faded where the page has nothing to
// weave), its title (why, or the level), and its badge (the intensity level, or a moving "···" while a model works on
// the tab's passages). One appearance from all of these, so they never overwrite each other: off beats waiting, waiting
// beats nothing to weave, and the badge keeps the level throughout. The chrome.action API is passed in, so this tests
// without a browser.
import { INTENSITY_OFF, INTENSITY_FULL, intensityLabel } from './levels.js';

const icon = (name) => Object.fromEntries([16, 32, 48].map((size) => [size, `/icons/${name}-${size}.png`]));
export const ICONS = { on: icon('icon'), off: icon('off'), faded: icon('faded') };
export const WAIT_FRAMES = ['·', '··', '···'];
export const FRAME_MS = 400;
// A wait that outlives this is dropped (the call has its own timeout; this is only so the badge can never stick).
export const WAIT_MAX_MS = 180000;
export const COLORS = { heavier: '#b45309', lighter: '#2563eb', full: '#9a3412', off: '#6b7280', waiting: '#475569' };
const NAME = 'Japanese Weave';

const clock = (until) => new Date(until).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

// Why a tab is grey (settings.js offReason), as its title says it.
export function reasonText(reason, until = 0) {
  return {
    'switched-off': 'switched off (turn it on in the popup)',
    paused: 'paused from Claude Code',
    'intensity-off': `off until ${clock(until)} (Alt+Shift+Period or the popup turns it back on)`,
    'no-gemini-key': 'needs a Gemini key (add one in the popup)',
    'no-anthropic-key': 'needs an Anthropic key (add one in the popup)',
    unsupported: "can't run on this page",
    hidden: 'no access to this page (if it is a website, allow it in the popup)',
    'site-off': 'turned off on this site',
    'not-on': 'off on this site until you turn it on in the popup',
    private: 'off on private sites like this one until you turn it on in the popup',
    'no-access': 'no access to this site yet (allow it in the popup)',
  }[reason] || 'off on this page';
}

// Why a page has nothing to weave (content.js settled), as its title says it.
export function nothingText(why) {
  return { none: 'nothing on this page to weave (no English paragraphs, or only topics kept in English)',
    unfit: 'no Japanese fit the text on this page' }[why] || '';
}

// "+1", "−2", "off", "full", or '' at normal.
export function badgeText(level) {
  if (level === INTENSITY_OFF) return 'off';
  if (level === INTENSITY_FULL) return 'full';
  return level ? `${level > 0 ? '+' : '−'}${Math.abs(level)}` : '';
}
const badgeColor = (level) => (level === INTENSITY_OFF ? COLORS.off : level === INTENSITY_FULL ? COLORS.full
  : level > 0 ? COLORS.heavier : COLORS.lighter);

// What the button shows: { icon: 'on' | 'off' | 'faded', title, text, color }. A page that gets no Japanese is grey and
// says why, whatever else goes on; otherwise a model at work shows over the rest; otherwise a page with nothing to weave
// (nothing: 'none' or 'unfit', nothingText) is faded and says which; the badge shows the level in all but the wait.
export function appearance({ level = 0, until = 0, reason = '', waiting = false, frame = 0, nothing = '' } = {}) {
  const levelText = level ? `${intensityLabel(level)} until ${clock(until)}` : '';
  if (reason) {
    const extra = level && reason !== 'intensity-off' ? ` · ${levelText}` : '';
    return { icon: 'off', title: `${NAME}: ${reasonText(reason, until)}${extra}`, text: badgeText(level), color: badgeColor(level) };
  }
  if (waiting) {
    return { icon: 'on', title: `${NAME}: waiting for the model to weave this page${levelText ? ` · ${levelText}` : ''}`,
      text: WAIT_FRAMES[frame % WAIT_FRAMES.length], color: COLORS.waiting };
  }
  if (nothingText(nothing)) {
    return { icon: 'faded', title: `${NAME}: ${nothingText(nothing)}${levelText ? ` · ${levelText}` : ''}`, text: badgeText(level),
      color: badgeColor(level) };
  }
  return { icon: 'on', title: levelText ? `${NAME}: ${levelText}` : NAME, text: badgeText(level), color: badgeColor(level) };
}

// action: chrome.action (or browser.action); any part of it may be missing. timers: setInterval/clearInterval and
// setTimeout/clearTimeout, replaceable in tests.
export function createToolbar(action, timers = globalThis) {
  let global = { level: 0, until: 0, reason: '' }; // the defaults: tabs not drawn yet, and the level every tab shows
  const tabs = new Map(); // tab id -> { reason, waits: Map(token -> timeout), nothing }
  const drawn = new Map(); // tab id (or 'default') -> the appearance last applied, property by property
  let frame = 0;
  let ticker = null;
  let tokens = 0;

  const call = (name, arg) => Promise.resolve().then(() => action?.[name]?.(arg)).catch(() => {});
  async function apply(tabId, look) {
    const key = tabId ?? 'default';
    const before = drawn.get(key) || {};
    drawn.set(key, look);
    const target = tabId == null ? {} : { tabId };
    const jobs = [];
    if (before.icon !== look.icon) jobs.push(call('setIcon', { ...target, path: ICONS[look.icon] }));
    if (before.title !== look.title) jobs.push(call('setTitle', { ...target, title: look.title }));
    if (before.color !== look.color) jobs.push(call('setBadgeBackgroundColor', { ...target, color: look.color }));
    if (before.text !== look.text) jobs.push(call('setBadgeText', { ...target, text: look.text }));
    await Promise.all(jobs);
  }
  const entry = (tabId) => {
    if (!tabs.has(tabId)) tabs.set(tabId, { reason: global.reason, waits: new Map(), nothing: '' });
    return tabs.get(tabId);
  };
  const lookOf = (tab) => appearance({ ...global, reason: tab.reason, waiting: tab.waits.size > 0, frame, nothing: tab.nothing });
  const draw = (tabId) => (tabs.has(tabId) ? apply(tabId, lookOf(tabs.get(tabId))) : Promise.resolve());
  function animate() {
    const busy = [...tabs.values()].some((t) => t.waits.size);
    if (busy && !ticker) {
      ticker = timers.setInterval(() => {
        frame++;
        for (const [id, t] of tabs) if (t.waits.size && !t.reason) void draw(id);
      }, FRAME_MS);
    } else if (!busy && ticker) {
      timers.clearInterval(ticker);
      ticker = null;
      frame = 0;
    }
  }
  function release(tabId, token) {
    const tab = tabs.get(tabId);
    if (!tab?.waits.has(token)) return;
    timers.clearTimeout(tab.waits.get(token));
    tab.waits.delete(token);
    animate();
    void draw(tabId);
  }
  function clearWaits(tab) {
    for (const timeout of tab.waits.values()) timers.clearTimeout(timeout);
    tab.waits.clear();
  }
  function closed(tabId) {
    const tab = tabs.get(tabId);
    if (tab) clearWaits(tab);
    tabs.delete(tabId);
    drawn.delete(tabId);
    animate();
  }

  return {
    // The level and the reason every tab shares (settings.js globalOffReason), and per tab its own reason:
    // perTab [[tabId, reason]] for every open tab (a tab missing from it has closed).
    async show({ level = 0, until = 0, reason = '' } = {}, perTab = []) {
      global = { level, until, reason };
      const open = new Set(perTab.map(([id]) => id));
      for (const id of [...tabs.keys()]) if (!open.has(id)) closed(id);
      for (const [id, why] of perTab) entry(id).reason = why;
      await Promise.all([apply(null, appearance(global)), ...[...tabs.keys()].map(draw)]);
    },
    async tab(tabId, reason) {
      entry(tabId).reason = reason;
      await draw(tabId);
    },
    // A model call for this tab's passages starts; the returned function ends it (more than once is fine).
    wait(tabId) {
      if (tabId == null) return () => {};
      const token = ++tokens;
      entry(tabId).waits.set(token, timers.setTimeout(() => release(tabId, token), WAIT_MAX_MS));
      animate();
      void draw(tabId);
      return () => release(tabId, token);
    },
    // What the tab's page has to weave, once it settled (content.js): '' (something), 'none' or 'unfit' (nothingText).
    async nothing(tabId, why) {
      if (tabId == null) return;
      entry(tabId).nothing = nothingText(why) ? why : '';
      await draw(tabId);
    },
    // A new page in the tab: its waits and what the last page had to weave are over, and the browser may have reset the
    // tab's button (Firefox does).
    async navigated(tabId, reason, { loading = true } = {}) {
      const tab = entry(tabId);
      if (loading) { clearWaits(tab); tab.nothing = ''; }
      tab.reason = reason;
      drawn.delete(tabId);
      animate();
      await draw(tabId);
    },
    closed,
    waiting: (tabId) => (tabs.get(tabId)?.waits.size || 0) > 0,
  };
}
