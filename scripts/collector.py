#!/usr/bin/env python3
"""Japanese Weave collector: one HTTP service that stores gloss interactions from every surface (the browser
extension through its native host, T3 Code through its server) in one SQLite database. Run it on an always-on
machine and reach it over a private network such as Tailscale. Python 3.9+, standard library only.

  collector.py --listen HOST:PORT [--db PATH]

  POST /v1/interactions  {"surface": "browser"|"t3", "items": [...]}  -> {"ok": true, "stored": n}  (see feedback.py)
  GET  /v1/recent?limit=N                                           -> {"ok": true, "items": [...]}
  GET  /v1/health                                                   -> {"ok": true, "interactions": n}

Bind it to the private network's address only (the Tailscale IP), not to every interface. Writes must be JSON
(Content-Type: application/json): a web page can't send that cross-site without a CORS preflight, which this server
never approves, so pages open in a browser on the network can't write to the log.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feedback  # noqa: E402

MAX_BODY = 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    db = None  # set by serve()
    server_version = 'JapaneseWeaveCollector/1'

    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/v1/health':
            return self.reply(200, {'ok': True, 'interactions': feedback.count(self.db)})
        if url.path == '/v1/recent':
            try:
                limit = max(1, min(500, int(parse_qs(url.query).get('limit', ['20'])[0])))
            except ValueError:
                return self.reply(400, {'ok': False, 'error': 'limit must be a number'})
            return self.reply(200, {'ok': True, 'items': feedback.recent(limit, self.db)})
        self.reply(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        if urlsplit(self.path).path != '/v1/interactions':
            return self.reply(404, {'ok': False, 'error': 'not found'})
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self.reply(415, {'ok': False, 'error': 'send application/json'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            size = -1
        if not 0 < size <= MAX_BODY:
            return self.reply(413, {'ok': False, 'error': 'body missing or too large'})
        try:
            body = json.loads(self.rfile.read(size).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return self.reply(400, {'ok': False, 'error': 'body is not JSON'})
        if not isinstance(body, dict) or body.get('surface') not in feedback.SURFACES or not isinstance(body.get('items'), list):
            return self.reply(400, {'ok': False, 'error': 'need {"surface": "browser"|"t3", "items": [...]}'})
        self.reply(200, {'ok': True, 'stored': feedback.record(body['items'], body['surface'], self.db)})

    def log_message(self, fmt, *args):  # one line per request on stderr, for journalctl
        sys.stderr.write(f'{self.address_string()} {fmt % args}\n')


def serve(host, port, db=None):
    Handler.db = db
    feedback.connect(db).close()  # create the database before the first request
    server = ThreadingHTTPServer((host, port), Handler)
    print(f'Japanese Weave collector on http://{host}:{port} -> {db or feedback.default_path()}', flush=True)
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--listen', required=True, metavar='HOST:PORT', help='for example 100.64.0.4:7853')
    parser.add_argument('--db', help='SQLite file (default: interactions.sqlite in the state dir)')
    args = parser.parse_args(argv)
    host, _, port = args.listen.rpartition(':')
    serve(host.strip('[]'), int(port), args.db)


if __name__ == '__main__':
    main()
