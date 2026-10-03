import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from street_photo_candidates import build, source_shot


class CandidateTests(unittest.TestCase):
    def test_matching_shot_is_unique_across_all_reconstructions(self):
        record = {'shots': {'source': {'capture_time': 10.001}}}
        self.assertEqual(source_shot([{'shots': {}}, record], 10001)[0], record)
        for records in ([{'shots': {}}], [record, record]):
            with self.assertRaises(ValueError):
                source_shot(records, 10001)

    def test_source_backed_projection_remains_unregistered_and_excludes_behind_camera(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record = {'shots': {'source': {'capture_time': 10, 'camera': 'p',
                      'rotation': [0, 0, 0], 'translation': [0, 0, 0]}},
                      'cameras': {'p': {'projection_type': 'perspective', 'focal': .5}},
                      'points': {'front': {'coordinates': [0, 0, 2]},
                                 'behind': {'coordinates': [0, 0, -2]}}}
            (root/'cluster.json').write_text(json.dumps(record))
            (root/'image.json').write_text(json.dumps({'id': '1', 'captured_at': 10000}))
            Image.new('RGB', (5, 5), (120, 50, 30)).save(root/'photo.png')
            proof = build(root/'cluster.json', root/'photo.png', root/'image.json', root/'out')
            self.assertEqual(proof['projected_color_candidates'], 1)
            self.assertEqual(proof['admitted_world_samples'], 0)
            self.assertFalse(proof['world_modified'])
            self.assertEqual(proof['coordinate_frame'], 'unregistered_reconstruction')
            with np.load(root/'out/observations.npz') as data:
                self.assertEqual(data['rgb'].tolist(), [[120, 50, 30]])
                self.assertEqual(data['cluster_xyz'].tolist(), [[0, 0, 2]])
            with self.assertRaises(FileExistsError):
                build(root/'cluster.json', root/'photo.png', root/'image.json', root/'out')


if __name__ == '__main__': unittest.main()
