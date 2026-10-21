"""ChunkCells.get returns exactly what the per-chunk mask returned, in the same order."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chunk_cells import ChunkCells  # noqa: E402


class ChunkCellsTests(unittest.TestCase):
    def test_matches_the_mask_including_negative_coordinates_and_empty_chunks(self):
        rng = np.random.default_rng(7)
        cells = np.stack([rng.integers(-200, 600, 50_000), rng.integers(-64, 300, 50_000),
                          rng.integers(-300, 512, 50_000)], axis=1)
        index = ChunkCells(cells)
        for cx in range(-14, 38):
            for cz in range(-20, 33):
                expected = cells[(cells[:, 0] // 16 == cx) & (cells[:, 2] // 16 == cz)]
                np.testing.assert_array_equal(index.get(cx, cz), expected)
        self.assertEqual(len(index.get(10_000, 0)), 0)

    def test_empty_input(self):
        self.assertEqual(len(ChunkCells(np.zeros((0, 3), int)).get(0, 0)), 0)


if __name__ == '__main__':
    unittest.main()
