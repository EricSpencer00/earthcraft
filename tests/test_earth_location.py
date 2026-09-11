import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from earth_location import kml_location,select_cached,location
from metric_chart import chart


class LocationTests(unittest.TestCase):
    def test_kml_kmz_and_ambiguous_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'point.kml'
            raw=b'<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Point><coordinates>-87.62443,41.8972,0</coordinates></Point></Placemark></kml>'
            p.write_bytes(raw)
            self.assertEqual(kml_location(p)[:2],(-87.62443,41.8972))
            z=Path(tmp)/'point.kmz'
            with zipfile.ZipFile(z,'w') as archive:archive.writestr('doc.kml',raw)
            self.assertEqual(kml_location(z)[:2],kml_location(p)[:2])
            p.write_bytes(b'<kml><Point/><Point/></kml>')
            with self.assertRaises(ValueError):kml_location(p)
            with self.assertRaises(ValueError):location(float('nan'),1)

    def test_containment_ranking_and_no_wrong_location_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'sources.json').write_text(json.dumps(chart(-87.62443,41.8972,256)))
            cat={'regions':{'neutral':{'source':'s','points':'p'},
                            'photo':{'source':'s','points':'p','photo_layer':{'source_world':'w'}}}}
            found,_=select_cached(-87.62443,41.8972,cat,resolve=lambda _:root)
            self.assertEqual(found['region'],'photo')
            found,_=select_cached(0,0,cat,resolve=lambda _:root)
            self.assertIsNone(found)


if __name__=='__main__':unittest.main()
