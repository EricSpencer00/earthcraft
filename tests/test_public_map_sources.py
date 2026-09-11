import sys
from pathlib import Path
import unittest
import json
import io
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from public_map_sources import normalize,prepare
from metric_chart import chart
from osm_json_to_kml import metres,building_tag


class PublicMapTests(unittest.TestCase):
    def test_explicit_negative_tags_and_nonfinite_dimensions(self):
        self.assertFalse(building_tag({'building':'no'}))
        self.assertTrue(building_tag({'building':'no','building:part':'yes'}))
        for value in ('9'*500,'NaN','infinity',None,23):self.assertIsNone(metres(value))
        self.assertEqual(metres('12.5 m'),12.5)

    def test_resume_is_frozen_bounded_and_offline_after_response(self):
        def terrain(lon,lat,size,path):
            path.mkdir()
            for name in ('elevation.tif','rasters.npz','cook-buildings-2022.json'):
                (path/name).write_bytes(b'terrain fixture')
            (path/'sources.json').write_text(json.dumps(chart(lon,lat,size)))
            return path
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source'
            with patch('public_map_sources.terrain',side_effect=terrain) as acquire,patch('public_map_sources.urllib.request.urlopen',side_effect=OSError('offline')):
                with self.assertRaisesRegex(OSError,'offline'):prepare(0,0,16,source)
                self.assertEqual(acquire.call_count,1)
            with patch('public_map_sources.urllib.request.urlopen') as network:
                with self.assertRaisesRegex(ValueError,'cooldown'):prepare(0,0,16,source,resume=True)
                network.assert_not_called()
            statepath=Path(tmp)/'source-acquisition.json'
            state=json.loads(statepath.read_text());state['last_request_utc']='2000-01-01T00:00:00+00:00'
            statepath.write_text(json.dumps(state))
            response=io.BytesIO(b'{"elements":[]}')
            # Simulate an interruption after the complete raw response is frozen.
            with patch('public_map_sources.terrain') as acquire,patch('public_map_sources.urllib.request.urlopen',return_value=response),patch('public_map_sources.normalize',side_effect=RuntimeError('interrupted')):
                with self.assertRaisesRegex(RuntimeError,'interrupted'):prepare(0,0,16,source,resume=True)
                acquire.assert_not_called()
            with patch('public_map_sources.urllib.request.urlopen') as network:
                self.assertEqual(prepare(0,0,16,source,resume=True),source)
                self.assertEqual(prepare(0,0,16,source,resume=True),source)
                with self.assertRaisesRegex(ValueError,'request'):prepare(.001,0,16,source,resume=True)
                (source/'osm-ways.json').write_text('["corruption"]')
                with self.assertRaisesRegex(ValueError,'changed'):prepare(0,0,16,source,resume=True)
                network.assert_not_called()

    def test_explicit_height_and_complete_geometry(self):
        way={'type':'way','id':3,'nodes':[1,2,3,1],
             'geometry':[{'lon':x,'lat':y} for x,y in [(0,0),(.001,0),(.001,.001),(0,0)]],
             'tags':{'building':'yes','height':'12 m'}}
        ways,report=normalize({'elements':[way,{'type':'relation','id':4}]})
        self.assertTrue(ways[0]['closed']);self.assertEqual(report['explicit_height_features'],1)
        self.assertEqual(len(report['omitted_features']),1)
        way['tags']={'building':'yes','building:levels':'3'}
        self.assertEqual(normalize({'elements':[way]})[1]['height_unknown_features'],1)
        way['geometry'].pop()
        with self.assertRaises(ValueError):normalize({'elements':[way]})
        with self.assertRaises(ValueError):normalize({'remark':'timeout','elements':[]})


if __name__=='__main__':unittest.main()
