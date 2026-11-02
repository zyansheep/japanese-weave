"""Prints the word state as the extension receives it from the native host (no keys): each word with its glosses, safe
glosses and entry id, and the estimates and budget, with the dictionary words +1 and +2 may add as dictionaryWords.
check-lab.mjs and matcher-lab.mjs take it as WORDS, as they take a copy of state.json; worker-lab.mjs takes it as the
host's reply; level-replay.mjs takes it, with dictionaryWords, as its WORDS.

  python tools/prompt-lab/words.py > words.json              # from your state, as the native host answers
  python tools/prompt-lab/words.py state.json > words.json   # from a copy of a state, which stays as it is (no day's
                                                             # changes); its knowledge.json is read from the state dir
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import fw_host  # noqa: E402
import fwlib  # noqa: E402
import webdata  # noqa: E402

if len(sys.argv) > 1:
    state = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    reply = dict(fwlib.public_state(state), ok=True, prefs=state.get('prefs') or {},
                 reading={'version': webdata.reading_version()})
    webdata.add_word_data(reply['words'], state)
    reply.update(fw_host.adaptive(state, reply['words']))
else:
    reply = fw_host.handle({'type': 'state'})
    state = fw_host.hub.current(fw_host.STATE_MAX_AGE, wait=False)[0]
reply['dictionaryWords'] = webdata.dictionary_words(state, fw_host.knowledge.load())['entries']
for key in ('geminiKey', 'anthropicKey'):
    reply.pop(key, None)
print(json.dumps(reply, ensure_ascii=False))
