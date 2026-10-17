import { describe, it, expect } from 'bun:test';
import { readingState, account, newStay, paused, IDLE_MS, PAUSE_MS } from '../src/attention.js';

describe('on-screen time', () => {
  const page = { hidden: false, focused: true, playing: false, lastInput: 0 };
  it('counts only time a reader could spend reading; the rest by why not', () => {
    const stay = newStay(0);
    account(stay, 1000, readingState(page, 1000));
    account(stay, 2000, readingState({ ...page, focused: false }, 2000)); // another window, or watching elsewhere
    account(stay, 3000, readingState({ ...page, hidden: true }, 3000)); // another tab
    account(stay, 4000, readingState({ ...page, playing: true }, 4000)); // a video or music on the page
    account(stay, 5000, readingState(page, IDLE_MS + 4500)); // no input for a while: left open
    account(stay, 6000, readingState({ ...page, lastInput: 5500 }, 6000));
    expect(stay).toMatchObject({ wall: 6000, ms: 2000, away: 2000, media: 1000, idle: 1000 });
  });
  it('a long gap between ticks is away, whatever the page looks like now', () => {
    const stay = account(newStay(0), 60000, readingState(page, 1000)); // a throttled background tab or a sleep
    expect(stay).toMatchObject({ wall: 60000, ms: 0, away: 60000 });
  });
  it('a stay is paused once nothing was read for PAUSE_MS, whatever the reason', () => {
    const stay = account(newStay(0), 1000, readingState(page, 1000));
    for (let now = 2000; now <= 1000 + PAUSE_MS; now += 1000) account(stay, now, readingState({ ...page, focused: false }, now));
    expect(paused(stay, 1000 + PAUSE_MS)).toBe(false);
    account(stay, 2000 + PAUSE_MS, readingState({ ...page, focused: false }, 2000 + PAUSE_MS));
    expect(paused(stay, 2000 + PAUSE_MS)).toBe(true);
    expect(stay).toMatchObject({ ms: 1000, away: PAUSE_MS + 1000 });
  });
  it('away wins over media and media over idle', () => {
    expect(readingState({ ...page, focused: false, playing: true, lastInput: -IDLE_MS }, 1)).toEqual({ away: true, media: false, idle: false });
    expect(readingState({ ...page, playing: true, lastInput: -IDLE_MS }, 1)).toEqual({ away: false, media: true, idle: false });
  });
});
