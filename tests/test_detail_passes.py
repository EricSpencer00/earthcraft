"""Street-detail passes on synthetic fixtures: trees, curbs/paint, facades, buildings, water."""
from pathlib import Path
import sys
import unittest

import numpy as np
from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import detail_buildings as bd
import detail_facade as fc
import detail_street as st
import detail_trees as tr
from detail_context import Tile
from detail_water import water_polygon

FRAME = {'crs': 'EPSG:32616', 'west': 0, 'north': 0, 'vertical_offset_m': 0}


def crown(shape, r, c, height, radius):
    rr, cc = np.indices(shape)
    d2 = (rr - r) ** 2 + (cc - c) ** 2
    return np.where(d2 <= radius ** 2, height * np.sqrt(np.clip(1 - d2 / radius ** 2, 0, 1)) + 0.0, 0)


class TreeTests(unittest.TestCase):
    def test_two_crowns_give_two_tops_and_separate_crowns(self):
        chm = np.maximum(crown((40, 40), 12, 12, 12, 5), crown((40, 40), 12, 25, 8, 4))
        smooth = chm.copy()
        tops = tr.detect_tops(smooth)
        self.assertEqual(len(tops), 2)
        labels = tr.segment(smooth, tops)
        self.assertEqual(set(np.unique(labels)) - {0}, {2, 3})
        self.assertTrue((labels[12, 9:15] == 2).all() and (labels[12, 23:28] == 3).all())

    def test_tree_voxels_are_rooted_persistent_and_shaped(self):
        chm = crown((30, 30), 15, 15, 10, 4)
        labels = np.where(chm > 0, 2, 0)
        tree = {'id': 't', 'label': 2, 'trunk': (15, 15), 'height': 10, 'crown_base': 3, 'radius': 4, 'shape': 'oak'}
        ground = np.full((30, 30), 64)
        cells, gy = tr.tree_voxels(tree, labels, chm, ground, 100, 200)
        self.assertEqual(gy, 64)
        trunk = [k for k, v in cells.items() if v[1] == 'trunk_estimated']
        self.assertEqual(min(y for _, y, _ in trunk), 65)
        self.assertTrue(all(x == 115 and z == 215 for x, _, z in trunk))
        leaves = [v[0] for v in cells.values() if 'leaves' in v[0]]
        self.assertTrue(leaves and all('persistent=true' in s for s in leaves))
        self.assertTrue(any('distance=1' in s for s in leaves))
        self.assertEqual(max(y for _, y, _ in cells), 74)
        self.assertTrue(any(v[1] == 'canopy_observed' for v in cells.values()))
        self.assertEqual(tr.classify({'height': 14, 'radius': 2, 'top_fraction': 0.05}), 'spruce')
        self.assertEqual(tr.classify({'height': 9, 'radius': 5, 'top_fraction': 0.3}), 'oak')
        self.assertTrue(tr.admit({'area': 3, 'height': 3, 'std': 0.1, 'p10': 2.9}, (0, 0), np.ones((1, 1), bool)))

    def test_trunks_avoid_roads_and_buildings(self):
        labels = np.zeros((10, 10), int); labels[2:8, 2:8] = 2
        blocked = np.zeros((10, 10), bool); blocked[:, :5] = True
        r, c, shift = tr.place_trunk(5, 4, labels, 2, blocked)
        self.assertEqual((r, c), (5, 5))
        self.assertIsNone(tr.place_trunk(5, 4, labels, 2, np.ones((10, 10), bool)))


class StreetTests(unittest.TestCase):
    def test_curb_offset_found_on_crowned_road_profile(self):
        tile = Tile(FRAME, 0, 0, 64)
        cell = 0.5
        n = 128
        z = (np.arange(n) + 0.5) * cell
        offset = np.abs(z - 32)
        profile = np.where(offset < 7, 180.3 - 0.02 * offset, 180.32)   # 2% crown, 0.16 m curb at 7 m
        dtm = np.repeat(profile[:, None], n, axis=1)
        line = LineString([(5, 32), (60, 32)])
        result = st.curb_offsets(line, dtm, tile, cell, np.zeros((n, n), bool))
        self.assertAlmostEqual(result[1]['offset'], 7.0, delta=0.75)
        self.assertAlmostEqual(result[-1]['offset'], 7.0, delta=0.75)

    def test_paint_noise_and_widths(self):
        half = np.zeros((20, 20), bool)
        half[2, 2] = True                         # isolated hit -> noise
        half[10, 4:12] = True                     # a painted line
        blocks = st.paint_blocks(half)
        self.assertFalse(blocks[1, 1])
        self.assertTrue(blocks[5, 2:6].all())
        self.assertEqual(st.fallback_half_width({'lanes': '2', 'highway': 'residential'})[0], 3.2 + 1.2)

    def test_zebra_bars_alternate_along_the_crossing(self):
        tile = Tile(FRAME, 0, 0, 32)
        road = np.zeros((32, 32), bool); road[10:20, :] = True
        osm = [{'id': 'way/1', 'type': 'way', 'tags': {'footway': 'crossing', 'crossing:markings': 'zebra'},
                'geom': LineString([(16.5, 8), (16.5, 22)])}]
        (cw,) = st.crosswalk_cells(osm, road, tile)
        rows = sorted({z for _, z in cw['cells']})
        self.assertTrue(all(10 <= z < 20 for z in rows))
        self.assertTrue(all((z - rows[0]) % 2 == 0 for z in rows))

    def test_lamp_shape_hangs_a_lantern_over_the_road(self):
        blocks = st.furniture_blocks('street_lamp', 0, 0, 64, (1, 0))
        lantern = [b for b in blocks if 'lantern' in b[3]]
        self.assertEqual(lantern, [(1, 70, 0, 'minecraft:lantern[hanging=true,waterlogged=false]')])
        self.assertIn('east=low', [b for b in blocks if b[1] == 71 and b[0] == 0][0][3])


class FacadeTests(unittest.TestCase):
    def test_colour_parsing_and_palette(self):
        self.assertEqual(fc.parse_colour('#9C8679'), (156, 134, 121))
        self.assertEqual(fc.parse_colour('Grey'), (128, 128, 128))
        self.assertIsNone(fc.parse_colour('#zz'))
        self.assertEqual(fc.nearest_block((10, 10, 12), fc.WALLS)[0], 'minecraft:black_concrete')
        self.assertEqual(fc.glass_for((20, 20, 20)), 'minecraft:gray_stained_glass')

    def test_window_rhythm_from_a_rectified_photo(self):
        img = np.full((60, 90, 3), 200.0)
        for row in range(0, 60, 12):
            for col in range(0, 90, 9):
                img[row + 3:row + 9, col + 2:col + 6] = 30
        obs = fc.observe_from_image(img)
        self.assertEqual(obs['column_period_px'], 9)
        self.assertEqual(obs['row_period_px'], 12)
        self.assertGreater(obs['colour_rgb'][0], 150)

    def test_exterior_faces_only_touch_outside_air(self):
        vol = np.zeros((6, 6, 5), np.uint8)
        vol[1:5, 1:5, 1:4] = 1
        solid = vol > 0
        footprint = np.zeros((6, 6), bool); footprint[1:5, 1:5] = True
        faces = fc.exterior_faces(vol, solid, footprint, np.zeros((6, 6), int), 0, 1, 3)
        cells = {(r, c) for r, c, _, _, _ in faces}
        self.assertNotIn((2, 2), cells)
        self.assertIn((1, 2), cells)


class BuildingTests(unittest.TestCase):
    def test_regularize_closes_shell_keeps_heights_and_drops_fragments(self):
        tile = Tile(FRAME, 0, 0, 32)
        names = ['minecraft:air', 'minecraft:stone', 'minecraft:light_gray_concrete', 'minecraft:gray_concrete']
        ylo, H = 60, 40
        vol = np.zeros((32, 32, H), np.uint8)
        vol[:, :, :5] = 1                                   # terrain up to y=64
        ground = np.full((32, 32), 64)
        vol[8:20, 8:20, 5:25] = 2                           # building y 65..84
        vol[9:19, 9:19, 6:24] = 0                           # hollow
        vol[8, 10:14, 10:20] = 0                            # a hole in a wall
        vol[12, 12, 12] = 2                                 # interior clutter
        vol[27, 27, 15] = 3                                 # floating fragment
        world = {'vol': vol, 'names': names, 'ylo': ylo, 'ground_y': ground}
        fp = [{'id': 'way/1', 'geom': box(8, 8, 20, 20), 'tags': {}}]
        edits, report = bd.regularize(world, fp, tile, (0, 0, 32, 32), np.zeros((32, 32), bool))
        self.assertEqual(report['regularized'], 1)
        added = {(x, y, z) for x, y, z, s, e, _ in edits if e is None}
        self.assertIn((12, 70, 8), added)                   # hole closed
        removed = {(x, y, z) for x, y, z, s, e, f in edits if s == 'minecraft:air'}
        self.assertIn((12, 72, 12), removed)                # clutter gone
        self.assertIn((27, 75, 27), removed)                # fragment gone
        self.assertFalse(any(y > 84 for _, y, _ in added))  # no height change
        top, coverage = bd.roof_heights(np.where(np.ones((5, 5)), 70.0, np.nan), np.ones((5, 5), bool))
        self.assertTrue((top == 70).all() and coverage == 1.0)


class WaterTests(unittest.TestCase):
    def test_shoreline_split_keeps_lake_side_and_checks_land(self):
        tile = Tile(FRAME, 0, 0, 100)
        shore = [LineString([(40, -5), (60, 105)])]
        poly = water_polygon(tile, shore, [box(80, 40, 90, 50)], [(95, 5)], land_seeds=[(5, 50)])
        self.assertTrue(poly.contains(Polygon([(90, 1), (99, 1), (99, 9)]).centroid))
        self.assertFalse(poly.contains(box(81, 41, 89, 49).centroid))
        with self.assertRaises(ValueError):
            water_polygon(tile, shore, [], [(95, 5)], land_seeds=[(95, 95)])


if __name__ == '__main__':
    unittest.main()
