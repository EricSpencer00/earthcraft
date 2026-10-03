import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from world_coverage import read_world_coverage, STATUS
from progress_snapshot import build_snapshot, _valid_public_snapshot


class WorldCoverageTests(unittest.TestCase):
    def fixture(self, root):
        save = root / 'runtime/traversal/saves/Earthcraft'
        save.mkdir(parents=True)
        config = root / 'runtime/traversal/config/earthcraft-live.json'
        config.parent.mkdir(parents=True)
        frame = {'crs': 'EPSG:3857', 'west': -32, 'north': 33}
        config.write_text(json.dumps({'coordinate_frame': frame}))
        tiles = {
            'large': {'size_m': 512, 'world_offset_xz': [-512, -512], 'status': STATUS},
            'overlap': {'size_m': 256, 'world_offset_xz': [-256, -256], 'status': STATUS},
        }
        (save / 'city-coverage.json').write_text(json.dumps({'frame': frame, 'tiles': tiles}))
        (save / 'regional-quality.json').write_text(json.dumps({'frame': frame, 'tiles': {
            '512:-1_-1': {'quality': 'scan-points-and-roof', 'upgraded_chunks': 1,
                         'existing_or_edited_chunks_preserved': 1023},
        }}))
        return save

    def test_overlap_negative_coordinates_and_partial_scan_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            world = read_world_coverage(root)
            self.assertEqual(world['native_tiles'], 2)
            self.assertEqual(world['unique_cells'], 4)
            self.assertEqual(world['unique_area_km2'], 0.262144)
            self.assertEqual(world['scan_upgrade_parent_tiles'], 1)
            self.assertEqual(world['photo_colored_cells'], 0)
            self.assertTrue(all(c['scan_upgrade_recorded_in_parent_tile'] for c in world['cells']))
            self.assertTrue(all(c['appearance_state'] == 'pending' for c in world['cells']))
            self.assertFalse(world['current_block_fill_verified'])
            self.assertEqual(world['cells'][0]['tile_id'], '-2_-2')
            self.assertEqual(len(world['cells'][0]['corners_lonlat']), 4)
            self.assertNotIn(str(root), json.dumps(world))

    def test_frame_disagreement_refuses_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            config = root / 'runtime/traversal/config/earthcraft-live.json'
            frame = json.loads(config.read_text())
            frame['coordinate_frame']['west'] = 0
            config.write_text(json.dumps(frame))
            with self.assertRaisesRegex(ValueError, 'frames differ'):
                read_world_coverage(root)

    def test_upgrade_without_new_declaration_joins_existing_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save = self.fixture(root)
            manifest = json.loads((save / 'city-coverage.json').read_text())
            del manifest['tiles']['large']
            (save / 'city-coverage.json').write_text(json.dumps(manifest))
            world = read_world_coverage(root)
            self.assertEqual(world['unique_cells'], 1)
            self.assertTrue(world['cells'][0]['scan_upgrade_recorded_in_parent_tile'])

    def test_ci_preserves_export_and_rejects_tampered_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            snapshot = build_snapshot(root)
            self.assertTrue(_valid_public_snapshot(snapshot))
            progress = root / 'progress/earth.json'
            progress.parent.mkdir()
            progress.write_text(json.dumps(snapshot))
            (root / 'runtime').rename(root / 'absent-runtime')
            rebuilt = build_snapshot(root)
            self.assertEqual(rebuilt['generated_world'], snapshot['generated_world'])
            self.assertEqual(rebuilt['active_save_coverage'], snapshot['active_save_coverage'])
            rebuilt['generated_world']['cells'][0]['corners_lonlat'][0][0] = 999
            self.assertFalse(_valid_public_snapshot(rebuilt))


if __name__ == '__main__':
    unittest.main()
