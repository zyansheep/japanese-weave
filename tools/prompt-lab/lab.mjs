// Prompt lab: runs passages through the extension's own pipeline (prompt, model, validation, picking, painting) and
// prints each one woven, ⟦word or phrase⟧ and ⟪sentence⟫, with the time and cost. See docs/swap-prompt.md.
//
//   GEMINI_KEY_FILE=~/secret/gemini-key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs [model] [passages.json]
//   ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key ... lab.mjs claude-haiku-5-5
//
// WORDS: the word state as the extension gets it (python tools/prompt-lab/words.py > /tmp/words.json); without it, the
// bundled Level 2 list. BATCH: passages per call (16, as in the extension). Keys are read from files so they never
// appear in a command line or the shell history.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Window } from 'happy-dom';
import { requestSwaps } from '../../extension/src/engine.js';
import { selectSwaps } from '../../extension/src/levels.js';
import { applySwaps } from '../../extension/src/text.js';
import { costUsd } from '../../extension/src/usage.js';
import { MAX_BATCH } from '../../extension/src/models.js';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const [model = 'gemini-3.8-flash', passagesPath = here('corpus.json')] = process.argv.slice(2);
const keyFile = (name) => (process.env[name] ? readFileSync(process.env[name], 'utf8').trim() : '');
const keys = { gemini: keyFile('GEMINI_KEY_FILE'), anthropic: keyFile('ANTHROPIC_KEY_FILE') };
if (!keys.gemini && !keys.anthropic) throw new Error('set GEMINI_KEY_FILE or ANTHROPIC_KEY_FILE to a file holding the key');
const BATCH = Number(process.env.BATCH || MAX_BATCH);
const state = process.env.WORDS ? JSON.parse(readFileSync(process.env.WORDS, 'utf8')) : { stage: 2, words: undefined };
const passages = JSON.parse(readFileSync(passagesPath, 'utf8'));
const doc = new Window().document;
let usd = 0;
const started = Date.now();
for (let i = 0; i < passages.length; i += BATCH) {
  const batch = passages.slice(i, i + BATCH);
  const r = await requestSwaps({ keys, model, level: 2, stage: state.stage, words: state.words, shownToday: [], batch });
  for (const u of r.usage) usd += costUsd(u.model, u.input, u.output, undefined, u.cached, u.written);
  if (r.errors.length) console.log('errors:', r.errors.join('; '));
  for (const p of batch) {
    const el = doc.createElement('p');
    el.textContent = p.text;
    applySwaps(el, selectSwaps(2, r.results[p.id], p.text, state.words, { seed: 1 }));
    const woven = [...el.childNodes].map((n) => (n.nodeType === 3 ? n.data
      : n.classList.contains('fw-group') && n.querySelector('.fw-end') ? `⟪${n.textContent}⟫` : `⟦${n.textContent}⟧`)).join('');
    console.log(`\n## ${p.id} (${r.model})\n${woven}`);
  }
}
console.log(`\n${model}: ${((Date.now() - started) / 1000).toFixed(1)} s, $${usd.toFixed(4)} at list prices`);
