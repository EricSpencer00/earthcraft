"""tiles/v2 packaging and the one-pass tile lifecycle against a fake Hugging Face Hub."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from regional_fixtures import (FRAME, FakeHub, REPO, fake_tile_pass, make_ctx, plan_for, reader_for, region,  # noqa: E402
                               write_templates)
import hf_store as hs  # noqa: E402
import regional_cache  # noqa: E402
import regional_generate as rg  # noqa: E402
import regional_tiles as rt  # noqa: E402
from chicago_tiles import Journal  # noqa: E402
from hf_store import DiskGuardError  # noqa: E402

LOCAL = ('/Users/', '/private/', '/var/', '/tmp/', '/Volumes/')


def strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


class PackagingTests(unittest.TestCase):
    def test_receipts_name_dataset_paths_and_published_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'gen' / '3_-4'
            cache = Path(directory) / 'cache'
            receipts, delivered, lq = fake_tile_pass('3_-4', root, cache)
            package = rt.package_tile({'id': '3_-4', 'size': 512}, root, receipts=receipts, delivered=delivered, lq=lq,
                                      plan_sha256='p' * 64, frame_sha256='f' * 64, generator={'revision': 'abc'},
                                      prefixes={cache: 'cache:'})
            paths = {item['path'] for item in package.items}
            base = 'tiles/v2/3_-4'
            self.assertEqual(paths, {f'{base}/tile.json', f'{base}/region/r.3.-4.mca', f'{base}/earthcraft.json',
                                     f'{base}/lq/region/r.3.-4.mca', f'{base}/lq/earthcraft.json',
                                     f'{base}/receipts/sources.json', f'{base}/receipts/geometry.json',
                                     f'{base}/receipts/appearance.json'})
            appearance = json.loads((package.directory / 'receipts/appearance.json').read_text())
            geometry = json.loads((package.directory / 'receipts/geometry.json').read_text())
            sources = json.loads((package.directory / 'receipts/sources.json').read_text())
            self.assertEqual(appearance['world'], base)
            self.assertEqual(geometry['world'], base + '/lq')
            self.assertEqual(sources['inputs'], 'gen:3_-4/sources')
            self.assertEqual(appearance['scan_source']['dtm']['cache'], 'cache:arcgrid/Lake/dtm')
            published = (package.directory / 'earthcraft.json').read_bytes()
            self.assertEqual(appearance['world_manifest_sha256'], hashlib.sha256(published).hexdigest())
            self.assertNotEqual(appearance['generated_world_manifest_sha256'], appearance['world_manifest_sha256'])
            world = json.loads(published)
            self.assertEqual(world['source']['elevation_assets'][0]['cache_path'], 'cache:terrain/15-1-2.png')
            self.assertEqual(world['dem_path'], 'gen:3_-4/sources/elevation.tif')
            manifest = json.loads((package.directory / 'tile.json').read_text())
            self.assertEqual(manifest['quality'], 'scan-roof')
            self.assertEqual(manifest['delivered_receipt'], 'appearance')
            for path in package.directory.rglob('*.json'):
                for text in strings(json.loads(path.read_text())):
                    self.assertFalse(any(marker in text for marker in LOCAL), (path.name, text))
            region_file = package.directory / 'region/r.3.-4.mca'
            self.assertEqual(hashlib.sha256(region_file.read_bytes()).hexdigest(), appearance['regions']['r.3.-4.mca'])

    def test_gate_refuses_unmapped_local_paths_and_foreign_regions(self):
        with self.assertRaisesRegex(ValueError, 'Local path left'):
            rt.assert_portable({'note': 'read from /Volumes/External/Earthcraft/x'}, 'receipt')
        rt.assert_portable({'url': 'https://example.invalid/var/tmp/x', 'wkt': 'PROJCRS["x"]'}, 'receipt')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / '0_0'
            receipts, delivered, _ = fake_tile_pass('0_0', root, Path(directory) / 'cache', scan=False)
            receipts['geometry']['note'] = '/Users/someone/elsewhere/file'
            with self.assertRaisesRegex(ValueError, 'Local path left'):
                rt.package_tile({'id': '0_0'}, root, receipts=receipts, delivered=delivered, plan_sha256='p',
                                frame_sha256='f', generator={})
            with self.assertRaisesRegex(ValueError, 'coordinates differ'):
                rt.audit_region(region(1, 0), 0, 0)


class LifecycleTests(unittest.TestCase):
    """claim -> one pass -> package -> verified commit -> journal with dataset evidence -> scratch deleted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.control = self.root / 'control'
        self.control.mkdir()
        self.plan = plan_for(['0_0', '1_0'])
        (self.control / 'plan.json').write_text(json.dumps(self.plan))
        self.frame = self.root / 'frame.json'
        self.frame.write_text(json.dumps(FRAME))
        self.hub = FakeHub()
        self.hub.add_repo(REPO, {'README.md': b'card'})
        self.ctx = make_ctx(self.root / 'hf', self.hub)
        self.store = hs.StoreClient(ctx=self.ctx)
        write_templates(self.control / 'templates')

    def tearDown(self):
        regional_cache.activate(None)
        os.environ.pop('EARTHCRAFT_WORLD_TEMPLATES', None)
        self.tmp.cleanup()

    def args(self, *extra):
        return rg.build_parser().parse_args(['--control', str(self.control), '--frame', str(self.frame), '--scratch',
                                             str(self.root / 'scratch'), '--min-free', '0', '--source-revision',
                                             'test-rev', '--batch-tiles', '2', *extra])

    def fake_generate(self, tile, job, root, ctx):
        regional_cache.admit(ctx['cache_root'] / 'terrain' / '15-1-2.png', 10)
        (ctx['cache_root'] / 'terrain').mkdir(parents=True, exist_ok=True)
        (ctx['cache_root'] / 'terrain' / '15-1-2.png').write_bytes(b'png')
        receipts, delivered, lq = fake_tile_pass(tile['id'], root, ctx['cache_root'])
        return receipts, {}, delivered, lq

    def test_one_pass_publishes_verifies_records_dataset_evidence_and_cleans_scratch(self):
        with patch('regional_generate.generate_tile', self.fake_generate):
            code = rg.Worker(self.args(), reader=reader_for(self.hub), store=self.store).run()
        self.assertEqual(code, 0)
        files = self.hub.repos[REPO]['files']
        for tile in ('0_0', '1_0'):
            rx = tile.split('_')[0]
            self.assertIn(f'tiles/v2/{tile}/region/r.{rx}.0.mca', files)
            self.assertIn(f'tiles/v2/{tile}/lq/region/r.{rx}.0.mca', files)
            self.assertIn(f'tiles/v2/{tile}/tile.json', files)
        self.assertEqual(len(self.hub.commits), 1, 'two tiles share one commit')
        with sqlite3.connect(self.control / 'jobs.sqlite') as db:
            rows = db.execute('SELECT tile,stage,state,evidence,evidence_sha256,evidence_commit FROM jobs '
                              'WHERE stage<3 ORDER BY tile,stage').fetchall()
        for tile, stage, state, evidence, digest, commit in rows:
            name = ('sources', 'geometry', 'appearance')[stage]
            self.assertEqual(state, 'complete')
            self.assertEqual(evidence, f'tiles/v2/{tile}/receipts/{name}.json')
            self.assertEqual(digest, hashlib.sha256(files[evidence]).hexdigest())
            self.assertEqual(commit, self.hub.commits[0]['oid'])
        gen = self.root / 'scratch' / 'gen'
        self.assertEqual(sorted(p.name for p in gen.iterdir()), [], 'per-tile and hf scratch are deleted')
        for path, data in files.items():
            if path.endswith('.json'):
                text = data.decode()
                self.assertFalse(any(marker in text for marker in LOCAL), path)
        manifest = json.loads(files['tiles/v2/0_0/tile.json'])
        self.assertEqual(manifest['generator']['revision'], 'test-rev')
        self.assertEqual(manifest['plan_sha256'], rg.digest(self.plan))

    def test_a_tile_already_on_the_hub_is_adopted_not_regenerated(self):
        with patch('regional_generate.generate_tile', self.fake_generate):
            rg.Worker(self.args('--tiles', '0_0'), reader=reader_for(self.hub), store=self.store).run()
        journal = Journal(self.control / 'jobs.sqlite', self.plan)
        journal.db.execute("UPDATE jobs SET state='pending',evidence=NULL,evidence_sha256=NULL WHERE tile='0_0'")
        journal.close()
        commits = len(self.hub.commits)
        with patch('regional_generate.generate_tile', side_effect=AssertionError('regenerated')):
            rg.Worker(self.args('--tiles', '0_0'), reader=reader_for(self.hub), store=self.store).run()
        self.assertEqual(len(self.hub.commits), commits)
        with sqlite3.connect(self.control / 'jobs.sqlite') as db:
            states = db.execute("SELECT state,evidence_commit FROM jobs WHERE tile='0_0' AND stage<3").fetchall()
        self.assertTrue(all(state == 'complete' and commit.startswith('adopted@') for state, commit in states))

    def test_disk_guard_pauses_a_tile_instead_of_failing_it(self):
        def floor(*_args):
            raise DiskGuardError('Free-space floor refuses cache x: 1 B free')
        with patch('regional_generate.generate_tile', floor):
            code = rg.Worker(self.args('--no-wait', '--tiles', '0_0'), reader=reader_for(self.hub),
                             store=self.store).run()
        self.assertEqual(code, 0)
        with sqlite3.connect(self.control / 'jobs.sqlite') as db:
            rows = db.execute("SELECT state,not_before,defer_reason FROM jobs WHERE tile='0_0' AND stage<3").fetchall()
        self.assertTrue(all(state == 'pending' and not_before > 0 and 'floor' in reason for state, not_before, reason in rows))
        self.assertFalse((self.control / 'failures').exists())
        self.assertEqual(self.hub.commits, [])

    def test_a_tile_that_outgrows_the_cache_fails_explicitly_and_the_queue_moves_on(self):
        calls = []

        def generate(tile, job, root, ctx):
            calls.append(tile['id'])
            if tile['id'] == '0_0':
                raise regional_cache.CacheCapacityError('Cache cap refuses: pinned + x > cap')
            return self.fake_generate(tile, job, root, ctx)
        with patch('regional_generate.generate_tile', generate):
            rg.Worker(self.args(), reader=reader_for(self.hub), store=self.store).run()
        self.assertEqual(calls, ['0_0', '1_0'])
        with sqlite3.connect(self.control / 'jobs.sqlite') as db:
            states = dict(db.execute("SELECT stage,state FROM jobs WHERE tile='0_0'").fetchall())
        self.assertEqual((states[0], states[1], states[2]), ('failed', 'pending', 'pending'))
        receipt = json.loads(next((self.control / 'failures').iterdir()).read_text())
        self.assertEqual(receipt['error_type'], 'CacheCapacityError')
        self.assertIn('tiles/v2/1_0/tile.json', self.hub.repos[REPO]['files'])


class JournalTileClaimTests(unittest.TestCase):
    def test_lanes_lease_whole_tiles_and_accept_lost_legacy_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = plan_for(['0_0', '1_0'])
            journal = Journal(root / 'jobs.sqlite', plan)
            journal.db.execute("UPDATE jobs SET state='complete',evidence=?,evidence_sha256='x' WHERE tile='1_0' "
                               "AND stage IN (0,1)", ('/Volumes/Gone/tiles/1_0/receipt.json',))
            base = journal.claim_tile('w', 'base', now=100)
            self.assertEqual((base['tile'], base['stages']), ('0_0', ['sources', 'geometry', 'appearance']))
            self.assertIsNone(journal.claim_tile('w', 'base', now=100))
            scans = journal.claim_tile('w', 'scans', now=100)
            self.assertEqual((scans['tile'], scans['stages']), ('1_0', ['appearance']))
            self.assertEqual(scans['prior'], {'sources': 'legacy', 'geometry': 'legacy'})
            raw = json.dumps({'tile': '1_0', 'stage': 'appearance', 'result': 'pass'}).encode()
            with self.assertRaisesRegex(ValueError, 'Dataset evidence'):
                journal.finish_remote(scans, 'appearance', str(root / 'local.json'), raw, 'c', now=101)
            journal.finish_remote(scans, 'appearance', 'tiles/v2/1_0/receipts/appearance.json', raw, 'c1', now=101)
            row = journal.db.execute("SELECT state,evidence,evidence_commit FROM jobs WHERE tile='1_0' AND stage=2").fetchone()
            self.assertEqual(tuple(row), ('complete', 'tiles/v2/1_0/receipts/appearance.json', 'c1'))
            with self.assertRaisesRegex(ValueError, 'Stale or expired'):
                journal.finish_remote(base, 'sources', 'tiles/v2/0_0/receipts/sources.json',
                                      json.dumps({'tile': '0_0', 'stage': 'sources', 'result': 'pass'}).encode(), 'c',
                                      now=100 + 7200)
            journal.close()


if __name__ == '__main__':
    unittest.main()
