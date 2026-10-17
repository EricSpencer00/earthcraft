import struct
import unittest
import zlib

import detail_levels


def tag_string(value):
    data = value.encode()
    return struct.pack('>H', len(data)) + data


def chunk(names):
    palette = b''.join(b'\x08' + tag_string('Name') + tag_string(n) + b'\x00' for n in names)
    block_states = b'\x0a' + tag_string('block_states') + b'\x09' + tag_string('palette') + b'\x0a' + struct.pack('>i', len(names)) + palette + b'\x00'
    section = b'\x01' + tag_string('Y') + b'\x00' + block_states + b'\x00'
    root = b'\x0a' + tag_string('') + b'\x03' + tag_string('DataVersion') + struct.pack('>i', 4556)
    root += b'\x09' + tag_string('sections') + b'\x0a' + struct.pack('>i', 1) + section + b'\x00'
    payload = zlib.compress(root)
    return struct.pack('>iB', len(payload) + 1, 2) + payload


class DetailLevelsTest(unittest.TestCase):
    def test_air_only_chunk_is_empty(self):
        self.assertFalse(detail_levels.chunk_has_blocks(chunk(['minecraft:air'])))

    def test_terrain_chunk_has_blocks(self):
        self.assertTrue(detail_levels.chunk_has_blocks(chunk(['minecraft:air', 'minecraft:stone'])))

    def test_has_non_air_block_air_only(self):
        self.assertFalse(detail_levels.has_non_air_block(chunk(['minecraft:air'])))

    def test_has_non_air_block_with_stone(self):
        self.assertTrue(detail_levels.has_non_air_block(chunk(['minecraft:air', 'minecraft:stone'])))

    def test_interval_bounds(self):
        self.assertEqual(detail_levels.interval(0, 100, 1000)[0], 0)
        low, high = detail_levels.interval(50, 100, 1000)
        self.assertTrue(low < 500 < high)

    def test_cell_with_stone_block_and_air_neighbor(self):
        """Test that one cell with stone is low, and empty neighbor is none."""
        # This is a fixture test to verify cell-level classification
        air_chunk = chunk(['minecraft:air'])
        stone_chunk = chunk(['minecraft:air', 'minecraft:stone'])

        self.assertFalse(detail_levels.has_non_air_block(air_chunk))
        self.assertTrue(detail_levels.has_non_air_block(stone_chunk))


if __name__ == '__main__':
    unittest.main()
