"""Keyless ground cover: classes from NAIP spectra, WorldCover context and OSM areas; the edits they make."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import regional_ground as rg

N = 40


def scene():
    """Lawn everywhere; a concrete strip, an asphalt lot, a bare-soil patch, a cropland field."""
    bands = np.zeros((4, N, N), np.uint8)
    bands[:] = np.array([60, 110, 50, 200], np.uint8)[:, None, None]        # lawn: red 60, NIR 200
    bands[:, 2:4, :] = np.array([190, 190, 185, 170], np.uint8)[:, None, None]   # concrete: bright grey
    bands[:, 10:18, 5:15] = np.array([90, 92, 95, 85], np.uint8)[:, None, None]  # asphalt: darker grey
    bands[:, 25:30, 25:30] = np.array([140, 110, 80, 130], np.uint8)[:, None, None]  # bare soil: warm
    bands[:, 12:16, 20:26] = np.array([120, 122, 95, 128], np.uint8)[:, None, None]  # dry grass: as green as red
    red, nir = bands[0].astype(float), bands[3].astype(float)
    ndvi = (nir - red) / (nir + red)
    valid, shadow = np.ones((N, N), bool), np.zeros((N, N), bool)
    cover = np.zeros((N, N), np.uint8);cover[32:40, :] = rg.CROPLAND
    none = np.zeros((N, N), bool)
    return bands, valid, ndvi, shadow, cover, none


class GroundTests(unittest.TestCase):
    def test_spectra_and_land_cover_pick_the_classes(self):
        bands, valid, ndvi, shadow, cover, none = scene()
        cls = rg.classify(bands, valid, ndvi, shadow, cover, none, none, np.ones((N, N), bool))
        self.assertEqual(cls[3, 20], rg.CONCRETE)
        self.assertEqual(cls[14, 10], rg.ASPHALT)
        self.assertEqual(cls[27, 27], rg.SOIL)
        self.assertEqual(cls[36, 20], rg.FIELD)
        self.assertEqual(cls[20, 30], rg.KEEP)  # lawn stays grass
        self.assertEqual(cls[14, 22], rg.KEEP)  # dry grass is not bare soil

    def test_shadow_specks_and_non_grass_cells_keep_their_block(self):
        bands, valid, ndvi, shadow, cover, none = scene()
        shadow[2:4, :20] = True                 # half the strip is in shadow: unknown, keep grass
        bands[:, 20, 20] = np.array([180, 180, 180, 160], np.uint8)  # one grey pixel: a speck, not a sidewalk
        ndvi = (bands[3].astype(float) - bands[0]) / (bands[3].astype(float) + bands[0])
        editable = np.ones((N, N), bool);editable[14, 10] = False  # e.g. a road or building cell
        cls = rg.classify(bands, valid, ndvi, shadow, cover, none, none, editable)
        self.assertEqual(cls[3, 5], rg.KEEP)
        self.assertEqual(cls[3, 30], rg.CONCRETE)
        self.assertEqual(cls[20, 20], rg.KEEP)
        self.assertEqual(cls[14, 10], rg.KEEP)

    def test_paint_in_paving_is_paving_but_a_green_island_stays_grass(self):
        bands, valid, ndvi, shadow, cover, none = scene()
        bands[:, 12, 8:10] = np.array([200, 180, 60, 150], np.uint8)[:, None]    # yellow paint in the lot
        bands[:, 14:16, 12:14] = np.array([50, 120, 40, 220], np.uint8)[:, None, None]  # planted island
        ndvi = (bands[3].astype(float) - bands[0]) / (bands[3].astype(float) + bands[0])
        cls = rg.classify(bands, valid, ndvi, shadow, cover, none, none, np.ones((N, N), bool))
        self.assertEqual(cls[12, 8], rg.ASPHALT)
        self.assertEqual(cls[14, 12], rg.KEEP)
        self.assertEqual(cls[20, 30], rg.KEEP)  # open lawn is not a hole

    def test_shade_keeps_grass_unless_osm_tags_parking(self):
        bands, valid, ndvi, shadow, cover, none = scene()
        bands[:, 20:26, 30:36] = np.array([55, 58, 60, 50], np.uint8)[:, None, None]  # tree shadow on lawn
        bands[:, 5:9, 30:38] = np.array([55, 58, 60, 50], np.uint8)[:, None, None]    # shadow across a lot
        parking = np.zeros((N, N), bool);parking[5:9, 30:38] = True
        ndvi = (bands[3].astype(float) - bands[0]) / (bands[3].astype(float) + bands[0])
        cls = rg.classify(bands, valid, ndvi, shadow, cover, parking, none, np.ones((N, N), bool))
        self.assertEqual(cls[23, 33], rg.KEEP)
        self.assertEqual(cls[6, 34], rg.ASPHALT)
        self.assertEqual(cls[14, 10], rg.ASPHALT)  # lit asphalt is still asphalt

    def test_osm_parking_and_wetland(self):
        bands, valid, ndvi, shadow, cover, none = scene()
        parking = np.zeros((N, N), bool);parking[5:9, 30:38] = True   # tagged lot the image shows as lawn-ish grey
        bands[:, 5:9, 30:38] = np.array([120, 120, 110, 120], np.uint8)[:, None, None]
        wetland = np.zeros((N, N), bool);wetland[20:24, 0:8] = True     # lush wetland
        ndvi = (bands[3].astype(float) - bands[0]) / (bands[3].astype(float) + bands[0])
        cls = rg.classify(bands, valid, ndvi, shadow, cover, parking, wetland, np.ones((N, N), bool))
        self.assertEqual(cls[6, 34], rg.ASPHALT)
        self.assertEqual(cls[22, 4], rg.MARSH)

    def test_layer_replaces_the_grass_it_expects_and_plants_into_air(self):
        from regional_trees import tile_grid
        frame = {'crs': 'EPSG:26916', 'west': 400000, 'north': 4640000}
        grid = tile_grid({'west': 400000, 'north': 4640000, 'size': 512}, frame)
        cls = np.zeros((512, 512), np.uint8);cls[0, 0] = rg.FIELD;cls[0, 1] = rg.CONCRETE
        surface = {'ground_y': np.full((512, 512), 70, np.int32),
                   'surface_key': np.full((512, 512), 'minecraft:grass_block[snowy=false]', dtype=object)}
        layer, counts = rg.ground_layer(cls, surface, grid, frame, '2021-07-01', True)
        self.assertEqual(counts, {'ground_field': 1, 'ground_concrete': 1})
        keys = layer.keys()
        cells = {cell: (keys[state], expect) for cell, (_, state, expect, _) in layer.edits.items()}
        self.assertEqual(cells[(0, 70, 0)][0], 'minecraft:farmland[moisture=7]')
        self.assertEqual(keys[cells[(0, 70, 0)][1]], 'minecraft:grass_block[snowy=false]')
        self.assertEqual(cells[(0, 71, 0)], ('minecraft:wheat[age=7]', -1))  # air required
        self.assertEqual(cells[(1, 70, 0)][0], 'minecraft:light_gray_concrete')


if __name__ == '__main__':
    unittest.main()
