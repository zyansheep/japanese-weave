// Prompt lab: runs passages through the extension's own pipeline (prompt, model, validation, picking, painting) at every
// intensity level -2..3 and prints each passage woven, ⟦word or phrase⟧ and ⟪sentence⟫, in the answers the levels pick
// from, then a table per level: swaps shown a passage, the share of the passage's words in Japanese, swaps with a
// learning word, phrases and sentences, and their ratio to level 0, without and with the random thinning; the cost and
// time per call; and, with an oracle (oracle.json), coverage and wrong-sense swaps. See docs/swap-prompt.md.
//
//   GEMINI_KEY_FILE=~/secret/gemini-key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs [model] [passages.json]
//   ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key ... lab.mjs claude-haiku-5-5
//
// WORDS: the word state as the extension gets it (python tools/prompt-lab/words.py > /tmp/words.json); without it, the
// bundled Level 2 list. BATCH: passages per call, PARALLEL: calls at once (both as in the extension: 16 and 1, Haiku 6
// and 3); BATCH_FULL: passages per call for the full answer (Haiku 3, else BATCH). LEVELS: the levels to measure,
// "-2,-1,0,1,2,3" by default. As in the extension (levels.js answerLevel), one set of calls at normal serves -2, -1 and
// 0, and one at full serves +1, +2 and full, planSwaps picking each level's share. SEEDS: days averaged over for the
// picking and the thinning (200). SAVE: a file to keep the answers in; REPLAY: such a file to measure again without
// calling a model (an edited oracle, a new picking; REVALIDATE=1 checks its answers again by today's validation); an
// answer it doesn't have is asked for. ORACLE: the judged passages (below), oracle.json by default. QUIET: leave out the
// woven passages. SUMMARY: a file for the tables as JSON. Keys are read from files so they never appear in a command
// line or the shell history.
//
// The oracle, per passage id: "use", the places a listed word fits in its listed sense, "en=ja" or "en=ja|ja" (any of
// them will do), "en#1=ja" for only the second place the English occurs; "ok", swaps that are fine but not owed; "not",
// listed words whose English is there in another sense (a swap there is wrong); "sentences", the sentences listed words
// and the grammar list can say completely; "bad", the English of phrase or sentence swaps judged wrong as a whole.
// Coverage is the share of "use" places a shown swap with one of its words covers. A shown word no entry speaks for is
// printed as unjudged, to be added.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Window } from 'happy-dom';
import { requestSwaps } from '../../extension/src/engine.js';
import { planSwaps, validateSwaps, answerLevel, jitterFor, wordCount, normalizeJa, swapLimit, intensityFactor, INTENSITY_FULL } from '../../extension/src/levels.js';
import { parseSwapsResponse } from '../../extension/src/gemini.js';
import { applySwaps, flatten, locate, toNode } from '../../extension/src/text.js';
import { costUsd } from '../../extension/src/usage.js';
import { hashKey } from '../../extension/src/hash.js';
import { MAX_BATCH, BATCH as BATCHES, FULL_BATCH, PARALLEL as PARALLELS } from '../../extension/src/models.js';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const [model = 'gemini-3.8-flash', passagesPath = here('corpus.json')] = process.argv.slice(2);
const env = process.env;
const replay = env.REPLAY ? JSON.parse(readFileSync(env.REPLAY, 'utf8')) : null;
const keyFile = (name) => (env[name] ? readFileSync(env[name], 'utf8').trim() : '');
const keys = { gemini: keyFile('GEMINI_KEY_FILE'), anthropic: keyFile('ANTHROPIC_KEY_FILE') };
if (!replay && !keys.gemini && !keys.anthropic) throw new Error('set GEMINI_KEY_FILE or ANTHROPIC_KEY_FILE to a file holding the key');
const name = replay?.model || model;
const BATCH = Number(env.BATCH || BATCHES[name] || MAX_BATCH);
const BATCH_FULL = Number(env.BATCH_FULL || FULL_BATCH[name] || BATCH);
const PARALLEL = Number(env.PARALLEL || PARALLELS[name] || 1);
const LEVELS = (env.LEVELS || '-2,-1,0,1,2,3').split(',').map(Number)
  .filter((l) => Number.isInteger(l) && l >= -2 && l <= INTENSITY_FULL);
const CALLS = [...new Set(LEVELS.map(answerLevel))].sort((a, b) => a - b);
const SEEDS = Number(env.SEEDS || 200);
const state = env.WORDS ? JSON.parse(readFileSync(env.WORDS, 'utf8')) : { stage: 2, words: undefined };
const passages = JSON.parse(readFileSync(passagesPath, 'utf8'));
const oraclePath = env.ORACLE || here('oracle.json');
const oracle = existsSync(oraclePath) ? JSON.parse(readFileSync(oraclePath, 'utf8')) : {};
const doc = new Window().document;
const learning = new Set((state.words || []).filter((w) => w.status && w.status !== 'known').map((w) => w.ja));

// One answer's calls (answerLevel: 0 or full): { results, raw, calls: [{ seconds, usd, model, errors }] }.
async function ask(answer) {
  const full = answer === INTENSITY_FULL;
  const size = full ? BATCH_FULL : BATCH;
  const batches = [];
  for (let i = 0; i < passages.length; i += size) batches.push(passages.slice(i, i + size));
  const raw = {}; // every answer as it came back, before validation (Anthropic's or Google's response shape)
  const fetchImpl = async (url, init) => {
    const res = await fetch(url, init);
    const data = await res.clone().json().catch(() => null);
    const text = (data?.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('') ||
      (data?.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('');
    try { for (const [id, swaps] of parseSwapsResponse(text)) raw[id] = swaps; } catch { /* an unusable answer has none */ }
    return res;
  };
  const results = {};
  const calls = [];
  let next = 0;
  await Promise.all(Array.from({ length: PARALLEL }, async () => {
    while (next < batches.length) {
      const n = next++;
      const t = Date.now();
      let r;
      try {
        r = await requestSwaps({ keys, model, level: 2, stage: state.stage, words: state.words, shownToday: [], batch: batches[n],
          fetchImpl, full });
      } catch (e) {
        r = { results: {}, usage: e.usage || [], errors: [String(e.message || e)], model: '' };
      }
      Object.assign(results, r.results);
      calls[n] = { passages: batches[n].length, seconds: (Date.now() - t) / 1000, model: r.model, errors: r.errors,
        usd: r.usage.reduce((sum, u) => sum + costUsd(u.model, u.input, u.output, undefined, u.cached, u.written), 0),
        output: r.usage.reduce((sum, u) => sum + u.output, 0) };
    }
  }));
  for (const p of passages) results[p.id] ||= [];
  return { results, raw, calls };
}

const answers = replay?.answers || {};
// REVALIDATE: the saved answers checked again by today's validation
if (env.REVALIDATE) {
  for (const a of Object.values(answers)) for (const p of passages) a.results[p.id] = validateSwaps(2, a.raw[p.id] || [], state.words, p.text);
}
for (const answer of CALLS) if (!answers[answer]) answers[answer] = await ask(answer);
if (env.SAVE) writeFileSync(env.SAVE, JSON.stringify({ model: name, passages: passagesPath, answers }));

// Where each swap lands, as applySwaps places it: the first that fits wins, the rest of an overlap is skipped.
const flats = new Map();
function placed(p, swaps) {
  if (!flats.has(p.id)) {
    const el = doc.createElement('p');
    el.textContent = p.text;
    flats.set(p.id, flatten(el));
  }
  const flat = flats.get(p.id);
  const used = [];
  const out = [];
  for (const swap of swaps) {
    const m = locate(flat, swap.o, swap.ctx);
    if (!m || used.some((u) => m.start < u.end && u.start < m.end) || !toNode(flat, m, swap.o)) continue;
    used.push(m);
    out.push({ swap, m });
  }
  return out;
}
const wordsOf = (s) => s.words || [s.word || s.f];

// The oracle's entries for a passage, each place as a span of the passage's text.
const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// whole words, a possessive included ("grandfather" in "grandfather's"), not a contraction ("can" in "can't")
const spans = (text, en) => [...text.matchAll(new RegExp(`(?<![\\p{L}\\p{N}'’])${escape(en)}(?![\\p{L}\\p{N}]|['’](?!s\\b))`, 'giu'))]
  .map((m) => ({ start: m.index, end: m.index + m[0].length }));
const judged = new Map();
function judgedFor(p) {
  if (judged.has(p.id)) return judged.get(p.id);
  const o = oracle[p.id];
  if (!o) { judged.set(p.id, null); return null; }
  if (o.text && o.text !== p.text) console.warn(`oracle: ${p.id} has another text`);
  const parse = (entries = []) => entries.flatMap((e) => {
    const at = e.indexOf('=');
    const [en, which] = e.slice(0, at).split('#');
    const ja = e.slice(at + 1).split('|');
    const all = spans(p.text, en);
    if (!all.length) console.warn(`oracle: ${p.id}: "${en}" is not in the passage`);
    const pick = which ? which.split(',').map(Number) : all.map((_, i) => i);
    return pick.filter((i) => all[i]).map((i) => ({ key: `${en}#${i}`, en, ja, ...all[i] }));
  });
  const j = { use: parse(o.use), ok: parse(o.ok), not: parse(o.not), sentences: (o.sentences || []).map((s) => s.trim()),
    bad: new Set(o.bad || []) };
  judged.set(p.id, j);
  return j;
}
const overlaps = (a, b) => a.start < b.end && b.start < a.end;
// What the oracle says of a passage's shown swaps: the "use" places covered, and each shown word as right, fine, wrong or
// unjudged; a sentence the oracle lists as rendered whole when a sentence swap replaces it.
function judge(p, shown) {
  const j = judgedFor(p);
  if (!j) return null;
  const out = { covered: new Set(), right: 0, ok: 0, wrong: [], unjudged: [], sentences: new Set() };
  for (const { swap, m } of shown) {
    const bad = j.bad.has(swap.o);
    if (bad) out.wrong.push(`${swap.o} -> ${swap.f} (as a whole)`);
    if (swap.kind === 'sentence' && !bad && j.sentences.includes(swap.o.trim())) out.sentences.add(swap.o.trim());
    // a phrase or sentence judged right covers every place in it, also where Japanese says it without the word ("I")
    if (swap.parts && !bad) for (const e of j.use) if (overlaps(e, m)) out.covered.add(e.key);
    for (const w of wordsOf(swap)) {
      const hits = (list) => list.filter((e) => e.ja.includes(w) && overlaps(e, m));
      const use = hits(j.use);
      for (const e of use) out.covered.add(e.key);
      if (use.length) out.right++;
      else if (hits(j.ok).length) out.ok++;
      else if (hits(j.not).length) out.wrong.push(`${hits(j.not)[0].en} -> ${w}${swap.parts ? ` in ${swap.f}` : ''}`);
      else out.unjudged.push(`${w} for "${swap.o}"${swap.parts ? ` in ${swap.f}` : ''}`);
    }
  }
  return out;
}

const seedOf = (s, text) => parseInt(hashKey(`day${s}:${text}`).slice(-8), 16);
// One level, averaged over SEEDS days: per passage, the shown swaps' counts, without the random thinning (jitter 1) or
// with it, as the extension picks them.
// HEAVY_ANSWERS: +1 and +2 from answers of their own where a saved run has them, as the extension picked them before
// they shared full's (0.8.0), to measure old runs the same way.
const source = (level) => (env.HEAVY_ANSWERS && answers[level] ? answers[level] : answers[answerLevel(level)]);
function measure(level, thinning) {
  const { results } = source(level);
  const rows = [];
  for (const p of passages) {
    const sum = { shown: 0, ja: 0, learning: 0, phrases: 0, sentences: 0, limit: 0, right: 0, ok: 0, wrong: 0, covered: 0, whole: 0 };
    const memo = new Map();
    let last = null;
    for (let s = 0; s < SEEDS; s++) {
      const seed = seedOf(s, p.text);
      const plan = planSwaps(2, results[p.id], p.text, state.words, { seed, intensity: level, jitter: thinning ? jitterFor(seed) : 1 });
      const key = plan.swaps.map((x) => results[p.id].indexOf(x)).join(',');
      if (!memo.has(key)) {
        const shown = placed(p, plan.swaps);
        memo.set(key, { shown, verdict: judge(p, shown) });
      }
      const { shown, verdict } = memo.get(key);
      last = { shown, verdict };
      sum.shown += shown.length;
      // the most planSwaps would show with candidates to spare: the level's ceiling, the lighter share, the thinning
      const factor = intensityFactor(level);
      sum.limit += Math.round(swapLimit(2, p.text, 0) * Math.max(1, factor)) * Math.min(1, factor) * (thinning ? jitterFor(seed) : 1);
      for (const { swap } of shown) {
        sum.ja += wordCount(swap.o);
        sum.learning += wordsOf(swap).some((w) => learning.has(w)) ? 1 : 0;
        sum.phrases += swap.kind === 'phrase' ? 1 : 0;
        sum.sentences += swap.kind === 'sentence' ? 1 : 0;
      }
      if (verdict) {
        sum.right += verdict.right;
        sum.ok += verdict.ok;
        sum.wrong += verdict.wrong.length;
        sum.covered += verdict.covered.size;
        sum.whole += verdict.sentences.size;
      }
    }
    const row = Object.fromEntries(Object.entries(sum).map(([k, v]) => [k, v / SEEDS]));
    const j = judgedFor(p);
    rows.push({ p, ...row, words: wordCount(p.text), candidates: results[p.id].length, last,
      places: j ? j.use.length : 0, possible: j ? j.sentences.length : 0 });
  }
  return rows;
}

const fmt = (n, d = 2) => (Number.isFinite(n) ? n.toFixed(d) : '-');
const total = (rows, k) => rows.reduce((sum, r) => sum + r[k], 0);
const n = passages.length;

if (!env.QUIET) {
  for (const answer of CALLS) {
    console.log(`\n# ${answer ? "full's answer (+1, +2 and full pick from it), shown at full" : "normal's answer (-2, -1 and 0 pick from it), shown at 0"}`);
    for (const p of passages) {
      const el = doc.createElement('p');
      el.textContent = p.text;
      applySwaps(el, planSwaps(2, answers[answer].results[p.id], p.text, state.words, { seed: seedOf(0, p.text), intensity: answer }).swaps);
      const woven = [...el.childNodes].map((c) => (c.nodeType === 3 ? c.data
        : c.classList.contains('fw-group') && c.querySelector('.fw-end') ? `⟪${c.textContent}⟫` : `⟦${c.textContent}⟧`)).join('');
      console.log(`${p.id}: ${woven}`);
    }
  }
}

console.log(`\n${name}, ${passagesPath.split('/').pop()}, ${n} passages, ${total(passages.map((p) => ({ w: wordCount(p.text) })), 'w')} words; ` +
  `${SEEDS} days averaged`);
console.log('\nCalls, per call: passages, seconds (mean, max), output tokens, cost; candidates a passage as returned and valid');
console.log('| Answer | Calls | Passages | Seconds | Max s | Output tokens | $ a call | $ per 1,000 passages | Returned | Valid |');
console.log('|---|---|---|---|---|---|---|---|---|---|');
for (const answer of CALLS) {
  const { calls, raw, results } = answers[answer];
  const usd = calls.reduce((s, c) => s + c.usd, 0);
  const errors = calls.flatMap((c) => c.errors || []);
  console.log(`| ${answer ? 'full' : 'normal'} | ${calls.length} | ${fmt(n / calls.length, 1)} | ${fmt(calls.reduce((s, c) => s + c.seconds, 0) / calls.length, 1)} | ` +
    `${fmt(Math.max(...calls.map((c) => c.seconds)), 1)} | ${Math.round(calls.reduce((s, c) => s + (c.output || 0), 0) / calls.length)} | ` +
    `${fmt(usd / calls.length, 4)} | ${fmt((usd / n) * 1000, 2)} | ${fmt(passages.reduce((s, p) => s + (raw[p.id]?.length || 0), 0) / n)} | ` +
    `${fmt(passages.reduce((s, p) => s + results[p.id].length, 0) / n)} |${errors.length ? ` errors: ${errors.join('; ')}` : ''}`);
}

const measured = {};
const summary = { set: passagesPath.split('/').pop(), passages: n, calls: {}, levels: {} };
for (const answer of CALLS) {
  const { calls } = answers[answer];
  const usd = calls.reduce((s, c) => s + c.usd, 0);
  summary.calls[answer ? 'full' : 'normal'] = { calls: calls.length, seconds: calls.reduce((s, c) => s + c.seconds, 0) / calls.length,
    max: Math.max(...calls.map((c) => c.seconds)), usd, perCall: usd / calls.length, errors: calls.flatMap((c) => c.errors || []).length };
}
for (const thinning of [false, true]) {
  measured[thinning] = Object.fromEntries(LEVELS.map((l) => [l, measure(l, thinning)]));
  const base = measured[thinning][0];
  console.log(`\n${thinning ? 'With' : 'Without'} the random thinning: a passage's shown swaps, Japanese share of its words, swaps with a ` +
    'learning word, phrases, sentences; ceiling (the most planSwaps would show, given candidates to spare); ratio to level 0 of ' +
    'shown, share and ceiling');
  console.log('| Level | Shown | Share | Learning | Phrases | Sentences | Ceiling | x shown | x share | x ceiling |');
  console.log('|---|---|---|---|---|---|---|---|---|---|');
  for (const l of LEVELS) {
    const rows = measured[thinning][l];
    const share = total(rows, 'ja') / total(rows, 'words');
    const ratio = (k) => (base ? total(rows, k) / total(base, k) : NaN);
    (summary.levels[l] ||= {})[thinning ? 'thin' : 'plain'] = { shown: total(rows, 'shown') / n, share, learning: total(rows, 'learning') / n,
      phrases: total(rows, 'phrases') / n, sentences: total(rows, 'sentences') / n, xShown: ratio('shown'), xShare: ratio('ja') };
    console.log(`| ${l > 0 ? '+' : ''}${l} | ${fmt(total(rows, 'shown') / n)} | ${fmt(share * 100, 1)}% | ${fmt(total(rows, 'learning') / n)} | ` +
      `${fmt(total(rows, 'phrases') / n)} | ${fmt(total(rows, 'sentences') / n)} | ${l >= INTENSITY_FULL ? 'all' : fmt(total(rows, 'limit') / n)} | ` +
      `${fmt(ratio('shown'))} | ${fmt(ratio('ja'))} | ${l >= INTENSITY_FULL ? '-' : fmt(ratio('limit'))} |`);
  }
}

if (passages.some((p) => judgedFor(p))) {
  const rowsAt = measured[false];
  const places = total(rowsAt[LEVELS[0]], 'places');
  const possible = total(rowsAt[LEVELS[0]], 'possible');
  console.log(`\nOracle (no thinning): ${places} places a listed word fits, ${possible} sentences listed words can say whole; ` +
    'shown words right, fine, wrong; coverage of the places by what is shown and by the valid candidates');
  console.log('| Level | Places covered | Coverage | In candidates | Sentences whole | Right | Fine | Wrong | Wrong share |');
  console.log('|---|---|---|---|---|---|---|---|---|');
  for (const l of LEVELS) {
    const rows = rowsAt[l];
    // the valid candidates, all of them shown at once (where they fit), for what the model found
    let found = 0;
    for (const p of passages) {
      const v = judge(p, placed(p, planSwaps(2, source(l).results[p.id], p.text, state.words, { intensity: INTENSITY_FULL }).swaps));
      found += v ? v.covered.size : 0;
    }
    const judgedWords = total(rows, 'right') + total(rows, 'ok') + total(rows, 'wrong');
    Object.assign(summary.levels[l], { places, covered: total(rows, 'covered'), found, wrong: total(rows, 'wrong'), judged: judgedWords,
      whole: total(rows, 'whole'), possible });
    console.log(`| ${l > 0 ? '+' : ''}${l} | ${fmt(total(rows, 'covered'), 1)} | ${fmt((100 * total(rows, 'covered')) / places, 0)}% | ` +
      `${fmt((100 * found) / places, 0)}% | ${fmt(total(rows, 'whole'), 1)} of ${possible} | ${fmt(total(rows, 'right'), 1)} | ` +
      `${fmt(total(rows, 'ok'), 1)} | ${fmt(total(rows, 'wrong'), 1)} | ${fmt((100 * total(rows, 'wrong')) / judgedWords, 1)}% |`);
  }
  const top = Math.max(...LEVELS);
  const misses = new Map();
  const missedSentences = [];
  const wrong = [];
  const unjudged = new Map();
  for (const l of LEVELS) {
    for (const r of rowsAt[l]) {
      const v = judge(r.p, r.last.shown);
      if (!v) continue;
      for (const w of v.wrong) wrong.push(`${l}: ${r.p.id}: ${w}`);
      for (const u of v.unjudged) unjudged.set(`${r.p.id}: ${u}`, [...(unjudged.get(`${r.p.id}: ${u}`) || []), l]);
      if (l !== top) continue;
      const j = judgedFor(r.p);
      for (const e of j.use) {
        if (v.covered.has(e.key)) continue;
        // why: no valid candidate says it (the model's miss, or validation's drop: a returned swap with the word), or
        // one does but didn't fit on the page
        const results = source(l).results[r.p.id];
        const at = (o, ctx) => { const m = locate(flats.get(r.p.id), String(o || '').trim(), String(ctx || '').trim()); return m && overlaps(m, e); };
        const candidate = results.some((s) => wordsOf(s).some((w) => e.ja.includes(w)) && at(s.o, s.ctx));
        const returned = (source(l).raw[r.p.id] || []).some((s) => e.ja.some((ja) => normalizeJa(s.f || '').includes(normalizeJa(ja))) &&
          at(s.o, s.c ?? s.ctx));
        const why = candidate ? 'not placed' : returned ? 'dropped in validation' : 'not returned';
        const key = e.ja.join('|');
        misses.set(key, [...(misses.get(key) || []), `${r.p.id} "${e.en}" (${why})`]);
      }
      for (const s of j.sentences) if (!v.sentences.has(s)) missedSentences.push(`${r.p.id}: ${s}`);
    }
  }
  console.log(`\nMissed at ${top > 0 ? '+' : ''}${top}, by word (${[...misses.values()].flat().length}):`);
  for (const [w, list] of [...misses].sort((a, b) => b[1].length - a[1].length)) console.log(`  ${w} ${list.length}: ${list.join('; ')}`);
  console.log(`Sentences not rendered whole at ${top > 0 ? '+' : ''}${top} (${missedSentences.length}):`);
  for (const s of missedSentences) console.log(`  ${s}`);
  console.log(`Wrong-sense swaps, by level (${wrong.length}; at -2 and -1 one day's picking):`);
  for (const w of wrong) console.log(`  ${w}`);
  if (unjudged.size) {
    console.log(`Unjudged (${unjudged.size}), with the levels showing them:`);
    for (const [u, ls] of unjudged) console.log(`  ${u} [${ls.join(' ')}]`);
  }
  const sentenceSwaps = new Set();
  for (const answer of CALLS) {
    for (const p of passages) {
      const j = judgedFor(p);
      for (const s of answers[answer].results[p.id]) {
        if (s.kind === 'sentence' && j && !j.sentences.includes(s.o.trim()) && !j.bad.has(s.o)) sentenceSwaps.add(`${p.id}: ${s.o} -> ${s.f}`);
      }
    }
  }
  if (sentenceSwaps.size) {
    console.log(`Sentence swaps the oracle doesn't list (${sentenceSwaps.size}):`);
    for (const s of sentenceSwaps) console.log(`  ${s}`);
  }
}
if (env.SUMMARY) writeFileSync(env.SUMMARY, JSON.stringify(summary));
