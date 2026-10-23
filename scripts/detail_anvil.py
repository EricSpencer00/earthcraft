"""Minimal Java 1.21.x Anvil chunk codec for the street-detail layer.

Unlike the generator helpers (which assume default-state palettes), this
codec round-trips game-saved chunks: block states with properties, light-only
sections, biomes and every unrelated chunk tag are preserved untouched.
Only the sections an edit touches are re-encoded.

Packed long arrays follow the 1.16+ layout: entries never span two longs,
``bits = max(4, ceil(log2(len(palette))))`` for block states, a one-entry
palette has no ``data`` array, and heightmaps use ``ceil(log2(height + 1))``
bits per column.
"""
import copy
import io
import math
import re
import zlib
from pathlib import Path

import nbtlib as n
import numpy as np

SECTOR = 4096
AIR = 'minecraft:air'
AIR_LIKE = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
# Blocks that neither block motion nor count for OCEAN_FLOOR. Only the
# states this layer itself writes, plus common vanilla plants, need to be
# listed; Minecraft keeps heightmaps current after any later block update.
NON_BLOCKING = {'minecraft:short_grass', 'minecraft:tall_grass', 'minecraft:fern',
                'minecraft:large_fern', 'minecraft:dandelion', 'minecraft:poppy',
                'minecraft:white_carpet', 'minecraft:light_gray_carpet',
                'minecraft:gray_carpet', 'minecraft:yellow_carpet', 'minecraft:black_carpet',
                'minecraft:white_carpet', 'minecraft:rail', 'minecraft:torch',
                'minecraft:oak_sign', 'minecraft:oak_wall_sign', 'minecraft:oak_button',
                'minecraft:stone_button', 'minecraft:light_weighted_pressure_plate',
                'minecraft:heavy_weighted_pressure_plate', 'minecraft:stone_pressure_plate'}
FLUIDS = {'minecraft:water', 'minecraft:lava'}
REGION_NAME = re.compile(r'r\.(-?\d+)\.(-?\d+)\.mca')


def state_key(entry):
    """Canonical ``name[k=v,...]`` text for an NBT palette entry."""
    name = str(entry['Name'])
    props = entry.get('Properties')
    if not props:
        return name
    return name + '[' + ','.join(f'{k}={props[k]}' for k in sorted(props)) + ']'


def parse_state(key):
    """Inverse of :func:`state_key`; validates the canonical form."""
    match = re.fullmatch(r'([a-z0-9_.-]+:[a-z0-9_/.-]+)(?:\[([a-z0-9_=,.-]*)\])?', key)
    if not match:
        raise ValueError(f'Invalid block state {key!r}')
    name, body = match.groups()
    props = {}
    if body:
        for item in body.split(','):
            k, _, v = item.partition('=')
            if not k or not v or k in props:
                raise ValueError(f'Invalid block state property in {key!r}')
            props[k] = v
    return name, props


def canonical(key):
    name, props = parse_state(key)
    return name if not props else name + '[' + ','.join(f'{k}={props[k]}' for k in sorted(props)) + ']'


def state_compound(key):
    name, props = parse_state(key)
    entry = n.Compound({'Name': n.String(name)})
    if props:
        entry['Properties'] = n.Compound({k: n.String(v) for k, v in sorted(props.items())})
    return entry


def block_name(key):
    return key.split('[', 1)[0]


def pack(values, bits):
    per_long = 64 // bits
    values = np.asarray(values, dtype=np.uint64).ravel()
    if len(values) and int(values.max()) >= 1 << bits:
        raise ValueError('Packed value exceeds bit width')
    padded = np.zeros(math.ceil(len(values) / per_long) * per_long, dtype=np.uint64)
    padded[:len(values)] = values
    matrix = padded.reshape(-1, per_long)
    words = np.bitwise_or.reduce(matrix << (np.arange(per_long, dtype=np.uint64) * np.uint64(bits)), axis=1)
    return n.LongArray(words.view(np.int64))


def unpack(words, bits, count):
    data = np.asarray(words, dtype=np.int64).view(np.uint64)
    per_long = 64 // bits
    if len(data) != math.ceil(count / per_long):
        raise ValueError(f'Packed array has {len(data)} longs; expected {math.ceil(count / per_long)}')
    index = np.arange(count)
    shift = ((index % per_long) * bits).astype(np.uint64)
    return ((data[index // per_long] >> shift) & np.uint64((1 << bits) - 1)).astype(np.int64)


def block_bits(palette_size):
    return max(4, (palette_size - 1).bit_length())


def decode_states(states):
    """Return (palette keys, int array[4096] in YZX order) for a block_states tag."""
    palette = [state_key(p) for p in states['palette']]
    if not palette:
        raise ValueError('Empty block palette')
    if len(palette) == 1:
        return palette, np.zeros(4096, np.int64)
    if 'data' not in states:
        raise ValueError('Multi-entry palette without packed data')
    values = unpack(states['data'], block_bits(len(palette)), 4096)
    if values.max() >= len(palette):
        raise ValueError('Packed block index outside palette')
    return palette, values


def encode_states(keys):
    """Encode a length-4096 sequence of canonical keys (YZX order)."""
    keys = np.asarray(keys, dtype=object).ravel()
    if keys.shape != (4096,):
        raise ValueError('A section has exactly 4096 block states')
    # Stable first-occurrence palette order keeps output deterministic.
    unique, first, inverse = np.unique(keys.astype(str), return_index=True, return_inverse=True)
    order = np.argsort(first, kind='stable')
    rank = np.empty(len(order), np.int64)
    rank[order] = np.arange(len(order))
    palette = [str(unique[i]) for i in order]
    states = n.Compound({'palette': n.List[n.Compound]([state_compound(k) for k in palette])})
    if len(palette) > 1:
        states['data'] = pack(rank[inverse], block_bits(len(palette)))
    return states


def read_region(path):
    """Parse every chunk: {(cx, cz): {'tag': Compound, 'timestamp': int}}."""
    path = Path(path)
    match = REGION_NAME.fullmatch(path.name)
    if not match:
        raise ValueError(f'Not a region file name: {path.name}')
    rx, rz = map(int, match.groups())
    raw = path.read_bytes()
    if not raw:
        return {}
    if len(raw) < 2 * SECTOR or len(raw) % SECTOR:
        raise ValueError(f'Invalid Anvil region length: {path.name}')
    result, occupied = {}, set()
    for index in range(1024):
        offset = int.from_bytes(raw[index * 4:index * 4 + 3], 'big') * SECTOR
        count = raw[index * 4 + 3]
        if not offset:
            continue
        if offset < 2 * SECTOR or not count or offset + count * SECTOR > len(raw):
            raise ValueError(f'Anvil chunk sector outside region: {path.name}/{index}')
        sectors = set(range(offset // SECTOR, offset // SECTOR + count))
        if occupied & sectors:
            raise ValueError('Overlapping Anvil chunk sectors')
        occupied |= sectors
        length = int.from_bytes(raw[offset:offset + 4], 'big')
        if not 1 < length <= count * SECTOR - 4:
            raise ValueError('Invalid compressed chunk length')
        if raw[offset + 4] != 2:
            raise ValueError('Only zlib (type 2) chunks are supported')
        tag = n.File.parse(io.BytesIO(zlib.decompress(raw[offset + 5:offset + 4 + length])))
        cx, cz = int(tag['xPos']), int(tag['zPos'])
        if (cx >> 5, cz >> 5) != (rx, rz) or (cx & 31) + (cz & 31) * 32 != index:
            raise ValueError(f'Chunk {cx},{cz} misplaced in {path.name}')
        timestamp = int.from_bytes(raw[SECTOR + index * 4:SECTOR + index * 4 + 4], 'big')
        result[(cx, cz)] = {'tag': tag, 'timestamp': timestamp,
                            'payload': raw[offset:offset + 4 + length], 'changed': False}
    return result


def encode_region(chunks):
    """Serialize {(cx, cz): {'tag', 'timestamp', ['payload', 'changed']}} into Anvil bytes.

    Unchanged chunks keep their original compressed payload byte-for-byte.
    """
    header = bytearray(2 * SECTOR)
    body = bytearray()
    for (cx, cz) in sorted(chunks, key=lambda k: ((k[1] & 31) * 32 + (k[0] & 31))):
        record = chunks[(cx, cz)]
        if record.get('payload') is not None and not record.get('changed', True):
            sector = bytes(record['payload'])
        else:
            stream = io.BytesIO()
            n.File(record['tag']).write(stream)
            payload = zlib.compress(stream.getvalue(), 6)
            sector = (len(payload) + 1).to_bytes(4, 'big') + b'\x02' + payload
        sector += b'\0' * (-len(sector) % SECTOR)
        count = len(sector) // SECTOR
        if count > 255:
            raise ValueError('Chunk exceeds 255 sectors; external .mcc chunks are not supported')
        slot = (cx & 31) + (cz & 31) * 32
        header[slot * 4:slot * 4 + 3] = (2 + len(body) // SECTOR).to_bytes(3, 'big')
        header[slot * 4 + 3] = count
        header[SECTOR + slot * 4:SECTOR + slot * 4 + 4] = int(record.get('timestamp', 0)).to_bytes(4, 'big')
        body.extend(sector)
    return bytes(header + body)


_FLAGS = {}


def _heightmap_flags(key):
    """(solid, motion-blocking, leaves) of one block state key, memoised."""
    flags = _FLAGS.get(key)
    if flags is None:
        name = block_name(key)
        solid = name not in AIR_LIKE
        flags = _FLAGS[key] = (solid, solid and name not in NON_BLOCKING, name.endswith('_leaves'))
    return flags


class ChunkVolume:
    """Lazy per-section decoded view of one chunk's block states."""

    def __init__(self, tag, min_y=-64, height=None, grow=False):
        """height: the dimension's block height (heightmap width). grow: create absent
        sections inside it, for sparse worlds that store only occupied sections."""
        self.tag = tag
        self.sections = {int(s['Y']): s for s in tag['sections']}
        self.min_y = int(tag.get('yPos', min_y // 16)) * 16
        self.height = height
        self.grow = grow and height is not None
        self._decoded = {}
        self.dirty = set()

    def section(self, sy):
        if sy not in self._decoded:
            section = self.sections.get(sy)
            if section is None or 'block_states' not in section:
                self._decoded[sy] = np.full(4096, AIR, dtype=object)
            else:
                palette, values = decode_states(section['block_states'])
                self._decoded[sy] = np.asarray(palette, dtype=object)[values]
        return self._decoded[sy]

    def get(self, x, y, z):
        return self.section(y >> 4)[((y & 15) << 8) | ((z & 15) << 4) | (x & 15)]

    def set(self, x, y, z, key):
        sy = y >> 4
        if sy not in self.sections:
            if not self.grow or not self.min_y <= y < self.min_y + self.height:
                raise ValueError(f'Section {sy} absent from chunk; refusing to extend the world height')
            self.add_section(sy)
        data = self.section(sy)
        data[((y & 15) << 8) | ((z & 15) << 4) | (x & 15)] = key
        self.dirty.add(sy)

    def add_section(self, sy):
        """An all-air section; biomes copied from the nearest existing section."""
        section = n.Compound({'Y': n.Byte(sy), 'block_states': encode_states(np.full(4096, AIR, dtype=object))})
        biomed = [y for y, s in self.sections.items() if 'biomes' in s]
        if biomed:
            section['biomes'] = copy.deepcopy(self.sections[min(biomed, key=lambda y: abs(y - sy))]['biomes'])
        self.tag['sections'] = n.List[n.Compound](sorted([*self.tag['sections'], section], key=lambda s: int(s['Y'])))
        self.sections[sy] = section
        self._decoded.pop(sy, None)

    def flush(self):
        """Re-encode dirty sections, refresh heightmaps, request relighting."""
        if not self.dirty:
            return False
        for sy in sorted(self.dirty):
            self.sections[sy]['block_states'] = encode_states(self._decoded[sy])
        self.refresh_heightmaps()
        # Stale light must not survive a block edit: Minecraft relights a
        # chunk whose isLightOn flag is cleared. Light-only sections outside
        # the block range carry nothing else and are dropped with their light.
        kept = []
        for section in self.tag['sections']:
            section.pop('BlockLight', None)
            section.pop('SkyLight', None)
            if 'block_states' in section or 'biomes' in section:
                kept.append(section)
        self.tag['sections'] = n.List[n.Compound](kept)
        self.tag['isLightOn'] = n.Byte(0)
        self.dirty.clear()
        return True

    def column_tops(self):
        """Highest Y+1-min_y per column for each heightmap rule, shape (4, 16, 16)."""
        ys = sorted(sy for sy, s in self.sections.items() if 'block_states' in s)
        tops = np.zeros((4, 16, 16), np.int64)
        for sy in reversed(ys):
            # A section holds a handful of distinct states: classify those once (memoised across chunks),
            # not each of its 4096 cells (block_name per cell was ~12 M calls a tile).
            lookup = {}
            codes = np.fromiter((lookup.setdefault(k, len(lookup)) for k in self.section(sy)), np.int32, 4096)
            flags = np.array([_heightmap_flags(k) for k in lookup], bool)[codes].reshape(16, 16, 16, 3)
            solid, blocking, leaves = flags[..., 0], flags[..., 1], flags[..., 2]
            fluid = np.array([block_name(k) in FLUIDS for k in lookup], bool)[codes].reshape(16, 16, 16)
            rules = [solid, blocking, blocking & ~leaves, blocking & ~fluid]
            for i, mask in enumerate(rules):
                any_y = mask.any(axis=0)
                top_index = 15 - np.argmax(mask[::-1], axis=0)
                value = sy * 16 + top_index + 1 - self.min_y
                update = any_y & (tops[i] == 0)
                tops[i][update] = value[update]
            if (tops > 0).all():
                break
        return tops

    def refresh_heightmaps(self):
        maps = self.tag.get('Heightmaps')
        if maps is None:
            return
        height = self.height or 16 * len([s for s in self.sections.values() if 'block_states' in s])
        bits = (height).bit_length()
        tops = self.column_tops()
        names = {'WORLD_SURFACE': 0, 'MOTION_BLOCKING': 1, 'MOTION_BLOCKING_NO_LEAVES': 2, 'OCEAN_FLOOR': 3}
        for key in list(maps.keys()):
            if key in names:
                # Column index is z*16+x, matching the vanilla layout.
                maps[key] = pack(tops[names[key]].reshape(256), bits)


def region_of_block(x, z):
    return x >> 9, z >> 9


def chunk_of_block(x, z):
    return x >> 4, z >> 4
