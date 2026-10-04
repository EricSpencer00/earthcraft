"""Upload generator-only shards through an existing authenticated HF client.

The private personal-save snapshot uploader is intentionally a separate route.
Checks the exact export schema and content hashes, then publishes the index last.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import time

SCHEMA = 'earthcraft.generated-tile-shard.v1'
ENTRY_KEYS = {'tile', 'quality', 'world_offset_xz', 'size_m', 'metres_per_block',
              'receipt_sha256', 'region_sha256', 'region_file', 'chunks',
              'source_grid', 'scan', 'roof_source_cells',
              'real_facade_photo_colors_applied', 'client_visual_verified', 'privacy_audit'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(archive, expected, plan_sha256):
    archive = Path(archive)
    if archive.stat().st_size > 256*2**20 or digest(archive) != expected:
        raise ValueError('Bounded immutable shard checksum required')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if any(not m.isfile() for m in members) or len({m.name for m in members}) != len(members):
            raise ValueError('Only unique regular generated files allowed')
        manifest = json.loads(tar.extractfile('manifest.json').read())
        if (set(manifest) != {'schema', 'frame', 'plan_sha256', 'entries', 'attribution', 'excludes', 'note'} or
                manifest['schema'] != SCHEMA or manifest['plan_sha256'] != plan_sha256 or
                not 1 <= len(manifest['entries']) <= 32):
            raise ValueError('Frozen generated export schema required')
        expected_members = {'manifest.json'}
        seen = set()
        for item in manifest['entries']:
            if set(item) != ENTRY_KEYS or not re.fullmatch(r'-?\d+_-?\d+', item['tile']) or item['tile'] in seen:
                raise ValueError('Unexpected generated tile metadata')
            seen.add(item['tile'])
            x, z = map(int, item['tile'].split('_'))
            if (item['world_offset_xz'] != [x*512, z*512] or item['size_m'] != 512 or
                    item['metres_per_block'] != 1 or item['region_file'] != f'r.{x}.{z}.mca' or
                    item['real_facade_photo_colors_applied'] != 0 or item['client_visual_verified'] is not False):
                raise ValueError('Coordinate or quality claims differ')
            name = item['tile']+'/'+item['region_file']+'.gz'
            expected_members.add(name)
            with gzip.GzipFile(fileobj=tar.extractfile(name)) as stream:
                result = hashlib.sha256(); count = 0
                while data := stream.read(1024**2):
                    count += len(data)
                    if count > 128*2**20: raise ValueError('Expanded region exceeds bound')
                    result.update(data)
            if result.hexdigest() != item['region_sha256']:
                raise ValueError('Generated region bytes differ')
        if {m.name for m in members} != expected_members:
            raise ValueError('Unlisted contents; never upload personal state')
    return manifest


def matches(item, path):
    if item is None: return False
    lfs = getattr(item, 'lfs', None)
    checksum = lfs.get('sha256') if isinstance(lfs, dict) else getattr(lfs, 'sha256', None)
    if checksum: return checksum == digest(path)
    raw = Path(path).read_bytes()
    return getattr(item, 'blob_id', None) == hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def upload(archive, expected, plan_sha256, repo_id, proof_path):
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi()
    manifest = validate(archive, expected, plan_sha256)
    owner = api.whoami()['name']
    if repo_id.split('/')[0] != owner:
        raise ValueError('Authenticated owner must match destination')
    def inventory():
        info = api.repo_info(repo_id, repo_type='dataset', files_metadata=True)
        if info.id != repo_id or not info.private:
            raise ValueError('Reuse the existing owned private dataset')
        return info, {s.rfilename: s for s in info.siblings}
    remote = 'generated-tiles/v1/shards/'+expected[:2]+'/'+expected+'.tar'
    info, files = inventory()
    if not matches(files.get(remote), archive):
        api.upload_file(path_or_fileobj=Path(archive), path_in_repo=remote,
                        repo_id=repo_id, repo_type='dataset',
                        commit_message='Archive verified Earthcraft generator regions')
    info, files = inventory()
    if not matches(files.get(remote), archive):
        raise ValueError('Remote shard checksum differs')
    index_path = Path(proof_path).with_suffix('.index.local.json')
    index_remote = 'generated-tiles/v1/index.json'
    if index_remote in files:
        prior = hf_hub_download(repo_id, index_remote, repo_type='dataset', revision=info.sha)
        index = json.loads(Path(prior).read_text())
        if index.get('schema') != 'earthcraft.generated-tile-index.v1' or index.get('plan_sha256') != plan_sha256:
            raise ValueError('Remote archive index belongs to another frozen plan')
    else:
        index = {'schema': 'earthcraft.generated-tile-index.v1', 'plan_sha256': plan_sha256,
                 'frame': manifest['frame'], 'shards': {}, 'tiles': {},
                 'attribution': manifest['attribution'], 'note': manifest['note']}
    if index['frame'] != manifest['frame']: raise ValueError('World frame changed')
    index_base_revision = info.sha
    index['shards'][expected] = {'path': remote, 'sha256': expected, 'bytes': Path(archive).stat().st_size}
    ranks = {'LQ': 1, 'scan-roof': 2, 'scan-points-and-roof': 3}
    for item in manifest['entries']:
        old = index['tiles'].get(item['tile'])
        if old and ranks[old['quality']] > ranks[item['quality']]: continue
        index['tiles'][item['tile']] = dict(item, shard_sha256=expected)
    index_path.write_text(json.dumps(index, sort_keys=True))
    info, files = inventory()
    if not matches(files.get(index_remote), index_path):
        if info.sha != index_base_revision:
            raise ValueError('Remote index changed during preparation; reload before committing')
        api.upload_file(path_or_fileobj=index_path, path_in_repo=index_remote,
                        repo_id=repo_id, repo_type='dataset', parent_commit=index_base_revision,
                        commit_message='Index verified Earthcraft generator shard')
    info, files = inventory()
    if not matches(files.get(index_remote), index_path): raise ValueError('Remote index differs')
    result = {'state': 'verified', 'repo': repo_id, 'private': True, 'revision': info.sha,
              'path': remote, 'archive_sha256': expected, 'plan_sha256': plan_sha256,
              'tiles': len(manifest['entries']), 'indexed_tiles': len(index['tiles']),
              'entries': manifest['entries'], 'verified_at': time.time(),
              'personal_save_uploaded': False, 'original_world_modified': False}
    Path(proof_path).write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--sha256', required=True); p.add_argument('--plan-sha256', required=True)
    p.add_argument('--repo-id', default='EricSpencer00/earthcraft-minecraft-world')
    p.add_argument('--proof', type=Path, required=True)
    a = p.parse_args()
    r = upload(a.archive, a.sha256, a.plan_sha256, a.repo_id, a.proof)
    print(json.dumps({k:v for k,v in r.items() if k != 'entries'}))
