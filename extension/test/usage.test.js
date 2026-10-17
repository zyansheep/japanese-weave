import { describe, expect, it } from 'bun:test';
import { memoryStorage } from '../src/cache.js';
import { costUsd, readUsage, recordUsage } from '../src/usage.js';

describe('usage', () => {
  it('prices tokens by model and day, thinking included in output', () => {
    expect(costUsd('gemini-3.8-flash', 1e6, 1e6, '2026-10-16')).toBeCloseTo(4.5);
    expect(costUsd('gemini-3.8-flash', 1e6, 1e6, '2027-01-01')).toBeCloseTo(9); // list price doubles in 2027
    expect(costUsd('gemini-3.5-flash-lite', 1e6, 1e6, '2027-06-01')).toBeCloseTo(2.8);
    expect(costUsd('some-new-model', 1e6, 1e6)).toBe(0);
  });

  it('cached prompt tokens bill at the cached rate, tokens written to the cache at the write rate', () => {
    // Haiku: 1M in, of which 600k read from the cache and 100k written; 1M out
    expect(costUsd('claude-haiku-5-5', 1e6, 1e6, '2026-10-17', 6e5, 1e5)).toBeCloseTo(0.3 * 0.1 + 0.6 * 0.01 + 0.1 * 0.125 + 0.5);
    expect(costUsd('gemini-3.8-flash', 1e6, 0, '2026-10-17', 1e6)).toBeCloseTo(0.075);
    expect(costUsd('gemini-4-argon', 1e6, 0, '2026-12-01', 1e6)).toBeCloseTo(0.2);
  });

  it('adds up per day and in total, and keeps a month of days', async () => {
    const storage = memoryStorage();
    expect(await readUsage(storage)).toBeNull();
    await recordUsage(storage, 'gemini-3.8-flash', { input: 2700, output: 1000 }, '2026-10-15');
    await recordUsage(storage, 'gemini-3.8-flash', { input: 2700, output: 1000 }, '2026-10-16');
    await recordUsage(storage, 'gemini-3.5-flash-lite', { input: 1000, output: 0 }, '2026-10-16');
    const usage = await readUsage(storage, '2026-10-16');
    expect(usage.since).toBe('2026-10-15');
    expect(usage.total).toMatchObject({ calls: 3, input: 6400, output: 2000 });
    expect(usage.total.usd).toBeCloseTo(2 * (2700 * 0.75 + 1000 * 3.75) / 1e6 + 1000 * 0.3 / 1e6);
    expect(usage.today).toMatchObject({ calls: 2, input: 3700, output: 1000 });
    for (let d = 1; d <= 40; d++) await recordUsage(storage, 'gemini-3.8-flash', { input: 1, output: 1 }, `2026-11-${String(d).padStart(2, '0')}`);
    const { geminiUsage } = await storage.get(['geminiUsage']);
    expect(Object.keys(geminiUsage.days)).toHaveLength(31);
    expect(geminiUsage.total.calls).toBe(43);
  });
});
