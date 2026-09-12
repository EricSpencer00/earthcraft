import gzip
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from building_delivery import REQUEST_SCHEMA, admit_request, has_pending_delivery, service_pending_requests


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class BuildingDeliveryTests(unittest.TestCase):
    def test_pending_delivery_ignores_terminal_history(self):
        with TemporaryDirectory() as folder:
            exchange=Path(folder);(exchange/'building-requests').mkdir();(exchange/'building-publications').mkdir()
            request=exchange/'building-requests'/'a.json';request.write_text('{}')
            self.assertTrue(has_pending_delivery(exchange))
            (exchange/'building-publications'/'a.json').write_text(json.dumps({'state':'complete'}))
            self.assertFalse(has_pending_delivery(exchange))
            (exchange/'building-publications'/'a.json').write_text(json.dumps({'state':'awaiting_importer'}))
            self.assertTrue(has_pending_delivery(exchange))

    def fixture(self, folder):
        root = Path(folder)
        exchange = root / 'exchange'
        for name in ('inbox', 'receipts', 'archive', 'building-requests', 'building-publications'):
            (exchange / name).mkdir(parents=True, exist_ok=True)
        world = root / 'world'; world.mkdir()
        (exchange / 'binding.json').write_text(json.dumps({'frame': 'frame', 'world': str(world)}))
        (exchange / 'status.json').write_text(json.dumps({'modes': ['new_chunk', 'building_delta']}))
        candidate = root / 'candidate'; candidate.mkdir()
        (candidate / 'building-layer.json').write_text('{}')
        stage = root / 'stage'; (stage / 'inbox').mkdir(parents=True)
        patch = {'version': 1, 'frame': 'frame', 'cx': 2, 'cz': 3, 'mode': 'building_delta',
                 'palette': ['minecraft:air', 'minecraft:stone'], 'runs': [[0, 1, 0, 1]], 'cells': 1,
                 'provenance': {'llm_used': False}}
        raw = json.dumps(patch, sort_keys=True, separators=(',', ':')).encode()
        packed = gzip.compress(raw, mtime=0); identity = hashlib.sha256(packed).hexdigest()
        (stage / 'inbox' / f'{identity}.json.gz').write_bytes(packed)
        manifest = {'schema': 'building-delta-stage-v1', 'frame': 'frame', 'candidate_world': str(candidate),
                    'candidate_manifest_sha256': digest(candidate / 'building-layer.json'), 'changed_cells': 1,
                    'installed': False, 'llm_used': False,
                    'patches': [{'patch': identity, 'chunk': [2, 3], 'cells': 1}]}
        (stage / 'manifest.json').write_text(json.dumps(manifest))
        manifest_sha = digest(stage / 'manifest.json')
        native = root / 'native.json'; native.write_text(json.dumps({'passed': True,
            'staged_manifest_sha256': manifest_sha, 'two_load_save_cycles_verified': True,
            'player_edit_preserved': True, 'block_entity_preserved': True, 'unowned_chunk_preserved': True,
            'district_patches': 1, 'district_written_cells': 1}))
        closed = root / 'closed.json'; closed.write_text(json.dumps({'passed': True, 'llm_used': False,
            'stage_manifest_sha256': manifest_sha, 'closed_writer_matches_native_blocks_and_block_entities': True,
            'two_native_save_cycles_previously_verified': True, 'patches': 1, 'changed_cells': 1}))
        request = {'schema': REQUEST_SCHEMA, 'stage': str(stage), 'stage_manifest_sha256': manifest_sha,
                   'frame': 'frame', 'native_verification': str(native), 'native_verification_sha256': digest(native),
                   'closed_verification': str(closed), 'closed_verification_sha256': digest(closed),
                   'baseline_audit': {'schema': 'live-building-baseline-audit-v1',
                                      'stage_manifest_sha256': manifest_sha,
                                      'ready_for_building_delta': True, 'llm_used': False},
                   'patches': 1, 'changed_cells': 1, 'llm_used': False}
        request_path = exchange / 'building-requests' / f'{manifest_sha}.json'
        request_path.write_text(json.dumps(request))
        return exchange, request_path, identity

    def test_admission_revalidates_stage_and_receipts(self):
        with TemporaryDirectory() as folder:
            exchange, request_path, identity = self.fixture(folder)
            request, patches = admit_request(request_path, json.loads((exchange / 'binding.json').read_text()))
        self.assertEqual(request['patches'], 1)
        self.assertEqual(list(patches), [identity])

    def test_publisher_owned_delivery_reserves_bounded_slot(self):
        with TemporaryDirectory() as folder:
            exchange, request_path, identity = self.fixture(folder)
            binding = json.loads((exchange / 'binding.json').read_text()); cache = {}
            self.assertEqual(service_pending_requests(exchange, binding, 1, cache), 1)
            self.assertTrue((exchange / 'inbox' / f'{identity}.json.gz').exists())
            first = json.loads((exchange / 'building-publications' / request_path.name).read_text())
            self.assertEqual(first['state'], 'awaiting_importer')
            (exchange / 'receipts' / f'{identity}.json').write_text(json.dumps({
                'result': 'applied_in_memory', 'written': 1, 'conflicts': 0, 'already_target': 0}))
            self.assertEqual(service_pending_requests(exchange, binding, 1, cache), 0)
            final = json.loads((exchange / 'building-publications' / request_path.name).read_text())
        self.assertEqual(final['state'], 'complete')
        self.assertEqual(final['written'], 1)

    def test_terminal_history_is_not_revalidated_before_pending_stage(self):
        with TemporaryDirectory() as folder:
            exchange, request_path, _ = self.fixture(folder)
            pending=exchange/'building-requests'/'z-pending.json';request_path.rename(pending)
            historic=exchange/'building-requests'/'a-history.json';historic.write_text('{}')
            (exchange/'building-publications'/historic.name).write_text(json.dumps({'state':'complete'}))
            binding=json.loads((exchange/'binding.json').read_text())
            with patch('building_delivery.admit_request',wraps=admit_request) as admitted:
                self.assertEqual(service_pending_requests(exchange,binding,1,{}),1)
            self.assertEqual([call.args[0] for call in admitted.call_args_list],[pending])

    def test_conflicted_receipt_is_preserved_and_labeled(self):
        with TemporaryDirectory() as folder:
            exchange, request_path, identity = self.fixture(folder)
            binding = json.loads((exchange / 'binding.json').read_text()); cache = {}
            service_pending_requests(exchange, binding, 1, cache)
            (exchange / 'receipts' / f'{identity}.json').write_text(json.dumps({
                'result': 'applied_in_memory', 'written': 0, 'conflicts': 1, 'already_target': 0}))
            service_pending_requests(exchange, binding, 1, cache)
            final = json.loads((exchange / 'building-publications' / request_path.name).read_text())
        self.assertEqual(final['state'], 'complete_with_conflicts')
        self.assertEqual(final['conflicts_preserved'], 1)


if __name__ == '__main__':
    unittest.main()
