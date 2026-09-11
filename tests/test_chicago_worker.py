import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from chicago_worker import point_crop_required


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


if __name__ == '__main__':
    unittest.main()
