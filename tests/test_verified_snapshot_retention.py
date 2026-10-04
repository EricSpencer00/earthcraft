import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verified_snapshot_retention import verify,release_stage,digest


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name).resolve();self.local=base/'runs/world-snapshots/snapshot'
        self.remote=base/'retained/snapshot'
        self.original=base/'playable/region/r.0.0.mca';self.original.parent.mkdir(parents=True);self.original.write_bytes(b'user edits')
        for root in [self.local,self.remote]:
            (root/'world/region').mkdir(parents=True);(root/'package').mkdir()
            (root/'world/region/r.0.0.mca').write_bytes(b'old staged world')
            archive=root/'package/region.tar';archive.write_bytes(b'archive fixture')
            source=root/'world/region/r.0.0.mca';stat=source.stat()
            manifest={'state':'complete','file_count':1,'source_bytes':stat.st_size,
                      'shards':[{'path':'region.tar','bytes':archive.stat().st_size,'sha256':digest(archive),
                                 'files':[{'path':'region/r.0.0.mca','bytes':stat.st_size,'sha256':digest(source)}]}]}
            (root/'package/manifest.json').write_text(json.dumps(manifest))
            (root/'world.stage.json').write_text(json.dumps({'files':[{'path':'region/r.0.0.mca','bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns}]}))
        self.proof=verify(self.remote)

    def test_release_only_redundant_stage_and_preserve_original(self):
        result=release_stage(self.local,self.proof)
        self.assertTrue(result['local_staging_released']);self.assertFalse((self.local/'world').exists())
        self.assertEqual(self.original.read_bytes(),b'user edits');self.assertTrue((self.remote/'world/region/r.0.0.mca').exists())
        self.assertTrue((self.local/'package/manifest.json').exists())

    def test_edited_stage_is_preserved(self):
        (self.local/'world/region/r.0.0.mca').write_bytes(b'new edits')
        with self.assertRaisesRegex(ValueError,'edited'):release_stage(self.local,self.proof)
        self.assertTrue((self.local/'world').exists())

    def test_same_size_edit_with_restored_timestamp_is_preserved(self):
        source = self.local/'world/region/r.0.0.mca'
        stat = source.stat()
        source.write_bytes(b'new edited world')
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(source.stat().st_size, stat.st_size)
        with self.assertRaisesRegex(ValueError, 'contents were edited'):
            release_stage(self.local, self.proof)
        self.assertTrue(source.exists())

    def test_changed_retained_source_refuses_proof(self):
        (self.remote/'world/region/r.0.0.mca').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source changed'):verify(self.remote)

    def test_changed_retained_archive_refuses_proof(self):
        (self.remote/'package/region.tar').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'archive changed'):verify(self.remote)

    def test_unverified_or_stale_proof_preserves_stage(self):
        self.proof['verified_at']=time.time()-86401
        with self.assertRaisesRegex(ValueError,'recent'):release_stage(self.local,self.proof)
        self.assertTrue((self.local/'world').exists())

    def test_live_save_target_rejected(self):
        with self.assertRaisesRegex(ValueError,'task world-snapshots'):release_stage(self.original.parent.parent,self.proof)

    def test_dry_run_keeps_staging(self):
        r=release_stage(self.local,self.proof,True)
        self.assertTrue(r['dry_run']);self.assertTrue((self.local/'world/region/r.0.0.mca').exists())

    def test_archive_contents_verified_without_old_staging_directory(self):
        import tarfile,io
        archive=self.remote/'package/region.tar';data=(self.remote/'world/region/r.0.0.mca').read_bytes()
        with tarfile.open(archive,'w:gz') as tar:
            item=tarfile.TarInfo('region/r.0.0.mca');item.size=len(data);tar.addfile(item,io.BytesIO(data))
        m=json.loads((self.remote/'package/manifest.json').read_text());m['shards'][0].update(bytes=archive.stat().st_size,sha256=digest(archive))
        (self.remote/'package/manifest.json').write_text(json.dumps(m))
        r=verify(self.remote,True)
        self.assertEqual(r['source_storage'],'verified_archive_contents');self.assertEqual(r['file_count'],1)

    def test_closed_excluded_minecraft_session_lock_is_allowed(self):
        s=json.loads((self.local/'world.stage.json').read_text());s['excluded']=['session.lock']
        (self.local/'world.stage.json').write_text(json.dumps(s));(self.local/'world/session.lock').write_bytes(b'\xe2\x98\x83')
        self.assertTrue(release_stage(self.local,self.proof,True)['dry_run'])

    def test_unexpected_added_file_is_preserved(self):
        (self.local/'world/new-user-edit.txt').write_text('preserve')
        with self.assertRaisesRegex(ValueError,'file list changed'):release_stage(self.local,self.proof)
        self.assertTrue((self.local/'world/new-user-edit.txt').exists())


if __name__ == '__main__':unittest.main()
