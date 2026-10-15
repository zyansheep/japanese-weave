"""Kana to modified-Hepburn romaji with macrons, for the sound hints (Python 3.9+, standard library only).

  romaji('とうきょう') == 'tōkyō'    romaji('がっこう') == 'gakkō'    romaji('おもう', verb_u=True) == 'omou'
"""
import re

_BASE = {
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
    'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'ゐ': 'i', 'ゑ': 'e', 'を': 'o', 'ん': 'n',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'ゔ': 'vu', 'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o', 'ゎ': 'wa',
}
_SMALL_Y = {'ゃ': 'a', 'ゅ': 'u', 'ょ': 'o'}
_SMALL_V = {'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o'}
_MACRON = {'a': 'ā', 'i': 'ī', 'u': 'ū', 'e': 'ē', 'o': 'ō'}


def hiragana(text):
    """Katakana to hiragana; the long-vowel mark ー and everything else stay as they are."""
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in str(text))


def is_kana(text):
    return bool(text) and all('ぁ' <= c <= 'ゖ' or 'ァ' <= c <= 'ヺ' or c in 'ーゝゞヽヾ' for c in text)


def _syllables(kana):
    out = []
    i = 0
    while i < len(kana):
        c = kana[i]
        nxt = kana[i + 1] if i + 1 < len(kana) else ''
        if c in _BASE and nxt in _SMALL_Y and _BASE[c].endswith('i') and c != 'い':
            stem = _BASE[c][:-1]
            stem = {'sh': 'sh', 'ch': 'ch', 'j': 'j'}.get(stem, stem + ('' if stem.endswith(('sh', 'ch', 'j')) else 'y'))
            out.append(stem + _SMALL_Y[nxt])
            i += 2
        elif c in _BASE and nxt in _SMALL_V and c not in _SMALL_V and c not in 'あいうえお':
            # ティ, ファ, ウィ, ヴァ and the like (loanwords)
            stem = {'て': 't', 'で': 'd', 'ふ': 'f', 'う': 'w', 'ゔ': 'v', 'し': 'sh', 'じ': 'j', 'ち': 'ch',
                    'つ': 'ts', 'と': 't', 'ど': 'd'}.get(c, _BASE[c][:-1])
            out.append(stem + _SMALL_V[nxt])
            i += 2
        else:
            out.append(c)
            i += 1
    return out


def romaji(kana, verb_u=False):
    """Modified Hepburn: long o/u/a/e get macrons (ou, oo, uu, aa, ee, ー); ei and ii stay. verb_u keeps a
    final う that is okurigana (思う omou, not omō)."""
    kana = hiragana(re.sub(r'\s+', '', str(kana)))
    parts = _syllables(kana)
    out = []
    geminate = False
    for k, part in enumerate(parts):
        if part == 'っ':
            geminate = True
            continue
        if part == 'ー':
            if out and out[-1] and out[-1][-1] in _MACRON:
                out[-1] = out[-1][:-1] + _MACRON[out[-1][-1]]
            continue
        roman = _BASE.get(part, part) if len(part) == 1 else part
        last = k == len(parts) - 1
        prev = out[-1] if out else ''
        vowel = prev[-1:] if prev else ''
        merge = ((vowel == 'o' and part in ('う', 'お')) or (vowel == 'u' and part == 'う')
                 or (vowel == 'a' and part == 'あ') or (vowel == 'e' and part == 'え'))
        if merge and not (verb_u and last and part == 'う'):
            out[-1] = prev[:-1] + _MACRON[vowel]
            continue
        if geminate and roman:
            roman = ('t' + roman) if roman.startswith('ch') else roman[0] + roman
            geminate = False
        if prev.endswith('n') and part != 'ん' and roman[:1] in ('a', 'i', 'u', 'e', 'o', 'y') and parts[k - 1] == 'ん':
            out[-1] = prev + "'"
        out.append(roman)
    return ''.join(out)
