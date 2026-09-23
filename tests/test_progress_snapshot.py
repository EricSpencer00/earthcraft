import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from progress_snapshot import build_snapshot
from global_projection import address_for


class ProgressSnapshotTests(unittest.TestCase):
    def test_active_save_manifest_is_separate_and_not_claimed_as_block_fill(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / 'runtime/traversal/saves/Earthcraft/city-coverage.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'tiles': {
                'one': {'size_m': 256, 'chunks': 256, 'world_offset_xz': [0, 0]},
                'two': {'size_m': 16, 'chunks': 1, 'world_offset_xz': [256, 0]},
            }}))
            binding = root / 'runtime/traversal/config/earthcraft-live.json'
            binding.parent.mkdir(parents=True)
            binding.write_text(json.dumps({'coordinate_frame': {
                'crs': 'EPSG:3857', 'west': 0, 'north': 0,
            }}))
            snapshot = build_snapshot(root, datetime(2026, 1, 1, tzinfo=timezone.utc))
            coverage = snapshot['active_save_coverage']
            self.assertEqual(coverage['state'], 'manifest_listed')
            self.assertEqual(coverage['manifest_tiles'], 2)
            self.assertEqual(coverage['manifest_chunk_declarations'], 257)
            self.assertEqual(sum(coverage['manifest_tile_center_page_counts'].values()), 2)
            self.assertEqual(coverage['atlas_page_address_schema'], 'earthcraft-atlas-v1')
            self.assertFalse(coverage['current_block_fill_verified'])
            self.assertIsNone(coverage['unlisted_chunks'])
            self.assertFalse(snapshot['claims']['active_save_fill_verified'])
            self.assertNotIn(str(root), json.dumps(snapshot))

    def test_missing_or_invalid_active_save_manifest_stays_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = build_snapshot(root, datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(snapshot['active_save_coverage']['state'], 'unavailable')
            manifest = root / 'runtime/traversal/saves/Earthcraft/city-coverage.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'tiles': {'bad': {'size_m': 256, 'chunks': 1}}}))
            snapshot = build_snapshot(root, datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(snapshot['active_save_coverage']['state'], 'invalid_manifest')

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
            self.assertEqual(snapshot['cells'][0]['atlas_page_center_id'],
                             address_for(snapshot['cells'][0]['longitude'],
                                         snapshot['cells'][0]['latitude']).id)
            self.assertEqual(snapshot['cells'][0]['atlas_page_center_basis'],
                             'exact_metric_tile_center')
            self.assertEqual(snapshot['cell_grid']['global_page_address_schema'],
                             'earthcraft-atlas-v1')
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
                'cell_grid': {},
                'rollup': {'generated_tiles': 12},
                'cells': [{'id': 'chicago/sample', 'longitude': -87.6, 'latitude': 41.9}],
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
            self.assertEqual(snapshot['cells'][0]['atlas_page_center_id'],
                             address_for(-87.6, 41.9).id)
            self.assertEqual(snapshot['cells'][0]['atlas_page_center_basis'],
                             'published_cell_center')


if __name__ == '__main__':
    unittest.main()
