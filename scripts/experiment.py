"""fw.py experiment: the delayed-start experiment so far (PLAN.md section 7, row E; NEXT_STEPS steps 6 and 15).
Python 3.9+, standard library only.

Each word taken in is drawn into an arm (fwlib.assign): held back for 3 weeks, or shown for 2 and paused for 1. At week
3 both are probed by a guaranteed showing, and the outcome is the first showing read: looked up or not, with its read
chance (knowledge.probe_outcomes). This reads the arms and outcomes from the word state and prints:

- the words in each arm and where they stand (held, shown, paused, probe due, probed, probe given up);
- from the gloss log, where there is one: the showings a word got in its shown days, and any showing while the
  experiment hid it (a leak: the Japanese chat brief forbids off-list words, but a model may still write one);
- the outcome per arm: the share looked up at the probe, compared by a two-proportion z test; and from MIN_FIT a side,
  a logistic regression of looked up on the arm with the read chance as a covariate (PLAN.md section 7);
- the intake: words a day over the last INTAKE_DAYS, and when each side reaches TARGET probed words;
- with --power, the power at TARGET a side from the simulated reader (tools/sim experiments.delayed_start) at the
  showings a day the shown arm gets.

Names of held-back words are never printed: seeing them would be exposure.

  fw.py experiment [--home DIR] [--power]
"""
import math
from pathlib import Path
import sys
import time

import feedback
import fwlib

TARGET = 80        # probed words a side the plan aims for (PLAN.md section 7: 73% to 95% power in simulation)
MIN_FIT = 20       # probed words a side before the logistic regression
INTAKE_DAYS = 14   # the intake rate is read over this many days


def arms(state, day):
    """[{ja, arm, day, probe, phase, outcome}] for every word in the experiment."""
    out = []
    for w in state['words']:
        phase = fwlib.phase(w, day)
        if phase is not None:
            a = w['arm']
            out.append({'ja': w['ja'], 'arm': a['arm'], 'day': a['day'], 'at': a.get('at'), 'probe': a['probe'],
                        'phase': phase, 'outcome': a.get('outcome'), 'missed': a.get('missed')})
    return out


def normal_tail(z):
    """P(|Z| > z) for a standard normal Z."""
    return math.erfc(abs(z) / math.sqrt(2))


def two_proportions(k1, n1, k2, n2):
    """(difference p2 - p1, its standard error unpooled, z from the pooled standard error, two-sided p), or None."""
    if not n1 or not n2:
        return None
    p1, p2, pooled = k1 / n1, k2 / n2, (k1 + k2) / (n1 + n2)
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    se0 = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p2 - p1) / se0 if se0 else 0.0
    return p2 - p1, se, z, normal_tail(z)


def logistic(ys, xs, iterations=50):
    """Logistic regression of 0/1 outcomes on rows of covariates (an intercept is added), by Newton's method:
    (coefficients, standard errors), intercept first; None when it doesn't converge (separation, too few)."""
    rows = [[1.0] + list(x) for x in xs]
    k = len(rows[0]) if rows else 0
    if not rows or len(rows) <= k:
        return None
    mean = min(max(sum(ys) / len(ys), 1e-3), 1 - 1e-3)
    beta = [math.log(mean / (1 - mean))] + [0.0] * (k - 1)
    for _ in range(iterations):
        grad, hess = [0.0] * k, [[0.0] * k for _ in range(k)]
        for y, row in zip(ys, rows):
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, sum(b * x for b, x in zip(beta, row))))))
            for i in range(k):
                grad[i] += (y - p) * row[i]
                for j in range(k):
                    hess[i][j] += p * (1 - p) * row[i] * row[j]
        step = _solve(hess, grad)
        if step is None:
            return None
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-9:
            break
    else:
        return None
    if max(abs(b) for b in beta) > 25:
        return None
    se = []
    for i in range(k):
        column = _solve(hess, [1.0 if j == i else 0.0 for j in range(k)])
        if column is None or column[i] <= 0:
            return None
        se.append(math.sqrt(column[i]))
    return beta, se


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def outcomes(found):
    """Per arm, the probed words' outcomes: {arm: [(looked up 0 or 1, read chance)]}. A glance counts as looked up."""
    out = {arm: [] for arm in fwlib.ARMS}
    for a in found:
        if isinstance(a['outcome'], dict):
            out[a['arm']].append((1 if a['outcome']['y'] > 0 else 0, a['outcome']['read']))
    return out


def analysis(found):
    """The outcome so far: {held: (looked up, probed), shown: (...), test: two_proportions or None, fit: (arm
    coefficient, its se, p, read coefficient) or None (fewer than MIN_FIT a side, or no fit)}."""
    by = outcomes(found)
    held, shown = by['held'], by['shown']
    k1, n1, k2, n2 = sum(y for y, _r in held), len(held), sum(y for y, _r in shown), len(shown)
    fit = None
    if n1 >= MIN_FIT and n2 >= MIN_FIT:
        result = logistic([y for y, _r in held + shown], [(0.0, r) for _y, r in held] + [(1.0, r) for _y, r in shown])
        if result:
            (_b0, arm, read), (_s0, se, _sr) = result
            fit = (arm, se, normal_tail(arm / se), read)
    return {'held': (k1, n1), 'shown': (k2, n2), 'test': two_proportions(k1, n1, k2, n2), 'fit': fit}


def exposure(found, log):
    """From the gloss log: (showings a day of shown-arm words in their shown days, read-weighted showings a day, leaks:
    {arm: showings while hidden}). A word's days start when it was taken in (its arm's at; else its day's start: it may
    have been shown before, as a word not introduced yet), and the shown days end now for a word still in them."""
    windows, leaks = {}, {arm: 0 for arm in fwlib.ARMS}
    for a in found:
        start = a['at'] if isinstance(a.get('at'), int) else _ms(a['day'])
        shown_end = _ms(fwlib.add_days(a['day'], fwlib.SHOW_DAYS))
        hidden = (start, _ms(a['probe'])) if a['arm'] == 'held' else (shown_end, _ms(a['probe']))
        windows[a['ja']] = (a['arm'], start, shown_end, hidden)
    shown, read, days = 0, 0.0, 0.0
    now = max((e.at for e in log.events), default=0)
    for ja, (arm, start, end, _h) in windows.items():
        if arm == 'shown' and start <= now:
            days += max(0.0, min(end, now) - start) / 86_400_000
    for event in log.events:
        if not hasattr(event, 'obs'):
            continue
        for o in event.obs:
            window = windows.get(o.word)
            if window is None:
                continue
            arm, start, end, (h0, h1) = window
            if arm == 'shown' and start <= event.at < end:
                shown += 1
                read += max(o.read, o.weight if o.y > 0 else 0.0)
            if h0 <= event.at < h1:
                leaks[arm] += 1
    return (shown / days if days else None), (read / days if days else None), leaks


def _ms(day):
    return int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)


def intake(found, day):
    """(words taken into the experiment a day over the last INTAKE_DAYS, or since it started if that is sooner, {arm:
    words assigned})."""
    since = fwlib.add_days(day, -INTAKE_DAYS)
    first = min((a['day'] for a in found), default=day)
    span = min(INTAKE_DAYS, fwlib.days_between(first, day) + 1)
    recent = sum(1 for a in found if since < a['day'] <= day)
    return recent / span, {arm: sum(1 for a in found if a['arm'] == arm) for arm in fwlib.ARMS}


def weeks_to(found, target, per_day, day):
    """Weeks from `day` until `target` words a side are due their probe: each is due fwlib.PROBE_DAY days after it came
    in, and the words still to come in arrive at per_day a day, half to a side. None when more are needed and none
    come in."""
    days = 0.0
    for arm in fwlib.ARMS:
        due = sorted(a['probe'] for a in found if a['arm'] == arm)
        if len(due) >= target:
            days = max(days, fwlib.days_between(day, due[target - 1]))
        elif not per_day:
            return None
        else:
            days = max(days, (target - len(due)) / (per_day / 2) + fwlib.PROBE_DAY)
    return max(0.0, days) / 7


def power(rate, sizes=(20, 40, TARGET), reps=200):
    """The simulated reader's power at `rate` showings a day for a shown word (tools/sim experiments.delayed_start),
    or None when tools/sim isn't here (an installed copy has only scripts and data)."""
    sim = fwlib.PLUGIN_ROOT / 'tools' / 'sim'
    if not (sim / 'experiments.py').exists():
        return None
    if str(sim) not in sys.path:
        sys.path.insert(0, str(sim))
    import experiments as simulated
    return simulated.delayed_start(rates=(round(rate, 2),), rhos=('inert', 0.0, 0.03, 0.1), sizes=sizes, pool=1500,
                                   reps=reps)


def report(state, log=None, day=None, with_power=False):
    """What fw.py experiment prints."""
    day = day or fwlib.today()
    found = arms(state, day)
    started = (state.get('experiment') or {}).get('since')
    if not found:
        return ('The delayed-start experiment: no words in it yet. Each word taken in from now gets an arm '
                '(fwlib.assign), once labels come from the model and the budget goes unspent.' if not started else
                f'The delayed-start experiment (since {started}): no words in it yet.')
    phases = {arm: {} for arm in fwlib.ARMS}
    for a in found:
        name = 'given up' if a['missed'] else 'probed' if a['outcome'] else a['phase']
        phases[a['arm']][name] = phases[a['arm']].get(name, 0) + 1
    order = ('held', 'shown', 'paused', 'probe', 'probed', 'given up')
    label = {'probe': 'probe due'}
    lines = [f'The delayed-start experiment (since {started}, seed {state["experiment"]["seed"]}): '
             f'{len(found)} words, each held back {fwlib.PROBE_DAY} days or shown {fwlib.SHOW_DAYS} and paused '
             f'{fwlib.PROBE_DAY - fwlib.SHOW_DAYS}, then probed by a guaranteed showing.']
    for arm in fwlib.ARMS:
        parts = [f'{phases[arm][p]} {label.get(p, p)}' for p in order if phases[arm].get(p)]
        lines.append(f'  {arm}: {sum(phases[arm].values())} ({", ".join(parts)})')
    if log is not None:
        per_day, read, leaks = exposure(found, log)
        if per_day is not None:
            lines.append(f'  shown words in their shown days: {per_day:.2f} showings a day ({read:.2f} read)')
        lines.append(f'  showings while hidden (leaks): held {leaks["held"]}, shown in its paused days '
                     f'{leaks["shown"]}' + ('' if not any(leaks.values()) else
                                            ' (each is exposure the design rules out: see which surface showed them)'))
    else:
        per_day = None
        lines.append('  (no gloss log here: showings and leaks are counted where the log is, the collector with one)')
    result = analysis(found)
    (k1, n1), (k2, n2) = result['held'], result['shown']
    share = lambda k, n: f'{k}/{n} = {k / n:.0%}' if n else '0/0'
    lines.append(f'Outcome, looked up at the probe: held {share(k1, n1)}, shown {share(k2, n2)}.')
    if result['test']:
        diff, se, z, p = result['test']
        lines.append(f'  shown minus held: {diff:+.2f} (standard error {se:.2f}); two-proportion z {z:+.2f}, p {p:.2f}')
    if result['fit']:
        arm, se, p, read = result['fit']
        lines.append(f'  logistic regression with the read chance: shown {arm:+.2f} on the log-odds scale (standard '
                     f'error {se:.2f}, p {p:.2f}); read chance {read:+.2f}. Negative: exposure taught (row E holds).')
    else:
        lines.append(f'  the logistic regression with the read chance comes at {MIN_FIT} probed words a side.')
    rate, assigned = intake(found, day)
    weeks = weeks_to(found, TARGET, rate, day)
    lines.append(f'Intake: {rate:.1f} words a day into the experiment lately ({assigned["held"]} '
                 f'held and {assigned["shown"]} shown so far); '
                 + (f'{TARGET} words a side due their probe in about {weeks:.1f} weeks.' if weeks is not None else
                    f'none lately, so no date for {TARGET} probed words a side.'))
    if with_power:
        table = power(per_day or 4.6)
        lines.append(table if table else 'The power estimate needs tools/sim (run it from the repository).')
        if table and per_day is None:
            lines.append('  (no showings measured yet: at the simulated reader\'s 4.6 showings a day)')
    else:
        lines.append('  fw.py experiment --power: the simulated reader\'s power at this rate of showings (tools/sim).')
    return '\n'.join(lines)


def add_arguments(parser):
    parser.add_argument('--home', help='the state dir (default: this one)')
    parser.add_argument('--power', action='store_true', help='the power from the simulated reader (about 10 s)')
    parser.add_argument('--now', type=float, help='seconds since the epoch (default: now)')


def run(args):
    import evaluate
    import knowledge
    home = Path(args.home).expanduser() if args.home else fwlib.home()
    now = args.now if args.now is not None else time.time()
    day = time.strftime('%Y-%m-%d', time.localtime(now))
    with fwlib.using_home(home):
        state = fwlib.load_state(create=False, lock=False)
        if state is None:
            sys.exit(f'Japanese Weave: no word state in {home}')
        log = None
        if (home / feedback.DB_NAME).exists():
            rows, contexts = feedback.load(feedback.default_path())
            if rows or contexts:
                reading = knowledge.load().get('reading') or {}
                log = evaluate.extract(rows, contexts, state['words'], knowledge.anki_intervals(),
                                       knowledge.read_commands(), int(now * 1000),
                                       reading.get('pace') or knowledge.PACE_MS, reading.get('top') or knowledge.READ_MAX)
    print(report(state, log, day, args.power))
