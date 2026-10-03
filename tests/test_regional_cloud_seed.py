import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from regional_cloud_seed import write_seed


class CloudSeedTests(unittest.TestCase):
    def test_old_worker_and_bytecode_are_replaced_while_source_assets_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            base, output = Path(folder)/'old.tar.gz', Path(folder)/'seed.tar.gz'
            with tarfile.open(base,'w:gz') as stream:
                for name,raw in [('scripts/regional_cloud_worker.py',b'old protocol'),
                                 ('scripts/__pycache__/worker.pyc',b'old bytecode'),
                                 ('control/frame.json',b'original frozen frame')]:
                    member=tarfile.TarInfo(name);member.size=len(raw);stream.addfile(member,io.BytesIO(raw))
            write_seed(base,output,{'scripts/regional_cloud_worker.py':b'current footprint-aware protocol'})
            with tarfile.open(output) as stream:
                self.assertEqual(stream.extractfile('scripts/regional_cloud_worker.py').read(),b'current footprint-aware protocol')
                self.assertEqual(stream.extractfile('control/frame.json').read(),b'original frozen frame')
                self.assertNotIn('scripts/__pycache__/worker.pyc',stream.getnames())


if __name__ == '__main__': unittest.main()
