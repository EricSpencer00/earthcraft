"""Lake water tiles: bed from the bathymetry crop, deterministic chunks, index digests."""
import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import lake_tiles as lt  # noqa: E402
from contributions import chunk_digest  # noqa: E402
from inspect_world import chunks  # noqa: E402

FRAME = {'crs': '+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m',
         'west': -32, 'north': 33}
FIXTURE = Path(__file__).resolve().parent / 'fixtures/anvil/water-tower-r.0.0.region'


def crop(folder, depth_m):
    grid = np.full((4, 4), int(depth_m * 10), '<i2')
    (Path(folder) / 'bathymetry.bin.gz').write_bytes(gzip.compress(grid.tobytes()))
    (Path(folder) / 'bathymetry.json').write_text(json.dumps(
        {'width': 4, 'height': 4, 'west': -87.64443, 'north': 41.9172, 'step_deg': 0.01, 'scale': 0.1}))


class LakeTileTests(unittest.TestCase):
    def test_bed_follows_depth_and_clamps_at_the_world_floor(self):
        with tempfile.TemporaryDirectory() as folder:
            crop(folder, -50)
            bed, clamped = lt.Bathymetry(folder, FRAME).bed(0, 0)
            self.assertTrue((bed == 10).all())
            self.assertEqual(clamped, 0)
            crop(folder, -300)
            bed, clamped = lt.Bathymetry(folder, FRAME).bed(0, 0)
            self.assertTrue((bed == lt.MIN_Y + lt.BED_DEPTH - 1).all())
            self.assertEqual(clamped, lt.TILE * lt.TILE)

    def test_chunk_holds_sand_bed_water_and_air(self):
        _, sample, _ = next(chunks(FIXTURE))
        tag = lt.chunk_tag(copy.deepcopy(sample), 3, 4, np.full((16, 16), 10))
        self.assertEqual((int(tag['xPos']), int(tag['zPos'])), (3, 4))
        palettes = {int(s['Y']): [str(p['Name']) for p in s['block_states']['palette']] for s in tag['sections']}
        self.assertEqual(palettes[1], ['minecraft:water'])
        self.assertEqual(palettes[0], ['minecraft:air', 'minecraft:sand', 'minecraft:water'])
        self.assertEqual(palettes[3], ['minecraft:air', 'minecraft:water'])
        self.assertNotIn(-1, palettes)

    def test_rebuilding_a_tile_gives_the_same_chunks(self):
        _, sample, _ = next(chunks(FIXTURE))
        with tempfile.TemporaryDirectory() as folder:
            crop(folder, -40)
            bathy = lt.Bathymetry(folder, FRAME)
            first, bed, _ = lt.region_bytes(bathy, copy.deepcopy(sample), '0_0', folder)
            second, _, _ = lt.region_bytes(bathy, copy.deepcopy(sample), '0_0', folder)
            self.assertEqual(chunk_digest(first), chunk_digest(second))
            self.assertEqual(bed, [20, 20])


if __name__ == '__main__':
    unittest.main()
