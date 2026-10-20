import { test, expect } from 'bun:test';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { cleanSettings } from '../src/settings.js';

// The popup at the ends of the intensity scale: off says until when and offers a way back; the buttons stop at the ends.
test('popup: off until a time with a way back, full, and the Less and More buttons at the ends', async () => {
  const win = new Window({ url: 'chrome-extension://fw-test/src/popup.html' });
  const keys = ['chrome', 'document', 'window'];
  const saved = Object.fromEntries(keys.map((k) => [k, globalThis[k]]));
  const html = readFileSync(new URL('../src/popup.html', import.meta.url), 'utf8');
  win.document.body.innerHTML = html.slice(html.indexOf('<body>') + 6, html.indexOf('<script'));
  const until = new Date(2026, 9, 19, 21, 40).getTime();
  let intensity = { level: -3, until };
  const asked = [];
  const status = () => ({ config: { settings: { ...cleanSettings(), newSitesEnabled: true }, stage: 1, hasKey: true, paused: false,
    origins: ['<all_urls>'], challenge: 0, intensity }, words: 0, usage: {}, modelStatus: null });
  Object.assign(globalThis, {
    window: win, document: win.document,
    chrome: {
      runtime: { sendMessage: async (msg) => {
        asked.push(msg);
        if (msg.type === 'status') return status();
        if (msg.type === 'intensity') { intensity = msg.step ? { level: 3, until } : { level: 0, until: 0 }; return { ok: true, intensity }; }
        return { ok: false, error: 'no host' };
      } },
      tabs: { query: async () => [{ id: 1, url: 'https://example.com/a' }] },
      permissions: { contains: async () => true },
    },
  });
  const $ = (id) => win.document.getElementById(id);
  const settle = () => new Promise((r) => setTimeout(r, 20));
  const clock = new Date(until).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  try {
    await import('../src/popup.js?test');
    await settle();
    expect($('operationText').textContent).toBe(`Off until ${clock}`);
    expect($('intensityInfo').textContent).toBe(`off until ${clock}`);
    expect($('intensityBack').hidden).toBe(false);
    expect($('lighter').disabled).toBe(true);
    expect($('heavier').disabled).toBe(false);
    $('intensityBack').click();
    await settle();
    expect(asked.find((m) => m.type === 'intensity')).toEqual({ type: 'intensity', step: 0 });
    expect($('operationText').textContent).toBe('On');
    expect($('intensityInfo').textContent).toBe('normal');
    expect($('intensityBack').hidden).toBe(true);
    $('heavier').click();
    await settle();
    expect($('intensityInfo').textContent).toBe(`full until ${clock}`);
    expect($('heavier').disabled).toBe(true);
    expect($('lighter').disabled).toBe(false);
  } finally {
    await win.happyDOM.close();
    Object.assign(globalThis, saved);
  }
});
