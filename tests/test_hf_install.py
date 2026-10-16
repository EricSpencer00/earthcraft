"""hf_install: refuse an open world; install tiles/v2 packages with backups archived on the Hub."""
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import nbtlib as n

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from regional_fixtures import (FRAME, FakeHub, REPO, chunk_record, fake_tile_pass, make_ctx,  # noqa: E402
                               reader_for)
import hf_install  # noqa: E402
import hf_store as hs  # noqa: E402
from region_expansion import encode, records  # noqa: E402
import regional_tiles as rt  # noqa: E402

HOLD_LOCK = '''
import fcntl, sys, time
handle = open(sys.argv[1], 'r+b')
fcntl.lockf(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
print('locked', flush=True)
sys.stdin.read()
'''


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hub = FakeHub()
        self.hub.add_repo(REPO, {'README.md': b'card'})
        self.store = hs.StoreClient(ctx=make_ctx(self.root / 'hf', self.hub))
        self.save = self.root / 'save'
        (self.save / 'region').mkdir(parents=True)
        (self.save / 'session.lock').write_bytes(b'\xe2\x98\x83')
        (self.save / 'city-coverage.json').write_text(json.dumps({'frame': FRAME, 'tiles': {}}))
        n.File({'Data': n.Compound({'LevelName': n.String('Earthcraft'),
                                    'Player': n.Compound({'Pos': n.List[n.Double]([n.Double(1)] * 3)})})},
               gzipped=True).save(self.save / 'level.dat')
        self.scratch = self.root / 'install-scratch'

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, tile, scan=True):
        root = self.root / 'gen' / tile
        receipts, delivered, lq = fake_tile_pass(tile, root, self.root / 'cache', scan=scan)
        package = rt.package_tile({'id': tile, 'size': 512}, root, receipts=receipts, delivered=delivered, lq=lq,
                                  plan_sha256='p', frame_sha256='f', generator={'revision': 'r'},
                                  prefixes={self.root / 'cache': 'cache:'})
        self.store.push([{k: v for k, v in item.items() if k != 'role'} for item in package.items], 'publish')
        return package

    def install(self, *extra, reader=None):
        self.reader = reader or reader_for(self.hub)
        return hf_install.main(['--save', str(self.save), '--scratch', str(self.scratch), '--min-free', '0',
                                '--no-sync', '--no-native-compression', '--control', str(self.root / 'none'),
                                '--attempt', 'test-attempt', *extra], reader=self.reader, store=self.store)

    def test_open_world_is_refused_before_any_download_or_write(self):
        self.publish('0_0')
        before = {p: p.read_bytes() for p in self.save.rglob('*') if p.is_file()}
        holder = subprocess.Popen([sys.executable, '-c', HOLD_LOCK, str(self.save / 'session.lock')],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'locked')
            self.assertEqual(self.install('--tiles', '0_0'), hf_install.EXIT_WORLD_OPEN)
        finally:
            holder.stdin.close()
            holder.wait()
            holder.stdout.close()
        after = {p: p.read_bytes() for p in self.save.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(self.scratch.exists() and any(self.scratch.rglob('*')))
        self.assertEqual(self.reader.opener.requests, [], 'nothing was downloaded')
        self.assertFalse(any(p.startswith('archive/') for p in self.hub.repos[REPO]['files']))

    def test_new_tile_is_added_backups_are_archived_and_scratch_returns_to_zero(self):
        package = self.publish('0_0')
        self.assertEqual(self.install('--tiles', '0_0'), 0)
        installed = (self.save / 'region' / 'r.0.0.mca').read_bytes()
        self.assertEqual(records(installed), records((package.directory / 'region' / 'r.0.0.mca').read_bytes()))
        quality = json.loads((self.save / 'regional-quality.json').read_text())['tiles']['512:0_0']
        self.assertEqual((quality['quality'], quality['added_chunks']), ('scan-roof', 1024))
        self.assertEqual(quality['source_receipt_sha256'], package.receipts['appearance']['sha256'])
        files = self.hub.repos[REPO]['files']
        prefix = 'archive/install-backups/test-attempt/'
        archived = sorted(p for p in files if p.startswith(prefix))
        self.assertIn(prefix + '0_0/city-coverage.json.gz', archived)
        self.assertIn(prefix + 'installation.json', archived)
        self.assertFalse(any('level.dat' in p for p in archived), 'level.dat holds the player; never archived')
        report = json.loads(files[prefix + 'installation.json'])
        self.assertIn('BorderSize', report['level_dat_border_before']['0_0'])
        self.assertFalse(self.scratch.exists(), 'install scratch is empty and removed')
        self.assertEqual(self.install('--control', str(self.root / 'none')), 0)  # nothing selected twice

    def test_scan_upgrade_replaces_only_chunks_that_still_match_the_lq_baseline(self):
        package = self.publish('0_0')
        lq = records((package.directory / 'lq' / 'region' / 'r.0.0.mca').read_bytes())
        current = dict(lq)
        current[5] = chunk_record(5, 0, 'minecraft:gold_block')  # a player edit
        (self.save / 'region' / 'r.0.0.mca').write_bytes(encode(current))
        self.assertEqual(self.install('--tiles', '0_0'), 0)
        merged = records((self.save / 'region' / 'r.0.0.mca').read_bytes())
        scan = records((package.directory / 'region' / 'r.0.0.mca').read_bytes())
        self.assertEqual(merged[5], current[5])
        self.assertEqual(merged[0], scan[0])
        quality = json.loads((self.save / 'regional-quality.json').read_text())['tiles']['512:0_0']
        self.assertEqual((quality['upgraded_chunks'], quality['existing_or_edited_chunks_preserved']), (1023, 1))
        backup = self.hub.repos[REPO]['files']['archive/install-backups/test-attempt/0_0/region/r.0.0.mca.gz']
        self.assertEqual(records(gzip.decompress(backup)), current)

    def test_lq_package_never_overwrites_existing_chunks(self):
        self.publish('0_0', scan=False)
        existing = {0: chunk_record(0, 0, 'minecraft:gold_block')}
        (self.save / 'region' / 'r.0.0.mca').write_bytes(encode(existing))
        self.assertEqual(self.install('--tiles', '0_0'), 0)
        merged = records((self.save / 'region' / 'r.0.0.mca').read_bytes())
        self.assertEqual(merged[0], existing[0])
        self.assertEqual(len(merged), 1024)

    def test_dry_run_reads_nothing_from_the_save_lock(self):
        holder = subprocess.Popen([sys.executable, '-c', HOLD_LOCK, str(self.save / 'session.lock')],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'locked')
            self.assertEqual(self.install('--tiles', '0_0', '--dry-run'), 0)
        finally:
            holder.stdin.close()
            holder.wait()
            holder.stdout.close()
        self.assertFalse(self.scratch.exists())


if __name__ == '__main__':
    unittest.main()
