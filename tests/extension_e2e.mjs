// End-to-end extension check (needs Playwright and its Chromium: `npm i playwright && npx playwright install chromium`,
// or PLAYWRIGHT= the path of a playwright-core module whose Chromium is installed; Google Chrome ignores
// --load-extension, so this uses Playwright's Chromium). Run setup first, registering the profile dir used here:
//   python3 scripts/fw.py setup --user-data-dir /tmp/fw-profile --known-top 300 [--gemini-key-file KEY]
//   PROFILE=/tmp/fw-profile node tests/extension_e2e.mjs
// To leave your own setup alone, give both commands a scratch JAPANESE_WEAVE_HOME (and setup a scratch HOME and
// XDG_CONFIG_HOME, so it writes no host manifest into your real browsers).
// Loads a temporary copy of ~/.japanese-weave/extension (or $JAPANESE_WEAVE_HOME/extension) with website access
// granted up front, since Chrome's grant dialog can't be clicked from a script. Serves a fake x.com timeline and a
// fake article on another site (synthetic text), and prints what happened: native host sync (each word with its
// glosses and safe glosses), the reading index from the host, the swaps code is sure of within a second of the page
// loading (no model key needed: without one, those are all, and no request is made; with one, the model's check
// adds the rest), delayed hover tip, click flip, health text untouched, links and forms intact, seen/flip events
// flushed back to the word state, forged page events ignored, no swaps on webmail, the article read and its reading
// counts in the state dir's exposure.json (counts and digests, never text), and the article reverting when its site
// is turned off.
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { cpSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
const { chromium } = await import(process.env.PLAYWRIGHT || 'playwright');

const STATE_DIR = process.env.JAPANESE_WEAVE_HOME || join(homedir(), '.japanese-weave');
const INSTALLED = join(STATE_DIR, 'extension');
const EXT = mkdtempSync(join(tmpdir(), 'fw-ext-'));
cpSync(INSTALLED, EXT, { recursive: true });
const manifest = JSON.parse(readFileSync(join(EXT, 'manifest.json'), 'utf8'));
manifest.host_permissions.push(...manifest.optional_host_permissions); // same key, so the same extension ID
writeFileSync(join(EXT, 'manifest.json'), JSON.stringify(manifest));
const tweets = [
  ['t1', 'en', 'Finally shipped the new search page and it is very fast now, but the old filters still need work.'],
  ['t2', 'en', 'I always forget my umbrella, so of course it rained again today and I got soaked without one.'],
  ['t3', 'en', 'Maybe the best part of the weekend was cooking with friends and trying a new recipe together.'],
  ['t4', 'en', 'Went to the doctor about the migraine medication and the symptoms are still there, ugh.'],
  ['t5', 'en', 'Reading <a href="/someone">@someone</a> and their thread about compilers, also very good, check <a href="https://t.co/abc"><span>https://</span>example.com</a> now'],
];
const html = `<!doctype html><html><head><meta charset="utf-8"><title>Home / X</title></head><body><main>${tweets
  .map(([id, lang, t]) => `<article data-testid="tweet" id="${id}"><div lang="${lang}" dir="auto" data-testid="tweetText"><span>${t}</span></div></article>`)
  .join('\n')}</main></body></html>`;

const ARTICLE_URL = 'https://reading.example/2026/10/notes';
const article = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Notes</title></head><body>
<header><nav><a href="/">Home</a> and <a href="/about">About</a></nav></header>
<main><article>
<h1>What I learned from a slow week</h1>
<p id="a1">The garden was very quiet this week, and I finally read the book my friend gave me last year.</p>
<p id="a2">Maybe the best part was walking home every evening without my phone, because the light was always so warm.</p>
<p id="a3">My doctor changed the dose of my medication, and the side effects are still there, so I am resting.</p>
<p id="a4">We also cooked a big dinner together now that the kitchen is finally fixed, and it was a lot of fun.</p>
<form><p id="a5">Write your comment here and tell us what you think about the article today.</p><textarea></textarea></form>
</article></main></body></html>`;

const userDataDir = process.env.PROFILE || mkdtempSync(join(tmpdir(), 'fw-profile-'));
// CHROMIUM: another Chromium build to run (Playwright's own may not start everywhere, NixOS for one); newer ones need
// the command line's extension loading switched back on.
const ctx = await chromium.launchPersistentContext(userDataDir, {
  ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : { channel: 'chromium' }), // not the headless shell: it can't load extensions
  headless: true,
  args: ['--headless=new', `--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`,
    '--disable-features=DisableLoadExtensionCommandLineSwitch'],
});
const out = {};
try {
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent('serviceworker', { timeout: 20000 });
  out.extensionId = sw.url().split('/')[2];
  for (let i = 0; i < 40 && !(await sw.evaluate(() => !!globalThis.chrome?.storage)); i++) await new Promise((r) => setTimeout(r, 250));
  // Wait for the install-time sync from the native host.
  let state = {};
  for (let i = 0; i < 40 && !state.wordState; i++) {
    state = await sw.evaluate(() => chrome.storage.local.get(['wordState', 'hostKey']));
    if (!state.wordState) await new Promise((r) => setTimeout(r, 500));
  }
  out.synced = state.wordState ? { stage: state.wordState.stage, words: state.wordState.words.length, version: state.wordState.version,
    withGlosses: state.wordState.words.filter((w) => w.glosses).length, safe: state.wordState.words.flatMap((w) => (w.safe || []).map((g) => `${w.ja}=${g}`)).slice(0, 12),
    withEntry: state.wordState.words.filter((w) => w.id).length } : null;
  out.hostKeyFromSetup = !!state.hostKey;
  for (let i = 0; i < 20; i++) {
    const { readingIndex } = await sw.evaluate(() => chrome.storage.local.get('readingIndex'));
    out.readingIndex = readingIndex ? { version: readingIndex.version, entries: readingIndex.entries.length } : null;
    if (readingIndex) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  const calls = () => sw.evaluate(() => chrome.storage.local.get('modelCalls').then((o) => o.modelCalls?.count || 0));
  const callsAtStart = await calls();

  await ctx.route('https://x.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html }));
  const page = await ctx.newPage();
  page.on('console', (m) => { if (/japanese-weave/.test(m.text())) (out.console ||= []).push(m.text()); });
  const loaded = Date.now();
  await page.goto('https://x.com/home');
  await page.waitForSelector('.fw-word', { timeout: 60000 }).catch(() => {});
  out.firstSwapMs = Date.now() - loaded; // the swaps code is sure of: they don't wait for a model
  out.sureSwaps = await page.$$eval('.fw-word', (els) => els.map((e) => e.dataset.ja));
  await page.waitForTimeout(3000);
  out.swaps = await page.$$eval('.fw-word', (els) => els.map((e) => ({ tweet: e.closest('article').id, ja: e.dataset.ja, en: e.dataset.en, tip: e.dataset.tip })));
  out.healthTouched = await page.$$eval('#t4 .fw-word', (els) => els.length);
  out.linksIntact = await page.$eval('#t5', (el) => [...el.querySelectorAll('a')].map((a) => a.textContent));
  out.texts = await page.$$eval('[data-testid=tweetText]', (els) => els.map((e) => e.textContent));
  if (out.swaps.length) {
    const first = page.locator('.fw-word').first();
    await first.hover();
    await page.waitForTimeout(1000); // the gloss shows after a short dwell, which also counts as a peek
    out.hoverAfter = await first.evaluate((e) => getComputedStyle(e, '::after').content);
    await first.click();
    out.afterClick = await first.evaluate((e) => ({ text: e.textContent, english: e.classList.contains('fw-en') }));
    await first.click();
    out.afterSecondClick = await first.textContent();
    await first.click(); // leave it flipped to English: one flip event
    // Events: seen + flip are queued, then flushed to the host after ~10 s.
    await page.waitForTimeout(13000);
    out.pendingAfterFlush = await sw.evaluate(() => chrome.storage.local.get('pendingEvents').then((o) => o.pendingEvents));
    out.identifyingAttrs = await page.$$eval('[data-fw-key],[data-fw-word]', (els) => els.length);
    // The page itself can't forge events: synthetic clicks and hovers, on a real swap or on its own fw-word span.
    out.forged = await page.evaluate(async () => {
      const real = document.querySelector('.fw-word');
      const before = real.textContent;
      const fake = Object.assign(document.createElement('span'), { className: 'fw-word', textContent: 'très' });
      fake.dataset.fwWord = 'très';
      document.querySelector('main').append(fake);
      for (const el of [real, fake]) {
        el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
        el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      }
      await new Promise((r) => setTimeout(r, 1500));
      return { realUnchanged: real.textContent === before, fakeUnchanged: fake.textContent === 'très' };
    });
    out.forged.pending = await sw.evaluate(() => chrome.storage.local.get('pendingEvents').then((o) => o.pendingEvents || []));
  }
  // A plain article on another site: the dynamically registered content script runs there too.
  for (let i = 0; i < 40; i++) {
    const ids = await sw.evaluate(() => chrome.scripting.getRegisteredContentScripts().then((s) => s.map((x) => x.id)));
    if (ids.includes('weave-sites')) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  await ctx.route('https://reading.example/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: article }));
  const reading = await ctx.newPage();
  reading.on('console', (m) => { if (/japanese-weave/.test(m.text())) (out.console ||= []).push(m.text()); });
  await reading.goto(ARTICLE_URL);
  await reading.waitForSelector('.fw-word', { timeout: 60000 }).catch(() => {});
  await reading.waitForTimeout(3000);
  out.articleSwaps = await reading.$$eval('.fw-word', (els) => els.map((e) => ({ p: e.closest('[id]')?.id || e.parentElement.tagName, ja: e.dataset.ja, en: e.dataset.en })));
  out.articleHealthTouched = await reading.$$eval('#a3 .fw-word', (els) => els.length);
  out.articleFormTouched = await reading.$$eval('form .fw-word', (els) => els.length);
  out.articleNavIntact = await reading.$eval('nav', (n) => n.textContent);

  // Read an article: input now and then while it is on screen; its stays end when the page goes, and the worker counts
  // the dictionary entries it shows.
  const reader = await ctx.newPage();
  await reader.goto(`${ARTICLE_URL}-again`);
  await reader.waitForSelector('.fw-word', { timeout: 60000 }).catch(() => {});
  for (let i = 0; i < 4; i++) { await reader.mouse.move(100 + 10 * i, 200); await reader.waitForTimeout(800); }
  await reader.goto('about:blank');

  // Webmail starts off even with every site on: no swaps, so no page text sent.
  await ctx.route('https://mail.google.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: article }));
  const callsBeforeMail = await calls();
  const mail = await ctx.newPage();
  await mail.goto('https://mail.google.com/mail/u/0/');
  await mail.waitForTimeout(8000);
  out.mailSwaps = await mail.$$eval('.fw-word', (els) => els.length);
  out.mailModelCalls = (await calls()) - callsBeforeMail;

  const popup = await ctx.newPage();
  await popup.goto(`chrome-extension://${out.extensionId}/src/popup.html`);
  await popup.waitForTimeout(1500);
  out.popup = await popup.$eval('body', (b) => b.innerText);
  // Turning the site off from the popup reverts the open article.
  await popup.evaluate(() => chrome.runtime.sendMessage({ type: 'save', siteOverride: { host: 'reading.example', enabled: false } }));
  await reading.waitForTimeout(1500);
  out.articleSwapsAfterSiteOff = await reading.$$eval('.fw-word', (els) => els.length);
  out.articleTextAfterSiteOff = await reading.$eval('#a1', (e) => e.textContent);
  // The worker sends the reading counts with the log (about 10 s after the last); the host puts them in the daily
  // store chat's counts use.
  await new Promise((r) => setTimeout(r, 12000));
  out.pendingReading = await sw.evaluate(() => chrome.storage.local.get('pendingReading').then((o) => o.pendingReading || []));
  const exposure = JSON.parse(readFileSync(join(STATE_DIR, 'exposure.json'), 'utf8'));
  const today = new Date().toLocaleDateString('en-CA');
  out.readingCountsToday = exposure.reading?.[today] || null;
  out.webDigests = Object.keys(exposure.read || {}).filter((d) => d.startsWith('web:')).length;
  out.countsHoldNoText = !JSON.stringify(exposure).includes('garden');
  out.modelCalls = (await calls()) - callsAtStart;
} finally {
  await ctx.close();
}
console.log(JSON.stringify(out, null, 1));
