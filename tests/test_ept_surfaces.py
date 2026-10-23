"""EPT scan surfaces, the shared EPT node cache, and the plan-priority dispatch route."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import detail_points
from ept_surfaces import fill, rasters
from chicago_tiles import Journal
import regional_generate
from regional_fixtures import plan_for

GRID = {'west': 1000.0, 'north': 2000.0, 'size': 32, 'crs': 'EPSG:26916'}


def points(cells, z, cls):
    """One return at the centre of each (row, col) cell."""
    rows, cols = np.array(cells).T
    xyz = np.column_stack((GRID['west'] + cols + .5, GRID['north'] - rows - .5, np.full(len(rows), z, float)))
    return xyz, np.full(len(rows), cls, np.uint8)


class EptSurfaceTests(unittest.TestCase):
    def test_roof_is_the_building_return_ground_fills_under_it_and_trees_never_lift_the_dsm(self):
        everywhere = [(r, c) for r in range(32) for c in range(32)]
        roof = [(r, c) for r in range(10, 20) for c in range(10, 20)]
        tree = [(r, c) for r in range(2, 5) for c in range(2, 5)]
        ground = [cell for cell in everywhere if cell not in roof]
        parts = [points(ground, 100.0, 2), points(roof, 110.0, 6), points(tree, 118.0, 5)]
        xyz = np.concatenate([p[0] for p in parts]);cls = np.concatenate([p[1] for p in parts])
        dtm, dsm, count = rasters(xyz, cls, GRID)
        self.assertTrue(np.isnan(dtm[15, 15]) and count[15, 15] == 0)
        dtm, dsm = fill(dtm, dsm)
        self.assertEqual(dtm[15, 15], 100.0)      # ground under the roof from the nearest ground return
        self.assertEqual(dsm[15, 15], 110.0)      # roof from the building return
        self.assertEqual(dsm[3, 3], 100.0)        # vegetation excluded: the DSM is the ground there
        self.assertTrue(np.isfinite(dsm).all())

    def test_single_cell_dsm_gap_takes_its_highest_neighbour(self):
        roof = [(r, c) for r in range(10, 13) for c in range(10, 13) if (r, c) != (11, 11)]
        ground = [(r, c) for r in range(32) for c in range(32) if not (10 <= r < 13 and 10 <= c < 13)]
        parts = [points(ground, 100.0, 2), points(roof, 107.0, 6)]
        dtm, dsm, _ = rasters(np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts]), GRID)
        self.assertTrue(np.isnan(dsm[11, 11]))
        _, dsm = fill(dtm, dsm)
        self.assertEqual(dsm[11, 11], 107.0)


class SharedNodeCacheTests(unittest.TestCase):
    def test_cached_getter_uses_the_ept_buildings_layout(self):
        calls = []

        def frozen_get(url, path, limit):
            calls.append((url, str(path), limit))
            return b'{}'
        with patch('regional_scans.frozen_get', frozen_get):
            get = detail_points.getter('https://usgs-lidar-public.s3.amazonaws.com/IL_X_2022', '/cache/ept')
            get('ept.json', 1);get('ept-hierarchy/0-0-0-0.json', 1);get('ept-data/3-1-2-0.laz', 1)
        self.assertEqual([c[1] for c in calls], ['/cache/ept/IL_X_2022/ept.json', '/cache/ept/IL_X_2022/hierarchy/0-0-0-0.json',
                                                 '/cache/ept/IL_X_2022/data/3-1-2-0.laz'])
        self.assertEqual([c[2] for c in calls], [4 * 2**20, 4 * 2**20, 64 * 2**20])
        self.assertEqual(calls[2][0], 'https://usgs-lidar-public.s3.amazonaws.com/IL_X_2022/ept-data/3-1-2-0.laz')


class ShaMemoTests(unittest.TestCase):
    def test_hash_is_remembered_by_identity_and_redone_after_a_change(self):
        from region_expansion import sha, sha_memo
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'index.sqlite';path.write_bytes(b'one')
            first = sha_memo(path)
            self.assertEqual(first, sha(path))
            with patch('region_expansion.sha', side_effect=AssertionError('re-hashed')):
                self.assertEqual(sha_memo(path), first)  # remembered
            path.write_bytes(b'two!')
            self.assertEqual(sha_memo(path), sha(path))  # identity changed: hashed again
            self.assertNotEqual(sha_memo(path), first)


class PriorityRouteTests(unittest.TestCase):
    def test_ept_route_clears_locality_ranks_so_plan_priority_decides(self):
        plan = plan_for(['0_0', '1_0', '2_0'])
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            journal = Journal(control / 'jobs.sqlite', plan)
            journal.prioritize_tiles(['2_0', '1_0'], 'locality-v1', stages=('sources', 'geometry', 'appearance'))
            journal.close()
            record = regional_generate.apply_locality_route(control, plan, plan['frame'], {}, log=lambda *_: None)
            self.assertEqual(record['route'], 'plan priority')
            journal = Journal(control / 'jobs.sqlite', plan)
            ranked = journal.db.execute('SELECT COUNT(*) FROM jobs WHERE route_rank IS NOT NULL').fetchone()[0]
            first = journal.claim('sources', 'worker', now=1)
            journal.close()
            marker = json.loads((control / 'route-locality.json').read_text())
        self.assertEqual(ranked, 0)
        self.assertEqual(first['tile'], '0_0')  # highest plan priority (nearest the origin)
        self.assertEqual((marker['version'], marker['route']), (regional_generate.ROUTE_VERSION, 'plan priority'))


if __name__ == '__main__':
    unittest.main()
