"""Chat replies woven by code (README "Chat replies"; fw.py weave). Claude writes a reply in plain English and passes it
here; the extension's own pipeline (chatweave.mjs, under node or bun) swaps in Japanese by the word model and the
chat's level, and Claude rewrites the result into Japanese and mixed sentences and checks it (check_reply.py) before
sending it. At full, the words come from the dictionary too: every entry with a sense flagged safe that isn't in the
word list yet, priced by its fitted prior (knowledge.prior_estimate), loanwords and sensitive fields left out.
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
    open chance of a typical allowed sentence once the sentence model is ready (sentence_model.typical; it replaces
    the price of a sentence), maxChars, the most Japanese characters in one (sentence_model.max_chars), and grammar:
    the names of the known patterns and this week's learning pattern ({name, explain, example} or None)."""
    model = know.get('grammar') if isinstance(know.get('grammar'), dict) else None
    statuses = grammar.statuses(state)
    shape = sentence_model.typical(model)
    learning, _since = grammar.learning(state)
    return {'sentenceChance': round(shape[1], 4) if shape else None, 'maxChars': sentence_model.max_chars(model),
            'grammar': {'known': [p.name for p in grammar.PATTERNS if statuses[p.id] == 'known'],
                        'learning': {'name': learning.name, 'explain': learning.explain, 'example': learning.example}
                        if learning else None}}


def weave(draft, level=0, state=None):
    """The draft woven at the level (intensity.LEVELS above off): the weave's first line, -----, then the text."""
    js = runtime()
    if js is None:
        raise RuntimeError('no node or bun to run the weave')
    state = state or hub.current(fw_host.STATE_MAX_AGE, wait=False)[0]
    know = knowledge.load()
    words, adaptive = words_for(state)
    full = intensity.is_full(level)
    if full:
        words = words + pool(words, state, know)
    budget = intensity.budget(level, knowledge.challenge_of(state) or None) if not full else 0
    payload = {'draft': draft, 'words': words, 'intensity': 3 if full else level,
               'budgetPer100': budget if math.isfinite(budget) else 0, 'recency': adaptive.get('recency'),
               'sentencePrice': intensity.sentence_price(know), **sentence_plan(state, know)}
    run = subprocess.run([js, str(SCRIPT)], input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                         text=True, timeout=TIMEOUT)
    if run.returncode != 0:
        raise RuntimeError((run.stderr or 'the weave failed').strip().splitlines()[-1][:300])
    return run.stdout.rstrip('\n')
