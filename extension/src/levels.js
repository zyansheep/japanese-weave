// Level schedule, word lists, and guards. Pure: no DOM, no chrome APIs.
import { STARTER } from './starter.js';
import { locate } from './text.js';
import { ENGLISH } from './english.js';

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
  'ていました', 'でしょう', 'こと', 'ん', 'んです', 'んだ', 'のです', '。', '、', '？', '！', '?', '!',
  // いる after a て form, when the answer splits it off (行って|いました)
  'いる', 'います', 'いた', 'いました', 'いない', 'いません']);
const NOT_GRAMMAR = new Set(['とても', 'もの', 'なか', 'かな', 'とか', 'なに', 'なん', 'はな', 'もも', 'かた']);
// Particles that never follow each other, because together they spell a word (な+に is なに "what", も+の is もの "thing").
const NOT_AFTER = new Set(['な|に', 'も|の', 'も|のです', 'も|のに', 'か|な', 'と|か', 'も|も', 'は|な', 'か|た', 'な|か', 'な|ん', 'と|て']);
export function isGrammar(t) {
  if (!t || NOT_GRAMMAR.has(t)) return false;
  const reach = [new Set([''])]; // position -> the last piece of each way to read t up to there
  for (let i = 1; i <= t.length; i++) {
    reach[i] = new Set();
    for (let j = Math.max(0, i - 6); j < i; j++) {
      const piece = t.slice(j, i);
      if (GRAMMAR.has(piece) && [...reach[j]].some((last) => !NOT_AFTER.has(`${last}|${piece}`))) reach[i].add(piece);
    }
  }
  return reach[t.length].size > 0;
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
// So the past of suru is suru, not the kanji for "below" read the same way (shita), which glossed every "studied"
// (benkyou shita) "below".
function pieceWord(t, idx) {
  const exact = idx.exact.get(exactJa(t));
  if (exact) return exact;
  const folded = idx.folded.get(normalizeJa(t));
  if (folded && normalizeJa(folded.canon) === normalizeJa(t)) return folded; // as listed, in another script
  for (const readings of [false, true]) {
    if (readings && folded) return folded; // a kanji word written in kana
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
// A sentence's closing mark, which stands for the sentence in the log: its end span's word (text.js), opened for the
// translation.
export const isSentenceMark = (t) => /^[。？！?!]$/.test(t);

// A phrase or sentence swap: every piece a listed word (or a form of one) or grammar, the pieces spelling f, at least one
// listed word. Each word piece gets its gloss, romaji and kana from the list. null if anything is off.
const wordPart = (t, hit) => ({ t, word: hit.word, g: hit.en, h: hit.hint, ...(hit.kana && hit.kana !== hit.canon ? { k: hit.kana } : {}),
  ...(hit.status ? { status: hit.status } : {}) });

// The compact answer's pieces, regrouped where the model split a word or an ending ("行き|まし|た" is 行きました): of the
// groupings in which every group is a listed word (or a form of one, perhaps with grammar after it) or grammar, the one
// with the most groups, so the model's own split is kept wherever it holds. null if there is none.
const MAX_GROUP = 4;
function regroup(pieces, idx) {
  const best = [[]];
  for (let j = 1; j <= pieces.length; j++) {
    for (let i = Math.max(0, j - MAX_GROUP); i < j; i++) {
      if (!best[i] || (best[j] && best[j].length >= best[i].length + 1)) continue;
      const t = pieces.slice(i, j).join('');
      const hit = isGrammar(t) ? null : pieceWord(t, idx);
      if (hit || isGrammar(t)) best[j] = [...best[i], hit ? wordPart(t, hit) : { t }];
    }
  }
  return best[pieces.length] || null;
}

function validateGroup(kind, s, o, idx) {
  if (!Array.isArray(s.parts) || !s.parts.length || s.parts.length > MAX_PARTS) return null;
  const pieces = s.parts.map((p) => (typeof p?.t === 'string' ? p.t.trim() : ''));
  if (pieces.some((t) => !t)) return null;
  let parts = [];
  if (s.parts.every((p) => p.w === null)) { // the compact answer: no piece says which word it is
    parts = regroup(pieces, idx);
    if (!parts) return null;
  } else {
    for (const [i, p] of s.parts.entries()) {
      const t = pieces[i];
      // w: the listed word the answer says the piece is, '' for grammar
      const named = typeof p.w === 'string' && p.w.trim();
      const hit = named ? idx.exact.get(exactJa(p.w)) || idx.folded.get(normalizeJa(p.w)) : null;
      if (named) {
        if (!hit || !formOf(t, hit)) return null;
        parts.push(wordPart(t, hit));
      } else if (isGrammar(t)) {
        parts.push({ t });
      } else return null;
    }
  }
  // A phrase sits inside an English sentence, which keeps its own full stop.
  if (kind === 'phrase') while (parts.length > 1 && !parts.at(-1).word && /^[。？！?!]+$/.test(parts.at(-1).t)) parts.pop();
  const f = parts.map((p) => p.t).join('');
  // The pieces are what is shown; f only has to say the same. Haiku sometimes wrote f with the bars, or with a comma or
  // full stop the pieces leave out.
  const bare = (t) => exactJa(t).replace(/[|、。，,？！?!]/g, '');
  if (bare(f) !== bare(s.f)) return null;
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
  const pieces = typeof s.p === 'string' && s.p.trim() ? s.p.split('|').filter((t) => t.trim()) : []; // also a bar at the end
  const kind = KINDS[s.k] === 'word' && pieces.length > 1 ? 'phrase' : KINDS[s.k]; // a phrase sent as a word
  return { kind, o: s.o, f: s.f, parts: pieces.length ? pieces.map((t) => ({ t, w: null })) : undefined, g: s.g, ctx: s.c };
}

// A word swap of a listed word: its written form canonicalized (kanji as listed), gloss, romaji and kana from the list.
const wordSwap = (hit, o, ctx, sharedWords) => ({
  o,
  f: hit.canon,
  g: hit.en,
  h: hit.hint,
  ...(hit.kana && hit.kana !== hit.canon ? { k: hit.kana } : {}),
  ctx: typeof ctx === 'string' ? ctx.trim() : '',
  ...(sharedWords ? { word: hit.word, ...(hit.status ? { status: hit.status } : {}) } : {}),
});
// Endings a verb or adjective may carry and still stand alone in English order.
const ENDINGS = new Set(['ます', 'ました', 'ません', 'ませんでした', 'た', 'て', 'ない', 'なかった', 'いる', 'います', 'いた', 'いました',
  'ている', 'ています', 'ていた', 'ていました']);
const SUBJECTS = /^(i|we|you|he|she|they|it|this|that)\b/i;
// A phrase that is one listed verb or adjective alone, conjugated to fit the English ("The company itta the update
// would...", "There was" as atta): the listed word, if a phrase would leave English hanging after it (dangles). Not
// where the English it replaces has its subject ("I stopped" as yamemashita), which a word swap would lose.
function loneWord(group) {
  const [first, ...rest] = group.parts;
  if (SUBJECTS.test(group.o) || wordCount(group.o) > 3) return null;
  return first.word && rest.every((p) => !p.word && ENDINGS.has(p.t)) ? first.word : null;
}

// Fail closed: keep only swaps whose Japanese is on the list (the plugin's word state, else the level's bundled list).
// A word swap is one listed word (wordSwap). A phrase or sentence swap is listed words and grammar only (validateGroup);
// a phrase of one conjugated word that would leave English hanging after it is shown as a word swap of that word as
// listed. Swaps without a kind are word swaps.
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
      if (!group || numberTrap(group, text)) continue;
      if (!dangles(group, text)) { out.push(group); continue; }
      const lone = loneWord(group);
      const swap = lone && wordSwap(idx.exact.get(exactJa(lone)), o, s.ctx, sharedWords);
      if (swap && !misplaced(swap, text)) out.push(swap);
      continue;
    }
    const hit = idx.exact.get(exactJa(s.f)) || idx.folded.get(normalizeJa(s.f));
    if (!hit) continue;
    const swap = wordSwap(hit, o, s.ctx, sharedWords);
    if (!numberTrap(swap, text) && !misplaced(swap, text)) out.push(swap);
  }
  return out;
}

// Word swaps the prompt forbids that the passage's hints (hints.js) invited on Haiku: mae or ato for "before" or "after",
// which only go after a noun in Japanese order ("mae you run the script"), and a verb that acts on something where
// nothing follows to act on ("the program will tomeru with an error"). Every place the English occurs is checked. Also
// two seen in full answers: ageru "give (away)" for "gave me" (that is kureru), and a swap that would leave a
// contraction's tail on the page ("I've" as watashi've), where the swap's context puts it there.
// Since the held-out test (docs/swap-prompt.md, "General fixes, checked on a second fresh set"), the classes of the
// mistakes the check pipeline made there, each checked where the swap's context puts it (else at every place its
// English occurs, all of which must be wrong):
// - a joiner (english.json "joiners": while, though, when, until...) with a word after it: Japanese puts aida, kedo or
//   toki after what they join ("aida we waited"); "when" and "where" before a helper verb ask a question;
// - "'s" after a word whose "'s" is a verb (english.json "verb_s": "it's" as sore's), and a swap of a pronoun with its
//   contracted verb ("I'm" as watashi), which drops the verb;
// - the generic verbs suru and yaru for any English verb but "do" ("made a cake" as suru): other verbs name their own
//   action;
// - aru and nai, the existence verbs of things, where what exists or is had is a person or an animal ("we have a dog"
//   is iru);
// - a word for "all", "whole" or "entire" over a stretch of time ("all week", "my whole life" are ~juu, not zenbu);
// - a free-choice word (nandemo "anything / whatever") for "anything" after a negation, "if" or "before", or in a
//   question (english.json "polarity": "didn't say anything" is nanimo).
const CLAUSE_JOINERS = new Set(['before', 'after']);
const TRANSITIVE = new Set(['止める', '開ける', '始める', '変える', '上げる', '終える', '消す', '入れる', '出す', '続ける']);
const NO_OBJECT = new Set(['with', 'at', 'in', 'on', 'for', 'to', 'from', 'by', 'and', 'but', 'or', 'so', 'when', 'if', 'because',
  'after', 'before', 'until', 'while', 'now', 'again', 'soon', 'suddenly', 'today', 'yet', 'too', 'then', 'there', 'here',
  'early', 'late', 'already']);
const TO_ME = new Set(['me', 'us']);
const CONTRACTION = /^['’](ve|m|ll|d|re)\b/i;
const CONTRACTION_S = /^['’](ve|m|ll|d|re|s)\b/i;
const JOINERS = new Set(ENGLISH.joiners);
const VERB_S = new Set(ENGLISH.verb_s);
const POLARITY = new Set(ENGLISH.polarity);
const ASKING = new Set(['when', 'where']);
const HELPERS = new Set(['do', 'does', 'did', 'is', 'are', 'was', 'were', 'am', 'will', 'would', 'can', 'could', 'should',
  'shall', 'may', 'might', 'must', 'have', 'has', 'had']);
// a pronoun with its contracted verb, as one swap's English
// ("there's" is not one: it is the existence verb's English, aru)
const PRONOUN_CONTRACTED = /^(i|you|we|they|he|she|it|that|this|here|what|who|where|when|why|how)['’](m|re|ve|ll|d|s)$/i;
const GENERIC_VERBS = new Set(['する', 'やる']);
const DO = new Set(['do', 'does', 'did', 'doing', 'done']);
const THING_EXIST = new Set(['ある', '有る', '在る', '無い', 'ない']);
const FREE_CHOICE = /^(何|なん|誰|だれ|いつ|どこ|どれ|どちら|どっち)でも$/;
const ALL_OF = new Set(['all', 'whole', 'entire']);
// "dont" and the rest as typed without the apostrophe
const NEGATED = /n't$|^(dont|didnt|doesnt|cant|wont|isnt|arent|wasnt|werent|couldnt|wouldnt|shouldnt|havent|hasnt|aint)$/;
// People and animals (an open class: the common ones), with their plurals: what exists is iru, not aru.
const LIVING_BASE = ['person', 'man', 'woman', 'guy', 'kid', 'child', 'baby', 'boy', 'girl', 'friend', 'family', 'parent',
  'mother', 'mom', 'mum', 'mommy', 'father', 'dad', 'daddy', 'son', 'daughter', 'brother', 'sister', 'sibling', 'wife',
  'husband', 'boyfriend', 'girlfriend', 'partner', 'cousin', 'aunt', 'uncle', 'grandma', 'grandpa', 'grandmother',
  'grandfather', 'grandparent', 'grandchild', 'grandson', 'granddaughter', 'nephew', 'niece', 'roommate', 'neighbor',
  'neighbour', 'coworker', 'colleague', 'boss', 'teacher', 'student', 'classmate', 'teammate', 'doctor', 'nurse',
  'customer', 'client', 'user', 'fan', 'follower', 'reader', 'viewer', 'subscriber', 'employee', 'member', 'guest',
  'visitor', 'stranger', 'adult', 'teen', 'teenager', 'toddler', 'twin', 'manager', 'buddy', 'folk', 'animal', 'pet',
  'cat', 'kitten', 'kitty', 'dog', 'puppy', 'pup', 'horse', 'pony', 'cow', 'pig', 'sheep', 'goat', 'rabbit', 'bunny',
  'hamster', 'mouse', 'rat', 'bird', 'parrot', 'duck', 'goose', 'owl', 'crow', 'snake', 'lizard', 'turtle', 'tortoise',
  'frog', 'bear', 'lion', 'tiger', 'wolf', 'fox', 'deer', 'monkey', 'elephant', 'insect', 'bug', 'bee', 'ant', 'spider',
  'butterfly', 'cockroach', 'roach', 'flea', 'worm', 'shark', 'whale', 'dolphin', 'squirrel', 'raccoon', 'mosquito', 'cattle'];
const pluralsOf = (w) => (Object.hasOwn(ENGLISH.plurals, w) ? ENGLISH.plurals[w]
  : [/[^aeiou]y$/.test(w) ? `${w.slice(0, -1)}ies` : /(s|sh|ch|x)$/.test(w) ? `${w}es` : `${w}s`]);
const LIVING = new Set(LIVING_BASE.flatMap((w) => [w, ...pluralsOf(w)]));
// "a lot of friends", "two of my cats": a quantity before "of", which goes on to what is counted
const QUANTITY = new Set(['lot', 'lots', 'couple', 'bunch', 'few', 'some', 'any', 'many', 'none', 'number', 'pair', 'group',
  'plenty', 'ton', 'tons', 'dozen', 'dozens', 'all', 'most', 'each', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
  'eight', 'nine', 'ten']);
// where the noun phrase after "have" or "there is" ends
const PHRASE_ENDS = new Set(['with', 'at', 'in', 'on', 'for', 'to', 'from', 'by', 'about', 'into', 'like', 'than', 'as', 'that',
  'who', 'which', 'whose', 'where', 'when', 'while', 'because', 'but', 'so', 'if', 'is', 'are', 'was', 'were']);
const NP_DETERMINERS = new Set(['the', 'a', 'an', 'this', 'that', 'these', 'those', 'my', 'your', 'his', 'her', 'our', 'their',
  'its', 'some', 'any', 'no', 'every', 'each', 'two', 'three', 'many', 'few']);
// a stretch of time after "all", "whole" or "entire"
const TIME = new Set(['day', 'days', 'night', 'nights', 'week', 'weeks', 'weekend', 'weekends', 'month', 'months', 'year', 'years',
  'hour', 'hours', 'minute', 'minutes', 'morning', 'mornings', 'afternoon', 'evening', 'summer', 'winter', 'spring',
  'autumn', 'season', 'semester', 'term', 'life', 'lives', 'lifetime', 'career', 'childhood', 'time', 'decade', 'decades',
  'century', 'era', 'along', 'long']);

const lowerEn = (s) => String(s).toLowerCase().replace(/’/g, "'");
const wordsIn = (s) => lowerEn(s).match(/[a-z0-9]+(?:'[a-z]+)*/g) || [];
// The words after end up to the next punctuation: the rest of the clause.
const clauseAfter = (text, end) => wordsIn(text.slice(end).split(/[.!?;:,()\n"“”—–]/)[0]);
// The words of the sentence before at, and whether the sentence ends in a question mark.
const sentenceBefore = (text, at) => wordsIn(text.slice(0, at).split(/[.!?\n]/).at(-1));
const asks = (text, end) => /[.!?\n]/.exec(text.slice(end))?.[0] === '?';

// Where a word swap lands: the place its context points at, else every place its English occurs as a whole word.
function placesOf(swap, text) {
  const whole = (s, o) => [...s.matchAll(new RegExp(`(?<![\\p{L}\\p{N}])${o.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\p{L}\\p{N}])`, 'gu'))]
    .map((m) => m.index);
  const at = swap.ctx ? text.indexOf(swap.ctx) : -1;
  const inCtx = at >= 0 ? whole(swap.ctx, swap.o) : [];
  return inCtx.length === 1 ? [at + inCtx[0]] : whole(text, swap.o);
}
const nextWord = (text, end) => /^\s*([A-Za-z][A-Za-z']*)/.exec(text.slice(end))?.[1];
export function misplaced(swap, text) {
  if (swap.parts) return false;
  const o = lowerEn(swap.o);
  if (/^[前後]$/.test(swap.f) && CLAUSE_JOINERS.has(o)) return true;
  if (PRONOUN_CONTRACTED.test(o)) return true;
  if (GENERIC_VERBS.has(swap.f) && !DO.has(o)) return true;
  if (!text) return false;
  const places = placesOf(swap, text);
  const everywhere = (wrong) => places.length > 0 && places.every((i) => wrong(i, i + swap.o.length));
  if (everywhere((_, end) => (VERB_S.has(o) ? CONTRACTION_S : CONTRACTION).test(text.slice(end)))) return true;
  if (JOINERS.has(o) && everywhere(joinsAhead(o, text))) return true;
  if (THING_EXIST.has(swap.f) && everywhere((_, end) => livingAfter(text, end))) return true;
  if (ALL_OF.has(o) && everywhere((_, end) => timeAfter(text, end))) return true;
  if (FREE_CHOICE.test(swap.f) && everywhere((at, end) => sentenceBefore(text, at).some((w) => POLARITY.has(w) || NEGATED.test(w))
    || asks(text, end))) return true;
  const check = TRANSITIVE.has(swap.f) ? (next) => !next || NO_OBJECT.has(next.toLowerCase())
    : swap.f === 'あげる' ? (next) => TO_ME.has(next?.toLowerCase()) : null;
  if (!check) return false;
  for (let i = text.indexOf(swap.o); i !== -1; i = text.indexOf(swap.o, i + 1)) if (check(nextWord(text, i + swap.o.length))) return true;
  return false;
}
// A joiner with a word after it in its sentence stands in front of what it joins; not "when" or "where" before a helper
// verb ("When did you...?").
const joinsAhead = (o, text) => (_, end) => {
  if (!/^\s+[\p{L}\p{N}]/u.test(text.slice(end))) return false;
  return !(ASKING.has(o) && HELPERS.has(clauseAfter(text, end)[0]));
};
// A person or an animal in the noun phrase after end ("have a black dog", "there are two kids"), up to the end of the
// clause, a preposition, a verb of being or "and" before a new determiner; a possessive ("my mom's car") is not its head.
function livingAfter(text, end) {
  const words = clauseAfter(text, end).slice(0, 7);
  for (const [i, w] of words.entries()) {
    if (PHRASE_ENDS.has(w) || (w === 'of' && !QUANTITY.has(words[i - 1]))) return false;
    if ((w === 'and' || w === 'or') && NP_DETERMINERS.has(words[i + 1])) return false;
    if (!/'s$/.test(w) && LIVING.has(w)) return true;
  }
  return false;
}
// A stretch of time after "all", "whole" or "entire": "all day", "all of my adult life", "the whole week". Determiners
// and "of" come first; then at most two words before the time word.
function timeAfter(text, end) {
  let other = 0;
  for (const w of clauseAfter(text, end)) {
    if (TIME.has(w)) return true;
    if (!other && (w === 'of' || NP_DETERMINERS.has(w) || /'s$/.test(w))) continue;
    if (++other > 2 || NP_DETERMINERS.has(w)) return false;
  }
  return false;
}

// A phrase in Japanese order that ends in a verb ends its clause: "I 店に行きました with my mom" leaves English hanging
// after the verb. Punctuation, the end, or a word that starts a new clause may follow it, wherever it occurs.
// A subject pronoun starts a clause too: "As soon as we ie ni hairimashita we could smell the smoke".
const CLAUSE_START = new Set(['and', 'but', 'so', 'or', 'because', 'then', 'when', 'while', 'if', 'until', 'since', 'though',
  'although', 'unless', 'i', 'we', 'he', 'she', 'they']);
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
// "one-on-one", "the first", "the one", "one day" (someday), 一つ for people or days; since the held-out test also
// "one of" naming a member ("one of my friends", not "only one of them") and "one" after a determiner and a modifier
// ("the most recent one"). The prompt forbids these; this
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
// "one of" names one member, a pronoun ("one of my friends"), but counts after these ("only one of the ideas")
const COUNT_FOCUS = new Set(['only', 'just', 'exactly', 'least', 'than', 'but']);
// "the most recent one", "a really good one", "that red one": a determiner, perhaps an intensifier, one word, one
const INTENSIFIERS = new Set(['most', 'least', 'more', 'less', 'very', 'really', 'so', 'quite', 'pretty', 'too']);
const FUNCTION_EN = new Set(ENGLISH.function);
const modifier = (w, det) => !!w && !FUNCTION_EN.has(w) && !NUMBER.has(w) && !ORDINAL.test(w) &&
  !(['this', 'that', 'which'].includes(det) && /(?:[^s]s|..ed)$/.test(w)); // "that makes one": a subject and its verb
// a counter (一つ) counts things, never these
const UNITS = new Set(['day', 'days', 'week', 'weeks', 'month', 'months', 'year', 'years', 'hour', 'hours', 'minute', 'minutes',
  'min', 'mins', 'second', 'seconds', 'night', 'nights', 'time', 'times', 'person', 'people', 'guy', 'guys', 'man', 'men',
  'woman', 'women', 'kid', 'kids', 'child', 'children', 'friend', 'friends', 'dollar', 'dollars', 'buck', 'bucks', 'cent',
  'cents', 'yen', 'euro', 'euros', 'pound', 'pounds', 'mile', 'miles', 'km', 'meter', 'meters', 'feet', 'foot', 'inch',
  'inches', 'percent', 'player', 'players', 'member', 'members', 'student', 'students']);
// people a counter never counts, also as "two of the founders"
const PEOPLE = new Set(['people', 'persons', 'friends', 'kids', 'children', 'guys', 'men', 'women', 'boys', 'girls', 'babies',
  'adults', 'members', 'students', 'teachers', 'parents', 'brothers', 'sisters', 'sons', 'daughters', 'colleagues', 'coworkers',
  'teammates', 'founders', 'users', 'players', 'customers', 'employees', 'engineers', 'developers', 'doctors', 'nurses',
  'artists', 'writers', 'authors', 'fans', 'followers', 'readers', 'viewers', 'workers', 'neighbors', 'neighbours', 'guests',
  'speakers', 'winners', 'leaders', 'managers', 'investors', 'candidates', 'voters', 'citizens', 'soldiers', 'officers',
  'singers', 'actors', 'us', 'you', 'dogs', 'cats', 'animals']);
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
      const [p2, p3] = [tokens[i - 2]?.w || '', tokens[i - 3]?.w || ''];
      const pronoun = DETERMINERS.has(prev) || (ADJECTIVES.has(prev) && DETERMINERS.has(p2)) ||
        (modifier(prev, p2) && DETERMINERS.has(p2)) || (modifier(prev, p3) && INTENSIFIERS.has(p2) && DETERMINERS.has(p3));
      if (t.w === 'one' && (pronoun || AFTER_ONE.has(next) || (next === 'of' && !COUNT_FOCUS.has(prev)))) return true;
      if (counter && (UNITS.has(next) || PEOPLE.has(next))) return true;
      if (counter && next === 'of' && PEOPLE.has(DETERMINERS.has(tokens[i + 2]?.w) ? tokens[i + 3]?.w : tokens[i + 2]?.w)) return true;
    }
  }
  return false;
}

const SENTENCE_BREAK = /[.!?…]+(?:\s|$)|\n+/g;
export function sentenceCount(text) {
  return Math.max(1, String(text).split(SENTENCE_BREAK).filter((s) => wordCount(s) >= 3).length);
}

// Where each sentence of a passage starts, split as sentenceCount splits it; a piece of fewer than 3 words ("Dr.",
// "Hi!") runs on into the next sentence, as sentenceCount doesn't count it as one.
function sentenceStarts(text) {
  const t = String(text);
  const starts = [0];
  for (const m of t.matchAll(SENTENCE_BREAK)) if (wordCount(t.slice(starts.at(-1), m.index)) >= 3) starts.push(m.index + m[0].length);
  return starts;
}
// The sentence a place in the passage is in.
function sentenceAt(at, starts) {
  let i = 0;
  while (i + 1 < starts.length && starts[i + 1] <= at) i++;
  return i;
}

// Swaps a passage should get at a density (percent of its words); also the ceiling then.
export function swapTarget(text, density) {
  return Math.round((wordCount(text) * density) / 100);
}

// The prompt's per-passage target (prompt.js): the density target, else one or two a sentence by level. planSwaps no
// longer caps by it; a lookup budget sets the amount instead.
export const swapLimit = (level, text, density) => (density > 0 ? swapTarget(text, density) : (level <= 1 ? 1 : 2) * sentenceCount(text));
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
// A word whose delayed-start probe is due: the host sends its arm as 'held:probe' or 'shown:probe' (../scripts/fwlib.py
// arm_label).
const isProbe = (w) => typeof w?.arm === 'string' && w.arm.endsWith(':probe');

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

// Intensity (the reader's heavier/lighter shortcut, ../scripts/intensity.py): levels -3..3. The ends go further than a
// budget: off (-3) weaves nothing and calls no model, full (3) has no budget, so it shows every validated candidate of a
// prompt that asks for all the Japanese the list allows. Between them, a lookup budget (INTENSITY_BUDGETS).
export const INTENSITY_OFF = -3;
export const INTENSITY_FULL = 3;
export const INTENSITY_LEVELS = [-3, -2, -1, 0, 1, 2, 3];
export const INTENSITY_NAMES = { '-3': 'off', '-2': 'much lighter', '-1': 'lighter', 0: 'normal', 1: 'heavier', 2: 'much heavier', 3: 'full' };
// The level's old "times the usual amount" (intensity.py FACTORS), kept as the plan's intensity so logged plans stay
// comparable; it no longer scales anything here.
export const INTENSITY_FACTORS = { '-3': 0, '-2': 0.5, '-1': 0.7, 0: 1, 1: 1.4, 2: 1.8 };
export const intensityFactor = (level) => INTENSITY_FACTORS[level] ?? 1;
// What a level means, as ../scripts/intensity.py BUDGETS, budget() and PER_SENTENCE and ../scripts/fwlib.py FREE_P and
// KNOWN_PRIOR_K say (keep them the same): the lookups per 100 words read the reader puts up with, spent on words they
// don't know at a glance yet and on whole sentences in Japanese. Words they do know (a mean predicted lookup chance
// under FREE_P) cost nothing and are used wherever the model offered them, at every level but off. Normal starts at the
// chat's lookup rate before levels meant this (0.33 per 100 words shown, 2026-10-20). Off has no budget and full no
// limit; normal's budget from the host (the popup's challenge, when set) scales the others with it. A word the
// log has nothing on yet starts at KNOWN_PRIOR_K (log-odds of not looking it up) if its status is known, else at PRIOR_K.
export const INTENSITY_BUDGETS = { '-2': 0.15, '-1': 0.3, 0: 0.5, 1: 0.8, 2: 1.2 };
export const INTENSITY_PER_SENTENCE = { '-2': 1, '-1': 1, 0: 1, 1: 2, 2: 2 }; // most budgeted (not-yet-known) swaps in one sentence
export const FREE_P = 0.08;
export const KNOWN_PRIOR_K = 2.5;
// A whole sentence in Japanese costs its translation lookups: the chance that a reader who reads it opens its translation
// (the gloss on its closing mark), whatever words it uses. The host sends the web's (knowledge.json sentencePrice, fitted
// from the log); until it does, this. Measured 2026-10-20 on chat, since the web did not log those opens yet (text.js
// does now): of the 439 sentence ends in the 123 T3 replies with a word count (2026-10-15 to 20), 24 were opened (a
// peek of at least 300 ms, each sentence once), 5.5% a sentence shown (90% interval 3.5-7.6%, resampling replies).
// Weighed by the chance each was read, as the replay weighs words (time on screen at 300 ms a word, at most 0.7, and
// every line down to the lowest one opened), that is 8.4% a sentence read, 90% interval 5.4-11.4%. Chat opens a gloss
// after 0.3 s and the web after 0.7 s, but the fitted chat offset (-0.06) says lookup chances differ little.
export const SENTENCE_PRICE = 0.08;
// The most of a passage's budget whole sentences may take, as ../scripts/intensity.py SENTENCE_SHARE says for chat too
// (keep them the same): words and phrases get the rest, and what one of the two can't use goes to the other. Ranked like
// a word whose lookup chance is their price, sentences would otherwise get only what learning words leave, and nothing
// once code finds enough candidates. 0.18 keeps chat at the one sentence in five (about 1.1 per 100 words) its brief
// asked for at Stage 3 before sentences were priced: 0.18 * 0.5 / 0.08 = 1.1 at normal.
export const SENTENCE_SHARE = 0.18;
// Lookups per 100 words a level allows for words not known at a glance: 0 at off, null (no limit) at full. normal:
// normal's budget (the host's word state budget), which scales every level.
export function intensityBudget(level, normal) {
  if (level === INTENSITY_OFF) return 0;
  if (level === INTENSITY_FULL) return null;
  const base = INTENSITY_BUDGETS[level] ?? INTENSITY_BUDGETS[0];
  return normal > 0 ? (base * normal) / INTENSITY_BUDGETS[0] : base;
}
export const perSentence = (level) => INTENSITY_PER_SENTENCE[level] ?? INTENSITY_PER_SENTENCE[0];
// The model answer a level's swaps are picked from: normal's for the lighter levels, full's for the heavier ones. Asked
// for more with a higher target of their own, Haiku showed little more than at normal (+1 1.13 times, +2 1.25, and +2
// below normal once), and pushed harder, it made more wrong swaps (8% at +2); full's answer holds about 1.55 times
// normal's, at full's accuracy (docs/swap-prompt.md). So +1, +2 and full share one answer and one cache, and stepping
// between them never waits for a model.
export const answerLevel = (level) => (level > 0 ? INTENSITY_FULL : 0);
// 'heavier (+1)', 'much lighter (−2)', 'off', 'full', 'normal': the ends have no number, they aren't a step of the amount.
export function intensityLabel(level) {
  const name = INTENSITY_NAMES[level] ?? INTENSITY_NAMES[0];
  return level && Math.abs(level) < INTENSITY_FULL ? `${name} (${level > 0 ? '+' : '−'}${Math.abs(level)})` : name;
}
// Full has no ceiling; its plan says 5, the largest factor a plan may record (hostsource.js cleanPlan).
const FULL_PLAN_FACTOR = 5;
// Sentences, then phrases, then words: where a bigger swap and smaller ones inside it are free alike, the bigger one is
// kept.
const SIZE = { sentence: 0, phrase: 1 };
const size = (c) => SIZE[c.s.kind] ?? 2;
// Random thinning of free words, so what words known at a glance cost in reading time and lookups can be measured
// (knowledge.py calibrate): half the passages are left alone, the other half keep each free swap with a random share
// between JITTER_MIN and 1. The budget, the swaps it pays for and whole sentences are the same either way. Until
// 2026-10-20 the share was 0.5 to 1 and scaled the budget too: 5.3% Japanese against 6.5%, too little contrast to
// read, and no way to tell what free words cost from what the budgeted ones do. Seeded by the passage and day like
// everything else here, so a passage repaints the same way.
export const JITTER_SHARE = 0.5;
export const JITTER_MIN = 0.2;
export function jitterFor(seed) {
  const random = seededRandom((seed ^ 0x9e3779b9) >>> 0);
  return random() < JITTER_SHARE ? 1 : Math.round((JITTER_MIN + (1 - JITTER_MIN) * random()) * 100) / 100;
}
const round3 = (x) => Math.round(x * 1000) / 1000;
const sum = (xs) => xs.reduce((a, b) => a + b, 0);

// A swap's listed words with their estimates: the host's k and sd (knowledge.json), else the prior for the word's status,
// and the mean lookup chance p from k. The words a lookup would cost (due) are the ones not known at a glance: the
// host's free flag (knowledge.json, with hysteresis) when it sends one, else p under FREE_P; a whole
// sentence also costs its translation's price. expected: the lookups predicted if it is read, every word and the
// translation.
function costOf(s, words, price) {
  const listed = (s.words || [s.word || s.f]).map((ja) => {
    const w = words.get(ja);
    const k = Number.isFinite(w?.k) ? w.k : w?.status === 'known' ? KNOWN_PRIOR_K : PRIOR_K;
    const p = peekChance(k);
    return { ja, w, k, sd: Number.isFinite(w?.sd) ? w.sd : PRIOR_SD, p, free: typeof w?.free === 'boolean' ? w.free : p < FREE_P };
  });
  const due = listed.filter((x) => !x.free);
  const translation = s.kind === 'sentence' ? price : 0;
  return { s, listed, due, cost: sum(due.map((x) => x.p)) + translation, expected: sum(listed.map((x) => x.p)) + translation };
}

// A passage's swaps, picked from every validated candidate when results are read from the cache, so new estimates, a
// new budget or another intensity on the same side of normal never cost a model call. Returns { swaps, plan }: plan says
// how the amount was chosen ({ level, intensity: its factor, jitter: the share of free swaps kept, limit: swaps kept,
// candidates: distinct ones the passage can place, budget: lookups the passage may spend if read, spent: what the
// swaps kept within the budget cost, expected: lookups predicted if it is read, sentencePrice: what a whole sentence
// cost, arms: the delayed-start arm of each word kept that has one, as the host sends it }), logged with the passage's
// shown record. With the plugin's word state, a candidate with a word the host didn't send never counts: the host
// leaves out the words the delayed-start experiment hides (../scripts/fwlib.py hidden).
// Only candidates text.js locate places in the passage count, as applySwaps will place them, and of those that overlap
// only the first kept. A swap whose listed words are all known at a glance (mean lookup chance under FREE_P), and that
// is not a whole sentence, is free: kept wherever the model offered it, bigger swaps first, but for the passage's random
// thinning (jitter, 1 for half the passages, see jitterFor), which keeps each with that chance. The others spend the
// budget: budgetPer100 (the level's, intensityBudget) per 100 words of the passage, whatever the thinning. A word or
// phrase costs the lookup chances of its words not known at a glance, a whole sentence sentencePrice (SENTENCE_PRICE
// without one from the host) and the chances of its words not known at a glance. They go by learning value, a lookup
// chance near TARGET_P: for the least-known word in them, judged on k drawn from N(k, sd) so uncertain words still get
// shown, or for a sentence of free words, its price. One with a word whose delayed-start probe is due (arm ending in
// ':probe') and that wasn't shown today goes first, outside the budget: the probe's guaranteed showing. Then the rest
// while their cost fits, at most perSentence of them in one
// sentence, a whole sentence only alone in its sentence (a full sentence passes to the next candidate). Whole sentences
// spend up to SENTENCE_SHARE of the budget and words and phrases the rest, each going down the one ranking until the
// first that doesn't fit its part; a part one of them can't use (its candidates ran out first) goes to the other, which
// goes on from where it stopped. Then the first that didn't fit gets in with probability remaining / cost, so short
// passages still get learning words on average. The
// popup's density, when set, caps the total: budgeted swaps first, then free ones. One use per Japanese word. Seeded by
// the passage and day, so a passage repaints the same way.
// Full is the same with no budget: everything is free, so every candidate is kept, bigger swaps first, with no
// per-sentence cap, density cap or thinning, and a Japanese word again where the passage says its English again, as
// the full prompt asks.
export function planSwaps(level, swaps, text, sharedWords, { density = 0, budgetPer100, seed = 0, shownToday = [],
  intensity = 0, jitter = 1, sentencePrice } = {}) {
  const words = new Map((sharedWords || []).map((w) => [w.ja, w]));
  const full = intensity >= INTENSITY_FULL;
  const price = sentencePrice > 0 && sentencePrice <= 1 ? sentencePrice : SENTENCE_PRICE;
  const starts = sentenceStarts(text);
  const items = []; // distinct candidates the passage can place, in the model's order
  const distinct = new Set();
  for (const s of new Set(swaps)) {
    const key = JSON.stringify([s.kind || 'word', s.o, s.f, s.ctx || '']);
    const place = distinct.has(key) ? null : locate({ text: String(text) }, s.o, s.ctx);
    distinct.add(key);
    const cost = place && costOf(s, words, price);
    if (cost && (!words.size || cost.listed.every((x) => x.w))) items.push({ ...cost, place, sentence: sentenceAt(place.start, starts) });
  }
  const share = full ? 1 : jitter;
  const budget = full ? Infinity : intensity <= INTENSITY_OFF ? 0
    : ((Number.isFinite(budgetPer100) ? Math.max(0, budgetPer100) : intensityBudget(intensity)) * wordCount(text)) / 100;
  const kept = [];
  let spent = 0; // what the swaps kept within the budget cost
  const armsOf = () => Object.fromEntries(kept.flatMap((c) => c.listed).filter((x) => typeof x.w?.arm === 'string').map((x) => [x.ja, x.w.arm]));
  const planOf = () => ({ swaps: items.filter((c) => kept.includes(c)).map((c) => c.s), plan: { level: intensity,
    intensity: full ? FULL_PLAN_FACTOR : intensityFactor(intensity), jitter: share, limit: kept.length, candidates: items.length,
    ...(full ? {} : { budget: round3(budget), spent: round3(spent) }), expected: round3(sum(kept.map((c) => c.expected))),
    sentencePrice: price, ...(Object.keys(armsOf()).length ? { arms: armsOf() } : {}) } });
  if (intensity <= INTENSITY_OFF) return planOf();
  const cap = density > 0 && !full ? swapTarget(text, density) : Infinity;
  const most = full ? Infinity : perSentence(intensity);
  const random = seededRandom(seed);
  const thinning = seededRandom((seed ^ 0x85ebca6b) >>> 0);
  const shown = new Set(shownToday);
  const priceK = Math.log((1 - price) / price);
  const ranked = full ? [] : items.filter((c) => c.cost > 0);
  for (const c of ranked) {
    const lead = c.due.length ? c.due.reduce((a, b) => (b.k < a.k ? b : a)) : null;
    c.value = -Math.abs((lead ? lead.k + lead.sd * normal(random) : priceK) - TARGET_K);
  }
  ranked.sort((a, b) => b.value - a.value || size(a) - size(b));
  // One use per Japanese word, decided as swaps are kept: a known word offered alone still shows where a phrase or
  // sentence with it was offered first but didn't fit the budget. Full may use a word again.
  const used = new Set();
  const unused = (c) => full || !c.listed.some((x) => used.has(exactJa(x.ja)));
  const clear = (c) => !kept.some((k) => c.place.start < k.place.end && k.place.start < c.place.end);
  const inSentence = new Map(); // sentence -> budgeted swaps kept in it; a whole sentence fills it
  const whole = (c) => c.s.kind === 'sentence';
  const room = (c) => (inSentence.get(c.sentence) || 0) < (whole(c) ? 1 : most);
  const take = (c) => {
    kept.push(c);
    for (const x of c.listed) used.add(exactJa(x.ja));
  };
  const spend = (c, outside = false) => {
    take(c);
    inSentence.set(c.sentence, whole(c) ? Infinity : (inSentence.get(c.sentence) || 0) + 1);
    if (!outside) spent += c.cost;
  };
  // A due probe first, outside the budget: the best ranked candidate with a word whose probe is due and that wasn't shown
  // today, costly or free (thinning mustn't drop it).
  const due = (c) => c.listed.some((x) => isProbe(x.w) && !shown.has(x.ja.normalize('NFKC').toLowerCase()));
  const probe = full ? null : [...ranked, ...items.filter((c) => !c.cost)].find(due);
  if (probe && cap > 0) spend(probe, true);
  // The budget's two parts: what is left of each, and stop, the place in the ranking of the first that didn't fit it
  const parts = [{ left: budget * (1 - SENTENCE_SHARE) }, { left: budget * SENTENCE_SHARE }];
  const partOf = (c) => parts[whole(c) ? 1 : 0];
  const fits = (c) => !kept.includes(c) && unused(c) && room(c) && clear(c);
  const go = (from, only) => {
    for (let i = from; i < ranked.length && kept.length < cap; i++) {
      const c = ranked[i];
      const part = partOf(c);
      if ((only && part !== only) || part.stop !== undefined || !fits(c)) continue;
      if (c.cost <= part.left) { spend(c); part.left -= c.cost; } else part.stop = i;
    }
  };
  go(0);
  for (const [done, other] of [parts, [...parts].reverse()]) { // a part whose candidates ran out hands on its rest
    if (done.stop !== undefined || other.stop === undefined || !(done.left > 0)) continue;
    other.left += done.left;
    done.left = 0;
    const from = other.stop;
    delete other.stop;
    go(from, other);
  }
  for (const part of parts) {
    const c = ranked[part.stop];
    if (c && kept.length < cap && fits(c) && random() < part.left / c.cost) spend(c);
  }
  // Free swaps, bigger first, else in the model's order; one thinned out uses its words too, so thinning keeps a word
  // with chance jitter.
  for (const c of items.filter((x) => full || !x.cost).sort((a, b) => size(a) - size(b))) {
    if (kept.length >= cap) break;
    if (!unused(c) || !clear(c)) continue;
    if (share >= 1 || thinning() < share) take(c);
    else for (const x of c.listed) used.add(exactJa(x.ja));
  }
  return planOf();
}

// planSwaps' swaps alone, at the usual intensity and without thinning.
export function selectSwaps(level, swaps, text, sharedWords, options = {}) {
  return planSwaps(level, swaps, text, sharedWords, { ...options, intensity: 0, jitter: 1 }).swaps;
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
