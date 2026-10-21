// The web pipeline's glue (PLAN.md section 6, "Candidates on the web"; docs/swap-prompt.md, "The web pipeline"). Code
// finds every place a word in use could go (matcher.js), takes the options flagged safe by itself (senses.js, with the
// flags the native host sends per word), and one model call per batch of passages checks the rest and writes the dense
// sentences and phrases (verify.js); levels.js validates every candidate and planSwaps picks a level's share. This
// module holds what the background needs between those steps: the matcher's entries and flags from the word list, a
// passage's answer keyed by what was asked and stored by place, so a changed word list re-asks only the passages whose
// doubtful spans changed, and the dictionary entries a passage read shows (reading counts). Pure.
import { buildIndex, matchText } from './matcher.js';
import { toHiragana } from './senses.js';
import { planPassage, contextFor, CHECK_VERSION } from './verify.js';
import { validateSwaps } from './levels.js';
import { hashKey } from './hash.js';

// The matcher's entries and the sense flags for a word list (the host's words, or the bundled lists): each word
// indexed by its glosses as the host lists them (else its gloss, split as matcher.js glossAlternatives does), shown to
// the model with its gloss as written (label), and its entry id, for the reading counts. A word's glosses the host
// lists as safe are safe; every other is doubtful (senses.js senseFlag finds no row). Returns { words, index, flags }.
export function pipelineFor(words) {
  const list = Array.isArray(words) ? words : [];
  const entries = list.map((w) => ({ ja: w.ja, kana: w.kana || '', en: Array.isArray(w.glosses) && w.glosses.length ? w.glosses : w.en,
    label: w.en, ...(Number.isInteger(w.id) ? { id: w.id } : {}) }));
  const flags = new Map();
  for (const w of list) {
    for (const gloss of Array.isArray(w.safe) ? w.safe : []) {
      const key = `${w.ja}\t${gloss}`;
      if (!flags.has(key)) flags.set(key, []);
      flags.get(key).push({ kana: toHiragana(w.kana || ''), flag: 's', id: Number.isInteger(w.id) ? w.id : null });
    }
  }
  return { words: list, index: buildIndex(entries), flags };
}

// A passage's plan (verify.js planPassage): its spans, numbered, each safe (code takes it) or doubtful (the model is
// asked), and its dense sentences.
export const planText = (pipe, text) => planPassage(text, matchText(pipe.index, text), pipe.flags);

const label = (o) => o.label ?? o.en;
// The cache key of a passage's answer: the check prompt's version, the text, each doubtful span with its options as the
// model sees them, and the dense sentences. Safe spans are left out: the model only reads them, so a word that becomes
// safe, or a new safe word, re-asks nothing.
export function answerKey(plan) {
  const doubtful = plan.spans.filter((m) => m.doubtful.length)
    .map((m) => [m.span.start, m.span.end, m.doubtful.map((o) => `${o.ja}=${label(o)}`)]);
  return `${CHECK_VERSION}:${hashKey(JSON.stringify([plan.text, doubtful, plan.sentences.map((s) => [s.start, s.end])]))}`;
}

// A parsed answer (verify.js parseCheckResponse: picks by span number and option letter, sentences and phrases by
// number) stored by place, as numbers change with the list: { picks: [[start, end, ja or null]], swaps: [{ s | r:
// [start, end], p }] } (s a sentence, r a phrase from the start of one span to the end of another).
export function storeAnswer(plan, answer) {
  const picks = [];
  for (const m of plan.spans) {
    if (!m.doubtful.length || !answer?.picks?.has(m.n)) continue;
    const k = answer.picks.get(m.n);
    picks.push([m.span.start, m.span.end, Number.isInteger(k) && m.doubtful[k] ? m.doubtful[k].ja : null]);
  }
  const swaps = [];
  for (const s of answer?.swaps || []) {
    const at = String(s.at).trim();
    const sentence = /^s(\d+)$/i.exec(at);
    const range = /^(\d+)(?:\s*-\s*(\d+))?$/.exec(at);
    if (sentence) {
      const x = plan.sentences.find((y) => y.n === Number(sentence[1]));
      if (x) swaps.push({ s: [x.start, x.end], p: s.p });
    } else if (range) {
      const a = plan.spans[Number(range[1]) - 1];
      const b = plan.spans[Number(range[2] || range[1]) - 1];
      if (a && b && b.span.end > a.span.start) swaps.push({ r: [a.span.start, b.span.end], p: s.p });
    }
  }
  return { picks, swaps };
}

// A stored answer back in a plan's numbers, as verify.js candidatesOf takes it: { picks: Map span number -> option
// index or null, swaps: [{ at, p }] }. A place the plan no longer has is left out.
export function loadAnswer(plan, stored) {
  if (!stored || !Array.isArray(stored.picks)) return null;
  const at = (start, end) => plan.spans.find((m) => m.span.start === start && m.span.end === end);
  const picks = new Map();
  for (const [start, end, ja] of stored.picks) {
    const m = at(start, end);
    if (!m?.doubtful.length) continue;
    const k = ja === null ? -1 : m.doubtful.findIndex((o) => o.ja === ja);
    if (ja === null || k >= 0) picks.set(m.n, ja === null ? null : k);
  }
  const swaps = [];
  for (const s of Array.isArray(stored.swaps) ? stored.swaps : []) {
    if (typeof s?.p !== 'string') continue;
    if (Array.isArray(s.s)) {
      const x = plan.sentences.find((y) => y.start === s.s[0] && y.end === s.s[1]);
      if (x) swaps.push({ at: `s${x.n}`, p: s.p });
    } else if (Array.isArray(s.r)) {
      const a = plan.spans.find((m) => m.span.start === s.r[0]);
      const b = plan.spans.find((m) => m.span.end === s.r[1]);
      if (a && b && b.n >= a.n) swaps.push({ at: a.n === b.n ? `${a.n}` : `${a.n}-${b.n}`, p: s.p });
    }
  }
  return { picks, swaps };
}

// The reading index (the host's readingIndex: [[entry id, JMdict pos codes, "gloss;gloss"]], every swappable entry's
// safe glosses, in rank order) as a matcher index, so the first option of a span is the most frequent entry.
export function readingIndexOf(rows) {
  return buildIndex((Array.isArray(rows) ? rows : []).map(([id, pos, glosses]) => ({ ja: '', id,
    ...(pos ? { pos: String(pos).split(',') } : {}), en: String(glosses).split(';') })));
}

// The dictionary entries a passage read shows, {entry id: times}, as chat's counts are kept (scripts/reading.py),
// counts only: every span of the reading index (all swappable entries' safe glosses; never closed-class English) for
// its most frequent entry, then each span of the words in use whose option is safe or the model's pick (answer, as
// loadAnswer gives it) and that is kept as a word swap (levels.js validateSwaps), where no counted span overlaps it.
// level and words: as for candidatesOf.
export function readingCounts({ text, reading, plan, answer, words, level = 2 }) {
  const out = {};
  const taken = [];
  const add = (id, span) => { out[id] = (out[id] || 0) + 1; taken.push(span); };
  if (reading) for (const s of matchText(reading, text)) if (!s.function && Number.isInteger(s.options[0]?.id)) add(s.options[0].id, s);
  for (const m of plan?.spans || []) {
    const k = m.safe.length ? null : answer?.picks?.get(m.n);
    const entry = m.safe[0] || (Number.isInteger(k) ? m.doubtful[k] : null);
    if (!Number.isInteger(entry?.id) || taken.some((t) => m.span.start < t.end && t.start < m.span.end)) continue;
    const swap = { k: 'w', o: m.span.english, f: entry.ja, p: '', g: '', c: contextFor(text, m.span.start, m.span.end) };
    if (validateSwaps(level, [swap], words, text).length) add(entry.id, m.span);
  }
  return out;
}
