from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from roof_color import matching_grid, roof_mask


class RoofColorTests(unittest.TestCase):
    def test_only_valid_nonvegetated_shell_roofs_are_selected(self):
        owner = np.array([[1, 0], [2, 3]])
        valid = np.array([[1, 1], [1, 0]])
        shadow = np.array([[0, 0], [1, 0]])
        vegetation = np.array([[0, 0], [0, 0]])
        np.testing.assert_array_equal(roof_mask(owner, valid, shadow, vegetation),
                                      np.array([[True, False], [False, False]]))

    def test_grid_must_match_exactly(self):
        source = {'crs': 'local', 'west': 0, 'north': 16, 'size': 16}
        matching_grid({'source_grid': dict(source)}, source)
        with self.assertRaisesRegex(ValueError, 'exactly'):
            matching_grid({'source_grid': {**source, 'north': 17}}, source)

    def test_mismatched_masks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'share'):
            roof_mask(np.ones((2, 2)), np.ones((2, 3)), np.ones((2, 3)), np.ones((2, 3)))


if __name__ == '__main__':
    unittest.main()
