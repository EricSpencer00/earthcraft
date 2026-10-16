"""Upload generator-only shards through an existing authenticated HF client.

The private personal-save snapshot uploader is intentionally a separate route.
Checks the export schema and hashes, then commits the shard and index together.
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
INDEX_LIMIT = 16 * 2**20


class ConcurrentIndexChangedError(ValueError):
    concurrent_index_change = True


def digest(path):
    if isinstance(path, io.BytesIO):
        position = path.tell(); path.seek(0)
        try: return hashlib.file_digest(path, 'sha256').hexdigest()
        finally: path.seek(position)
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def size(archive):
    return len(archive.getbuffer()) if isinstance(archive, io.BytesIO) else Path(archive).stat().st_size


def validate(archive, expected, plan_sha256):
    if size(archive) > 256*2**20 or digest(archive) != expected:
        raise ValueError('Bounded immutable shard checksum required')
    if isinstance(archive, io.BytesIO): archive.seek(0)
    with (tarfile.open(fileobj=archive) if isinstance(archive, io.BytesIO) else tarfile.open(archive)) as tar:
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
    result = hashlib.sha1(b'blob '+str(size(path)).encode()+b'\0')
    position = path.tell() if isinstance(path, io.BytesIO) else None
    stream = path if position is not None else Path(path).open('rb')
    try:
        stream.seek(0)
        while raw := stream.read(1024**2): result.update(raw)
    finally:
        if position is not None: stream.seek(position)
        else: stream.close()
    return getattr(item, 'blob_id', None) == result.hexdigest()


def check_references(index, files, *, candidate=None):
    """Every index reference must resolve in this snapshot or this atomic commit."""
    if not isinstance(index.get('shards'), dict) or not isinstance(index.get('tiles'), dict):
        raise ValueError('Malformed remote archive index')
    for checksum, record in index['shards'].items():
        remote = 'generated-tiles/v1/shards/'+checksum[:2]+'/'+checksum+'.tar'
        if (not re.fullmatch(r'[a-f0-9]{64}', checksum) or record.get('sha256') != checksum or
                record.get('path') != remote or type(record.get('bytes')) is not int or
                not 1 <= record['bytes'] <= 256*2**20):
            raise ValueError('Malformed immutable shard reference')
        if checksum == candidate: continue
        item = files.get(remote)
        lfs = getattr(item, 'lfs', None)
        actual = lfs.get('sha256') if isinstance(lfs, dict) else getattr(lfs, 'sha256', None)
        if item is None or actual != checksum:
            raise ValueError('Referenced shard unavailable or content hash differs')
        if getattr(item, 'size', record['bytes']) != record['bytes']:
            raise ValueError('Referenced shard size differs')
    if any(item.get('shard_sha256') not in index['shards'] for item in index['tiles'].values()):
        raise ValueError('Dangling tile shard reference')


def upload(archive, expected, plan_sha256, repo_id, proof_path):
    from huggingface_hub import HfApi, HfFileSystem, CommitOperationAdd
    api = HfApi()
    manifest = validate(archive, expected, plan_sha256)
    owner = api.whoami()['name']
    if repo_id.split('/')[0] != owner:
        raise ValueError('Authenticated owner must match destination')
    def inventory(revision=None):
        info = api.repo_info(repo_id, repo_type='dataset', files_metadata=True, revision=revision)
        if info.id != repo_id or not info.private:
            raise ValueError('Reuse the existing owned private dataset')
        return info, {s.rfilename: s for s in info.siblings}
    remote = 'generated-tiles/v1/shards/'+expected[:2]+'/'+expected+'.tar'
    info, files = inventory()
    shard_present = matches(files.get(remote), archive)
    if remote in files and not shard_present:
        raise ValueError('Immutable remote shard checksum differs')
    index_remote = 'generated-tiles/v1/index.json'
    if index_remote in files:
        # A full per-revision disk cache and per-batch index copy grow quadratically.
        # Keep the pinned index in bounded RAM; retain its digest in the small proof.
        with HfFileSystem().open('datasets/'+repo_id+'/'+index_remote, 'rb',
                                 revision=info.sha, block_size=0) as stream:
            prior = stream.read(INDEX_LIMIT+1)
        if len(prior) > INDEX_LIMIT: raise ValueError('Index metadata exceeds bound')
        if not matches(files[index_remote], io.BytesIO(prior)):
            raise ValueError('Pinned prior index content differs')
        index = json.loads(prior)
        if index.get('schema') != 'earthcraft.generated-tile-index.v1' or index.get('plan_sha256') != plan_sha256:
            raise ValueError('Remote archive index belongs to another frozen plan')
    else:
        index = {'schema': 'earthcraft.generated-tile-index.v1', 'plan_sha256': plan_sha256,
                 'frame': manifest['frame'], 'shards': {}, 'tiles': {},
                 'attribution': manifest['attribution'], 'note': manifest['note']}
    if index['frame'] != manifest['frame']: raise ValueError('World frame changed')
    # Validate the entire old index before adding the candidate; missing references stop.
    check_references(index, files)
    index_base_revision = info.sha
    index['shards'][expected] = {'path': remote, 'sha256': expected, 'bytes': size(archive)}
    ranks = {'LQ': 1, 'scan-roof': 2, 'scan-points-and-roof': 3}
    for item in manifest['entries']:
        old = index['tiles'].get(item['tile'])
        if old and ranks[old['quality']] > ranks[item['quality']]: continue
        index['tiles'][item['tile']] = dict(item, shard_sha256=expected)
    check_references(index, files, candidate=expected)
    raw_index = json.dumps(index, sort_keys=True).encode()
    if len(raw_index) > INDEX_LIMIT: raise ValueError('Index metadata exceeds bound')
    index_buffer = io.BytesIO(raw_index)
    committed_revision = index_base_revision
    if not (shard_present and matches(files.get(index_remote), index_buffer)):
        latest, _ = inventory()
        if latest.sha != index_base_revision:
            raise ConcurrentIndexChangedError('Remote index changed during preparation; reload before committing')
        operations = []
        if not shard_present:
            if isinstance(archive, io.BytesIO): archive.seek(0)
            operations.append(CommitOperationAdd(path_in_repo=remote,
                path_or_fileobj=archive if isinstance(archive, io.BytesIO) else Path(archive)))
        operations.append(CommitOperationAdd(path_in_repo=index_remote, path_or_fileobj=index_buffer))
        # create_commit finishes LFS preupload before publishing the single atomic
        # repo commit. parent_commit prevents replacing a concurrent writer's index.
        commit = api.create_commit(repo_id=repo_id, repo_type='dataset', operations=operations,
            parent_commit=index_base_revision, num_threads=1,
            commit_message='Archive and index verified Earthcraft generator shard')
        committed_revision = commit.oid
    info, files = inventory(committed_revision)
    if info.sha != committed_revision: raise ValueError('Pinned commit verification required')
    if not matches(files.get(remote), archive): raise ValueError('Remote shard checksum differs')
    if not matches(files.get(index_remote), index_buffer): raise ValueError('Remote index differs')
    check_references(index, files)
    result = {'state': 'verified', 'repo': repo_id, 'private': True, 'revision': info.sha,
              'path': remote, 'archive_sha256': expected, 'plan_sha256': plan_sha256,
              'tiles': len(manifest['entries']), 'indexed_tiles': len(index['tiles']),
              'index_sha256': hashlib.sha256(raw_index).hexdigest(), 'index_bytes': len(raw_index),
              'entries': manifest['entries'], 'verified_at': time.time(),
              'personal_save_uploaded': False, 'original_world_modified': False}
    Path(proof_path).write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--archive', type=Path)
    source.add_argument('--archive-stdin', action='store_true')
    p.add_argument('--sha256', required=True); p.add_argument('--plan-sha256', required=True)
    p.add_argument('--repo-id', default='EricSpencer00/earthcraft-minecraft-world')
    p.add_argument('--proof', type=Path, required=True)
    p.add_argument('--retry-state', type=Path)
    p.add_argument('--attempt-id')
    a = p.parse_args()
    archive = a.archive
    if a.archive_stdin:
        import os, sys
        # This route uses bounded RAM, never local bulk staging or the Xet cache.
        os.environ['HF_HUB_DISABLE_XET'] = '1'
        raw = sys.stdin.buffer.read(64*2**20+1)
        if len(raw) > 64*2**20: raise ValueError('In-memory transport exceeds 64 MiB')
        archive = io.BytesIO(raw)
    try:
        r = upload(archive, a.sha256, a.plan_sha256, a.repo_id, a.proof)
    except Exception as error:
        from hf_archive_rate_limit import retry_receipt, persist_retry
        receipt = retry_receipt(error, attempt_id=a.attempt_id, archive_sha256=a.sha256)
        if receipt is not None and a.retry_state:
            persist_retry(a.retry_state, receipt)
        raise
    print(json.dumps({k:v for k,v in r.items() if k != 'entries'}))
