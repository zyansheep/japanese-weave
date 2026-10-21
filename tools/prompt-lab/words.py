"""Prints the word state as the extension receives it from the native host (no keys): each word with its glosses, safe
glosses and entry id. check-lab.mjs and matcher-lab.mjs take it as WORDS, as they take a copy of state.json."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import fw_host  # noqa: E402

reply = fw_host.handle({'type': 'state'})
for key in ('geminiKey', 'anthropicKey'):
    reply.pop(key, None)
print(json.dumps(reply, ensure_ascii=False))
