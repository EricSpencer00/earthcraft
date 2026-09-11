import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import nbtlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import earthcraft


class InstallTests(unittest.TestCase):
    def test_photo_corruption_never_publishes_partial_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);world=root/'source';world.mkdir()
            (world/'block-verification.json').write_text('{}')
            (world/'resources.zip').write_bytes(b'photo pack')
            (world/'level.dat').write_bytes(b'not parsed before copy verification')
            config=root/'runtime/traversal/config';config.mkdir(parents=True)
            (config/'flymod.json').write_text('{}')
            original_copy=shutil.copytree
            def corrupted(source,destination,**kwargs):
                result=original_copy(source,destination,**kwargs)
                (Path(destination)/'resources.zip').write_bytes(b'corrupted')
                return result
            with patch.object(earthcraft,'ROOT',root),patch.object(earthcraft.shutil,'copytree',side_effect=corrupted):
                with self.assertRaisesRegex(ValueError,'Copied save or source changed'):
                    earthcraft.install(world,'Unpublished')
            self.assertFalse((root/'runtime/traversal/saves/Unpublished').exists())
            self.assertEqual((world/'resources.zip').read_bytes(),b'photo pack')
            self.assertEqual(len(list((root/'runtime/traversal').glob('.earthcraft-install-*'))),1)

    def test_atomic_publication_does_not_replace_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'staged';source.mkdir()
            (source/'level.dat').write_bytes(b'new')
            existing=root/'existing';existing.mkdir()
            with self.assertRaises(FileExistsError):earthcraft.publish_install(source,existing)
            self.assertTrue(source.exists());self.assertEqual(list(existing.iterdir()),[])
            earthcraft.publish_install(source,root/'new')
            self.assertEqual((root/'new/level.dat').read_bytes(),b'new')

    def test_traversal_install_preserves_blocks_and_resets_base_speed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = root / 'source'
            (world / 'region').mkdir(parents=True)
            (world / 'region/r.0.0.mca').write_bytes(b'unchanged region fixture')
            (world / 'block-verification.json').write_text('{}')
            nbtlib.File({'Data': nbtlib.Compound({
                'LevelName': nbtlib.String('Source'),
                'Player': nbtlib.Compound({'abilities': nbtlib.Compound({
                    'flySpeed': nbtlib.Float(.2)})})})}, gzipped=True).save(world / 'level.dat')
            config = root / 'runtime/traversal/config'
            config.mkdir(parents=True)
            (config / 'flymod.json').write_text('{}')
            with patch.object(earthcraft, 'ROOT', root):
                installed = earthcraft.install(world, 'Test')
                self.assertEqual(installed, root / 'runtime/traversal/saves/Test')
                self.assertEqual((installed / 'region/r.0.0.mca').read_bytes(),
                                 (world / 'region/r.0.0.mca').read_bytes())
                level = nbtlib.load(installed / 'level.dat')
                self.assertAlmostEqual(float(level['Data']['Player']['abilities']['flySpeed']), .05)
                self.assertEqual(str(level['Data']['LevelName']), 'Test')
                with self.assertRaises(FileExistsError):
                    earthcraft.install(world, 'Test')
                with self.assertRaises(ValueError):
                    earthcraft.install(world, '../escape')
                # Native world controls must not redirect installation away
                # from the configured Geographic Explorer game directory.
                (world / 'travel-controls.json').write_text('{}')
                native = earthcraft.install(world, 'Native')
                self.assertEqual(native.parent, root / 'runtime/traversal/saves')
                native_level = nbtlib.load(native / 'level.dat')
                self.assertAlmostEqual(float(native_level['Data']['Player']['abilities']['flySpeed']), .2)


if __name__ == '__main__':
    unittest.main()
