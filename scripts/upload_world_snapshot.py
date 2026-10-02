"""Resumable world upload using the Hub client's existing secure login.

Destination ownership and private visibility are checked before every upload.
Only manifest-listed shards and explicitly named release metadata are uploaded.
The completion manifest is committed last. No token is accepted on the CLI.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

from world_snapshot import atomic_json, digest, SCHEMA
from restore_world_snapshot import checked_shards


def card(manifest):
    return '''---
license: other
pretty_name: Earthcraft Minecraft world
tags:
  - minecraft
  - geospatial
  - lidar
  - chicago
---

# Earthcraft

I am building Chicagoland in Minecraft at one block per metre. This is a complete
snapshot of the save generated so far, including its current coordinates,
datapacks, custom resource pack, dimensions, and player state. Chicagoland is
still incomplete. This is not a generated copy of the entire Earth.

Snapshot: **{snapshot}**. The save contains **{chunks:,} serialized chunks** and
**{files:,} files**. The uncompressed package input is **{gib:.2f} GiB**.
Installed regional tile qualities: `{qualities}`. Scan upgrades measure roofs
and some point geometry; they do not establish complete façade accuracy.

Each geographic shard groups up to {span} × {span} Minecraft regions. Coordinates
and block contents are unchanged. `manifest.json` records every file's SHA-256,
the archive checksums, the fixed world frame, and the omitted cache files.

Download one complete snapshot directory from `snapshots/`, then restore into
a new saves folder with Python:

```sh
python restore_world_snapshot.py --package . --destination /path/to/saves/Earthcraft
```

To restore selected areas, repeat `--shard` with keys from the manifest.
The metadata shard is always needed. Use Minecraft Java 1.21.10. For distant
rendering, install compatible Distant Horizons and Sodium separately; their
jars and Minecraft's own game files are not included. LOD caches rebuild from
the restored blocks.

The snapshot is private and includes player state. Geographic inputs include
OpenStreetMap contributors (ODbL), Overture source footprints, county/USGS
LiDAR, and aerial imagery where available. The custom Water Tower photo skin
retains its attribution in `resources.zip`. Data and photo licenses are
separate from Earthcraft's Apache-2.0 source license. Check the save's retained
source receipts and texture attribution before redistributing derived content.
'''.format(snapshot=manifest['snapshot_id'], chunks=manifest['minecraft_chunks'],
           files=manifest['file_count'], gib=manifest['source_bytes']/2**30,
           qualities=json.dumps(manifest['installed_quality_tiles'], sort_keys=True),
           span=manifest['region_span'])


def git_blob_sha(path):
    result = hashlib.sha1()
    result.update(('blob '+str(path.stat().st_size)+'\0').encode())
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024**2), b''):
            result.update(data)
    return result.hexdigest()


def matches(item, source, expected):
    if item is None:
        return False
    lfs = getattr(item, 'lfs', None)
    checksum = lfs.get('sha256') if isinstance(lfs, dict) else getattr(lfs, 'sha256', None)
    return checksum == expected if checksum else getattr(item, 'blob_id', None) == git_blob_sha(source)


def upload(package, repo_id, *, api=None):
    if api is None:
        from huggingface_hub import HfApi
        api = HfApi()
    package = Path(package).resolve()
    manifest = json.loads((package/'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA or manifest.get('state') != 'complete':
        raise ValueError('A completed checked package is required')
    snapshot = manifest['snapshot_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', snapshot):
        raise ValueError('Simple snapshot identifier required')
    checked_shards(package, manifest)
    owner = api.whoami()['name']
    if repo_id.split('/')[0] != owner:
        raise ValueError('Dataset must belong to the authenticated user')
    def private():
        info = api.repo_info(repo_id, repo_type='dataset', files_metadata=True)
        if info.id != repo_id or not info.private:
            raise ValueError('World uploads require the verified private dataset')
        return {s.rfilename: s for s in info.siblings}
    remote = private().get(f'snapshots/{snapshot}/manifest.json')
    if remote is not None and not matches(remote, package/'manifest.json', digest(package/'manifest.json')):
        raise ValueError('Snapshot identifier already contains a different completed manifest')
    (package/'README.md').write_text(card(manifest))
    shutil.copyfile(Path(__file__).with_name('restore_world_snapshot.py'), package/'restore_world_snapshot.py')
    members = [s['path'] for s in manifest['shards']]
    members += ['restore_world_snapshot.py', 'README.md', 'manifest.json']
    state_path = package/'upload-state.local.json'
    state = {'state': 'uploading', 'repo_id': repo_id, 'snapshot_id': snapshot,
             'verified': {}, 'total_files': len(members)}
    for relative in members:
        target = f'snapshots/{snapshot}/{relative}'
        source = package/relative
        expected = digest(source)
        remote = private().get(target)
        if not matches(remote, source, expected):
            api.upload_file(path_or_fileobj=source, path_in_repo=target,
                            repo_id=repo_id, repo_type='dataset',
                            commit_message=f'Earthcraft {snapshot}: {relative}')
        if not matches(private().get(target), source, expected):
            raise ValueError('Hub file checksum does not match the snapshot')
        state['verified'][relative] = expected
        state['time'] = time.time()
        atomic_json(state_path, state)
        print(json.dumps({'state': 'uploading', 'verified_files': len(state['verified']),
                          'total_files': len(members)}), flush=True)
    source = package/'README.md'
    expected = digest(source)
    if not matches(private().get('README.md'), source, expected):
        api.upload_file(path_or_fileobj=source, path_in_repo='README.md',
                        repo_id=repo_id, repo_type='dataset', commit_message=f'Describe completed Earthcraft snapshot {snapshot}')
    if not matches(private().get('README.md'), source, expected):
        raise ValueError('Hub dataset card checksum differs')
    state['state'] = 'complete'
    state['url'] = f'https://huggingface.co/datasets/{repo_id}/tree/main/snapshots/{snapshot}'
    atomic_json(state_path, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--repo-id', required=True)
    args = parser.parse_args()
    result = upload(args.package, args.repo_id)
    print(json.dumps({k: v for k, v in result.items() if k != 'verified'}))


if __name__ == '__main__':
    main()
