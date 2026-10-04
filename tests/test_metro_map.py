import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import metro_map as m


class MetroMapTests(unittest.TestCase):
    def fixture(self, root):
        save = root / 'runtime/traversal/saves/Earthcraft'
        save.mkdir(parents=True)
        binding = root / 'runtime/traversal/config/earthcraft-live.json'
        binding.parent.mkdir(parents=True)
        frame = {'crs': 'EPSG:3857', 'west': -32, 'north': 33}
        binding.write_text(json.dumps({'coordinate_frame': frame}))
        cache = root / 'map'
        cache.mkdir()
        plan = {'frame': frame, 'scope': 'Frozen metro', 'city_area_m2': 512**2 * 2,
                'tiles': [{'id': '-1_-1', 'west': -544, 'north': 545, 'size': 512,
                           'world_offset_xz': [-512, -512]},
                          {'id': '0_0', 'west': -32, 'north': 33, 'size': 512,
                           'world_offset_xz': [0, 0]}]}
        (cache / 'plan.json').write_text(json.dumps(plan))
        polygon = {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
                   'properties': {'private_path': str(root)}, 'geometry': {
                       'type': 'Polygon', 'coordinates': [[[-1, -1], [1, -1], [1, 1], [-1, -1]]]}}]}
        for name in ('metro.geojson', 'kenosha.geojson'):
            (cache / name).write_text(json.dumps(polygon))
        tiles = {str((x, z)): {'size_m': 256, 'world_offset_xz': [x, z], 'status': m.STATUS}
                 for x in (-512, -256) for z in (-512, -256)}
        tiles['overlap'] = {'size_m': 512, 'world_offset_xz': [-512, -512], 'status': m.STATUS}
        tiles['unaccepted'] = {'size_m': 512, 'world_offset_xz': [0, 0], 'status': 'planned'}
        (save / 'city-coverage.json').write_text(json.dumps({'frame': frame, 'tiles': tiles}))
        (save / 'regional-quality.json').write_text(json.dumps({'frame': frame, 'tiles': {
            '512:-1_-1': {'quality': 'scan-points-and-roof', 'upgraded_chunks': 1,
                         'existing_or_edited_chunks_preserved': 1023},
            '512:0_0': {'quality': 'LQ', 'upgraded_chunks': 1024}}}))
        (root / 'publisher-status.json').write_text(json.dumps({'state': 'storage_boundary', 'delivered_tiles': 1}))
        self.queue(cache, {'-1_-1': ['complete', 'complete', 'complete', 'pending'],
                           '0_0': ['complete', 'running', 'pending', 'pending']})
        return cache, save

    def queue(self, cache, jobs, observed=None):
        counts = [{state: sum(states[i] == state for states in jobs.values())
                   for state in m.STATES if any(states[i] == state for states in jobs.values())}
                  for i in range(4)]
        record = {'schema_version': 1, 'plan_sha256': hashlib.sha256((cache / 'plan.json').read_bytes()).hexdigest(),
                  'jobs': jobs, 'counts': counts, 'observed_unix': observed or time.time(),
                  'active_workers': 2, 'generation_state': 'running'}
        (cache / 'queue.json').write_text(json.dumps(record))

    def test_frame_union_partial_scans_and_appearance_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, save = self.fixture(root)
            before = {p: p.read_bytes() for p in save.iterdir()}
            provider = m.MetroMap(root, cache)
            result = provider.snapshot()
            self.assertEqual(result['metro']['installed_tiles'], 1)
            self.assertEqual(result['metro']['installed_scan_tiles'], 1)
            self.assertEqual(result['metro']['delivery_state'], 'storage_boundary')
            self.assertEqual(result['metro']['planned_tiles'], 2)
            self.assertEqual(result['rollup']['generated_tiles'], 1)
            left, right = result['cells']
            self.assertTrue(left['installed_in_save'])
            self.assertTrue(left['scan_upgrade_recorded_in_parent_tile'])
            self.assertFalse(left['photo_color_applied'])
            self.assertEqual(left['source_state'], 'complete')
            self.assertEqual(left['game_verify_state'], 'pending')
            self.assertFalse(right['installed_in_save'])
            self.assertFalse(right['scan_upgrade_recorded_in_parent_tile'])
            self.assertEqual(right['state'], 'running')
            # Negative X and south-positive Z use the inherited offset, without recentering.
            self.assertAlmostEqual(left['corners_lonlat'][0][0], -544 / 111319.49079327358)
            self.assertGreater(left['corners_lonlat'][0][1], left['corners_lonlat'][2][1])
            world = provider.snapshot('world')['generated_world']
            self.assertEqual(world['unique_cells'], 4)
            self.assertEqual(world['photo_colored_cells'], 0)
            self.assertFalse(world['current_block_fill_verified'])
            self.assertEqual(before, {p: p.read_bytes() for p in save.iterdir()})
            self.assertNotIn(str(root), json.dumps(result))
            (root / 'save-observation.json').write_text(json.dumps({
                'manifest_sha256': hashlib.sha256((save / 'city-coverage.json').read_bytes()).hexdigest(),
                'quality_sha256': hashlib.sha256((save / 'regional-quality.json').read_bytes()).hexdigest(),
                'observed_utc': '2026-10-03T04:00:00+00:00'}))
            self.assertEqual(provider.snapshot()['metro']['save_record_utc'], '2026-10-03T04:00:00+00:00')
            (save / 'regional-quality.json').write_text((save / 'regional-quality.json').read_text()+' ')
            with self.assertRaisesRegex(ValueError, 'observation'):
                provider.snapshot()

    def test_one_legacy_cell_does_not_claim_a_whole_512m_tile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, save = self.fixture(root)
            manifest = m.read(save / 'city-coverage.json')
            manifest['tiles'] = {'partial': manifest['tiles'][str((-512, -512))]}
            (save / 'city-coverage.json').write_text(json.dumps(manifest))
            result = m.MetroMap(root, cache).snapshot()
            self.assertEqual(result['metro']['installed_tiles'], 0)
            self.assertFalse(result['cells'][0]['installed_in_save'])

    def test_missing_queue_does_not_claim_generation_or_current_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, _ = self.fixture(root)
            (cache / 'queue.json').unlink()
            result = m.MetroMap(root, cache).snapshot()
            self.assertFalse(result['metro']['queue_available'])
            self.assertTrue(result['metro']['queue_stale'])
            self.assertIsNone(result['rollup']['generated_tiles'])
            self.assertEqual(result['cells'][0]['geometry_state'], 'unknown')
            self.assertEqual(result['metro']['installed_tiles'], 1)

    def test_cache_ages_and_invalidates_without_inventing_fresh_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, _ = self.fixture(root)
            observed = time.time()
            self.queue(cache, {'-1_-1': ['complete', 'complete', 'pending', 'pending']}, observed)
            provider = m.MetroMap(root, cache)
            with patch.object(m.time, 'time', return_value=observed + 1):
                first = provider.snapshot()
            with patch.object(m.time, 'time', return_value=observed + 182):
                stale = provider.snapshot()
            self.assertEqual(first['updated_utc'], stale['updated_utc'])
            self.assertTrue(stale['metro']['queue_stale'])
            self.queue(cache, {'0_0': ['complete', 'complete', 'pending', 'pending']}, observed + 200)
            with patch.object(m.time, 'time', return_value=observed + 201):
                self.assertNotEqual(provider.snapshot()['updated_utc'], first['updated_utc'])

    def test_plan_and_receipt_frames_must_agree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, save = self.fixture(root)
            provider = m.MetroMap(root, cache)
            quality = m.read(save / 'regional-quality.json')
            quality['frame']['west'] = 0
            (save / 'regional-quality.json').write_text(json.dumps(quality))
            with self.assertRaisesRegex(ValueError, 'frame'):
                provider.snapshot()
            plan = m.read(cache / 'plan.json')
            plan['frame']['north'] = 0
            (cache / 'plan.json').write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, 'frame'):
                m.MetroMap(root, cache)

    def test_queue_cannot_silently_use_another_plan_or_inconsistent_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, _ = self.fixture(root)
            provider = m.MetroMap(root, cache)
            queue = m.read(cache / 'queue.json')
            queue['plan_sha256'] = 'other-plan'
            (cache / 'queue.json').write_text(json.dumps(queue))
            with self.assertRaisesRegex(ValueError, 'frozen metro plan'):
                provider.snapshot()
            queue['counts'][0]['complete'] = 95_399
            (cache / 'queue.json').write_text(json.dumps(queue))
            with self.assertRaisesRegex(ValueError, 'counts disagree'):
                provider.snapshot()

    def test_sanitized_sync_is_read_only_and_expired_leases_are_not_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, _ = self.fixture(root)
            with sqlite3.connect(cache / 'jobs.sqlite') as db:
                db.execute('CREATE TABLE jobs(tile TEXT, stage INTEGER, state TEXT, expires REAL, owner TEXT)')
                db.executemany('INSERT INTO jobs VALUES(?,?,?,?,?)', [
                    ('-1_-1', 0, 'complete', None, None),
                    ('-1_-1', 1, 'running', 1, 'expired-worker'),
                    ('0_0', 0, 'running', time.time() + 3600, 'private-worker-name')])
            (cache / 'supervisor-status.json').write_text(json.dumps({'state': 'running'}))
            config = root / 'config.json'
            config.write_text(json.dumps({'host': 'mac-mini', 'hostname': 'erics-mac-mini.local',
                'user': 'ericspencer', 'python': '/existing/python', 'control': str(cache)}))
            database_before = (cache / 'jobs.sqlite').read_bytes()
            real_run = subprocess.run
            commands = []
            def fake_run(command, **kwargs):
                commands.append(command)
                if '-G' in command:
                    return subprocess.CompletedProcess(command, 0,
                        stdout='hostname erics-mac-mini.local\nuser ericspencer\nidentityfile ~/.ssh/id_ed25519_mac_mini\n')
                return real_run([sys.executable, '-'], input=kwargs['input'], capture_output=True, check=True)
            with patch.object(m.subprocess, 'run', side_effect=fake_run):
                m.sync_queue(config, cache / 'queue.json')
            record = m.read(cache / 'queue.json')
            self.assertEqual(record['jobs']['-1_-1'][1], 'pending')
            self.assertEqual(record['active_workers'], 1)
            self.assertNotIn('private-worker-name', json.dumps(record))
            self.assertEqual(database_before, (cache / 'jobs.sqlite').read_bytes())
            m.sync_control(cache, cache / 'queue.json')
            self.assertEqual(m.read(cache / 'queue.json')['active_workers'], 1)
            self.assertEqual(database_before, (cache / 'jobs.sqlite').read_bytes())
            self.assertEqual(commands[0], ['/usr/bin/ssh', '-G', 'mac-mini'])
            self.assertFalse((cache / 'queue.partial').exists())

    def test_changed_ssh_route_preserves_last_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache, _ = self.fixture(root)
            config = root / 'config.json'
            config.write_text(json.dumps({'host': 'mac-mini', 'hostname': 'erics-mac-mini.local', 'user': 'ericspencer'}))
            before = (cache / 'queue.json').read_bytes()
            wrong_route = subprocess.CompletedProcess([], 0, stdout='hostname unrelated\nuser ericspencer\n')
            with patch.object(m.subprocess, 'run', return_value=wrong_route) as run:
                with self.assertRaisesRegex(ValueError, 'authorized mini route'):
                    m.sync_queue(config, cache / 'queue.json')
            self.assertEqual(run.call_count, 1)
            self.assertEqual(before, (cache / 'queue.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
