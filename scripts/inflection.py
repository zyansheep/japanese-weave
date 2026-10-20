"""Written forms of Japanese words back to their lexicon entries (Python 3.9+, standard library only).

T3 Code chats gloss every word of a fully Japanese sentence, conjugated verbs whole, so the gloss log holds forms like
なります or 待っています where the word list has なる and 待つ. deinflect() walks a form back, one ending at a time,
through polite forms (ます, ません, ました, ましょう(か)), te forms and what follows them (ている, てくる, てしまう...),
past, negative, potential, passive, causative, volitional and conditional forms, a stem with たい, やすい, にくい or
そう, i-adjective endings (adverbial く, so 見えなく is 見えない is 見える), na-adjectives with な or に, です after a
word, a trailing こと, and suru-nouns (確認します is 確認). A bare masu stem (休み, 作り) is left alone: it is usually a
noun. Lexicon.entry() keeps only the candidates that are real entries of the right kind: the word as written first,
then the one the fewest steps back (so 出てきます is 出てくる when the list has it, else 出る), then the earliest in
the list.

  Lexicon([{'ja': 'なる', 'kana': 'なる'}]).entry('なります') == 'なる'

The endings are those of conjugate() in extension/src/levels.js, read backwards, plus the potential, passive,
causative and volitional forms and the te-form helpers. A verb ending in る after an i or e sound is ichidan unless
it is one of GODAN_RU (帰る, 入る, 知る...), so 要ります is never いる (to exist).
"""
import functools
import re
import unicodedata

from kana import hiragana

V1, V5, VK, VS, ADJ, NOUN = 1, 2, 4, 8, 16, 32  # an entry's kind: ichidan, godan, 来る, する, i-adjective, anything else
STEM, TE, FINAL = 64, 128, 256                  # on the way back: a masu stem, a te form, a form that ends a word
SURFACE = FINAL | TE | V1 | V5 | VK | VS | ADJ | NOUN  # a looked-up word (a bare masu stem is a noun, not a verb)
MAX_STEPS = 8
# dictionary ending -> its i, a, e, o forms, the te/ta stem and whether te/ta are voiced (で/だ), as in levels.js
GODAN = {'う': ('い', 'わ', 'え', 'お', 'っ', False), 'く': ('き', 'か', 'け', 'こ', 'い', False),
         'ぐ': ('ぎ', 'が', 'げ', 'ご', 'い', True), 'す': ('し', 'さ', 'せ', 'そ', 'し', False),
         'つ': ('ち', 'た', 'て', 'と', 'っ', False), 'ぬ': ('に', 'な', 'ね', 'の', 'ん', True),
         'ぶ': ('び', 'ば', 'べ', 'ぼ', 'ん', True), 'む': ('み', 'ま', 'め', 'も', 'ん', True),
         'る': ('り', 'ら', 'れ', 'ろ', 'っ', False)}
# godan verbs that look ichidan (an i or e sound before る), matched on the end of the entry as written
GODAN_RU = ('帰る', '入る', '知る', '走る', '切る', '要る', '限る', '減る', '蹴る', '滑る', '喋る', '握る', '参る', '散る',
            '混じる', '交じる', '湿る', '茂る', '焦る', '練る', '陥る', '遮る', '罵る', '嘲る', '捻る', '翻る', '蘇る',
            '甦る', '覆る', '弄る', '齧る', '照る', '耽る', '毟る', '漲る', '迸る')
I_OR_E = set('いきぎしじちぢにひびぴみりえけげせぜてでねへべぺめれ')
WRITTEN = re.compile('[\u3005\u4e00-\u9fff\u30a1-\u30fa\u30fc]')  # a kanji or katakana: a suru-noun's own part
POLITE = ('ます', 'ました', 'ません', 'ませんでした', 'ましょう', 'ましょうか', 'ますか', 'ませんか', 'ましたか')
HELPERS = (('いる', V1), ('る', V1), ('いく', V5), ('しまう', V5), ('おく', V5), ('みる', V1), ('くる', VK),
           ('ある', V5), ('くれる', V1), ('あげる', V1), ('もらう', V5))  # after a te form: 待っている is 待つ


def _rules():
    """(ending, what replaces it, the kinds the form may be, the kind it gives)."""
    rules = []
    add = lambda ending, base, needs, gives: rules.append((ending, base, needs, gives))
    for ending in POLITE + ('ながら', 'そう'):
        add(ending, '', FINAL, STEM)
    for ending in ('たい', 'やすい', 'にくい'):
        add(ending, '', ADJ, STEM)
    for ending in ('です', 'でした', 'ですか', 'でしょう', 'でしょうか', 'こと'):
        add(ending, '', FINAL, SURFACE)
    add('する', '', VS, NOUN)  # suru-nouns, and できる after one (確認できます)
    add('できる', '', V1, NOUN)
    add('な', '', FINAL, NOUN)  # na-adjectives: 大事な, 正確に
    add('に', '', FINAL, NOUN)
    # ichidan
    add('', 'る', STEM, V1)
    add('て', 'る', TE, V1)
    for past in ('た', 'たら', 'たり', 'よう', 'れば'):
        add(past, 'る', FINAL, V1)
    add('ない', 'る', ADJ, V1)
    add('られる', 'る', V1, V1)  # potential or passive
    add('れる', 'る', V1, V1)  # 見れる for 見られる
    add('させる', 'る', V1, V1)
    # godan
    for u, (i, a, e, o, te, voiced) in GODAN.items():
        t, d = ('で', 'だ') if voiced else ('て', 'た')
        add(i, u, STEM, V5)
        add(te + t, u, TE, V5)
        for past in (d, d + 'ら', d + 'り'):
            add(te + past, u, FINAL, V5)
        add(a + 'ない', u, ADJ, V5)
        add(a + 'れる', u, V1, V5)  # passive
        add(a + 'せる', u, V1, V5)  # causative
        add(e + 'る', u, V1, V5)  # potential: 戻れます is 戻る
        add(e + 'ば', u, FINAL, V5)
        add(o + 'う', u, FINAL, V5)
    for written in ('行', 'い'):
        add(written + 'って', written + 'く', TE, V5)
        for past in ('った', 'ったら', 'ったり'):
            add(written + past, written + 'く', FINAL, V5)
    # 来る and する
    for i, a, base in (('来', '来', '来る'), ('き', 'こ', 'くる')):
        add(i, base, STEM, VK)
        add(i + 'て', base, TE, VK)
        for past in ('た', 'たら', 'たり'):
            add(i + past, base, FINAL, VK)
        add(a + 'ない', base, ADJ, VK)
        add(a + 'られる', base, V1, VK)
        add(a + 'れる', base, V1, VK)
        add(a + 'よう', base, FINAL, VK)
        add(base[:-1] + 'れば', base, FINAL, VK)
    add('し', 'する', STEM, VS)
    add('して', 'する', TE, VS)
    for ending in ('した', 'したら', 'したり', 'しよう', 'すれば'):
        add(ending, 'する', FINAL, VS)
    add('しない', 'する', ADJ, VS)
    add('される', 'する', V1, VS)
    add('させる', 'する', V1, VS)
    # what follows a te form
    for t in ('て', 'で'):
        for helper, kind in HELPERS:
            add(t + helper, t, kind, TE)
        add(t + 'ください', t, FINAL, TE)
    add('ちゃう', 'て', V5, TE)
    add('じゃう', 'で', V5, TE)
    # i-adjectives (and the negative ない, which ends like one)
    for ending in ('かった', 'くて', 'く', 'ければ', 'さ', 'そう'):
        add(ending, 'い', FINAL, ADJ)
    add('くない', 'い', ADJ, ADJ)
    add('くなる', 'い', V5, ADJ)
    add('ないで', 'ない', FINAL, ADJ)
    return rules


RULES = _rules()
BY_LAST = {}
for _rule in RULES:
    BY_LAST.setdefault(_rule[0][-1:], []).append(_rule)


def normalize(word):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(word)))


def fold(word):
    """Width, spacing and script folded, for comparing with entries and their readings (as levels.js normalizeJa)."""
    return hiragana(normalize(word)).lower()


@functools.lru_cache(maxsize=8192)
def deinflect(word):
    """Every way back from a written form toward a dictionary form: ((form, kinds, steps), ...), the word itself first,
    then by steps."""
    out, seen = [], set()
    level = [(normalize(word), SURFACE)]
    for steps in range(MAX_STEPS + 1):
        following = []
        for form, kinds in level:
            if (form, kinds) in seen:
                continue
            seen.add((form, kinds))
            out.append((form, kinds, steps))
            for ending, base, needs, gives in BY_LAST.get(form[-1:], []) + BY_LAST.get('', []):
                if not kinds & needs or not form.endswith(ending):
                    continue
                stem = form[:len(form) - len(ending)]
                found = stem + base
                if not found or gives == NOUN and not WRITTEN.search(stem):
                    continue
                following.append((found, gives))
                if gives == ADJ and found.endswith('よい'):  # いい conjugates as よい
                    following.append((found[:-2] + 'いい', ADJ))
        level = following
    return tuple(out)


def word_class(ja, kana=''):
    """What kind of word an entry is, from how it ends (the lexicon has no parts of speech)."""
    reading = hiragana(kana or ja)
    if ja.endswith('する'):
        return VS
    if reading.endswith('くる') and (ja.endswith('来る') or ja == 'くる' or ja.endswith(('てくる', 'でくる'))):
        return VK
    if ja.endswith('る') and len(reading) >= 2:
        return V5 if ja.endswith(GODAN_RU) or reading[-2] not in I_OR_E else V1
    if ja[-1:] in GODAN and len(ja) >= 2:
        return V5
    if ja.endswith('い'):
        return ADJ
    return NOUN


class Lexicon:
    """The entries of a word list ([{ja, kana}] in lexicon order), for finding the entry a written form belongs to."""

    def __init__(self, words):
        self.forms = {}  # folded spelling -> [(index, 0 as written or 1 its reading, kind, ja)]
        for index, w in enumerate(words):
            kind = word_class(w['ja'], w.get('kana') or '')
            for via, spelling in enumerate((w['ja'], w.get('kana') or '')):
                if spelling:
                    self.forms.setdefault(fold(spelling), []).append((index, via, kind, w['ja']))
        self.cache = {}

    def entry(self, word):
        """The entry (its ja) that word is, as written or a form of it, or None."""
        if word not in self.cache:
            best = None
            for form, kinds, steps in deinflect(word):
                if best is not None and steps > best[0]:
                    break
                if steps and not kinds & NOUN and len(form) < 2:
                    continue
                for index, via, kind, ja in self.forms.get(fold(form), ()):
                    if kinds & NOUN or kinds & kind:
                        best = min(best or (steps, via, index, ja), (steps, via, index, ja))
            self.cache[word] = best[3] if best else None
        return self.cache[word]


_last = (None, None)


def lexicon(words):
    """A Lexicon for these words, reused while they stay the same."""
    global _last
    key = tuple((w['ja'], w.get('kana') or '') for w in words)
    if _last[0] != key:
        _last = (key, Lexicon(words))
    return _last[1]
