import { describe, expect, it } from 'bun:test';
import { parseState, readCached, refresh, queueEvents, flushEvents, shownToday, cleanInteraction, queueLog, flushLog, describeStats,
  refreshReadingIndex, queueReading, readToday, flushReading, STATE_KEY, PENDING_KEY, LOG_KEY, READING_KEY, INDEX_KEY } from '../src/hostsource.js';
import { memoryStorage } from '../src/cache.js';
import { validateSwaps } from '../src/levels.js';
import { pipelineFor, planText } from '../src/pipeline.js';

// Shape the native host sends (scripts/fw_host.py "state").
const REPLY = {
  ok: true,
  version: 'abc123',
  stage: 1,
  paused: false,
  words: [
    { ja: 'だから', en: 'so / therefore', hint: 'dakara', kana: 'だから', status: 'active' },
    { ja: '多分', en: 'maybe', hint: 'tabun', kana: 'たぶん', status: 'shaky' },
    { ja: 'でも', en: 'but', hint: 'demo', kana: 'でも', status: 'known' },
  ],
  geminiKey: 'KEY',
};
const host = (reply) => async () => reply;

describe('parseState', () => {
  it('reads the host reply shape', () => {
    const s = parseState(REPLY);
    expect(s).toEqual({ version: 'abc123', stage: 1, paused: false, sites: 'all', challenge: 0, budget: 0, sentencePrice: 0, reading: '', dictionary: '',
      recency: null, intensity: null, words: REPLY.words });
    const intensity = { level: -1, until: 1800000000000, at: 1799999000000 };
    expect(parseState({ ...REPLY, prefs: { intensity_browser: { ...intensity, source: 'shortcut' } } }).intensity).toEqual(intensity);
    for (const level of [-3, 3]) { // off and full
      expect(parseState({ ...REPLY, prefs: { intensity_browser: { ...intensity, level } } }).intensity).toEqual({ ...intensity, level });
    }
    for (const level of [4, -4, 1.5, '1']) expect(parseState({ ...REPLY, prefs: { intensity_browser: { ...intensity, level } } }).intensity).toBe(null);
  });
  it('carries per-word estimates and the dial', () => {
    const words = [{ ...REPLY.words[0], k: 0.4, sd: 0.8, free: false }, { ...REPLY.words[1], k: 'high', sd: 1, free: 'yes' }];
    const s = parseState({ ...REPLY, words, challenge: 1.5, budget: 2.1, rate: { per100: 1.2, peeks: 6, shownWords: 500, days: 3 } });
    expect(s.words.map((w) => [w.k, w.sd, w.free])).toEqual([[0.4, 0.8, false], [undefined, undefined, undefined]]);
    expect([s.challenge, s.budget]).toEqual([1.5, 2.1]);
    expect(s).not.toHaveProperty('rate'); // the daily controller is gone, and its rate with it
    expect(parseState({ ...REPLY, challenge: -1 })).toMatchObject({ challenge: 0 });
    // the web's price of a whole sentence, once knowledge.json has one: lookups per sentence read, 0 to 1 (0: none yet)
    expect(parseState({ ...REPLY, sentencePrice: 0.12 }).sentencePrice).toBe(0.12);
    for (const sentencePrice of [0, -0.1, 1.5, '0.1', NaN, null]) expect(parseState({ ...REPLY, sentencePrice }).sentencePrice).toBe(0);
    // recency: when each word was last read, and the fit's floor, for its current chance (levels.js currentChance)
    const read = [[1800000000000, 0.7], [1799990000000, 0.3]];
    const timed = parseState({ ...REPLY, words: [{ ...REPLY.words[0], k: 1, sd: 1, read: [...read, [1, 2], ['x', 0.1]] }],
      recency: { floor: -3.9, days: 7 } });
    expect([timed.words[0].read, timed.recency]).toEqual([read, { floor: -3.9, days: 7 }]);
    for (const recency of [{ floor: 'low', days: 7 }, { floor: -3.9, days: 0 }, null]) {
      expect(parseState({ ...REPLY, recency }).recency).toBe(null);
    }
  });
  it('carries each word\'s glosses, the safe ones among them and its entry id, and the reading index\'s version', () => {
    const words = [{ ...REPLY.words[0], glosses: ['so', 'therefore', '', 7], safe: ['therefore', 'nope'], id: 1007310 },
      { ...REPLY.words[1], glosses: 'maybe', safe: ['maybe'], id: -1 }, { ...REPLY.words[2], glosses: ['but'], id: 1.5 }];
    const s = parseState({ ...REPLY, words, reading: { version: 'a1b2c3' } });
    expect(s.words.map((w) => [w.glosses, w.safe, w.id])).toEqual([[['so', 'therefore'], ['therefore'], 1007310],
      [undefined, undefined, undefined], [['but'], [], undefined]]);
    expect(s.reading).toBe('a1b2c3');
    expect(parseState({ ...REPLY, reading: { version: 'has spaces' } }).reading).toBe('');
  });
  it('carries each word\'s delayed-start arm while it has one', () => {
    const words = [{ ...REPLY.words[0], arm: 'held:probe' }, { ...REPLY.words[1], arm: '<b>x</b>' }];
    expect(parseState({ ...REPLY, words }).words.map((w) => w.arm)).toEqual(['held:probe', undefined]);
  });
  it('reads the site default setup chose; anything but "ask" means all sites', () => {
    expect(parseState({ ...REPLY, prefs: { extension_sites: 'ask' } }).sites).toBe('ask');
    expect(parseState({ ...REPLY, prefs: { extension_sites: 'all' } }).sites).toBe('all');
    expect(parseState({ ...REPLY, prefs: { extension_sites: 'bogus' } }).sites).toBe('all');
  });
  it('drops unusable rows and duplicates; bad stage falls back to 1', () => {
    const s = parseState({ stage: 9, words: [{ ja: 'でも', en: 'but' }, { ja: 'でも', en: 'x' }, { ja: ' ', en: 'y' }, null, { ja: 'もう' }] });
    expect(s.words).toEqual([{ ja: 'でも', en: 'but', hint: '', kana: '', status: 'active' }]);
    expect(s.stage).toBe(1);
  });
  it('throws when nothing usable is left', () => {
    expect(() => parseState({ words: [] })).toThrow();
  });
});

describe('refresh', () => {
  it('stores the state and the setup key; a new version reports changed', async () => {
    const storage = memoryStorage();
    const r = await refresh({ storage, send: host(REPLY), now: () => 5 });
    expect(r.error).toBeNull();
    expect(r.changed).toBe(true);
    expect((await readCached(storage)).fetchedAt).toBe(5);
    expect((await storage.get(['hostKey'])).hostKey).toBe('KEY');
    const again = await refresh({ storage, send: host(REPLY) });
    expect(again.changed).toBe(false);
  });
  it('keeps the last good list when the host is missing or answers badly', async () => {
    const storage = memoryStorage();
    await refresh({ storage, send: host(REPLY) });
    const missing = await refresh({ storage, send: async () => { throw new Error('Specified native messaging host not found.'); } });
    expect(missing.error).toContain('not found');
    expect(missing.state.version).toBe('abc123');
    const bad = await refresh({ storage, send: host({ ok: true, words: [] }) });
    expect(bad.error).toBeTruthy();
    expect((await readCached(storage)).words).toHaveLength(3);
  });
  it('a synced list drives validation and the matcher, its safe glosses what code takes alone', () => {
    const s = parseState({ ...REPLY, words: [{ ...REPLY.words[0], glosses: ['so', 'therefore'], safe: [] },
      { ...REPLY.words[1], glosses: ['maybe'], safe: ['maybe'] }, REPLY.words[2],
      { ja: '犬', en: 'dog', hint: 'inu', kana: 'いぬ', status: 'known', glosses: ['dog'], safe: ['dog'] }] });
    expect(validateSwaps(1, [{ o: 'so', f: 'だから', ctx: 'so then' }], s.words)[0].f).toBe('だから');
    expect(validateSwaps(1, [{ o: 'maybe', f: 'たぶん', ctx: 'maybe' }], s.words)[0].f).toBe('多分');
    expect(validateSwaps(1, [{ o: 'already', f: 'もう', ctx: 'x' }], s.words)).toEqual([]);
    // closed-class English is never safe, whatever the flags say ("maybe")
    const plan = planText(pipelineFor(s.words), 'Maybe it rains, but so what, the dog goes out anyway.');
    expect(plan.spans.map((m) => [m.span.english, m.safe.map((o) => o.ja), m.doubtful.map((o) => o.ja)]))
      .toEqual([['Maybe', [], ['多分']], ['but', [], ['でも']], ['so', [], ['だから']], ['dog', ['犬'], []]]);
  });
});

describe('events', () => {
  it('counts a word as seen or peeked once per day, keeps every flip', async () => {
    const storage = memoryStorage();
    await queueEvents(storage, [{ word: '多分', kind: 'seen' }, { word: '多分', kind: 'seen' }, { word: 'でも', kind: 'flip' },
      { word: '多分', kind: 'peek' }, { word: '多分', kind: 'peek' }], '2026-10-01');
    await queueEvents(storage, [{ word: '多分', kind: 'seen' }, { word: 'でも', kind: 'flip' }, { word: 'x', kind: 'bogus' }], '2026-10-01');
    await queueEvents(storage, [{ word: '多分', kind: 'seen' }], '2026-10-02');
    const { [PENDING_KEY]: pending } = await storage.get([PENDING_KEY]);
    expect(pending).toEqual([
      { word: '多分', kind: 'seen', day: '2026-10-01' },
      { word: 'でも', kind: 'flip', day: '2026-10-01' },
      { word: '多分', kind: 'peek', day: '2026-10-01' },
      { word: 'でも', kind: 'flip', day: '2026-10-01' },
      { word: '多分', kind: 'seen', day: '2026-10-02' },
    ]);
  });
  it('flush sends and clears; a failed flush keeps the queue', async () => {
    const storage = memoryStorage();
    await queueEvents(storage, [{ word: 'でも', kind: 'flip' }], '2026-10-01');
    const failed = await flushEvents({ storage, send: async () => { throw new Error('no host'); } });
    expect(failed.error).toBe('no host');
    expect((await storage.get([PENDING_KEY]))[PENDING_KEY]).toHaveLength(1);
    const sent = [];
    const ok = await flushEvents({ storage, send: async (m) => { sent.push(m); return { ok: true, applied: 1 }; } });
    expect(ok).toEqual({ sent: 1, applied: 1 });
    expect(sent[0].type).toBe('events');
    expect((await storage.get([PENDING_KEY]))[PENDING_KEY]).toEqual([]);
  });
  it('words seen today come back for the prompt; peeks and other days do not', async () => {
    const storage = memoryStorage();
    await queueEvents(storage, [{ word: '多分', kind: 'seen' }, { word: 'でも', kind: 'peek' }], '2026-10-01');
    expect(await shownToday(storage, '2026-10-01')).toEqual(['多分']);
    expect(await shownToday(storage, '2026-10-02')).toEqual([]);
  });
  it('readCached ignores empty state', async () => {
    expect(await readCached(memoryStorage({ [STATE_KEY]: { words: [] } }))).toBeNull();
  });
});

describe('interaction log', () => {
  const now = 1_800_000_000_000;
  const peek = { kind: 'peek', word: 'et', at: now - 2000, dwellMs: 1234.4, view: 'a1b2', context: 'c0ffee', position: 1,
    box: [10.2, 400, 600, 80.6], annotations: [['de', 12.4, 0], ['et', 140, 0.2]] };
  it('keeps words, timing and positions, rounded, and nothing else', () => {
    expect(cleanInteraction({ ...peek, text: 'page text' }, now)).toEqual({ kind: 'peek', word: 'et', at: now - 2000,
      dwellMs: 1234, view: 'a1b2', context: 'c0ffee', position: 1, box: [10, 400, 600, 81], annotations: [['de', 12, 0], ['et', 140, 0]] });
    expect(cleanInteraction({ ...peek, uid: 'a1b2-7' }, now).uid).toBe('a1b2-7');
  });
  it('shown records carry the passage word count instead of a word', () => {
    const shown = { kind: 'shown', at: now, view: 'a1b2', context: 'c0ffee', words: 48, box: [0, 0, 600, 80], annotations: [['de', 1, 0]] };
    expect(cleanInteraction(shown, now)).toEqual(shown);
    for (const words of [0, 1.5, 10001, '48']) expect(cleanInteraction({ ...shown, words }, now)).toBeNull();
    const plan = { level: -1, intensity: 0.7, jitter: 0.62, limit: 2, candidates: 4, budget: 0.186, expected: 0.41 };
    expect(cleanInteraction({ ...shown, plan: { ...plan, text: 'x' } }, now)).toEqual({ ...shown, plan });
    const old = { level: -1, intensity: 0.7, jitter: 0.62, limit: 2, candidates: 4 }; // before levels meant a lookup budget
    expect(cleanInteraction({ ...shown, plan: old }, now)).toEqual({ ...shown, plan: old });
    const full = { level: 3, intensity: 5, jitter: 1, limit: 9, candidates: 9, expected: 2.5 }; // levels.js planSwaps at full: no budget
    expect(cleanInteraction({ ...shown, plan: full }, now)).toEqual({ ...shown, plan: full });
    const priced = { ...plan, sentencePrice: 0.08 }; // since whole sentences cost a price and thinning left the budget alone
    expect(cleanInteraction({ ...shown, plan: priced }, now)).toEqual({ ...shown, plan: priced });
    // since extension 0.10: what the budgeted swaps cost, and the delayed-start arms of the words shown (none: left out)
    const armed = { ...priced, spent: 0.1, arms: { 今: 'shown', 前: 'held:probe' } };
    expect(cleanInteraction({ ...shown, plan: armed }, now)).toEqual({ ...shown, plan: armed });
    expect(cleanInteraction({ ...shown, plan: { ...priced, arms: {} } }, now)).toEqual({ ...shown, plan: priced });
    for (const arms of [[], { 今: 1 }, { 今: 'Shown!' }, null]) expect(cleanInteraction({ ...shown, plan: { ...priced, arms } }, now)).toBeNull();
    expect(cleanInteraction({ ...shown, plan: { ...priced, spent: -1 } }, now)).toBeNull();
    for (const sentencePrice of [-0.01, 1.01, '0.08', null]) expect(cleanInteraction({ ...shown, plan: { ...plan, sentencePrice } }, now)).toBeNull();
    expect(cleanInteraction({ ...shown, plan: { ...plan, budget: 0, expected: 0 } }, now).plan).toMatchObject({ budget: 0, expected: 0 });
    for (const bad of [{ ...plan, level: 4 }, { ...plan, jitter: 1.2 }, { ...plan, limit: -1 }, { ...plan, budget: -0.1 },
      { ...plan, expected: 1001 }, { ...plan, budget: NaN }, { ...plan, budget: '0.5' }, { ...plan, expected: null }]) {
      expect(cleanInteraction({ ...shown, plan: bad }, now)).toBeNull();
    }
  });
  it('clicks keep a point and what was hit; selections a box, a length and the glossed words inside, never text', () => {
    const where = { at: now, view: 'a1b2', context: 'c0ffee', box: [0, 0, 600, 80], annotations: [['de', 1, 0], ['et', 90, 0], ['ou', 200, 20]] };
    const click = { ...where, kind: 'click', x: 40.4, y: 12.6, target: 'text', uid: 'a1b2-3' };
    expect(cleanInteraction({ ...click, text: 'page text' }, now)).toEqual({ ...click, x: 40, y: 13 });
    const select = { ...where, kind: 'select', x: 0, y: 0, width: 120.2, height: 18, chars: 31, covered: [1, 0, 1], ms: 640, text: 'page text' };
    expect(cleanInteraction(select, now)).toEqual({ ...where, kind: 'select', x: 0, y: 0, width: 120, height: 18, chars: 31, covered: [0, 1], ms: 640 });
    const stay = { ...where, kind: 'visible', y: 0, height: 80, ms: 4200.4, wall: 9000, away: 0, media: 3000, idle: 1800, uid: 'a1b2-4' };
    expect(cleanInteraction(stay, now)).toEqual({ ...stay, ms: 4200 });
    for (const bad of [{ ...stay, ms: 9500 }, { ...stay, idle: -1 }, { ...stay, wall: 4e6 }, { ...stay, height: 'tall' }]) {
      expect(cleanInteraction(bad, now)).toBeNull();
    }
    for (const bad of [{ ...click, target: 'image' }, { ...click, x: 'left' }, { ...click, y: 1e6 }, { ...select, covered: [3] },
      { ...select, chars: 0 }, { ...select, width: -1 }, { ...select, ms: -5 }, { ...select, covered: 'all' }]) {
      expect(cleanInteraction(bad, now)).toBeNull();
    }
  });
  it('describes what the log holds and what is still queued', () => {
    const reply = { ok: true, counts: { 'browser:peek': [40, 5], 't3:peek': [3, 1], 't3:pin': [1, 0], 'browser:flip': [2, 0],
      'browser:click': [1, 1] }, shownWords: [12345, 300], readingMs: [2_430_000, 60_000] };
    expect(describeStats(reply, [{ kind: 'peek' }, { kind: 'shown' }])).toEqual({ peeks: '44 (6 today) · 4 in chat',
      more: '2 flips · 1 click · 0 selections · 12,345 words shown · 41 min reading on screen · 1 waiting to send' });
    expect(describeStats({ ok: true, counts: {}, shownWords: [0, 0] }).peeks).toBe('0 (0 today)');
  });
  it('rejects anything malformed', () => {
    for (const bad of [{ ...peek, kind: 'seen' }, { ...peek, word: '' }, { ...peek, view: 'has spaces' },
      { ...peek, at: now + 3_600_000 }, { ...peek, dwellMs: -1 }, { ...peek, position: 1.5 }, { ...peek, box: [1, 2, 3] },
      { ...peek, annotations: [['de', 'left', 0]] }, { ...peek, annotations: Array(201).fill(['de', 0, 0]) }, { ...peek, uid: 'a b' }, null]) {
      expect(cleanInteraction(bad, now)).toBeNull();
    }
  });
  it('flush sends the log and clears it; a host without the log keeps the queue', async () => {
    const storage = memoryStorage();
    await queueLog(storage, [cleanInteraction(peek, now)]);
    const old = await flushLog({ storage, send: async () => ({ ok: false, error: 'unknown message type' }) });
    expect(old.sent).toBe(0);
    expect((await storage.get([LOG_KEY]))[LOG_KEY]).toHaveLength(1);
    const sent = [];
    expect((await flushLog({ storage, send: async (m) => { sent.push(m); return { ok: true, stored: 1 }; } })).sent).toBe(1);
    expect(sent.map((m) => [m.type, m.items.length])).toEqual([['log', 1]]);
    expect((await storage.get([LOG_KEY]))[LOG_KEY]).toEqual([]);
  });
});

describe('the reading index and the reading counts', () => {
  const INDEX = { ok: true, version: 'v1', entries: [[1361110, 'adj-i', 'new'], [1464900, 'n', 'sunday'], ['x', 'n', 'bad'], [5, 'n', '']] };
  it('asks for the index once per version and keeps what is well formed; without a host, nothing changes', async () => {
    const storage = memoryStorage();
    const asked = [];
    const send = async (msg) => { asked.push(msg.type); return INDEX; };
    expect(await refreshReadingIndex({ storage, send, version: 'v1' })).toEqual({ changed: true, count: 2 });
    expect((await storage.get([INDEX_KEY]))[INDEX_KEY]).toEqual({ version: 'v1', entries: INDEX.entries.slice(0, 2) });
    expect(await refreshReadingIndex({ storage, send, version: 'v1' })).toEqual({ changed: false });
    expect(await refreshReadingIndex({ storage, send, version: '' })).toEqual({ changed: false });
    expect(asked).toEqual(['readingIndex']);
    const old = await refreshReadingIndex({ storage, send: async () => ({ ok: false, error: 'unknown message type' }), version: 'v2' });
    expect(old.error).toContain('unknown message type');
    expect((await storage.get([INDEX_KEY]))[INDEX_KEY].version).toBe('v1');
  });
  it('queues each passage once a day, counts only; flushes to the host and keeps them when it fails', async () => {
    const storage = memoryStorage();
    const item = { d: 'web:00ff00ff00ff00ff', day: '2026-10-21', entries: { 1361110: 2 } };
    expect(await queueReading(storage, item)).toBe(true);
    expect(await queueReading(storage, item)).toBe(false); // read again today
    expect(await readToday(storage, item.d, '2026-10-21')).toBe(true);
    expect(await readToday(storage, item.d, '2026-10-22')).toBe(false);
    expect(await queueReading(storage, { ...item, day: '2026-10-22' })).toBe(true); // another day: the host counts it once
    for (const bad of [{ ...item, d: 'the text itself' }, { ...item, entries: {} }, { ...item, entries: { x: 1 } }, { ...item, entries: { 1: 0 } },
      { ...item, day: 'today' }, null]) expect(await queueReading(storage, bad)).toBe(false);
    const failing = await flushReading({ storage, send: async () => ({ ok: false, error: 'unknown message type' }) });
    expect(failing).toEqual({ sent: 0, error: 'unknown message type' });
    expect((await storage.get([READING_KEY]))[READING_KEY]).toHaveLength(2);
    const sent = [];
    expect(await flushReading({ storage, send: async (msg) => { sent.push(msg); return { ok: true, taken: 2 }; } })).toEqual({ sent: 2, taken: 2 });
    expect(sent).toEqual([{ type: 'reading', passages: [item, { ...item, day: '2026-10-22' }] }]);
    expect((await storage.get([READING_KEY]))[READING_KEY]).toEqual([]);
  });
});
