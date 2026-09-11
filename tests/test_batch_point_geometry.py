import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from shapely.geometry import box,mapping
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from batch_point_geometry import load_batch_points


class BatchPointTests(unittest.TestCase):
    def test_missing_eastern_acquisition_is_not_silently_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'manifest.json').write_text(json.dumps({'coverage_geometry':mapping(box(0,0,8,16)),
                'output_horizontal_crs':'fixture','points_sha256':'unused'}))
            # The source checksum is checked before the coverage gate; stub only
            # this file IO boundary, not the geometric coverage calculation.
            (path/'points.npz').write_bytes(b'fixture')
            import hashlib
            manifest=json.loads((path/'manifest.json').read_text())
            manifest['points_sha256']=hashlib.sha256(b'fixture').hexdigest()
            (path/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'Acquire missing original point tiles'):
                load_batch_points(path,path,{'crs':'fixture','size':16,'west':0,'north':16},np.zeros((16,16)),0)


if __name__=='__main__':unittest.main()
