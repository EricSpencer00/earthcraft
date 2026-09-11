import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from facade_observation_job import GATE, admitted, hull_fraction, match_images, mutual_ratio_matches, packed_mask


class FacadeObservationJobTests(unittest.TestCase):
    def test_hull_fraction_requires_area(self):
        self.assertEqual(hull_fraction(np.array([[1, 1], [2, 2]]), (100, 100)), 0.0)
        self.assertAlmostEqual(hull_fraction(np.array([[0, 0], [100, 0], [0, 100]]), (100, 100)), .5)

    def test_gate_needs_every_independent_measure(self):
        metrics = {"mutual_ratio_matches": GATE["mutual_ratio_matches"],
                   "fundamental_inliers": GATE["fundamental_inliers"],
                   "homography_inliers": GATE["homography_inliers"],
                   "homography_hull_fractions": [.03, .03],
                   "median_homography_reprojection_px": 1.0}
        self.assertTrue(admitted(metrics))
        metrics["fundamental_inliers"] -= 1
        self.assertFalse(admitted(metrics))

    def test_mutual_ratio_excludes_one_way_match(self):
        a = np.array([[0., 0.], [10., 0.], [0., 10.]], np.float32)
        b = np.array([[0., 0.], [10., 0.], [0., 10.], [100., 100.]], np.float32)
        found = mutual_ratio_matches(a, b)
        self.assertEqual(len(found), 3)

    def test_packed_mask_is_fixed_size_and_refuses_wrong_grid(self):
        self.assertEqual(len(packed_mask(np.ones((16, 16), bool))), 44)
        with self.assertRaises(ValueError):
            packed_mask(np.ones((15, 16), bool))

    def test_known_projective_transform_has_supported_geometry(self):
        rng = np.random.default_rng(5)
        source = np.zeros((280, 320, 3), np.uint8)
        for x, y in rng.integers([20, 20], [300, 260], size=(130, 2)):
            cv2.circle(source, (int(x), int(y)), 4, (255, 255, 255), -1)
            cv2.line(source, (int(x) - 6, int(y)), (int(x) + 6, int(y)), (80, 80, 80), 1)
        matrix = np.array([[1.0, .04, 9], [.02, 1.0, 7], [.0001, .0002, 1]], np.float32)
        target = cv2.warpPerspective(source, matrix, (320, 280))
        metrics = match_images(source, target)
        self.assertGreaterEqual(metrics["fundamental_inliers"], 20)
        self.assertGreaterEqual(metrics["homography_inliers"], 20)
        self.assertGreater(min(metrics["homography_hull_fractions"]), .02)


if __name__ == "__main__":
    unittest.main()
