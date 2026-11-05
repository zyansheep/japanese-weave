// Weaves an English chat draft the way the extension weaves a page, with no model call (fw.py weave; README "Chat
// replies"): matcher.js finds every place a word in use could go, senses.js says which options are safe, verify.js
// candidatesOf and levels.js planSwaps pick them by the word model and the level's lookup budget. A span the web would
// ask its model about is taken at its first option and marked {{English→Japanese}}, for the writer to keep or undo.
// Words known at a glance go in line by line (each once a line, as planSwaps keeps one use of a word per passage);
// learning words come from one budget for the whole reply, at most the level's number a sentence. Code fences, inline
// code, link targets, URLs and tags are left alone. Reads one JSON object on stdin: { draft, words (the host's state
// message's words), budgetPer100, intensity (levels.js: 3 is full, with no budget), sentencePrice, recency,
// sentenceChance, maxChars, grammar: { known: [pattern names], learning: { name, explain, example } | null } }
// (chatweave.py sentence_plan). Prints a first line with what it did, how many sentences to make Japanese (the
// sentences' share of the budget over sentenceChance, the open chance of a typical allowed sentence, when the sentence
// model is ready, else over the price), how long each may be and which grammar they may use, a line of -----, then
// the woven draft; the count is never more than the price alone gives. Runs under node or bun.
import { readFileSync } from 'node:fs';

const src = (name) => new URL(`../extension/src/${name}`, import.meta.url).href;
const { pipelineFor, planText } = await import(src('pipeline.js'));
const { candidatesOf } = await import(src('verify.js'));
const { planSwaps, exactJa, isZeroJapanese, wordCount, SENTENCE_SHARE, SENTENCE_PRICE } = await import(src('levels.js'));
const { locate } = await import(src('text.js'));

const input = JSON.parse(readFileSync(0, 'utf8'));
const words = Array.isArray(input.words) ? input.words : [];
const byJa = new Map(words.map((w) => [exactJa(w.ja), w]));
const pipe = pipelineFor(words);
const intensity = Number.isFinite(input.intensity) ? input.intensity : 0;
const full = intensity >= 3;
const budgetPer100 = Number.isFinite(input.budgetPer100) ? input.budgetPer100 : 0.5;
const price = input.sentencePrice > 0 && input.sentencePrice <= 1 ? input.sentencePrice : SENTENCE_PRICE;
const opts = { sentencePrice: price, recency: input.recency || null, now: Date.now(), jitter: 1, intensity };

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
const isFree = (ja) => byJa.get(exactJa(ja))?.free === true;

function placed(text, offset, budget) {
  const plan = planText(pipe, text);
  const candidates = candidatesOf(plan, firstOptions(plan), words, 2);
  const { swaps } = planSwaps(2, candidates, text, words, { ...opts, budgetPer100: budget });
  const out = [];
  for (const s of swaps) {
    if (s.kind) continue; // sentences and phrases: none without a model's answer
    const m = locate({ text }, s.o, s.ctx);
    if (!m) continue;
    const unsure = plan.spans.some((x) => x.doubtful.length && x.span.start === m.start && x.span.end === m.end);
    out.push({ start: offset + m.start, end: offset + m.end, en: text.slice(m.start, m.end), ja: s.f, reading: s.k,
      gloss: s.g, unsure, free: isFree(s.f) });
  }
  return out;
}

// Known words, line by line (a budget of 0 keeps the free swaps only); then learning words over the whole reply.
const swaps = [];
for (const l of lines) if (!zero(l) && wordCount(l.text) >= 3) swaps.push(...placed(l.text, l.start, 0).filter((s) => s.free));
const lineOf = (pos) => lines.find((l) => pos >= l.start && pos <= l.start + l.text.length);
for (const s of placed(whole, 0, budgetPer100)) {
  if (s.free || zero(lineOf(s.start)) || swaps.some((t) => s.start < t.end && t.start < s.end)) continue;
  swaps.push(s);
}
swaps.sort((a, b) => a.start - b.start);

const KANJI = /[㐀-䶿一-鿿々]/;
const abbr = (s) => `<abbr title="${KANJI.test(s.ja) && s.reading ? `${s.reading} · ${s.gloss}` : s.gloss}">${s.ja}</abbr>`;
const restore = (t) => t.replace(/[-]/g, (c) => saved[c.charCodeAt(0) - 0xE000]);
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

const learning = swaps.filter((s) => !s.free);
// The sentence model's count (the budget over a typical allowed sentence's open chance), never more than the count the
// price gave before it (the reader found that many already too much): the smaller of the two.
const chance = input.sentenceChance > 0 && input.sentenceChance <= 1 ? input.sentenceChance : price;
const sentenceBudget = (budgetPer100 * wordCount(whole) / 100) * SENTENCE_SHARE;
const sentences = Math.max(1, Math.min(Math.round(sentenceBudget / chance), Math.round(sentenceBudget / price)));
const maxChars = Number.isFinite(input.maxChars) && input.maxChars > 0 ? input.maxChars : 14;
const g = input.grammar && Array.isArray(input.grammar.known) ? input.grammar : null;
const l = g && g.learning;
const grammarLine = g ? ` Each one at most ${maxChars} Japanese characters (split a longer one), using only these ` +
  `grammar patterns: ${g.known.join(', ')}; plus at most one learning pattern in a sentence. ` +
  (l ? `This week's learning pattern is ${l.name}: ${l.explain}, as in ${l.example}; use it on purpose once or twice. `
    : 'No learning pattern this week. ') +
  'Any other grammar stays out: rephrase that sentence with these, or keep it English.' : '';
console.log(`jw-weave: ${swaps.length - learning.length} known-word swaps and ${learning.length} learning-word swaps ` +
  `(${learning.map((s) => s.ja).join(', ') || 'none'}${learning.length ? ': keep each whose sense fits' : ''}). ` +
  `${swaps.filter((s) => s.unsure).length} swaps are marked ` +
  '{{English→Japanese}}: the code wasn\'t sure of the sense. ' + (full
  ? 'No limit on Japanese or mixed sentences: make every sentence the user could follow Japanese or mixed.'
  : `Make about ${sentences} sentence${sentences === 1 ? '' : 's'} Japanese or mixed, each with a translation.`) +
  grammarLine);
console.log('-----');
console.log(result);
