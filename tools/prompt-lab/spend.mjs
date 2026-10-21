// A spend log for the lab's paid calls: every call's tokens and cost, one JSON line each, appended to a file, with a
// running total and a hard cap. meter() wraps fetch, reads the usage from each Anthropic or Gemini response and records
// it, and refuses a call once the total has reached the cap, so a loop of calls stops before it spends more. Shared by
// tools/sense-flags.mjs and check-lab.mjs.
//
//   SPEND_LOG=/tmp/spend.jsonl SPEND_CAP=5 bun tools/sense-flags.mjs ...
//
// SPEND_LOG: the file (required for a paid call); SPEND_CAP: the most in US dollars the file may total (default 5).
// The log holds no keys and no text: the time, the tool, a label, the model and the token counts.
import { appendFileSync, existsSync, readFileSync } from 'node:fs';
import { costUsd, priceFor } from '../../extension/src/usage.js';

export function spendLog(path = process.env.SPEND_LOG, cap = Number(process.env.SPEND_CAP || 5)) {
  if (!path) throw new Error('set SPEND_LOG to a file for the spend log before making paid calls');
  const lines = existsSync(path) ? readFileSync(path, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  let total = lines.reduce((s, l) => s + (l.usd || 0), 0);
  return {
    path, cap,
    total: () => total,
    // one billed call; returns its cost
    record(tool, label, model, usage) {
      if (!priceFor(model)) throw new Error(`no price for ${model}: add it to extension/src/usage.js before spending on it`);
      const usd = costUsd(model, usage.input || 0, usage.output || 0, undefined, usage.cached || 0, usage.written || 0);
      total += usd;
      appendFileSync(path, `${JSON.stringify({ time: new Date().toISOString(), tool, label, model, ...usage, usd, total })}\n`);
      return usd;
    },
    guard() {
      if (total >= cap) throw new Error(`spend cap reached: $${total.toFixed(4)} of $${cap} in ${path}`);
    },
  };
}

// The usage of a provider's response body, as anthropic.js and gemini.js count it.
export function usageOf(data) {
  if (data?.usage) {
    const u = data.usage;
    const cached = u.cache_read_input_tokens || 0;
    const written = u.cache_creation_input_tokens || 0;
    return { input: (u.input_tokens || 0) + cached + written, cached, written, output: u.output_tokens || 0 };
  }
  const m = data?.usageMetadata;
  if (m) return { input: m.promptTokenCount || 0, cached: m.cachedContentTokenCount || 0, written: 0, output: (m.candidatesTokenCount || 0) + (m.thoughtsTokenCount || 0) };
  return null;
}

// fetch, metered: refuses to call past the cap, records each response's usage under tool and label(body) (a short
// name for the call). onUsage(usage, usd, seconds) also gets each.
export function meter(log, tool, { fetchImpl = globalThis.fetch, label = () => '', onUsage } = {}) {
  return async (url, init) => {
    log.guard();
    const t = Date.now();
    const res = await fetchImpl(url, init);
    const data = await res.clone().json().catch(() => null);
    const usage = usageOf(data);
    let model = /models\/([^:]+):/.exec(url)?.[1] || '';
    try { model = JSON.parse(init.body).model || model; } catch { /* Gemini's body names no model */ }
    if (usage) {
      const usd = log.record(tool, label(init?.body), model, usage);
      onUsage?.(usage, usd, (Date.now() - t) / 1000);
    }
    return res;
  };
}
