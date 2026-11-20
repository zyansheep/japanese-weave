"""Offline tests for scripts/backup.py, the snapshot the Nix module takes before a new version first runs, and for the
copies fw.py makes from a read-only install (the Nix store). Run: python -m unittest tests/test_backup.py"""
import datetime as dt
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import backup  # noqa: E402
import fw  # noqa: E402


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'state'
        self.home.mkdir()
        (self.home / 'state.json').write_text(json.dumps({'words': []}))
        (self.home / 'anki.csv').write_text('ja,en\n')
        (self.home / 'anthropic_key').write_text('not-a-real-key\n')
        (self.home / 'gemini_key').write_text('not-a-real-key\n')
        (self.home / 'state.lock').write_text('')
        (self.home / 'reading-index.sqlite').write_text('')
        db = sqlite3.connect(self.home / 'interactions.sqlite')
        db.execute('create table t (x)')
        db.execute('insert into t values (1)')
        db.commit()
        self.writer = db  # left open, as the collector would hold it

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def at(self, minute):
        return dt.datetime(2026, 11, 20, 12, minute, 0)

    def test_a_new_version_is_backed_up_once(self):
        target = backup.backup(self.home, '20261120-abc1234', now=self.at(0))
        self.assertEqual(target.name, '20261120-abc1234-20261120-120000')
        self.assertEqual(sorted(p.name for p in target.iterdir()), ['anki.csv', 'interactions.sqlite', 'state.json'])
        copy = sqlite3.connect(target / 'interactions.sqlite')
        self.assertEqual(copy.execute('select x from t').fetchall(), [(1,)])
        copy.close()
        self.assertEqual(stat.S_IMODE(os.stat(self.home / 'backups').st_mode), 0o700)
        self.assertIsNone(backup.backup(self.home, '20261120-abc1234', now=self.at(1)))  # same version: nothing
        self.assertEqual(len(backup.snapshots(self.home / 'backups')), 1)

    def test_keys_and_rebuilt_caches_are_never_copied(self):
        target = backup.backup(self.home, 'v1', now=self.at(0))
        names = {p.name for p in target.iterdir()}
        self.assertFalse(names & {'anthropic_key', 'gemini_key', 'reading-index.sqlite', 'state.lock'})

    def test_only_the_newest_ones_are_kept(self):
        for minute in range(7):
            backup.backup(self.home, f'v{minute}', keep=5, now=self.at(minute))
        kept = [p.name for p in backup.snapshots(self.home / 'backups')]
        self.assertEqual(kept, [f'v{m}-20261120-12{m:02d}00' for m in range(2, 7)])
        self.assertEqual((self.home / 'backups' / backup.LAST).read_text().strip(), 'v6')

    def test_no_state_dir_no_backup(self):
        self.assertIsNone(backup.backup(Path(self.tmp.name) / 'missing', 'v1'))
        self.assertFalse((Path(self.tmp.name) / 'missing').exists())

    def test_quiet_prints_nothing(self):
        with mock.patch('sys.stdout', new=io.StringIO()) as out:
            backup.main(['--version', 'v1', '--home', str(self.home), '--quiet'])
            self.assertEqual(out.getvalue(), '')
            backup.main(['--version', 'v2', '--home', str(self.home)])
            self.assertIn('backed up', out.getvalue())


class ReadOnlySource(unittest.TestCase):
    def test_copies_of_a_read_only_tree_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'store', Path(tmp) / 'copy'
            (source / 'sub').mkdir(parents=True)
            (source / 'sub' / 'a.txt').write_text('a')
            for path in (source / 'sub' / 'a.txt', source / 'sub', source):
                path.chmod(0o555 if path.is_dir() else 0o444)
            try:
                fw.replace_tree(source, target)
                (target / 'sub' / 'b.txt').write_text('b')  # writable, unlike the source
                (target / 'sub' / 'a.txt').write_text('changed')
                fw.replace_tree(source, target)  # and replaceable
                self.assertEqual(sorted(p.name for p in (target / 'sub').iterdir()), ['a.txt'])
                self.assertEqual((target / 'sub' / 'a.txt').read_text(), 'a')
            finally:
                fw.writable(source)


if __name__ == '__main__':
    unittest.main()
