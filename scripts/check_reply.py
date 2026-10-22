"""Checks a woven chat reply before it is sent (fw.py check-reply; README "Chat replies"), listing what to fix line by
line. It finds the errors blind judges kept finding in woven replies that code can see:
  - a sentence half Japanese or more with no translation on its closing mark;
  - もし with no conditional ending after it in its sentence;
  - a pronoun (あなた 私 僕 君 彼 彼女), or これ, それ or 自分 in a sentence that stays English;
  - a word that follows its noun in Japanese (前 後 中 上 下 間 代わりに くらい まで) put before an English word;
  - a Japanese word right next to a number;
  - in a sentence that stays English, a Japanese word that isn't a noun (or an adjective right before an English
    noun), by the word's types in data/dictionary.tsv; a form the dictionary doesn't have (a conjugated verb) too;
  - Japanese outside <abbr> tags other than the structural particles (は が を に で の へ と ね よ) and です/ます: か, や, も,
    から, まで, より, なら, けど, ので, のに and a nominalising の after a verb or adjective carry meaning, so they need a gloss;
  - する on an English word (arrive します, use して, build しない);
  - "an" before a Japanese word, or any English article inside a sentence half Japanese or more;
  - そして opening a sentence, the learner's "And...";
  - one content word (with kanji) glossed REPEAT or more times in a reply.
Code (fenced and inline) is skipped. Python 3.9+ standard library only."""
import functools
import re
from pathlib import Path

DICTIONARY = Path(__file__).resolve().parent.parent / 'data' / 'dictionary.tsv'
ABBR = re.compile(r'<abbr title="([^"]*)">([^<]*)</abbr>')
JAPANESE = re.compile('[぀-ヿ㐀-鿿々ー]')
RUN = re.compile('[぀-ヿ㐀-鿿々ー、。？！「」・〜（）]+')
KANJI = re.compile('[㐀-鿿]')
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
MARKS = ('。', '？', '！', '?', '!')
PRONOUNS = {'あなた', '私', '僕', '君', '彼', '彼女', 'あなたたち', '私たち'}
ENGLISH_ONLY_NOUNS = {'これ', 'それ', 'あれ', '自分'}  # nouns by type, but they stand in for English pronouns
AFTER_NOUN = {'前', '後', '中', '上', '下', '間', '代わりに', 'くらい', 'まで'}
CONDITIONAL = re.compile('なら|たら|だら|れば|えば|けば|せば|てば|ねば|べば|めば|ければ|ても|でも')
# Japanese a reply may leave outside the tags: the structural particles and the polite copula and endings
GRAMMAR = sorted({'は', 'が', 'を', 'に', 'で', 'の', 'へ', 'と', 'ね', 'よ', 'な', 'には', 'では', 'とは',
                  'です', 'でした', 'ですか', 'ですね', 'ですよ', 'でしょう', 'ます', 'ました', 'ません', 'ましょう',
                  'ませんか', 'ではありません', 'じゃありません', '、', '。', '？', '！', '「', '」', '・', 'ー', '〜', '（', '）'},
                 key=len, reverse=True)
NOUN_TYPES = ('n', 'n-adv', 'n-t', 'n-suf', 'n-pref', 'num', 'ctr', 'pn')
ADJECTIVE_TYPES = ('adj-i', 'adj-na', 'adj-no', 'adj-pn', 'adj-f', 'adj-ix')
VERB_OR_ADJECTIVE = re.compile(r'^(v|adj-i)')
SURU = re.compile(r'([A-Za-z][A-Za-z-]*)\s*((?:し[なまよてた]|さ[れせ]|す[るれ])[ぁ-ん]{0,6})')
ARTICLE = re.compile(r'\b(a|an|the)\b', re.I)
REPEAT = 5


@functools.lru_cache(maxsize=1)
def word_types(path=DICTIONARY):
    """{spelling: the JMdict types of every entry written that way}, from data/dictionary.tsv (ja, kana and forms)."""
    types, header = {}, None
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#'):
                continue
            cells = line.rstrip('\n').split('\t')
            if header is None:
                header = cells
                continue
            row = dict(zip(header, cells))
            pos = set(filter(None, re.split(r'[,|]', row.get('pos', ''))))
            for spelling in [row.get('ja'), row.get('kana')] + (row.get('forms') or '').split(','):
                if spelling:
                    types.setdefault(spelling, set()).update(pos)
    return types


def ungrammatical(run):
    """The part of a bare run of Japanese that isn't GRAMMAR, or '' when it all is."""
    i, rest = 0, []
    while i < len(run):
        for g in GRAMMAR:
            if run.startswith(g, i):
                i += len(g)
                break
        else:
            rest.append(run[i])
            i += 1
    return ''.join(rest)


def sentences(line):
    """A line's sentences as lists of tokens: ('w', ja, title) for a glossed word, ('m', mark, title) for a glossed
    closing mark (which ends its sentence), ('t', text) for the rest, which is split at English sentence ends."""
    out, cur, at = [], [], 0
    for m in ABBR.finditer(line):
        if m.start() > at:
            cur.append(('t', line[at:m.start()]))
        title, ja = m.group(1), m.group(2)
        if ja in MARKS:
            out.append(cur + [('m', ja, title)])
            cur = []
        else:
            cur.append(('w', ja, title))
        at = m.end()
    if at < len(line):
        cur.append(('t', line[at:]))
    final = []
    for sent in out + ([cur] if cur else []):
        piece = []
        for tok in sent:
            if tok[0] != 't':
                piece.append(tok)
                continue
            for k, part in enumerate(re.split(r'(?<=[.!?。？！])\s+', tok[1])):
                if k and piece:
                    final.append(piece)
                    piece = []
                if part:
                    piece.append(('t', part))
        if piece:
            final.append(piece)
    return final


def problems(text, types=None):
    """[(line number, what is wrong, the sentence)]; line 0 for the whole reply."""
    types = word_types() if types is None else types
    found = []
    fence = False
    for number, raw in enumerate(text.split('\n'), 1):
        if re.match(r'\s*(```|~~~)', raw):
            fence = not fence
            continue
        if fence:
            continue
        line = re.sub(r'`[^`]*`', ' CODE ', raw)
        for sent in sentences(line):
            words = [t for t in sent if t[0] == 'w']
            if not words and not any(t[0] == 't' and JAPANESE.search(t[1]) for t in sent):
                continue
            add = lambda what: found.append((number, what, show))
            bare = ''.join(t[1] for t in sent if t[0] == 't')
            english = len(LATIN_WORD.findall(bare))
            japanese = len(words) + sum(1 for t in sent if t[0] == 't' and JAPANESE.search(t[1]))
            show = re.sub(r'\s+', ' ', ''.join(t[1] for t in sent)).strip()[:90]
            translated = any(t[0] == 'm' and LATIN_WORD.search(t[2]) for t in sent)
            stays_english = english > japanese and not translated
            if japanese >= 3 and japanese >= english and not translated:
                add('a sentence that is half Japanese or more has no translation on its closing mark')
            at = next((i for i, t in enumerate(sent) if (t[0] == 'w' and t[1] == 'もし') or
                       (t[0] == 't' and re.search('(?<![぀-ヿ])もし', t[1]))), None)
            if at is not None and not CONDITIONAL.search(''.join(t[1] for t in sent[at + 1:] if t[0] in 'wt')):
                add('もし with no conditional ending (なら, たら, ば) after it: write the condition in Japanese or keep '
                    '"if" in English')
            marked = ''.join(t[1] if t[0] == 't' else f'<w>{t[1]}' for t in sent if t[0] != 'm')
            for m in SURU.finditer(marked.replace('<w>', '')):
                add(f'"{m.group(1)} {m.group(2)}": する on an English word. Use a Japanese verb from the weave\'s '
                    'output, or keep that clause English')
            if re.search(r'\ban\s+<w>', marked, re.I):
                add('"an" before a Japanese word reads wrong: use "a", or drop it')
            elif not stays_english and japanese >= 3 and ARTICLE.search(bare):
                add('an English article inside a sentence that is half Japanese or more: drop it')
            first = next((t for t in sent if not (t[0] == 't' and not t[1].strip(' *-#>'))), None)
            if first and first[0] == 'w' and first[1] == 'そして':
                add('そして opening a sentence reads as the learner\'s "And...": use また or それに, join the clauses with '
                    'the て-form, or drop it')
            for i, t in enumerate(sent):
                if t[0] == 't' and i and sent[i - 1][0] == 'w' and re.match('の[はがをも]', t[1]):
                    pos = types.get(sent[i - 1][1]) or set()
                    verbal = not pos or any(VERB_OR_ADJECTIVE.match(p) for p in pos)
                    if verbal and not any(p in NOUN_TYPES for p in pos):
                        add(f'the nominalising の after {sent[i - 1][1]} is unglossed: wrap it with the verb or '
                            'adjective')
            for i, t in enumerate(sent):
                if t[0] != 'w':
                    continue
                ja = t[1]
                next_text = sent[i + 1][1] if i + 1 < len(sent) and sent[i + 1][0] == 't' else ''
                prev_text = sent[i - 1][1] if i and sent[i - 1][0] == 't' else ''
                if ja in PRONOUNS:
                    add(f'the pronoun {ja}: Japanese leaves it out, so drop it')
                elif stays_english and ja in ENGLISH_ONLY_NOUNS:
                    add(f'{ja} as an English pronoun in a sentence that stays English')
                if ja in AFTER_NOUN and re.match(r'\s*[A-Za-z]', next_text):
                    add(f'{ja} follows its noun in Japanese, but here it stands before English like a preposition')
                if re.search(r'\d\s*$', prev_text) or re.match(r'\s*\d', next_text):
                    add(f'{ja} sits right next to a number')
                if stays_english:
                    pos = types.get(ja)
                    attributive = bool(re.match(r'\s*[A-Za-z]', next_text))
                    if pos is None:
                        add(f'{ja} is a form the dictionary does not list (a conjugated verb or phrase) inside a '
                            'sentence that stays English')
                    elif not any(p in NOUN_TYPES for p in pos) and not (
                            attributive and any(p in ADJECTIVE_TYPES for p in pos)):
                        kind = '/'.join(sorted(pos)) or 'not a noun'
                        add(f'{ja} ({kind}) in a sentence that stays English, where only nouns, and adjectives right '
                            'before a noun, go')
            for t in sent:
                if t[0] == 't':
                    for run in RUN.findall(t[1]):
                        if ungrammatical(run):
                            add(f'Japanese outside the <abbr> tags: {run}')
    counts = {}
    for m in ABBR.finditer(re.sub(r'```.*?```', '', text, flags=re.S)):
        if m.group(2) not in MARKS and KANJI.search(m.group(2)):  # content words: grammar repeats by nature
            counts[m.group(2)] = counts.get(m.group(2), 0) + 1
    for ja, n in sorted(counts.items(), key=lambda x: -x[1]):
        if n >= REPEAT:
            found.append((0, f'{ja} is glossed {n} times in this reply: vary it, or keep some of them English', ''))
    return found


def report(text, types=None):
    """What fw.py check-reply prints."""
    found = problems(text, types)
    if not found:
        return 'jw-check: no problems found.'
    lines = [f'jw-check: {len(found)} things to fix (fix each, or keep it only where the check is plainly wrong):']
    lines += [f'- line {number}: {what}. In: "{where}"' if number else f'- {what}.' for number, what, where in found]
    return '\n'.join(lines)
