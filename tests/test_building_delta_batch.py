import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from building_delta_batch import stage_batch


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class BuildingDeltaBatchTests(unittest.TestCase):
    def test_receipt_bound_batch_stages_without_installing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source' / 'world'; candidate = root / 'candidates' / 'tile' / 'world'
            (source / 'region').mkdir(parents=True); (candidate / 'region').mkdir(parents=True)
            (source / 'earthcraft.json').write_text('{}')
            (candidate / 'earthcraft.json').write_text('{}')
            (candidate / 'building-layer.json').write_text('{}')
            (candidate / 'region' / 'r.0.0.mca').write_bytes(b'candidate')
            record = {'tile': 'tile', 'base_world': str(source), 'candidate_world': str(candidate)}
            receipt = {
                'schema': 'building-rerun-receipt-v1', 'result': 'pass', 'llm_used': False,
                'installed': False, 'baseline': record, 'candidate_world': str(candidate),
                'candidate_manifest_sha256': digest(candidate / 'building-layer.json'),
                'candidate_region_sha256': {'r.0.0.mca': digest(candidate / 'region' / 'r.0.0.mca')},
            }
            (candidate.parent / 'building-rerun-receipt.json').write_text(json.dumps(receipt))
            rerun = root / 'rerun'; rerun.mkdir()
            completed = rerun / 'manifest.completed.json'
            manifest = {'schema': 'building-rerun-manifest-v1', 'state': 'complete', 'llm_used': False,
                        'installed': False, 'tiles': [record], 'completed_tiles': 1, 'output_root': str(rerun)}
            completed.write_text(json.dumps(manifest))
            binding = root / 'binding.json'; binding.write_text('{}')
            def fake_stage(original, styled, binding_path, output):
                self.assertEqual((original, styled, binding_path),
                                 (source.resolve(), candidate.resolve(), binding))
                output.mkdir(); (output / 'manifest.json').write_text('{}')
                return {'patches': [], 'changed_cells': 0}
            result = stage_batch(completed, binding, root / 'stages', fake_stage)
            self.assertEqual(result['state'], 'complete')
            self.assertEqual(result['tiles_staged'], 1)
            self.assertFalse(result['installed'])
            self.assertFalse(result['llm_used'])


if __name__ == '__main__':
    unittest.main()
