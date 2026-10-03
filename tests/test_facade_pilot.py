import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from pyproj import Transformer
from shapely.geometry import box

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from facade_pilot import select,attach_cached_photo_candidates,canonical_sha
from playable_acceptance_snapshot import ANCHORS

FRAME={'crs':'+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +datum=WGS84 +units=m',
       'west':-32,'north':33,'vertical_offset_m':-116,'dimension_min_y':-64,'dimension_height':1024}


def database(path):
    db=sqlite3.connect(path)
    db.executescript('CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE buildings(id TEXT,geometry BLOB,source TEXT); CREATE VIRTUAL TABLE bounds USING rtree(rowid,minx,maxx,miny,maxy);')
    db.execute('INSERT INTO meta VALUES(?,?)',('source',json.dumps({'crs':FRAME['crs'],'provider':'fixture'})))
    project=Transformer.from_crs(4326,FRAME['crs'],always_xy=True)
    for area,lonlat in ANCHORS.items():
        east,north=project.transform(*lonlat)
        for ordinal in reversed(range(13)):
            geometry=box(east+ordinal*8-3,north-3,east+ordinal*8+3,north+3)
            rowid=db.execute('INSERT INTO buildings VALUES(?,?,?)',(f'{area}-{ordinal:02d}',geometry.wkb,json.dumps({'dataset':'fixture'}))).lastrowid
            left,bottom,right,top=geometry.bounds
            db.execute('INSERT INTO bounds VALUES(?,?,?,?,?)',(rowid,left,right,bottom,top))
    db.commit();db.close()


class PilotTests(unittest.TestCase):
    def test_twenty_nearest_source_ids_are_stable_and_do_not_use_photo_ranking(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'index.sqlite';database(path)
            first=select(path,FRAME);second=select(path,FRAME)
            self.assertEqual(first['selection_sha256'],second['selection_sha256'])
            self.assertEqual(first['building_count'],20)
            for area in ANCHORS:
                self.assertEqual([b['source_building_id'] for b in first['buildings'] if b['area']==area],
                                 [f'{area}-{i:02d}' for i in range(10)])
            self.assertTrue(all(b['registration']=='pending' and not b['world_wall_anchor_verified'] for b in first['buildings']))
            self.assertFalse(first['new_face_colors_applied'])

    def test_coordinate_frame_cannot_be_rebased(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'index.sqlite';database(path)
            frame=dict(FRAME,crs='EPSG:3857')
            with self.assertRaisesRegex(ValueError,'inherited frame'):select(path,frame)
            with self.assertRaisesRegex(ValueError,'ten buildings'):select(path,FRAME,9)

    def test_cached_discovery_keeps_zero_coverage_buildings_and_no_signed_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'index.sqlite';database(path);pilot=select(path,FRAME)
            (root/'fixture-nearby.json').write_text(json.dumps({'data':[{'id':'123','captured_at':1000,
                'camera_type':'spherical','is_pano':True,'thumb_2048_url':'https://private-candidate.example',
                'geometry':{'type':'Point','coordinates':ANCHORS['elmhurst']}}]}))
            result=attach_cached_photo_candidates(pilot,root)
            per=result['photo_discovery']['per_building']
            self.assertEqual(len(per),20);self.assertEqual(len(per['downtown-01']['nearby_metadata_candidates']),0)
            self.assertEqual(len(per['elmhurst-01']['nearby_metadata_candidates']),1)
            self.assertNotIn('private-candidate',json.dumps(result))
            self.assertEqual(canonical_sha(result['buildings']),result['selection_sha256'])

    def test_selection_mutation_cannot_be_hidden_by_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'index.sqlite';database(path);pilot=select(path,FRAME)
            pilot['buildings'][0]['source_building_id']='replacement'
            with self.assertRaisesRegex(ValueError,'selection'):attach_cached_photo_candidates(pilot,folder)
