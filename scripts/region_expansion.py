"""Expand a closed staging save from verified tiles without rewriting chunk NBT.

By default existing chunks, including entirely cleared chunks, always win.
An explicit ownership pass can fill only previously ungenerated void chunks.
The compressed record and timestamp are copied byte for byte. This is a staging operation;
publishing over a user's save requires a separate snapshot and session fence.
"""
import argparse
from collections import defaultdict
import fcntl
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import zlib
import nbtlib as n

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


def empty_unowned_record(record):
    """Conservatively identify a void chunk containing no blocks or entities."""
    raw = record[0]
    mode, payload = raw[4], raw[5:]
    decoded = zlib.decompress(payload) if mode == 2 else gzip.decompress(payload) if mode == 1 else payload
    tag = n.File.parse(io.BytesIO(decoded))
    if tag.get('block_entities') or tag.get('TileEntities') or tag.get('entities'):
        return False
    air = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
    # A stale unused non-air palette is preserved conservatively as an edit.
    return all(str(entry['Name']) in air for section in tag.get('sections', [])
               if 'block_states' in section for entry in section['block_states']['palette'])


def materialize_unowned_empty(world, baseline, plans, ownership, progress_paths, mappings=()):
    """Fill previously visited void chunks while retaining cleared geography.

The default expansion preserves all saved chunks. This explicit second pass
admits only all-air chunks outside published/generated ownership. It never
changes a known generated chunk or any chunk with non-air blocks or entities.
"""
    owned = json.loads(ownership.read_text())
    protected = set(owned['chunks'])
    coverage = json.loads((baseline/'city-coverage.json').read_text())
    if owned['frame'] != coverage['frame']:
        raise ValueError('Frozen ownership uses a different coordinate frame')
    for tile in coverage['tiles'].values():
        x, z = tile['world_offset_xz']
        protected.update(f'{cx},{cz}' for cx in range(x//16, (x+tile['size_m'])//16)
                         for cz in range(z//16, (z+tile['size_m'])//16))
    sources, states = {}, []
    for directory, progress in zip(plans, progress_paths, strict=True):
        plan = json.loads((directory/'plan.json').read_text())
        state = json.loads(progress.read_text())
        if state['request']['plans'] != [digest(plan)] or plan['frame'] != coverage['frame']:
            raise ValueError('Ownership materialization plan/frame changed')
        db = sqlite3.connect(f'file:{directory / "jobs.sqlite"}?mode=ro', uri=True)
        try:
            rows = db.execute("SELECT tile,evidence,evidence_sha256 FROM jobs WHERE stage=1 AND state='complete'").fetchall()
        finally:
            db.close()
        if ({row[0] for row in rows} != {tile['id'] for tile in plan['tiles']} or
                any(t['size'] != 256 for t in plan['tiles'])):
            raise ValueError('Complete 256 m plans required for ownership materialization')
        sources.update({tile: (remap(path, mappings), expected) for tile, path, expected in rows})
        states.append((progress, state))
    source_cache, changed = {}, {}
    for path in sorted((baseline/'region').glob('r.*.*.mca')):
        original = records(path.read_bytes())
        target = world/'region'/path.name
        actual = records(target.read_bytes())
        rx, rz = map(int, path.name.split('.')[1:3])
        replacements = []
        for slot, value in original.items():
            cx, cz = rx*32+slot%32, rz*32+slot//32
            key, tile = f'{cx},{cz}', f'{cx//16}_{cz//16}'
            if key in protected or tile not in sources or not empty_unowned_record(value):
                if actual.get(slot) != value:
                    raise ValueError('Known geography or player chunk changed')
                continue
            receipt, expected = sources[tile]
            if tile not in source_cache:
                if sha(receipt) != expected:
                    raise ValueError('Geometry receipt changed during void materialization')
                proof = json.loads(receipt.read_text())
                if proof.get('result') != 'pass' or proof.get('tile') != tile:
                    raise ValueError('Geometry receipt rejected or misplaced')
                region = receipt.parent/'world/region'/path.name
                raw = region.read_bytes()
                if hashlib.sha256(raw).hexdigest() != proof['regions'][path.name]:
                    raise ValueError('Source region changed during void materialization')
                # Only one immutable source tile stays decoded at a time.
                source_cache = {tile: records(raw)}
            incoming = source_cache[tile][slot]
            if actual.get(slot) not in (value, incoming):
                raise ValueError('Unowned void chunk changed after staging')
            actual[slot] = incoming
            replacements.append([cx, cz])
        if replacements:
            atomic(target, encode(actual))
            if records(target.read_bytes()) != actual:
                raise ValueError('Void materialization did not read back exactly')
            changed[path.name] = replacements
            for progress, state in states:
                if path.name in state['regions']:
                    state['regions'][path.name]['sha256'] = sha(target)
                    state['regions'][path.name]['unowned_empty_chunks_materialized'] = replacements
                    atomic(progress, json.dumps(state).encode())
            print(json.dumps({'materialized_void_region': path.name, 'chunks': len(replacements)}), flush=True)
    result = {'ownership_sha256': sha(ownership), 'materialized_unowned_empty_chunks':
              sum(len(value) for value in changed.values()), 'regions': changed,
              'known_generated_and_nonempty_chunks_preserved': True}
    atomic(world/'void-materialization.json', json.dumps(result, indent=2).encode())
    return result


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
    parser.add_argument('--baseline', type=Path,
                        help='Verified original snapshot for an explicit unowned-void materialization pass')
    parser.add_argument('--ownership', type=Path, help='Frozen published chunk ownership JSON')
    parser.add_argument('--additional-progress', type=Path, action='append', default=[])
    args = parser.parse_args()
    with (args.world/'session.lock').open('a+b') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.baseline or args.ownership:
            if not args.baseline or not args.ownership:
                parser.error('--baseline and --ownership must be supplied together')
            result = materialize_unowned_empty(args.world, args.baseline, args.plan,
                args.ownership, [args.progress, *args.additional_progress], args.map_root)
        else:
            result = expand(args.world, args.plan, args.progress, args.map_root)
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
