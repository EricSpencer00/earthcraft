import tempfile
import unittest
from pathlib import Path
import sys

import nbtlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from world_border import bounds_for_plan, bounds_for_tiles, update_world_border


class WorldBorderTests(unittest.TestCase):
    def test_level_and_runtime_border_use_the_same_envelope(self):
        with tempfile.TemporaryDirectory() as folder:
            world = Path(folder)
            nbtlib.File({'Data': nbtlib.Compound({})}, gzipped=True).save(world / 'level.dat')
            bounds = bounds_for_tiles([
                {'world_offset_xz': [-256, -256], 'size_m': 256},
                {'world_offset_xz': [0, 0], 'size_m': 256},
            ])
            update_world_border(world, bounds)

            level = nbtlib.load(world / 'level.dat')['Data']
            runtime = nbtlib.load(world / 'data/world_border.dat')['data']
            self.assertEqual(float(level['BorderCenterX']), float(runtime['center_x']))
            self.assertEqual(float(level['BorderCenterZ']), float(runtime['center_z']))
            self.assertEqual(float(level['BorderSize']), float(runtime['size']))
            self.assertEqual(float(runtime['lerp_target']), 4608.0)

    def test_plan_border_does_not_shrink_to_materialized_tiles(self):
        plan = {'tiles': [
            {'world_offset_xz': [-4096, -2048], 'size': 256},
            {'world_offset_xz': [8192, 6144], 'size': 256},
        ]}
        planned = bounds_for_plan(plan)
        materialized = bounds_for_tiles(plan['tiles'][:1])
        self.assertEqual(planned['min_x'], -4096)
        self.assertEqual(planned['max_x'], 8448)
        self.assertGreater(planned['size'], materialized['size'])


if __name__ == '__main__':
    unittest.main()
