// When time on screen counts as reading (content.js): only while the page is visible, its window focused, no sound or
// video plays on it, and the reader gave some input in the last IDLE_MS. No DOM access here, so this tests without a
// browser.

export const IDLE_MS = 30000;
export const GAP_MS = 3000; // a longer gap between ticks means throttled timers (a background tab) or a sleeping machine
// A stay with no reading for this long ends there (the reader left, or left the page open); the next input starts a new
// one, so a page left showing in an unfocused window doesn't send a stay of mostly away time every STAY_MAX_MS.
export const PAUSE_MS = 60000;

// state: what the page looks like now. hidden and focused from the document, playing from its media elements.
export function readingState({ hidden, focused, playing, lastInput }, now) {
  const away = hidden || !focused;
  const media = !away && playing;
  return { away, media, idle: !away && !media && now - lastInput > IDLE_MS };
}

// Adds the time since the stay's last tick to one bucket: away, media, idle, or ms (reading).
export function account(stay, now, state) {
  const dt = Math.max(0, now - stay.last);
  stay.last = now;
  stay.wall += dt;
  const bucket = state.away || dt > GAP_MS ? 'away' : state.media ? 'media' : state.idle ? 'idle' : 'ms';
  stay[bucket] += dt;
  if (bucket === 'ms') stay.lastRead = now;
  return stay;
}

export const paused = (stay, now) => now - stay.lastRead > PAUSE_MS;

export function newStay(now) {
  return { at: now, last: now, lastRead: now, wall: 0, ms: 0, away: 0, media: 0, idle: 0 };
}
