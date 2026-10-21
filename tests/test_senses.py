"""The sense-flag lookups (scripts/senses.py) against the shared test vectors that extension/test/senses.test.js also
runs, and the committed flags file's shape."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import senses  # noqa: E402

VECTORS = json.loads((ROOT / 'tests' / 'senses_cases.json').read_text(encoding='utf-8'))


class SharedVectors(unittest.TestCase):
    flags = senses.parse(VECTORS['file'])

    def test_flags(self):
        for case in VECTORS['flags']:
            with self.subTest(case['entry'], gloss=case['gloss']):
                self.assertEqual(senses.sense_flag(self.flags, case['entry'], case['gloss']), case['flag'])

    def test_function_glosses(self):
        for case in VECTORS['function']:
            self.assertEqual((case['gloss'], senses.function_gloss(case['gloss'])), (case['gloss'], case['function']))

    def test_spans(self):
        for case in VECTORS['spans']:
            self.assertEqual(senses.safe_options(self.flags, case['span']), case['safe'])

    def test_a_file_with_other_fields_is_refused(self):
        with self.assertRaises(ValueError):
            senses.parse('# x\nid\tja\tkana\tgloss\tflag\n')


class TheFile(unittest.TestCase):
    def test_committed_flags_parse_and_name_known_entries(self):
        if not senses.PATH.exists():
            self.skipTest('no data/senses.tsv')
        flags = senses.load()
        self.assertGreater(len(flags), 1000)
        rows = [r for rs in flags.values() for r in rs]
        self.assertTrue(all(r[1] in ('s', 'd') for r in rows))
        self.assertTrue(any(r[1] == 's' for r in rows) and any(r[1] == 'd' for r in rows))
        # no closed-class gloss is ever written: those are doubtful by rule
        self.assertFalse(any(senses.function_gloss(gloss) for _, gloss in flags))


if __name__ == '__main__':
    unittest.main()
