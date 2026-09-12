import unittest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from filter_building_stage import select_safe_patches


class FilterBuildingStageTests(unittest.TestCase):
    def test_selects_only_exactly_admitted_base_chunks(self):
        manifest = {'patches': [
            {'patch': 'a' * 64, 'chunk': [1, 2], 'cells': 10},
            {'patch': 'b' * 64, 'chunk': [3, 4], 'cells': 20},
        ]}
        audit = {'schema': 'live-building-baseline-audit-v1', 'encoding_exact': True,
                 'baseline_mismatches': 0, 'records': [
                     {'chunk': [1, 2], 'cells': 10, 'base_delivery': 'protected_bootstrap_already_target',
                      'base_patch': 'c' * 64, 'bootstrap_base_patch': 'd' * 64},
                     {'chunk': [3, 4], 'cells': 20, 'base_delivery': 'protected_bootstrap_not_exact',
                      'base_patch': 'e' * 64, 'bootstrap_base_patch': 'f' * 64},
                 ]}
        accepted, excluded = select_safe_patches(manifest, audit)
        self.assertEqual(accepted, [manifest['patches'][0]])
        self.assertEqual(excluded[0]['chunk'], [3, 4])
        self.assertEqual(excluded[0]['base_delivery'], 'protected_bootstrap_not_exact')

    def test_rejects_any_encoding_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'not exact'):
            select_safe_patches({'patches': []}, {
                'schema': 'live-building-baseline-audit-v1', 'encoding_exact': False,
                'baseline_mismatches': 1, 'records': []})


if __name__ == '__main__':
    unittest.main()
