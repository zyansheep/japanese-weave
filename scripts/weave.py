#!/usr/bin/env python3
"""UserPromptSubmit hook: prints a short Japanese Weave brief as additional context for the next reply.

The hook's context stays in the conversation, so a chat gets the whole brief (its rules and the known words most useful
in chat by name) once, and on later turns a short reminder with what changed (chat_turn). Each brief is also kept as a
record of what the reply was offered: a `brief` op (hub.py).

Usage: weave.py --hook   (reads the hook JSON on stdin, prints hook JSON)
       weave.py          (prints the plain brief, for checking: a chat's first turn)
"""
import csv
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwlib  # noqa: E402
import hub  # noqa: E402
import intensity  # noqa: E402
import knowledge  # noqa: E402
import threads  # noqa: E402

STALE = 30 * 60  # refresh chat exposure in the background when the index is older than this
SYNC_EVERY = 120  # with a collector: sync the cache in the background when it is older than this
# The claude.ai / ChatGPT blocks (export.py), which have no lookup model: per stage (target % of words in Japanese,
# cap %, max Japanese words in one sentence). The chat brief spends a lookup budget instead (intensity.BUDGETS).
STAGES = {1: (6, 10, 2), 2: (12, 20, 4), 3: (25, 35, 4), 4: (40, 55, 4)}
TAIL = 2_000_000  # bytes at the end of a transcript searched for the last reply and the last compaction
SHORT = 40  # replies with fewer prose words are too short to measure
LEARN_MAX = 24  # most learning words listed, best learning value first, at every level
PROBES_MAX = 3  # most words a brief asks for once for the delayed-start experiment's probe
DEFAULT_SWAPS = 12  # known connectors and adverbs (the base list's tier A), first first, named on every turn
# Claude Code hands Claude at most 10,000 characters of a hook's context and puts the rest in a file it doesn't ask
# Claude to read, so a brief stays under this; a known word that doesn't fit is named on the next turn.
BRIEF_MAX = 9500
# The known words named besides the default swaps take at most this many characters, the most useful in chat first
# (priority); the others are still known, just not named (capped). From Stage 3 (150 known words, so any list the
# cap shortens, unless the stage is pinned lower), the rest of a whole brief takes at most about 4,900: the rules
# 2,970 characters with <abbr> glosses (footer glosses are shorter), 24 learning words up to 900, a probe line up to
# 200, the default swaps 360, the feedback line on the last reply 430 and the known line's own words 220. So a
# whole brief stays under BRIEF_MAX at any number of known words; a pinned lower stage's longer rules can push the
# last few named words to the next turn. 250 to 290 words fit: on the 2026-10-20 state all 255 known at a glance,
# 285 of 422 with 500 known and 255 of 1,318 with 1,500 (base-list meanings run longer than the Anki ones).
KNOWN_ROOM = 4400
RECUR_DAYS = 2  # a known word that came up in the user's chat replies on this many days is named first
# A chat gets the whole brief again this many turns after it last did, in case the earlier one left the model's
# context without a compaction the transcript shows (see chat_turn). In the user's transcripts the median chat had 3
# prompts and 1 in 5 had over 20, and compactions came a median of 9 prompts apart, so a missed one costs at most
# about two compactions' worth of turns without the list; the whole brief is about 3.7 later ones (8,182 against
# 2,226 characters on the 2026-10-20 state), so this adds about 300 characters a turn.
FULL_EVERY = 20
MEMORY_KEPT = 50  # chats whose known list sessions.json keeps, the newest; an older one gets the whole brief again
# The brief names no numbers and no katakana words: it asks for plain English for numbers and no katakana loanwords.
NUMBER = re.compile('^〜|^[〇一二三四五六七八九十百千万億]+つ?$')
KATAKANA = re.compile('^[\u30a0-\u30ff\uff66-\uff9f]+$')
# Learning words asked for per 100 words: the fewest and the most, and the most per word allowed in a sentence (about
# five prose sentences in 100 words).
PER_100, PER_SENTENCE_100 = (0.5, 8), 5
# Whole sentences in Japanese asked for per 100 words at most: three in five, as the brief asked at its heaviest
# before sentences were priced. What the budget would buy past this goes to learning words (intensity.split).
SENTENCES_MAX = 3
# Learning words by lookup chance: the least chance in each group.
TIERS = ((0.2, 'often'), (0.12, 'sometimes'), (0.0, 'rarely'))
OVER, UNDER = 1.5, 0.5  # the feedback line: expected lookups past this many times the budget are over or under it
EXTRA = re.compile(r'\s*(?:\**recall:|.*\s=\s.*")', re.I)  # footer gloss lines (and replies' old recall cue)
ABBR = re.compile(r'<abbr\b[^>]*>(.*?)</abbr>', re.I | re.S)  # T3 gloss markup: count the word, not its title
# Japanese left over once the listed words are taken out, counted as off-list words: each run of kanji or katakana,
# and below Stage 3 hiragana outside whole sentences too, one word per two characters. The hiragana in a whole sentence
# is the particles and です/ます it puts around listed words; from Stage 3, so is any other.
WORDLIKE = re.compile(f'[{fwlib.KANJI}]+|[\u30a1-\u30fa][\u30a1-\u30fa\u30fc]*|[\uff66-\uff9d][\uff66-\uff9f]*')
HIRAGANA = re.compile('[\u3041-\u309f]')
SENSES = re.compile(r'\s*/\s*')  # between a known word's senses, 'inside / during', written 'inside/during'
# A whole sentence in Japanese: from a sentence break (an English one, a line break or a Japanese closing mark) to a
# Japanese closing mark, with no Latin letters in between (see whole_sentences).
CLAUSE = re.compile(r'[^.!?\n。？！]*[。？！]')
LATIN = re.compile('[A-Za-z]')


def exposure():
    """Per-word chat exposure. Starts a background refresh when the index is stale or missing, or with a collector
    set, when the cache is (that sync also sends this machine's new chat replies and anything queued)."""
    path = fwlib.home() / 'exposure.json'
    data = fwlib.read_json(path, {}) or {}
    if fwlib.collector_url():
        fresh = not hub.stale(SYNC_EVERY)
    else:  # every refresh writes scan.json, also when no reply was new
        scanned = fwlib.home() / fwlib.SCAN
        try:
            fresh = time.time() - (scanned if scanned.exists() else path).stat().st_mtime < STALE
        except OSError:
            fresh = False
    if not fresh:
        knowledge.start_refresh()  # `fw.py refresh`, never waited on
    return data.get('words', {})


def chance(word, know=None):
    """The chance the user looks a word up in chat: its estimate (knowledge.json, T3 offset) when the gloss log has
    one, else from its status, known words at fwlib.KNOWN_PRIOR_K and the rest at the neutral prior."""
    know = know or {}
    estimate = knowledge.word_estimate(know, word['ja'], 't3')
    if estimate:
        return estimate[0]
    offset = (know.get('offset') or {}).get('t3', 0.0)
    return knowledge.sigmoid(offset - (fwlib.KNOWN_PRIOR_K if word['status'] == 'known' else knowledge.prior(None)))


# Fully Japanese example sentences and the listed words each one needs. The brief shows two whose words the user knows
# at a glance: a fixed pair used words still in the user's queue and nudged Claude toward them.
EXAMPLES = (('今日は忙しいです。', ('今日', '忙しい')), ('これで大丈夫です。', ('大丈夫',)), ('それは本当です。', ('本当',)),
            ('仕事は終わりました。', ('仕事', '終わる')), ('これは簡単です。', ('簡単',)), ('問題は設定です。', ('問題', '設定')))


def examples(chances):
    """'like "A" or "B"' from EXAMPLES, the first two whose words are known at a glance (else the last two)."""
    fits = [s for s, needs in EXAMPLES if all(chances.get(ja, 1.0) < fwlib.FREE_P for ja in needs)]
    a, b = (fits + [s for s, _ in EXAMPLES[::-1] if s not in fits])[:2]
    return f'like "{a}" or "{b}"'


def lists(state, seen=None, know=None, most=LEARN_MAX):
    """The word lists: (known at a glance, in list order; learning, best learning value first, at most `most` (None:
    all); {ja: lookup chance} for every word in use). Known at a glance means a lookup chance under fwlib.FREE_P,
    whatever the label; every other word in use is learning. Words in use are fwlib.introduced's, so none the
    delayed-start experiment hides (fwlib.hidden)."""
    seen = seen or {}
    words = fwlib.introduced(state)
    p = {w['ja']: chance(w, know) for w in words}
    free = [w for w in state['words'] if w['ja'] in p and p[w['ja']] < fwlib.FREE_P]
    learning = sorted((w for w in words if p[w['ja']] >= fwlib.FREE_P),
                      key=lambda w: (-knowledge.learning_value(p[w['ja']]), -seen.get(w['ja'], {}).get('n', 0)))
    return free, learning[:most], p


def probes(state):
    """The words whose delayed-start probe is due (fwlib.phase), at most PROBES_MAX, the longest due first: the brief
    asks for each once, outside the budget, until a showing of it is read."""
    due = [w for w in fwlib.introduced(state) if fwlib.phase(w) == 'probe']
    return sorted(due, key=lambda w: w['arm']['probe'])[:PROBES_MAX]


def lookup_budget(state, level=0):
    """Lookups per 100 words the level allows for words not known at a glance (intensity.budget); the challenge, when
    the user set one, is normal's."""
    return intensity.budget(level, knowledge.challenge_of(state) or None)


def half_up(x):
    return math.floor(x + 0.5)


def most_per_100(level):
    """The most learning words per 100 words a level asks for: as many as fit, intensity.per_sentence in a sentence."""
    return min(PER_100[1], PER_SENTENCE_100 * intensity.per_sentence(level))


def rounded(n):
    """An amount per 100 words as the brief says it: to the nearest half under 2, the nearest whole number above."""
    return half_up(2 * n) / 2 if n < 2 else half_up(n)


def per_100(budget, chances, most=PER_100[1]):
    """Learning words per 100 words that spend about the budget: budget / their mean lookup chance, rounded, from
    PER_100's fewest to most. None without learning words."""
    if not chances:
        return None
    return max(PER_100[0], min(most, rounded(budget * len(chances) / sum(chances))))


def amounts(budget, chances, price, level, sentences=True):
    """(learning words per 100 words or None without any, whole sentences in Japanese per 100 words, the learning words'
    part of the budget, the sentences' part): the budget split as intensity.split does on the web, sentences up to
    intensity.SENTENCE_SHARE of it at price each and learning words the rest at their mean lookup chance (chances),
    and what one can't use goes to the other. Learning words can spend at most most_per_100(level) of them, and nothing
    without any; sentences at most SENTENCES_MAX, and nothing without words known at a glance to write them with
    (sentences False)."""
    mean = sum(chances) / len(chances) if chances else 0.0
    words, whole = intensity.split(budget, most_per_100(level) * mean, SENTENCES_MAX * price if sentences else 0.0)
    return per_100(words, chances, most_per_100(level)), rounded(whole / price), words, whole


@functools.lru_cache(maxsize=1)
def base_list():
    """{key: (rank, tier)} for the base word list (data/lexicon.csv): its order, connectors and the words technical chat
    uses first, and its tier (A: connectors and adverbs, T: technical, B: everyday, C: poor fits;
    tools/assemble_lexicon.py). Empty when it can't be read."""
    try:
        with open(fwlib.LEXICON, encoding='utf-8', newline='') as file:
            rows = [r for r in csv.DictReader(file) if (r.get('ja') or '').strip()]
    except (OSError, ValueError, csv.Error):
        return {}
    return {fwlib.key(r['ja']): (i, (r.get('tier') or '').strip()) for i, r in enumerate(rows)}


def named_words(free):
    """The words known at a glance the brief may name: in the base list's order (most useful in chat first), then the
    others (Anki words) in list order. Numbers and katakana words stay out, since the brief asks for neither."""
    rank = base_list()
    words = [w for w in free if not NUMBER.match(w['ja']) and not KATAKANA.match(w['ja'])
             and not w['en'].startswith('counter')]
    return sorted(words, key=lambda w: rank.get(fwlib.key(w['ja']), (len(rank), ''))[0])


def default_swaps(words):
    """The first DEFAULT_SWAPS of the named words that are connectors or adverbs (tier A), else, with a base list
    without tiers, the first of them in its order."""
    rank = base_list()
    tiered = any(t for _, t in rank.values())
    return [w for w in words if not tiered or rank.get(fwlib.key(w['ja']), (0, ''))[1] == 'A'][:DEFAULT_SWAPS]


def days_seen(seen, ja):
    """Days a word came up in the user's chat replies (exposure)."""
    entry = seen.get(ja) if isinstance(seen, dict) else None
    days = entry.get('days') if isinstance(entry, dict) else None
    return len(days) if isinstance(days, list) else 0


def priority(words, seen=None):
    """Words known at a glance in the order the brief names them, the most useful in chat first: those that came up in
    the user's chat replies (seen, the exposure index) on RECUR_DAYS days or more, then the base list's others, then
    the rest (Anki words, whose meanings are the cards' and often odd). Each group in the base list's order (frequency,
    connectors and technical words early: its tier), the words it lacks by the days they came up, then in list order."""
    rank, seen = base_list(), seen or {}

    def key(w):
        r, days = rank.get(fwlib.key(w['ja'])), days_seen(seen, w['ja'])
        return 0 if days >= RECUR_DAYS else 1 if r else 2, r[0] if r else len(rank), -days

    return sorted(words, key=key)


def entry(w):
    """A known word as the list names it: 'ja meaning', its senses joined by a slash, 'ずっと all along/by far'."""
    return w['ja'] + ' ' + SENSES.sub('/', w['en'].strip())


def capped(words, seen=None, room=KNOWN_ROOM):
    """The first words by priority whose entries fit in room characters: the known words a brief may name."""
    out, size = [], 0
    for w in priority(words, seen):
        size += len(entry(w)) + 2
        if size > room:
            break
        out.append(w)
    return out


def entries(words, room):
    """'ja meaning; ja meaning' for as many words as fit in room characters, and the words named."""
    out, size = [], 0
    for w in words:
        size += len(entry(w)) + 2
        if size > room:
            break
        out.append(entry(w))
    return '; '.join(out), words[:len(out)]


def known_line(words, gone, first, room, swaps=True, more=0):
    """The known words the chat hasn't been named yet (words, from capped), as many as fit in room characters: the
    whole list on a chat's first turn, else the changes since (words newly known at a glance or newly among the most
    useful; gone: words no longer known at a glance) with a pointer to the earlier list. more: the words known at a
    glance left out of the list (capped). Returns (line, words named, gone words named); the rest of words wait for
    the next turn."""
    if first:
        head = (f'Known{", besides the default swaps" if swaps else ""} (each Japanese word, then its meaning; write '
                'the Japanese every time the English has that meaning): ')
        rest = lambda left: (f'; and {left:,} more the user knows, not named here: keep the English for any word '
                             'not named' if left else '')
        text, named = entries(words, room - len(head) - len(rest(len(words) + more)))
        return head + (text or 'none') + rest(len(words) - len(named) + more), named, []
    rest = lambda left: f'; and {left} more, named next turn' if left else ''
    out = 'Known: as listed earlier in this chat'
    lead = '; no longer known at a glance, so keep the English unless listed as learning: '
    tail = '. If you can no longer see that list, use only the Japanese words named in this message.'
    dropped, size = [], len(out) + len(lead) + len(tail)
    for ja in gone:
        size += len(ja) + 2
        if size > room:
            break
        dropped.append(ja)
    if dropped:
        out += lead + ', '.join(dropped)
    text, named = entries(words, room - len(out) - len('; also known: ') - len(tail) - len(rest(len(words))))
    if text:
        out += '; also known: ' + text + rest(len(words) - len(named))
    return out + tail, named, dropped


def turned(level):
    """', turned lighter for now by the user' and the like: what the reader's intensity is now (intensity.py)."""
    if intensity.is_full(level):
        return ', turned to full for now by the user'
    return f', turned {intensity.NAMES[level]} for now by the user' if level else ''


def per_100_text(n, what):
    """'about 2 learning words per 100 words (6 in a 300-word reply)' for n 2 and what 'learning word'."""
    return f'about {n:g} {what}{"" if n == 1 else "s"} per 100 words ({half_up(3 * n)} in a 300-word reply)'


def rules(stage, level, markup, like, n, m):
    """The brief's rules, in full: a chat's first turn, and any turn on which they change (see compose). n and m: the
    learning words and whole sentences in Japanese asked for per 100 words (amounts); None at full, which has no
    budget: then every sentence the user could follow is Japanese and every listed word goes in wherever it fits."""
    full = intensity.is_full(level)
    as_listed = ('Write each word exactly as listed (same kanji and kana), never conjugated, and with no particles, '
                 'です/ます or other Japanese grammar around it; adjectives go before their noun as listed ("a 新しい '
                 'build", "the 簡単 fix"); suru-nouns stand in for the English verb or noun ("let me 確認 the logs"). ')
    space = 'Put a space on each side of a Japanese word inside English text, as for an English word. '
    fit = ('Use a word only where it means exactly what the English word would mean there; where one doesn\'t fit, '
           'keep the English word (だから only for "so" meaning therefore, never "so big"; でも and しかし only for "but" '
           'joining two ideas; もう for "already", まだ for "still" or "not yet"; 前 and 後 only before or after a noun '
           'or a time, never joining clauses; はい and いいえ only as answers). ')
    plain = ('Plain English for code, commands, paths, numbers, names, IDs'
             + (' and option letters' if full else ', option letters and steps the user will follow')
             + ', and no Japanese right before a path; zero Japanese for health, medication, legal, money, safety, '
             'emergency, driving or urgent topics. Japanese goes only in your chat reply: never '
             'in files, code, configs, commit or PR text, messages to other agents, subagent prompts, or drafts and '
             'documents meant to be shared. ')
    glosses = ('No bold on Japanese words and no glosses, readings or romaji in the text itself. Wrap each Japanese '
               'word in <abbr title="hiragana reading · meaning">, like <abbr title="たぶん · maybe">多分</abbr>; a word '
               'written only in kana gets just its meaning, like <abbr title="but / however">でも</abbr>. Glossed words '
               'are looked up one at a time, so in a fully Japanese sentence wrap each word the same way (conjugated '
               'verbs whole, with the reading and meaning of that form), leave particles and です/ます outside the tags, and '
               'wrap the closing 。 or ？ in an <abbr> whose title is the English translation of the whole sentence, like '
               '<abbr title="もんだい · problem">問題</abbr>は<abbr title="せってい · settings">設定</abbr>です'
               '<abbr title="The problem is the settings.">。</abbr> The user hovers or clicks to see a title, so add no '
               'footer.'
               if markup else
               'No bold on Japanese words and no inline glosses or romaji. End the reply with one footer line glossing the '
               'learning words you used that are not glossed earlier in this chat, at most 8, with the romaji, like: '
               '(多分 = maybe, "tabun"); nothing to gloss, no footer.')
    if full:  # the one thing full says that normal doesn't: no budget
        amount = ('There is no budget for now, and too little Japanese is the usual miss: write every sentence the '
                  'user could follow fully in Japanese (listed words, known or learning, plus common grammar: '
                  f'particles, demonstratives, verb endings and plain です/ます forms), {like} and swap every listed '
                  'word, known or learning, into the other sentences wherever the English has its meaning, steps '
                  'included, with no cap per sentence. ')
    else:
        most = intensity.per_sentence(level)
        words = (per_100_text(n, 'learning word') + f', at most {"one" if most == 1 else most} in a sentence, each '
                 'where the English around it makes its meaning clear, spread out' if n else
                 'no learning words right now, only known ones')
        sentences = (per_100_text(m, 'whole sentence') + ' fully in Japanese: short, plain です/ます sentences of '
                     f'known words, particles and これ/それ only, guessable from the English around them, {like} Steps '
                     'stay English, so put the Japanese sentences in the explanation, summary and advice around '
                     'them. ' if m else 'no whole sentences in Japanese right now. ')
        amount = ('Learning words and whole sentences in Japanese cost a lookup now and then (a sentence when the user '
                  f'has to look up what it says), so they come in measured amounts: {words}; and {sentences}')
    english = ('Every other sentence stays an English sentence with Japanese words swapped in, like "The build passes '
               f'今, でも the cache is stale, だから I cleared it." {as_listed}' if stage < 3 else
               'The other sentences stay English with listed words swapped in as written, without particles. ')
    return (f'Japanese Weave (Stage {stage}{turned(level)}): the user is a native English speaker learning Japanese '
            'passively. Write your chat reply in English and swap in Japanese words from the lists below. Known '
            'words cost the user nothing, so write each one in Japanese wherever the English has its meaning. '
            f'{amount}Leaving a known word in English is the usual miss, so check each sentence as you write it. '
            f'{space}{english}No other Japanese anywhere, not even a common word, and no katakana loanwords; a '
            f'sentence needing one stays English. {fit}{plain}{glosses}')


def digest(stage, level, markup, n, m):
    """The rules on a chat's later turns: a pointer to the whole brief earlier in the chat, this reply's amounts, and
    the rules that matter most restated in a line."""
    full = intensity.is_full(level)
    english = ('the rest English with listed words swapped in as listed'
               + (', never conjugated and without particles' if stage < 3 else ''))
    if full:
        amount = ('no budget: every sentence the user could follow fully in Japanese (listed words, known or learning, '
                  f'plus common grammar), {english}, every one wherever the English has its meaning, steps included')
    else:
        most = intensity.per_sentence(level)
        learn = (per_100_text(n, 'learning word') + f', at most {"one" if most == 1 else most} in a sentence, where '
                 'the English around each makes it clear' if n else 'no learning words, only known ones')
        whole = (per_100_text(m, 'whole sentence') + ' fully in Japanese: short, plain です/ます sentences of known '
                 'words only' if m else 'no whole sentences in Japanese')
        amount = ('each known word in Japanese wherever the English has its meaning (leaving one in English is the '
                  f'usual miss); {learn}; {whole}; {english}')
    gloss = ('each Japanese word in <abbr title="hiragana reading · meaning"> (kana-only words: just the meaning), as '
             'before' if markup else 'learning words you use glossed in one footer line with romaji, as before')
    steps = ' and names' if full else ', names and steps the user will follow'
    return (f'Japanese Weave (Stage {stage}{turned(level)}): the Japanese Weave brief earlier in this chat still '
            f'holds. This reply: {amount}. No other Japanese and no katakana loanwords; plain English for code, '
            f'commands, paths, numbers{steps}; Japanese only in your chat reply, never in files, code, commits, '
            'messages to other agents or documents to share; none for health, medication, legal, money, safety or '
            f'urgent topics; {gloss}.')


def compose(state, seen, markup=False, know=None, level=0, had=None, tail='', room=BRIEF_MAX):
    """(brief, what the chat has after it, plan). had: what the chat already has from earlier turns, {known: the words
    named so far, rules: which rules}; None on its first turn (see chat_turn). Then the brief is whole: the rules, the
    learning words, the default swaps and the known words by name, the most useful in chat first, as many as fit
    KNOWN_ROOM (capped). Later it is short: the rules as a digest (in full again when they change: at another stage,
    with other glosses or examples), the learning words, the default swaps and the known words that changed. tail:
    what the caller adds (the feedback line), kept within room with the rest.

    markup: the chat renders <abbr> titles on hover or click (see abbr_glosses), so each Japanese word carries its own
    gloss instead of a footer line. know: knowledge.json (adaptive difficulty): each word's lookup chance (chance),
    which splits the words in use into known at a glance and learning and orders the learning ones by learning value,
    most useful first, and the price of a whole sentence in Japanese (intensity.sentence_price). level: the chat
    intensity in effect (intensity.py). Below full it is a lookup budget: known words go in wherever they fit, and the
    learning words and whole sentences asked for are as many as spend about the budget, split between them as on the
    web (amounts). Full is the same brief with no budget: every sentence the user could follow in Japanese and every
    listed word wherever it fits.

    plan: what the reply was offered, as a web passage's plan (feedback.py): level, its factor, the price of a whole
    sentence, the budget and the expected lookups per 100 words (none at full), the learning words and whole sentences
    asked for per 100 words (ask, sentences), the costly words offered with their lookup chances, the delayed-start
    arms of the words it offers or asks for (fwlib.arm_label: 'shown', 'held:probe', 'shown:probe'), the words known at
    a glance, and whether the brief was whole.

    The delayed-start experiment: words it hides (fwlib.hidden) are in no list, and the words whose probe is due
    (probes) get a line of their own asking for each once, outside the budget, where it fits."""
    stage = fwlib.stage_for(state)
    full = intensity.is_full(level)
    free, learning, p = lists(state, seen, know)
    like = examples(p)
    words = named_words(free)
    price = intensity.sentence_price(know)
    budget = lookup_budget(state, level)
    n, m = (None, None) if full else amounts(budget, [p[w['ja']] for w in learning], price, level, bool(words))[:2]
    key = hashlib.sha1(json.dumps([stage, markup, like], ensure_ascii=False).encode()).hexdigest()[:12]
    sent = set(had['known']) if had and isinstance(had.get('known'), list) else None
    head = (digest(stage, level, markup, n, m) if sent is not None and had.get('rules') == key else
            rules(stage, level, markup, like, n, m))
    lines = [head, 'Learning now, by how often the user looks one up: ' + learning_groups(learning, p, stage)]
    swaps = default_swaps(words)
    if swaps:
        lines.append('Default swaps (write the Japanese every time the English word has that meaning): '
                     + '; '.join(f'{w["en"]} → {w["ja"]}' for w in swaps))
    due = [w for w in probes(state) if not intensity.is_full(level)]  # full uses every listed word anyway
    probe = ['Use each of these once in this reply where it fits naturally, like a learning word and on top of the '
             'amounts above: ' + '; '.join(f'{w["ja"]} = {w["en"]}' for w in due)] if due else []
    swapped = {w['ja'] for w in swaps}
    names = {w['ja'] for w in words}
    listed = capped([w for w in words if w['ja'] not in swapped], seen)
    unnamed = [w for w in listed if sent is None or w['ja'] not in sent]
    gone = [] if sent is None else sorted(ja for ja in sent if ja not in names)
    left = room - sum(len(line) + 1 for line in lines + probe) - len(tail)
    known, named, dropped = known_line(unnamed, gone, sent is None, left, bool(swaps),
                                       len(words) - len(swaps) - len(listed))
    lines += probe + [known]
    after = {'known': sorted(((sent or set()) - set(dropped)) | swapped | {w['ja'] for w in named}), 'rules': key}
    arms = {w['ja']: fwlib.arm_label(w) for w in learning + due + free if fwlib.arm_label(w)}
    plan = {'level': level, 'intensity': intensity.factor(level),
            'offered': [[w['ja'], round(p[w['ja']], 3)] for w in learning], 'arms': dict(list(arms.items())[:200]),
            'known': len(free), 'whole': sent is None, 'sentencePrice': round(price, 4)}
    if not full:
        mean = sum(p[w['ja']] for w in learning) / len(learning) if learning else 0
        plan.update(budget=round(budget, 3), ask=n or 0, sentences=m, expected=round((n or 0) * mean + m * price, 3))
    return '\n'.join(lines), after, plan


def brief(state, seen, markup=False, know=None, level=0, had=None):
    """The brief alone (compose): with had None, a chat's first turn."""
    return compose(state, seen, markup, know, level, had)[0]


def abbr_glosses(state, host_check=None):
    """True when Japanese words should carry <abbr> glosses: the user opted in (`fw.py glosses abbr`, for a T3 Code build
    that shows <abbr> titles) and T3 Code hosts this chat. Terminals and upstream T3 would lose the gloss."""
    if (state.get('prefs') or {}).get('chat_glosses') != 'abbr':
        return False
    return (host_check or t3_host)()


def learning_groups(learning, p, stage):
    """The learning words by lookup chance (TIERS), each group with its rough odds: 'often, about 1 in 4: 結局 = after
    all; ... | rarely, about 1 in 10: ...'. Below Stage 3 each word carries its romaji."""
    entry = lambda w: f'{w["ja"]} = {w["en"]}' + (f', "{w["hint"]}"' if stage <= 2 else '')
    groups, ceiling = [], 2.0
    for least, name in TIERS:
        group = [w for w in learning if least <= p[w['ja']] < ceiling]
        ceiling = least
        if group:
            mean = sum(p[w['ja']] for w in group) / len(group)
            odds = f'about 1 in {half_up(1 / mean)}' if mean < 0.6 else 'more often than not'
            groups.append(f'{name}, {odds}: ' + '; '.join(entry(w) for w in group))
    return ' | '.join(groups) or 'none'


def tail_lines(path):
    """The complete lines of the last TAIL bytes of a transcript."""
    with open(path, 'rb') as file:
        size = file.seek(0, 2)
        file.seek(max(0, size - TAIL))
        if size > TAIL:
            file.readline()  # skip the cut line
        return [raw.decode('utf-8', 'replace') for raw in file]


def last_reply(path, lines=None):
    """Text of the latest main-thread assistant reply in a Claude Code or Codex transcript, or None."""
    texts = [text for _, text in fwlib.replies(tail_lines(path) if lines is None else lines) if text.strip()]
    return texts[-1] if texts else None


def compaction(lines):
    """The latest compaction in transcript lines, by its id (else its time), or None: Claude Code writes a system
    line with subtype compact_boundary, Codex a line of type compacted. What came before it is out of the model's
    context, earlier briefs included."""
    mark = None
    for line in lines:
        if '"compact_boundary"' not in line and '"compacted"' not in line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'compacted' or (item.get('type') == 'system'
                                               and item.get('subtype') == 'compact_boundary'):
            payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
            mark = str(item.get('uuid') or payload.get('window_id') or item.get('timestamp') or len(lines))[:100]
    return mark


def uses(text, words):
    """({ja: uses} for the words a reply uses, the prose left once they are taken out). Code (fwlib.strip_code), links
    and glosses are skipped (and an old reply's recall cue). Longest first, each match removed before the next search:
    一緒に is not also 一緒."""
    prose = fwlib.norm(fwlib.strip_code(ABBR.sub(r'\1', text)))
    rest = '\n'.join(line for line in prose.split('\n') if not EXTRA.match(line))
    found = {}
    for word in sorted(words, key=lambda w: -len(w['ja'])):
        regex = fwlib.pattern(word['ja'], word.get('kana', ''))
        n = len(regex.findall(rest))
        if n:
            found[word['ja']] = n
            rest = regex.sub(' ', rest)
    return found, rest


def english_words(prose):
    return len(re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", fwlib.JAPANESE.sub(' ', prose)))


def share(text, words):
    """(Japanese words, prose words, learning words used, Japanese words off the lists) in a reply (see uses).

    Japanese has no spaces, so off-list Japanese is counted as one word per two characters."""
    found, rest = uses(text, words)
    status = {w['ja']: w['status'] for w in words}
    listed = sum(found.values())
    learning = sum(n for ja, n in found.items() if status[ja] != 'known')
    off_list = len(fwlib.JAPANESE.findall(rest)) // 2
    return listed + off_list, english_words(rest) + listed + off_list, learning, off_list


def whole_sentences(rest):
    """(whole sentences in Japanese, the text around them) in what uses left of a reply: each run from a sentence break
    to a Japanese closing mark (。, ？ or ！) with no Latin letters in it (CLAUSE)."""
    spans = [m.span() for m in CLAUSE.finditer(rest) if not LATIN.search(m.group())]
    around, at = [], 0
    for start, end in spans:
        around.append(rest[at:start])
        at = end
    return len(spans), ' '.join(around + [rest[at:]])


def calibration(text, state, level=0, know=None, seen=None):
    """One line on the last reply in this chat: its Japanese words (known at a glance, learning, off the lists) and
    whole sentences in Japanese, and the lookups they would cost per 100 words against the level's budget, so the next
    reply corrects toward it. Known words are free, named in the brief or not; an off-list word costs a word's neutral
    prior and a whole sentence its price (intensity.sentence_price). None at full (no budget) or off, or for a short
    reply. know and seen as for brief, so the lists and amounts are the brief's: over or under names the learning words
    or the sentences, whichever spent past its part of the budget (amounts)."""
    if intensity.is_full(level) or intensity.is_off(level):
        return ''
    stage = fwlib.stage_for(state)
    found, rest = uses(text, fwlib.introduced(state))
    sentences, around = whole_sentences(rest)
    stray = len(WORDLIKE.findall(rest)) + (len(HIRAGANA.findall(around)) // 2 if stage < 3 else 0)
    total = english_words(rest) + sum(found.values()) + max(stray, len(fwlib.JAPANESE.findall(rest)) // 2)
    if total < SHORT:
        return ''
    free_words, learning_words, p = lists(state, seen, know)
    free, learning = {w['ja'] for w in free_words}, {w['ja'] for w in learning_words}
    known = sum(n for ja, n in found.items() if ja in free)
    learned = sum(n for ja, n in found.items() if ja in learning)
    off_list = sum(n for ja, n in found.items() if ja not in free and ja not in learning) + stray
    price, budget = intensity.sentence_price(know), lookup_budget(state, level)
    # the most the brief asks for may spend less than the budget: "under" is under what it asks
    _, _, words_part, whole_part = amounts(budget, [p[ja] for ja in learning], price, level,
                                           bool(named_words(free_words)))
    words = 100 * (sum(p[ja] * n for ja, n in found.items() if ja not in free)
                   + stray * knowledge.sigmoid(-knowledge.prior(None))) / total
    whole = 100 * sentences * price / total
    expected = words + whole
    which = lambda off: ' and '.join(name for name, spent, part in (('learning words', words, words_part),
                                                                    ('whole Japanese sentences', whole, whole_part))
                                     if off(spent, part)) or 'learning words'
    verdict = ('it had Japanese outside the lists, so this time use no Japanese outside the lists' if off_list else
               f'over budget, so use fewer {which(lambda spent, part: spent > OVER * part)} this time'
               if expected > OVER * budget else
               f'under budget, so use more {which(lambda spent, part: spent < UNDER * part)} this time unless the '
               'topic needs zero Japanese or the user asked for none' if expected < UNDER * (words_part + whole_part)
               else 'on budget')
    return (f'\nYour last reply in this chat had {known + learned + off_list} Japanese words in {total} ({known} known, '
            f'{learned} learning, {off_list} off the lists) and {sentences} whole Japanese '
            f'sentence{"" if sentences == 1 else "s"}: about {expected:.2f} expected lookups per 100 words against a '
            f'budget of {round(budget, 2):g} (known words are free, a whole sentence costs {round(price, 3):g}): '
            f'{verdict}.')


def chat_turn(state, session, build, mark=None, now=None):
    """One turn of a chat with a session id, under the sessions.json lock: counts the turn and builds the brief with
    build(had) -> (text, what the chat has after it, plan) (see compose). had is None, so the brief is whole, on the chat's first turn, after a compaction
    since the last whole one (mark: the transcript's latest, see compaction; None: none seen, nothing new) and
    FULL_EVERY turns after it, for a context lost in a way the transcript doesn't show. Returns (text, plan)."""
    path = fwlib.home() / 'sessions.json'
    with fwlib.locked('sessions.lock'):
        sessions = fwlib.read_json(path, {})
        sessions = sessions if isinstance(sessions, dict) else {}
        entry = sessions.get(session)
        if not isinstance(entry, dict) or not isinstance(entry.get('n'), int):
            entry = sessions[session] = {'n': 0}
        entry.pop('recalled', None)  # the recall cue's memory, from before it went
        entry['n'] += 1
        entry['t'] = int(now or time.time())
        had = entry.get('brief') if isinstance(entry.get('brief'), dict) else {}
        whole = (not isinstance(had.get('at'), int) or not isinstance(had.get('known'), str)
                 or (mark is not None and mark != had.get('mark')) or entry['n'] - had['at'] >= FULL_EVERY)
        text, after, plan = build(None if whole else dict(had, known=[ja for ja in had['known'].split('\n') if ja]))
        entry['brief'] = dict(after, known='\n'.join(after['known']), at=entry['n'] if whole else had['at'],
                              mark=mark if mark is not None else had.get('mark'))  # one line per chat in the file
        when = lambda k: (sessions[k].get('t') if isinstance(sessions[k], dict)
                          and isinstance(sessions[k].get('t'), int) else 0)
        newest = sorted(sessions, key=when)
        sessions = {k: sessions[k] for k in newest[-200:]}  # keep the newest chats only
        for k in newest[:-MEMORY_KEPT]:
            if isinstance(sessions.get(k), dict):
                sessions[k].pop('brief', None)
        fwlib.write_json(path, sessions)
    return text, plan


def process_args(pid):
    """Command line of a process as a list (Linux /proc, else ps), and its parent pid."""
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as file:
            args = [a.decode('utf-8', 'replace') for a in file.read().split(b'\0') if a]
        with open(f'/proc/{pid}/stat') as file:
            parent = int(file.read().rsplit(')', 1)[1].split()[1])
        return args, parent
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(['ps', '-o', 'ppid=', '-o', 'args=', '-p', str(pid)], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        parent, _, args = out.partition(' ')
        return args.split(), int(parent)
    except (OSError, ValueError, subprocess.SubprocessError):
        return [], 0


def t3_host(pid=None, lookup=process_args):
    """True when T3 Code started the Claude Code process running this hook: `t3 serve`, or the desktop app's server
    (`t3code .../apps/server/dist/bin.mjs`). T3 runs every chat thread through the Agent SDK, so Claude Code reports
    itself as an SDK script there. An Agent SDK script you run yourself (even from a T3 terminal) has its own parent,
    so it stays silent."""
    pid = pid or os.getppid()
    for _ in range(4):
        args, parent = lookup(pid)
        if is_claude(args):
            host, _ = lookup(parent)
            return bool(host) and os.path.basename(host[0]) in ('t3', 't3code') and (
                'serve' in host[1:] or any(a.endswith('/apps/server/dist/bin.mjs') for a in host[1:]))
        if parent <= 1:
            break
        pid = parent
    return False


def codex_exec(pid=None):
    """True when a `codex exec` process starts this hook (directly or through a shell)."""
    pid = pid or os.getppid()
    for _ in range(3):
        args, parent = process_args(pid)
        if args and os.path.basename(args[0]).startswith('codex'):
            return 'exec' in args[1:] or 'e' in args[1:2]
        if parent <= 1:
            break
        pid = parent
    return False


def is_claude(args):
    """Claude Code's own process: `claude`, the native build under .../claude/versions/<version>, or node cli.js."""
    return bool(args) and (os.path.basename(args[0]) == 'claude' or '/claude/versions/' in args[0]
                           or any(os.path.basename(a) == 'cli.js' for a in args[:2]))


def claude_args(pid=None):
    """Command line of the Claude Code process that started this hook (directly or through a shell), or []."""
    pid = pid or os.getppid()
    for _ in range(3):
        args, parent = process_args(pid)
        if is_claude(args):
            return args
        if parent <= 1:
            break
        pid = parent
    return []


def one_shot(args):
    """True for a one-shot scripted `claude -p` run. Chat hosts also use --print (Remote Control, claude.ai/code,
    desktop and IDE hosts run `claude --print --sdk-url ... --input-format stream-json`), so those weave."""
    printing = any(a in ('-p', '--print') for a in args)
    streaming = any(a == '--sdk-url' or a.startswith('--sdk-url=') or a == '--input-format=stream-json' or
                    (a == '--input-format' and i + 1 < len(args) and args[i + 1] == 'stream-json')
                    for i, a in enumerate(args))
    return printing and not streaming


def interactive(env=None, data=None, exec_check=codex_exec, args_check=claude_args, host_check=t3_host):
    """Weave by default; stay silent only in clear one-shot scripted runs, whose output must never get Japanese.

    Claude Code: skip `claude -p` without --sdk-url / --input-format stream-json (see one_shot), and Agent SDK
    library scripts (CLAUDE_CODE_ENTRYPOINT sdk-ts or sdk-py) unless a chat app hosts them (T3 Code). Codex (its hook input has a turn_id): skip under
    `codex exec`; the Codex TUI runs hooks from its app-server instead. JAPANESE_WEAVE_FORCE=1 weaves anyway;
    JAPANESE_WEAVE_OFF=1 never weaves."""
    env = os.environ if env is None else env
    if env.get('JAPANESE_WEAVE_OFF') == '1':
        return False
    if env.get('JAPANESE_WEAVE_FORCE') == '1':
        return True
    if 'turn_id' in (data or {}):
        return not exec_check()
    if env.get('CLAUDE_CODE_ENTRYPOINT') in ('sdk-ts', 'sdk-py'):
        return host_check()
    return not one_shot(args_check())


def off_note(until, codex=False):
    """What the hook says instead of the brief while chat intensity is off (intensity.py), like threads.note."""
    how = ('pressing heavier (Alt+Shift+.) or saying "japanese back on"' if codex else
           'pressing heavier (Alt+Shift+.), saying "japanese back on" or running /japanese-weave:intensity normal')
    return (f'Japanese Weave: the user turned Japanese off in chat replies for now, until {intensity.clock(until)}, so '
            'reply in plain English, with no Japanese words or glosses. They can bring it back sooner by '
            f'{how}.')


def switch(session, prompt, thread):
    """This chat's switch after the user's prompt: a plain request to stop Japanese here, or to bring it back, or
    `/japanese-weave:intensity off here`, takes effect on this very turn and goes to the ops log (threads.py)."""
    asked = threads.request(prompt)
    if asked is None or asked[0] == threads.is_off(thread):
        return thread
    try:
        return hub.change_thread(session, asked[0], asked[1], wait=False)[0]
    except Exception:  # not recorded, but this turn still does what was asked
        return {'off': asked[0], 'at': intensity.now_ms(), 'source': asked[1]}


STALE_EXPORT_DAYS = 7


def export_notice(state, now=None):
    """Once per export: a nudge when a pasted claude.ai / ChatGPT block is over a week behind the word state."""
    exported = state.get('exported') or {}
    if not exported.get('at') or exported.get('noticed') or exported.get('version') == fwlib.version(state):
        return None
    now = now or time.time()
    if now - exported['at'] < STALE_EXPORT_DAYS * 86400:
        return None
    hub.submit({'kind': 'noticed'}, wait=False)
    return ('Japanese Weave: the block you pasted into claude.ai or ChatGPT is over a week behind your word list. '
            'Run /japanese-weave:export to refresh it.')


def main(argv):
    hook = '--hook' in argv
    data = {}
    if hook:
        try:
            data = json.load(sys.stdin)
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not interactive(data=data):
            return 0  # scripted run: no brief at all
    try:
        state = fwlib.load_state()
    except Exception as error:  # never block a prompt because of Japanese
        print(f'Japanese Weave: state unreadable ({error.__class__.__name__}); run /japanese-weave:status', file=sys.stderr)
        return 0
    if state.get('paused'):
        return 0
    session = data.get('session_id') if threads.valid_session(data.get('session_id')) else None
    try:
        live, thread = hub.live_chat(session, state)
    except Exception:
        live, thread = dict(intensity.NORMAL), None
    level = live['level']
    if session:
        thread = switch(session, data.get('prompt'), thread)
    if hook and intensity.is_off(level) and (threads.request(data.get('prompt')) or (True,))[0] is False:
        try:  # "japanese back on" also ends an off spell in all chats, on this very turn
            live = hub.set_intensity('chat', 0, 'chat')[0]
        except Exception:
            live = dict(intensity.NORMAL)
        level = live['level']
    codex = 'turn_id' in data
    # Japanese off in this chat (at any level), or in all chats for now: one line instead of the brief and its extras
    note =(threads.note(codex=codex) if threads.is_off(thread) else
            off_note(live['until'], codex) if intensity.is_off(level) else None)
    if note:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': note}},
                         ensure_ascii=False))
        return 0
    markup = hook and abbr_glosses(state)
    seen, know = exposure(), knowledge.load()
    reply = mark = None
    if hook:
        try:
            lines = tail_lines(data['transcript_path'])
            reply, mark = last_reply(None, lines), compaction(lines)
        except Exception:
            pass  # no transcript yet or unreadable: the brief alone still helps
    try:
        tail = calibration(reply, state, level, know, seen) if reply else ''
    except Exception:
        tail = ''
    build = lambda had: compose(state, seen, markup=markup, know=know, level=level, had=had, tail=tail)
    try:
        if hook and session:
            text, plan = chat_turn(state, session, build, mark)
        else:
            text, _, plan = build(None)
    except Exception:  # the chat's memory unreadable or unwritable: the whole brief, as on a first turn
        try:
            text, _, plan = build(None)
        except Exception as error:  # never block a prompt because of Japanese
            print(f'Japanese Weave: no brief ({error.__class__.__name__})', file=sys.stderr)
            return 0
    text += tail
    if not hook:
        print(text)
        return 0
    out = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}
    try:
        notice = export_notice(state)
    except Exception:
        notice = None
    if notice:
        out['systemMessage'] = notice  # shown to the user, outside the brief
    print(json.dumps(out, ensure_ascii=False), flush=True)
    if session:
        keep_plan(plan, session, len(text), data.get('turn_id'))
    return 0


def keep_plan(plan, session, chars, turn=None):
    """Keeps what this reply was offered as a brief op (hub.keep): the plan (compose), the chat's session id, the
    brief's length and, from Codex, the turn id. Never waits long and never fails the hook: a lost record only
    leaves a gap in the log."""
    try:
        record = dict(plan, kind='brief', session=session, chars=chars)
        if threads.valid_session(turn):
            record['turn'] = turn
        hub.keep(record)
    except Exception as error:
        print(f'Japanese Weave: brief not recorded ({error.__class__.__name__})', file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
