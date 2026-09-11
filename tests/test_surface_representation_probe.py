import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from surface_representation_probe import block_occlusion,world_coordinates
from facade_skin import to_enu


class SurfaceRepresentationTests(unittest.TestCase):
    def test_ray_intersections_and_parallel_misses(self):
        cells=np.array([[0,0,0]])
        target=np.array([[.5,.5,.5],[2,.5,.5],[-.1,.5,.5],[2,3,.5]])
        blocked=block_occlusion([-2,.5,.5],target,cells)
        np.testing.assert_allclose(blocked[:3],[.5,2,0])
        self.assertEqual(blocked[3],0)
        np.testing.assert_array_equal(block_occlusion([-2,2,.5],[[2,2,.5]],cells),[0])
        np.testing.assert_array_equal(block_occlusion([-2,.5,.5],target,np.empty((0,3))),np.zeros(4))
        self.assertEqual(block_occlusion([.5,.5,.5],[[2,.5,.5]],cells)[0],1.5)

    def test_coordinate_roundtrip_and_invalid_inputs(self):
        xyz=np.array([[-4.,12.,33.],[0.,-2.,-3.]])
        meta={'west':-32,'north':33};offset=-117.;ground=181.19298958597915
        np.testing.assert_allclose(to_enu(world_coordinates(xyz,meta,offset,ground),meta,offset,ground),xyz)
        with self.assertRaises(ValueError):block_occlusion([0,0,0],[[0,0,0]],[[1,1,1]])
        with self.assertRaises(ValueError):block_occlusion([0,0,0],[[1,1,1]],[[.2,0,0]])


if __name__=='__main__':unittest.main()
