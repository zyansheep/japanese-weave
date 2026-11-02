// Replays the lab's passages through the planner at normal, +1, +2 and full, with no model call and no cost: swaps per
// 100 words, whole sentences and dictionary words, and the budget spent (docs/swap-prompt.md, "Heavier than normal").
// Two bounds, since only a model can say which doubtful spans fit and which sentences it can write:
//   sure: no answer: the swaps code is sure of, a passage's first painting;
//   all:  every doubtful span accepted at its first option and every sentence code asks for written whole.
// A sentence in "all" is made here (the span's words, not a model's Japanese), so it doesn't go through validateSwaps:
// the planner sees what it costs, not whether it would be written.
//
//   python tools/prompt-lab/words.py state.json > words.json    # with JAPANESE_WEAVE_HOME at a copy of the state
//   WORDS=words.json bun tools/prompt-lab/level-replay.mjs
//   WORDS=words.json SRC=/path/to/an/older/extension/src bun tools/prompt-lab/level-replay.mjs    # before a change
//
// WORDS: words.py's output. SRC: the extension's src (this tree's). DAYS: seeds a passage is averaged over (20). FILES: the
// lab's sets (all of them, each passage once, zero-Japanese topics left out).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const env = process.env;
const src = env.SRC || here('../../extension/src');
const load = (name) => import(`${src}/${name}`);
const { pipelineFor, planText, heavierPlan, dictionaryWordsOf, clearOfSentences } = await load('pipeline.js');
const { candidatesOf } = await load('verify.js');
const { planSwaps, wordCount, isZeroJapanese, intensityBudget, jitterFor, isHeavier } = await load('levels.js');
const { hashKey } = await load('hash.js');

const reply = JSON.parse(readFileSync(env.WORDS, 'utf8'));
const words = reply.words;
const pipe = pipelineFor(words);
const extra = heavierPlan && reply.dictionaryWords ? pipelineFor(dictionaryWordsOf(reply.dictionaryWords, words)) : null;
const DAYS = Number(env.DAYS || 20);
const files = (env.FILES || 'eval16,heldout,live,web,corpus,fresh,fresh2').split(',');
const seen = new Set();
const passages = [];
for (const f of files) {
  for (const p of JSON.parse(readFileSync(here(`${f}.json`), 'utf8'))) {
    if (seen.has(p.text) || wordCount(p.text) < 6 || isZeroJapanese(p.text)) continue;
    seen.add(p.text);
    passages.push(p);
  }
}

// A sentence as validateSwaps would give it, from the first option of each span in it.
function written(plan, x) {
  const parts = [];
  for (const m of plan.spans.filter((y) => y.span.start >= x.start && y.span.end <= x.end)) {
    const o = m.safe[0] || m.doubtful[0];
    if (!parts.some((q) => q.word === o.ja)) parts.push({ t: o.ja, word: o.ja, g: o.en, h: '' }, { t: '\u3092' });
  }
  parts.push({ t: '\u3002' });
  const list = parts.filter((q) => q.word).map((q) => q.word);
  return { kind: 'sentence', o: x.text, f: parts.map((q) => q.t).join(''), g: x.text, parts, words: list, word: list[0], ctx: x.text };
}

const heavier = (level) => !!isHeavier?.(level);
function day(p, level, bound, d) {
  const seed = parseInt(hashKey(`day${d}:${p.text}`).slice(-8), 16);
  const plan = heavier(level) ? heavierPlan(pipe, extra, p.text) : planText(pipe, p.text);
  const listed = heavier(level) ? [...words, ...plan.added] : words;
  const answer = bound === 'all' ? { picks: new Map(plan.spans.filter((m) => m.doubtful.length).map((m) => [m.n, 0])), swaps: [] } : null;
  let candidates = candidatesOf(plan, answer, listed, 2);
  if (bound === 'all') candidates = [...plan.sentences.map((x) => written(plan, x)), ...candidates];
  else if (heavier(level) && plan.ask && plan.sentences.length) candidates = clearOfSentences(plan, candidates);
  return planSwaps(2, candidates, p.text, listed, { budgetPer100: intensityBudget(level, reply.budget), sentencePrice: reply.sentencePrice,
    recency: reply.recency, seed, intensity: level, jitter: jitterFor(seed), now: Date.UTC(2026, 10, 1) });
}

const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const totalWords = passages.reduce((s, p) => s + wordCount(p.text), 0);
const out = [];
for (const bound of ['sure', 'all']) {
  for (const level of [0, 1, 2, 3]) {
    const t = { swaps: 0, english: 0, sentences: 0, dictionary: 0, spent: 0, budget: 0, over: 0, plans: 0, expected: 0 };
    for (const p of passages) {
      for (let d = 0; d < DAYS; d++) {
        const { swaps, plan } = day(p, level, bound, d);
        t.swaps += swaps.length;
        t.english += sum(swaps.map((s) => wordCount(s.o)));
        t.sentences += swaps.filter((s) => s.kind === 'sentence').length;
        t.dictionary += plan.dictionary || 0;
        t.spent += plan.spent || 0;
        t.budget += plan.budget || 0;
        t.expected += plan.expected || 0;
        t.plans += 1;
        if (plan.budget !== undefined && plan.spent > plan.budget + 1e-9) t.over += 1;
      }
    }
    const per = (x) => Math.round((1000 * x) / DAYS / totalWords * 100) / 1000;
    out.push({ bound, level, swapsPer100: per(t.swaps), englishGonePer100: per(t.english), sentencesPer100Passages: Math.round((1000 * t.sentences) / DAYS / passages.length) / 10,
      dictionaryPer100: per(t.dictionary), lookupsPer100: per(t.expected), spentPer100: per(t.spent),
      budgetShare: t.budget ? Math.round((1000 * t.spent) / t.budget) / 1000 : null, overBudgetPlans: `${t.over}/${t.plans}` });
  }
}
console.log(`${passages.length} passages, ${totalWords} words, ${DAYS} days`);
console.table(out);
