from pathlib import Path
import unittest


class BuildEntrypointTests(unittest.TestCase):
    def test_double_click_uses_replay_and_photo_catalog(self):
        root=Path(__file__).resolve().parents[1]
        entry=(root/'Build Earthcraft.command').read_text()
        self.assertIn('set -e',entry)
        self.assertIn('scripts/earth_location.py --kml location.kmz',entry)
        self.assertIn('scripts/earth_location.py --kml location.kml',entry)
        self.assertIn('PYTHON="$ROOT/.venv/bin/python"',entry)
        self.assertIn('exec "$PYTHON" scripts/earthcraft.py --replay-check',entry)
        self.assertNotIn('/Users/',entry)


if __name__=='__main__':unittest.main()
