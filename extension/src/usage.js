// Model token usage and its estimated cost, per day and in total. Prices are list prices in USD per 1M tokens: Google's
// paid tier (https://ai.google.dev/gemini-api/docs/pricing, checked 2026-10-17) and Anthropic's
// (https://platform.claude.com/docs/en/about-claude/pricing, checked 2026-10-17). Thinking tokens bill as output; cached
// prompt tokens at the cached rate, and tokens written to Anthropic's cache at the write rate.
// Free-tier Gemini keys are not charged, so for them this is what the same use would cost on a paid key.
import { localDay } from './hostsource.js';

export const PRICES = {
  'gemini-3.8-flash': [{ from: '0000-00-00', input: 0.75, cached: 0.075, output: 3.75 },
    { from: '2027-01-01', input: 1.5, cached: 0.15, output: 7.5 }],
  'gemini-4-argon': [{ from: '0000-00-00', input: 2, cached: 0.1, output: 10 }, { from: '2026-12-01', input: 4, cached: 0.2, output: 20 }],
  'gemini-3.5-flash-lite': [{ from: '0000-00-00', input: 0.3, cached: 0.03, output: 2.5 }],
  'claude-haiku-5-5': [{ from: '0000-00-00', input: 0.1, cached: 0.01, written: 0.125, output: 0.5 }],
};
const KEY = 'geminiUsage'; // every provider's, under its first name
const DAYS_KEPT = 31;

export function priceFor(model, day = localDay()) {
  return [...(PRICES[model] || [])].reverse().find((p) => p.from <= day) || null;
}

// input counts every prompt token; cached and written are the parts of it read from and written to a cache.
export function costUsd(model, input, output, day = localDay(), cached = 0, written = 0) {
  const price = priceFor(model, day);
  if (!price) return 0;
  const plain = Math.max(0, input - cached - written);
  return (plain * price.input + cached * (price.cached ?? price.input) + written * (price.written ?? price.input) +
    output * price.output) / 1e6;
}

const empty = () => ({ calls: 0, input: 0, output: 0, usd: 0 });
const add = (total, input, output, usd) => ({
  calls: total.calls + 1, input: total.input + input, output: total.output + output, usd: total.usd + usd,
});

// One billed model call: tokens in and out (thinking included), priced at today's rate for that model.
export async function recordUsage(storage, model, usage, day = localDay()) {
  const count = (n) => Math.max(0, Math.round(Number(n) || 0));
  const input = count(usage?.input);
  const output = count(usage?.output);
  const usd = costUsd(model, input, output, day, count(usage?.cached), count(usage?.written));
  const { [KEY]: saved } = await storage.get([KEY]);
  const usageNow = saved?.total ? saved : { since: day, total: empty(), days: {} };
  usageNow.total = add(usageNow.total, input, output, usd);
  usageNow.days = { ...usageNow.days, [day]: add(usageNow.days?.[day] || empty(), input, output, usd) };
  for (const old of Object.keys(usageNow.days).sort().slice(0, -DAYS_KEPT)) delete usageNow.days[old];
  await storage.set({ [KEY]: usageNow });
  return usageNow;
}

export async function readUsage(storage, day = localDay()) {
  const { [KEY]: saved } = await storage.get([KEY]);
  if (!saved?.total) return null;
  return { since: saved.since, total: saved.total, today: saved.days?.[day] || empty() };
}
