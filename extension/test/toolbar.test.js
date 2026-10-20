import { describe, expect, test } from 'bun:test';
import { existsSync, readFileSync } from 'node:fs';
import { appearance, badgeText, createToolbar, reasonText, nothingText, ICONS, WAIT_FRAMES, COLORS } from '../src/toolbar.js';

const clock = (t) => new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const until = new Date(2026, 9, 19, 21, 40).getTime();

// chrome.action as the browser keeps it: per-tab values over the defaults (tabId undefined).
function fakeAction() {
  const values = new Map();
  const calls = [];
  const setter = (prop, field) => async (arg) => {
    calls.push([prop, arg.tabId]);
    const key = arg.tabId ?? 'default';
    values.set(key, { ...values.get(key), [prop]: arg[field] });
  };
  const action = { setIcon: setter('icon', 'path'), setTitle: setter('title', 'title'), setBadgeText: setter('text', 'text'),
    setBadgeBackgroundColor: setter('color', 'color') };
  return { action, calls, shown: (tabId) => ({ ...values.get('default'), ...values.get(tabId) }) };
}
function fakeTimers() {
  let id = 0;
  const intervals = new Map();
  const timeouts = new Map();
  return {
    intervals, timeouts,
    setInterval: (fn) => (intervals.set(++id, fn), id), clearInterval: (i) => intervals.delete(i),
    setTimeout: (fn) => (timeouts.set(++id, fn), id), clearTimeout: (i) => timeouts.delete(i),
    tick() { for (const fn of [...intervals.values()]) fn(); },
    expire() { for (const [i, fn] of [...timeouts]) { timeouts.delete(i); fn(); } },
  };
}
const settle = () => new Promise((r) => setTimeout(r, 0));

describe('appearance', () => {
  test('the badge says the level, short: +1, −2, off, full, nothing at normal', () => {
    expect([-3, -2, -1, 0, 1, 2, 3].map(badgeText)).toEqual(['off', '−2', '−1', '', '+1', '+2', 'full']);
    expect(appearance()).toEqual({ icon: 'on', title: 'Japanese Weave', text: '', color: COLORS.lighter });
    expect(appearance({ level: 1, until })).toEqual({ icon: 'on', title: `Japanese Weave: heavier (+1) until ${clock(until)}`,
      text: '+1', color: COLORS.heavier });
    expect(appearance({ level: -2, until })).toMatchObject({ text: '−2', color: COLORS.lighter, title: `Japanese Weave: much lighter (−2) until ${clock(until)}` });
    expect(appearance({ level: 3, until })).toMatchObject({ icon: 'on', text: 'full', color: COLORS.full, title: `Japanese Weave: full until ${clock(until)}` });
  });
  test('a page without Japanese is grey and says why; off says until when', () => {
    expect(appearance({ level: -3, until, reason: 'intensity-off' })).toEqual({ icon: 'off', text: 'off', color: COLORS.off,
      title: `Japanese Weave: off until ${clock(until)} (Alt+Shift+Period or the popup turns it back on)` });
    expect(appearance({ reason: 'site-off' })).toEqual({ icon: 'off', title: 'Japanese Weave: turned off on this site', text: '', color: COLORS.lighter });
    // the level still runs elsewhere, so the badge keeps it and the title says both
    expect(appearance({ level: 1, until, reason: 'paused' })).toMatchObject({ icon: 'off', text: '+1',
      title: `Japanese Weave: paused from Claude Code · heavier (+1) until ${clock(until)}` });
    for (const reason of ['switched-off', 'paused', 'no-gemini-key', 'no-anthropic-key', 'unsupported', 'hidden', 'site-off', 'not-on',
      'private', 'no-access']) {
      expect(reasonText(reason)).not.toBe(reasonText('something else'));
    }
    expect(reasonText('no-anthropic-key')).toContain('Anthropic key');
    expect(reasonText('unsupported')).toBe("can't run on this page");
  });
  test('a model at work shows over the level, never over a reason', () => {
    expect(WAIT_FRAMES.map((_, frame) => appearance({ level: 1, until, waiting: true, frame }).text)).toEqual(WAIT_FRAMES);
    expect(appearance({ waiting: true, frame: 4 })).toEqual({ icon: 'on', text: WAIT_FRAMES[1], color: COLORS.waiting,
      title: 'Japanese Weave: waiting for the model to weave this page' });
    expect(appearance({ level: 2, until, waiting: true }).title).toContain(`much heavier (+2) until ${clock(until)}`);
    expect(appearance({ waiting: true, reason: 'site-off' })).toMatchObject({ icon: 'off', text: '' });
  });
  test('a page with nothing to weave is faded and says which, under off and the wait, with the level kept', () => {
    expect(appearance({ nothing: 'none' })).toEqual({ icon: 'faded', text: '', color: COLORS.lighter,
      title: 'Japanese Weave: nothing on this page to weave (no English paragraphs found)' });
    expect(appearance({ nothing: 'unfit', level: 1, until })).toEqual({ icon: 'faded', text: '+1', color: COLORS.heavier,
      title: `Japanese Weave: no Japanese fit the text on this page · heavier (+1) until ${clock(until)}` });
    expect(appearance({ nothing: 'unfit', level: 3, until }).text).toBe('full');
    // off (any reason) > waiting > nothing to weave > normal
    expect(appearance({ nothing: 'none', reason: 'site-off' })).toEqual(appearance({ reason: 'site-off' }));
    expect(appearance({ nothing: 'none', level: -3, until, reason: 'intensity-off' })).toMatchObject({ icon: 'off', text: 'off' });
    expect(appearance({ nothing: 'unfit', waiting: true, frame: 2 })).toEqual(appearance({ waiting: true, frame: 2 }));
    expect(appearance({ nothing: '' })).toEqual(appearance());
    expect(appearance({ nothing: 'something else' })).toEqual(appearance()); // only the two known states fade the icon
    expect(nothingText('none')).not.toBe(nothingText('unfit'));
    expect(ICONS.faded[16]).toBe('/icons/faded-16.png');
  });
});

test('every icon the manifest and the toolbar name is there (tools/make-icons.sh)', () => {
  const root = new URL('../', import.meta.url);
  const manifest = JSON.parse(readFileSync(new URL('manifest.json', root), 'utf8'));
  const paths = [...Object.values(manifest.icons), ...Object.values(manifest.action.default_icon),
    ...Object.values(ICONS).flatMap((set) => Object.values(set).map((p) => p.replace(/^\//, '')))];
  expect(paths).toContain('icons/off-16.png');
  expect(paths).toContain('icons/faded-48.png');
  for (const path of paths) expect(existsSync(new URL(path, root))).toBe(true);
});

describe('createToolbar', () => {
  test('levels, per-tab reasons and a moving badge while a call runs, back to the level when it ends', async () => {
    const { action, calls, shown } = fakeAction();
    const timers = fakeTimers();
    const bar = createToolbar(action, timers);
    await bar.show({ level: 1, until }, [[1, ''], [2, 'site-off']]);
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: '+1', color: COLORS.heavier });
    expect(shown(2)).toMatchObject({ icon: ICONS.off, text: '+1', title: expect.stringContaining('turned off on this site') });
    expect(shown(9)).toMatchObject({ icon: ICONS.on, text: '+1' }); // a tab not drawn yet has the defaults
    expect(ICONS.off[16]).toBe('/icons/off-16.png');

    const done = bar.wait(1);
    const again = bar.wait(1);
    await settle();
    expect(bar.waiting(1)).toBe(true);
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: WAIT_FRAMES[0], color: COLORS.waiting });
    expect(timers.intervals.size).toBe(1);
    const before = calls.length;
    timers.tick(); await settle();
    expect(shown(1).text).toBe(WAIT_FRAMES[1]);
    timers.tick(); await settle();
    expect(shown(1).text).toBe(WAIT_FRAMES[2]);
    expect(calls.slice(before)).toEqual([['text', 1], ['text', 1]]); // only the text changes between frames
    done(); done(); await settle();
    expect(shown(1).text).toBe(WAIT_FRAMES[2]); // the other call still runs
    again(); await settle();
    expect(bar.waiting(1)).toBe(false);
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: '+1', color: COLORS.heavier });
    expect(timers.intervals.size).toBe(0);
    expect(timers.timeouts.size).toBe(0);

    // Nothing changed: nothing sent.
    const settled = calls.length;
    await bar.show({ level: 1, until }, [[1, ''], [2, 'site-off']]);
    expect(calls.length).toBe(settled);
    // Off everywhere: every tab grey, "off".
    await bar.show({ level: -3, until, reason: 'intensity-off' }, [[1, 'intensity-off'], [2, 'intensity-off']]);
    for (const id of [1, 2, 'none']) expect(shown(id)).toMatchObject({ icon: ICONS.off, text: 'off', color: COLORS.off });
    // A disabled tab never shows the wait.
    bar.wait(2); await settle();
    expect(shown(2).text).toBe('off');
    timers.tick(); await settle();
    expect(shown(2).text).toBe('off');
  });

  test('a wait never sticks: it ends on navigation, on close, and after WAIT_MAX_MS', async () => {
    const { action, calls, shown } = fakeAction();
    const timers = fakeTimers();
    const bar = createToolbar(action, timers);
    await bar.show({ level: 0 }, [[1, ''], [2, ''], [3, '']]);
    bar.wait(1); bar.wait(2); bar.wait(3);
    await settle();
    expect([1, 2, 3].map((id) => shown(id).text)).toEqual(['·', '·', '·']);
    // A new page: the old page's calls no longer show, and the tab is drawn in full again (Firefox resets it).
    const before = calls.length;
    await bar.navigated(1, '');
    expect(bar.waiting(1)).toBe(false);
    expect(shown(1).text).toBe('');
    expect(calls.slice(before).filter(([, id]) => id === 1).map(([prop]) => prop).sort()).toEqual(['color', 'icon', 'text', 'title']);
    // The page finished loading: its own calls keep showing.
    bar.wait(1); await settle();
    await bar.navigated(1, '', { loading: false });
    expect(bar.waiting(1)).toBe(true);
    // Closed: forgotten, also when the worker missed the event and the next redraw no longer lists the tab.
    bar.closed(2);
    expect(bar.waiting(2)).toBe(false);
    bar.wait(4);
    await bar.show({ level: 0 }, [[1, ''], [3, '']]);
    expect(bar.waiting(4)).toBe(false);
    // A call that never comes back (a worker stopped mid-call) is dropped after WAIT_MAX_MS.
    timers.expire(); await settle();
    expect(bar.waiting(1) || bar.waiting(3)).toBe(false);
    expect(shown(3).text).toBe('');
    expect(timers.intervals.size).toBe(0);
  });

  test('nothing to weave per tab: faded until Japanese turns up or the tab loads another page', async () => {
    const { action, calls, shown } = fakeAction();
    const timers = fakeTimers();
    const bar = createToolbar(action, timers);
    await bar.show({ level: 1, until }, [[1, ''], [2, ''], [3, 'site-off']]);
    await bar.nothing(1, 'none');
    await bar.nothing(3, 'unfit');
    expect(shown(1)).toMatchObject({ icon: ICONS.faded, text: '+1', title: expect.stringContaining('no English paragraphs found') });
    expect(shown(2)).toMatchObject({ icon: ICONS.on, text: '+1' });
    expect(shown(3)).toMatchObject({ icon: ICONS.off, title: expect.stringContaining('turned off on this site') });
    // It outlasts a redraw of every tab (settings or the level changed), and the off reason still wins while it lasts.
    await bar.show({ level: 0 }, [[1, ''], [2, ''], [3, 'site-off']]);
    expect(shown(1)).toMatchObject({ icon: ICONS.faded, text: '' });
    await bar.show({ level: 0 }, [[1, ''], [2, ''], [3, '']]);
    expect(shown(3)).toMatchObject({ icon: ICONS.faded, title: 'Japanese Weave: no Japanese fit the text on this page' });
    // A model at work shows over it, and it comes back when the call ends.
    const done = bar.wait(1); await settle();
    expect(shown(1)).toMatchObject({ icon: ICONS.on, text: WAIT_FRAMES[0] });
    done(); await settle();
    expect(shown(1).icon).toBe(ICONS.faded);
    // Japanese turned up: the plain icon. An unknown state counts as something to weave.
    await bar.nothing(1, '');
    expect(shown(1)).toMatchObject({ icon: ICONS.on, title: 'Japanese Weave' });
    await bar.nothing(1, 'bogus');
    expect(shown(1).icon).toBe(ICONS.on);
    // A new page forgets it (an SPA's new URL too); the page finishing its load does not.
    await bar.nothing(2, 'none');
    await bar.navigated(2, '', { loading: false });
    expect(shown(2).icon).toBe(ICONS.faded);
    await bar.navigated(2, '');
    expect(shown(2).icon).toBe(ICONS.on);
    // Closed: forgotten. Nothing is sent for a tab that is no tab.
    bar.closed(3);
    const before = calls.length;
    await bar.nothing(undefined, 'none');
    expect(calls.length).toBe(before);
    await bar.nothing(3, 'none');
    expect(shown(3).icon).toBe(ICONS.faded); // a tab the worker hears from again is drawn again
  });

  test('works with parts of the API missing or failing (tests, old browsers, a closed tab)', async () => {
    const bar = createToolbar(undefined, fakeTimers());
    await bar.show({ level: 2, until }, [[1, '']]);
    bar.wait(1)();
    bar.wait(undefined)();
    const failing = createToolbar({ setBadgeText: () => { throw new Error('No tab with id: 5'); },
      setTitle: async () => { throw new Error('No tab with id: 5'); } }, fakeTimers());
    await failing.tab(5, '');
    await failing.navigated(5, 'unsupported');
  });
});
