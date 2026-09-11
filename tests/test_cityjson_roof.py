import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cityjson_roof import EMPTY, rasterize_roof


class CityJsonRoofTests(unittest.TestCase):
    meta={'west':-2,'north':2,'size':4,'crs':'same'}
    def write(self,root,vertices,ring,reference='same',feature='b'):
        header={'type':'CityJSON','transform':{'scale':[1,1,1],'translate':[0,0,0]},'metadata':{'referenceSystem':reference}}
        boundaries=[[ring]] if ring and isinstance(ring[0],list) else [[[ring]]]
        roof={'type':'Solid','lod':'2.2','boundaries':boundaries,'semantics':{'surfaces':[{'type':'RoofSurface'}],'values':[[0]]}}
        item={'type':'CityJSONFeature','id':feature,'vertices':vertices,'CityObjects':{'p':{'geometry':[roof]}}}
        p=Path(root)/'x.city.jsonl';p.write_text(json.dumps(header)+'\n'+json.dumps(item));return p
    def test_pitched_transform_negative_coordinates_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.write(d,[[-2,0,3],[2,0,7],[2,2,7],[-2,2,3]],[0,1,2,3])
            top,ok,report=rasterize_roof(p,'b',self.meta,10)
            again=rasterize_roof(p,'b',self.meta,10)[0]
            self.assertTrue(ok.any());self.assertEqual(report['raster_cells'],8);np.testing.assert_array_equal(top,again)
            self.assertEqual(top[0,0],13);self.assertEqual(top[0,3],16);self.assertEqual(top[3,0],EMPTY)
    def test_roof_hole_is_not_rasterized(self):
        with tempfile.TemporaryDirectory() as d:
            vertices=[[-2,0,1],[2,0,1],[2,2,1],[-2,2,1],[-.75,.25,1],[-.25,.25,1],[-.25,.75,1],[-.75,.75,1]]
            p=self.write(d,vertices,[[0,1,2,3],[4,5,6,7]])
            top,ok,report=rasterize_roof(p,'b',self.meta,0)
            self.assertTrue(ok.any());self.assertEqual(report['raster_cells'],7);self.assertEqual(top[1,1],EMPTY)
    def test_hole_feature_mismatch_and_nonfinite_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.write(d,[[0,0,1],[1,0,1],[1,1,1],[0,1,1]],[0,1,2,3])
            self.assertFalse(rasterize_roof(p,'nope',self.meta,0)[1].any())
            self.assertFalse(rasterize_roof(p,'b',{**self.meta,'crs':'other'},0)[1].any())
            p=self.write(d,[[0,0,float('nan')],[1,0,1],[1,1,1],[0,1,1]],[0,1,2,3])
            self.assertFalse(rasterize_roof(p,'b',self.meta,0)[1].any())

    def test_collinear_face_rejected_and_boundary_center_supported(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.write(d,[[-2,0,1],[0,0,1],[2,0,1]],[0,1,2])
            self.assertFalse(rasterize_roof(p,'b',self.meta,0)[1].any())
            p=self.write(d,[[-2,0,1],[-.5,0,1],[-.5,2,1],[-2,2,1]],[0,1,2,3])
            top,support,_=rasterize_roof(p,'b',self.meta,0)
            self.assertTrue(support[1,1]);self.assertEqual(top[1,1],1)

    def test_first_three_collinear_vertices_do_not_define_the_plane(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.write(d,[[-2,0,1],[0,0,1],[2,0,1],[2,2,3],[-2,2,3]],[0,1,2,3,4])
            top,support,_=rasterize_roof(p,'b',self.meta,0)
            self.assertTrue(support[:2].all())
            self.assertEqual(top[0,0],2)

    def test_nonidentity_transform_and_invalid_vertex_indices(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.write(d,[[-4,0,0],[4,0,0],[4,4,0],[-4,4,0]],[0,1,2,3])
            header,feature=map(json.loads,p.read_text().splitlines())
            header['transform']={'scale':[.5,.5,.001],'translate':[0,0,12]}
            p.write_text(json.dumps(header)+'\n'+json.dumps(feature))
            top,support,_=rasterize_roof(p,'b',self.meta,-2)
            self.assertTrue(support[:2].all());self.assertTrue((top[support]==10).all())
            for bad in (-1,1.5,True):
                feature['CityObjects']['p']['geometry'][0]['boundaries'][0][0][0][0]=bad
                p.write_text(json.dumps(header)+'\n'+json.dumps(feature))
                self.assertFalse(rasterize_roof(p,'b',self.meta,0)[1].any())


if __name__=='__main__':unittest.main()
