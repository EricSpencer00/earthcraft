"""push-batch (caller-owned files) and the stdlib DatasetReader against a fake Hub."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hf_store as hs  # noqa: E402
from regional_fixtures import FakeHub, REPO, make_ctx, reader_for  # noqa: E402


class PushBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hub = FakeHub()
        self.hub.add_repo(REPO, {})
        self.ctx = make_ctx(self.root / 'scratch', self.hub, batch_files=2)

    def tearDown(self):
        self.tmp.cleanup()

    def items(self, count):
        result = []
        for index in range(count):
            path = self.root / 'owned' / f'{index}.bin'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\0' + bytes([index]) * 100)
            digest = hs.hash_file(path)
            result.append({'path': f'tiles/v2/{index}_0/region/r.{index}.0.mca', 'local': str(path),
                           'sha256': digest.sha256, 'bytes': digest.size})
        return result

    def test_batches_are_bounded_verified_and_leave_the_callers_files(self):
        items = self.items(5)
        result = hs.push_batch(self.ctx, items, 'Generate tiles/v2')
        self.assertEqual(len(self.hub.commits), 3)  # 2 + 2 + 1 under batch_files=2
        self.assertEqual(set(result['files']), {item['path'] for item in items})
        for item in items:
            self.assertTrue(Path(item['local']).exists(), 'the caller deletes after verification')
            self.assertEqual(result['files'][item['path']]['sha256'], item['sha256'])
        self.assertFalse(any((self.root / 'scratch' / 'ledger').glob('batch-*.json')), 'per-batch ledger removed')

    def test_a_changed_local_file_is_refused_before_commit(self):
        items = self.items(1)
        Path(items[0]['local']).write_bytes(b'changed')
        with self.assertRaises(hs.VerificationError):
            hs.push_batch(self.ctx, items, 'x')
        self.assertEqual(self.hub.commits, [])

    def test_tampered_remote_fails_verification(self):
        items = self.items(1)
        self.hub.tamper[items[0]['path']] = b'\0tampered'
        with self.assertRaises(hs.VerificationError):
            hs.push_batch(self.ctx, items, 'x')

    def test_subprocess_client_parses_the_result_event(self):
        calls = []

        def run(command, capture_output, text):
            calls.append(command)
            manifest = json.loads(Path(command[command.index('--manifest') + 1]).read_text())
            self.assertEqual(manifest['schema'], hs.BATCH_SCHEMA)
            lines = [json.dumps({'event': 'commit_verified', 'oid': 'abc'}),
                     json.dumps({'event': 'result', 'command': 'push-batch', 'files': {'a': {}}, 'commits': ['abc']})]
            return SimpleNamespace(returncode=0, stdout='\n'.join(lines), stderr='')
        client = hs.StoreClient(python='/hf/python', scratch=self.root / 'hf', cap=2**30, min_free=0, run=run)
        result = client.push([{'path': 'a', 'local': '/x', 'sha256': '0' * 64, 'bytes': 1}], 'label')
        self.assertEqual(result['commits'], ['abc'])
        self.assertEqual(calls[0][0], '/hf/python')
        self.assertEqual(list((self.root / 'hf').iterdir()), [], 'manifest removed')
        failing = hs.StoreClient(python='p', scratch=self.root / 'hf', run=lambda *a, **k: SimpleNamespace(
            returncode=1, stdout='', stderr='boom'))
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            failing.push([{'path': 'a', 'local': '/x', 'sha256': '0' * 64, 'bytes': 1}], 'label')


class DatasetReaderTests(unittest.TestCase):
    def test_reads_stream_verified_files_and_reject_tampering(self):
        hub = FakeHub()
        region = b'\0' * 9000 + b'chunk'
        hub.add_repo(REPO, {'tiles/v2/0_0/region/r.0.0.mca': region, 'tiles/v2/0_0/tile.json': b'{"a": 1}\n'})
        reader = reader_for(hub)
        info = reader.info(['tiles/v2/0_0/region/r.0.0.mca', 'tiles/v2/0_0/tile.json', 'missing.json'])
        self.assertEqual(set(info), {'tiles/v2/0_0/region/r.0.0.mca', 'tiles/v2/0_0/tile.json'})
        self.assertIsNotNone(info['tiles/v2/0_0/region/r.0.0.mca'].sha256)
        self.assertEqual(json.loads(reader.read('tiles/v2/0_0/tile.json')), {'a': 1})
        with tempfile.TemporaryDirectory() as directory:
            admitted = []
            target = Path(directory) / 'r.0.0.mca'
            digest = reader.download('tiles/v2/0_0/region/r.0.0.mca', target, admit=admitted.append)
            self.assertEqual(target.read_bytes(), region)
            self.assertEqual(admitted, [len(region)])
            self.assertEqual(digest.sha256, info['tiles/v2/0_0/region/r.0.0.mca'].sha256)
            with self.assertRaises(FileNotFoundError):
                reader.download('missing.json', Path(directory) / 'missing.json')
            wrong = hs.RemoteFile('tiles/v2/0_0/tile.json', 9, None, 'f' * 40)
            with self.assertRaises(hs.VerificationError):
                reader.download('tiles/v2/0_0/tile.json', Path(directory) / 'tile.json', expected=wrong)
            self.assertFalse((Path(directory) / 'tile.json').exists())
            self.assertFalse(any(Path(directory).glob('*.partial')))
        self.assertEqual(reader.head_revision(), hub.repos[REPO]['sha'])


if __name__ == '__main__':
    unittest.main()
