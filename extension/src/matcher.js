// Code finds the places a listed word could go (PLAN.md section 6, "Candidates on the web"; docs/matcher.md). An index
// from English (each gloss, its inflections by rule and by the irregular tables, and phrases that stand for it, such as
// "there is" for "exist") to entries, and a matcher that marks every span of a passage an entry could take, with all
// its options: "you" gets both あなた and 君, and a model settles the doubtful ones later. Whole words only, as
// tools/prompt-lab/lab.mjs places the oracle's words: a possessive counts ("grandfather" in "grandfather's"), a
// contraction doesn't ("can" in "can't"). Case-insensitive. The longest match wins and spans never overlap.
//
// The same algorithm as scripts/matcher.py; both run the shared test vectors in tests/matcher_cases.json. The English
// morphology is data/english.json, generated into english.js. Pure.
import { ENGLISH } from './english.js';

const FUNCTION = new Set(ENGLISH.function);
const VERBS = ENGLISH.verbs;
const PLURALS = ENGLISH.plurals;
const COMPARATIVES = ENGLISH.comparatives;
const DETERMINERS = new Set(ENGLISH.determiners);
const AUX = ENGLISH.auxiliaries;
const BETWEEN = new Set(AUX.between);
const PARTICIPLES = new Set(Object.values(VERBS).flat());
// phrase -> the glosses it stands for, read the other way: gloss -> phrases
const STANDS_FOR = new Map();
for (const [phrase, glosses] of Object.entries(ENGLISH.phrases)) {
  for (const g of glosses) STANDS_FOR.set(g, [...(STANDS_FOR.get(g) || []), phrase]);
}

// The rules that drop a match, each on unless switched off (buildIndex's `without`, for measuring them):
//   kinds    inflect a gloss as the entry's part of speech allows (a noun only gets plurals, an adjective -er and -est);
//            off: every gloss gets every form
//   lexical  where a span is a gloss as written, options that only reach it by a rule's inflection go ("times" is 回,
//            not a form of "time"; "later" is 後, not a form of "late"); irregular forms stay ("people" is 人 and 人間)
//   helpers  "have" and "do" as helper verbs ("had been", "did not") are no match for a gloss "have" or "do"
//   nouns    after a determiner a verb is a noun: "the rest", "a look at" drop entries that are only verbs
//   hyphens  no match inside a hyphenated word ("one-on-one", "well-known")
export const RULES = ['kinds', 'lexical', 'helpers', 'nouns', 'hyphens'];

const TOKEN = /[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu;
const LAST_WORD_CHAR = /[\p{L}\p{N}]$/u;
const FIRST_WORD_CHAR = /^[\p{L}\p{N}]/u;
const APOSTROPHE = /['’]/;

// The words of a text: { start, end, word (lower case, ’ as '), stem (a possessive's word without 's, and where it ends),
// open (no apostrophe right before), closed (none right after), hyphenBefore, hyphenAfter }, and between each word and
// the next its separator: ' ' for white space, '-' for a hyphen, null for anything else (a phrase never crosses it).
function tokenize(text) {
  const tokens = [];
  for (const m of text.matchAll(TOKEN)) {
    const start = m.index;
    const end = start + m[0].length;
    const word = m[0].toLowerCase().replace(/’/g, "'");
    const before = text[start - 1] || '';
    const after = text[end] || '';
    tokens.push({
      start, end, word,
      stem: /'s$/.test(word) ? { word: word.slice(0, -2), end: end - 2 } : null,
      open: !APOSTROPHE.test(before),
      closed: !APOSTROPHE.test(after),
      hyphenBefore: before === '-' && LAST_WORD_CHAR.test(text.slice(Math.max(0, start - 3), start - 1)),
      hyphenAfter: after === '-' && FIRST_WORD_CHAR.test(text.slice(end + 1, end + 3)),
    });
  }
  const seps = [];
  for (let k = 0; k + 1 < tokens.length; k++) {
    const gap = text.slice(tokens[k].end, tokens[k + 1].start);
    seps.push(/^\s+$/.test(gap) ? ' ' : gap === '-' ? '-' : null);
  }
  return { tokens, seps };
}

// An entry's parts of speech, as a string of letters: n noun, v verb, a adjective ('' for the rest: adverbs, particles,
// interjections). From entry.pos when the entry has it (JMdict codes: v1, v5k, vs, adj-i, adj-na, n...; a list or a
// string), else guessed from the Japanese: ending in い an adjective, in a u-row kana a verb (not もう, こう, どう, nor
// a counted number like 二つ), anything else a noun.
const VERB_END = /[うくぐすつぬぶむる]$/;
const NOT_VERB = /^[ぁ-ゟ]*[おこそとのほもよろごぞどぼぽょ]う$|^[一二三四五六七八九十百千万幾]+つ$|^いくつ$/;
export function entryKinds(entry) {
  if (entry.pos) {
    const codes = Array.isArray(entry.pos) ? entry.pos : String(entry.pos).split(/[,;\s]+/);
    let k = '';
    for (const c of codes) {
      if (c === 'vs') k += 'nv';
      else if (c.startsWith('v')) k += 'v';
      else if (c.startsWith('adj')) k += 'a';
      else if (c.startsWith('n') || c === 'pn') k += 'n';
    }
    return [...new Set(k)].sort().join('');
  }
  const ja = String(entry.ja || '').replace(/^[〜~]/, '');
  if (ja.endsWith('い')) return 'a';
  if (VERB_END.test(ja)) return NOT_VERB.test(ja) ? (ja.endsWith('つ') ? 'n' : '') : 'v';
  return 'n';
}

// The English alternatives of a gloss: "a / b" split at the slashes (a list is taken as it is), lower case, without
// parentheses, dots, a leading "to" or a counter's description ("counter for years").
export function glossAlternatives(en) {
  const list = Array.isArray(en) ? en : String(en ?? '').split('/');
  const out = [];
  for (const raw of list) {
    const g = String(raw).toLowerCase().replace(/’/g, "'").replace(/\([^)]*\)/g, ' ').replace(/\.\.\.|…/g, ' ')
      .replace(/["“”!?;:,]/g, ' ').replace(/\s+/g, ' ').trim().replace(/^to /, '');
    if (g && !/\bcounter\b/.test(g) && !out.includes(g)) out.push(g);
  }
  return out;
}

const inflectable = (w) => !FUNCTION.has(w) || Object.hasOwn(VERBS, w);
const syllables = (w) => (w.match(/[aeiouy]+/g) || []).length;
// The stems a suffix goes on: "stop" -> "stopp"; "visit" both ways, "visited" and "visitted" (only one is English, and
// the other never turns up in a text), as "travelled" and "traveled" both are.
function stems(w) {
  if (!/[^aeiou][aeiou][bdfgklmnprstvz]$/.test(w)) return [w];
  const doubled = w + w.at(-1);
  return syllables(w) === 1 ? [doubled] : [w, doubled];
}
function sForms(w) {
  if (/(s|x|z|ch|sh)$/.test(w)) return [`${w}es`];
  if (/[^aeiou]y$/.test(w)) return [`${w.slice(0, -1)}ies`];
  if (/[^aeiou]o$/.test(w)) return [`${w}s`, `${w}es`];
  return [`${w}s`];
}
function edForms(w) {
  if (w.endsWith('e')) return [`${w}d`];
  if (/[^aeiou]y$/.test(w)) return [`${w.slice(0, -1)}ied`];
  return stems(w).map((s) => `${s}ed`);
}
function ingForms(w) {
  if (w.endsWith('ie')) return [`${w.slice(0, -2)}ying`];
  if (/[^aeiouy]e$/.test(w) && w.length > 2) return [`${w.slice(0, -1)}ing`];
  return stems(w).map((s) => `${s}ing`);
}
function nounForms(w) {
  return w.endsWith('man') && w.length > 3 ? [...sForms(w), `${w.slice(0, -3)}men`] : sForms(w);
}
function adjForms(w) {
  if (w.endsWith('e')) return [`${w}r`, `${w}st`];
  if (/[^aeiou]y$/.test(w)) return [`${w.slice(0, -1)}ier`, `${w.slice(0, -1)}iest`];
  return stems(w).flatMap((s) => [`${s}er`, `${s}est`]);
}
// One word's forms for the parts of speech in kinds (all of them when kinds is null), without the word itself, each
// with where it comes from: 1 an irregular table (data/english.json), 2 a rule. A word in a table gets that table's
// forms instead of the rule's (a verb still gets -s and -ing by rule).
function formsOf(w, kinds) {
  const out = new Map();
  if (!inflectable(w)) return out;
  const has = (k) => kinds === null || kinds.includes(k);
  const put = (forms, how) => { for (const f of forms) if (f !== w && !(out.get(f) <= how)) out.set(f, how); };
  const table = (t, rule) => (Object.hasOwn(t, w) ? put(t[w], 1) : put(rule(w), 2));
  if (has('v')) {
    table(VERBS, edForms);
    put([...sForms(w), ...ingForms(w)], 2);
  }
  if (has('n')) table(PLURALS, nounForms);
  if (has('a')) table(COMPARATIVES, adjForms);
  return out;
}
export const wordForms = (w, kinds = null) => [...formsOf(w, kinds).keys()];

// A gloss's spellings to match: [{ words, seps, inflected }], inflected 0 as written, 1 by an irregular table, 2 by a
// rule. One word gets its forms; a phrase its first word as a verb ("took time off") and its last as a noun ("younger
// sisters"); "be wrong" is also "wrong" (and "is wrong", "were wrong" as forms of "be").
function glossForms(words, seps, kinds) {
  const out = [{ words, seps, inflected: 0 }];
  const has = (k) => kinds === null || kinds.includes(k);
  if (words.length === 1) {
    for (const [f, how] of formsOf(words[0], kinds)) out.push({ words: [f], seps, inflected: how });
    return out;
  }
  const [first, ...rest] = words;
  if (has('v')) for (const [f, how] of formsOf(first, 'v')) out.push({ words: [f, ...rest], seps, inflected: how });
  if (has('n')) for (const [f, how] of formsOf(words.at(-1), 'n')) out.push({ words: [...words.slice(0, -1), f], seps, inflected: how });
  if (first === 'be') out.push({ words: rest, seps: seps.slice(1), inflected: 0 });
  return out;
}

// The index: a trie of words, each step keyed by the separator before the word (' ' or '-', none for the first) and the
// word; a node's hits are the entries whose English ends there, each as { entry (its place in entries), gloss (the
// alternative it came from), inflected (as in glossForms; the lowest if several reach it) }. without: rules to switch
// off (RULES), to measure them.
export function buildIndex(entries, { without = [] } = {}) {
  const rules = new Set(RULES.filter((r) => !without.includes(r)));
  const root = { next: new Map(), hits: [] };
  const kindsOf = [];
  entries.forEach((entry, idx) => {
    const kinds = entryKinds(entry);
    kindsOf.push(kinds);
    for (const gloss of glossAlternatives(entry.en)) {
      for (const spelling of [gloss, ...(STANDS_FOR.get(gloss) || [])]) {
        const { tokens, seps } = tokenize(spelling);
        if (!tokens.length || seps.includes(null)) continue;
        for (const form of glossForms(tokens.map((t) => t.word), seps, rules.has('kinds') ? kinds : null)) {
          let node = root;
          form.words.forEach((w, k) => {
            const key = (k ? form.seps[k - 1] : '') + w;
            if (!node.next.has(key)) node.next.set(key, { next: new Map(), hits: [] });
            node = node.next.get(key);
          });
          const same = node.hits.find((h) => h.entry === idx);
          if (!same) node.hits.push({ entry: idx, gloss, inflected: form.inflected });
          else if (form.inflected < same.inflected) Object.assign(same, { gloss, inflected: form.inflected });
        }
      }
    }
  });
  return { entries, kinds: kindsOf, root, rules };
}

// "have" or "do" at token j is a helper verb: a word that makes it one comes next, perhaps after "not", "never",
// "already"...; for "have", also a participle ("had stopped", "has become").
const participle = (w) => PARTICIPLES.has(w) || (w.length >= 4 && w.endsWith('ed') && !w.endsWith('eed'));
function helper(aux, tokens, seps, j) {
  for (let k = j + 1; k < tokens.length && seps[k - 1] === ' '; k++) {
    const w = tokens[k].word;
    if (aux.next.includes(w) || (aux.participle && participle(w))) return true;
    if (!BETWEEN.has(w)) return false;
  }
  return false;
}
// A word without a contraction's tail, to tell a function word: "there's" is "there", "isn't" is "is".
const bare = (w) => w.replace(/n't$/, '').replace(/'(s|re|ve|ll|d|m)$/, '');

// Every span of text an entry of the index could take: [{ start, end, english, options, glosses, function }], in order.
// options: the entries (as given to buildIndex) in their order there; glosses: for each option, the English alternative
// of its gloss it matched (the word as listed: "think" for "thought", "exist" for "there is"); function: every word of
// the span is a closed-class word (data/english.json "function"; "there's" as "there"). start and end index the text as
// JavaScript strings do (UTF-16 code units).
export function matchText(index, input) {
  const text = String(input ?? '');
  const { tokens, seps } = tokenize(text);
  const { root, rules, entries, kinds } = index;
  const candidates = [];
  const add = (i, j, end, last, hits) => {
    let keep = hits;
    if (i === j && rules.has('helpers')) {
      for (const [name, aux] of Object.entries(AUX)) {
        if (name !== 'between' && aux.forms.includes(last) && helper(aux, tokens, seps, j)) keep = keep.filter((h) => h.gloss !== name);
      }
    }
    const first = i === j ? last : tokens[i].word;
    if (rules.has('nouns') && i > 0 && seps[i - 1] === ' ' && DETERMINERS.has(tokens[i - 1].word) && !first.endsWith('ing')) {
      keep = keep.filter((h) => kinds[h.entry] !== 'v' || h.gloss.startsWith('be '));
    }
    if (rules.has('lexical') && keep.some((h) => h.inflected === 0)) keep = keep.filter((h) => h.inflected !== 2);
    if (keep.length) candidates.push({ start: tokens[i].start, end, i, j, last, hits: keep });
  };
  for (let i = 0; i < tokens.length; i++) {
    if (!tokens[i].open || (rules.has('hyphens') && tokens[i].hyphenBefore)) continue;
    let node = root;
    for (let j = i; j < tokens.length; j++) {
      const t = tokens[j];
      const sep = j === i ? '' : seps[j - 1];
      if (sep === null) break;
      if (t.stem) {
        const possessive = node.next.get(sep + t.stem.word);
        if (possessive?.hits.length) add(i, j, t.stem.end, t.stem.word, possessive.hits);
      }
      const child = node.next.get(sep + t.word);
      if (!child) break;
      if (child.hits.length && t.closed && !(rules.has('hyphens') && t.hyphenAfter)) add(i, j, t.end, t.word, child.hits);
      node = child;
    }
  }
  candidates.sort((a, b) => (b.end - b.start) - (a.end - a.start) || a.start - b.start);
  const taken = [];
  for (const c of candidates) if (!taken.some((t) => c.start < t.end && t.start < c.end)) taken.push(c);
  taken.sort((a, b) => a.start - b.start);
  return taken.map((c) => {
    const hits = [...c.hits].sort((a, b) => a.entry - b.entry);
    const words = [...tokens.slice(c.i, c.j).map((t) => t.word), c.last];
    return { start: c.start, end: c.end, english: text.slice(c.start, c.end), options: hits.map((h) => entries[h.entry]),
      glosses: hits.map((h) => h.gloss), function: words.every((w) => FUNCTION.has(bare(w))) };
  });
}
