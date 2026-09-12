import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import laspy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lidar_spatial_index import SpatialPointIndex


class LidarSpatialIndexTests(unittest.TestCase):
    def test_retain_query_and_reuse_compact_provider_classes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'fixture.las'
            header=laspy.LasHeader(point_format=6,version='1.4')
            header.scales=np.array([.01,.01,.01]);header.offsets=np.zeros(3)
            points=laspy.LasData(header)
            points.x=np.array([10,20,140,150,250],float)
            points.y=np.array([10,20,140,150,250],float)
            points.z=np.array([1,2,3,4,5],float)
            points.classification=np.array([1,6,11,15,2],np.uint8)
            points.withheld=np.array([0,0,0,1,0],bool)
            points.write(source)
            raw=source.read_bytes();record={'sha256':hashlib.sha256(raw).hexdigest(),
                'bytes':len(raw),'url':'fixture','member':'fixture.las'}
            asset={'id':'12345678','native_geometry':{'type':'Polygon','coordinates':[[
                [10,10],[250,10],[250,250],[10,250],[10,10]]]}}
            indexer=SpatialPointIndex(root/'indexes')
            index=indexer.ensure(source,record,asset)
            self.assertEqual(indexer.ensure(source,record,asset),index)
            manifest=__import__('json').loads((index/'manifest.json').read_text())
            self.assertEqual(manifest['retained_points'],3)
            result=indexer.query(index,(0,0,130,130))
            np.testing.assert_allclose(result['xyz'],[[10,10,1],[20,20,2]])
            np.testing.assert_array_equal(result['classification'],[1,6])


if __name__=='__main__':unittest.main()
