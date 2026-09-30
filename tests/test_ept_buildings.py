from pathlib import Path
import sys
import tempfile
import unittest

import laspy
import numpy as np
from pyproj import Transformer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ept_buildings import indexed_node


class EptIndexTests(unittest.TestCase):
    def test_full_density_classes_boundaries_and_index_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)/'data';data.mkdir();path=data/'0-0-0-0.las'
            header=laspy.LasHeader(point_format=3,version='1.2');points=laspy.LasData(header)
            points.x=np.array([0,10,255.99,256,20,30],float)
            points.y=np.array([-1,-2,-3,-4,-5,-6],float)
            points.z=np.array([200,210,211,220,230,240],float)
            points.classification=np.array([2,6,6,6,5,6],np.uint8)
            points.withheld=np.array([0,0,0,0,0,1],np.uint8);points.write(path)
            inverse=Transformer.from_crs(3857,3857,always_xy=True)
            grid={'crs':'EPSG:3857','west':0,'north':0,'size':256}
            xyz,classes=indexed_node(path,6,inverse,grid,reserve_bytes=0)
            # Candidate bins include the boundary bin; exact half-open crop
            # filtering follows in crop(), preserving every class-2/6 return.
            self.assertEqual(len(xyz),4)
            self.assertEqual(sorted(classes.tolist()),[2,6,6,6])
            again,_=indexed_node(path,6,inverse,grid)
            np.testing.assert_array_equal(again,xyz)
            array=next((data.parent/'indexes').rglob('coordinates.npy'))
            values=np.load(array);values[0,2]+=10;np.save(array,values)
            with self.assertRaisesRegex(ValueError,'coordinates changed'):
                indexed_node(path,6,inverse,grid)


if __name__=='__main__':unittest.main()
