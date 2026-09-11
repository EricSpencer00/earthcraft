import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import nbtlib as n
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from live_city import cached_region, encode_chunk, validate, publish, digest, allowed, feed, sha, archive_receipted
from metric_world import packed


class LiveTests(unittest.TestCase):
    def patch(self):
        return dict(version=1,frame='fixture',cx=-2,cz=3,mode='new_chunk',
                    palette=['minecraft:stone'],runs=[[0,4,0]],cells=4)

    def test_run_bounds(self):
        for runs in ([[0,0,0]], [[0,4,0],[3,2,0]], [[262143,2,0]], [[-1,4,0]], [[0,4,2]]):
            p=self.patch();p['runs']=runs
            with self.assertRaises(ValueError):validate(p)

    def test_cell_count_and_modes(self):
        p=self.patch();p['cells']=3
        with self.assertRaises(ValueError):validate(p)
        p=self.patch();p['mode']='pavement'
        with self.assertRaises(ValueError):validate(p)

    def test_allowlist_no_code_or_entities(self):
        for name in ['minecraft:command_block','minecraft:chest','minecraft:lava','custom:stone','minecraft:white_concrete[bad]']:
            self.assertFalse(allowed(name,'new_chunk'))
        self.assertTrue(allowed('minecraft:light_gray_concrete','pavement'))
        self.assertTrue(allowed('minecraft:light_gray_stained_glass','new_chunk'))
        self.assertTrue(allowed('minecraft:iron_block','new_chunk'))
        self.assertFalse(allowed('minecraft:light_gray_stained_glass','pavement'))

    def test_native_negative_coordinates_and_cell_order(self):
        values=np.zeros(4096,int);values[[0,15,16,256,4095]]=1
        tag=n.Compound({'xPos':n.Int(-2),'zPos':n.Int(3),'sections':n.List[n.Compound]([
            n.Compound({'Y':n.Byte(-4),'block_states':n.Compound({'palette':n.List[n.Compound]([
                n.Compound({'Name':n.String('minecraft:air')}),n.Compound({'Name':n.String('minecraft:stone')})]),
                'data':packed(values,4)})})])})
        p=encode_chunk(tag,'fixture',{})
        result=np.zeros(262144,int)
        for start,count,code in p['runs']:result[start:start+count]=1
        np.testing.assert_array_equal(result[:4096],values)
        self.assertEqual(int(result.sum()),5)
        self.assertEqual((p['cx'],p['cz']),(-2,3))
        tag['sections'][0]['block_states']['palette'][1]['Properties']=n.Compound({'axis':n.String('x')})
        with self.assertRaises(ValueError):encode_chunk(tag,'fixture',{})

    def test_publication_is_replayable_and_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'inbox').mkdir();(root/'receipts').mkdir()
            first=publish(root,self.patch());second=publish(root,copy.deepcopy(self.patch()))
            self.assertEqual(first,second)
            files=list((root/'inbox').iterdir());self.assertEqual(len(files),1)
            self.assertEqual(digest(files[0].read_bytes()),first)
            self.assertEqual(json.loads(gzip.decompress(files[0].read_bytes())),self.patch())

    def test_archive_uses_bounded_inbox_and_preserves_pending(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in ('inbox','receipts','archive'):(root/name).mkdir()
            (root/'inbox/done.json.gz').write_bytes(b'done')
            (root/'inbox/pending.json.gz').write_bytes(b'pending')
            (root/'receipts/done.json').write_text('{}')
            (root/'receipts/old.json').write_text('{}')
            archive_receipted(root)
            self.assertEqual((root/'archive/done.json.gz').read_bytes(),b'done')
            self.assertEqual((root/'inbox/pending.json.gz').read_bytes(),b'pending')
            self.assertFalse((root/'archive/old.json.gz').exists())

    def test_region_cache_hits_without_changing_encoded_output_and_rejects_mutation(self):
        tag=n.Compound({'xPos':n.Int(0),'zPos':n.Int(0),'sections':n.List[n.Compound]([
            n.Compound({'Y':n.Byte(-4),'block_states':n.Compound({'palette':n.List[n.Compound]([
                n.Compound({'Name':n.String('minecraft:stone')})])})})])})
        with tempfile.TemporaryDirectory() as d:
            region=Path(d)/'r.0.0.mca';region.write_bytes(b'immutable')
            expected=sha(region);chunks={(0,0):tag};checked={};cache={}
            with patch('live_city.read_region',return_value=chunks) as reader:
                cached=cached_region(region,expected,checked,cache)
                hit=cached_region(region,expected,checked,cache)
                self.assertEqual(reader.call_count,1)
                self.assertEqual(encode_chunk(cached[(0,0)],'fixture',{}),
                                 encode_chunk(hit[(0,0)],'fixture',{}))
                self.assertEqual(encode_chunk(cached[(0,0)],'fixture',{}),
                                 encode_chunk(cached_region(region,expected,{},{})[(0,0)],'fixture',{}))
                for name in ('cached','uncached'):
                    target=Path(d)/name;(target/'inbox').mkdir(parents=True);(target/'receipts').mkdir()
                cached_id=publish(Path(d)/'cached',encode_chunk(cached[(0,0)],'fixture',{}))
                uncached_id=publish(Path(d)/'uncached',encode_chunk(cached_region(region,expected,{},{})[(0,0)],'fixture',{}))
                self.assertEqual(cached_id,uncached_id)
                self.assertEqual((Path(d)/'cached'/'inbox'/f'{cached_id}.json.gz').read_bytes(),
                                 (Path(d)/'uncached'/'inbox'/f'{uncached_id}.json.gz').read_bytes())
                region.write_bytes(b'mutated')
                with self.assertRaisesRegex(ValueError,'Immutable region changed'):
                    cached_region(region,expected,checked,cache)

    def test_restart_skips_a_fully_seen_region_without_decoding(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);exchange=root/'exchange';world=root/'world';region=world/'region'/'r.0.0.mca'
            for path in (exchange/'inbox',exchange/'receipts',exchange/'archive',region.parent):path.mkdir(parents=True,exist_ok=True)
            region.write_bytes(b'immutable')
            frame={'crs':'EPSG:26916','vertical_offset_m':0,'west':100,'north':200}
            report={'world_offset_xz':[0,0],'source':{'crs':'EPSG:26916','west':100,'north':200},
                    'vertical_offset_m':0,'dimension_height':1024,'dimension_min_y':-64}
            report_path=world/'earthcraft.json';report_path.write_text(json.dumps(report))
            receipt={'world_manifest_sha256':sha(report_path),'regions':{region.name:sha(region)}}
            evidence=root/'evidence.json';evidence.write_text(json.dumps(receipt))
            (exchange/'binding.json').write_text(json.dumps({'protected_chunks':['0,0'],'frame':frame,'coordinate_frame':frame}))
            journal=root/'jobs.sqlite'
            import sqlite3
            with sqlite3.connect(journal) as db:
                db.execute('CREATE TABLE jobs (tile, evidence, evidence_sha256, stage, state, priority)')
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('tile',str(evidence),sha(evidence),1,'complete',0))
            with patch('live_city.read_region',return_value={(0,0):object()}) as reader:
                feed(exchange,journal,once=True)
                self.assertEqual(reader.call_count,1)
                self.assertEqual(json.loads((exchange/'published.json').read_text())['complete_regions'],{str(region):sha(region)})
                feed(exchange,journal,once=True)
                self.assertEqual(reader.call_count,1)
            state=json.loads((exchange/'published.json').read_text())
            self.assertEqual(state['complete_regions'][str(region)],sha(region))


if __name__=='__main__':unittest.main()
