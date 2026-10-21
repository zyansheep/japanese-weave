"""The matcher (scripts/matcher.py) against the shared test vectors that extension/test/matcher.test.js also runs."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import matcher  # noqa: E402

VECTORS = json.loads((ROOT / 'tests' / 'matcher_cases.json').read_text(encoding='utf-8'))


def utf16(text, i):
    """A code-point offset as JavaScript counts it (UTF-16 code units), as the vectors give offsets."""
    return len(text[:i].encode('utf-16-le')) // 2


class SharedVectors(unittest.TestCase):
    def test_cases(self):
        for case in VECTORS['cases']:
            with self.subTest(case['name'], without=case['without']):
                spans = matcher.match_text(matcher.build_index(VECTORS['entries'], without=case['without']), case['text'])
                got = [{'start': utf16(case['text'], s['start']), 'end': utf16(case['text'], s['end']), 'english': s['english'],
                        'options': [e['ja'] for e in s['options']], 'glosses': s['glosses'], 'function': s['function']}
                       for s in spans]
                self.assertEqual(got, case['spans'])

    def test_offsets_are_code_points_in_python(self):
        case = next(c for c in VECTORS['cases'] if '😀' in c['text'])
        span = matcher.match_text(matcher.build_index(VECTORS['entries']), case['text'])[0]
        self.assertEqual(case['text'][span['start']:span['end']], span['english'])

    def test_word_forms(self):
        for f in VECTORS['forms']:
            self.assertEqual((f['word'], f['kinds'], matcher.word_forms(f['word'], f['kinds'])), (f['word'], f['kinds'], f['forms']))

    def test_parts_of_speech(self):
        for k in VECTORS['kinds']:
            entry = {key: value for key, value in k.items() if key != 'kinds'}
            self.assertEqual((entry['ja'], matcher.entry_kinds(entry)), (entry['ja'], k['kinds']))

    def test_gloss_alternatives(self):
        for a in VECTORS['alternatives']:
            self.assertEqual(matcher.gloss_alternatives(a['en']), a['alternatives'])

    def test_rules_exist(self):
        for case in VECTORS['cases']:
            for rule in case['without']:
                self.assertIn(rule, matcher.RULES)


class Matcher(unittest.TestCase):
    def test_options_are_the_entries_given(self):
        entries = [{'ja': 'あなた', 'en': 'you', 'id': 1}, {'ja': '君', 'en': 'you', 'id': 2}]
        span, = matcher.match_text(matcher.build_index(entries), 'Thank you.')
        self.assertIs(span['options'][0], entries[0])
        self.assertIs(span['options'][1], entries[1])

    def test_nothing_to_match(self):
        index = matcher.build_index([{'ja': '犬', 'en': 'dog'}])
        self.assertEqual(matcher.match_text(index, ''), [])
        self.assertEqual(matcher.match_text(index, None), [])
        self.assertEqual(matcher.match_text(matcher.build_index([]), 'A dog.'), [])
        index = matcher.build_index([{'ja': '年', 'en': 'counter for years'}, {'ja': '空', 'en': ''}])
        self.assertEqual(matcher.match_text(index, 'Two years.'), [])

    def test_longest_wins_without_overlaps(self):
        index = matcher.build_index([{'ja': '行く', 'en': 'go'}, {'ja': '帰る', 'en': 'go back'}, {'ja': '後ろ', 'en': 'back'}])
        got = [(s['english'], [e['ja'] for e in s['options']]) for s in matcher.match_text(index, 'We go back. Go! Back.')]
        self.assertEqual(got, [('go back', ['帰る']), ('Go', ['行く']), ('Back', ['後ろ'])])


class EnglishData(unittest.TestCase):
    def test_extension_copy_is_current(self):
        """extension/src/english.js is what `python3 scripts/matcher.py --js` writes from data/english.json."""
        self.assertEqual(matcher.JS.read_text(encoding='utf-8'), matcher.js_module())
        self.assertEqual(matcher.read_js(matcher.JS.read_text(encoding='utf-8')), matcher.ENGLISH)

    def test_tables_are_lower_case_words(self):
        for name in ('verbs', 'plurals', 'comparatives'):
            for base, forms in matcher.ENGLISH[name].items():
                for w in [base] + forms:
                    self.assertRegex(w, "^[a-z']+$", f'{name}: {base}')
        for w in matcher.ENGLISH['function'] + matcher.ENGLISH['determiners']:
            self.assertRegex(w, "^[a-z]+$")
        for phrase, glosses in matcher.ENGLISH['phrases'].items():
            self.assertEqual(phrase, phrase.lower())
            self.assertTrue(glosses)


if __name__ == '__main__':
    unittest.main()
