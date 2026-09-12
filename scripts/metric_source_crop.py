"""Exact integer-window crop of frozen metric sources, keeping the same chart."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, box


def _intersects_child(coordinates, transformer, child, closed=False):
    if not coordinates:
        return False
    x, y = transformer.transform(*zip(*coordinates))
    points = list(zip(x, y))
    if closed and len(points) >= 4:
        geometry = Polygon(points)
    elif len(points) >= 2:
        geometry = LineString(points)
    else:
        geometry = box(points[0][0], points[0][1], points[0][0], points[0][1])
    return geometry.intersects(child)


def _crop_vectors(parent, destination, meta, col, row, size):
    """Select parent vector observations intersecting the exact child AOI."""
    west, north = meta['west'] + col, meta['north'] - row
    child = box(west, north - size, west + size, north)
    transformer = Transformer.from_crs(4326, meta['crs'], always_xy=True)

    ways = json.loads((parent/'osm-ways.json').read_text())
    ways = [way for way in ways if _intersects_child(
        way.get('coordinates', ()), transformer, child, way.get('closed', False))]
    (destination/'osm-ways.json').write_text(json.dumps(ways))

    county = json.loads((parent/'cook-buildings-2022.json').read_text())
    features = []
    for feature in county.get('features', ()):
        rings = feature.get('geometry', {}).get('rings', ())
        if any(_intersects_child(ring, transformer, child, True) for ring in rings):
            features.append(feature)
    county['features'] = features
    county.pop('exceededTransferLimit', None)
    (destination/'cook-buildings-2022.json').write_text(json.dumps(county))
    return ways, county


def crop(parent,destination,col,row,size):
    meta=json.loads((parent/'sources.json').read_text())
    if any(not isinstance(v,int) or v%16 for v in (col,row,size)) or size<16:
        raise ValueError('Crop must align with metre chunks')
    if min(col,row)<0 or max(col+size,row+size)>meta['size']:
        raise ValueError('Crop must be wholly inside acquired raster')
    if destination.exists():raise FileExistsError(destination)
    window=Window(col,row,size,size)
    destination.mkdir(parents=True)
    raster_name=meta.get('elevation_raster','usgs-elevation.tif')
    with rasterio.open(parent/raster_name) as source:
        profile=source.profile.copy();profile.update(width=size,height=size,transform=source.window_transform(window))
        with rasterio.open(destination/raster_name,'w',**profile) as target:target.write(source.read(window=window))
    with np.load(parent/'rasters.npz') as source:
        arrays={key:source[key][row:row+size,col:col+size] for key in source.files}
    np.savez_compressed(destination/'rasters.npz',**arrays)
    ways,county=_crop_vectors(parent,destination,meta,col,row,size)
    meta.update(size=size,west=meta['west']+col,north=meta['north']-row)
    codes,counts=np.unique(arrays['cover'],return_counts=True)
    meta['cover_classes']=dict(zip(map(str,codes),map(int,counts)))
    meta['elevation_range_m']=[float(arrays['elevation'].min()),float(arrays['elevation'].max())]
    meta['elevation_sha256']=hashlib.sha256((destination/raster_name).read_bytes()).hexdigest()
    meta['osm_subset_sha256']=hashlib.sha256((destination/'osm-ways.json').read_bytes()).hexdigest()
    meta['county_sha256']=hashlib.sha256((destination/'cook-buildings-2022.json').read_bytes()).hexdigest()
    meta['buildings_available']=bool(county.get('features'))
    meta['derived_crop']={'parent':str(parent.resolve()),'window_xywh':[col,row,size,size],
        'resampling':'none','same_projection_origin':True,
        'parent_manifest_sha256':hashlib.sha256((parent/'sources.json').read_bytes()).hexdigest(),
        'vector_selection':'exact child intersection in the frozen metric chart',
        'osm_feature_count':len(ways),'county_feature_count':len(county.get('features',()))}
    meta['cell_ground_distances_scope']='Inherited conservative parent-chart corner checks; crop is wholly contained'
    (destination/'sources.json').write_text(json.dumps(meta,indent=2))
    return destination


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--col',type=int,required=True);p.add_argument('--row',type=int,required=True);p.add_argument('--size',type=int,required=True)
    a=p.parse_args();print(crop(a.source,a.output,a.col,a.row,a.size))
