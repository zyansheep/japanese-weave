import { describe, test, expect } from 'bun:test';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spendLog, meter, usageOf } from '../../tools/prompt-lab/spend.mjs';

// The prompt lab's spend log (tools/prompt-lab/spend.mjs): every paid call recorded, and no call past the cap.
const claude = (usage) => ({ ok: true, status: 200, clone() { return this; }, json: async () => ({ usage, content: [] }) });
const body = JSON.stringify({ model: 'claude-haiku-5-5' });

describe('spend log', () => {
  test('records each call with its cost and a running total that includes what the file held', async () => {
    const path = join(mkdtempSync(join(tmpdir(), 'spend-')), 'spend.jsonl');
    writeFileSync(path, `${JSON.stringify({ tool: 'before', usd: 0.06 })}\n`);
    const log = spendLog(path, 5);
    expect(log.total()).toBeCloseTo(0.06);
    const seen = [];
    const fetchImpl = meter(log, 'lab', { fetchImpl: async () => claude({ input_tokens: 1000, cache_read_input_tokens: 2000, output_tokens: 4000 }),
      label: () => 'one call', onUsage: (u, usd) => seen.push(usd) });
    await fetchImpl('https://api.anthropic.com/v1/messages', { body });
    // 1,000 input at $0.10, 2,000 cached at $0.01, 4,000 output at $0.50 per million
    expect(seen[0]).toBeCloseTo(0.00212, 6);
    const last = JSON.parse(readFileSync(path, 'utf8').trim().split('\n').at(-1));
    expect(last).toMatchObject({ tool: 'lab', label: 'one call', model: 'claude-haiku-5-5', input: 3000, cached: 2000, output: 4000 });
    expect(last.total).toBeCloseTo(0.06212, 6);
  });
  test('refuses a call once the total reaches the cap, and a model it has no price for', async () => {
    const path = join(mkdtempSync(join(tmpdir(), 'spend-')), 'spend.jsonl');
    writeFileSync(path, `${JSON.stringify({ usd: 5 })}\n`);
    let calls = 0;
    const fetchImpl = meter(spendLog(path, 5), 'lab', { fetchImpl: async () => { calls++; return claude({ output_tokens: 1 }); } });
    await expect(fetchImpl('https://api.anthropic.com/v1/messages', { body })).rejects.toThrow('spend cap');
    expect(calls).toBe(0);
    const open = meter(spendLog(join(mkdtempSync(join(tmpdir(), 'spend-')), 's.jsonl'), 5), 'lab', { fetchImpl: async () => claude({ output_tokens: 1 }) });
    await expect(open('https://api.anthropic.com/v1/messages', { body: JSON.stringify({ model: 'claude-unknown' }) })).rejects.toThrow('no price');
    expect(() => spendLog('')).toThrow('SPEND_LOG');
  });
  test("reads Gemini's usage too", () => {
    expect(usageOf({ usageMetadata: { promptTokenCount: 10, cachedContentTokenCount: 4, candidatesTokenCount: 2, thoughtsTokenCount: 3 } }))
      .toEqual({ input: 10, cached: 4, written: 0, output: 5 });
    expect(usageOf({})).toBeNull();
  });
});
