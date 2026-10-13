from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from appearance_ab import RGBN, add_observed_canopy, aligned_window, bounded_npz, capture_relation, proposals, visible_bands


class AppearanceABTests(unittest.TestCase):
    def fixture(self):
        shape = (5, 5)
        return {'rgb': np.full((*shape, 3), 100, np.uint8),
                'valid': np.ones(shape, bool), 'shadow': np.zeros(shape, bool),
                'vegetation': np.zeros(shape, bool), 'owner': np.ones(shape, np.int32),
                'top': np.full(shape, 10),
                'current': np.full(shape, 'minecraft:stone_bricks', object),
                'protected': np.zeros(shape, bool),
                'blocks': ['minecraft:gray_concrete'],
                'colours': {'minecraft:gray_concrete': [100, 100, 100],
                            'minecraft:stone_bricks': [160, 160, 160]}}

    def test_nir_is_never_visible_colour_and_band_order_is_required(self):
        bands = np.zeros((4, 2, 2), np.uint8); bands[3] = 250
        np.testing.assert_array_equal(visible_bands(bands, RGBN), np.zeros((2, 2, 3)))
        with self.assertRaisesRegex(ValueError, 'NIR'):
            visible_bands(bands, ['near_infrared', 'red', 'green', 'blue'])

    def test_exact_subcrop_and_partial_or_fractional_coverage_rejection(self):
        image = {'crs': 'EPSG:3857', 'west': 0, 'north': 32, 'size': 32}
        world = dict(image, west=8, north=24, size=16)
        self.assertEqual(aligned_window(image, world), (slice(8, 24), slice(8, 24)))
        for change in [dict(west=8.5), dict(west=24), dict(crs='EPSG:4326')]:
            with self.assertRaises(ValueError):aligned_window(image, dict(world, **change))

    def test_invalid_vegetation_shadow_photo_and_existing_colour_are_retained(self):
        f = self.fixture()
        f['valid'][1, 1] = False; f['vegetation'][1, 2] = True
        f['shadow'][2, 1] = True; f['protected'][2, 2] = True
        f['current'][3, 3] = 'minecraft:blue_concrete'
        chosen, _ = proposals(**f, minimum_neighbors=1)
        self.assertTrue(chosen)
        self.assertFalse({(1, 1), (1, 2), (2, 1), (2, 2), (3, 3)} & set(chosen))
        self.assertTrue(all(v['rgb_error_candidate'] < v['rgb_error_before'] for v in chosen.values()))

    def test_consensus_does_not_cross_owner_or_roof_height_boundaries(self):
        f = self.fixture(); f['owner'][:] = 2; f['owner'][2, 2] = 1
        chosen, _ = proposals(**f)
        self.assertNotIn((2, 2), chosen)
        f['owner'][:] = 1; f['top'][2, 2] = 20
        chosen, _ = proposals(**f)
        self.assertNotIn((2, 2), chosen)

    def test_saturated_sample_abstains_and_worse_colour_never_replaces_baseline(self):
        f = self.fixture(); f['rgb'][:] = [20, 220, 20]
        chosen, metrics = proposals(**f)
        self.assertFalse(chosen); self.assertGreater(metrics['abstentions']['high_chroma'], 0)
        f = self.fixture(); f['colours']['minecraft:gray_concrete'] = [240, 240, 240]
        chosen, _ = proposals(**f)
        self.assertFalse(chosen)

    def test_mismatched_grids_rejected(self):
        f = self.fixture(); f['rgb'] = f['rgb'][:4]
        with self.assertRaisesRegex(ValueError, 'grids'):
            proposals(**f)

    def test_cross_epoch_is_not_simultaneous_capture(self):
        self.assertEqual(capture_relation('2019-08-02T00:00:00+00:00', ['2022-04-05', '2022-06-29']), 'disjoint')
        self.assertEqual(capture_relation(['2022-01-01', '2022-12-31'], ['2022-04-05', '2022-06-29']), 'overlap')
        self.assertEqual(capture_relation(None, ['2022-04-05', '2022-06-29']), 'unknown')

    def test_canopy_never_overwrites_original_or_excluded_blocks(self):
        source = np.zeros((8, 4, 4), np.uint16); source[0] = 1; source[4, 1, 1] = 2
        ground = np.zeros((4, 4), np.int32); excluded = np.zeros((4, 4), bool); excluded[2, 2] = True
        cells = np.array([[0, 4, 0], [1, 4, 1], [2, 4, 2], [3, 1, 3], [9, 4, 1]], np.int32)
        result, proof = add_observed_canopy(source, cells, ground, excluded, 0, 3)
        self.assertEqual(proof['added_observed_voxels'], 1)
        self.assertEqual(proof['rejected_voxels'], 4)
        np.testing.assert_array_equal(result[source != 0], source[source != 0])
        self.assertEqual(result[4, 0, 0], 3); self.assertEqual(source[4, 0, 0], 0)
        self.assertFalse(proof['trunks_inferred'])

    def test_canopy_input_cannot_silently_requantize_floating_coordinates(self):
        with self.assertRaisesRegex(ValueError, 'Integral'):
            add_observed_canopy(np.zeros((8, 4, 4)), np.array([[1.5, 4, 1]]),
                                np.zeros((4, 4)), np.zeros((4, 4), bool), 0, 3)

    def test_compressed_array_size_does_not_hide_expanded_memory_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'compressed.npz'
            np.savez_compressed(path, points=np.zeros((10000, 3), np.float64))
            self.assertLess(path.stat().st_size, 10000)
            with self.assertRaisesRegex(ValueError, 'Expanded'):
                bounded_npz(path, 10000)


if __name__ == '__main__':
    unittest.main()
