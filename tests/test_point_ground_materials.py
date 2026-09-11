import sys
from pathlib import Path
import unittest
import numpy as np
import json, hashlib, tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from point_ground_materials import road_mask, load


class GroundMaterialTests(unittest.TestCase):
    def test_batch_sources_route_only_when_every_provider_is_cook(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);meta={'crs':'test','size':16,'west':0,'north':16}
            np.savez(root/'points.npz',xyz=[[1.2,14.8,100]],classification=[11],withheld=[0])
            manifest={'sources':[{'url':'https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/cook-las5.zip'}],
                'output_horizontal_crs':'test','points_sha256':hashlib.sha256((root/'points.npz').read_bytes()).hexdigest()}
            (root/'manifest.json').write_text(json.dumps(manifest))
            mask,report=load(root,meta,np.full((16,16),100.),np.zeros((16,16),bool))
            self.assertEqual(int(mask.sum()),1)
            manifest['sources'].append({'url':'https://different-provider.invalid'})
            (root/'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(load(root,meta,np.full((16,16),100.),np.zeros((16,16),bool)),(None,None))

    def test_class_withheld_height_and_boundaries_no_dilation(self):
        meta={'size':16,'west':0,'north':16}
        xyz=np.array([[1.2,14.8,100],[1.8,14.2,100.2], # same observed cell
                      [2,14,104], # elevated bridge: cannot flatten onto ground
                      [3,13,100],[4,12,100], # withheld and other class
                      [16,8,100],[-1,8,100],[8,0,100], # outside half-open chart
                      [0,16,100]],dtype=float)
        mask,report=road_mask(xyz,[11,11,11,11,5,11,11,11,11],[0,0,0,1,0,0,0,0,0],meta,np.full((16,16),100.))
        self.assertEqual(set(map(tuple,np.argwhere(mask))),{(1,1),(0,0)})
        self.assertEqual(report['rejected_height_returns'],1)
        self.assertEqual(report['accepted_returns'],3)
        self.assertEqual(report['candidate_ground_cells'],2)
