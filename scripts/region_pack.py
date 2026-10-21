"""Packed region files: Anvil chunk records without sector padding.

An Anvil `.mca` file gives every chunk whole 4 KiB sectors. Earthcraft chunks
compress to ~0.5 KiB, so ~89% of each published region is zero padding. A
packed region (`r.X.Z.mcp`) keeps the same chunk records byte for byte and the
original location table, so `unpack()` rebuilds the exact `.mca` (same sha256):

    0      8   magic b'ECRPACK1'
    8      4   u32 size of the original .mca in bytes
    12     4   u32 residue length (0 when every byte outside the records is zero)
    16  12288  1024 x (u32 byte offset, u32 record length, u32 timestamp)
 12304   4096  1024 x original location entry (u24 sector offset, u8 sector count)
 16400    ...  chunk records (4-byte length + compression byte + payload), in index order
   ...    ...  residue: zlib of the .mca with header and records zeroed

All integers are big-endian, like Anvil. A missing chunk has offset 0 and
length 0. Readers can Range-read the 16,400-byte header and then one record.
Generator-written regions have zero padding and no residue; a region Minecraft
has saved keeps stale bytes in free sectors, which the residue preserves.
"""
import hashlib
import struct
import sys
import zlib
from pathlib import Path

MAGIC = b'ECRPACK1'
SECTOR = 4096
CHUNKS = 1024
HEADER = 16 + CHUNKS * 12 + CHUNKS * 4
MAX_MCA_BYTES = 256 * 2**20  # Anvil's location table cannot address more


def pack(mca):
    """Return the packed form of one .mca; raise if it cannot round-trip exactly."""
    mca = bytes(mca)
    if len(mca) < 2 * SECTOR or len(mca) % SECTOR or len(mca) > MAX_MCA_BYTES:
        raise ValueError('Not a sector-aligned Anvil region')
    entries, records, at = [], [], HEADER
    for i in range(CHUNKS):
        location = mca[i * 4:i * 4 + 4]
        timestamp = struct.unpack_from('>I', mca, SECTOR + i * 4)[0]
        offset, sectors = int.from_bytes(location[:3], 'big'), location[3]
        if offset == 0 and sectors == 0:
            entries.append((0, 0, timestamp))
            continue
        start = offset * SECTOR
        if offset < 2 or sectors == 0 or start + 5 > len(mca):
            raise ValueError(f'Chunk {i} has an invalid location')
        length = struct.unpack_from('>I', mca, start)[0] + 4
        if length < 6 or length > sectors * SECTOR or start + length > len(mca):
            raise ValueError(f'Chunk {i} record does not fit its sectors')
        entries.append((at, length, timestamp))
        records.append(mca[start:start + length])
        at += length
    base = bytearray(mca)
    base[:2 * SECTOR] = bytes(2 * SECTOR)
    for (at, length, _), location in zip(entries, (mca[i * 4:i * 4 + 4] for i in range(CHUNKS))):
        if length:
            start = int.from_bytes(location[:3], 'big') * SECTOR
            base[start:start + length] = bytes(length)
    residue = zlib.compress(bytes(base), 9) if any(base) else b''
    head = bytearray(MAGIC + struct.pack('>II', len(mca), len(residue)))
    for entry in entries:
        head += struct.pack('>III', *entry)
    head += mca[:CHUNKS * 4]
    packed = bytes(head) + b''.join(records) + residue
    if unpack(packed) != mca:
        raise ValueError('Region has overlapping chunks; keep it as .mca')
    return packed


def unpack(packed):
    """Rebuild the original .mca from a packed region."""
    packed = bytes(packed)
    if len(packed) < HEADER or packed[:8] != MAGIC:
        raise ValueError('Not a packed Earthcraft region')
    size, residue = struct.unpack_from('>II', packed, 8)
    if size < 2 * SECTOR or size % SECTOR or size > MAX_MCA_BYTES or residue > len(packed) - HEADER:
        raise ValueError('Invalid packed region size')
    records_end = len(packed) - residue
    if residue:
        inflater = zlib.decompressobj()
        mca = bytearray(inflater.decompress(packed[records_end:], size + 1))
        if len(mca) != size or not inflater.eof:
            raise ValueError('Packed region residue does not match its size')
    else:
        mca = bytearray(size)
    locations = packed[16 + CHUNKS * 12:HEADER]
    mca[:CHUNKS * 4] = locations
    for i in range(CHUNKS):
        at, length, timestamp = struct.unpack_from('>III', packed, 16 + i * 12)
        struct.pack_into('>I', mca, SECTOR + i * 4, timestamp)
        offset, sectors = int.from_bytes(locations[i * 4:i * 4 + 3], 'big'), locations[i * 4 + 3]
        if not length:
            if at or offset or sectors:
                raise ValueError(f'Chunk {i} is half missing')
            continue
        start = offset * SECTOR
        if (at < HEADER or at + length > records_end or offset < 2 or
                length > sectors * SECTOR or start + sectors * SECTOR > size):
            raise ValueError(f'Chunk {i} is out of bounds')
        mca[start:start + length] = packed[at:at + length]
    return bytes(mca)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('pack', 'unpack', 'check'))
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    data = args.source.read_bytes()
    if args.mode == 'check':
        packed = pack(data)
        print(f'{args.source}: {len(data)} -> {len(packed)} bytes, rebuilds {sha256(data)}')
        sys.exit(0)
    if args.output is None:
        parser.error('output is required')
    result = pack(data) if args.mode == 'pack' else unpack(data)
    if args.output.exists():
        parser.error(f'{args.output} exists; write each file once')
    args.output.write_bytes(result)
    print(f'{args.output}: {len(result)} bytes, sha256 {sha256(result)}')
