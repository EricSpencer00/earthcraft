import base64
import unittest
import zlib

import site_cache


def decode(m):
    bits = zlib.decompress(base64.b64decode(m['bitmap']))
    return {f"{m['x0'] + i % m['width']}_{m['z0'] + i // m['width']}"
            for i in range(m['width'] * m['height']) if bits[i >> 3] >> (i & 7) & 1}


class SiteCacheTest(unittest.TestCase):
    def test_bitmap_round_trips_ids(self):
        ids = {'-3_-2', '0_0', '5_1', '-3_1'}
        m = site_cache.bitmap(ids)
        self.assertEqual(decode(m), ids)
        self.assertEqual(m['count'], 4)
        self.assertIsNone(site_cache.bitmap([]))

    def test_world_json_drops_id_lists_for_bitmaps(self):
        snapshot = {'cells': [{'id': 'legacy'}], 'updated_utc': 'x', 'generated_world': {
            'cells': [{'tile_id': '1_2'}, {'tile_id': '2_2'}], 'v2_tiles': ['0_1'], 'lake_tiles': [], 'cell_size_m': 256}}
        world = site_cache.world_json(snapshot)
        self.assertNotIn('cells', world)
        gw = world['generated_world']
        self.assertNotIn('cells', gw)
        self.assertEqual(decode(gw['cells_bitmap']), {'1_2', '2_2'})
        self.assertEqual(decode(gw['v2_bitmap']), {'0_1'})
        self.assertIsNone(gw['lake_bitmap'])

    def test_refuses_to_drop_per_cell_fields(self):
        with self.assertRaises(SystemExit):
            site_cache.world_json({'generated_world': {'cells': [{'tile_id': '1_2', 'state': 'failed'}]}})


if __name__ == '__main__':
    unittest.main()
