import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aws_terrain import write_rasters
from regional_sources import validate
from region_expansion import sha


class RegionalSourceTests(unittest.TestCase):
    def test_zero_length_raster_cannot_reuse_success_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'sources'
            source.mkdir()
            tile = {'id': '0_0', 'size': 16, 'west': 0, 'north': 16}
            (source / 'sources.json').write_text(json.dumps(tile))
            (root / 'sources-receipt.json').write_text(json.dumps({
                'tile': '0_0', 'stage': 'sources', 'result': 'pass',
                'sources_sha256': sha(source / 'sources.json'),
            }))
            (source / 'rasters.npz').touch()
            with self.assertRaises((EOFError, ValueError)):
                validate(root, tile)
            write_rasters(source / 'rasters.npz', np.ones((16, 16)), np.zeros((16, 16), np.uint8))
            self.assertEqual(validate(root, tile), source)

    def test_failed_write_does_not_replace_previous_raster(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rasters.npz'
            path.write_bytes(b'previous frozen artifact')
            with patch('aws_terrain.np.savez_compressed', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    write_rasters(path, np.ones((16,16)), np.zeros((16,16), np.uint8))
            self.assertEqual(path.read_bytes(), b'previous frozen artifact')


if __name__ == '__main__': unittest.main()
