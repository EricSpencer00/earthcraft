import json
from pathlib import Path
import sys,tempfile,unittest

import osmium
from pyproj import Transformer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_way_index import build_index,MetricWayIndex


class MetricWayIndexTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'source.osm.pbf';crs='EPSG:3857'
        reverse=Transformer.from_crs(crs,4326,always_xy=True)
        positions=[(0,0),(10,10),(100,100),(110,110)]
        with osmium.SimpleWriter(str(source)) as writer:
            for i,(x,y) in enumerate(positions,1):
                writer.add_node(osmium.osm.mutable.Node(id=i,location=reverse.transform(x,y)))
            writer.add_way(osmium.osm.mutable.Way(id=9,nodes=[1,2],tags={'highway':'path'}))
            writer.add_way(osmium.osm.mutable.Way(id=20,nodes=[3,4,3],tags={'building':'yes'}))
        path=root/'index.sqlite';build_index(source,path,crs)
        return source,path,crs

    def test_exact_selection_order_and_read_only_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            source,path,crs=self.fixture(Path(folder));index=MetricWayIndex(path,source,crs)
            try:
                self.assertEqual([w['id'] for w in index.select(source,crs,-1,111,113)],[9,20])
                one=index.select(source,crs,-1,11,13)
                self.assertEqual(len(one),1);self.assertEqual(one[0]['tags'],{'highway':'path'})
                self.assertFalse(one[0]['closed'])
                self.assertEqual(index.select(source,crs,200,250,16),[])
                with self.assertRaises(Exception):index.db.execute('DELETE FROM ways')
                source.touch()
                with self.assertRaisesRegex(ValueError,'changed during run'):index.select(source,crs,0,10,16)
            finally:index.close()

    def test_rejects_different_frame_and_changed_index(self):
        with tempfile.TemporaryDirectory() as folder:
            source,path,crs=self.fixture(Path(folder))
            with self.assertRaisesRegex(ValueError,'coordinate frame'):MetricWayIndex(path,source,'EPSG:26916')
            with path.open('ab') as stream:stream.write(b'changed')
            with self.assertRaisesRegex(ValueError,'index changed'):MetricWayIndex(path,source,crs)

    def test_existing_or_interrupted_build_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            source,path,crs=self.fixture(Path(folder))
            with self.assertRaises(FileExistsError):build_index(source,path,crs)
            other=Path(folder)/'other.sqlite'
            other.with_suffix('.sqlite.building').write_bytes(b'prior incomplete evidence')
            with self.assertRaises(FileExistsError):build_index(source,other,crs)


if __name__=='__main__':unittest.main()
