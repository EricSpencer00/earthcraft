"""Expand a closed staging save from verified tiles without rewriting chunk NBT.

Existing chunks, including entirely cleared chunks, always win. The compressed
record and timestamp are copied byte for byte. This is a staging operation;
publishing over a user's save requires a separate snapshot and session fence.
"""
import argparse
from collections import defaultdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from chicago_tiles import digest


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def records(raw):
    """Read compressed Anvil records without decompressing the block volume."""
    if not raw:
        return {}
    if len(raw) < 8192 or len(raw) % 4096:
        raise ValueError('Invalid region size')
    result, occupied = {}, set()
    for slot in range(1024):
        offset = int.from_bytes(raw[slot*4:slot*4+3], 'big')
        count = raw[slot*4+3]
        if not offset:
            if count:
                raise ValueError('Sector count without location')
            continue
        sectors = set(range(offset, offset+count))
        if offset < 2 or not count or (offset+count)*4096 > len(raw) or occupied & sectors:
            raise ValueError('Invalid or overlapping region sectors')
        occupied |= sectors
        begin = offset*4096
        length = int.from_bytes(raw[begin:begin+4], 'big')
        if not 1 < length <= count*4096-4 or raw[begin+4] not in (1, 2, 3):
            raise ValueError('Unsupported or invalid compressed chunk')
        result[slot] = (raw[begin:begin+4+length], raw[4096+slot*4:4100+slot*4])
    return result


def encode(items):
    header, payload = bytearray(8192), bytearray()
    for slot, (record, stamp) in sorted(items.items()):
        count = (len(record)+4095)//4096
        offset = 2+len(payload)//4096
        if not 0 <= slot < 1024 or not 1 <= count <= 255 or offset >= 2**24 or len(stamp) != 4:
            raise ValueError('Invalid output record')
        header[slot*4:slot*4+3] = offset.to_bytes(3, 'big')
        header[slot*4+3] = count
        header[4096+slot*4:4100+slot*4] = stamp
        payload.extend(record)
        payload.extend(bytes(count*4096-len(record)))
    return bytes(header+payload)


def merge(original, incoming):
    combined = dict(original)
    for slot, value in incoming.items():
        combined.setdefault(slot, value)
    return combined


def atomic(path, raw):
    temporary = Path(str(path)+'.partial')
    with temporary.open('wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def remap(path, mappings):
    path = Path(path)
    for old, new in mappings:
        if path.is_relative_to(old):
            return new/path.relative_to(old)
    return path


def inputs(plan_dir, mappings=()):
    plan = json.loads((plan_dir/'plan.json').read_text())
    tiles = {tile['id']: tile for tile in plan['tiles']}
    db = sqlite3.connect(f'file:{plan_dir / "jobs.sqlite"}?mode=ro', uri=True)
    try:
        rows = db.execute("SELECT tile,evidence,evidence_sha256 FROM jobs WHERE stage=1 AND state='complete'").fetchall()
    finally:
        db.close()
    if {row[0] for row in rows} != set(tiles):
        raise ValueError('All planned geometry tiles must be complete before assembly')
    groups = defaultdict(list)
    for identifier, receipt, expected in rows:
        path = remap(receipt, mappings)
        if sha(path) != expected:
            raise ValueError(f'Geometry receipt changed: {identifier}')
        proof = json.loads(path.read_text())
        if proof.get('result') != 'pass' or proof.get('tile') != identifier:
            raise ValueError('Geometry receipt rejected or misplaced')
        world = path.parent/'world'
        if sha(world/'earthcraft.json') != proof['world_manifest_sha256']:
            raise ValueError('World manifest changed')
        manifest = json.loads((world/'earthcraft.json').read_text())
        tile, frame = tiles[identifier], plan['frame']
        if (manifest['source']['crs'] != frame['crs'] or
                manifest['vertical_offset_m'] != frame['vertical_offset_m'] or
                manifest['world_offset_xz'] != tile['world_offset_xz'] or
                manifest['source']['size'] != tile['size'] or
                manifest['source']['west'] != tile['west'] or
                manifest['source']['north'] != tile['north'] or
                manifest['dimension_height'] != frame['dimension_height'] or
                manifest['dimension_min_y'] != frame['dimension_min_y']):
            raise ValueError('Source tile uses a different coordinate frame')
        for name, expected_hash in proof['regions'].items():
            if not re.fullmatch(r'r\.-?\d+\.-?\d+\.mca', name):
                raise ValueError('Invalid region name')
            groups[name].append((world/'region'/name, expected_hash, tile))
    return plan, groups


def expand(world, plans, progress, mappings=()):
    groups, all_tiles = defaultdict(list), []
    coordinate_frame = None
    fingerprints = []
    for directory in plans:
        plan, sources = inputs(directory, mappings)
        if coordinate_frame is not None and coordinate_frame != plan['frame']:
            raise ValueError('Expansion plans use different frames')
        coordinate_frame = plan['frame']
        fingerprints.append(digest(plan))
        all_tiles.extend(plan['tiles'])
        for name, entries in sources.items():
            groups[name].extend(entries)
    original_coverage = json.loads((world/'city-coverage.json').read_text())
    if original_coverage['frame'] != coordinate_frame:
        raise ValueError('Staging save uses a different frame')
    identity = {'plans': fingerprints, 'policy': 'preserve_every_existing_chunk_v1'}
    state = json.loads(progress.read_text()) if progress.exists() else {'request': identity, 'regions': {}}
    if state['request'] != identity:
        raise ValueError('Expansion request changed; use separate staging')
    for number, (name, sources) in enumerate(sorted(groups.items()), 1):
        target = world/'region'/name
        if name in state['regions']:
            if sha(target) != state['regions'][name]['sha256']:
                raise ValueError('Completed staging region changed')
            continue
        original = records(target.read_bytes()) if target.exists() else {}
        combined = dict(original)
        for path, expected, tile in sources:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(f'Source region changed: {path}')
            incoming = records(raw)
            rx, rz = map(int, name.split('.')[1:3])
            x, z = tile['world_offset_xz']
            expected_x = max(0, min(rx*32+32, (x+tile['size'])//16)-max(rx*32, x//16))
            expected_z = max(0, min(rz*32+32, (z+tile['size'])//16)-max(rz*32, z//16))
            if len(incoming) != expected_x*expected_z:
                raise ValueError('Source region is missing owned tile chunks')
            for slot in incoming:
                cx, cz = rx*32+slot%32, rz*32+slot//32
                if not (x <= cx*16 < x+tile['size'] and z <= cz*16 < z+tile['size']):
                    raise ValueError('Chunk record lies outside its owned tile')
            combined = merge(combined, incoming)
        if combined != original:
            atomic(target, encode(combined))
        actual = records(target.read_bytes())
        if actual != combined or any(actual[slot] != value for slot, value in original.items()):
            raise ValueError('Staging changed original or incoming chunk bytes')
        state['regions'][name] = {'sha256': sha(target), 'preserved_chunks': len(original),
                                  'added_chunks': len(combined)-len(original), 'chunks': len(combined)}
        atomic(progress, json.dumps(state).encode())
        if number % 25 == 0:
            print(json.dumps({'assembled_regions': number, 'total_regions': len(groups)}), flush=True)
    coverage = dict(original_coverage)
    coverage['tiles'] = dict(coverage['tiles'])
    for tile in all_tiles:
        coverage['tiles'][tile['id']] = {'id': tile['id'], 'world_offset_xz': tile['world_offset_xz'],
            'size_m': tile['size'], 'status': 'geometry_verified_existing_chunks_preserved'}
    coverage.update(generated_tiles=len(coverage['tiles']),
                    generated_tile_area_m2=sum(t['size_m']**2 for t in coverage['tiles'].values()),
                    full_city_appearance_verified=False, physical_accuracy_verified=False)
    from world_border import bounds_for_tiles, update_world_border
    update_world_border(world, bounds_for_tiles(coverage['tiles'].values()))
    atomic(world/'city-coverage.json', json.dumps(coverage, indent=2).encode())
    result = {'request': identity, 'assembled_regions': len(groups), 'tiles': len(all_tiles),
        'added_chunks': sum(v['added_chunks'] for v in state['regions'].values()),
        'preserved_chunks': sum(v['preserved_chunks'] for v in state['regions'].values()),
        'compressed_existing_chunk_bytes_preserved': True, 'player_files_changed': False,
        'game_load_verified': False, 'physical_accuracy_verified': False}
    atomic(world/'region-expansion.json', json.dumps(result, indent=2).encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--plan', type=Path, action='append', required=True)
    parser.add_argument('--progress', type=Path, required=True)
    parser.add_argument('--map-root', nargs=2, type=Path, action='append', default=[])
    args = parser.parse_args()
    with (args.world/'session.lock').open('a+b') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(expand(args.world, args.plan, args.progress, args.map_root), indent=2))


if __name__ == '__main__':
    main()
