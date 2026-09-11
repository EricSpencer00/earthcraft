import sys
from pathlib import Path
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from appearance_adapter import capture_interval, temporal_relation, rank_capture, paint_surface


class AppearanceAdapterTests(unittest.TestCase):
    def test_dates_preserve_uncertainty(self):
        self.assertEqual(str(capture_interval('2024-02')[1]),'2024-02-29')
        r=temporal_relation('2022',['2022-04-05','2022-06-29'])
        self.assertTrue(r['intervals_overlap'])
        self.assertFalse(r['same_exact_day'])
        self.assertGreater(r['maximum_gap_days'],200)
        self.assertLess(rank_capture('2022-05-01','2022-05-02'),rank_capture('2022','2022-05-02'))

    def test_unknown_and_reversed_dates_rejected(self):
        for value in [None,'Taken in 2022','2022-13',['2023','2022']]:
            with self.assertRaises(ValueError):capture_interval(value)
        self.assertEqual(str(capture_interval('2022-01-01T00:30:00+01:00')[0]),'2021-12-31')

    def fixture(self):
        p=np.array([[0.,0.,5.],[1.,0.,5.]])
        normals=np.array([[0.,0.,-1.]]*2)
        rgb=np.zeros((21,21,3),np.uint8);rgb[10,12]=[180,100,30]
        depth=np.full((21,21),5.)
        camera={'K':[[10,0,10],[0,10,10],[0,0,1]],'R':np.eye(3),'t':np.zeros(3)}
        return p,normals,rgb,depth,camera,np.ones((21,21),bool)

    def test_direct_pixel_projection_keeps_geometry_and_observed_black(self):
        args=self.fixture();before=args[0].copy();result=paint_surface(*args)
        np.testing.assert_array_equal(result['rgba'],[[0,0,0,255],[180,100,30,255]])
        np.testing.assert_array_equal(args[0],before)
        np.testing.assert_allclose(result['source_uv'],[[10,10],[12,10]])

    def test_missing_occluded_or_masked_depth_never_paints(self):
        for depth in [float('inf'),float('nan'),0,3,7]:
            args=list(self.fixture());args[3][:]=depth
            self.assertFalse(paint_surface(*args)['visible'].any())
        args=list(self.fixture());args[5][:]=False
        self.assertFalse(paint_surface(*args)['visible'].any())

    def test_back_faces_behind_camera_and_outside_image(self):
        args=list(self.fixture());args[1]*=-1
        self.assertFalse(paint_surface(*args)['visible'].any())
        args=list(self.fixture());args[0][:,2]=-5
        self.assertFalse(paint_surface(*args)['visible'].any())
        args=list(self.fixture());args[0][:,0]=100
        self.assertFalse(paint_surface(*args)['visible'].any())

    def test_bad_transform_rejected(self):
        args=list(self.fixture());args[4]['R']=np.diag([-1,1,1])
        with self.assertRaises(ValueError):paint_surface(*args)

    def test_positive_camera_depth_not_ray_range(self):
        args=list(self.fixture());args[0][1]=[3,0,5]
        self.assertTrue(paint_surface(*args)['visible'][1])


if __name__=='__main__':unittest.main()
