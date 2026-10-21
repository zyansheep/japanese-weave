"""The simulated reader's experiments (run.py), each reproducing one of the review's results with the real code where
the real code applies: the replay (knowledge.py), the evaluation harness (scripts/evaluate.py) and calibrate's interval
from resampling days. Each returns its report as text; seeds are fixed, so a run repeats. Python 3.9+, standard
library only."""
import math
from pathlib import Path
import random
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / 'scripts'))
import evaluate  # noqa: E402
import feedback  # noqa: E402
import fwlib  # noqa: E402
import inflection  # noqa: E402
import knowledge  # noqa: E402
import logs  # noqa: E402
import stats  # noqa: E402
from world import DAY, HOUR, MINUTE, World, TrueWord, local_midnight, day_name, retention  # noqa: E402

START = '2026-09-01'
STATUS = ('known', 'learning', 'new')


def _share(x):
    return f'{x:.0%}' if x == x else '-'


# ---- volumes ----

def volumes(seeds=(1, 2), days=14):
    """Words shown and lookups a day, per surface and kind, against the live log's (PLAN.md section 11)."""
    lines = [f'Volumes of the simulated reader, a day (mean of {len(seeds)} seeds over {days} days); the live log: '
             'web about 8,000 words and 5 lookups, chat about 12,000 words and 35 to 40 lookups.']
    totals = {}
    for seed in seeds:
        world = World(seed=seed)
        for _ in range(days):
            for s in world.simulate_day():
                t = totals.setdefault(s.surface, {'words': 0, 'shown': 0, 'japanese': 0, 'lookups': 0, 'kinds': {}})
                t['words'] += s.words
                t['shown'] += 1
                for m in s.marks:
                    t['japanese'] += 1
                    t['lookups'] += m.looked
                    kind = m.kind if m.kind != 'word' else ('form' if m.form else m.status)
                    k = t['kinds'].setdefault(kind, [0, 0])
                    k[0] += 1
                    k[1] += m.looked
    n = len(seeds) * days
    for surface, name in (('browser', 'web'), ('t3', 'chat')):
        t = totals[surface]
        kinds = ', '.join(f'{kind} {v[1] / n:.1f} of {v[0] / n:.0f}' for kind, v in sorted(t['kinds'].items()))
        lines.append(f'  {name}: {t["shown"] / n:.0f} {"passages" if surface == "browser" else "messages"}, '
                     f'{t["words"] / n:,.0f} words, {t["japanese"] / n:.0f} Japanese, {t["lookups"] / n:.1f} lookups '
                     f'(by kind, lookups of shown: {kinds})')
    return '\n'.join(lines)


# ---- convergence of one word ----

def reading_for(chance, pace=knowledge.PACE_MS, top=knowledge.READ_MAX):
    """(ms, words, share) whose read chance is `chance`: one word, all the way down."""
    return -pace * math.log(1 - min(chance / top, 0.999)), 1, 1.0


def convergence_log(words, exposures, mu, p_true, seed=1):
    """A log of `words` words, each shown `exposures` times, 3 times a day (09:00, 14:00, 19:00) on the web, read with
    chance w ~ 0.7 Beta(2.5, 1.5) from time on screen and, when read, looked up with chance p_true. mu: their prior
    (knowledge.prior; above 1 from an Anki interval). Observations as extract() makes them."""
    rng = random.Random(seed)
    names = [f'w{i}' for i in range(words)]
    interval = math.expm1((mu - 0.5) / 0.8) if mu > 1.0 else None  # knowledge.prior's inverse
    status = 'known' if mu > 1.0 else 'queue'
    t0 = local_midnight(START)
    events = []
    for i in range(exposures):
        at = t0 + (i // 3) * DAY + (9 + 5 * (i % 3)) * HOUR
        day, key = day_name(at), ('browser', f'e{i}', 'p')
        group = evaluate.Group(at, 'browser', key, False, [])
        for name in names:
            w = knowledge.READ_MAX * rng.betavariate(2.5, 1.5)
            looked = rng.random() < w and rng.random() < p_true
            if not looked and w < 0.05:
                continue  # as knowledge.observed drops it
            ms, count, share = reading_for(w)
            group.obs.append(evaluate.Observation(
                at=at, day=day, surface='browser', key=key, episode=False, word=name, written=name, form=False,
                y=1.0 if looked else 0.0, weight=1.0 if looked else w, near=1.0 if looked else 0.0, ms=ms,
                words=count, share=share, read=w, status='known' if status == 'known' else 'new'))
        events.append(group)
    entries = [{'ja': n, 'kana': '', 'status': status, 'added': START, 'changed': START} for n in names]
    return evaluate.Log(events, {n: interval for n in names} if interval else {}, entries)


def estimates(model, log, exposures):
    """The model's P(lookup | read) for every word an hour after exposure `exposures`, fitted on everything before."""
    at = log.events[exposures - 1].at + HOUR
    model.fit(log.before(at), at)
    probe = lambda name: evaluate.Observation(at=at, day=day_name(at), surface='browser', key=('browser', 'probe', 'p'),
                                              episode=False, word=name, written=name, form=False, read=0.5)
    return [model.predict(probe(w['ja'])) for w in log.words]


class NoPull(evaluate.Today):
    """Today's model without the pull toward the prior between observations (FORGET_DAYS infinite)."""
    name = 'today without the pull'

    def fit(self, log, until):
        saved, knowledge.FORGET_DAYS = knowledge.FORGET_DAYS, float('inf')
        try:
            super().fit(log, until)
        finally:
            knowledge.FORGET_DAYS = saved

    def predict(self, obs):
        saved, knowledge.FORGET_DAYS = knowledge.FORGET_DAYS, float('inf')
        try:
            return super().predict(obs)
        finally:
            knowledge.FORGET_DAYS = saved


def convergence(words=200, checkpoints=(10, 30, 100, 300, 1000), cases=((1.0, 0.05), (1.0, 0.60), (3.0, 0.025),
                                                                         (3.0, 0.25))):
    """How many exposures a model needs to move a word from its prior to its true chance (the review's q1)."""
    models = [evaluate.MODELS[evaluate.CURRENT]] + ([lambda: evaluate.Fitted(frozen=True)]
                                                   if evaluate.CURRENT == 'fit' else []) + [evaluate.Today, NoPull]
    lines = [f'Convergence of one word: {words} words per case, each shown 3 times a day and read with chance '
             '0.7 Beta(2.5, 1.5); median estimate [10th-90th percentile] after n exposures. PLAN.md: a word starting '
             'at 27% whose true chance is 5% must cross 8% within 300 exposures. The fit learns its prior from the '
             'words, which here are all alike; "priors held" keeps the prior where it started, as for one word among '
             'many others.']
    for mu, p_true in cases:
        log = convergence_log(words, max(checkpoints), mu, p_true)
        for make in models:
            model = make()
            cells, crossed = [], None
            for n in checkpoints:
                q = estimates(model, log, n)
                median = stats.quantile(q, 0.5)
                cells.append(f'{n}: {median:.3f} [{stats.quantile(q, 0.1):.3f}-{stats.quantile(q, 0.9):.3f}]')
                if crossed is None and p_true < 0.08 <= knowledge.sigmoid(-mu) and median < 0.08:
                    crossed = n
            verdict = ''
            if p_true < 0.08 <= knowledge.sigmoid(-mu):
                verdict = f'; under 8% by {crossed} exposures' if crossed else '; never under 8% here'
            lines.append(f'  prior {knowledge.sigmoid(-mu):.3f}, true {p_true:.3f}, {model.name}: '
                         + '  '.join(cells) + verdict)
    return '\n'.join(lines)


# ---- recovery by class ----

def _replay_at(home, at):
    with fwlib.using_home(Path(home)):
        rows, contexts = feedback.load(feedback.default_path())
        state = fwlib.load_state(create=False, lock=False)
        intervals = knowledge.anki_intervals()
    return knowledge.replay(rows, contexts, inflection.lexicon(state['words']).entry, intervals, [], at)


def _estimates(name, home, log, at):
    """{word: P(lookup | read) on the web} for every word with evidence before `at`, by today's model or the fit."""
    if name == 'today':
        words, offset = _replay_at(home, at)
        return {ja: knowledge.sigmoid(offset['browser'] - w.k) for ja, w in words.items()}
    fit = knowledge.Fit(log, at, recency=name != 'step4')
    if name == 'step4':
        return {ja: fit.q(ja, 'browser') for ja in fit.words}
    reads = knowledge.word_reads(log, fit, at)  # the current chance, as the planner would price it then
    return {ja: knowledge.sigmoid(knowledge.recency_log_odds(
        fit.estimate(ja)[0], knowledge.since_read(*reads.get(ja, ((), 1.0)), at)[0], fit.theta[knowledge.FLOOR]))
        for ja in fit.words}


def recovery(seeds=(1, 2, 3), weeks=(1, 2, 4)):
    """The current model (evaluate.CURRENT), NEXT_STEPS step 4's (the fit without recency) and today's on the simulated
    reader's log: their estimates per class at 1, 2 and 4 weeks against the truth (the current model's at its current
    chance then: the clock at 08:59); their expected lookups per class against the truth's, each day predicted from
    the days before, over days 8-28 and the last week; and the reading model the profile likelihood picks
    (knowledge.tune) against the reader's true pace and cap (the review's q1)."""
    days = 7 * max(weeks)
    names = (evaluate.CURRENT, 'step4', 'today') if evaluate.CURRENT != 'today' else ('today',)
    est = {}      # (name, week, class) -> [per seed summary]
    calib = {}    # (name or 'truth' or 'seen', span, class) -> sum
    reading = []
    for seed in seeds:
        with tempfile.TemporaryDirectory() as home:
            world, snapshots = logs.simulate(home, days, checkpoints=[7 * w for w in weeks], seed=seed)
            log = evaluate.load(home, world.time() + DAY, knowledge.PACE_MS, knowledge.READ_MAX)
            for week in weeks:
                at, snapshot = snapshots[7 * week]
                for name in names:
                    found = _estimates(name, home, log, at)
                    for cls in STATUS:
                        pairs = [(e, snapshot[ja][0]) for ja, e in found.items()
                                 if ja in snapshot and snapshot[ja][1] == cls]
                        if len(pairs) >= 5:
                            est.setdefault((name, week, cls), []).append({
                                'n': len(pairs), 'est': stats.mean(e for e, _t in pairs),
                                'true': stats.mean(t for _e, t in pairs),
                                'bias': stats.mean(stats.logit(e) - stats.logit(t) for e, t in pairs),
                                'rho': stats.spearman([e for e, _t in pairs], [t for _e, t in pairs])})
            models = [evaluate.MODELS[name]() for name in names]
            result = evaluate.evaluate(log, models, evaluate.load_truth(Path(home) / logs.TRUTH))
            last = result['days'][-7:]
            for o, p, _q, true in result['rows']:
                if o.day not in result['days'][7:]:
                    continue
                cls = 'form' if o.form else o.status
                for span in ('all', 'last') if o.day in last else ('all',):
                    for name, value in list(zip(names, p)) + [('truth', true), ('seen', o.y)]:
                        calib[(name, span, cls)] = calib.get((name, span, cls), 0.0) + value
            tuned = knowledge.tune(log, world.time() + DAY)
            reading.append((seed, tuned['best'], tuned['tuned'], world.cfg['pace'], world.cfg['top']))
    lines = [f"Recovery on the simulated reader's log ({len(seeds)} seeds): P(lookup | read) of every word with "
             'evidence, at 08:59 after each week, against the true chance then (means over seeds; logit bias: mean of '
             'logit(estimate) - logit(truth)).',
             f'  {"model":<6} {"week":>4} {"class":<9} {"words":>6} {"estimate":>9} {"true":>6} {"logit bias":>11} '
             f'{"Spearman":>9}']
    for name in names:
        for week in weeks:
            for cls in STATUS:
                rows = est.get((name, week, cls))
                if rows:
                    m = lambda key: stats.mean(r[key] for r in rows)
                    lines.append(f'  {name:<6} {week:>4} {cls:<9} {m("n"):6.0f} {m("est"):9.3f} {m("true"):6.3f} '
                                 f'{m("bias"):+11.2f} {m("rho"):9.2f}')
    lines.append(f'Expected lookups against the truth\'s (true read chance times true chance) and those seen, each day '
                 f'predicted from the days before: days 8-{days}, and the last 7 days (in brackets). Within 20%: 0.8 to '
                 '1.2.')
    lines.append(f'  {"class":<9} {"truth":>13}' + ''.join(f' {name + "/truth":>15}' for name in names)
                 + f' {"seen/truth":>15}')
    for cls in STATUS + ('form', 'all'):
        get = lambda name, span: (sum(v for (n, s, c), v in calib.items() if n == name and s == span) if cls == 'all'
                                  else calib.get((name, span, cls), 0.0))
        if not get('truth', 'all'):
            continue
        ratio = lambda name: (f'{get(name, "all") / get("truth", "all"):.2f} '
                              f'({get(name, "last") / get("truth", "last"):.2f})' if get('truth', 'last') else '')
        lines.append(f'  {cls:<9} {get("truth", "all"):6.0f} ({get("truth", "last"):4.0f})'
                     + ''.join(f' {ratio(name):>15}' for name in names) + f' {ratio("seen"):>15}')
    lines.append('The reading model the profile likelihood picks on each whole log (knowledge.tune; it replaces the '
                 'defaults only when inside the grid and 3 log-likelihood units better):')
    for seed, best, tuned, pace, top in reading:
        found = '; '.join(f'{surface} {b["pace"]} ms, cap {b["top"]}' for surface, b in best.items())
        lines.append(f'  seed {seed}: best {found} ({"used" if tuned else "defaults kept"}); '
                     f'the reader\'s true {pace} ms, cap {top}')
    return '\n'.join(lines)


# ---- a surface difference ----

def offset(factors=(0.5, 1.0, 2.0), seeds=(1, 2), days=28):
    """The fitted chat offset when chat's lookup chances are a factor apart from the web's (the world's chat_factor; a
    factor of 2 is about +0.69 on the log-odds scale for small chances), for the current model (its offset, with its
    sd) and today's (PLAN.md row M: today's moves 0.02 a message)."""
    lines = [f'The chat offset against a true surface difference ({days} days, seeds {", ".join(map(str, seeds))}): '
             'the fit\'s offset (sd) and today\'s.']
    for factor in factors:
        cells = []
        for seed in seeds:
            with tempfile.TemporaryDirectory() as home:
                world, _ = logs.simulate(home, days, seed=seed, chat_factor=factor)
                now = world.time() + DAY
                fit = knowledge.Fit(evaluate.load(home, now, knowledge.PACE_MS, knowledge.READ_MAX), now)
                _words, today = _replay_at(home, now)
                cells.append(f'{fit.theta[knowledge.T3]:+.2f} ({fit.theta_sd[knowledge.T3]:.2f}) and '
                             f'{today["t3"]:+.2f}')
        lines.append(f'  chat chances x {factor:g} (log {math.log(factor):+.2f}): ' + '; '.join(cells))
    return '\n'.join(lines)


# ---- the harness on a simulated log ----

def harness(seed=1, days=28):
    """The evaluation harness on a simulated log: the fit without recency (step 4's) against the current model
    (evaluate.CURRENT), today's and a constant, with the true chances' floor; and what calibrate prints for it."""
    with tempfile.TemporaryDirectory() as home:
        world, _ = logs.simulate(home, days, seed=seed)
        now = world.time() + DAY
        log = evaluate.load(home, now, knowledge.PACE_MS, knowledge.READ_MAX)
        result = evaluate.evaluate(log, [evaluate.Stale(), evaluate.current(), evaluate.Today(), evaluate.Constant()],
                                   evaluate.load_truth(Path(home) / logs.TRUTH))
        lines = [f'The harness on a simulated log (seed {seed}, {days} days; reading at the true pace and cap; step4 is '
                 'the fit without recency, NEXT_STEPS step 4\'s model):',
                 evaluate.report_text(result, log)]
        with fwlib.using_home(Path(home)):
            know = knowledge.refresh(now / 1000)
        reading = know['reading']
        lines.append(f'calibrate on the same log, by knowledge.json\'s model (reading model by profile likelihood, '
                     f'knowledge.tune: {knowledge.reading_text(reading["pace"], reading["top"])}; best '
                     + '; '.join(f'{s} {b["pace"]} ms and {b["top"]}' for s, b in reading['best'].items())
                     + f'; the reader\'s true pace is {world.cfg["pace"]} ms, cap '
                     f'{world.cfg["top"]}):')
        lines += knowledge.classes_text(know['calibration']['classes'], know['calibration']['days'])
    return '\n'.join(lines)


# ---- the recency term ----

HOUR_U = 1 / (24 * knowledge.LONG_DAYS)  # u an hour after a read


def true_decay(word, cfg):
    """How much a word's true P(lookup | read) rises, in log odds, from an hour after a read to a week after, as written
    and at a mood of 1: from the FSRS recall at its stability now. None for a word never learned (recall 0 at any gap)
    or an inert reader's (recall stays)."""
    if cfg['inert'] or word.S <= 0:
        return 0.0
    q = lambda days: (1 - retention(word.S, days)) * cfg['habit'] + retention(word.S, days) * cfg['check']
    return stats.logit(q(knowledge.LONG_DAYS)) - stats.logit(q(1 / 24))


def fitted_decay(fit, ja):
    """The same rise by the fit: its recency term for the word, from an hour after a read to its long-term chance."""
    k = fit.estimate(ja)[0]
    return -k - knowledge.recency_log_odds(k, HOUR_U, fit.theta[knowledge.FLOOR])


def _daily_means(rows, i, j, member):
    """Per day, the mean of model j's loss minus model i's over the rows that member takes."""
    days = {}
    for o, p, _q, _t in rows:
        if member(o):
            d = days.setdefault(o.day, [0.0, 0])
            d[0] += evaluate.loss(p[j], o.y) - evaluate.loss(p[i], o.y)
            d[1] += 1
    return [d / n for d, n in days.values()]


def recency(seeds=(1, 2, 3), days=28, worlds=(('the reader', {}), ('reading refreshes, never strengthens (rho 0)',
                                                                   {'rho': 0.0}), ('an inert reader', {'inert': True}))):
    """The shared recency term (NEXT_STEPS step 10) on the simulated reader, against the fit without it (step4) and the
    truth. Its floor and context offset against the truth's (a recognised word is looked up 2% of the time, odds
    0.0204; no context effect); the decay, how much a word's chance rises from an hour after a read to a week after,
    fitted (the term, for the word's k) against true (FSRS at the word's stability at the end), weighted by the word's
    observations; each class's expected lookups against the truth's, each day predicted from the days before (days
    8-28); the log loss and the fit's paired difference with step4 by day (90% interval, resampling days); and the
    days of held-out log a 90% interval needs to exclude no difference on learning words, from the spread of the daily
    differences. Worlds: the reader; one whose reading refreshes recall but never strengthens it (rho 0); and an inert
    one, whose memory nothing changes, where the term must vanish."""
    lines = [recency.__doc__.split('(NEXT_STEPS')[0].strip() + f' ({len(seeds)} seeds of {days} days each):']
    true_floor = 0.02 / 0.98
    for world_name, cfg in worlds:
        floors, contexts, decay, calib, losses, paired, learning = [], [], {}, {}, {}, [], []
        for seed in seeds:
            with tempfile.TemporaryDirectory() as home:
                world, _ = logs.simulate(home, days, seed=seed, **cfg)
                now = world.time()
                log = evaluate.load(home, now, knowledge.PACE_MS, knowledge.READ_MAX)
                result = evaluate.evaluate(log, [evaluate.Stale(), evaluate.current()],
                                           evaluate.load_truth(Path(home) / logs.TRUTH))
                fit = knowledge.Fit(log, now)
                floors.append(math.exp(fit.theta[knowledge.FLOOR]))
                contexts.append((fit.theta[knowledge.CONTEXT], fit.theta_sd[knowledge.CONTEXT]))
                counts = {}
                for o in log.scored():
                    counts[o.word] = counts.get(o.word, 0) + 1
                for w in world.words:
                    if w.ja in fit.index and counts.get(w.ja):
                        cls = {'active': 'learning', 'queue': 'new'}.get(w.status, w.status)
                        for c in (cls, 'all'):
                            d = decay.setdefault(c, [0.0, 0.0, 0, 0])
                            d[0] += counts[w.ja] * true_decay(w, world.cfg)
                            d[1] += counts[w.ja] * fitted_decay(fit, w.ja)
                            d[2] += 1
                            d[3] += counts[w.ja]
                late = result['days'][7:]
                for o, p, _q, true in result['rows']:
                    if o.day in late:
                        for c in ('form' if o.form else o.status, 'all'):
                            for name, value in (('step4', p[0]), ('fit', p[1]), ('truth', true)):
                                calib[(name, c)] = calib.get((name, c), 0.0) + value
                                losses[(name, c)] = losses.get((name, c), 0.0) + evaluate.loss(value, o.y)
                            calib[('n', c)] = calib.get(('n', c), 0) + 1
                summary = evaluate.summary(result)['paired']['fit']
                paired.append((seed, {c: summary[c] for c in ('all', 'known', 'learning', 'new', 'browser', 't3')}))
                learning += _daily_means(result['rows'], 0, 1, lambda o: not o.form and o.status == 'learning')
        lines.append(f'{world_name}:')
        lines.append(f'  floor (odds) {", ".join(f"{f:.4f}" for f in floors)} (truth {true_floor:.4f}); context '
                     + ', '.join(f'{c:+.2f} ({sd:.2f})' for c, sd in contexts) + ' (truth 0)')
        lines.append('  decay from an hour after a read to a week after, in log odds (observation-weighted means):')
        for c in ('known', 'learning', 'new', 'all'):
            if c in decay:
                t, f, n, weight = decay[c]
                ratio = f'{f / t:.2f}' if abs(t) > 1e-9 else '-'
                lines.append(f'    {c:<9} {n:5d} words  true {t / weight:+.2f}  fitted {f / weight:+.2f}  fitted/true '
                             f'{ratio}')
        lines.append('  expected lookups over the truth\'s and log loss (days 8-28; truth\'s loss is the floor):')
        lines.append(f'    {"class":<9}{"n":>7}{"truth":>8}{"step4":>14}{"fit":>14}{"truth loss":>12}')
        for c in ('known', 'learning', 'new', 'form', 'all'):
            n = calib.get(('n', c), 0)
            if n:
                t = calib[('truth', c)]
                cell = lambda name: f'{calib[(name, c)] / t:6.2f} {losses[(name, c)] / n:.4f}'
                lines.append(f'    {c:<9}{n:>7}{t:>8.0f}  {cell("step4")}  {cell("fit")}  {losses[("truth", c)] / n:10.4f}')
        lines.append('  fit minus step4, log loss per observation by day (90% interval), days better:')
        for seed, table in paired:
            cells = []
            for c, k in table.items():
                interval = f' ({k["interval"][0]:+.4f} to {k["interval"][1]:+.4f})' if k['interval'] else ''
                cells.append(f'{evaluate.NAMES.get(c, c)} {k["diff"]:+.4f}{interval} {k["better"]}/{k["days"]}')
            lines.append(f'    seed {seed}: ' + '; '.join(cells))
        if len(learning) > 2:
            m, sd = stats.mean(learning), stats.mean([(x - stats.mean(learning)) ** 2 for x in learning]) ** 0.5
            need = (1.645 * sd / m) ** 2 if m else float('inf')
            lines.append(f'  learning words, fit minus step4 a day: mean {m:+.4f}, sd {sd:.4f} over {len(learning)} days; '
                         f'a 90% interval excludes 0 after about {need:.0f} days (if the mean were half: {4 * need:.0f})')
    return '\n'.join(lines)


# ---- calibration window ----

def calibration_days(seeds=(1, 2, 3), days=56, windows=(1, 2, 3, 5, 7, 14, 21, 28), off=1.2, draws=400):
    """How many days calibrate's check needs to tell a calibrated model from one off by 20% (the review's q4): chat
    lookups a day from the simulated reader, predicted by its true chances times 1, 1.2 or 1 / 1.2. Pass: the 90%
    interval of seen/expected from resampling days inside 0.83-1.2 (knowledge.day_interval); the old rule: seen within
    20% of expected over the window."""
    series = []
    for seed in seeds:
        world = World(seed=seed)
        run = []
        for _ in range(days):
            listed = [0.0, 0.0]
            every = [0.0, 0.0]
            for s in world.simulate_day():
                if s.surface != 't3':
                    continue
                for m in s.marks:
                    for target, keep in ((every, True), (listed, m.kind == 'word')):
                        if keep:
                            target[0] += m.read * m.q
                            target[1] += m.looked
            run.append((tuple(every), tuple(listed)))
        series.append(run)
    noise = [math.log(s / e) for run in series for (e, s), _l in run if s > 0]
    lines = [f'Calibration window: chat lookups a day from the simulated reader ({len(seeds)} seeds of {days} days, a '
             f'mood SD of {World(seed=1).cfg["day_sd"]} a day); a perfect model\'s daily log(seen/expected) has SD '
             f'{stats.mean([(x - stats.mean(noise)) ** 2 for x in noise]) ** 0.5:.2f}. P(pass) for a model that is '
             f'calibrated / {off:g} times too high / {off:g} times too low, over every window of that many days.']
    low, high = knowledge.CALIBRATED
    for which, name in ((0, 'all chat lookups'), (1, 'lookups of listed words')):
        lines.append(f'  {name}:')
        lines.append(f'    {"days":>4}  {"interval inside 0.83-1.2":>26}  {"old rule: within 20%":>22}')
        for width in windows:
            passed = {c: [0, 0] for c in (1.0, off, 1 / off)}
            for run in series:
                for start in range(0, len(run) - width + 1):
                    window = [r[which] for r in run[start:start + width]]
                    for c in passed:
                        pairs = [(c * e, s) for e, s in window]
                        interval = knowledge.day_interval(pairs, knowledge.ratio, draws=draws)
                        passed[c][0] += bool(interval and low <= interval[0] and interval[1] <= high)
                        ratio = knowledge.ratio(pairs)
                        passed[c][1] += ratio is not None and abs(ratio - 1) <= 0.2
            count = sum(len(run) - width + 1 for run in series)
            cell = lambda i: '/'.join(f'{passed[c][i] / count:.2f}' for c in (1.0, off, 1 / off))
            lines.append(f'    {width:>4}  {cell(0):>26}  {cell(1):>22}')
    return '\n'.join(lines)


# ---- the delayed-start experiment ----

def _new_word(rng, cfg):
    """A new word as it joins: never met (40%), or met before and mostly forgotten."""
    ease = math.exp(cfg['ease_sd'] * rng.gauss(0, 1))
    if rng.random() < cfg['learn_new']:
        return TrueWord({'ja': 'w'}, 'learning', 0.0, None, ease)
    return TrueWord({'ja': 'w'}, 'learning', math.exp(math.log(3.0) + 0.8 * rng.gauss(0, 1)),
                    -rng.uniform(5, 60) * DAY, ease)


def _poisson(rng, mean):
    n, total = 0, rng.expovariate(1)
    while total < mean:
        n += 1
        total += rng.expovariate(1)
    return n


def delayed_word(rng, cfg, shown, rate, inert=False, shown_days=14, probe_day=21):
    """One word of the delayed-start design (PLAN.md section 7): held back, or shown `rate` times a day for
    `shown_days` days and then paused; at probe_day 10:00 a guaranteed showing. (looked up, its read chance). inert:
    reading and lookups leave memory as it was, so both arms are alike at the probe."""
    word = _new_word(rng, cfg)
    for day in range(shown_days if shown else 0):
        for _ in range(_poisson(rng, rate)):
            t = day * DAY + int(rng.uniform(9, 22.5) * HOUR)
            if not inert and rng.random() < knowledge.READ_MAX * rng.betavariate(2.5, 1.5):
                word.read(t, rng, cfg)
    t = probe_day * DAY + 10 * HOUR
    read = knowledge.READ_MAX * rng.betavariate(2.5, 1.5)
    return float(rng.random() < read and rng.random() < word.chance(t, cfg)), read


def delayed_start(rates=(4.6, 1.0), rhos=('inert', 0.0, 0.03, 0.1), sizes=(20, 40, 80, 160), pool=3000, reps=300,
                  seed=7):
    """Power of the delayed-start experiment: held back for 3 weeks, or shown for 2 and paused for 1, then probed;
    whether the probe was looked up, by logistic regression on the arm with its read chance as a covariate (Wald test
    at 0.05). Rows "inert": exposure changes nothing in memory, so a pass is a false positive."""
    base = dict(World(seed=1).cfg)
    lines = ['Delayed start (PLAN.md section 7): power by words per arm, from pools of simulated words; rho is how '
             'much a reading strengthens memory (0: only lookups teach; inert: nothing does, the false-positive rate).',
             f'  {"a day":>5} {"rho":>6} {"probe looked up, held/shown":>28}  ' + ' '.join(f'N={n:<4}' for n in sizes)]
    rng = random.Random(seed)
    for rate in rates:
        for rho in rhos:
            cfg = dict(base, rho=0.0 if rho == 'inert' else rho)
            arms = [[delayed_word(rng, cfg, shown, rate, rho == 'inert') for _ in range(pool)]
                    for shown in (False, True)]
            power = []
            for n in sizes:
                hits = done = 0
                for _ in range(reps):
                    held, shown = rng.choices(arms[0], k=n), rng.choices(arms[1], k=n)
                    ys = [y for y, _r in held + shown]
                    xs = [(0.0, r) for _y, r in held] + [(1.0, r) for _y, r in shown]
                    fit = stats.logistic(ys, xs)
                    if fit is None:
                        continue
                    done += 1
                    hits += stats.normal_tail(fit[0][1] / fit[1][1]) < 0.05
                power.append(hits / done if done else float('nan'))
            looked = '/'.join(f'{stats.mean(y for y, _r in arm):.3f}' for arm in arms)
            lines.append(f'  {rate:5.1f} {str(rho):>6} {looked:>28}  ' + ' '.join(f'{p:6.2f}' for p in power))
    return '\n'.join(lines)


# ---- recurrence intake ----

V, KNOWN, CORE, TOPICS = 22_000, 300, 1_000, 300
WARM, MEASURE, AFTER, CAP = 60, 60, 14, 3


def _present(rng, rate, days, out, entry):
    """Poisson(rate) occurrences on each of `days`: the days with any, each with its count (at most CAP), into out."""
    p = 1 - math.exp(-rate)
    if p <= 0:
        return
    tail = [math.exp(-rate) * rate ** k / math.factorial(k) for k in range(CAP)]
    i = -1
    while True:  # the gaps between days with any are geometric
        i += 1 if p >= 1 else int(math.log(1 - rng.random()) / math.log(1 - p)) + 1
        if i >= len(days):
            return
        u, count, cum = rng.random() * p, 1, tail[1]
        while count < CAP and u > cum:
            count += 1
            cum += tail[count] if count < CAP else 1
        out.setdefault(entry, {})[days[i]] = count


def text_counts(model, tokens, seed, days=WARM + MEASURE + AFTER):
    """{entry rank: {day: occurrences, at most CAP}} for a reader's text of `tokens` dictionary tokens a day: flat
    (every entry Poisson by its Zipf share every day) or topical (entries past CORE belong to topics that come and go
    in runs of 3 days on average, a topic's interest Zipf over topics)."""
    rng = random.Random(seed)
    harmonic = sum(1 / r for r in range(1, V + 1))
    base = [tokens / (r * harmonic) for r in range(1, V + 1)]
    out = {}
    every = list(range(days))
    if model == 'flat':
        for e in range(KNOWN, V):
            _present(rng, base[e], every, out, e)
        return out
    topic = [rng.randrange(TOPICS) for _ in range(V)]
    interest = [1 / t for t in range(1, TOPICS + 1)]
    rng.shuffle(interest)
    u = [1.0 if e < CORE else interest[topic[e]] for e in range(V)]
    scale = sum(base[e] for e in range(CORE, V)) / sum(base[e] * u[e] for e in range(CORE, V))
    for e in range(CORE, V):
        u[e] *= scale  # the topical tokens keep their total
    members = {}
    for e in range(CORE, V):
        members.setdefault(topic[e], []).append(u[e])
    pi = [min(0.95, 0.1 * stats.mean(members.get(t, [1.0])) ** 0.7) for t in range(TOPICS)]
    stay = 1 - 1 / 3.0
    active, quiet = {}, {}
    for t in range(TOPICS):
        enter = min(1.0, pi[t] * (1 - stay) / max(1e-9, 1 - pi[t]))
        on = rng.random() < pi[t]
        for d in range(days):
            (active if on else quiet).setdefault(t, []).append(d)
            on = rng.random() < (stay if on else enter)
    for e in range(KNOWN, V):
        if e < CORE:
            _present(rng, base[e], every, out, e)
            continue
        t = topic[e]
        _present(rng, base[e] * u[e] / pi[t], active.get(t, []), out, e)
        _present(rng, 0.05 * base[e] * u[e], quiet.get(t, []), out, e)
    return out


def _exposures(rng, counts, day, share):
    """Exposures in the AFTER days after `day`, each day's occurrences shown with chance `share`."""
    total = 0
    for d in range(day + 1, day + 1 + AFTER):
        n = counts.get(d, 0)
        total += n if share >= 1 else sum(rng.random() < share for _ in range(n))
    return total


def thresholds(counts, need, window, rng):
    """Every word joins at its first `need` different days within `window`: (admitted a day, median rank, median
    exposures in AFTER days, share with 8 or more when all shown, when half shown) over days WARM to WARM + MEASURE."""
    joined = []
    for e, by_day in counts.items():
        seen = sorted(by_day)
        for i in range(need - 1, len(seen)):
            if seen[i] - seen[i - need + 1] < window:
                if WARM <= seen[i] < WARM + MEASURE:
                    joined.append((e, seen[i]))
                break
    return _intake(counts, joined, rng, MEASURE)


def ranked(counts, per_day, rng, need=3, window=30, start=30):
    """Each day from `start`, the per_day eligible words (need different days within window) seen on the most days in
    it join, the more frequent first at a tie."""
    by_day = {}
    for e, seen in counts.items():
        for d in seen:
            by_day.setdefault(d, []).append(e)
    inside, joined, picked = {}, set(), []
    for d in range(WARM + MEASURE):
        for e in by_day.get(d, []):
            inside[e] = inside.get(e, 0) + 1
        for e in by_day.get(d - window, []):
            inside[e] -= 1
        if d < start:
            continue
        k = int(per_day) + (rng.random() < per_day % 1)
        eligible = sorted((e for e, n in inside.items() if n >= need and e not in joined),
                          key=lambda e: (-inside[e], e))[:k]
        joined.update(eligible)
        if d >= WARM:
            picked += [(e, d) for e in eligible]
    return _intake(counts, picked, rng, MEASURE)


def _intake(counts, joined, rng, days):
    if not joined:
        return 0.0, float('nan'), float('nan'), float('nan'), float('nan')
    full = [_exposures(rng, counts[e], d, 1.0) for e, d in joined]
    half = [_exposures(rng, counts[e], d, 0.5) for e, d in joined]
    return (len(joined) / days, stats.quantile([e + 1 for e, _d in joined], 0.5), stats.quantile(full, 0.5),
            stats.mean(x >= 8 for x in full), stats.mean(x >= 8 for x in half))


def recurrence(models=('flat', 'topical'), tokens=(10_000, 5_000), rules=((3, 30), (5, 30), (8, 30), (12, 30)),
               intakes=(2, 5, 20), seed=1):
    """Do words nominated by the reader's text come back (the review's q5, PLAN.md section 6)? A dictionary of 22,000
    entries, Zipf by rank; the reader's text has `tokens` entry tokens a day; the top 300 are known. Words joining on
    days 60-119: how many a day, their median rank, median exposures in their first 14 days (at most 3 a day), and the
    share with 8 or more when every occurrence is shown and when half are."""
    rng = random.Random(seed)
    lines = [recurrence.__doc__.split('?')[0] + '? ' + ' '.join(recurrence.__doc__.split('?')[1].split())]
    header = f'    {"rule":<14} {"a day":>6} {"median rank":>11} {"exposures":>9} {">= 8":>6} {">= 8, half":>10}'
    for model in models:
        for n in tokens:
            counts = text_counts(model, n, seed)
            lines.append(f'  {model} text, {n:,} tokens a day:')
            lines.append(header)
            for need, window in rules:
                row = thresholds(counts, need, window, rng)
                lines.append(f'    {f"{need} in {window} days":<14} {row[0]:6.1f} {row[1]:11.0f} {row[2]:9.0f} '
                             f'{_share(row[3]):>6} {_share(row[4]):>10}')
            for k in intakes:
                row = ranked(counts, k, rng)
                lines.append(f'    {f"{k} a day, ranked":<14} {row[0]:6.1f} {row[1]:11.0f} {row[2]:9.0f} '
                             f'{_share(row[3]):>6} {_share(row[4]):>10}')
    return '\n'.join(lines)


# ---- starting values from the word itself ----

def _first(o):
    """A word's first exposures (evaluate.FIRST) while it is new or learning: where the word features decide."""
    return o.seen is not None and o.seen < evaluate.FIRST and o.status in knowledge.FEATURED


def _daily(rows, member, i=1, j=0):
    """{day: [sum of loss differences, n]} of model i against model j over the rows that are members."""
    out = {}
    for o, p, _q, _t in rows:
        if member(o):
            d = out.setdefault(o.day, [0.0, 0])
            d[0] += evaluate.loss(p[i], o.y) - evaluate.loss(p[j], o.y)
            d[1] += 1
    return out


def _paired(days):
    """(mean difference, 90% interval or None, days better, days) over [(sum, n)] days."""
    interval = knowledge.day_interval(days, evaluate._mean_difference)
    return evaluate._mean_difference(days), interval, sum(1 for d, _n in days if d < 0), len(days)


def _oracle(world, fit, names, target=None):
    """Least squares of each featured word's true k on the first day (its log-odds of not looking it up when read, mood
    1), or its target ({ja: k}), on an intercept and its word features as the world drew them, over the fit's words
    whose start the features drew: what the fit's coefficients would be with every word's k known."""
    known = knowledge.kanji_counts(w.ja for w in world.words if w.kind == 'known')
    xs, ys = [], []
    for ja in fit.words:
        w = world.by_ja.get(ja)
        if w is None or w.k0 is None or world.entries.get(ja) is None or (target is not None and ja not in target):
            continue
        x = knowledge.word_features(world.entries[ja], known)
        xs.append([1.0] + [x.get(n, 0.0) for n in names])
        ys.append(w.k0 if target is None else target[ja])
    k = len(names) + 1
    a = [[sum(r[i] * r[j] for r in xs) + (1e-6 if i == j else 0.0) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * y for r, y in zip(xs, ys)) for i in range(k)]
    beta = stats._solve(a, b)
    return dict(zip(['intercept'] + list(names), beta)) if beta else {}, len(ys)


OVERCLAIM = 0.1  # the share of words not recognised, and of made-up ones, the simulated reader claims in a probe


def prior_run(seed, days, truth, extra, widths=(None,), probes=False):
    """One simulated log of the world with starting values from features: the evaluation of the flat start against
    the word features, for each prior width of the coefficients (None: knowledge.WORD_SD), and the coefficients of a
    fit of the whole log against the truth's. With probes, the reader answers a monthly probe the evening before the
    first day and after the last (cfg probe_days, overclaim OVERCLAIM): the models above leave the answers out, and at
    the default width the fit with them (knowledge.Fit's probes) is evaluated too, as rows' third column, and checked
    against the last probe (probe_checks). {width: {rows, fit, days}, shown, probe}, world."""
    out = {}
    with tempfile.TemporaryDirectory() as home:
        world, _ = logs.simulate(home, days, seed=seed, prior=truth, extra=extra,
                                 **({'probe_days': (0, days), 'overclaim': OVERCLAIM} if probes else {}))
        now = world.time() + DAY
        for width in widths:
            saved = dict(knowledge.SHARED)
            if width is not None:
                knowledge.SHARED.update({n: (0.0, width) for n in knowledge.WORD_FEATURES})
            try:
                log = evaluate.load(home, now, knowledge.PACE_MS, knowledge.READ_MAX)
                models = [evaluate.Fitted(plain=True, probes=False), evaluate.Fitted(probes=False)]
                if probes and width is None:
                    models.append(evaluate.Fitted())
                result = evaluate.evaluate(log, models, evaluate.load_truth(Path(home) / logs.TRUTH))
                models[1].fit(log, now)
                out[width] = {'rows': result['rows'], 'fit': models[1].last, 'days': result['days']}
                if len(models) > 2:
                    out['probe'] = probe_checks(world, log)
                if width == widths[0]:  # each word's true k averaged over its showings, as the fit's k is
                    truth, shown = evaluate.load_truth(Path(home) / logs.TRUTH), {}
                    for o in log.scored():
                        found = truth.get((o.key[0], o.key[1], o.key[2], o.word))
                        if found:
                            shown.setdefault(o.word, []).append(-stats.logit(found[0]))
                    out['shown'] = {ja: stats.mean(ks) for ja, ks in shown.items()}
            finally:
                knowledge.SHARED.clear()
                knowledge.SHARED.update(saved)
    return out, world


def _refreshed(log, until, probes):
    """A fit as a refresh makes it (prior_reach): the free flags from a fit of the day before, then the fit."""
    first = knowledge.Fit(log, until - DAY, probes=probes)
    flags = knowledge.free_flags(first, [w['ja'] for w in log.words], {})
    return knowledge.Fit(log, until, free=flags, probes=probes)


def _recognised(fit, ja, entry, table):
    """P(the reader recognises a word at a glance out of context) by a fit: sigmoid(k - probe) averaged over k's
    uncertainty (the probit approximation, MacKay 1992), k the word's estimate, else its prior (for a word the fit has
    no features for, from its entry: knowledge.prior_estimate)."""
    if ja in fit.index or (ja in fit.by_ja and fit.entries.get(ja) is not None):
        k, sd = fit.estimate(ja)
    else:
        k, sd = knowledge.prior_estimate(table, 'new', None, entry)
    return knowledge.sigmoid((k - fit.theta[knowledge.PROBE]) / math.sqrt(1 + math.pi * sd * sd / 8))


def probe_checks(world, log):
    """The last probe as the check on the model: fits as a refresh makes them just before it, without and with the
    earlier probes' answers, and its answers predicted by each (log loss per answer, P(known) = 1 - r q at each fit's
    over-claiming rate: knowledge.Fit.known_chance), the goal (the population's weighted share recognised) by each
    fit, by the probe's own estimate from its answers alone (corrected for guessing) and by the truth then; and the
    words the earlier probes answered (left out where only starting values count)."""
    import dictionary
    import probe
    d = dictionary.load()
    last = world.probes[-1]
    fits = {name: _refreshed(log, last['at'], name == 'with') for name in ('without', 'with')}
    answers, _fakes, extra = knowledge.probe_answers([last], log.entries, d)
    entries = dict(log.entries, **extra)
    out = {'probed': {ja for _at, ja, _y in knowledge.probe_answers(world.probes[:-1], log.entries, d)[0]},
           'truth': world.goal(last['at']), 'estimate': last['estimate'].get('corrected', last['estimate']['known']),
           'answers': len(answers), 'fits': fits}
    frame = world.frame or probe.Frame(d)
    total = sum(it['weight'] for it in frame.items)
    by_id = {e['id']: ja for ja, e in log.entries.items() if e is not None}
    for name, fit in fits.items():
        table = {'prior': knowledge.prior_table(fit)}
        loss = []
        for _at, ja, y in answers:
            known = 1 - fit.probe_read * (1 - _recognised(fit, ja, entries.get(ja), table))
            loss.append(evaluate.loss(known, 1 - y))
        goal = 0.0
        for it in frame.items:
            entry = d.get(it['ids'][0])
            ja = by_id.get(entry['id'], entry['ja'])
            goal += it['weight'] * _recognised(fit, ja, entries.get(ja) or entry, table)
        out[name] = {'loss': stats.mean(loss), 'goal': goal / total, 'offset': fit.theta[knowledge.PROBE],
                     'read': fit.probe_read}
    return out


def priors(seeds=(1, 2, 3), days=28, extra=2000, truth=None, widths=(0.25, None, 1.0, 2.0),
           windows=(3, 5, 7, 10, 14, 21), reach=True, probes=True):
    """Starting values from the word itself (NEXT_STEPS step 13) on a world whose words start from their features
    (world.py, cfg prior; world.PRIOR the true coefficients): the fitted coefficients against the true ones and the
    oracle's, and the word features against the flat start (the prior by origin and Anki interval alone) on each
    word's first exposures while new or learning, day by day; with probes, what the monthly probe's answers add
    (probe_text); with reach, how far the prior reaches (prior_reach), with and without the probe."""
    from world import PRIOR
    truth = truth or PRIOR
    names = knowledge.WORD_FEATURES
    runs = []
    for seed in seeds:
        run, world = prior_run(seed, days, truth, extra, widths, probes)
        runs.append((seed, run, world))
    lines = [f'Starting values from the word itself: {len(seeds)} seeds of {days} days, {extra} extra dictionary '
             f'entries in the queue. Each word not known starts at k0 = {truth["base"]} + sum beta x + '
             f'{truth["noise"]} z (world.py). Coefficients of a fit of the whole log, with 90% intervals (+- 1.645 '
             'sd), against the truth and two oracles, least squares over the same words on the features of their '
             'true k on the first day (k0) and averaged over their showings (shown: what a k for all of a word\'s '
             'exposures, as the fit\'s, can recover when reading and lookups teach), per seed:']
    lines.append(f'  {"feature":<16} {"true":>6}' + ''.join(f'  {"seed " + str(s) + " fit":>21} {"k0":>6} {"shown":>6}'
                                                        for s, _r, _w in runs))
    oracles = [(_oracle(world, run[None]['fit'], names)) for _s, run, world in runs]
    shown = [(_oracle(world, run[None]['fit'], names, run['shown'])) for _s, run, world in runs]
    for name in names:
        cells = ''
        for (seed, run, world), (oracle, _n), (seen, _m) in zip(runs, oracles, shown):
            fit = run[None]['fit']
            j = knowledge.SHARED_NAMES.index(name)
            mean, sd = fit.theta[j], fit.theta_sd[j]
            cells += (f'  {mean:+6.2f} ({mean - 1.645 * sd:+.2f} to {mean + 1.645 * sd:+.2f})'
                      f' {oracle.get(name, 0):+6.2f} {seen.get(name, 0):+6.2f}')
        lines.append(f'  {name:<16} {truth.get(name, 0.0):+6.2f}' + cells)
    lines.append('  featured words in each fit: ' + ', '.join(str(n) for _o, n in oracles))
    for width in widths:
        label = f'prior sd {width if width is not None else knowledge.WORD_SD:g}'
        all_days, first_days, errors = [], [], []
        for (seed, run, world), (oracle, _n) in zip(runs, oracles):
            r = run[width]
            all_days += list(_daily(r['rows'], lambda o: True).values())
            first_days += list(_daily(r['rows'], _first).values())
            fit = r['fit']
            errors += [(fit.theta[knowledge.SHARED_NAMES.index(n)] - oracle.get(n, 0.0)) ** 2 for n in names]
        mean, interval, better, n = _paired(first_days)
        rmse = math.sqrt(stats.mean(errors))
        interval = f'{interval[0]:+.4f} to {interval[1]:+.4f}' if interval else '-'
        losses = [(evaluate.loss(p[0], o.y), evaluate.loss(p[1], o.y), evaluate.loss(t, o.y))
                  for _s, run, _w in runs for o, p, _q, t in run[width]['rows'] if _first(o) and t is not None]
        flat, fitted, true = (stats.mean(x[i] for x in losses) for i in range(3))
        lines.append(f'{label}: word features against the flat start, log loss per observation on first exposures '
                     f'(first {evaluate.FIRST} of a new or learning word, {len(losses)}): {mean:+.4f} ({interval}), '
                     f'better on {better} of {n} days; flat {flat:.4f}, features {fitted:.4f}, the true chances '
                     f'{true:.4f} (closing {(flat - fitted) / (flat - true):.0%} of the gap); all observations '
                     f'{_paired(all_days)[0]:+.4f}; coefficients off the oracle\'s by {rmse:.2f} (root mean square)')
    seed, run, _world = runs[0]
    daily = _daily(run[None]['rows'], _first)
    lines.append(f'Seed {seed}, day by day (first exposures; difference x 1000, observations): ' + ', '.join(
        f'{day[5:]} {1000 * d / n:+.0f} ({n})' for day, (d, n) in sorted(daily.items())))
    lines.append('Days of log needed: of the replays of each seed\'s first n days (a log n days old), how many have '
                 'the 90% interval of the difference on first exposures below 0:')
    for n in windows:
        hits = 0
        for _seed, run, _world in runs:
            window = [v for _d, v in sorted(_daily(run[None]['rows'], _first).items())[:n]]
            interval = knowledge.day_interval(window, evaluate._mean_difference)
            hits += bool(len(window) == n and interval and interval[1] < 0)
        lines.append(f'  {n:>2} days: {hits} of {len(runs)}')
    daily = [d / c for _s, run, _w in runs for _day, (d, c) in sorted(_daily(run[None]['rows'], _first).items())[1:]]
    mean, sd = stats.mean(daily), stats.mean([(x - stats.mean(daily)) ** 2 for x in daily]) ** 0.5
    if mean < 0:
        lines.append(f'  Each day after the first (when both start alike): {mean:+.4f} a day, sd {sd:.4f}; a 90% '
                     f'interval excludes 0 in half of the logs after about {(1.645 * sd / mean) ** 2:.0f} days, in 80% '
                     f'after about {(2.487 * sd / mean) ** 2:.0f}.')
    if probes:
        lines.append(probe_text(runs, truth))
    if reach:
        lines.append(prior_reach(seeds, days, extra, truth, probes=probes))
    return '\n'.join(lines)


def probe_text(runs, truth):
    """priors' lines on the monthly probe (prior_run with probes): the rank coefficient with and without its answers,
    the fit with them against the fit without on first exposures, day by day, and the last probe as the check."""
    j = knowledge.SHARED_NAMES.index('rank')
    lines = [f'The monthly probe (probe.py): 60 words and 6 made up, answered the evening before the first day and '
             f'after the last from the reader\'s true recall, claiming {OVERCLAIM:.0%} of the words not recognised and '
             'of the made-up ones; the fit with the first probe\'s answers against the fit without (rows\' third '
             'column against the second), per seed:']
    for seed, run, _world in runs:
        check = run['probe']
        without, with_ = check['fits']['without'], check['fits']['with']
        lines.append(f'  seed {seed}: rank coefficient {without.theta[j]:+.2f} (sd {without.theta_sd[j]:.2f}) '
                     f'without, {with_.theta[j]:+.2f} (sd {with_.theta_sd[j]:.2f}) with (true {truth["rank"]:+.2f}); '
                     f'probe offset {with_.theta[knowledge.PROBE]:+.2f} (sd {with_.theta_sd[knowledge.PROBE]:.2f}), '
                     f'over-claiming {1 - with_.probe_read:.0%} from the made-up words; the last probe\'s '
                     f'{check["answers"]} answers, log loss per answer {check["without"]["loss"]:.4f} without, '
                     f'{check["with"]["loss"]:.4f} with; the goal {check["truth"]:.1%} true, {check["estimate"]:.1%} '
                     f'by the probe\'s answers alone, {check["without"]["goal"]:.1%} by the fit without, '
                     f'{check["with"]["goal"]:.1%} with')
    for label, member in (('first exposures', _first),
                          ('first exposures of words no probe asked', None)):
        days_ = []
        for _seed, run, _world in runs:
            probed = run['probe']['probed']
            test = member or (lambda o, probed=probed: _first(o) and o.word not in probed)
            days_ += list(_daily(run[None]['rows'], test, 2, 1).values())
        mean, interval, better, n = _paired(days_)
        interval = f'{interval[0]:+.4f} to {interval[1]:+.4f}' if interval else '-'
        count = sum(c for _d, c in days_)
        lines.append(f'  {label} ({count}): {mean:+.4f} per observation ({interval}), better on {better} of {n} days')
    days_ = [v for _seed, run, _world in runs for v in _daily(run[None]['rows'], lambda o: True, 2, 1).values()]
    lines.append(f'  all observations: {_paired(days_)[0]:+.4f} per observation')
    return '\n'.join(lines)


def prior_reach(seeds=(1, 2, 3), days=28, extra=2000, truth=None, bands=(1, 3000, 10_000, 30_000), probes=True):
    """How far the prior reaches: a world whose extra dictionary entries are never shown (cfg extra_shown False), so
    the coefficients are learned from the lexicon's words alone (most within the first 5,000 ranks), and the extra
    entries' starting chances from a refresh's fit (two fits, the second with the first's free flags) against the
    truth's, by rank band: mean predicted and true chance, and the mean error on the log-odds scale, flat and with
    the word features; with probes, also with the answers of a probe the evening before the first day (overclaim
    OVERCLAIM), the entries it asked about left out (their k is no longer a starting value)."""
    from world import PRIOR
    truth = truth or PRIOR
    sums, probed = {}, 0
    for seed in seeds:
        with tempfile.TemporaryDirectory() as home:
            world, _ = logs.simulate(home, days, seed=seed, prior=truth, extra=extra, extra_shown=False,
                                     **({'probe_days': (0,), 'overclaim': OVERCLAIM} if probes else {}))
            now = world.time() + DAY
            log = evaluate.load(home, now, knowledge.PACE_MS, knowledge.READ_MAX)
            first = knowledge.Fit(log, now - DAY, probes=False)
            flags = knowledge.free_flags(first, [w['ja'] for w in log.words], {})
            fits = {'flat': knowledge.Fit(log, now, plain=True, probes=False),
                    'features': knowledge.Fit(log, now, free=flags, probes=False)}
            asked = set()
            if probes:
                fits['probe'] = _refreshed(log, now, True)
                asked = {ja for _at, ja, _y in knowledge.probe_log(log)[0]}
            for w in world.extras:
                if w.ja in asked:
                    probed += 1
                    continue
                rank = world.entries[w.ja]['rank']
                band = next(b for b in bands[1:] if rank < b)
                for name, fit in fits.items():
                    k = fit.prior(w.ja)[0]
                    s = sums.setdefault((band, name), [0, 0.0, 0.0, 0.0])
                    s[0] += 1
                    s[1] += knowledge.sigmoid(-k)
                    s[2] += knowledge.sigmoid(-w.k0)
                    s[3] += k - w.k0
    lines = [f'How far the prior reaches ({len(seeds)} seeds of {days} days): {extra} dictionary entries never shown, '
             'their starting chance from the fit of the rest (mean predicted against the true one), and the mean '
             'error on the log-odds scale (predicted k - true k; positive: thought better known), by rank band'
             + (f'; probe: with a probe\'s answers too, the {probed} entries it asked about left out' if probes else '')
             + ':']
    for low, high in zip(bands, bands[1:]):
        cells = []
        for name in ('flat', 'features', 'probe'):
            n, p, t, e = sums.get((high, name), [0, 0, 0, 0])
            if n:
                cells.append(f'{name} {p / n:.0%} against {t / n:.0%}, error {e / n:+.2f}')
        if cells:
            lines.append(f'  ranks {low:,} to {high - 1:,} ({sums[(high, "flat")][0]} words): ' + '; '.join(cells))
    return '\n'.join(lines)


# ---- reading speed as evidence ----

SPEED_TRUTH = {'unknown': 10.0, 'japanese': 1.0, 'dwell': True, 'group': 0.3}  # world.py cfg speed: 10 words of time
SPEED_WORLDS = (  # (name, cfg): the term must find nothing in the first and its effect in the others
    ('no effect: time grows with each Japanese word read and each gloss opened, not with words not recognised',
     {'speed': dict(SPEED_TRUTH, unknown=0.0)}),
    ('a word read and not recognised costs 10 words of time (3 s), looked up 90% of the time',
     {'speed': SPEED_TRUTH}),
    ('the same, looked up 50% of the time', {'speed': SPEED_TRUTH, 'habit': 0.5}),
    ('a word read and not recognised costs 40 words of time (12 s), looked up 90% of the time',
     {'speed': dict(SPEED_TRUTH, unknown=40.0)}),
)
SPEED_CLASSES = ('all', 'known', 'learning', 'new', 'first', 'browser', 't3')


class _SpeedOracle:
    """The speed term's first stage (knowledge.Fit._speed_alone) with every word at its true chance when shown (the
    truth's P(lookup | read)) instead of the fit's: the parameters the term would find if it knew every word's chance,
    the yardstick for the fitted ones (unknown is in units of that chance: a reader who looks up half the words not
    recognised has twice as many unknown words behind a chance as one who looks up all of them)."""
    _speed_pass, _speed_alone = knowledge.Fit._speed_pass, knowledge.Fit._speed_alone
    _timed, _temper = False, None

    def __init__(self, log, until, truth):
        self.names, self.priors, self.group_sd = knowledge.SPEED_NAMES, knowledge.SPEED_SHARED, knowledge.SPEED_GROUP_SD
        self.units, groups, self.k = [], {}, []
        for event in log.events:
            if event.at >= until:
                break
            for ms, count, japanese, listed, _active, _dwell in getattr(event, 'units', ()):
                if ms < knowledge.SPEED_THROUGH * knowledge.by_surface(log.pace, event.surface) * count:
                    continue
                t3 = event.surface == 't3'
                ws = []
                for ja in listed:
                    q = truth.get((event.key[0], event.key[1], event.key[2], ja), (0.5, 0))[0]
                    q = min(1 - 1e-6, max(1e-6, q))
                    ws.append(len(self.k))
                    self.k.append(math.log((1 - q) / q))
                group = groups.setdefault(event.key if t3 else event.key[:2], len(groups))
                decay = 0.5 ** ((until - event.at) / DAY / knowledge.EVIDENCE_DAYS)
                self.units.append((t3, group, math.log(ms), math.log(count / knowledge.SPEED_WORDS), japanese / count,
                                   1 / count, tuple(ws), decay * knowledge.SPEED_WEIGHT))
        self.alpha = [0.0] * len(groups)
        self.group_units = [[] for _ in groups]
        for j, unit in enumerate(self.units):
            self.group_units[unit[1]].append(j)

    def fit(self):
        """(theta, sd): the term's parameters and their sd."""
        theta = self._speed_alone(self.k, [self.priors[name][0] for name in self.names])
        return theta, [self.speed_sd[name] for name in self.names]


def _speed_member(cls):
    if cls == 'first':
        return _first
    if cls in ('browser', 't3'):
        return lambda o: o.surface == cls
    if cls == 'all':
        return lambda o: True
    return lambda o: not o.form and o.status == cls


def speed_run(seed, days, cfg):
    """One simulated log of a world (cfg): the fit with the speed term against the fit without it, day by day ({class:
    [(sum of loss differences, n)] by day}), and expected lookups per class (each model's, the truth's, those seen);
    and the term on the whole log, fitted and with every word's chance known (_SpeedOracle)."""
    with tempfile.TemporaryDirectory() as home:
        world, _ = logs.simulate(home, days, seed=seed, **cfg)
        now = world.time()
        log = evaluate.load(home, now, knowledge.PACE_MS, knowledge.READ_MAX)
        truth = evaluate.load_truth(Path(home) / logs.TRUTH)
        models = [evaluate.MODELS['nospeed'](), evaluate.MODELS['speed']()]
        result = evaluate.evaluate(log, models, truth)
        fit = knowledge.Fit(log, now, speed=True)
        oracle = _SpeedOracle(log, now, truth)
        theta, sd = oracle.fit()
        rows = result['rows']
        return {'seed': seed, 'fit': fit.speed_summary(),
                'seconds': (fit.seconds, knowledge.Fit(log, now, speed=False).seconds),
                'oracle': {n: (v, s) for n, v, s in zip(oracle.names, theta, sd)}, 'oracle group': oracle.group_sd,
                'daily': {cls: list(_daily(rows, _speed_member(cls)).values()) for cls in SPEED_CLASSES},
                'expected': {cls: [sum(r[1][0] for r in rows if _speed_member(cls)(r[0])),
                                   sum(r[1][1] for r in rows if _speed_member(cls)(r[0])),
                                   sum(r[3] for r in rows if _speed_member(cls)(r[0])),
                                   sum(r[0].y for r in rows if _speed_member(cls)(r[0]))] for cls in SPEED_CLASSES}}


def speed(seeds=(1, 2, 3), days=28, worlds=SPEED_WORLDS, processes=1):
    """Reading speed as evidence (NEXT_STEPS step 19; knowledge.SPEED) on the simulated reader with a true reading-time
    process (world.py cfg speed): log ms of a passage or paragraph grows with each Japanese word read (1 word of time),
    each word read and not recognised (`unknown` words of time, looked up or not), each gloss's time open, and a page's
    or message's own pace (sd 0.3 on log time). For each world: the term's parameters on the whole log (unknown and
    japanese with their sd, and the groups' sd) against the same term's with every word's true chance known (the
    oracle); how much of the words' information came from time (most: the largest share of any word's); the speed
    fit's log loss against the fit without it, day by day (90% interval from resampling days, days better), per class
    and seed, then pooled, and the days a 90% interval needs to exclude no difference, from the spread of the daily
    differences; and expected lookups over the truth's, both fits. processes: runs at once (seeds and worlds)."""
    tasks = [(seed, days, cfg) for _name, cfg in worlds for seed in seeds]
    if processes > 1:
        import multiprocessing
        with multiprocessing.Pool(processes) as pool:
            runs = pool.starmap(speed_run, tasks)
    else:
        runs = [speed_run(*task) for task in tasks]
    lines = [speed.__doc__.split(' For each world')[0].strip() + f' {len(seeds)} seeds of {days} days each.']
    for w, (world_name, _cfg) in enumerate(worlds):
        lines.append(f'{world_name}:')
        mine = runs[w * len(seeds):(w + 1) * len(seeds)]
        for run in mine:
            f, o = run['fit'], run['oracle']
            cell = lambda name: (f'{f["shared"][name]:+.2f} ({f["sd"][name]:.2f}), oracle {o[name][0]:+.2f} '
                                 f'({o[name][1]:.2f})')
            lines.append(f'  seed {run["seed"]}: unknown {cell("unknown")}; japanese {cell("japanese")}; '
                         f'length web {f["shared"]["length web"]:.2f}, chat {f["shared"]["length chat"]:.2f}; groups\' '
                         f'sd {f["group"]:.2f} (oracle {run["oracle group"]:.2f}); {f["units"]} passages and '
                         f'paragraphs; time\'s share of the words\' information {f["share"]:.2%}, at most '
                         f'{f["most"]:.0%}; a fit from nothing {run["seconds"][0]:.1f} s (without the term '
                         f'{run["seconds"][1]:.1f} s)')
            cells = []
            for cls in SPEED_CLASSES:
                mean, interval, better, n = _paired(run['daily'][cls])
                if mean is None:
                    continue
                interval = f' ({interval[0]:+.4f} to {interval[1]:+.4f})' if interval else ''
                cells.append(f'{evaluate.NAMES.get(cls, cls)} {mean:+.4f}{interval} {better}/{n}')
            lines.append('    speed minus nospeed, log loss per observation by day: ' + '; '.join(cells))
        lines.append('  pooled over seeds: a day\'s mean difference (sd), days better, and the days a 90% interval '
                     'needs to exclude 0; expected lookups over the truth\'s without and with the term:')
        for cls in SPEED_CLASSES:
            xs = [d / n for run in mine for d, n in run['daily'][cls]]
            sums = [sum(run['expected'][cls][i] for run in mine) for i in range(4)]
            if len(xs) > 2:
                m = stats.mean(xs)
                sd = stats.mean([(x - m) ** 2 for x in xs]) ** 0.5
                need = (1.645 * sd / m) ** 2 if m else float('inf')
                ratio = f'{sums[0] / sums[2]:.2f} and {sums[1] / sums[2]:.2f}' if sums[2] else '-'
                lines.append(f'    {evaluate.NAMES.get(cls, cls):<9} {m:+.5f} ({sd:.5f}) over {len(xs)} days, '
                             f'better on {sum(1 for x in xs if x < 0)}; {need:,.0f} days; {ratio}')
    return '\n'.join(lines)
