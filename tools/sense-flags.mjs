// Sense flags (PLAN.md section 6, NEXT_STEPS step 12; docs/matcher.md, "Sense flags"): each pair of an entry and one
// of its English glosses is safe or doubtful. Safe: wherever that English appears in ordinary prose, in any of its
// forms, this Japanese word renders it correctly almost always (at least 95 times in 100), so code may swap it without
// reading the sentence. Doubtful: anything else; a model reads the sentence first (extension/src/verify.js). A gloss
// made only of closed-class English (data/english.json "function": I, you, so, about, one...) is always doubtful and
// never asked about. Everything else is asked of a model once, in calls of about BATCH pairs, and the answers are kept
// in a cache, so a rerun costs nothing; the flags go to data/senses.tsv, read by extension/src/senses.js and
// scripts/senses.py. A pair the file doesn't have is doubtful.
//
//   SPEND_LOG=/tmp/spend.jsonl ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key WORDS=/tmp/state.json \
//     bun tools/sense-flags.mjs
//
// WORDS: a copy of a word state: its words' glosses as listed and their dictionary entries' glosses (INTRODUCED=1:
// only known, active and shaky words). ALL=1: also every swappable dictionary entry. DRY=1: count the pairs and
// estimate the cost from the cache's calls so far, without calling. MODEL (claude-haiku-5-5), BATCH (80 pairs a call),
// PARALLEL (4 calls at once), THINKING=0 (no thinking). CACHE: the answers (default
// ~/.cache/japanese-weave-build/senses-<prompt version>-<model><-nothink>.jsonl). OUT: the flags file (data/senses.tsv);
// NOWRITE=1 leaves it alone. Every call's tokens and cost go to SPEND_LOG (tools/prompt-lab/spend.mjs), which also
// holds the cap (SPEND_CAP, $5).
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse as parseDictionary } from '../extension/src/dictionary.js';
import { glossAlternatives } from '../extension/src/matcher.js';
import { askClaude } from '../extension/src/anthropic.js';
import { costUsd } from '../extension/src/usage.js';
import { functionGloss, toHiragana, formatSenses, parseSenses } from '../extension/src/senses.js';
import { spendLog, meter } from './prompt-lab/spend.mjs';

// Bumped whenever the prompt or the answer's shape changes: the cache and the flags file name it. PROMPT=1 reruns an
// older one. Prompt 1 judged each pair from the entry's senses alone; its flags changed between two runs for common
// words (時 "time", 行く "go", 仕事 "work" safe in one, doubtful in the other), and 17 of 140 judged safe hits in the
// lab were wrong. Prompt 2 also shows, for each gloss, the other common words that share that English (docs/matcher.md).
export const FLAG_VERSION = 2;

const FLAG_RULES = `You sort English-Japanese word pairs for a browser extension that replaces English words on web pages with Japanese words the reader is learning. For SAFE pairs, code makes the swap wherever the English occurs, without reading the sentence. For DOUBTFUL pairs, a model reads each sentence first.

A pair is SAFE when, wherever that English word or phrase appears in ordinary English prose (news, social media, comments, blogs, shops), in any of its forms (plural, past, -ing, -er, -est), this Japanese word says the same thing at least 95 times in 100: a reader of the sentence with that one word swapped would understand what the English says.

It is DOUBTFUL when, counting how often each use turns up in real text (not whether it exists at all), more than about 1 in 20 uses mean something this Japanese word doesn't:
- another sense of the English: "play" is 遊ぶ for children at play, but also play the piano, play a role, a play; "run" is 走る, but also run a company or a script; "light" is 光, but also light as in not heavy; "date" is 日付, but also a romantic date;
- phrasal verbs and set phrases that change the meaning: give up, look up, turn out, make up, take off, figure out;
- a different word class: "need" is often a verb where the Japanese is a noun or adjective only, or the reverse, when the swap would read wrongly;
- the Japanese is narrower or wider than the English: "brother" as 兄 (older brothers only), "rice" as ご飯 (also any meal);
- a rare, slang or old sense of the Japanese word: "spy" as 犬 is doubtful, though "dog" as 犬 is safe;
- names, brands and titles with the same spelling (Apple, Will, May, Bill).

Safe, for comparison: dog 犬, Tuesday 火曜日, library 図書館, delicious 美味しい, cheap 安い, slowly ゆっくり, to swim 泳ぐ, homework 宿題.

When unsure, choose doubtful: a doubtful pair is still used once a model has read its sentence, while a wrong safe pair puts a wrong word on the page.`;
export const FLAG_PROMPTS = {
  1: `${FLAG_RULES}

Each entry is a Japanese word with its reading, its parts of speech (JMdict codes) and its English senses (senses separated by |), followed by numbered English words or phrases to judge for it. Answer with the numbers of the safe pairs and of the doubtful pairs; every number in exactly one list.`,
  2: `${FLAG_RULES}

After an English word, "also" lists other common Japanese words that have the same English among their meanings, the most frequent first. Where some of them are the usual translation in other uses ("time" is 時間 for an amount of time, 回 for "three times", 時 for "when"), the pair is doubtful. It is safe only where this word is the usual translation in almost every use; a katakana loanword, a formal synonym or a rare word alongside it doesn't make it doubtful by itself.

Each entry is a Japanese word with its reading, its parts of speech (JMdict codes) and its English senses (senses separated by |), followed by numbered English words or phrases to judge for it, each perhaps with its "also". Answer with the numbers of the safe pairs and of the doubtful pairs; every number in exactly one list.`,
};

export const FLAG_SCHEMA = {
  type: 'object',
  properties: {
    safe: { type: 'array', items: { type: 'integer' } },
    doubtful: { type: 'array', items: { type: 'integer' } },
  },
  required: ['safe', 'doubtful'],
};

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const PARTS = { n: 'n', v: 'v', adj: 'adj', adv: 'adv', exp: 'exp', conj: 'conj', int: 'int', pn: 'pn', pref: 'pref', suf: 'suf', ctr: 'ctr', prt: 'prt', aux: 'aux' };
const posLabel = (pos) => [...new Set(pos.map((p) => PARTS[p] || PARTS[p.split('-')[0]] || p))].slice(0, 4).join(',');

// The pairs to judge: { key, id, ja, kana, gloss, entry }, entry: { ja, kana, pos, senses }, grouped by entry in rank
// order. From the word state's words (their glosses as listed, and their dictionary entry's) and, with all, every
// swappable entry. Closed-class glosses are counted, not judged.
export function pairsFor({ dictionary, words = [], all = false }) {
  const byJa = new Map();
  for (const e of dictionary) {
    for (const f of [e.ja, ...e.forms]) {
      if (!byJa.has(f)) byJa.set(f, []);
      byJa.get(f).push(e);
    }
  }
  const entryOf = (w) => {
    const same = (byJa.get(w.ja) || []);
    return same.find((e) => e.ja === w.ja && (!w.kana || e.kana === toHiragana(w.kana))) || same.find((e) => e.ja === w.ja) || same[0] || null;
  };
  const groups = new Map(); // `${ja}\t${kana}` -> { id, ja, kana, pos, senses, rank, glosses: Set }
  const add = (key, base, glosses) => {
    if (!groups.has(key)) groups.set(key, { ...base, glosses: new Set() });
    for (const g of glosses) groups.get(key).glosses.add(g);
  };
  const fromEntry = (e) => ({ id: e.id, ja: e.ja, kana: e.kana, pos: e.pos, senses: e.glosses, rank: e.rank });
  for (const w of words) {
    const e = entryOf(w);
    const listed = glossAlternatives(w.en);
    if (e) {
      // the word as the word state writes it, its dictionary entry's senses (and its listed gloss as one more, where
      // the dictionary's don't say it)
      const own = e.glosses.flatMap((s) => glossAlternatives(s));
      const extra = listed.filter((g) => !own.includes(g));
      add(`${w.ja}\t${toHiragana(w.kana || e.kana)}`, { ...fromEntry(e), ja: w.ja, kana: toHiragana(w.kana || e.kana),
        senses: extra.length ? [...e.glosses, extra] : e.glosses }, [...listed, ...own]);
    } else {
      add(`${w.ja}\t${toHiragana(w.kana || '')}`, { id: null, ja: w.ja, kana: toHiragana(w.kana || ''), pos: [], senses: [listed], rank: Infinity }, listed);
    }
  }
  if (all) for (const e of dictionary) if (e.swap) add(`${e.ja}\t${e.kana}`, fromEntry(e), e.glosses.flatMap((s) => glossAlternatives(s)));
  // English -> the ranked entries with it among their glosses, most frequent first, for the "also" of prompt 2
  const byGloss = new Map();
  for (const e of dictionary) {
    if (e.score === null) continue;
    for (const g of new Set(e.glosses.flatMap((sense) => glossAlternatives(sense)))) {
      if (!byGloss.has(g)) byGloss.set(g, []);
      byGloss.get(g).push(e);
    }
  }
  const out = { judge: [], closed: [] };
  for (const g of [...groups.values()].sort((a, b) => a.rank - b.rank || a.ja.localeCompare(b.ja))) {
    const entry = { id: g.id, ja: g.ja, kana: g.kana, pos: g.pos, senses: g.senses };
    for (const gloss of g.glosses) {
      const others = (byGloss.get(gloss) || []).filter((e) => e.id !== g.id && e.ja !== g.ja);
      const pair = { key: `${g.ja}\t${g.kana}\t${gloss}`, id: g.id, ja: g.ja, kana: g.kana, gloss, entry,
        also: others.slice(0, ALSO).map((e) => `${e.ja} (${e.glosses[0].slice(0, 2).join('; ')})`), more: Math.max(0, others.length - ALSO) };
      out[functionGloss(gloss) ? 'closed' : 'judge'].push(pair);
    }
  }
  return out;
}
const ALSO = 4;

// A call's user message: each entry once, then its pairs numbered from 1 across the call, from prompt 2 each with the
// other words that share its English ("also").
export function flagContent(pairs, version = FLAG_VERSION) {
  const lines = [];
  let last = null;
  pairs.forEach((p, i) => {
    if (p.entry !== last) {
      last = p.entry;
      const senses = p.entry.senses.map((s) => s.join('; ')).join(' | ');
      lines.push(`${p.ja} (${p.kana}${p.entry.pos.length ? `; ${posLabel(p.entry.pos)}` : ''}): ${senses}`);
    }
    const also = version >= 2 && p.also.length ? ` (also: ${p.also.join(', ')}${p.more ? ` and ${p.more} more` : ''})` : '';
    lines.push(`  ${i + 1} ${p.gloss}${also}`);
  });
  return `Pairs:\n${lines.join('\n')}`;
}

// The answer: { safe: [...], doubtful: [...] } -> flag per pair number ('s', 'd'); a number in both or neither is
// doubtful (null for neither, so it can be counted).
export function parseFlags(text, count) {
  const data = JSON.parse(text);
  const safe = new Set((data?.safe || []).map(Number));
  const doubtful = new Set((data?.doubtful || []).map(Number));
  return Array.from({ length: count }, (_, i) => (safe.has(i + 1) && !doubtful.has(i + 1) ? 's' : doubtful.has(i + 1) ? 'd' : null));
}

// Calls of about size pairs, an entry never split between two.
export function batches(pairs, size) {
  const out = [];
  let cur = [];
  for (let i = 0; i < pairs.length; i++) {
    cur.push(pairs[i]);
    const endOfEntry = pairs[i + 1]?.entry !== pairs[i].entry;
    if (cur.length >= size && endOfEntry) { out.push(cur); cur = []; }
  }
  if (cur.length) out.push(cur);
  return out;
}

// The order a sample asks the pairs in: the first in rank order, each other one with the entries shuffled by its
// number, so the same pair is judged among other neighbours.
function sampleOrder(pairs, sample) {
  if (sample === 1) return pairs;
  const groups = [];
  for (const p of pairs) (groups.at(-1)?.[0].entry === p.entry ? groups.at(-1) : groups[groups.push([]) - 1]).push(p);
  let seed = sample * 7919 + 13;
  const rand = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
  for (let i = groups.length - 1; i > 0; i--) { const k = Math.floor(rand() * (i + 1)); [groups[i], groups[k]] = [groups[k], groups[i]]; }
  return groups.flat();
}

async function main() {
  const env = process.env;
  const model = env.MODEL || 'claude-haiku-5-5';
  const thinking = env.THINKING !== '0';
  const version = Number(env.PROMPT || FLAG_VERSION);
  const samples = (env.SAMPLES || '1').split(',').map(Number);
  const size = Number(env.BATCH || 80);
  const parallel = Number(env.PARALLEL || 4);
  const base = env.CACHE || `${homedir()}/.cache/japanese-weave-build/senses-${version}-${model}${thinking ? '' : '-nothink'}`;
  const cachePath = (sample) => `${base}${sample > 1 ? `-s${sample}` : ''}.jsonl`;
  const outPath = env.OUT || here('../data/senses.tsv');
  const header = { version, model, thinking, samples: samples.length };
  const dictionary = parseDictionary(readFileSync(here('../data/dictionary.tsv'), 'utf8'));
  const state = env.WORDS ? JSON.parse(readFileSync(env.WORDS, 'utf8')) : { words: [] };
  const words = (state.words || []).filter((w) => !env.INTRODUCED || ['known', 'active', 'shaky'].includes(w.status));
  const { judge, closed } = pairsFor({ dictionary, words, all: !!env.ALL });

  // each sample's answers so far: key -> flag
  const caches = new Map(samples.map((n) => [n, new Map()]));
  const callsSoFar = [];
  for (const n of samples) {
    mkdirSync(dirname(cachePath(n)), { recursive: true });
    if (!existsSync(cachePath(n))) continue;
    for (const line of readFileSync(cachePath(n), 'utf8').split('\n').filter(Boolean)) {
      const r = JSON.parse(line);
      if (r.key) caches.get(n).set(r.key, r.flag);
      if (r.call) callsSoFar.push(r.call);
    }
  }
  // the flags file counts as a cache too, when the same prompt, model and samples wrote it
  const final = new Map();
  if (existsSync(outPath)) {
    const text = readFileSync(outPath, 'utf8');
    if (text.startsWith(formatSenses([], header).split('\n')[0])) {
      for (const [key, rows] of parseSenses(text)) {
        const [ja, gloss] = key.split('\t');
        for (const r of rows) final.set(`${ja}\t${r.kana}\t${gloss}`, r.flag);
      }
    }
  }
  const todo = new Map(samples.map((n) => [n, sampleOrder(judge, n).filter((p) => !final.has(p.key) && !caches.get(n).has(p.key))]));
  const asks = [...todo.values()].reduce((a, l) => a + l.length, 0);
  const perPair = callsSoFar.length ? callsSoFar.reduce((a, c) => a + c.usd, 0) / callsSoFar.reduce((a, c) => a + c.pairs, 0) : null;
  console.log(`prompt ${version}, ${samples.length} sample${samples.length > 1 ? 's' : ''}; ${words.length} words${env.ALL ? ' and every swappable entry' : ''}: ` +
    `${judge.length + closed.length} pairs, ${closed.length} closed-class (doubtful, not asked), ${judge.length} to judge, ${asks} to ask` +
    (perPair ? `; about $${(perPair * asks).toFixed(3)} at $${(perPair * 1000).toFixed(4)} per 1,000 pairs so far` : ''));

  if (asks && !env.DRY) {
    const key = readFileSync(env.ANTHROPIC_KEY_FILE || '', 'utf8').trim();
    if (!key) throw new Error('set ANTHROPIC_KEY_FILE to a file holding the key');
    const log = spendLog();
    const queue = samples.flatMap((n) => batches(todo.get(n), size).map((batch) => ({ n, batch })));
    let next = 0;
    let done = 0;
    let stop = false;
    await Promise.all(Array.from({ length: parallel }, async () => {
      while (next < queue.length && !stop) {
        const { n, batch } = queue[next++];
        let usage = null;
        const t = Date.now();
        const fetchImpl = meter(log, 'sense-flags', { label: () => `prompt ${version} sample ${n}, ${batch.length} pairs`, onUsage: (u) => { usage = u; } });
        try {
          const { byId: flags } = await askClaude({ fetchImpl, apiKey: key, model, system: FLAG_PROMPTS[version], user: flagContent(batch, version),
            schema: FLAG_SCHEMA, timeoutMs: 120000, thinking, parse: (text) => parseFlags(text, batch.length) });
          const lines = batch.map((p, i) => JSON.stringify({ key: p.key, flag: flags[i] || 'd', ...(flags[i] ? {} : { unanswered: true }) }));
          const usd = costUsd(model, usage.input, usage.output, undefined, usage.cached, usage.written);
          lines.push(JSON.stringify({ call: { pairs: batch.length, usd, seconds: (Date.now() - t) / 1000, ...usage, unanswered: flags.filter((f) => !f).length } }));
          appendFileSync(cachePath(n), `${lines.join('\n')}\n`);
          batch.forEach((p, i) => caches.get(n).set(p.key, flags[i] || 'd'));
          done += batch.length;
          process.stderr.write(`\r${done} of ${asks} asked, $${log.total().toFixed(4)} in the spend log`);
        } catch (e) {
          console.error(`\na call of ${batch.length} pairs failed: ${e.message}`);
          if (/spend cap/.test(e.message)) stop = true;
        }
      }
    }));
    process.stderr.write('\n');
  }

  // a pair is safe when every sample says so; it is written once every sample has judged it
  for (const p of judge) {
    if (final.has(p.key) || !samples.every((n) => caches.get(n).has(p.key))) continue;
    final.set(p.key, samples.every((n) => caches.get(n).get(p.key) === 's') ? 's' : 'd');
  }
  const flagged = judge.filter((p) => final.has(p.key));
  const safe = flagged.filter((p) => final.get(p.key) === 's').length;
  console.log(`judged ${flagged.length} of ${judge.length}: ${safe} safe (${((100 * safe) / Math.max(1, flagged.length)).toFixed(1)}%), ` +
    `${flagged.length - safe} doubtful`);
  if (samples.length > 1) {
    const both = judge.filter((p) => samples.every((n) => caches.get(n).has(p.key)));
    const agree = both.filter((p) => new Set(samples.map((n) => caches.get(n).get(p.key))).size === 1).length;
    const each = samples.map((n) => `${both.filter((p) => caches.get(n).get(p.key) === 's').length}`).join(', ');
    console.log(`samples agree on ${agree} of ${both.length} pairs (${((100 * agree) / Math.max(1, both.length)).toFixed(1)}%); safe in each: ${each}`);
  }
  if (!env.NOWRITE && !env.DRY) {
    const rankOf = new Map(dictionary.map((e) => [e.id, e.rank]));
    const rows = flagged.map((p) => ({ id: p.id, ja: p.ja, kana: p.kana, gloss: p.gloss, flag: final.get(p.key), rank: rankOf.get(p.id) ?? Infinity }));
    writeFileSync(outPath, formatSenses(rows, header));
    console.log(`wrote ${rows.length} pairs to ${outPath}`);
  }
}

if (import.meta.main) await main();
