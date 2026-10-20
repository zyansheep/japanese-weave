// Prompt lab: runs passages through the extension's own pipeline (prompt, model, validation, picking, painting) and
// prints each one woven, ⟦word or phrase⟧ and ⟪sentence⟫, with the time and cost, then the counts: candidates a passage
// as the model returned them and after validation, passages left with none, swaps shown, how many are learning words,
// and of the learning words a passage's "learn" lists (web.json, live.json), how many came back. See
// docs/swap-prompt.md.
//
//   GEMINI_KEY_FILE=~/secret/gemini-key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs [model] [passages.json]
//   ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key ... lab.mjs claude-haiku-5-5
//
// WORDS: the word state as the extension gets it (python tools/prompt-lab/words.py > /tmp/words.json); without it, the
// bundled Level 2 list. BATCH: passages per call, PARALLEL: calls at once (both as in the extension: 16 and 1, Haiku 6
// and 3). Keys are read from files so they never appear in a command line or the shell history.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Window } from 'happy-dom';
import { requestSwaps } from '../../extension/src/engine.js';
import { selectSwaps } from '../../extension/src/levels.js';
import { parseSwapsResponse } from '../../extension/src/gemini.js';
import { applySwaps } from '../../extension/src/text.js';
import { costUsd } from '../../extension/src/usage.js';
import { MAX_BATCH, BATCH as BATCHES, PARALLEL as PARALLELS } from '../../extension/src/models.js';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const [model = 'gemini-3.8-flash', passagesPath = here('corpus.json')] = process.argv.slice(2);
const keyFile = (name) => (process.env[name] ? readFileSync(process.env[name], 'utf8').trim() : '');
const keys = { gemini: keyFile('GEMINI_KEY_FILE'), anthropic: keyFile('ANTHROPIC_KEY_FILE') };
if (!keys.gemini && !keys.anthropic) throw new Error('set GEMINI_KEY_FILE or ANTHROPIC_KEY_FILE to a file holding the key');
const BATCH = Number(process.env.BATCH || BATCHES[model] || MAX_BATCH);
const PARALLEL = Number(process.env.PARALLEL || PARALLELS[model] || 1);
const state = process.env.WORDS ? JSON.parse(readFileSync(process.env.WORDS, 'utf8')) : { stage: 2, words: undefined };
const passages = JSON.parse(readFileSync(passagesPath, 'utf8'));
const doc = new Window().document;
let usd = 0;
const started = Date.now();
const batches = [];
for (let i = 0; i < passages.length; i += BATCH) batches.push(passages.slice(i, i + BATCH));
const answers = [];
// Every answer as it came back, before validation (Anthropic's or Google's response shape).
const raw = new Map();
const fetchImpl = async (url, init) => {
  const res = await fetch(url, init);
  const data = await res.clone().json().catch(() => null);
  const text = (data?.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('') ||
    (data?.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('');
  try { for (const [id, swaps] of parseSwapsResponse(text)) raw.set(id, swaps); } catch { /* an unusable answer has none */ }
  return res;
};
let next = 0;
await Promise.all(Array.from({ length: PARALLEL }, async () => {
  while (next < batches.length) {
    const n = next++;
    const t = Date.now();
    answers[n] = await requestSwaps({ keys, model, level: 2, stage: state.stage, words: state.words, shownToday: [], batch: batches[n], fetchImpl });
    answers[n].seconds = (Date.now() - t) / 1000;
  }
}));
const learning = new Set((state.words || []).filter((w) => w.status && w.status !== 'known').map((w) => w.ja));
const counts = { raw: 0, valid: 0, none: 0, shown: 0, shownLearning: 0, learn: 0, learnFound: 0 };
for (const [n, batch] of batches.entries()) {
  const r = answers[n];
  for (const u of r.usage) usd += costUsd(u.model, u.input, u.output, undefined, u.cached, u.written);
  console.log(`\n# call ${n + 1}: ${batch.length} passages, ${r.seconds.toFixed(1)} s`);
  if (r.errors.length) console.log('errors:', r.errors.join('; '));
  for (const p of batch) {
    const el = doc.createElement('p');
    el.textContent = p.text;
    const shown = selectSwaps(2, r.results[p.id], p.text, state.words, { seed: 1 });
    applySwaps(el, shown);
    const found = new Set(r.results[p.id].flatMap((s) => s.words || [s.word]));
    Object.assign(counts, { raw: counts.raw + (raw.get(p.id)?.length || 0), valid: counts.valid + r.results[p.id].length,
      none: counts.none + !r.results[p.id].length, shown: counts.shown + shown.length,
      shownLearning: counts.shownLearning + shown.filter((s) => (s.words || [s.word]).some((w) => learning.has(w))).length,
      learn: counts.learn + (p.learn || []).length, learnFound: counts.learnFound + (p.learn || []).filter((w) => found.has(w)).length });
    const woven = [...el.childNodes].map((n) => (n.nodeType === 3 ? n.data
      : n.classList.contains('fw-group') && n.querySelector('.fw-end') ? `⟪${n.textContent}⟫` : `⟦${n.textContent}⟧`)).join('');
    console.log(`\n## ${p.id} (${r.model})\n${woven}`);
  }
}
const per = (k) => (counts[k] / passages.length).toFixed(2);
console.log(`\n${model}: ${((Date.now() - started) / 1000).toFixed(1)} s, $${usd.toFixed(4)} at list prices`);
console.log(`${passages.length} passages: ${per('raw')} candidates a passage as returned, ${per('valid')} after validation, ` +
  `${counts.none} with none; ${per('shown')} shown a passage, ${counts.shownLearning} of ${counts.shown} with a learning word` +
  `${counts.learn ? `; ${counts.learnFound} of the ${counts.learn} learning words in "learn" found` : ''}`);
