// One batch, one model call (fallback model once on failure), validated swaps out.
import { buildSystemParts, buildUserContent, RESPONSE_SCHEMA } from './prompt.js';
import { askGemini } from './gemini.js';
import { askClaude } from './anthropic.js';
import { candidatesFor, providerOf, TIMEOUT_MS, DEFAULT_TIMEOUT_MS } from './models.js';
import { validateSwaps, intensityFactor, INTENSITY_FULL } from './levels.js';

const ASK = { gemini: askGemini, anthropic: askClaude };

// cooldown: Map<model, until ms>. Rate-limited or overloaded models are skipped until then, so the
// free tier's per-minute limit does not cost every batch a failed request before the fallback.
export const COOLDOWN_MS = { 429: 60000, 503: 30000, 529: 30000, timeout: 30000 };

// keys: { gemini, anthropic } (apiKey alone is the Gemini key). density: percent of words to swap (0: the level's
// per-sentence rules). heavy: the reader's heavier intensity (1, 2; 0 otherwise), which asks the model for more, or full
// (3), which asks for every swap the list allows and leaves the density out. Returns { results, model, errors, usage }:
// results are every validated swap, uncapped (selectSwaps picks from them when they're read from the cache); usage
// lists every billed attempt ({ model, input, output, cached?, written? } tokens); a thrown error carries the same `usage`.
export async function requestSwaps({ keys, apiKey, model, level, stage = 0, batch, words, shownToday, fetchImpl, timeoutMs, cooldown = new Map(), now = Date.now, density = 0, heavy = 0 }) {
  const keyFor = { gemini: apiKey, ...keys };
  const percent = density > 0 && heavy < INTENSITY_FULL ? Math.round(density * intensityFactor(heavy)) : 0;
  const system = buildSystemParts(level, words, shownToday, percent, stage, heavy);
  const user = buildUserContent(batch, percent, { level, words, heavy });
  const candidates = candidatesFor(model, keyFor);
  if (!candidates.length) throw new Error(`No key for ${model}`);
  const models = candidates.filter((m) => !(cooldown.get(m) > now()));
  const errors = [];
  const spent = [];
  if (!models.length) {
    const error = new Error('Model rate limit; retrying shortly');
    error.retryAfterMs = Math.max(1000, Math.min(...candidates.map((m) => cooldown.get(m))) - now());
    throw error;
  }
  const texts = new Map(batch.map(({ id, text }) => [id, text]));
  for (const m of models) {
    try {
      const provider = providerOf(m);
      const limit = timeoutMs ?? (TIMEOUT_MS[m] ?? DEFAULT_TIMEOUT_MS) * Math.max(1, batch.length / 8);
      const { byId, usage } = await ASK[provider]({ fetchImpl, apiKey: keyFor[provider], model: m, system, user, schema: RESPONSE_SCHEMA, timeoutMs: limit });
      spent.push({ model: m, ...usage });
      const results = {};
      for (const { id } of batch) {
        if (!byId.has(id)) throw new Error('Incomplete model response');
        results[id] = validateSwaps(level, byId.get(id), words, texts.get(id));
      }
      return { results, model: m, errors, usage: spent };
    } catch (e) {
      if (e?.usage) spent.push({ model: m, ...e.usage });
      const timeout = e?.name === 'TimeoutError';
      const wait = e?.retryAfterMs || COOLDOWN_MS[timeout ? 'timeout' : e?.status];
      if (wait) cooldown.set(m, now() + wait);
      errors.push(`${m}: ${timeout ? 'timeout' : e?.message || e}`);
    }
  }
  const error = new Error(errors.join('; '));
  error.usage = spent;
  const until = candidates.map((m) => cooldown.get(m) || 0).filter((t) => t > now());
  if (until.length === candidates.length) error.retryAfterMs = Math.max(1000, Math.min(...until) - now());
  throw error;
}
