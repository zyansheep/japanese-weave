// Which listed words a passage may say, found in code from their English glosses ("has" for aru "have / exist",
// "became" for naru "become"), so each passage tells the model which words to look at (prompt.js). Haiku at low effort
// missed most of them in the long word list, or took them for unlisted (docs/swap-prompt.md). A hint is only a lead:
// the model checks the sense ("enter the code" is not hairu "enter"), and validation in levels.js still decides what is
// shown. Pure.

const IRREGULAR = {
  be: ['is', 'are', 'was', 'were', 'been', 'being'], have: ['has', 'had', 'having'], do: ['does', 'did', 'done', 'doing'],
  become: ['became', 'becomes', 'becoming'], come: ['came', 'comes', 'coming'], go: ['goes', 'went', 'gone', 'going'],
  man: ['men'], woman: ['women'], child: ['children'], kid: ['kids'], person: ['people'], see: ['saw', 'seen'],
  say: ['said', 'says'], think: ['thought'], know: ['knew', 'known'], feel: ['felt'], buy: ['bought'], meet: ['met'],
  write: ['wrote', 'written', 'writing'], read: ['reading', 'reads'], speak: ['spoke', 'spoken'], take: ['took', 'taken'],
  stand: ['stood'], understand: ['understood'], hear: ['heard'], sleep: ['slept'], begin: ['began', 'begun'],
  give: ['gave', 'given'], make: ['made'], get: ['got', 'gotten'], die: ['died', 'dying'], rise: ['rose', 'risen'],
  good: [], one: [], all: [], now: [], i: [], he: [], him: [], she: [], her: [], we: [], you: [], it: [],
};
// English the gloss leaves out, for a few words where the usual English isn't in the gloss.
const EXTRA = {
  について: ['about'],
  ある: ['there is', 'there are', 'there was', 'there were', "there's"],
  違う: ['different', 'differently'],
  自分: ['myself', 'yourself', 'himself', 'herself', 'itself', 'ourselves', 'yourselves', 'themselves'],
  時間: ['hour'],
};
// English too vague to point at a known word: "and" between nouns is not soshite, "so" as "very" is not tsumari, a
// question word that joins clauses ("when I got there") is not itsu. A learning word is hinted on any of these, a known
// word not.
const VAGUE = new Set(['and', 'so', 'that', 'what', 'which', 'who', 'when', 'where', 'how', 'then', 'right', 'well', 'like',
  'much', 'no', 'there', 'here', 'do', 'does', 'did', 'make', 'have', 'has', 'had', 'time', 'times', 'about', 'side', 'ask',
  'asks', 'asked', 'asking']);
// Number words are left to the prompt's number rules: as hints they only invite "Day one" and "the last one". Katakana
// loanwords are not in the prompt's lists (they teach nothing on an English page).
const NUMERAL = /^[一二三四五六七八九十百千万]+つ?$/;
const KATAKANA_ONLY = /^[゠-ヿ〜]+$/;

// The regular forms of one English word: plural or third person, past, -ing, and the adverb or comparative of an
// adjective, with the irregular ones from the table.
export function englishForms(word) {
  const w = word.toLowerCase();
  if (IRREGULAR[w]) return [w, ...IRREGULAR[w]];
  const out = new Set([w]);
  const consonantY = /[^aeiou]y$/.test(w);
  const doubled = /^[^aeiou]*[aeiou][bdgmnprt]$/.test(w) ? w + w.slice(-1) : w; // stop -> stopped, plan -> planning
  out.add(/(s|x|z|ch|sh)$/.test(w) ? `${w}es` : consonantY ? `${w.slice(0, -1)}ies` : `${w}s`);
  out.add(w.endsWith('e') ? `${w}d` : consonantY ? `${w.slice(0, -1)}ied` : `${doubled}ed`);
  out.add(/[^e]e$/.test(w) ? `${w.slice(0, -1)}ing` : `${doubled}ing`);
  out.add(w.endsWith('e') ? `${w}r` : `${w}er`);
  out.add(w.endsWith('e') ? `${w}st` : `${w}est`);
  out.add(consonantY ? `${w.slice(0, -1)}ily` : w.endsWith('le') ? `${w.slice(0, -1)}y` : `${w}ly`);
  return [...out];
}

// The English a gloss stands for: each "/" alternative without "to", dots or "be" ("be wrong" is also "wrong"). A
// phrase is matched with its first word in any form ("take time off": "took time off").
function glossPhrases(en) {
  const out = [];
  for (let alt of String(en).toLowerCase().split('/')) {
    alt = alt.replace(/\.\.\.|…|\(.*?\)/g, '').replace(/^\s*to\s+/, '').trim();
    if (!alt || /counter/.test(alt)) continue;
    const [first, ...rest] = alt.split(/\s+/);
    for (const form of englishForms(first)) out.push([form, ...rest].join(' '));
    if (first === 'be' && rest.length === 1) out.push(...englishForms(rest[0]));
  }
  return out;
}

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const patterns = new Map(); // gloss -> RegExp of every phrase it stands for
// "have" as a helper verb ("have been", "had to", "has never played") is not "have / exist".
const HELPER = /^(have|has|had|having)$/i;
const AFTER_HELPER = /^\s+(to|been|got|gotten|never|already|just|not|ever|always|also|still|had|become|come|n't|[a-z]+(ed|en|wn|ne|ught))\b/i;

// The passage's listed words: [{ ja, en, found, forms, places, learning }], found being the English as it is written in
// the passage (its first occurrence), forms every spelling of it there (first occurrence of each, whatever the case),
// places how often it occurs, learning true for an active or shaky word. words: the plugin's word state (words without
// a status are skipped); never number words or katakana loanwords.
export function wordHints(text, words) {
  const out = [];
  for (const w of words || []) {
    if (!w?.ja || !w.status || NUMERAL.test(w.ja) || KATAKANA_ONLY.test(w.ja)) continue;
    const key = `${w.ja}\u0000${w.en}`;
    if (!patterns.has(key)) {
      const phrases = [...new Set([...glossPhrases(w.en), ...(EXTRA[w.ja] || [])])].sort((a, b) => b.length - a.length);
      patterns.set(key, phrases.length ? new RegExp(`(?<![\\p{L}'’-])(?:${phrases.map(escape).join('|')})(?![\\p{L}'’-])`, 'iu') : null);
    }
    const re = patterns.get(key);
    if (!re) continue;
    const all = new RegExp(re.source, 'giu');
    const learning = w.status !== 'known';
    const found = [...text.matchAll(all)].filter((m) => !(HELPER.test(m[0]) && AFTER_HELPER.test(text.slice(m.index + m[0].length))) &&
      (learning || !VAGUE.has(m[0].toLowerCase()))).map((m) => m[0]);
    const seen = new Set();
    const forms = found.filter((f) => !seen.has(f.toLowerCase()) && seen.add(f.toLowerCase()));
    if (found.length) out.push({ ja: w.ja, en: w.en, found: found[0], forms, places: found.length, learning });
  }
  return out;
}
