#!/usr/bin/env python3
"""UserPromptSubmit hook: prints a short Japanese Weave brief as additional context for the next reply.

Usage: weave.py --hook   (reads the hook JSON on stdin, prints hook JSON)
       weave.py          (prints the plain brief, for checking)
"""
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
SENTENCES = {3: 2, 4: 4}  # fully Japanese sentences in a ten-sentence reply, from Stage 3
IN_TEN = {1: 'one sentence in ten', 2: 'one sentence in five', 3: 'three sentences in ten', 4: 'two sentences in five',
          5: 'half the sentences', 6: 'three sentences in five'}
LOT, SOME = (6, 30), (3, 10)  # chat exposure (distinct days, replies) for the "seen a lot" and "sometimes" tiers
TAIL = 2_000_000  # bytes at the end of a transcript searched for the last reply
SHORT = 40  # replies with fewer prose words are too short to measure
RECALL_EVERY = 3
# Most known words listed per tier at full, so the brief stays small at Stages 3-4 (hundreds of known words).
LIMITS = {'rare': 24, 'some': 60, 'lot': 80}
# Below full: words known at a glance listed (until the band takes over), the first ones as swaps; most learning words
# listed, best learning value first.
KNOWN_SWAPS, KNOWN_MAX, LEARN_MAX = 40, 150, 24
# Learning words asked for per 100 words: the fewest and the most, and the most per word allowed in a sentence (about
# five prose sentences in 100 words).
PER_100, PER_SENTENCE_100 = (0.5, 8), 5
# Learning words by lookup chance: the least chance in each group.
TIERS = ((0.2, 'often'), (0.12, 'sometimes'), (0.0, 'rarely'))
OVER, UNDER = 1.5, 0.5  # the feedback line: expected lookups past this many times the budget are over or under it
EXTRA = re.compile(r'\s*(?:\**recall:|.*\s=\s.*")', re.I)  # recall cue and footer gloss lines
ABBR = re.compile(r'<abbr\b[^>]*>(.*?)</abbr>', re.I | re.S)  # T3 gloss markup: count the word, not its title
# Japanese left over once the listed words are taken out, counted as off-list words: each run of kanji or katakana,
# and below Stage 3 hiragana too, one word per two characters. From Stage 3 the hiragana is the particles and です/ます
# that Japanese sentences put around listed words.
WORDLIKE = re.compile(f'[{fwlib.KANJI}]+|[\u30a1-\u30fa][\u30a1-\u30fa\u30fc]*|[\uff66-\uff9d][\uff66-\uff9f]*')
HIRAGANA = re.compile('[\u3041-\u309f]')


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


def japanese_sentences(stage, level=0):
    """Fully Japanese sentences in a ten-sentence reply at a stage (from Stage 3), scaled by the reader's intensity
    (intensity.py: lighter or heavier for a while)."""
    sentences = SENTENCES.get(min(stage, 4), 0)
    f = intensity.factor(level)
    return max(1, min(6, round(sentences * f))) if sentences and f != 1 else sentences


def chance(word, know=None):
    """The chance the user looks a word up in chat: its estimate (knowledge.json, T3 offset) when the gloss log has
    one, else from its status, known words at fwlib.KNOWN_PRIOR_K and the rest at the neutral prior."""
    know = know or {}
    estimate = knowledge.word_estimate(know, word['ja'], 't3')
    if estimate:
        return estimate[0]
    offset = (know.get('offset') or {}).get('t3', 0.0)
    return knowledge.sigmoid(offset - (fwlib.KNOWN_PRIOR_K if word['status'] == 'known' else knowledge.prior(None)))


def lists(state, seen=None, know=None):
    """The word lists below full: (known at a glance, in lexicon order; learning, best learning value first, at most
    LEARN_MAX; {ja: lookup chance} for every word in use). Known at a glance means a lookup chance under fwlib.FREE_P,
    whatever the status; every other word in use is learning, known ones too."""
    seen = seen or {}
    p = {w['ja']: chance(w, know) for w in fwlib.introduced(state)}
    free = [w for w in state['words'] if w['ja'] in p and p[w['ja']] < fwlib.FREE_P]
    learning = sorted((w for w in fwlib.introduced(state) if p[w['ja']] >= fwlib.FREE_P),
                      key=lambda w: (-knowledge.learning_value(p[w['ja']]), w['status'] != 'shaky',
                                     -seen.get(w['ja'], {}).get('n', 0)))
    return free, learning[:LEARN_MAX], p


def lookup_budget(state, level=0):
    """Lookups per 100 words the level allows for words not known at a glance (intensity.budget); the challenge, when
    the user set one, is normal's."""
    return intensity.budget(level, knowledge.challenge_of(state) or None)


def half_up(x):
    return math.floor(x + 0.5)


def most_per_100(level):
    """The most learning words per 100 words a level asks for: as many as fit, intensity.per_sentence in a sentence."""
    return min(PER_100[1], PER_SENTENCE_100 * intensity.per_sentence(level))


def per_100(budget, chances, most=PER_100[1]):
    """Learning words per 100 words that spend about the budget: budget / their mean lookup chance, to the nearest half
    under 2 and the nearest whole number from there, from PER_100's fewest to most. None without learning words."""
    if not chances:
        return None
    n = budget * len(chances) / sum(chances)
    n = half_up(2 * n) / 2 if n < 2 else half_up(n)
    return max(PER_100[0], min(most, n))


def tier(word, seen):
    """'learn' for active and shaky words; known words by chat exposure: 'lot', 'some' or 'rare' (uncountable: 'some')."""
    if word['status'] != 'known':
        return 'learn'
    count = seen.get(word['ja'])
    if count is None:
        return 'some'
    days, replies = len(count['days']), count['n']
    if days >= LOT[0] and replies >= LOT[1]:
        return 'lot'
    return 'some' if days >= SOME[0] and replies >= SOME[1] else 'rare'


def brief(state, seen, recall=None, markup=False, know=None, level=0):
    """The per-turn instructions. markup: the chat renders <abbr> titles on hover or click (see abbr_glosses), so each
    Japanese word carries its own gloss instead of a footer line. know: knowledge.json (adaptive difficulty): each
    word's lookup chance (chance), which splits the words in use into known at a glance and learning and orders the
    learning ones by learning value, most useful first. level: the chat intensity in effect (intensity.py). Below full
    it is a lookup budget: known-at-a-glance words go in wherever they fit, and the learning words asked for are as
    many as spend about the budget (per_100). At full there is no budget: every sentence that can be plain Japanese
    is, and the rest swap in every listed word."""
    stage = fwlib.stage_for(state)
    full = intensity.is_full(level)
    as_listed = ('Write each word exactly as listed (same kanji and kana), never conjugated, and with no particles, '
                 'です/ます or other Japanese grammar around it; adjectives go before their noun as listed ("a 新しい '
                 'build", "the 簡単 fix"); suru-nouns stand in for the English verb or noun ("let me 確認 the logs"). ')
    like = 'like "問題は設定です。" or "これで大丈夫です。"'
    space = 'Put a space on each side of a Japanese word inside English text, as for an English word. '
    fit = lambda instead: ('Use a word only where it means exactly what the English word would mean there; where one '
                           f'doesn\'t fit, {instead} (だから only for "so" meaning therefore, never "so big"; でも and '
                           'しかし only for "but" joining two ideas; もう for "already", まだ for "still" or "not yet"; 前 '
                           'and 後 only before or after a noun or a time, never joining clauses; はい and いいえ only as '
                           'answers). ')
    plain = ('Plain English for code, commands, paths, numbers, names, IDs'
             + (' and option letters' if full else ', option letters and steps the user will follow')
             + ', and no Japanese right before a path; zero Japanese, and no recall cue, for health, medication, '
             'legal, money, safety, emergency, driving or urgent topics. Japanese goes only in your chat reply: never '
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
               'footer.\n'
               if markup else
               'No bold on Japanese words and no inline glosses or romaji. End the reply with one footer line glossing the '
               'learning words you used that are not glossed earlier in this chat, at most 8, with the romaji, like: '
               '(多分 = maybe, "tabun"); nothing to gloss, no footer.\n')
    if full:
        entry = lambda w: w['ja'] if w['status'] == 'known' else f'{w["ja"]} = {w["en"]}' + (f', "{w["hint"]}"' if stage <= 2 else '')
        def usefulness(w):
            if not know or w['status'] == 'known':
                return 0
            estimate = knowledge.word_estimate(know, w['ja'], 't3')
            return -knowledge.learning_value(estimate[0] if estimate else 0.5)
        words = sorted(fwlib.introduced(state),
                       key=lambda w: (usefulness(w), w['status'] != 'shaky', -seen.get(w['ja'], {}).get('n', 0)))
        def picked(name):
            found = [w for w in words if tier(w, seen) == name]
            if name == 'rare':  # least exposure first, so fading words get pushed back in
                found.sort(key=lambda w: seen.get(w['ja'], {}).get('n', 0))
            return found[:LIMITS.get(name)]
        group = lambda name, show=entry: '; '.join(show(w) for w in picked(name)) or 'none'
        banded = sum(w['status'] == 'known' for w in state['words']) >= fwlib.COLLAPSE_AT
        known = '; '.join(f'{w["en"]} → {w["ja"]}' for name in ('rare', 'some', 'lot') for w in picked(name)) or 'none'
        text = (f'Japanese Weave (Stage {stage}, turned to full for now by the user): the user is a native English '
                'speaker learning Japanese passively and wants, for now, all the Japanese they can follow, with no '
                'percentage target or cap. Write every sentence of your chat reply that can be written fully in plain '
                'Japanese the user would follow (the words in the lists below plus common grammar: particles, '
                f'demonstratives, verb endings and plain desu/masu forms) fully in Japanese, {like} Sentences that '
                'must stay English (steps the user will follow, code, commands, paths, exact technical detail, or '
                'anything that needs a Japanese word not in the lists) stay English sentences, with the Japanese '
                'swapped in for every English word that has a word in the lists, known or learning. '
                f'{as_listed}No other Japanese words and no katakana loanwords. Too little Japanese is the usual miss, '
                'so check each sentence as you write it: fully Japanese if it can be, otherwise every listed word '
                'swapped in. '
                + space + fit('keep the English word') + plain + glosses
                + f'Learning now (use each one wherever it fits): {group("learn")}\n'
                + (band_line(state) if banded else
                   f'Known (write the Japanese every time the English word has that meaning): {known}'))
    else:
        free, learning, p = lists(state, seen, know)
        sentences = japanese_sentences(stage, level)
        grammar = ('Every sentence stays an English sentence with Japanese words swapped in, like "The build passes 今, '
                   f'でも the cache is stale, だから I cleared it." {as_listed}No Japanese outside these lists and no '
                   'katakana loanwords; if a sentence starts turning Japanese, rewrite it in English. '
                   if stage < 3 else
                   f'About {IN_TEN[sentences]} should be fully Japanese '
                   f'({sentences} in a ten-sentence reply): short, plain です/ます sentences of known words, particles '
                   f'and これ/それ only, guessable from the English around them, {like} Steps stay English, so put the '
                   'Japanese sentences in the explanation, summary and advice around them. The other sentences stay '
                   'English with listed words swapped in as written, without particles. No other Japanese anywhere, '
                   'not even a common word, and no katakana loanwords; a sentence needing one stays English. ')
        n = per_100(lookup_budget(state, level), [p[w['ja']] for w in learning], most_per_100(level))
        most = intensity.per_sentence(level)
        ask = (f'Learning words cost a lookup now and then: about {n:g} per 100 words ({half_up(3 * n)} in a 300-word '
               f'reply), at most {"one" if most == 1 else most} in a sentence, each where the English around it makes '
               'its meaning clear, spread out. ' if n else 'No learning words right now, only known ones. ')
        turned = f', turned {intensity.NAMES[level]} for now by the user' if level else ''
        text = (f'Japanese Weave (Stage {stage}{turned}): the user is a native English speaker learning Japanese '
                'passively. Write your chat reply in English and swap in Japanese words from the lists below. Known '
                'words cost the user nothing, so write each one in Japanese wherever the English has its meaning. '
                f'{ask}Leaving a known word in English is the usual miss, so check each sentence as you write '
                f'it. {space}{grammar}{fit("keep the English word")}'
                + plain + glosses
                + 'Learning now, by how often the user looks one up: '
                + learning_groups(learning, p, stage) + '\n'
                + (glance_band(state, free, learning) if len(free) >= fwlib.COLLAPSE_AT else glance_list(free)))
    if recall and not markup:  # with popover glosses every word already tests recall, so no extra lines
        text += (f'\nIf this reply is longer than a few sentences, open it with "Recall: {recall["ja"]}?" on its own line '
                 f'and end it, after the footer, with: Recall: {recall["ja"]} = {recall["en"]}, "{recall["hint"]}"')
    return text


def abbr_glosses(state, host_check=None):
    """True when Japanese words should carry <abbr> glosses: the user opted in (`fw.py glosses abbr`, for a T3 Code build
    that shows <abbr> titles) and T3 Code hosts this chat. Terminals and upstream T3 would lose the gloss."""
    if (state.get('prefs') or {}).get('chat_glosses') != 'abbr':
        return False
    return (host_check or t3_host)()


DEFAULT_SWAPS = 12  # in band mode: the top-ranked known words (connectors and adverbs) written in Japanese every time


def cut(words):
    return ', '.join(words[:fwlib.EXCEPTIONS_MAX]) + (f' and {len(words) - fwlib.EXCEPTIONS_MAX} more'
                                                     if len(words) > fwlib.EXCEPTIONS_MAX else '')


def default_swaps(words, head='Default swaps (write the Japanese every time the English word has that meaning)'):
    return (f'{head}: ' + '; '.join(f'{w["en"]} → {w["ja"]}' for w in words[:DEFAULT_SWAPS]) + '\n') if words else ''


def band_line(state):
    """The known words at full, as a band (fwlib.known_band)."""
    n, missing, extra = fwlib.known_band(state)
    return default_swaps([w for w in state['words'] if w['status'] == 'known']) + (
        f'Known (use freely, never gloss): roughly the {n} most common Japanese words and phrases'
        + (f', except these not learned yet: {cut(missing)}' if missing else '')
        + (f'; also known: {cut(extra)}' if extra else '') + '.')


def glance_band(state, free, learning):
    """Below full, from fwlib.COLLAPSE_AT words known at a glance: those words as a band. Every word in the band that
    isn't one of them (not introduced yet, or looked up too often) and isn't listed as learning is named."""
    n, missing, extra = fwlib.known_band(state, {w['ja'] for w in free}, {w['ja'] for w in learning})
    return default_swaps(free, 'Default swaps') + (
        'Known (if unsure a word is one of them, keep the English): roughly the '
        f'{n} most common Japanese words and phrases'
        + (f', except these, not known at a glance yet: {cut(missing)}' if missing else '')
        + (f'; also known: {cut(extra)}' if extra else '') + '.')


def glance_list(free):
    """Below full, under fwlib.COLLAPSE_AT words known at a glance: the most common as swaps, then more by name."""
    rest = free[KNOWN_SWAPS:KNOWN_MAX]
    return ('Known (write the Japanese every time the English word has that meaning): '
            + ('; '.join(f'{w["en"]} → {w["ja"]}' for w in free[:KNOWN_SWAPS]) or 'none')
            + (f'; also {", ".join(w["ja"] for w in rest)}' if rest else ''))


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


def last_reply(path):
    """Text of the latest main-thread assistant reply in a Claude Code transcript, or None."""
    with open(path, 'rb') as file:
        size = file.seek(0, 2)
        file.seek(max(0, size - TAIL))
        if size > TAIL:
            file.readline()  # skip the cut line
        lines = [raw.decode('utf-8', 'replace') for raw in file]
    texts = [text for _, text in fwlib.replies(lines) if text.strip()]
    return texts[-1] if texts else None


def uses(text, words):
    """({ja: uses} for the words a reply uses, the prose left once they are taken out). Code, links, glosses and the
    recall cue are skipped. Longest first, each match removed before the next search: 一緒に is not also 一緒."""
    prose = fwlib.norm(fwlib.CODE.sub(' ', ABBR.sub(r'\1', text)))
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


def calibration(text, state, level=0, know=None, seen=None):
    """One line on the last reply in this chat: its Japanese words (known at a glance, learning, off the lists) and the
    lookups they would cost per 100 words against the level's budget, so the next reply corrects toward it. Known
    words are free; an off-list word costs a word's neutral prior. None at full (no budget) or off, or for a short
    reply. know and seen as for brief, so the lists are the brief's."""
    if intensity.is_full(level) or intensity.is_off(level):
        return ''
    stage = fwlib.stage_for(state)
    found, rest = uses(text, fwlib.introduced(state))
    stray = len(WORDLIKE.findall(rest)) + (len(HIRAGANA.findall(rest)) // 2 if stage < 3 else 0)
    total = english_words(rest) + sum(found.values()) + max(stray, len(fwlib.JAPANESE.findall(rest)) // 2)
    if total < SHORT:
        return ''
    free, learning, p = lists(state, seen, know)
    free, learning = {w['ja'] for w in free}, {w['ja'] for w in learning}
    known = sum(n for ja, n in found.items() if ja in free)
    learned = sum(n for ja, n in found.items() if ja in learning)
    off_list = sum(n for ja, n in found.items() if ja not in free and ja not in learning) + stray
    spent = (sum(p[ja] * n for ja, n in found.items() if ja not in free)
             + stray * knowledge.sigmoid(-knowledge.prior(None)))
    expected, budget = 100 * spent / total, lookup_budget(state, level)
    # the most the brief asks for may spend less than the budget (most_per_100): "under" is under what it asks
    reach = most_per_100(level) * sum(p[ja] for ja in learning) / len(learning) if learning else 0
    verdict = ('it had Japanese outside the lists, so this time use no Japanese outside the lists' if off_list else
               'over budget, so use fewer learning words this time' if expected > OVER * budget else
               'under budget, so use more learning words this time unless the topic needs zero Japanese or the user '
               'asked for none' if expected < UNDER * min(budget, reach) else 'on budget')
    return (f'\nYour last reply in this chat had {known + learned + off_list} Japanese words in {total} ({known} known, '
            f'{learned} learning, {off_list} off the lists): about {expected:.2f} expected lookups per 100 words against '
            f'a budget of {round(budget, 2):g} (known words are free): {verdict}.')


def next_recall(state, session):
    """Every third turn in a chat (1st, 4th, ...): the recall word, shaky first, then active, not yet used here."""
    if not session:
        return None
    path = fwlib.home() / 'sessions.json'
    with fwlib.locked('sessions.lock'):
        sessions = fwlib.read_json(path, {}) or {}
        entry = sessions.setdefault(session, {'n': 0, 'recalled': []})
        entry['n'] += 1
        entry['t'] = int(time.time())
        pick = None
        if (entry['n'] - 1) % RECALL_EVERY == 0:
            pool = [w for w in fwlib.introduced(state) if w['status'] in ('shaky', 'active')]
            pick = next((w for w in pool if w['ja'] not in entry['recalled']), None)
            if pick:
                entry['recalled'].append(pick['ja'])
        if len(sessions) > 200:  # keep the newest chats only
            sessions = dict(sorted(sessions.items(), key=lambda kv: kv[1].get('t', 0))[-200:])
        fwlib.write_json(path, sessions)
    return pick


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
            'reply in plain English, with no Japanese words, glosses or recall lines. They can bring it back sooner by '
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
    text = brief(state, seen, next_recall(state, data.get('session_id')) if hook and not markup else None,
                 markup=markup, know=know, level=level)
    if hook:
        try:
            reply = last_reply(data['transcript_path'])
            text += calibration(reply, state, level, know, seen) if reply else ''
        except Exception:
            pass  # no transcript yet or unreadable: the brief alone still helps
        out = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}
        try:
            notice = export_notice(state)
        except Exception:
            notice = None
        if notice:
            out['systemMessage'] = notice  # shown to the user, outside the brief
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
