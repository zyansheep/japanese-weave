// What normal and the lighter levels produce on the lab's passages, as digests: each passage's model prompt and answer
// key, and its plan and swaps with no answer and with every doubtful span accepted, at -2, -1 and 0, for three days,
// thinned as the worker thins. The bundled list stands in for the word state, with made-up estimates. digests() is run on
// this tree by heavier.test.js. The fixture it compares with (fixtures/normal-levels.json) came from the code before
// the levels above normal changed:
//   git archive <commit> extension/src | tar -x -C /tmp/old
//   SRC=/tmp/old/extension/src bun extension/test/levelsgolden.js > extension/test/fixtures/normal-levels.json
import { readFileSync } from 'node:fs';

const lab = (name) => JSON.parse(readFileSync(new URL(`../../tools/prompt-lab/${name}.json`, import.meta.url), 'utf8'));

export function digests({ pipelineFor, planText, answerKey, buildCheckContent, candidatesOf, planSwaps, wordCount, isZeroJapanese, intensityBudget,
  jitterFor, wordsForLevel, hashKey }) {
  const words = wordsForLevel(2).map((w, i) => ({ ...w, status: i < 100 ? 'known' : 'active', glosses: w.en.split(' / '),
    safe: i % 3 ? [] : w.en.split(' / ').slice(0, 1), k: i < 100 ? 3 : 0.4 + (i % 10) / 5, sd: 0.8, free: i < 100 }));
  const pipe = pipelineFor(words);
  const seen = new Set();
  const out = {};
  for (const name of ['eval16', 'heldout', 'live', 'web', 'corpus', 'fresh', 'fresh2']) {
    for (const { text } of lab(name)) {
      if (seen.has(text) || wordCount(text) < 6 || isZeroJapanese(text)) continue;
      seen.add(text);
      const plan = planText(pipe, text);
      const all = { picks: new Map(plan.spans.filter((m) => m.doubtful.length).map((m) => [m.n, 0])), swaps: [] };
      const made = [];
      for (const answer of [null, all]) {
        const candidates = candidatesOf(plan, answer, words, 2);
        for (const intensity of [-2, -1, 0]) {
          for (const day of [1, 2, 3]) {
            const seed = parseInt(hashKey(`day${day}:${text}`).slice(-8), 16);
            made.push(planSwaps(2, candidates, text, words, { budgetPer100: intensityBudget(intensity, 0.5), sentencePrice: 0.11, seed, intensity,
              jitter: jitterFor(seed), now: 1_790_000_000_000 }));
          }
        }
      }
      out[hashKey(text)] = hashKey(JSON.stringify([answerKey(plan), buildCheckContent([{ id: 'p', plan }]), made]));
    }
  }
  return out;
}

if (import.meta.main) {
  const src = process.env.SRC;
  const mods = await Promise.all(['pipeline.js', 'verify.js', 'levels.js', 'hash.js'].map((f) => import(`${src}/${f}`)));
  console.log(JSON.stringify(digests(Object.assign({}, ...mods)), null, 0));
}
