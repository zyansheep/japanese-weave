"""Code finds the places a listed word could go (Python 3.9+, standard library only).

PLAN.md section 6 and docs/matcher.md say why. An index from English (each gloss, its inflections by rule and by the
irregular tables, and phrases that stand for it, such as "there is" for "exist") to entries, and a matcher that marks
every span of a passage an entry could take, with all its options: "you" gets both あなた and 君, and a model settles
the doubtful ones later. Whole words only, as tools/prompt-lab/oracle.mjs places the oracle's words: a possessive counts
("grandfather" in "grandfather's"), a contraction doesn't ("can" in "can't"). Case-insensitive. The longest match wins
and spans never overlap.

  index = build_index([{'ja': '思う', 'en': 'think'}])
  match_text(index, 'I thought so.')[0]['english'] == 'thought'

The same algorithm as extension/src/matcher.js; both run the shared test vectors in tests/matcher_cases.json. The
English morphology is data/english.json; `python3 scripts/matcher.py --js` writes the extension's copy of it,
extension/src/english.js. Offsets index the text as Python strings do (code points; JavaScript counts UTF-16 units).
"""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'english.json'
JS = ROOT / 'extension' / 'src' / 'english.js'
ENGLISH = json.loads(DATA.read_text(encoding='utf-8'))

FUNCTION = frozenset(ENGLISH['function'])
VERBS = ENGLISH['verbs']
PLURALS = ENGLISH['plurals']
COMPARATIVES = ENGLISH['comparatives']
DETERMINERS = frozenset(ENGLISH['determiners'])
AUX = ENGLISH['auxiliaries']
BETWEEN = frozenset(AUX['between'])
PARTICIPLES = frozenset(f for forms in VERBS.values() for f in forms)
STANDS_FOR = {}  # gloss -> the phrases that stand for it
for _phrase, _glosses in ENGLISH['phrases'].items():
    for _g in _glosses:
        STANDS_FOR.setdefault(_g, []).append(_phrase)

# The rules that drop a match, each on unless switched off (build_index's `without`, for measuring them); see
# extension/src/matcher.js for what each does.
RULES = ('kinds', 'lexical', 'helpers', 'nouns', 'hyphens')

TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
WORD_CHAR = re.compile(r'[^\W_]')
SPACE = re.compile('[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+')  # JavaScript's \s
APOSTROPHE = ("'", '’')


def _tokenize(text):
    """The words of a text and the separators between them, as in matcher.js tokenize()."""
    tokens = []
    for m in TOKEN.finditer(text):
        start, end = m.start(), m.end()
        word = m.group().lower().replace('’', "'")
        before = text[start - 1] if start > 0 else ''
        after = text[end] if end < len(text) else ''
        tokens.append({
            'start': start, 'end': end, 'word': word,
            'stem': {'word': word[:-2], 'end': end - 2} if word.endswith("'s") else None,
            'open': before not in APOSTROPHE,
            'closed': after not in APOSTROPHE,
            'hyphen_before': before == '-' and start >= 2 and bool(WORD_CHAR.match(text[start - 2])),
            'hyphen_after': after == '-' and end + 1 < len(text) and bool(WORD_CHAR.match(text[end + 1])),
        })
    seps = []
    for a, b in zip(tokens, tokens[1:]):
        gap = text[a['end']:b['start']]
        seps.append(' ' if SPACE.fullmatch(gap) else '-' if gap == '-' else None)
    return tokens, seps


VERB_END = re.compile('[うくぐすつぬぶむる]$')
NOT_VERB = re.compile('^[ぁ-ゟ]*[おこそとのほもよろごぞどぼぽょ]う$|^[一二三四五六七八九十百千万幾]+つ$|^いくつ$')


def entry_kinds(entry):
    """An entry's parts of speech as letters: n noun, v verb, a adjective ('' for the rest). From entry['pos'] when it
    has one (JMdict codes, a list or a string), else guessed from the Japanese: ending in い an adjective, in a u-row
    kana a verb (not もう, こう, どう, nor a counted number like 二つ), anything else a noun."""
    pos = entry.get('pos')
    if pos:
        codes = pos if isinstance(pos, (list, tuple)) else re.split(r'[,;\s]+', str(pos))
        kinds = ''
        for c in codes:
            if c == 'vs':
                kinds += 'nv'
            elif c.startswith('v'):
                kinds += 'v'
            elif c.startswith('adj'):
                kinds += 'a'
            elif c.startswith('n') or c == 'pn':
                kinds += 'n'
        return ''.join(sorted(set(kinds)))
    ja = re.sub('^[〜~]', '', str(entry.get('ja') or ''))
    if ja.endswith('い'):
        return 'a'
    if VERB_END.search(ja):
        return ('n' if ja.endswith('つ') else '') if NOT_VERB.match(ja) else 'v'
    return 'n'


def gloss_alternatives(en):
    """The English alternatives of a gloss: "a / b" split at the slashes (a list is taken as it is), lower case,
    without parentheses, dots, a leading "to" or a counter's description."""
    items = en if isinstance(en, (list, tuple)) else str(en if en is not None else '').split('/')
    out = []
    for raw in items:
        g = str(raw).lower().replace('’', "'")
        g = re.sub(r'\([^)]*\)', ' ', g)
        g = re.sub(r'\.\.\.|…', ' ', g)
        g = re.sub('["“”!?;:,]', ' ', g)
        g = re.sub(r'\s+', ' ', g).strip()
        if g.startswith('to '):
            g = g[3:]
        if g and not re.search(r'\bcounter\b', g) and g not in out:
            out.append(g)
    return out


def _inflectable(w):
    return w not in FUNCTION or w in VERBS


def _stems(w):
    if not re.search('[^aeiou][aeiou][bdfgklmnprstvz]$', w):
        return [w]
    doubled = w + w[-1]
    return [doubled] if len(re.findall('[aeiouy]+', w)) == 1 else [w, doubled]


def _s_forms(w):
    if re.search('(s|x|z|ch|sh)$', w):
        return [w + 'es']
    if re.search('[^aeiou]y$', w):
        return [w[:-1] + 'ies']
    if re.search('[^aeiou]o$', w):
        return [w + 's', w + 'es']
    return [w + 's']


def _ed_forms(w):
    if w.endswith('e'):
        return [w + 'd']
    if re.search('[^aeiou]y$', w):
        return [w[:-1] + 'ied']
    return [s + 'ed' for s in _stems(w)]


def _ing_forms(w):
    if w.endswith('ie'):
        return [w[:-2] + 'ying']
    if re.search('[^aeiouy]e$', w) and len(w) > 2:
        return [w[:-1] + 'ing']
    return [s + 'ing' for s in _stems(w)]


def _noun_forms(w):
    return _s_forms(w) + [w[:-3] + 'men'] if w.endswith('man') and len(w) > 3 else _s_forms(w)


def _adj_forms(w):
    if w.endswith('e'):
        return [w + 'r', w + 'st']
    if re.search('[^aeiou]y$', w):
        return [w[:-1] + 'ier', w[:-1] + 'iest']
    return [f for s in _stems(w) for f in (s + 'er', s + 'est')]


def _forms_of(w, kinds):
    """One word's forms for the parts of speech in kinds (all when None), without the word itself: {form: 1 from an
    irregular table, 2 by a rule}, as in matcher.js formsOf()."""
    out = {}
    if not _inflectable(w):
        return out
    has = lambda k: kinds is None or k in kinds  # noqa: E731

    def put(forms, how):
        for f in forms:
            if f != w and not (f in out and out[f] <= how):
                out[f] = how

    def table(t, rule):
        if w in t:
            put(t[w], 1)
        else:
            put(rule(w), 2)

    if has('v'):
        table(VERBS, _ed_forms)
        put(_s_forms(w) + _ing_forms(w), 2)
    if has('n'):
        table(PLURALS, _noun_forms)
    if has('a'):
        table(COMPARATIVES, _adj_forms)
    return out


def word_forms(w, kinds=None):
    """One word's forms for the parts of speech in kinds (all of them when kinds is None), without the word itself."""
    return list(_forms_of(w, kinds))


def _gloss_forms(words, seps, kinds):
    """A gloss's spellings to match: [(words, seps, inflected)], inflected 0 as written, 1 by an irregular table, 2 by a
    rule, as in matcher.js glossForms()."""
    out = [(words, seps, 0)]
    has = lambda k: kinds is None or k in kinds  # noqa: E731
    if len(words) == 1:
        out += [([f], seps, how) for f, how in _forms_of(words[0], kinds).items()]
        return out
    first, rest = words[0], words[1:]
    if has('v'):
        out += [([f] + rest, seps, how) for f, how in _forms_of(first, 'v').items()]
    if has('n'):
        out += [(words[:-1] + [f], seps, how) for f, how in _forms_of(words[-1], 'n').items()]
    if first == 'be':
        out.append((rest, seps[1:], 0))
    return out


class Index:
    """A trie of words, each step keyed by the separator before the word (' ' or '-', none for the first) and the word;
    a node's hits are the entries whose English ends there: {'entry': its place in entries, 'gloss', 'inflected' (as in
    _gloss_forms; the lowest if several reach it)}."""

    def __init__(self, entries, kinds, root, rules):
        self.entries, self.kinds, self.root, self.rules = entries, kinds, root, rules


def build_index(entries, without=()):
    """The index of entries ({'ja', 'en'[, 'pos']}). without: rules to switch off (RULES), to measure them."""
    entries = list(entries)
    rules = frozenset(r for r in RULES if r not in without)
    root = {'next': {}, 'hits': []}
    kinds_of = []
    for idx, entry in enumerate(entries):
        kinds = entry_kinds(entry)
        kinds_of.append(kinds)
        for gloss in gloss_alternatives(entry.get('en')):
            for spelling in [gloss] + STANDS_FOR.get(gloss, []):
                tokens, seps = _tokenize(spelling)
                if not tokens or None in seps:
                    continue
                words = [t['word'] for t in tokens]
                for form, form_seps, inflected in _gloss_forms(words, seps, kinds if 'kinds' in rules else None):
                    node = root
                    for k, w in enumerate(form):
                        key = (form_seps[k - 1] if k else '') + w
                        node = node['next'].setdefault(key, {'next': {}, 'hits': []})
                    same = next((h for h in node['hits'] if h['entry'] == idx), None)
                    if same is None:
                        node['hits'].append({'entry': idx, 'gloss': gloss, 'inflected': inflected})
                    elif inflected < same['inflected']:
                        same.update(gloss=gloss, inflected=inflected)
    return Index(entries, kinds_of, root, rules)


def _participle(w):
    return w in PARTICIPLES or (len(w) >= 4 and w.endswith('ed') and not w.endswith('eed'))


def _helper(aux, tokens, seps, j):
    """"have" or "do" at token j is a helper verb, as in matcher.js helper()."""
    k = j + 1
    while k < len(tokens) and seps[k - 1] == ' ':
        w = tokens[k]['word']
        if w in aux['next'] or (aux['participle'] and _participle(w)):
            return True
        if w not in BETWEEN:
            return False
        k += 1
    return False


def _bare(w):
    """A word without a contraction's tail ("there's" is "there", "isn't" is "is")."""
    return re.sub("'(s|re|ve|ll|d|m)$", '', re.sub("n't$", '', w))


def match_text(index, text):
    """Every span of text an entry of the index could take: [{'start', 'end', 'english', 'options', 'glosses',
    'function'}], in order; options are the entries as given to build_index, glosses the alternative each matched,
    function whether every word of the span is a closed-class word (as in matcher.js matchText())."""
    text = '' if text is None else str(text)
    tokens, seps = _tokenize(text)
    rules, kinds = index.rules, index.kinds
    candidates = []

    def add(i, j, end, last, hits):
        keep = hits
        if i == j and 'helpers' in rules:
            for name, aux in AUX.items():
                if name != 'between' and last in aux['forms'] and _helper(aux, tokens, seps, j):
                    keep = [h for h in keep if h['gloss'] != name]
        first = last if i == j else tokens[i]['word']
        if ('nouns' in rules and i > 0 and seps[i - 1] == ' ' and tokens[i - 1]['word'] in DETERMINERS
                and not first.endswith('ing')):
            keep = [h for h in keep if kinds[h['entry']] != 'v' or h['gloss'].startswith('be ')]
        if 'lexical' in rules and any(h['inflected'] == 0 for h in keep):
            keep = [h for h in keep if h['inflected'] != 2]
        if keep:
            candidates.append({'start': tokens[i]['start'], 'end': end, 'i': i, 'j': j, 'last': last, 'hits': keep})

    for i in range(len(tokens)):
        if not tokens[i]['open'] or ('hyphens' in rules and tokens[i]['hyphen_before']):
            continue
        node = index.root
        for j in range(i, len(tokens)):
            t = tokens[j]
            sep = '' if j == i else seps[j - 1]
            if sep is None:
                break
            if t['stem']:
                possessive = node['next'].get(sep + t['stem']['word'])
                if possessive and possessive['hits']:
                    add(i, j, t['stem']['end'], t['stem']['word'], possessive['hits'])
            child = node['next'].get(sep + t['word'])
            if child is None:
                break
            if child['hits'] and t['closed'] and not ('hyphens' in rules and t['hyphen_after']):
                add(i, j, t['end'], t['word'], child['hits'])
            node = child
    candidates.sort(key=lambda c: (-(c['end'] - c['start']), c['start']))
    # Spans cover whole tokens (a possessive's stem lies inside its token), so two overlap exactly when their tokens
    # do: marking the tokens taken finds that in linear time, where comparing with every span taken was quadratic.
    taken, used = [], bytearray(len(tokens))
    for c in candidates:
        if not any(used[c['i']:c['j'] + 1]):
            used[c['i']:c['j'] + 1] = b'\x01' * (c['j'] + 1 - c['i'])
            taken.append(c)
    taken.sort(key=lambda c: c['start'])
    spans = []
    for c in taken:
        hits = sorted(c['hits'], key=lambda h: h['entry'])
        words = [t['word'] for t in tokens[c['i']:c['j']]] + [c['last']]
        spans.append({'start': c['start'], 'end': c['end'], 'english': text[c['start']:c['end']],
                      'options': [index.entries[h['entry']] for h in hits], 'glosses': [h['gloss'] for h in hits],
                      'function': all(_bare(w) in FUNCTION for w in words)})
    return spans


def js_module(data=None):
    """extension/src/english.js: data/english.json as an ES module."""
    data = ENGLISH if data is None else data
    lines = ',\n'.join(f'  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}' for k, v in data.items())
    return ('// Generated by scripts/matcher.py --js from data/english.json (English morphology for matcher.js); edit\n'
            '// the JSON and run it again.\n'
            f'export const ENGLISH = {{\n{lines},\n}};\n')


def read_js(text):
    """The data in an english.js module (the inverse of js_module)."""
    body = text.split('export const ENGLISH = ', 1)[1].rstrip().rstrip(';')
    return json.loads(re.sub(r',\n}$', '\n}', body))


def synthetic(n):
    """n entries with English drawn from the lexicon's gloss words, as tools/prompt-lab/matcher-lab.mjs synthetic()
    makes them (the same entries): one to three alternatives of one to three words, a third each verbs, adjectives and
    nouns by their Japanese ending."""
    lines = (ROOT / 'data' / 'lexicon.csv').read_text(encoding='utf-8').split('\n')[1:]
    vocab = list(dict.fromkeys(w for line in lines for w in re.findall("[a-z']+", (line.split(',') + [''])[1].lower())))
    seed = 1

    def rand(k):
        nonlocal seed
        seed = seed * 48271 % 2147483647
        return seed % k

    out = []
    for i in range(n):
        alts = []
        for _ in range(1 + rand(3)):
            size = 1 + (1 + rand(2) if rand(4) == 0 else 0)
            alts.append(' '.join(vocab[rand(len(vocab))] for _ in range(size)))
        out.append({'ja': f"語{i}{['る', 'い', ''][i % 3]}", 'en': ' / '.join(alts)})
    return out


def timing(state_path):
    """Index build and match times for the introduced words of a word state, all its words, and 22,000 synthetic
    entries, matching the prompt lab's judged passages."""
    import statistics
    import time
    lab = ROOT / 'tools' / 'prompt-lab'
    oracle = json.loads((lab / 'oracle.json').read_text(encoding='utf-8'))
    texts = {}
    for name in ('eval16', 'heldout', 'live', 'web', 'corpus'):
        for p in json.loads((lab / f'{name}.json').read_text(encoding='utf-8')):
            if p['id'] in oracle:
                texts.setdefault(p['id'], p['text'])
    words = json.loads(Path(state_path).read_text(encoding='utf-8'))['words']
    sets = [('introduced words', [w for w in words if w.get('status') in ('known', 'active', 'shaky')]),
            ('every word in the state', words), ('22,000 synthetic entries', synthetic(22000))]
    print(f'Timing (Python {sys.version.split()[0]}, one run, {len(texts)} passages):')
    for name, entries in sets:
        t0 = time.perf_counter()
        index = build_index(entries)
        build = (time.perf_counter() - t0) * 1000
        times = []
        for text in texts.values():
            t0 = time.perf_counter()
            match_text(index, text)
            times.append((time.perf_counter() - t0) * 1000)
        print(f'  {name} ({len(entries)}): build {build:.1f} ms; match {statistics.mean(times):.3f} ms a passage '
              f'(median {statistics.median(times):.3f}, max {max(times):.3f})')


if __name__ == '__main__':
    if sys.argv[1:] == ['--js']:
        JS.write_text(js_module(), encoding='utf-8')
        print(f'wrote {JS.relative_to(ROOT)}')
    elif sys.argv[1:2] == ['--time'] and len(sys.argv) == 3:
        timing(sys.argv[2])
    else:
        sys.exit('usage: python3 scripts/matcher.py --js          (writes extension/src/english.js)\n'
                 '       python3 scripts/matcher.py --time STATE  (index and match times; STATE: a copy of state.json)')
