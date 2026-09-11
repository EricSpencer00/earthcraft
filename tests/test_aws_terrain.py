from pathlib import Path
import sys
import unittest
import numpy as np
from pyproj import Transformer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aws_terrain import decode,tile_pixels
from metric_chart import chart,geographic_centres


class AwsTerrainTests(unittest.TestCase):
    def test_documented_terrarium_encoding_and_negative_elevation(self):
        result=decode(np.array([[[137,219,68],[127,255,128],[128,0,0]]],np.uint8))
        np.testing.assert_array_equal(result,[[2523.265625,-.5,0]])

    def test_dateline_wrap_without_polar_clipping(self):
        x,y=tile_pixels(np.array([-180.,180.]),np.array([0.,0.]))
        np.testing.assert_array_equal(x,[0,0])
        with self.assertRaises(ValueError):tile_pixels(0,89)
        with self.assertRaises(ValueError):tile_pixels(float('nan'),0)

    def test_chart_scale_and_roundtrip_at_different_locations(self):
        for lon,lat in [(-87.62443,41.8972),(-121.7603,46.8523),(179.999,0),(18.4,-33.9)]:
            meta=chart(lon,lat,32);lons,lats=geographic_centres(meta)
            x,y=Transformer.from_crs(4326,meta['crs'],always_xy=True).transform(lons,lats)
            rows,cols=np.mgrid[:32,:32]
            np.testing.assert_allclose(x,cols-15.5,atol=1e-7)
            np.testing.assert_allclose(y,15.5-rows,atol=1e-7)
            self.assertLess(max(abs(d-1) for d in meta['cell_ground_distances_m']),1e-5)

    def test_unbounded_area_is_rejected_before_allocation(self):
        for size in (0,17,1000000):
            with self.assertRaises(ValueError):chart(0,0,size)


if __name__=='__main__':unittest.main()
