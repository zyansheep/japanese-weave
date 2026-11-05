"""Grammar patterns in chat replies (docs/grammar.md): the inventory, a scanner that finds them in a sentence, the
Japanese and mixed sentences of a reply with the fingerprint that joins them to the gloss log, and each pattern's
status for the reader (known, learning or new). Python 3.9+, standard library only.

A reply's Japanese or mixed sentences end with a 。 or ？ wrapped in an <abbr> whose title is the translation (a
"sentence open" is a hover on it). The hook reads the local transcripts (fwlib.read_replies) and sends, per reply, a
fingerprint of its annotation texts (the inner texts of its <abbr>s in order, hashed) and per sentence the index of its
closing mark among those annotations, its Japanese character count, its English word count and its pattern ids
(record): never the text. The collector computes the same fingerprint from the message's annotations
(contexts.annotations) and joins the two (sentence_model.py).

The scanner works on a sentence's segments: the glossed words (an <abbr>'s inner text: a word, a conjugated verb whole,
or a particle the rules gloss) and the bare text between them (particles, です and the like), English runs and code
standing for one noun ("Ｘ"). Bare text is split into grammar units (GRAMMAR); a glossed word that is only grammar
units (には, のは, ですが) is split the same way. Each pattern's scanner looks at the tokens with regexes, and where a
pattern turns on a verb or adjective form (a relative clause, plain past, potential) it checks the form against the
dictionary (data/dictionary.tsv) through inflection.deinflect.

The reader's status per pattern is the word state's "grammar" section, {pattern id: {status, since}}, changed by
grammar ops (hub.py). A pattern it doesn't list is known when it is one of the basics (BASIC) and new otherwise.
"""
import functools
import hashlib
import html
import re
from pathlib import Path

import inflection

VERSION = 1  # of the scanner and the sentence records; a new one makes the refresh read every transcript again
DICTIONARY = Path(__file__).resolve().parent.parent / 'data' / 'dictionary.tsv'
STATUSES = ('known', 'learning', 'new')
MARKS = ('。', '？')
JAPANESE = re.compile('[぀-ヿ㐀-鿿ｦ-ﾟ々ー]')  # the characters counted (kana, kanji, ー), as the analysis counted them
ENGLISH_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’_./-]*")
ABBR = re.compile(r'<abbr\b([^>]*)>(.*?)</abbr>', re.S | re.I)
TITLE = re.compile(r'title\s*=\s*"([^"]*)"', re.I)
TAG = re.compile(r'<[^>]+>')
FENCE = re.compile(r'^[ \t]*(```|~~~).*?(?:^[ \t]*\1[^\n]*$|\Z)', re.S | re.M)
INLINE_CODE = re.compile(r'`[^`\n]*`')
# Where a sentence can start before its closing mark: a line break, or an English sentence's end.
BOUNDARY = re.compile(r'(\n|(?<=[A-Za-z0-9`)\]"\'*])[.!?:;](?:\*\*)?(?=\s)|[:.](?=\*\*\s))')
LATIN_RUN = re.compile(r"[A-Za-z0-9`~/._'\"\-+#@$%&=()\[\]{}<>|\\,:;!?*’]+(?:[ \t]+[A-Za-z0-9`~/._'\"\-+#@$%&=()\[\]{}<>|"
                       r"\\,:;!?*’]+)*")
NOUN = 'Ｘ'  # an English run or code inside a Japanese sentence: a noun, as far as the grammar goes
PUNCTUATION = set('、。？！「」『』（）・：…')
KATAKANA_WORD = re.compile('[ァ-ヺー]{2,}')
KATAKANA = re.compile('[ァ-ヺー]')


class Pattern:
    def __init__(self, id_, name, explain, example, basic=False):
        self.id, self.name, self.explain, self.example, self.basic = id_, name, explain, example, basic

    def line(self):
        """'ている (doing, a state): an action going on or its result. 待っています (I am waiting).'"""
        return f'{self.name}: {self.explain}. {self.example}'


# The inventory, the basics first, then the rest roughly by how often they came up in chat replies (2026-10-22 to
# 11-03, docs/grammar.md), which is the order intake takes them in when the log has no counts yet. Names name the
# Japanese marker and its English sense; an example is a sentence with its translation.
PATTERNS = [Pattern(*p) for p in (
    ('wa', 'は (topic)', 'marks what the sentence is about', 'これは問題です (this is a problem)', True),
    ('ga', 'が (subject)', 'marks who or what does or is something', '時間がかかります (it takes time)', True),
    ('wo', 'を (object)', 'marks what a verb acts on', 'ログを読みます (I read the log)', True),
    ('ni', 'に (target, time)', 'where something goes, when, or to whom', '三時に始めます (it starts at three)', True),
    ('de', 'で (place, means)', 'where something happens, or with what', 'テストで分かります (the test shows it)', True),
    ('e', 'へ (toward)', 'the direction of movement', '東京へ行きます (I go to Tokyo)', True),
    ('no', 'の (of)', 'joins two nouns: X の Y is "the Y of X"', '今日の仕事 (today\'s work)', True),
    ('desu', 'です (is)', 'the polite "is", and でした "was"', '大丈夫です (it is fine)', True),
    ('masu', 'ます (polite verb)', 'the polite ending of a verb', '待ちます (I will wait)', True),
    ('masen', 'ません (polite not)', 'the polite negative, ではありません "is not"', '分かりません (I don\'t know)', True),
    ('mashita', 'ました (polite past)', 'the polite past of a verb', '終わりました (it finished)', True),
    ('mashou', 'ましょう (let\'s)', '"let\'s", and ましょうか "shall I"', '始めましょう (let\'s start)', True),
    ('kudasai', 'てください (please)', 'a polite request after a verb\'s te-form', '待ってください (please wait)', True),
    ('kosoado', 'これ, それ, この, ここ (this, that)', 'pointing words, and 何 "what", どこ "where"',
     'それは本当です (that is true)', True),
    ('mo', 'も (also)', '"also" or "too", in place of は or が', 'これも大切です (this matters too)', True),
    ('ka', 'か (question)', 'at the end makes a question; between nouns means "or"', '分かりますか (do you see?)', True),
    ('to', 'と (and, with)', '"and" between nouns, or "with" someone', '犬と猫 (dogs and cats)', True),
    ('ne-yo', 'ね, よ (right?, you know)', 'soft endings: ね seeks agreement, よ tells something new',
     'いいですね (nice, isn\'t it)', True),
    ('adj-noun', 'adjective + noun', 'an い-adjective right before its noun', '新しい問題 (a new problem)', True),
    ('teiru', 'ている (is doing)', 'an action going on, or a state that holds', '待っています (I am waiting)'),
    ('dake', 'だけ, しか (only)', 'だけ "only"; しか with a negative "nothing but"', '一つだけです (just one)'),
    ('suru-verb', 'noun + する (do)', 'a noun made a verb with する', '確認します (I will check)'),
    ('naru', 'なる (become)', 'X になる, adjective-く なる: to become', '速くなります (it gets faster)'),
    ('relative', 'relative clause', 'a verb in plain form right before the noun it describes',
     '昨日読んだ本 (the book I read yesterday)'),
    ('kara-from', 'から (from)', 'after a noun: "from"', '東京から来ました (I came from Tokyo)'),
    ('potential', 'can (できる, 〜える)', 'できる "can do", or a verb\'s potential form', '読めます (I can read it)'),
    ('plain-neg', 'ない (plain not)', 'the plain negative of a verb or adjective', '分からない (don\'t know)'),
    ('cleft', 'X のは Y です (what X is, is Y)', 'の turns a clause into "the one that"; は makes it the topic',
     '大切なのは時間です (what matters is time)'),
    ('conjecture', 'でしょう, かもしれません (probably, maybe)', 'a guess: でしょう "probably", かもしれません "might"',
     '雨かもしれません (it might rain)'),
    ('kedo', 'けど, が (but)', 'joins two clauses: "X, but Y"; のに "even though"',
     '高いですが、いいです (it is pricey, but good)'),
    ('nara', 'なら (if it is)', '"if it is X", "if you mean X"', '明日なら大丈夫です (tomorrow is fine)'),
    ('quote', 'と思う, と言う (that)', 'と before 思う, 言う, 書く quotes a thought or words',
     'いいと思います (I think it is good)'),
    ('reason', 'から, ので (because)', 'after a clause: "because"', '遅いので、待ちます (it is slow, so I wait)'),
    ('plain-past', 'た (plain past)', 'the plain past of a verb or adjective', '終わった (it ended)'),
    ('nominal', 'の, こと (doing, the fact that)', 'の or こと turn a verb into a noun', '読むのが好きです (I like reading)'),
    ('te-link', 'て (and then)', 'the te-form joining two actions: "X and Y"', '読んで、書きます (I read and write)'),
    ('to-when', 'と (when, if)', 'after a plain verb: "whenever X, Y"', '押すと動きます (press it and it moves)'),
    ('passive', 'れる, させる (is done, makes do)', 'passive and causative forms', '使われます (it is used)'),
    ('made', 'まで (until, up to)', 'how far, or until when', '五時まで待ちます (I wait until five)'),
    ('tara', 'たら (if, when)', '"if" or "once X happens"', '終わったら、教えてください (when it is done, tell me)'),
    ('da', 'だ (plain is)', 'the plain "is"', '問題だ (it is a problem)'),
    ('ba', 'ば (if)', 'the conditional 〜ば, and なければ "unless, must"', '読めば分かります (read it and you\'ll see)'),
    ('yori', 'より (than)', 'X より Y: "Y more than X"', '昨日より速いです (faster than yesterday)'),
    ('tai', 'たい (want to)', 'a verb stem + たい: "want to"', '見たいです (I want to see)'),
    ('to-iu', 'という (called, that)', 'X という Y: "a Y called X", "the Y that X"',
     '「速い」という意味 (the meaning "fast")'),
    ('temo', 'ても (even if)', 'te-form + も: "even if"', '遅くても大丈夫です (it\'s fine even if slow)'),
    ('ya', 'や (and so on)', 'X や Y: "X and Y, among others"', '本やノート (books, notes and such)'),
    ('neg-potential', 'can\'t (できません, 〜えない)', 'the negative of できる or of a potential form',
     '読めません (I can\'t read it)'),
    ('mada', 'まだ (not yet, still)', 'with a negative or alone, "not yet"; with a positive, "still"',
     'まだ終わっていません (it isn\'t done yet)'),
    ('stack', 'stacked particles (には, での, からの)', 'two particles together: に + は, で + の, から + の',
     'ここには何もありません (there is nothing here)'),
)]
BY_ID = {p.id: p for p in PATTERNS}
IDS = tuple(BY_ID)
BASIC = tuple(p.id for p in PATTERNS if p.basic)

# Bare grammar units, longest first when split (tokens). The negative copula stays whole.
GRAMMAR = sorted({'ではありません', 'じゃありません', 'ではない', 'じゃない', 'でしょう', 'ましょう', 'ませんでした',
                  'ません', 'ました', 'ます', 'でした', 'です', 'だった', 'だろう', 'けれども', 'けれど', 'けど', 'から',
                  'まで', 'より', 'だけ', 'しか', 'ばかり', 'ので', 'のに', 'など', 'くらい', 'ぐらい', 'って', 'という',
                  'なら', 'んです', 'んだ', 'として', 'にとって', 'について', 'によって', 'による', 'にとっては', 'は', 'が', 'を', 'に', 'で', 'の', 'へ', 'と', 'も', 'か', 'ね', 'よ', 'よね',
                  'や', 'な', 'だ'} | PUNCTUATION, key=len, reverse=True)
UNITS = set(GRAMMAR)
# Words bare text may hold (from before every word was glossed): taken whole, so their kana aren't read as particles.
BARE_WORDS = sorted({'もう', 'まだ', 'また', 'とても', 'ちょっと', 'もっと', 'すぐ', 'いつも', 'ここ', 'そこ', 'どこ',
                     'これ', 'それ', 'あれ', 'どれ', 'この', 'その', 'あの', 'どの', 'どう', 'こう', 'そう', 'ある',
                     'あり', 'あります', 'ありました', 'ありません', 'いる', 'います', 'いません', 'する', 'します',
                     'しました', 'しません', 'して', 'なる', 'なります', 'なりました', 'いい', 'よく', 'ない', 'なく',
                     'たくさん', 'みんな', 'ください', 'ちゃんと', 'やはり', 'すべて', 'ほど', 'まま', 'ため', 'よう',
                     'こと', 'もの', 'ところ', 'はず', 'とき', 'でも', 'だから', 'それから', 'なので', 'ですから',
                     'しかし', 'そして', 'つまり', 'たとえば', 'なに', 'なん', 'だれ', 'いつ'}, key=len, reverse=True)
# Glossed words kept whole even though they read as grammar units: conjunctions and "even".
WHOLE = {'でも', 'だから', 'ですから', 'なので', 'それから', 'それで', 'それに', 'では', 'じゃ', 'しかし', 'そして'}
KOSOADO = ('これ', 'それ', 'あれ', 'どれ', 'この', 'その', 'あの', 'どの', 'ここ', 'そこ', 'あそこ', 'どこ', 'こちら',
           'そちら', 'どちら', 'こんな', 'そんな', 'あんな', 'どんな', 'こう', 'そう', 'どう', '何', 'なに', 'なん',
           '誰', 'だれ', 'いつ')
CONJUNCTIONS = ('それから', 'それで', 'それに', 'それでも', 'そうして', 'そして', 'それぞれ', 'そうです')
NOMINAL = ('こと', 'の', 'もの', 'ところ', 'はず', 'ため', 'よう', 'ん', 'とき', '時', '方', 'ほう')
QUOTE_VERB = re.compile('^(?:思|おも|言|い[いうっわえま]|書|か[いくけ]|聞|き[いくけ]|呼|よ[ぶびん]|考|かんが|答|こた|伝|つた)')
POLITE_END = ('ます', 'ました', 'ません', 'ませんでした', 'です', 'でした', 'でしょう')
PLAIN_END = re.compile('(?:[うくぐすつぬぶむる]|[ただ]|ない|なかった|たい)$')  # a plain form that can describe a noun
E_ROW = dict(zip('えけげせぜてでねへべぺめれ', 'うくぐすずつづぬふぶぷむる'))
A_ROW = set('わかがさたなばまら')


# ---- the dictionary: spelling -> parts of speech, and whether a sense is "to be able to" ----

@functools.lru_cache(maxsize=1)
def lexicon(path=DICTIONARY):
    """{spelling: (frozenset of JMdict types, able)} from data/dictionary.tsv (ja, kana and forms); able: a sense
    is "to be able to ...", the mark of a potential verb listed as its own entry (読める)."""
    out, header = {}, None
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#'):
                continue
            cells = line.rstrip('\n').split('\t')
            if header is None:
                header = cells
                continue
            row = dict(zip(header, cells))
            pos = frozenset(filter(None, re.split(r'[,|]', row.get('pos', ''))))
            able = 'able to' in (row.get('glosses') or '').split(';')[0]
            for spelling in [row.get('ja'), row.get('kana')] + (row.get('forms') or '').split(','):
                if spelling:
                    old = out.get(spelling)
                    out[spelling] = (pos | old[0], able or old[1]) if old else (pos, able)
    return out


KIND_POS = {inflection.V1: ('v1',), inflection.V5: ('v5',), inflection.VK: ('vk',), inflection.VS: ('vs',),
            inflection.ADJ: ('adj-i', 'adj-ix')}


def predicate(token, lex=None):
    """(kind, steps, dictionary form) when the token is a verb ('verb') or i-adjective ('adj') form, by
    inflection.deinflect checked against the dictionary's types (the fewest steps back first), else None. A
    suru-noun as written is a noun; with する after it (確認します) it is a verb."""
    lex = lexicon() if lex is None else lex
    return _predicate(token, id(lex)) if lex is lexicon() else _find_predicate(token, lex)


@functools.lru_cache(maxsize=65536)
def _predicate(token, _which):
    return _find_predicate(token, lexicon())


def _find_predicate(token, lex):
    if len(token) < 2 and token not in lex:
        return None
    for form, kinds, steps in inflection.deinflect(token):
        found = lex.get(form)
        if not found:
            continue
        types = found[0]
        for kind, prefixes in KIND_POS.items():
            if kind == inflection.VS and steps == 0 and not form.endswith('する'):
                continue  # 確認 alone is a noun
            if kinds & kind and any(t.startswith(prefixes) for t in types):
                return ('adj' if kind == inflection.ADJ else 'verb', steps, form)
        if steps and kinds & inflection.NOUN and any(t.startswith('vs') for t in types) and \
                re.search('(?:し|す|さ|でき)', token[len(form):]):
            return 'verb', steps, form + 'する'
    for form, kinds, steps in inflection.deinflect(token):  # a loanword with する: アップデートします
        if steps and kinds & inflection.NOUN and KATAKANA_WORD.fullmatch(form) and \
                re.match('(?:し|す|さ|でき)', token[len(form):]):
            return 'verb', steps, form + 'する'
    return None


# ---- tokens ----

def _split(run, glossed=False):
    """A run of bare text (or a glossed word made only of grammar units) as tokens. In bare text a verb or adjective
    form of kana longer than the grammar unit at the same place is taken whole (なって, できます)."""
    out, chunk, i = [], '', 0
    while i < len(run):
        took = next((unit for unit in (GRAMMAR if glossed else BARE_WORDS + GRAMMAR) if run.startswith(unit, i)), None)
        if not glossed and not chunk:
            shortest = i + max(2, len(took or '') + 1)
            longer = next((run[i:j] for j in range(min(len(run), i + 12), shortest - 1, -1)
                           if re.fullmatch('[ぁ-ゖ]+', run[i:j]) and predicate(run[i:j]) is not None), None)
            took = longer or took
        if took is None:
            chunk += run[i]
            i += 1
            continue
        if chunk:
            out.append(chunk)
            chunk = ''
        out.append(took)
        i += len(took)
    if chunk:
        out.append(chunk)
    return out


def _known_piece(piece, lex):
    """A piece a glossed word may be split into: a grammar unit, a verb or adjective form, or a dictionary word of
    two characters or more (or one kanji)."""
    return piece in UNITS or predicate(piece, lex) is not None or (
        piece in lex and (len(piece) > 1 or re.match('[一-鿿々]', piece)))


@functools.lru_cache(maxsize=16384)
def _peel(word):
    """A glossed word as its pieces: whole when it is a dictionary word or a verb or adjective form; else split into
    the fewest known pieces, the longest first (読むと is 読む と, だと思います is だ と 思います), or whole when it
    can't be."""
    lex = lexicon()
    if word in WHOLE or word in lex or predicate(word, lex) is not None or len(word) > 24:
        return (word,)
    best = {len(word): ()}
    inside = lambda i: 0 < i < len(word) and KATAKANA.match(word[i - 1]) and KATAKANA.match(word[i])
    for start in range(len(word) - 1, -1, -1):
        if inside(start):
            continue  # a katakana word is never split
        options = [(word[start:end],) + best[end] for end in range(len(word), start, -1)
                   if end in best and not inside(end) and _known_piece(word[start:end], lex)]
        if options:
            best[start] = min(options, key=len)
    return best.get(0) or (word,)


def clean(text):
    """A piece of a reply as the reader sees it: tags and markdown marks gone, entities decoded."""
    return html.unescape(TAG.sub('', text)).replace('*', '').replace('`', '').replace('_', '')


def tokens(segments):
    """The tokens of a sentence's segments [(kind, text)]: kind 'w' a glossed word, 't' bare text. English runs and
    code become NOUN; spaces go; bare text is split into grammar units and bare words; a glossed word stays whole
    unless it is only grammar units (には, のは), or splits into known pieces (_peel: 読むと, のある)."""
    out = []
    for kind, text in segments:
        text = INLINE_CODE.sub(f' {NOUN} ', text)
        text = LATIN_RUN.sub(f' {NOUN} ', clean(text) if kind == 't' else text)
        for part in text.split():
            if part == NOUN:
                if not out or out[-1] != NOUN:
                    out.append(NOUN)
                continue
            if kind == 'w' and part not in WHOLE:
                pieces = _split(part, glossed=True)
                out += pieces if all(p in UNITS for p in pieces) else list(_peel(part))
            elif kind == 'w':
                out.append(part)
            else:
                out += _split(part)
    return [t for t in out if t]


# ---- the scanner ----

def _is_predicate_end(token, lex):
    """A token that ends a clause: a polite ending, a verb or adjective form, or the copula."""
    return token.endswith(POLITE_END) or token in ('だ', 'だった', 'ない') or predicate(token, lex) is not None


def scan(segments, lex=None):
    """{pattern id: uses} for one sentence (segments as tokens takes them)."""
    lex = lexicon() if lex is None else lex
    toks = tokens(segments)
    found = {}
    add = lambda id_: found.__setitem__(id_, found.get(id_, 0) + 1)
    n = len(toks)
    pred = {}

    def p(i):  # predicate() of token i, cached
        if i not in pred:
            pred[i] = predicate(toks[i], lex) if 0 <= i < n and toks[i] not in UNITS and toks[i] != NOUN else None
        return pred[i]

    def nxt(i):
        return toks[i + 1] if i + 1 < n else ''

    def prv(i):
        return toks[i - 1] if i > 0 else ''

    def is_content(t):
        return bool(t) and t not in UNITS and t not in PUNCTUATION

    def noun_at(j):
        """Token j is a noun (or an English run): what a particle or a clause before it can attach to."""
        t = toks[j] if 0 <= j < n else NOUN
        if t == NOUN:
            return True
        if not is_content(t) or t in NOMINAL or t.startswith(KOSOADO[:8]) and len(t) <= 2:
            return False
        types = (lex.get(t) or (frozenset(), False))[0]
        return bool(types & {'n', 'n-adv', 'n-t', 'n-suf', 'pn', 'num', 'ctr', 'adj-no'}) or (not types and p(j) is None)

    for i, t in enumerate(toks):
        before, after = prv(i), nxt(i)
        # particles
        if t == 'は':
            add('wa')
        elif t == 'が':
            if before.endswith(POLITE_END) or after == '、' or before in ('だ',) or (
                    p(i - 1) and p(i - 1)[0] == 'verb' and after in ('、', '')):
                add('kedo')
            else:
                add('ga')
        elif t == 'を':
            add('wo')
        elif t == 'に':
            types = (lex.get(before) or (frozenset(), False))[0]
            if not ('adj-na' in types and (not types & {'n', 'n-adv', 'n-t'} or p(i + 1))):
                add('ni')  # not the adverb of a na-adjective: 簡単にできます
        elif t == 'で':
            if after == '、' and noun_at(i - 1) and i > 0:
                add('te-link')  # the copula's te-form: "X で、" is "it is X, and"
            elif not (after == 'は' and toks[i + 2:i + 3] in (['ありません'], ['ない'], ['なく'])):
                add('de')
        elif t == 'へ':
            add('e')
        elif t == 'の':
            na = before == 'な' and 'adj-na' in (lex.get(prv(i - 1)) or (frozenset(), False))[0]
            if na or (_is_predicate_end(before, lex) and before not in UNITS and not before.endswith(POLITE_END)):
                if after == 'は':
                    add('cleft')
                else:
                    add('nominal')
            elif before in ('くらい', 'ぐらい', 'みたい') or is_content(before) or before == NOUN or before in UNITS:
                add('no')
        elif t == 'も':
            if (before.endswith(('て', 'で')) and len(before) > 1 and p(i - 1)) or before in ('くて',):
                add('temo')
            elif not (before == 'か' and nxt(i).startswith('しれ')):
                add('mo')
        elif t == 'か':
            if not after.startswith('しれ') and not (after == 'も' and toks[i + 2:i + 3] and toks[i + 2].startswith('しれ')):
                add('ka')
        elif t == 'と':
            if after.startswith(('いう', '言う', 'いった', '言った')) and (noun_at(i + 2) or after in ('いう', '言う')):
                add('to-iu')
            elif QUOTE_VERB.match(after):
                add('quote')
            elif p(i - 1) and p(i - 1)[0] == 'verb' and (p(i - 1)[1] == 0 or before.endswith('ない')) \
                    and not before.endswith(POLITE_END):
                add('to-when')
            else:
                add('to')
        elif t in ('ね', 'よ', 'よね'):
            add('ne-yo')
        elif t == 'や' and before:
            add('ya')
        elif t in ('だけ', 'しか', 'ばかり'):
            add('dake')
        elif t == 'から':
            if _is_predicate_end(before, lex) and before != NOUN:
                add('reason')
            else:
                add('kara-from')
        elif t == 'ので' or (t == 'なので' and i > 0):
            add('reason')
        elif t in ('けど', 'けれど', 'けれども') or (t == 'のに' and _is_predicate_end(before, lex)):
            add('kedo')
        elif t == 'まで':
            add('made')
        elif t == 'より':
            add('yori')
        elif t == 'なら':
            add('nara')
        elif t in ('だ', 'だった'):
            add('da')
        elif t == 'って' and i > 0 and (noun_at(i - 1) or _is_predicate_end(before, lex)):
            add('to-iu')
        elif t == 'でも' and i > 0 and before not in PUNCTUATION:
            add('mo')  # X でも: "even at X"
        elif t == 'という':
            add('to-iu')
        elif t in ('でしょう', 'だろう'):
            add('conjecture')
        elif t in ('ではありません', 'じゃありません'):
            add('masen')
        elif t in ('ではない', 'じゃない'):
            add('plain-neg')
        elif t in ('んです', 'んだ'):
            add('nominal')
        # stacked particles: に, で, と, から, まで, へ, より then は, も or の (not the negative copula では)
        if t in ('に', 'で', 'と', 'から', 'まで', 'へ', 'より') and after in ('は', 'も', 'の'):
            if not (t == 'で' and after == 'は' and toks[i + 2:i + 3] in (['ありません'], ['ない'])):
                add('stack')
        # demonstratives and question words
        if t.startswith(KOSOADO) and t not in CONJUNCTIONS and not t.startswith(('そうです', 'そうし', 'いつも')):
            add('kosoado')
            if len(t) > 2 and t.endswith('も'):
                add('mo')  # どちらも, 誰も
        if t == 'まだ' or (t not in UNITS and t.startswith('まだ') and len(t) > 2):
            add('mada')
        # the polite copula and endings (a glossed word may carry them: 大丈夫です)
        if t in ('です', 'でした') or (t not in UNITS and re.search('(?:です|でした)(?:か|ね|よ)?$', t)
                                     and not t.endswith('ませんでした')):
            add('desu')
        if t == 'です' and before in ('ない',) or (t not in UNITS and t.endswith('ないです')):
            add('plain-neg')
        if t not in UNITS or t in ('ます', 'ません', 'ました', 'ましょう', 'ませんでした'):
            if re.search('ます(?:か|よ|ね|が|けど|から|ので)?$', t) or t == 'ます':
                add('masu')
            if 'ません' in t:
                add('masen')
            if 'ました' in t:
                add('mashita')
            if 'ましょう' in t:
                add('mashou')
            if t not in UNITS and re.search('(?:ます|ました|ません|ましょう|です|でした|でしょう)か$', t):
                add('ka')
        if t in UNITS or t == NOUN:
            continue
        # verb and adjective forms inside a glossed word
        if re.search('[てで]ください', t) or (t == 'ください' and before.endswith(('て', 'で'))):
            add('kudasai')
        if re.search('[てで](?:い(?:ます|まし|ませ|る|た|ない|なか|れ)|る$)', t) or (
                t.startswith(('います', 'いる', 'いた', 'いない', 'いません', 'いまし')) and before.endswith(('て', 'で'))
                and len(before) > 1):
            add('teiru')
        form = p(i)
        if form and form[2].endswith('する') and len(form[2]) > 2 and not re.search('(?:でき|出来)', t) or (
                form is None and re.search('[一-鿿々ァ-ヺー](?:し(?:ます|まし|ませ|て|た|な|よう|たい)|する|すれ|され|させ)', t)) or (
                t.startswith(('します', 'しまし', 'しませ', 'する', 'して', 'した', 'しない'))
                and (before == NOUN or re.search('[一-鿿々ァ-ヺー]$', before))):
            add('suru-verb')
        if (t.startswith(('なり', 'なっ', 'なる', 'なれ', 'ならな')) and not t.startswith('なるほど')) or \
                re.search('[くに]な(?:り|っ|る|れ|らな)', t):
            add('naru')
        if 'でしょう' in t or 'だろう' in t or 'かもしれ' in t or (t == 'かも' or t.endswith('かも')):
            add('conjecture')
        if re.search('(?:[いきぎしちにびみりえけげせてねべめれ一-鿿]たい|たく(?:ない|て|な)|たかった)', t) and p(i):
            add('tai')
        if (t.endswith('なら') and len(t) > 2) or (t.endswith('ならば')):
            add('nara')
        if re.search('[ただ]ら$', t) and p(i):
            add('tara')
        if (re.search('[えけげせてねべめれ]ば$', t) and p(i) and t not in ('例えば', 'たとえば')) or 'なければ' in t:
            add('ba')
        if re.search('の(?:は)$', t) and len(t) > 2:
            add('cleft')
        elif re.search('(?:の|こと)(?:が|を|も|で|に|です|だ)?$', t) and len(t) > 2 and predicate(
                re.sub('(?:の|こと)(?:が|を|も|で|に|です|だ)?$', '', t), lex):
            add('nominal')
        if 'ことができ' in t or 'ことが出来' in t:
            add('nominal')
        if re.search('[てで]も$', t) and len(t) > 2 and p(i) or re.search('くても$', t):
            add('temo')
        if t == 'こと' and _is_predicate_end(before, lex):
            add('nominal')
        able = _potential(t, lex)
        if able:
            negative = re.search('(?:ませ|ない|なか|なく)', t[t.find(able):] if able in t else t)
            add('neg-potential' if negative else 'potential')
        elif re.search('(?:され|させ)', t) or (re.search(f'[{"".join(A_ROW)}][れせ](?:る|ます|まし|ませ|た|て|ない|なか)',
                                                          t) and form and form[0] == 'verb' and form[1] > 0):
            add('passive')
        if form is None:
            if after and noun_at(i + 1) and t.endswith('のある'):
                add('relative')
            continue
        kind, steps, base = form
        polite = re.search('(?:ます|ました|ません|ましょう|ませんでした)', t)
        if kind == 'verb' or (kind == 'adj' and not t.endswith('い')) or t.endswith(('ない', 'なかった')):
            if t.endswith(('ない', 'なかった', 'なく')) and steps > 0 and not polite and not t.endswith('ください'):
                add('plain-neg')
            if re.search('(?:[ただ]|かった)$', t) and steps > 0 and not polite:
                add('plain-past')
        if re.search('[てで]$', t) and steps > 0 and after not in ('も', 'は') and not after.startswith(
                ('い', 'くだ', 'み', 'しま', 'お', 'く', 'あ', 'も')) and not t.endswith('ください'):
            add('te-link')
        if not polite and (kind == 'verb' or t.endswith(('ない', 'なかった', 'たい'))) and PLAIN_END.search(t) \
                and after and noun_at(i + 1):
            add('relative')
        if kind == 'adj' and steps == 0 and after and noun_at(i + 1):
            add('adj-noun')
    return found


def _potential(token, lex):
    """The part of a token that makes it a potential form (できる, 読める, 見られる), or None."""
    if 'でき' in token or '出来' in token:
        return 'でき' if 'でき' in token else '出来'
    for form, kinds, steps in inflection.deinflect(token):
        if not kinds & inflection.V1 or len(form) < 2 or not form.endswith('る'):
            continue
        found = lex.get(form)
        if form.endswith('られる') and steps > 0:
            base = form[:-3] + 'る'
            if (lex.get(base) or (frozenset(),))[0] & {'v1'}:
                return form[:-3]
        e = form[-2]
        if e in E_ROW:
            godan = form[:-2] + E_ROW[e]
            gtypes = (lex.get(godan) or (frozenset(),))[0]
            if any(t.startswith('v5') for t in gtypes) and (found is None or found[1]):
                return form[:-2]
    return None


def scan_ids(segments, lex=None):
    """The pattern ids a sentence uses, in inventory order."""
    found = scan(segments, lex)
    return [i for i in IDS if found.get(i)]


# ---- the sentences of a reply ----

def strip_code(text):
    """The reply without fenced code (T3 shows no glosses there); inline code becomes a noun placeholder."""
    return FENCE.sub('\n', text)


def pieces(text):
    """(plain text, [(kind, start, end, inner, title)]): the reply as the reader sees it, its <abbr>s in order (kind
    'w'), and the bare text between them (kind 't'), with their places in the plain text."""
    text = strip_code(text)
    out, parts, last, at = [], [], 0, 0
    for m in ABBR.finditer(text):
        bare = text[last:m.start()]
        if bare:
            parts.append(('t', at, at + len(bare), bare, None))
            out.append(bare)
            at += len(bare)
        inner = clean(m.group(2)).strip()
        title = TITLE.search(m.group(1))
        parts.append(('w', at, at + len(inner), inner, html.unescape(title.group(1)) if title else ''))
        out.append(inner)
        at += len(inner)
        last = m.end()
    if last < len(text):
        parts.append(('t', at, at + len(text) - last, text[last:], None))
        out.append(text[last:])
    return ''.join(out), parts


def annotation_texts(text):
    """The inner texts of a reply's <abbr>s in order, as T3 reports them (its annotations' words)."""
    return [inner for kind, _s, _e, inner, _t in pieces(text)[1] if kind == 'w']


def norm(word):
    """An annotation's text as the fingerprint takes it: tags and markdown marks gone, trimmed."""
    return clean(str(word)).strip()


def fingerprint(words):
    """A reply's fingerprint: its annotation texts in order, normalized and hashed (16 hex digits)."""
    return hashlib.sha256('\x1f'.join(norm(w) for w in words).encode('utf-8')).hexdigest()[:16]


def sentences(text):
    """The Japanese and mixed sentences of a reply: those whose 。 or ？ is an <abbr> (its title the translation).
    [{index: the mark's place among the reply's annotations, mark, translation, text, segments: [(kind, text)],
    jchars, en}]. A sentence starts after the previous mark, or at the last line break or English sentence end
    before its own mark."""
    plain, parts = pieces(text)
    words = [p for p in parts if p[0] == 'w']
    out, previous = [], 0
    for index, (_k, start, end, inner, title) in enumerate(words):
        if inner not in MARKS:
            continue
        begin = previous
        for m in BOUNDARY.finditer(plain, previous, start):
            begin = m.end()
        previous = end
        segments = []
        for kind, s, e, piece, _t in parts:
            if e <= begin or s >= start:
                continue
            cut = piece if kind == 'w' else plain[max(s, begin):min(e, start)]
            if kind == 'w' and (s < begin or e > start):
                continue
            segments.append((kind, cut))
        body = re.sub(r'^\s*(?:[-*>#]+|\d+\.)\s+', '', clean(plain[begin:start])).strip()
        out.append({'index': index, 'mark': inner, 'translation': title, 'text': body + inner,
                    'segments': segments, 'jchars': len(JAPANESE.findall(body)),
                    'en': len(ENGLISH_WORD.findall(body))})
    return out


def record(text, day, lex=None, plans=None):
    """What the hook sends about one reply (hub.py sentences records), or None when it has no Japanese or mixed
    sentence: {fp, day, v: VERSION, s: [[mark index, Japanese characters, English words, [pattern ids]]]}. A reply with
    render-time levels (twins.py) gets record_levels' fields too."""
    import twins
    if twins.has_levels(text):
        return record_levels(text, day, lex, plans)
    found = sentences(text)
    if not found:
        return None
    lex = lexicon() if lex is None else lex
    return {'fp': fingerprint(annotation_texts(text)), 'day': day, 'v': VERSION,
            's': [[s['index'], s['jchars'], s['en'], scan_ids(s['segments'], lex)] for s in found]}


def record_levels(text, day, lex=None, plans=None):
    """The record of a reply with render-time levels (twins.py), still never text: fp, the fingerprint of every glossed
    word in it (twins.annotation_texts: what no level shows at once, the key that dedupes it); s, its Japanese
    sentences (those of its twins), each by its mark's place among all those glossed words, with a fifth number, the
    open chance check-reply predicted for it, when its plan is here; e, its elements in order ([kind 's' a twin or 'w'
    a swapped word, threshold, the twin whose English holds the word or -1, propensities at -2..2 or [], expected opens
    or None, explored 1/0 or None]: twins.elements, with the plan's numbers); a, per glossed word the element it
    belongs to (-1: none, shown above off); fps, the fingerprint of what each level -2..3 shows (twins.fingerprints),
    which the collector matches a T3 message's annotations against, with the level the message reports; lv, the level
    it was shown at when sent (the plan's view), and d, the pattern ids of each twin check-reply dropped, when its plan
    is here (twins.plan_for). None when it has no Japanese sentence."""
    import twins
    if not twins.well_formed(text):
        return None  # markup check-reply didn't write (edited, cut short): what each level shows can't be trusted
    els, owners = twins.elements(text)
    shown = twins.inert_code(twins.render(text, twins.FULL))
    found = sentences(shown)
    if not found:
        return None
    full = [j for j, o in enumerate(owners) if twins.visible(els, o, twins.FULL)]
    texts = twins.annotation_texts(text)
    for level in twins.SHOWING:
        if twins.shown_texts(text, level) != [texts[j] for j, o in enumerate(owners) if twins.visible(els, o, level)]:
            return None  # glossed words the elements don't account for (one in code a level reads): no join to trust
    if len(full) != len(annotation_texts(shown)):
        return None
    lex = lexicon() if lex is None else lex
    plan = twins.plan_for(text, plans) or {}
    ps = plan.get('s') if isinstance(plan.get('s'), list) and len(plan['s']) == len(found) else None
    planned = plan.get('e') if isinstance(plan.get('e'), list) and len(plan['e']) == len(els) and all(
        isinstance(x, list) and len(x) == 5 and x[0] == e['kind'] and x[1] == e['on']
        for x, e in zip(plan['e'], els)) else None
    out = {'fp': fingerprint(twins.annotation_texts(text)), 'day': day, 'v': VERSION,
           's': [[full[s['index']], s['jchars'], s['en'], scan_ids(s['segments'], lex)]
                 + ([round(float(ps[k]), 4)] if ps else []) for k, s in enumerate(found)],
           'e': [[e['kind'], e['on'], -1 if e['parent'] is None else e['parent']]
                 + (list(planned[i][2:]) if planned else [[], None, None]) for i, e in enumerate(els)],
           'a': owners}
    fps = twins.fingerprints(text)
    out['fps'] = [fps[level] for level in twins.SHOWING]
    if isinstance(plan.get('view'), int):
        out['lv'] = plan['view']
    if isinstance(plan.get('d'), list) and plan['d']:
        out['d'] = [list(ids) for ids in plan['d'] if isinstance(ids, list)][:50]
    return out


def valid_record(r):
    """A sentences record as it arrives over the network: a reply's fields (record), and those of a reply with levels
    (record_levels) when it has them."""
    count = lambda x, top: isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= top
    share = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1
    ids = lambda x: isinstance(x, list) and len(x) <= len(IDS) and all(isinstance(i, str) and 0 < len(i) <= 20 for i in x)
    hexed = lambda x: isinstance(x, str) and re.fullmatch('[0-9a-f]{16}', x) is not None
    if not (isinstance(r, dict) and set(r) <= {'fp', 'day', 'v', 's', 'e', 'a', 'fps', 'lv', 'd'} and hexed(r.get('fp'))
            and isinstance(r.get('day'), str) and count(r.get('v', 0), 1000) and isinstance(r.get('s'), list)
            and 0 < len(r['s']) <= 200
            and all(isinstance(s, list) and len(s) in (4, 5) and count(s[0], 1000) and count(s[1], 10_000)
                    and count(s[2], 10_000) and ids(s[3]) and (len(s) == 4 or share(s[4])) for s in r['s'])):
        return False
    levels = {'e', 'a', 'fps'} & set(r)
    if not levels:
        return not {'lv', 'd'} & set(r) and all(len(s) == 4 for s in r['s'])
    e, a, fps = r.get('e'), r.get('a'), r.get('fps')
    level = lambda x, low: isinstance(x, int) and not isinstance(x, bool) and low <= x <= 3
    element = lambda x: (isinstance(x, list) and len(x) == 6 and x[0] in ('s', 'w') and level(x[1], -2)
                         and isinstance(x[2], int) and not isinstance(x[2], bool) and -1 <= x[2] < len(e)
                         and isinstance(x[3], list) and len(x[3]) in (0, 5) and all(share(v) for v in x[3])
                         and (x[4] is None or (isinstance(x[4], (int, float)) and not isinstance(x[4], bool)
                                               and 0 <= x[4] <= 100))
                         and x[5] in (None, 0, 1) and not isinstance(x[5], bool))
    return (levels == {'e', 'a', 'fps'} and isinstance(e, list) and len(e) <= 500 and all(element(x) for x in e)
            and isinstance(a, list) and len(a) <= 2000
            and all(isinstance(x, int) and not isinstance(x, bool) and -1 <= x < len(e) for x in a)
            and all(s[0] < len(a) for s in r['s'])
            and isinstance(fps, list) and len(fps) == 6 and all(hexed(x) for x in fps)
            and ('lv' not in r or level(r['lv'], -3))
            and ('d' not in r or (isinstance(r['d'], list) and len(r['d']) <= 50 and all(ids(x) for x in r['d']))))


# ---- the reader's status per pattern ----

def statuses(state):
    """{pattern id: known, learning or new} for every pattern: the state's grammar section, else known for the
    basics and new for the rest."""
    kept = (state or {}).get('grammar') if isinstance((state or {}).get('grammar'), dict) else {}
    out = {}
    for p in PATTERNS:
        entry = kept.get(p.id)
        status = entry.get('status') if isinstance(entry, dict) else None
        out[p.id] = status if status in STATUSES else ('known' if p.basic else 'new')
    return out


def learning(state):
    """The pattern being learned now (a Pattern) and since when, or (None, None)."""
    kept = (state or {}).get('grammar') if isinstance((state or {}).get('grammar'), dict) else {}
    for p in PATTERNS:
        entry = kept.get(p.id)
        if isinstance(entry, dict) and entry.get('status') == 'learning':
            return p, entry.get('since')
    return None, None


def valid_change(op):
    """What is wrong with a grammar op ({"set": [[pattern id, status]], "source"}), or None."""
    changes = op.get('set')
    good = (set(op) <= {'uid', 'day', 'at', 'machine', 'kind', 'set', 'source'} and isinstance(changes, list)
            and 0 < len(changes) <= len(IDS)
            and all(isinstance(c, list) and len(c) == 2 and c[0] in BY_ID and c[1] in STATUSES for c in changes)
            and sum(1 for c in changes if c[1] == 'learning') <= 1
            and isinstance(op.get('source'), str) and 0 < len(op['source']) <= 20)
    return None if good else 'grammar: {"set": [[pattern id, "known"|"learning"|"new"]], "source"}, one learning at most'


def apply(state, op, day):
    """A grammar op applied to the state: each pattern's status, with the day it changed; a pattern set to learning
    puts any other learning one back to new, so one is learned at a time."""
    kept = state.setdefault('grammar', {})
    if not isinstance(kept, dict):
        kept = state['grammar'] = {}
    changed = []
    for id_, status in op['set']:
        if status == 'learning':
            for other, entry in kept.items():
                if other != id_ and isinstance(entry, dict) and entry.get('status') == 'learning':
                    kept[other] = {'status': 'new', 'since': day}
                    changed.append([other, 'new'])
        old = statuses(state)[id_]
        if old != status:
            kept[id_] = {'status': status, 'since': day}
            changed.append([id_, status])
    return {'changed': changed}
