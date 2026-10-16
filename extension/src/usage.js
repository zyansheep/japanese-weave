// Gemini token usage and its estimated cost, per day and in total. Prices are Google's paid-tier list prices in USD
// per 1M tokens (https://ai.google.dev/gemini-api/docs/pricing, checked 2026-10-15); thinking tokens bill as output.
// Free-tier keys are not charged, so for them this is what the same use would cost on a paid key.
import { localDay } from './hostsource.js';

export const PRICES = {
  'gemini-3.8-flash': [{ from: '0000-00-00', input: 0.75, output: 3.75 }, { from: '2027-01-01', input: 1.5, output: 7.5 }],
  'gemini-3.5-flash-lite': [{ from: '0000-00-00', input: 0.3, output: 2.5 }],
};
const KEY = 'geminiUsage';
const DAYS_KEPT = 31;

export function priceFor(model, day = localDay()) {
  return [...(PRICES[model] || [])].reverse().find((p) => p.from <= day) || null;
}

export function costUsd(model, input, output, day = localDay()) {
  const price = priceFor(model, day);
  return price ? (input * price.input + output * price.output) / 1e6 : 0;
}

const empty = () => ({ calls: 0, input: 0, output: 0, usd: 0 });
const add = (total, input, output, usd) => ({
  calls: total.calls + 1, input: total.input + input, output: total.output + output, usd: total.usd + usd,
});

// One billed model call: tokens in and out (thinking included), priced at today's rate for that model.
export async function recordUsage(storage, model, usage, day = localDay()) {
  const input = Math.max(0, Math.round(Number(usage?.input) || 0));
  const output = Math.max(0, Math.round(Number(usage?.output) || 0));
  const usd = costUsd(model, input, output, day);
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
