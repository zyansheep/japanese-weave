"""Render-time levels in chat replies (docs/chat-weave.md, "Levels after the fact"): a reply carries its Japanese at
every level at once, code decides for each element the lowest level at which it shows, and the client (T3 Code)
renders the message at the reader's current level, so the panel's arrows and English only change every message on
screen after the fact. Python 3.9+, standard library only.

What Claude writes (the authoring syntax, read by fw.py check-reply: parse_authored): the reply in English, keeping
the weave's word swaps whose sense fits as <abbr title="reading · meaning">JAPANESE</abbr>, and right after an English
sentence that can be Japanese within the weave's limits, on the same line, its Japanese twin in <ja>...</ja>: one or
two Japanese or mixed sentences glossed as before, each ending with a bare 。 or ？. The twin's English sentence is the
text right before the <ja>: back to the end of the sentence before it (a . ! ? : or ; followed by a space, the
abbreviations in ABBREVIATIONS aside), the end of an earlier </ja> on the line, or the start of the line after any
list, heading or quote marker. Code puts that English sentence on each bare closing mark as its translation, so it is
written once, and adds a closing mark the twin lacks.

What Claude sends (the final markup, written by code: markup; the contract with the T3 clients):
  - a swapped word in English text:
    <abbr title="reading · meaning" data-en="original English" data-on="N">JAPANESE</abbr>, the gloss at levels >= N
    and the plain English of data-en below;
  - a twinned sentence: <span data-jw="ja" data-on="N">JAPANESE</span><span data-jw="en" data-on="N">ENGLISH</span>,
    the Japanese at levels >= N and the English below; the Japanese's words are glossed abbrs with no data-on (it shows
    or hides whole), the English keeps its own swaps, whose thresholds are at most N (a sentence turns English before
    its words do as the level goes down);
  - anything without data-on or data-jw renders as before, so older messages are unchanged.
A threshold is in -2..3 (3: full only). At -3 (off, and English only) nothing is Japanese. render(text, level) is the
reply as the reader sees it at a level, in the older markup every other reader of transcripts understands.

Thresholds (assign). One priority order per reply and kind, cut at each budgeted level's lookup budget (-2..2, the
level's intensity.budget per 100 of the reply's English words): twins spend the sentences' part, SENTENCE_SHARE of
it, each Japanese sentence at its predicted open chance but never less than the price of a sentence (so no level asks
for more Japanese sentences than the count before the sentence model did: the user's densities stay), and words the
whole budget at their current lookup chance, as the weave's planner gives a chat reply's learning words (it has no
sentence candidates there). Levels are filled from -2 up, each starting from the level below, so the sets are nested.
At each level twins come first (a twin shown hides its English sentence's swaps, whose lookups it then charges to the
words' part as the costly words in its Japanese), then words, at most intensity.per_sentence costly words in one
English sentence and one use of each learning word below full; the element at the edge of a part gets in with the
chance (what is left) / (its cost), so short replies still get some on average and a level's expected spend is its
part (assign: systematic rounding, one draw per reply and part). Words known at a glance
cost nothing and show from -2 (in an English twin, while it is English); dictionary words the reader doesn't have
yet (the weave's pool) and a learning word's later uses show only at full, as before; whatever no budgeted level took
shows at full. The order is by value per expected open: a word's learning value exp(-|logit p - logit TARGET_P|) (1 at
the target chance) over p; a twin's SENTENCE_VALUE plus the learning values of the costly words in its Japanese plus
PATTERN_VALUE when it uses this week's learning pattern, over its sentences' part.

Exploration. Each element's place in its order is, with chance EXPLORE, a uniform random place instead of its rank,
drawn afresh for every reply (the element's x: 1 when explored). So every element has a known chance of showing at
each level that is never 0 or 1 by the model's predictions alone, and the elements placed at random are a sample the
model didn't choose: calibrate compares its expected opens with the seen ones there (sentence_model.exploration).

Propensities (propensities). An element's propensity at a level is the chance it shows there under this
randomization: the share of DRAWS independent runs of assign (each its own coin and place for every element, and its
own rounding draws) in which it is visible at that level, seeded by the reply so a re-check repeats it. With 1,000
draws a propensity is within about 0.03 of the exact chance (95%), exact for the elements whose placement no draw can
change (0 or 1). check-reply keeps them with the thresholds in a local file (remember_plan) under the reply's
fingerprint, and the hook's sentence record carries them (grammar.record): never text.
"""
import hashlib
import html
import math
import random
import re
import time

import fwlib
import grammar
import intensity

OFF, FULL = intensity.OFF, intensity.FULL
BUDGETED = (-2, -1, 0, 1, 2)          # the levels with a lookup budget, filled in this order
SHOWING = BUDGETED + (FULL,)          # the levels with Japanese; an element's threshold is one of them
EXPLORE = 0.2                         # the chance an element's place in its order is drawn at random
DRAWS = 1000                          # runs of assign for the propensities
MAX_SENTENCES = 2                     # Japanese sentences in one twin (a long English sentence split in two)
SENTENCE_VALUE = 1.0                  # a twin's own value (reading practice), in learning words at their target
PATTERN_VALUE = 1.0                   # and what this week's learning pattern adds
TARGET_P = 0.25                       # a learning word's best lookup chance (knowledge.TARGET_P, levels.js TARGET_P)
PRIOR_P = 1 / (1 + math.e)            # the lookup chance of a word with no estimate (k 1.0, levels.js PRIOR_K)
SWAPS = 'weave-swaps.json'            # the weave's swaps per chat session, for check-reply (remember_swaps)
PLANS = 'reply-plans.json'            # each checked reply's thresholds and propensities (remember_plan)
SESSIONS_KEPT = 30
PLANS_KEPT = 500
KEEP_MS = 3 * 86_400_000
MARKS = ('。', '？')
END_MARKS = MARKS + ('！',)

TAG_BODY = r'((?:[^>"\']|"[^"]*"|\'[^\']*\')*)'  # a tag's attributes, a > inside quotes included
ABBR = re.compile(r'<abbr\b' + TAG_BODY + r'>(.*?)</abbr>', re.S | re.I)
ATTR = re.compile(r'([\w-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'>]+))')  # as HTML reads them
TWIN_OPEN = re.compile(r'<span\b' + TAG_BODY + r'>', re.I)
SPAN_CLOSE = re.compile(r'</span\s*>', re.I)
BLANK_LINE = re.compile(r'\n[ \t]*\n')
THRESHOLD = re.compile(r'-2|-1|0|1|2|3')  # the thresholds the contract allows: any other data-on renders as written
UNSURE = re.compile(r'\{\{([^{}]*?)→\s*(<abbr\b[^>]*>.*?</abbr>)\s*\}\}', re.S)
JA_TAG = re.compile(r'<(/?)ja>', re.I)
CODE_SPAN = re.compile(r'``.+?``|`[^`\n]+`')
FENCE_LINE = re.compile(r'^\s*(```|~~~)')
PLACEHOLDER = '\ue000-\uf8ff'
PROTECTED = re.compile(f'[{PLACEHOLDER}]')
# Where an English sentence ends inside a line: a . ! ? : or ; after anything but a space (a word, a number, 5%, 30°,
# "...", _italic_, ~~struck~~, a placeholder for code or a glossed word), with any closing quotes, brackets or emphasis
# marks after it, then a space; or an emoji before a space, which ends a sentence of its own.
EMOJI = '\u2600-\u27bf\U0001f300-\U0001faff'
BOUNDARY = re.compile(rf'(?<=\S)[.!?:;][\'"”’)\]*_~]*(?=\s)|[:.](?=\*\*\s)|[{EMOJI}](?=\s)')
SENTENCE_END = re.compile(r'[.!?][\'"”’)\]*_~]*\s')  # inside a twin's English: one sentence too many when 2+
TASK = re.compile(r'^\[[ xX]\]')
ABBREVIATIONS = ('e.g.', 'i.e.', 'etc.', 'vs.', 'cf.', 'approx.', 'mr.', 'mrs.', 'ms.', 'dr.', 'no.', 'fig.')
PREFIX = re.compile(r'^\s*(?:(?:[-*+]|\d+[.)])\s+|#{1,6}\s+|>\s*)*')
LETTER = re.compile(f'[A-Za-z{PLACEHOLDER}]')
LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')


def esc(text):
    """An attribute value: & " < > as entities."""
    return html.escape(str(text), quote=True).replace('&#x27;', "'")


def attrs(raw):
    """{name: value} of a tag's attributes, entities decoded; the first of a name counts, as in HTML."""
    out = {}
    for k, *values in ATTR.findall(raw or ''):
        out.setdefault(k.lower(), html.unescape(next((v for v in values if v), '')))
    return out


def meaning(title):
    """The meaning of a gloss title ('reading · meaning / alternative', or just the meaning for a kana word): after
    the reading, before the alternatives, as the clients take it."""
    _reading, dot, rest = str(title or '').partition('·')
    return (rest if dot else _reading).split(' / ')[0].strip()


def plain(text):
    """English as a translation reads: tags gone (an abbr becomes its data-en, else its meaning), links their text,
    markdown marks and extra spaces gone, entities decoded."""
    def word(m):
        a = attrs(m.group(1))
        return a.get('data-en') or meaning(a.get('title'))
    text = ABBR.sub(word, str(text))
    text = LINK.sub(r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text).replace('**', '').replace('__', '').replace('`', '')
    return re.sub(r'\s+', ' ', text).strip()


# ---- the final markup: reading it back and rendering it ----

LIST_ITEM = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s')
INDENTED = re.compile(r'^(?: {4}|\t)')


def _indented(text):
    """[(is_code, piece)] of text outside fences: an indented code block (lines indented four spaces or a tab, at the
    start or after a blank line, not under a list item, where markdown reads them as a list's continuation) is code."""
    lines = text.split('\n')
    out, code, previous, blank = [], [], None, True
    for i, line in enumerate(lines):
        piece = line + ('\n' if i < len(lines) - 1 else '')
        if code and (INDENTED.match(line) or not line.strip()):
            code.append(piece)
            continue
        if code:
            out.append((True, ''.join(code)))
            code = []
        if INDENTED.match(line) and line.strip() and blank and not (previous and LIST_ITEM.match(previous)):
            code.append(piece)
            continue
        out.append((False, piece))
        if line.strip():
            previous = line if not INDENTED.match(line) else previous
        blank = not line.strip()
    if code:
        out.append((True, ''.join(code)))
    merged = []
    for is_code, piece in out:
        if merged and merged[-1][0] == is_code:
            merged[-1] = (is_code, merged[-1][1] + piece)
        else:
            merged.append((is_code, piece))
    return merged


def _chunks(text):
    """[(is_code, piece)]: fenced code blocks and indented ones apart, as markdown reads them (T3 shows no markup in
    them)."""
    out, at = [], 0
    for m in grammar.FENCE.finditer(text):
        if m.start() > at:
            out += _indented(text[at:m.start()])
        out.append((True, m.group()))
        at = m.end()
    if at < len(text):
        out += _indented(text[at:])
    return out


def _code_out(piece):
    """(the piece with each inline code span one private-use character, the spans): markup in code is text."""
    saved = []
    return CODE_SPAN.sub(lambda m: saved.append(m.group()) or chr(0xE000 + len(saved) - 1), piece), saved


class Span:
    """A twin span as the clients read it: kind 'ja' or 'en', its threshold as written (on: the number when it is one
    of -2..3, else None: rendered as written), where its opening tag starts and ends, where its first </span> after
    it starts and ends (None when it never closes), and its content."""
    __slots__ = ('kind', 'on', 'start', 'open_end', 'close', 'close_end', 'content', 'nested', 'split')


def _spans(p):
    """The twin spans in p (code out), each matched to its </span> as HTML nests spans, and whether the message is
    broken as the clients judge it: a twin span that never closes, holds a blank line before the first </span> after
    it, or sits inside another twin span."""
    found, stack = [], []
    for m in re.finditer(TWIN_OPEN.pattern + '|' + SPAN_CLOSE.pattern, p, re.I):
        if m.group().startswith('</'):
            if stack:
                sp = stack.pop()
                if sp is not None:
                    sp.close, sp.close_end = m.start(), m.end()
            continue
        a = attrs(m.group(1))
        if a.get('data-jw') not in ('ja', 'en'):
            stack.append(None)
            continue
        sp = Span()
        sp.kind, sp.start, sp.open_end, sp.close, sp.close_end = a['data-jw'], m.start(), m.end(), None, None
        sp.on = int(a['data-on']) if THRESHOLD.fullmatch(a.get('data-on', '') or '') else None
        sp.nested = any(x is not None and x.on is not None for x in stack)
        stack.append(sp)
        found.append(sp)
    for sp in found:
        sp.content = p[sp.open_end:sp.close if sp.close is not None else len(p)]
        first = SPAN_CLOSE.search(p, sp.open_end)
        sp.split = bool(first and BLANK_LINE.search(p[sp.open_end:first.start()]))
    valid = [sp for sp in found if sp.on is not None]
    broken = any(sp.close is None or sp.split or sp.nested for sp in valid)
    return valid, broken


def _pairs(p, valid):
    """{id of a ja span: its en span}: a Japanese span pairs with an English one right after it (only whitespace
    between) with the same threshold."""
    out = {}
    for a in valid:
        b = next((x for x in valid if a.close is not None and x.start >= a.close_end), None)
        if a.kind == 'ja' and b is not None and b.kind == 'en' and a.on == b.on and not p[a.close_end:b.start].strip():
            out[id(a)] = b
    return out


def _last_translation(content):
    """The translation of a Japanese twin's last sentence mark, or ''."""
    found = ''
    for m in ABBR.finditer(content):
        if grammar.clean(m.group(2)).strip() in END_MARKS and attrs(m.group(1)).get('title', '').strip():
            found = attrs(m.group(1))['title'].strip()
    return found


SELF_CLOSING = re.compile(r'<(abbr|span)\b' + TAG_BODY + r'/>', re.I)
ABBR_OPEN = re.compile(r'<abbr\b' + TAG_BODY + r'>', re.I)
ABBR_CLOSE = re.compile(r'</abbr\s*>', re.I)


def _repair(p):
    """p (code out) as the clients read a finished message's broken tags: a self-closing <abbr/> or level <span/> is
    dropped; an abbr with a threshold whose </abbr> doesn't come before the next abbr or the end of its block is its
    English (data-en, else its gloss's meaning) in place of its tag and the word after it, at every level, and the rest
    of the block is read as it is."""
    def drop(m):
        a = attrs(m.group(2))
        return '' if m.group(1).lower() == 'abbr' or 'data-jw' in a or 'data-on' in a else m.group()
    p = SELF_CLOSING.sub(drop, p)
    out, at = [], 0
    for m in ABBR_OPEN.finditer(p):
        if m.start() < at:
            continue
        a = attrs(m.group(1))
        close, after = ABBR_CLOSE.search(p, m.end()), ABBR_OPEN.search(p, m.end())
        block = BLANK_LINE.search(p, m.end())
        limit = min(x.start() for x in (after, block) if x) if (after or block) else len(p)
        if _threshold(a) is None or (close and close.start() < limit):
            continue
        word = re.match(r'[^\s<]*', p[m.end():])
        english = a.get('data-en') or meaning(a.get('title'))
        out.append(p[at:m.start()] + esc_text(english))
        at = m.end() + word.end()
    return ''.join(out) + p[at:]


def tokens(text):
    """The final markup in document order, as the clients read it: ('raw', text); ('abbr', {attributes}, inner, as
    written); ('pair', N, japanese, english, between) for a Japanese twin with an English one right after it (only
    whitespace between, the same threshold); ('ja', N, japanese) and ('en', N, english) for a twin with no pair; and in
    a broken message (_spans) ('hidden', N, japanese, never) for each Japanese twin (with what an unclosed one took in,
    up to the next English twin of its threshold; never: it is paired, or left open, so it never shows), its English
    twins unwrapped and every </span> dropped; broken tags as _repair reads them. A twin span
    whose data-on isn't exactly -2..3 is raw markup, as written, its glosses read as any. Fenced code is raw; inline
    code is text (no markup)."""
    out = []
    for code, piece in _chunks(str(text)):
        if code:
            out.append(('raw', piece))
            continue
        p, saved = _code_out(piece)
        p = _repair(p)
        back = lambda t: _restore(t, saved)
        valid, broken = _spans(p)
        pairs = _pairs(p, valid)
        taken = {id(b) for b in pairs.values()}
        plain_ = (lambda t: SPAN_CLOSE.sub('', t)) if broken else (lambda t: t)
        at = 0
        for i, sp in enumerate(valid):
            if sp.start < at:
                continue  # inside a span already taken
            out += _abbrs(plain_(p[at:sp.start]), back)
            if broken:
                if sp.kind == 'ja':
                    nxt = next((b for b in valid[i + 1:] if b.kind == 'en' and b.on == sp.on), None)
                    end = sp.close_end if sp.close is not None else (nxt.start if nxt else len(p))
                    out.append(('hidden', sp.on, back(p[sp.open_end:end]), id(sp) in pairs or sp.close is None))
                    at = end
                else:
                    at = sp.open_end  # an English twin unwrapped: its content comes as the text after it
                continue
            if id(sp) in taken:
                continue
            if id(sp) in pairs:
                en = pairs[id(sp)]
                out.append(('pair', sp.on, back(sp.content), back(en.content), back(p[sp.close_end:en.start])))
                at = en.close_end
            else:
                out.append((sp.kind, sp.on, back(sp.content)))
                at = sp.close_end
        out += _abbrs(plain_(p[at:]), back)
    return out


def _abbrs(text, back=lambda t: t):
    out, at = [], 0
    for m in ABBR.finditer(text):
        if m.start() > at:
            out.append(('raw', back(text[at:m.start()])))
        out.append(('abbr', attrs(m.group(1)), back(m.group(2)), back(m.group())))
        at = m.end()
    if at < len(text):
        out.append(('raw', back(text[at:])))
    return out


def has_levels(text):
    """Whether a reply carries render-time levels (any data-on or data-jw, in any case, as HTML reads names)."""
    low = str(text).lower()
    return 'data-on' in low or 'data-jw' in low


def _threshold(a):
    """An abbr's threshold: its data-on when it is exactly -2..3, else None (rendered as written)."""
    value = a.get('data-on')
    return int(value) if value is not None and THRESHOLD.fullmatch(value) else None


def _word(t, level):
    """One glossed word at a level: one with a threshold shows from it (else its data-en, else its meaning, else as
    written); one with a data-on that isn't one, or with none, as written above off, and at off its meaning (a
    closing mark: nothing), as English only shows older messages."""
    a, inner, raw = t[1], t[2], t[3]
    on = _threshold(a)
    if on is not None:
        if level >= on:
            return f'<abbr title="{esc(a.get("title", ""))}">{inner}</abbr>'
        english = a.get('data-en') or meaning(a.get('title'))
        return esc_text(english) if english else raw
    if 'data-on' in a and level > OFF:
        return raw
    if level <= OFF:
        return '' if grammar.clean(inner).strip() in END_MARKS else esc_text(meaning(a.get('title')))
    return f'<abbr title="{esc(a.get("title", ""))}">{inner}</abbr>'


def esc_text(text):
    """Plain English put back into markdown: angle brackets as entities, so it can't open a tag."""
    return str(text).replace('<', '&lt;').replace('>', '&gt;')


def _inside(content, level, japanese):
    """A twin's content at a level: the Japanese one's glosses plain, the English one's words by their thresholds."""
    if japanese:
        return ''.join(x[1] if x[0] == 'raw' else f'<abbr title="{esc(x[1].get("title", ""))}">{x[2]}</abbr>'
                       for x in _abbrs(content))
    return ''.join(x[1] if x[0] == 'raw' else _word(x, level) for x in _abbrs(content))


def render(text, level):
    """The reply as the reader sees it at a level (-3..3), in the markup from before levels, by the rule the clients
    share: a pair (a Japanese twin right before an English one of the same threshold, only whitespace between) shows
    its Japanese from its threshold and its English below; an English twin with no pair always shows; a Japanese one
    with no pair shows from its threshold and below it is its last sentence mark's translation, or nothing; in a
    broken message (_spans) every pair is English; a data-on that isn't exactly -2..3 renders as written; markup in
    inline code is text. A reply without levels is unchanged above off; at off its glossed words become their meanings
    and its sentence marks go (English only)."""
    text = str(text)
    if not has_levels(text) and level > OFF:
        return text
    out = []
    for t in tokens(text):
        kind = t[0]
        if kind == 'raw':
            out.append(t[1])
        elif kind == 'abbr':
            out.append(_word(t, level))
        elif kind == 'pair':
            out.append(_inside(t[2], level, True) + t[4] if level > OFF and level >= t[1] else
                       t[4] + _inside(t[3], level, False))
        elif kind == 'en':
            out.append(_inside(t[2], level, False))
        elif kind == 'ja':
            out.append(_inside(t[2], level, True) if level > OFF and level >= t[1] else
                       esc_text(_last_translation(t[2])))
        elif kind == 'hidden':  # a broken message: a pair's or an open one's Japanese never, a lone one's as such
            if not t[3]:
                out.append(_inside(t[2], level, True) if level > OFF and level >= t[1] else
                           esc_text(_last_translation(t[2])))
    return ''.join(out)


def well_formed(text):
    """Whether every twin span is a pair with a threshold, in a message that isn't broken, and every swapped word's
    data-on is a threshold: what check-reply writes, and the only markup a sentence record is made for."""
    for code, piece in _chunks(str(text)):
        if code:
            continue
        p, _saved = _code_out(piece)
        if _repair(p) != p:
            return False
        valid, broken = _spans(p)
        jw = sum(1 for m in TWIN_OPEN.finditer(p) if attrs(m.group(1)).get('data-jw') in ('ja', 'en'))
        if broken or jw != len(valid) or 2 * len(_pairs(p, valid)) != len(valid):
            return False
        if any(('data-on' in attrs(m.group(1)) and _threshold(attrs(m.group(1))) is None)
               or not attrs(m.group(1)).get('title', '').strip() for m in ABBR.finditer(p)):
            return False
    return True


def elements(text):
    """(elements, owners) of well-formed final markup (well_formed). elements in document order: {kind: 's' (a twin)
    or 'w' (a swapped word with data-on outside a twin's Japanese), on: its threshold, parent: the twin whose English
    holds the word, or None}; owners: per glossed word of the reply in order (the Japanese of a twin before its
    English), the index of the element it belongs to, or -1 (a word without data-on, shown at every level above off).
    An abbr without data-on inside a twin's English shows while the English does: an element with threshold -2."""
    els, owners = [], []
    for t in tokens(text):
        if t[0] == 'abbr':
            on = _threshold(t[1])
            if on is not None:
                owners.append(len(els))
                els.append({'kind': 'w', 'on': on, 'parent': None})
            else:
                owners.append(-1)
        elif t[0] == 'pair':
            twin = len(els)
            els.append({'kind': 's', 'on': t[1], 'parent': None})
            owners += [twin] * sum(1 for _ in ABBR.finditer(t[2]))
            for m in ABBR.finditer(t[3]):
                on = _threshold(attrs(m.group(1)))
                owners.append(len(els))
                els.append({'kind': 'w', 'on': -2 if on is None else on, 'parent': twin})
    return els, owners


def visible(els, i, level):
    """Whether element i shows at a level: a twin from its threshold; a word from its threshold, and in a twin's
    English only below the twin's; i -1 (no element) above off."""
    if level <= OFF:
        return False
    if i < 0:
        return True
    e = els[i]
    if e['kind'] == 's':
        return level >= e['on']
    return level >= e['on'] and (e['parent'] is None or level < els[e['parent']]['on'])


def annotation_texts(text):
    """The inner texts of a reply's glossed words in order, every element's included, as the fingerprint takes them
    (what shown_texts would give with every span open); glosses in code are text."""
    out = []
    for t in tokens(text):
        if t[0] == 'abbr':
            out.append(grammar.clean(t[2]).strip())
        elif t[0] == 'pair':
            out += [grammar.clean(m.group(2)).strip() for m in ABBR.finditer(t[2])]
            out += [grammar.clean(m.group(2)).strip() for m in ABBR.finditer(t[3])]
    return out


def inert_code(text):
    """The text with markup inside code (inline, fenced or indented) made inert ('<' as an entity), as a client shows
    it: code, no gloss."""
    out = []
    for code, piece in _chunks(str(text)):
        out.append(piece.replace('<', '&lt;') if code else CODE_SPAN.sub(lambda m: m.group().replace('<', '&lt;'),
                                                                          piece))
    return ''.join(out)


def shown_texts(text, level):
    """The glossed words a client shows at a level, in order: the abbrs with a title of render's output, code inert
    (an abbr with no title is no gloss)."""
    return [inner for kind, _s, _e, inner, title in grammar.pieces(inert_code(render(text, level)))[1]
            if kind == 'w' and (title or '').strip()]


def fingerprints(text):
    """{level: the fingerprint of the reply's glossed words as shown at that level} for -2..3 (shown_texts), what T3
    reports from its screen."""
    return {level: grammar.fingerprint(shown_texts(text, level)) for level in SHOWING}


# ---- what Claude writes: English with <ja> twins ----

class Problem(Exception):
    pass


def _protect(line, saved):
    """The line with inline code, then glossed words, each one private-use character (no letter, no mark), so neither
    a <ja> in code nor a period in a gloss counts; restore puts them back."""
    def keep(m):
        saved.append(m.group())
        return chr(0xE000 + len(saved) - 1)
    return ABBR.sub(keep, CODE_SPAN.sub(keep, line))


def _restore(text, saved):
    while PROTECTED.search(text):
        text = PROTECTED.sub(lambda m: saved[ord(m.group()) - 0xE000], text)
    return text


SINGLE = re.compile(r'(?:^|[\s.(])[A-Za-z]\.$')  # U.S., a.m., e.g.: a single letter and a period ends no sentence


def _abbreviation(before):
    """Whether text ending with a period ends with an abbreviation of ABBREVIATIONS or a single letter's period (U.S.,
    a.m., plan A.): a period that may or may not end a sentence."""
    low = before.lower()
    listed = low.endswith(ABBREVIATIONS) and re.search(
        r'(^|[\s(])(' + '|'.join(map(re.escape, ABBREVIATIONS)) + r')$', low)
    return bool(listed or SINGLE.search(before))


# An abbreviation or a single letter's period, then a space and a capital: "the U.S. The cache", "plan A. It is",
# "Dr. Smith". Whether a sentence ends there can't be told, so a twin's English with one keeps its sentence English.
AMBIGUOUS = re.compile(r'(?:(?<![A-Za-z])[A-Za-z]|(?<![A-Za-z])(?i:' + '|'.join(
    re.escape(a[:-1]) for a in ABBREVIATIONS) + r'))\.\s+(?=[A-Z])')


def boundaries(s, start=0, end=None):
    """The sentence ends (BOUNDARY matches) in s[start:end], but for the abbreviations in ABBREVIATIONS and a period
    after a single letter (U.S., a.m.)."""
    end = len(s) if end is None else end
    for m in BOUNDARY.finditer(s, start, end):
        if m.group().startswith('.') and _abbreviation(s[start:m.start() + 1]):
            continue
        yield m


def _sentence_start(s, start, end):
    """Where the English sentence ending at `end` begins, searching s[start:end]: after the last sentence end there
    (boundaries), else at start; leading spaces skipped."""
    begin = start
    for m in boundaries(s, start, end):
        begin = m.end()
    while begin < end and s[begin].isspace():
        begin += 1
    return begin


def _open_marks(text):
    """The markdown text leaves open, on a line with its code and glosses as placeholders: bold (**), italic (* or _
    at a word's edge), a link's [ or (, or a backtick; '' when none."""
    single = re.sub(r'\*\*', '', text)
    found = [mark for mark, n in (('**', text.count('**')),
                                  ('*', len(re.findall(r'\*', re.sub(r'(?<=\s)\*(?=\s)', '', single)))),
                                  ('_', len(re.findall(r'(?<!\w)_|_(?!\w)', text))), ('`', text.count('`')))
             if n % 2]
    found += [o for o, c in (('[', ']'), ('(', ')')) if text.count(o) != text.count(c)]
    return ' '.join(found)


def twin_trouble(s, begin, english, japanese):
    """Why a twin can't stand in for its English, or None (s: its line with code and glosses as placeholders; begin:
    where its English starts): its English is more than one sentence (abbreviations aside), or has an abbreviation or
    a single letter's period before a capital (AMBIGUOUS: where it starts can't be told), sits in a table row or a
    task list, holds a link, leaves markdown open (bold, italic, a link, code) or starts inside markdown the line left open before it, or its Japanese
    leaves markdown open. Such a twin would cut through the reader's markdown, or hide a sentence with its own."""
    if AMBIGUOUS.search(english):
        return ('its English has an abbreviation or a single letter with a period before a capital (as in "the U.S. '
                'The" or "Dr. Smith"), so where its sentence starts is unclear')
    ends = [m for m in SENTENCE_END.finditer(english + ' ')
            if not (m.group().startswith('.') and _abbreviation(english[:m.start() + 1]))]
    if len(ends) > 1:
        return 'its English holds more than one sentence'
    if s.lstrip().startswith('|'):
        return 'it is in a table'
    if TASK.match(english):
        return 'it is in a task list'
    if LINK.search(english):
        return 'its English holds a link, which the twin would hide'
    for where, text in (('its English', english), ('the line before its English', s[:begin]),
                        ('its Japanese', japanese)):
        left = _open_marks(text)
        if left:
            return f'{where} leaves markdown open ({left})'
    return None


def parse_authored(text):
    """What Claude wrote, as lines of segments: [[('raw', text) | ('twin', english, japanese, why)]] with each line's
    number, and what it found: (lines, problems, notes). why: twin_trouble's reason to keep the sentence English, or
    None. problems stop the check (a <ja> without its </ja> or its
    English sentence, a Japanese sentence outside <ja>); notes are what code fixed or kept ({{...}} marks the writer
    left, put back in English). Fenced code is raw, as is inline code."""
    out, problems, notes, fence = [], [], [], False
    for number, line in enumerate(str(text).split('\n'), 1):
        if FENCE_LINE.match(line):
            fence = not fence
            out.append((number, [('raw', line)]))
            continue
        if fence:
            out.append((number, [('raw', line)]))
            continue
        saved = []
        s = _protect(line, saved)
        left = UNSURE.findall(_restore(s, saved))
        if left:
            line = UNSURE.sub(lambda m: m.group(1), _restore(s, saved))
            notes.append((number, f'{len(left)} unsure swap{"s" if len(left) > 1 else ""} left as {{{{English→...}}}}: '
                                  'put back in English'))
            saved = []
            s = _protect(line, saved)
        segments, at, open_at, prefix = [], 0, None, PREFIX.match(s).end()
        for m in JA_TAG.finditer(s):
            if not m.group(1):  # <ja>
                if open_at is not None:
                    problems.append((number, 'a <ja> inside another <ja>'))
                    break
                open_at = m
                continue
            if open_at is None:
                problems.append((number, 'a </ja> with no <ja> before it'))
                break
            end = open_at.start()
            while end > at and s[end - 1].isspace():
                end -= 1
            begin = _sentence_start(s, max(at, prefix), end)
            english = s[begin:end]
            if not LETTER.search(english):
                problems.append((number, 'a <ja> twin with no English sentence right before it on its line'))
            else:
                if begin > at:
                    segments.append(('raw', _restore(s[at:begin], saved)))
                japanese = s[open_at.end():m.start()]
                segments.append(('twin', _restore(english, saved), _restore(japanese, saved).strip(),
                                 twin_trouble(s, begin, english, japanese)))
            at, open_at = m.end(), None
        if open_at is not None:
            problems.append((number, 'a <ja> with no </ja> on the same line'))
        if at < len(s):
            segments.append(('raw', _restore(s[at:], saved)))
        out.append((number, segments))
    return out, problems, notes


# ---- the reply's parts, from what Claude wrote ----

def _english(text, number, sid, parent):
    """English text (with the weave's swaps) as parts: ('raw', text) and words {k: 'w', title, ja, en?, line, sid,
    twin}; sid counts the line's sentences (a list, shared along the line), its ends found with code and glosses as
    placeholders, so a sentence that ends right after a gloss ends there."""
    out, saved = [], []
    s = CODE_SPAN.sub(lambda m: saved.append(m.group()) or chr(0xE000 + len(saved) - 1), text)
    found = list(ABBR.finditer(s))
    flat = ABBR.sub('\ue000', s)  # each gloss one placeholder: where the sentence ends fall, gloss by gloss
    shift, ends, starts = 0, [m.end() for m in boundaries(flat)], []
    for m in found:
        starts.append(m.start() - shift)
        shift += len(m.group()) - 1
    at = 0
    for m, place in zip(found, starts):
        before = s[at:m.start()]
        if before:
            out.append(('raw', _restore(before, saved)))
        a = attrs(m.group(1))
        n = sid[0] + sum(1 for e in ends if e <= place)
        out.append({'k': 'w', 'title': a.get('title', ''), 'ja': m.group(2), 'en': a.get('data-en'), 'line': number,
                    'sid': ('twin', parent) if parent is not None else (number, n), 'twin': parent})
        at = m.end()
    if at < len(s):
        out.append(('raw', _restore(s[at:], saved)))
    sid[0] += len(ends)
    return out


def _fence_lines(lines):
    """The line numbers inside fenced code (their raw segments are code, not English)."""
    inside, fence = set(), False
    for number, segments in lines:
        raw = segments[0][1] if len(segments) == 1 and segments[0][0] == 'raw' else None
        if raw is not None and FENCE_LINE.match(raw):
            fence = not fence
            inside.add(number)
        elif fence:
            inside.add(number)
    return inside


def reply_parts(lines):
    """The reply as parts in order: ('raw', text), ('code', a line of fenced code), words (_english) and twins {k: 't',
    english: [parts], japanese, line, source: the English as written}; a newline is raw. A twin's words name it by its
    place among the twins."""
    code = _fence_lines(lines)
    out, twins = [], 0
    for k, (number, segments) in enumerate(lines):
        if k:
            out.append(('raw', '\n'))
        if number in code:
            out += [('code', s[1]) for s in segments]
            continue
        sid = [0]
        for seg in segments:
            if seg[0] == 'raw':
                out += _english(seg[1], number, sid, None)
            else:
                out.append({'k': 't', 'english': _english(seg[1], number, [0], twins), 'japanese': seg[2],
                            'line': number, 'source': seg[1], 'why': seg[3] if len(seg) > 3 else None})
                sid[0] += 1
                twins += 1
    return out


def japanese_twin(japanese, english):
    """A twin's Japanese with its closing marks glossed: each bare 。 or ？ takes the English sentence (plain) as its
    title, a bare ！ becomes 。, and a twin that doesn't end with a mark gets one (？ when the English asks)."""
    title = esc(english)
    pieces, at = [], 0
    for m in ABBR.finditer(japanese):
        pieces.append(('t', japanese[at:m.start()]))
        pieces.append(('w', m.group()))
        at = m.end()
    pieces.append(('t', japanese[at:]))
    out = []
    for kind, piece in pieces:
        if kind == 'w':
            out.append(piece)
            continue
        out.append(re.sub('[。？！]', lambda m: f'<abbr title="{title}">{"。" if m.group() == "！" else m.group()}</abbr>',
                          piece))
    text = ''.join(out).strip()
    last = re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', text))[-1:]
    if last not in MARKS:
        text += f'<abbr title="{title}">{"？" if english.rstrip(" *_").endswith("?") else "。"}</abbr>'
    return text


def word_markup(w, on):
    return (f'<abbr title="{esc(w["title"])}" data-en="{esc(w["en"])}" data-on="{on}">{w["ja"]}</abbr>')


# ---- thresholds and propensities ----

class Element:
    """One thing a level can show: kind 's' (a twin) or 'w' (a swapped word). cost_s: what it spends of the
    sentences' part; cost_w: of the words' part (a word's lookup chance unless it is free; a twin's costly words);
    opens: its expected opens if read; value: what showing it teaches; parent: the twin (an index) whose English
    holds the word; sid: the English sentence a word is in; fixed: a threshold it takes whatever the order (free
    words -2, dictionary words and a learning word's later uses FULL); children: a twin's words."""
    __slots__ = ('kind', 'cost_s', 'cost_w', 'opens', 'value', 'parent', 'sid', 'fixed', 'children', 'ja')

    def __init__(self, kind, cost_s=0.0, cost_w=0.0, opens=0.0, value=0.0, parent=None, sid=None, fixed=None,
                 ja=None):
        self.kind, self.cost_s, self.cost_w, self.opens, self.value = kind, cost_s, cost_w, opens, value
        self.parent, self.sid, self.fixed, self.children, self.ja = parent, sid, fixed, [], ja

    def density(self):
        cost = self.cost_s if self.kind == 's' else self.cost_w
        return self.value / cost if cost > 0 else math.inf


def learning_value(p):
    """A word's learning value on a positive scale: 1 at TARGET_P, e^-1 a unit of log odds away (knowledge.learning_
    value, exponentiated)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.exp(-abs(math.log(p / (1 - p)) - math.log(TARGET_P / (1 - TARGET_P))))


def link(els):
    """Each twin's children (the words its English holds)."""
    for e in els:
        e.children = []
    for i, e in enumerate(els):
        if e.kind == 'w' and e.parent is not None:
            els[e.parent].children.append(i)
    return els


def _order(els, kind, on, rng, explore, explored):
    """The elements of a kind still to place, best first: by density (ties in document order), each place drawn at
    random instead with chance explore."""
    idx = [i for i, e in enumerate(els) if e.kind == kind and on[i] is None]
    ranked = sorted(idx, key=lambda i: (-els[i].density(), i))
    key = {}
    for r, i in enumerate(ranked):
        if rng.random() < explore:
            key[i] = rng.random()
            explored[i] = True
        else:
            key[i] = (r + 0.5) / len(ranked)
    return sorted(idx, key=lambda i: key[i])


def assign(els, budgets, rng, explore=EXPLORE, per_sentence=intensity.per_sentence):
    """(thresholds, explored) for the elements (Element, linked) under one draw of the randomization (module
    docstring). budgets: {level: (words' part, sentences' part)} in lookups for each budgeted level. An element is
    taken at a level when what its part has spent there plus u times its cost is within the part, u one uniform draw
    per reply and part: so the element at the edge gets in with the chance (what is left) / (its cost), the same draw
    at every level keeps the sets nested, and a level's expected spend is its part (systematic rounding). A twin's
    costly words are held to the words' part the same way, with that part's draw. So an element can show at any level
    where what came before it leaves its part unspent: the randomization never rules it out. What still depends on the
    model's predictions is how likely it is: its cost (its predicted chances), and for the elements not explored, its
    place by value per expected open; the propensities say by how much."""
    n = len(els)
    on = [e.fixed for e in els]
    explored = [False] * n
    twins = _order(els, 's', on, rng, explore, explored)
    words = _order(els, 'w', on, rng, explore, explored)
    u_s, u_w = rng.random(), rng.random()
    shown = lambda i, level: on[i] is not None and on[i] <= level and (
        els[i].parent is None or on[els[i].parent] is None or level < on[els[i].parent])
    for level in BUDGETED:
        words_part, sentences_part = budgets[level]
        spent_s = sum(e.cost_s for i, e in enumerate(els) if e.kind == 's' and on[i] is not None and on[i] <= level)
        spent_w = sum(e.cost_w for i, e in enumerate(els) if shown(i, level))
        for i in twins:
            if on[i] is not None:
                continue
            e = els[i]
            freed = sum(els[c].cost_w for c in e.children if shown(c, level))
            if spent_s + u_s * e.cost_s <= sentences_part + 1e-12 and \
                    (e.cost_w <= freed or spent_w + u_w * (e.cost_w - freed) <= words_part + 1e-12):
                on[i] = level
                spent_s += e.cost_s
                spent_w += e.cost_w - freed
        counts = {}
        for i, e in enumerate(els):
            if e.kind == 'w' and e.cost_w > 0 and shown(i, level):
                counts[e.sid] = counts.get(e.sid, 0) + 1
        most = per_sentence(level)
        hidden = lambda i: els[i].parent is not None and on[els[i].parent] is not None and on[els[i].parent] <= level
        for i in words:
            if on[i] is not None or hidden(i) or counts.get(els[i].sid, 0) >= most:
                continue
            if spent_w + u_w * els[i].cost_w <= words_part + 1e-12:
                on[i] = level
                spent_w += els[i].cost_w
                counts[els[i].sid] = counts.get(els[i].sid, 0) + 1
    on = [FULL if x is None else x for x in on]
    for i, e in enumerate(els):  # a word in a twin's English: never above the twin (it shows only below it)
        if e.kind == 'w' and e.parent is not None:
            on[i] = min(on[i], on[e.parent])
    return on, explored


def shown_at(els, on, i, level):
    """Whether element i shows at a level under thresholds on."""
    if level <= OFF:
        return False
    if on[i] > level:
        return False
    parent = els[i].parent
    return parent is None or level < on[parent]


def propensities(els, budgets, draws=DRAWS, seed=0, explore=EXPLORE):
    """[[chance element i shows at each budgeted level]]: the share of `draws` runs of assign in which it does (module
    docstring), seeded."""
    rng = random.Random(seed)
    counts = [[0] * len(BUDGETED) for _ in els]
    for _ in range(draws):
        on, _x = assign(els, budgets, rng, explore)
        for i in range(len(els)):
            for k, level in enumerate(BUDGETED):
                if shown_at(els, on, i, level):
                    counts[i][k] += 1
    return [[round(c / draws, 3) for c in row] for row in counts]


def budgets_for(words, normal=None):
    """{level: (words' part, sentences' part)} in lookups for a reply of `words` English words: the level's budget per
    100 words (intensity.budget, normal's the challenge when set) times words / 100, and SENTENCE_SHARE of it."""
    out = {}
    for level in BUDGETED:
        b = intensity.budget(level, normal) * words / 100
        out[level] = (b, intensity.SENTENCE_SHARE * b)
    return out


# ---- local memory: the weave's swaps, each checked reply's plan ----

def session_key(session=None):
    """The chat's session id (Claude Code puts it in CLAUDE_CODE_SESSION_ID for the commands Claude runs), or ''."""
    import os
    value = session if session is not None else os.environ.get('CLAUDE_CODE_SESSION_ID', '')
    return str(value or '')[:100]


def remember_swaps(swaps, level, session=None, now_ms=None):
    """Keeps the weave's swaps for the check step ({ja, title, en, kana, p, free, dictionary}), per session, the last
    SESSIONS_KEPT sessions of the last KEEP_MS."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    path = fwlib.home() / SWAPS
    kept = fwlib.read_json(path, {})
    kept = {k: v for k, v in (kept if isinstance(kept, dict) else {}).items()
            if isinstance(v, dict) and now_ms - (v.get('at') or 0) < KEEP_MS}
    kept[session_key(session)] = {'at': now_ms, 'level': level, 'swaps': swaps}
    kept = dict(sorted(kept.items(), key=lambda kv: kv[1]['at'])[-SESSIONS_KEPT:])
    try:
        fwlib.write_json(path, kept)
    except OSError:
        pass


def swaps_for(session=None):
    """The weave's last swaps in this session (remember_swaps), else the latest of any session; [] when none."""
    kept = fwlib.read_json(fwlib.home() / SWAPS, {})
    if not isinstance(kept, dict) or not kept:
        return []
    found = kept.get(session_key(session))
    if not isinstance(found, dict):
        found = max((v for v in kept.values() if isinstance(v, dict)), key=lambda v: v.get('at') or 0, default={})
    swaps = found.get('swaps')
    return [s for s in swaps if isinstance(s, dict)] if isinstance(swaps, list) else []


def remember_plan(fp, plan, now_ms=None):
    """Keeps a checked reply's plan under its fingerprint (grammar.fingerprint of annotation_texts): {at, level (the
    chat level it was checked at), view (the level it is read at: the chat level, English only aside), e: [[kind,
    on, propensities, p, x]] per element in document order, d: [[pattern ids]] per dropped twin, s: [p] per Japanese
    sentence}, the last PLANS_KEPT."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    path = fwlib.home() / PLANS
    kept = fwlib.read_json(path, {})
    kept = kept if isinstance(kept, dict) else {}
    kept[fp] = dict(plan, at=now_ms)
    kept = dict(sorted(kept.items(), key=lambda kv: (kv[1] or {}).get('at') or 0)[-PLANS_KEPT:])
    try:
        fwlib.write_json(path, kept)
    except OSError:
        pass


_plans = (None, None)  # (the plans file and its modification time, its contents): a scan reads it once


def plans_now():
    """reply-plans.json's contents, read again only when the file changed."""
    global _plans
    path = fwlib.home() / PLANS
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return {}
    if _plans[0] != key:
        _plans = (key, fwlib.read_json(path, {}))
    return _plans[1]


def plan_for(text, plans=None):
    """The plan check-reply kept for this reply (remember_plan), or None."""
    plans = plans if plans is not None else plans_now()
    if not isinstance(plans, dict) or not has_levels(text):
        return None
    found = plans.get(grammar.fingerprint(annotation_texts(text)))
    return found if isinstance(found, dict) else None


def level_now(state=None):
    """The chat level on this machine's state: the level a reply is read at once any English-only spell is over (a
    reply written during one shows its Japanese then)."""
    state = state if state is not None else fwlib.load_state()
    return intensity.current((state or {}).get('prefs'), 'chat')['level']


def view(text, level=None, plans=None):
    """What a reader of the transcript counts as shown (fwlib.read_replies, the hook's last reply and topical words):
    the reply rendered at the level it was shown at when sent (its plan's view), else at `level` (the chat level in
    force now). A plan from a check under English only (view -3, from before views were the chat level) counts at
    `level` instead: the reply shows its Japanese once English only is off. A reply without levels is unchanged above
    off. Only rendered Japanese is an exposure."""
    if not has_levels(text):
        return text if level is None or level > OFF else render(text, level)
    plan = plan_for(text, plans)
    shown = plan.get('view') if plan and isinstance(plan.get('view'), int) else None
    if shown is None or shown <= OFF:
        shown = level
    return render(text, 0 if shown is None else shown)


def seed_of(text):
    """A seed from a reply's text, so its propensities repeat."""
    return int(hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:8], 16)
