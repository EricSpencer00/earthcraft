import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chicago_worker import point_crop_required, promote_styled_shell


class ChicagoWorkerTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
