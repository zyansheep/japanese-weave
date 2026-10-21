#!/usr/bin/env python3
"""The simulated reader (PLAN.md section 10, NEXT_STEPS step 3): a reader whose memory, reading and lookup habits are
known (world.py), whose days are written as the live system writes them (logs.py), and the experiments that check
models and designs against the truth (experiments.py). Python 3.9+, standard library only; seeds are fixed.

  python3 tools/sim/run.py all                 every experiment below, in about a minute
  python3 tools/sim/run.py volumes             words shown and lookups a day, against the live log's
  python3 tools/sim/run.py convergence         exposures needed to move one word from its prior to its true chance
  python3 tools/sim/run.py recovery            today's model per class (known, learning, new) at 1, 2 and 4 weeks
  python3 tools/sim/run.py harness             scripts/evaluate.py on a simulated log: today's model against a
                                               constant, with the true chances' floor; and calibrate's classes
  python3 tools/sim/run.py calibration-days    days calibrate needs to tell a calibrated model from one off by 20%
  python3 tools/sim/run.py delayed-start       power of the delayed-start experiment (PLAN.md section 7)
  python3 tools/sim/run.py recurrence          whether words nominated by the reader's text come back (section 6)
  python3 tools/sim/run.py log DIR [--days N] [--seed S]
                                               a simulated state dir (interactions.sqlite, state.json, anki.csv,
                                               truth.jsonl, and knowledge.json from the replay) for
                                               JAPANESE_WEAVE_HOME=DIR scripts/fw.py calibrate, or
                                               scripts/evaluate.py --home DIR

Nothing here reads or writes your word state: the state dir is a temporary one before anything runs.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ['JAPANESE_WEAVE_HOME'] = tempfile.mkdtemp(prefix='jw-sim-home-')  # never yours
os.environ['JAPANESE_WEAVE_NO_REFRESH'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiments  # noqa: E402
import fwlib  # noqa: E402
import knowledge  # noqa: E402
import logs  # noqa: E402

EXPERIMENTS = {
    'volumes': experiments.volumes,
    'convergence': experiments.convergence,
    'recovery': experiments.recovery,
    'harness': experiments.harness,
    'calibration-days': experiments.calibration_days,
    'delayed-start': experiments.delayed_start,
    'recurrence': experiments.recurrence,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description='The simulated reader and its experiments.',
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument('experiment', choices=['all', 'log'] + list(EXPERIMENTS))
    parser.add_argument('dir', nargs='?', help='log: the state dir to write (new or empty)')
    parser.add_argument('--days', type=int, default=28, help='log: days to simulate')
    parser.add_argument('--seed', type=int, default=1, help='log: the world\'s seed')
    args = parser.parse_args(argv)
    if args.experiment == 'log':
        if not args.dir:
            parser.error('log needs a DIR')
        home = Path(args.dir).expanduser().resolve()
        if home == (Path.home() / '.japanese-weave').resolve() or (home.exists() and any(home.iterdir())):
            sys.exit(f'{home} is not new or empty: the simulated reader only writes a state dir of its own.')
        world, _ = logs.simulate(home, args.days, seed=args.seed)
        now = world.time() // 1000 + 86_400
        with fwlib.using_home(home):
            knowledge.refresh(now)  # knowledge.json, so calibrate has something to print
        print(f'{args.days} simulated days (seed {args.seed}) in {home}, replayed as of {time.ctime(now)}. Try '
              f'JAPANESE_WEAVE_HOME={home} scripts/fw.py calibrate, or scripts/evaluate.py --home {home} --now {now}')
        return
    names = list(EXPERIMENTS) if args.experiment == 'all' else [args.experiment]
    for name in names:
        started = time.time()
        print(f'== {name} ==')
        print(EXPERIMENTS[name]())
        print(f'({time.time() - started:.0f} s)\n', flush=True)


if __name__ == '__main__':
    main()
