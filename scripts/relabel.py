"""fw.py relabel: every word's label under the day-count rules against the label the fitted model gives it, so the
words whose labels differ can be checked before labels come from the model (NEXT_STEPS step 8); and --restore, which
undoes the switch while the old fields last (fwlib.restore). Python 3.9+, standard library only.

The old label is the word's status as the day rules left it (its legacy status once the state has switched). The new
one is what the next refresh would give it: the fit of the whole log as knowledge.refresh makes it (the reading model
it would use, the free flags carried from knowledge.json with their hysteresis, the prior by origin), and fwlib.label:
known when free, learning once in use, else new. Nothing is written.

  fw.py relabel [--dry-run] [--home DIR] [--examples N] [--now SECONDS]
  fw.py relabel --restore
"""
from pathlib import Path
import sys
import time

import evaluate
import feedback
import fwlib
import knowledge

NAMES = {'queue': 'new', 'active': 'learning', 'shaky': 'shaky', 'known': 'known'}
# The kinds of difference, in the order they are listed, each with what it means.
KINDS = (
    ('known', 'learning', 'known by the day rules, not known at a glance by the model'),
    ('learning', 'known', 'learning by the day rules, known at a glance by the model'),
    ('shaky', 'learning', 'shaky folds into learning: not known at a glance by the model'),
    ('shaky', 'known', 'shaky by the day rules, known at a glance by the model'),
    ('new', 'known', 'not introduced, but known at a glance by the model: in use from now, free'),
    ('new', 'learning', 'not introduced, now learning'),
    ('learning', 'new', 'learning by the day rules, not introduced by the model'),
    ('known', 'new', 'known by the day rules, not introduced by the model'),
)


def preview(home=None, now=None):
    """{state, words: [{ja, en, old, new, free, k, sd, mu, p, low, high, obs, opened, recent, interval, origin,
    command}], fit, reading} for a state dir (default: this one), as of now (seconds). Raises fwlib.NoState without a
    word state or a gloss log there."""
    home = Path(home) if home else fwlib.home()
    if not (home / feedback.DB_NAME).exists():
        raise fwlib.NoState(f'no gloss log in {home}: run this where the log is (the collector, with one)')
    now = now if now is not None else time.time()
    now_ms, today = int(now * 1000), time.strftime('%Y-%m-%d', time.localtime(now))
    with fwlib.using_home(home):
        rows, contexts = feedback.load(feedback.default_path())
        state = fwlib.load_state(create=False, lock=False)
        if state is None:
            raise fwlib.NoState(f'no word state in {home}')
        previous = knowledge.load()
        intervals, commands = knowledge.anki_intervals(), knowledge.read_commands()
    reading = previous.get('reading')
    fresh = isinstance(reading, dict) and reading.get('day') == today and reading.get('model') == knowledge.MODEL
    pace, top = (reading['pace'], reading['top']) if fresh else (knowledge.PACE_MS, knowledge.READ_MAX)
    log = evaluate.extract(rows, contexts, state['words'], intervals, commands, now_ms, pace, top)
    known = previous.get('words') if isinstance(previous.get('words'), dict) else {}
    before = {ja: e['free'] for ja, e in known.items() if isinstance(e, dict) and isinstance(e.get('free'), bool)}
    if not fresh:
        reading = dict(knowledge.tune(log, now_ms, before), day=today, model=knowledge.MODEL)
        pace, top = reading['pace'], reading['top']
    fit = knowledge.Fit(log, now_ms, pace, top, free=before)
    flags = knowledge.free_flags(fit, [w['ja'] for w in state['words']], before)
    stats = knowledge.word_stats(log, now_ms)
    commanded = {c['word'] for c in commands}
    out = []
    for w in state['words']:
        ja = w['ja']
        old = (w.get('legacy') or {}).get('status', w['status'])
        k, sd = fit.estimate(ja)
        s = stats.get(ja) or {}
        out.append({'ja': ja, 'en': w['en'], 'old': NAMES.get(old, old), 'new': NAMES[fwlib.label(w, flags[ja])],
                    'free': flags[ja], 'before': before.get(ja), 'k': k, 'sd': sd, 'mu': fit.prior(ja)[0],
                    'p': knowledge.sigmoid(-k), 'low': knowledge.sigmoid(-(k + knowledge.FREE_Z * sd)),
                    'high': knowledge.sigmoid(-(k - knowledge.FREE_Z * sd)), 'obs': s.get('obs', 0),
                    'opened': s.get('opened', 0), 'recent': len(s.get('recent') or ()), 'interval': intervals.get(ja),
                    'origin': knowledge.origin_at(w, today), 'command': ja in commanded})
    return {'state': state, 'words': out, 'fit': fit, 'reading': reading}


def why(w):
    """One line on why the model labels a word as it does: its lookup chance with the ends of its uncertainty, which way
    the free line's hysteresis held it (knowledge.free_flag), and the evidence, or the prior alone."""
    line = fwlib.FREE_P
    if w['free']:
        side = (f'under {line:.0%}' if w['high'] < line else
                f'free: it started free{" by its prior" if w["before"] is None else ""} and its low end has not passed '
                f'{line:.0%}')
    else:
        side = (f'over {line:.0%}' if w['low'] > line else
                f'not free: its high end has not fallen under {line:.0%}'
                + (' (it started on its prior\'s side)' if w['before'] is None else ''))
    chance = f'lookup chance {w["p"]:.2f} ({w["low"]:.2f} to {w["high"]:.2f}), {side}'
    card = f'an Anki interval of {w["interval"]:g} days' if w['interval'] is not None else 'no Anki card'
    if w['obs']:
        recent = f', {w["recent"]} in the last 14 days' if w['recent'] else ''
        return f'{chance}; opened {w["opened"]} of {w["obs"]} times shown{recent}; {card}'
    if w['command']:
        return f'{chance}; a known or learn command, nothing shown'
    return f'{chance}; nothing shown yet, so its prior alone: {card}, origin {w["origin"]}'


def summary(found, examples=5):
    """The words whose labels differ, by kind, with up to `examples` of each and why."""
    words = found['words']
    differ = [w for w in words if w['old'] != w['new']]
    counts = {}
    for w in words:
        counts.setdefault(w['old'], {}).setdefault(w['new'], 0)
        counts[w['old']][w['new']] += 1
    fit = found['fit']
    shared = fit.shared()
    lines = [f'Labels under the day-count rules against the fitted model\'s, for {len(words)} words: {len(differ)} '
             'differ. Nothing was changed.',
             f'  the model: {len(fit.words)} words with evidence; reading {found["reading"]["pace"]} ms a word, at most '
             f'{found["reading"]["top"]}; chat offset {shared["t3"]:+.2f}; known at a glance under an '
             f'{fwlib.FREE_P:.0%} lookup chance, with hysteresis',
             '  old \\ new   ' + ''.join(f'{n:>10}' for n in ('new', 'learning', 'known'))]
    for old in ('new', 'learning', 'shaky', 'known'):
        if old in counts:
            lines.append(f'  {old:<12}' + ''.join(f'{counts[old].get(n, 0):>10}' for n in ('new', 'learning', 'known')))
    for old, new, meaning in KINDS:
        group = [w for w in differ if (w['old'], w['new']) == (old, new)]
        if not group:
            continue
        evidence = sum(1 for w in group if w['obs'])
        lines.append(f'{old} -> {new}: {len(group)} ({meaning}; {evidence} with evidence in the log, '
                     f'{len(group) - evidence} by their prior alone)')
        order = lambda ws: sorted(ws, key=lambda w: (-w['obs'], w['p'] if new == 'known' else -w['p']))
        shown, prior = order([w for w in group if w['obs']]), order([w for w in group if not w['obs']])
        half = min(len(shown), max(examples - len(prior), (examples + 1) // 2))  # both sorts, when both are there
        for w in shown[:half] + prior[:examples - half]:
            lines.append(f'  {w["ja"]} ({w["en"]}): {why(w)}')
    return '\n'.join(lines)


def add_arguments(parser):
    parser.add_argument('--dry-run', action='store_true', help='list the differences and change nothing (the default)')
    parser.add_argument('--home', help='the state dir (default: this one)')
    parser.add_argument('--examples', type=int, default=5, help='words shown for each kind of difference')
    parser.add_argument('--now', type=float, help='seconds since the epoch (default: now)')
    parser.add_argument('--restore', action='store_true',
                        help='undo the switch: labels from the day-count rules again (README "How words advance")')


def run(args):
    if args.restore:
        if args.dry_run or args.home:
            sys.exit('--restore changes the word state where it lives; it takes no --dry-run or --home')
        import hub
        result = hub.submit({'kind': 'restore'})
        if result.get('queued'):
            print(f'(Saved here: {fwlib.collector_url()} did not answer, so the next sync sends it.)')
        elif not result.get('ok'):
            sys.exit(f'Not restored: {result.get("error")}')
        elif result.get('restored') is None:
            sys.exit('Nothing to restore: labels already come from the day-count rules, or the old fields are gone '
                     f'({fwlib.LEGACY_DAYS} days after the switch).')
        else:
            print(f'Restored the day-count rules\' labels and fields for {result["restored"]} words. Now check out the '
                  'code from before the switch to run those rules (README "How words advance").')
        return
    try:
        found = preview(Path(args.home).expanduser() if args.home else None, args.now)
    except fwlib.NoState as error:
        sys.exit(f'Japanese Weave: {error}')
    print(summary(found, max(0, args.examples)))
    labels = found['state'].get('labels') or {}
    if labels.get('from') == 'model':
        print(f'(This state has had its labels from the model since {labels["since"]}; "old" is each word\'s status '
              'then, kept under its legacy field.)')
