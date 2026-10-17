from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chicago_tiles import Journal, parse_shard, tile_shard
from regional_supervisor import pending_work


def plan(rows=12, cols=3):
    return {'tiles': [{'id': f'{x}_{z}', 'priority': float(z * cols + x)} for z in range(-rows // 2, rows // 2)
                      for x in range(cols)]}


class ShardingTests(unittest.TestCase):
    def test_parse_and_band_assignment(self):
        self.assertIsNone(parse_shard(None))
        self.assertIsNone(parse_shard('0/1'))
        self.assertEqual(parse_shard('1/3'), (1, 3))
        for bad in ('3/3', '-1/2', 'x', '1'):
            with self.assertRaises(ValueError):
                parse_shard(bad)
        self.assertEqual({tile_shard(f'{x}_{z}', 2) for x in range(-5, 5) for z in range(0, 4)}, {0})
        self.assertEqual(tile_shard('7_4', 2), 1)
        self.assertEqual(tile_shard('-7_-1', 2), 1)  # band -1 -> shard 1 (Python modulo)

    def test_shards_partition_the_plan_without_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            frozen = plan()
            path = Path(directory) / 'jobs.sqlite'
            Journal(path, frozen).close()
            claimed = {}
            for index in range(3):
                journal = Journal(path, frozen)
                try:
                    while (job := journal.claim_tile(f'box{index}', shard=(index, 3))) is not None:
                        claimed.setdefault(job['tile'], []).append(index)
                finally:
                    journal.close()
            self.assertEqual(sorted(claimed), sorted(t['id'] for t in frozen['tiles']))
            self.assertTrue(all(len(owners) == 1 for owners in claimed.values()))
            for tile, owners in claimed.items():
                self.assertEqual(owners[0], tile_shard(tile, 3))
            self.assertEqual(len({o[0] for o in claimed.values()}), 3)
            # claimed tiles are 'running', so every shard still reports work until its leases finish
            self.assertTrue(pending_work(Path(directory), 'base', (0, 3)))


if __name__ == '__main__':
    unittest.main()
