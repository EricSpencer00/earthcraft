"""Read Arnis worlds with nbtlib; inspect bounds and extract a compact playtest copy."""
import argparse, io, json, shutil, zlib
from pathlib import Path
import nbtlib


def chunks(path):
    raw = path.read_bytes()
    # Minecraft creates zero-byte placeholders for empty entity regions.
    # Expected terrain chunk counts are validated by the world verifier.
    if not raw:return
    if len(raw)<8192 or len(raw)%4096:
        raise ValueError(f'Invalid Anvil region length: {path.name}')
    occupied=set()
    for index in range(1024):
        offset = int.from_bytes(raw[index*4:index*4+3], 'big') * 4096
        if not offset:
            continue
        count=raw[index*4+3]
        if offset<8192 or not count or offset+count*4096>len(raw):
            raise ValueError(f'Anvil chunk sector outside region: {path.name}/{index}')
        sectors=set(range(offset//4096,offset//4096+count))
        if occupied&sectors:raise ValueError('Overlapping Anvil chunk sectors')
        occupied|=sectors
        length = int.from_bytes(raw[offset:offset+4], 'big')
        if not 1<length<=count*4096-4:raise ValueError('Invalid Anvil compressed chunk length')
        if raw[offset+4] != 2:
            raise ValueError('Expected zlib-compressed Arnis chunks')
        tag = nbtlib.File.parse(io.BytesIO(zlib.decompress(raw[offset+5:offset+4+length])))
        yield index, tag, raw[offset:offset+raw[index*4+3]*4096]


def compact_region(source, destination, size):
    """Retain original compressed sectors for chunks inside the selected square."""
    original = source.read_bytes()
    header = bytearray(8192)
    payload = bytearray()
    retained = 0
    for index, tag, sector in chunks(source):
        x, z = int(tag['xPos']), int(tag['zPos'])
        if not (0 <= x < size//16 and 0 <= z < size//16):
            continue
        offset = 2 + len(payload)//4096
        header[index*4:index*4+3] = offset.to_bytes(3, 'big')
        header[index*4+3] = len(sector)//4096
        header[4096+index*4:4100+index*4] = original[4096+index*4:4100+index*4]
        payload.extend(sector)
        retained += 1
    destination.write_bytes(header+payload)
    return retained


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('world', type=Path)
    parser.add_argument('--compact-copy', type=Path)
    args = parser.parse_args()
    meta = json.loads((args.world/'metadata.json').read_text())
    level = nbtlib.load(args.world/'level.dat')['Data']
    size = int(meta['maxMcX'])+1
    assert meta['minMcX'] == meta['minMcZ'] == 0
    assert meta['maxMcZ']+1 == size and size % 16 == 0
    result = {'extent_blocks': [size,size], 'scale_setting': meta['scale'],
              'source_data_version': int(level['DataVersion']), 'chunks_before': 0,
              'game_load_verified': False, 'accuracy_verified': False}
    for region in (args.world/'region').glob('r.*.*.mca'):
        result['chunks_before'] += sum(1 for _ in chunks(region))
    if args.compact_copy:
        dst = args.compact_copy
        if dst.exists():
            raise FileExistsError(f'Refusing to overwrite {dst}')
        if shutil.disk_usage(dst.parent).free < 20*1024**3:
            raise RuntimeError('Internal free-space reserve would be at risk')
        shutil.copytree(args.world,dst)
        result['chunks_after'] = 0
        for region in (args.world/'region').glob('r.*.*.mca'):
            result['chunks_after'] += compact_region(region,dst/'region'/region.name,size)
        assert result['chunks_after'] == (size//16)**2
        # Verify preserved NBT contents through independent decompression/parsing.
        for region in (dst/'region').glob('r.*.*.mca'):
            originals={(int(t['xPos']),int(t['zPos'])):t for _,t,_ in chunks(args.world/'region'/region.name)}
            for _,tag,_ in chunks(region):
                assert tag == originals[(int(tag['xPos']),int(tag['zPos']))]
        nbt = nbtlib.load(dst/'level.dat')
        nbt['Data']['LevelName'] = nbtlib.String('Earthcraft — Water Tower 64m')
        # Creative overview, looking north toward the tower; no geometry edits.
        player = nbt['Data']['Player']
        player['Pos'] = nbtlib.List[nbtlib.Double]([32,-29,59])
        player['Rotation'] = nbtlib.List[nbtlib.Float]([180,8])
        player['abilities']['flying'] = nbtlib.Byte(1)
        nbt.save()
        result['copy_bytes'] = sum(f.stat().st_size for f in dst.rglob('*') if f.is_file())
        result['copy'] = str(dst)
        (dst/'earthcraft-inspection.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__ == '__main__':
    main()
