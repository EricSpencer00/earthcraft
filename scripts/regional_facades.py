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
# Assessor labels as the open data portal publishes them (counts in the 2024 index).
WALL_LABELS = {'masonry': 'masonry', 'frame': 'frame', 'frame + masonry': 'frame_masonry', 'stucco': 'stucco'}
TYPE_FLOORS = {'1 story': 1, '1.5 story': 1, '2 story': 2, '3 story +': 3, 'split level': 2}  # residence type -> storeys
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


def parts(sources, meta):
    """[(local geometry, tags)] for closed OSM building:part ways that tag a colour or material (Willis
    Tower's tubes are black glass; its outline says nothing). Largest first: rasterized in order, the
    most specific (smallest) part owns a cell when parts nest."""
    west, north = meta['west'], meta['north']
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    out = []
    for way in json.loads((Path(sources) / 'osm-ways.json').read_text()):
        tags = way['tags']
        if not way.get('closed') or len(way['coordinates']) < 4 or not tags.get('building:part'):
            continue
        if not any(k in tags for k in ('building:colour', 'building:material', 'building:facade:colour',
                                       'building:facade:material', 'facade:colour', 'facade:material')):
            continue
        x, y = project.transform(*zip(*way['coordinates']))
        geom = make_valid(Polygon(zip(np.asarray(x) - west, north - np.asarray(y))))
        if not geom.is_empty:
            out.append((geom, {'id': f"osm/{way['id']}", 'tags': tags}))
    return sorted(out, key=lambda item: -item[0].area)


def labels(shapes, size):
    if not shapes:
        return np.zeros((size, size), np.int32)
    return rasterize([(geom, index + 1) for index, (geom, _) in enumerate(shapes)], out_shape=(size, size),
                     transform=Affine.identity(), fill=0, dtype='int32')


def exposed(lab, ground, vol, solid, ylo):
    """Exposed wall cells: dict of arrays z, x, y, dx, dz, building (label), corner.

    A wall is a solid block of a building column, above ground and below that column's top (the roof
    edge), whose neighbour at the same height is open to the sky (above everything in that neighbour
    column): the footprint's outer faces, the faces of setbacks inside one footprint (Willis Tower's
    tubes), courtyards, and the part of a tall wall that rises above a touching lower neighbour. Party
    walls below a neighbour's roof, and the inside of a hollow shell under its roof, stay hidden. A face cell is a corner
    when either neighbour along the face is not also an exposed face in that direction."""
    Z, X, H = vol.shape
    z, x = np.nonzero(lab > 0)
    if not len(z):
        return None
    own = solid[vol[z, x, :]]
    top = np.where(own.any(axis=1), H - 1 - np.argmax(own[:, ::-1], axis=1), -1)
    yy = np.arange(H)[None, :]
    body = own & (yy > (ground[z, x] - ylo)[:, None]) & (yy < top[:, None])
    keep = body.any(axis=1)  # columns with wall above ground (bounds memory to the buildings)
    z, x, body = z[keep], x[keep], body[keep]
    index = np.full((Z, X), -1, np.int64)
    index[z, x] = np.arange(len(z))

    def face(dx, dz, zs, xs):
        """Exposed-face mask (len(zs) x H) for footprint columns zs, xs facing (dx, dz)."""
        nz, nx = zs + dz, xs + dx
        border = (nz < 0) | (nz >= Z) | (nx < 0) | (nx >= X)  # beyond the tile: treat as open
        outside = solid[vol[np.clip(nz, 0, Z - 1), np.clip(nx, 0, X - 1), :]]
        outside[border] = False
        roof = np.where(outside.any(axis=1), H - 1 - np.argmax(outside[:, ::-1], axis=1), -1)
        return body[index[zs, xs]] & (np.arange(H)[None, :] > roof[:, None])

    out = []
    for dx, dz in DIRS:
        wall = face(dx, dz, z, x)
        if not wall.any():
            continue
        az, ax = abs(dx), abs(dz)  # along the face
        sides = []
        for s in (1, -1):
            sz, sx = z + s * az, x + s * ax
            inside = (sz >= 0) & (sz < Z) & (sx >= 0) & (sx < X)
            k = np.where(inside, index[np.clip(sz, 0, Z - 1), np.clip(sx, 0, X - 1)], -1)
            side = np.zeros_like(wall)
            ok = k >= 0
            side[ok] = face(dx, dz, z[k[ok]], x[k[ok]])
            sides.append(side)
        corner = ~(sides[0] & sides[1])
        i, j = np.nonzero(wall)
        out.append((z[i], x[i], j + ylo, np.full(len(i), dx), np.full(len(i), dz), lab[z[i], x[i]], corner[i, j]))
    if not out:
        return None
    names = ('z', 'x', 'y', 'dx', 'dz', 'building', 'corner')
    return {name: np.concatenate([part[k] for part in out]) for k, name in enumerate(names)}


def ledges(lab, top_y, roof_plane):
    """Ledge tops on a face: top cells of a footprint column that are not on a roof plane and have a
    taller neighbour in the same footprint, e.g. the notches a lidar surface leaves on a tower face.
    They are painted as wall so a face reads continuous instead of streaked with roof colour."""
    Z, X = lab.shape
    taller = np.zeros((Z, X), bool)
    for dx, dz in DIRS:
        nz, nx = slice(max(dz, 0), Z + min(dz, 0)), slice(max(dx, 0), X + min(dx, 0))
        hz, hx = slice(max(-dz, 0), Z + min(-dz, 0)), slice(max(-dx, 0), X + min(-dx, 0))
        taller[hz, hx] |= (lab[nz, nx] == lab[hz, hx]) & (top_y[nz, nx] >= top_y[hz, hx] + 2)
    z, x = np.nonzero((lab > 0) & ~roof_plane & taller)
    return z, x


def style(info, height, area):
    kind = info['tags'].get('building')
    if height > 30:
        return 'tall'
    if kind in HOUSES or (area < 350 and height <= 12):
        return 'house'
    return 'mid'


def curtain(block):
    """(window, spandrel) for a glass wall: the glass itself, and the solid block of its colour between
    window rows, so a black glass tower reads black instead of banded with blue stock windows."""
    if 'stained_glass' in block:
        return block, block.replace('_stained_glass', '_concrete')
    return block, 'minecraft:light_gray_concrete'


def plan(cells, shapes, ground, top, surface_key, grid_x0, grid_z0, palette, recorded=None, part_shapes=()):
    """Per-cell edits (x, y, z, state, role, building) and a per-building evidence record. A colour or
    material tagged on the OSM building:part a cell lies in wins over the building's own wall."""
    from material_router import choose_with_evidence
    base = np.vectorize(lambda key: key.split('[', 1)[0])(surface_key)
    road_distance = ndimage.distance_transform_edt(~np.isin(base, list(ROADS)))
    edits, buildings = [], {}
    for label in np.unique(cells['building']):
        info = shapes[label - 1][1]
        sel = cells['building'] == label
        z, x, y = cells['z'][sel], cells['x'][sel], cells['y'][sel]
        dx, dz, corner = cells['dx'][sel], cells['dz'][sel], cells['corner'][sel]
        part = cells['part'][sel] if 'part' in cells else np.zeros(len(z), np.int32)
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
        walls = np.full(len(z), wall, dtype=object)
        parts_tagged = 0
        for pid in np.unique(part[part > 0]):
            block_p, _ = choose_with_evidence(part_shapes[pid - 1][1]['tags'], 'facade', palette)
            if block_p:
                walls[part == pid] = f'minecraft:{block_p}'
                parts_tagged += 1
        if parts_tagged:
            wall_role = 'tagged'
        windows = np.full(len(z), GLASS[kind], dtype=object)
        for block_w in set(walls):
            if 'glass' in block_w:  # curtain wall: windows of its own glass, solid spandrels of its colour
                pane, spandrel = curtain(block_w)
                windows[walls == block_w] = pane
                walls[walls == block_w] = spandrel
        for k in range(len(z)):
            state = DOOR if door[k] else windows[k] if window[k] else walls[k]
            role = 'door' if door[k] else 'window' if window[k] else 'wall'
            edits.append((grid_x0 + int(x[k]), int(y[k]), grid_z0 + int(z[k]), state, role, info['id']))
        buildings[info['id']] = {'floors': int(levels), 'floors_role': floors_role, 'floor_height_m': round(floor_h, 2),
                                 'style': kind, 'wall': wall, 'wall_role': wall_role, 'wall_evidence': why,
                                 'parts_tagged': parts_tagged, 'height_m': round(height, 1), 'door': bool(door.any())}
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
            votes.setdefault(int(lab[row, col]), []).append((WALL_LABELS.get(str(wall).strip().lower()),
                                                             TYPE_FLOORS.get(str(kind).strip().lower())))
    return {label: (Counter(w for w, _ in v if w).most_common(1)[0][0] if any(w for w, _ in v) else None,
                    Counter(f for _, f in v if f).most_common(1)[0][0] if any(f for _, f in v) else None)
            for label, v in votes.items()}


def paint(tile, frame, world, sources, work, assessor=None, surface=None, defer=False):
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
    surface = surface if surface is not None else tile_surface(world, grid, volume=True)
    vol, names, ylo = surface['vol'], surface['names'], surface['ylo']
    solid = np.array([n.split('[', 1)[0] not in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air')
                      for n in names])
    lab = labels(shapes, grid.size)
    cells = exposed(lab, surface['ground_y'], vol, solid, ylo)
    if cells is None:
        return {'result': 'no exposed walls', 'buildings': 0}
    from roof_color import roof_planes
    lz, lx = ledges(lab, surface['top_y'], roof_planes(lab, surface['top_y']))
    if len(lz):  # ledge tops on a face: painted as solid wall (corner: never a window or door)
        extra = {'z': lz, 'x': lx, 'y': surface['top_y'][lz, lx], 'dx': np.zeros(len(lz), int),
                 'dz': np.zeros(len(lz), int), 'building': lab[lz, lx], 'corner': np.ones(len(lz), bool)}
        cells = {k: np.concatenate([cells[k], extra[k].astype(cells[k].dtype)]) for k in cells}
    part_shapes = parts(sources, meta)
    cells['part'] = labels(part_shapes, grid.size)[cells['z'], cells['x']]
    current = np.asarray(names, dtype=object)[vol[cells['z'], cells['x'], cells['y'] - ylo]]
    recorded = recorded_walls(assessor, meta, lab) if assessor is not None else {}
    edits, buildings = plan(cells, shapes, surface['ground_y'], surface['top_y'], surface['surface_key'],
                            grid.x0, grid.z0, texture_colors(), recorded, part_shapes)
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
    if defer:  # applied with the other passes' layers by the caller
        report.update(_layer=str(work / 'layer'))
        return report
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
