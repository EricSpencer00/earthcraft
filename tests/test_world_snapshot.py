import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from world_snapshot import digest, inventory, package, stage, shard_key
from restore_world_snapshot import restore
from upload_world_snapshot import upload


def fixture(root):
    world = root/'world'
    world.mkdir()
    # Real gzip/NBT envelope; arbitrary embedded player data remains byte exact.
    value = b'inventory-and-spawn'
    player = b'\x0a\x00\x00\x08\x00\x06Player'+len(value).to_bytes(2, 'big')+value+b'\x00'
    (world/'level.dat').write_bytes(gzip.compress(player, mtime=0))
    (world/'level.dat_old').write_bytes(b'previous-level-backup')
    (world/'session.lock').write_bytes(bytes(3))
    (world/'playerdata').mkdir()
    (world/'playerdata/local-player.dat').write_bytes(b'inventory slots and position')
    (world/'data').mkdir()
    (world/'data/DistantHorizons.sqlite').write_bytes(b'derived cache')
    (world/'city-coverage.json').write_text(json.dumps({'frame': {'west': -32, 'north': 33, 'vertical_offset_m': -116}}))
    (world/'regional-quality.json').write_text(json.dumps({'tiles': {'0_0': {'quality': 'LQ'}}}))
    for relative in ('region/r.-1.0.mca', 'region/r.-9.0.mca',
                     'entities/r.-1.0.mca', 'DIM-1/region/r.0.0.mca'):
        path = world/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = bytearray(12288)
        raw[0:4] = b'\x00\x00\x02\x01'
        raw[8192:8201] = b'unchanged'
        path.write_bytes(raw)
    (world/'region/c.-1.0.mcc').write_bytes(b'oversized external chunk')
    return world


def pack(world, output):
    with contextlib.redirect_stdout(io.StringIO()):
        return package(world, output, snapshot_id='fixture', reserve_gib=0)


class FakeHub:
    def __init__(self):
        self.files = {}
        self.calls = []
        self.private = True
        self.owner = 'fixture-owner'
        self.fail_at = None
        self.bad_checksum = False

    def whoami(self):
        return {'name': self.owner}

    def repo_info(self, repo_id, **kwargs):
        siblings = []
        for path, raw in self.files.items():
            sha = hashlib.sha256(raw).hexdigest()
            if self.bad_checksum:
                sha = '0'*64
            # Exercise both Hub representations used by huggingface_hub 0.24.
            lfs = {'sha256': sha} if path.endswith('.tar.gz') else None
            blob = hashlib.sha1(('blob '+str(len(raw))+'\0').encode()+raw).hexdigest()
            siblings.append(SimpleNamespace(rfilename=path, lfs=lfs, blob_id=blob))
        return SimpleNamespace(id=repo_id, private=self.private, siblings=siblings)

    def upload_file(self, path_or_fileobj, path_in_repo, **kwargs):
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise ConnectionError('simulated interrupted transfer')
        self.files[path_in_repo] = Path(path_or_fileobj).read_bytes()
        self.calls.append(path_in_repo)


class WorldSnapshotTests(unittest.TestCase):
    def test_full_restore_keeps_coordinates_regions_and_player_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root)
            staged = root/'staged'; stage(world, staged)
            manifest = pack(staged, root/'package')
            self.assertEqual(manifest['minecraft_chunks'], 3)
            self.assertEqual(manifest['installed_quality_tiles'], {'LQ': 1})
            self.assertEqual(manifest['world_frame']['vertical_offset_m'], -116)
            restored = root/'restored'
            result = restore(root/'package', restored)
            self.assertTrue(result['complete_save'])
            selected, excluded = inventory(world)
            self.assertIn('data/DistantHorizons.sqlite', excluded)
            for entry in selected:
                self.assertEqual((world/entry['path']).read_bytes(), (restored/entry['path']).read_bytes())
            self.assertEqual({f['path'] for f in inventory(restored)[0]}, {f['path'] for f in selected})
            self.assertEqual((restored/'session.lock').read_bytes(), bytes(3))
            self.assertFalse((restored/'data/DistantHorizons.sqlite').exists())

    def test_negative_boundaries_external_payload_and_selected_restore(self):
        self.assertEqual(shard_key('region/r.-1.0.mca'), 'overworld/x-1_z0')
        self.assertEqual(shard_key('region/r.-8.0.mca'), 'overworld/x-1_z0')
        self.assertEqual(shard_key('region/r.-9.0.mca'), 'overworld/x-2_z0')
        self.assertEqual(shard_key('region/c.-1.0.mcc'), 'overworld/x-1_z0')
        self.assertEqual(shard_key('region/c.-257.0.mcc'), 'overworld/x-2_z0')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root)
            pack(world, root/'package')
            restore(root/'package', root/'restored', selected=['overworld/x-1_z0'])
            self.assertTrue((root/'restored/level.dat').exists())
            self.assertTrue((root/'restored/playerdata/local-player.dat').exists())
            self.assertTrue((root/'restored/region/c.-1.0.mcc').exists())
            self.assertFalse((root/'restored/region/r.-9.0.mca').exists())

    def test_live_java_compatible_lock_refuses_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root)
            code = 'import fcntl,sys; f=open(sys.argv[1],"r+b"); fcntl.lockf(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read()'
            child = subprocess.Popen([sys.executable, '-c', code, str(world/'session.lock')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'locked')
                with self.assertRaisesRegex(RuntimeError, 'Save and Quit'):
                    stage(world, root/'staged')
                self.assertFalse((root/'staged').exists())
            finally:
                child.communicate('done', timeout=5)

    def test_symlinks_and_launcher_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root)
            link = world/'outside'; link.symlink_to(root/'unknown')
            with self.assertRaisesRegex(ValueError, 'Nonregular'):
                inventory(world)
            link.unlink()
            (world/'launcher_accounts.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'credential'):
                inventory(world)

    def test_resume_checks_shards_and_rejects_changed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root); output = root/'package'
            first = pack(world, output)
            before = {s['path']: (output/s['path']).stat().st_mtime_ns for s in first['shards']}
            pack(world, output)
            self.assertEqual(before, {s['path']: (output/s['path']).stat().st_mtime_ns for s in first['shards']})
            (world/'level.dat').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'input differs'):
                pack(world, output)

    def test_damaged_archive_is_rejected_before_creating_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root); output = root/'package'
            manifest = pack(world, output)
            (output/manifest['shards'][0]['path']).write_bytes(b'truncated')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                restore(output, root/'restored')
            self.assertFalse((root/'restored').exists())

    def test_archive_traversal_and_unobserved_member_are_rejected(self):
        for name in ('../outside', 'unlisted'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); world = fixture(root); output = root/'package'
                manifest = pack(world, output)
                shard = manifest['shards'][0]; archive_path = output/shard['path']
                with tarfile.open(archive_path, 'w:gz') as archive:
                    member = tarfile.TarInfo(name); member.size = 3
                    archive.addfile(member, io.BytesIO(b'bad'))
                shard['sha256'] = digest(archive_path)
                (output/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    restore(output, root/'restored')
                self.assertFalse((root/'outside').exists())

    def test_incomplete_region_not_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root)
            (world/'region/r.-1.0.mca').write_bytes(b'partial')
            with self.assertRaisesRegex(ValueError, 'Incomplete Anvil'):
                pack(world, root/'package')
            self.assertFalse((root/'package/manifest.json').exists())


class SnapshotUploadTests(unittest.TestCase):
    def test_completed_snapshot_identifier_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root); output = root/'package'
            pack(world, output)
            api = FakeHub()
            api.files['snapshots/fixture/manifest.json'] = b'different completed snapshot'
            with self.assertRaisesRegex(ValueError, 'different completed manifest'):
                upload(output, 'fixture-owner/world', api=api)
            self.assertFalse(api.calls)

    def test_interruption_resumes_verified_files_and_publishes_manifest_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root); output = root/'package'
            manifest = pack(world, output)
            api = FakeHub(); api.fail_at = 2
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ConnectionError):
                upload(output, 'fixture-owner/world', api=api)
            self.assertFalse(any(p.endswith('manifest.json') for p in api.files))
            initial = list(api.calls); api.fail_at = None
            with contextlib.redirect_stdout(io.StringIO()):
                result = upload(output, 'fixture-owner/world', api=api)
            self.assertEqual(api.calls[:2], initial)
            self.assertEqual(len(api.calls), len(set(api.calls)))
            self.assertEqual(api.calls[-2:], ['snapshots/fixture/manifest.json', 'README.md'])
            self.assertEqual(result['state'], 'complete')
            self.assertEqual(json.loads(api.files['snapshots/fixture/manifest.json']), manifest)
            self.assertNotIn('pack-state.local.json', '\n'.join(api.files))
            self.assertNotIn('upload-state.local.json', '\n'.join(api.files))
            calls = list(api.calls)
            with contextlib.redirect_stdout(io.StringIO()):
                upload(output, 'fixture-owner/world', api=api)
            self.assertEqual(api.calls, calls)

    def test_wrong_owner_public_visibility_and_wrong_remote_hash_stop_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); world = fixture(root); output = root/'package'
            pack(world, output)
            api = FakeHub()
            with self.assertRaisesRegex(ValueError, 'belong'):
                upload(output, 'someone-else/world', api=api)
            api.private = False
            with self.assertRaisesRegex(ValueError, 'private'):
                upload(output, 'fixture-owner/world', api=api)
            self.assertFalse(api.calls)
            api.private = True; api.bad_checksum = True
            with self.assertRaisesRegex(ValueError, 'checksum'):
                upload(output, 'fixture-owner/world', api=api)
            self.assertEqual(len(api.calls), 1)
            self.assertFalse(any(p.endswith('manifest.json') for p in api.files))


if __name__ == '__main__':
    unittest.main()
