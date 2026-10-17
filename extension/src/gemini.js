// Gemini generateContent call. No chrome APIs; fetch is injectable for tests.
export const THINKING_LEVEL = 'low';
export const API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/';

export function buildRequest({ system, user, schema, thinkingLevel = THINKING_LEVEL }) {
  return {
    systemInstruction: { parts: [{ text: system }] },
    contents: [{ role: 'user', parts: [{ text: user }] }],
    generationConfig: {
      responseMimeType: 'application/json',
      responseSchema: schema,
      thinkingConfig: { thinkingLevel },
    },
  };
}

// Returns { byId: Map<id, swaps[]>, usage: { input, output } } (tokens; thinking counts as output). Throws on HTTP
// error, timeout, or unparseable output.
export async function callGemini({ fetchImpl = globalThis.fetch, apiKey, model, body, timeoutMs = 20000 }) {
  const res = await fetchImpl(`${API_BASE}${model}:generateContent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`);
    error.status = res.status;
    // 429 bodies carry RetryInfo ("retryDelay": "36s"); fall back to Retry-After.
    let detail = '';
    try { detail = await res.text(); } catch { /* status alone is enough */ }
    const delay = Number(/"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"/.exec(detail)?.[1] || res.headers?.get?.('retry-after'));
    if (delay > 0) error.retryAfterMs = Math.min(delay, 600) * 1000;
    throw error;
  }
  const data = await res.json();
  const meta = data?.usageMetadata || {};
  // promptTokenCount includes any implicitly cached tokens (cachedContentTokenCount), which bill at the cached rate
  const usage = { input: meta.promptTokenCount || 0, output: (meta.candidatesTokenCount || 0) + (meta.thoughtsTokenCount || 0),
    ...(meta.cachedContentTokenCount ? { cached: meta.cachedContentTokenCount } : {}) };
  const text = (data?.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('');
  try {
    return { byId: parseSwapsResponse(text), usage };
  } catch (e) {
    e.usage = usage; // an unusable answer is still billed
    throw e;
  }
}

// The engine's provider call: system is the prompt in parts (sent joined), user the passages.
export function askGemini({ fetchImpl, apiKey, model, system, user, schema, timeoutMs }) {
  return callGemini({ fetchImpl, apiKey, model, timeoutMs, body: buildRequest({ system: [].concat(system).join(''), user, schema }) });
}

export function parseSwapsResponse(text) {
  const data = JSON.parse(text);
  const out = new Map();
  for (const t of data?.passages || data?.tweets || []) {
    if (t && typeof t.id === 'string') out.set(t.id, Array.isArray(t.swaps) ? t.swaps : []);
  }
  return out;
}
