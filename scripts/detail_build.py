"""Build street-detail layers for one tile from region COPIES and open sources.

    PYTHONPATH=scripts python scripts/detail_build.py --regions COPY/region \
        --frame frame.json --points ept-crop --sources sources/ \
        --tile -256 -256 512 --halo 24 --out layers/ [--facades facades.json]

Writes ``<out>/<layer>/`` directories (see ``detail_layer``) for ``trees``,
``street``, ``streetlights`` (City inventory; licence unstated, kept separate
so it can be excluded from a public release) and optionally ``facades``.
Every write goes through ``WriteGuard``. The canonical save is never read
directly: pass copies.
"""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from detail_context import NATURAL_GROUND, Tile, lidar_rasters, load_geojson, load_osm, world_surface
from detail_guard import WriteGuard
from detail_layer import LayerBuilder
from detail_points import load as load_points
import detail_street as st
import detail_trees as tr

LIC = {
    'usgs': 'Public domain (USGS 3DEP)',
    'osm': 'ODbL 1.0 (c) OpenStreetMap contributors',
    'chicago': 'City of Chicago Data Portal Terms of Use',
    'chicago_gis': 'Unstated (City of Chicago public ArcGIS service); verify before public release',
}


def build_context(a, frame):
    tile = Tile(frame, a.tile[0] - a.halo, a.tile[1] - a.halo, a.tile[2] + 2 * a.halo)
    accept = (a.tile[0], a.tile[1], a.tile[0] + a.tile[2], a.tile[1] + a.tile[2])
    world = world_surface(a.regions, tile)
    xyz, cls, _, _, intensity = load_points(a.points)
    lr = lidar_rasters((xyz, cls, intensity), tile, cell=1.0)
    lrh = lidar_rasters((xyz, cls, intensity), tile, cell=0.5)
    sources = Path(a.sources)
    osm, osm_meta = load_osm(sources / 'osm_halo.json', tile)
    chicago_buildings = load_geojson(sources / 'chicago_buildings_syp8-uezg.geojson', tile)
    footprints = [f['geom'] for f in chicago_buildings]
    footprints += [f['geom'] for f in osm if ('building' in f['tags'] or 'building:part' in f['tags'])
                   and f['geom'].geom_type in ('Polygon', 'MultiPolygon')
                   and f['tags'].get('building') not in ('roof', 'canopy') and f['tags'].get('layer', '0') in ('0', '1')]
    footprint_mask = tile.raster([(g, 1) for g in footprints]).astype(bool)
    from affine import Affine
    from rasterio.features import rasterize
    half_transform = Affine(0.5, 0, tile.x0, 0, 0.5, tile.z0)
    fp_half = rasterize([(g, 1) for g in footprints], out_shape=(2 * tile.size, 2 * tile.size),
                        transform=half_transform).astype(bool)
    ways = st.drive_ways(osm)
    poly, curb_receipt = st.carriageway(ways, lrh, tile, fp_half | (lrh['building_count'] > 0))
    road_mask = tile.raster([(poly, 1)]).astype(bool)
    road_half = rasterize([(poly, 1)], out_shape=(2 * tile.size, 2 * tile.size), transform=half_transform).astype(bool)
    base = np.vectorize(lambda k: k.split('[', 1)[0])(world['surface_key'])
    water_mask = base == 'minecraft:water'
    protect = np.zeros_like(road_mask)
    landmarks = []
    for f in osm:
        if f['id'] in a.protect_osm or (is_landmark(f['tags']) and f['geom'].geom_type in ('Polygon', 'MultiPolygon')):
            protect |= tile.raster([(f['geom'].buffer(a.protect_buffer), 1)]).astype(bool)
            if f['id'] not in a.protect_osm:
                landmarks.append(f['id'])
    classes = st.surface_classes(tile, world, road_mask, footprint_mask, osm, protect=protect)
    paint_half, _ = st.intensity_paint(lrh, road_half)
    return dict(tile=tile, accept=accept, world=world, lr=lr, lrh=lrh, osm=osm, osm_meta=osm_meta,
                chicago_buildings=chicago_buildings, footprint_mask=footprint_mask, ways=ways,
                road_poly=poly, curb_receipt=curb_receipt, road_mask=road_mask, road_half=road_half,
                water_mask=water_mask, classes=classes, protect=protect, paint_half=paint_half, surface_base=base,
                landmarks=landmarks)


# Mapped heritage buildings keep their scanned shape: closing shells or recolouring
# would erase real recesses, battlements and ornament (the 1869 Pumping Station was
# flattened this way). Every layer treats them like --protect-osm.
LANDMARK_KEYS = ('historic', 'heritage', 'heritage:operator', 'ref:nrhp')
LANDMARK_BUILDINGS = {'church', 'cathedral', 'chapel', 'mosque', 'synagogue', 'temple', 'castle'}


def is_landmark(tags):
    return any(tags.get(k) not in (None, 'no') for k in LANDMARK_KEYS) or tags.get('building') in LANDMARK_BUILDINGS


def inside(accept, x, z):
    return accept[0] <= x < accept[2] and accept[1] <= z < accept[3]


def trees_layer(ctx, frame, project):
    tile, world, accept = ctx['tile'], ctx['world'], ctx['accept']
    trees, rejected, labels, chm = tr.build_trees(ctx['lr'], world, ctx['footprint_mask'] | ctx['protect'], ctx['road_mask'],
                                                   ctx['water_mask'], tile, accept)
    layer = LayerBuilder('trees', frame, 'LiDAR canopy trees (USGS 3DEP %s)' % project)
    blocked = world['built'] | ctx['footprint_mask'] | ctx['protect']
    counts = {'trees': 0, 'pits': 0, 'skipped_cells': 0}
    shapes = {}
    for tree in trees:
        voxels, gy = tr.tree_voxels(tree, labels, chm, world['ground_y'], tile.x0, tile.z0)
        common = {'feature': tree['id'], 'height_m': tree['height'], 'crown_radius_m': tree['radius'],
                  'crown_base_m': tree['crown_base'], 'returns': tree['points'], 'shape': tree['shape']}
        observed = layer.source('tree_canopy', f'USGS 3DEP {project}', LIC['usgs'], 'observed',
                                note='position, height, crown footprint and upper canopy surface', **common)
        estimated = layer.source('tree_structure', f'USGS 3DEP {project}', LIC['usgs'], 'estimated',
                                 note='trunk, branches, crown underside and leaf block chosen from crown shape', **common)
        placed = 0
        for (x, y, z), (state, role) in sorted(voxels.items()):
            r, c = z - tile.z0, x - tile.x0
            if not (0 <= r < tile.size and 0 <= c < tile.size) or blocked[r, c] or y <= world['ground_y'][r, c]:
                counts['skipped_cells'] += 1
                continue
            if 'log' in state and role == 'trunk_estimated':
                priority = 3
            elif 'log' in state:
                priority = 2
            else:
                priority = 1
            layer.add(x, y, z, state, observed if role == 'canopy_observed' else estimated, priority=priority)
            placed += 1
        tr_r, tr_c = tree['trunk']
        surface = world['surface_key'][tr_r, tr_c]
        if surface.split('[')[0] not in NATURAL_GROUND:
            layer.add(tile.x0 + tr_c, gy, tile.z0 + tr_r, tr.PIT, estimated, expect=surface, priority=4)
            counts['pits'] += 1
        counts['trees'] += 1
        shapes[tree['shape']] = shapes.get(tree['shape'], 0) + 1
    osm_trees = [(f['geom'].x, f['geom'].y) for f in ctx['osm'] if f['tags'].get('natural') == 'tree'
                 and f['geom'].geom_type == 'Point' and inside(accept, f['geom'].x, f['geom'].y)]
    tops = np.array([(tile.x0 + t['top'][1] + 0.5, tile.z0 + t['top'][0] + 0.5) for t in trees]) if trees else np.zeros((0, 2))
    matched = sum(1 for x, z in osm_trees if len(tops) and np.hypot(tops[:, 0] - x, tops[:, 1] - z).min() <= 3.0)
    report = {'trees_admitted': counts['trees'], 'tree_pits': counts['pits'], 'shapes': shapes,
              'candidate_tops': counts['trees'] + sum(rejected.values()), 'rejected': rejected,
              'cells_skipped_on_buildings_or_below_ground': counts['skipped_cells'],
              'osm_tree_nodes_in_tile': len(osm_trees), 'osm_trees_within_3m_of_a_lidar_top': matched,
              'height_m_percentiles': np.percentile([t['height'] for t in trees], [10, 50, 90]).round(1).tolist() if trees else None}
    return layer, report, trees


def street_layer(ctx, frame, project, a):
    tile, world, accept, classes = ctx['tile'], ctx['world'], ctx['accept'], ctx['classes']
    layer = LayerBuilder('street', frame, 'Sidewalks, curbs, paint and street furniture')
    gy = world['ground_y']
    surf = world['surface_key']
    report = {}
    # Sidewalk + curb surfaces (carriageway edge measured from LiDAR curb profiles where available).
    measured = sum(1 for r in ctx['curb_receipt'] for s in ('left_source', 'right_source') if r[s] == 'lidar_curb_profile')
    curb_src = layer.source('curb', 'USGS 3DEP %s ground profile + OSM roads' % project, LIC['usgs'] + '; ' + LIC['osm'], 'observed',
                            note='carriageway edge at the measured curb rise; %d of %d road sides measured, rest from OSM lanes' % (measured, 2 * len(ctx['curb_receipt'])))
    walk_src = layer.source('sidewalk', 'Canonical world pavement minus measured carriageway', LIC['usgs'] + '; ' + LIC['osm'], 'derived',
                            note='material (smooth stone) is representative; extent derived from curb line and footprints')
    grass_src = layer.source('green', 'OSM landuse=grass/garden polygons; base-world orthophoto green cells', LIC['osm'] + '; ' + 'Public domain (NAIP via base world)', 'derived')
    n_walk = n_curb = n_green = 0
    for r, c in zip(*np.nonzero(classes['sidewalk'] | classes['green'])):
        x, z = tile.x0 + c, tile.z0 + r
        if not inside(accept, x, z):
            continue
        if classes['green'][r, c] and not classes['road'][r, c]:
            layer.add(x, gy[r, c], z, st.GRASS, grass_src, expect=surf[r, c]); n_green += 1
        elif classes['curb'][r, c]:
            layer.add(x, gy[r, c], z, st.CURB, curb_src, expect=surf[r, c]); n_curb += 1
        else:
            layer.add(x, gy[r, c], z, st.SIDEWALK, walk_src, expect=surf[r, c]); n_walk += 1
    report['sidewalk_cells'], report['curb_cells'], report['green_cells'] = n_walk, n_curb, n_green
    patch_src = layer.source('carriageway_patch', 'OSM roads + measured carriageway', LIC['osm'] + '; ' + LIC['usgs'], 'derived',
                             note='grass, sand or orthophoto-green blocks inside the carriageway resurfaced with the surrounding road block')
    road_block = st.local_road_block(ctx['surface_base'], classes['road'] & ~classes['road_patch'])
    n_patch = 0
    for r, c in zip(*np.nonzero(classes['road_patch'])):
        x, z = tile.x0 + c, tile.z0 + r
        if inside(accept, x, z):
            layer.add(x, gy[r, c], z, road_block[r, c], patch_src, expect=surf[r, c]); n_patch += 1
    report['carriageway_patch_cells'] = n_patch
    # Crosswalks.
    crossings = st.crosswalk_cells(ctx['osm'], ctx['road_mask'], tile, ctx['lrh'], ctx['paint_half'])
    cross_mask = np.zeros_like(ctx['road_mask'])
    n_cross = 0
    supports = []
    for cw in crossings:
        support = cw['lidar_paint_support']
        supports.append(support)
        evidence = 'observed' if cw['tag'] and support is not None and support >= 0.15 else 'estimated'
        src = layer.source('crosswalk', 'OSM footway=crossing' + (' + LiDAR intensity' if evidence == 'observed' else ''),
                           LIC['osm'] + ('; ' + LIC['usgs'] if evidence == 'observed' else ''), evidence,
                           feature=cw['osm'], style=cw['style'], markings_tag=cw['tag'],
                           lidar_paint_support=None if support is None else round(support, 3))
        for x, z in cw['cells']:
            r, c = z - tile.z0, x - tile.x0
            cross_mask[r, c] = True
            if inside(accept, x, z):
                layer.add(x, gy[r, c], z, st.PAINT_WHITE, src, expect=surf[r, c], priority=2); n_cross += 1
    report['crosswalks'] = len(crossings)
    report['crosswalk_cells'] = n_cross
    report['crosswalk_lidar_support_median'] = float(np.median([s for s in supports if s is not None])) if supports else None
    # Lane lines.
    paint_blocks = st.paint_blocks(ctx['paint_half'])
    lines, cells = st.lane_lines(ctx['ways'], ctx['road_mask'], paint_blocks, tile, cross_mask)
    curb_distance = ndimage.distance_transform_edt(ctx['road_mask'])
    line_sources = {}
    n_lane = {'observed': 0, 'estimated': 0}
    for (x, z), i in cells.items():
        r, c = z - tile.z0, x - tile.x0
        if not inside(accept, x, z) or curb_distance[r, c] < 2:
            continue
        line = lines[i]
        key = (line['osm'], line['evidence'])
        if key not in line_sources:
            line_sources[key] = layer.source('lane_marking', 'OSM road + LiDAR intensity peaks' if line['evidence'] == 'observed' else 'OSM lanes tag',
                                             LIC['osm'] + ('; ' + LIC['usgs'] if line['evidence'] == 'observed' else ''), line['evidence'],
                                             feature=line['osm'], colour_rule='yellow centre line on two-way roads (estimated colour)')
        state = st.PAINT_YELLOW if line['colour'] == 'yellow' else st.PAINT_WHITE
        layer.add(x, gy[r, c], z, state, line_sources[key], expect=surf[r, c], priority=1)
        n_lane[line['evidence']] += 1
    report['lane_line_cells'] = n_lane
    report['lane_lines'] = {'observed': sum(1 for l in lines if l['evidence'] == 'observed'),
                            'estimated': sum(1 for l in lines if l['evidence'] == 'estimated')}
    # Furniture (OSM nodes + Chicago municipal points).
    items = []
    for f in ctx['osm']:
        t, g = f['tags'], f['geom']
        if g.geom_type != 'Point':
            continue
        kind = None
        if t.get('highway') == 'bus_stop':
            kind = 'bus_stop'
        elif t.get('emergency') == 'fire_hydrant':
            kind = 'fire_hydrant'
        elif t.get('amenity') == 'post_box':
            kind = 'post_box'
        elif t.get('amenity') == 'waste_basket':
            kind = 'waste_basket'
        elif t.get('amenity') == 'bench':
            kind = 'bench'
        elif t.get('barrier') == 'bollard':
            kind = 'bollard'
        elif t.get('amenity') == 'bicycle_parking':
            kind = 'bike_rack'
        if kind:
            items.append((kind, g.x, g.y, t, 'OSM ' + f['id'], LIC['osm']))
    sources = Path(a.sources)
    for name, kind in (('chicago_bikeracks_hgdw-64h3.geojson', 'bike_rack'), ('chicago_divvy_bbyy-e7gq.geojson', 'bike_share')):
        path = sources / name
        if path.exists():
            for f in load_geojson(path, tile):
                items.append((kind, f['geom'].x, f['geom'].y, f['properties'], 'Chicago ' + name.split('_')[-1].split('.')[0], LIC['chicago']))
    # Signals: OSM signal nodes -> one pole per curb quadrant.
    signal_points = [(f['geom'].x, f['geom'].y, f['id']) for f in ctx['osm']
                     if f['tags'].get('highway') == 'traffic_signals' and f['geom'].geom_type == 'Point']
    for x, z, members in st.signal_corners(signal_points, classes, tile):
        items.append(('traffic_signal', x + 0.5, z + 0.5, {'osm_nodes': members[:6]}, 'OSM traffic_signals cluster', LIC['osm']))
    for f in ctx['osm']:
        if f['tags'].get('highway') == 'street_lamp' and f['geom'].geom_type == 'Point':
            items.append(('street_lamp', f['geom'].x, f['geom'].y, {}, 'OSM ' + f['id'], LIC['osm']))
    placed = {}
    dedupe = []
    for kind, x, z, tags, ref, lic in items:
        if not inside(accept, x, z):
            continue
        if any(k == kind and math.hypot(x - px, z - pz) < 2.5 for k, px, pz in dedupe):
            continue
        spot = (0.0, int(math.floor(x)), int(math.floor(z))) if kind == 'traffic_signal' else st.snap_to_sidewalk(x, z, tile, classes)
        if spot is None:
            placed.setdefault(kind, {'placed': 0, 'no_sidewalk_cell': 0})['no_sidewalk_cell'] += 1
            continue
        _, bx, bz = spot
        r, c = bz - tile.z0, bx - tile.x0
        direction = st.toward_road(bx, bz, tile, classes)
        src = layer.source(kind, ref.split(' ')[0] + ' ' + ('municipal dataset' if ref.startswith('Chicago') else 'mapped point'),
                           lic, 'derived', feature=ref, position_shift_m=round(spot[0], 2),
                           note='mapped position; block shape is a representative model',
                           **{k: v for k, v in tags.items() if k in ('name', 'shelter', 'bench', 'total_docks', 'station_name', 'ref')})
        for (vx, vy, vz, state) in st.furniture_blocks(kind, bx, bz, int(gy[r, c]), direction, tags):
            layer.add(vx, vy, vz, state, src, priority=5)
        dedupe.append((kind, x, z))
        placed.setdefault(kind, {'placed': 0, 'no_sidewalk_cell': 0})['placed'] += 1
    report['furniture'] = placed
    return layer, report


def building_footprints(ctx):
    """One footprint per building: OSM building ways, plus City footprints OSM lacks."""
    osm = [f for f in ctx['osm'] if 'building' in f['tags'] and f['geom'].geom_type in ('Polygon', 'MultiPolygon')
           and f['tags'].get('building') not in ('roof', 'canopy', 'no') and f['tags'].get('layer', '0') in ('0', '1')]
    from shapely.strtree import STRtree
    tree = STRtree([f['geom'] for f in osm]) if osm else None
    out = [{'id': f['id'], 'geom': f['geom'], 'tags': f['tags'], 'source': 'osm'} for f in osm]
    for f in ctx['chicago_buildings']:
        g = f['geom']
        if g.is_empty or g.area < 8:
            continue
        overlap = sum(g.intersection(osm[i]['geom']).area for i in tree.query(g)) if tree is not None else 0
        if overlap / g.area < 0.3:
            out.append({'id': 'chicago/' + str(f['properties'].get('bldg_id')), 'geom': g.buffer(0),
                        'tags': {}, 'source': 'chicago'})
    return out


def buildings_layer(ctx, frame):
    import detail_buildings as bd
    layer = LayerBuilder('buildings', frame, 'Regularized building volumes (scan placement + heights, closed shells)')
    footprints = building_footprints(ctx)
    edits, report = bd.regularize(ctx['world'], footprints, ctx['tile'], ctx['accept'], ctx['protect'])
    lic = {'osm': LIC['osm'], 'chicago': LIC['chicago']}
    rows = {}
    for f in footprints:
        rows[f['id']] = None
    for x, y, z, state, expect, fid in edits:
        if rows.get(fid) is None:
            if fid == 'outline_band':
                rows[fid] = layer.source('outline_band_cleanup', 'Canonical save occupancy + footprints', 'n/a (derived from the save)', 'derived',
                                         note='scan wall returns within 2 m outside a closed outline, above street level')
            elif fid == 'fragment':
                rows[fid] = layer.source('fragment_removal', 'Canonical save occupancy', 'n/a (derived from the save)', 'derived',
                                         note='small floating components outside all footprints, not attached to a building')
            else:
                src = 'osm' if fid.startswith('way/') else 'chicago'
                rows[fid] = layer.source('building_volume', 'Footprint (%s) + roof heights from the canonical save' % src,
                                         lic[src], 'derived', feature=fid,
                                         note='outline/step walls and roof plate closed; interior clutter removed; heights unchanged')
        layer.add(x, y, z, state, rows[fid], expect=expect)
    report['footprints_osm'] = sum(1 for f in footprints if f['source'] == 'osm')
    report['footprints_chicago_only'] = sum(1 for f in footprints if f['source'] == 'chicago')
    return layer, report


def streetlights_layer(ctx, frame, a):
    tile, world, accept, classes = ctx['tile'], ctx['world'], ctx['accept'], ctx['classes']
    layer = LayerBuilder('streetlights', frame, 'City of Chicago light-structure inventory positions')
    path = Path(a.sources) / 'chicago_streetlights_gisapps-STREETLIGHTDEMO-0.geojson'
    report = {'inventory_points_in_tile': 0, 'placed': 0, 'no_sidewalk_cell': 0, 'lidar_pole_confirmed': 0}
    lr = ctx['lr']
    hag, row, col, ok, cls = lr['hag'], lr['point_row'], lr['point_col'], lr['point_ok'], lr['_cls']
    pole_pts = ok & np.isin(cls, (1, 3, 4, 5)) & (hag > 3) & (hag < 12)
    pole_count = np.bincount(row[pole_pts] * tile.size + col[pole_pts], minlength=tile.size ** 2).reshape(tile.size, tile.size)
    for f in load_geojson(path, tile):
        x, z = f['geom'].x, f['geom'].y
        if not inside(accept, x, z):
            continue
        report['inventory_points_in_tile'] += 1
        spot = st.snap_to_sidewalk(x, z, tile, classes, max_shift=3)
        if spot is None:
            report['no_sidewalk_cell'] += 1
            continue
        _, bx, bz = spot
        r, c = bz - tile.z0, bx - tile.x0
        near = pole_count[max(0, r - 1):r + 2, max(0, c - 1):c + 2].sum() >= 3
        report['lidar_pole_confirmed'] += int(near)
        src = layer.source('street_lamp', 'City of Chicago STREETLIGHTDEMO LightStructures', LIC['chicago_gis'], 'derived',
                           feature=f"CBI_ID {f['properties'].get('CBI_ID')}", comed_pole_id=f['properties'].get('COMED_POLE_ID'),
                           position_shift_m=round(spot[0], 2), lidar_pole_returns_nearby=bool(near),
                           note='inventory position; pole/arm/lantern shape is representative')
        for (vx, vy, vz, state) in st.furniture_blocks('street_lamp', bx, bz, int(world['ground_y'][r, c]),
                                                         st.toward_road(bx, bz, tile, classes)):
            layer.add(vx, vy, vz, state, src, priority=5)
        report['placed'] += 1
    return layer, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--regions', type=Path, required=True)
    p.add_argument('--frame', type=Path, required=True)
    p.add_argument('--points', type=Path, required=True)
    p.add_argument('--project', default='USGS_LPC_IL_4County_Cook_2017_LAS_2019')
    p.add_argument('--sources', type=Path, required=True)
    p.add_argument('--tile', type=int, nargs=3, metavar=('X0', 'Z0', 'SIZE'), required=True)
    p.add_argument('--halo', type=int, default=24)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--facades', type=Path, help='JSON list of facade targets (see detail_facade)')
    p.add_argument('--layers', default='trees,street,streetlights,facades')
    p.add_argument('--protect-osm', nargs='*', default=['way/130147025'],
                   help='OSM features (with photo-skin anchors) that no layer may touch, plus a buffer')
    p.add_argument('--protect-buffer', type=float, default=3.0)
    p.add_argument('--max-bytes', type=int, default=60 * 10**6)
    p.add_argument('--free-floor', type=int, default=15 * 2**30)
    a = p.parse_args()
    frame = json.loads(a.frame.read_text())
    guard = WriteGuard(a.out, a.max_bytes, a.free_floor)
    start = time.time()
    ctx = build_context(a, frame)
    summary = {'tile': a.tile, 'halo': a.halo, 'context_seconds': round(time.time() - start, 1),
               'osm_base': ctx['osm_meta'].get('timestamp_osm_base'),
               'curb_sides_measured': sum(1 for r in ctx['curb_receipt'] for s in ('left_source', 'right_source') if r[s] == 'lidar_curb_profile'),
               'curb_sides_total': 2 * len(ctx['curb_receipt']),
               'protected_landmarks': ctx['landmarks']}
    wanted = a.layers.split(',')
    extra = {'tile': {'x0': a.tile[0], 'z0': a.tile[1], 'size': a.tile[2]}, 'built_with': 'scripts/detail_build.py'}
    if 'buildings' in wanted:
        layer, report = buildings_layer(ctx, frame)
        summary['buildings'] = layer.write(a.out / 'buildings', guard, {**extra, 'report': report})['edits_by_kind'] | {'report': report}
    if 'trees' in wanted:
        layer, report, _ = trees_layer(ctx, frame, a.project)
        summary['trees'] = layer.write(a.out / 'trees', guard, {**extra, 'report': report})['edits_by_kind'] | {'report': report}
    if 'street' in wanted:
        layer, report = street_layer(ctx, frame, a.project, a)
        summary['street'] = layer.write(a.out / 'street', guard, {**extra, 'report': report,
                                        'curb_receipt': ctx['curb_receipt']})['edits_by_kind'] | {'report': report}
    if 'streetlights' in wanted:
        layer, report = streetlights_layer(ctx, frame, a)
        summary['streetlights'] = layer.write(a.out / 'streetlights', guard, {**extra, 'report': report})['edits_by_kind'] | {'report': report}
    if 'facades' in wanted and a.facades:
        import detail_facade as fc
        layer, report = fc.facade_layer(ctx, frame, json.loads(a.facades.read_text()), a)
        summary['facades'] = layer.write(a.out / 'facades', guard, {**extra, 'report': report})['edits_by_kind'] | {'report': report}
    summary['seconds'] = round(time.time() - start, 1)
    guard.write_text(a.out / 'build-summary.json', json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == '__main__':
    main()
