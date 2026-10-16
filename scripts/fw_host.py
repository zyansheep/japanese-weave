#!/usr/bin/env python3
"""Chrome native messaging host for the Japanese Weave extension (Python 3.9+, standard library only).

Messages (JSON, 4-byte native-endian length prefix, one per line of conversation):
  {"type": "ping"}                      -> {"ok": true, "host": 1}
  {"type": "state"}                     -> {"ok": true, "version", "stage", "paused", "words": [{ja, en, hint, status}],
                                            "prefs": {"extension_sites"?: "all" | "ask"} (absent means "all"),
                                            "geminiKey"?: stored key if setup saved one}
  {"type": "events", "events": [{"word", "kind": "seen"|"flip"|"peek", "day"?}]} -> {"ok": true, "applied": n}
  {"type": "log", "items": [{"kind": "peek"|"flip"|"shown", ...}]}  -> {"ok": true, "stored", "duplicates", "rejected"}
                                                                     (see feedback.py)
  {"type": "prefs", "challenge": n}     -> {"ok": true, "challenge": n}  (adaptive difficulty dial, knowledge.py)

The state reply also carries adaptive difficulty: each word's knowledge estimate (k, sd) when the gloss log has
evidence for it, the dial (challenge), the peek budget per 100 words, and the realized peek rate.

With a collector set, the state lives on the collector: replies come from this machine's cache and changes are queued
here and shown in it at once; a background sync (refreshed when the cache is over STATE_MAX_AGE seconds old) sends
them and refreshes the cache. The browser never waits on the network.
"""
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwlib  # noqa: E402
import feedback  # noqa: E402
import hub  # noqa: E402
import knowledge  # noqa: E402

MAX_EVENTS = 500
STATE_MAX_AGE = 60


def read_message(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    size = struct.unpack('=I', header)[0]
    if size > 1024 * 1024:
        raise ValueError('message too large')
    return json.loads(stream.read(size).decode('utf-8'))


def write_message(stream, message):
    data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    stream.write(struct.pack('=I', len(data)))
    stream.write(data)
    stream.flush()


def handle(message):
    kind = message.get('type') if isinstance(message, dict) else None
    if kind == 'ping':
        return {'ok': True, 'host': 1}
    if kind == 'state':
        state, _ = hub.current(STATE_MAX_AGE, wait=False)  # here: applies today's promotion; else the cache
        reply = dict(fwlib.public_state(state), ok=True, prefs=state.get('prefs') or {})
        key = fwlib.home() / 'gemini_key'
        if key.exists():
            reply['geminiKey'] = key.read_text().strip()
        return dict(reply, **adaptive(state, reply['words']))
    if kind == 'events':
        events = message.get('events')
        if not isinstance(events, list):
            return {'ok': False, 'error': 'events must be a list'}
        today = fwlib.today()
        clean = []
        for event in events[:MAX_EVENTS]:
            if not isinstance(event, dict) or event.get('kind') not in ('seen', 'flip', 'peek'):
                continue
            word = event.get('word')
            day = event['day'] if hub.is_day(event.get('day')) else today
            if isinstance(word, str) and 0 < len(word) <= 100:
                clean.append({'word': word, 'kind': event['kind'], 'day': min(day, today)})
        if not clean:
            return {'ok': True, 'applied': 0}
        return {'ok': True, 'applied': hub.submit({'kind': 'events', 'events': clean}, wait=False).get('applied', 0)}
    if kind == 'log':
        items = message.get('items')
        if not isinstance(items, list):
            return {'ok': False, 'error': 'items must be a list'}
        collector = fwlib.collector_url()
        if collector:  # one database for every machine; a failure keeps the items queued in the extension
            return feedback.forward(collector, items, 'browser')
        return dict(feedback.record(items, 'browser'), ok=True)
    if kind == 'prefs':
        challenge = message.get('challenge')
        if not knowledge.valid_challenge(challenge):
            return {'ok': False, 'error': f'challenge must be one of {list(knowledge.CHALLENGES)}'}
        hub.submit({'kind': 'set', 'prefs': {'challenge': challenge}}, wait=False)
        return {'ok': True, 'challenge': challenge}
    return {'ok': False, 'error': 'unknown message type'}


def adaptive(state, words):
    """Per-word estimates and the dial for the extension; starts a background refresh when they are stale. Never
    waits on the network: the refresh runs in its own process."""
    know = knowledge.load()
    for w in words:
        entry = (know.get('words') or {}).get(w['ja'])
        if entry:
            w['k'], w['sd'] = round(entry['k'], 2), round(entry['sd'], 2)
    if not fwlib.collector_url() and knowledge.is_stale(know) and feedback.default_path().exists():
        knowledge.start_refresh()  # with a collector, it keeps the estimates fresh and they come with the cache
    challenge = knowledge.challenge_of(state)
    scale = (know.get('controller') or {}).get('scale', 1.0)
    return {'challenge': challenge, 'budget': round(challenge * scale, 3), 'rate': know.get('rate')}


def main():
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            message = read_message(stdin)
        except ValueError as error:
            write_message(stdout, {'ok': False, 'error': str(error)})
            return
        if message is None:
            return
        try:
            reply = handle(message)
        except Exception as error:  # report, don't crash Chrome's port
            reply = {'ok': False, 'error': f'{error.__class__.__name__}: {error}'}
        write_message(stdout, reply)


if __name__ == '__main__':
    main()
