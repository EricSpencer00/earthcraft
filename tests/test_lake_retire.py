"""Open-water plan tiles published as lake/v1 leave the generation queue."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chicago_tiles import Journal  # noqa: E402

PLAN = {'tiles': [{'id': f'{x}_0', 'priority': float(x)} for x in range(4)]}


def states(journal, tile):
    return [row[0] for row in journal.db.execute('SELECT state FROM jobs WHERE tile=? ORDER BY stage', (tile,))]


class RetireTests(unittest.TestCase):
    def test_retired_tiles_are_never_leased(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'jobs.sqlite', PLAN)
            try:
                self.assertEqual(journal.retire_tiles(['1_0', '3_0', '9_9'], 'lake/v1/index.json', 'ab' * 32, 'c' * 40), 2)
                self.assertEqual(states(journal, '1_0'), ['complete', 'complete', 'complete', 'pending'])
                row = journal.db.execute('SELECT evidence,evidence_commit FROM jobs WHERE tile=? AND stage=0', ('1_0',)).fetchone()
                self.assertEqual(tuple(row), ('lake/v1/index.json', 'c' * 40))
                claimed = []
                while (job := journal.claim_tile('w', 'lq')) is not None:
                    claimed.append(job['tile'])
                self.assertEqual(claimed, ['0_0', '2_0'])
                self.assertIsNone(journal.claim_tile('w', 'scans'))  # LQ leases still running
                # A second pass changes nothing.
                self.assertEqual(journal.retire_tiles(['1_0', '3_0'], 'lake/v1/index.json', 'ab' * 32, 'c' * 40), 0)
            finally:
                journal.close()

    def test_built_water_tile_skips_its_scan_pass_but_live_leases_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'jobs.sqlite', PLAN)
            try:
                journal.db.execute("UPDATE jobs SET state='complete',evidence='tiles/v2/0_0/x' WHERE tile='0_0' AND stage<2")
                live = journal.claim_tile('w', 'lq')  # leases 1_0
                self.assertEqual(live['tile'], '1_0')
                self.assertEqual(journal.retire_tiles(['0_0', '1_0'], 'lake/v1/index.json', 'ab' * 32, 'c' * 40), 2)
                self.assertEqual(states(journal, '0_0'), ['complete', 'complete', 'complete', 'pending'])
                self.assertEqual(states(journal, '1_0'), ['running', 'running', 'complete', 'pending'])
                self.assertEqual(journal.db.execute(
                    "SELECT evidence FROM jobs WHERE tile='0_0' AND stage=0").fetchone()[0], 'tiles/v2/0_0/x')
            finally:
                journal.close()


if __name__ == '__main__':
    unittest.main()
