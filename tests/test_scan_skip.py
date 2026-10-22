"""Tiles with no mapped building skip the scan upgrade; tile phases are timed."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chicago_tiles import Journal  # noqa: E402
import regional_generate as rg  # noqa: E402

PLAN = {'tiles': [{'id': f'{x}_0', 'priority': float(x), 'west': x * 512, 'north': 512, 'size': 512}
                  for x in range(4)]}


def footprint_index(path, boxes):
    db = sqlite3.connect(path)
    db.execute('CREATE VIRTUAL TABLE bounds USING rtree(rowid,minx,maxx,miny,maxy)')
    db.executemany('INSERT INTO bounds VALUES (?,?,?,?,?)', [(i, *b) for i, b in enumerate(boxes, 1)])
    db.commit()
    db.close()


class ScanSkipTests(unittest.TestCase):
    def test_empty_tiles_are_the_ones_no_footprint_comes_near(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'footprints.sqlite'
            # (minx, maxx, miny, maxy): one house inside tile 0; one 10 m past tile 2's east edge (in tile 3's pad).
            footprint_index(path, [(100, 120, 200, 220), (1546, 1560, 100, 110)])
            self.assertEqual(rg.empty_tiles(PLAN['tiles'], path), ['1_0'])
            self.assertEqual(rg.empty_tiles(PLAN['tiles'], path, pad=0), ['1_0', '2_0'])

    def test_only_the_scan_stage_is_retired(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'jobs.sqlite', PLAN)
            try:
                self.assertEqual(journal.retire_tiles(['1_0'], 'sources/indexes/regional-footprints.sqlite#no-buildings',
                                                      'ab' * 32, None, stages=(2,)), 1)
                states = [row[0] for row in journal.db.execute('SELECT state FROM jobs WHERE tile=? ORDER BY stage', ('1_0',))]
                self.assertEqual(states, ['pending', 'pending', 'complete', 'pending'])
                job = journal.claim_tile('w', 'base', tiles=['1_0'])  # LQ still runs; no scan rides along
                self.assertEqual(job['stages'], ['sources', 'geometry'])
            finally:
                journal.close()

    def test_phases_charge_each_lap(self):
        with mock.patch.object(rg.time, 'monotonic', side_effect=[0.0, 2.0, 2.5, 9.0]):
            phase = rg.Phases()
            phase.lap('sources')
            phase.lap('lq_build')
            phase.lap('sources')
        self.assertEqual(phase.seconds, {'sources': 8.5, 'lq_build': 0.5})


if __name__ == '__main__':
    unittest.main()
