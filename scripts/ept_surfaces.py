"""Paired DTM/DSM for one tile from one classified USGS 3DEP EPT survey.

The county scan surfaces (ISGS ArcGrid strips of 0.8-1.2 GB, or ImageServer
exports) are rasters derived from the same airborne LiDAR collections USGS
publishes as EPT in the pipeline's own AWS region. One full-density crop of the
tile (~30 MB of LAZ) gives both surfaces at 1 m: the DTM is the mean of ground
returns (class 2), filled under buildings from the nearest ground within 50 m;
the DSM is the highest non-vegetation return (classes 1, 2, 6, 9, 17), so tree
canopy never lifts a roof. Writes exactly what regional_scans.acquire writes
(dtm.tif, dsm.tif, metric-surfaces.npz, probe.json), so the scan build is
unchanged, and keeps the crop for the building points and trees of the same
survey: one download, one epoch per tile.
"""
import json
from pathlib import Path

import numpy as np
from scipy import ndimage

import detail_points
from detail_guard import WriteGuard
from region_expansion import atomic, sha

SURFACE_CLASSES = (1, 2, 6, 9, 17)  # ground, building, water, bridge deck, unclassified; never vegetation 3/4/5
GROUND_FILL_M = 50
CROP_CAP = 700 * 2**20
CROP_FLOOR = 4 * 2**30


def pair_for(tile, frame, catalog):
    """The newest classified EPT survey over the tile, as a scan pair (None without one)."""
    from regional_point_catalog import candidates
    surveys = candidates(tile, frame, catalog) if catalog else []
    if not surveys:
        return None
    survey = surveys[0]
    return {'kind': 'ept', 'project': survey['project'], 'county': f"USGS 3DEP {survey['project']}",
            'year': survey.get('year_from_project_name'),
            'dtm': {'vertical_reference': 'EPT native vertical (NAVD88 metres for USGS 3DEP)'}}


def rasters(xyz, cls, grid):
    """(dtm, dsm, ground_count) on the tile's 1 m grid, NaN where unobserved."""
    size = grid['size']
    col = np.floor(xyz[:, 0] - grid['west']).astype(np.int64)
    row = np.floor(grid['north'] - xyz[:, 1]).astype(np.int64)
    inside = (col >= 0) & (col < size) & (row >= 0) & (row < size)
    flat = row * size + col
    ground = inside & (cls == 2)
    count = np.bincount(flat[ground], minlength=size * size)
    total = np.bincount(flat[ground], weights=xyz[ground, 2], minlength=size * size)
    dtm = np.where(count > 0, total / np.maximum(count, 1), np.nan).reshape(size, size)
    surface = inside & np.isin(cls, SURFACE_CLASSES)
    dsm = np.full(size * size, -np.inf)
    np.maximum.at(dsm, flat[surface], xyz[surface, 2])
    dsm = dsm.reshape(size, size)
    dsm[np.isinf(dsm)] = np.nan
    return dtm, dsm, count.reshape(size, size)


def fill(dtm, dsm):
    """Ground under buildings from the nearest ground cell (within GROUND_FILL_M); single-cell
    DSM gaps from their highest neighbour; the DSM never below the DTM."""
    known = np.isfinite(dtm)
    if not known.any():
        return dtm, dsm
    distance, (rr, cc) = ndimage.distance_transform_edt(~known, return_indices=True)
    dtm = np.where(distance <= GROUND_FILL_M, dtm[rr, cc], np.nan)
    gaps = ~np.isfinite(dsm)
    neighbour = ndimage.maximum_filter(np.where(gaps, -np.inf, dsm), size=3)
    dsm = np.where(gaps & np.isfinite(neighbour), neighbour, dsm)
    dsm = np.where(np.isfinite(dsm) & np.isfinite(dtm), np.maximum(dsm, dtm), dsm)
    return dtm, dsm


def acquire(pair, grid, destination, fallback, cache, frame):
    """Same contract as regional_scans.acquire: (DTM with LQ fallback, probe record)."""
    import rasterio
    from rasterio.transform import from_origin
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    guard = WriteGuard(destination, CROP_CAP, CROP_FLOOR)
    points = destination / 'ept-points'
    crop = detail_points.crop(pair['project'], frame, grid['west'], grid['north'], grid['size'], 0, guard, points,
                              cache=cache)
    xyz, cls, _, _, _ = detail_points.load(points)
    dtm, dsm, ground = rasters(xyz, cls, grid)
    del xyz
    observed = np.isfinite(dtm) & np.isfinite(fallback)
    residual = (dtm - fallback)[observed]
    if len(residual) < 1000 or np.median(np.abs(residual)) > 2:
        raise ValueError(f"EPT ground does not agree with the LQ terrain ({len(residual)} cells)")
    dtm, dsm = fill(dtm, dsm)
    valid = np.isfinite(dtm) & np.isfinite(dsm)
    valid &= (dsm >= dtm - 1) & (dsm - dtm <= 700) & (dtm > -100) & (dtm < 800)  # as regional_scans.acquire
    elevation = np.where(valid, dtm, fallback).astype(np.float32)
    roof = np.where(valid, dsm, elevation).astype(np.float32)
    transform = from_origin(grid['west'], grid['north'], 1, 1)
    for name, array in (('dtm.tif', dtm), ('dsm.tif', dsm)):
        with rasterio.open(destination / name, 'w', driver='GTiff', width=grid['size'], height=grid['size'], count=1,
                           dtype='float32', crs=grid['crs'], transform=transform, nodata=np.nan, compress='deflate') as raster:
            raster.write(array.astype(np.float32), 1)
    np.savez_compressed(destination / 'metric-surfaces.npz', dtm=elevation, dsm=roof, valid=valid)
    record = {'schema': 'earthcraft-regional-scan-surface-crop-v1', 'county': pair['county'],
              'capture_year': pair['year'], 'grid': {key: grid[key] for key in ('crs', 'west', 'north', 'size')},
              'source': pair, 'valid_cells': int(valid.sum()), 'missing_cells': int((~valid).sum()),
              'surfaces_sha256': sha(destination / 'metric-surfaces.npz'),
              'assets': {name: sha(destination / name) for name in ('dtm.tif', 'dsm.tif')},
              'derivation': 'USGS 3DEP EPT full-density crop: DTM mean of class 2 per 1 m cell, nearest ground '
                            f'within {GROUND_FILL_M} m under structures; DSM highest return of classes '
                            f'{list(SURFACE_CLASSES)} (vegetation excluded), single-cell gaps from the highest neighbour',
              'ground_cells_observed': int((ground > 0).sum()),
              'lq_ground_residual_m': {'median_abs': round(float(np.median(np.abs(residual))), 3),
                                       'cells': int(len(residual))},
              'points': {key: crop[key] for key in ('project', 'cropped_points', 'points_sha256', 'downloaded_bytes',
                                                     'classes_in_crop')},
              'resampling': 'per-cell aggregation of raw returns; one-metre output sampling is not source accuracy',
              'roof_mask_source': 'Mapped building footprints; vegetation outside footprints is excluded',
              'walls': 'Derived exposed side shell; facade measurements unavailable',
              'raw_points_acquired': True, 'physical_accuracy_verified': False, 'llm_used': False}
    atomic(destination / 'probe.json', json.dumps(record, indent=2).encode())
    return elevation, record
