"""Freeze a 64 m Water Tower evaluation window without resampling or new downloads."""
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import rasterio
from rasterio.windows import Window
from pyproj import Transformer, Geod

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_focus(parent, surfaces, destination):
    meta = json.loads((parent/'sources.json').read_text())
    probe = json.loads((surfaces/'probe.json').read_text())
    if (meta['size'], meta['west'], meta['north']) != (512, -256, 257):
        raise ValueError('Expected the frozen Chicago chart; refusing a different location')
    if probe['grid'] != {k: meta[k] for k in ('crs', 'west', 'north', 'size')}:
        raise ValueError('Surface grid mismatch')
    inputs = {str(p.resolve()): sha(p) for p in [parent/'sources.json',
        parent/'rasters.npz', parent/'usgs-elevation.tif', parent/'osm-ways.json',
        parent/'cook-buildings-2022.json', surfaces/'probe.json', surfaces/'metric-surfaces.npz']}
    if destination.exists():
        saved = json.loads((destination/'focus-manifest.json').read_text())
        if saved['inputs'] != inputs:
            raise ValueError('Frozen focus inputs changed; use a new experiment directory')
        for filename, digest in saved['outputs'].items():
            if sha(destination/filename) != digest:
                raise ValueError('Frozen focus output changed: '+filename)
        return destination, destination/'surfaces'
    destination.mkdir(parents=True)
    roof = destination/'surfaces'
    roof.mkdir()
    window = Window(224, 224, 64, 64)
    selection = np.s_[224:288, 224:288]
    with np.load(parent/'rasters.npz') as data:
        cropped = {key: data[key][selection] for key in data.files}
    with np.load(surfaces/'metric-surfaces.npz') as data:
        roof_data = {key: data[key][selection] for key in data.files}
    if any(a.shape != (64, 64) for a in [*cropped.values(), *roof_data.values()]):
        raise ValueError('Unexpected source array dimensions')
    np.savez_compressed(destination/'rasters.npz', **cropped)
    np.savez_compressed(roof/'metric-surfaces.npz', **roof_data)
    with rasterio.open(parent/'usgs-elevation.tif') as original:
        profile = original.profile.copy()
        profile.update(width=64, height=64, transform=original.window_transform(window))
        with rasterio.open(destination/'usgs-elevation.tif', 'w', **profile) as target:
            target.write(original.read(window=window))
    for filename in ('osm-ways.json', 'cook-buildings-2022.json'):
        shutil.copyfile(parent/filename, destination/filename)
    meta.update(size=64, west=-32, north=33)
    meta['elevation_range_m'] = [float(cropped['elevation'].min()), float(cropped['elevation'].max())]
    codes, counts = np.unique(cropped['cover'], return_counts=True)
    meta['cover_classes'] = dict(zip(map(str, codes), map(int, counts)))
    meta['elevation_sha256'] = sha(destination/'usgs-elevation.tif')
    inverse = Transformer.from_crs(meta['crs'], 4326, always_xy=True)
    geod = Geod(ellps='WGS84')
    distances = []
    for x, y in ((-32,33), (31,33), (-32,-30), (31,-30)):
        lon, lat = inverse.transform(x,y)
        for dx, dy in ((1,0),(0,-1)):
            lon2, lat2 = inverse.transform(x+dx,y+dy)
            distances.append(geod.inv(lon,lat,lon2,lat2)[2])
    meta['cell_ground_distances_m'] = distances
    meta['derived_crop'] = {'parent': str(parent.resolve()), 'pixel_window_xywh': [224,224,64,64],
        'resampling': 'none; exact array slice', 'parent_source_report_sha256': inputs[str((parent/'sources.json').resolve())],
        'scope': 'All current iteration is Water Tower only. Vector files retain original source features; rasterization clips them.'}
    (destination/'sources.json').write_text(json.dumps(meta, indent=2))
    probe['grid'] = {key: meta[key] for key in ('crs','west','north','size')}
    probe.pop('flat_cap_comparison', None)
    probe.pop('surface_minus_ground_quantiles_m', None)
    probe['parent_probe'] = str((surfaces/'probe.json').resolve())
    probe['derived_crop'] = meta['derived_crop']
    probe['asset_statistics_scope'] = 'Original acquired 512 m assets; cropped validity is recorded separately.'
    probe['cropped_valid_cells'] = {key: int(np.isfinite(value).sum()) for key,value in roof_data.items()}
    (roof/'probe.json').write_text(json.dumps(probe, indent=2))
    outputs = {str(p.relative_to(destination)): sha(p) for p in destination.rglob('*') if p.is_file()}
    (destination/'focus-manifest.json').write_text(json.dumps({'inputs': inputs, 'outputs': outputs,
        'acceptance': 'NOT geographic accuracy: exact crop only; facade and silhouette acceptance remain open.'}, indent=2))
    return destination, roof


if __name__ == '__main__':
    print(prepare_focus(ROOT/'runs/metric-water-tower-local', ROOT/'runs/cook-surface-2022',
                        ROOT/'runs/water-tower-focus-64'))
