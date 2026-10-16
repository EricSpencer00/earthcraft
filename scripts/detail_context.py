"""Shared analysis grid for detail layers: canonical world surface + aligned sources.

Everything is expressed on the block grid of one tile (1 block = 1 m, X east,
Z south) using the inherited frame: ``X = E - frame.west``, ``Z = frame.north - N``.
The canonical save decides where the ground is (``ground_y``) and which
columns already hold structure (``built``); sources only decide *what* goes
on that surface. Nothing here writes to a world.
"""
import json
from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import transform as shapely_transform

from detail_render import load_window

GROUND_BLOCKS = {'minecraft:grass_block', 'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:podzol',
                 'minecraft:sand', 'minecraft:gravel', 'minecraft:moss_block', 'minecraft:rooted_dirt'}
NATURAL_GROUND = {'minecraft:grass_block', 'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:podzol',
                  'minecraft:moss_block', 'minecraft:rooted_dirt'}
WATER = {'minecraft:water'}


class Tile:
    """A square block window [x0, x0+size) x [z0, z0+size)."""

    def __init__(self, frame, x0, z0, size):
        self.frame, self.x0, self.z0, self.size = frame, int(x0), int(z0), int(size)
        self.to_frame = Transformer.from_crs(4326, frame['crs'], always_xy=True)
        self.from_frame = Transformer.from_crs(frame['crs'], 4326, always_xy=True)
        self.transform = Affine(1, 0, self.x0, 0, 1, self.z0)

    def lonlat_to_block(self, lon, lat):
        e, n = self.to_frame.transform(np.asarray(lon, float), np.asarray(lat, float))
        return np.asarray(e) - self.frame['west'], self.frame['north'] - np.asarray(n)

    def block_to_lonlat(self, x, z):
        return self.from_frame.transform(np.asarray(x, float) + self.frame['west'], self.frame['north'] - np.asarray(z, float))

    def geometry(self, geojson_geometry):
        """GeoJSON (lon/lat) geometry -> shapely geometry in continuous block coordinates."""
        def project(lon, lat, *_):
            return self.lonlat_to_block(lon, lat)
        return shapely_transform(project, shape(geojson_geometry))

    def raster(self, shapes, fill=0, dtype='uint8', all_touched=False):
        shapes = [(g, v) for g, v in shapes if g is not None and not g.is_empty]
        if not shapes:
            return np.full((self.size, self.size), fill, dtype)
        return rasterize(shapes, out_shape=(self.size, self.size), transform=self.transform, fill=fill,
                         dtype=dtype, all_touched=all_touched)

    def inside(self, x, z):
        return (x >= self.x0) & (x < self.x0 + self.size) & (z >= self.z0) & (z < self.z0 + self.size)


def world_surface(region_dir, tile, ylo=40, yhi=470):
    """Canonical ground and occupancy from the region copies (never the live save)."""
    vol, names = load_window(region_dir, (tile.x0, tile.z0, tile.x0 + tile.size, tile.z0 + tile.size), ylo, yhi)
    base = np.array([k.split('[', 1)[0] for k in names], dtype=object)
    solid = np.array([b not in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air') for b in base])
    occupied = solid[vol]
    first_air = np.argmax(~occupied, axis=2)
    surface_y = first_air - 1 + ylo
    top = vol.shape[2] - 1 - np.argmax(occupied[:, :, ::-1], axis=2) + ylo
    zz, xx = np.indices(first_air.shape)
    surface_id = vol[zz, xx, np.maximum(first_air - 1, 0)]
    # Robust local ground: median of low surface columns in a 31 m window.
    low = np.where(surface_y <= np.percentile(surface_y, 60) + 2, surface_y, np.nan).astype(float)
    filled = low.copy()
    hole = np.isnan(filled)
    idx = ndimage.distance_transform_edt(hole, return_distances=False, return_indices=True)
    filled = filled[tuple(idx)]
    local = ndimage.median_filter(filled, size=31)
    built = (top > surface_y) | (surface_y > local + 2)
    ground_cols = ~built
    idx = ndimage.distance_transform_edt(~ground_cols, return_distances=False, return_indices=True)
    ground_y = np.where(ground_cols, surface_y, surface_y[tuple(idx)])
    return {'vol': vol, 'names': names, 'ylo': ylo, 'ground_y': ground_y.astype(np.int32),
            'surface_key': np.asarray(names, dtype=object)[surface_id], 'surface_id': surface_id,
            'top_y': top.astype(np.int32), 'built': built}


def lidar_rasters(points, tile, cell=1.0, chm_max=32.0):
    """DTM, intensity, relief and above-ground class rasters on the tile grid.

    ``points`` is (xyz in frame metres, cls, intensity). Vegetation classes 3/4/5
    in this survey are height bands (all single returns), so the CHM here is
    an *above-ground non-building* surface; tree admission happens later.
    """
    xyz, cls, intensity = points
    frame = tile.frame
    bx = xyz[:, 0] - frame['west']
    bz = frame['north'] - xyz[:, 1]
    r = int(round(1 / cell))
    n = tile.size * r
    col = np.floor((bx - tile.x0) * r).astype(np.int64)
    row = np.floor((bz - tile.z0) * r).astype(np.int64)
    ok = (col >= 0) & (col < n) & (row >= 0) & (row < n)
    out = {'cell': cell}
    g = ok & (cls == 2)
    flat = row[g] * n + col[g]
    count = np.bincount(flat, minlength=n * n).reshape(n, n)
    zsum = np.bincount(flat, weights=xyz[g, 2], minlength=n * n).reshape(n, n)
    isum = np.bincount(flat, weights=intensity[g].astype(float), minlength=n * n).reshape(n, n)
    valid = count > 0
    dtm = np.where(valid, zsum / np.maximum(count, 1), np.nan)
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    dtm_filled = dtm[tuple(idx)]
    out.update(ground_count=count.astype(np.uint16), dtm=dtm.astype(np.float32), dtm_filled=dtm_filled.astype(np.float32),
               intensity=np.where(valid, isum / np.maximum(count, 1), np.nan).astype(np.float32))
    out['relief'] = (dtm_filled - ndimage.median_filter(dtm_filled, size=int(9 * r) | 1)).astype(np.float32)
    # Height above the (filled) DTM for every point in the tile.
    rows, cols = np.clip(row, 0, n - 1), np.clip(col, 0, n - 1)
    hag = xyz[:, 2] - dtm_filled[rows, cols]
    above = ok & np.isin(cls, (1, 3, 4, 5)) & (hag > 0.5) & (hag < chm_max)
    flat = row[above] * n + col[above]
    chm = np.zeros(n * n, np.float32)
    np.maximum.at(chm, flat, hag[above].astype(np.float32))
    out['chm'] = chm.reshape(n, n)
    out['above_count'] = np.bincount(flat, minlength=n * n).reshape(n, n).astype(np.uint16)
    b = ok & (cls == 6)
    out['building_count'] = np.bincount(row[b] * n + col[b], minlength=n * n).reshape(n, n).astype(np.uint16)
    tall = ok & np.isin(cls, (1, 5)) & (hag >= chm_max)
    out['tall_count'] = np.bincount(row[tall] * n + col[tall], minlength=n * n).reshape(n, n).astype(np.uint16)
    out['hag'], out['point_row'], out['point_col'], out['point_ok'], out['_cls'] = hag, row, col, ok, cls
    return out


def load_osm(path, tile):
    """OSM Overpass JSON -> list of {'id','type','tags','geom'} in block coordinates."""
    data = json.loads(Path(path).read_text())
    features = []
    for e in data['elements']:
        tags = e.get('tags', {})
        if e['type'] == 'node':
            if not tags:
                continue
            x, z = tile.lonlat_to_block(e['lon'], e['lat'])
            geom = Point(float(x), float(z))
        elif e['type'] == 'way' and 'geometry' in e:
            pts = [(p['lon'], p['lat']) for p in e['geometry'] if p]
            if len(pts) < 2:
                continue
            x, z = tile.lonlat_to_block([p[0] for p in pts], [p[1] for p in pts])
            coords = list(zip(map(float, x), map(float, z)))
            closed = e.get('nodes', [None])[0] == e.get('nodes', [None])[-1] and len(coords) >= 4
            area = closed and (tags.get('area') == 'yes' or any(k in tags for k in ('building', 'landuse', 'leisure', 'natural', 'amenity', 'building:part', 'area:highway')) and tags.get('highway') in (None, 'pedestrian'))
            geom = Polygon(coords).buffer(0) if area else LineString(coords)
        else:
            continue
        features.append({'id': f"{e['type']}/{e['id']}", 'type': e['type'], 'tags': tags, 'geom': geom})
    return features, data.get('osm3s', {})


def load_geojson(path, tile):
    data = json.loads(Path(path).read_text())
    out = []
    for f in data['features']:
        if f.get('geometry'):
            out.append({'properties': f['properties'], 'geom': tile.geometry(f['geometry'])})
    return out
