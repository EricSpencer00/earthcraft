import json
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from water_tower_focus import prepare_focus, ROOT


class FocusTests(unittest.TestCase):
    def test_frozen_crop_preserves_samples_and_coordinates(self):
        parent=ROOT/'runs/metric-water-tower-local'
        if not parent.exists(): self.skipTest('Local frozen sources unavailable')
        source, roof=prepare_focus(parent, ROOT/'runs/cook-surface-2022', ROOT/'runs/water-tower-focus-64')
        meta=json.loads((source/'sources.json').read_text())
        self.assertEqual((meta['size'],meta['west'],meta['north']),(64,-32,33))
        self.assertLess(max(abs(d-1) for d in meta['cell_ground_distances_m']),1e-6)
        for old,new in ((parent/'rasters.npz',source/'rasters.npz'),
                        (ROOT/'runs/cook-surface-2022/metric-surfaces.npz',roof/'metric-surfaces.npz')):
            with np.load(old) as a, np.load(new) as b:
                for key in a.files: np.testing.assert_array_equal(a[key][224:288,224:288],b[key])
        self.assertEqual(json.loads((roof/'probe.json').read_text())['grid'],
                         {k:meta[k] for k in ('crs','west','north','size')})


if __name__=='__main__': unittest.main()
