import importlib.util
from pathlib import Path
import tempfile
import unittest
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
