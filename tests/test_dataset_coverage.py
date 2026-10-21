import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dataset_coverage import dataset_cells, projection, read_dataset_coverage

TM = '+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m'


def bitmap(*chunks):
    bits = bytearray(128)
    for cx, cz in chunks:
        i = cx + cz * 32
        bits[i >> 3] |= 1 << (i & 7)
    return base64.b64encode(bytes(bits)).decode()


def index(*regions):
    return json.dumps({'schema': 'earthcraft.world-regions.v1', 'regions': list(regions)}).encode()


class DatasetCoverageTests(unittest.TestCase):
    def test_only_cells_with_chunks_are_listed(self):
        region = {'x': -1, 'z': 2, 'chunk_bitmap': bitmap((0, 0), (17, 31))}
        self.assertEqual(dataset_cells(index(region), set()), {(-2, 4), (-1, 5)})

    def test_published_tile_adds_its_four_cells(self):
        self.assertEqual(dataset_cells(index(), {'3_-1'}), {(6, -2), (7, -2), (6, -1), (7, -1)})

    def test_coverage_matches_dataset_not_local_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'configs').mkdir()
            (root / 'configs/world-frame.json').write_text(json.dumps({'crs': TM, 'west': -32, 'north': 33}))
            region = {'x': 0, 'z': 0, 'chunk_bitmap': bitmap((0, 0))}
            world = read_dataset_coverage(root, fetch=lambda _: ('a' * 40, index(region), {'1_1'}))
            self.assertEqual(world['unique_cells'], 5)
            self.assertEqual(world['dataset_revision'], 'a' * 40)
            self.assertFalse(world['current_block_fill_verified'])
            self.assertNotIn(str(root), json.dumps(world))
            # Cells carry only ids; the site places them from the published projection.
            self.assertEqual(world['cells'][0], {'tile_id': '0_0'})
            self.assertEqual(world['projection'], {'method': 'transverse_mercator_wgs84', 'lat_0': 41.8972,
                                                   'lon_0': -87.62443, 'k': 1.0, 'west': -32, 'north': 33})

    def test_frames_the_site_cannot_invert_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Transverse Mercator'):
            projection({'crs': 'EPSG:3857', 'west': 0, 'north': 0})


if __name__ == '__main__':
    unittest.main()
