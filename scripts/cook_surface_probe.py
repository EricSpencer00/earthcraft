"""Acquire a bounded native-resolution LiDAR surface/ground crop for source QA.

No world changes. Heights remain measured raster observations, not inferred roofs.
"""
import hashlib
import json
import math
from pathlib import Path
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.features import rasterize
from shapely.geometry import Polygon
from shapely.ops import transform as transform_geometry
from shapely import make_valid

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'runs/metric-water-tower-local'
OUT = ROOT / 'runs/cook-surface-2022'
BASE = 'https://data.isgs.illinois.edu/arcgis/rest/services/Elevation/IL_Cook_{}_2022/ImageServer'


def fetch(url, path, limit):
    if path.exists():
        return path.read_bytes()
    with urllib.request.urlopen(url, timeout=90) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Source response exceeds download bound')
    path.write_bytes(raw)
    return raw


def main():
    if shutil.disk_usage(ROOT).free < 21 * 1024**3:
        raise ValueError('Preserve internal free-space reserve')
    OUT.mkdir(exist_ok=True)
    meta = json.loads((SOURCE / 'sources.json').read_text())
    size = meta['size']
    if size != 512:
        raise ValueError('Frozen diagnostic extent is 512 metres')
    transformer = Transformer.from_crs(meta['crs'], 6455, always_xy=True)
    xs, ys = transformer.transform(
        [meta['west'], meta['west'] + size, meta['west'], meta['west'] + size],
        [meta['north'], meta['north'], meta['north'] - size, meta['north'] - size])
    bounds = [math.floor(min(xs)) - 2, math.floor(min(ys)) - 2,
              math.ceil(max(xs)) + 2, math.ceil(max(ys)) + 2]
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if width * height > 4_000_000:
        raise ValueError('Native crop exceeds four million pixels')
    arrays, assets = {}, []
    for kind in ('DTM', 'DSM'):
        service = BASE.format(kind)
        metadata_path = OUT / f'{kind}-service.json'
        service_meta = json.loads(fetch(service + '?f=pjson', metadata_path, 1024**2))
        if 'error' in service_meta:
            raise ValueError(service_meta['error'])
        wkt = service_meta['extent']['spatialReference']['wkt']
        if 'NAVD88' not in wkt or 'US survey foot' not in wkt:
            raise ValueError('Vertical datum/units require explicit review')
        params = {'bbox': ','.join(map(str, bounds)), 'bboxSR': 6455,
                  'imageSR': 6455, 'size': f'{width},{height}', 'format': 'tiff',
                  'pixelType': 'F32', 'f': 'image',
                  'renderingRule': json.dumps({'rasterFunction': 'None'}),
                  'interpolation': 'RSP_NearestNeighbor'}
        url = service + '/exportImage?' + urllib.parse.urlencode(params)
        path = OUT / f'{kind}-native.tif'
        raw = fetch(url, path, 20 * 1024**2)
        with rasterio.open(path) as dataset:
            if dataset.count != 1 or dataset.width != width or dataset.height != height:
                raise ValueError('Unexpected raster dimensions')
            expected = rasterio.transform.from_origin(bounds[0], bounds[3], 1, 1)
            if not dataset.transform.almost_equals(expected):
                raise ValueError('Native pixel grid changed')
            values = dataset.read(1, masked=True).filled(np.nan)
            metric = np.full((size, size), np.nan, np.float32)
            reproject(values, metric, src_transform=dataset.transform, src_crs=dataset.crs,
                      dst_transform=rasterio.transform.from_origin(meta['west'], meta['north'], 1, 1),
                      dst_crs=meta['crs'], src_nodata=np.nan, dst_nodata=np.nan,
                      resampling=Resampling.nearest)
        metric *= 1200 / 3937
        arrays[kind.lower()] = metric
        assets.append({'kind': kind, 'url': url, 'sha256': hashlib.sha256(raw).hexdigest(),
                       'bytes': len(raw), 'valid_metric_cells': int(np.isfinite(metric).sum()),
                       'range_m': [float(np.nanmin(metric)), float(np.nanmax(metric))]})
    difference = arrays['dsm'] - arrays['dtm']
    county = json.loads((SOURCE / 'cook-buildings-2022.json').read_text())
    projection = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    checks = []
    for feature in county['features']:
        geometry = None
        for ring in feature['geometry']['rings']:
            polygon = make_valid(Polygon(ring))
            geometry = polygon if geometry is None else geometry.symmetric_difference(polygon)
        geometry = transform_geometry(projection.transform, geometry)
        mask = rasterize([(geometry, 1)], out_shape=(size, size),
                         transform=rasterio.transform.from_origin(meta['west'], meta['north'], 1, 1)).astype(bool)
        if not mask.any():
            continue
        valid = mask & np.isfinite(arrays['dsm'])
        attrs = feature['attributes']
        cap = attrs['Max_Point'] * 1200 / 3937
        residual = cap - arrays['dsm'][valid]
        checks.append({'county_objectid': attrs['OBJECTID'], 'footprint_cells': int(mask.sum()),
                       'valid_dsm_cells': int(valid.sum()),
                       'flat_cap_minus_surface_median_m': float(np.median(residual)) if residual.size else None,
                       'flat_cap_exceeds_surface_by_2m_cells': int((residual > 2).sum()),
                       'surface_exceeds_flat_cap_by_2m_cells': int((residual < -2).sum())})
    np.savez_compressed(OUT / 'metric-surfaces.npz', **arrays)
    report = {'capture_year': 2022, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
              'grid': {key: meta[key] for key in ('crs', 'west', 'north', 'size')},
              'source_page': 'https://clearinghouse.isgs.illinois.edu/node/1879',
              'vertical_datum': 'NAVD88', 'vertical_source_units': 'US survey feet',
              'native_pixel_size_m': 1200 / 3937, 'resampling': 'nearest',
              'assets': assets, 'surface_minus_ground_quantiles_m':
              np.nanpercentile(difference, [0, 25, 50, 75, 95, 100]).tolist(),
              'flat_cap_comparison': checks,
              'limitations': ['DSM includes vegetation and is not a building classifier.',
                              'Both rasters share source lineage; comparison is not independent accuracy.',
                              'Source interpolation and missing vertical walls remain unresolved.'],
              'world_modified': False}
    (OUT / 'probe.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'report': str(OUT/'probe.json'), 'assets': assets,
                      'buildings_compared': len(checks), 'world_modified': False}, indent=2))


if __name__ == '__main__':
    main()
