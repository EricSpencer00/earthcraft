"""One capped LRU cache for every regional source, inside one disk footprint.

The regional generator used to keep terrain tiles, EPT nodes, original county
LAS and ArcGrid members, their spatial indexes and the OSM PBFs on an external
drive with no upper bound. They now share one cache root (runs/scratch/cache/,
default 12 GiB) and one footprint: cache + per-tile scratch + control <= 25 GiB
with at least 40 GiB left free on the volume.

Writers call ``admit(path, nbytes)`` before writing into the cache. The least
recently used units are evicted first; then the footprint cap and free-space
floor are checked. Readers call ``touch(path)`` on every cache hit so a unit in
use is never the next to go. With no cache activated (other pipelines, tests)
the hooks fall back to each caller's original free-space reserve.

A *unit* is the eviction granule: the entries of one directory that share a
stem, the name up to its first '.'. A terrain tile ``15-8392-12177.png`` and
its ``.json`` manifest form one unit, as do an EPT node ``7-1-2-3.laz`` with its
``.receipt.json`` and ``.lock``, or an index directory ``7-1-2-3/`` (it holds a
``manifest.json``) with ``7-1-2-3.lock``. A directory without ``manifest.json``
is a container and is descended into. Hidden entries (``.lru.sqlite``,
``.locks/``, in-progress ``.x.building-*`` directories) are operational and
never evicted. Units used since the oldest active tile began are pinned.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

from hf_store import DiskGuardError, GiB, human

DEFAULT_CACHE_CAP = 12 * GiB
DEFAULT_FOOTPRINT_CAP = 25 * GiB
DEFAULT_MIN_FREE = 40 * GiB
STALE_STAGING_SECONDS = 24 * 3600


class CacheCapacityError(DiskGuardError):
    """Units pinned by running tiles alone exceed the cache cap; eviction cannot help."""


def entry_bytes(path):
    """Allocated bytes of a file or tree (sparse files count what they occupy, at least their length)."""
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0
    if not path.is_dir() or path.is_symlink():
        return max(info.st_size, getattr(info, 'st_blocks', 0) * 512)
    total = 0
    for directory, _, files in os.walk(path):
        for name in files:
            try:
                item = (Path(directory) / name).lstat()
            except FileNotFoundError:
                continue
            total += max(item.st_size, getattr(item, 'st_blocks', 0) * 512)
    return total


def existing_ancestor(path):
    path = Path(path).absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


class Footprint:
    """Hard byte cap over several roots plus an absolute free-space floor.

    ``check`` runs before writes; ``watch`` samples between them so a library
    write (a world build, a LAZ decode) that crosses either limit is flagged
    and refused at the next check instead of filling the disk.
    """

    def __init__(self, roots, cap=DEFAULT_FOOTPRINT_CAP, floor=DEFAULT_MIN_FREE, statvfs=os.statvfs, volume=None):
        """roots: {name: path or zero-argument callable returning bytes (e.g. a cache's tracked size)}."""
        self.roots = {name: (path if callable(path) else Path(path).absolute()) for name, path in dict(roots).items()}
        if cap <= 0 or floor < 0:
            raise ValueError('A positive footprint cap and a non-negative floor are required')
        paths = [path for path in self.roots.values() if not callable(path)]
        if volume is None and not paths:
            raise ValueError('A path on the guarded volume is required')
        self.volume = Path(volume or paths[0]).absolute()
        self.cap, self.floor, self.statvfs = int(cap), int(floor), statvfs
        self.peak_used, self.min_free, self.peak_detail = 0, None, {}
        self.breach, self.breach_kind = None, None
        self._lock = threading.Lock()

    def used(self, detail=False):
        parts = {name: (int(path()) if callable(path) else entry_bytes(path)) for name, path in self.roots.items()}
        total = sum(parts.values())
        return (total, parts) if detail else total

    def free(self):
        stat = self.statvfs(str(existing_ancestor(self.volume)))
        return stat.f_bavail * stat.f_frsize

    def _record(self, used, free, detail=None):
        with self._lock:
            self.peak_used = max(self.peak_used, used)
            self.min_free = free if self.min_free is None else min(self.min_free, free)
            for key, value in (detail or {}).items():
                self.peak_detail[key] = max(self.peak_detail.get(key, 0), value)

    def check(self, incoming=0, what='write'):
        if self.breach:
            raise DiskGuardError(self.breach)
        used, detail = self.used(detail=True)
        free = self.free()
        self._record(used, free, detail)
        if used + incoming > self.cap:
            raise DiskGuardError(f'Footprint cap refuses {what}: {human(used)} used + {human(incoming)} '
                                 f'> cap {human(self.cap)}')
        if free - incoming < self.floor:
            raise DiskGuardError(f'Free-space floor refuses {what}: {human(free)} free - {human(incoming)} '
                                 f'< floor {human(self.floor)}')
        return {'used': used, 'free': free, 'detail': detail}

    def clear_breach(self):
        """Forget a flagged breach once usage is back under both limits (pause, then resume)."""
        used, free = self.used(), self.free()
        if used <= self.cap and free >= self.floor:
            self.breach, self.breach_kind = None, None
        return self.breach is None

    def _flag(self, kind, message):
        if not self.breach:
            self.breach, self.breach_kind = message, kind

    @contextmanager
    def watch(self, interval=5.0, extra=None):
        """Sample usage in the background; ``extra`` adds named roots with their own caps."""
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    used, detail = self.used(detail=True)
                    free = self.free()
                    for name, (path, cap) in (extra or {}).items():
                        size = entry_bytes(path)
                        detail[name] = size
                        if size > cap:
                            self._flag(name, f'{name} exceeded its cap: {human(size)} > {human(cap)}')
                    self._record(used, free, detail)
                    if used > self.cap:
                        self._flag('cap', f'Footprint exceeded its cap: {human(used)} > {human(self.cap)}')
                    elif free < self.floor:
                        self._flag('floor', f'Free space fell below the floor: {human(free)} < {human(self.floor)}')
                except OSError:
                    pass
                stop.wait(interval)

        thread = threading.Thread(target=sample, name='footprint-watch', daemon=True)
        thread.start()
        try:
            yield self
        finally:
            stop.set()
            thread.join(timeout=10)

    def report(self):
        used, detail = self.used(detail=True)
        free = self.free()
        self._record(used, free, detail)
        return {'cap_bytes': self.cap, 'floor_bytes': self.floor, 'peak_bytes': self.peak_used,
                'min_free_bytes': self.min_free, 'peak_detail_bytes': dict(sorted(self.peak_detail.items())),
                'final_bytes': used, 'final_detail_bytes': detail, 'free_bytes': free}


class SourceCache:
    """LRU units under one root with a hard cap; see the module docstring."""

    def __init__(self, root, cap=DEFAULT_CACHE_CAP, footprint=None, *, clock=time.time, log=None,
                 used_ttl=15.0):
        if cap <= 0:
            raise ValueError('A positive cache cap is required')
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cap, self.footprint, self.clock = int(cap), footprint, clock
        self.log = log or (lambda **_: None)
        self.owner = f'{os.uname().nodename}:{os.getpid()}:{id(self):x}'
        self.used_ttl, self._used, self._used_at = used_ttl, None, 0.0
        self.peak_used, self.evicted_bytes, self.evicted_units = 0, 0, 0
        self._db = sqlite3.connect(self.root / '.lru.sqlite', timeout=30, isolation_level=None,
                                   check_same_thread=False)
        self._db.execute('PRAGMA busy_timeout=30000')
        self._db.execute('CREATE TABLE IF NOT EXISTS units (unit TEXT PRIMARY KEY, last_used REAL NOT NULL)')
        self._db.execute('CREATE TABLE IF NOT EXISTS active (owner TEXT PRIMARY KEY, pid INTEGER, since REAL)')
        self._db_lock = threading.Lock()
        self._sweep_staging()

    # -- identity
    def owns(self, path):
        try:
            Path(path).absolute().relative_to(self.root)
            return True
        except ValueError:
            return False

    @staticmethod
    def stem(name):
        return name.split('.', 1)[0]

    def unit_of(self, path):
        """Unit id ('dir/stem') of a cache path; containers are directories without manifest.json."""
        relative = Path(os.path.normpath(Path(path).absolute())).relative_to(self.root)
        parts = relative.parts
        if not parts or any(part.startswith('.') for part in parts):
            return None
        directory = self.root
        for index, part in enumerate(parts):
            candidate = directory / part
            if index == len(parts) - 1 or (candidate / 'manifest.json').is_file():
                parent = '/'.join(parts[:index])
                return (parent + '/' if parent else '') + self.stem(part)
            directory = candidate
        return None

    # -- enumeration
    def units(self):
        """{unit: {'paths': [...], 'bytes': n, 'mtime': t}} for everything evictable."""
        found = {}

        def visit(directory, prefix):
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name)
            except FileNotFoundError:
                return
            for entry in entries:
                if entry.name.startswith('.'):
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False) and not (path / 'manifest.json').is_file():
                    visit(path, prefix + entry.name + '/')
                    continue
                unit = prefix + self.stem(entry.name)
                slot = found.setdefault(unit, {'paths': [], 'bytes': 0, 'mtime': 0.0})
                slot['paths'].append(path)
                slot['bytes'] += entry_bytes(path)
                try:
                    slot['mtime'] = max(slot['mtime'], path.lstat().st_mtime)
                except FileNotFoundError:
                    pass

        visit(self.root, '')
        return found

    def used(self, fresh=False):
        now = time.monotonic()
        if fresh or self._used is None or now - self._used_at > self.used_ttl:
            self._used, self._used_at = entry_bytes(self.root), now
        self.peak_used = max(self.peak_used, self._used)
        return self._used

    # -- bookkeeping
    def _stamp(self, unit, when=None):
        if unit is None:
            return
        with self._db_lock:
            self._db.execute('INSERT INTO units(unit,last_used) VALUES(?,?) ON CONFLICT(unit) DO UPDATE SET '
                             'last_used=max(last_used,excluded.last_used)', (unit, self.clock() if when is None else when))

    def touch(self, path):
        if self.owns(path):
            self._stamp(self.unit_of(path))

    def begin_tile(self):
        """Pin everything this worker uses from now until end_tile()."""
        with self._db_lock:
            self._db.execute('INSERT OR REPLACE INTO active(owner,pid,since) VALUES(?,?,?)',
                             (self.owner, os.getpid(), self.clock()))

    def end_tile(self):
        with self._db_lock:
            self._db.execute('DELETE FROM active WHERE owner=?', (self.owner,))

    def protected_since(self):
        """Oldest start among live tiles; units used after it are pinned."""
        oldest = None
        with self._db_lock:
            rows = self._db.execute('SELECT owner,pid,since FROM active').fetchall()
        for owner, pid, since in rows:
            alive = True
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            except PermissionError:
                pass
            if not alive:
                with self._db_lock:
                    self._db.execute('DELETE FROM active WHERE owner=?', (owner,))
                continue
            oldest = since if oldest is None else min(oldest, since)
        return oldest

    @contextmanager
    def _exclusive(self):
        with (self.root / '.lru.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    # -- admission and eviction
    def admit(self, path, incoming, what=None):
        """Make room for ``incoming`` new bytes at ``path``; raise before anything is written."""
        incoming = max(0, int(incoming))
        what = what or Path(path).name
        unit = self.unit_of(path)
        with self._exclusive():
            used = self.used(fresh=True)
            if used + incoming > self.cap:
                used = self._evict(used + incoming - self.cap, keep=unit)
            if used + incoming > self.cap:
                raise CacheCapacityError(
                    f'Cache cap refuses {what}: {human(used)} pinned/in use + {human(incoming)} > cap '
                    f'{human(self.cap)}; the running tile needs more source cache than the cap allows')
            if self.footprint is not None:
                self.footprint.check(incoming, f'cache {what}')
            self._stamp(unit)
            self._used = used + incoming  # conservative until the next walk
            self.peak_used = max(self.peak_used, self._used)

    def _evict(self, needed, keep=None):
        since = self.protected_since()
        units = self.units()
        with self._db_lock:
            stamps = dict(self._db.execute('SELECT unit,last_used FROM units').fetchall())
        candidates = []
        for unit, slot in units.items():
            last = max(stamps.get(unit, 0.0), slot['mtime'])
            if unit == keep or (since is not None and last >= since):
                continue
            candidates.append((last, unit, slot))
        candidates.sort(key=lambda item: (item[0], item[1]))
        freed = 0
        for last, unit, slot in candidates:
            if freed >= needed:
                break
            for path in slot['paths']:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            with self._db_lock:
                self._db.execute('DELETE FROM units WHERE unit=?', (unit,))
            freed += slot['bytes']
            self.evicted_bytes += slot['bytes']
            self.evicted_units += 1
            self.log(event='cache_evict', unit=unit, bytes=slot['bytes'], last_used=round(last, 1))
        self._prune_empty()
        return self.used(fresh=True)

    def _prune_empty(self):
        for directory, _, _ in sorted(os.walk(self.root), key=lambda item: -len(item[0])):
            path = Path(directory)
            if path != self.root and not path.name.startswith('.'):
                try:
                    path.rmdir()
                except OSError:
                    pass

    def _sweep_staging(self):
        """Remove abandoned hidden build directories (a killed index build) older than a day."""
        cutoff = time.time() - STALE_STAGING_SECONDS
        for directory, names, _ in os.walk(self.root):
            for name in list(names):
                path = Path(directory) / name
                if name.startswith('.') and '.building-' in name:
                    try:
                        if path.lstat().st_mtime < cutoff:
                            shutil.rmtree(path, ignore_errors=True)
                    except FileNotFoundError:
                        pass
                    names.remove(name)

    def clear(self):
        """Delete every unit (operator request); the root and its bookkeeping stay."""
        with self._exclusive():
            for slot in self.units().values():
                for path in slot['paths']:
                    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
            with self._db_lock:
                self._db.execute('DELETE FROM units')
            self._prune_empty()
            return self.used(fresh=True)

    def status(self):
        units = self.units()
        return {'root': str(self.root), 'cap_bytes': self.cap, 'used_bytes': self.used(fresh=True),
                'units': len(units), 'peak_bytes': self.peak_used, 'evicted_bytes': self.evicted_bytes,
                'evicted_units': self.evicted_units,
                'by_namespace': {name: sum(s['bytes'] for u, s in units.items() if u.split('/')[0] == name)
                                 for name in sorted({u.split('/')[0] for u in units})}}

    def close(self):
        self.end_tile()
        self._db.close()


# --------------------------------------------------------------------------
# Hooks used by the source modules (no-ops for everything outside the cache)

_ACTIVE = None


def activate(cache):
    global _ACTIVE
    _ACTIVE = cache
    return cache


def active():
    return _ACTIVE


def owns(path):
    return _ACTIVE is not None and _ACTIVE.owns(path)


def admit(path, incoming, *, what=None, legacy_reserve=None, legacy_message=None):
    """Cache paths: evict, then cap and floor checks. Elsewhere: the caller's original
    free-space reserve, unchanged (``free < legacy_reserve`` refuses)."""
    if owns(path):
        _ACTIVE.admit(path, incoming, what)
        return
    if legacy_reserve is not None:
        if shutil.disk_usage(existing_ancestor(path)).free < legacy_reserve:
            raise ValueError(legacy_message or 'Preserve the configured free-space reserve')


def touch(path):
    if owns(path):
        _ACTIVE.touch(path)


def require_free(path, legacy_reserve, message):
    """Free-space check before a bulk write outside the cache (sources, worlds, imagery).

    With a guarded run active, its footprint cap and free-space floor decide
    (DiskGuardError: the generator pauses the tile). Otherwise the caller's
    original fixed reserve applies unchanged.
    """
    if _ACTIVE is not None and _ACTIVE.footprint is not None:
        _ACTIVE.footprint.check(0, message)
        return
    if shutil.disk_usage(existing_ancestor(path)).free < legacy_reserve:
        raise ValueError(message)


def describe(path):
    """Portable name for a cache path in receipts: 'cache:<relative>'."""
    if owns(path):
        return 'cache:' + Path(os.path.normpath(Path(path).absolute())).relative_to(_ACTIVE.root).as_posix()
    return None


def status_json():
    return json.dumps(_ACTIVE.status() if _ACTIVE else {'active': False})
