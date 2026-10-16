"""Street-detail layer: Anvil codec, layer format, write guard and apply-to-copy."""
from pathlib import Path
import math
import sys
import tempfile
import unittest

import nbtlib as n
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from detail_anvil import (ChunkVolume, block_bits, canonical, decode_states, encode_region, encode_states,
                          pack, parse_state, read_region, unpack)
from detail_apply import apply, refuse_save, verify_region
from detail_guard import WriteGuard
from detail_layer import LayerBuilder, load_manifest, read_region_edits

FRAME = {'crs': 'EPSG:32616', 'west': 0, 'north': 0, 'vertical_offset_m': 0}


def synthetic_chunk(cx, cz, sections=range(-4, 4), ground=0):
    """A 1.21-style chunk: stone below `ground`, grass at ground, air above, plus a light-only section."""
    secs = []
    for sy in sections:
        keys = np.full(4096, 'minecraft:air', dtype=object)
        ys = np.arange(4096) >> 8
        y = sy * 16 + ys
        keys[y < ground] = 'minecraft:stone'
        keys[y == ground] = 'minecraft:grass_block[snowy=false]'
        secs.append(n.Compound({'Y': n.Byte(sy), 'block_states': encode_states(keys),
                                'biomes': n.Compound({'palette': n.List[n.String]([n.String('minecraft:plains')])}),
                                'SkyLight': n.ByteArray([0] * 2048)}))
    secs.append(n.Compound({'Y': n.Byte(min(sections) - 1), 'SkyLight': n.ByteArray([0] * 2048)}))
    height = 16 * len(list(sections))
    bits = height.bit_length()
    hm = pack(np.full(256, ground + 1 - min(sections) * 16), bits)
    return n.Compound({'DataVersion': n.Int(4556), 'xPos': n.Int(cx), 'zPos': n.Int(cz), 'yPos': n.Int(min(sections)),
                       'Status': n.String('minecraft:full'), 'isLightOn': n.Byte(1), 'sections': n.List[n.Compound](secs),
                       'Heightmaps': n.Compound({k: hm for k in ('WORLD_SURFACE', 'MOTION_BLOCKING',
                                                                 'MOTION_BLOCKING_NO_LEAVES', 'OCEAN_FLOOR')}),
                       'block_entities': n.List[n.Compound]([])})


def write_region(path, chunks):
    path.write_bytes(encode_region({(t['xPos'], t['zPos']): {'tag': t, 'timestamp': 7} for t in chunks}))


class CodecTests(unittest.TestCase):
    def test_packed_longs_never_span_and_round_trip(self):
        values = np.arange(4096) % 37
        for bits in (4, 5, 6, 11, 13):
            words = pack(values % (1 << bits), bits)
            self.assertEqual(len(words), math.ceil(4096 / (64 // bits)))
            np.testing.assert_array_equal(unpack(words, bits, 4096), values % (1 << bits))
        self.assertEqual(block_bits(1), 4)
        self.assertEqual(block_bits(17), 5)
        with self.assertRaises(ValueError):
            pack([16], 4)

    def test_block_state_keys_are_canonical(self):
        self.assertEqual(canonical('minecraft:oak_leaves[waterlogged=false,persistent=true,distance=2]'),
                         'minecraft:oak_leaves[distance=2,persistent=true,waterlogged=false]')
        self.assertEqual(parse_state('minecraft:stone'), ('minecraft:stone', {}))
        with self.assertRaises(ValueError):
            parse_state('stone[')

    def test_section_encoding_keeps_properties_and_single_palette(self):
        keys = np.full(4096, 'minecraft:air', dtype=object)
        keys[5] = 'minecraft:oak_log[axis=y]'
        palette, values = decode_states(encode_states(keys))
        self.assertEqual(palette, ['minecraft:air', 'minecraft:oak_log[axis=y]'])
        self.assertEqual(values[5], 1)
        single = encode_states(np.full(4096, 'minecraft:stone', dtype=object))
        self.assertNotIn('data', single)

    def test_region_round_trip_preserves_untouched_payloads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'r.0.0.mca'
            write_region(path, [synthetic_chunk(0, 0), synthetic_chunk(1, 0)])
            chunks = read_region(path)
            self.assertEqual(set(chunks), {(0, 0), (1, 0)})
            volume = ChunkVolume(chunks[(0, 0)]['tag'], height=128)
            volume.set(3, 1, 4, 'minecraft:oak_log[axis=y]')
            self.assertTrue(volume.flush())
            chunks[(0, 0)]['changed'] = True
            (Path(folder) / 'out').mkdir()
            final = Path(folder) / 'out' / 'r.0.0.mca'
            final.write_bytes(encode_region(chunks))
            reread = read_region(final)
            self.assertEqual(bytes(reread[(1, 0)]['payload']), bytes(chunks[(1, 0)]['payload']))
            edited = ChunkVolume(reread[(0, 0)]['tag'])
            self.assertEqual(edited.get(3, 1, 4), 'minecraft:oak_log[axis=y]')
            self.assertEqual(int(reread[(0, 0)]['tag']['isLightOn']), 0)
            self.assertFalse(any('SkyLight' in s for s in reread[(0, 0)]['tag']['sections']))
            heights = unpack(reread[(0, 0)]['tag']['Heightmaps']['WORLD_SURFACE'], 8, 256).reshape(16, 16)
            self.assertEqual(heights[4, 3], 1 + 1 + 64)   # log top y=1 -> 2 above min_y=-64
            self.assertEqual(verify_region(final), [])
            self.assertEqual(reread[(0, 0)]['timestamp'], 7)

    def test_refuses_to_grow_the_world(self):
        volume = ChunkVolume(synthetic_chunk(0, 0))
        with self.assertRaises(ValueError):
            volume.set(0, 400, 0, 'minecraft:stone')


class LayerTests(unittest.TestCase):
    def test_guard_caps_bytes_overwrites_and_root(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = WriteGuard(folder, 10, 0)
            guard.write_bytes(Path(folder) / 'a', b'12345')
            with self.assertRaises(FileExistsError):
                guard.write_bytes(Path(folder) / 'a', b'1')
            with self.assertRaises(OSError):
                guard.write_bytes(Path(folder) / 'b', b'123456')
            with self.assertRaises(PermissionError):
                guard.write_bytes(Path(folder).parent / 'escape', b'1')
            with self.assertRaises(OSError):
                WriteGuard(folder, 100, 1 << 62).write_bytes(Path(folder) / 'c', b'1')

    def test_builder_priorities_and_round_trip(self):
        layer = LayerBuilder('t', FRAME)
        s = layer.source('tree_canopy', 'test', 'PD', 'observed', feature='t1')
        self.assertTrue(layer.add(-1, 70, 513, 'minecraft:oak_leaves[persistent=true,distance=1,waterlogged=false]', s, priority=1))
        self.assertFalse(layer.add(-1, 70, 513, 'minecraft:stone', s, priority=0))
        self.assertTrue(layer.add(-1, 70, 513, 'minecraft:oak_log[axis=y]', s, priority=3))
        layer.add(5, 64, 5, 'minecraft:smooth_stone', s, expect='minecraft:gray_concrete')
        with self.assertRaises(ValueError):
            layer.source('x', 'y', 'z', 'guessed')
        with tempfile.TemporaryDirectory() as folder:
            manifest = layer.write(Path(folder) / 'layer', WriteGuard(folder, 10**6, 0))
            self.assertEqual(manifest['edits'], 2)
            self.assertEqual(set(manifest['regions']), {'r.-1.1', 'r.0.0'})
            load_manifest(Path(folder) / 'layer')
            rows = list(read_region_edits(Path(folder) / 'layer' / manifest['regions']['r.-1.1']['path']))
            (_, chunk, edits), = rows
            self.assertEqual(chunk, (-1, 32))
            self.assertEqual(edits[0][:4], (15, 70, 1, 'minecraft:oak_log[axis=y]'))
            self.assertIsNone(edits[0][4])

    def test_apply_checks_preconditions_and_never_writes_saves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            regions = root / 'copy'
            regions.mkdir()
            write_region(regions / 'r.0.0.mca', [synthetic_chunk(0, 0), synthetic_chunk(1, 1)])
            layer = LayerBuilder('street', FRAME)
            s = layer.source('sidewalk', 'test', 'PD', 'derived')
            layer.add(2, 0, 2, 'minecraft:smooth_stone', s, expect='minecraft:grass_block')   # ok
            layer.add(2, 0, 3, 'minecraft:smooth_stone', s, expect='minecraft:gray_concrete')  # precondition fails
            layer.add(2, 1, 2, 'minecraft:lantern[hanging=false,waterlogged=false]', s)          # air -> ok
            layer.add(2, -1, 2, 'minecraft:oak_log[axis=y]', s)                                  # stone, must be air -> fails
            layer.write(root / 'layer', WriteGuard(root, 10**6, 0))
            save = root / 'save'
            (save / 'region').mkdir(parents=True)
            (save / 'level.dat').write_bytes(b'')
            with self.assertRaises(PermissionError):
                refuse_save(save / 'region')
            out = root / 'out'
            receipt = apply(regions, [root / 'layer'], out, WriteGuard(out.parent, 10**7, 0))
            stats = receipt['regions']['r.0.0']
            self.assertEqual((stats['applied'], stats['precondition_failed']), (2, 2))
            self.assertEqual(stats['chunks_changed'], 1)
            self.assertTrue(stats['verified'])
            result = read_region(out / 'r.0.0.mca')
            volume = ChunkVolume(result[(0, 0)]['tag'])
            self.assertEqual(volume.get(2, 0, 2), 'minecraft:smooth_stone')
            self.assertEqual(volume.get(2, 0, 3), 'minecraft:grass_block[snowy=false]')
            original = read_region(regions / 'r.0.0.mca')
            self.assertEqual(bytes(result[(1, 1)]['payload']), bytes(original[(1, 1)]['payload']))
            with self.assertRaises(PermissionError):
                apply(regions, [root / 'layer'], regions / 'nested', WriteGuard(root, 10**7, 0))


if __name__ == '__main__':
    unittest.main()
