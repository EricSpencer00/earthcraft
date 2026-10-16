"""Verify actual private shard bytes before deleting a recoverable transport copy."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re

from upload_generated_tiles import validate


def restore(proof, expected, expected_bytes, plan, repo, *, api=None, filesystem=None):
    if (not re.fullmatch(r'[a-f0-9]{64}', expected) or
            not re.fullmatch(r'[a-f0-9]{64}', plan) or
            type(expected_bytes) is not int or not 0 < expected_bytes <= 256*2**20):
        raise ValueError('Bounded pinned archive required')
    remote = 'generated-tiles/v1/shards/'+expected[:2]+'/'+expected+'.tar'
    if (proof.get('state') != 'verified' or proof.get('private') is not True or
            proof.get('repo') != repo or proof.get('path') != remote or
            proof.get('archive_sha256') != expected or proof.get('plan_sha256') != plan or
            proof.get('personal_save_uploaded') is not False or
            proof.get('original_world_modified') is not False or
            not re.fullmatch(r'[a-f0-9]{40}', proof.get('revision', ''))):
        raise ValueError('Verified private generator proof required')
    if api is None or filesystem is None:
        from huggingface_hub import HfApi, HfFileSystem
        api, filesystem = HfApi(), HfFileSystem()
    revision = proof['revision']
    info = api.repo_info(repo, repo_type='dataset', revision=revision)
    if info.id != repo or not info.private or info.sha != revision:
        raise ValueError('Pinned existing private destination required')
    with filesystem.open('datasets/'+repo+'/'+remote, 'rb', revision=revision, block_size=0) as stream:
        raw = stream.read(expected_bytes+1)
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Actual restored archive bytes differ')
    manifest = validate(io.BytesIO(raw), expected, plan)
    if manifest['entries'] != proof.get('entries') or len(manifest['entries']) != proof.get('tiles'):
        raise ValueError('Restored manifest differs from upload proof')
    return {'state':'restore_verified','repo':repo,'private':True,'revision':revision,
            'archive_sha256':expected,'plan_sha256':plan,'bytes':len(raw),
            'tiles':len(manifest['entries']),'expanded_region_hashes_verified':True,
            'personal_save_uploaded':False,'original_world_modified':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--proof',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--bytes',type=int,required=True)
    p.add_argument('--plan-sha256',required=True)
    p.add_argument('--repo-id',required=True)
    a=p.parse_args()
    if a.proof.stat().st_size > 2**20: raise ValueError('Proof exceeds bound')
    result=restore(json.loads(a.proof.read_text()),a.sha256,a.bytes,a.plan_sha256,a.repo_id)
    print(json.dumps(result))


if __name__ == '__main__': main()
