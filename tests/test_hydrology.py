import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_world import water_polygon, waterway_width
from osm_hydrology import extract
from public_map_sources import normalize


class HydrologyTests(unittest.TestCase):
    def test_mapped_water_surface_tags(self):
        self.assertTrue(water_polygon({'natural':'water'}))
        self.assertTrue(water_polygon({'waterway':'riverbank'}))
        self.assertFalse(water_polygon({'natural':'beach'}))
        self.assertEqual(waterway_width({'waterway':'river','width':'12 m'}),12)
        self.assertIsNone(waterway_width({'waterway':'river'}))

    def test_overpass_water_relation_geometry_is_preserved(self):
        data={'elements':[
            {'type':'relation','id':99,'tags':{'natural':'water','water':'lake'},
             'members':[
                 {'type':'way','ref':1,'role':'outer','geometry':[{'lon':0,'lat':0},{'lon':2,'lat':0},{'lon':2,'lat':2}]},
                 {'type':'way','ref':2,'role':'outer','geometry':[{'lon':2,'lat':2},{'lon':0,'lat':2},{'lon':0,'lat':0}]},
             ]}
        ]}
        ways,inventory=normalize(data)
        self.assertEqual(len(ways),1)
        self.assertEqual(ways[0]['tags']['source_relation_id'],99)
        self.assertEqual(inventory['water_relation_features'],1)

    @unittest.skipUnless(os.environ.get('EARTHCRAFT_RUN_EXTERNAL_HYDROLOGY') == '1' and
                         Path('/Volumes/LaCie/Earthcraft/chicago/sources/chicago.osm.pbf').exists(),
                         'External Chicago hydrology extraction is opt-in')
    def test_lake_michigan_relation_is_extractable(self):
        features,report=extract('/Volumes/LaCie/Earthcraft/chicago/sources/chicago.osm.pbf',
                                relation_names=['Lake Michigan'])
        lake=[feature for feature in features if feature['tags'].get('name')=='Lake Michigan']
        self.assertTrue(lake)
        self.assertEqual(report['relation_polygon_count'],len(lake))
        self.assertTrue(any(feature['closed'] for feature in lake))


if __name__=='__main__':
    unittest.main()
