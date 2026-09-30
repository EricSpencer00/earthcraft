import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from chicago_tiles import tile_plan
from regional_scans import acquire, vertical_units, SURVEY_FOOT,frozen_get


class RegionalScanTests(unittest.TestCase):
    def test_interrupted_source_receipt_requires_identical_publisher_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'original.laz';path.write_bytes(b'original survey')
            with patch('regional_scans.urllib.request.urlopen',return_value=io.BytesIO(b'changed survey')):
                with self.assertRaisesRegex(ValueError,'differs from publisher'):frozen_get('https://publisher.example/scan',path)
            self.assertEqual(path.read_bytes(),b'original survey')
            with patch('regional_scans.urllib.request.urlopen',return_value=io.BytesIO(b'original survey')):
                self.assertEqual(frozen_get('https://publisher.example/scan',path),b'original survey')
            self.assertTrue(path.with_name('original.laz.receipt.json').exists())
            self.assertEqual(frozen_get('https://publisher.example/scan',path),b'original survey')

    def test_horizontal_feet_do_not_imply_vertical_feet(self):
        with self.assertRaises(ValueError):
            vertical_units({'extent': {'spatialReference': {'wkid': 6455}}})
        factor, _ = vertical_units({'extent': {'spatialReference': {'wkid': 6455, 'latestVcsWkid': 6360}}})
        self.assertEqual(factor, SURVEY_FOOT)

    def test_scan_gaps_and_spikes_keep_explicit_fallback(self):
        grid = {'crs': 'EPSG:3857', 'west': 0, 'north': 0, 'size': 2}
        pair = {'county': 'fixture', 'year': 2022, 'dtm': {}, 'dsm': {}}
        ground = np.array([[200, np.nan], [200, 200]], np.float32)
        surface = np.array([[210, np.nan], [3000, 190]], np.float32)
        def fake(layer, grid, path):
            path.write_bytes(b'observed');return ground if path.name == 'dtm.tif' else surface
        with tempfile.TemporaryDirectory() as directory, patch('regional_scans.sample_surface', fake):
            elevation, record = acquire(pair, grid, Path(directory), np.full((2, 2), 195))
            np.testing.assert_array_equal(elevation, [[200, 195], [195, 195]])
            with np.load(Path(directory)/'metric-surfaces.npz') as arrays:
                np.testing.assert_array_equal(arrays['valid'], [[True, False], [False, False]])
            self.assertEqual(record['valid_cells'], 1)
            self.assertFalse(record['raw_points_acquired'])

    def test_region_budget_is_explicit_and_default_stays_bounded(self):
        inverse = Transformer.from_crs(3857, 4326, always_xy=True)
        document = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {},
            'geometry': mapping(transform(inverse.transform, box(0, 0, 32000, 32000)))}]}
        frame = {'crs': 'EPSG:3857', 'west': 0, 'north': 0, 'vertical_offset_m': -100,
            'dimension_min_y': -64, 'dimension_height': 1024}
        with self.assertRaises(ValueError):
            tile_plan(document, frame, tile_size=512)
        plan = tile_plan(document, frame, tile_size=512, max_area_m2=2e9)
        self.assertGreater(plan['city_area_m2'], 1e9)
        self.assertFalse(plan['playable_city'])


if __name__ == '__main__':
    unittest.main()
