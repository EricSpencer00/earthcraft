from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from inspect_world import chunks


class RegionBoundaryTests(unittest.TestCase):
    def test_truncated_sidecar_and_out_of_file_offset_are_explicit_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);p=root/'r.0.0.mca'
            p.write_bytes(b'');self.assertEqual(list(chunks(p)),[])
            p.write_bytes(b'\x00'*4096)
            with self.assertRaisesRegex(ValueError,'region length'):list(chunks(p))
            raw=bytearray(8192);raw[:4]=b'\x00\x00\x03\x01';p.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'sector outside'):list(chunks(p))
            (root/'._r.0.0.mca').write_bytes(b'AppleDouble metadata')
            self.assertEqual(list(root.glob('r.*.*.mca')),[p])


if __name__=='__main__':unittest.main()
