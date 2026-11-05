// Weaves an English chat draft the way the extension weaves a page, with no model call (fw.py weave; README "Chat
// replies"): matcher.js finds every place a word in use could go, senses.js says which options are safe, verify.js
// candidatesOf and levels.js planSwaps pick them by the word model. A span the web would ask its model about is taken at
// its first option and marked {{English→Japanese}}, for the writer to keep or undo. Since replies carry every level
// at once (twins.py), the weave offers every candidate, as at full (intensity 3): words known at a glance line by line
// (each once a line, as planSwaps keeps one use of a word per passage at normal), then the rest over the whole reply at
// full, a word again where it fits, and at most DICTIONARY_MAX of the dictionary words (the host's pool, flagged dictionary: true)
// that aren't in the word list, those nearest the target lookup chance first; check-reply decides what each level
// shows. Code fences, inline code, link targets, URLs and tags are left alone. Reads one JSON object on stdin: { draft,
// words (the host's state message's words, with the pool's), recency, intensity (levels.js: 3 is full, the default) }.
// Prints one JSON object: { text: the woven draft, words: its word count, swaps: [{ ja, title, en, kana, p, free,
// dictionary, unsure }] }, p the word's current lookup chance as levels.js costOf prices it (its long-term chance when
// known at a glance). Runs under node or bun.
import { readFileSync } from 'node:fs';

const src = (name) => new URL(`../extension/src/${name}`, import.meta.url).href;
const { pipelineFor, planText } = await import(src('pipeline.js'));
const { candidatesOf } = await import(src('verify.js'));
const { planSwaps, exactJa, isZeroJapanese, wordCount, currentChance, FREE_P, KNOWN_PRIOR_K, TARGET_P } =
  await import(src('levels.js'));
const { locate } = await import(src('text.js'));

const DICTIONARY_MAX = 6; // dictionary words offered in one reply: they show only at full
const PRIOR_K = 1.0; // levels.js PRIOR_K: the knowledge of a word the log has nothing on

const input = JSON.parse(readFileSync(0, 'utf8'));
const words = Array.isArray(input.words) ? input.words : [];
const byJa = new Map(words.map((w) => [exactJa(w.ja), w]));
const pipe = pipelineFor(words);
const intensity = Number.isFinite(input.intensity) ? input.intensity : 3;
const now = Date.now();
const opts = { recency: input.recency || null, now, jitter: 1, intensity };

// Lines, code fences kept out; inside a line, inline code, link targets, URLs and tags become one private-use
// character each (no letter, so the matcher skips it), put back at the end.
const saved = [];
const lines = [];
let fence = false;
for (const raw of String(input.draft || '').split('\n')) {
  if (/^\s*(```|~~~)/.test(raw)) { fence = !fence; lines.push({ raw, code: true, text: '' }); continue; }
  if (fence) { lines.push({ raw, code: true, text: '' }); continue; }
  const text = raw.replace(/`[^`]*`|\]\([^)]*\)|https?:\/\/\S+|<[^>]+>/g, (m) => String.fromCharCode(0xE000 + saved.push(m) - 1));
  lines.push({ raw, code: false, text });
}
let at = 0;
for (const l of lines) { l.start = at; at += l.text.length + 1; }
const whole = lines.map((l) => l.text).join('\n');
const zero = (l) => l.code || isZeroJapanese(l.text);

// Every doubtful span at its first option: what the web's model would be asked about, left to the writer here.
const firstOptions = (plan) => ({ picks: new Map(plan.spans.filter((m) => m.doubtful.length).map((m) => [m.n, 0])), swaps: [] });

// A word's price as levels.js costOf has it: its long-term chance when known at a glance, else its current one.
function chanceOf(ja) {
  const w = byJa.get(exactJa(ja));
  const k = Number.isFinite(w?.k) ? w.k : w?.status === 'known' ? KNOWN_PRIOR_K : PRIOR_K;
  const long = 1 / (1 + Math.exp(k));
  const free = typeof w?.free === 'boolean' ? w.free : long < FREE_P;
  return { p: free ? long : currentChance(k, w?.read, now, opts.recency), free, dictionary: w?.dictionary === true };
}

function placed(text, offset, level) {
  const plan = planText(pipe, text);
  const candidates = candidatesOf(plan, firstOptions(plan), words, 2);
  const { swaps } = planSwaps(2, candidates, text, words, { ...opts, intensity: level, budgetPer100: 0 });
  const out = [];
  for (const s of swaps) {
    if (s.kind) continue; // sentences and phrases: none without a model's answer
    const m = locate({ text }, s.o, s.ctx);
    if (!m) continue;
    const unsure = plan.spans.some((x) => x.doubtful.length && x.span.start === m.start && x.span.end === m.end);
    out.push({ start: offset + m.start, end: offset + m.end, en: text.slice(m.start, m.end), ja: s.f, reading: s.k,
      gloss: s.g, unsure, ...chanceOf(s.f) });
  }
  return out;
}

// Known words line by line, at normal with no budget: each once a line; then the rest over the whole reply, at full.
const swaps = [];
for (const l of lines) if (!zero(l) && wordCount(l.text) >= 3) swaps.push(...placed(l.text, l.start, 0).filter((s) => s.free));
const lineOf = (pos) => lines.find((l) => pos >= l.start && pos <= l.start + l.text.length);
const logit = (p) => Math.log(p / (1 - p));
const closeness = (s) => -Math.abs(logit(Math.min(Math.max(s.p, 1e-6), 1 - 1e-6)) - logit(TARGET_P));
const rest = placed(whole, 0, intensity).filter((s) => !s.free && !zero(lineOf(s.start)));
const dictionary = new Set(rest.filter((s) => s.dictionary).sort((a, b) => closeness(b) - closeness(a)).slice(0, DICTIONARY_MAX));
for (const s of rest) {
  if ((s.dictionary && !dictionary.has(s)) || swaps.some((t) => s.start < t.end && t.start < s.end)) continue;
  swaps.push(s);
}
swaps.sort((a, b) => a.start - b.start);

const KANJI = /[㐀-䶿一-鿿々]/;
const title = (s) => (KANJI.test(s.ja) && s.reading ? `${s.reading} · ${s.gloss}` : s.gloss);
const abbr = (s) => `<abbr title="${title(s)}">${s.ja}</abbr>`;
const restore = (t) => t.replace(/[\uE000-\uF8FF]/g, (c) => saved[c.charCodeAt(0) - 0xE000]);
const result = lines.map((l) => {
  if (l.code) return l.raw;
  let t = '';
  let from = 0;
  for (const s of swaps.filter((x) => x.start >= l.start && x.end <= l.start + l.text.length)) {
    t += l.text.slice(from, s.start - l.start) + (s.unsure ? `{{${s.en}→${abbr(s)}}}` : abbr(s));
    from = s.end - l.start;
  }
  return restore(t + l.text.slice(from));
}).join('\n');

const round = (x) => Math.round(x * 10000) / 10000;
console.log(JSON.stringify({ text: result, words: wordCount(whole), swaps: swaps.map((s) => ({ ja: s.ja, title: title(s),
  en: s.en, kana: s.reading || '', p: round(s.p), free: s.free, dictionary: s.dictionary, unsure: s.unsure })) }));
