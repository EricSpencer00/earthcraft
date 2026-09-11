import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import osmium
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import chicago

class ChicagoExtractTest(unittest.TestCase):
    def test_crossing_way_keeps_external_node_references(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);sources=root/'sources';sources.mkdir();state=root/'state';state.mkdir()
            boundary={'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'Polygon','coordinates':[[[-87.631,41.879],[-87.629,41.879],[-87.629,41.881],[-87.631,41.881],[-87.631,41.879]]]}}]}
            (sources/'chicago-boundary.geojson').write_text(json.dumps(boundary))
            xml=root/'fixture.osm'
            xml.write_text('''<osm version="0.6" generator="earthcraft-test">
<node id="1" lat="41.880" lon="-87.640"/>
<node id="2" lat="41.880" lon="-87.620"/>
<node id="3" lat="41.900" lon="-87.650"/>
<node id="4" lat="41.901" lon="-87.650"/>
<way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>
<way id="11"><nd ref="3"/><nd ref="4"/><tag k="highway" v="residential"/></way>
</osm>''')
            with osmium.SimpleWriter(str(sources/'illinois.osm.pbf')) as writer:
                for obj in osmium.FileProcessor(str(xml)):writer.add(obj)
            with patch.object(chicago,'STATE',state),patch.object(chicago,'guard'),contextlib.redirect_stdout(io.StringIO()):
                chicago.prepare(root)
            ids=[(obj.type_str(),obj.id) for obj in osmium.FileProcessor(str(sources/'chicago.osm.pbf'))]
            self.assertIn(('w',10),ids)
            self.assertIn(('n',1),ids);self.assertIn(('n',2),ids)
            self.assertNotIn(('w',11),ids)
            report=json.loads((state/'inventory.json').read_text())
            self.assertFalse(report['ai_used']);self.assertFalse(report['landmark_overrides'])
            self.assertEqual(report['counts']['selected_ways'],1)
            self.assertTrue((sources/'chicago.osm').exists())

if __name__=='__main__':unittest.main()
