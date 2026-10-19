#!/usr/bin/env python3
"""Japanese Weave collector: the one home of the word state and of the gloss interaction log, for every machine and
surface (the browser extension through its native host, T3 Code through its server, the chat hook and commands on
any machine). Run it on an always-on machine, in the same state dir as that machine's own plugin, and reach it over a
private network such as Tailscale. Python 3.9+, standard library only.

  collector.py --listen HOST:PORT [--db PATH]

  GET  /v1/state?rev=REV  the word state, knowledge estimates and chat counts, for a machine's cache (hub.py)
                          -> {"ok": true, "rev", "state", "knowledge", "exposure"} or {"ok": true, "unchanged": true}
  POST /v1/ops            {"machine", "ops": [...]}: changes to the word state, applied in order (hub.py)
                          -> {"ok": true, "results": [...], "rev"}
  POST /v1/interactions   {"surface": "browser"|"t3", "items": [...]}
                          -> {"ok": true, "stored": n, "duplicates": n, "rejected": n}  (see feedback.py)
  GET  /v1/stats?since=MS how much has been collected, in all and since MS (default: the start of today here)
                          -> {"ok": true, "counts": {"browser:peek": [all, since], ...}, "shownWords", "readingMs",
                              "last"}
  GET  /v1/export?after=ID&seq=SEQ&limit=N   new interactions and changed contexts, for a mirror (feedback.py)
  GET  /v1/recent?limit=N                                           -> {"ok": true, "items": [...]}
  GET  /v1/health                                -> {"ok": true, "interactions": n, "host", "home", "state": bool}
  GET  /v1/intensity?surface=chat|browser        the level in effect -> {"ok": true, "intensity": {level, until, at}}
  POST /v1/intensity      {"surface", "step": 1|-1|0, "source"}: one press of the heavier/lighter shortcut (T3 Code,
                          fw.py intensity), recorded as a set op -> {"ok": true, "intensity": {level, until, at}}

Every few minutes it also applies the day's promotion rules and refreshes the knowledge estimates (hub.maintain).

Bind it to the private network's address only (the Tailscale IP), not to every interface. Writes must be JSON
(Content-Type: application/json): a web page can't send that cross-site without a CORS preflight, which this server
never approves, so pages open in a browser on the network can't write to the log. Every request must also be
addressed to this machine by name or address (the Host header), so a page can't reach it through DNS rebinding.
"""
import argparse
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feedback  # noqa: E402
import fwlib  # noqa: E402
import hub  # noqa: E402
import intensity  # noqa: E402

MAX_BODY = 1024 * 1024
MAX_OPS_BODY = 8 * 1024 * 1024  # an Anki sync carries every review word
MAINTAIN_EVERY = 600
PRESSES = threading.Lock()  # intensity presses one at a time, so two quick ones make two steps


class Handler(BaseHTTPRequestHandler):
    db = None  # set by serve()
    home = None  # the state dir; None: the usual one (tests serve another)
    server_version = 'JapaneseWeaveCollector/2'

    def handle_one_request(self):
        if self.home is None:
            return super().handle_one_request()
        with fwlib.using_home(self.home):
            return super().handle_one_request()

    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        packed = len(data) > 4096 and 'gzip' in self.headers.get('Accept-Encoding', '')
        if packed:  # the word state is about 0.5 MB of JSON
            data = gzip.compress(data, 6)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if packed:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def host_allowed(self):
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]').lower()
        name = socket.gethostname().lower()
        if host in (self.server.server_address[0], 'localhost', '127.0.0.1', name):  # exact: no lookalike names
            return True
        self.reply(421, {'ok': False, 'error': 'address this collector by its own host name'})
        return False

    def do_GET(self):
        if not self.host_allowed():
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        number = lambda name, default: int(query.get(name, [str(default)])[0])
        if url.path == '/v1/export':
            try:
                after, seq, limit = number('after', 0), number('seq', 0), max(1, min(feedback.EXPORT_PAGE, number('limit', 1000)))
            except ValueError:
                return self.reply(400, {'ok': False, 'error': 'after, seq and limit must be numbers'})
            return self.reply(200, dict(feedback.export(after, seq, limit, self.db), ok=True))
        if url.path == '/v1/state':
            return self.reply(*hub.snapshot(query.get('rev', [''])[0]))
        if url.path == '/v1/health':
            return self.reply(200, {'ok': True, 'interactions': feedback.count(self.db), 'host': socket.gethostname(),
                                    'home': str(fwlib.home()), 'state': (fwlib.home() / 'state.json').exists()})
        if url.path == '/v1/stats':
            try:
                since = number('since', int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))) * 1000)
            except ValueError:
                return self.reply(400, {'ok': False, 'error': 'since must be a number'})
            return self.reply(200, dict(feedback.stats(since, self.db), ok=True))
        if url.path == '/v1/intensity':
            surface = query.get('surface', [''])[0]
            if surface not in intensity.SURFACES:
                return self.reply(400, {'ok': False, 'error': 'surface is chat or browser'})
            prefs = fwlib.load_state().get('prefs') if (fwlib.home() / 'state.json').exists() else {}
            return self.reply(200, {'ok': True, 'intensity': intensity.current(prefs, surface)})
        if url.path == '/v1/recent':
            try:
                limit = max(1, min(500, number('limit', 20)))
            except ValueError:
                return self.reply(400, {'ok': False, 'error': 'limit must be a number'})
            return self.reply(200, {'ok': True, 'items': feedback.recent(limit, self.db)})
        self.reply(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        if not self.host_allowed():
            return
        path = urlsplit(self.path).path
        if path not in ('/v1/interactions', '/v1/ops', '/v1/intensity'):
            return self.reply(404, {'ok': False, 'error': 'not found'})
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self.reply(415, {'ok': False, 'error': 'send application/json'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            size = -1
        if not 0 < size <= (MAX_OPS_BODY if path == '/v1/ops' else MAX_BODY):
            return self.reply(413, {'ok': False, 'error': 'body missing or too large'})
        try:
            body = json.loads(self.rfile.read(size).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return self.reply(400, {'ok': False, 'error': 'body is not JSON'})
        if path == '/v1/ops':
            return self.reply(*hub.receive(body, self.db))
        if path == '/v1/intensity':
            source = body.get('source', 'unknown') if isinstance(body, dict) else None
            if not isinstance(body, dict) or body.get('surface') not in intensity.SURFACES or body.get('step') not in (-1, 0, 1) \
                    or isinstance(body.get('step'), bool) or not isinstance(source, str) or not 0 < len(source) <= 20:
                return self.reply(400, {'ok': False, 'error': 'need {"surface": "chat"|"browser", "step": 1|-1|0, "source"}'})
            if not (fwlib.home() / 'state.json').exists():
                return self.reply(409, {'ok': False, 'error': hub.NO_STATE})
            with PRESSES:
                try:
                    value = hub.change_intensity_here(body['surface'], body['step'], source, self.db)
                except ValueError as error:
                    return self.reply(400, {'ok': False, 'error': str(error)})
            return self.reply(200, {'ok': True, 'intensity': value})
        if not isinstance(body, dict) or body.get('surface') not in feedback.SURFACES or not isinstance(body.get('items'), list):
            return self.reply(400, {'ok': False, 'error': 'need {"surface": "browser"|"t3", "items": [...]}'})
        self.reply(200, dict(feedback.record(body['items'], body['surface'], self.db), ok=True))

    def log_message(self, fmt, *args):  # one line per request on stderr, for journalctl
        sys.stderr.write(f'{self.address_string()} {fmt % args}\n')


def maintain_forever():
    while True:
        try:
            hub.maintain()
        except Exception as error:  # keep serving; try again next round
            print(f'maintenance failed: {error.__class__.__name__}: {error}', file=sys.stderr, flush=True)
        time.sleep(MAINTAIN_EVERY)


def serve(host, port, db=None):
    Handler.db = db
    feedback.connect(db).close()  # create the database before the first request
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=maintain_forever, daemon=True).start()
    print(f'Japanese Weave collector on http://{host}:{port} -> {db or feedback.default_path()}', flush=True)
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--listen', required=True, metavar='HOST:PORT', help='for example 100.64.0.4:7853')
    parser.add_argument('--db', help='SQLite file (default: interactions.sqlite in the state dir)')
    args = parser.parse_args(argv)
    if fwlib.collector_url():
        sys.exit(f'{fwlib.home()} points at a collector ({fwlib.collector_url()}): the collector serves its own state '
                 'dir, so remove its "collector" file first.')
    host, _, port = args.listen.rpartition(':')
    serve(host.strip('[]'), int(port), args.db)


if __name__ == '__main__':
    main()
