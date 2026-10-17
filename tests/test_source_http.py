import email.message
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import source_http
from regional_scans import same_crs


class Response(io.BytesIO):
    def __init__(self, data, status=206, content_range=None):
        super().__init__(data)
        self.status, self.headers = status, {'Content-Range': content_range}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class SourceHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'EARTHCRAFT_HOST_SLOTS': self.directory.name,
                                           'EARTHCRAFT_HOST_CONNECTIONS': '2'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def test_host_slots_bound_concurrency_across_threads(self):
        active, peak, lock = [0], [0], threading.Lock()

        def hold():
            with source_http.host_slot('https://publisher.example/a.zip', poll=0.01):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                time.sleep(0.05)
                with lock:
                    active[0] -= 1
        threads = [threading.Thread(target=hold) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak[0], 2)

    def test_retries_transient_errors_but_not_a_changed_archive(self):
        headers = email.message.Message()
        headers['Retry-After'] = '3'
        replies = [urllib.error.HTTPError('u', 503, 'busy', headers, None), TimeoutError('slow'),
                   Response(b'abcd', content_range='bytes 10-13/100')]
        sleeps = []

        def urlopen(request, timeout):
            self.assertEqual(request.get_header('Range'), 'bytes=10-13')
            self.assertEqual(request.get_header('If-match'), '"v1"')
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        with patch('urllib.request.urlopen', urlopen):
            self.assertEqual(source_http.get_range('https://h/a.zip', 10, 13, '"v1"', 100, sleep=sleeps.append), b'abcd')
        self.assertEqual(sleeps[0], 3.0)
        self.assertEqual(len(sleeps), 2)
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('u', 412, 'changed', None, None)):
            with self.assertRaisesRegex(ValueError, 'version changed'):
                source_http.get_range('https://h/a.zip', 10, 13, '"v1"', 100, sleep=sleeps.append)
        with patch('urllib.request.urlopen', return_value=Response(b'x' * 100, status=200)):
            with self.assertRaisesRegex(ValueError, 'ignored'):
                source_http.get_range('https://h/a.zip', 10, 13, '"v1"', 100, sleep=sleeps.append)
        with self.assertRaisesRegex(ValueError, 'Bounded'):
            source_http.get_range('https://h/a.zip', 0, 64 * 2**20, '"v1"', 2**30)

    def test_esri_echo_of_the_frame_crs_is_the_same_crs(self):
        frame = ('+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs +type=crs')
        from pyproj import CRS
        frame_wkt = CRS(frame).to_wkt()
        esri = ('PROJCS["Unknown_based_on_WGS_84_ellipsoid_Transverse_Mercator",GEOGCS["unknown",'
                'DATUM["D_Unknown_based_on_WGS_84_ellipsoid",SPHEROID["WGS 84",6378137,298.257223563]],'
                'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
                'PARAMETER["latitude_of_origin",41.8972],PARAMETER["central_meridian",-87.62443],'
                'PARAMETER["scale_factor",1],PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
                'UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH]]')
        bounds = (-30000, -20000, -29488, -19488)
        self.assertTrue(same_crs(esri, frame_wkt, bounds))
        self.assertFalse(same_crs(esri.replace('-87.62443', '-87.6'), frame_wkt, bounds))


if __name__ == '__main__':
    unittest.main()
