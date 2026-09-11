import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from facade_registration import rotation, project
from facade_skin import face_samples, to_enu
from facade_surface import supported_patch, visible_samples, vertical_planes


class FacadePipelineTests(unittest.TestCase):
    def test_camera_is_right_handed(self):
        for yaw in (0,.7,2.4):
            r=rotation(yaw,.3,-.07)
            np.testing.assert_allclose(r@r.T,np.eye(3),atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(r),1)

    def test_projection_and_behind_camera(self):
        p=[0,0,0,0,0,0,np.log(100)]
        uv,depth=project(np.array([[0,10,0],[1,10,1],[0,-1,0]]),p,(200,200))
        np.testing.assert_allclose(uv[:2],[[100,100],[110,90]])
        self.assertLess(depth[2],0)

    def test_world_mapping_preserves_metres(self):
        w=np.array([[5,80,9],[6,81,10]])
        e=to_enu(w,{'west':-32,'north':33},-116,181)
        np.testing.assert_allclose(e[1]-e[0],[1,-1,1])

    def test_face_uv_and_offsets(self):
        cell=np.array([5,80,9])
        for face,axis,value in [('west',0,4.998),('east',0,6.002),('north',2,8.998),('south',2,10.002)]:
            p=face_samples(cell,face)
            self.assertEqual(p.shape,(256,3))
            np.testing.assert_allclose(p[:,axis],value)
            self.assertGreater(p[0,1],p[-1,1])
        p=face_samples(cell,'west')
        self.assertLess(p[0,2],p[15,2])
        p=face_samples(cell,'east')
        self.assertGreater(p[0,2],p[15,2])

    def test_visibility_rejects_foreground_occlusion(self):
        p=[0,0,0,0,0,0,np.log(100)]
        samples=np.array([[0,10,0],[0,5,0],[0,-1,0]])
        _,visible=visible_samples(samples,np.array([[0,5,0]]),p,(200,200),radius=0,tolerance=.01)
        np.testing.assert_array_equal(visible,[False,True,False])

    def test_plane_fit_uses_points(self):
        y,z=np.meshgrid(np.linspace(-2,2,20),np.linspace(0,8,40))
        p=np.c_[np.ones(y.size)*3,y.ravel(),z.ravel()]
        planes=vertical_planes(p)
        self.assertTrue(planes)
        self.assertGreater(abs(planes[0]['normal'][0]),.999)
        self.assertAlmostEqual(planes[0]['center'][0],3)

    def test_support_does_not_bridge_large_gap(self):
        x,z=np.meshgrid(np.r_[np.arange(0,1,.2),np.arange(4,5,.2)],np.arange(0,2,.2))
        local=np.c_[x.ravel(),z.ravel()]
        plane={'local':local,'center':np.zeros(3),'tangent':np.array([1,0,0])}
        p,d,report=supported_patch(plane,max_edge=.6,max_support=.3)
        self.assertTrue(len(p))
        self.assertFalse(np.any((p[:,0]>1)&(p[:,0]<4)))
        self.assertLessEqual(d.max(),.3)

    def test_invalid_geometry_rejected(self):
        with self.assertRaises(ValueError):vertical_planes([[0,0,float('nan')]])


if __name__=='__main__':unittest.main()
