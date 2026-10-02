"""Restore all or selected spatial shards into a new Minecraft save.

Requires only Python's standard library. Both archives and extracted files are
checked against manifest.json; the destination must be new. No coordinates,
block palettes, level.dat, or player state are transformed.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def safe_path(value):
    path = PurePosixPath(value)
    if not value or value == '.' or path.is_absolute() or '..' in path.parts or '\\' in value or str(path) != value:
        raise ValueError('Unsafe snapshot path')
    return path


def checked_shards(package, manifest, *, selected=None):
    """Validate the manifest and archive checksums before writing any output."""
    package = Path(package).resolve()
    if manifest.get('schema') != 'earthcraft-world-snapshot-v1' or manifest.get('state') != 'complete':
        raise ValueError('A completed supported snapshot is required')
    shards = manifest['shards']
    keys = {shard['key'] for shard in shards}
    paths = {shard['path'] for shard in shards}
    if len(keys) != len(shards) or len(paths) != len(shards) or 'metadata' not in keys:
        raise ValueError('Unique shards and the metadata shard are required')
    if selected is not None:
        if not set(selected) <= keys:
            raise ValueError('Unknown geographic shard')
        shards = [s for s in shards if s['key'] == 'metadata' or s['key'] in selected]
    expected = {}
    for shard in shards:
        archive_path = package/safe_path(shard['path'])
        if (not shard['path'].startswith('shards/') or not shard['path'].endswith('.tar.gz') or
                archive_path.resolve() != archive_path or not archive_path.is_file() or
                not re.fullmatch(r'[0-9a-f]{64}', shard['sha256']) or digest(archive_path) != shard['sha256']):
            raise ValueError('Shard missing or checksum differs: '+shard['key'])
        for entry in shard['files']:
            safe_path(entry['path'])
            if (entry['path'] == 'session.lock' or not isinstance(entry['bytes'], int) or
                    entry['bytes'] < 0 or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])):
                raise ValueError('Invalid snapshot member')
            if entry['path'] in expected:
                raise ValueError('Duplicate snapshot member')
            expected[entry['path']] = entry
    if selected is None and len(expected) != manifest['file_count']:
        raise ValueError('Snapshot file count differs')
    return shards, expected


def restore(package, destination, *, selected=None):
    package, destination = Path(package).resolve(), Path(destination).resolve()
    manifest = json.loads((package/'manifest.json').read_text())
    shards, expected = checked_shards(package, manifest, selected=selected)
    if destination.exists():
        raise FileExistsError('Restore into a new save directory')
    destination.mkdir(parents=True)
    extracted = set()
    for shard in shards:
        entries = {entry['path']: entry for entry in shard['files']}
        with tarfile.open(package/shard['path'], 'r|gz') as archive:
            for member in archive:
                safe_path(member.name)
                if not member.isfile() or member.name not in entries or member.name in extracted:
                    raise ValueError('Unexpected, duplicate or nonregular archive member')
                entry = entries[member.name]
                if member.size != entry['bytes']:
                    raise ValueError('Extracted member size differs')
                target = destination/member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                value = hashlib.sha256()
                with archive.extractfile(member) as source, target.open('xb') as output:
                    for data in iter(lambda: source.read(1024**2), b''):
                        value.update(data)
                        output.write(data)
                if value.hexdigest() != entry['sha256']:
                    raise ValueError('Extracted member checksum differs')
                extracted.add(member.name)
    if extracted != set(expected):
        raise ValueError('Snapshot is missing declared members')
    (destination/'session.lock').write_bytes(bytes(3))
    return {'restored_files': len(extracted), 'shards': len(shards),
            'snapshot_id': manifest['snapshot_id'], 'complete_save': selected is None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--shard', action='append', help='Repeat for selected geographic shards; metadata is always included')
    args = parser.parse_args()
    print(json.dumps(restore(args.package, args.destination, selected=args.shard)))


if __name__ == '__main__':
    main()
