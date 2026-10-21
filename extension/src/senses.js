// Sense flags (data/senses.tsv, written by tools/sense-flags.mjs; docs/matcher.md, "Sense flags"): for a pair of an
// entry and one of its English glosses, safe ('s') or doubtful ('d'). Safe means that wherever that English appears in
// ordinary prose, this Japanese word renders it correctly almost always, so code may swap a span the matcher found
// (matcher.js matchText) without a model reading the sentence. A pair the file doesn't have, and every gloss of
// closed-class English (data/english.json "function"), is doubtful: a model checks it first (verify.js). The same
// lookups as scripts/senses.py; tests/senses_cases.json holds the vectors both run. Pure.
import { ENGLISH } from './english.js';

// One line per entry: its JMdict id (empty for a word that is not an entry), its written form and reading as the word
// state or the dictionary writes them, then its safe and its doubtful glosses (as matcher.js glossAlternatives writes
// them), separated by ;.
export const SENSES_FIELDS = ['id', 'ja', 'kana', 'safe', 'doubtful'];
const FUNCTION = new Set(ENGLISH.function);

export const toHiragana = (s) => String(s ?? '').replace(/[ァ-ヶ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));

// A gloss made only of closed-class words ("about", "this one", "there is") is never safe: the matcher marks such
// spans function, and their sense is the sentence's.
const bare = (w) => w.replace(/n't$/, '').replace(/'(s|re|ve|ll|d|m)$/, '');
export function functionGloss(gloss) {
  const words = String(gloss).toLowerCase().replace(/’/g, "'").match(/[\p{L}\p{N}]+(?:'[\p{L}\p{N}]+)*/gu) || [];
  return words.length > 0 && words.every((w) => FUNCTION.has(bare(w)));
}

// The file's text -> Map `${ja}\t${gloss}` -> [{ kana, flag, id }].
export function parseSenses(text) {
  const lines = String(text).split('\n');
  let start = 0;
  while (start < lines.length && lines[start].startsWith('#')) start++;
  if ((lines[start] || '') !== SENSES_FIELDS.join('\t')) throw new Error(`senses: fields ${lines[start]}, expected ${SENSES_FIELDS}`);
  const flags = new Map();
  for (const line of lines.slice(start + 1)) {
    if (!line) continue;
    const [id, ja, kana, safe = '', doubtful = ''] = line.split('\t');
    for (const [flag, list] of [['s', safe], ['d', doubtful]]) {
      for (const gloss of list ? list.split(';') : []) {
        const key = `${ja}\t${gloss}`;
        if (!flags.has(key)) flags.set(key, []);
        flags.get(key).push({ kana, flag, id: id ? Number(id) : null });
      }
    }
  }
  return flags;
}

// The flag of an entry ({ ja, kana? }) for one of its glosses as matcher.js matched it (glossAlternatives' form): 's',
// 'd', or null when the file doesn't have it. Homographs written alike are told apart by kana when the entry has it;
// otherwise all of them must be safe.
export function senseFlag(flags, entry, gloss) {
  if (functionGloss(gloss)) return 'd';
  const rows = flags.get(`${entry.ja}\t${gloss}`);
  if (!rows) return null;
  const kana = entry.kana ? toHiragana(entry.kana) : '';
  const own = kana ? rows.filter((r) => r.kana === kana) : [];
  return (own.length ? own : rows).every((r) => r.flag === 's') ? 's' : 'd';
}

// For each option of a span (matcher.js matchText), whether code may swap it alone: never in a span of closed-class
// English, else when its pair is flagged safe.
export function safeOptions(flags, span) {
  return span.options.map((o, i) => !span.function && senseFlag(flags, o, span.glosses[i]) === 's');
}

// The file's text from rows of pairs { id, ja, kana, gloss, flag, rank }, one line per entry: in the dictionary's rank
// order (words that aren't entries last), then by Japanese and reading; each list of glosses in alphabetical order.
export function formatSenses(rows, { version, model, thinking, samples = 1, confirm = false } = {}) {
  const entries = new Map();
  for (const r of rows) {
    const key = `${r.id ?? ''}\t${r.ja}\t${r.kana}`;
    if (!entries.has(key)) entries.set(key, { ...r, s: [], d: [] });
    entries.get(key)[r.flag === 's' ? 's' : 'd'].push(r.gloss);
  }
  // Infinity - Infinity is NaN, which falls through to the next key like 0
  const sorted = [...entries.values()].sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity) || a.ja.localeCompare(b.ja) ||
    a.kana.localeCompare(b.kana));
  const safe = rows.filter((r) => r.flag === 's').length;
  const how = samples > 1 ? `, safe in all of ${samples} samples${confirm ? ', the later ones asking only what the earlier found safe' : ''}` : '';
  return [
    `# Japanese Weave sense flags, format 1: built by tools/sense-flags.mjs (prompt ${version}, ${model}${thinking === false ? ', no thinking' : ''}${how}), described in docs/matcher.md.`,
    '# One line per entry (id: JMdict sequence number, empty for a word that is not one; ja and kana as the word state or the dictionary writes them): its safe and its doubtful English glosses, as matcher.js glossAlternatives writes them,',
    `# separated by ;. A gloss not listed, and any gloss of closed-class English, is doubtful. ${rows.length} pairs of ${entries.size} entries, ${safe} safe.`,
    SENSES_FIELDS.join('\t'),
    ...sorted.map((e) => [e.id ?? '', e.ja, e.kana, e.s.sort().join(';'), e.d.sort().join(';')].join('\t')),
    '',
  ].join('\n');
}

// From the extension package, once it ships the file: load(chrome.runtime.getURL('data/senses.tsv')).
export async function load(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`senses: ${response.status} ${url}`);
  return parseSenses(await response.text());
}
