// The worker (background.js) flushes queued events, log and reading counts to the host 10 s after the last one, from a
// module-level timer. Each worker test loads its own copy of the worker, and every copy reaches storage through the
// global chrome, so a timer one test leaves running fires on the next test's storage and empties its queues. A worker
// test calls trackTimers() before loading the worker and the function it returns in its finally: that clears every
// timeout set in between and puts back the real setTimeout.
export function trackTimers() {
  const real = globalThis.setTimeout;
  const set = [];
  globalThis.setTimeout = (fn, ms, ...args) => {
    const timer = real(fn, ms, ...args);
    set.push(timer);
    return timer;
  };
  return () => {
    for (const timer of set) clearTimeout(timer);
    globalThis.setTimeout = real;
  };
}
