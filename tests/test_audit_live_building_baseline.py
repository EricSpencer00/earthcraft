import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from audit_live_building_baseline import historic_packets, protected_bootstrap_receipt


class HistoricPacketTests(unittest.TestCase):
    def test_protected_bootstrap_requires_exact_current_state_without_conflict(self):
        receipt={'mode':'building_delta','chunk':'2,3','result':'applied_in_memory',
                 'written':0,'already_target':5,'conflicts':0}
        self.assertTrue(protected_bootstrap_receipt(receipt,(2,3),5))
        self.assertFalse(protected_bootstrap_receipt({**receipt,'conflicts':1},(2,3),5))
        self.assertFalse(protected_bootstrap_receipt({**receipt,'already_target':4},(2,3),5))

    def test_historic_packets_indexes_receipt_by_coordinate_tuple(self):
        packet={'version':1,'frame':'f','cx':2,'cz':3,'mode':'new_chunk',
                'palette':['minecraft:stone'],'runs':[[0,1,0]],'cells':1,'provenance':{}}
        with TemporaryDirectory() as folder:
            exchange=Path(folder);(exchange/'receipts').mkdir();(exchange/'archive').mkdir()
            identity='a'*64
            (exchange/'receipts'/(identity+'.json')).write_text(json.dumps({
                'patch':identity,'chunk':'2,3','mode':'new_chunk','result':'applied_in_memory',
                'time':'2026-09-12T00:00:00Z'}))
            (exchange/'archive'/(identity+'.json.gz')).write_bytes(gzip.compress(json.dumps(packet).encode(),mtime=0))
            found=historic_packets(exchange,{(2,3):object()})
        self.assertIn((2,3),found)
        self.assertEqual(found[(2,3)][0]['patch'],identity)
        self.assertEqual(found[(2,3)][1]['runs'],[[0,1,0]])


if __name__=='__main__':unittest.main()
