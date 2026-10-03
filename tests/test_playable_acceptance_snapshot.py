import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from playable_acceptance_snapshot import capture,sha
from playable_world_acceptance import verify_snapshot

FRAME={'crs':'+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +datum=WGS84 +units=m',
       'west':-32,'north':33,'vertical_offset_m':-116,'dimension_min_y':-64,'dimension_height':1024}


def fixture(root):
    world=root/'save';world.mkdir();(world/'session.lock').write_bytes(bytes(3))
    (world/'level.dat').write_bytes(b'frozen-player-inventory')
    (world/'city-coverage.json').write_text(json.dumps({'frame':FRAME}))
    (world/'regional-quality.json').write_text(json.dumps({'frame':FRAME,'tiles':{'fixture':{'quality':'scan-points-and-roof','upgraded_chunks':1}}}))
    (world/'region').mkdir();(world/'region/r.-52.-1.mca').write_bytes(b'frozen-elmhurst-chunks')
    (world/'region/r.999.999.mca').write_bytes(b'outside-bounded-excerpt')
    binding=root/'binding.json';binding.write_text(json.dumps({'world':str(world),'frame':'inherited-plan','coordinate_frame':FRAME}))
    return world,binding


class SnapshotTests(unittest.TestCase):
    def test_bounded_capture_preserves_source_bytes_and_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,binding=fixture(root)
            before={p.name:sha(p) for p in world.rglob('*') if p.is_file()}
            capture(world,binding,root/'excerpt');proof=verify_snapshot(root/'excerpt')
            self.assertEqual(proof['frame'],FRAME);self.assertEqual(proof['scan_upgrade_tiles'],1)
            self.assertEqual(proof['binding_frame'],'inherited-plan')
            self.assertTrue((root/'excerpt/world/region/r.-52.-1.mca').exists())
            self.assertFalse((root/'excerpt/world/region/r.999.999.mca').exists())
            self.assertEqual(before,{p.name:sha(p) for p in world.rglob('*') if p.is_file()})
            self.assertAlmostEqual(proof['anchors']['elmhurst']['world_xz'][0],-26182.7018966,places=5)
            self.assertFalse(proof['full_world_acceptance'])

    def test_frame_mismatch_and_byte_budget_fail_before_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,binding=fixture(root)
            with self.assertRaisesRegex(ValueError,'fixed bounds'):capture(world,binding,root/'too-large',max_bytes=1)
            self.assertFalse((root/'too-large').exists())
            (world/'city-coverage.json').write_text(json.dumps({'frame':dict(FRAME,west=0)}))
            with self.assertRaisesRegex(ValueError,'frame differ'):capture(world,binding,root/'wrong-frame')
            self.assertFalse((root/'wrong-frame').exists())

    def test_existing_output_and_changed_snapshot_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,binding=fixture(root);capture(world,binding,root/'excerpt')
            with self.assertRaises(FileExistsError):capture(world,binding,root/'excerpt')
            (root/'excerpt/world/level.dat').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'Frozen save'):verify_snapshot(root/'excerpt')

    def test_source_symlinks_do_not_expand_the_transfer_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,binding=fixture(root)
            (root/'outside.txt').write_bytes(b'outside the authorized excerpt')
            (world/'level.dat').unlink();(world/'level.dat').symlink_to(root/'outside.txt')
            with self.assertRaisesRegex(ValueError,'inside the bound world'):capture(world,binding,root/'excerpt')
            self.assertFalse((root/'excerpt').exists())
