"""Lossless, resumable Cook LAS acquisition using the indexed ZIP member alone.

ZIP method 8 and gzip share raw DEFLATE. Wrap the original compressed member in
a gzip header/trailer without recompression; every decompressed LAS byte stays
identical. Check the publisher CRC, full length, and SHA before publishing.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import urllib.error
import zipfile
import fcntl

from http_zip_range import RangeReader
from local_paths import bulk_root

BASE='https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/'
HEADER=b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff'
BLOCK=8*2**20


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def save_json(path,record):
    temporary=path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(record,indent=2));temporary.replace(path)


def resume_checkpoint(partial,state):
    """Recover the single write-ahead block left by a stopped downloader."""
    partial=Path(partial);completed=state.get('compressed_bytes')
    if type(completed) is not int or completed<0:raise ValueError('Invalid download checkpoint length')
    expected_size=len(HEADER)+completed;actual_size=partial.stat().st_size
    # Payload is flushed before its atomic checkpoint. A stop in that narrow
    # window can leave exactly one uncommitted block, which is safe to discard.
    if expected_size<actual_size<=expected_size+BLOCK:
        with partial.open('r+b') as stream:stream.truncate(expected_size)
        actual_size=expected_size
    if actual_size!=expected_size or sha(partial)!=state.get('sha256'):
        raise ValueError('Interrupted cache changed; preserve it')
    return completed


def verify_ready(ready, partial, asset):
    """Prove a crash-window ready file is the completed current partial."""
    ready,partial=Path(ready),Path(partial)
    partial_size=partial.stat().st_size
    if ready.stat().st_size!=partial_size+8:
        raise ValueError('Prior verified-file staging changed; preserve for inspection')
    digest=hashlib.sha256();remaining=partial_size
    with ready.open('rb') as stream:
        while remaining:
            chunk=stream.read(min(BLOCK,remaining))
            if not chunk:raise ValueError('Prior verified-file staging changed; preserve for inspection')
            digest.update(chunk);remaining-=len(chunk)
        trailer=stream.read()
    expected=struct.pack('<II',int(asset['crc32'],16),asset['uncompressed_bytes']%(2**32))
    if digest.hexdigest()!=sha(partial) or trailer!=expected:
        raise ValueError('Prior verified-file staging changed; preserve for inspection')
    uncompressed=hashlib.sha256();count=0
    with gzip.open(ready,'rb') as stream:
        while chunk:=stream.read(BLOCK):
            count+=len(chunk)
            if count>asset['uncompressed_bytes']:
                raise ValueError('Decompression exceeds indexed size')
            uncompressed.update(chunk)
    if count!=asset['uncompressed_bytes']:raise ValueError('Incomplete decompressed LAS')
    return count,uncompressed.hexdigest()


def _same_prefix(left, right, length):
    remaining=length
    with Path(left).open('rb') as a,Path(right).open('rb') as b:
        while remaining:
            size=min(BLOCK,remaining);first=a.read(size);second=b.read(size)
            if len(first)!=size or first!=second:return False
            remaining-=size
    return True


def prepare_ready(ready, partial, asset):
    """Create or deterministically recover the final verified gzip staging."""
    ready,partial=Path(ready),Path(partial)
    trailer=struct.pack('<II',int(asset['crc32'],16),asset['uncompressed_bytes']%(2**32))
    if ready.exists() and ready.stat().st_size!=partial.stat().st_size+len(trailer):
        # An older downloader could copy a then-current partial and append the
        # final trailer before another process completed the shared partial, or
        # stop during that copy before appending the trailer. Replace either
        # form only when every surviving payload byte is an exact prefix.
        ready_size=ready.stat().st_size;partial_size=partial.stat().st_size
        payload_size=ready_size-len(trailer)
        with ready.open('rb') as stream:
            stream.seek(max(0,payload_size));observed_trailer=stream.read()
        interrupted_copy=(len(HEADER)<=ready_size<=partial_size and _same_prefix(ready,partial,ready_size))
        older_ready=(len(HEADER)<=payload_size<partial_size and observed_trailer==trailer and
                     _same_prefix(ready,partial,payload_size))
        if not (interrupted_copy or older_ready):
            raise ValueError('Prior verified-file staging changed; preserve for inspection')
        replacement=ready.with_name(ready.name+'.replacement')
        if replacement.exists():raise ValueError('Prior replacement staging exists; preserve for inspection')
        shutil.copyfile(partial,replacement)
        with replacement.open('ab') as target:target.write(trailer)
        replacement.replace(ready)
    elif not ready.exists():
        shutil.copyfile(partial,ready)
        with ready.open('ab') as target:target.write(trailer)
    return verify_ready(ready,partial,asset)


def validate_asset(asset):
    if not re.fullmatch(r'\d{8}',asset['id']):raise ValueError('Invalid survey ID')
    if not re.fullmatch(re.escape(BASE)+r'cook-las[1-5]\.zip',asset['url']):raise ValueError('Not the public Cook source')
    archive_prefix=asset['url'].rsplit('/',1)[1][:-4]
    # Cook archives 1/3/4/5 use a directory prefix while cook-las2 stores
    # members at archive root.  Both are exact, frozen member identities; the
    # ZIP central-directory and local-header checks below still prove the
    # selected bytes, so accepting the two observed layouts does not broaden
    # the source.
    if asset['member'] not in (archive_prefix+'/'+asset['id']+'.las',asset['id']+'.las'):
        raise ValueError('Member/source mismatch')
    if asset['compression']!=8 or not 0<asset['compressed_bytes']<asset['uncompressed_bytes']<=3*2**30:
        raise ValueError('Unsupported codec or indexed member size')
    if not re.fullmatch(r'[0-9a-f]{8}',asset['crc32']):raise ValueError('Invalid CRC')


def verify(path,record,asset):
    if record['url']!=asset['url'] or record['member']!=asset['member'] or record['archive_etag']!=asset['archive_etag']:
        raise ValueError('Cached source identity changed')
    if record['bytes']!=asset['uncompressed_bytes'] or record['zip_crc32_verified']!=asset['crc32']:
        raise ValueError('Cached source/index disagree')
    if path.suffix=='.gz':
        if sha(path)!=record['compressed_sha256']:raise ValueError('Compressed cache checksum mismatch')
    elif sha(path)!=record['sha256']:raise ValueError('Original LAS checksum mismatch')
    return path,record


def acquire(asset,publisher,root,reserve_bytes=100*2**30):
    validate_asset(asset);root=Path(root)
    base=bulk_root().resolve()
    if not base.exists() or not root.resolve().is_relative_to(base):
        raise ValueError('Use the mounted Earthcraft bulk volume; no internal fallback')
    root.mkdir(parents=True,exist_ok=True)
    # Multiple tile workers can discover the same indexed LAS member at once.
    # Serialize only that asset's cache transition, not the whole tile queue.
    # The lock is an operational file, never part of a source receipt.
    with (root/(asset['id']+'.lock')).open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            for suffix in ('.las','.las.gz'):
                existing=root/(asset['id']+suffix)
                manifest=root/(asset['id']+('.json' if suffix=='.las' else '.las.gz.json'))
                if existing.exists() or manifest.exists():
                    if not existing.is_file() or not manifest.is_file():raise ValueError('Unpaired original cache; preserve for recovery')
                    return verify(existing,json.loads(manifest.read_text()),asset)
            if shutil.disk_usage(root).free<reserve_bytes+2*asset['compressed_bytes']+2**30:
                raise ValueError('Insufficient bulk space with 100 GiB reserve and working allowance')
            path=root/(asset['id']+'.las.gz');partial=path.with_suffix('.gz.part');checkpoint=partial.with_name(partial.name+'.json')
            remote=RangeReader(asset['url'],budget=asset['compressed_bytes']+4*2**20)
            if remote.etag!=asset['archive_etag']:raise ValueError('Remote archive version changed; re-index before fetching')
            with zipfile.ZipFile(remote) as archive:
                info=archive.getinfo(asset['member'])
                if (info.file_size,info.compress_size,info.header_offset,info.CRC,info.compress_type)!=(
                        asset['uncompressed_bytes'],asset['compressed_bytes'],asset['header_offset'],int(asset['crc32'],16),8):
                    raise ValueError('Remote ZIP directory differs from frozen catalog')
                if info.flag_bits&1:raise ValueError('Encrypted source not supported')
            remote.seek(info.header_offset);local=remote.read(30)
            if local[:4]!=b'PK\x03\x04':raise ValueError('Invalid ZIP local header')
            fields=struct.unpack('<4s5H3I2H',local)
            if fields[3]!=8 or fields[2]&1:raise ValueError('Local ZIP flags or compression changed')
            name=remote.read(fields[-2]);extra=remote.read(fields[-1])
            if name.decode('utf8' if fields[2]&0x800 else 'cp437')!=asset['member']:raise ValueError('Local member name changed')
            start=remote.tell();completed=0
            if partial.exists() or checkpoint.exists():
                state=json.loads(checkpoint.read_text())
                if state['asset']!=asset:raise ValueError('Interrupted cache changed; preserve it')
                completed=resume_checkpoint(partial,state)
                if not 0<=completed<=asset['compressed_bytes'] or partial.stat().st_size!=len(HEADER)+completed:
                    raise ValueError('Invalid download checkpoint length')
                with partial.open('rb') as stream:
                    if stream.read(len(HEADER))!=HEADER:raise ValueError('Invalid cached gzip header')
            else:
                partial.write_bytes(HEADER)
                save_json(checkpoint,{'asset':asset,'compressed_bytes':0,'sha256':sha(partial)})
            digest=hashlib.sha256()
            with partial.open('rb') as stream:
                while chunk:=stream.read(BLOCK):digest.update(chunk)
            with partial.open('ab') as target:
                while completed<asset['compressed_bytes']:
                    remote.seek(start+completed)
                    chunk=remote.read(min(BLOCK,asset['compressed_bytes']-completed))
                    target.write(chunk);target.flush();digest.update(chunk);completed+=len(chunk)
                    save_json(checkpoint,{'asset':asset,'compressed_bytes':completed,'sha256':digest.hexdigest()})
                    if completed//(64*2**20)!=(completed-len(chunk))//(64*2**20):
                        print(f"{asset['id']}: {completed//2**20}/{asset['compressed_bytes']//2**20} MiB cached",flush=True)
            # Keep resumable payload untouched until the entire gzip has been verified.
            ready=path.with_suffix('.gz.ready')
            count,uncompressed_sha256=prepare_ready(ready,partial,asset)
            record={'url':asset['url'],'member':asset['member'],'archive_etag':asset['archive_etag'],
                'bytes':count,'sha256':uncompressed_sha256,'compressed_sha256':sha(ready),
                'zip_crc32_verified':asset['crc32'],'capture_interval':publisher['capture_interval'],
                'publisher':publisher,'license':'No access or use restrictions per publisher XML',
                'storage':'Original ZIP DEFLATE bytes wrapped as gzip; lossless LAS recovery',
                'retrieved_utc':datetime.now(timezone.utc).isoformat(),'network_bytes_this_attempt':remote.transferred}
            ready.rename(path);save_json(root/(asset['id']+'.las.gz.json'),record)
            # These are our own transfer checkpoints, not source observations; the exact
            # payload is now verified in the completed gzip file.
            partial.unlink();checkpoint.unlink()
            return path,record
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)
