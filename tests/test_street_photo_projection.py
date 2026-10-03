from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from street_photo_projection import project_camera, project_shot


class StreetProjectionTests(unittest.TestCase):
    def test_perspective_pixel_centers_and_behind_camera_rejection(self):
        points = [[0,0,10], [1,0,10], [0,1,10], [0,0,-10], [100,0,10]]
        result = project_camera(points, {'projection_type':'perspective','focal':.8}, 1000, 750)
        np.testing.assert_allclose(result['source_uv'][:3], [[499.5,374.5],[579.5,374.5],[499.5,454.5]])
        np.testing.assert_array_equal(result['in_image'], [True,True,True,False,False])

    def test_radial_distortion_uses_calibration_instead_of_panorama_wrap(self):
        result = project_camera([[1,0,10]], {'projection_type':'perspective','focal':1,'k1':1,'k2':0}, 1000, 750)
        self.assertAlmostEqual(result['source_uv'][0,0],600.5)
        with self.assertRaisesRegex(ValueError, 'Cropped'):
            project_camera([[0,0,1]], {'projection_type':'perspective','focal':1,'width':1000,'height':750},1000,500)

    def test_panorama_seam_and_pose_conventions(self):
        result=project_camera([[0,0,1],[1,0,0],[0,0,-1]], {'projection_type':'spherical'},1000,500)
        np.testing.assert_allclose(result['source_uv'], [[499.5,249.5],[749.5,249.5],[999.5,249.5]])
        self.assertTrue(result['in_image'].all())
        result=project_shot([[0,0,0]], {'rotation':[0,0,0],'translation':[0,0,10]}, {'projection_type':'perspective','focal':1},1000,750)
        np.testing.assert_allclose(result['source_uv'],[[499.5,374.5]])
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            project_camera([[0,0,1]], {'projection_type':'unknown'},1000,750)


if __name__ == '__main__': unittest.main()
