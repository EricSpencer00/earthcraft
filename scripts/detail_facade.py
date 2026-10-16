"""Facade prototype: material colour + window grid painted onto the EXISTING shell.

Only blocks that already exist and face exterior air are recoloured; no wall,
window or opening is added or removed (appearance-only, as CV_BUILDING_POLISH
requires). Evidence per building is explicit:

* ``observed`` colour/material: a street photo registered to the wall
  (``observe_from_image``; Panoramax/Mapillary) - none was available here;
* ``tagged`` colour/material: OSM ``building:colour`` / ``building:material``
  on the building or part (a mapper's observation, not ours);
* ``estimated`` window grid: rows from levels/stories and height, bays from a
  fixed module; positions are not observed;
* ``unknown`` material: the existing block is kept and only windows are drawn.
"""
import colorsys
import math

import numpy as np

# Mean texel colours (sRGB 0..255) of candidate wall/glass blocks, measured from the 1.21.10 client jar.
WALLS = {
    'minecraft:white_concrete': (207, 213, 214), 'minecraft:light_gray_concrete': (125, 125, 115),
    'minecraft:gray_concrete': (55, 58, 62), 'minecraft:black_concrete': (8, 10, 15),
    'minecraft:brown_concrete': (96, 60, 32), 'minecraft:white_terracotta': (210, 178, 161),
    'minecraft:light_gray_terracotta': (135, 107, 98), 'minecraft:gray_terracotta': (58, 42, 36),
    'minecraft:brown_terracotta': (77, 51, 36), 'minecraft:terracotta': (152, 94, 68),
    'minecraft:smooth_stone': (159, 159, 159), 'minecraft:stone': (126, 126, 126),
    'minecraft:andesite': (136, 136, 137), 'minecraft:polished_andesite': (132, 135, 134),
    'minecraft:calcite': (223, 224, 221), 'minecraft:smooth_sandstone': (224, 214, 170),
    'minecraft:bricks': (151, 98, 83), 'minecraft:mud_bricks': (137, 104, 79),
    'minecraft:polished_blackstone': (53, 49, 57), 'minecraft:polished_deepslate': (72, 72, 73),
    'minecraft:polished_tuff': (98, 104, 100), 'minecraft:quartz_block': (236, 230, 223),
    'minecraft:stone_bricks': (122, 122, 122), 'minecraft:granite': (149, 103, 86),
    'minecraft:polished_granite': (154, 107, 89),
}
GLASS = {
    'minecraft:black_stained_glass': (25, 25, 25), 'minecraft:gray_stained_glass': (76, 76, 76),
    'minecraft:light_gray_stained_glass': (153, 153, 153), 'minecraft:brown_stained_glass': (102, 76, 51),
    'minecraft:light_blue_stained_glass': (102, 153, 216), 'minecraft:cyan_stained_glass': (76, 127, 153),
}
NAMED = {'black': (20, 20, 20), 'white': (235, 235, 235), 'grey': (128, 128, 128), 'gray': (128, 128, 128),
         'brown': (120, 80, 50), 'beige': (220, 205, 170), 'red': (160, 60, 50), 'tan': (200, 170, 130),
         'silver': (190, 190, 195), 'darkgray': (70, 70, 70), 'lightgray': (200, 200, 200)}


def parse_colour(value):
    if not value:
        return None
    value = value.strip().lower().replace('grey', 'gray').replace(' ', '')
    if value.startswith('#') and len(value) in (4, 7):
        h = value[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    return NAMED.get(value)


def _lab(rgb):
    c = np.asarray(rgb, float) / 255
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def nearest_block(rgb, table):
    names = list(table)
    d = np.linalg.norm(_lab([table[k] for k in names]) - _lab(rgb), axis=1)
    i = int(np.argmin(d))
    return names[i], float(d[i])


def glass_for(rgb):
    """Window glass that reads darker than the wall, as occupied interiors do from the street.

    Shells are hollow, so light glass would show the pale back wall through the window;
    dark glass keeps the window/wall contrast. Very dark walls get grey (reflective) glass.
    """
    h, l, s = colorsys.rgb_to_hls(*(np.asarray(rgb) / 255))
    if l < 0.25:
        return 'minecraft:gray_stained_glass'
    if s > 0.2 and 0.02 < h < 0.15:
        return 'minecraft:brown_stained_glass'
    return 'minecraft:black_stained_glass'


def window_period(profile, min_period=2, max_period=12):
    """Dominant repeat (in samples) of a 1-D facade intensity profile by autocorrelation."""
    x = np.asarray(profile, float)
    x = x - x.mean()
    if len(x) < 2 * min_period or not np.any(x):
        return None, 0.0
    ac = np.correlate(x, x, mode='full')[len(x) - 1:]
    ac = ac / ac[0]
    hi = min(max_period, len(x) // 2)
    if hi <= min_period:
        return None, 0.0
    lag = int(np.argmax(ac[min_period:hi + 1])) + min_period
    return lag, float(ac[lag])


def observe_from_image(pixels_rgb, mask=None, trim=0.2):
    """Robust facade colour and window rhythm from a RECTIFIED wall crop (H x W x 3).

    The crop must already be registered to the wall (see street_photo_projection):
    this function never decides which wall a photo shows. Returns the trimmed
    median colour of the brightest-but-not-glare wall pixels, plus horizontal
    and vertical periods of dark (window) structure, with their autocorrelation.
    """
    img = np.asarray(pixels_rgb, float)
    if img.ndim != 3 or img.shape[2] != 3:
        raise ValueError('RGB crop required')
    valid = np.ones(img.shape[:2], bool) if mask is None else np.asarray(mask, bool)
    lum = img.mean(axis=2)
    vals = lum[valid]
    if vals.size < 50:
        return None
    lo, hi = np.quantile(vals, [0.5, 1 - trim / 2])
    wall = valid & (lum >= lo) & (lum <= hi)
    colour = np.median(img[wall], axis=0)
    dark = (lum < np.quantile(vals, 0.35)) & valid
    col_period, col_score = window_period(dark.mean(axis=0))
    row_period, row_score = window_period(dark.mean(axis=1))
    return {'colour_rgb': [int(round(c)) for c in colour], 'window_fraction': float(dark[valid].mean()),
            'column_period_px': col_period, 'column_score': col_score,
            'row_period_px': row_period, 'row_score': row_score, 'pixels': int(valid.sum())}


def exterior_faces(vol, solid, footprint, ground_y, ylo, y_min, y_max):
    """Existing solid cells inside a footprint that touch air outside it horizontally.

    ``vol``/``solid`` are [z, x, y] over the context tile; ``footprint`` should be
    rasterised with ``all_touched`` so walls on the outline count as inside.
    Returns tuples (r, c, y, dr, dc): cell and the outward normal of one exposed face.
    """
    rows, cols = np.nonzero(footprint)
    out = []
    for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        rr, cc = rows + dr, cols + dc
        ok = (rr >= 0) & (rr < footprint.shape[0]) & (cc >= 0) & (cc < footprint.shape[1])
        r0, c0, r1, c1 = rows[ok], cols[ok], rr[ok], cc[ok]
        edge = ~footprint[r1, c1]
        r0, c0, r1, c1 = r0[edge], c0[edge], r1[edge], c1[edge]
        for y in range(y_min, y_max + 1):
            iy = y - ylo
            if iy < 0 or iy >= vol.shape[2]:
                continue
            hit = solid[r0, c0, iy] & ~solid[r1, c1, iy] & (y > ground_y[r0, c0])
            out.extend((a, b, y, dr, dc) for a, b in zip(r0[hit], c0[hit]))
    return out


def facade_layer(ctx, frame, targets, args):
    from detail_layer import LayerBuilder
    tile, world = ctx['tile'], ctx['world']
    vol, names, ylo = world['vol'], world['names'], world['ylo']
    air = np.array([n.split('[')[0] in ('minecraft:air', 'minecraft:cave_air') for n in names])
    solid = ~air[vol]
    layer = LayerBuilder('facades', frame, 'Prototype facade colour + window grid on existing shells')
    osm = {f['id']: f for f in ctx['osm']}
    chicago = {str(f['properties'].get('bldg_id')): f for f in ctx['chicago_buildings']}
    report = {}
    claimed = np.zeros(vol.shape[:2], bool)
    for target in targets:
        parts = []
        for ref in target['parts']:
            f = osm.get(ref)
            if f is None:
                raise ValueError(f'{ref} not in the OSM extract')
            parts.append(f)
        stories = None
        if target.get('chicago_bldg_id') and target['chicago_bldg_id'] in chicago:
            props = chicago[target['chicago_bldg_id']]['properties']
            stories = int(props['stories']) if str(props.get('stories', '')).isdigit() and int(props['stories']) > 0 else None
        stats = {'parts': len(parts), 'cells': 0, 'windows': 0, 'wall': 0, 'evidence': {}}
        # Taller parts first so a podium never repaints its tower.
        parts.sort(key=lambda f: -float(f['tags'].get('height', 0) or 0))
        known_levels = [(float(f['tags']['height']), int(f['tags']['building:levels'])) for f in parts
                        if f['tags'].get('height') and f['tags'].get('building:levels', '').isdigit()]
        for f in parts:
            t = f['tags']
            mask = tile.raster([(f['geom'], 1)], all_touched=True).astype(bool) & ~claimed
            if not mask.any():
                continue
            gy = int(np.median(world['ground_y'][mask]))
            height = float(t['height']) if t.get('height') else None
            levels = int(t['building:levels']) if t.get('building:levels', '').isdigit() else None
            if levels is None and height:
                same = [lv for h, lv in known_levels if abs(h - height) <= 3]
                levels = same[0] if same else None
            levels = levels or stories
            top = gy + int(round(height)) if height else int(world['top_y'][mask].max())
            bottom = gy + 1
            if t.get('min_height'):
                bottom = gy + int(round(float(t['min_height'])))
            elif height and levels and height / levels > 8:
                # A short top part (e.g. a 2-level penthouse tagged with the full height).
                bottom = top - int(round(levels * 3.5))
            colour = parse_colour(t.get('building:colour') or target.get('colour_tag'))
            material = t.get('building:material')
            if colour is not None:
                wall, delta = nearest_block(colour, WALLS)
                wall_evidence = 'tagged'
                glass = glass_for(colour)
            else:
                wall, delta, wall_evidence, glass = None, None, 'unknown', 'minecraft:black_stained_glass'
            curtain = material == 'glass'
            fh = (top - gy) / levels if levels else 3.6
            fh = min(max(fh, 2.8), 6.0)
            bay = 3 if curtain else 2
            src_wall = layer.source('facade_material', 'OSM building:colour/material', 'ODbL 1.0 (c) OpenStreetMap contributors',
                                    'observed' if wall_evidence == 'tagged' else 'estimated', feature=f['id'],
                                    building=target['name'], colour_tag=t.get('building:colour'), material_tag=material,
                                    block=wall, lab_distance=None if delta is None else round(delta, 1),
                                    note='mapper-tagged colour mapped to nearest block (CIELAB); no registered street image available')
            src_win = layer.source('facade_windows', 'Floor count (OSM levels / Chicago stories) + LiDAR/OSM height', 'ODbL 1.0; City of Chicago Terms of Use',
                                   'estimated', feature=f['id'], building=target['name'], levels=levels,
                                   floor_height_m=round(fh, 2), bay_m=bay, glass=glass,
                                   note='window rows from floor count; columns from a fixed bay module, not observed')
            if bottom > gy + 1:
                fh = 3.5
            faces = exterior_faces(vol, solid, mask, world['ground_y'], ylo, bottom, min(top, ylo + vol.shape[2] - 1))
            seen = set()
            for r, c, y, dr, dc in faces:
                if (r, c, y) in seen:
                    continue
                seen.add((r, c, y))
                x, z = tile.x0 + c, tile.z0 + r
                current = names[vol[r, c, y - ylo]]
                along = z if dc != 0 else x
                level = (y - gy - 1) / fh
                within = (level - math.floor(level)) * fh
                is_window = (0.9 <= within < 0.9 + (0.66 if curtain else 0.5) * fh + 0.01) and (along % bay != 0) and y < top - 1
                if y <= gy + 1 and target.get('storefront'):
                    is_window = along % bay != 0
                if is_window:
                    layer.add(x, y, z, glass, src_win, expect=current); stats['windows'] += 1
                elif wall is not None:
                    layer.add(x, y, z, wall, src_wall, expect=current); stats['wall'] += 1
                stats['cells'] += 1
            stats['evidence'][f['id']] = {'wall': wall_evidence, 'block': wall, 'levels': levels, 'floor_height_m': round(fh, 2),
                                         'faces': len(seen), 'height_m': height}
            claimed |= mask
        report[target['name']] = stats
    return layer, report
