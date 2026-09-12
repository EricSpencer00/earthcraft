import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from global_projection import (
    BAND_COUNT,
    CHUNK_SIZE_M,
    WORLD_PAGE_COUNT,
    PageAddress,
    address_for,
    atlas_summary,
    columns_in_band,
    geographic_coordinates,
    page_coordinates,
    page_generation_envelope,
    page_geographic_bounds,
    page_manifest,
    rebase_coordinates,
)


class GlobalProjectionTests(unittest.TestCase):
    def test_every_coordinate_gets_one_formula_address(self):
        chicago = address_for(-87.6298, 41.8781)
        self.assertEqual(chicago, address_for(-87.6298, 41.8781))
        self.assertGreater(WORLD_PAGE_COUNT, 1_000_000)
        self.assertEqual(address_for(10, -90), address_for(-140, -90))
        self.assertEqual(address_for(10, 90), address_for(-140, 90))
        self.assertNotEqual(address_for(179.999, 0), address_for(-179.999, 0))

    def test_band_and_column_bounds_cover_world_without_gaps(self):
        self.assertEqual(page_geographic_bounds(PageAddress(0, 0))["south"], -90)
        last = PageAddress(BAND_COUNT - 1, columns_in_band(BAND_COUNT - 1) - 1)
        self.assertAlmostEqual(page_geographic_bounds(last)["north"], 90)
        for band in (0, BAND_COUNT // 2, BAND_COUNT - 1):
            count = columns_in_band(band)
            first = page_geographic_bounds(PageAddress(band, 0))
            final = page_geographic_bounds(PageAddress(band, count - 1))
            self.assertEqual(first["west"], -180)
            self.assertAlmostEqual(final["east"], 180)

    def test_generation_envelopes_are_chunk_aligned(self):
        for point in ((-87.6298, 41.8781), (0, 0), (25, 89.99), (25, -89.99)):
            envelope = page_generation_envelope(address_for(*point))
            for value in envelope.values():
                self.assertEqual(value % CHUNK_SIZE_M, 0)
            self.assertGreater(envelope["east"], envelope["west"])
            self.assertGreater(envelope["north"], envelope["south"])

    def test_page_round_trip_and_cross_page_rebase(self):
        longitude, latitude = -87.6298, 41.8781
        mapped = page_coordinates(longitude, latitude)
        recovered = geographic_coordinates(mapped["page"], mapped["x"], mapped["z"])
        self.assertAlmostEqual(recovered[0], longitude, places=8)
        self.assertAlmostEqual(recovered[1], latitude, places=8)

        bounds = page_geographic_bounds(mapped["page"])
        neighbor_point = (bounds["east"] + 1e-6, latitude)
        source_coordinates = page_coordinates(*neighbor_point, mapped["page"])
        rebased = rebase_coordinates(
            mapped["page"], source_coordinates["x"], source_coordinates["z"]
        )
        self.assertNotEqual(rebased["page"], mapped["page"])
        recovered = geographic_coordinates(rebased["page"], rebased["x"], rebased["z"])
        self.assertAlmostEqual(recovered[0], neighbor_point[0], places=8)
        self.assertAlmostEqual(recovered[1], neighbor_point[1], places=8)

    def test_manifest_is_json_safe_and_declares_no_inference(self):
        manifest = page_manifest(address_for(-87.6298, 41.8781))
        json.dumps(manifest)
        self.assertFalse(manifest["inference_used"])
        self.assertEqual(manifest["generation"]["tile_size_m"], 256)
        self.assertTrue(math.isfinite(manifest["projection"]["maximum_sampled_scale_error_ppm"]))
        self.assertEqual(atlas_summary()["page_count"], WORLD_PAGE_COUNT)


if __name__ == "__main__":
    unittest.main()
