"""Synthetic city geometry and crash/restart tests; not geographic accuracy claims."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chicago_tiles import Journal, tile_plan
from metric_frame import tile_layout,validate_frame


FRAME={'crs':'EPSG:3857','west':0,'north':0,'vertical_offset_m':-100,
       'dimension_min_y':-64,'dimension_height':1024}


def fixture_plan():
    inverse=Transformer.from_crs(3857,4326,always_xy=True)
    geometry=box(-30,-30,30,30).difference(box(-5,-5,5,5))
    document={'type':'FeatureCollection','features':[{'type':'Feature','properties':{},
        'geometry':mapping(transform(inverse.transform,geometry))}]}
    return tile_plan(document,FRAME,tile_size=16,halo=8)


class ChicagoTileTests(unittest.TestCase):
    def test_polygon_area_grid_holes_and_replay(self):
        a=fixture_plan();b=fixture_plan()
        self.assertEqual(a,b)
        self.assertEqual(len(a['tiles']),16)
        self.assertAlmostEqual(a['city_area_m2'],3500,places=6)
        self.assertAlmostEqual(sum(t['city_area_m2'] for t in a['tiles']),3500,places=6)
        self.assertFalse(a['playable_city'])
        self.assertEqual(a['appearance_tiles_verified'],0)
        for t in a['tiles']:
            self.assertEqual(t['world_offset_xz'],[t['tx']*16,t['tz']*16])
            self.assertEqual(t['source_bounds'],[t['west']-8,t['north']-24,t['west']+24,t['north']+8])

    def test_single_vertical_reference_and_alignment_validation(self):
        meta={'crs':FRAME['crs'],'size':16,'west':-32,'north':16}
        low=np.full((16,16),164.2);high=low+12
        a=tile_layout(meta,low,FRAME);b=tile_layout(meta,high,FRAME)
        self.assertEqual(a[0],b[0]);self.assertEqual(a[3],[-32,-16])
        np.testing.assert_array_equal(b[1]-a[1],12)
        for key,value in [('west',-31),('crs','EPSG:4326'),('size',17)]:
            bad=dict(meta);bad[key]=value
            with self.assertRaises(ValueError):tile_layout(bad,low,FRAME)
        for elevation in [low*np.nan,low+2000,low-2000]:
            with self.assertRaises(ValueError):tile_layout(meta,elevation,FRAME)
        with self.assertRaises(ValueError):tile_layout(meta,low,FRAME,2000)
        for crs in ('EPSG:4326','EPSG:3435'):
            with self.assertRaises(ValueError):validate_frame(dict(FRAME,crs=crs))

    def test_journal_fences_expired_workers_and_resume_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=fixture_plan();path=root/'jobs.sqlite'
            journal=Journal(path,plan)
            self.assertIsNone(journal.claim('geometry','worker',now=1))
            job=journal.claim('sources','old',now=1,lease_seconds=2)
            # A concurrent connection cannot claim the same tile while its lease lives.
            second=Journal(path,plan)
            other=second.claim('sources','other',now=2,lease_seconds=10)
            self.assertNotEqual(job['tile'],other['tile'])
            retry=second.claim('sources','new',now=4)
            self.assertEqual(job['tile'],retry['tile']);self.assertNotEqual(job['token'],retry['token'])
            receipt=root/'receipt.json'
            receipt.write_text(json.dumps({'tile':job['tile'],'stage':'sources','result':'pass'}))
            with self.assertRaises(ValueError):journal.finish(job,receipt,now=5)
            second.finish(retry,receipt,now=5)
            journal.close();second.close()
            journal=Journal(path,plan)
            self.assertEqual(sum(s['count'] for s in journal.summary()),64)
            next_job=journal.claim('geometry','worker',now=6)
            self.assertEqual(next_job['tile'],job['tile'])
            self.assertIsNone(journal.claim('appearance','worker',now=6))
            journal.close()
            changed=copy.deepcopy(plan);changed['frame']['west']=99
            with self.assertRaises(ValueError):Journal(path,changed)

    def test_changed_receipt_cannot_unlock_next_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);journal=Journal(root/'jobs.sqlite',fixture_plan())
            job=journal.claim('sources','worker',now=1)
            receipt=root/'receipt.json'
            receipt.write_text(json.dumps({'tile':job['tile'],'stage':'sources','result':'fail'}))
            with self.assertRaises(ValueError):journal.finish(job,receipt,now=2)
            receipt.write_text(json.dumps({'tile':job['tile'],'stage':'sources','result':'pass'}))
            journal.finish(job,receipt,now=2)
            receipt.write_text('{}')
            with self.assertRaises(ValueError):journal.claim('geometry','worker',now=3)
            journal.close()

    def test_failed_source_does_not_block_other_tiles_or_unlock_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);journal=Journal(root/'jobs.sqlite',fixture_plan())
            job=journal.claim('sources','worker',now=1)
            receipt=root/'failure.json';receipt.write_text(json.dumps(dict(job,result='failed',error='missing source')))
            journal.fail(job,receipt)
            self.assertIsNone(journal.claim('geometry','worker',now=2))
            other=journal.claim('sources','worker',now=2)
            self.assertNotEqual(other['tile'],job['tile'])
            with self.assertRaises(ValueError):journal.fail(job,receipt)
            journal.close()


if __name__=='__main__':unittest.main()
