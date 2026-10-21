#!/usr/bin/env python3
"""Checks the probe's estimate (scripts/probe.py) on simulated readers whose knowledge is known.

  python3 tools/probe_sim.py [--reps 2000] [--months 12] [--seed 1]

A simulated reader knows each word of the probe's population with a chance that falls with its frequency rank,
1 / (1 + (rank / r50)^1.5): r50 is the rank at which they know half the words (200: a beginner; 1,000; 4,000:
advanced). Each repetition draws a reader (which words they know), works out the true share of the frequency weight
they know, and has them answer a probe: yes to the words they know, and, for an over-claimer, yes to a word they don't
know or a made-up one with the chance `guess`. It reports the estimate's bias (mean estimate minus truth), its
standard error (the spread of the estimates, and the mean of the standard errors the probe reports), and how often the
90% interval holds the truth; the same for the estimate corrected for guessing. --months then follows readers through
monthly probes, each drawn without the words of the last 90 days, as the no-repeat rule does, and reports the bias
and coverage of each month: leaving out the recent words must not bias later probes.
"""
import argparse
import datetime as dt
from pathlib import Path
import random
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import probe  # noqa: E402

PROFILES = ((200, 0.0), (1000, 0.0), (4000, 0.0), (1000, 0.1))  # (r50, guess)
SLOPE = 1.5


def reader(frame, r50, rng, slope=SLOPE):
    """{word: known} for one simulated reader."""
    return {it['ja']: rng.random() < 1 / (1 + (it['rank'] / r50) ** slope) for it in frame.items}


def truth(frame, knows):
    total = sum(it['weight'] for it in frame.items)
    return sum(it['weight'] for it in frame.items if knows[it['ja']]) / total


def answers(questions, knows, guess, rng):
    """Yes to a known word; to an unknown or made-up one, yes with the chance guess."""
    return [1 if not q.get('fake') and knows[q['ja']] else int(rng.random() < guess) for q in questions]


def one(frame, knows, guess, rng, state=None, today='2026-10-20'):
    """One probe answered by a reader: its op (probe.record)."""
    p = probe.sample(state, rng=rng, today=today, frame=frame)
    return probe.record(p, answers(p['questions'], knows, guess, rng), 'terminal', 0)


def score(results):
    """[(truth, estimate)] -> {bias, sd, se (mean reported), coverage, deff, corrected bias and coverage}."""
    error = [e['known'] - t for t, e in results]
    out = {'n': len(results), 'truth': statistics.mean(t for t, _ in results), 'bias': statistics.mean(error),
           'sd': statistics.pstdev(error), 'se': statistics.mean(e['se'] for _, e in results),
           'coverage': statistics.mean(e['low'] <= t <= e['high'] for t, e in results),
           'deff': statistics.mean(e['deff'] for _, e in results if e['deff'] is not None)}
    fixed = [(t, e) for t, e in results if e.get('corrected') is not None]
    if fixed:
        out['corrected_bias'] = statistics.mean(e['corrected'] - t for t, e in fixed)
        out['corrected_coverage'] = statistics.mean(e['corrected_low'] <= t <= e['corrected_high'] for t, e in fixed)
    return out


def profile(frame, r50, guess, reps, seed):
    """reps readers, each answering one probe."""
    rng, results = random.Random(seed), []
    for _ in range(reps):
        knows = reader(frame, r50, rng)
        results.append((truth(frame, knows), one(frame, knows, guess, rng)['estimate']))
    return score(results)


def months(frame, r50, guess, reps, count, seed):
    """reps readers, each answering a probe every 30 days for count months: [score per month]."""
    rng, by_month = random.Random(seed), [[] for _ in range(count)]
    start = dt.date(2026, 10, 20)
    for r in range(reps):
        knows, state = reader(frame, r50, rng), {'probes': []}
        true = truth(frame, knows)
        for m in range(count):
            day = (start + dt.timedelta(days=30 * m)).isoformat()
            op = one(frame, knows, guess, rng, state, day)
            probe.keep(state, dict(op, uid=f'{r}-{m}', day=day, at=0))
            by_month[m].append((true, op['estimate']))
    return [score(results) for results in by_month]


def repeats(state):
    """Words asked in two probes less than probe.REPEAT_DAYS days apart (the rule says none)."""
    seen, found = {}, 0
    for p in state['probes']:
        for w in p['words']:
            if w[0] in seen and probe.fwlib.days_between(seen[w[0]], p['day']) < probe.REPEAT_DAYS:
                found += 1
            seen[w[0]] = p['day']
    return found


def line(name, s):
    text = (f'{name}: truth {100 * s["truth"]:.1f}%, bias {100 * s["bias"]:+.2f} points, standard error '
            f'{100 * s["sd"]:.2f} (reported {100 * s["se"]:.2f}), 90% interval coverage {100 * s["coverage"]:.1f}%, '
            f'deff {s["deff"]:.2f}')
    if 'corrected_bias' in s:
        text += (f'; corrected: bias {100 * s["corrected_bias"]:+.2f}, coverage {100 * s["corrected_coverage"]:.1f}%')
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--reps', type=int, default=2000)
    p.add_argument('--months', type=int, default=12)
    p.add_argument('--seed', type=int, default=1)
    args = p.parse_args()
    frame = probe.Frame()
    print(f'Population: {len(frame.items)} words in {len(frame.bands)} bands of '
          f'{", ".join(str(len(b)) for b in frame.bands)} words; {probe.WORDS} words and {probe.FAKES} made up a probe.')
    for r50, guess in PROFILES:
        name = f'r50 {r50}' + (f', says yes to {100 * guess:.0f}% of unknown and made-up words' if guess else '')
        print(line(name, profile(frame, r50, guess, args.reps, args.seed)))
    if args.months:
        reps = max(1, args.reps // 10)
        print(f'Monthly probes without the words of the last {probe.REPEAT_DAYS} days, {reps} readers (r50 1000):')
        for m, s in enumerate(months(frame, 1000, 0.0, reps, args.months, args.seed), 1):
            print('  ' + line(f'month {m}', s))


if __name__ == '__main__':
    main()
