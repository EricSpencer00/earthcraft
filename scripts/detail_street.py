"""Street detail on the canonical ground: carriageway, curbs, sidewalks, paint, furniture.

Measured where possible:
* curb offsets: the LiDAR ground profile perpendicular to each OSM road shows
  the road crown falling toward the gutter and a ~0.15 m rise at the curb;
  the median offset per road side becomes the carriageway edge;
* paint: retro-reflective markings return high LiDAR intensity relative to the
  surrounding asphalt (top-hat on the ground intensity raster);
* crosswalks: OSM ``footway=crossing`` ways (with ``crossing:markings``),
  confirmed against intensity contrast.
Mapped (OSM/municipal points): street lamps, signals, bus stops, bike racks,
bike share docks, hydrants, post boxes, benches, waste baskets, bollards.
Block shapes for furniture are representative (estimated), never observed.
"""
import math

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

DRIVE = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential',
         'living_street', 'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'}
LANE_M = 3.2


def drive_ways(osm):
    ways = []
    for f in osm:
        t = f['tags']
        if f['type'] != 'way' or not isinstance(f['geom'], LineString):
            continue
        if t.get('highway') not in DRIVE or t.get('tunnel') in ('yes', 'building_passage') or t.get('covered') == 'yes':
            continue
        if t.get('layer', '0').lstrip('-').isdigit() and int(t.get('layer', '0')) < 0:
            continue
        ways.append(f)
    return ways


def fallback_half_width(tags):
    """Mapped-lane estimate when no curb is measurable (labelled estimated)."""
    if 'width' in tags:
        try:
            return float(tags['width'].split()[0]) / 2, 'osm_width_tag'
        except ValueError:
            pass
    lanes = tags.get('lanes')
    lanes = int(lanes) if lanes and lanes.isdigit() else (1 if tags.get('oneway') == 'yes' else 2)
    parking = 0 if tags.get('highway') in ('primary', 'secondary', 'trunk') else 2.4
    if tags.get('oneway') == 'yes':
        return (lanes * LANE_M + parking) / 2, 'osm_lanes_estimate'
    return (lanes * LANE_M) / 2 + parking / 2, 'osm_lanes_estimate'


def sample_dtm(dtm, tile, cell, x, z):
    c = np.floor((np.asarray(x) - tile.x0) / cell).astype(int)
    r = np.floor((np.asarray(z) - tile.z0) / cell).astype(int)
    n = dtm.shape[0]
    inside = (c >= 0) & (c < n) & (r >= 0) & (r < n)
    out = np.full(np.shape(x), np.nan)
    out[inside] = dtm[r[inside], c[inside]]
    return out


def curb_offsets(line, dtm, tile, cell, stop_mask, step=2.0, max_offset=18.0, rise=0.08):
    """Median measured curb offset (m) left (+1) and right (-1) of a way, plus detection rates."""
    length = line.length
    if length < 4:
        return {}
    distances = np.arange(1.0, length - 1.0, step)
    result = {}
    ts = np.arange(0.5, max_offset, 0.5)
    for side in (1, -1):
        found, tried = [], 0
        for d in distances:
            p = line.interpolate(d)
            a, b = line.interpolate(max(0, d - 1)), line.interpolate(min(length, d + 1))
            dx, dz = b.x - a.x, b.y - a.y
            norm = math.hypot(dx, dz)
            if norm == 0:
                continue
            # Left normal in a y-down (z south) frame, matching shapely's single-sided buffer sign.
            nx, nz = -dz / norm * side, dx / norm * side
            xs, zs = p.x + nx * ts, p.y + nz * ts
            h = sample_dtm(dtm, tile, cell, xs, zs)
            blocked = sample_dtm(stop_mask.astype(float), tile, cell, xs, zs) > 0
            tried += 1
            limit = np.argmax(blocked) if blocked.any() else len(ts)
            for i in range(3, min(limit, len(ts)) - 3):
                here = h[i]
                if np.isnan(here) or np.isnan(h[i + 2]) or np.isnan(h[i + 3]):
                    continue
                before = np.nanmin(h[max(0, i - 2):i + 1])
                if h[i + 2] - before >= rise and h[i + 3] - before >= rise:
                    found.append(ts[i] + 0.5)
                    break
        if tried:
            result[side] = {'offset': float(np.median(found)) if len(found) >= max(3, 0.25 * tried) else None,
                            'detections': len(found), 'samples': tried,
                            'iqr': float(np.subtract(*np.percentile(found, [75, 25]))) if len(found) >= 3 else None}
    return result


def carriageway(ways, lr_half, tile, stop_mask_half):
    """Polygon union of measured (or estimated) carriageways and a per-way receipt."""
    polys, receipt = [], []
    for f in ways:
        line = f['geom']
        offsets = curb_offsets(line, lr_half['dtm'], tile, lr_half['cell'], stop_mask_half)
        fallback, how = fallback_half_width(f['tags'])
        sides = {}
        for side in (1, -1):
            measured = offsets.get(side, {}).get('offset')
            # Reject implausible measurements (beyond a median, or narrower than one lane).
            if measured is not None and (measured < 2.5 or measured > max(2.2 * fallback, fallback + 6)):
                measured = None
            sides[side] = (measured, 'lidar_curb_profile') if measured is not None else (fallback, how)
            polys.append(line.buffer(sides[side][0] * side, single_sided=True, cap_style='flat'))
        receipt.append({'osm': f['id'], 'name': f['tags'].get('name'), 'highway': f['tags'].get('highway'),
                        'left_m': round(sides[1][0], 2), 'left_source': sides[1][1],
                        'right_m': round(sides[-1][0], 2), 'right_source': sides[-1][1],
                        'profile': {str(k): v for k, v in offsets.items()}})
    return unary_union(polys) if polys else None, receipt


def intensity_paint(lr_half, road_half, threshold=9000, absolute=30000):
    """Paint candidates: intensity well above the local asphalt median, on the carriageway only."""
    inten = lr_half['intensity']
    valid = ~np.isnan(inten) & road_half
    filled = np.where(valid, inten, np.nan)
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    base = ndimage.median_filter(np.where(valid, filled, filled[tuple(idx)]), size=11)
    paint = valid & (inten - base > threshold) & (inten > absolute)
    return paint, base


def paint_blocks(mask_half, min_component=2):
    """0.5 m paint hits -> 1 m blocks; isolated single-block hits are treated as noise."""
    n = mask_half.shape[0] // 2
    blocks = mask_half[:2 * n, :2 * n].reshape(n, 2, n, 2).any(axis=(1, 3))
    labels, count = ndimage.label(blocks, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    keep = sizes >= min_component
    keep[0] = False
    return keep[labels]


def to_blocks(mask_half):
    """0.5 m mask -> 1 m mask (a block is painted if >= 2 of its 4 sub-cells are)."""
    n = mask_half.shape[0] // 2
    return mask_half[:2 * n, :2 * n].reshape(n, 2, n, 2).sum(axis=(1, 3)) >= 2


# ---------------------------------------------------------------------------
# Surface classes on the 1 m block grid
# ---------------------------------------------------------------------------
PAVED = {'minecraft:gray_concrete', 'minecraft:light_gray_concrete', 'minecraft:white_concrete',
         'minecraft:black_concrete', 'minecraft:yellow_concrete', 'minecraft:brown_concrete',
         'minecraft:pink_concrete', 'minecraft:stone'}
# The base world routed orthophoto green onto some ground cells as green/lime concrete;
# off the carriageway these are planters/lawns and render better as grass.
ORTHO_GREEN = {'minecraft:green_concrete', 'minecraft:lime_concrete'}
SIDEWALK = 'minecraft:smooth_stone'
CURB = 'minecraft:polished_andesite'
PAINT_WHITE = 'minecraft:white_concrete'
PAINT_YELLOW = 'minecraft:yellow_concrete'
GRASS = 'minecraft:grass_block[snowy=false]'


def surface_classes(tile, world, road_mask, footprint_mask, osm, max_sidewalk_m=14, protect=None):
    """Block-grid masks: carriageway, sidewalk, curb, service/parking (left as asphalt)."""
    base = np.vectorize(lambda k: k.split('[', 1)[0])(world['surface_key'])
    paved = np.isin(base, list(PAVED))
    built = world['built'] | footprint_mask | (protect if protect is not None else False)
    service, parking, plaza, green = [], [], [], []
    for f in osm:
        t, g = f['tags'], f['geom']
        if f['type'] == 'way' and t.get('highway') == 'service' and isinstance(g, LineString):
            if t.get('tunnel') != 'yes' and t.get('covered') != 'yes':
                service.append(g.buffer(2.6 if t.get('service') in ('alley', 'driveway') else 3.2, cap_style='flat'))
        if t.get('amenity') == 'parking' and g.geom_type in ('Polygon', 'MultiPolygon') and t.get('parking') not in ('underground', 'multi-storey'):
            parking.append(g)
        if (t.get('highway') == 'pedestrian' or t.get('place') == 'square') and g.geom_type in ('Polygon', 'MultiPolygon'):
            plaza.append(g)
        if (t.get('landuse') in ('grass', 'flowerbed') or t.get('leisure') in ('garden',) or t.get('natural') in ('scrub', 'grassland')) and g.geom_type in ('Polygon', 'MultiPolygon'):
            green.append(g)
    service_mask = tile.raster([(g, 1) for g in service]).astype(bool) & ~road_mask
    parking_mask = tile.raster([(g, 1) for g in parking]).astype(bool)
    plaza_mask = tile.raster([(g, 1) for g in plaza]).astype(bool)
    green_mask = tile.raster([(g, 1) for g in green]).astype(bool)
    distance = ndimage.distance_transform_edt(~road_mask)
    sidewalk = (paved & ~road_mask & ~built & ~service_mask & ~parking_mask & ~green_mask &
                ((distance <= max_sidewalk_m) | plaza_mask))
    curb = sidewalk & ndimage.binary_dilation(road_mask, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    ortho_green = np.isin(base, list(ORTHO_GREEN)) & ~road_mask & ~built
    return {'paved': paved, 'sidewalk': sidewalk, 'curb': curb, 'service': service_mask, 'parking': parking_mask,
            'green': (green_mask & ~built & ~road_mask) | ortho_green, 'ortho_green': ortho_green,
            'plaza': plaza_mask, 'built': built, 'road': road_mask}


def crosswalk_cells(osm, road_mask, tile, lr_half=None, paint_half=None, band=1.5):
    """Zebra/ladder bars (alternate 1 m bars along the walking direction) inside the carriageway."""
    out = []
    for f in osm:
        t, g = f['tags'], f['geom']
        if f['type'] != 'way' or t.get('footway') != 'crossing' or not isinstance(g, LineString):
            continue
        markings = t.get('crossing:markings', '')
        if t.get('crossing') == 'unmarked' or markings in ('no', 'none'):
            continue
        style = 'lines' if markings in ('lines', 'dashes', 'lines:paired') else 'zebra'
        evidence = 'osm_markings_tag' if markings else 'osm_crossing_without_markings_tag'
        x0, z0, x1, z1 = g.buffer(band + 1).bounds
        cells = []
        for z in range(int(math.floor(z0)), int(math.ceil(z1))):
            for x in range(int(math.floor(x0)), int(math.ceil(x1))):
                r, c = z - tile.z0, x - tile.x0
                if not (0 <= r < tile.size and 0 <= c < tile.size) or not road_mask[r, c]:
                    continue
                p = Point(x + 0.5, z + 0.5)
                d = g.distance(p)
                if d > band:
                    continue
                along = g.project(p)
                if style == 'zebra' and int(math.floor(along)) % 2 == 0:
                    cells.append((x, z))
                elif style == 'lines' and d > band - 1:
                    cells.append((x, z))
        if not cells:
            continue
        support = None
        if paint_half is not None:
            hits = [paint_half[2 * (z - tile.z0):2 * (z - tile.z0) + 2, 2 * (x - tile.x0):2 * (x - tile.x0) + 2].any()
                    for x, z in cells]
            support = float(np.mean(hits))
        out.append({'osm': f['id'], 'style': style, 'tag': markings or None, 'evidence': evidence,
                    'cells': cells, 'lidar_paint_support': support})
    return out


def lane_lines(ways, road_mask, paint_blocks, tile, crosswalk_mask, min_support=6):
    """Lane lines at the offsets where LiDAR paint concentrates (observed), else from lanes (estimated)."""
    lines = []
    for f in ways:
        t, g = f['tags'], f['geom']
        if g.length < 20:
            continue
        twoway = t.get('oneway') not in ('yes', '-1')
        x0, z0, x1, z1 = g.buffer(12).bounds
        offsets = []
        for z in range(int(z0), int(z1) + 1):
            for x in range(int(x0), int(x1) + 1):
                r, c = z - tile.z0, x - tile.x0
                if not (0 <= r < tile.size and 0 <= c < tile.size) or not paint_blocks[r, c] or crosswalk_mask[r, c]:
                    continue
                p = Point(x + 0.5, z + 0.5)
                along = g.project(p)
                if along < 8 or along > g.length - 8:
                    continue
                q = g.interpolate(along)
                a, b = g.interpolate(max(0, along - 1)), g.interpolate(min(g.length, along + 1))
                side = np.sign((b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x))
                offsets.append(side * q.distance(p))
        peaks = []
        if offsets:
            hist, edges = np.histogram(offsets, bins=np.arange(-12, 12.5, 1.0))
            for i in np.argsort(-hist):
                if hist[i] < min_support:
                    break
                centre = (edges[i] + edges[i + 1]) / 2
                if all(abs(centre - p) > 2.0 for p, _ in peaks):
                    peaks.append((centre, int(hist[i])))
        lanes = t.get('lanes')
        lanes = int(lanes) if lanes and lanes.isdigit() else None
        if peaks:
            for centre, support in peaks:
                yellow = twoway and abs(centre) < 1.6
                lines.append({'osm': f['id'], 'offset': centre, 'colour': 'yellow' if yellow else 'white',
                              'pattern': 'solid' if yellow else 'dashed', 'evidence': 'observed',
                              'support_cells': support})
        elif lanes and lanes >= 2:
            if twoway:
                lines.append({'osm': f['id'], 'offset': 0.0, 'colour': 'yellow', 'pattern': 'solid', 'evidence': 'estimated'})
            per_side = lanes // 2 if twoway else lanes
            for k in range(1, per_side):
                for side in ((1, -1) if twoway else (0,)):
                    offset = side * k * LANE_M if twoway else (k - lanes / 2) * LANE_M
                    lines.append({'osm': f['id'], 'offset': offset, 'colour': 'white', 'pattern': 'dashed',
                                  'evidence': 'estimated'})
        for line in lines:
            if line['osm'] == f['id'] and 'geom' not in line:
                line['geom'] = g.offset_curve(line['offset']) if line['offset'] else g
    cells = {}
    for i, line in enumerate(lines):
        geom = line['geom']
        if geom.is_empty:
            continue
        parts = list(geom.geoms) if hasattr(geom, 'geoms') else [geom]
        for part in parts:
            n = int(part.length)
            for s in range(n):
                if line['pattern'] == 'dashed' and (s % 12) >= 3:
                    continue
                p = part.interpolate(s + 0.5)
                x, z = int(math.floor(p.x)), int(math.floor(p.y))
                r, c = z - tile.z0, x - tile.x0
                if 0 <= r < tile.size and 0 <= c < tile.size and road_mask[r, c] and not crosswalk_mask[r, c]:
                    cells.setdefault((x, z), i)
    return lines, cells


# ---------------------------------------------------------------------------
# Street furniture: mapped positions, representative block shapes
# ---------------------------------------------------------------------------
POLE = 'minecraft:polished_blackstone_wall[east=none,north=none,south=none,up=true,waterlogged=false,west=none]'
SIGNAL_POLE = 'minecraft:andesite_wall[east=none,north=none,south=none,up=true,waterlogged=false,west=none]'
DIRS = {(1, 0): 'east', (-1, 0): 'west', (0, 1): 'south', (0, -1): 'north'}
OPPOSITE = {'east': 'west', 'west': 'east', 'south': 'north', 'north': 'south'}


def wall_state(name, connect=()):
    sides = {d: ('low' if d in connect else 'none') for d in ('east', 'north', 'south', 'west')}
    return f'minecraft:{name}[' + ','.join(f'{k}={sides[k]}' for k in sorted(sides)) + ',up=true,waterlogged=false]'


def snap_to_sidewalk(x, z, tile, classes, max_shift=4):
    """Nearest sidewalk/plaza cell to a mapped point (mapped nodes often sit on the curb line)."""
    ok = classes['sidewalk'] & ~classes['curb']
    cx, cz = int(math.floor(x)), int(math.floor(z))
    best = None
    for dz in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            r, c = cz + dz - tile.z0, cx + dx - tile.x0
            if 0 <= r < tile.size and 0 <= c < tile.size and ok[r, c]:
                d = math.hypot(cx + dx + 0.5 - x, cz + dz + 0.5 - z)
                if d <= max_shift and (best is None or d < best[0]):
                    best = (d, cx + dx, cz + dz)
    return best


def toward_road(x, z, tile, classes, reach=6):
    """Unit grid direction from a sidewalk cell toward the nearest carriageway cell."""
    best = None
    for (dx, dz), _ in DIRS.items():
        for step in range(1, reach + 1):
            r, c = z + dz * step - tile.z0, x + dx * step - tile.x0
            if not (0 <= r < tile.size and 0 <= c < tile.size):
                break
            if classes['road'][r, c]:
                if best is None or step < best[0]:
                    best = (step, (dx, dz))
                break
            if classes['built'][r, c]:
                break
    return best[1] if best else (0, 1)


def furniture_blocks(kind, x, z, gy, direction=(0, 1), tags=None):
    """Representative block stack for one mapped item; returns [(x, y, z, state)]."""
    tags = tags or {}
    out = []
    dx, dz = direction
    face = DIRS.get((dx, dz), 'south')
    if kind == 'street_lamp':
        for y in range(gy + 1, gy + 7):
            out.append((x, y, z, POLE))
        out.append((x, gy + 7, z, wall_state('polished_blackstone_wall', (face,))))
        out.append((x + dx, gy + 7, z + dz, wall_state('polished_blackstone_wall', (OPPOSITE[face],))))
        out.append((x + dx, gy + 6, z + dz, 'minecraft:lantern[hanging=true,waterlogged=false]'))
    elif kind == 'traffic_signal':
        for y in range(gy + 1, gy + 5):
            out.append((x, y, z, SIGNAL_POLE))
        # One ~1 m signal head on the pole top, plus a pedestrian head facing the crossing.
        out.append((x, gy + 5, z, 'minecraft:black_concrete'))
        out.append((x, gy + 6, z, wall_state('andesite_wall')))
    elif kind == 'bus_stop':
        out.append((x, gy + 1, z, 'minecraft:iron_bars[east=false,north=false,south=false,waterlogged=false,west=false]'))
        out.append((x, gy + 2, z, 'minecraft:iron_bars[east=false,north=false,south=false,waterlogged=false,west=false]'))
        out.append((x, gy + 3, z, 'minecraft:blue_terracotta'))
        if tags.get('shelter') == 'yes':
            # 3 m shelter parallel to the curb, set back one block: glass back wall + slab roof.
            px, pz = -dz, dx
            for k in (-1, 0, 1):
                bx, bz = x - dx + px * k, z - dz + pz * k
                for y in (gy + 1, gy + 2):
                    out.append((bx - dx, y, bz - dz, 'minecraft:glass'))
                for back in (0, 1):
                    out.append((bx - dx * back, gy + 3, bz - dz * back, 'minecraft:smooth_stone_slab[type=bottom,waterlogged=false]'))
            if tags.get('bench') == 'yes':
                out.append((x - dx, gy + 1, z - dz, 'minecraft:dark_oak_slab[type=bottom,waterlogged=false]'))
    elif kind == 'bike_rack':
        out.append((x, gy + 1, z, 'minecraft:iron_bars[east=false,north=false,south=false,waterlogged=false,west=false]'))
    elif kind == 'bike_share':
        out.append((x, gy + 1, z, 'minecraft:light_blue_concrete'))
        out.append((x, gy + 2, z, 'minecraft:light_blue_concrete'))
        px, pz = -dz, dx
        docks = max(4, min(16, int(tags.get('total_docks', 10)) // 2))
        for k in range(1, docks + 1):
            out.append((x + px * k, gy + 1, z + pz * k, 'minecraft:light_blue_stained_glass_pane[east=false,north=false,south=false,waterlogged=false,west=false]'))
    elif kind == 'fire_hydrant':
        out.append((x, gy + 1, z, wall_state('red_nether_brick_wall')))
    elif kind == 'post_box':
        out.append((x, gy + 1, z, 'minecraft:blue_terracotta'))
    elif kind == 'waste_basket':
        out.append((x, gy + 1, z, 'minecraft:cauldron'))
    elif kind == 'bollard':
        out.append((x, gy + 1, z, wall_state('andesite_wall')))
    elif kind == 'bench':
        out.append((x, gy + 1, z, 'minecraft:dark_oak_slab[type=bottom,waterlogged=false]'))
    return out


def signal_corners(signal_points, classes, tile, radius=22):
    """One pole per intersection quadrant at the curb cell nearest the signalised node cluster."""
    clusters = []
    for x, z, ref in signal_points:
        for cl in clusters:
            if math.hypot(cl['x'] - x, cl['z'] - z) < 25:
                cl['members'].append(ref)
                n = len(cl['members'])
                cl['x'] += (x - cl['x']) / n; cl['z'] += (z - cl['z']) / n
                break
        else:
            clusters.append({'x': x, 'z': z, 'members': [ref]})
    curb = np.argwhere(classes['curb'])
    poles = []
    for cl in clusters:
        zs = curb[:, 0] + tile.z0 + 0.5; xs = curb[:, 1] + tile.x0 + 0.5
        d = np.hypot(xs - cl['x'], zs - cl['z'])
        for qx in (-1, 1):
            for qz in (-1, 1):
                sel = (d < radius) & (np.sign(xs - cl['x']) == qx) & (np.sign(zs - cl['z']) == qz)
                if sel.any():
                    i = np.flatnonzero(sel)[np.argmin(d[sel])]
                    poles.append((int(curb[i, 1] + tile.x0), int(curb[i, 0] + tile.z0), cl['members']))
    return poles
