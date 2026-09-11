import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from metric_world import roof_shell_floor


class RoofShellTests(unittest.TestCase):
    def test_measured_setback_keeps_roof_and_exposed_wall(self):
        ground = np.zeros((7, 7), int)
        mask = np.zeros((7, 7), bool)
        mask[1:6, 1:6] = True
        top = np.full((7, 7), 5)
        top[2:5, 3:5] = 10
        low = roof_shell_floor(top, mask, ground)
        self.assertEqual(low[1, 1], 1)  # Exterior wall.
        self.assertEqual(low[3, 2], 5)  # Lower roof, not a ten-metre box.
        self.assertEqual(low[3, 3], 6)  # Exposed tower side above the lower roof.
        self.assertEqual(top[3, 3], 10)

    def test_flat_interior_contains_only_cap(self):
        g = np.full((5, 5), 3)
        t = np.full((5, 5), 12)
        mask = np.ones((5, 5), bool)
        self.assertTrue(np.all(roof_shell_floor(t, mask, g) == 12))


if __name__ == '__main__':
    unittest.main()
