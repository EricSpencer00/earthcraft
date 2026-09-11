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
    def test_snapshot_exposes_stage_state_for_each_materialized_cell(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / 'runs/demo'
            run.mkdir(parents=True)
            (run / 'plan.json').write_text(json.dumps({
                'frame': {'crs': 'EPSG:3857'},
                'tiles': [{'id': '0_0', 'west': 0, 'north': 256, 'size': 256}],
            }))
            with sqlite3.connect(run / 'jobs.sqlite') as db:
                db.execute('CREATE TABLE jobs (tile TEXT,stage INTEGER,state TEXT)')
                db.executemany('INSERT INTO jobs VALUES (?,?,?)', [
                    ('0_0', 0, 'complete'), ('0_0', 1, 'complete'),
                    ('0_0', 2, 'pending'), ('0_0', 3, 'pending'),
                ])
                db.commit()
            snapshot = build_snapshot(root, datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(snapshot['cell_grid']['chunks_per_cell'], 256)
            self.assertEqual(snapshot['cell_grid']['materialized_cells'], 1)
            self.assertEqual(snapshot['cells'][0]['state'], 'generated')
            self.assertEqual(snapshot['cells'][0]['chunks_total'], 256)
            self.assertEqual(snapshot['cells'][0]['chunks_generated'], 256)
            self.assertNotIn('/Users/', json.dumps(snapshot))

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

    def test_ci_preserves_last_public_aggregate_without_local_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed = {
                'schema_version': 1,
                'updated_utc': '2026-01-01T00:00:00+00:00',
                'scope': {'id': 'earth', 'coverage_percent': None},
                'rollup': {'generated_tiles': 12},
                'local': {
                    'state': 'idle',
                    'source_tiles_complete': 14,
                    'source_tiles_total': 20,
                    'geometry_tiles_complete': 12,
                    'stages': {},
                    'private_paths_included': False,
                },
                'claims': {'private_data_in_snapshot': False},
            }
            (root / 'progress').mkdir()
            (root / 'progress/earth.json').write_text(json.dumps(seed))
            snapshot = build_snapshot(root, datetime(2026, 1, 2, tzinfo=timezone.utc))
            self.assertEqual(snapshot['rollup']['generated_tiles'], 12)
            self.assertEqual(snapshot['local']['source_tiles_complete'], 14)
            self.assertEqual(snapshot['local']['state'], 'public_snapshot')
            self.assertEqual(snapshot['updated_utc'], '2026-01-02T00:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
