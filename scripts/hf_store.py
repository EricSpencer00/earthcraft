"""Write Earthcraft directly to its one durable store: a public Hugging Face dataset.

The old path wrote bulk archives to an external drive, read them back and only
then uploaded. That drive died from careless bulk writes. This tool never
stages bulk data. Existing files are uploaded in place; only transformed files
(privacy-stripped level.dat, the repacked snapshot metadata, generated
indexes) and bounded migration downloads are written, and only into one scratch
root guarded by a hard byte cap and an absolute free-space floor. Both are
checked before every write and between batches. Hugging Face client caches
(hub blobs, Xet chunk/shard/staging caches, logs, temp files) are pointed into
the same guarded root and bounded by environment variables before the client
library is imported.

Uploads are grouped into bounded commits (default 1 GiB / 400 files), retried
with exponential backoff and jitter on 429/5xx/connection errors (Retry-After
is respected), recorded in a small resumable JSON ledger, and verified on the
Hub (size plus LFS/Xet SHA-256, or git blob id) before any scratch copy is
deleted. Nothing on the Hub is ever deleted by this tool.

  hf_store.py --scratch DIR [--cap 3G] [--min-free 50G] push-files --src DIR --dest PREFIX
  hf_store.py --scratch DIR push-world --save SAVE [--only-changed] [--dry-run]
  hf_store.py --scratch DIR migrate [--only tiles,validations,snapshot,oakbrook,card] [--limit N]
  hf_store.py --scratch DIR verify --manifest PATH|hf:PATH [--prefix P] [--rewrite OLD=NEW]
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import base64
import fnmatch
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from world_snapshot import inventory, save_lock  # noqa: E402  (POSIX record lock, as Minecraft)

KiB, MiB, GiB = 2**10, 2**20, 2**30
DEFAULT_REPO = 'EricSpencer00/earthcraft'
SOURCE_REPO = 'EricSpencer00/earthcraft-minecraft-world'
OAKBROOK_REPOS = {'EricSpencer00/earthcraft-oakbrook-source-bound-preview': 'preview',
                  'EricSpencer00/earthcraft-oakbrook-native-chunks': 'native'}
SNAPSHOT_ID = 'chicagoland-20261001-closed-save'
SNAPSHOT_PREFIX = f'snapshots/{SNAPSHOT_ID}/'
LEDGER_SCHEMA = 'earthcraft.hf-store-ledger.v1'
REGIONS_SCHEMA = 'earthcraft.world-regions.v1'
MANIFEST_SCHEMA = 'earthcraft.world-manifest.v1'
REGION_FILE = re.compile(r'r\.(-?\d+)\.(-?\d+)\.mca')
EXTERNAL_CHUNK = re.compile(r'c\.(-?\d+)\.(-?\d+)\.mcc')
REGION_KINDS = ('region', 'entities', 'poi')
PLAYER_TREES = ('playerdata', 'stats', 'advancements')
PLAYER_PATH = re.compile(r'^(?:\./)?(?:playerdata|stats|advancements)/')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
USERNAME = re.compile(r'[A-Za-z0-9_]{3,16}')
RETRY_STATUS = {408, 409, 412, 425, 429, 500, 502, 503, 504}
DOWNLOAD_MARGIN = 64 * MiB
REDACTED = '[redacted player identifier]'


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def parse_size(text):
    """'3G' is 3 * 10**9 bytes; '3GiB' or '3Gi' is 3 * 2**30 bytes."""
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([KMGT]?)(i?)B?\s*', str(text), re.I)
    if not match:
        raise argparse.ArgumentTypeError(f'Invalid size: {text!r}')
    unit = match[2].upper()
    power = 'KMGT'.index(unit) + 1 if unit else 0
    return int(float(match[1]) * ((1024 if match[3] else 1000) ** power))


def human(size):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(size) < 1024 or unit == 'TiB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{size} B'
        size /= 1024


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def safe_repo_path(value):
    path = PurePosixPath(value)
    if (not value or value.startswith('/') or '\\' in value or '..' in path.parts or
            str(path) != value or any(part in ('', '.') for part in path.parts)):
        raise ValueError(f'Unsafe repository path: {value!r}')
    return value


# --------------------------------------------------------------------------
# Disk guard


class DiskGuardError(RuntimeError):
    """A write was refused before it started; nothing was partially written."""


class DiskGuard:
    """Hard byte cap for one scratch root plus an absolute free-space floor.

    The floor defaults to max(25 GB, 10% of the volume). Every write this tool
    performs goes through `check`/`write_bytes`; client-library writes (hub
    downloads, Xet caches) are admitted with `check(incoming=...)` first and
    sampled by `watch` while they run.
    """

    def __init__(self, root, cap_bytes, *, floor_bytes=None, floor_fraction=0.10,
                 floor_min_bytes=25 * 10**9, statvfs=os.statvfs):
        self.root = Path(root).expanduser().absolute()
        if cap_bytes <= 0:
            raise ValueError('A positive scratch cap is required')
        self.cap = int(cap_bytes)
        self.floor_bytes = floor_bytes
        self.floor_fraction = floor_fraction
        self.floor_min_bytes = floor_min_bytes
        self.statvfs = statvfs
        self.peak_used = 0
        self.peak_detail = {}
        self.min_free = None
        self.breach = None
        self._lock = threading.Lock()

    # -- measurement
    def _existing(self):
        path = self.root
        while not path.exists():
            path = path.parent
        return path

    def volume(self):
        stat = self.statvfs(str(self._existing()))
        return stat.f_blocks * stat.f_frsize, stat.f_bavail * stat.f_frsize

    def floor(self, total=None):
        if self.floor_bytes is not None:
            return int(self.floor_bytes)
        total = self.volume()[0] if total is None else total
        return max(int(self.floor_min_bytes), int(self.floor_fraction * total))

    @staticmethod
    def _detail_key(parts):
        if parts[:2] == ('hf-home', 'xet') and len(parts) > 4:
            return 'hf-home/xet/*/' + parts[3]
        if parts[0] == 'hf-home' and len(parts) > 2:
            return '/'.join(parts[:2]) if parts[1] != 'xet' else '/'.join(parts[:3])
        return parts[0]

    def used(self, detail=False):
        total, breakdown = 0, {}
        if not self.root.exists():
            return (0, {}) if detail else 0
        for directory, _, files in os.walk(self.root):
            for name in files:
                path = Path(directory) / name
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue
                size = max(info.st_size, getattr(info, 'st_blocks', 0) * 512)
                total += size
                if detail:
                    key = self._detail_key(path.relative_to(self.root).parts)
                    breakdown[key] = breakdown.get(key, 0) + size
        return (total, breakdown) if detail else total

    def status(self):
        used, detail = self.used(detail=True)
        total, free = self.volume()
        return {'root': str(self.root), 'cap_bytes': self.cap, 'used_bytes': used,
                'free_bytes': free, 'volume_bytes': total, 'floor_bytes': self.floor(total),
                'detail': detail}

    def _record(self, used, free, detail=None):
        with self._lock:
            self.peak_used = max(self.peak_used, used)
            self.min_free = free if self.min_free is None else min(self.min_free, free)
            for key, value in (detail or {}).items():
                self.peak_detail[key] = max(self.peak_detail.get(key, 0), value)

    def check(self, incoming=0, what='write'):
        """Raise before a write of `incoming` bytes would break the cap or floor."""
        self.raise_if_breached()
        used = self.used()
        total, free = self.volume()
        floor = self.floor(total)
        self._record(used, free)
        if used + incoming > self.cap:
            raise DiskGuardError(f'Scratch cap refuses {what}: {human(used)} used + {human(incoming)} '
                                 f'> cap {human(self.cap)} under {self.root}')
        if free - incoming < floor:
            raise DiskGuardError(f'Free-space floor refuses {what}: {human(free)} free - {human(incoming)} '
                                 f'< floor {human(floor)} on the scratch volume')
        return {'used': used, 'free': free, 'floor': floor}

    def admit(self, incoming, what='write', wait_seconds=0, poll_seconds=30, sleep=time.sleep):
        """Block up to `wait_seconds` for capacity, then abort cleanly."""
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                return self.check(incoming, what)
            except DiskGuardError:
                if time.monotonic() >= deadline:
                    raise
                sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))

    def raise_if_breached(self):
        if self.breach:
            raise DiskGuardError(self.breach)

    # -- guarded writes
    def path(self, *parts):
        target = Path(os.path.normpath(self.root.joinpath(*parts)))
        if target != self.root and self.root not in target.parents:
            raise ValueError('Scratch writes must stay inside the guarded root')
        return target

    def ensure_dir(self, *parts):
        target = self.path(*parts)
        if not target.exists():
            self.check(0, f'mkdir {target.name}')
            target.mkdir(parents=True, exist_ok=True)
        return target

    def write_bytes(self, target, data, *, durable=True):
        target = Path(target)
        target = self.path(target) if not target.is_absolute() else Path(os.path.normpath(target))
        if self.root not in target.parents:
            raise ValueError('Scratch writes must stay inside the guarded root')
        self.check(len(data), f'write {target.name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.partial')
        try:
            with partial.open('wb') as stream:
                stream.write(data)
                if durable:
                    stream.flush()
                    os.fsync(stream.fileno())
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return target

    def write_json(self, target, value):
        return self.write_bytes(target, (json.dumps(value, indent=1, sort_keys=True) + '\n').encode())

    def append_line(self, target, line):
        data = (line.rstrip('\n') + '\n').encode()
        target = self.path(target)
        self.check(len(data), 'log append')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('ab') as stream:
            stream.write(data)

    def remove(self, target):
        target = Path(os.path.normpath(Path(target).absolute()))
        if self.root not in target.parents:
            raise ValueError('Refusing to delete outside the guarded scratch root')
        target.unlink(missing_ok=True)

    def prune(self, *parts):
        """Remove empty directories (bottom-up) under a scratch subtree."""
        base = self.path(*parts)
        if not base.exists():
            return
        for directory, _, _ in sorted(os.walk(base), key=lambda item: -len(item[0])):
            try:
                Path(directory).rmdir()
            except OSError:
                pass

    def clear(self, *parts):
        base = self.path(*parts)
        if base == self.root:
            raise ValueError('Refusing to clear the whole scratch root')
        if base.exists():
            shutil.rmtree(base)

    @contextmanager
    def watch(self, interval=0.5):
        """Sample usage while client libraries write; flag a breach for the next check."""
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    used, detail = self.used(detail=True)
                    total, free = self.volume()
                    self._record(used, free, detail)
                    if used > self.cap:
                        self.breach = f'Scratch exceeded cap during a client write: {human(used)} > {human(self.cap)}'
                    elif free < self.floor(total):
                        self.breach = f'Free space fell below the floor during a client write: {human(free)}'
                except OSError:
                    pass
                stop.wait(interval)

        thread = threading.Thread(target=sample, name='disk-guard-watch', daemon=True)
        thread.start()
        try:
            yield self
        finally:
            stop.set()
            thread.join(timeout=5)

    def report(self):
        total, free = self.volume()
        final, detail = self.used(detail=True)
        self._record(final, free, detail)
        return {'cap_bytes': self.cap, 'floor_bytes': self.floor(total), 'peak_scratch_bytes': self.peak_used,
                'min_free_bytes': self.min_free, 'peak_detail_bytes': dict(sorted(self.peak_detail.items())),
                'final_scratch_bytes': final, 'final_detail_bytes': dict(sorted(detail.items()))}


def bound_hf_environment(guard, *, shard_cache_bytes=128 * MiB, log_max='16mb', environ=None):
    """Point every Hugging Face client write into the guarded scratch root.

    Must run before huggingface_hub/hf_xet are imported: both read these at
    import. The login token stays where `hf auth login` put it.
    """
    environ = os.environ if environ is None else environ
    loaded = [name for name in ('huggingface_hub', 'hf_xet') if name in sys.modules]
    if loaded and environ is os.environ:
        raise RuntimeError('Hugging Face client imported before its caches were bounded: ' + ', '.join(loaded))
    original_home = Path(environ.get('HF_HOME', '~/.cache/huggingface')).expanduser()
    token_path = Path(environ.get('HF_TOKEN_PATH', original_home / 'token')).expanduser()
    home = guard.path('hf-home')
    tmp = guard.path('tmp')
    values = {
        'HF_HOME': str(home),
        'HF_HUB_CACHE': str(home / 'hub'),
        'HF_ASSETS_CACHE': str(home / 'assets'),
        'HF_XET_CACHE': str(home / 'xet'),
        'HF_TOKEN_PATH': str(token_path),
        # Xet chunk cache off: every byte is read once, so a cache only fills disk.
        'HF_XET_CHUNK_CACHE_SIZE_BYTES': '0',
        'HF_XET_SHARD_CACHE_SIZE_LIMIT': str(int(shard_cache_bytes)),
        'HF_XET_LOG_DIR_MAX_SIZE': log_max,
        'HF_HUB_DISABLE_TELEMETRY': '1',
        'HF_HUB_DISABLE_PROGRESS_BARS': '1',
        'HF_HUB_DISABLE_SYMLINKS_WARNING': '1',
        'TMPDIR': str(tmp),
    }
    for path in (home, tmp):
        guard.check(0, 'create client cache directory')
        path.mkdir(parents=True, exist_ok=True)
    environ.update(values)
    import tempfile
    tempfile.tempdir = None  # re-read TMPDIR
    return values


# --------------------------------------------------------------------------
# Hashing and remote comparison


@dataclass(frozen=True)
class Digest:
    size: int
    sha256: str
    blob_id: str  # git blob sha1, used for small non-LFS files


def hash_bytes(data):
    return Digest(len(data), hashlib.sha256(data).hexdigest(),
                  hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest())


def hash_file(path):
    with Path(path).open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        sha, blob, seen = hashlib.sha256(), hashlib.sha1(b'blob %d\0' % size), 0
        for data in iter(lambda: stream.read(MiB), b''):
            sha.update(data)
            blob.update(data)
            seen += len(data)
    if seen != size:
        raise RuntimeError(f'File changed while hashing: {Path(path).name}')
    return Digest(size, sha.hexdigest(), blob.hexdigest())


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    sha256: str | None  # LFS/Xet content hash when the file is a pointer
    blob_id: str | None


def remote_matches(remote, digest):
    if remote is None:
        return False, 'missing'
    if remote.size != digest.size:
        return False, f'size {remote.size} != {digest.size}'
    if remote.sha256:
        return (remote.sha256 == digest.sha256), 'lfs sha256 differs'
    if remote.blob_id:
        return (remote.blob_id == digest.blob_id), 'git blob id differs'
    return False, 'no remote checksum'


# --------------------------------------------------------------------------
# Retry


def retry_after_seconds(error, now=None):
    response = getattr(error, 'response', None)
    headers = getattr(response, 'headers', None) or {}
    try:
        value = headers.get('retry-after') or headers.get('Retry-After')
    except AttributeError:
        value = None
    waits = []
    if value:
        value = str(value).strip()
        if re.fullmatch(r'\d+(?:\.\d+)?', value):
            waits.append(float(value))
        else:
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                waits.append(max(0.0, when.timestamp() - (time.time() if now is None else now)))
            except (TypeError, ValueError, OverflowError):
                pass
    for amount, unit in re.findall(r'(?:retry after|retry this action in(?: about)?)\s+(\d+(?:\.\d+)?)\s*'
                                   r'(seconds?|minutes?|hours?)', str(error), re.I):
        scale = 3600 if unit.lower().startswith('hour') else 60 if unit.lower().startswith('minute') else 1
        waits.append(float(amount) * scale)
    return max(waits) if waits else None


def is_retryable(error):
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None)
    if status is not None:
        return status in RETRY_STATUS
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    for module, names in (('httpx', ('TransportError',)), ('requests', ('ConnectionError', 'Timeout'))):
        library = sys.modules.get(module)
        if library and any(isinstance(error, getattr(library, name, ())) for name in names):
            return True
    return False


class Retry:
    """Exponential backoff with jitter; Retry-After is a lower bound on the wait."""

    def __init__(self, attempts=8, base=2.0, cap=900.0, max_wait=3900.0, sleep=time.sleep,
                 rand=random.random, log=None):
        self.attempts, self.base, self.cap, self.max_wait = attempts, base, cap, max_wait
        self.sleep, self.rand, self.log = sleep, rand, log or (lambda **_: None)

    def delay(self, attempt, error):
        backoff = min(self.cap, self.base * 2**attempt)
        delay = backoff / 2 + self.rand() * backoff / 2
        after = retry_after_seconds(error)
        if after is not None:
            delay = max(delay, after + self.rand() * min(5.0, after * 0.1 + 0.5))
        return min(delay, self.max_wait)

    def call(self, fn, *, what='request', before_retry=None):
        for attempt in range(self.attempts):
            try:
                return fn()
            except Exception as error:  # noqa: BLE001 - classified below
                if not is_retryable(error) or attempt == self.attempts - 1:
                    raise
                delay = self.delay(attempt, error)
                status = getattr(getattr(error, 'response', None), 'status_code', None)
                self.log(event='retry', what=what, attempt=attempt + 1, status=status,
                         error=type(error).__name__, wait_seconds=round(delay, 1))
                self.sleep(delay)
                if before_retry:
                    before_retry()


# --------------------------------------------------------------------------
# Hub adapter (the only place huggingface_hub is touched)


class Hub:
    def __init__(self, repo_type='dataset'):
        from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download
        self.api, self.repo_type = HfApi(), repo_type
        self._op, self._download = CommitOperationAdd, hf_hub_download

    def whoami(self):
        return self.api.whoami()['name']

    def info(self, repo, revision=None):
        info = self.api.repo_info(repo, repo_type=self.repo_type, revision=revision)
        return {'sha': info.sha, 'private': bool(info.private), 'id': info.id}

    @staticmethod
    def _remote(item):
        lfs = getattr(item, 'lfs', None)
        sha = (lfs.get('sha256') if isinstance(lfs, dict) else getattr(lfs, 'sha256', None)) if lfs else None
        return RemoteFile(getattr(item, 'path', None) or item.rfilename, item.size, sha, item.blob_id)

    def list_files(self, repo, revision=None, prefixes=None):
        if not prefixes:
            info = self.api.repo_info(repo, repo_type=self.repo_type, revision=revision, files_metadata=True)
            return {s.rfilename: self._remote(s) for s in info.siblings}
        result = {}
        for prefix in prefixes:
            try:
                items = list(self.api.list_repo_tree(repo, path_in_repo=prefix.rstrip('/'), recursive=True,
                                                     revision=revision, repo_type=self.repo_type))
            except Exception as error:  # noqa: BLE001
                if getattr(getattr(error, 'response', None), 'status_code', None) == 404:
                    continue
                raise
            for item in items:
                if hasattr(item, 'size') and hasattr(item, 'blob_id'):
                    result[item.path] = self._remote(item)
        return result

    def paths_info(self, repo, paths, revision=None, expand=True):
        items = self.api.get_paths_info(repo, list(paths), expand=expand, revision=revision,
                                        repo_type=self.repo_type)
        return {item.path: self._remote(item) for item in items if hasattr(item, 'blob_id')}

    def commit(self, repo, items, message, revision=None):
        operations = []
        for item in items:
            operation = self._op(path_in_repo=item.path_in_repo, path_or_fileobj=str(item.local))
            if operation.upload_info.sha256.hex() != item.digest.sha256:
                raise RuntimeError(f'File changed before commit: {item.path_in_repo}')
            operations.append(operation)
        info = self.api.create_commit(repo, operations, commit_message=message, repo_type=self.repo_type,
                                      revision=revision)
        return {'oid': info.oid, 'url': info.commit_url}

    def download(self, repo, path, revision, local_dir):
        return Path(self._download(repo_id=repo, filename=path, repo_type=self.repo_type,
                                   revision=revision, local_dir=str(local_dir)))


# --------------------------------------------------------------------------
# Ledger and batch committer


class Ledger:
    """Small resumable record of verified remote paths, kept in guarded scratch."""

    def __init__(self, guard, name, repo_id):
        self.guard, self.repo_id = guard, repo_id
        self.path = guard.path('ledger', f'{name}.json')
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data.get('schema') != LEDGER_SCHEMA or self.data.get('repo_id') != repo_id:
                raise ValueError(f'Ledger {self.path.name} belongs to another repository or schema')
        else:
            self.data = {'schema': LEDGER_SCHEMA, 'repo_id': repo_id, 'created_utc': utc_now(),
                         'files': {}, 'commits': [], 'notes': {}}

    @property
    def files(self):
        return self.data['files']

    def verified(self, path, digest=None, sha256=None):
        entry = self.files.get(path)
        if not entry:
            return False
        want = digest.sha256 if digest else sha256
        return want is None or entry['sha256'] == want

    def record(self, path, digest, commit, source=None):
        entry = {'sha256': digest.sha256, 'bytes': digest.size, 'blob_id': digest.blob_id,
                 'commit': commit, 'verified_utc': utc_now()}
        if source:
            entry['source'] = source
        self.files[path] = entry

    def save(self):
        self.data['updated_utc'] = utc_now()
        self.guard.write_json(self.path, self.data)


@dataclass
class Item:
    path_in_repo: str
    local: Path
    digest: Digest
    scratch: bool = False  # delete the local copy once the remote copy is verified
    source: dict | None = None


class VerificationError(RuntimeError):
    pass


class BatchCommitter:
    def __init__(self, hub, repo_id, ledger, guard, *, retry, log, max_bytes=GiB, max_files=400,
                 revision='main', label='upload', expand=True, on_flushed=None):
        if max_files < 1 or max_bytes < 1:
            raise ValueError('Positive batch bounds are required')
        self.hub, self.repo_id, self.ledger, self.guard = hub, repo_id, ledger, guard
        self.retry, self.log, self.max_bytes, self.max_files = retry, log, max_bytes, max_files
        self.revision, self.label, self.expand, self.on_flushed = revision, label, expand, on_flushed
        self.pending, self.stats = [], {'committed_files': 0, 'committed_bytes': 0, 'skipped_files': 0,
                                        'skipped_bytes': 0, 'commits': []}

    @property
    def pending_bytes(self):
        return sum(item.digest.size for item in self.pending)

    def add(self, item):
        safe_repo_path(item.path_in_repo)
        self.guard.raise_if_breached()
        if self.ledger.verified(item.path_in_repo, item.digest):
            self._skip(item)
            return False
        if any(p.path_in_repo == item.path_in_repo for p in self.pending):
            raise ValueError(f'Duplicate path in batch: {item.path_in_repo}')
        if self.pending and (len(self.pending) + 1 > self.max_files or
                             self.pending_bytes + item.digest.size > self.max_bytes):
            self.flush()
        self.pending.append(item)
        return True

    def _skip(self, item):
        self.stats['skipped_files'] += 1
        self.stats['skipped_bytes'] += item.digest.size
        if item.scratch:
            self.guard.remove(item.local)

    def _remote(self, paths, revision):
        found, paths = {}, list(paths)
        for start in range(0, len(paths), 50):
            chunk = paths[start:start + 50]
            found.update(self.retry.call(lambda c=chunk: self.hub.paths_info(self.repo_id, c, revision=revision,
                                                                             expand=self.expand),
                                         what='get_paths_info'))
        return found

    def verify(self, items, revision):
        remote = self._remote([item.path_in_repo for item in items], revision)
        failures = []
        for item in items:
            ok, reason = remote_matches(remote.get(item.path_in_repo), item.digest)
            if not ok:
                failures.append(f'{item.path_in_repo}: {reason}')
        if failures:
            raise VerificationError('Remote verification failed: ' + '; '.join(failures[:10]))

    def _drop_present(self):
        """Before a retry, drop files an ambiguous earlier attempt already committed."""
        remote = self._remote([item.path_in_repo for item in self.pending], self.revision)
        keep = []
        for item in self.pending:
            if remote_matches(remote.get(item.path_in_repo), item.digest)[0]:
                self.ledger.record(item.path_in_repo, item.digest, 'pre-retry-verified', item.source)
                self.stats['committed_files'] += 1
                self.stats['committed_bytes'] += item.digest.size
                if item.scratch:
                    self.guard.remove(item.local)
            else:
                keep.append(item)
        if len(keep) != len(self.pending):
            self.ledger.save()
        self.pending = keep

    def flush(self):
        if not self.pending:
            return None
        self.guard.check(0, 'start of commit batch')
        batch_bytes, number = self.pending_bytes, len(self.stats['commits']) + 1
        message = (f'{self.label}: batch {number} ({len(self.pending)} files, {human(batch_bytes)}) '
                   f'[hf_store]')
        started = time.monotonic()

        def attempt():
            if not self.pending:  # an ambiguous earlier attempt already committed everything
                return None
            return self.hub.commit(self.repo_id, list(self.pending), message, revision=self.revision)

        commit = self.retry.call(attempt, what='create_commit', before_retry=self._drop_present)
        if not self.pending or commit is None:
            return None
        self.verify(self.pending, commit['oid'])
        for item in self.pending:
            self.ledger.record(item.path_in_repo, item.digest, commit['oid'], item.source)
        entry = {'oid': commit['oid'], 'url': commit.get('url'), 'files': len(self.pending),
                 'bytes': batch_bytes, 'label': self.label, 'utc': utc_now(),
                 'seconds': round(time.monotonic() - started, 1)}
        self.ledger.data['commits'].append(entry)
        self.ledger.save()
        for item in self.pending:  # only now, after remote verification
            if item.scratch:
                self.guard.remove(item.local)
        self.stats['committed_files'] += len(self.pending)
        self.stats['committed_bytes'] += batch_bytes
        self.stats['commits'].append(entry)
        self.log(event='commit_verified', label=self.label, oid=commit['oid'], files=len(self.pending),
                 bytes=batch_bytes, seconds=entry['seconds'], scratch_bytes=self.guard.used())
        self.pending = []
        if self.on_flushed:
            self.on_flushed()
        self.guard.check(0, 'between commit batches')
        return entry

    def close(self):
        return self.flush()


def simulate_batches(sizes, max_bytes, max_files):
    commits, files, total = 0, 0, 0
    for size in sizes:
        if files and (files + 1 > max_files or total + size > max_bytes):
            commits, files, total = commits + 1, 0, 0
        files, total = files + 1, total + size
    return commits + (1 if files else 0)


# --------------------------------------------------------------------------
# Minimal NBT reader/splicer (stdlib only; byte-exact outside removed tags)


class NbtError(ValueError):
    pass


_FIXED = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
_ARRAY = {7: 1, 11: 4, 12: 8}


def _int(raw, pos, size, signed=True):
    if pos < 0 or pos + size > len(raw):
        raise NbtError('Truncated NBT')
    return int.from_bytes(raw[pos:pos + size], 'big', signed=signed)


def _skip(raw, tag, pos, depth=0):
    if depth > 512:
        raise NbtError('NBT nesting too deep')
    if tag in _FIXED:
        end = pos + _FIXED[tag]
    elif tag in _ARRAY:
        count = _int(raw, pos, 4)
        if count < 0:
            raise NbtError('Negative NBT array length')
        end = pos + 4 + count * _ARRAY[tag]
    elif tag == 8:
        end = pos + 2 + _int(raw, pos, 2, False)
    elif tag == 9:
        element, count = _int(raw, pos, 1, False), _int(raw, pos + 1, 4)
        pos += 5
        if count < 0 or (count and element == 0):
            raise NbtError('Invalid NBT list')
        if count and element in _FIXED:
            pos += count * _FIXED[element]
        else:
            for _ in range(count):
                pos = _skip(raw, element, pos, depth + 1)
        end = pos
    elif tag == 10:
        while True:
            child = _int(raw, pos, 1, False)
            pos += 1
            if child == 0:
                break
            pos += 2 + _int(raw, pos, 2, False)
            pos = _skip(raw, child, pos, depth + 1)
        end = pos
    else:
        raise NbtError(f'Unknown NBT tag {tag}')
    if end > len(raw):
        raise NbtError('Truncated NBT')
    return end


def _children(raw, pos):
    children = []
    while True:
        start, tag = pos, _int(raw, pos, 1, False)
        pos += 1
        if tag == 0:
            return children
        length = _int(raw, pos, 2, False)
        name, payload = bytes(raw[pos + 2:pos + 2 + length]), pos + 2 + length
        end = _skip(raw, tag, payload)
        children.append((name, tag, start, payload, end))
        pos = end


def _root(raw):
    if not raw or raw[0] != 10:
        raise NbtError('NBT root must be a compound')
    payload = 3 + _int(raw, 1, 2, False)
    if _skip(raw, 10, payload) != len(raw):
        raise NbtError('Trailing bytes after NBT root')
    return payload


def nbt_find(raw, path):
    pos, found = _root(raw), None
    for index, name in enumerate(path):
        name = name.encode() if isinstance(name, str) else name
        found = next((child for child in _children(raw, pos) if child[0] == name), None)
        if found is None:
            return None
        if index < len(path) - 1:
            if found[1] != 10:
                return None
            pos = found[3]
    return found


def nbt_value(raw, tag, pos, depth=0):
    if depth > 512:
        raise NbtError('NBT nesting too deep')
    if tag in (1, 2, 3, 4):
        return _int(raw, pos, _FIXED[tag])
    if tag in (5, 6):
        return struct.unpack('>f' if tag == 5 else '>d', raw[pos:pos + _FIXED[tag]])[0]
    if tag in _ARRAY:
        count, width = _int(raw, pos, 4), _ARRAY[tag]
        if tag == 7:
            return bytes(raw[pos + 4:pos + 4 + count])
        return [_int(raw, pos + 4 + width * i, width) for i in range(count)]
    if tag == 8:
        length = _int(raw, pos, 2, False)
        return bytes(raw[pos + 2:pos + 2 + length]).decode('utf-8', 'replace')
    if tag == 9:
        element, count, pos = _int(raw, pos, 1, False), _int(raw, pos + 1, 4), pos + 5
        values = []
        for _ in range(count):
            values.append(nbt_value(raw, element, pos, depth + 1))
            pos = _skip(raw, element, pos, depth + 1)
        return values
    if tag == 10:
        return {name.decode('utf-8', 'replace'): nbt_value(raw, child, payload, depth + 1)
                for name, child, _, payload, _ in _children(raw, pos)}
    raise NbtError(f'Unknown NBT tag {tag}')


def nbt_get(raw, path):
    found = nbt_find(raw, path)
    return None if found is None else nbt_value(raw, found[1], found[3])


def nbt_splice(raw, path, replacement=b''):
    """Remove (or replace) every tag at `path`; returns (bytes, count)."""
    count = 0
    while True:
        found = nbt_find(raw, path)
        if found is None or (replacement and raw[found[2]:found[4]] == replacement):
            break
        raw = raw[:found[2]] + replacement + raw[found[4]:]
        count += 1
        if replacement:
            break
        if count > 64:
            raise NbtError('Too many duplicate tags')
    _root(raw)
    return raw, count


def empty_list_tag(name, element=10):
    name = name.encode() if isinstance(name, str) else name
    return bytes([9]) + len(name).to_bytes(2, 'big') + name + bytes([element]) + bytes(4)


def nbt_unwrap(data):
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data), 'gzip'
    return data, None


def nbt_wrap(raw, mode):
    return gzip.compress(raw, compresslevel=9, mtime=0) if mode == 'gzip' else raw


def uuid_from_ints(values):
    raw = b''.join(int(v).to_bytes(4, 'big', signed=True) for v in values)
    text = raw.hex()
    return f'{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}'


def strip_level_player(data):
    """Return level.dat bytes without Data.Player (single-player position/inventory)."""
    raw, mode = nbt_unwrap(data)
    stripped, count = nbt_splice(raw, ('Data', 'Player'))
    if nbt_find(stripped, ('Data',)) is None:
        raise NbtError('level.dat has no Data compound')
    return nbt_wrap(stripped, mode), count


def strip_scoreboard_players(data):
    raw, mode = nbt_unwrap(data)
    found = nbt_find(raw, ('data', 'PlayerScores'))
    if found is None:
        return data, 0
    element = raw[found[3]] if found[1] == 9 else 10
    stripped, _ = nbt_splice(raw, ('data', 'PlayerScores'), empty_list_tag('PlayerScores', element))
    return nbt_wrap(stripped, mode), len(nbt_value(raw, found[1], found[3]) or [])


def level_summary(data):
    raw, _ = nbt_unwrap(data)
    return {'level_name': nbt_get(raw, ('Data', 'LevelName')),
            'minecraft_version': nbt_get(raw, ('Data', 'Version', 'Name')),
            'data_version': nbt_get(raw, ('Data', 'DataVersion')),
            'has_player': nbt_find(raw, ('Data', 'Player')) is not None}


# --------------------------------------------------------------------------
# Player-identifier discovery, redaction and the final privacy gate


def collect_identifiers(names, read):
    """Player UUIDs and names known to a save. Never logged or uploaded.

    UUIDs come from player file names and level.dat Data.Player.UUID; names
    come from scoreboard PlayerScores entries that look like real accounts
    (datapack fake players such as '#counter' or objective names are ignored).
    """
    names, found = set(names), set()
    for name in names:
        parts = name.split('/')
        if len(parts) == 2 and parts[0] in PLAYER_TREES:
            stem = parts[1].split('.')[0]
            if UUID.fullmatch(stem):
                found.add(stem.lower())
    if 'level.dat' in names:
        try:
            raw, _ = nbt_unwrap(read('level.dat'))
            value = nbt_get(raw, ('Data', 'Player', 'UUID'))
            if isinstance(value, list) and len(value) == 4:
                found.add(uuid_from_ints(value))
        except (NbtError, OSError, EOFError):
            pass
    if 'data/scoreboard.dat' in names:
        try:
            raw, _ = nbt_unwrap(read('data/scoreboard.dat'))
            objectives = {str(o.get('Name', '')).lower() for o in nbt_get(raw, ('data', 'Objectives')) or []
                          if isinstance(o, dict)}
            for entry in nbt_get(raw, ('data', 'PlayerScores')) or []:
                name = entry.get('Name') if isinstance(entry, dict) else None
                if isinstance(name, str) and (UUID.fullmatch(name) or (
                        USERNAME.fullmatch(name) and name.lower() not in objectives)):
                    found.add(name.lower())
        except (NbtError, OSError, EOFError):
            pass
    for value in list(found):
        if UUID.fullmatch(value):
            found.add(value.replace('-', ''))
    return frozenset(value for value in found if len(value) >= 3)


_ID_PATTERNS = {}


def identifier_pattern(identifiers):
    """Case-insensitive matcher; account names match only as whole words."""
    key = frozenset(identifiers)
    if key not in _ID_PATTERNS:
        parts = [re.escape(i) if re.fullmatch(r'[0-9a-f-]{32,36}', i) else
                 r'(?<![A-Za-z0-9_])' + re.escape(i) + r'(?![A-Za-z0-9_])'
                 for i in sorted(key, key=len, reverse=True)]
        _ID_PATTERNS[key] = re.compile('|'.join(parts), re.I) if parts else None
    return _ID_PATTERNS[key]


def contains_identifier(data, identifiers):
    pattern = identifier_pattern(identifiers or ())
    if pattern is None:
        return False
    if data[:2] == b'\x1f\x8b':
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError):
            pass
    return pattern.search(data.decode('latin-1')) is not None


def file_contains_identifier(path, identifiers, chunk=4 * MiB):
    """Streaming scan of a file already on disk (gzip members are decompressed)."""
    pattern = identifier_pattern(identifiers or ())
    if pattern is None:
        return False
    path = Path(path)
    with path.open('rb') as stream:
        gzipped = stream.read(2) == b'\x1f\x8b'
    overlap = max(len(i) for i in identifiers) + 1
    opener = gzip.open if gzipped else open
    try:
        with opener(path, 'rb') as stream:
            tail = ''
            for data in iter(lambda: stream.read(chunk), b''):
                text = tail + data.decode('latin-1')
                if pattern.search(text):
                    return True
                tail = text[-overlap:]
    except (OSError, EOFError):
        if gzipped:
            return file_contains_identifier_raw(path, pattern, overlap, chunk)
        raise
    return False


def file_contains_identifier_raw(path, pattern, overlap, chunk):
    with Path(path).open('rb') as stream:
        tail = ''
        for data in iter(lambda: stream.read(chunk), b''):
            text = tail + data.decode('latin-1')
            if pattern.search(text):
                return True
            tail = text[-overlap:]
    return False


def redact_json(data, identifiers):
    """Drop player-file entries and identifiers from a JSON receipt; None if unchanged."""
    text = data.decode('utf-8')
    pattern = identifier_pattern(identifiers or ())
    if not (pattern and pattern.search(text)) and not re.search(
            r'"(?:\./)?(?:playerdata|stats|advancements)/', text):
        return None, 0
    removed = 0

    def bad(value):
        return isinstance(value, str) and bool(PLAYER_PATH.search(value) or (pattern and pattern.search(value)))

    def walk(value):
        nonlocal removed
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if bad(key) or (isinstance(item, str) and PLAYER_PATH.search(item) and key in ('path', 'file')):
                    removed += 1
                    continue
                result[key] = walk(item)
            return result
        if isinstance(value, list):
            result = []
            for item in value:
                if bad(item) or (isinstance(item, dict) and bad(item.get('path'))):
                    removed += 1
                    continue
                result.append(walk(item))
            return result
        if isinstance(value, str) and pattern and pattern.search(value):
            removed += 1
            value = pattern.sub(REDACTED, value)
        return value

    result = walk(json.loads(text))
    if not removed:
        return None, 0
    indent = 2 if text.lstrip().startswith('{\n') or text.lstrip().startswith('[\n') else None
    out = json.dumps(result, indent=indent) + ('\n' if text.endswith('\n') else '')
    return out.encode(), removed


def public_member(name, data, identifiers):
    """Privacy transform for one save metadata file.

    Returns (action, data, note): action is 'drop', 'keep' or 'transform'.
    """
    parts = name.split('/')
    if parts[0] in PLAYER_TREES:
        return 'drop', None, 'player data'
    if name == 'session.lock':
        return 'drop', None, 'session lock'
    if name == 'level.dat_old':
        return 'drop', None, 'level backup containing player state'
    lowered = name.lower()
    if 'distanthorizons' in lowered or 'distant_horizons' in lowered:
        return 'drop', None, 'Distant Horizons cache'
    if name == 'level.dat':
        out, removed = strip_level_player(data)
        return ('transform', out, 'Data.Player removed') if removed else ('keep', data, None)
    if name.endswith('scoreboard.dat'):
        out, removed = strip_scoreboard_players(data)
        return ('transform', out, f'{removed} PlayerScores entries removed') if removed else ('keep', data, None)
    if name.endswith('.json'):
        try:
            out, removed = redact_json(data, identifiers)
        except (UnicodeDecodeError, json.JSONDecodeError):
            out, removed = None, 0
        if out is not None:
            data = out
            if contains_identifier(data, identifiers):
                return 'drop', None, 'player identifier could not be redacted'
            return 'transform', data, f'{removed} player-file entries/identifiers redacted'
    if contains_identifier(data, identifiers):
        return 'drop', None, 'contains a player identifier'
    return 'keep', data, None


# --------------------------------------------------------------------------
# Anvil region headers and world layout


def region_bitmap(header):
    """1024-bit chunk presence, LSB-first, index (cx&31)+(cz&31)*32 like the location table."""
    bits, count = bytearray(128), 0
    for index in range(min(1024, len(header) // 4)):
        entry = header[index * 4:index * 4 + 4]
        if int.from_bytes(entry[:3], 'big') > 0 and entry[3] > 0:
            bits[index >> 3] |= 1 << (index & 7)
            count += 1
    return count, bytes(bits)


def bitmap_has(bitmap, cx, cz):
    index = (cx & 31) + (cz & 31) * 32
    return bool(bitmap[index >> 3] >> (index & 7) & 1)


def bucket(x):
    return x // 16


def world_destination(relative):
    """Map a save-relative path to its world/ repository path, or None for metadata."""
    parts = relative.split('/')
    if len(parts) < 2 or parts[-2] not in REGION_KINDS:
        return None
    name, kind, dims = parts[-1], parts[-2], parts[:-2]
    region, external = REGION_FILE.fullmatch(name), EXTERNAL_CHUNK.fullmatch(name)
    if region:
        rx, rz = int(region[1]), int(region[2])
    elif external and kind == 'region':
        rx, rz = int(external[1]) // 32, int(external[2]) // 32
    else:
        return None
    base = f'world/{kind}' if not dims else 'world/dimensions/' + '/'.join(dims) + f'/{kind}'
    return {'kind': kind, 'x': rx, 'z': rz, 'path': f'{base}/{bucket(rx)}/{name}',
            'dimension': '/'.join(dims) or 'overworld', 'external': bool(external)}


def read_header(path, size=4096):
    with Path(path).open('rb') as stream:
        return stream.read(size)


@dataclass
class WorldEntry:
    source: str
    path: str
    size: int
    kind: str
    local: Path | None = None
    data: bytes | None = None  # transformed content held in memory until written
    note: str | None = None
    region: dict | None = None
    digest: Digest | None = None


def plan_world(save, *, hash_files=False):
    """Classify every save file. Read-only: never writes to or locks the save."""
    save = Path(save).resolve()
    files, inventory_excluded = inventory(save)
    names = [record['path'] for record in files]
    identifiers = collect_identifiers(names, lambda name: (save / name).read_bytes())
    plan = {'entries': [], 'excluded': {}, 'excluded_paths': [], 'omitted_empty': [], 'warnings': [],
            'identifiers': identifiers, 'transforms': {}}
    for path in inventory_excluded:
        if not contains_identifier(path.encode(), identifiers):
            plan['excluded_paths'].append(path)
        plan['excluded']['inventory exclusion (lock, Finder, LOD cache, diagnostics)'] = (
            plan['excluded'].get('inventory exclusion (lock, Finder, LOD cache, diagnostics)', 0) + 1)
    for record in files:
        relative, size = record['path'], record['bytes']
        source = save / relative
        destination = world_destination(relative)
        if destination:
            if size == 0:
                plan['omitted_empty'].append(relative)
                continue
            entry = WorldEntry(relative, destination['path'], size, destination['kind'], local=source,
                               region=destination)
            if destination['kind'] == 'region' and not destination['external']:
                header = read_header(source)
                if size < 8192 or size % 4096:
                    plan['warnings'].append(f'{relative}: unusual region length {size}')
                count, bits = region_bitmap(header)
                destination.update({'chunks': count, 'chunk_bitmap': base64.b64encode(bits).decode()})
            if hash_files:
                entry.digest = hash_file(source)
            plan['entries'].append(entry)
            continue
        action, data, note = 'keep', None, None
        parts = relative.split('/')
        lowered = relative.lower()
        if (parts[0] in PLAYER_TREES or relative in ('session.lock', 'level.dat_old') or
                'distanthorizons' in lowered or 'distant_horizons' in lowered):
            action, note = 'drop', public_member(relative, b'', identifiers)[2]
        elif relative == 'level.dat' or relative.endswith(('scoreboard.dat', '.json')):
            action, data, note = public_member(relative, source.read_bytes(), identifiers)
        elif file_contains_identifier(source, identifiers):
            action, note = 'drop', 'contains a player identifier'
        if action == 'drop':
            plan['excluded'][note] = plan['excluded'].get(note, 0) + 1
            if not contains_identifier(relative.encode(), identifiers) and parts[0] not in PLAYER_TREES:
                plan['excluded_paths'].append(relative)
            continue
        entry = WorldEntry(relative, 'world/meta/' + relative, size, 'meta', local=source)
        if action == 'transform':
            entry.data, entry.note, entry.local, entry.size = data, note, None, len(data)
            entry.digest = hash_bytes(data)
            plan['transforms'][relative] = note
        elif hash_files:
            entry.digest = hash_file(source)
        plan['entries'].append(entry)
    return plan


def git_revision(path):
    try:
        result = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True,
                                text=True, timeout=10, check=True)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def world_frame(save, frame_path=None):
    if frame_path:
        return json.loads(Path(frame_path).read_text())
    coverage = Path(save) / 'city-coverage.json'
    if coverage.exists():
        return json.loads(coverage.read_text()).get('frame')
    return None


def regions_index(plan, *, captured_at, source_revision):
    regions = []
    for entry in plan['entries']:
        meta = entry.region
        if not meta or meta['kind'] != 'region' or meta['dimension'] != 'overworld' or meta['external']:
            continue
        regions.append({'x': meta['x'], 'z': meta['z'], 'path': entry.path, 'bytes': entry.size,
                        'sha256': entry.digest.sha256 if entry.digest else None,
                        'chunks': meta['chunks'], 'chunk_bitmap': meta['chunk_bitmap']})
    regions.sort(key=lambda item: (item['x'], item['z']))
    return {'schema': REGIONS_SCHEMA,
            'revision_note': ('Raw Anvil files at world/region/<bx>/r.X.Z.mca with bx = floor(X/16). '
                              'Pin the dataset commit that contains this index and fetch regions from '
                              'that same revision. Captured ' + str(captured_at) + ' from Earthcraft source '
                              'revision ' + str(source_revision) + '.'),
            'regions': regions}


def world_manifest(plan, *, frame, captured_at, source_revision, level, extra_files=()):
    files = [{'path': e.path, 'source': e.source, 'bytes': e.size,
              'sha256': e.digest.sha256 if e.digest else None, 'kind': e.kind,
              **({'transform': e.note} if e.note else {})} for e in plan['entries']]
    files += list(extra_files)
    files.sort(key=lambda item: item['path'])
    return {'schema': MANIFEST_SCHEMA, 'captured_at': captured_at, 'source_revision': source_revision,
            'frame': frame, 'frame_sha256': hashlib.sha256(canonical_json(frame)).hexdigest() if frame else None,
            'world_name': level.get('level_name'), 'minecraft_version': level.get('minecraft_version'),
            'data_version': level.get('data_version'), 'metres_per_block': 1,
            'layout': {'region': 'world/region/{bx}/r.{x}.{z}.mca', 'entities': 'world/entities/{bx}/r.{x}.{z}.mca',
                       'poi': 'world/poi/{bx}/r.{x}.{z}.mca', 'bucket': 'bx = floor(x / 16)',
                       'other_dimensions': 'world/dimensions/<DIM>/<kind>/{bx}/r.{x}.{z}.mca',
                       'metadata': 'world/meta/<save-relative path>', 'regions_index': 'world/index/regions.json'},
            'file_count': len(files), 'bytes': sum(item['bytes'] for item in files),
            'files': files,
            'omitted_empty_region_files': sorted(plan['omitted_empty']),
            'excluded': {'categories': dict(sorted(plan['excluded'].items())),
                         'paths': sorted(plan['excluded_paths'])},
            'privacy': {'player_data_uploaded': False,
                        'never_uploaded': ['playerdata/', 'stats/', 'advancements/', 'session.lock',
                                           'level.dat_old', 'Distant Horizons caches'],
                        'transforms': dict(sorted(plan['transforms'].items())),
                        'identifier_scan': 'passed for every uploaded metadata file'},
            'coverage_note': 'Partial Chicagoland at 1 m per block. Absent regions are not generated, not empty.'}


# --------------------------------------------------------------------------
# Context and logging


@dataclass
class Context:
    repo: str
    guard: DiskGuard
    hub: object
    retry: Retry
    batch_bytes: int = GiB
    batch_files: int = 400
    expand: bool = True
    log_name: str | None = None
    echo: bool = True
    events: list = field(default_factory=list)

    def log(self, **event):
        event = {'utc': utc_now(), **event}
        line = json.dumps(event, sort_keys=True, default=str)
        self.events.append(event)
        if self.echo:
            print(line, flush=True)
        if self.log_name:
            try:
                self.guard.append_line(self.log_name, line)
            except DiskGuardError:
                pass

    def committer(self, ledger, label, on_flushed=None):
        return BatchCommitter(self.hub, self.repo, ledger, self.guard, retry=self.retry, log=self.log,
                              max_bytes=self.batch_bytes, max_files=self.batch_files, label=label,
                              expand=self.expand, on_flushed=on_flushed)

    def check_destination(self):
        owner = self.retry.call(self.hub.whoami, what='whoami')
        if self.repo.split('/')[0] != owner:
            raise ValueError('Destination dataset must belong to the authenticated user')
        info = self.retry.call(lambda: self.hub.info(self.repo), what='repo_info')
        return info

    def fetch_json(self, repo, path, revision=None, limit=64 * MiB, expected=None):
        """Bounded download of one small file into scratch; returns bytes, keeps nothing."""
        self.guard.check(expected or limit // 8, f'download {path}')
        local_dir = self.guard.ensure_dir('fetch')
        local = self.retry.call(lambda: self.hub.download(repo, path, revision, local_dir),
                                what='hf_hub_download')
        try:
            if local.stat().st_size > limit:
                raise ValueError(f'{path} exceeds the {human(limit)} metadata bound')
            raw = local.read_bytes()
        finally:
            self.guard.remove(local)
            self.guard.clear('fetch')
        return raw


# --------------------------------------------------------------------------
# push-files


def push_files(ctx, src, dest, include=(), exclude=()):
    src = Path(src).resolve()
    dest = dest.strip('/')
    safe_repo_path(dest)
    ctx.check_destination()
    ledger = Ledger(ctx.guard, 'files-' + hashlib.sha256((str(src) + '|' + dest).encode()).hexdigest()[:12],
                    ctx.repo)
    committer = ctx.committer(ledger, f'Archive {dest}')
    count = 0
    for directory, directories, files in os.walk(src, followlinks=False):
        directories.sort()
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(src).as_posix()
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'Refusing non-regular file: {relative}')
            if include and not any(fnmatch.fnmatch(relative, pattern) for pattern in include):
                continue
            if any(fnmatch.fnmatch(relative, pattern) for pattern in exclude):
                continue
            committer.add(Item(f'{dest}/{relative}', path, hash_file(path), scratch=False,
                               source={'path': relative}))
            count += 1
    committer.close()
    return {'files': count, **{k: v for k, v in committer.stats.items() if k != 'commits'},
            'commits': [c['oid'] for c in committer.stats['commits']]}


# --------------------------------------------------------------------------
# push-world


def catalog_world_entry(manifest_digest, regions_digest, manifest):
    regions = [f for f in manifest['files'] if f['kind'] == 'region']
    return {'status': 'published', 'captured_at': manifest['captured_at'],
            'source_revision': manifest['source_revision'],
            'manifest': {'path': 'world/manifest.json', 'bytes': manifest_digest.size,
                         'sha256': manifest_digest.sha256},
            'regions_index': {'path': 'world/index/regions.json', 'bytes': regions_digest.size,
                              'sha256': regions_digest.sha256, 'schema': REGIONS_SCHEMA},
            'region_path_template': 'world/region/{bx}/r.{x}.{z}.mca', 'bucket_rule': 'bx = floor(x / 16)',
            'region_files': len(regions), 'region_bytes': sum(f['bytes'] for f in regions),
            'minecraft_version': manifest['minecraft_version'], 'data_version': manifest['data_version'],
            'player_data_included': False}


def push_world(ctx, save, *, only_changed=False, dry_run=False, frame_path=None, source_revision=None,
               update_catalog=True):
    save = Path(save).resolve()
    if not (save / 'level.dat').is_file():
        raise ValueError('A Minecraft save with level.dat is required')
    revision = source_revision or git_revision(save)
    if dry_run:
        return plan_world_dry_run(ctx, save, only_changed=only_changed, frame_path=frame_path,
                                  source_revision=revision)
    ctx.check_destination()
    with save_lock(save):  # refuses while Minecraft has the world open
        captured_at = utc_now()
        before = inventory(save)[0]
        plan = plan_world(save, hash_files=True)
        level = level_summary((save / 'level.dat').read_bytes())
        remote = {}
        if only_changed:
            remote = load_remote_manifest(ctx)
        ledger = Ledger(ctx.guard, 'world', ctx.repo)
        committer = ctx.committer(ledger, 'Sync world')
        identifiers = plan['identifiers']
        skipped_unchanged = 0
        ordered = sorted(plan['entries'], key=lambda e: (e.kind == 'meta', e.path))
        for entry in ordered:
            if remote.get(entry.path) == entry.digest.sha256:
                skipped_unchanged += 1
                continue
            if entry.data is not None:
                if contains_identifier(entry.data, identifiers):
                    raise RuntimeError(f'Privacy gate: {entry.source} still contains a player identifier')
                local = ctx.guard.write_bytes(ctx.guard.path('xform', entry.path), entry.data)
                committer.add(Item(entry.path, local, entry.digest, scratch=True, source={'save': entry.source}))
            else:
                if entry.kind == 'meta' and file_contains_identifier(entry.local, identifiers):
                    raise RuntimeError(f'Privacy gate: {entry.source} contains a player identifier')
                committer.add(Item(entry.path, entry.local, entry.digest, scratch=False,
                                   source={'save': entry.source}))
        committer.flush()
        regions = regions_index(plan, captured_at=captured_at, source_revision=revision)
        regions_raw = canonical_json(regions)
        regions_digest = hash_bytes(regions_raw)
        manifest = world_manifest(plan, frame=world_frame(save, frame_path), captured_at=captured_at,
                                  source_revision=revision, level=level,
                                  extra_files=[{'path': 'world/index/regions.json', 'source': None,
                                                'bytes': regions_digest.size, 'sha256': regions_digest.sha256,
                                                'kind': 'index'}])
        manifest_raw = (json.dumps(manifest, indent=1, sort_keys=True) + '\n').encode()
        final = [('world/index/regions.json', regions_raw), ('world/manifest.json', manifest_raw)]
        if update_catalog:
            catalog = json.loads(ctx.fetch_json(ctx.repo, 'catalog.json'))
            catalog['world'] = catalog_world_entry(hash_bytes(manifest_raw), regions_digest, manifest)
            final.append(('catalog.json', canonical_json(catalog)))
        for path, raw in final:
            if contains_identifier(raw, identifiers):
                raise RuntimeError(f'Privacy gate: {path} contains a player identifier')
            local = ctx.guard.write_bytes(ctx.guard.path('xform', path), raw)
            committer.add(Item(path, local, hash_bytes(raw), scratch=True))
        committer.close()  # manifest and catalog are committed last
        if inventory(save)[0] != before:
            raise RuntimeError('Save changed while its lock was held; remote manifest may be inconsistent')
    ctx.guard.prune('xform')
    return {'state': 'complete', 'captured_at': captured_at, 'files': len(plan['entries']),
            'skipped_unchanged': skipped_unchanged, 'regions': len(regions['regions']),
            **{k: v for k, v in committer.stats.items() if k != 'commits'},
            'commits': [c['oid'] for c in committer.stats['commits']]}


def load_remote_manifest(ctx):
    try:
        manifest = json.loads(ctx.fetch_json(ctx.repo, 'world/manifest.json'))
    except Exception as error:  # noqa: BLE001
        if getattr(getattr(error, 'response', None), 'status_code', None) == 404 or 'EntryNotFound' in type(error).__name__:
            return {}
        raise
    if manifest.get('schema') != MANIFEST_SCHEMA:
        raise ValueError('Remote world manifest has an unsupported schema')
    return {item['path']: item['sha256'] for item in manifest['files'] if item.get('sha256')}


def plan_world_dry_run(ctx, save, *, only_changed, frame_path, source_revision):
    """Headers only, no lock, no uploads; safe while the game has the save open."""
    plan = plan_world(save, hash_files=only_changed)
    remote = load_remote_manifest(ctx) if only_changed else {}
    entries = plan['entries']
    upload = [e for e in entries if not (e.digest and remote.get(e.path) == e.digest.sha256)]
    by_kind = {}
    for entry in entries:
        slot = by_kind.setdefault(entry.kind, {'files': 0, 'bytes': 0})
        slot['files'] += 1
        slot['bytes'] += entry.size
    regions = [e for e in entries if e.kind == 'region' and not e.region['external']]
    ordered = sorted(upload, key=lambda e: (e.kind == 'meta', e.path))
    # Data batches, then one final commit with regions.json, manifest.json and catalog.json.
    commits = simulate_batches([e.size for e in ordered], ctx.batch_bytes, ctx.batch_files) + 1
    level = level_summary((save / 'level.dat').read_bytes())
    buckets = {}
    for entry in regions:
        b = bucket(entry.region['x'])
        buckets[b] = buckets.get(b, 0) + 1
    try:
        guard = ctx.guard.status()
        guard['would_admit_writes'] = (guard['free_bytes'] >= guard['floor_bytes'] and
                                       guard['used_bytes'] <= guard['cap_bytes'])
    except OSError as error:
        guard = {'error': str(error)}
    return {
        'state': 'dry-run', 'save': save.name, 'lock_taken': False, 'uploads': 0,
        'source_revision': source_revision, 'frame_present': world_frame(save, frame_path) is not None,
        'minecraft_version': level['minecraft_version'], 'data_version': level['data_version'],
        'level_has_player_tag': level['has_player'],
        'by_kind': by_kind, 'total_files': len(entries), 'total_bytes': sum(e.size for e in entries),
        'to_upload_files': len(upload) + 3, 'to_upload_bytes': sum(e.size for e in upload),
        'only_changed': only_changed, 'unchanged_files': len(entries) - len(upload),
        'commits_planned': commits,
        'batch_limits': {'bytes': ctx.batch_bytes, 'files': ctx.batch_files},
        'regions_indexed': len(regions), 'region_chunks': sum(e.region['chunks'] for e in regions),
        'region_buckets': len(buckets), 'max_files_per_bucket': max(buckets.values()) if buckets else 0,
        'omitted_empty_region_files': len(plan['omitted_empty']),
        'excluded': plan['excluded'], 'excluded_paths': plan['excluded_paths'][:50],
        'transforms': plan['transforms'], 'player_identifiers_known': len(plan['identifiers']),
        'header_warnings': plan['warnings'][:20], 'disk_guard': guard,
        'final_files': ['world/index/regions.json', 'world/manifest.json', 'catalog.json'],
    }


# --------------------------------------------------------------------------
# migrate


class Held(Exception):
    """Content check refused publication; the file is not uploaded."""


TILE_MEMBER = re.compile(r'-?\d+_-?\d+/r\.-?\d+\.-?\d+\.mca(?:\.gz)?')
SNAPSHOT_GEO_MEMBER = re.compile(r'(?:DIM-?1/)?(?:region|entities|poi)/(?:r\.-?\d+\.-?\d+\.mca|c\.-?\d+\.-?\d+\.mcc)')


def check_tile_shard(local):
    with tarfile.open(local, 'r:') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if not members or any(not m.isfile() for m in members):
            raise Held('tile shard has non-regular members')
        bad = [n for n in names if n != 'manifest.json' and not TILE_MEMBER.fullmatch(n)]
        if bad or 'manifest.json' not in names:
            raise Held(f'tile shard has unexpected members ({len(bad)})')
        manifest = json.loads(archive.extractfile('manifest.json').read(4 * MiB))
    if 'player state' not in manifest.get('excludes', []):
        raise Held('tile shard manifest does not declare player state excluded')
    return {'tiles': len(names) - 1}


def check_validation(local, name):
    if name.endswith('.tar'):
        with tarfile.open(local, 'r:') as archive:
            for member in archive.getmembers():
                parts = member.name.split('/')
                if any(part in PLAYER_TREES for part in parts) or member.name.endswith('level.dat_old'):
                    raise Held('validation world contains player files')
                if not member.isfile() and not member.isdir():
                    raise Held('validation world contains links or devices')
                if parts[-1] == 'level.dat':
                    if level_summary(archive.extractfile(member).read(4 * MiB))['has_player']:
                        raise Held('validation level.dat contains Data.Player')
    elif name.endswith('manifest.json'):
        if json.loads(Path(local).read_text()).get('player_data_present') is not False:
            raise Held('validation manifest does not record player_data_present=false')


def repack_metadata(source, guard, target):
    """Rewrite the snapshot metadata shard without player state; deterministic like the packer."""
    with tarfile.open(source, 'r:gz') as archive:
        members = [m for m in archive.getmembers()]
        if any(not m.isfile() for m in members):
            raise Held('metadata shard has non-regular members')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    identifiers = collect_identifiers(contents, lambda name: contents[name])
    records, removed, transforms, kept = [], {}, {}, []
    for name in [m.name for m in members]:
        action, data, note = public_member(name, contents[name], identifiers)
        if action == 'drop':
            key = name.split('/')[0] + '/' if name.split('/')[0] in PLAYER_TREES else name
            removed[key] = removed.get(key, 0) + 1
            continue
        if action == 'transform':
            transforms[name] = note
        if contains_identifier(data, identifiers):
            raise Held(f'privacy gate: metadata member still identifies a player ({len(kept)})')
        kept.append((name, data))
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', compresslevel=1, mtime=0, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as out:
            for name, data in kept:
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                out.addfile(info, io.BytesIO(data))
                digest = hash_bytes(data)
                records.append({'path': name, 'bytes': digest.size, 'sha256': digest.sha256})
    raw = buffer.getvalue()
    guard.write_bytes(target, raw)
    return {'records': sorted(records, key=lambda r: r['path']), 'removed': removed, 'transforms': transforms,
            'digest': hash_bytes(raw), 'identifiers_found': len(identifiers),
            'original_files': len(members), 'original_bytes': sum(len(v) for v in contents.values())}


def public_snapshot_manifest(manifest, repack, *, source_repo, source_revision, source_manifest_sha256,
                             source_metadata_sha256):
    manifest = json.loads(json.dumps(manifest))
    for shard in manifest['shards']:
        if shard['key'] == 'metadata':
            old_files = shard['files']
            shard.update({'bytes': repack['digest'].size, 'sha256': repack['digest'].sha256,
                          'files': repack['records']})
        elif any(not SNAPSHOT_GEO_MEMBER.fullmatch(f['path']) for f in shard['files']):
            raise Held(f'geographic shard {shard["key"]} lists non-region members')
    removed_count = len(old_files) - len(repack['records'])
    manifest['file_count'] -= removed_count
    manifest['source_bytes'] += (sum(r['bytes'] for r in repack['records']) - sum(f['bytes'] for f in old_files))
    manifest['compressed_bytes'] = sum(s['bytes'] for s in manifest['shards'])
    manifest['public_redaction'] = {
        'schema': 'earthcraft.public-redaction.v1', 'redacted_utc': utc_now(),
        'source_repo': source_repo, 'source_revision': source_revision,
        'source_manifest_sha256': source_manifest_sha256, 'source_metadata_shard_sha256': source_metadata_sha256,
        'removed_files': repack['removed'], 'transformed_files': repack['transforms'],
        'note': ('Player state was removed for public distribution: playerdata/, stats/, advancements/, '
                 'level.dat_old and level.dat Data.Player; scoreboard player scores emptied; player-file '
                 'entries removed from receipts. Geographic shards are byte-identical to the source.')}
    return manifest


def snapshot_card(manifest):
    redaction = manifest['public_redaction']
    return f'''# Earthcraft world snapshot `{manifest['snapshot_id']}`

A complete, coordinate-preserving snapshot of the Earthcraft save as it stood on
{manifest['created_utc'][:10]}: partial Chicagoland at one block per metre in
Minecraft Java {manifest['minecraft_version']}. It is not a copy of the whole
Earth. **{manifest['file_count']:,} files** in {len(manifest['shards'])} archives
({manifest['compressed_bytes'] / 1e9:.2f} GB compressed); **{manifest['minecraft_chunks']:,}
serialized chunks**.

Each `shards/<dimension>/x<X>_z<Z>.tar.gz` groups up to {manifest['region_span']} x {manifest['region_span']}
regions; `shards/metadata.tar.gz` holds level.dat, datapacks, the resource pack
and source receipts. `manifest.json` lists every file's SHA-256 and every
archive checksum.

This public copy has **no player data**. Removed or rewritten for publication:
`playerdata/`, `stats/`, `advancements/`, `level.dat_old`, `Data.Player` in
`level.dat`, scoreboard player scores, and player-file entries in receipts
(`public_redaction` in the manifest records the source revision and hashes).
The geographic shards are byte-identical to the private original. A restored
world spawns a fresh player at the world spawn.

Restore into a new save with Python 3 (standard library only):

```sh
python restore_world_snapshot.py --package . --destination /path/to/saves/Earthcraft
```

Repeat `--shard <key>` to restore selected areas; metadata is always included.
Removed: {json.dumps(redaction['removed_files'], sort_keys=True)}.
'''


class Copier:
    """Download one source file into guarded scratch, verify, check, queue for commit."""

    def __init__(self, ctx, committer, ledger, source_repo, source_revision, source_files, dest_files):
        self.ctx, self.committer, self.ledger = ctx, committer, ledger
        self.source_repo, self.source_revision = source_repo, source_revision
        self.source_files, self.dest_files = source_files, dest_files
        self.counts = {'copied': 0, 'present': 0, 'held': [], 'source_mismatch': []}

    def admit(self, size, what):
        try:
            self.ctx.guard.check(size + DOWNLOAD_MARGIN, what)
        except DiskGuardError:
            if not self.committer.pending:
                raise
            self.committer.flush()
            self.ctx.guard.check(size + DOWNLOAD_MARGIN, what)

    def fetch(self, path):
        remote = self.source_files[path]
        self.admit(remote.size, f'download {path}')
        local_dir = self.ctx.guard.ensure_dir('dl')
        local = Path(self.ctx.retry.call(lambda: self.ctx.hub.download(self.source_repo, path,
                                                                       self.source_revision, local_dir),
                                         what='hf_hub_download'))
        if self.ctx.guard.root not in local.absolute().parents:
            raise RuntimeError('Download escaped the guarded scratch root')
        digest = hash_file(local)
        ok, reason = remote_matches(remote, digest)
        if not ok:
            self.ctx.guard.remove(local)
            self.counts['source_mismatch'].append(path)
            raise VerificationError(f'Source checksum mismatch for {path}: {reason}')
        return local, digest

    def already(self, source_path, dest_path):
        remote, present = self.source_files[source_path], self.dest_files.get(dest_path)
        if present is not None and present.size == remote.size and (
                (remote.sha256 and present.sha256 == remote.sha256) or
                (not remote.sha256 and remote.blob_id and present.blob_id == remote.blob_id)):
            if dest_path not in self.ledger.files:
                self.ledger.files[dest_path] = {'sha256': remote.sha256, 'bytes': remote.size,
                                                'blob_id': present.blob_id, 'commit': 'preexisting',
                                                'verified_utc': utc_now(),
                                                'source': {'repo': self.source_repo, 'path': source_path,
                                                           'revision': self.source_revision}}
            self.counts['present'] += 1
            return True
        return False

    def copy(self, source_path, dest_path, check=None):
        if self.already(source_path, dest_path):
            return 'present'
        local, digest = self.fetch(source_path)
        if check:
            try:
                check(local)
            except Held as error:
                self.ctx.guard.remove(local)
                self.counts['held'].append({'path': source_path, 'reason': str(error)})
                self.ctx.log(event='held', path=source_path, reason=str(error))
                return 'held'
        self.committer.add(Item(dest_path, local, digest, scratch=True,
                                source={'repo': self.source_repo, 'path': source_path,
                                        'revision': self.source_revision}))
        self.counts['copied'] += 1
        return 'queued'


def migrate(ctx, groups, limit=None):
    ctx.check_destination()
    hub, guard = ctx.hub, ctx.guard
    source = ctx.retry.call(lambda: hub.info(SOURCE_REPO), what='repo_info source')
    source_revision = source['sha']
    source_files = ctx.retry.call(lambda: hub.list_files(SOURCE_REPO, source_revision), what='list source')
    dest_files = ctx.retry.call(lambda: hub.list_files(ctx.repo, 'main', prefixes=[
        'tiles', 'validations', 'snapshots', 'releases', 'archive']), what='list destination')
    ledger = Ledger(guard, 'migrate', ctx.repo)
    ledger.data['notes']['source'] = {'repo': SOURCE_REPO, 'revision': source_revision}

    def cleanup():
        """Self-cleaning scratch: drop hub download metadata and empty directories after each commit."""
        guard.clear('dl', '.cache')
        guard.prune('dl')
        guard.prune('xform')

    committer = ctx.committer(ledger, 'Migrate earthcraft-minecraft-world', on_flushed=cleanup)
    copier = Copier(ctx, committer, ledger, SOURCE_REPO, source_revision, source_files, dest_files)
    report = {'source_revision': source_revision, 'groups': sorted(groups)}
    if 'tiles' in groups:
        committer.label = 'Migrate tiles/v1'
        paths = sorted(p for p in source_files if p.startswith('generated-tiles/v1/'))
        paths = paths[:limit] if limit else paths
        for path in paths:
            check = check_tile_shard if path.endswith('.tar') else None
            copier.copy(path, 'tiles/v1/' + path[len('generated-tiles/v1/'):], check)
        committer.flush()
        report['tiles'] = {'selected': len(paths)}
    if 'validations' in groups:
        committer.label = 'Migrate validations/v1'
        paths = sorted(p for p in source_files if p.startswith('validations/v1/'))
        for path in paths:
            copier.copy(path, path, lambda local, p=path: check_validation(local, p))
        committer.flush()
        report['validations'] = {'selected': len(paths)}
    if 'snapshot' in groups:
        committer.label = f'Migrate snapshot {SNAPSHOT_ID} (player data removed)'
        report['snapshot'] = migrate_snapshot(ctx, copier, committer, ledger, source_files, source_revision)
    if 'oakbrook' in groups:
        committer.label = 'Archive Oakbrook files missing from releases/'
        report['oakbrook'] = migrate_oakbrook(ctx, committer, ledger, dest_files)
    committer.flush()
    if 'card' in groups:
        report['card'] = publish_card(ctx, ledger, source_revision, source_files)
    cleanup()
    ledger.save()
    report.update({'copied': copier.counts['copied'], 'already_present': copier.counts['present'],
                   'held': copier.counts['held'], 'source_mismatch': copier.counts['source_mismatch'],
                   **{k: v for k, v in committer.stats.items() if k != 'commits'},
                   'commits': [c['oid'] for c in committer.stats['commits']]})
    return report


def migrate_snapshot(ctx, copier, committer, ledger, source_files, source_revision):
    guard = ctx.guard
    prefix = SNAPSHOT_PREFIX
    manifest_path = prefix + 'manifest.json'
    raw_manifest = ctx.fetch_json(SOURCE_REPO, manifest_path, source_revision)
    if hash_bytes(raw_manifest).blob_id != source_files[manifest_path].blob_id:
        raise VerificationError('Source snapshot manifest differs from its listing')
    manifest = json.loads(raw_manifest)
    shards = {shard['path']: shard for shard in manifest['shards']}
    for shard_path, shard in sorted(shards.items()):
        if shard['key'] == 'metadata':
            continue
        if any(not SNAPSHOT_GEO_MEMBER.fullmatch(f['path']) for f in shard['files']):
            raise Held(f'geographic shard {shard["key"]} lists non-region members')
        remote = source_files[prefix + shard_path]
        if remote.sha256 != shard['sha256'] or remote.size != shard['bytes']:
            raise VerificationError(f'Snapshot shard listing differs from manifest: {shard_path}')
        copier.copy(prefix + shard_path, prefix + shard_path)
    copier.copy(prefix + 'restore_world_snapshot.py', prefix + 'restore_world_snapshot.py')
    metadata_source = prefix + 'shards/metadata.tar.gz'
    metadata = shards['shards/metadata.tar.gz']
    local, digest = copier.fetch(metadata_source)
    if digest.sha256 != metadata['sha256']:
        raise VerificationError('Metadata shard differs from the snapshot manifest')
    target = guard.path('xform', metadata_source)
    repack = repack_metadata(local, guard, target)
    guard.remove(local)
    public = public_snapshot_manifest(manifest, repack, source_repo=SOURCE_REPO, source_revision=source_revision,
                                      source_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
                                      source_metadata_sha256=metadata['sha256'])
    committer.add(Item(metadata_source, target, repack['digest'], scratch=True,
                       source={'repo': SOURCE_REPO, 'path': metadata_source, 'revision': source_revision,
                               'transform': 'player data removed'}))
    for path, raw in ((prefix + 'README.md', snapshot_card(public).encode()),
                      (manifest_path, (json.dumps(public, indent=2, sort_keys=True) + '\n').encode())):
        local = guard.write_bytes(guard.path('xform', path), raw)
        committer.add(Item(path, local, hash_bytes(raw), scratch=True,
                           source={'repo': SOURCE_REPO, 'revision': source_revision, 'generated': True}))
    committer.flush()
    ledger.data['notes']['snapshot'] = {'removed_files': repack['removed'], 'transforms': repack['transforms'],
                                        'file_count': public['file_count']}
    return {'shards': len(shards), 'file_count': public['file_count'], 'removed_files': repack['removed'],
            'transformed': sorted(repack['transforms']), 'metadata_sha256': repack['digest'].sha256}


def release_prefix(ctx):
    catalog = json.loads(ctx.fetch_json(ctx.repo, 'catalog.json'))
    release = catalog['release_id_sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', release):
        raise ValueError('Catalog release id is not a sha256')
    return f'releases/{release}/'


def migrate_oakbrook(ctx, committer, ledger, dest_files):
    prefix = release_prefix(ctx)
    result = {}
    for repo, sub in OAKBROOK_REPOS.items():
        info = ctx.retry.call(lambda r=repo: ctx.hub.info(r), what='repo_info oakbrook')
        files = ctx.retry.call(lambda r=repo, s=info['sha']: ctx.hub.list_files(r, s), what='list oakbrook')
        identical, archived, mismatched, infrastructure = 0, [], [], []
        copier = Copier(ctx, committer, ledger, repo, info['sha'], files, dest_files)
        archive = f'archive/{repo.split("/")[1]}@{info["sha"][:12]}/'
        for path, remote in sorted(files.items()):
            if path == '.gitattributes':
                infrastructure.append(path)
                continue
            present = dest_files.get(prefix + sub + '/' + path)
            same = present is not None and present.size == remote.size and (
                (remote.sha256 and present.sha256 == remote.sha256) or
                (not remote.sha256 and present.blob_id == remote.blob_id))
            if same:
                identical += 1
                continue
            if present is not None:
                mismatched.append(path)
            copier.copy(path, archive + path)
            archived.append(path)
        result[repo] = {'revision': info['sha'], 'files': len(files), 'identical_in_releases': identical,
                        'archived_missing': archived, 'mismatched': mismatched, 'archive_prefix': archive,
                        'not_data': infrastructure}
    committer.flush()
    ledger.data['notes']['oakbrook'] = result
    return result


def descriptor(ctx, path, remote, raw=None):
    """{path, bytes, sha256}; small non-LFS files are fetched (bounded) to hash them."""
    if remote is None:
        raise VerificationError(f'Catalog target missing on the Hub: {path}')
    sha = remote.sha256
    if not sha:
        raw = raw if raw is not None else ctx.fetch_json(ctx.repo, path, expected=remote.size)
        digest = hash_bytes(raw)
        if digest.blob_id != remote.blob_id or digest.size != remote.size:
            raise VerificationError(f'Fetched {path} differs from its Hub listing')
        sha = digest.sha256
    return {'path': path, 'bytes': remote.size, 'sha256': sha}


ATTRIBUTION = [
    {'source': 'USGS 3D Elevation Program (3DEP) lidar, incl. 2022 Mid-North Illinois / Merrick-Surdex',
     'license': 'U.S. Government public domain', 'url': 'https://www.usgs.gov/3d-elevation-program'},
    {'source': 'OpenStreetMap contributors', 'license': 'ODbL-1.0', 'url': 'https://www.openstreetmap.org/copyright'},
    {'source': 'Overture Maps Foundation building footprints', 'license': 'ODbL-1.0 (buildings theme)',
     'url': 'https://docs.overturemaps.org/attribution/'},
    {'source': 'Cook County GIS building footprints 2022', 'license': 'Cook County terms of use',
     'url': 'https://www.cookcountyil.gov/terms-use'},
    {'source': 'Illinois Height Modernization / county lidar (ISGS clearinghouse)',
     'license': 'Public; attribution as recorded in receipts', 'url': 'https://clearinghouse.isgs.illinois.edu/'},
    {'source': 'USDA NAIP aerial imagery (ground colour only)', 'license': 'U.S. Government public domain',
     'url': 'https://naip-usdaonline.hub.arcgis.com/'},
    {'source': 'ESA WorldCover 2021 v200 (land cover classes)', 'license': 'CC-BY-4.0',
     'url': 'https://esa-worldcover.org/'},
    {'source': 'Chicago Water Tower photo skin: "Chicago Water Tower view from west" by Dough4872, '
               'Wikimedia Commons (resized, reprojected, masked)', 'license': 'CC-BY-SA-4.0',
     'url': 'https://commons.wikimedia.org/wiki/File:Chicago_Water_Tower_view_from_west.jpeg'},
]


def publish_card(ctx, ledger, source_revision, source_files):
    """Extend catalog.json additively and rewrite the dataset card for the new layout."""
    guard = ctx.guard
    remote = ctx.retry.call(lambda: ctx.hub.list_files(ctx.repo, 'main', prefixes=[
        'tiles', 'validations', 'snapshots', 'archive']), what='list destination')
    root = ctx.retry.call(lambda: ctx.hub.paths_info(ctx.repo, ['catalog.json'], revision='main'),
                          what='get_paths_info')
    catalog_raw_before = ctx.fetch_json(ctx.repo, 'catalog.json')
    if hash_bytes(catalog_raw_before).blob_id != root['catalog.json'].blob_id:
        raise VerificationError('catalog.json changed while it was read')
    catalog = json.loads(catalog_raw_before)
    previous = json.loads(catalog_raw_before)
    shards = {p: r for p, r in remote.items() if p.startswith('tiles/v1/shards/')}
    index_raw = ctx.fetch_json(ctx.repo, 'tiles/v1/index.json', expected=remote['tiles/v1/index.json'].size)
    tile_index = json.loads(index_raw)
    snapshot_path = SNAPSHOT_PREFIX + 'manifest.json'
    snapshot_raw = ctx.fetch_json(ctx.repo, snapshot_path, expected=remote[snapshot_path].size)
    snapshot = json.loads(snapshot_raw)
    if snapshot.get('public_redaction') is None:
        raise VerificationError('Published snapshot manifest lacks its public redaction record')
    catalog['layout'] = {
        'schema': 'earthcraft.hf-layout.v1', 'durable_store': ctx.repo,
        'world': 'world/', 'tiles': 'tiles/v1/', 'snapshots': 'snapshots/<id>/',
        'validations': 'validations/v1/<generation>/', 'archive': 'archive/',
        'releases': 'releases/<release_id_sha256>/ (unchanged)',
        'pinning': 'Fetch catalog.json at a commit SHA, then every path at that same SHA.'}
    catalog.setdefault('world', {
        'status': 'awaiting-first-sync', 'manifest_path': 'world/manifest.json',
        'regions_index_path': 'world/index/regions.json', 'regions_index_schema': REGIONS_SCHEMA,
        'region_path_template': 'world/region/{bx}/r.{x}.{z}.mca', 'bucket_rule': 'bx = floor(x / 16)',
        'player_data_included': False})
    catalog['tiles'] = {'v1': {
        'status': 'published', 'schema': tile_index.get('schema'), 'plan_sha256': tile_index.get('plan_sha256'),
        'index': descriptor(ctx, 'tiles/v1/index.json', remote['tiles/v1/index.json'], index_raw),
        'tile_count': len(tile_index.get('tiles', {})), 'shard_count': len(shards),
        'shard_bytes': sum(r.size for r in shards.values()),
        'shard_path_template': 'tiles/v1/shards/{sha256[0:2]}/{sha256}.tar',
        'legacy_path_prefix': {'from': 'generated-tiles/v1/', 'to': 'tiles/v1/', 'note': (
            'index.json is byte-identical to the migrated source, so its shard path fields keep the '
            'legacy prefix; replace it or use shard_path_template.')},
        'shard_format': 'tar of manifest.json + <tile>/r.X.Z.mca.gz (one 512 m generator-only region per tile)',
        'attribution': tile_index.get('attribution'),
        'privacy': 'Generator-only regions; no players, entities or block entities.',
        'source': {'repo': SOURCE_REPO, 'revision': source_revision, 'path': 'generated-tiles/v1/'}}}
    catalog['snapshots'] = [{
        'id': SNAPSHOT_ID, 'status': 'published', 'player_data_included': False,
        'manifest': descriptor(ctx, snapshot_path, remote[snapshot_path], snapshot_raw),
        'readme': descriptor(ctx, SNAPSHOT_PREFIX + 'README.md', remote.get(SNAPSHOT_PREFIX + 'README.md')),
        'restore_script': descriptor(ctx, SNAPSHOT_PREFIX + 'restore_world_snapshot.py',
                                     remote.get(SNAPSHOT_PREFIX + 'restore_world_snapshot.py')),
        'file_count': snapshot['file_count'], 'shard_count': len(snapshot['shards']),
        'compressed_bytes': snapshot['compressed_bytes'], 'minecraft_chunks': snapshot['minecraft_chunks'],
        'minecraft_version': snapshot['minecraft_version'], 'redaction': snapshot['public_redaction']['note'],
        'source': {'repo': SOURCE_REPO, 'revision': source_revision}}]
    validations = {}
    for path, item in sorted(remote.items()):
        parts = path.split('/')
        if len(parts) == 4 and parts[:2] == ['validations', 'v1']:
            entry = validations.setdefault(parts[2], {'generation_id': parts[2], 'files': []})
            entry['files'].append(descriptor(ctx, path, item))
    for entry in validations.values():
        entry.update({'status': 'validation_only_not_accepted', 'source_public_distribution_approved': False,
                      'note': ('Migrated by owner direction on 2026-10-16. Its receipts still record '
                               'public_distribution_approved=false, the validation-only quality gate.')})
    catalog['validations'] = {'v1': [validations[k] for k in sorted(validations)]}
    archived = {p: r for p, r in remote.items() if p.startswith('archive/')}
    if archived:
        catalog['archive'] = {'status': 'inactive-preserved',
                              'files': [descriptor(ctx, p, r) for p, r in sorted(archived.items())],
                              'note': 'Byte-identical files from retired datasets; not active catalog objects.'}
    catalog['attribution'] = ATTRIBUTION
    added = {'layout', 'world', 'tiles', 'snapshots', 'validations', 'archive', 'attribution'}
    for key, value in previous.items():  # existing descriptors stay exactly as they were
        if key not in added and catalog.get(key) != value:
            raise RuntimeError(f'Existing catalog descriptor changed: {key}')
    catalog_raw = canonical_json(catalog)
    card = dataset_card(catalog).encode()
    committer = ctx.committer(ledger, 'Describe the direct-store layout')
    for path, raw in (('README.md', card), ('catalog.json', catalog_raw)):
        local = guard.write_bytes(guard.path('xform', path), raw)
        committer.add(Item(path, local, hash_bytes(raw), scratch=True, source={'generated': True}))
    committer.close()
    return {'catalog_sha256': hash_bytes(catalog_raw).sha256, 'catalog_bytes': len(catalog_raw),
            'readme_bytes': len(card), 'commits': [c['oid'] for c in committer.stats['commits']]}


def dataset_card(catalog):
    tiles = catalog['tiles']['v1']
    snapshot = catalog['snapshots'][0]
    release = catalog['release_id_sha256']
    return f'''---
pretty_name: Earthcraft
language:
  - en
license: other
tags:
  - minecraft
  - geospatial
  - lidar
  - openstreetmap
  - chicago
---

# Earthcraft

Chicagoland rebuilt in Minecraft Java at one block per metre from public
elevation, lidar, building and map data. This dataset is the project's single
durable store: the generator writes here directly in bounded, verified commits.
Coverage is **partial**. A region, tile or cell that is not listed is not
generated; it is not empty terrain.

## Start here

Fetch [`catalog.json`](catalog.json) **at a pinned commit** and resolve every
other path at that same commit. Every descriptor carries `path`, `bytes` and
`sha256`; check them before use.

```sh
REV=$(curl -s https://huggingface.co/api/datasets/EricSpencer00/earthcraft | python3 -c 'import json,sys;print(json.load(sys.stdin)["sha"])')
curl -L "https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/$REV/catalog.json"
```

## Layout

| Path | Contents |
| --- | --- |
| `catalog.json` | Entry point. Existing atlas, evidence and Oakbrook descriptors are unchanged; `layout`, `world`, `tiles`, `snapshots`, `validations`, `archive` and `attribution` describe the rest. |
| `world/manifest.json` | Canonical save manifest: every file's path, bytes and SHA-256, the world frame, `captured_at`, and the Earthcraft source revision. Written last by each world sync; `catalog.json` `world.status` says whether the first sync has happened. |
| `world/index/regions.json` | Every overworld region: `x`, `z`, `path`, `bytes`, `sha256`, `chunks`, and `chunk_bitmap` (base64 of 128 bytes; bit `i = (cx & 31) + (cz & 31) * 32`, LSB-first). |
| `world/region/<bx>/r.X.Z.mca` | Raw Anvil region files, `bx = floor(X / 16)`. Also `world/entities/<bx>/`, `world/poi/<bx>/`. |
| `world/meta/` | `level.dat` without `Data.Player`, datapacks, the photo-skin resource pack, source receipts. |
| `tiles/v1/` | {tiles['tile_count']:,} generated 512 m tiles in {tiles['shard_count']:,} content-addressed tar shards ({tiles['shard_bytes'] / 1e9:.2f} GB) plus `index.json`. |
| `snapshots/{snapshot['id']}/` | The 2026-10-01 closed-save snapshot: {snapshot['file_count']:,} files in {snapshot['shard_count']} gzip shards, restorable with the bundled standard-library script. |
| `validations/v1/` | Small validation-only generations with their receipts (not accepted world content). |
| `releases/{release[:12]}.../` | The earlier public atlas and Oakbrook preview/native release, unchanged. |
| `archive/` | Files from retired datasets kept byte-identical; not active catalog objects. |

## Reading regions over HTTP

`resolve/<commit>/<path>` serves raw bytes with `Range` support and CORS, so a
browser can read one chunk without downloading a region:

1. Look up the region in `world/index/regions.json` and test its chunk bit.
2. `GET` bytes `0-8191` of `world/region/<bx>/r.X.Z.mca`. Location entry
   `4 * i` holds a 3-byte sector offset and a 1-byte sector count.
3. `GET` bytes `offset * 4096` to `(offset + count) * 4096 - 1`: a 4-byte big-endian
   length, a compression byte (2 = zlib), then the chunk NBT.

```sh
curl -r 0-8191 -L "https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/$REV/world/region/0/r.0.0.mca" -o header.bin
```

Python: `huggingface_hub.hf_hub_download("EricSpencer00/earthcraft", path, repo_type="dataset", revision=REV)`.
Generated tiles: read `tiles/v1/index.json`, then `tiles/v1/shards/<sha[0:2]>/<sha>.tar`
(the index's shard `path` fields keep the legacy `generated-tiles/v1/` prefix).

## Privacy

No player data is published. `playerdata/`, `stats/`, `advancements/`,
`level.dat_old`, the session lock and Distant Horizons caches are never uploaded;
`Data.Player` is removed from `level.dat`, scoreboard player scores are emptied,
and player-file entries are removed from receipts. Every published metadata file
is scanned for the save's player identifiers before upload.

## Attribution and licences

Earthcraft's source code is Apache-2.0 (GitHub); data licences are separate.
Credit, as recorded in the retained source receipts:

- **USGS 3D Elevation Program** lidar, including 2022 Mid-North Illinois lidar /
  **Merrick-Surdex** (U.S. public domain).
- **© OpenStreetMap contributors** (ODbL-1.0). Databases derived from OSM,
  including the generated tiles and world, are subject to ODbL-1.0 share-alike.
- **Overture Maps Foundation** building footprints (buildings theme, ODbL-1.0).
- **Cook County GIS** 2022 building footprints ([terms](https://www.cookcountyil.gov/terms-use)),
  Illinois Height Modernization / county lidar via the ISGS clearinghouse.
- **USDA NAIP** aerial imagery for ground colour (U.S. public domain) and
  **ESA WorldCover 2021** land cover (CC BY 4.0).
- Water Tower photo skin in `world/meta/resources.zip` and the snapshot metadata:
  "Chicago Water Tower view from west" by **Dough4872**, Wikimedia Commons,
  **CC BY-SA 4.0**; resized, reprojected and masked into 16 px block textures.
  The adapted textures are shared under CC BY-SA 4.0. No Google imagery or
  tiles are used.

The Oakbrook preview/native release keeps its own scoped
[rights decision](releases/{release}/preview/rights-decision.json) and
[attribution](releases/{release}/preview/ATTRIBUTION.md).
'''


# --------------------------------------------------------------------------
# verify


def manifest_entries(manifest, prefix='', rewrite=None):
    entries = []

    def add(path, size, sha256=None, blob_id=None):
        if rewrite and path.startswith(rewrite[0]):
            path = rewrite[1] + path[len(rewrite[0]):]
        entries.append({'path': prefix + path, 'bytes': size, 'sha256': sha256, 'blob_id': blob_id})

    schema = manifest.get('schema')
    if schema == LEDGER_SCHEMA:
        for path, entry in manifest['files'].items():
            add(path, entry['bytes'], entry.get('sha256'), entry.get('blob_id'))
    elif schema == 'earthcraft-world-snapshot-v1':
        for shard in manifest['shards']:
            add(shard['path'], shard['bytes'], shard['sha256'])
    elif schema == 'earthcraft.generated-tile-index.v1':
        for shard in manifest['shards'].values():
            add(shard['path'], shard['bytes'], shard['sha256'])
    elif isinstance(manifest.get('files'), list):
        for item in manifest['files']:
            add(item['path'], item['bytes'], item.get('sha256'), item.get('blob_id'))
    else:
        raise ValueError('Unrecognized manifest: expected files, shards, or a hf_store ledger')
    return entries


def verify(ctx, manifest_ref, *, prefix='', rewrite=None, revision='main', download_limit=64 * MiB):
    if manifest_ref.startswith('hf:'):
        manifest = json.loads(ctx.fetch_json(ctx.repo, manifest_ref[3:], revision))
    else:
        manifest = json.loads(Path(manifest_ref).read_text())
    entries = manifest_entries(manifest, prefix, rewrite)
    paths = [entry['path'] for entry in entries]
    remote = {}
    for start in range(0, len(paths), 50):
        chunk = paths[start:start + 50]
        remote.update(ctx.retry.call(lambda c=chunk: ctx.hub.paths_info(ctx.repo, c, revision=revision,
                                                                        expand=ctx.expand), what='get_paths_info'))
    result = {'checked': len(entries), 'ok': 0, 'bytes': 0, 'missing': [], 'size_mismatch': [],
              'hash_mismatch': [], 'downloaded_to_hash': 0}
    for entry in entries:
        item = remote.get(entry['path'])
        if item is None:
            result['missing'].append(entry['path'])
            continue
        if item.size != entry['bytes']:
            result['size_mismatch'].append(entry['path'])
            continue
        if item.sha256 and entry['sha256']:
            good = item.sha256 == entry['sha256']
        elif entry.get('blob_id') and item.blob_id:
            good = item.blob_id == entry['blob_id']
        elif entry['sha256'] and item.size <= download_limit:
            raw = ctx.fetch_json(ctx.repo, entry['path'], revision, limit=download_limit)
            good = hashlib.sha256(raw).hexdigest() == entry['sha256']
            result['downloaded_to_hash'] += 1
        else:
            good = False
        if good:
            result['ok'] += 1
            result['bytes'] += entry['bytes']
        else:
            result['hash_mismatch'].append(entry['path'])
    result['passed'] = result['ok'] == result['checked']
    return result


# --------------------------------------------------------------------------
# CLI


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', default=DEFAULT_REPO, help='destination dataset (default %(default)s)')
    parser.add_argument('--scratch', type=Path, required=True, help='guarded scratch root (created if absent)')
    parser.add_argument('--cap', type=parse_size, default=parse_size('4GiB'),
                        help='hard byte cap for everything under --scratch, incl. HF caches (default 4GiB)')
    parser.add_argument('--min-free', type=parse_size, default=None,
                        help='absolute free-space floor; default max(25 GB, 10%% of the volume)')
    parser.add_argument('--min-free-fraction', type=float, default=0.10)
    parser.add_argument('--min-free-bytes', type=parse_size, default=25 * 10**9,
                        help='lower bound of the default floor (default 25G)')
    parser.add_argument('--batch-bytes', type=parse_size, default=GiB, help='max bytes per commit (1GiB)')
    parser.add_argument('--batch-files', type=int, default=400, help='max files per commit (400)')
    parser.add_argument('--attempts', type=int, default=8, help='attempts per Hub request (8)')
    parser.add_argument('--no-expand', action='store_true', help='verify without expand=True')
    parser.add_argument('--log', default=None, help='JSON-lines log file name inside --scratch')
    sub = parser.add_subparsers(dest='command', required=True)
    files = sub.add_parser('push-files', help='archive existing files as-is')
    files.add_argument('--src', type=Path, required=True)
    files.add_argument('--dest', required=True)
    files.add_argument('--include', action='append', default=[])
    files.add_argument('--exclude', action='append', default=[])
    world = sub.add_parser('push-world', help='upload a closed Minecraft save to world/')
    world.add_argument('--save', type=Path, required=True)
    world.add_argument('--only-changed', action='store_true')
    world.add_argument('--dry-run', action='store_true', help='plan only; no lock, no uploads')
    world.add_argument('--frame', type=Path, default=None, help='frame.json (default: city-coverage.json frame)')
    world.add_argument('--source-revision', default=None)
    world.add_argument('--no-catalog', action='store_true')
    move = sub.add_parser('migrate', help='copy the private world dataset into the public store')
    move.add_argument('--only', default='tiles,validations,snapshot,oakbrook,card')
    move.add_argument('--limit', type=int, default=None, help='first N tiles/v1 files (subset run)')
    check = sub.add_parser('verify', help='re-check remote sizes/hashes for a manifest')
    check.add_argument('--manifest', required=True, help='local JSON or hf:<path in repo>')
    check.add_argument('--prefix', default='')
    check.add_argument('--rewrite', default=None, help='OLD=NEW path prefix rewrite')
    check.add_argument('--revision', default='main')
    return parser


def main(argv=None, hub_factory=Hub):
    args = build_parser().parse_args(argv)
    guard = DiskGuard(args.scratch, args.cap, floor_bytes=args.min_free, floor_fraction=args.min_free_fraction,
                      floor_min_bytes=args.min_free_bytes)
    dry = args.command == 'push-world' and args.dry_run and not args.only_changed
    if not dry:
        guard.check(0, 'start')
        guard.ensure_dir()
        bound_hf_environment(guard)
    hub = None if dry else hub_factory()
    ctx = Context(args.repo, guard, hub, None, args.batch_bytes, args.batch_files, not args.no_expand, args.log)
    ctx.retry = Retry(attempts=args.attempts, log=ctx.log)
    started = time.monotonic()
    with (guard.watch() if not dry else _null()):
        if args.command == 'push-files':
            result = push_files(ctx, args.src, args.dest, args.include, args.exclude)
        elif args.command == 'push-world':
            result = push_world(ctx, args.save, only_changed=args.only_changed, dry_run=args.dry_run,
                                frame_path=args.frame, source_revision=args.source_revision,
                                update_catalog=not args.no_catalog)
        elif args.command == 'migrate':
            groups = {g.strip() for g in args.only.split(',') if g.strip()}
            unknown = groups - {'tiles', 'validations', 'snapshot', 'oakbrook', 'card'}
            if unknown:
                raise SystemExit(f'Unknown migrate groups: {sorted(unknown)}')
            result = migrate(ctx, groups, args.limit)
        else:
            rewrite = tuple(args.rewrite.split('=', 1)) if args.rewrite else None
            result = verify(ctx, args.manifest, prefix=args.prefix, rewrite=rewrite, revision=args.revision)
    if not dry:
        guard.clear('tmp') if guard.path('tmp').exists() else None
        result['disk'] = guard.report()
    result['seconds'] = round(time.monotonic() - started, 1)
    ctx.log(event='result', command=args.command, **result)
    if args.command == 'verify' and not result.get('passed'):
        return 1
    return 0


@contextmanager
def _null():
    yield


if __name__ == '__main__':
    raise SystemExit(main())
