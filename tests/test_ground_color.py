from pathlib import Path
import sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ground_color import nearest_colors
from cook_ortho import native_grid
from metric_chart import chart


class GroundColorTests(unittest.TestCase):
    def test_palette_identity_and_invalid_input(self):
        colors=np.array([[0,0,0],[255,255,255],[90,100,110]])
        np.testing.assert_array_equal(nearest_colors(colors,colors),[0,1,2])
        with self.assertRaises(ValueError):nearest_colors([[np.nan,0,0]],colors)

    def test_native_crop_is_bounded_and_rejects_oversized_or_nonmetric_grid(self):
        meta=chart(-87.62443,41.8972,64)
        bounds,w,h=native_grid(meta)
        self.assertLess(w*h,2_000_000);self.assertAlmostEqual(bounds[2]-bounds[0],w*.5)
        _,wide,high=native_grid(dict(meta,size=256))
        self.assertLess(wide*high,4_000_000)
        with self.assertRaises(ValueError):native_grid(dict(meta,size=512))
        with self.assertRaises(ValueError):native_grid(dict(meta,crs='EPSG:4326'))


if __name__=='__main__':unittest.main()
