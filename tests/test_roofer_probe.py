import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import laspy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from roofer_probe import LAS_SCALE, canonical_cityjson, prepare


class RooferProbeTests(unittest.TestCase):
    def test_prepare_keeps_classes_and_writes_projected_las_and_footprint(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);points=root/'points';source=root/'source';output=root/'output'
            points.mkdir();source.mkdir()
            xyz=np.array([[0.1234567,1.2345678,180.1],[2.3456789,3.4567891,190.2]])
            np.savez(points/'points.npz',xyz=xyz,classification=np.array([1,15],np.uint8),
                     withheld=np.array([0,1],np.uint8),intensity=np.array([4,5],np.uint16),
                     return_number=np.array([1,2],np.uint8))
            point_hash=hashlib.sha256((points/'points.npz').read_bytes()).hexdigest()
            crs='EPSG:3857'
            (points/'manifest.json').write_text(json.dumps({'points_sha256':point_hash,'output_horizontal_crs':crs,
                'vertical_datum':'NAVD88, Geoid18'}))
            (source/'sources.json').write_text(json.dumps({'crs':crs,'size':64,'west':-32,'north':33}))
            (source/'cook-buildings-2022.json').write_text(json.dumps({'features':[{'attributes':{'OBJECTID':833197,
                'Ground_Z':600,'Height':20},'geometry':{'rings':[[[-87.63,41.89],[-87.62,41.89],[-87.62,41.90],[-87.63,41.89]]]}}]}))
            las,footprint,report=prepare(points,source,output)
            data=laspy.read(las)
            np.testing.assert_array_equal(data.classification,[1,15])
            np.testing.assert_array_equal(data.withheld,[False,True])
            self.assertLessEqual(np.abs(np.column_stack((data.x,data.y,data.z))-xyz).max(),LAS_SCALE/2+1e-12)
            self.assertEqual(json.loads(footprint.read_text())['features'][0]['properties']['objectid'],833197)
            self.assertEqual(report['class_counts'],{'1':1,'15':1})

    def test_canonical_cityjson_ignores_metadata_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'output.city.jsonl'
            p.write_text('{"type":"CityJSON","metadata":{"time":"first"}}\n{"CityObjects":{},"vertices":[]}\n')
            self.assertEqual(canonical_cityjson(p),'[{"CityObjects":{},"vertices":[]}]')

    def test_canonical_cityjson_keeps_ring_adjacency_and_surface_groups(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);vertices=[[0,0,0],[1,0,0],[1,1,0],[0,1,0]]
            def write(name,boundaries):
                p=root/name;p.write_text('\n'.join((json.dumps({'type':'CityJSON','transform':{'scale':[1,1,1],'translate':[0,0,0]}}),
                    json.dumps({'type':'CityJSONFeature','vertices':vertices,'CityObjects':{'b':{'type':'BuildingPart',
                    'geometry':[{'type':'Solid','lod':'2.2','boundaries':boundaries}]}}}))))
                return canonical_cityjson(p)
            square=write('square.jsonl',[[[[0,1,2,3]]]])
            self.assertEqual(square,write('reversed.jsonl',[[[[2,1,0,3]]]]))
            self.assertNotEqual(square,write('crossed.jsonl',[[[[0,2,1,3]]]]))
            self.assertNotEqual(square,write('split-surfaces.jsonl',[[[[0,1,2]],[[0,2,3]]]]))


if __name__=='__main__':unittest.main()
