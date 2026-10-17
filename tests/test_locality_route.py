from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import regional_generate


def tile(tx, tz):
    return {'id': f'{tx}_{tz}', 'west': tx * 512, 'north': -tz * 512, 'size': 512}


class LocalityRouteTests(unittest.TestCase):
    def test_tiles_sharing_grid_members_run_together_then_row_major(self):
        tiles = [tile(x, z) for z in range(4) for x in range(3)]
        plan = {'tiles': list(reversed(tiles))}

        def members(t, frame, catalog):
            tx, tz = map(int, t['id'].split('_'))
            if tx == 2:  # a column outside the ArcGrid counties
                return None, None
            strip = f'z001{tz // 2:03}.adf'
            return {'county': 'Will'}, {'dtm': [strip], 'dsm': [strip]}
        with patch.object(regional_generate, 'grid_members', members):
            route, groups = regional_generate.locality_route(plan, {}, {}, [t['id'] for t in tiles])
        self.assertEqual(groups, 2)
        self.assertEqual(route, ['0_0', '1_0', '0_1', '1_1',      # strip 0, row-major
                                 '0_2', '1_2', '0_3', '1_3',      # strip 1
                                 '2_0', '2_1', '2_2', '2_3'])     # the rest, row-major


if __name__ == '__main__':
    unittest.main()
