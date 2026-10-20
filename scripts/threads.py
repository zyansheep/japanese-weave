"""One chat without Japanese: the reader turns Japanese off in a single chat thread, and later back on, by asking in
plain words ("no japanese for this thread please", "english only from now on", then "japanese back on") or with
`/japanese-weave:intensity off here` and `... normal here`. While a chat is off the hook sends no brief there, only
a short note to reply in plain English. Python 3.9+, standard library only.

The switch is kept per Claude Code (or Codex) session id in the word state: state["threads"] = {session: {off, at,
source}}. Each change is a `thread` op ({session, off, at, source}, hub.py), so the collector's ops table keeps it as
evidence: a chat turned off is the strongest "lighter" signal there is (knowledge.calibrate counts it as a lighter
press). Changes are merged per chat, the later one winning, so two machines never clobber each other's chats; chats
not changed for KEEP_DAYS, or past the newest MOST, are forgotten.
"""
import re
import time
import unicodedata

KEEP_DAYS = 60       # a chat's switch is forgotten this long after its last change
MOST = 500           # chats kept, the most recently changed first
DAY_MS = 86_400_000
SHORT = 12           # a prompt of at most this many words may ask without naming the chat ("no japanese please")
LONG = 16            # a sentence longer than this is about something else
EDGE = 2             # in a longer prompt only the first and last sentences are read, where a side request sits
HEAD = 4000          # characters read from each end of a very long prompt
SESSION = re.compile(r'[\w.:-]{1,100}')  # a Claude Code or Codex session id

LANG = r'(?:japanese|nihongo|jp)'
# (pattern, off, needs): needs 'any' (a short prompt, or a sentence naming the chat or the reader), or 'thread' (a
# sentence naming this chat or from now on).
PATTERNS = [(re.compile(p), off, needs) for p, off, needs in (
    (rf'\b{LANG} (?:back on|on again|back)\b', False, 'any'),
    (rf'\bturn (?:the )?{LANG} (?:back )?on\b', False, 'any'),
    (rf'\bturn (?:back )?on (?:the )?{LANG}\b', False, 'any'),
    (rf'\bbring back (?:the )?{LANG}\b', False, 'any'),
    (rf'\b(?:use|using|write|writing|add|adding|weave|weaving|mix|mixing|start|resume)(?: in| using| with)?'
     rf'(?: the| some)? {LANG}(?: words)?(?: in)? again\b', False, 'any'),
    (rf'\b{LANG} (?:is )?(?:ok|okay|fine|allowed|welcome) again\b', False, 'any'),
    (rf'\b(?:resume|restart|re-?enable) (?:the )?{LANG}\b', False, 'any'),
    (rf'\bno (?:more )?{LANG}\b', True, 'any'),
    (r'\b(?:english|eng) only\b', True, 'any'),
    (r'\bonly (?:in )?english\b', True, 'any'),
    (rf'\b(?:stop|quit|drop|ditch|skip|cut|pause|disable|enough with)'
     rf'(?: using| writing| adding| mixing in| weaving in| with| the| all| that| this)* {LANG}\b', True, 'any'),
    (rf"\b(?:don't|dont|do not|never|not) (?:use|write|add|include|mix in|weave in|put)(?: any)? {LANG}\b", True, 'any'),
    (rf'\bkeep (?:the |all )?{LANG} out\b', True, 'any'),
    (rf'\b(?:turn|switch|shut) (?:the )?{LANG} off\b', True, 'any'),
    (rf'\b(?:turn|switch) off (?:the )?{LANG}\b', True, 'any'),
    (rf'^{LANG} off\b', True, 'any'),
    (rf'\bwithout (?:any )?{LANG}\b', True, 'thread'),
    (r'\bjust (?:in )?english\b', True, 'thread'),
)]
# A sentence about files, code, the plugin itself, or someone else ("when the user says ...") is never a request.
ABOUT = re.compile(
    r"\b(?:files?|code|commits?|config\w*|docs?|documentation|readme|comments?|prs?|pull requests?|tests?|variables?|"
    r"functions?|filenames?|identifiers?|branch(?:es)?|scripts?|logs?|strings?|json|yaml|toml|csv|markdown|html|labels?|"
    r"names?|emails?|letters?|essays?|reports?|documents?|drafts?|slides?|translat\w*|version|data|hooks?|briefs?|"
    r"plugins?|extensions?|lexicon|features?|detect\w*|regex\w*|triggers?|prompts?|subagents?|agents?|if|when|whenever|"
    r"unless|whether|should|must|users?|they|claude|model)\b")
SCOPE = re.compile(r"\b(?:thread|chat|conversation|convo|session|here|anymore|any more|from now on|for now|going forward|"
                   r"the rest|whole|entire|today|please|plz|pls|me|you|your|replies|reply|responses|answers|messages)\b")
THREAD = re.compile(r"\b(?:thread|chat|conversation|convo|session|here|anymore|any more|from now on|going forward|"
                    r"the rest|whole|entire)\b")
ASKING = re.compile(r"^(?:(?:can|could|would|will|won't|cant|can't) (?:you|u|we)|please|pls|plz|kindly|how about|mind)\b")
# Words that may follow a request ("no japanese please"); anything else makes it a noun phrase ("no japanese version").
FOLLOW = {'please', 'plz', 'pls', 'thanks', 'thank', 'thx', 'ty', 'ok', 'okay', 'for', 'in', 'here', 'anymore', 'any',
          'from', 'at', 'now', 'today', 'until', 'till', 'while', 'either', 'this', 'then', 'again', 'ever', 'and',
          'but', 'so', 'with', 'to', 'on', 'words', 'word', 'text', 'characters', 'stuff', 'mixed', 'mode', 'going',
          'of', 'whatsoever', 'mate', 'man', 'bro', 'dude', 'sorry', 'lol'}
# A word right before the request that makes it a statement ("the page has no japanese", "I do not use japanese").
STATING = {'is', 'are', 'was', 'were', 'be', 'been', 'being', "isn't", "aren't", "wasn't", "there's", 'theres', 'has',
           'have', 'had', "hasn't", 'says', 'said', 'say', 'saying', 'typed', 'types', 'writes', 'wrote', 'means', 'i',
           'we', 'he', 'she', 'you', 'u', 'people'}
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
QUOTED = re.compile(r'"[^"\n]*"|(?<!\w)\'[^\'\n]*\'(?!\w)')
CODE = re.compile(r'```.*?```|`[^`\n]*`|https?://\S+', re.S)
COMMAND = re.compile(r'^/(?:japanese-weave:)?intensity\b(.*)', re.S)
TAGGED = re.compile(r'<command-name>/?(?:japanese-weave:)?intensity</command-name>.*?<command-args>(.*?)</command-args>',
                    re.S)


# ---- the switch ----

def valid_session(value):
    return isinstance(value, str) and bool(SESSION.fullmatch(value))


def valid(entry):
    """A stored switch: {off, at, source} (ops arrive over the network)."""
    return (isinstance(entry, dict) and set(entry) == {'off', 'at', 'source'} and isinstance(entry['off'], bool)
            and isinstance(entry['at'], int) and not isinstance(entry['at'], bool) and 0 <= entry['at'] <= 10 ** 14
            and isinstance(entry['source'], str) and 0 < len(entry['source']) <= 20)


def entry(state, session):
    """This chat's switch, or None when it was never changed (Japanese on)."""
    table = (state or {}).get('threads')
    value = table.get(session) if isinstance(table, dict) and valid_session(session) else None
    return value if valid(value) else None


def is_off(value):
    return bool(valid(value) and value['off'])


def later(*entries):
    """The latest of some entries for one chat (missing and malformed ones skipped; on a tie, the first)."""
    good = [e for e in entries if valid(e)]
    return max(good, key=lambda e: e['at']) if good else None


def merge(state, session, value, now=None):
    """Records one change to a chat in the state unless that chat has a later one already (ops from two machines
    arrive in any order), then forgets old chats. Returns whether the chat is off now."""
    table = state.get('threads')
    if not isinstance(table, dict):
        table = state['threads'] = {}
    old = table.get(session)
    if not valid(old) or old['at'] <= value['at']:
        table[session] = value
    prune(table, now)
    return is_off(table.get(session))


def prune(table, now=None):
    """Keeps the chats changed in the last KEEP_DAYS, at most MOST of them, the newest."""
    now = now if now is not None else int(time.time() * 1000)
    keep = sorted(((k, v) for k, v in table.items() if valid(v) and v['at'] >= now - KEEP_DAYS * DAY_MS),
                  key=lambda kv: kv[1]['at'])[-MOST:]
    table.clear()
    table.update(keep)


def note(codex=False):
    """What the hook says instead of the brief in a chat with Japanese off."""
    how = '"japanese back on"' + ('' if codex else ' or with /japanese-weave:intensity normal here')
    return ('Japanese Weave: the user turned Japanese off for this chat, so reply in plain English, with no Japanese '
            f'words, glosses or recall lines. They can turn it back on by saying {how}.')


# ---- asking in the prompt ----

def request(prompt):
    """(off, source) when the prompt asks to stop Japanese in this chat (off True) or to bring it back (False), else
    None. `/japanese-weave:intensity off here` and `normal here` are source 'command'; plain words are 'chat'.

    Plain words are read conservatively: only short sentences (at most LONG words) at the start or end of the prompt,
    without quoted text or code; not questions unless they ask ("can you stop using japanese?"); not statements ("the
    page has no japanese"); never about files, code, commits, configs, docs or the plugin itself, or about someone
    else (if, when, should, the user). A long prompt also has to name the chat or the reader (thread, here, from now
    on, please, me, your replies ...); a short one needn't."""
    if not isinstance(prompt, str):
        return None
    text = unicodedata.normalize('NFKC', prompt).replace('’', "'").replace('‘', "'")
    text = text.replace('“', '"').replace('”', '"').strip()
    command = COMMAND.match(text) or TAGGED.search(text)
    if command:
        args = command.group(1).lower().split()
        if 'here' not in args:
            return None
        return (True, 'command') if 'off' in args else (False, 'command') if {'normal', 'on'} & set(args) else None
    if text.startswith('/') or '<command-name>' in text:
        return None  # another command
    if len(text) > 2 * HEAD:
        text = text[:HEAD] + '\n' + text[-HEAD:]
    text = QUOTED.sub(' ', CODE.sub(' ', text)).lower()
    sentences = [s.strip() for s in re.split(r'(?<=[.!?;])\s+|\n+', text) if WORD.search(s)]
    short = sum(len(WORD.findall(s)) for s in sentences) <= SHORT
    if len(sentences) > 2 * EDGE:
        sentences = sentences[:EDGE] + sentences[-EDGE:]
    found = None
    for sentence in sentences:  # the last request wins
        asked = _asks(sentence, short)
        found = found if asked is None else asked
    return (found, 'chat') if found is not None else None


def _asks(sentence, short):
    """True (off), False (on) or None for one sentence."""
    words = WORD.findall(sentence)
    if len(words) > LONG or ABOUT.search(sentence):
        return None
    if sentence.endswith('?') and not ASKING.match(sentence):
        return None
    for pattern, off, needs in PATTERNS:  # bringing it back first: "no more english only, japanese back on"
        for match in pattern.finditer(sentence):
            if not _plain(sentence, match):
                continue
            if THREAD.search(sentence) or (needs == 'any' and (short or SCOPE.search(sentence))):
                return off
    return None


def _plain(sentence, match):
    """The match is a request in its own right: no noun after it, no subject or copula before it."""
    tail = sentence[match.end():].lstrip()
    after = WORD.findall(tail)[:1]
    if after and tail[0] not in ',;:.!?)-' and after[0] not in FOLLOW:
        return False
    head = sentence[:match.start()].rstrip()
    before = WORD.findall(head)[-2:]
    if not before or head[-1] in ',;:(-':  # a clause of its own: "thanks, no japanese please"
        return True
    last, prior = before[-1], before[0] if len(before) == 2 else ''
    if last in ('you', 'u', 'we'):
        return prior in ('can', 'could', 'would', 'will', 'please', 'pls', 'plz')  # "can you stop using japanese"
    if last in ('said', 'say'):
        return prior == 'i'  # "I said no japanese"
    return last not in STATING
