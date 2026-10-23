"""Keyless facade detail for every delivered tile: window rows per floor, a street door, wall material.

Every building is a closed OSM building way, or an Overture footprint OSM lacks (OSM wins where
they overlap). Its exposed walls are the footprint-edge columns whose outside neighbour is open
air at that height (party walls between touching buildings stay hidden); the top block of each
column is left as the roof edge. Per building, with evidence:

* floors: OSM ``building:levels`` when tagged ('tagged'), else the measured wall height / 3.3 m
  ('estimated');
* wall material or colour: OSM ``facade:``/``building:`` tags through material_router ('tagged'),
  else the Cook County Assessor's recorded exterior wall of the parcel inside the footprint
  ('recorded': masonry, frame, stucco), else a fixed style by building type and height ('estimated');
* window columns: a fixed bay phased on world coordinates, so neighbouring tiles agree
  ('estimated'); corners stay solid;
* door: one per building, on the ground-floor face nearest a road ('estimated').

Only existing exterior wall blocks change, and each edit requires the block it replaces, so
occupancy, roofs, roof colour and trees are untouched. No imagery is used (facade_measured: False).
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from scipy import ndimage
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.ops import transform as transform_geometry

from detail_guard import WriteGuard
from detail_layer import LayerBuilder

FLOOR_M = 3.3
GENERATED_WALL = 'minecraft:stone_bricks'  # the generic shell block metric_world writes
ROADS = {'minecraft:gray_concrete', 'minecraft:black_concrete'}
HOUSES = {'house', 'detached', 'semidetached_house', 'terrace', 'bungalow', 'residential', 'farm', 'cabin'}
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))  # (dx, dz) outward: east, west, south, north
DOOR = 'minecraft:dark_oak_planks'
STYLES = {  # estimated wall styles; one is picked per building from its id (stable across runs)
    'house': ('minecraft:bricks', 'minecraft:bricks', 'minecraft:brown_terracotta', 'minecraft:white_terracotta',
              'minecraft:light_gray_concrete', 'minecraft:smooth_sandstone'),
    'mid': ('minecraft:bricks', 'minecraft:brown_terracotta', 'minecraft:light_gray_concrete', 'minecraft:smooth_stone'),
    'tall': ('minecraft:light_gray_concrete', 'minecraft:white_concrete', 'minecraft:smooth_stone'),
}
RECORDED = {  # Cook County Assessor exterior wall -> plausible blocks (colour within a material is estimated)
    'masonry': ('minecraft:bricks', 'minecraft:bricks', 'minecraft:brown_terracotta'),
    'frame': ('minecraft:birch_planks', 'minecraft:white_terracotta', 'minecraft:light_gray_concrete',
              'minecraft:smooth_sandstone'),
    'frame_masonry': ('minecraft:bricks', 'minecraft:birch_planks'),
    'stucco': ('minecraft:smooth_sandstone', 'minecraft:white_concrete', 'minecraft:white_terracotta'),
}
TYPE_FLOORS = {'1': 1, '2': 2, '3': 3, '4': 2, '5': 1, '6': 1, '7': 2}  # assessor residence type -> storeys
GLASS = {'house': 'minecraft:gray_stained_glass', 'mid': 'minecraft:gray_stained_glass',
         'tall': 'minecraft:light_blue_stained_glass'}
ASSESSOR_LICENSE = "Cook County Open Data terms of use; Cook County Assessor's Office"
ASSESSOR_INDEX = 'cook-assessor-res-2024'
CAP = 400 * 2**20
FLOOR = 4 * 2**30


def stable(text, n):
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], 'big') % n


def footprints(sources, meta):
    """[(local geometry, {'id', 'tags'})]: Overture footprints OSM lacks first, then OSM buildings,
    so a rasterized label keeps the OSM building wherever both cover a cell."""
    from osm_json_to_kml import building_tag
    west, north = meta['west'], meta['north']
    shapes = []
    if meta.get('lq_footprint_receipt'):
        from regional_footprints import LQ, load_shapes
        for identifier, geometry in load_shapes(sources, meta, LQ):
            local = transform_geometry(lambda x, y, z=None: (np.asarray(x) - west, north - np.asarray(y)), geometry)
            shapes.append((make_valid(local), {'id': f'overture/{identifier}', 'tags': {}}))
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    for way in json.loads((Path(sources) / 'osm-ways.json').read_text()):
        tags = way['tags']
        if not way.get('closed') or len(way['coordinates']) < 4 or not tags.get('building') or tags.get('building') == 'no':
            continue
        if not building_tag(tags):
            continue
        x, y = project.transform(*zip(*way['coordinates']))
        shapes.append((make_valid(Polygon(zip(np.asarray(x) - west, north - np.asarray(y)))),
                       {'id': f"osm/{way['id']}", 'tags': tags}))
    return [(geom, info) for geom, info in shapes if not geom.is_empty]


def labels(shapes, size):
    if not shapes:
        return np.zeros((size, size), np.int32)
    return rasterize([(geom, index + 1) for index, (geom, _) in enumerate(shapes)], out_shape=(size, size),
                     transform=Affine.identity(), fill=0, dtype='int32')


def exposed(lab, ground, vol, solid, ylo):
    """Exposed wall cells: dict of arrays z, x, y, dx, dz, building (label), corner."""
    Z, X, H = vol.shape
    pad = np.pad(lab, 1)
    out = []
    edges = {}
    for dx, dz in DIRS:
        neighbour = pad[1 + dz:1 + dz + Z, 1 + dx:1 + dx + X]
        edges[(dx, dz)] = (lab > 0) & (neighbour == 0)
    for dx, dz in DIRS:
        edge = edges[(dx, dz)]
        z, x = np.nonzero(edge)
        if not len(z):
            continue
        nz, nx = np.clip(z + dz, 0, Z - 1), np.clip(x + dx, 0, X - 1)
        own = solid[vol[z, x, :]]
        outside = solid[vol[nz, nx, :]]
        border = (z + dz < 0) | (z + dz >= Z) | (x + dx < 0) | (x + dx >= X)
        outside[border] = False  # beyond the tile: treat as open
        top = np.where(own.any(axis=1), H - 1 - np.argmax(own[:, ::-1], axis=1), -1)
        yy = np.arange(H)[None, :]
        base = (ground[z, x] - ylo)[:, None]
        wall = own & ~outside & (yy > base) & (yy < top[:, None])  # above ground, below the roof edge
        az, ax = (abs(dx), abs(dz))  # along the face
        along = edges[(dx, dz)]
        padded = np.pad(along, 1)
        corner = ~(padded[1 + z + az, 1 + x + ax] & padded[1 + z - az, 1 + x - ax])
        i, j = np.nonzero(wall)
        out.append((z[i], x[i], j + ylo, np.full(len(i), dx), np.full(len(i), dz), lab[z[i], x[i]], corner[i]))
    if not out:
        return None
    names = ('z', 'x', 'y', 'dx', 'dz', 'building', 'corner')
    return {name: np.concatenate([part[k] for part in out]) for k, name in enumerate(names)}


def style(info, height, area):
    kind = info['tags'].get('building')
    if height > 30:
        return 'tall'
    if kind in HOUSES or (area < 350 and height <= 12):
        return 'house'
    return 'mid'


def plan(cells, shapes, ground, top, surface_key, grid_x0, grid_z0, palette, recorded=None):
    """Per-cell edits (x, y, z, state, role, building) and a per-building evidence record."""
    from material_router import choose_with_evidence
    base = np.vectorize(lambda key: key.split('[', 1)[0])(surface_key)
    road_distance = ndimage.distance_transform_edt(~np.isin(base, list(ROADS)))
    edits, buildings = [], {}
    for label in np.unique(cells['building']):
        info = shapes[label - 1][1]
        sel = cells['building'] == label
        z, x, y = cells['z'][sel], cells['x'][sel], cells['y'][sel]
        dx, dz, corner = cells['dx'][sel], cells['dz'][sel], cells['corner'][sel]
        g = int(np.median(ground[z, x]))
        height = float(np.median(top[z, x]) - g)
        if height < 2:
            continue
        kind = style(info, height, shapes[label - 1][0].area)
        try:
            levels = float(info['tags'].get('building:levels', ''))
        except ValueError:
            levels = 0
        record = (recorded or {}).get(label)
        floors_role = 'tagged' if 0 < levels <= 200 else 'recorded' if record and record[1] else 'estimated'
        levels = (levels if floors_role == 'tagged' else record[1] if floors_role == 'recorded'
                  else max(1, round(height / FLOOR_M)))
        floor_h = float(np.clip(height / levels, 2.6, 5.0))
        block, why = choose_with_evidence(info['tags'], 'facade', palette)
        if block:
            wall, wall_role = f'minecraft:{block}', 'tagged'
        elif record and record[0] in RECORDED:
            options = RECORDED[record[0]]
            wall, wall_role = options[stable(info['id'], len(options))], 'recorded'
            why = {'role': 'recorded', 'source': 'Cook County Assessor exterior wall', 'value': record[0]}
        else:
            wall, wall_role = STYLES[kind][stable(info['id'], len(STYLES[kind]))], 'estimated'
        offset = y - (ground[z, x] + 1)
        storey = np.floor(offset / floor_h)
        p = offset - storey * floor_h
        u = np.where(dz != 0, grid_x0 + x, grid_z0 + z)
        if kind == 'tall':
            window = (p >= 1) & (p < floor_h - 0.5)
        else:
            rows = 2 if floor_h >= 3.6 else 1
            bay = 3 if kind == 'house' else 2
            window = (p >= 1) & (p < 1 + rows) & ((u % bay) == 1)
        window &= ~corner
        door = np.zeros(len(z), bool)
        ground_floor = np.flatnonzero((offset <= 1) & ~corner)
        if len(ground_floor) and height < 60:  # towers: a lobby door is not worth guessing
            outside_z = np.clip(z[ground_floor] + dz[ground_floor], 0, ground.shape[0] - 1)
            outside_x = np.clip(x[ground_floor] + dx[ground_floor], 0, ground.shape[1] - 1)
            nearest = ground_floor[np.argmin(road_distance[outside_z, outside_x])]
            column = (z == z[nearest]) & (x == x[nearest]) & (dx == dx[nearest]) & (dz == dz[nearest])
            door = column & (offset <= 1)
        window &= ~door
        for k in range(len(z)):
            state = DOOR if door[k] else GLASS[kind] if window[k] else wall
            role = 'door' if door[k] else 'window' if window[k] else 'wall'
            edits.append((grid_x0 + int(x[k]), int(y[k]), grid_z0 + int(z[k]), state, role, info['id']))
        buildings[info['id']] = {'floors': int(levels), 'floors_role': floors_role, 'floor_height_m': round(floor_h, 2),
                                 'style': kind, 'wall': wall, 'wall_role': wall_role, 'wall_evidence': why,
                                 'height_m': round(height, 1), 'door': bool(door.any())}
    return edits, buildings


def recorded_walls(index, meta, lab):
    """{building label: (exterior wall, storeys)} from the assessor parcel points inside each footprint
    (the most common value when several cards fall in one building)."""
    from collections import Counter
    import cook_assessor_index
    votes = {}
    for x, y, wall, kind in cook_assessor_index.walls(index, meta):
        row, col = int(np.floor(meta['north'] - y)), int(np.floor(x - meta['west']))
        if 0 <= row < lab.shape[0] and 0 <= col < lab.shape[1] and lab[row, col] > 0:
            votes.setdefault(int(lab[row, col]), []).append((wall, TYPE_FLOORS.get(str(kind))))
    return {label: (Counter(w for w, _ in v if w).most_common(1)[0][0] if any(w for w, _ in v) else None,
                    Counter(f for _, f in v if f).most_common(1)[0][0] if any(f for _, f in v) else None)
            for label, v in votes.items()}


def paint(tile, frame, world, sources, work, assessor=None):
    """Facade detail into world/region in place. Returns the facade receipt."""
    from detail_apply import apply
    from material_router import texture_colors
    from regional_trees import tile_grid, tile_surface
    world, work, sources = Path(world), Path(work), Path(sources)
    meta = json.loads((sources / 'sources.json').read_text())
    shapes = footprints(sources, meta)
    if not shapes:
        return {'result': 'no mapped buildings', 'buildings': 0}
    grid = tile_grid(tile, frame)
    surface = tile_surface(world, grid, volume=True)
    vol, names, ylo = surface['vol'], surface['names'], surface['ylo']
    solid = np.array([n.split('[', 1)[0] not in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air')
                      for n in names])
    lab = labels(shapes, grid.size)
    cells = exposed(lab, surface['ground_y'], vol, solid, ylo)
    if cells is None:
        return {'result': 'no exposed walls', 'buildings': 0}
    current = np.asarray(names, dtype=object)[vol[cells['z'], cells['x'], cells['y'] - ylo]]
    del vol
    recorded = recorded_walls(assessor, meta, lab) if assessor is not None else {}
    edits, buildings = plan(cells, shapes, surface['ground_y'], surface['top_y'], surface['surface_key'],
                            grid.x0, grid.z0, texture_colors(), recorded)
    layer = LayerBuilder('facades', frame, 'Keyless facade detail: floors, windows, door, wall material')
    sources_by = {}

    def source(role, evidence):
        key = (role, evidence)
        if key not in sources_by:
            dataset, licence = ('Cook County Assessor residential characteristics', ASSESSOR_LICENSE) if evidence == 'recorded' \
                else ('OpenStreetMap, Overture footprints, generated geometry',
                      'ODbL 1.0 (c) OpenStreetMap contributors; Overture Maps Foundation')
            sources_by[key] = layer.source(f'facade_{role}', dataset, licence,
                                           'derived' if evidence in ('tagged', 'recorded') else 'estimated',
                                           basis=evidence)
        return sources_by[key]
    expect = {(int(grid.x0 + x), int(y), int(grid.z0 + z)): str(block)
              for x, y, z, block in zip(cells['x'], cells['y'], cells['z'], current)}
    counts = {'wall': 0, 'window': 0, 'door': 0}
    for x, y, z, state, role, bid in edits:
        was = expect[(x, y, z)]
        if role == 'wall' and (was.split('[', 1)[0] != GENERATED_WALL or state == was):
            continue  # a tagged or already-coloured wall keeps its block
        evidence = buildings[bid]['wall_role'] if role == 'wall' else 'estimated'
        layer.add(x, y, z, state, source(role, evidence), expect=was, priority={'door': 3, 'window': 2, 'wall': 1}[role])
        counts[role] += 1
    report = {'result': 'pass', 'buildings': len(buildings), 'edits': counts, 'facade_measured': False,
              'floors_tagged': sum(b['floors_role'] == 'tagged' for b in buildings.values()),
              'walls_tagged': sum(b['wall_role'] == 'tagged' for b in buildings.values()),
              'walls_recorded': sum(b['wall_role'] == 'recorded' for b in buildings.values()),
              'floors_recorded': sum(b['floors_role'] == 'recorded' for b in buildings.values()),
              'doors': sum(b['door'] for b in buildings.values()),
              'styles': {k: sum(b['style'] == k for b in buildings.values()) for k in STYLES},
              'license': 'ODbL 1.0 (c) OpenStreetMap contributors; Overture Maps Foundation' +
                         ('; ' + ASSESSOR_LICENSE if assessor is not None else '')}
    if not sum(counts.values()):
        return report
    guard = WriteGuard(work, CAP, FLOOR)
    layer.write(work / 'layer', guard, {'tile': tile['id']})
    manifest = json.loads((world / 'earthcraft.json').read_text())
    receipt = apply(world / 'region', [work / 'layer'], work / 'region', guard, height=manifest['dimension_height'])
    name = f'r.{grid.x0 >> 9}.{grid.z0 >> 9}'
    applied = receipt['regions'].get(name, {})
    if applied.get('verified'):
        import os
        os.replace(work / 'region' / f'{name}.mca', world / 'region' / f'{name}.mca')
    report.update(blocks_applied=applied.get('applied', 0), precondition_failed=applied.get('precondition_failed', 0),
                  region_before_sha256=applied.get('input_sha256'), region_after_sha256=applied.get('output_sha256'))
    return report


def record(world, report):
    path = Path(world) / 'earthcraft.json'
    manifest = json.loads(path.read_text())
    manifest['facades'] = report
    if report.get('buildings'):
        manifest.setdefault('limitations', []).append(
            'Facades: floors from OSM levels or measured height; window bays, doors and untagged wall materials '
            'are estimated styles, not observed from imagery.')
    path.write_text(json.dumps(manifest, indent=2))
