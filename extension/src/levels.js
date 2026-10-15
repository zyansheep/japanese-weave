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

// Fail closed: keep only swaps whose Japanese is on the list (the plugin's word state, else the level's bundled list).
// Canonicalizes the written form (kanji as listed), fills gloss, romaji and kana from the list.
export function validateSwaps(level, swaps, sharedWords) {
  if (!Array.isArray(swaps)) return [];
  const idx = indexFor(level, sharedWords);
  const out = [];
  for (const s of swaps) {
    if (!s || typeof s.o !== 'string' || typeof s.f !== 'string') continue;
    const o = s.o.trim();
    if (!o || !s.f.trim()) continue;
    const hit = idx.exact.get(exactJa(s.f)) || idx.folded.get(normalizeJa(s.f));
    if (!hit) continue;
    out.push({
      o,
      f: hit.canon,
      g: hit.en,
      h: hit.hint,
      ...(hit.kana && hit.kana !== hit.canon ? { k: hit.kana } : {}),
      ctx: typeof s.ctx === 'string' ? s.ctx.trim() : '',
      ...(sharedWords ? { word: hit.word, ...(hit.status ? { status: hit.status } : {}) } : {}),
    });
  }
  return out;
}

export function sentenceCount(text) {
  return Math.max(1, String(text).split(/[.!?…]+(?:\s|$)|\n+/).filter((s) => wordCount(s) >= 3).length);
}

// Density ceiling per passage, one use per Japanese word, learning words (active/shaky) kept before known ones.
export function capSwaps(level, swaps, text, sharedWords) {
  const max = (level <= 1 ? 1 : 2) * sentenceCount(text);
  const status = new Map((sharedWords || []).map((w) => [w.ja, w.status]));
  const rank = (s) => (status.get(s.word) && status.get(s.word) !== 'known' ? 0 : 1);
  const used = new Set();
  const unique = swaps.filter((s) => { const k = exactJa(s.word || s.f); if (used.has(k)) return false; used.add(k); return true; });
  const keep = new Set([...unique].sort((a, b) => rank(a) - rank(b)).slice(0, max));
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
