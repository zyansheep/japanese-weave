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
export function parseState(data) {
  const words = [];
  const seen = new Set();
  for (const w of data?.words || []) {
    if (!w || typeof w.ja !== 'string' || typeof w.en !== 'string') continue;
    const ja = w.ja.trim();
    const en = w.en.trim();
    if (!ja || !en || seen.has(ja)) continue;
    seen.add(ja);
    words.push({ ja, en, hint: typeof w.hint === 'string' ? w.hint.trim() : '', kana: typeof w.kana === 'string' ? w.kana.trim() : '',
      status: typeof w.status === 'string' ? w.status : 'active' });
  }
  if (!words.length) throw new Error('host sent no usable words');
  const stage = Math.floor(Number(data?.stage));
  return {
    version: String(data?.version ?? ''),
    stage: stage >= 1 && stage <= 4 ? stage : 1,
    paused: data?.paused === true,
    sites: data?.prefs?.extension_sites === 'ask' ? 'ask' : 'all', // setup's default for sites not set in the popup
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
// list in place and reports the reason. The Gemini key the plugin's setup stored comes along, kept separately.
export async function refresh({ storage, send, now = () => Date.now() }) {
  const cached = await readCached(storage);
  try {
    const reply = await send({ type: 'state' });
    if (!reply?.ok) throw new Error(reply?.error || 'no reply from the Japanese Weave host');
    const state = parseState(reply);
    const changed = !cached || cached.version !== state.version;
    const stored = { ...state, fetchedAt: now() };
    await storage.set({ [STATE_KEY]: stored, hostKey: typeof reply.geminiKey === 'string' ? reply.geminiKey : '' });
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
// glossed word in the same passage sat relative to the passage. Words and positions only, never page text.
// Returns null for anything malformed.
export function cleanInteraction(msg, now = Date.now()) {
  const num = (x) => typeof x === 'number' && Number.isFinite(x);
  const word = (x) => typeof x === 'string' && x.length > 0 && x.length <= 40;
  const id = (x) => typeof x === 'string' && /^[\w:-]{1,64}$/.test(x);
  if (!msg || !['peek', 'flip'].includes(msg.kind) || !word(msg.word) || !id(msg.view) || !id(msg.context)) return null;
  if (!num(msg.at) || msg.at < now - 86400000 || msg.at > now + 60000) return null;
  if (!num(msg.dwellMs) || msg.dwellMs < 0 || msg.dwellMs > 600000 || !Number.isInteger(msg.position)) return null;
  if (!Array.isArray(msg.box) || msg.box.length !== 4 || !msg.box.every(num)) return null;
  if (!Array.isArray(msg.annotations) || msg.annotations.length > ANNOTATIONS_MAX) return null;
  if (!msg.annotations.every((a) => Array.isArray(a) && a.length === 3 && word(a[0]) && num(a[1]) && num(a[2]))) return null;
  const round = (x) => Math.round(x);
  return { kind: msg.kind, word: msg.word, at: round(msg.at), dwellMs: round(msg.dwellMs), view: msg.view,
    context: msg.context, position: msg.position, box: msg.box.map(round),
    annotations: msg.annotations.map(([w, x, y]) => [w, round(x), round(y)]) };
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
