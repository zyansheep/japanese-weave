// Anthropic Messages API call (Claude Haiku). No chrome APIs; fetch is injectable for tests.
// https://platform.claude.com/docs/en/api/messages: JSON output via output_config.format, light thinking (adaptive at low
// effort), and the system prompt in two cached blocks.
import { parseSwapsResponse } from './gemini.js';

export const API_URL = 'https://api.anthropic.com/v1/messages';
export const API_VERSION = '2023-06-01';
const MAX_TOKENS = 16000;

// Structured output needs every object closed.
export function closed(schema) {
  if (schema?.type === 'object') {
    return { ...schema, additionalProperties: false,
      properties: Object.fromEntries(Object.entries(schema.properties).map(([k, v]) => [k, closed(v)])) };
  }
  if (schema?.type === 'array') return { ...schema, items: closed(schema.items) };
  return schema;
}

// system: the prompt in parts (prompt.js buildSystemParts). Each part ends a cache breakpoint, so the rules and known
// words are read from the cache even when the learning words' order has changed.
export function buildClaudeRequest({ model, system, user, schema }) {
  const parts = [].concat(system).filter(Boolean);
  return {
    model,
    max_tokens: MAX_TOKENS,
    system: parts.map((text) => ({ type: 'text', text, cache_control: { type: 'ephemeral' } })),
    messages: [{ role: 'user', content: user }],
    // With thinking off, Haiku broke the rules: 15-21 dropped swaps in 16 test passages, unlisted words, almost no
    // phrases. A little thinking brought it close to Argon (docs/swap-prompt.md) at about 4x the cost.
    thinking: { type: 'adaptive' },
    output_config: { effort: 'low', format: { type: 'json_schema', schema: closed(schema) } },
  };
}

// Returns { byId, usage: { input, cached, written, output } }: input counts every prompt token, of which cached were read
// from the cache and written went into it. Throws on HTTP error, timeout, a refusal or a cut-off answer.
export async function callClaude({ fetchImpl = globalThis.fetch, apiKey, body, timeoutMs = 20000 }) {
  const res = await fetchImpl(API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': API_VERSION,
      'anthropic-dangerous-direct-browser-access': 'true',
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`);
    error.status = res.status;
    let detail = '';
    try { detail = await res.text(); } catch { /* status alone is enough */ }
    // A spend cap is a 429 with no retry-after; waiting won't help, so it cools down like any other 429.
    const message = /"message"\s*:\s*"([^"]{1,200})"/.exec(detail)?.[1];
    if (message) error.message = `HTTP ${res.status}: ${message}`;
    const delay = Number(res.headers?.get?.('retry-after'));
    if (delay > 0) error.retryAfterMs = Math.min(delay, 600) * 1000;
    throw error;
  }
  const data = await res.json();
  const u = data?.usage || {};
  const cached = u.cache_read_input_tokens || 0;
  const written = u.cache_creation_input_tokens || 0;
  const usage = { input: (u.input_tokens || 0) + cached + written, cached, written, output: u.output_tokens || 0 };
  try {
    if (data?.stop_reason === 'refusal' || data?.stop_reason === 'max_tokens') throw new Error(`Answer ended: ${data.stop_reason}`);
    const text = (data?.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('');
    return { byId: parseSwapsResponse(text), usage };
  } catch (e) {
    e.usage = usage; // an unusable answer is still billed
    throw e;
  }
}

export function askClaude({ fetchImpl, apiKey, model, system, user, schema, timeoutMs }) {
  return callClaude({ fetchImpl, apiKey, timeoutMs, body: buildClaudeRequest({ model, system, user, schema }) });
}
