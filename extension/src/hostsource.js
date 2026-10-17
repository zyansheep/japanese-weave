// Word state from the Japanese Weave Claude Code plugin, through the browser native messaging host that its
// setup step installs. No chrome APIs here: the native-message function and storage are injected so this
// tests without a browser. Without the host, the bundled lists in levels.js keep working.

export const HOST = 'com.japaneseweave.host';
export const STATE_KEY = 'wordState';
export const PENDING_KEY = 'pendingEvents';
export const SENT_KEY = 'seenSent';
export const LOG_KEY = 'pendingLog';
const LOG_MAX = 500;
const ANNOTATIONS_MAX = 200;

// Drops unusable rows, keeps the rest. Throws when nothing usable is left, so a bad reply never replaces a good list.
// Adaptive difficulty (docs/adaptive-difficulty.md) comes along: per word, knowledge k and its uncertainty sd when the
// gloss log has evidence; the dial (challenge, peeks per 100 words), its budget, and the realized peek rate.
export function parseState(data) {
  const num = (x) => typeof x === 'number' && Number.isFinite(x);
  const words = [];
  const seen = new Set();
  for (const w of data?.words || []) {
    if (!w || typeof w.ja !== 'string' || typeof w.en !== 'string') continue;
    const ja = w.ja.trim();
    const en = w.en.trim();
    if (!ja || !en || seen.has(ja)) continue;
    seen.add(ja);
    words.push({ ja, en, hint: typeof w.hint === 'string' ? w.hint.trim() : '', kana: typeof w.kana === 'string' ? w.kana.trim() : '',
      status: typeof w.status === 'string' ? w.status : 'active', ...(num(w.k) && num(w.sd) ? { k: w.k, sd: w.sd } : {}) });
  }
  if (!words.length) throw new Error('host sent no usable words');
  const stage = Math.floor(Number(data?.stage));
  return {
    version: String(data?.version ?? ''),
    stage: stage >= 1 && stage <= 4 ? stage : 1,
    paused: data?.paused === true,
    sites: data?.prefs?.extension_sites === 'ask' ? 'ask' : 'all', // setup's default for sites not set in the popup
    challenge: num(data?.challenge) && data.challenge > 0 ? data.challenge : 0,
    budget: num(data?.budget) && data.budget > 0 ? data.budget : 0,
    rate: num(data?.rate?.per100) ? { per100: data.rate.per100, peeks: Number(data.rate.peeks) || 0,
      shownWords: Number(data.rate.shownWords) || 0, days: Number(data.rate.days) || 0 } : null,
    words,
  };
}

export async function readCached(storage) {
  const obj = await storage.get([STATE_KEY]);
  const s = obj?.[STATE_KEY];
  if (!s || !Array.isArray(s.words) || !s.words.length) return null;
  return s;
}

// Asks the host for the word state and stores it. Never throws: a missing host leaves the cached (or bundled)
// list in place and reports the reason. The model keys the plugin's setup stored come along, kept separately.
export async function refresh({ storage, send, now = () => Date.now() }) {
  const cached = await readCached(storage);
  try {
    const reply = await send({ type: 'state' });
    if (!reply?.ok) throw new Error(reply?.error || 'no reply from the Japanese Weave host');
    const state = parseState(reply);
    const changed = !cached || cached.version !== state.version;
    const stored = { ...state, fetchedAt: now() };
    const key = (k) => (typeof reply[k] === 'string' ? reply[k] : '');
    await storage.set({ [STATE_KEY]: stored, hostKey: key('geminiKey'), hostAnthropicKey: key('anthropicKey') });
    return { state: stored, changed, error: null };
  } catch (e) {
    return { state: cached, changed: false, error: String(e?.message || e) };
  }
}

export function localDay(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// Queues seen/flip/peek events. Seen and peek count once per word per day, so a busy timeline stays cheap.
export async function queueEvents(storage, events, day = localDay()) {
  const obj = await storage.get([PENDING_KEY, SENT_KEY]);
  const pending = Array.isArray(obj[PENDING_KEY]) ? obj[PENDING_KEY] : [];
  const sent = obj[SENT_KEY]?.day === day ? obj[SENT_KEY] : { day, words: [] };
  for (const e of events || []) {
    if (!e || typeof e.word !== 'string' || !['seen', 'flip', 'peek'].includes(e.kind)) continue;
    const word = e.word.normalize('NFKC').toLowerCase();
    if (e.kind !== 'flip') {
      const tag = e.kind === 'seen' ? word : `${e.kind}:${word}`;
      if (sent.words.includes(tag)) continue;
      sent.words.push(tag);
    }
    pending.push({ word, kind: e.kind, day });
  }
  await storage.set({ [PENDING_KEY]: pending.slice(-500), [SENT_KEY]: sent });
  return pending.length;
}

// Words already seen today, so the prompt can put the ones not yet shown first.
export async function shownToday(storage, day = localDay()) {
  const obj = await storage.get([SENT_KEY]);
  const sent = obj?.[SENT_KEY];
  return sent?.day === day ? sent.words.filter((w) => !w.includes(':')) : [];
}

// Sends queued events to the host; keeps them queued if the host is missing or fails.
export async function flushEvents({ storage, send }) {
  const obj = await storage.get([PENDING_KEY]);
  const pending = Array.isArray(obj[PENDING_KEY]) ? obj[PENDING_KEY] : [];
  if (!pending.length) return { sent: 0 };
  try {
    const reply = await send({ type: 'events', events: pending });
    if (!reply?.ok) throw new Error(reply?.error || 'host refused events');
    const now = await storage.get([PENDING_KEY]);
    const rest = (now[PENDING_KEY] || []).slice(pending.length); // events queued while we were sending
    await storage.set({ [PENDING_KEY]: rest });
    return { sent: pending.length, applied: reply.applied };
  } catch (e) {
    return { sent: 0, error: String(e?.message || e) };
  }
}

// One gloss interaction for the host's log: which word was peeked (or flipped) when and for how long, and where every
// glossed word in the same passage sat relative to the passage. Words and positions only, never page text. uid makes
// a resend harmless. kind 'shown' instead marks when a passage's Japanese first came on screen, with its word count:
// the denominator of the peek rate. 'click' and 'select' mark where the reader was: a click's point (x, y from the
// passage's corner) and whether it hit a link or control; a selection's box, its length in characters and the glossed
// words it covered (indexes into annotations), never its text. 'visible' is one stay of the passage on screen: ms is
// the time a reader could have been reading it, wall all of it, and away, media and idle the rest by why it doesn't
// count. Returns null for anything malformed.
export function cleanInteraction(msg, now = Date.now()) {
  const num = (x) => typeof x === 'number' && Number.isFinite(x);
  const word = (x) => typeof x === 'string' && x.length > 0 && x.length <= 40;
  const id = (x) => typeof x === 'string' && /^[\w:-]{1,64}$/.test(x);
  if (!msg || !['peek', 'flip', 'shown', 'click', 'select', 'visible'].includes(msg.kind) || !id(msg.view) || !id(msg.context)) return null;
  if (!num(msg.at) || msg.at < now - 86400000 || msg.at > now + 60000) return null;
  if (!Array.isArray(msg.box) || msg.box.length !== 4 || !msg.box.every(num)) return null;
  if (!Array.isArray(msg.annotations) || msg.annotations.length > ANNOTATIONS_MAX) return null;
  if (!msg.annotations.every((a) => Array.isArray(a) && a.length === 3 && word(a[0]) && num(a[1]) && num(a[2]))) return null;
  const round = (x) => Math.round(x);
  const common = { kind: msg.kind, at: round(msg.at), view: msg.view, context: msg.context, box: msg.box.map(round),
    annotations: msg.annotations.map(([w, x, y]) => [w, round(x), round(y)]) };
  if (msg.kind === 'shown') return Number.isInteger(msg.words) && msg.words > 0 && msg.words <= 10000 ? { ...common, words: msg.words } : null;
  if (msg.uid !== undefined && !id(msg.uid)) return null;
  const uid = msg.uid ? { uid: msg.uid } : {};
  const coord = (x) => num(x) && Math.abs(x) <= 100000;
  if (msg.kind === 'visible') {
    const times = ['ms', 'wall', 'away', 'media', 'idle'];
    if (!coord(msg.y) || !coord(msg.height) || msg.height < 0 || msg.ms > msg.wall) return null;
    if (!times.every((k) => num(msg[k]) && msg[k] >= 0 && msg[k] <= 3600000)) return null;
    return { ...common, y: round(msg.y), height: round(msg.height), ...Object.fromEntries(times.map((k) => [k, round(msg[k])])), ...uid };
  }
  if (msg.kind === 'click' || msg.kind === 'select') {
    if (!coord(msg.x) || !coord(msg.y)) return null;
    const at = { ...common, x: round(msg.x), y: round(msg.y) };
    if (msg.kind === 'click') return ['text', 'link'].includes(msg.target) ? { ...at, target: msg.target, ...uid } : null;
    if (!coord(msg.width) || !coord(msg.height) || msg.width < 0 || msg.height < 0) return null;
    if (!Number.isInteger(msg.chars) || msg.chars < 1 || msg.chars > 1000000 || !num(msg.ms) || msg.ms < 0 || msg.ms > 600000) return null;
    if (!Array.isArray(msg.covered) || !msg.covered.every((i) => Number.isInteger(i) && i >= 0 && i < msg.annotations.length)) return null;
    return { ...at, width: round(msg.width), height: round(msg.height), chars: msg.chars, covered: [...new Set(msg.covered)].sort((a, b) => a - b),
      ms: round(msg.ms), ...uid };
  }
  if (!word(msg.word) || !num(msg.dwellMs) || msg.dwellMs < 0 || msg.dwellMs > 600000 || !Number.isInteger(msg.position)) return null;
  return { ...common, word: msg.word, dwellMs: round(msg.dwellMs), position: msg.position, ...uid };
}

// What the host's log holds (fw_host stats) plus what is still queued here, as two lines for the popup:
// { peeks: '42 (7 today) · 5 in chat', more: '3 flips · 2 clicks · … · 40 min reading on screen' }.
export function describeStats(reply, queued = []) {
  const counts = reply?.counts || {};
  const sum = (kinds, surface) => Object.entries(counts)
    .filter(([key]) => kinds.includes(key.split(':')[1]) && (!surface || key.split(':')[0] === surface))
    .reduce((acc, [, v]) => [acc[0] + v[0], acc[1] + v[1]], [0, 0]);
  const plural = (n, noun) => `${n.toLocaleString('en-US')} ${noun}${n === 1 ? '' : 's'}`;
  const [peeks, today] = sum(['peek', 'pin']);
  const chat = sum(['peek', 'pin'], 't3')[0];
  const shown = reply?.shownWords?.[0] || 0;
  const waiting = queued.filter((i) => i?.kind !== 'shown').length;
  const minutes = Math.round((reply?.readingMs?.[0] || 0) / 60000);
  const more = [plural(sum(['flip'])[0], 'flip'), plural(sum(['click'])[0], 'click'), plural(sum(['select'])[0], 'selection'),
    `${plural(shown, 'word')} shown`, `${minutes} min reading on screen`];
  if (waiting) more.push(`${waiting} waiting to send`);
  return { peeks: `${peeks.toLocaleString('en-US')} (${today} today)${chat ? ` · ${chat} in chat` : ''}`, more: more.join(' · ') };
}

export async function queuedLog(storage) {
  const obj = await storage.get([LOG_KEY]);
  return Array.isArray(obj[LOG_KEY]) ? obj[LOG_KEY] : [];
}

export async function queueLog(storage, items) {
  const obj = await storage.get([LOG_KEY]);
  const pending = Array.isArray(obj[LOG_KEY]) ? obj[LOG_KEY] : [];
  pending.push(...items);
  await storage.set({ [LOG_KEY]: pending.slice(-LOG_MAX) });
  return pending.length;
}

// Sends queued interactions to the host's log; keeps them queued if the host is missing or older than the log.
export async function flushLog({ storage, send }) {
  const obj = await storage.get([LOG_KEY]);
  const pending = Array.isArray(obj[LOG_KEY]) ? obj[LOG_KEY] : [];
  if (!pending.length) return { sent: 0 };
  try {
    const reply = await send({ type: 'log', items: pending });
    if (!reply?.ok) throw new Error(reply?.error || 'host refused the log');
    const now = await storage.get([LOG_KEY]);
    await storage.set({ [LOG_KEY]: (now[LOG_KEY] || []).slice(pending.length) });
    return { sent: pending.length, stored: reply.stored };
  } catch (e) {
    return { sent: 0, error: String(e?.message || e) };
  }
}
