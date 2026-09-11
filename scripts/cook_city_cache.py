"""Lossless, resumable Cook LAS acquisition using the indexed ZIP member alone.

ZIP method 8 and gzip share raw DEFLATE. Wrap the original compressed member in
a gzip header/trailer without recompression; every decompressed LAS byte stays
identical. Check the publisher CRC, full length, and SHA before publishing.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import urllib.error
import zipfile

from http_zip_range import RangeReader
from local_paths import bulk_root

BASE='https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/'
HEADER=b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff'
BLOCK=8*2**20


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def save_json(path,record):
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(record,indent=2));temporary.replace(path)


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
        if state['asset']!=asset or sha(partial)!=state['sha256']:raise ValueError('Interrupted cache changed; preserve it')
        completed=state['compressed_bytes']
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
    if ready.exists():raise ValueError('Prior verified-file staging exists; preserve for inspection')
    shutil.copyfile(partial,ready)
    with ready.open('ab') as target:target.write(struct.pack('<II',int(asset['crc32'],16),asset['uncompressed_bytes']%(2**32)))
    uncompressed=hashlib.sha256();count=0
    with gzip.open(ready,'rb') as stream:
        while chunk:=stream.read(BLOCK):
            count+=len(chunk)
            if count>asset['uncompressed_bytes']:raise ValueError('Decompression exceeds indexed size')
            uncompressed.update(chunk)
    if count!=asset['uncompressed_bytes']:raise ValueError('Incomplete decompressed LAS')
    record={'url':asset['url'],'member':asset['member'],'archive_etag':asset['archive_etag'],
        'bytes':count,'sha256':uncompressed.hexdigest(),'compressed_sha256':sha(ready),
        'zip_crc32_verified':asset['crc32'],'capture_interval':publisher['capture_interval'],
        'publisher':publisher,'license':'No access or use restrictions per publisher XML',
        'storage':'Original ZIP DEFLATE bytes wrapped as gzip; lossless LAS recovery',
        'retrieved_utc':datetime.now(timezone.utc).isoformat(),'network_bytes_this_attempt':remote.transferred}
    ready.rename(path);save_json(root/(asset['id']+'.las.gz.json'),record)
    # These are our own transfer checkpoints, not source observations; the exact
    # payload is now verified in the completed gzip file.
    partial.unlink();checkpoint.unlink()
    return path,record
