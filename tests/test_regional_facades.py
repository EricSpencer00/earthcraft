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


class VolumeExposureTests(unittest.TestCase):
    def test_setbacks_and_walls_above_a_lower_neighbour_are_exposed(self):
        vol, lab, ground, top, surface, shapes, ylo = scene()
        vol[6:10, 8:12, 79 - ylo:90 - ylo] = 1        # a core rising from the podium roof to y 89
        vol[4:12, 15:20, 71 - ylo:75 - ylo] = 1       # a lower building touching the east face, to y 74
        shapes = shapes + [(box(15, 4, 20, 12), {'id': 'osm/2', 'tags': {'building': 'yes'}})]
        lab = rf.labels(shapes, 32)
        solid = np.array([n != 'minecraft:air' for n in NAMES])
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        core = (cells['building'] == 1) & (cells['y'] >= 79)
        self.assertEqual(set(cells['y'][core]), set(range(79, 89)))   # setback faces up to the core's roof edge
        east = (cells['building'] == 1) & (cells['x'] == 14) & (cells['dx'] == 1)
        self.assertEqual(set(cells['y'][east]), {75, 76, 77})         # only above the neighbour's roof

    def test_the_inside_of_a_hollow_shell_is_not_a_wall(self):
        vol, lab, ground, top, surface, shapes, ylo = scene()
        vol[5:11, 6:14, 71 - ylo:78 - ylo] = 0                         # hollow under the y 78 roof slab
        solid = np.array([n != 'minecraft:air' for n in NAMES])
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        self.assertEqual(len(cells['y']), 7 * 2 * (10 + 8))           # still only the outer perimeter faces

    def test_part_colour_paints_a_black_glass_curtain_wall(self):
        glass = {'black_stained_glass': [25, 25, 30], 'black_concrete': [8, 10, 15],
                 'light_gray_stained_glass': [150, 150, 150], 'light_gray_concrete': [125, 125, 115]}
        vol, lab, ground, top, surface, shapes, ylo = scene()
        vol[4:12, 5:15, 79 - ylo:110 - ylo] = 1                       # make it a 39 m tower
        top = np.where(lab > 0, 110, 70).astype(np.int32)
        solid = np.array([n != 'minecraft:air' for n in NAMES])
        cells = rf.exposed(lab, ground, vol, solid, ylo)
        part = [(box(5, 4, 15, 12), {'id': 'osm/9', 'tags': {'building:part': 'yes', 'building:colour': 'black',
                                                              'building:material': 'glass'}})]
        cells['part'] = rf.labels(part, 32)[cells['z'], cells['x']]
        edits, buildings = rf.plan(cells, shapes, ground, top, surface, 0, 0, glass, part_shapes=part)
        states = {(role, state) for _, _, _, state, role, _ in edits}
        self.assertIn(('window', 'minecraft:black_stained_glass'), states)
        self.assertIn(('wall', 'minecraft:black_concrete'), states)
        self.assertNotIn(('window', rf.GLASS['tall']), states)
        self.assertEqual(buildings['osm/1']['parts_tagged'], 1)

    def test_a_notch_on_a_face_is_a_ledge_and_a_roof_is_not(self):
        from roof_color import roof_planes
        lab = np.zeros((12, 12), np.int32);lab[2:10, 2:10] = 1
        top = np.where(lab > 0, 80, 60).astype(np.int32);top[2, 5] = 77   # a notch in the north edge
        z, x = rf.ledges(lab, top, roof_planes(lab, top))
        self.assertEqual(list(zip(z.tolist(), x.tolist())), [(2, 5)])


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


class AssessorLabelTests(unittest.TestCase):
    def test_portal_labels_map_to_walls_and_storeys(self):
        from unittest.mock import patch
        lab = np.zeros((32, 32), np.int32);lab[4:12, 5:15] = 1
        meta = {'west': 0.0, 'north': 32.0, 'size': 32}
        rows = [(8.5, 24.5, 'Frame + Masonry', '1.5 Story'), (9.5, 24.5, 'Frame + Masonry', '2 Story'),
                (10.5, 23.5, 'Masonry', '2 Story'), (30.5, 1.5, 'Stucco', '1 Story')]  # the last is outside
        with patch('cook_assessor_index.walls', return_value=rows):
            self.assertEqual(rf.recorded_walls('index', meta, lab), {1: ('frame_masonry', 2)})


if __name__ == '__main__':
    unittest.main()
