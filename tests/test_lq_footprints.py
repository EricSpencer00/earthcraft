"""LQ shells for Overture footprints that OSM has not mapped."""
from pathlib import Path
import sys
import unittest

import numpy as np
from shapely.geometry import box

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_world import supplemental_buildings


class SupplementalBuildingTests(unittest.TestCase):
    def setUp(self):
        # Tile: x 100..132 east, y 200..168 south (metre CRS, north up).
        self.meta={'size':32,'west':100,'north':200}
        self.elevation=np.full((32,32),10.0)
        osm=np.zeros((32,32),bool);osm[2:10,2:10]=True
        self.osm=[{'id':1,'mask':osm}]

    def test_unmapped_footprint_gets_presentation_height(self):
        house=box(120,180,130,190)  # 10 x 10 m, east and south of the OSM building
        [b]=supplemental_buildings([('house',house)],self.meta,self.osm,self.elevation,offset=-5)
        self.assertEqual((b['id'],b['height'],b['low'],b['high']),('house',6,5,10))
        self.assertEqual(int(b['mask'].sum()),100)
        self.assertTrue(b['wall'][10,20] and not b['wall'][15,25])  # hollow shell, like OSM shells

    def test_small_footprint_is_an_outbuilding(self):
        [shed]=supplemental_buildings([('shed',box(120,180,125,185))],self.meta,[],self.elevation,0)
        self.assertEqual(shed['height'],3)

    def test_mapped_building_is_not_doubled_and_overlap_stays_osm(self):
        same=box(102,190,110,198)  # the OSM building's own outline from Overture's OSM source
        partial=box(108,186,118,196)  # mostly new, overlapping the OSM building's corner
        result=supplemental_buildings([('same',same),('partial',partial)],self.meta,self.osm,self.elevation,0)
        self.assertEqual([b['id'] for b in result],['partial'])
        self.assertFalse((result[0]['mask']&self.osm[0]['mask']).any())

    def test_overlapping_supplements_do_not_share_cells(self):
        a,b=box(112,172,122,182),box(120,172,130,182)  # 20% overlap: neighbours, not one building
        result=supplemental_buildings([('a',a),('b',b)],self.meta,[],self.elevation,0)
        self.assertEqual([x['id'] for x in result],['a','b'])
        self.assertFalse((result[0]['mask']&result[1]['mask']).any())


if __name__=='__main__':unittest.main()
