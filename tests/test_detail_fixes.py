"""Water Tower pilot fixes: landmark protection, road patches in the local road colour, tree admission."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import detail_street as st  # noqa: E402
import detail_trees as tr  # noqa: E402
from detail_build import is_landmark  # noqa: E402


class LandmarkTests(unittest.TestCase):
    def test_heritage_tags_and_religious_buildings_are_landmarks(self):
        self.assertTrue(is_landmark({'building': 'water_tower', 'historic': 'building'}))
        self.assertTrue(is_landmark({'building': 'yes', 'heritage': '2'}))
        self.assertTrue(is_landmark({'building': 'church'}))
        self.assertFalse(is_landmark({'building': 'apartments', 'height': '257'}))
        self.assertFalse(is_landmark({'building': 'yes', 'historic': 'no'}))


class RoadPatchTests(unittest.TestCase):
    def test_patches_take_the_nearby_road_colour(self):
        base = np.full((20, 40), 'minecraft:gray_concrete', dtype=object)
        base[:, 20:] = 'minecraft:light_gray_concrete'
        base[10, 5] = base[10, 30] = 'minecraft:green_concrete'
        road = np.ones(base.shape, bool)
        patch = road & np.isin(base, list(st.ROAD_PATCH))
        block = st.local_road_block(base, road & ~patch)
        self.assertEqual(block[10, 5], 'minecraft:gray_concrete')
        self.assertEqual(block[10, 30], 'minecraft:light_gray_concrete')

    def test_road_without_a_known_surface_falls_back_to_asphalt(self):
        base = np.full((5, 5), 'minecraft:grass_block', dtype=object)
        block = st.local_road_block(base, np.zeros((5, 5), bool))
        self.assertTrue((block == st.ASPHALT).all())


class TreeAdmissionTests(unittest.TestCase):
    def crown(self, area, height):
        return {'area': area, 'height': height, 'std': 2.0, 'p10': 1.0}

    def test_small_or_low_crowns_are_rejected(self):
        near = np.zeros((3, 3), bool)
        self.assertEqual(tr.admit(self.crown(20, 9), (1, 1), near), [])
        self.assertIn('crown_too_small', tr.admit(self.crown(10, 9), (1, 1), near))
        self.assertIn('too_low', tr.admit(self.crown(20, 4.5), (1, 1), near))

    def test_tops_closer_than_a_crown_radius_merge(self):
        smooth = np.zeros((20, 20))
        smooth[10, 8] = smooth[10, 10] = 9.0   # 2 m apart: one crown
        smooth[10, 18] = 8.0                   # 8 m away: a second tree
        smooth = np.maximum(smooth, 0)
        self.assertEqual(len(tr.detect_tops(smooth)), 2)


if __name__ == '__main__':
    unittest.main()
