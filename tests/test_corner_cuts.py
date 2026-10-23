"""Quality lane, reopening scans retired as empty, and reusing a published LQ world."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chicago_tiles import Journal
import hf_store
from region_pack import pack
import regional_generate
from regional_fixtures import plan_for, region


def complete(journal, tile, stages, evidence='tiles/v2/x/receipts/s.json'):
    for stage in stages:
        journal.db.execute("UPDATE jobs SET state='complete',evidence=?,evidence_sha256=? WHERE tile=? AND stage=?",
                           (evidence, 'a' * 64, tile, stage))
    journal.db.commit()


class QualityLaneTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.folder.name) / 'jobs.sqlite', plan_for(['0_0', '1_0', '2_0', '3_0']))

    def tearDown(self):
        self.journal.close();self.folder.cleanup()

    def test_nearest_unfinished_tile_first_with_its_open_lq_stages(self):
        complete(self.journal, '1_0', (0, 1))                     # LQ done: scan only
        first = self.journal.claim_tile('w', 'quality', now=1)    # 0_0 has the best priority and no LQ yet
        self.assertEqual((first['tile'], first['stages']), ('0_0', ['sources', 'geometry', 'appearance']))
        second = self.journal.claim_tile('w', 'quality', now=1)
        self.assertEqual((second['tile'], second['stages']), ('1_0', ['appearance']))

    def test_failed_lq_or_live_lease_elsewhere_is_never_taken(self):
        self.journal.db.execute("UPDATE jobs SET state='failed' WHERE tile='0_0' AND stage=0")
        self.journal.db.commit()
        self.journal.claim_tile('other', 'lq', now=1, tiles=['1_0'])  # LQ of 1_0 is live elsewhere
        job = self.journal.claim_tile('w', 'quality', now=2)
        self.assertEqual(job['tile'], '2_0')

    def test_reopen_returns_only_scans_retired_as_empty(self):
        complete(self.journal, '0_0', (0, 1));complete(self.journal, '0_0', (2,), 'sources/indexes/f.sqlite#no-buildings')
        complete(self.journal, '1_0', (0, 1, 2))
        self.assertEqual(self.journal.reopen_stage(2, '#no-buildings'), 1)
        state = dict(self.journal.db.execute('SELECT tile,state FROM jobs WHERE stage=2').fetchall())
        self.assertEqual((state['0_0'], state['1_0']), ('pending', 'complete'))
        self.assertEqual(self.journal.reopen_stage(2, '#no-buildings'), 0)

    def test_reopened_stage_drops_an_old_route_rank(self):
        self.journal.prioritize_tiles(['3_0'], 'locality-v1', stages=('sources', 'geometry', 'appearance'))
        complete(self.journal, '3_0', (0, 1));complete(self.journal, '3_0', (2,), 'f.sqlite#no-buildings')
        complete(self.journal, '0_0', (0, 1))
        self.journal.reopen_stage(2, '#no-buildings')
        job = self.journal.claim_tile('w', 'quality', now=1)
        self.assertEqual(job['tile'], '0_0')  # plan priority, not the stale locality rank of 3_0


class FakeReader:
    def __init__(self, files):
        self.files = files

    def info(self, paths, revision=None):
        return {path: hf_store.hash_bytes(self.files[path]) for path in paths if path in self.files}

    def read(self, path, *, limit=None, revision=None, expected=None):
        return self.files[path]


class PublishedLqTests(unittest.TestCase):
    def published(self, quality='LQ', receipt=b'{"stage":"geometry","checks":{"ok":true}}'):
        mca = region(3, 4)
        base = 'tiles/v2/3_4'
        manifest = {'quality': quality, 'stages': {'geometry': {'receipt': f'{base}/receipts/geometry.json',
                                                                  'sha256': hf_store.hash_bytes(receipt).sha256}},
                    'files': [{'role': 'region', 'path': f'{base}/region/r.3.4.mcp',
                               'decoded': {'sha256': hashlib.sha256(mca).hexdigest()}},
                              {'role': 'world-manifest', 'path': f'{base}/earthcraft.json'}]}
        return mca, {f'{base}/tile.json': json.dumps(manifest).encode(), f'{base}/region/r.3.4.mcp': pack(mca),
                     f'{base}/earthcraft.json': b'{"source":{}}', f'{base}/receipts/geometry.json': receipt}

    def test_published_lq_is_unpacked_with_its_receipt(self):
        mca, files = self.published()
        with tempfile.TemporaryDirectory() as folder:
            world = Path(folder) / 'world'
            receipt = regional_generate.published_lq(FakeReader(files), {'id': '3_4'}, world)
            self.assertEqual((world / 'region' / 'r.3.4.mca').read_bytes(), mca)
            self.assertEqual(receipt['checks'], {'ok': True})
            self.assertEqual(receipt['world'], str(world.resolve()))
            self.assertEqual(receipt['reused_from'], 'tiles/v2/3_4/tile.json')

    def test_scanned_tampered_or_missing_tiles_are_built_instead(self):
        _, scanned = self.published(quality='scan-roof')
        _, tampered = self.published();tampered['tiles/v2/3_4/receipts/geometry.json'] = b'{"stage":"other"}'
        with tempfile.TemporaryDirectory() as folder:
            for files in (scanned, tampered, {}):
                self.assertIsNone(regional_generate.published_lq(FakeReader(files), {'id': '3_4'}, Path(folder) / 'w'))


if __name__ == '__main__':
    unittest.main()
