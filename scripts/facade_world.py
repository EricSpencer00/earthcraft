"""Read only requested native chunks; missing geometry is never treated as air."""
from functools import lru_cache
import io
from pathlib import Path
import zlib

import nbtlib


EMPTY = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
COLORS = 'white orange magenta light_blue yellow lime pink gray light_gray cyan purple blue brown green red black'.split()
MATERIALS = {f'minecraft:{c}_concrete' for c in COLORS} | {
    'minecraft:stone', 'minecraft:stone_bricks', 'minecraft:bricks',
    'minecraft:sandstone', 'minecraft:clay', 'minecraft:iron_block',
}


class BlockReader:
    def __init__(self, world):
        self.world = Path(world)

    @lru_cache(maxsize=2)
    def region(self, rx, rz):
        path = self.world/'region'/f'r.{rx}.{rz}.mca'
        if not path.is_file():
            return None
        raw = path.read_bytes()
        if len(raw) < 8192 or len(raw) % 4096:
            raise ValueError('Invalid native region length')
        return raw

    @lru_cache(maxsize=128)
    def chunk(self, cx, cz):
        raw = self.region(cx//32, cz//32)
        if raw is None:
            return None
        slot = (cz % 32)*32 + cx % 32
        offset = int.from_bytes(raw[slot*4:slot*4+3], 'big')*4096
        count = raw[slot*4+3]
        if not offset:
            return None
        length = int.from_bytes(raw[offset:offset+4], 'big')
        if offset < 8192 or count == 0 or offset+count*4096 > len(raw) or not 1 < length <= count*4096-4:
            raise ValueError('Invalid native chunk sector')
        if raw[offset+4] != 2:
            raise ValueError('Unsupported native chunk compression')
        tag = nbtlib.File.parse(io.BytesIO(zlib.decompress(raw[offset+5:offset+4+length])))
        if (int(tag['xPos']), int(tag['zPos'])) != (cx, cz):
            raise ValueError('Native chunk coordinates differ from its region slot')
        return tag

    def state(self, cell):
        x, y, z = cell
        if not -64 <= y < 960:
            return None
        tag = self.chunk(x//16, z//16)
        if tag is None:
            return None
        sections = self.sections(x//16,z//16)
        section=sections.get(y//16)
        if section is None or 'block_states' not in section:
            return {'Name': 'minecraft:air'}
        state = section['block_states']
        palette = state['palette']
        index = 0
        if len(palette) > 1:
            bits = max(4, (len(palette)-1).bit_length())
            slot = (y % 16)*256+(z % 16)*16+x % 16
            index = (int(state['data'][slot//(64//bits)]) >> ((slot % (64//bits))*bits)) & ((1 << bits)-1)
        return palette[index]

    @lru_cache(maxsize=128)
    def sections(self,cx,cz):
        tag=self.chunk(cx,cz)
        result={int(s['Y']):s for s in tag['sections']}
        if len(result)!=len(tag['sections']):raise ValueError('Duplicate section')
        return result

    def name(self, cell):
        state = self.state(cell)
        return None if state is None else str(state['Name'])

    def has_entity(self, cell):
        x, _, z = cell
        tag = self.chunk(x//16, z//16)
        return tag is None or any(tuple(int(b[k]) for k in ('x', 'y', 'z')) == tuple(cell)
                                 for b in tag.get('block_entities', []))

    def support(self, x, z):
        """Dry support and two air blocks. This can be a roof, not a street route."""
        for y in range(957, -64, -1):
            name = self.name((x, y, z))
            if name is None:
                return None
            if name in EMPTY:
                continue
            if name in MATERIALS | {'minecraft:grass_block', 'minecraft:dirt', 'minecraft:sand', 'minecraft:bedrock'}:
                if self.name((x, y+1, z)) in EMPTY and self.name((x, y+2, z)) in EMPTY:
                    return [x, y, z]
            return None
        return None
