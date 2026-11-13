"""Load check: tile choice from the status bitmaps and the blocks it asks the server about."""
import base64
import random
import unittest
import zlib

import nbtlib as n
import numpy as np

import detail_anvil
import load_check


def bitmap(ids):
    xy = [tuple(map(int, t.split('_'))) for t in ids]
    x0, z0 = min(x for x, _ in xy), min(z for _, z in xy)
    width, height = max(x for x, _ in xy) - x0 + 1, max(z for _, z in xy) - z0 + 1
    bits = bytearray((width * height + 7) // 8)
    for x, z in xy:
        i = (z - z0) * width + (x - x0)
        bits[i >> 3] |= 1 << (i & 7)
    return {'x0': x0, 'z0': z0, 'width': width, 'height': height,
            'bitmap': base64.b64encode(zlib.compress(bytes(bits))).decode()}


class LoadCheckTests(unittest.TestCase):
    def test_bitmap_round_trip_and_choice_prefers_detailed_tiles(self):
        published, detailed = ['-3_2', '0_0', '4_-1', '-56_-5', '7_7'], ['0_0', '-56_-5']
        self.assertEqual(sorted(load_check.bitmap_tiles(bitmap(published))), sorted(published))
        status = {'published_tiles': bitmap(published), 'detailed_tiles': bitmap(detailed)}
        chosen = load_check.choose(status, 3, '2026-11-13')
        self.assertEqual(sorted(chosen[:2]), sorted(detailed))
        self.assertIn(chosen[2], set(published) - set(detailed))
        self.assertEqual(chosen, load_check.choose(status, 3, '2026-11-13'))  # same day, same tiles
        self.assertEqual(load_check.choose({}, 3, 'x'), [])

    def test_top_blocks_are_the_highest_non_air_block_in_world_coordinates(self):
        keys = np.full(4096, 'minecraft:air', dtype=object).reshape(16, 16, 16)  # YZX
        keys[:5] = 'minecraft:stone'
        keys[5] = 'minecraft:grass_block[snowy=false]'
        section = n.Compound({'Y': n.Byte(4), 'block_states': detail_anvil.encode_states(keys.ravel())})
        empty = n.Compound({'Y': n.Byte(5), 'block_states': detail_anvil.encode_states(
            np.full(4096, 'minecraft:air', dtype=object))})
        chunks = {(-3, 2): {'tag': n.Compound({'sections': n.List[n.Compound]([section, empty])})}}
        blocks = load_check.top_blocks(chunks, 8, random.Random(1))
        self.assertEqual(len(blocks), 8)
        for x, y, z, state in blocks:
            self.assertEqual((x >> 4, z >> 4, y, state), (-3, 2, 4 * 16 + 5, 'minecraft:grass_block[snowy=false]'))

    def test_the_server_is_not_started_without_agreement_to_the_eula(self):
        from unittest import mock
        with mock.patch.dict(load_check.__dict__, {'check': mock.Mock()}), \
                mock.patch.dict('os.environ', {'MINECRAFT_EULA': ''}), self.assertRaises(SystemExit) as stop:
            load_check.main(['--tiles', '0_0'])
        self.assertIn('EULA', str(stop.exception))


if __name__ == '__main__':
    unittest.main()
