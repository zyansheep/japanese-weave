// The starting value of a word with no history: any dictionary entry's (dictionary.js) prior k, the log-odds of not
// looking it up when read, and its sd, from the regression the refresh fitted (knowledge.json "prior", as the native
// host sends it). The same as scripts/knowledge.py prior_estimate and word_features (docs/adaptive-difficulty.md);
// tests/prior_cases.json holds cases both sides must agree on.
export const WORD_FEATURES = ['rank', 'kanji known', 'all kanji known', 'kana', 'katakana', 'mixed', 'verb',
  'adjective', 'adverb', 'other', 'length', 'rare', 'loan'];
const ORIGINS = ['new', 'learning', 'known'];
const KANJI = /^[㐀-䶿一-鿿豈-﫿]$/;
const KANJI_LIKE = '々〆ヶヵ';

// How a written form is written: 'kanji' (kanji alone, with 々 〆 ヶ ヵ), 'mixed' (kanji and kana), 'kana' (no kanji,
// some hiragana) or 'katakana' (no kanji, no hiragana: katakana, letters or digits).
export function script(ja) {
  const chars = [...ja];
  if (chars.some((c) => KANJI.test(c))) {
    return chars.every((c) => KANJI.test(c) || KANJI_LIKE.includes(c)) ? 'kanji' : 'mixed';
  }
  return chars.some((c) => (c >= 'ぁ' && c <= 'ゖ') || c === 'ゝ' || c === 'ゞ') ? 'kana' : 'katakana';
}

// A part-of-speech group from JMdict tags, the first sense's first: noun, verb, adjective, adverb or other.
export function posGroup(pos) {
  const tag = (pos && pos[0]) || '';
  if (tag.startsWith('n') || ['num', 'ctr', 'pn'].includes(tag)) return 'noun';
  if (tag.startsWith('v')) return 'verb';
  if (tag.startsWith('adj')) return 'adjective';
  if (tag.startsWith('adv')) return 'adverb';
  return 'other';
}

// An entry's word features, {name: value} without zeros: rank (log10 of rank over the reference word's), kanji known
// (the share of its kanji among `known`, a string of the kanji known at a glance) and all kanji known, its script and
// part of speech against kanji alone and a noun, length (log2 of its characters over the reference word's), rare (not
// marked common) and loan (a source language other than English).
export function wordFeatures(entry, known = '', centre = {}) {
  const ja = entry.ja || '';
  const x = {
    rank: entry.rank ? Math.log10(entry.rank / (centre.rank ?? 1000)) : 0,
    length: Math.log2(Math.max(1, [...ja].length) / (centre.length ?? 2)),
  };
  const kanji = entry.kanji || [];
  if (kanji.length && known) {
    const share = kanji.filter((c) => known.includes(c)).length / kanji.length;
    if (share) x['kanji known'] = share;
    if (share === 1) x['all kanji known'] = 1;
  }
  for (const name of [script(ja), posGroup(entry.pos)]) if (WORD_FEATURES.includes(name)) x[name] = 1;
  if (!entry.common) x.rare = 1;
  if ((entry.loan || '').split(',').some((lang) => !['', 'eng', 'kata'].includes(lang))) x.loan = 1;
  return Object.fromEntries(Object.entries(x).filter(([, value]) => value));
}

// { k, sd } of a word without an estimate: by its Anki interval (days) and origin (new, learning or known: a
// dictionary word with no history is new), else its origin, plus its entry's word features for an origin the table
// features. null without a usable table.
export function priorOf(table, entry, { origin = 'new', interval = null } = {}) {
  if (!table || typeof table !== 'object') return null;
  const from = ORIGINS.includes(origin) ? origin : 'new';
  const card = Number.isFinite(interval);
  let k = card ? table.card?.[from] + table.interval * Math.log1p(Math.max(0, interval)) : table.none?.[from];
  if (entry && (table.featured || []).includes(from)) {
    for (const [name, value] of Object.entries(wordFeatures(entry, table.kanji || '', table.centre || {}))) {
      k += value * (table.words?.[name] ?? 0);
    }
  }
  const sd = table.sd?.[card ? 'card' : 'none'];
  return Number.isFinite(k) && Number.isFinite(sd) ? { k, sd } : null;
}
