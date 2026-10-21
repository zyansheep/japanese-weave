// The judge of the web pipeline's swaps against the oracle (oracle.mjs), shared by check-lab.mjs and worker-lab.mjs:
// where each swap lands on the passage as the page would place it, and whether each of its words there is right.
import { Window } from 'happy-dom';
import { flatten, locate, toNode } from '../../extension/src/text.js';
import { overlaps } from './oracle.mjs';

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
