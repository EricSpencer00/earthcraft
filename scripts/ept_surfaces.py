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


MIN_POINTS = 100_000  # returns a survey must have inside the tile: its bounding box can cover tiles it never flew


def pair_of(survey):
    return {'kind': 'ept', 'project': survey['project'], 'county': f"USGS 3DEP {survey['project']}",
            'year': survey.get('year_from_project_name'),
            'dtm': {'vertical_reference': 'EPT native vertical (NAVD88 metres for USGS 3DEP)'}}


def pair_for(tile, frame, catalog):
    """The newest classified EPT survey over the tile, as a scan pair (None without one)."""
    from regional_point_catalog import candidates
    surveys = candidates(tile, frame, catalog) if catalog else []
    return pair_of(surveys[0]) if surveys else None


def select(tile, frame, catalog, destination, cache, min_points=MIN_POINTS):
    """(pair, crop manifest) of the newest survey with returns in the tile, cropped once into
    destination/'ept-points' for the surfaces, building points and trees; (None, None) without one.
    A survey whose bounding box covers the tile but holds no returns there is skipped."""
    import shutil
    from regional_point_catalog import candidates
    destination = Path(destination)
    for survey in (candidates(tile, frame, catalog) if catalog else []):
        trial = destination / ('ept-points.' + survey['project'])
        if trial.exists():
            shutil.rmtree(trial)
        crop = detail_points.crop(survey['project'], frame, tile['west'], tile['north'], tile['size'], 0,
                                  WriteGuard(destination, CROP_CAP, CROP_FLOOR), trial, cache=cache)
        if crop['cropped_points'] >= min_points:
            trial.rename(destination / 'ept-points')
            return pair_of(survey), crop
        shutil.rmtree(trial)
    return None, None


def building_cells(points, grid):
    """Distinct 1 m cells holding a classified building return (class 6)."""
    xyz, cls, _, _, _ = detail_points.load(points)
    keep = cls == 6
    col = np.floor(xyz[keep, 0] - grid['west']).astype(np.int64)
    row = np.floor(grid['north'] - xyz[keep, 1]).astype(np.int64)
    inside = (col >= 0) & (col < grid['size']) & (row >= 0) & (row < grid['size'])
    return int(len(np.unique(row[inside] * grid['size'] + col[inside])))


def building_points(points, grid, destination, dtm, valid, surface_context=None,
                    ground_reference='paired LiDAR DTM'):
    """ept_buildings.crop's output (same admission, schema and receipt) from the tile's existing crop,
    so the scan does not download and decode every node a second time."""
    from scan_envelope import admit
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((Path(points) / 'manifest.json').read_text())
    xyz, cls, _, _, _ = detail_points.load(points)
    size = grid['size']
    inside = ((xyz[:, 0] >= grid['west']) & (xyz[:, 0] < grid['west'] + size) &
              (xyz[:, 1] > grid['north'] - size) & (xyz[:, 1] <= grid['north']))
    ground = np.flatnonzero(inside & (cls == 2))
    rows = np.floor(grid['north'] - xyz[ground, 1]).astype(int)
    cols = np.floor(xyz[ground, 0] - grid['west']).astype(int)
    selected = valid[rows, cols]
    residual = xyz[ground, 2][selected] - dtm[rows, cols][selected]
    if len(residual) < 20 or np.median(np.abs(residual)) > 2:
        raise ValueError('EPT Z does not agree with the selected metre ground reference: ' + ground_reference)
    building, classified, associated = admit(xyz, cls, grid, surface_context)
    np.savez_compressed(destination / 'points.npz', xyz=xyz[building])
    record = {'schema': 'earthcraft-classified-ept-building-crop-v1', 'project': manifest['project'],
              'output_horizontal_crs': grid['crs'], 'grid': {k: grid[k] for k in ('crs', 'west', 'north', 'size')},
              'points_sha256': sha(destination / 'points.npz'),
              'assets': [{'key': n['key'], 'sha256': n['sha256'], 'points': n['points']} for n in manifest['node_receipts']],
              'building_points': int(building.sum()), 'all_intersecting_octree_levels_acquired': True, 'downsampling': False,
              'retained_classes': [1, 6] if surface_context is not None else [6],
              'classified_class6_points': int(classified.sum()), 'associated_class1_points': int(associated.sum()),
              'point_admission': 'Provider class 6; class 1 requires mapped building, paired DSM/DTM envelope and >2 m '
                                 'ground clearance' if surface_context is not None else 'Provider class 6 only',
              'vertical_reference': 'Original EPT Z; metre interpretation checked against ' + ground_reference,
              'ground_reference': ground_reference, 'ground_comparison_points': int(len(residual)),
              'median_absolute_ground_residual_m': float(np.median(np.abs(residual))),
              'independent_accuracy_verified': False, 'facade_colour_measured': False,
              'derived_from_crop': manifest['points_sha256']}
    if surface_context is not None:
        from scan_envelope import identity
        record['association_context_sha256'] = identity(surface_context)
    atomic(destination / 'manifest.json', json.dumps(record, indent=2).encode())
    return record


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
    points = destination / 'ept-points'
    if (points / 'manifest.json').exists() and json.loads((points / 'manifest.json').read_text())['project'] == pair['project']:
        crop = json.loads((points / 'manifest.json').read_text())  # selected and cropped already (select)
    else:
        crop = detail_points.crop(pair['project'], frame, grid['west'], grid['north'], grid['size'], 0,
                                  WriteGuard(destination, CROP_CAP, CROP_FLOOR), points, cache=cache)
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
