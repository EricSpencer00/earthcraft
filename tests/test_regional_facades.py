"""Keyless facades: exposed walls, floors, window bays, the street door and wall material."""
from pathlib import Path
import sys
import unittest

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import regional_facades as rf

NAMES = ['minecraft:air', 'minecraft:stone_bricks', 'minecraft:grass_block', 'minecraft:gray_concrete']
PALETTE = {'light_gray_concrete': np.array([150., 150., 150.]), 'red_concrete': np.array([140., 30., 30.])}


def scene(levels=None, colour=None):
    """A 10 x 8 house (x 5..14, z 4..11) of solid stone bricks y 71..78 on grass at y 70, a road at z 0..1."""
    size, ylo, height = 32, 60, 40
    vol = np.zeros((size, size, height), np.uint16)
    vol[:, :, 70 - ylo] = 2
    vol[0:2, :, 70 - ylo] = 3
    vol[4:12, 5:15, 71 - ylo:79 - ylo] = 1
    tags = {'building': 'house'}
    if levels:
        tags['building:levels'] = str(levels)
    if colour:
        tags['building:colour'] = colour
    shapes = [(box(5, 4, 15, 12), {'id': 'osm/1', 'tags': tags})]
    lab = rf.labels(shapes, size)
    ground = np.full((size, size), 70, np.int32)
    top = np.where(lab > 0, 78, 70).astype(np.int32)
    surface = np.asarray(NAMES, dtype=object)[vol[:, :, 70 - ylo]]
    return vol, lab, ground, top, surface, shapes, ylo


class FacadeTests(unittest.TestCase):
    def test_exposed_walls_are_the_outside_faces_below_the_roof_edge(self):
        vol, lab, ground, top, surface, shapes, ylo = scene()
        solid = np.array([n != 'minecraft:air' for n in NAMES])
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        self.assertEqual(set(cells['y']), set(range(71, 78)))         # 78 is the roof edge
        self.assertEqual(len(cells['y']), 7 * 2 * (10 + 8))             # perimeter faces x storeys
        self.assertTrue(cells['corner'][(cells['x'] == 5) & (cells['dx'] == 0)].all())

    def test_floors_windows_door_and_estimated_wall(self):
        vol, lab, ground, top, surface, shapes, ylo = scene()
        solid = np.array([n != 'minecraft:air' for n in NAMES])
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        edits, buildings = rf.plan(cells, shapes, ground, top, surface, 0, 0, PALETTE)
        house = buildings['osm/1']
        self.assertEqual((house['floors'], house['floors_role'], house['style'], house['wall_role']),
                         (2, 'estimated', 'house', 'estimated'))      # 8 m / 3.3 m
        doors = [(x, y, z) for x, y, z, _, role, _ in edits if role == 'door']
        self.assertEqual(len(doors), 2)
        self.assertTrue(all(z == 4 for _, _, z in doors))              # the face toward the road (north)
        windows = [(x, y, z) for x, y, z, _, role, _ in edits if role == 'window']
        self.assertTrue(windows and all(x % 3 == 1 or z % 3 == 1 for x, _, z in windows))
        self.assertFalse(any(x in (5, 14) and z in (4, 11) for x, _, z in windows))  # corners stay solid
        self.assertEqual(sorted({y for _, y, _ in windows}), [72, 73, 76, 77])  # 4 m storeys: 2-high windows

    def test_tagged_levels_and_colour_win(self):
        vol, lab, ground, top, surface, shapes, ylo = scene(levels=3, colour='red')
        solid = np.array([n != 'minecraft:air' for n in NAMES])
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        _, buildings = rf.plan(cells, shapes, ground, top, surface, 0, 0, PALETTE)
        house = buildings['osm/1']
        self.assertEqual((house['floors'], house['floors_role']), (3, 'tagged'))
        self.assertEqual((house['wall'], house['wall_role']), ('minecraft:red_concrete', 'tagged'))


class RecordedWallTests(unittest.TestCase):
    def test_assessor_wall_and_storeys_rank_between_tags_and_estimates(self):
        vol, lab, ground, top, surface, shapes, ylo = scene()
        solid = np.array([n != 'minecraft:air' for n in NAMES])
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        _, buildings = rf.plan(cells, shapes, ground, top, surface, 0, 0, PALETTE, recorded={1: ('frame', 1)})
        house = buildings['osm/1']
        self.assertEqual((house['wall_role'], house['floors'], house['floors_role']), ('recorded', 1, 'recorded'))
        self.assertIn(house['wall'], rf.RECORDED['frame'])
        vol, lab, ground, top, surface, shapes, ylo = scene(levels=3, colour='red')
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        _, buildings = rf.plan(cells, shapes, ground, top, surface, 0, 0, PALETTE, recorded={1: ('frame', 1)})
        self.assertEqual((buildings['osm/1']['wall_role'], buildings['osm/1']['floors_role']), ('tagged', 'tagged'))


if __name__ == '__main__':
    unittest.main()
