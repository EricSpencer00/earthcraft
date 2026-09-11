from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from point_geometry import voxelize
from geographic_quality import audit


class PointGeometryTests(unittest.TestCase):
    def test_axes_and_under_overhang_gap_remains_empty(self):
        meta={'west':-32,'north':33,'size':64}
        cells=voxelize([[0.2,0.2,182.2],[0.2,0.2,190.2],[0.3,0.3,190.3]],meta,-116)
        np.testing.assert_array_equal(cells,[[32,66,32],[32,74,32]])
        self.assertNotIn([32,70,32],cells.tolist())

    def test_invalid_observations_fail_closed(self):
        meta={'west':-32,'north':33,'size':64}
        for points in ([[float('nan'),0,1]],[[32,0,1]],[[0,-31,1]]):
            with self.assertRaises(ValueError):voxelize(points,meta,0)

    def test_point_world_audit_does_not_require_heightfield(self):
        world=Path(__file__).resolve().parents[1]/'worlds/Earthcraft-Water-Tower-v7-3D-Points'
        if not (world/'top-heights.npy').exists():self.skipTest('Local point world unavailable')
        result=audit(world)
        self.assertEqual(result['tile_seams']['unexplained_jump_cells'],0)
        self.assertEqual(result['coverage']['measured_roof_cells'],0)
        self.assertGreater(result['coverage']['observed_3d_voxels'],0)


if __name__=='__main__':unittest.main()
