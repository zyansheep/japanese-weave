// Worker lab: the judged passages through the extension's own worker (extension/src/background.js), as pages ask it:
// up to 16 passages a message (content.js), the swaps code is sure of at once, then a wait for the model's check,
// which the worker batches, calls and caches as it does live; then full and normal picked from the same answers by
// the worker's planSwaps. Scored against an oracle as check-lab.mjs scores its own run of the pipeline, so the two can
// be compared (docs/swap-prompt.md, "The web pipeline").
//
//   python tools/prompt-lab/words.py <copy of state.json> > /tmp/words.json        # the host's reply for those words
//   SPEND_LOG=<log> SPEND_CAP=0.5 ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key WORDS=/tmp/words.json \
//     ORACLE=tools/prompt-lab/fresh2-oracle.json bun tools/prompt-lab/worker-lab.mjs tools/prompt-lab/fresh2.json
//
// WORDS: the native host's state reply (words.py). MODEL: the popup's model (claude-haiku-5-5). Without
// ANTHROPIC_KEY_FILE (or GEMINI_KEY_FILE for a Gemini model) no call is made: the safe swaps alone. Every call's
// tokens and cost go to SPEND_LOG (spend.mjs), which holds SPEND_CAP. VERBOSE=1: the wrong and unjudged hits. SAVE: a
// file for the worker's cached answers in check-lab.mjs's form, so `REPLAY=<file> WORDS=<the same words.json>
// check-lab.mjs` scores the very same answers its own way (every candidate, full, and normal without the thinning).
// The key goes to the worker as the host would pass it, and is never printed.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { hashKey } from '../../extension/src/hash.js';
import { pipelineFor, planText, answerKey, loadAnswer } from '../../extension/src/pipeline.js';
import { buildIndex, matchText } from '../../extension/src/matcher.js';
import { parseSenses } from '../../extension/src/senses.js';
import { planPassage } from '../../extension/src/verify.js';
import { judgedPassage } from './oracle.mjs';
import { placeSwaps, judgeSwaps } from './judge.mjs';
import { spendLog, meter } from './spend.mjs';

const here = (name) => fileURLToPath(new URL(name, import.meta.url));
const env = process.env;
const files = process.argv.slice(2);
if (!files.length || !env.WORDS) throw new Error('give passage files and WORDS (words.py output)');
const oracle = JSON.parse(readFileSync(env.ORACLE || here('oracle.json'), 'utf8'));
const passages = files.flatMap((f) => JSON.parse(readFileSync(f, 'utf8')));
const judged = passages.map((p) => ({ p, j: judgedPassage(p, oracle[p.id]) })).filter((x) => x.j);
const reply = JSON.parse(readFileSync(env.WORDS, 'utf8'));
const model = env.MODEL || 'claude-haiku-5-5';
const keyFile = model.startsWith('claude-') ? env.ANTHROPIC_KEY_FILE : env.GEMINI_KEY_FILE;
const apiKey = keyFile ? readFileSync(keyFile, 'utf8').trim() : '';

// The browser around the worker: storage, the native host (this reply, the key as setup would pass it), no tabs.
const local = { settings: { enabled: true, newSitesEnabled: true, model } };
const listeners = [];
const listener = (arr) => ({ addListener: (fn) => arr.push(fn) });
const calls = [];
globalThis.chrome = {
  storage: { local: {
    async setAccessLevel() {},
    async get(keys) { return structuredClone(keys == null ? local : Object.fromEntries((typeof keys === 'string' ? [keys] : keys).filter((k) => k in local).map((k) => [k, local[k]]))); },
    async set(values) { for (const [k, v] of Object.entries(values)) local[k] = structuredClone(v); },
    async remove(keys) { for (const k of keys) delete local[k]; },
  } },
  runtime: { id: 'lab', getURL: (path) => `chrome-extension://lab/${path}`, onMessage: listener(listeners), onInstalled: listener([]),
    onStartup: listener([]),
    sendNativeMessage: async (_host, msg) => (msg.type === 'state'
      ? { ...reply, ok: true, ...(apiKey ? { [model.startsWith('claude-') ? 'anthropicKey' : 'geminiKey']: apiKey } : {}) }
      : { ok: msg.type !== 'readingIndex' }) },
  tabs: { query: async () => [], sendMessage: async () => {} },
  alarms: { create: async () => {}, clear: async () => {}, onAlarm: listener([]) },
  action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {}, setIcon: async () => {}, setTitle: async () => {} },
  permissions: { getAll: async () => ({ origins: ['<all_urls>'] }), onAdded: listener([]), onRemoved: listener([]) },
  scripting: { getRegisteredContentScripts: async () => [], updateContentScripts: async () => {}, unregisterContentScripts: async () => {},
    registerContentScripts: async () => {} },
};
const log = apiKey ? spendLog() : null;
globalThis.fetch = log
  ? meter(log, 'worker-lab', { label: () => `${model}`, onUsage: (usage, usd, seconds) => calls.push({ ...usage, usd, seconds }) })
  : async () => { throw new Error('no key: no call'); };
const dispatch = (msg, sender) => new Promise((done) => {
  let handled = false;
  for (const fn of listeners) if (fn(msg, sender, done)) handled = true;
  if (!handled) done(undefined);
});
const page = { id: 'lab', tab: { id: 1 }, url: 'https://example.com/lab' };
const popup = { id: 'lab', url: 'chrome-extension://lab/src/popup.html' };

await import('../../extension/src/background.js');
await dispatch({ type: 'syncWords' }, popup);
const startedWith = log?.total() || 0;

// The page's requests, as content.js makes them: up to 16 passages a message, at once; then, for those awaiting the
// model, a wait for its answer.
let { revision } = await dispatch({ type: 'config' }, page);
const item = (p) => ({ key: `${revision}:${hashKey(p.text)}`, text: p.text });
const t0 = Date.now();
const firsts = [];
const waits = [];
for (let i = 0; i < judged.length; i += 16) {
  const items = judged.slice(i, i + 16).map(({ p }) => item(p));
  const r = await dispatch({ type: 'swaps', revision, items }, page);
  firsts.push(Date.now() - t0);
  const later = items.filter((x) => r.waiting?.includes(x.key)).map((x) => ({ ...x, keep: r.results[x.key] }));
  if (later.length) waits.push(dispatch({ type: 'swaps', revision, items: later, wait: true }, page));
}
const answered = await Promise.all(waits);
const t1 = Date.now();
const errors = answered.filter((r) => r.error).map((r) => r.error);

// The same passages at full and at normal, from the cached answers.
const pick = async () => {
  ({ revision } = await dispatch({ type: 'config' }, page));
  const out = {};
  for (let i = 0; i < judged.length; i += 16) {
    const items = judged.slice(i, i + 16).map(({ p }) => item(p));
    const r = await dispatch({ type: 'swaps', revision, items }, page);
    if (r.waiting?.length) throw new Error('a passage has no answer cached');
    for (const x of items) out[x.text] = r.results[x.key];
  }
  return out;
};
const normal = await pick();
for (let i = 0; i < 3; i++) await dispatch({ type: 'intensity', step: 1 }, popup);
const full = await pick();
await dispatch({ type: 'intensity', step: 0 }, popup);

const pipe = pipelineFor(reply.words);
const score = (picked) => {
  const t = { places: 0, covered: 0, right: 0, ok: 0, wrong: [], unjudged: [], swaps: 0 };
  for (const { p, j } of judged) {
    const spans = planText(pipe, p.text).spans.map((m) => m.span);
    const v = judgeSwaps(j, placeSwaps(p, picked[p.text]), spans);
    t.places += j.use.length;
    t.covered += v.covered.size;
    t.right += v.right; t.ok += v.ok;
    t.wrong.push(...v.wrong.map((w) => `${p.id}: ${w}`));
    t.unjudged.push(...v.unjudged.map((w) => `${p.id}: ${w}`));
    t.swaps += picked[p.text].length;
  }
  return { ...t, coverage: t.covered / t.places, wrongShare: t.wrong.length / (t.right + t.ok + t.wrong.length) };
};
const pct = (x) => `${(100 * x).toFixed(1)}%`;
const usd = calls.reduce((s, c) => s + c.usd, 0);
console.log(`The worker on ${judged.length} judged passages (${files.map((f) => f.split('/').pop()).join(', ')}), ${reply.words.length} words, ${model}${apiKey ? '' : ' (no key: the safe swaps alone)'}`);
console.log('| | Coverage | Wrong, wrong / judged | Unjudged | Swaps shown |');
console.log('|---|---|---|---|---|');
for (const [name, picked] of [['full', full], ['normal (today, as live: thinning and estimates)', normal]]) {
  const s = score(picked);
  console.log(`| ${name} | ${pct(s.coverage)} (${s.covered} of ${s.places}) | ${pct(s.wrongShare)}, ${s.wrong.length} / ${s.right + s.ok + s.wrong.length} | ${s.unjudged.length} | ${s.swaps} |`);
  if (env.VERBOSE) for (const w of [...s.wrong.map((x) => `wrong ${x}`), ...s.unjudged.map((x) => `unjudged ${x}`)]) console.log(`  ${w}`);
}
const seconds = calls.map((c) => c.seconds);
console.log(`Sure swaps for every passage after ${firsts.at(-1)} ms (first message ${firsts[0]} ms); all answers after ${((t1 - t0) / 1000).toFixed(1)} s.`);
console.log(`${calls.length} calls, ${(seconds.reduce((a, b) => a + b, 0) / Math.max(1, seconds.length)).toFixed(1)} s a call (max ${Math.max(0, ...seconds).toFixed(1)}), ` +
  `$${usd.toFixed(4)}, $${((1000 * usd) / judged.length).toFixed(3)} per 1,000 passages; spend log total $${(log?.total() || 0).toFixed(4)} (was $${startedWith.toFixed(4)}).` +
  `${errors.length ? ` Errors: ${errors.join('; ')}` : ''}`);
if (env.SAVE) {
  // check-lab's plan of each passage (its words: the introduced ones as WORDS lists them; the flags file), and the
  // worker's answer, stored by place, in that plan's numbers
  const words = reply.words.filter((w) => ['known', 'active', 'shaky'].includes(w.status))
    .map((w) => ({ ja: w.ja, en: w.en, hint: w.hint || '', kana: w.kana || '', status: w.status }));
  const flags = parseSenses(readFileSync(here('../../data/senses.tsv'), 'utf8'));
  const index = buildIndex(words);
  const answers = {};
  for (const { p } of judged) {
    const stored = local[`c:${answerKey(planText(pipe, p.text))}`]?.s;
    const answer = stored && loadAnswer(planPassage(p.text, matchText(index, p.text), flags), stored);
    if (answer) answers[p.id] = { picks: [...answer.picks], swaps: answer.swaps };
  }
  const variant = { model, batch: 6, thinking: true, effort: 'low', phrases: true, sentences: true, maxWords: 14, glosses: 'listed', worker: true };
  writeFileSync(env.SAVE, JSON.stringify({ variant, runs: [{ seed: 'worker', answers, calls: calls.map((c) => ({ passages: 0, seconds: c.seconds,
    errors: [], usd: c.usd, input: c.input, output: c.output, cached: c.cached || 0 })) }] }));
}
process.exit(0);
