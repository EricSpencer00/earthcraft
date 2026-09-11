from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from building_layer import envelope, shell_for_chunk, checked_roof_override


class BuildingLayerTests(unittest.TestCase):
    def test_setback_and_courtyard_are_not_a_max_height_box(self):
        mask = np.zeros((16, 16), bool); mask[1:15, 1:15] = True
        mask[5:8, 5:8] = False
        ground = np.zeros((16, 16), int)
        cells = [[x, 8 if x < 9 else 18, z] for z, x in np.argwhere(mask)]
        top, floor, count = envelope(cells, mask, ground, 99)
        self.assertEqual(count, int(mask.sum()))
        self.assertEqual(top[3, 3], 8)
        self.assertEqual(top[10, 12], 18)
        self.assertEqual(floor[1, 3], 1)
        self.assertEqual(floor[3, 3], 8)
        layer = dict(owner=mask.astype(int), floor=floor, top=top, protected_air=[])
        shell = shell_for_chunk(layer, 0, 0, 32, 0)
        self.assertFalse(shell[:, 6, 6].any())
        self.assertTrue(shell[1:9, 1, 3].all())
        self.assertFalse(shell[9:, 3, 3].any())

    def test_sparse_roof_fills_holes_and_preserves_every_observed_maximum(self):
        mask = np.zeros((16, 16), bool); mask[2:14, 2:14] = True
        ground = np.zeros((16, 16), int)
        points = np.array([[2, 10, 2], [13, 10, 2], [2, 10, 13], [13, 10, 13], [7, 14, 7]])
        top, floor, count = envelope(points, mask, ground, 50)
        self.assertEqual(count, 5)
        self.assertTrue((top[mask] >= 10).all())
        self.assertTrue((top[mask] <= 14).all())
        self.assertGreaterEqual(top[7, 7], 14)
        other = envelope(points[::-1], mask, ground, 50)
        np.testing.assert_array_equal(top, other[0]); np.testing.assert_array_equal(floor, other[1])

    def test_height_fallback_only_without_samples(self):
        mask = np.zeros((16, 16), bool); mask[2:14, 2:14] = True
        top, _, count = envelope([], mask, np.zeros((16, 16), int), 12)
        self.assertEqual(count, 0)
        self.assertTrue((top[mask] == 12).all())

    def test_fill_does_not_widen_sampled_spire_or_move_setback(self):
        mask = np.zeros((16, 16), bool); mask[2:14, 2:14] = True
        ground = np.zeros((16, 16), int)
        cells = np.array([[x, 10 if x < 8 else 20, z] for z, x in np.argwhere(mask)])
        cells[(cells[:, 0] == 10) & (cells[:, 2] == 8), 1] = 30
        top, _, _ = envelope(cells, mask, ground, 99)
        np.testing.assert_array_equal(top[cells[:, 2], cells[:, 0]], cells[:, 1])
        self.assertEqual(int(((top >= 30) & mask).sum()), 1)
        self.assertEqual(top[5, 7], 10)
        self.assertEqual(top[5, 8], 20)

    def test_photo_exposure_protection_in_negative_city_coordinates(self):
        layer = dict(owner=np.ones((16, 16), int), floor=np.ones((16, 16), int),
                     top=np.full((16, 16), 10), protected_air=[[-15, 5, -30]])
        shell = shell_for_chunk(layer, 0, 0, 32, 0, (-16, -32))
        self.assertFalse(shell[5, 2, 1])
        self.assertTrue(shell[5, 2, 2])
        self.assertFalse(shell[0].any())

    def test_boundary_paint_does_not_expand_geometry(self):
        layer = dict(owner=np.ones((16,16),int), geometry_owner=np.zeros((16,16),int),
                     floor=np.ones((16,16),int), top=np.full((16,16),20), protected_air=[])
        layer['geometry_owner'][4:12,4:12] = 1
        shell = shell_for_chunk(layer,0,0,32,0)
        self.assertTrue(shell[1:21,5,5].all())
        self.assertFalse(shell[:,1,1].any())

    def test_roof_override_rejects_misalignment_and_bad_height(self):
        mask = np.zeros((16, 16), bool); mask[2:12, 2:12] = True
        top = np.full(mask.shape, 20)
        selected, gates = checked_roof_override(top, mask, mask, 20)
        np.testing.assert_array_equal(selected, mask)
        self.assertFalse(gates['independent_accuracy_verified'])
        support = mask.copy(); support[5:7, 5:7] = False
        selected, _ = checked_roof_override(top, support, mask, 20)
        self.assertFalse(selected[5:7, 5:7].any())
        with self.assertRaisesRegex(ValueError, 'height'):
            checked_roof_override(top, mask, mask, 40)
        with self.assertRaisesRegex(ValueError, 'footprint'):
            checked_roof_override(top, np.roll(mask, 5, axis=1), mask, 20)
        invalid = top.astype(float); invalid[5, 5] = np.nan
        with self.assertRaisesRegex(ValueError, 'finite'):
            checked_roof_override(invalid, mask, mask, 20)


if __name__ == '__main__':
    unittest.main()
