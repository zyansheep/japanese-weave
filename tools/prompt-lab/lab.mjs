// Prompt lab: runs passages through the extension's own pipeline (prompt, Gemini, validation, picking, painting) and
// prints each one woven, ⟦word or phrase⟧ and ⟪sentence⟫, with the time and cost. See docs/swap-prompt.md.
//
//   GEMINI_KEY_FILE=~/secret/gemini-key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs [model] [passages.json]
//
// WORDS: the word state as the extension gets it (python tools/prompt-lab/words.py > /tmp/words.json); without it, the
// bundled Level 2 list. The key is read from a file so it never appears in a command line or the shell history.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Window } from 'happy-dom';
import { requestSwaps } from '../../extension/src/engine.js';
import { selectSwaps } from '../../extension/src/levels.js';
import { applySwaps } from '../../extension/src/text.js';
import { costUsd } from '../../extension/src/usage.js';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const [model = 'gemini-3.8-flash', passagesPath = here('corpus.json')] = process.argv.slice(2);
if (!process.env.GEMINI_KEY_FILE) throw new Error('set GEMINI_KEY_FILE to a file holding the Gemini key');
const apiKey = readFileSync(process.env.GEMINI_KEY_FILE, 'utf8').trim();
const state = process.env.WORDS ? JSON.parse(readFileSync(process.env.WORDS, 'utf8')) : { stage: 2, words: undefined };
const passages = JSON.parse(readFileSync(passagesPath, 'utf8'));
const doc = new Window().document;
let usd = 0;
const started = Date.now();
for (let i = 0; i < passages.length; i += 8) {
  const batch = passages.slice(i, i + 8);
  const r = await requestSwaps({ apiKey, model, level: 2, stage: state.stage, words: state.words, shownToday: [], batch });
  for (const u of r.usage) usd += costUsd(u.model, u.input, u.output);
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
