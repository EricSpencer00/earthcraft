import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chicago_worker import build_or_resume_staging, check_storage_capacity, point_crop_required, promote_styled_shell, source_for_tile


class ChicagoWorkerTests(unittest.TestCase):
    def test_storage_reserves_follow_control_and_output_volumes(self):
        with patch('chicago_worker.shutil.disk_usage', side_effect=[
                SimpleNamespace(free=2 * 2**30), SimpleNamespace(free=102 * 2**30)]) as usage:
            check_storage_capacity(Path('/control/plan'), Path('/bulk/tiles'))
        self.assertEqual([call.args[0] for call in usage.call_args_list],
                         [Path('/control/plan'), Path('/bulk/tiles')])
        with patch('chicago_worker.shutil.disk_usage', side_effect=[
                SimpleNamespace(free=2 * 2**30), SimpleNamespace(free=100 * 2**30)]):
            with self.assertRaisesRegex(ValueError, 'Bulk output'):
                check_storage_capacity(Path('/control/plan'), Path('/bulk/tiles'))

    def test_lidar_is_skipped_for_unadmitted_osm_only_tile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'sources.json').write_text('{}')
            (root / 'cook-buildings-2022.json').write_text(json.dumps({'features': []}))
            (root / 'osm-ways.json').write_text(json.dumps([{'tags': {'building': 'yes'}}]))
            self.assertEqual(point_crop_required(root)[0], False)

    def test_explicit_osm_height_profile_requires_lidar(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'sources.json').write_text(json.dumps({'building_source_kind': 'osm-explicit'}))
            (root / 'cook-buildings-2022.json').write_text(json.dumps({'features': []}))
            (root / 'osm-ways.json').write_text(json.dumps([{'tags': {'building': 'yes', 'height': '12 m'}}]))
            self.assertEqual(point_crop_required(root)[0], True)

    def test_styled_shell_is_a_verified_sibling_of_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);observed=root/'world.observed';source=root/'sources';world=root/'world'
            observed.mkdir();source.mkdir();(observed/'raw.txt').write_text('unchanged')
            def compile_fn(raw,inputs,staging):
                self.assertEqual(raw,observed);self.assertEqual(inputs,source);staging.mkdir()
                (staging/'styled.txt').write_text('derived')
            verify_fn=Mock(return_value={'safe_spawn':True})
            checks,record=promote_styled_shell(observed,source,world,compile_fn,verify_fn)
            self.assertEqual(checks,{'safe_spawn':True});self.assertEqual((observed/'raw.txt').read_text(),'unchanged')
            self.assertTrue((world/'styled.txt').is_file());self.assertFalse((root/'world.styled').exists())
            self.assertFalse(record['resumed']);verify_fn.assert_called_once_with(root/'world.styled')
            _,resumed=promote_styled_shell(observed,source,world,compile_fn,verify_fn)
            self.assertTrue(resumed['resumed']);self.assertEqual(verify_fn.call_count,2)

    def test_source_for_tile_routes_through_shared_cache(self):
        tile={'west':0,'north':1024,'size':256}
        frame={'crs':'EPSG:3857'}
        with tempfile.TemporaryDirectory() as folder, patch(
                'chicago_worker.source_from_cache') as cached:
            destination=Path(folder)/'child'
            cached.side_effect=lambda *args,**kwargs: destination.mkdir()
            result=source_for_tile(tile,frame,destination,source_cache=Path(folder)/'cache',
                                   anchor_west=0,anchor_north=1024)
            self.assertEqual(result,{'crs':'EPSG:3857',**tile})
            cached.assert_called_once()

    def test_partial_geometry_staging_is_preserved_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'sources';source.mkdir()
            staging=root/'world.building';staging.mkdir();(staging/'partial').write_text('audit me')
            def build_fn(actual_source,destination,point_source=None,world_frame=None):
                self.assertEqual(actual_source,source);self.assertFalse(destination.exists())
                destination.mkdir();(destination/'ready').write_text('complete')
            def verify_fn(destination):
                if not (destination/'ready').exists():raise FileNotFoundError('incomplete')
                return {'safe_spawn':True}
            checks,archived=build_or_resume_staging(
                source,staging,None,{'frame':'fixture'},build_fn,verify_fn)
            self.assertEqual(checks,{'safe_spawn':True})
            self.assertEqual((archived/'partial').read_text(),'audit me')
            self.assertEqual((staging/'ready').read_text(),'complete')


if __name__ == '__main__':
    unittest.main()
