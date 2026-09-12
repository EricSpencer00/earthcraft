import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
spec=importlib.util.spec_from_file_location('dashboard',Path(__file__).resolve().parents[1]/'scripts/dashboard.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RegionTests(unittest.TestCase):
    def test_partial_chunk_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'r.-1.2.mca'
            raw=bytearray(12288)
            raw[:4]=bytes([0,0,2,1])
            raw[4:8]=bytes([0,0,3,1])
            p.write_bytes(raw)
            result=m.region_header(p)
            self.assertEqual((result['x'],result['z'],result['chunks']),(-1,2,1))
    def test_short_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'r.0.0.mca';p.write_bytes(b'partial')
            self.assertIsNone(m.region_header(p))
    def test_queue_uses_tile_cells_and_marks_outside_plan(self):
        plan={'tiles':[
            {'id':'0_0','world_offset_xz':[0,0],'size':256},
            {'id':'2_0','world_offset_xz':[512,0],'size':256},
        ]}
        installed={'tiles':{}}
        def fake_json(path):
            return plan if Path(path)==m.PLAN_PATH else installed
        m.CHUNK_CACHE.clear()
        with patch.object(m,'read_json',side_effect=fake_json), \
             patch.object(m,'_journal_states',return_value=({'0_0':{0:'complete'}},
                 {'leased_tiles':0,'worker_owners':0,'superseded_leases':0})), \
             patch.object(m,'_border_snapshot',return_value=None):
            result=m.chunk_snapshot('queue')
        self.assertEqual(result['area']['cell_size_blocks'],256)
        self.assertEqual(result['area']['width_cells'],3)
        self.assertEqual(result['cells'],[1,-1,0])
        self.assertEqual(result['counts'],{'white':1,'gray':1,'green':0})

    def test_playable_view_uses_exact_applied_receipts_and_protected_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            exchange=Path(tmp);(exchange/'receipts').mkdir()
            (exchange/'binding.json').write_text(json.dumps({'protected_chunks':['0,0']}))
            (exchange/'receipts/applied.json').write_text(json.dumps(
                {'mode':'new_chunk','result':'applied_in_memory','chunk':'0,-2'}))
            (exchange/'receipts/rejected.json').write_text(json.dumps(
                {'mode':'new_chunk','result':'nonempty_chunk_preserved','chunk':'0,-1'}))
            m.PLAYABLE_CACHE.update(exchange=None,receipts=set(),chunks=set())
            chunks,count=m._playable_chunks(exchange)
            self.assertEqual(chunks,{(0,0),(0,-2)})
            self.assertEqual(count,2)

    def test_live_atlas_leaves_unaccepted_chunks_visibly_empty(self):
        plan={'tiles':[{'id':'0_0','world_offset_xz':[0,0],'size':256}]}
        def fake_json(path):
            return plan if Path(path)==m.PLAN_PATH else {'tiles':{}}
        m.CHUNK_CACHE.clear()
        with patch.object(m,'read_json',side_effect=fake_json), \
             patch.object(m,'_playable_chunks',return_value=({(0,0),(0,-2)},2)), \
             patch.object(m,'_journal_states',return_value=({},
                 {'leased_tiles':0,'worker_owners':0,'superseded_leases':0})), \
             patch.object(m,'_border_snapshot',return_value=None), \
             patch.object(m,'_live_import_snapshot',return_value={'state':'ready','pending_chunks':0}):
            result=m.chunk_snapshot('live')
        self.assertEqual(result['area']['cell_size_blocks'],16)
        self.assertEqual(result['area']['top_block'],-32)
        self.assertEqual(result['cells'],[2,-1,2])
        self.assertEqual(result['counts'],{'white':0,'gray':0,'green':2})
        self.assertEqual(result['minecraft_import']['state'],'ready')

    def test_live_import_error_requires_relaunch(self):
        with tempfile.TemporaryDirectory() as tmp:
            exchange=Path(tmp);(exchange/'inbox').mkdir()
            (exchange/'status.json').write_text(json.dumps({'state':'error'}))
            for index in range(3):
                (exchange/'inbox'/f'{index:064x}.json.gz').write_bytes(b'pending')
            result=m._live_import_snapshot(exchange)
        self.assertEqual(result['state'],'error')
        self.assertEqual(result['pending_chunks'],3)
        self.assertTrue(result['needs_minecraft_relaunch'])

    def test_modern_runtime_border_does_not_require_removed_legacy_fields(self):
        plan={'tiles':[{'world_offset_xz':[0,0],'size':256}]}
        expected=m.bounds_for_plan(plan)
        level={'Data':{}}
        runtime={'data':{'center_x':expected['center_x'],'center_z':expected['center_z'],
                         'size':expected['size']}}
        def fake_load(path):
            return level if Path(path).name=='level.dat' else runtime
        with patch.object(m,'read_json',return_value=plan),patch.object(m.n,'load',side_effect=fake_load):
            self.assertTrue(m._border_snapshot()['synced'])
