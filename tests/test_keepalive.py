"""keepalive.get against a local HTTP server: reuse, errors and truncation behave like urlopen."""
import http.client
import http.server
from pathlib import Path
import sys
import threading
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import keepalive  # noqa: E402


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    connections = set()

    def do_GET(self):
        Handler.connections.add(self.client_address)
        if self.path == '/missing':
            self.send_response(404); self.send_header('Content-Length', '0'); self.end_headers(); return
        body = b'x' * 1000
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class KeepAliveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_reuses_one_connection_and_bounds_the_body(self):
        Handler.connections.clear()
        for _ in range(5):
            self.assertEqual(len(keepalive.get(self.base + '/a', 10_000)), 1000)
        self.assertEqual(len(Handler.connections), 1)
        self.assertEqual(len(keepalive.get(self.base + '/a', 100)), 101)  # limit + 1 signals oversize

    def test_http_errors_raise_like_urlopen(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            keepalive.get(self.base + '/missing', 100)
        self.assertEqual(caught.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
