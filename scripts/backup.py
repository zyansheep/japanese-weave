#!/usr/bin/env python3
"""Japanese Weave backups: a snapshot of the state dir before a new version first runs on it. Python 3.9+, standard
library only.

  backup.py --version VERSION [--keep N] [--home DIR] [--quiet]

Silent when VERSION is the one that last ran (backups/last-version), so a service can run it before every start (the
Nix module's collector unit does, as ExecStartPre, and its client side on each switch). When the version changed, it
copies the word state and the logs (the top-level .json and .csv files) and the gloss log (interactions.sqlite,
through SQLite's backup API, so a copy taken while something writes is still whole) into
backups/<VERSION>-<YYYYmmdd-HHMMSS>, keeps the newest N of them (default 5), records VERSION, and prints one line
(not with --quiet, for hooks that must print nothing).
Keys (anthropic_key, gemini_key) and caches that rebuild themselves (reading-index.sqlite) are never copied.
"""
import argparse
import datetime as dt
from pathlib import Path
import os
import re
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwlib  # noqa: E402

BACKUPS = 'backups'
LAST = 'last-version'
KEEP = 5
DATABASES = ('interactions.sqlite',)
SUFFIXES = ('.json', '.csv')
STAMP = re.compile(r'-(\d{8}-\d{6})$')


def copy_database(source, target):
    """A consistent copy of a SQLite file, even while another process writes to it."""
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=30)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def snapshots(folder):
    """The backup folders, oldest first (by the time in their names)."""
    found = [p for p in folder.iterdir() if p.is_dir() and STAMP.search(p.name)] if folder.is_dir() else []
    return sorted(found, key=lambda p: (STAMP.search(p.name).group(1), p.name))


def backup(home, version, keep=KEEP, now=None):
    """The folder written, or None when VERSION already ran here (or there is no state dir yet)."""
    if not home.is_dir():
        return None
    folder = home / BACKUPS
    last = folder / LAST
    if last.exists() and last.read_text().strip() == version:
        return None
    files = sorted(p for p in home.iterdir() if p.is_file() and p.suffix in SUFFIXES and not p.name.startswith('tmp'))
    databases = [home / name for name in DATABASES if (home / name).is_file()]
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    stamp = (now or dt.datetime.now()).strftime('%Y%m%d-%H%M%S')
    target = folder / f'{re.sub(r"[^A-Za-z0-9._+-]", "_", version)}-{stamp}'
    partial = target.with_name(target.name + '.partial')
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(mode=0o700)
    for path in files:
        shutil.copy2(path, partial / path.name)
    for path in databases:
        copy_database(path, partial / path.name)
    partial.rename(target)
    for old in snapshots(folder)[:-keep] if keep > 0 else []:
        shutil.rmtree(old)
    last.write_text(version + '\n')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', required=True)
    parser.add_argument('--keep', type=int, default=KEEP)
    parser.add_argument('--home', help='the state dir (default: as every script finds it)')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser() if args.home else fwlib.home()
    target = backup(home, args.version, args.keep)
    if target and not args.quiet:
        print(f'Japanese Weave {args.version}: state backed up to {target}')


if __name__ == '__main__':
    main()
