"""Immutable, spatially sharded Minecraft saves; packaging runs on a worker.

Stage uses APFS clones where available and holds Minecraft's POSIX save lock.
Only the staged save is transferred to the packaging worker. No game jars,
launcher configuration, authentication cache, or raw survey cache is selected.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile

SCHEMA = 'earthcraft-world-snapshot-v1'
REGION = re.compile(r'r\.(-?\d+)\.(-?\d+)\.mca$')
EXTERNAL_CHUNK = re.compile(r'c\.(-?\d+)\.(-?\d+)\.mcc$')
EXCLUDED = {'session.lock', '.DS_Store'}
EXCLUDED_TREES = {'pre-expansion-evidence'}
FORBIDDEN = {'launcher_accounts.json', 'launcher_profiles.json', '.env', 'token'}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024**2), b''):
            value.update(data)
    return value.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    temporary.replace(path)


@contextmanager
def save_lock(world):
    """Use the same POSIX record lock as Minecraft Java, never flock."""
    with (Path(world)/'session.lock').open('r+b') as lock:
        try:
            fcntl.lockf(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Minecraft has this world open; Save and Quit to Title first') from error
        try:
            yield
        finally:
            fcntl.lockf(lock.fileno(), fcntl.LOCK_UN)


def inventory(world):
    world = Path(world).resolve()
    if not (world/'level.dat').is_file():
        raise ValueError('A Minecraft save with level.dat is required')
    selected, excluded = [], []
    for root, directories, files in os.walk(world, followlinks=False):
        for name in directories[:]:
            path = Path(root)/name
            if path.is_symlink():
                raise ValueError('Symlink inside save: '+str(path.relative_to(world)))
            if name in EXCLUDED_TREES or name.startswith('.'):
                directories.remove(name)
                excluded.append(str(path.relative_to(world))+'/')
        for name in sorted(files):
            path = Path(root)/name
            relative = path.relative_to(world).as_posix()
            if path.is_symlink() or not path.is_file():
                raise ValueError('Nonregular save file: '+relative)
            if name in FORBIDDEN or path.suffix.lower() in ('.jar', '.key', '.pem'):
                raise ValueError('Unexpected application/credential file in save: '+relative)
            # LOD caches are derived and can contain open SQLite journals. Keep
            # the full blocks and let the installed renderer rebuild its cache.
            if (name in EXCLUDED or name.startswith('._') or
                    'distanthorizons' in name.lower() or 'distant_horizons' in name.lower()):
                excluded.append(relative)
                continue
            stat = path.stat()
            selected.append({'path': relative, 'bytes': stat.st_size,
                             'mtime_ns': stat.st_mtime_ns})
    return sorted(selected, key=lambda item: item['path']), sorted(excluded)


def clone_or_copy(source, target):
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        clone = library.clonefile
        clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        clone.restype = ctypes.c_int
        if clone(os.fsencode(source), os.fsencode(target), 0) == 0:
            return
        # Cross-volume/unsupported clones fall back to a normal file copy.
    shutil.copy2(source, target)


def stage(world, destination):
    world, destination = Path(world).resolve(), Path(destination).resolve()
    if destination == world or world in destination.parents:
        raise ValueError('Snapshot must be outside the live save')
    if destination.exists():
        raise FileExistsError('Use a new immutable snapshot directory')
    with save_lock(world):
        files, excluded = inventory(world)
        destination.mkdir(parents=True)
        for record in files:
            source, target = world/record['path'], destination/record['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            clone_or_copy(source, target)
        if inventory(world)[0] != files:
            raise RuntimeError('Save changed while its lock was held; snapshot was not admitted')
        (destination/'session.lock').write_bytes(bytes(3))
        receipt = {'schema': SCHEMA, 'state': 'staged',
                   'created_utc': datetime.now(timezone.utc).isoformat(),
                   'files': files, 'excluded': excluded,
                   'source_bytes': sum(record['bytes'] for record in files)}
        atomic_json(destination.parent/(destination.name+'.stage.json'), receipt)
    return receipt


def shard_key(relative, span=8):
    path = PurePosixPath(relative)
    match = REGION.fullmatch(path.name)
    external = EXTERNAL_CHUNK.fullmatch(path.name)
    if external and path.parent.name == 'region':
        # Oversized Anvil chunks store their payload next to the region file.
        # Keep them in the same spatial shard, including at negative positions.
        x, z = map(int, external.groups())
        match = REGION.fullmatch(f'r.{x//32}.{z//32}.mca')
    if match and path.parent.name in ('region', 'entities', 'poi'):
        dimension = path.parts[:-2]
        label = '-'.join(dimension) if dimension else 'overworld'
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', label):
            raise ValueError('Unsupported dimension path')
        x, z = map(int, match.groups())
        return f'{label}/x{x//span}_z{z//span}'
    return 'metadata'


class HashedReader:
    def __init__(self, stream):
        self.stream = stream
        self.sha = hashlib.sha256()
        self.bytes = 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.sha.update(data)
        self.bytes += len(data)
        return data


def region_chunks(path):
    if path.parent.name != 'region' or not REGION.fullmatch(path.name):
        return None
    length = path.stat().st_size
    if length == 0:
        return 0
    if length < 8192 or length % 4096:
        raise ValueError('Incomplete Anvil region: '+path.name)
    with path.open('rb') as stream:
        header = stream.read(4096)
    count = 0
    for offset in range(0, 4096, 4):
        sector = int.from_bytes(header[offset:offset+3], 'big')
        size = header[offset+3]
        if not sector and not size:
            continue
        if sector < 2 or not size or (sector+size)*4096 > length:
            raise ValueError('Anvil chunk points outside region: '+path.name)
        count += 1
    return count


def package(world, output, *, snapshot_id, span=8, reserve_gib=20):
    world, output = Path(world).resolve(), Path(output).resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', snapshot_id):
        raise ValueError('Simple snapshot identifier required')
    if not 1 <= span <= 16:
        raise ValueError('Region span must be between 1 and 16')
    if output == world or world in output.parents:
        raise ValueError('Package output must be outside the save')
    output.mkdir(parents=True, exist_ok=True)
    with save_lock(world):
        files, excluded = inventory(world)
        identity = {'schema': SCHEMA, 'snapshot_id': snapshot_id,
                    'span': span, 'files': files, 'excluded': excluded}
        plan_path = output/'pack-plan.local.json'
        if plan_path.exists() and json.loads(plan_path.read_text()) != identity:
            raise ValueError('Immutable snapshot input differs from interrupted package')
        atomic_json(plan_path, identity)
        groups = defaultdict(list)
        for record in files:
            groups[shard_key(record['path'], span)].append(record)
        state_path = output/'pack-state.local.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        for key, members in sorted(groups.items()):
            if key in state:
                previous = state[key]
                archive = output/previous['path']
                if not archive.exists() or digest(archive) != previous['sha256']:
                    raise ValueError('Completed shard changed: '+key)
                continue
            if shutil.disk_usage(output).free < reserve_gib*2**30:
                raise RuntimeError('Worker storage reserve reached')
            relative = 'shards/'+key+'.tar.gz'
            target = output/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name+'.partial')
            records = []
            with partial.open('wb') as stream, gzip.GzipFile(
                    fileobj=stream, mode='wb', compresslevel=1, mtime=0, filename='') as compressed:
                with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                    for member in members:
                        source = world/member['path']
                        before = source.stat()
                        if (before.st_size, before.st_mtime_ns) != (member['bytes'], member['mtime_ns']):
                            raise RuntimeError('Staged save changed during packaging')
                        info = tarfile.TarInfo(member['path'])
                        info.size = member['bytes']
                        info.mode = 0o644
                        with source.open('rb') as handle:
                            reader = HashedReader(handle)
                            archive.addfile(info, reader)
                        after = source.stat()
                        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                            raise RuntimeError('Staged save changed during packaging')
                        record = {'path': member['path'], 'bytes': reader.bytes,
                                  'sha256': reader.sha.hexdigest()}
                        count = region_chunks(source)
                        if count is not None:
                            record['minecraft_chunks'] = count
                        records.append(record)
            partial.replace(target)
            state[key] = {'key': key, 'path': relative, 'bytes': target.stat().st_size,
                          'sha256': digest(target), 'files': records}
            atomic_json(state_path, state)
            print(json.dumps({'state': 'packing', 'shards_done': len(state),
                              'shards_total': len(groups), 'compressed_bytes': sum(s['bytes'] for s in state.values())}), flush=True)
        if inventory(world)[0] != files:
            raise RuntimeError('Staged save changed; package was not admitted')
        quality_path = world/'regional-quality.json'
        quality = Counter(item['quality'] for item in json.loads(quality_path.read_text())['tiles'].values()) if quality_path.exists() else {}
        manifest = {'schema': SCHEMA, 'snapshot_id': snapshot_id, 'state': 'complete',
                    'created_utc': datetime.now(timezone.utc).isoformat(),
                    'world_name': 'Earthcraft', 'minecraft_version': '1.21.10',
                    'metres_per_block': 1, 'region_span': span,
                    'source_bytes': sum(f['bytes'] for f in files),
                    'compressed_bytes': sum(s['bytes'] for s in state.values()),
                    'file_count': len(files), 'excluded': excluded,
                    'installed_quality_tiles': dict(quality),
                    'minecraft_chunks': sum(f.get('minecraft_chunks', 0) for s in state.values() for f in s['files']),
                    'shards': [state[key] for key in sorted(state)],
                    'coverage_note': 'Complete snapshot of the generated save; Chicagoland coverage is partial. The entire Earth is not generated.'}
        frame_path = world/'city-coverage.json'
        if frame_path.exists():
            manifest['world_frame'] = json.loads(frame_path.read_text()).get('frame')
        atomic_json(output/'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    inspect = sub.add_parser('inventory')
    inspect.add_argument('--world', type=Path, required=True)
    staging = sub.add_parser('stage')
    staging.add_argument('--world', type=Path, required=True)
    staging.add_argument('--destination', type=Path, required=True)
    packing = sub.add_parser('pack')
    packing.add_argument('--world', type=Path, required=True)
    packing.add_argument('--output', type=Path, required=True)
    packing.add_argument('--snapshot-id', required=True)
    packing.add_argument('--region-span', type=int, default=8)
    packing.add_argument('--reserve-gib', type=float, default=20)
    args = parser.parse_args()
    if args.operation == 'inventory':
        files, excluded = inventory(args.world)
        result = {'files': len(files), 'bytes': sum(f['bytes'] for f in files), 'excluded': excluded}
    elif args.operation == 'stage':
        result = stage(args.world, args.destination)
        result = {k: v for k, v in result.items() if k != 'files'}
    else:
        result = package(args.world, args.output, snapshot_id=args.snapshot_id,
                         span=args.region_span, reserve_gib=args.reserve_gib)
        result = {k: v for k, v in result.items() if k not in ('shards', 'world_frame')}
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
