"""Shared, aligned metric-source supertiles for fast deterministic city builds."""
import fcntl
import errno
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from metric_source_crop import crop
from metric_sources import prepare


SUPERTILE_SIZE = 1024
SOURCE_LOCK_WAIT_SECONDS = 1.0
SOURCE_RETRY_AFTER_SECONDS = 30.0


class SourceCacheBusy(RuntimeError):
    """Another worker owns this source build; dispatch this tile elsewhere."""

    def __init__(self, key, retry_after=SOURCE_RETRY_AFTER_SECONDS):
        self.key = key
        self.retry_after = retry_after
        super().__init__(f'shared source {key} still building after one second')


def supertile_grid(tile, frame, anchor_west, anchor_north, size=SUPERTILE_SIZE):
    """Return the aligned parent grid and exact child window for a plan tile."""
    if type(size) is not int or size < tile['size'] or size % tile['size']:
        raise ValueError('Source supertile must be an integer multiple of tile size')
    dx = tile['west'] - anchor_west
    dz = anchor_north - tile['north']
    if min(dx, dz) < 0 or dx % tile['size'] or dz % tile['size']:
        raise ValueError('Tile is not aligned to the frozen city source lattice')
    parent_west = anchor_west + (dx // size) * size
    parent_north = anchor_north - (dz // size) * size
    col, row = tile['west'] - parent_west, parent_north - tile['north']
    if min(col, row) < 0 or max(col + tile['size'], row + tile['size']) > size:
        raise ValueError('Computed child window escapes source supertile')
    return ({'crs': frame['crs'], 'west': parent_west,
             'north': parent_north, 'size': size}, col, row)


def _validate(path, grid):
    manifest = path / 'sources.json'
    if not manifest.is_file():
        raise ValueError('Cached source supertile has no manifest')
    existing = json.loads(manifest.read_text())
    if any(existing.get(key) != value for key, value in grid.items()):
        raise ValueError('Cached source supertile grid changed')
    for name in ('rasters.npz', existing.get('elevation_raster', 'usgs-elevation.tif'),
                 'osm-ways.json', 'cook-buildings-2022.json'):
        if not (path / name).is_file():
            raise ValueError(f'Cached source supertile is incomplete: {name}')


def source_from_cache(tile, frame, destination, cache_root, anchor_west,
                      anchor_north, way_index=None, size=SUPERTILE_SIZE,
                      prepare_fn=prepare, crop_fn=crop,
                      lock_wait_seconds=SOURCE_LOCK_WAIT_SECONDS):
    """Build one immutable parent once, then crop a child without resampling.

    A filesystem lock makes this safe across the independent Chicago worker
    processes. The remote services are contacted only by the process that
    creates a missing parent; all other workers wait and reuse it.
    """
    destination, cache_root = Path(destination), Path(cache_root)
    parent_grid, col, row = supertile_grid(
        tile, frame, anchor_west, anchor_north, size)
    key = f"w{parent_grid['west']}-n{parent_grid['north']}-s{size}"
    parent = cache_root / key
    locks = cache_root / '.locks'
    locks.mkdir(parents=True, exist_ok=True)
    if not 0 <= lock_wait_seconds <= 60:
        raise ValueError('Source-cache lock wait must be between 0 and 60 seconds')
    with (locks / f'{key}.lock').open('a+') as lock:
        deadline = time.monotonic() + lock_wait_seconds
        while True:
            try:
                fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if not isinstance(error, BlockingIOError) and error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise SourceCacheBusy(key)
                time.sleep(min(.05, max(0, deadline-time.monotonic())))
        try:
            if parent.exists():
                _validate(parent, parent_grid)
            else:
                staging = cache_root / f'.{key}.building-{os.getpid()}-{uuid.uuid4().hex}'
                try:
                    prepare_fn(staging, size, grid=parent_grid, way_index=way_index)
                    _validate(staging, parent_grid)
                    staging.rename(parent)
                except Exception:
                    if staging.exists():
                        # AppleDouble sidecars on removable macOS volumes can
                        # disappear during traversal; this directory is an
                        # unpublished derived staging area and is safe to retry.
                        shutil.rmtree(staging,ignore_errors=True)
                    raise
        finally:
            fcntl.lockf(lock, fcntl.LOCK_UN)
    crop_fn(parent, destination, col, row, tile['size'])
    return {'parent': str(parent.resolve()), 'parent_grid': parent_grid,
            'window_xywh': [col, row, tile['size'], tile['size']]}
