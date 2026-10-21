// The prompt lab's oracle (oracle.json, fresh-oracle.json, fresh2-oracle.json): each judged entry's places as spans of
// the passage's text. Shared by matcher-lab.mjs, which judges the code matcher's spans, and check-lab.mjs, which judges
// the web pipeline's swaps (lab.mjs judged the old swap prompt's the same way, until the pipeline replaced it).
//
// The oracle, per passage id: "use", the places a listed word fits in its listed sense, "en=ja" or "en=ja|ja" (any of
// them will do), "en#1=ja" for only the second place the English occurs; "ok", swaps that are fine but not owed; "not",
// listed words whose English is there in another sense (a swap there is wrong); "sentences", the sentences listed words
// and the grammar list can say completely; "bad", the English of phrase or sentence swaps judged wrong as a whole.
// Coverage is the share of "use" places a shown swap with one of its words covers. A shown word no entry speaks for is
// unjudged, to be added.
import { Window } from 'happy-dom';
import { flatten, locate, toNode } from '../../extension/src/text.js';

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Where en occurs in text: whole words, a possessive included ("grandfather" in "grandfather's"), not a contraction
// ("can" in "can't").
export const oracleSpans = (text, en) => [...text.matchAll(new RegExp(`(?<![\\p{L}\\p{N}'’])${escape(en)}(?![\\p{L}\\p{N}]|['’](?!s\\b))`, 'giu'))]
  .map((m) => ({ start: m.index, end: m.index + m[0].length }));

export const overlaps = (a, b) => a.start < b.end && b.start < a.end;

// A passage's judged entries, each place a span: { use, ok, not: [{ key, en, ja: [...], start, end }], sentences, bad },
// or null when the oracle (o, its entry for the passage) has none. An entry whose English isn't in the passage, or a
// text that differs from the passage's, is reported through warn.
export function judgedPassage(p, o, warn = console.warn) {
  if (!o) return null;
  if (o.text && o.text !== p.text) warn(`oracle: ${p.id} has another text`);
  const parse = (entries = []) => entries.flatMap((e) => {
    const at = e.indexOf('=');
    const [en, which] = e.slice(0, at).split('#');
    const ja = e.slice(at + 1).split('|');
    const all = oracleSpans(p.text, en);
    if (!all.length) warn(`oracle: ${p.id}: "${en}" is not in the passage`);
    const pick = which ? which.split(',').map(Number) : all.map((_, i) => i);
    return pick.filter((i) => all[i]).map((i) => ({ key: `${en}#${i}`, en, ja, ...all[i] }));
  });
  return { use: parse(o.use), ok: parse(o.ok), not: parse(o.not), sentences: (o.sentences || []).map((s) => s.trim()),
    bad: new Set(o.bad || []) };
}

// Where swaps land, as the page places them: text.js on the passage as a page element, the first that fits wins (all:
// every one that can be placed, overlapping or not). [{ swap, m: { start, end } }]
const doc = new Window().document;
const flats = new Map();
export function placeSwaps(p, swaps, all = false) {
  if (!flats.has(p.text)) {
    const el = doc.createElement('p');
    el.textContent = p.text;
    flats.set(p.text, flatten(el));
  }
  const flat = flats.get(p.text);
  const used = [];
  const out = [];
  for (const swap of swaps) {
    const m = locate(flat, swap.o, swap.ctx);
    if (!m || !toNode(flat, m, swap.o)) continue;
    if (!all && used.some((u) => m.start < u.end && u.start < m.end)) continue;
    used.push(m);
    out.push({ swap, m });
  }
  return out;
}

// The judge of placed swaps (placeSwaps) against a passage's judged entries (judgedPassage): each listed word of a swap
// where it lands is right (it covers a "use" place), fine ("ok"), wrong ("not"; or the whole swap is in "bad") or
// unjudged, and a phrase or sentence not judged bad covers every "use" place inside it. Word swaps are split by kind:
// function (every word of the matcher's span there closed-class: spans, the matcher's spans of the passage) or content;
// the words of phrases and sentences are "group". Returns { covered: Set of place keys, right, ok, wrong: [what],
// unjudged: [what], sentences: Set, by: { fn, content, group: { right, ok, wrong, unjudged } } }.
const wordsOf = (s) => s.words || [s.word || s.f];
export function judgeSwaps(j, shown, spans = []) {
  const out = { covered: new Set(), right: 0, ok: 0, wrong: [], unjudged: [], sentences: new Set(),
    by: { fn: { right: 0, ok: 0, wrong: 0, unjudged: 0 }, content: { right: 0, ok: 0, wrong: 0, unjudged: 0 }, group: { right: 0, ok: 0, wrong: 0, unjudged: 0 } } };
  for (const { swap, m } of shown) {
    const kind = swap.parts ? 'group' : spans.find((s) => s.start === m.start && s.end === m.end)?.function ? 'fn' : 'content';
    const bad = j.bad.has(swap.o);
    if (bad) { out.wrong.push(`${swap.o} -> ${swap.f} (as a whole)`); out.by.group.wrong++; }
    if (swap.kind === 'sentence' && !bad && j.sentences.includes(swap.o.trim())) out.sentences.add(swap.o.trim());
    if (swap.parts && !bad) for (const e of j.use) if (overlaps(e, m)) out.covered.add(e.key);
    for (const w of wordsOf(swap)) {
      const hits = (list) => list.filter((e) => e.ja.includes(w) && overlaps(e, m));
      const use = hits(j.use);
      for (const e of use) out.covered.add(e.key);
      const verdict = use.length ? 'right' : hits(j.ok).length ? 'ok' : hits(j.not).length ? 'wrong' : 'unjudged';
      out.by[kind][verdict]++;
      if (verdict === 'right') out.right++;
      else if (verdict === 'ok') out.ok++;
      else if (verdict === 'wrong') out.wrong.push(`${hits(j.not)[0].en} -> ${w}${swap.parts ? ` in ${swap.f}` : ''}`);
      else out.unjudged.push(`${w} for "${swap.o}"${swap.parts ? ` in ${swap.f}` : ''}`);
    }
  }
  return out;
}
