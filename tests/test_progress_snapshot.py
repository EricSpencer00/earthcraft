import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from progress_snapshot import build_snapshot


class ProgressSnapshotTests(unittest.TestCase):
    def test_global_snapshot_does_not_fabricate_denominator_or_private_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'runs/demo').mkdir(parents=True)
            with sqlite3.connect(root / 'runs/demo/jobs.sqlite') as db:
                db.execute('CREATE TABLE jobs (stage INTEGER,state TEXT)')
                db.executemany('INSERT INTO jobs VALUES (?,?)', [(0, 'complete'), (1, 'complete'), (0, 'pending')])
                db.commit()
            snapshot = build_snapshot(root, datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(snapshot['scope']['id'], 'earth')
            self.assertIsNone(snapshot['scope']['coverage_percent'])
            self.assertEqual(snapshot['rollup']['generated_tiles'], 1)
            self.assertEqual(snapshot['local']['source_tiles_total'], 2)
            self.assertFalse(snapshot['local']['private_paths_included'])
            self.assertNotIn('/Users/', json.dumps(snapshot))


if __name__ == '__main__':
    unittest.main()
