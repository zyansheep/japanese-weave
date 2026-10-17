// Level schedule, word lists, and guards. Pure: no DOM, no chrome APIs.
import { STARTER } from './starter.js';

// L3/L4 prompts are not built yet; anything above this uses L2 rules.
export const MAX_BUILT_LEVEL = 2;

// stage: from the Claude Code plugin's word state (0 when there is none); chosen: after the popup override;
// effective: what the prompt uses.
export function resolveLevel(settings = {}, stage = 0) {
  const override = Number(settings.levelOverride) || 0;
  const synced = Number(stage) >= 1 && Number(stage) <= 4 ? Number(stage) : 0;
  const chosen = override >= 1 && override <= 4 ? override : synced || 1;
  return { stage: synced, chosen, effective: Math.min(chosen, MAX_BUILT_LEVEL) };
}

// Bundled starter lists (starter.js, generated from data/lexicon.csv by scripts/make_blocks.py), used when the
// Claude Code plugin's word state isn't available.

const W = ([ja, en, hint, kana]) => ({ ja, en, hint, kana });
export const L1_WORDS = STARTER.slice(0, 40).map(W);
export const L2_WORDS = STARTER.slice(0, 150).map(W);

export function wordsForLevel(level) {
  return level <= 1 ? L1_WORDS : L2_WORDS;
}

const toHiragana = (s) => s.replace(/[\u30a1-\u30f6]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));
// Width, spacing and script folded: "多分", " 多分 ", "ﾀﾌﾞﾝ", "タブン" and "たぶん" all fold to "たぶん" or "多分".
export function normalizeJa(s) {
  return toHiragana(String(s).normalize('NFKC')).replace(/\s+/g, '').toLowerCase();
}

export const exactJa = (s) => String(s).normalize('NFKC').replace(/\s+/g, '');

const indexCache = new Map();
function indexFor(level, sharedWords) {
  const lvl = level <= 1 ? 1 : 2;
  if (!sharedWords && indexCache.has(lvl)) return indexCache.get(lvl);
  const idx = new Map();
  const folded = new Map();
  for (const w of sharedWords || wordsForLevel(lvl)) {
    const entry = { canon: w.ja, en: w.en, hint: w.hint, kana: w.kana || '', word: w.ja, status: w.status };
    idx.set(exactJa(w.ja), entry);
    // The model sometimes writes the reading instead of the kanji; accept it and show the listed form.
    for (const form of new Set([normalizeJa(w.ja), w.kana ? normalizeJa(w.kana) : ''])) {
      if (form) folded.set(form, folded.has(form) && folded.get(form) !== entry ? null : entry);
    }
  }
  const result = { exact: idx, folded };
  if (!sharedWords) indexCache.set(lvl, result);
  return result;
}

// Japanese has no letter case; kept so callers stay the same shape as other weaves.
export function matchCase(ja) {
  return ja;
}

// Grammar a phrase or sentence swap may put around listed words. Pieces may run together (ですか？, では), but a word that
// happens to be spelled with grammar kana is not grammar.
export const PARTICLES = ['は', 'が', 'を', 'に', 'で', 'と', 'も', 'の', 'へ', 'から', 'まで', 'より', 'や', 'か', 'ね', 'よ', 'な'];
const GRAMMAR = new Set([...PARTICLES, 'です', 'でした', 'ではない', 'じゃない', 'ではありません', 'じゃありません', 'だ', 'だった',
  'ます', 'ました', 'ません', 'ませんでした', 'ましょう', 'ない', 'なかった', 'たい', 'て', 'た', 'ている', 'ています', 'ていた',
  'ていました', 'でしょう', 'こと', 'ん', 'んです', 'のです', '。', '、', '？', '！', '?', '!',
  // いる after a て form, when the answer splits it off (行って|いました)
  'いる', 'います', 'いた', 'いました', 'いない', 'いません']);
const NOT_GRAMMAR = new Set(['とても', 'もの', 'なか', 'かな', 'とか', 'なに', 'なん', 'はな', 'もも', 'かた']);
export function isGrammar(t) {
  if (!t || NOT_GRAMMAR.has(t)) return false;
  const ok = [true];
  for (let i = 1; i <= t.length; i++) {
    ok[i] = false;
    for (let j = Math.max(0, i - 6); j < i && !ok[i]; j++) ok[i] = ok[j] && GRAMMAR.has(t.slice(j, i));
  }
  return ok[t.length];
}

// The forms a listed word may take in a phrase or sentence: itself and, for a verb or an i-adjective, the endings the
// prompt allows (ます forms, ない, た, て, ている, たい) and a few common ones, so a piece is checked against the word's
// own conjugation rather than its first kanji (見せた is not a form of 見る). A verb ending in る after a kanji or an
// i/e sound may be either kind, so both are made; the extra ones are never Japanese.
const MASU = ['ます', 'ました', 'ません', 'ませんでした', 'ましょう', 'たい', 'たかった', 'たくない', 'ながら'];
const NAI = ['ない', 'なかった', 'なくて', 'ないで'];
const TE = ['', 'いる', 'います', 'いた', 'いました', 'いない', 'いません', 'る', 'ます', 'しまう', 'しまった', 'ください'];
// last kana -> [i, a, e, o] rows and the te stem (voiced: で/だ)
const GODAN = { う: ['い', 'わ', 'え', 'お', 'っ'], く: ['き', 'か', 'け', 'こ', 'い'], ぐ: ['ぎ', 'が', 'げ', 'ご', 'い', 1],
  す: ['し', 'さ', 'せ', 'そ', 'し'], つ: ['ち', 'た', 'て', 'と', 'っ'], ぬ: ['に', 'な', 'ね', 'の', 'ん', 1],
  ぶ: ['び', 'ば', 'べ', 'ぼ', 'ん', 1], む: ['み', 'ま', 'め', 'も', 'ん', 1], る: ['り', 'ら', 'れ', 'ろ', 'っ'] };
const teForms = (te, voiced) => [...TE.map((e) => te + (voiced ? 'で' : 'て') + e), te + (voiced ? 'だ' : 'た')];
function conjugate(w) {
  const out = new Set([w]);
  const add = (list) => { for (const f of list) out.add(f); };
  const stem = w.slice(0, -1);
  const last = w.slice(-1);
  if (w === 'する') { add([...MASU, ...NAI].map((e) => 'し' + e)); add(teForms('し')); add(['すれば', 'しよう', 'される', 'させる']); return out; }
  if (w === '来る' || w === 'くる') {
    const [i, a] = w === '来る' ? ['来', '来'] : ['き', 'こ'];
    add(MASU.map((e) => i + e)); add(NAI.map((e) => a + e)); add(teForms(i)); add([stem + 'れば', a + 'よう']); return out;
  }
  if (w.length >= 2 && last === 'い') { // i-adjective; いい conjugates as よい
    const s = w.endsWith('いい') ? w.slice(0, -2) + 'よ' : stem;
    add(['かった', 'くない', 'くなかった', 'くて', 'く', 'ければ', 'さ', 'そう'].map((e) => s + e));
    return out;
  }
  if (w.length < 2 || !GODAN[last]) return out;
  const [i, a, e, o, te, voiced] = GODAN[last];
  add(MASU.map((x) => stem + i + x));
  add((w === 'ある' ? NAI : NAI.map((x) => a + x)).map((x) => (w === 'ある' ? x : stem + x)));
  add(teForms(stem + (/^(行|い)く$/.test(w) ? 'っ' : te), voiced));
  add([stem + e + 'ば', stem + e + 'る', stem + o + 'う']);
  if (last === 'る' && !/[あかさたなはまやらわがざだばぱうくすつぬふむゆるぐずづぶぷおこそとのほもよろをごぞどぼぽ]$/.test(stem)) {
    add([...MASU, ...NAI, 'れば', 'よう', 'られる', 'させる', 'ろ'].map((x) => stem + x)); add(teForms(stem));
  }
  return out;
}
const formsCache = new Map();
function formsOf(w) {
  if (!formsCache.has(w)) formsCache.set(w, [...conjugate(w)].map(normalizeJa));
  return formsCache.get(w);
}

// t is the listed word as written (kanji or kana) or one of its forms, perhaps with grammar after it (行きましたね).
// Returns the length of the form matched, 0 if none. readings: false to leave out the kana of a kanji word.
function formOf(t, entry, readings = true) {
  const nt = normalizeJa(t);
  let best = 0;
  for (const w of readings ? [entry.canon, entry.kana] : [entry.canon]) {
    if (!w) continue;
    for (const f of formsOf(w)) {
      if (f.length > best && nt.startsWith(f) && (nt.length === f.length || isGrammar(nt.slice(f.length)))) best = f.length;
    }
  }
  return best;
}

// A piece the answer didn't name: the listed word it is, as written or as a form of one, else null. The longest form
// wins; words as listed come before kanji words written in kana (しました is する, not 知る read しる).
function pieceWord(t, idx) {
  const hit = idx.exact.get(exactJa(t)) || idx.folded.get(normalizeJa(t));
  if (hit) return hit;
  for (const readings of [false, true]) {
    let best = null;
    let length = 0;
    for (const entry of idx.exact.values()) {
      const n = formOf(t, entry, readings);
      if (n > length) { best = entry; length = n; }
    }
    if (best) return best;
  }
  return null;
}

const MAX_PARTS = 40;
const SENTENCE_END = /[。？！?!]$/;

// A phrase or sentence swap: every piece a listed word (or a form of one) or grammar, the pieces spelling f, at least one
// listed word. Each word piece gets its gloss, romaji and kana from the list. null if anything is off.
function validateGroup(kind, s, o, idx) {
  if (!Array.isArray(s.parts) || !s.parts.length || s.parts.length > MAX_PARTS) return null;
  const parts = [];
  for (const p of s.parts) {
    const t = typeof p?.t === 'string' ? p.t.trim() : '';
    if (!t) return null;
    // w: the listed word the answer says the piece is, '' for grammar, null when it didn't say (the compact answer)
    const named = typeof p.w === 'string' && p.w.trim();
    const hit = named ? idx.exact.get(exactJa(p.w)) || idx.folded.get(normalizeJa(p.w))
      : p.w === null && !isGrammar(t) ? pieceWord(t, idx) : null;
    if (named || hit) {
      if (!hit || !formOf(t, hit)) return null;
      parts.push({ t, word: hit.word, g: hit.en, h: hit.hint, ...(hit.kana && hit.kana !== hit.canon ? { k: hit.kana } : {}),
        ...(hit.status ? { status: hit.status } : {}) });
    } else if (isGrammar(t)) {
      parts.push({ t });
    } else return null;
  }
  const f = parts.map((p) => p.t).join('');
  if (exactJa(f) !== exactJa(s.f)) return null;
  const words = parts.filter((p) => p.word).map((p) => p.word);
  if (!words.length || (kind === 'sentence' && !SENTENCE_END.test(f))) return null;
  const g = (typeof s.g === 'string' && s.g.trim() ? s.g.trim() : o).slice(0, 300); // the English it replaces says it too
  // word: the one the passage's picking ranks it by, its first learning word if it has one
  const lead = parts.find((p) => p.word && p.status && p.status !== 'known') || parts.find((p) => p.word);
  return { kind, o, f, g, parts, words, word: lead.word, ...(lead.status ? { status: lead.status } : {}),
    ctx: typeof s.ctx === 'string' ? s.ctx.trim() : '' };
}

// The compact answer (prompt.js): k "w" | "p" | "s", c for ctx, and a phrase or sentence's pieces as one string
// "a|b|c" without naming their words, which validateGroup then looks up. The full shape passes through.
const KINDS = { w: 'word', p: 'phrase', s: 'sentence' };
function expand(s) {
  if ('kind' in s || !KINDS[s.k]) return s;
  const pieces = typeof s.p === 'string' && s.p.trim() ? s.p.split('|') : [];
  const kind = KINDS[s.k] === 'word' && pieces.length > 1 ? 'phrase' : KINDS[s.k]; // a phrase sent as a word
  return { kind, o: s.o, f: s.f, parts: pieces.length ? pieces.map((t) => ({ t, w: null })) : undefined, g: s.g, ctx: s.c };
}

// Fail closed: keep only swaps whose Japanese is on the list (the plugin's word state, else the level's bundled list).
// A word swap is one listed word: its written form canonicalized (kanji as listed), gloss, romaji and kana from the list.
// A phrase or sentence swap is listed words and grammar only (validateGroup). Swaps without a kind are word swaps.
// text: the passage, to drop number words that are labels or ranks there (numberTrap).
export function validateSwaps(level, swaps, sharedWords, text = '') {
  if (!Array.isArray(swaps)) return [];
  const idx = indexFor(level, sharedWords);
  const out = [];
  for (const raw of swaps) {
    if (!raw || typeof raw !== 'object') continue;
    const s = expand(raw);
    if (typeof s.o !== 'string' || typeof s.f !== 'string') continue;
    const o = s.o.trim();
    if (!o || !s.f.trim()) continue;
    if (s.kind === 'phrase' || s.kind === 'sentence') {
      const group = validateGroup(s.kind, s, o, idx);
      if (group && !numberTrap(group, text) && !dangles(group, text)) out.push(group);
      continue;
    }
    const hit = idx.exact.get(exactJa(s.f)) || idx.folded.get(normalizeJa(s.f));
    if (!hit) continue;
    const swap = {
      o,
      f: hit.canon,
      g: hit.en,
      h: hit.hint,
      ...(hit.kana && hit.kana !== hit.canon ? { k: hit.kana } : {}),
      ctx: typeof s.ctx === 'string' ? s.ctx.trim() : '',
      ...(sharedWords ? { word: hit.word, ...(hit.status ? { status: hit.status } : {}) } : {}),
    };
    if (!numberTrap(swap, text)) out.push(swap);
  }
  return out;
}

// A phrase in Japanese order that ends in a verb ends its clause: "I 店に行きました with my mom" leaves English hanging
// after the verb. Punctuation, the end, or a word that starts a new clause may follow it, wherever it occurs.
const CLAUSE_START = new Set(['and', 'but', 'so', 'or', 'because', 'then', 'when', 'while', 'if', 'until', 'since', 'though',
  'although', 'unless']);
export function dangles(swap, text) {
  if (swap.kind !== 'phrase' || !text) return false;
  const at = swap.parts.findLastIndex((p) => p.word);
  if (at < 0 || !/[うくぐすつぬぶむる]$/.test(swap.parts[at].word)) return false;
  if (swap.parts.slice(at + 1).some((p) => /こと|の/.test(p.t))) return false; // 読むこと: a noun
  for (let i = text.indexOf(swap.o); i !== -1; i = text.indexOf(swap.o, i + 1)) {
    const next = /^\s*([A-Za-z][A-Za-z']*)/.exec(text.slice(i + swap.o.length))?.[1];
    if (next && !CLAUSE_START.has(next.toLowerCase())) return true;
  }
  return false;
}

// Number words that name a label, a rank or "someone" read wrong as a count: "Day one" as 日 一つ, "number one",
// "one-on-one", "the first", "the one", "one day" (someday), 一つ for people or days. The prompt forbids these; this
// drops the ones it lets through. It looks at the swap's English where it sits in the passage (every place it occurs).
const NUMBER = new Set(['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
  'twenty', 'thirty', 'forty', 'fifty', 'hundred', 'thousand']);
const ORDINAL = /^(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(st|nd|rd|th))$/;
const LABELS = new Set(['day', 'week', 'month', 'year', 'chapter', 'episode', 'ep', 'season', 'level', 'part', 'page', 'step',
  'round', 'phase', 'act', 'volume', 'vol', 'issue', 'number', 'no', 'gate', 'track', 'lesson', 'unit', 'section', 'stage',
  'game', 'grade', 'version', 'gen', 'generation', 'world', 'wave', 'line', 'platform', 'figure', 'fig', 'table', 'question',
  'rule', 'player', 'team', 'plan', 'option', 'type', 'class', 'tier', 'rank', 'top', 'model', 'series', 'mark', 'case', 'test',
  'trial', 'problem', 'exercise', 'slide', 'scene', 'take', 'sprint', 'quarter', 'half', 'period', 'set', 'match', 'leg', 'stop',
  'room', 'floor', 'seat', 'row', 'group', 'zone', 'block', 'exit', 'route', 'flight', 'item', 'point', 'pick', 'draft', 'wonder']);
// "the one", "no one", "this one", "the only one", "a new one": one as a pronoun ("only one of them" is a count)
const DETERMINERS = new Set(['the', 'this', 'that', 'which', 'no', 'each', 'every', 'any', 'some', 'a', 'an', 'another',
  'other', 'my', 'your', 'his', 'her', 'our', 'their', 'its']);
const ADJECTIVES = new Set(['last', 'next', 'only', 'right', 'wrong', 'same', 'new', 'old', 'big', 'small', 'little', 'good',
  'bad', 'better', 'best', 'real', 'main', 'favorite', 'favourite', 'lucky', 'chosen', 'special', 'first', 'second']);
// "one another", "one day", "one time", "one by one", "one on one"
const AFTER_ONE = new Set(['another', 'day', 'time', 'night', 'morning', 'evening', 'afternoon', 'by', 'on', 'or', 'way', 'thing']);
// a counter (一つ) counts things, never these
const UNITS = new Set(['day', 'days', 'week', 'weeks', 'month', 'months', 'year', 'years', 'hour', 'hours', 'minute', 'minutes',
  'min', 'mins', 'second', 'seconds', 'night', 'nights', 'time', 'times', 'person', 'people', 'guy', 'guys', 'man', 'men',
  'woman', 'women', 'kid', 'kids', 'child', 'children', 'friend', 'friends', 'dollar', 'dollars', 'buck', 'bucks', 'cent',
  'cents', 'yen', 'euro', 'euros', 'pound', 'pounds', 'mile', 'miles', 'km', 'meter', 'meters', 'feet', 'foot', 'inch',
  'inches', 'percent', 'player', 'players', 'member', 'members', 'student', 'students']);
const NUMERAL_JA = /^[一二三四五六七八九十百千万]+(つ)?$/;
const TOKEN = /[a-z0-9]+(?:'[a-z]+)?/g;

export function numberTrap(swap, text) {
  if (!text) return false;
  const numerals = swap.parts ? swap.parts.filter((p) => p.word && NUMERAL_JA.test(p.word)).map((p) => p.word)
    : NUMERAL_JA.test(swap.f) ? [swap.f] : [];
  const label = !swap.parts && LABELS.has(swap.o.toLowerCase());
  if (!numerals.length && !label) return false;
  const counter = numerals.some((n) => n.endsWith('つ'));
  const lower = text.toLowerCase();
  const tokens = [...lower.matchAll(TOKEN)].map((m) => ({ w: m[0], start: m.index, end: m.index + m[0].length }));
  const o = swap.o.toLowerCase();
  for (let at = lower.indexOf(o); at !== -1; at = lower.indexOf(o, at + 1)) {
    const end = at + o.length;
    if (/[a-z0-9]/.test(lower[at - 1] || '') || /[a-z0-9]/.test(lower[end] || '')) continue; // inside a longer word
    const inside = tokens.map((t, i) => [t, i]).filter(([t]) => t.start >= at && t.end <= end);
    if (label) {
      const next = tokens.find((t) => t.start >= end)?.w || '';
      if (NUMBER.has(next) || ORDINAL.test(next) || /^\d+$/.test(next)) return true;
      continue;
    }
    for (const [t, i] of inside) {
      if (!NUMBER.has(t.w) && !ORDINAL.test(t.w)) continue;
      const prev = tokens[i - 1]?.w || '';
      const next = tokens[i + 1]?.w || '';
      if (ORDINAL.test(t.w) || LABELS.has(prev)) return true;
      const pronoun = DETERMINERS.has(prev) || (ADJECTIVES.has(prev) && DETERMINERS.has(tokens[i - 2]?.w || ''));
      if (t.w === 'one' && (pronoun || AFTER_ONE.has(next))) return true;
      if (counter && UNITS.has(next)) return true;
    }
  }
  return false;
}

export function sentenceCount(text) {
  return Math.max(1, String(text).split(/[.!?…]+(?:\s|$)|\n+/).filter((s) => wordCount(s) >= 3).length);
}

// Swaps a passage should get at a density (percent of its words); also the ceiling then.
export function swapTarget(text, density) {
  return Math.round((wordCount(text) * density) / 100);
}

// Swaps a passage may have: the level's per-sentence rule, or the density target.
const swapLimit = (level, text, density) => (density > 0 ? swapTarget(text, density) : (level <= 1 ? 1 : 2) * sentenceCount(text));
// One use per Japanese word: a phrase or sentence uses every listed word in it.
const oncePerWord = (swaps) => {
  const used = new Set();
  return swaps.filter((s) => {
    const keys = (s.words || [s.word || s.f]).map(exactJa);
    if (keys.some((k) => used.has(k))) return false;
    for (const k of keys) used.add(k);
    return true;
  });
};

// Density ceiling per passage, one use per Japanese word, learning words (active/shaky) kept before known ones.
export function capSwaps(level, swaps, text, sharedWords, density = 0) {
  const max = swapLimit(level, text, density);
  const status = new Map((sharedWords || []).map((w) => [w.ja, w.status]));
  const rank = (s) => (status.get(s.word) && status.get(s.word) !== 'known' ? 0 : 1);
  const unique = oncePerWord(swaps);
  const keep = new Set([...unique].sort((a, b) => rank(a) - rank(b)).slice(0, max));
  return unique.filter((s) => keep.has(s));
}

// Adaptive difficulty (docs/adaptive-difficulty.md): the predicted peek chance learning words are aimed at, and the
// knowledge (log-odds) of a word the gloss log has nothing on yet. P(peek) = 1 / (1 + e^k).
export const TARGET_P = 0.25;
const TARGET_K = Math.log((1 - TARGET_P) / TARGET_P);
const PRIOR_K = 1.0;
const PRIOR_SD = Math.SQRT2;
const peekChance = (k) => 1 / (1 + Math.exp(k));

// Seeded uniform [0, 1) (mulberry32) and standard normal (Box-Muller).
export function seededRandom(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const normal = (random) => Math.sqrt(-2 * Math.log(1 - random())) * Math.cos(2 * Math.PI * random());

// A passage's swaps, picked from every validated candidate when results are read from the cache, so new estimates or a
// new dial never cost a model call. budgetPer100: expected peeks per 100 words (0: the dial is off, capSwaps). Learning
// words go by learning value, a predicted peek chance near TARGET_P, judged on k drawn from N(k, sd) so uncertain words
// still get shown. One active word not shown today goes first, outside the budget, so new words gather evidence. Then
// learning words while their peek chances fit the passage's budget; the first that doesn't fit gets in with probability
// remaining / p, so short passages still get learning words on average. Known words fill the rest. Seeded by the
// passage and day, so a passage repaints the same way.
export function selectSwaps(level, swaps, text, sharedWords, { density = 0, budgetPer100 = 0, seed = 0, shownToday = [] } = {}) {
  if (!(budgetPer100 > 0)) return capSwaps(level, swaps, text, sharedWords, density);
  const max = swapLimit(level, text, density);
  const words = new Map((sharedWords || []).map((w) => [w.ja, w]));
  const learning = (s) => words.get(s.word)?.status && words.get(s.word).status !== 'known';
  const shown = new Set(shownToday);
  const random = seededRandom(seed);
  const unique = oncePerWord(swaps);
  const ranked = unique.filter(learning).map((s) => {
    const w = words.get(s.word);
    const k = Number.isFinite(w.k) ? w.k : PRIOR_K;
    const sd = Number.isFinite(w.sd) ? w.sd : PRIOR_SD;
    return { s, p: peekChance(k), value: -Math.abs(k + sd * normal(random) - TARGET_K),
      fresh: w.status === 'active' && !shown.has(w.ja.normalize('NFKC').toLowerCase()) };
  }).sort((a, b) => b.value - a.value);
  const keep = new Set();
  const fresh = ranked.find((c) => c.fresh);
  if (fresh && max > 0) keep.add(fresh.s);
  let remaining = (budgetPer100 * wordCount(text)) / 100;
  for (const c of ranked) {
    if (keep.size >= max) break;
    if (keep.has(c.s)) continue;
    if (c.p <= remaining) { keep.add(c.s); remaining -= c.p; continue; }
    if (random() < remaining / c.p) keep.add(c.s);
    break;
  }
  for (const s of unique) {
    if (keep.size >= max) break;
    if (!learning(s)) keep.add(s);
  }
  return unique.filter((s) => keep.has(s));
}

export function wordCount(text) {
  const t = String(text).trim();
  return t ? t.split(/\s+/).length : 0;
}

// Small, conservative client-side guard for the zero-Japanese topics. The model rule does the real work;
// this just guarantees the obvious cases never leave the page.
const ZERO_JAPANESE = [
  /\b(doctor|hospital|diagnos\w*|symptoms?|medication|meds|ibuprofen|tylenol|prescription|overdose|vaccine|cancer|surgery|pregnan\w*|therapist|antidepressants?|ssri|adderall|stimulants?|chemo)\b/i,
  /\b(lawsuit|attorney|lawyer|sued|indict\w*|court order|legal advice|subpoena)\b/i,
  /\b(invest\w*|stock market|crypto|bitcoin|mortgage|loan|taxes|salary|bank account|financial advice)\b/i,
  /\b(911|emergency|evacuat\w*|shooting|wildfire|earthquake|hurricane|tornado|active shooter)\b/i,
  /\b(driving|drunk driv\w*|car crash|dui|speed limit|seat ?belt)\b/i,
];

export function isZeroJapanese(text) {
  return ZERO_JAPANESE.some((re) => re.test(text));
}
