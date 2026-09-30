import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from shapely.geometry import box
from shapely import to_wkb

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from regional_footprints import crop,load_shapes


class FootprintTests(unittest.TestCase):
    def test_crop_uses_metric_extent_and_requires_unchanged_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory);index=source/'index.sqlite';grid={'crs':'EPSG:26916','west':-16,'north':32,'size':16}
            record={'crs':grid['crs'],'release':'frozen','source':'frozen source','license':'ODbL 1.0'}
            with sqlite3.connect(index) as db:
                db.executescript('CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE buildings(rowid INTEGER PRIMARY KEY,id TEXT,geometry BLOB,source TEXT); CREATE VIRTUAL TABLE bounds USING rtree(rowid,minx,maxx,miny,maxy);')
                db.execute('INSERT INTO meta VALUES(?,?)',('source',json.dumps(record)))
                for row,(identifier,geometry) in enumerate([('inside',box(-12,20,-10,22)),('outside',box(2,20,4,22))],1):
                    db.execute('INSERT INTO buildings VALUES(?,?,?,?)',(row,identifier,to_wkb(geometry),'[]'))
                    left,bottom,right,top=geometry.bounds;db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(row,left,right,bottom,top))
            receipt=crop(index,grid,source/'scan-footprints.geojson');self.assertEqual(receipt['count'],1)
            self.assertFalse(receipt['geometry_measured'])
            with self.assertRaisesRegex(ValueError,'source receipt'):load_shapes(source,grid)
            grid['scan_footprint_receipt']=receipt
            shapes=load_shapes(source,grid);self.assertEqual(shapes[0][0],'inside')
            with self.assertRaisesRegex(ValueError,'another tile grid'):load_shapes(source,{**grid,'north':48})
            with (source/'scan-footprints.geojson').open('a') as stream:stream.write(' ')
            with self.assertRaisesRegex(ValueError,'changed'):load_shapes(source,grid)


if __name__=='__main__':unittest.main()
