from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from region_pack import HEADER, SECTOR, pack, unpack

FIXTURE = Path(__file__).resolve().parent / 'fixtures/anvil/water-tower-r.0.0.region'


def region(chunks):
    """A sparse .mca: {index: (sector offset, sector count, payload, timestamp)}."""
    size = 2 + max((o + n for o, n, _, _ in chunks.values()), default=0)
    mca = bytearray(size * SECTOR)
    for i, (offset, sectors, payload, timestamp) in chunks.items():
        mca[i * 4:i * 4 + 4] = offset.to_bytes(3, 'big') + bytes([sectors])
        struct.pack_into('>I', mca, SECTOR + i * 4, timestamp)
        record = struct.pack('>IB', len(payload) + 1, 2) + payload
        mca[offset * SECTOR:offset * SECTOR + len(record)] = record
    return bytes(mca)


class RegionPackTests(unittest.TestCase):
    def test_real_region_round_trips_byte_for_byte(self):
        mca = FIXTURE.read_bytes()
        # A Minecraft-saved region: stale bytes in free sectors travel as residue.
        self.assertEqual(unpack(pack(mca)), mca)

    def test_out_of_order_sectors_and_missing_chunks_round_trip(self):
        mca = region({0: (5, 1, b'a' * 100, 7), 31: (2, 2, b'b' * 5000, 9), 1023: (4, 1, b'c', 1)})
        packed = pack(mca)
        self.assertEqual(unpack(packed), mca)
        self.assertEqual(len(packed), HEADER + (5 + 100) + (5 + 5000) + (5 + 1))

    def test_stale_bytes_in_free_sectors_are_kept(self):
        mca = bytearray(region({0: (2, 1, b'a', 0), 1: (4, 1, b'b', 0)}))
        mca[2 * SECTOR + 100] = 1  # after a record, as Minecraft leaves them
        mca[3 * SECTOR:4 * SECTOR] = bytes(range(256)) * 16  # a whole freed sector
        self.assertEqual(unpack(pack(bytes(mca))), bytes(mca))

    def test_zero_padding_needs_no_residue(self):
        packed = pack(region({0: (2, 1, b'a', 0)}))
        self.assertEqual(struct.unpack_from('>I', packed, 12)[0], 0)

    def test_corrupt_packed_header_is_refused(self):
        packed = bytearray(pack(region({0: (2, 1, b'a', 0)})))
        struct.pack_into('>I', packed, 16 + 4, 10**6)  # record length past the end
        with self.assertRaises(ValueError):
            unpack(bytes(packed))
        with self.assertRaisesRegex(ValueError, 'Not a packed'):
            unpack(b'x' * HEADER)


if __name__ == '__main__':
    unittest.main()
