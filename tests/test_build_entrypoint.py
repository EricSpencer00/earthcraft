import json
from pathlib import Path
import unittest


class BuildEntrypointTests(unittest.TestCase):
    def test_double_click_uses_replay_and_photo_catalog(self):
        root=Path(__file__).resolve().parents[1]
        entry=(root/'Build Earthcraft.command').read_text()
        self.assertIn('set -e',entry)
        self.assertIn('scripts/earth_location.py --kml location.kmz',entry)
        self.assertIn('scripts/earth_location.py --kml location.kml',entry)
        self.assertIn('exec .venv/bin/python scripts/earthcraft.py --replay-check',entry)
        catalog=json.loads((root/'configs/atlas-regions.json').read_text())
        region=catalog['regions'][catalog['default_region']]
        self.assertIn('photo_layer',region)
        self.assertTrue(region['photo_layer']['start_at_detail'])


if __name__=='__main__':unittest.main()
