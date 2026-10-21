// Check lab: runs the web pipeline on the judged passages with your words and scores it against oracle.json, as
// matcher-lab.mjs scores the matcher (docs/swap-prompt.md, "Code finds the places, a model checks them"). Code finds the spans (matcher.js), takes the options flagged safe (senses.js), and a model checks the
// doubtful ones and writes the dense sentences and some phrases (verify.js), in calls of BATCH passages; only passages
// with something doubtful or dense get a call.
//
//   cp <snapshot>/state.json /tmp/state.json
//   OFFLINE=1 WORDS=/tmp/state.json bun tools/prompt-lab/check-lab.mjs        # the code part alone: no calls, no cost
//   SPEND_LOG=/tmp/spend.jsonl ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key WORDS=/tmp/state.json \
//     SEEDS=1,2,3 SAVE=/tmp/run.json bun tools/prompt-lab/check-lab.mjs
//
// WORDS: a copy of a word state; its introduced words (known, active, shaky) are the list, as the extension gets it.
// GLOSSES=dictionary: the matcher indexes each word's dictionary glosses (data/dictionary.tsv) instead of its listed
// gloss; GLOSSES=both: both. FLAGS: the sense flags (data/senses.tsv). MODEL (claude-haiku-5-5), BATCH (6), PARALLEL
// (3), SEEDS: runs, each with its own order of the passages into calls ("1,2,3"). THINKING=0: no thinking; EFFORT:
// Haiku's effort (low). PHRASES=0: the prompt without phrases; PHRASES=rare: only where one fits well (prompt 2).
// SENTENCES=0: no dense sentences asked. MAXWORDS: the longest dense sentence (verify.js DENSE_MAX_WORDS). SAVE: a file
// for the answers; REPLAY: such a file, scored again without calls. OFFLINE=1: no calls (the safe options alone).
// VERBOSE=1: the wrong, unjudged and missed, and every sentence and phrase. SUMMARY: a file for the numbers as JSON.
// Every call's tokens and cost go to SPEND_LOG (spend.mjs), which holds the cap.
//
// Scored as the old swap prompt's lab (lab.mjs) scored swaps: each listed word of a swap where it lands is right (it covers a "use" place), fine
// ("ok"), wrong ("not"; or the whole swap is in "bad") or unjudged; a phrase or sentence not judged bad covers every
// "use" place inside it; coverage is the share of "use" places covered; the wrong share is wrong / (right + fine +
// wrong). Three ways: every candidate (as matcher-lab counts every option), what planSwaps shows at full (no budget:
// all that can be placed), and at normal (the budget, without the random thinning, averaged over NORMAL_DAYS days).
// Word swaps are split into function words (every word of the span closed-class) and content words; the words of
// phrases and sentences are counted apart. Intervals: 5th to 95th percentile over 2,000 resamples of the passages.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildIndex, matchText, glossAlternatives } from '../../extension/src/matcher.js';
import { parseSenses, safeOptions } from '../../extension/src/senses.js';
import { parse as parseDictionary } from '../../extension/src/dictionary.js';
import { planPassage, needsCall, requestChecks, candidatesOf, CHECK_VERSION, DENSE_MAX_WORDS } from '../../extension/src/verify.js';
import { planSwaps, wordCount, INTENSITY_FULL } from '../../extension/src/levels.js';
import { hashKey } from '../../extension/src/hash.js';
import { judgedPassage, overlaps, placeSwaps, judgeSwaps } from './oracle.mjs';
import { spendLog, meter } from './spend.mjs';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const env = process.env;
const files = process.argv.slice(2).length ? process.argv.slice(2) : ['eval16', 'heldout', 'live', 'web', 'corpus'].map((s) => here(`${s}.json`));
const oracle = JSON.parse(readFileSync(env.ORACLE || here('oracle.json'), 'utf8'));
const passages = [];
for (const f of files) for (const p of JSON.parse(readFileSync(f, 'utf8'))) if (!passages.some((q) => q.id === p.id)) passages.push(p);
const judged = passages.map((p) => ({ p, j: judgedPassage(p, oracle[p.id]) })).filter((x) => x.j);
if (!env.WORDS) throw new Error('set WORDS to a copy of a word state');
const state = JSON.parse(readFileSync(env.WORDS, 'utf8'));
// the list as the native host sends it (fwlib.public_state)
const words = state.words.filter((w) => ['known', 'active', 'shaky'].includes(w.status))
  .map((w) => ({ ja: w.ja, en: w.en, hint: w.hint || '', kana: w.kana || '', status: w.status }));
const flags = parseSenses(readFileSync(env.FLAGS || here('../../data/senses.tsv'), 'utf8'));

// The matcher's entries: the words with their listed gloss, their dictionary entry's glosses, or both.
function entriesFor(mode) {
  if (!mode || mode === 'listed') return words;
  const dictionary = parseDictionary(readFileSync(here('../../data/dictionary.tsv'), 'utf8'));
  const toHira = (s) => String(s).replace(/[ァ-ヶ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));
  return words.map((w) => {
    const same = dictionary.filter((e) => e.ja === w.ja);
    const e = same.find((x) => x.kana === toHira(w.kana)) || same[0];
    if (!e) return w;
    const own = e.glosses.flatMap((s) => glossAlternatives(s));
    const list = mode === 'both' ? [...new Set([...glossAlternatives(w.en), ...own])] : own;
    return { ...w, en: list.join(' / '), pos: e.pos };
  });
}
const entries = entriesFor(env.GLOSSES);
const index = buildIndex(entries);
const options = { sentences: env.SENTENCES !== '0', maxWords: Number(env.MAXWORDS || DENSE_MAX_WORDS) };
const plans = new Map(judged.map(({ p }) => [p.id, planPassage(p.text, matchText(index, p.text), flags, options)]));
const callFor = judged.filter(({ p }) => needsCall(plans.get(p.id)));

const model = env.MODEL || 'claude-haiku-5-5';
const BATCH = Number(env.BATCH || 6);
const PARALLEL = Number(env.PARALLEL || 3);
const thinking = env.THINKING !== '0';
const phrases = env.PHRASES === '0' ? false : env.PHRASES === 'rare' ? 'rare' : true;
const effort = env.EFFORT || undefined;
const SEEDS = (env.SEEDS || '1').split(',').map(Number);
const variant = { model, batch: BATCH, thinking, effort: effort || 'low', phrases, sentences: options.sentences, maxWords: options.maxWords,
  glosses: env.GLOSSES || 'listed', version: CHECK_VERSION };

// One run: the passages that need a call, shuffled by the seed into calls of BATCH, PARALLEL at once. Returns
// { answers: { id: { picks: [[n, k]], swaps } }, calls: [{ passages, seconds, usd, input, output, errors }] }.
const shuffle = (list, seed) => {
  let s = seed * 7919 + 17;
  const rand = () => (s = (s * 48271) % 2147483647) / 2147483647;
  const out = [...list];
  for (let i = out.length - 1; i > 0; i--) { const k = Math.floor(rand() * (i + 1)); [out[i], out[k]] = [out[k], out[i]]; }
  return out;
};
async function run(seed) {
  const key = readFileSync(env.ANTHROPIC_KEY_FILE || '', 'utf8').trim();
  if (!key) throw new Error('set ANTHROPIC_KEY_FILE to a file holding the key, or OFFLINE=1');
  const log = spendLog();
  const order = shuffle(callFor, seed);
  const batches = [];
  for (let i = 0; i < order.length; i += BATCH) batches.push(order.slice(i, i + BATCH));
  const answers = {};
  const calls = [];
  let next = 0;
  await Promise.all(Array.from({ length: PARALLEL }, async () => {
    while (next < batches.length) {
      const n = next++;
      const batch = batches[n].map(({ p }) => ({ id: p.id, plan: plans.get(p.id) }));
      const t = Date.now();
      const spent = [];
      const fetchImpl = meter(log, 'check-lab', { label: () => `seed ${seed}, ${batch.length} passages`, onUsage: (u, usd) => spent.push({ ...u, usd }) });
      let errors = [];
      try {
        const r = await requestChecks({ keys: { anthropic: key }, model, batch, words, fetchImpl, thinking, effort, phrases, timeoutMs: 120000 });
        for (const [id, a] of Object.entries(r.answers)) answers[id] = { picks: [...a.picks], swaps: a.swaps };
        errors = r.errors;
      } catch (e) {
        errors = [String(e.message || e)];
        if (/spend cap/.test(errors[0])) throw e;
      }
      calls[n] = { passages: batch.length, seconds: (Date.now() - t) / 1000, errors,
        usd: spent.reduce((s, u) => s + u.usd, 0), input: spent.reduce((s, u) => s + u.input, 0),
        output: spent.reduce((s, u) => s + u.output, 0), cached: spent.reduce((s, u) => s + (u.cached || 0), 0) };
    }
  }));
  return { seed, answers, calls };
}

// A passage's candidates from a run's answer (none for a passage that needed no call: its safe options alone).
const candidates = (id, answer) => candidatesOf(plans.get(id), answer && { picks: new Map(answer.picks), swaps: answer.swaps }, words);

// Where swaps land and how they are judged (oracle.mjs), each word swap's kind from the span it sits on.
const placed = (p, swaps, all = false) => placeSwaps(p, swaps, all);
const judge = (p, j, shown) => judgeSwaps(j, shown, plans.get(p.id).spans.map((m) => m.span));

// Per passage, for one run: what each way shows and how it is judged.
const NORMAL_DAYS = Number(env.NORMAL_DAYS || 50);
const seedOf = (s, text) => parseInt(hashKey(`day${s}:${text}`).slice(-8), 16);
function score(answers) {
  return judged.map(({ p, j }) => {
    const cands = candidates(p.id, answers?.[p.id]);
    const all = judge(p, j, placed(p, cands, true));
    const full = judge(p, j, placed(p, planSwaps(2, cands, p.text, words, { intensity: INTENSITY_FULL }).swaps));
    // normal: the share of days each place is covered and each verdict comes up
    const normal = { covered: 0, right: 0, ok: 0, wrong: 0, sentences: 0 };
    for (let d = 0; d < NORMAL_DAYS; d++) {
      const v = judge(p, j, placed(p, planSwaps(2, cands, p.text, words, { seed: seedOf(d, p.text), intensity: 0, jitter: 1 }).swaps));
      normal.covered += v.covered.size / NORMAL_DAYS;
      normal.right += v.right / NORMAL_DAYS;
      normal.ok += v.ok / NORMAL_DAYS;
      normal.wrong += v.wrong.length / NORMAL_DAYS;
      normal.sentences += v.sentences.size / NORMAL_DAYS;
    }
    const plan = plans.get(p.id);
    const groups = cands.filter((c) => c.parts);
    return { p, j, all, full, normal, places: j.use.length, call: needsCall(plan), words: wordCount(p.text),
      sentencesAsked: plan.sentences.length, sentencesValid: groups.filter((c) => c.kind === 'sentence').length,
      phrasesValid: groups.filter((c) => c.kind === 'phrase').length,
      sentencesReturned: (answers?.[p.id]?.swaps || []).filter((s) => /^s\d+$/i.test(s.at)).length,
      phrasesReturned: (answers?.[p.id]?.swaps || []).filter((s) => !/^s\d+$/i.test(s.at)).length,
      doubtful: plan.spans.filter((m) => m.doubtful.length).length,
      picked: plan.spans.filter((m) => m.doubtful.length && Number.isInteger(new Map(answers?.[p.id]?.picks || []).get(m.n))).length,
      answered: plan.spans.filter((m) => m.doubtful.length && new Map(answers?.[p.id]?.picks || []).has(m.n)).length };
  });
}

const sum = (rows, f) => rows.reduce((s, r) => s + f(r), 0);
function totals(rows) {
  const places = sum(rows, (r) => r.places);
  const way = (k) => {
    const right = sum(rows, (r) => (k === 'normal' ? r.normal.right : r[k].right));
    const ok = sum(rows, (r) => (k === 'normal' ? r.normal.ok : r[k].ok));
    const wrong = sum(rows, (r) => (k === 'normal' ? r.normal.wrong : r[k].wrong.length));
    const covered = sum(rows, (r) => (k === 'normal' ? r.normal.covered : r[k].covered.size));
    const out = { covered, coverage: covered / places, right, ok, wrong, wrongShare: wrong / (right + ok + wrong),
      sentences: sum(rows, (r) => (k === 'normal' ? r.normal.sentences : r[k].sentences.size)) };
    if (k !== 'normal') {
      out.unjudged = sum(rows, (r) => r[k].unjudged.length);
      for (const kind of ['fn', 'content', 'group']) {
        const x = Object.fromEntries(['right', 'ok', 'wrong', 'unjudged'].map((v) => [v, sum(rows, (r) => r[k].by[kind][v])]));
        out[kind] = { ...x, wrongShare: x.wrong / (x.right + x.ok + x.wrong) };
      }
    }
    return out;
  };
  return { places, possible: sum(rows, (r) => r.j.sentences.length), passages: rows.length, all: way('all'), full: way('full'),
    normal: way('normal'), calls: sum(rows, (r) => (r.call ? 1 : 0)), doubtful: sum(rows, (r) => r.doubtful),
    picked: sum(rows, (r) => r.picked), answered: sum(rows, (r) => r.answered), sentencesAsked: sum(rows, (r) => r.sentencesAsked),
    sentencesReturned: sum(rows, (r) => r.sentencesReturned), sentencesValid: sum(rows, (r) => r.sentencesValid),
    phrasesReturned: sum(rows, (r) => r.phrasesReturned), phrasesValid: sum(rows, (r) => r.phrasesValid) };
}
function interval(rows, f, n = 2000) {
  let seed = 12345;
  const rand = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
  const values = [];
  for (let b = 0; b < n; b++) values.push(f(totals(rows.map(() => rows[Math.floor(rand() * rows.length)]))));
  values.sort((a, b) => a - b);
  return [values[Math.floor(0.05 * n)], values[Math.floor(0.95 * n)]];
}
// Wilson's 90% interval for k of n
function wilson(k, n, z = 1.645) {
  if (!n) return [NaN, NaN];
  const p = k / n;
  const c = (p + (z * z) / (2 * n)) / (1 + (z * z) / n);
  const h = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / (1 + (z * z) / n);
  return [c - h, c + h];
}
const pct = (x, d = 1) => (Number.isFinite(x) ? `${(100 * x).toFixed(d)}%` : '-');
const range = ([lo, hi], d = 1) => `${pct(lo, d)}-${pct(hi, d)}`;

// The code part, which needs no call: the matcher's hits split by flag, as matcher-lab counts them (every option of
// every span), and what share of the places the safe options cover.
function codeReport() {
  const t = { safe: { right: 0, ok: 0, wrong: 0, unjudged: 0 }, doubtful: { right: 0, ok: 0, wrong: 0, unjudged: 0 } };
  const covered = { safe: 0, doubtful: 0 };
  const wrong = [];
  const perPassage = [];
  let places = 0;
  for (const { p, j } of judged) {
    places += j.use.length;
    const plan = plans.get(p.id);
    const seen = { safe: new Set(), any: new Set() };
    const row = { right: 0, ok: 0, wrong: 0 };
    for (const m of plan.spans) {
      const s = m.span;
      const safe = safeOptions(flags, s);
      s.options.forEach((e, i) => {
        const on = (list) => list.filter((x) => x.ja.includes(e.ja) && overlaps(x, s));
        const use = on(j.use);
        const v = use.length ? 'right' : on(j.ok).length ? 'ok' : on(j.not).length ? 'wrong' : 'unjudged';
        t[safe[i] ? 'safe' : 'doubtful'][v]++;
        if (safe[i] && v !== 'unjudged') row[v]++;
        if (safe[i] && v === 'wrong') wrong.push(`${p.id}: ${s.english} -> ${e.ja} (${s.glosses[i]})`);
        for (const x of use) { seen.any.add(x.key); if (safe[i]) seen.safe.add(x.key); }
      });
    }
    covered.safe += seen.safe.size;
    covered.doubtful += seen.any.size - seen.safe.size;
    perPassage.push(row);
  }
  const s = t.safe;
  const judgedSafe = s.right + s.ok + s.wrong;
  // the safe hits' wrong share, resampling passages
  let seed = 999;
  const rand = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
  const boots = [];
  for (let b = 0; b < 2000; b++) {
    let w = 0; let n = 0;
    for (let i = 0; i < perPassage.length; i++) { const r = perPassage[Math.floor(rand() * perPassage.length)]; w += r.wrong; n += r.right + r.ok + r.wrong; }
    boots.push(w / n);
  }
  boots.sort((a, b) => a - b);
  console.log(`\nCode alone: ${entries.length} entries (${variant.glosses} glosses), ${judged.length} judged passages, ${places} "use" places`);
  console.log('| Options | Right / fine / wrong | Wrong share | 90% interval (Wilson; resampling passages) | Unjudged | "use" places covered |');
  console.log('|---|---|---|---|---|---|');
  console.log(`| flagged safe | ${s.right} / ${s.ok} / ${s.wrong} | ${pct(s.wrong / judgedSafe)} | ${range(wilson(s.wrong, judgedSafe))}; ` +
    `${range([boots[100], boots[1900]])} | ${s.unjudged} | ${covered.safe} (${pct(covered.safe / places)}) |`);
  const d = t.doubtful;
  console.log(`| doubtful | ${d.right} / ${d.ok} / ${d.wrong} | ${pct(d.wrong / (d.right + d.ok + d.wrong))} | | ${d.unjudged} | ` +
    `${covered.doubtful} more (${pct(covered.doubtful / places)}) |`);
  const spans = [...plans.values()].flatMap((pl) => pl.spans);
  const dense = judged.reduce((n, { p }) => n + plans.get(p.id).sentences.length, 0);
  const denseListed = judged.reduce((n, { p, j }) => n + plans.get(p.id).sentences.filter((x) => j.sentences.includes(x.text)).length, 0);
  console.log(`Spans: ${spans.length}, ${spans.filter((m) => m.safe.length).length} taken by code, ${spans.filter((m) => m.doubtful.length).length} doubtful ` +
    `(${spans.filter((m) => m.doubtful.length && m.span.function).length} of them closed-class English). Dense sentences: ${dense}, ${denseListed} of them ` +
    `in the oracle's list of ${judged.reduce((n, { j }) => n + j.sentences.length, 0)}. Passages needing a call: ${callFor.length} of ${judged.length}.`);
  if (wrong.length) console.log(`Wrong safe hits: ${wrong.join('; ')}`);
  return { safe: { ...s, wrongShare: s.wrong / judgedSafe, wilson: wilson(s.wrong, judgedSafe), boot: [boots[100], boots[1900]] }, doubtful: d, covered, places };
}

const summary = { variant, passages: judged.length, code: codeReport(), runs: [] };
const runs = env.REPLAY ? JSON.parse(readFileSync(env.REPLAY, 'utf8')).runs : [];
if (!env.REPLAY && !env.OFFLINE) for (const seed of SEEDS) runs.push(await run(seed));
if (env.SAVE) writeFileSync(env.SAVE, JSON.stringify({ variant, runs }));
if (env.OFFLINE) runs.push({ seed: 0, answers: {}, calls: [] });

console.log(`\nThe pipeline: ${model}, ${BATCH} passages a call, thinking ${thinking ? `on (${effort || 'low'} effort)` : 'off'}, phrases ${phrases === 'rare' ? 'rare' : phrases ? 'on' : 'off'}, ` +
  `dense sentences ${options.sentences ? `up to ${options.maxWords} words` : 'off'}, ${variant.glosses} glosses`);
console.log('| Seed | Coverage: all / full / normal | Wrong: all (fn, content, groups) / full / normal | Unjudged | Sentences whole (all / full) | ' +
  'Picks made / answered / doubtful | Sentences asked / returned / valid | Phrases returned / valid | Calls | s a call (mean, max) | $ per 1,000 passages | No call |');
console.log('|---|---|---|---|---|---|---|---|---|---|---|---|');
for (const r of runs) {
  const rows = score(r.answers);
  const t = totals(rows);
  const usd = r.calls.reduce((s, c) => s + (c?.usd || 0), 0);
  const seconds = r.calls.map((c) => c?.seconds || 0);
  const errors = r.calls.flatMap((c) => c?.errors || []);
  const ci = { coverage: interval(rows, (x) => x.full.coverage), wrong: interval(rows, (x) => x.full.wrongShare) };
  summary.runs.push({ seed: r.seed, ...t, ci, usd, perThousand: (1000 * usd) / judged.length, calls: r.calls.length,
    seconds: seconds.reduce((a, b) => a + b, 0) / Math.max(1, seconds.length), maxSeconds: Math.max(0, ...seconds),
    output: r.calls.reduce((s, c) => s + (c?.output || 0), 0) / Math.max(1, r.calls.length), errors: errors.length,
    noCall: (judged.length - callFor.length) / judged.length });
  console.log(`| ${r.seed} | ${pct(t.all.coverage)} / ${pct(t.full.coverage)} (${range(ci.coverage, 0)}) / ${pct(t.normal.coverage)} | ` +
    `${pct(t.all.wrongShare)} (${pct(t.all.fn.wrongShare)}, ${pct(t.all.content.wrongShare)}, ${pct(t.all.group.wrongShare)}) / ` +
    `${pct(t.full.wrongShare)} (${range(ci.wrong)}) / ${pct(t.normal.wrongShare)} | ${t.all.unjudged} | ${t.all.sentences} / ${t.full.sentences} of ${t.possible} | ` +
    `${t.picked} / ${t.answered} / ${t.doubtful} | ${t.sentencesAsked} / ${t.sentencesReturned} / ${t.sentencesValid} | ${t.phrasesReturned} / ${t.phrasesValid} | ` +
    `${r.calls.length} | ${(seconds.reduce((a, b) => a + b, 0) / Math.max(1, seconds.length)).toFixed(1)}, ${Math.max(0, ...seconds).toFixed(1)} | ` +
    `${((1000 * usd) / judged.length).toFixed(3)} | ${pct((judged.length - callFor.length) / judged.length, 0)} |${errors.length ? ` errors: ${errors.join('; ')}` : ''}`);
  if (env.VERBOSE) {
    for (const row of rows) {
      for (const w of row.all.wrong) console.log(`  wrong ${row.p.id}: ${w}`);
      for (const u of row.all.unjudged) console.log(`  unjudged ${row.p.id}: ${u}`);
      for (const e of row.j.use) if (!row.all.covered.has(e.key)) console.log(`  missed ${row.p.id}: ${e.en}=${e.ja.join('|')}`);
      for (const c of candidates(row.p.id, r.answers[row.p.id]).filter((x) => x.parts)) console.log(`  ${c.kind} ${row.p.id}: ${c.o} -> ${c.f}`);
    }
  }
}
if (runs.length > 1) {
  const k = (f) => summary.runs.map(f);
  const span = (xs, d = 1) => `${pct(Math.min(...xs), d)}-${pct(Math.max(...xs), d)}`;
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  console.log(`\nOver ${runs.length} seeds (mean, range): coverage all ${pct(mean(k((x) => x.all.coverage)))} (${span(k((x) => x.all.coverage))}), ` +
    `full ${pct(mean(k((x) => x.full.coverage)))} (${span(k((x) => x.full.coverage))}), normal ${pct(mean(k((x) => x.normal.coverage)))}; ` +
    `wrong all ${pct(mean(k((x) => x.all.wrongShare)))} (${span(k((x) => x.all.wrongShare))}), full ${pct(mean(k((x) => x.full.wrongShare)))} ` +
    `(${span(k((x) => x.full.wrongShare))}); sentences whole ${mean(k((x) => x.full.sentences)).toFixed(1)} of ${summary.runs[0].possible}; ` +
    `$${mean(k((x) => x.perThousand)).toFixed(3)} per 1,000 passages (${Math.min(...k((x) => x.perThousand)).toFixed(3)}-${Math.max(...k((x) => x.perThousand)).toFixed(3)}); ` +
    `${mean(k((x) => x.seconds)).toFixed(1)} s a call`);
}
if (env.SUMMARY) writeFileSync(env.SUMMARY, JSON.stringify(summary, null, 1));
