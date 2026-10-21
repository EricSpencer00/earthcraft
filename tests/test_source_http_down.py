"""A host that keeps refusing connections is marked down for every worker and skipped."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import source_http  # noqa: E402

URL = 'https://dead.example.invalid/archive.zip'


class HostDownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'EARTHCRAFT_HOST_SLOTS': self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_two_connection_failures_mark_the_host_down_for_everyone(self):
        calls = []

        def refuse(*_, **__):
            calls.append(1)
            raise urllib.error.URLError(ConnectionRefusedError(61, 'refused'))
        with patch('urllib.request.urlopen', refuse):
            with self.assertRaises(source_http.HostUnavailable):
                source_http.get_range(URL, 0, 9, '"e"', 100, sleep=lambda _: None)
            self.assertEqual(len(calls), 2)
            # Another request (another worker) fails at once, without touching the network.
            with self.assertRaises(source_http.HostUnavailable):
                source_http.get_range(URL, 10, 19, '"e"', 100, sleep=lambda _: None)
            self.assertEqual(len(calls), 2)
        self.assertTrue((Path(self.tmp.name) / 'dead.example.invalid.down').exists())

    def test_the_mark_expires(self):
        source_http.mark_host_down(URL, seconds=60, now=lambda: 1000.0)
        with self.assertRaises(source_http.HostUnavailable):
            source_http.check_host(URL, now=lambda: 1059.0)
        source_http.check_host(URL, now=lambda: 1061.0)

    def test_http_errors_from_a_live_server_do_not_mark_it_down(self):
        def busy(*_, **__):
            raise urllib.error.HTTPError(URL, 503, 'busy', {}, None)
        with patch('urllib.request.urlopen', busy):
            with self.assertRaises(urllib.error.HTTPError):
                source_http.get_range(URL, 0, 9, '"e"', 100, attempts=3, sleep=lambda _: None)
        source_http.check_host(URL)

    def test_unavailable_is_a_retryable_network_error(self):
        import hf_store
        self.assertTrue(hf_store.is_retryable(source_http.HostUnavailable('down')))


if __name__ == '__main__':
    unittest.main()
