#!/usr/bin/env python3
"""UserPromptSubmit hook: prints a short Japanese Weave brief as additional context for the next reply.

Usage: weave.py --hook   (reads the hook JSON on stdin, prints hook JSON)
       weave.py          (prints the plain brief, for checking)
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwlib  # noqa: E402
import hub  # noqa: E402
import knowledge  # noqa: E402

STALE = 30 * 60  # refresh chat exposure in the background when the index is older than this
SYNC_EVERY = 120  # with a collector: sync the cache in the background when it is older than this
# Stage: (target % of words in Japanese, cap %, max Japanese words in one sentence).
STAGES = {1: (6, 10, 2), 2: (12, 20, 4), 3: (25, 35, 4), 4: (40, 55, 4)}
LOT, SOME = (6, 30), (3, 10)  # chat exposure (distinct days, replies) for the "seen a lot" and "sometimes" tiers
TAIL = 2_000_000  # bytes at the end of a transcript searched for the last reply
SHORT = 40  # replies with fewer prose words are too short to measure
RECALL_EVERY = 3
# Most known words listed per tier, so the brief stays small at Stages 3-4 (hundreds of known words).
LIMITS = {'rare': 24, 'some': 60, 'lot': 80}
DRIFT = 4  # Japanese words off the lists (particles, verbs, です) in one reply: below Stage 3 it slipped into Japanese
EXTRA = re.compile(r'\s*(?:\**recall:|.*\s=\s.*")', re.I)  # recall cue and footer gloss lines
ABBR = re.compile(r'<abbr\b[^>]*>(.*?)</abbr>', re.I | re.S)  # T3 gloss markup: count the word, not its title


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


def brief(state, seen, recall=None, markup=False, know=None):
    """The per-turn instructions. markup: the chat renders <abbr> titles on hover or click (see abbr_glosses), so each
    Japanese word carries its own gloss instead of a footer line. know: knowledge.json (adaptive difficulty), which
    orders learning words by learning value, most useful first."""
    stage = fwlib.stage_for(state)
    target, cap, per_sentence = STAGES[min(stage, 4)]
    entry = lambda w: w['ja'] if w['status'] == 'known' else f'{w["ja"]} = {w["en"]}' + (f', "{w["hint"]}"' if stage <= 2 else '')
    def usefulness(w):
        if not know or w['status'] == 'known':
            return 0
        estimate = knowledge.word_estimate(know, w['ja'], 't3')
        return -knowledge.learning_value(estimate[0] if estimate else 0.5)
    words = sorted(fwlib.introduced(state),
                   key=lambda w: (usefulness(w), w['status'] != 'shaky', -seen.get(w['ja'], {}).get('n', 0)))
    swap = lambda w: f'{w["en"]} → {w["ja"]}'
    def group(name, show=entry):
        picked = [w for w in words if tier(w, seen) == name]
        if name == 'rare':  # least exposure first, so fading words get pushed back in
            picked.sort(key=lambda w: seen.get(w['ja'], {}).get('n', 0))
        return '; '.join(show(w) for w in picked[:LIMITS.get(name)]) or 'none'
    banded = sum(w['status'] == 'known' for w in state['words']) >= fwlib.COLLAPSE_AT
    grammar = ('Every sentence stays an English sentence with Japanese words swapped in, like "The build passes 今, でも '
               'the cache is stale, だから I cleared it." Write each word exactly as listed (same kanji and kana), never '
               'conjugated, and with no particles, です/ます or other Japanese grammar around it; adjectives go before their '
               'noun as listed ("a 新しい build", "the 簡単 fix"); suru-nouns stand in for the English verb or noun ("let me '
               '確認 the logs"). No Japanese outside these lists and no katakana loanwords; if a sentence starts turning '
               'Japanese, rewrite it in English. '
               if stage < 3 else
               f'About {"one sentence in five" if stage == 3 else "two sentences in five"} should be fully Japanese '
               f'({"2" if stage == 3 else "4"} in a ten-sentence reply): short, plain です/ます sentences, guessable from the '
               'English around them, like "問題は設定です。" or "これで大丈夫です。" Steps stay English, so put the Japanese '
               'sentences in the explanation, summary and advice around them. The other sentences stay English with listed '
               'words swapped in as written, without particles. In Japanese sentences use any common word the user would '
               'follow, not only the lists. ')
    text = (f'Japanese Weave (Stage {stage}): the user is a native English speaker learning Japanese passively. Write your '
            f'chat reply in English and swap in Japanese words from the lists below: about {target}% of the words in a reply '
            f'(about {round(target * 1.5)} in a 150-word reply{", so most sentences carry one or two" if stage <= 2 else ""}), '
            f'never over {cap}%, at most {per_sentence} in one sentence. Under target is the usual miss, so count as you write. '
            'Put a space on each side of a Japanese word inside English text, as for an English word. '
            f'{grammar}Use a word only where it means exactly what the English word would mean there; where one doesn\'t '
            'fit, use another from the lists instead (だから only for "so" meaning therefore, never "so big"; でも and しかし '
            'only for "but" joining two ideas; もう for "already", まだ for "still" or "not yet"; 前 and 後 only before or '
            'after a noun or a time, never joining clauses; はい and いいえ only as answers). If a reply runs long on '
            f'Japanese, cut {"known" if banded else "seen-a-lot"} words first. Plain English for code, '
            'commands, paths, numbers, names, IDs, option letters and steps the user will follow, and no Japanese right before '
            'a path; zero Japanese, and no recall cue, for health, medication, legal, money, safety, emergency, driving or '
            'urgent topics. Japanese goes only in your chat reply: never in files, code, configs, commit or PR text, messages '
            'to other agents, subagent prompts, or drafts and documents meant to be shared. '
            + ('No bold on Japanese words and no glosses, readings or romaji in the text itself. Wrap each Japanese '
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
            + f'Learning now (use 2 to 4 per reply): {group("learn")}\n'
            f'Rarely seen (pepper in a couple per reply where the sentence makes the meaning obvious): {group("rare")}\n'
            + (band_line(state) if banded else
               f'Seen sometimes (about 2 in 3 places one fits): {group("some")}\n'
               f'Seen a lot, as default swaps (write the Japanese every time the English word has that meaning): {group("lot", swap)}'))
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


def band_line(state):
    n, missing, extra = fwlib.known_band(state)
    swaps = [w for w in state['words'] if w['status'] == 'known'][:DEFAULT_SWAPS]
    defaults = ('Default swaps (write the Japanese every time the English word has that meaning): '
                + '; '.join(f'{w["en"]} → {w["ja"]}' for w in swaps) + '\n') if swaps else ''
    cut = lambda xs: ', '.join(xs[:fwlib.EXCEPTIONS_MAX]) + (f' and {len(xs) - fwlib.EXCEPTIONS_MAX} more' if len(xs) > fwlib.EXCEPTIONS_MAX else '')
    return defaults + (f'Known (use freely, never gloss): roughly the {n} most common Japanese words and phrases'
            + (f', except these not learned yet: {cut(missing)}' if missing else '')
            + (f'; also known: {cut(extra)}' if extra else '') + '.')


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


def share(text, words):
    """(Japanese words, prose words, learning words used, Japanese words off the lists) in a reply.

    Code, links, glosses and the recall cue are skipped. Japanese has no spaces, so off-list Japanese is counted as
    one word per two characters."""
    prose = fwlib.norm(fwlib.CODE.sub(' ', ABBR.sub(r'\1', text)))
    prose = '\n'.join(line for line in prose.split('\n') if not EXTRA.match(line))
    listed = learning = 0
    rest = prose
    # Longest first, each match removed before the next search: 一緒に is not also 一緒.
    for word in sorted(words, key=lambda w: -len(w['ja'])):
        regex = fwlib.pattern(word['ja'], word.get('kana', ''))
        uses = len(regex.findall(rest))
        listed += uses
        learning += uses if word['status'] != 'known' else 0
        rest = regex.sub(' ', rest)
    off_list = len(fwlib.JAPANESE.findall(rest)) // 2
    english = len(re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", fwlib.JAPANESE.sub(' ', rest)))
    return listed + off_list, english + listed + off_list, learning, off_list


def calibration(text, state):
    """One line on how much Japanese the last reply in this chat had, so the next one corrects toward the target."""
    japanese, total, learning, off_list = share(text, fwlib.introduced(state))
    if total < SHORT:
        return ''
    stage = fwlib.stage_for(state)
    target, cap, _ = STAGES[min(stage, 4)]
    percent = 100 * japanese / total
    verdict = ('it slipped into Japanese sentences, so this time write English sentences with only list words in Japanese'
               if off_list >= DRIFT and stage < 3 else f'over the {cap}% cap, so use less Japanese this time' if percent > cap else
               f'under the {target}% target, so use more this time unless the topic needs zero Japanese'
               if percent < 0.7 * target else 'on target')
    return (f'\nYour last reply in this chat was {percent:.0f}% Japanese ({japanese} of {total} words, {learning} '
            f'learning word{"" if learning == 1 else "s"}): {verdict}.')


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
    markup = hook and abbr_glosses(state)
    text = brief(state, exposure(), next_recall(state, data.get('session_id')) if hook and not markup else None,
                 markup=markup, know=knowledge.load())
    if hook:
        try:
            reply = last_reply(data['transcript_path'])
            text += calibration(reply, state) if reply else ''
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
