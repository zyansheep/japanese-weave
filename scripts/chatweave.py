"""Chat replies woven by code (README "Chat replies"; fw.py weave). Claude writes a reply in plain English and passes it
here; the extension's own pipeline (chatweave.mjs, under node or bun) swaps in every Japanese word that could go there,
as at full, since a reply now carries every level at once (twins.py): the words known at a glance, the learning words,
and a few dictionary words: entries with a sense flagged safe that aren't in the word list yet, priced by their fitted
prior (knowledge.prior_estimate), loanwords and sensitive fields left out, which show only at full. The first line asks
for a Japanese twin of each sentence that fits the length and grammar limits, up to a cap (twin_cap), and the swaps
are kept for the check step (twins.remember_swaps), which fills in each one's English. Claude writes the reply with
its twins and passes it to check_reply.py, which decides what each level shows and prints the markup to send.
Python 3.9+ standard library only."""
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import fwlib
import fw_host
import grammar
import hub
import intensity
import knowledge
import sentence_model
import webdata

SCRIPT = Path(__file__).resolve().parent / 'chatweave.mjs'
EXTENSION = Path(__file__).resolve().parent.parent / 'extension' / 'src'
SENSES = Path(__file__).resolve().parent.parent / 'data' / 'senses.tsv'
TIMEOUT = 30
# The most twins the first line asks for: TWIN_SLACK times the sentences much heavier (+2, the heaviest level with a
# budget) can show in the reply at the price of a sentence, at least TWIN_MIN. Full shows every twin, but full costs
# more reading than it teaches (docs/chat-weave.md, round 4) and is rarely read, so twins past what +2 uses mostly
# serve it; the slack leaves the ranking a choice (and room for its random places, twins.EXPLORE) and covers the twins
# the check drops, while bounding the writing a reply takes.
TWIN_SLACK = 1.5
TWIN_MIN = 3
POOL = 'weave-pool.json'  # the dictionary words full adds, kept in the state folder until the word list or fit changes
SENSITIVE = ('med', 'pharm', 'dent', 'vet', 'psy', 'anat', 'physiol', 'embryo', 'law', 'finc', 'econ', 'bus')


def runtime():
    """The JavaScript runtime the weave runs under (node, else bun), or None: then chat keeps the classic brief."""
    if not (SCRIPT.exists() and EXTENSION.exists()):
        return None
    return shutil.which('node') or shutil.which('bun')


def words_for(state):
    """The word list as the extension gets it from the host (fw_host.handle 'state': public_state, word data and the
    fit's estimates; never a key), with normal's budget, the fit's recency and the price of a whole sentence."""
    words = fwlib.public_state(state)['words']
    webdata.add_word_data(words, state)
    return words, fw_host.adaptive(state, words)


def safe_senses():
    """{(entry id, ja): [safe glosses]} from data/senses.tsv."""
    out = {}
    with open(SENSES, encoding='utf-8') as f:
        rows = [line.rstrip('\n').split('\t') for line in f if not line.startswith('#')]
    for row in rows[1:]:
        if len(row) >= 4 and row[3]:
            out[(row[0], row[1])] = row[3].split(';')
    return out


def dictionary_words(have, know):
    """Every dictionary entry not in the word list (have: its spellings) with a sense flagged safe, as the host gives a
    learning word: its safe glosses, and k and sd from its fitted prior as a new word. Loanwords, katakana-only words
    and medical, legal and financial entries are left out, as for topical words."""
    import dictionary
    safe = safe_senses()
    out = []
    for e in dictionary.load():
        glosses = safe.get((str(e['id']), e['ja'])) if e['swap'] and e['ja'] not in have else None
        if not glosses or e['loan'] or set(e['field']) & set(SENSITIVE) or all('゠' <= c <= 'ヿ' for c in e['ja']):
            continue
        guess = knowledge.prior_estimate(know, 'new', None, e)
        if guess is None:
            continue
        out.append({'ja': e['ja'], 'en': ' / '.join(glosses[:2]), 'hint': e['romaji'], 'kana': e['kana'],
                    'status': 'active', 'glosses': glosses, 'safe': glosses, 'id': e['id'], 'k': round(guess[0], 2),
                    'sd': round(guess[1], 2), 'free': False})
    return out


def pool(words, state, know):
    """dictionary_words, kept in POOL until the word list or the fitted prior changes (a few seconds to build)."""
    have = sorted(w['ja'] for w in words)
    key = hashlib.sha256(json.dumps([know.get('prior'), have], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = fwlib.home() / POOL
    kept = fwlib.read_json(path, {})
    if isinstance(kept, dict) and kept.get('key') == key and isinstance(kept.get('words'), list):
        return kept['words']
    extra = dictionary_words(set(have), know)
    try:
        fwlib.write_json(path, {'key': key, 'words': extra})
    except OSError:
        pass
    return extra


def sentence_plan(state, know):
    """What the weave's first line says about Japanese and mixed sentences (docs/grammar.md): sentenceChance, the
    open chance of a typical allowed sentence once the sentence model is ready (sentence_model.typical; the count it
    pays for is used when smaller than the price's, chatweave.mjs), maxChars, the most Japanese characters in one (sentence_model.max_chars), and grammar:
    the names of the known patterns and this week's learning pattern ({name, explain, example} or None)."""
    model = know.get('grammar') if isinstance(know.get('grammar'), dict) else None
    statuses = grammar.statuses(state)
    shape = sentence_model.typical(model)
    learning, _since = grammar.learning(state)
    return {'sentenceChance': round(shape[1], 4) if shape else None, 'maxChars': sentence_model.max_chars(model),
            'grammar': {'known': [p.name for p in grammar.PATTERNS if statuses[p.id] == 'known'],
                        'learning': {'name': learning.name, 'explain': learning.explain, 'example': learning.example}
                        if learning else None}}


def twin_cap(words, normal=None, price=intensity.SENTENCE_PRICE):
    """(the most twins to ask for in a reply of `words` English words, the sentences +2 shows at the price): see
    TWIN_SLACK."""
    heaviest = intensity.SENTENCE_SHARE * intensity.budget(2, normal) * words / 100 / max(price, 1e-6)
    return max(TWIN_MIN, math.ceil(TWIN_SLACK * heaviest)), heaviest


def first_line(woven, plan, cap, heaviest):
    """The weave's first line: the swaps (known, learning by name, dictionary words by name, unsure ones), then the
    twins to write, their limits and grammar (sentence_plan) and this week's learning pattern."""
    swaps = woven['swaps']
    known = [s for s in swaps if s['free']]
    learning = [s for s in swaps if not s['free'] and not s.get('dictionary')]
    pool = [s for s in swaps if s.get('dictionary')]
    names = lambda group: ', '.join(dict.fromkeys(s['ja'] for s in group))
    g = plan.get('grammar') or {}
    l = g.get('learning')
    line = (f'jw-weave: {len(known)} known-word swaps and {len(learning)} learning-word swaps'
            + (f' ({names(learning)}: keep each whose sense fits)' if learning else '')
            + (f', and {len(pool)} dictionary words that show only at full ({names(pool)})' if pool else '')
            + f'. {sum(1 for s in swaps if s.get("unsure"))} swaps are marked {{{{English→Japanese}}}}: the code '
            'wasn\'t sure of the sense. '
            f'Twins: right after each sentence you can say in Japanese within these limits, write its Japanese in '
            f'<ja>...</ja>, up to {cap} twins (much heavier shows about {max(1, round(heaviest))}, full shows them all; '
            'code chooses which show at each level). '
            f'Each Japanese sentence at most {plan.get("maxChars") or 14} Japanese characters (a twin may be two short '
            f'sentences), using only these grammar patterns: {", ".join(g.get("known") or ())}; plus at most one '
            'learning pattern in a sentence. '
            + (f'This week\'s learning pattern is {l["name"]}: {l["explain"]}, as in {l["example"]}; use it in a twin '
               'or two. ' if l else 'No learning pattern this week. ')
            + 'Any other grammar stays out: rephrase, or leave that sentence without a twin.')
    return line


def weave(draft, level=0, state=None, session=None):
    """The draft woven with every candidate swap: the weave's first line, -----, then the text. The swaps are kept for
    the check step (twins.remember_swaps). level: the chat's level now (the first line doesn't depend on it)."""
    js = runtime()
    if js is None:
        raise RuntimeError('no node or bun to run the weave')
    state = state or hub.current(fw_host.STATE_MAX_AGE, wait=False)[0]
    know = knowledge.load()
    words, adaptive = words_for(state)
    words = words + [dict(w, dictionary=True) for w in pool(words, state, know)]
    payload = {'draft': draft, 'words': words, 'intensity': intensity.FULL, 'recency': adaptive.get('recency')}
    run = subprocess.run([js, str(SCRIPT)], input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                         text=True, timeout=TIMEOUT)
    if run.returncode != 0:
        raise RuntimeError((run.stderr or 'the weave failed').strip().splitlines()[-1][:300])
    woven = json.loads(run.stdout)
    import twins
    twins.remember_swaps(woven['swaps'], level, session)
    price = intensity.sentence_price(know)
    cap, heaviest = twin_cap(woven['words'], knowledge.challenge_of(state) or None, price)
    return first_line(woven, sentence_plan(state, know), cap, heaviest) + '\n-----\n' + woven['text']
