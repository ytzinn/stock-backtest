"""
Minimal HTTP server that serves dashboard/status/health.json.

Usage: python -m dashboard.server [--port 8502]
"""
from __future__ import annotations

import argparse
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from dashboard.config import HEALTH_JSON

log = logging.getLogger(__name__)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/health'):
            if HEALTH_JSON.exists():
                data = HEALTH_JSON.read_bytes()
                code = 200
            else:
                data = b'{"error":"health.json not found"}'
                code = 503
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
            if code >= 400:
                log.warning('%s %s -> %d', self.command, self.path, code)
        else:
            self.send_response(404)
            self.end_headers()
            log.warning('%s %s -> 404', self.command, self.path)

    def log_message(self, *args):
        pass  # 정상 요청 suppress; 에러는 위에서 직접 로깅


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8502)
    port = ap.parse_args().port
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
    server = _ThreadingHTTPServer(('0.0.0.0', port), _Handler)
    log.warning('Health API listening on port %d', port)
    server.serve_forever()


if __name__ == '__main__':
    main()
