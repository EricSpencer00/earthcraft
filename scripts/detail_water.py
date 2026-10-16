"""Water pass: mapped lake/river polygons -> water blocks at the measured surface.

The base save filled Lake Michigan east of the shore with solid terrain.
This layer splits the tile along the lake relation's shoreline ways (OSM
relation 1205149 near Chicago; piers/breakwaters stay land), keeps the side
containing a lake seed point, and turns the terrain there into water:

* surface: ``--surface-m`` (OSM ``ele=176`` for Lake Michigan by default, or a
  LiDAR class-9 median when available) mapped with the frame's vertical offset;
* bed: estimated shelving, 2 m at the shore deepening 1 m per 40 m to 10 m
  (NOAA Great Lakes bathymetry is the measured replacement; not yet ingested);
* every solid cell from the bed to the surface becomes water, anything above
  the surface in a water column becomes air; the bed itself becomes sand.

    PYTHONPATH=scripts python scripts/detail_water.py --regions COPY/region \
        --frame frame.json --osm osm_lake_michigan_members.json \
        --tile 512 0 512 --seed 1020 256 --out layers/
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import split, unary_union

from detail_context import Tile, world_surface
from detail_guard import WriteGuard
from detail_layer import LayerBuilder

WATER = 'minecraft:water[level=0]'


def water_polygon(tile, shore_lines, holes, seeds, land_seeds=()):
    frame_box = box(tile.x0, tile.z0, tile.x0 + tile.size, tile.z0 + tile.size)
    lines = unary_union([l for l in shore_lines if l.intersects(frame_box)])
    if lines.is_empty:
        return None
    pieces = split(frame_box, lines)
    water = [p for p in pieces.geoms if any(p.contains(Point(s)) for s in seeds)]
    if not water:
        return None
    poly = unary_union(water)
    if any(poly.contains(Point(s)) for s in land_seeds):
        raise ValueError('A land seed fell on the water side: shoreline does not split the tile as expected')
    if holes:
        poly = poly.difference(unary_union(holes))
    return poly


def build(region_dir, frame, osm_path, tile, seeds, surface_m, guard, out, land_seeds=()):
    data = json.loads(Path(osm_path).read_text())
    shore, holes = [], []
    for e in data['elements']:
        if e['type'] != 'way' or 'geometry' not in e:
            continue
        x, z = tile.lonlat_to_block([p['lon'] for p in e['geometry']], [p['lat'] for p in e['geometry']])
        coords = list(zip(map(float, x), map(float, z)))
        tags = e.get('tags', {})
        if tags.get('man_made') in ('pier', 'breakwater') or tags.get('seamark:type'):
            if len(coords) >= 4 and coords[0] == coords[-1]:
                holes.append(Polygon(coords).buffer(0))
            continue
        if len(coords) >= 2 and not tags.get('waterway'):
            shore.append(LineString(coords))
    poly = water_polygon(tile, shore, holes, seeds, land_seeds)
    if poly is None:
        raise ValueError('No water side found for the seeds in this tile')
    mask = tile.raster([(poly, 1)]).astype(bool)
    world = world_surface(region_dir, tile, ylo=20, yhi=200)
    vol, names, ylo = world['vol'], np.asarray(world['names'], dtype=object), world['ylo']
    base = np.array([k.split('[', 1)[0] for k in names], dtype=object)
    solid = ~np.isin(base, ['minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'])
    surface_y = int(math.floor(surface_m + frame['vertical_offset_m']))
    shore_dist = ndimage.distance_transform_edt(mask)
    depth = np.clip(2 + shore_dist / 40.0, 2, 10).astype(int)
    layer = LayerBuilder('water', frame, 'Lake surface water from mapped shoreline')
    src_water = layer.source('lake_water', 'OSM relation 1205149 (Lake Michigan) shoreline + ele tag', 'ODbL 1.0 (c) OpenStreetMap contributors',
                             'derived', surface_m=surface_m, surface_y=surface_y,
                             note='extent from mapped shoreline; surface from OSM ele (LiDAR class 9 preferred when acquired)')
    src_bed = layer.source('lake_bed', 'Estimated shelving profile', 'n/a', 'estimated',
                           note='2 m at shore, +1 m per 40 m offshore, max 10 m; replace with NOAA Great Lakes bathymetry')
    counts = {'water_columns': int(mask.sum()), 'water_cells': 0, 'cleared_above_surface': 0, 'bed_cells': 0}
    for r, c in zip(*np.nonzero(mask)):
        x, z = tile.x0 + c, tile.z0 + r
        bed = surface_y - int(depth[r, c])
        for y in range(bed + 1, surface_y + 1):
            iy = y - ylo
            current = names[vol[r, c, iy]]
            if current.startswith('minecraft:water'):
                continue
            layer.add(x, y, z, WATER, src_water, expect=current if solid[vol[r, c, iy]] else None)
            counts['water_cells'] += 1
        top = int(world['top_y'][r, c])
        for y in range(surface_y + 1, top + 1):
            iy = y - ylo
            if solid[vol[r, c, iy]]:
                layer.add(x, y, z, 'minecraft:air', src_water, expect=names[vol[r, c, iy]])
                counts['cleared_above_surface'] += 1
        iy = bed - ylo
        if solid[vol[r, c, iy]] and base[vol[r, c, iy]] != 'minecraft:sand':
            layer.add(x, bed, z, 'minecraft:sand', src_bed, expect=names[vol[r, c, iy]])
            counts['bed_cells'] += 1
    manifest = layer.write(Path(out) / 'water', guard, {'tile': {'x0': tile.x0, 'z0': tile.z0, 'size': tile.size},
                                                        'report': counts})
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--regions', type=Path, required=True)
    p.add_argument('--frame', type=Path, required=True)
    p.add_argument('--osm', type=Path, required=True)
    p.add_argument('--tile', type=int, nargs=3, required=True, metavar=('X0', 'Z0', 'SIZE'))
    p.add_argument('--seed', type=float, nargs=2, action='append', required=True, metavar=('X', 'Z'))
    p.add_argument('--land-seed', type=float, nargs=2, action='append', default=[], metavar=('X', 'Z'),
                   help='Known land points; the build fails if any ends up on the water side')
    p.add_argument('--surface-m', type=float, default=176.0)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--max-bytes', type=int, default=30 * 10**6)
    p.add_argument('--free-floor', type=int, default=15 * 2**30)
    a = p.parse_args()
    frame = json.loads(a.frame.read_text())
    guard = WriteGuard(a.out, a.max_bytes, a.free_floor)
    manifest = build(a.regions, frame, a.osm, Tile(frame, *a.tile), [tuple(s) for s in a.seed], a.surface_m, guard, a.out,
                     [tuple(s) for s in a.land_seed])
    print(json.dumps({k: manifest[k] for k in ('edits', 'edits_by_kind', 'report')}, indent=1))


if __name__ == '__main__':
    main()
