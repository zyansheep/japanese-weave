// The models the popup offers, which provider and key each needs, and what to try when one fails.
export const MODELS = {
  'gemini-3.8-flash': 'Gemini 3.8 Flash',
  'gemini-4-argon': 'Gemini 4 Argon (best Japanese; paid keys only)',
  'gemini-3.5-flash-lite': 'Gemini 3.5 Flash-Lite',
  'claude-haiku-5-5': 'Claude Haiku 5.5 (Anthropic key; cheapest)',
};
export const DEFAULT_MODEL = 'gemini-3.8-flash';
// Passages per model call. The prompt (~3,300 tokens) is paid once per call, so fuller calls cost less per passage.
export const MAX_BATCH = 16;
// Tried once when the chosen model fails, if its provider has a key.
export const FALLBACK_MODEL = {
  'gemini-4-argon': 'gemini-3.8-flash',
  'gemini-3.8-flash': 'gemini-3.5-flash-lite',
  'claude-haiku-5-5': 'gemini-3.8-flash',
};
// How long a call of 8 passages may take; a bigger batch gets proportionally longer (engine.js). Argon writes whole
// sentences more slowly (12-15 s in testing).
export const TIMEOUT_MS = { 'gemini-4-argon': 35000 };
export const DEFAULT_TIMEOUT_MS = 20000;

export const providerOf = (model) => (String(model).startsWith('claude-') ? 'anthropic' : 'gemini');

// The model, then its fallback, keeping only those whose provider has a key. keys: { gemini, anthropic }.
export function candidatesFor(model, keys) {
  return [model, FALLBACK_MODEL[model]].filter((m) => m && keys[providerOf(m)]);
}
