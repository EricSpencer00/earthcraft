import json
from pathlib import Path
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pilot_photo_inputs import decode_cluster, download


class PhotoInputTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.dumps([{'cameras': {}, 'shots': {}, 'points': {},
                               'reference_lla': {'latitude': 41, 'longitude': -87, 'altitude': 0}}]).encode()

    def test_real_source_zlib_decodes_without_claiming_world_registration(self):
        decoded, records = decode_cluster(zlib.compress(self.raw))
        self.assertEqual(decoded, self.raw)
        self.assertEqual(records[0]['reference_lla']['altitude'], 0)
        self.assertNotIn('world_from_cluster', records[0])

    def test_truncated_and_concatenated_streams_are_rejected(self):
        compressed = zlib.compress(self.raw)
        for raw in (compressed[:-2], compressed + compressed):
            with self.assertRaisesRegex(ValueError, 'Bounded calibration stream'):
                decode_cluster(raw)

    def test_decompression_bomb_is_bounded(self):
        with self.assertRaisesRegex(ValueError, 'Bounded calibration stream'):
            decode_cluster(zlib.compress(self.raw), limit=20)

    def test_unexpected_source_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'reconstruction schema'):
            decode_cluster(b'{"colors_applied": 100}')

    def test_non_source_origin_is_rejected_before_network_access(self):
        with self.assertRaisesRegex(ValueError, 'content origin'):
            download('https://example.com/private.jpg', 100)


if __name__ == '__main__':
    unittest.main()
