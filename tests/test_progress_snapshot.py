import json
import copy
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from progress_snapshot import build_snapshot
from global_projection import MAX_PAGE_SCALE_ERROR_PPM, address_for


def public_cell_snapshot():
    return {
        'schema_version': 1,
        'updated_utc': '2026-01-01T00:00:00+00:00',
        'scope': {'id': 'earth', 'coverage_percent': None},
        'cell_grid': {
            'schema_version': 1,
            'addressing': 'region/tile_id',
            'cell_size_m': 256,
            'minecraft_chunk_size_m': 16,
            'chunks_per_cell': 256,
            'materialized_cells': 1,
            'materialized_cells_are_observed_or_queued': True,
            'unmeasured_cells_omitted': True,
        },
        'rollup': {'generated_tiles': 1, 'materialized_cells': 1},
        'cells': [{
            'id': 'chicago/0_0', 'region_id': 'chicago', 'tile_id': '0_0',
            'latitude': 41.88, 'longitude': -87.63, 'width_deg': 0.003,
            'height_deg': 0.002, 'size_m': 256, 'chunks_total': 256,
            'chunks_sourced': 256, 'chunks_generated': 256,
            'chunks_styled': 0, 'chunks_verified': 0,
            'source_state': 'complete', 'geometry_state': 'complete',
            'appearance_state': 'pending', 'game_verify_state': 'pending',
            'state': 'generated',
        }],
        'local': {'private_paths_included': False},
        'claims': {'private_data_in_snapshot': False},
    }


class ProgressSnapshotTests(unittest.TestCase):
    def test_city_destination_is_page_addressed_without_claiming_terrain_fill(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cities = root / 'configs/cities.json'
            cities.parent.mkdir(parents=True)
            cities.write_text(json.dumps({'cities': [{
                'id': 'elmhurst-il', 'name': 'Elmhurst, IL',
                'wgs84': [-87.9403418, 41.8994745], 'status': 'registered',
            }]}))
            snapshot = build_snapshot(root, datetime(2026, 1, 1, tzinfo=timezone.utc))
            [elmhurst] = snapshot['city_destinations']
            self.assertEqual(elmhurst['atlas_page_id'], 'ec1/b0893/c0466')
            self.assertEqual(elmhurst['atlas_page_schema'], 'earthcraft-atlas-v1')
            self.assertLessEqual(elmhurst['page_maximum_sampled_scale_error_ppm'],
                                 MAX_PAGE_SCALE_ERROR_PPM)
            self.assertIsNone(elmhurst['active_save_manifest_has_tile_center_in_page'])
            self.assertEqual(elmhurst['active_save_physical_fill'], 'unknown_not_scanned')

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
            self.assertEqual(snapshot['city_destinations'], [])
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
            seed = public_cell_snapshot()
            seed['rollup']['generated_tiles'] = 12
            seed['cells'][0].update(longitude=-87.6, latitude=41.9)
            seed['local'].update({
                    'state': 'idle',
                    'source_tiles_complete': 14,
                    'source_tiles_total': 20,
                    'geometry_tiles_complete': 12,
                    'stages': {},
                    'private_paths_included': False,
            })
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

    def test_ci_preserves_generated_atlas_snapshot_across_repeated_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / 'runs/demo'
            run.mkdir(parents=True)
            (run / 'plan.json').write_text(json.dumps({
                'frame': {'crs': 'EPSG:3857'},
                'tiles': [{'id': '0_0', 'west': 0, 'north': 256, 'size': 256}],
            }))
            journal = run / 'jobs.sqlite'
            with sqlite3.connect(journal) as db:
                db.execute('CREATE TABLE jobs (tile TEXT,stage INTEGER,state TEXT)')
                db.executemany('INSERT INTO jobs VALUES (?,?,?)', [
                    ('0_0', 0, 'complete'), ('0_0', 1, 'complete'),
                    ('0_0', 2, 'pending'), ('0_0', 3, 'pending'),
                ])
            original = build_snapshot(root)
            journal.unlink()
            (root / 'progress').mkdir()
            for _ in range(2):
                (root / 'progress/earth.json').write_text(json.dumps(original))
                restored = build_snapshot(root)
                self.assertEqual(restored['local']['state'], 'public_snapshot')
                self.assertEqual(restored['rollup'], original['rollup'])
                self.assertEqual(restored['cells'][0]['tile_id'], '0_0')
                self.assertEqual(restored['cells'][0]['atlas_page_center_id'],
                                 original['cells'][0]['atlas_page_center_id'])
                original = restored

    def test_ci_preserves_valid_cells_but_rejects_unsafe_or_malformed_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'progress').mkdir()
            seed = public_cell_snapshot()
            (root / 'progress/earth.json').write_text(json.dumps(seed))
            preserved = build_snapshot(root, datetime(2026, 1, 2, tzinfo=timezone.utc))
            self.assertEqual(preserved['cells'][0]['tile_id'], '0_0')
            self.assertEqual(preserved['rollup']['materialized_cells'], 1)

            invalid = [
                ('private field', lambda value: value.update(private_path='/Users/' + 'example/private')),
                ('bad cell id', lambda value: value['cells'][0].update(tile_id='not-a-cell')),
                ('bad stage state', lambda value: value['cells'][0].update(appearance_state='invented')),
                ('bad cell dimensions', lambda value: value['cells'][0].update(size_m=0)),
                ('oversized coordinate', lambda value: value['cells'][0].update(latitude=10**1000)),
                ('non-finite grid', lambda value: value['cell_grid'].update(cell_size_m=float('nan'))),
            ]
            for label, mutate in invalid:
                with self.subTest(label=label):
                    candidate = copy.deepcopy(seed)
                    mutate(candidate)
                    (root / 'progress/earth.json').write_text(json.dumps(candidate))
                    snapshot = build_snapshot(root, datetime(2026, 1, 3, tzinfo=timezone.utc))
                    self.assertEqual(snapshot['local']['state'], 'no_local_run')
                    self.assertEqual(snapshot['cells'], [])


if __name__ == '__main__':
    unittest.main()
