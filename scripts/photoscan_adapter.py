"""Opt-in bridge to the shared photoscan LiDAR-to-buildings library.

Nothing in the default pipeline imports this file. It converts an EarthCraft tile
(meta, offset, county footprints, admitted points) into photoscan's offline
``build_buildings(points=...)`` call and reads the product back with an exact grid
check. Photoscan's ``inferred >= 2`` columns are adaptation, never observation.
See docs/PHOTOSCAN_ADAPTER.md.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.ops import transform

PHOTOSCAN_SRC = Path(os.environ.get('PHOTOSCAN_SRC', Path.home()/'photoscan'/'src'))


def _photoscan():
    if str(PHOTOSCAN_SRC) not in sys.path:
        sys.path.insert(0, str(PHOTOSCAN_SRC))
    from photoscan import buildings, grid
    return buildings, grid


def target_grid(meta, offset):
    """EarthCraft tile meta -> photoscan TargetGrid (1 m cells, floor Y, shared offset)."""
    _, grid = _photoscan()
    return grid.TargetGrid(meta['crs'], float(meta['west']), float(meta['north']), int(meta['size']),
                           int(meta['size']), 1.0, float(offset), 'floor')


def county_labels(county, meta):
    """Cook County footprint features -> (label int32[z, x], records); 1-based, 0 = none."""
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    shapes, records = [], []
    for feature in county['features']:
        geometry = None
        for ring in feature['geometry']['rings']:
            polygon = make_valid(Polygon(ring))
            geometry = polygon if geometry is None else geometry.symmetric_difference(polygon)
        geometry = transform(project.transform, geometry)
        if geometry.is_empty:
            continue
        records.append({'id': feature['attributes']['OBJECTID'], 'name': '', 'props': feature['attributes']})
        shapes.append((geometry, len(records)))
    size = meta['size']
    shapes.sort(key=lambda item: -item[0].area)
    label = (rasterize(shapes, out_shape=(size, size), transform=from_origin(meta['west'], meta['north'], 1, 1),
                       fill=0, dtype='int32') if shapes else np.zeros((size, size), np.int32))
    return label, records


def build(meta, offset, label, records, enz, cls, point_manifest, destination):
    """Offline photoscan product for one tile; returns (rasters, buildings, manifest)."""
    buildings, _ = _photoscan()
    grid = target_grid(meta, offset)
    fp = buildings.Footprints.from_labels(label, records, source='earthcraft cook-buildings-2022')
    buildings.build_buildings(grid, fp, destination, points=(enz, cls, point_manifest), log=lambda *_: None)
    return buildings.read(destination, expect_grid=grid)


def _column_top(cells, size):
    top = np.full((size, size), -1, np.int32)
    np.maximum.at(top, (cells[:, 2], cells[:, 0]), cells[:, 1])
    return top


def compare_water_tower(source, points, scratch):
    """EarthCraft observed-voxel column tops vs photoscan roofY on the 64 m Water Tower tile."""
    from geographic_quality import vertical_layout
    from point_geometry import load_tower_points
    meta = json.loads((source/'sources.json').read_text())
    elevation = np.load(source/'rasters.npz')['elevation']
    offset, ground, _ = vertical_layout(elevation)
    cells, _ = load_tower_points(points, source, meta, ground, offset)
    current = _column_top(cells, meta['size'])
    label, records = county_labels(json.loads((source/'cook-buildings-2022.json').read_text()), meta)
    tower = 1 + next(i for i, r in enumerate(records) if r['id'] == 833197)
    data = np.load(points/'points.npz')
    keep = data['withheld'] == 0
    manifest = json.loads((points/'manifest.json').read_text())
    result = {'tile': {k: meta[k] for k in ('west', 'north', 'size')}, 'offset': offset}
    if scratch.exists():
        raise FileExistsError(scratch)
    try:
        variants = {'all_classes': keep,
                    # Same classes EarthCraft admits for the tower, plus ground for photoscan's groundY.
                    'earthcraft_classes': keep & np.isin(data['classification'], [1, 2, 6, 15, 19])}
        for name, mask in variants.items():
            rasters, recs, _ = build(meta, offset, label, records, data['xyz'][mask],
                                     data['classification'][mask], manifest, scratch/name)
            roof = rasters['roofY'].astype(np.int32)
            foot = label == tower
            both = foot & (roof >= 0) & (current >= 0)
            diff = roof[both]-current[both]
            measured = foot & (rasters['inferred'] == 0) & (roof >= 0)
            rec = recs[tower-1]
            result[name] = {
                'footprint_columns': int(foot.sum()),
                'current_observed_columns': int((foot & (current >= 0)).sum()),
                'photoscan_roof_columns': int((foot & (roof >= 0)).sum()),
                'photoscan_measured_columns': int(measured.sum()),
                'photoscan_inferred_columns': {str(c): int((foot & (rasters['inferred'] == c) & (roof >= 0)).sum())
                                               for c in (1, 2, 3)},
                'roof_minus_current_blocks': {'n': int(len(diff)), 'mean': float(diff.mean()),
                    'mae': float(np.abs(diff).mean()), 'p90_abs': float(np.percentile(np.abs(diff), 90)),
                    'max_abs': int(np.abs(diff).max())} if len(diff) else None,
                'top_y': {'current': int(current[foot].max()), 'photoscan': int(roof[foot].max())},
                'base_columns_tile': int((rasters['baseY'] >= 0).sum()),
                'canopy_columns_tile': int((rasters['canopyY'] >= 0).sum()),
                'ground_minus_earthcraft_blocks_median': float(np.median(
                    rasters['groundY'][rasters['groundY'] >= 0].astype(int)-ground[rasters['groundY'] >= 0])),
                'tower_record': {k: rec.get(k) for k in ('status', 'roof_shape', 'confidence', 'observed_fraction',
                                                         'spikes_removed', 'tiers')},
            }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('runs/water-tower-focus-64'))
    parser.add_argument('--points', type=Path, default=Path('runs/water-tower-points-2022'))
    parser.add_argument('--scratch', type=Path, default=Path('runs/scratch/photoscan-compare'))
    args = parser.parse_args()
    print(json.dumps(compare_water_tower(args.source, args.points, args.scratch), indent=1, default=str))


if __name__ == '__main__':
    main()
