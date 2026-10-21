// The models the popup offers, which provider and key each needs, and what to try when one fails.
export const MODELS = {
  'gemini-3.8-flash': 'Gemini 3.8 Flash (fastest)',
  'gemini-4-argon': 'Gemini 4 Argon (best Japanese; paid keys only)',
  'claude-haiku-5-5': 'Claude Haiku 5.5, light thinking (Anthropic key; slower)',
};
// Every model a call can come from, fallbacks included, for the popup's "last answer from".
export const MODEL_NAMES = { ...MODELS, 'gemini-3.5-flash-lite': 'Gemini 3.5 Flash-Lite' };
export const DEFAULT_MODEL = 'gemini-3.8-flash';
// Passages per model call (verify.js requestChecks). The check prompt (~2,100 tokens) is paid once per call, so fuller
// calls cost less per passage; a page asks for at most this many at once.
export const MAX_BATCH = 16;
// Haiku thinks before it answers (anthropic.js), so it gets smaller calls, several at once: calls of 6 took 24 s on the
// lab's fresh passages (at most 39 s; docs/swap-prompt.md), and calls of 12 cost 11% less but took 31 s. Its prompt is
// read from Anthropic's cache at a tenth of the price, so the extra calls cost little. Every other model: one call of up
// to MAX_BATCH at a time.
export const BATCH = { 'claude-haiku-5-5': 6 };
export const PARALLEL = { 'claude-haiku-5-5': 3 };
// Tried once when the chosen model fails, if its provider has a key.
export const FALLBACK_MODEL = {
  'gemini-4-argon': 'gemini-3.8-flash',
  'gemini-3.8-flash': 'gemini-3.5-flash-lite',
  'claude-haiku-5-5': 'gemini-3.8-flash',
};
// How long a call of 8 passages may take; a bigger batch gets proportionally longer (verify.js requestChecks). Argon
// writes whole sentences more slowly (12-15 s in testing); Haiku's check of 6 took at most 39 s in the lab.
export const TIMEOUT_MS = { 'gemini-4-argon': 35000, 'claude-haiku-5-5': 60000 };
export const DEFAULT_TIMEOUT_MS = 20000;

// cooldown: Map<model, until ms>. Rate-limited or overloaded models are skipped until then, so the free tier's
// per-minute limit does not cost every batch a failed request before the fallback.
export const COOLDOWN_MS = { 429: 60000, 503: 30000, 529: 30000, timeout: 30000 };

export const providerOf = (model) => (String(model).startsWith('claude-') ? 'anthropic' : 'gemini');

// The model, then its fallback, keeping only those whose provider has a key. keys: { gemini, anthropic }.
export function candidatesFor(model, keys) {
  return [model, FALLBACK_MODEL[model]].filter((m) => m && keys[providerOf(m)]);
}
