"""Backtest (fw.py backtest; ETHOS.md "Measure it"): replays the chat replies woven so far through the current weave
and check, on copies of the word state, and reports how much Japanese each would show, what the check dropped and put
back, and how that compares with the replies as they were sent. Python 3.9+, standard library only.

Where the replies come from: the chat transcripts (fwlib.transcript_roots), each `fw.py weave` call with its draft on
stdin and the `fw.py check-reply` calls after it in the same transcript, the last of which is the reply as written.
Drafts written more than once (a resumed chat copies its transcript) count once.

What runs: each draft through `fw.py weave`, and each reply written with <ja> twins through `fw.py check-reply`, both
at the levels asked for (default: the ones in force now), in a temporary state dir per worker holding copies of
state.json, knowledge.json and the weave's dictionary pool, never a collector: nothing live is read after the copy or
written at all. Replies from before twins (no <ja>) only have their draft woven: today's check stops on them.

What it reports, per 100 English words of the drafts (code left out): the weave's swaps offered in the text, the share
marked unsure, the words for twins only; and on the replies with twins, as sent and now: word swaps at full and at
normal, twins and twins at the sentence level, twins dropped and why, swaps put back, and what the check mended.
--out FILE keeps every reply's numbers and the totals as JSON, and --against FILE prints the totals of an earlier run
beside these, so a change to the brief, the weave or the check shows its effect before it ships; --fw PATH replays
through another checkout's scripts/fw.py (main, say) to make that earlier run.
"""
import collections
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import fwlib

FW = Path(__file__).resolve().parent / 'fw.py'
COPIED = ('state.json', 'knowledge.json', 'weave-pool.json')
CALL = re.compile(r'fw\.py"?\s+(weave|check-reply)\b[^\n<]*<<\s*[\'"]?(\w+)[\'"]?\n(.*)\n\2\s*$', re.S)
CODE = re.compile(r'```.*?```|`[^`\n]*`', re.S)
LATIN = re.compile(r"[A-Za-z]+")
TIMEOUT = 180


def _text(content):
    if isinstance(content, list):
        return '\n'.join(c.get('text', '') for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ''


def calls(path):
    """[(kind, body, output, timestamp)] of a transcript's weave and check-reply calls, in order."""
    found, by_id = [], {}
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return []
    for line in lines:
        if 'fw.py' not in line and 'tool_result' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get('message') or {}).get('content')
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_use':
                m = CALL.search(str((c.get('input') or {}).get('command', '')))
                if m:
                    item = [m.group(1), m.group(3), None, entry.get('timestamp') or '']
                    by_id[c.get('id')] = item
                    found.append(item)
            elif c.get('type') == 'tool_result' and c.get('tool_use_id') in by_id:
                by_id[c['tool_use_id']][2] = _text(c.get('content'))
    return [tuple(x) for x in found]


def replies(roots=None, since=''):
    """[{draft, at, reply, sent}] from the transcripts: each weave's draft with the last check-reply input after it
    (reply) and that check's output (sent), once per draft, oldest first. since: only those at or after it."""
    out = {}
    for root in roots or fwlib.transcript_roots():
        for path in sorted(Path(root).expanduser().glob('**/*.jsonl')) if Path(root).expanduser().exists() else ():
            current = None
            for kind, body, output, at in calls(path):
                if kind == 'weave':
                    key = hashlib.sha1(body.encode()).hexdigest()[:16]
                    current = out.setdefault(key, {'id': key, 'draft': body, 'at': at, 'reply': None, 'sent': None})
                elif current is not None:
                    current['reply'], current['sent'] = body, output
    return sorted((r for r in out.values() if (r['at'] or '') >= since), key=lambda r: r['at'] or '')


def english_words(draft):
    return len(LATIN.findall(CODE.sub(' ', draft)))


def parse_check(output):
    """The numbers of one check-reply output, or None when it printed no final markup."""
    if not output or '\n=====\n' not in output:
        return None
    head, final = output.split('\n=====\n', 1)
    english = re.sub(r'<span data-jw="ja".*?</span>', '', final, flags=re.S)
    levels = [int(x) for x in re.findall(r'<abbr title="[^"]*"[^>]*?data-on="(-?\d)"', english)]
    twins = [int(x) for x in re.findall(r'data-jw="ja" data-on="(-?\d)"', final)]
    reasons = collections.Counter()
    for line in head.splitlines():
        m = re.match(r'- line \d+, twin dropped[^:]*: (.*)', line)
        if m:
            reasons[re.split(r'[:(".]', m.group(1))[0].strip()[:60] or 'other'] += 1
    back = re.search(r'(\d+) put back\b', head)
    return {'words_full': len(levels), 'words_normal': sum(1 for x in levels if x <= 0), 'twins': len(twins),
            'twins_normal': sum(1 for x in twins if x <= 0), 'dropped': sum(reasons.values()),
            'put_back': int(back.group(1)) if back else 0,
            'mended': sum(1 for line in head.splitlines() if line.startswith('- mended:')), 'reasons': dict(reasons)}


def parse_weave(output):
    """The numbers of one weave output: swaps in the text, unsure ones, words for twins only, twins asked for."""
    if not output or '\n-----\n' not in output:
        return None
    head, text = output.split('\n-----\n', 1)
    asked = re.search(r'write (\d+) twins?', head) or re.search(r'about (\d+) \(where', head)
    others = re.search(r'For twins only[^:]*: (.*?)\. Twins', head)
    return {'offered': len(re.findall(r'<abbr ', text)), 'unsure': len(re.findall(r'\{\{[^}]*→<abbr', text)),
            'twin_only': len(others.group(1).split('; ')) if others else 0, 'asked': int(asked.group(1)) if asked else 0}


def _home(source, into):
    into.mkdir(parents=True, exist_ok=True)
    for name in COPIED:
        if (source / name).exists():
            shutil.copy2(source / name, into / name)
    return into


def _fw(home, args, text, fw=FW):
    env = dict(os.environ, JAPANESE_WEAVE_HOME=str(home), CLAUDE_CODE_SESSION_ID=f'backtest-{os.getpid()}')
    run = subprocess.run([sys.executable, str(fw), *args], input=text, capture_output=True, text=True, env=env,
                         timeout=TIMEOUT)
    return run.stdout if run.returncode == 0 else run.stdout + run.stderr


def run_one(reply, home, levels, fw=FW):
    flags = ['--level', str(levels[0]), '--sentences', str(levels[1])]
    out = {'id': reply['id'], 'at': reply['at'], 'english': english_words(reply['draft']),
           'weave': parse_weave(_fw(home, ['weave', *flags], reply['draft'], fw))}
    if reply['reply'] and '<ja>' in reply['reply']:
        out['sent'] = parse_check(reply['sent'])
        out['now'] = parse_check(_fw(home, ['check-reply', *flags], reply['reply'], fw))
    return out


def totals(results):
    """Sums over the results: the weave's over every draft, the checks' over the replies both checks finished."""
    t = collections.Counter(drafts=len(results))
    reasons = {'sent': collections.Counter(), 'now': collections.Counter()}
    for r in results:
        t['english'] += r['english']
        for k, v in (r.get('weave') or {}).items():
            t['weave_' + k] += v
        if r.get('sent') and r.get('now'):
            t['checked'] += 1
            t['checked_english'] += r['english']
            for side in ('sent', 'now'):
                for k, v in r[side].items():
                    if k == 'reasons':
                        reasons[side].update(v)
                    else:
                        t[f'{side}_{k}'] += v
    return dict(t), {k: dict(v.most_common()) for k, v in reasons.items()}


def report(t, reasons, against=None):
    per = lambda n, words: f'{100 * n / words:5.2f}' if words else '    -'
    old = against or {}
    rows = [f'Backtest: {t.get("drafts", 0)} drafts ({t.get("english", 0)} English words), '
            f'{t.get("checked", 0)} replies with twins checked ({t.get("checked_english", 0)} words).',
            'Per 100 English words' + (' (an earlier run in brackets):' if against else ':')]

    def line(label, key, words_key):
        now = per(t.get(key, 0), t.get(words_key, 0))
        was = f' [{per(old.get(key, 0), old.get(words_key, 0)).strip()}]' if against else ''
        return f'  {label:38s} {now}{was}'
    rows += [line('weave: swaps offered in the text', 'weave_offered', 'english'),
             line('weave: unsure swaps', 'weave_unsure', 'english'),
             line('weave: words for twins only', 'weave_twin_only', 'english'),
             line('weave: twins asked for', 'weave_asked', 'english')]
    for side, name in (('sent', 'as sent'), ('now', 'checked now')):
        rows += [line(f'{name}: word swaps at full', f'{side}_words_full', 'checked_english'),
                 line(f'{name}: word swaps at normal', f'{side}_words_normal', 'checked_english'),
                 line(f'{name}: twins', f'{side}_twins', 'checked_english'),
                 line(f'{name}: twins at normal', f'{side}_twins_normal', 'checked_english'),
                 line(f'{name}: twins dropped', f'{side}_dropped', 'checked_english'),
                 line(f'{name}: swaps put back', f'{side}_put_back', 'checked_english')]
    rows.append(f'  checked now: mended {t.get("now_mended", 0)} times')
    for side in ('now',):
        if reasons.get(side):
            rows.append('Twins dropped now, by reason: ' + '; '.join(f'{v} {k}' for k, v in reasons[side].items()))
    return '\n'.join(rows)


def main(args):
    levels = args.levels
    found = replies(since=args.since or '')
    if args.limit:
        found = found[-args.limit:]
    if not found:
        print('No woven replies in the transcripts.')
        return
    source = fwlib.home()
    with tempfile.TemporaryDirectory(prefix='jw-backtest-') as tmp:
        homes = [_home(source, Path(tmp) / f'home{k}') for k in range(max(1, args.jobs))]
        free = list(homes)
        done = []

        def work(reply):
            home = free.pop()
            try:
                return run_one(reply, home, levels, Path(args.fw).expanduser() if args.fw else FW)
            finally:
                free.append(home)
        with concurrent.futures.ThreadPoolExecutor(len(homes)) as pool:
            for n, result in enumerate(pool.map(work, found), 1):
                done.append(result)
                if n % 50 == 0:
                    print(f'  {n} of {len(found)}', file=sys.stderr)
    t, reasons = totals(done)
    against = json.loads(Path(args.against).read_text())['totals'] if args.against else None
    print(report(t, reasons, against))
    if args.out:
        Path(args.out).write_text(json.dumps({'levels': levels, 'totals': t, 'reasons': reasons, 'replies': done},
                                             ensure_ascii=False))
        print(f'Kept in {args.out}.')
