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
