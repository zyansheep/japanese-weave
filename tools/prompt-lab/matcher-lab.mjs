// Matcher lab: runs the code matcher (extension/src/matcher.js) over the judged passages with your words and scores it
// as check-lab.mjs scores the pipeline's swaps, against oracle.json (docs/matcher.md). No model, no network, no cost.
//
//   cp ~/.japanese-weave/state.json /tmp/state.json && WORDS=/tmp/state.json bun tools/prompt-lab/matcher-lab.mjs
//
// WORDS: a word state (a copy of state.json, or words.py's output); its introduced words (known, active, shaky) are
// the entries. ENTRIES: a JSON list of entries ({ ja, en[, pos] }) to use instead (a dictionary's, say). Passage files
// as arguments, or every set here (eval16, heldout, live, web, corpus), each passage once; only judged passages count.
// ORACLE: another oracle. MATCHER=naive: the naive baseline instead of the matcher (each gloss alternative as written,
// plus -s, -es, -ed, -d, -ing and y to -ies, whole words, longest first, no overlaps). WITHOUT=helpers,nouns: rules to
// switch off (matcher.js RULES); ABLATE=1: a line for each rule switched off on its own. EXTRA=1: the English the old
// prompt's hints (hints.js) added to a few glosses (about for について, there is for ある...) as glosses too. CHECKS=1: drop the options the
// extension's validation would drop as word swaps (numberTrap, misplaced in levels.js). VERBOSE=1: list the places
// missed, the wrong hits and the unjudged ones. TIMING=1: index build and match times, also for every word in the state
// and for 22,000 synthetic entries. SUMMARY: a file for the numbers as JSON.
//
// Scored as check-lab.mjs scores a swap: every option of every span is a hit; it covers the "use" places it overlaps whose
// Japanese includes it, and is right (it covers one), fine ("ok"), wrong ("not") or unjudged. Coverage is the share of
// "use" places covered. Wrong share: wrong / (right + fine + wrong), also split by function words (every word of the
// span closed-class, data/english.json) and content words. Intervals: 5th to 95th percentile over 2,000 resamples of
// the passages.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildIndex, matchText, RULES } from '../../extension/src/matcher.js';
import { ENGLISH } from '../../extension/src/english.js';
import { numberTrap, misplaced } from '../../extension/src/levels.js';
import { judgedPassage, oracleSpans, overlaps } from './oracle.mjs';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const env = process.env;
const files = process.argv.slice(2).length ? process.argv.slice(2) : ['eval16', 'heldout', 'live', 'web', 'corpus'].map((s) => here(`${s}.json`));
const oracle = JSON.parse(readFileSync(env.ORACLE || here('oracle.json'), 'utf8'));
const passages = [];
for (const f of files) for (const p of JSON.parse(readFileSync(f, 'utf8'))) if (!passages.some((q) => q.id === p.id)) passages.push(p);
const judged = passages.map((p) => ({ p, j: judgedPassage(p, oracle[p.id]) })).filter((x) => x.j);
const INTRODUCED = new Set(['known', 'active', 'shaky']);
const state = env.WORDS ? JSON.parse(readFileSync(env.WORDS, 'utf8')) : null;
let entries = env.ENTRIES ? JSON.parse(readFileSync(env.ENTRIES, 'utf8'))
  : (state?.words || []).filter((w) => INTRODUCED.has(w.status)).map((w) => ({ ja: w.ja, en: w.en, ...(w.pos ? { pos: w.pos } : {}) }));
if (!entries.length) throw new Error('set WORDS to a word state (a copy of state.json) or ENTRIES to a list of entries');
// the old prompt's hints' English the gloss leaves out, as more glosses
const HINT_EXTRA = { について: ['about'], ある: ['there is', 'there are', 'there was', 'there were', "there's"],
  違う: ['different', 'differently'], 自分: ['myself', 'yourself', 'himself', 'herself', 'itself', 'ourselves', 'yourselves', 'themselves'],
  時間: ['hour'] };
if (env.EXTRA) entries = entries.map((e) => (HINT_EXTRA[e.ja] ? { ...e, en: [String(e.en), ...HINT_EXTRA[e.ja]].join(' / ') } : e));

const FUNCTION = new Set(ENGLISH.function);
// The naive baseline: each gloss alternative as written and with -s, -es, -ed, -d, -ing (y -> -ies), whole words as
// the oracle's, the longest first; a match overlapping a longer one is dropped, one on the same place adds an option.
function naiveIndex(list) {
  const forms = [];
  list.forEach((entry, idx) => {
    for (let g of String(entry.en).toLowerCase().split('/')) {
      g = g.trim();
      if (!g) continue;
      const all = new Set([g, `${g}s`, `${g}es`, `${g}ed`, `${g}d`, `${g}ing`, ...(g.endsWith('y') ? [`${g.slice(0, -1)}ies`] : [])]);
      for (const f of all) forms.push({ f, idx });
    }
  });
  return { entries: list, forms };
}
function naiveMatch({ entries: list, forms }, text) {
  const hits = forms.flatMap(({ f, idx }) => oracleSpans(text, f).map((s) => ({ ...s, idx })));
  hits.sort((a, b) => (b.end - b.start) - (a.end - a.start) || a.start - b.start || a.idx - b.idx);
  const spans = [];
  for (const h of hits) {
    const same = spans.find((s) => s.start === h.start && s.end === h.end);
    if (same) { if (!same.idx.includes(h.idx)) same.idx.push(h.idx); continue; }
    if (!spans.some((s) => overlaps(s, h))) spans.push({ start: h.start, end: h.end, idx: [h.idx] });
  }
  return spans.sort((a, b) => a.start - b.start).map((s) => {
    const english = text.slice(s.start, s.end);
    const words = english.toLowerCase().replace(/’/g, "'").replace(/'s$/, '').split(/\s+/);
    return { start: s.start, end: s.end, english, options: s.idx.sort((a, b) => a - b).map((i) => list[i]),
      function: words.every((w) => FUNCTION.has(w)) };
  });
}

const matcher = (without) => (env.MATCHER === 'naive'
  ? ((ix) => (text) => naiveMatch(ix, text))(naiveIndex(entries))
  : ((ix) => (text) => matchText(ix, text))(buildIndex(entries, { without })));

// One passage's spans judged: per hit (span and option) its verdict; the "use" places covered.
function judge({ p, j }, spans) {
  const out = { covered: new Set(), hits: [], spans: [] };
  for (const s of spans) {
    let options = s.options;
    if (env.CHECKS) options = options.filter((e) => { const swap = { o: s.english, f: e.ja, word: e.ja }; return !numberTrap(swap, p.text) && !misplaced(swap, p.text); });
    const verdicts = [];
    for (const e of options) {
      const on = (list) => list.filter((x) => x.ja.includes(e.ja) && overlaps(x, s));
      const use = on(j.use);
      for (const x of use) out.covered.add(x.key);
      const verdict = use.length ? 'right' : on(j.ok).length ? 'ok' : on(j.not).length ? 'wrong' : 'unjudged';
      verdicts.push(verdict);
      out.hits.push({ verdict, function: s.function, text: `${p.id}: ${s.english} -> ${e.ja}` });
    }
    if (verdicts.length) out.spans.push({ function: s.function, verdict: ['right', 'ok', 'wrong', 'unjudged'].find((v) => verdicts.includes(v)) });
  }
  return out;
}

// The numbers over a set of judged passages (rows: one per passage).
function tally(rows) {
  const t = { places: 0, covered: 0, entries: 0, entriesCovered: 0, spans: 0, options: 0, right: 0, ok: 0, wrong: 0, unjudged: 0,
    fn: { right: 0, ok: 0, wrong: 0, unjudged: 0 }, content: { right: 0, ok: 0, wrong: 0, unjudged: 0 },
    spanVerdicts: { right: 0, ok: 0, wrong: 0, unjudged: 0 }, placesHit: { use: 0, ok: 0, not: 0 } };
  for (const r of rows) {
    t.places += r.j.use.length;
    t.covered += r.v.covered.size;
    t.entries += r.entryKeys.length;
    t.entriesCovered += r.entryKeys.filter((keys) => keys.some((k) => r.v.covered.has(k))).length;
    t.spans += r.v.spans.length;
    for (const s of r.v.spans) t.spanVerdicts[s.verdict]++;
    for (const h of r.v.hits) {
      t.options++;
      t[h.verdict]++;
      t[h.function ? 'fn' : 'content'][h.verdict]++;
    }
    for (const k of ['use', 'ok', 'not']) t.placesHit[k] += r.placesHit[k];
  }
  const share = (x) => x.wrong / (x.right + x.ok + x.wrong);
  return { ...t, coverage: t.covered / t.places, wrongShare: share(t), fnShare: share(t.fn), contentShare: share(t.content),
    placeWrongShare: t.placesHit.not / (t.placesHit.use + t.placesHit.ok + t.placesHit.not) };
}

function run(without) {
  const match = matcher(without);
  return judged.map(({ p, j }) => {
    const spans = match(p.text);
    const v = judge({ p, j }, spans);
    // the reviewers' count: each "use" entry once (covered at any of its places), and each judged place hit by an
    // option it names
    const entryKeys = Object.values(Object.groupBy(j.use, (e) => `${e.en}=${e.ja.join('|')}`)).map((list) => list.map((e) => e.key));
    const placesHit = Object.fromEntries(['use', 'ok', 'not'].map((k) => [k, j[k].filter((x) => spans.some((s) => overlaps(x, s) &&
      s.options.some((e) => x.ja.includes(e.ja)))).length]));
    return { p, j, spans, v, entryKeys, placesHit };
  });
}

// 5th and 95th percentiles of f over resamples of the passages (a fixed seed, so runs agree)
function interval(rows, f, n = 2000) {
  let seed = 12345;
  const rand = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
  const values = [];
  for (let b = 0; b < n; b++) values.push(f(tally(rows.map(() => rows[Math.floor(rand() * rows.length)]))));
  values.sort((a, b) => a - b);
  return [values[Math.floor(0.05 * n)], values[Math.floor(0.95 * n)]];
}

const pct = (x, d = 1) => (Number.isFinite(x) ? `${(100 * x).toFixed(d)}%` : '-');
const span = ([lo, hi]) => `${pct(lo, 0)}-${pct(hi, 0)}`;
const label = env.MATCHER === 'naive' ? 'naive baseline' : `matcher${env.WITHOUT ? ` without ${env.WITHOUT}` : ''}`;
const without = (env.WITHOUT || '').split(',').filter(Boolean);
const rows = run(without);
const t = tally(rows);
console.log(`${label}: ${entries.length} entries${env.EXTRA ? ' (with hints.js extras)' : ''}${env.CHECKS ? ', after the extension\'s checks' : ''}; ` +
  `${judged.length} judged passages, ${t.places} "use" places (${t.entries} entries)`);
console.log('\n| | Count | Share | 90% interval |');
console.log('|---|---|---|---|');
console.log(`| "use" places covered | ${t.covered} of ${t.places} | ${pct(t.coverage)} | ${span(interval(rows, (x) => x.coverage))} |`);
console.log(`| "use" entries covered (each once) | ${t.entriesCovered} of ${t.entries} | ${pct(t.entriesCovered / t.entries)} | |`);
console.log(`| hits (span x option), judged | ${t.right + t.ok + t.wrong} of ${t.options} | | |`);
console.log(`| right / fine / wrong | ${t.right} / ${t.ok} / ${t.wrong} | wrong ${pct(t.wrongShare)} | ${span(interval(rows, (x) => x.wrongShare))} |`);
console.log(`| function words: right / fine / wrong | ${t.fn.right} / ${t.fn.ok} / ${t.fn.wrong} | wrong ${pct(t.fnShare)} | ${span(interval(rows, (x) => x.fnShare))} |`);
console.log(`| content words: right / fine / wrong | ${t.content.right} / ${t.content.ok} / ${t.content.wrong} | wrong ${pct(t.contentShare)} | ${span(interval(rows, (x) => x.contentShare))} |`);
console.log(`| unjudged hits (function, content) | ${t.unjudged} (${t.fn.unjudged}, ${t.content.unjudged}) | | |`);
console.log(`| spans; options a span | ${t.spans} | ${(t.options / t.spans).toFixed(2)} | |`);
console.log(`| spans with a right option / only fine / only wrong / unjudged | ${t.spanVerdicts.right} / ${t.spanVerdicts.ok} / ${t.spanVerdicts.wrong} / ${t.spanVerdicts.unjudged} | | |`);
console.log(`| judged places hit: use / ok / not (reviewers' count) | ${t.placesHit.use} / ${t.placesHit.ok} / ${t.placesHit.not} | not ${pct(t.placeWrongShare)} | |`);

// the English behind the wrong hits, most first
const wrongBy = new Map();
for (const r of rows) for (const h of r.v.hits) if (h.verdict === 'wrong') {
  const en = h.text.split(': ')[1].split(' -> ')[0].toLowerCase();
  wrongBy.set(en, (wrongBy.get(en) || 0) + 1);
}
const byCount = [...wrongBy].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
console.log(`\nWrong hits by English: ${byCount.slice(0, 25).map(([en, n]) => `${en} ${n}`).join(', ')}` +
  `${byCount.length > 25 ? `, and ${byCount.length - 25} more with ${byCount.slice(25).reduce((s, [, n]) => s + n, 0)}` : ''}`);

const summary = { label, entries: entries.length, passages: judged.length, ...t, wrongBy: Object.fromEntries(byCount) };
if (env.ABLATE && env.MATCHER !== 'naive') {
  console.log('\nEach rule switched off on its own (the others on):');
  console.log('| Without | Covered | Coverage | Right / fine / wrong | Wrong share | Function | Content | Unjudged | Spans |');
  console.log('|---|---|---|---|---|---|---|---|---|');
  const line = (name, x) => console.log(`| ${name} | ${x.covered} | ${pct(x.coverage)} | ${x.right} / ${x.ok} / ${x.wrong} | ${pct(x.wrongShare)} | ` +
    `${pct(x.fnShare)} | ${pct(x.contentShare)} | ${x.unjudged} | ${x.spans} |`);
  line('(none)', t);
  summary.ablation = {};
  for (const rule of RULES) line(rule, (summary.ablation[rule] = tally(run([...without, rule]))));
  line('all', (summary.ablation.all = tally(run(RULES))));
}

if (env.VERBOSE) {
  console.log('\nMissed "use" places:');
  for (const r of rows) for (const e of r.j.use) if (!r.v.covered.has(e.key)) console.log(`  ${r.p.id}: ${e.en}=${e.ja.join('|')}`);
  for (const verdict of ['wrong', 'unjudged']) {
    console.log(`\n${verdict === 'wrong' ? 'Wrong' : 'Unjudged'} hits:`);
    for (const r of rows) for (const h of r.v.hits) if (h.verdict === verdict) console.log(`  ${h.text}${h.function ? ' (function)' : ''}`);
  }
}

if (env.TIMING) {
  const ms = (f) => { const t0 = performance.now(); const out = f(); return [performance.now() - t0, out]; };
  const timeMatch = (index) => {
    for (const { p } of judged) matchText(index, p.text); // warm up
    const times = judged.map(({ p }) => ms(() => matchText(index, p.text))[0]).sort((a, b) => a - b);
    return `match ${(times.reduce((a, b) => a + b, 0) / times.length).toFixed(3)} ms a passage (median ${times[times.length >> 1].toFixed(3)}, max ${times.at(-1).toFixed(3)})`;
  };
  console.log('\nTiming (Bun, one run):');
  const sets = [['introduced words', entries]];
  if (state?.words) sets.push(['every word in the state', state.words.map((w) => ({ ja: w.ja, en: w.en }))]);
  sets.push(['22,000 synthetic entries', synthetic(22000)]);
  for (const [name, list] of sets) {
    buildIndex(list.slice(0, 50)); // warm up
    const [build, index] = ms(() => buildIndex(list));
    console.log(`  ${name} (${list.length}): build ${build.toFixed(1)} ms; ${timeMatch(index)}`);
  }
}

// n entries with English drawn from the lexicon's gloss words: one to three alternatives of one to three words, a third
// of them verbs, a third adjectives, a third nouns (by their Japanese ending). Deterministic.
export function synthetic(n) {
  const lexicon = readFileSync(here('../../data/lexicon.csv'), 'utf8').split('\n').slice(1);
  const vocab = [...new Set(lexicon.flatMap((line) => (line.split(',')[1] || '').replace(/"/g, '').toLowerCase().match(/[a-z']+/g) || []))];
  let seed = 1;
  const rand = (k) => (seed = (seed * 48271) % 2147483647) % k;
  const out = [];
  for (let i = 0; i < n; i++) {
    const alts = Array.from({ length: 1 + rand(3) }, () => Array.from({ length: 1 + (rand(4) === 0 ? 1 + rand(2) : 0) }, () => vocab[rand(vocab.length)]).join(' '));
    out.push({ ja: `語${i}${['る', 'い', ''][i % 3]}`, en: alts.join(' / ') });
  }
  return out;
}

if (env.SUMMARY) writeFileSync(env.SUMMARY, JSON.stringify(summary, null, 1));
