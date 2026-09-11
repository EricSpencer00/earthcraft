import sys
from pathlib import Path
import unittest
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from affine_photo_probe import affine_view,rootsift,fit,spatial_keys,features


class AffinePhotoTests(unittest.TestCase):
    def test_spatial_budget_and_deterministic_order(self):
        keys=[cv2.KeyPoint(float(x),float(y),3,response=float(response))
              for x,y,response in [(5,5,10),(6,6,9),(7,7,8),(90,90,1)]]
        chosen=spatial_keys(keys,(100,100),2)
        self.assertEqual([k.pt for k in chosen],[(5.,5.),(90.,90.)])
        self.assertEqual([k.pt for k in spatial_keys(list(reversed(keys)),(100,100),2)],
                         [k.pt for k in chosen])
        self.assertEqual(spatial_keys([],(100,100)),[])
        with self.assertRaises(ValueError):spatial_keys(keys,(100,100),1201)

    def test_roi_selection_retains_only_masked_features(self):
        rng=np.random.default_rng(9)
        image=rng.integers(0,256,(240,320),dtype=np.uint8)
        mask=np.zeros_like(image);mask[60:180,80:240]=255
        points,descriptors,counts=features(image,mask,[(1,0)],'roi-spatial')
        self.assertGreater(len(points),5)
        self.assertLessEqual(len(points),1200)
        self.assertEqual(descriptors.shape,(len(points),128))
        self.assertTrue(((points[:,0]>=80)&(points[:,0]<240)&
                         (points[:,1]>=60)&(points[:,1]<180)).all())
        empty=features(image,np.zeros_like(mask),[(1,0)],'roi-spatial')
        self.assertEqual(empty[0].shape,(0,2))
        self.assertEqual(empty[1].shape,(0,128))

    def test_inverse_pixel_mapping_and_identity(self):
        image=np.arange(80*100,dtype=np.uint8).reshape(80,100);mask=np.full_like(image,255)
        warped,_,inverse=affine_view(image,mask,1,0)
        np.testing.assert_array_equal(warped,image)
        for tilt,angle in [(np.sqrt(2),60),(2,120)]:
            _,_,inverse=affine_view(image,mask,tilt,angle)
            forward=cv2.invertAffineTransform(inverse)
            p=np.array([[15,22,1],[50,67,1]],float)
            transformed=p@forward.T
            np.testing.assert_allclose(np.c_[transformed,np.ones(2)]@inverse.T,p[:,:2],atol=1e-10)
        with self.assertRaises(ValueError):affine_view(image,mask,4,0)

    def test_root_descriptor_and_empty_matches(self):
        d=rootsift([[1,3,0],[0,0,0]])
        np.testing.assert_allclose(d[0],[.5,np.sqrt(.75),0])
        self.assertTrue(np.isfinite(d).all())
        empty=(np.empty((0,2),np.float32),np.empty((0,128),np.float32))
        result,*_=fit(empty,empty,[(100,100),(100,100)])
        self.assertEqual(result['homography_inliers'],0)
        self.assertFalse(result['matching_candidate_gate'])


if __name__=='__main__':unittest.main()
