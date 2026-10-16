"""Verify a retained snapshot before releasing its redundant task staging copy.

Never targets the playable save. The local release requires the original stage
inventory, a matching completed package, and a full retained-copy hash audit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import tarfile
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_file(root, relative):
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('Unsafe snapshot path')
    target = root / p
    if any(part.is_symlink() for part in [target, *target.parents]):
        raise ValueError('Snapshot symlinks are forbidden')
    return target


def verify(root, archives_only=False):
    root = Path(root).resolve()
    manifest_path = root / 'package/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('state') != 'complete':
        raise ValueError('Completed snapshot required')
    files = []
    for shard in manifest['shards']:
        archive = safe_file(root / 'package', shard['path'])
        if archive.stat().st_size != shard['bytes'] or digest(archive) != shard['sha256']:
            raise ValueError('Retained archive changed')
        if not archives_only and (root / 'world').exists():
            for item in shard['files']:
                source = safe_file(root / 'world', item['path'])
                if source.stat().st_size != item['bytes'] or digest(source) != item['sha256']:
                    raise ValueError('Retained staged source changed')
                files.append(item['path'])
        else:
            expected = {item['path']:item for item in shard['files']}
            seen = set()
            with tarfile.open(archive, 'r|gz') as tar:
                for member in tar:
                    if not member.isfile() or member.name not in expected or member.name in seen:
                        raise ValueError('Retained archive file list differs')
                    item = expected[member.name]
                    stream = tar.extractfile(member)
                    if member.size != item['bytes'] or hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                        raise ValueError('Retained archived source changed')
                    seen.add(member.name); files.append(member.name)
            if seen != expected.keys(): raise ValueError('Incomplete retained archive')
        print(json.dumps({'verified_shard':shard.get('key', shard['path']), 'source_files':len(files)}), flush=True)
    if len(files) != manifest['file_count'] or len(set(files)) != len(files):
        raise ValueError('Snapshot inventory differs')
    return {'schema': 'earthcraft.snapshot-retention.v1', 'verified_at': time.time(),
            'host': socket.gethostname(), 'retained_root': str(root),
            'manifest_sha256': digest(manifest_path), 'file_count': len(files),
            'archive_count': len(manifest['shards']), 'source_bytes': manifest['source_bytes'],
            'all_archives_verified': True, 'all_source_files_verified': True,
            'source_storage': 'world_and_archives' if not archives_only and (root / 'world').exists() else 'verified_archive_contents'}


def release_stage(root, proof):
    root = Path(root).absolute()
    if root.parent.name != 'world-snapshots' or root.parent.parent.name != 'runs':
        raise ValueError('Only a task world-snapshots staging copy may be released')
    if root.is_symlink() or (root / 'world').is_symlink():
        raise ValueError('Staging symlinks are forbidden')
    manifest_path = root / 'package/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    stage = json.loads((root / 'world.stage.json').read_text())
    if (proof.get('schema') != 'earthcraft.snapshot-retention.v1' or
            not proof.get('all_archives_verified') or not proof.get('all_source_files_verified') or
            proof.get('manifest_sha256') != digest(manifest_path) or
            proof.get('file_count') != manifest['file_count'] or
            time.time() - proof.get('verified_at', 0) > 86400 or
            Path(proof.get('retained_root', '')).name != root.name or
            Path(proof['retained_root']).absolute() == root):
        raise ValueError('A recent independently retained complete snapshot is required')
    expected = {item['path']: item for item in stage['files']}
    packaged = {item['path']: item for shard in manifest['shards'] for item in shard['files']}
    if expected.keys() != packaged.keys():
        raise ValueError('Stage and package inventories differ')
    world = root / 'world'
    actual = {p.relative_to(world).as_posix() for p in world.rglob('*') if p.is_file()}
    if actual != expected.keys():
        raise ValueError('Staging file list changed; preserve all bytes')
    for relative, item in expected.items():
        stat = safe_file(world, relative).stat()
        if stat.st_size != item['bytes'] or stat.st_mtime_ns != item['mtime_ns']:
            raise ValueError('Staged snapshot was edited; preserve all bytes')
    # Stage is closed, immutable, task-owned, and retained with two verified
    # representations on another volume. The user's live save is never read.
    shutil.rmtree(world)
    result = dict(proof, local_staging_released=True, local_root=str(root),
                  release_at=time.time(), original_playable_save_modified=False)
    (root / 'retention-proof.local.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['verify', 'release-stage'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--proof', type=Path, required=True)
    p.add_argument('--archives-only', action='store_true')
    a = p.parse_args()
    if a.action == 'verify':
        result = verify(a.root, a.archives_only)
        a.proof.write_text(json.dumps(result, indent=2) + '\n')
    else:
        result = release_stage(a.root, json.loads(a.proof.read_text()))
    print(json.dumps(result))
