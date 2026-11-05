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
Code (fenced and inline) is skipped.

With a plan (Plan: the reader's grammar statuses, the sentence model and the level), it also scores each Japanese or
mixed sentence (score): its Japanese characters, its grammar patterns (grammar.py), the patterns not allowed (new ones,
or a second learning one) and its predicted open chance (sentence_model.predict); it flags one longer than the weave's
limit, using patterns not allowed, or more likely to be opened than sentence_model.TARGET, and ends with the reply's
expected sentence opens against the level's budget for sentences. Python 3.9+ standard library only."""
import functools
import re
from pathlib import Path

import grammar
import intensity
import sentence_model

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


def sentence_problems(sent, types, english=False):
    """[(what is wrong, the index of the glossed word it is about, or None)] for one sentence (a token list from
    sentences). english: the sentence is English whatever its share of Japanese (an English sentence of a reply with
    twins, twins.py), so its words are held to the stays-English rules and it needs no translation."""
    found = []
    add = lambda what, i=None: found.append((what, i))
    words = [t for t in sent if t[0] == 'w']
    bare = ''.join(t[1] for t in sent if t[0] == 't')
    english_words = len(LATIN_WORD.findall(bare))
    japanese = len(words) + sum(1 for t in sent if t[0] == 't' and JAPANESE.search(t[1]))
    translated = any(t[0] == 'm' and LATIN_WORD.search(t[2]) for t in sent)
    stays_english = english or (english_words > japanese and not translated)
    if not english and japanese >= 3 and japanese >= english_words and not translated:
        add('a sentence that is half Japanese or more has no translation on its closing mark')
    at = next((i for i, t in enumerate(sent) if (t[0] == 'w' and t[1] == 'もし') or
               (t[0] == 't' and re.search('(?<![぀-ヿ])もし', t[1]))), None)
    if at is not None and not CONDITIONAL.search(''.join(t[1] for t in sent[at + 1:] if t[0] in 'wt')):
        add('もし with no conditional ending (なら, たら, ば) after it: write the condition in Japanese or keep '
            '"if" in English', at if sent[at][0] == 'w' else None)
    marked = ''.join(t[1] if t[0] == 't' else f'<w>{t[1]}' for t in sent if t[0] != 'm')
    for m in SURU.finditer(marked.replace('<w>', '')):
        add(f'"{m.group(1)} {m.group(2)}": する on an English word. Use a Japanese verb from the weave\'s '
            'output, or keep that clause English')
    after_an = next((i + 1 for i, t in enumerate(sent[:-1]) if t[0] == 't' and sent[i + 1][0] == 'w'
                     and re.search(r'\ban\s+$', t[1], re.I)), None)
    if after_an is not None:
        add('"an" before a Japanese word reads wrong: use "a", or drop it', after_an)
    elif not stays_english and japanese >= 3 and ARTICLE.search(bare):
        add('an English article inside a sentence that is half Japanese or more: drop it')
    first = next((i for i, t in enumerate(sent) if not (t[0] == 't' and not t[1].strip(' *-#>'))), None)
    if first is not None and sent[first][0] == 'w' and sent[first][1] == 'そして':
        add('そして opening a sentence reads as the learner\'s "And...": use また or それに, join the clauses with '
            'the て-form, or drop it', first)
    for i, t in enumerate(sent):
        if t[0] == 't' and i and sent[i - 1][0] == 'w' and re.match('の[はがをも]', t[1]):
            pos = types.get(sent[i - 1][1]) or set()
            verbal = not pos or any(VERB_OR_ADJECTIVE.match(p) for p in pos)
            if verbal and not any(p in NOUN_TYPES for p in pos):
                add(f'the nominalising の after {sent[i - 1][1]} is unglossed: wrap it with the verb or '
                    'adjective', i - 1)
    for i, t in enumerate(sent):
        if t[0] != 'w':
            continue
        ja = t[1]
        next_text = sent[i + 1][1] if i + 1 < len(sent) and sent[i + 1][0] == 't' else ''
        prev_text = sent[i - 1][1] if i and sent[i - 1][0] == 't' else ''
        if ja in PRONOUNS:
            add(f'the pronoun {ja}: Japanese leaves it out, so drop it', i)
        elif stays_english and ja in ENGLISH_ONLY_NOUNS:
            add(f'{ja} as an English pronoun in a sentence that stays English', i)
        if ja in AFTER_NOUN and re.match(r'\s*[A-Za-z]', next_text):
            add(f'{ja} follows its noun in Japanese, but here it stands before English like a preposition', i)
        if re.search(r'\d\s*$', prev_text) or re.match(r'\s*\d', next_text):
            add(f'{ja} sits right next to a number', i)
        if stays_english:
            pos = types.get(ja)
            attributive = bool(re.match(r'\s*[A-Za-z]', next_text))
            if pos is None:
                add(f'{ja} is a form the dictionary does not list (a conjugated verb or phrase) inside a '
                    'sentence that stays English', i)
            elif not any(p in NOUN_TYPES for p in pos) and not (
                    attributive and any(p in ADJECTIVE_TYPES for p in pos)):
                kind = '/'.join(sorted(pos)) or 'not a noun'
                add(f'{ja} ({kind}) in a sentence that stays English, where only nouns, and adjectives right '
                    'before a noun, go', i)
    for t in sent:
        if t[0] == 't':
            for run in RUN.findall(t[1]):
                if ungrammatical(run):
                    add(f'Japanese outside the <abbr> tags: {run}')
    return found


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
            show = re.sub(r'\s+', ' ', ''.join(t[1] for t in sent)).strip()[:90]
            found += [(number, what, show) for what, _i in sentence_problems(sent, types)]
    counts = {}
    for m in ABBR.finditer(re.sub(r'```.*?```', '', text, flags=re.S)):
        if m.group(2) not in MARKS and KANJI.search(m.group(2)):  # content words: grammar repeats by nature
            counts[m.group(2)] = counts.get(m.group(2), 0) + 1
    for ja, n in sorted(counts.items(), key=lambda x: -x[1]):
        if n >= REPEAT:
            found.append((0, f'{ja} is glossed {n} times in this reply: vary it, or keep some of them English', ''))
    return found


class Plan:
    """What the sentence scorer and the levels need: the reader's pattern statuses (grammar.statuses), the sentence
    model (knowledge.json "grammar", or None), the chat level with normal's budget (the challenge, or None), and for
    the levels (finish): knowledge.json (know, for a word the weave didn't offer), the weave's swaps (twins.swaps_for),
    the price of a sentence, this week's learning pattern (its id, or None), the level the reply is read at (view:
    the chat level, also while English only is on, since the reply shows its Japanese once that is off), the weave's
    word list and recency (words, recency; words None: the twins' words the weave didn't offer go unpriced)."""

    def __init__(self, statuses, model=None, level=0, normal=None, know=None, swaps=(), price=None, learning=None,
                 view=None, words=None, recency=None):
        self.statuses, self.model, self.level, self.normal = statuses, model, level, normal
        self.ready = bool(model and model.get('ready'))
        self.limit = sentence_model.max_chars(model)
        self.know, self.swaps, self.learning = know or {}, list(swaps or ()), learning
        self.price = price if price is not None else intensity.sentence_price(know)
        self.view = level if view is None else view
        self.words = words  # the weave's word list (chatweave.words_for), for the twins' words it didn't offer
        self.recency = recency  # and the fit's recency, as the weave has it (fw_host.adaptive)

    @classmethod
    def load(cls, level=None, session=None):
        """From this machine's state and knowledge.json (the cache, with a collector), at the chat level in effect
        unless one is given."""
        import fwlib
        import knowledge
        import twins
        state, know = fwlib.load_state(), knowledge.load()
        level = intensity.current(state.get('prefs'), 'chat')['level'] if level is None else level
        model = know.get('grammar') if isinstance(know.get('grammar'), dict) else None
        learning = grammar.learning(state)[0]
        try:  # the words the weave weaves from, with their estimates, as it prices them
            import chatweave
            words, adaptive = chatweave.words_for(state)
            recency = adaptive.get('recency')
        except Exception:
            words, recency = fwlib.public_state(state)['words'], None
        return cls(grammar.statuses(state), model, level, knowledge.challenge_of(state) or None, know,
                   twins.swaps_for(session), intensity.sentence_price(know), learning.id if learning else None, level,
                   words, recency)


def score(text, plan):
    """[{line, text, chars, ids, new, learning, p, flags}] for each Japanese or mixed sentence of a reply: one whose
    closing mark is glossed, or half Japanese or more. flags: what to fix, [] when nothing."""
    out, fence = [], False
    for number, raw in enumerate(text.split('\n'), 1):
        if re.match(r'\s*(```|~~~)', raw):
            fence = not fence
            continue
        if fence:
            continue
        line = re.sub(r'`[^`]*`', ' CODE ', raw)
        for sent in sentences(line):
            words = [t for t in sent if t[0] == 'w']
            bare = ''.join(t[1] for t in sent if t[0] == 't')
            japanese = len(words) + sum(1 for t in sent if t[0] == 't' and JAPANESE.search(t[1]))
            marked = any(t[0] == 'm' for t in sent)
            if not marked and not (japanese >= 3 and japanese >= len(LATIN_WORD.findall(bare))):
                continue
            segments = [(t[0], t[1]) for t in sent if t[0] in 'wt']
            body = ''.join(t[1] for t in segments)
            chars = len(grammar.JAPANESE.findall(body))
            ids = grammar.scan_ids(segments)
            new, learning = sentence_model.allowed(ids, plan.statuses)
            p = sentence_model.predict(plan.model, chars, ids, plan.statuses)
            flags = []
            if chars > plan.limit:
                flags.append(f'too long: {chars} Japanese characters, the limit is {plan.limit}')
            if new:
                flags.append('grammar not on the list: ' + ', '.join(grammar.BY_ID[i].name for i in new))
            if len(learning) > 1:
                flags.append('more than one learning pattern: ' + ', '.join(grammar.BY_ID[i].name for i in learning))
            if plan.ready and p > sentence_model.TARGET:
                flags.append(f'likely to be opened: {p:.0%}, over the {sentence_model.TARGET:.0%} aimed at')
            show = re.sub(r'\s+', ' ', ''.join(t[1] for t in sent)).strip()
            out.append({'line': number, 'text': show[:90], 'chars': chars, 'ids': ids, 'new': new,
                        'learning': learning, 'p': p, 'flags': flags})
    return out


def words_of(text):
    """About how many words the reply's English draft had: its English words and its glossed words (sentence marks
    left out), code left out."""
    prose = re.sub(r'```.*?```', ' ', text, flags=re.S)
    prose = re.sub(r'`[^`]*`', ' ', prose)
    glossed = sum(1 for m in ABBR.finditer(prose) if m.group(2) not in MARKS)
    return len(LATIN_WORD.findall(ABBR.sub(' ', prose))) + glossed


def sentence_budget(text, plan):
    """The lookups the level lets the reply's sentences spend: its budget per 100 words times the reply's words, times
    intensity.SENTENCE_SHARE; None at full (no budget)."""
    budget = intensity.budget(plan.level, plan.normal)
    if budget is None:
        return None
    return budget * words_of(text) / 100 * intensity.SENTENCE_SHARE


def sentence_lines(text, plan):
    """The scorer's part of the report: one line per sentence, and the expected opens against the budget."""
    scored = score(text, plan)
    if not scored:
        return [], 0
    names = lambda ids: ', '.join(grammar.BY_ID[i].name for i in ids) or 'no patterns'
    lines = [f'Japanese and mixed sentences (Japanese characters, the limit {plan.limit}; grammar; chance the user '
             f'opens the translation{"" if plan.ready else ", from the prior until the model is ready"}):']
    flagged = 0
    for s in scored:
        head = f'- line {s["line"]}: "{s["text"]}" {s["chars"]} characters; {names(s["ids"])}; {s["p"]:.1%}'
        if s['flags']:
            flagged += 1
            head += ('. FIX (split it, simplify it, or leave it English): ' + '; '.join(s['flags']))
        lines.append(head)
    expected = sum(s['p'] for s in scored)
    budget = sentence_budget(text, plan)
    level = intensity.NAMES.get(plan.level, 'normal')
    if budget is None:
        lines.append(f'Expected sentence opens: {expected:.2f} for {len(scored)} sentences (no budget at {level}).')
    else:
        verdict = ('over the budget: fewer or easier Japanese sentences' if expected > budget * 1.25 else
                   'under the budget' if expected < budget * 0.5 else 'about the budget')
        lines.append(f'Expected sentence opens: {expected:.2f} for {len(scored)} sentences, against a budget of '
                     f'{budget:.2f} at {level}: {verdict}.')
    return lines, flagged


def report(text, types=None, plan=None):
    """What fw.py check-reply prints: the problems, then with a plan the sentences scored (sentence_lines)."""
    found = problems(text, types)
    extra, flagged = sentence_lines(text, plan) if plan is not None else ([], 0)
    if not found and not flagged:
        return '\n'.join(['jw-check: no problems found.'] + extra)
    lines = [f'jw-check: {len(found) + flagged} things to fix (fix each, or keep it only where the check is plainly '
             'wrong):']
    lines += [f'- line {number}: {what}. In: "{where}"' if number else f'- {what}.' for number, what, where in found]
    return '\n'.join(lines + extra)


# ---- replies with levels (twins.py): English with <ja> twins in, the final markup out ----

SEPARATOR = '====='
LEVEL_NAMES = {-2: '-2 much lighter', -1: '-1 lighter', 0: ' 0 normal', 1: '+1 heavier', 2: '+2 much heavier',
               3: '+3 full'}


class Checked:
    """What finish found: the final markup (None when there is something to fix first), the lines it prints, and for
    tests the elements with their thresholds, propensities and explored flags."""

    def __init__(self, text, final=None, elements=(), on=(), props=(), explored=(), summary=None):
        self.text, self.final, self.elements, self.on = text, final, list(elements), list(on)
        self.props, self.explored, self.summary = list(props), list(explored), summary or {}


def _walk(parts, line=1):
    """(line, part, twin or None) for every part in order, a twin's English parts after it with the twin."""
    for p in parts:
        if isinstance(p, tuple) and p[1] == '\n':
            line += 1
        yield line, p, None
        if isinstance(p, dict) and p['k'] == 't':
            for q in p['english']:
                yield line, q, p


def _word_info(w, plan, now_ms, swap=None):
    """A word's English original, lookup chance, whether it is known at a glance and whether it is a dictionary word
    the reader doesn't have: from its swap in the weave (match_swaps), its English the swap's when the match is sure,
    else its gloss's meaning; without one, from knowledge.json, else the neutral prior."""
    import fwlib
    import knowledge
    import twins
    same = swap or next((s for s in plan.swaps if s.get('ja') == w['ja']), None)
    en = w.get('en') or (swap or {}).get('en') or twins.meaning(w['title'])
    if same and isinstance(same.get('p'), (int, float)):
        return en, float(same['p']), bool(same.get('free')), bool(same.get('dictionary'))
    estimate = knowledge.word_estimate(plan.know, w['ja'], 't3', now_ms)
    if estimate:
        return en, estimate[0], estimate[0] < fwlib.FREE_P, False
    return en, twins.PRIOR_P, False, False


CONTEXT_WORDS = 6
WORD = re.compile(r"[a-z0-9']+")


def _context_words(text, last):
    """The words of English text, lowercased: its last CONTEXT_WORDS (last) or its first."""
    found = WORD.findall(str(text or '').lower())
    return found[-CONTEXT_WORDS:] if last else found[:CONTEXT_WORDS]


def reply_contexts(parts):
    """{id of a word part: (words before it, words after it)} in the reply's English as the reader reads it in English
    (twins' English, code out), other swaps skipped: what match_swaps compares with the draft's English around each
    swap."""
    stream = []  # words, and word parts
    def add(q):
        if isinstance(q, tuple):
            if q[0] == 'raw':
                stream.extend(WORD.findall(re.sub(r'`[^`]*`', ' ', q[1]).lower()))
        elif q['k'] == 'w':
            stream.append(q)
    for p in parts:
        if isinstance(p, dict) and p['k'] == 't':
            for q in p['english']:
                add(q)
        else:
            add(p)
    out = {}
    for i, q in enumerate(stream):
        if isinstance(q, dict):
            before = [x for x in stream[:i] if isinstance(x, str)][-CONTEXT_WORDS:]
            after = [x for x in stream[i + 1:] if isinstance(x, str)][:CONTEXT_WORDS]
            out[id(q)] = (before, after)
    return out


def match_swaps(words, contexts, swaps):
    """{id of a word part: (its swap in the weave, sure)}: each kept swap matched to the draft's swap of the same word
    and gloss whose English around it overlaps most with the reply's (contexts: reply_contexts), each draft swap used
    once, so a dropped earlier use doesn't shift the later ones. Sure: one candidate, or all with one English, or the
    best overlap above every candidate with another English; else the English is the gloss's meaning."""
    out, used = {}, set()
    score = lambda w, s: (len(set(contexts.get(id(w), ([], []))[0]) & set(_context_words(s.get('before'), True)))
                          + len(set(contexts.get(id(w), ([], []))[1]) & set(_context_words(s.get('after'), False))))
    for w in words:
        same = [s for s in swaps if s.get('ja') == w['ja'] and s.get('title') == w['title']]
        free = [s for s in same if id(s) not in used] or same
        if not free:
            continue
        if len({s.get('en') for s in free}) == 1:
            best, sure = free[0], True
        else:
            ranked = sorted(free, key=lambda s: -score(w, s))
            best = ranked[0]
            top = score(w, best)
            sure = top > 0 and all(score(w, s) < top for s in ranked[1:] if s.get('en') != best.get('en'))
        if sure:
            used.add(id(best))
        out[id(w)] = (best, sure)
    return out


GRAMMAR_WORDS = set(grammar.WHOLE) | set(grammar.BARE_WORDS) | set(grammar.KOSOADO) | set(grammar.CONJUNCTIONS)
CONTENT = re.compile('[㐀-鿿々ァ-ヺ]')  # kanji or katakana: a content word, not grammar


def weave_price(word, recency, now_ms):
    """(lookup chance, known at a glance) of a word of the weave's list ({ja, status, k?, free?, read?}: the host's
    words, chatweave.words_for), as the weave prices a swap (chatweave.mjs chanceOf, levels.js costOf): k from the
    word, else its status's prior; its free flag, else its long-term chance under FREE_P; its long-term chance when
    free, else its current one (recency), on the web's scale as the weave's are."""
    import fwlib
    import knowledge
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    k = word['k'] if number(word.get('k')) else (fwlib.KNOWN_PRIOR_K if word.get('status') == 'known' else 1.0)
    long = knowledge.sigmoid(-k)
    free = word['free'] if isinstance(word.get('free'), bool) else long < fwlib.FREE_P
    if free:
        return long, True
    return knowledge.current_chance({'k': k, 'read': word.get('read')}, recency, now_ms), False


def _unoffered(marked, plan, now_ms):
    """The content words of a twin's Japanese the weave didn't offer: (priced, outside). priced: [(ja, chance)] of the
    weave's own words among them that aren't known at a glance (an entry of the list the weave weaves from, a
    conjugated form too, priced as the weave prices its swaps: weave_price); outside: whether one isn't on that list
    (a dictionary word, a word the list leaves out, or none): such a twin shows only at full, as a dictionary word
    does. Grammar (kana only, every piece a grammar unit or a listed grammar word) costs nothing. Without the list
    (plan.words None), nothing is priced."""
    import inflection
    if plan.words is None:
        return [], False
    offered = inflection.Lexicon([{'ja': s['ja'], 'kana': s.get('kana') or ''} for s in plan.swaps if s.get('ja')])
    listed = inflection.lexicon([{'ja': w['ja'], 'kana': w.get('kana') or ''} for w in plan.words])
    by_ja = {w['ja']: w for w in plan.words}
    priced, outside = [], False
    for m in ABBR.finditer(marked):
        word = m.group(2).strip()
        if word in MARKS or offered.entry(word) is not None:
            continue
        if not CONTENT.search(word) and (word in GRAMMAR_WORDS or
                                         all(x in grammar.UNITS for x in grammar._split(word, glossed=True))):
            continue
        ja = listed.entry(word)
        if ja is None or by_ja[ja].get('dictionary'):
            outside = True
            continue
        chance, free = weave_price(by_ja[ja], plan.recency, now_ms)
        if not free:
            priced.append((ja, chance))
    return priced, outside


def _english_view(parts, keep):
    """Each line's English as sentences() reads it: words as <abbr title="#k">, k their place in keep; twins as their
    English; inline code as CODE."""
    out, line = {}, 1
    for p in parts:
        if isinstance(p, tuple):
            if p[1] == '\n':
                line += 1
            elif p[0] == 'raw':
                out.setdefault(line, []).append(re.sub(r'`[^`]*`', ' CODE ', p[1]))
            continue
        if p['k'] == 'w':
            out.setdefault(line, []).append(f'<abbr title="#{keep.index(id(p))}">{p["ja"]}</abbr>')
        else:
            for q in p['english']:
                out.setdefault(line, []).append(
                    re.sub(r'`[^`]*`', ' CODE ', q[1]) if isinstance(q, tuple) else
                    f'<abbr title="#{keep.index(id(q))}">{q["ja"]}</abbr>')
    return {n: ''.join(pieces) for n, pieces in out.items()}


def _twin_words(marked, costly):
    """The costly words in a twin's Japanese, [(ja, lookup chance)]: its glossed words that are (a form of) one of the
    weave's costly swaps ({ja: p})."""
    import inflection
    if not costly:
        return []
    lex = inflection.Lexicon([{'ja': ja} for ja in costly])
    out = []
    for m in ABBR.finditer(marked):
        if m.group(2) in MARKS:
            continue
        ja = lex.entry(m.group(2))
        if ja in costly:
            out.append((ja, costly[ja]))
    return out


def finish(text, plan, types=None, seed=None, remember=True, now_ms=None):
    """A reply written with <ja> twins (twins.py) checked and made into the final markup: a Checked whose text is what
    fw.py check-reply prints. Problems code can't fix stop it (a <ja> without its </ja> or English sentence, Japanese
    outside a twin and outside a gloss, a Japanese sentence outside <ja>). Otherwise it fixes what it can see: a twin
    that breaks the weave's limits (more than MAX_SENTENCES sentences, a sentence over the length limit, grammar not
    on the list or two learning patterns in one sentence) or any rule of problems() is dropped, its sentence left
    English; a swap that breaks the stays-English rules (sentence_problems with english), or a content word's fifth
    gloss, goes back to its English. Then it scores each twin (sentence_model.predict per sentence) and each swap (the
    weave's lookup chance), assigns thresholds and propensities (twins.assign, twins.propensities), writes the final
    markup, checks that it reads back to the same elements and thresholds and that every level renders clean, and
    keeps the plan (twins.remember_plan) for the hook's sentence record."""
    import knowledge
    import random
    import time
    import twins
    types = word_types() if types is None else types
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    lines, blocking, notes = twins.parse_authored(text)
    parts = twins.reply_parts(lines)
    words, all_twins = [], []
    for line, p, twin in _walk(parts):
        if isinstance(p, tuple):
            if p[0] == 'raw':
                bare = re.sub(r'``.+?``|`[^`\n]+`', ' ', p[1])
                for run in re.findall('[぀-ヿ㐀-鿿々ー、。？！「」]+', bare):
                    if JAPANESE.search(run):
                        blocking.append((line, ('a Japanese sentence outside <ja>' if re.search('[。？]', run)
                                                else 'Japanese outside a <ja> twin and outside a gloss') + f': {run}'))
            continue
        if p['k'] == 'w':
            if grammar.clean(p['ja']).strip() in MARKS:
                blocking.append((line, 'a Japanese sentence outside <ja> (a glossed closing mark in English text)'))
            words.append(p)
        else:
            all_twins.append(p)
    if blocking:
        out = [f'jw-check: {len(blocking)} thing{"s" if len(blocking) > 1 else ""} to fix before there is anything '
               'to send:']
        out += [f'- line {n}: {what}' for n, what in sorted(set(blocking))]
        out.append('Write every Japanese sentence as the English sentence followed by its twin in <ja>...</ja> on the '
                   'same line, keep other Japanese in glosses (or quoted in backticks), then run check-reply again.')
        return Checked('\n'.join(out))
    fixed = [f'- line {n}: {what}' for n, what in notes]
    matched = match_swaps(words, reply_contexts(parts), plan.swaps)
    for w in words:  # each kept swap is the draft's swap whose English around it matches best
        swap, sure = matched.get(id(w), (None, False))
        w['en'], w['p'], w['free'], w['dictionary'] = _word_info(w, plan, now_ms, swap if sure else None)
        if swap is not None and not sure:  # the word's numbers from its swap all the same: only the English is unsure
            w['p'], w['free'], w['dictionary'] = float(swap.get('p', w['p'])), bool(swap.get('free')), \
                bool(swap.get('dictionary'))
    # English sentences: swaps that can't stay go back to English
    keep = [id(w) for w in words]
    reverted = {}
    for number, line_text in _english_view(parts, keep).items():
        for sent in sentences(line_text):
            for what, i in sentence_problems(sent, types, english=True):
                if i is not None and sent[i][0] == 'w' and sent[i][2].startswith('#'):
                    k = int(sent[i][2][1:])
                    reverted.setdefault(k, (number, what))
    # twins: checked whole, dropped when anything is wrong
    costly = {w['ja']: w['p'] for w in words if not w['free']}
    costly.update({s['ja']: s['p'] for s in plan.swaps if not s.get('free') and isinstance(s.get('p'), (int, float))
                   and s.get('ja')})
    dropped, kept = [], []
    for t in all_twins:
        english = twins.plain(''.join(q[1] if isinstance(q, tuple) else q['en'] for q in t['english']))
        t['marked'] = twins.japanese_twin(t['japanese'], english)
        found = grammar.sentences(t['marked']) if JAPANESE.search(t['japanese']) else []
        t['sentences'] = [(s['jchars'], grammar.scan_ids(s['segments'])) for s in found]
        why = t.get('why')  # cut through markdown or more than one sentence (twins.twin_trouble)
        if why:
            pass
        elif not found:
            why = 'no Japanese sentence in it'
        elif len(found) > twins.MAX_SENTENCES:
            why = f'{len(found)} Japanese sentences, at most {twins.MAX_SENTENCES} in one twin'
        else:
            wrong = [what for n, what, _where in problems(t['marked'], types) if n]
            for chars, ids in t['sentences']:
                new, learning = sentence_model.allowed(ids, plan.statuses)
                if chars > plan.limit:
                    wrong.append(f'too long: {chars} Japanese characters, the limit is {plan.limit}')
                if new:
                    wrong.append('grammar not on the list: ' + ', '.join(grammar.BY_ID[i].name for i in new))
                if len(learning) > 1:
                    wrong.append('more than one learning pattern: ' + ', '.join(grammar.BY_ID[i].name
                                                                                for i in learning))
            why = wrong[0] if wrong else None
        if why:
            t['why'] = why
            dropped.append(t)
            fixed.append(f'- line {t["line"]}, twin dropped (its sentence stays English): {why}. "{english[:70]}"')
        else:
            kept.append(t)
    kept_ids = {id(t) for t in kept}
    # a content word's fifth gloss at full (kept twins' Japanese and the English words outside them) goes back
    uses = {}
    for t in kept:
        for m in ABBR.finditer(t['marked']):
            if m.group(2) not in MARKS and KANJI.search(m.group(2)):
                uses[m.group(2)] = uses.get(m.group(2), 0) + 1
    for k, w in enumerate(words):
        inside = w['twin'] is not None and id(all_twins[w['twin']]) in kept_ids
        if inside or k in reverted or not KANJI.search(w['ja']):
            continue
        uses[w['ja']] = uses.get(w['ja'], 0) + 1
        if uses[w['ja']] >= REPEAT:
            reverted[k] = (w['line'], f'{w["ja"]} glossed {REPEAT} times or more in this reply')
    for k, (number, what) in sorted(reverted.items()):
        fixed.append(f'- line {number}, swap put back in English: {what}')
    # the elements, in document order (a twin before its English's words)
    els, owner = [], {}
    seen_costly = set()
    dictionary = {w['ja'] for w in words if w['dictionary']} | {
        x['ja'] for x in plan.swaps if x.get('dictionary') and x.get('ja')}

    def word_element(w, parent):
        free = w['free']
        fixed_on = -2 if free else (twins.FULL if w['dictionary'] or w['ja'] in seen_costly else None)
        if not free:
            seen_costly.add(w['ja'])
        e = twins.Element('w', cost_w=0.0 if free else w['p'], opens=0.0 if free else w['p'],
                          value=0.0 if free else twins.learning_value(w['p']), parent=parent, sid=w['sid'],
                          fixed=fixed_on, ja=w['ja'])
        owner[id(w)] = len(els)
        els.append(e)

    for p in parts:
        if isinstance(p, tuple):
            continue
        if p['k'] == 'w':
            if keep.index(id(p)) not in reverted:
                word_element(p, None)
            continue
        if id(p) in kept_ids:
            ps = [sentence_model.predict(plan.model, chars, ids, plan.statuses) for chars, ids in p['sentences']]
            priced, outside = _unoffered(p['marked'], plan, now_ms)
            held = _twin_words(p['marked'], costly) + priced
            inside = [x for _ja, x in held]
            ids_all = {i for _c, ids in p['sentences'] for i in ids}
            p['p'] = ps
            twin = len(els)
            owner[id(p)] = twin
            # a dictionary word, a word not in the word list, or a learning word already used before it, shows only
            # at full: so does its twin
            late = outside or any(ja in dictionary or ja in seen_costly for ja, _x in held)
            els.append(twins.Element('s', cost_s=sum(max(x, plan.price) for x in ps), cost_w=sum(inside),
                                     opens=sum(ps) + sum(inside),
                                     value=twins.SENTENCE_VALUE + sum(twins.learning_value(x) for x in inside)
                                     + (twins.PATTERN_VALUE if plan.learning in ids_all else 0.0),
                                     fixed=twins.FULL if late else None))
            for q in p['english']:
                if not isinstance(q, tuple) and keep.index(id(q)) not in reverted:
                    word_element(q, twin)
            seen_costly.update(ja for ja, _x in held)
        else:
            for q in p['english']:
                if not isinstance(q, tuple) and keep.index(id(q)) not in reverted:
                    word_element(q, None)
    twins.link(els)
    # the reply's English words, for the budgets: the markup with every twin dropped, shown at off
    english_only = _markup(parts, {}, reverted, keep, kept_ids=set())
    count = words_of(twins.render(english_only, intensity.OFF))
    budgets = twins.budgets_for(count, plan.normal)
    rng = random.Random(seed)
    on, explored = twins.assign(els, budgets, rng)
    props = twins.propensities(els, budgets, seed=twins.seed_of(text))
    on_part = {pid: on[i] for pid, i in owner.items()}
    final = _markup(parts, on_part, reverted, keep, kept_ids)
    back, owners = twins.elements(final)
    expected = [(e.kind, on[i], e.parent) for i, e in enumerate(els)]
    error = None
    if [(b['kind'], b['on'], b['parent']) for b in back] != expected:
        error = 'the final markup does not read back to the thresholds assigned'
    texts = twins.annotation_texts(final)
    for level in (intensity.OFF,) + twins.SHOWING:
        shown = twins.render(final, level)
        if 'data-on' in shown or 'data-jw' in shown or '<ja>' in shown:
            error = f'level {level} does not render clean'
        if twins.shown_texts(final, level) != [texts[j] for j, o in enumerate(owners)
                                               if twins.visible(back, o, level)]:
            error = f'level {level} shows other glossed words than its thresholds say'
    if error:
        return Checked(f'jw-check: something went wrong in code ({error}). Send the English reply as you wrote it, '
                       'with the <ja> twins taken out.')
    summary = _summary(els, on, budgets, plan)
    if remember and els:
        sentence_ps = [round(x, 4) for t in kept for x in t['p']]
        twins.remember_plan(grammar.fingerprint(texts), {
            'level': plan.level, 'view': plan.view,
            'e': [[e.kind, on[i], props[i], round(e.opens, 4), int(explored[i])] for i, e in enumerate(els)],
            's': sentence_ps,
            'd': [sorted({i for _c, ids in t.get('sentences') or () for i in ids}) for t in dropped]}, now_ms)
    n_words = sum(1 for e in els if e.kind == 'w')
    n_free = sum(1 for e in els if e.kind == 'w' and e.fixed == -2)
    head = (f'jw-check: {len(kept)} twin{"s" if len(kept) != 1 else ""} and {n_words} word swap'
            f'{"s" if n_words != 1 else ""} ({n_free} known at a glance)'
            + (f'; {len(dropped)} twin{"s" if len(dropped) != 1 else ""} dropped' if dropped else '')
            + (f', {len(reverted)} swap{"s" if len(reverted) != 1 else ""} put back in English' if reverted else '')
            + (':' if fixed else '.'))
    out = [head] + fixed + summary['lines']
    out.append(f'Send everything below the {SEPARATOR} line exactly as it is, as your whole final message.')
    out += [SEPARATOR, final]
    return Checked('\n'.join(out), final, els, on, props, explored, summary)


def _markup(parts, on, reverted, keep, kept_ids):
    """twins.markup with the reverted swaps as their English and the dropped twins as their English alone."""
    import twins
    out = []
    for p in parts:
        if isinstance(p, tuple):
            out.append(p[1])
        elif p['k'] == 'w':
            out.append(twins.esc_text(p['en']) if keep.index(id(p)) in reverted else
                       twins.word_markup(p, on.get(id(p), 0)))
        elif id(p) in kept_ids:
            n = on.get(id(p), 0)
            out.append(f'<span data-jw="ja" data-on="{n}">{p["marked"]}</span>'
                       f'<span data-jw="en" data-on="{n}">{_markup(p["english"], on, reverted, keep, kept_ids)}'
                       '</span>')
        else:
            out.append(_markup(p['english'], on, reverted, keep, kept_ids))
    return ''.join(out)


def _summary(els, on, budgets, plan):
    """{lines, levels: {level: {twins, sentence opens, words, word lookups}}}: what each level shows and its expected
    opens against its budget's parts."""
    import twins
    levels, lines = {}, ['Per level, if read (sentences: their translations\' expected opens against the sentences\' '
                         'part of the budget; costly words: their expected lookups against the words\' part):']
    for level in twins.SHOWING:
        shown = [i for i in range(len(els)) if twins.shown_at(els, on, i, level)]
        t = [i for i in shown if els[i].kind == 's']
        w = [i for i in shown if els[i].kind == 'w' and els[i].cost_w > 0]
        opens_s = sum(els[i].opens - els[i].cost_w for i in t)
        opens_w = sum(els[i].cost_w for i in shown)
        levels[level] = {'twins': len(t), 'opens': round(opens_s, 4), 'words': len(w), 'lookups': round(opens_w, 4)}
        now = ' (now)' if level == plan.level else ''
        if level in budgets:
            words_part, sentences_part = budgets[level]
            lines.append(f'  {LEVEL_NAMES[level]}{now}: {len(t)} sentence{"s" if len(t) != 1 else ""} '
                         f'{opens_s:.2f} of {sentences_part:.2f}; {len(w)} costly word{"s" if len(w) != 1 else ""} '
                         f'{opens_w:.2f} of {words_part:.2f}')
        else:
            lines.append(f'  {LEVEL_NAMES[level]}{now}: {len(t)} sentence{"s" if len(t) != 1 else ""} {opens_s:.2f}; '
                         f'{len(w)} costly word{"s" if len(w) != 1 else ""} {opens_w:.2f} (no budget)')
    return {'lines': lines, 'levels': levels}
