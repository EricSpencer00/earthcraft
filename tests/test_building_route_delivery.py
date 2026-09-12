import json
import unittest
from unittest.mock import patch

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from building_route_delivery import _closed_ok, _fixture, _native_ok, await_publication, route_tiles


class BuildingRouteDeliveryTests(unittest.TestCase):
    def test_uses_route_suffix_without_reordering(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'route.json'
            path.write_text('{"ordered_tiles":["2_1","1_1","3_1"]}')
            self.assertEqual(route_tiles(path, '1_1'), ['1_1', '3_1'])

    def test_requires_full_preservation_native_receipt(self):
        receipt = {'passed': True, 'staged_manifest_sha256': 'a',
                   'two_load_save_cycles_verified': True, 'player_edit_preserved': True,
                   'block_entity_preserved': True, 'unowned_chunk_preserved': True}
        self.assertTrue(_native_ok(receipt, 'a'))
        self.assertFalse(_native_ok({**receipt, 'player_edit_preserved': False}, 'a'))

    def test_requires_closed_native_binding_and_no_llm(self):
        receipt = {'passed': True, 'stage_manifest_sha256': 'a',
                   'two_native_save_cycles_previously_verified': True,
                   'closed_writer_matches_native_blocks_and_block_entities': True,
                   'llm_used': False}
        self.assertTrue(_closed_ok(receipt, 'a'))
        self.assertFalse(_closed_ok({**receipt, 'llm_used': True}, 'a'))

    def test_disposable_native_fixture_uses_local_apfs_temp(self):
        self.assertEqual(_fixture('proof').parent, Path('/private/tmp'))

    def test_completed_conflicted_publication_is_terminal_and_preserved(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); (root / 'building-publications').mkdir()
            stage = root / 'stage'; stage.mkdir(); (stage / 'manifest.json').write_text('{}')
            import building_route_delivery
            with patch.object(building_route_delivery, 'sha', return_value='a' * 64):
                (root / 'building-publications' / ('a' * 64 + '.json')).write_text(json.dumps({
                    'state': 'complete_with_conflicts', 'conflicts_preserved': 1}))
                result = await_publication(root, stage)
            self.assertEqual(result['conflicts_preserved'], 1)


if __name__ == '__main__':
    unittest.main()
