"""LiDAR trees: canopy height model -> tree tops -> crowns -> Minecraft blocks.

Observed (from the point cloud): tree position, total height, crown footprint
and the canopy's upper surface. Estimated: trunk, crown underside, branches
and the leaf block (oak / birch / spruce picked from crown shape, not species).

The survey's classes 3/4/5 are height bands of every non-ground, non-building
return (all single returns), so they are not trusted as "vegetation" alone.
A crown is admitted only if it is away from mapped/scanned buildings, tall
enough, wide enough and volumetric (points spread through its depth rather
than lying on one plane), and its trunk lands on a non-road, non-building,
non-water column of the canonical world.
"""
import hashlib
import math

import numpy as np
from scipy import ndimage

LEAVES = {'oak': 'minecraft:oak_leaves', 'birch': 'minecraft:birch_leaves', 'spruce': 'minecraft:spruce_leaves'}
# Chicago street trees are broadleaf (honeylocust, linden, elm, maple): bark reads as oak/dark wood;
# birch leaves are only a lighter crown tint for narrow crowns, never white birch trunks.
LOGS = {'oak': 'minecraft:oak_log', 'birch': 'minecraft:oak_log', 'spruce': 'minecraft:spruce_log'}
PIT = 'minecraft:coarse_dirt'


def unit_hash(*values):
    raw = hashlib.sha256('|'.join(map(str, values)).encode()).digest()
    return int.from_bytes(raw[:8], 'big') / 2**64


def canopy(lr, exclude, min_height=2.0):
    """Smoothed CHM restricted to admissible columns."""
    chm = np.where(exclude, 0, lr['chm']).astype(float)
    chm[chm < min_height] = 0
    smooth = ndimage.gaussian_filter(chm, 0.8)
    smooth[chm == 0] = 0
    return chm, smooth


def detect_tops(smooth, min_height=4.0):
    """Local maxima with a height-dependent suppression radius (3 m, or 30% of height if larger).

    Urban street-tree crowns are 3-5 m in radius; two tops closer than that are one crown.
    """
    peaks = (smooth == ndimage.maximum_filter(smooth, size=5)) & (smooth >= min_height)
    rows, cols = np.nonzero(peaks)
    order = np.argsort(-smooth[rows, cols], kind='stable')
    kept = []
    for i in order:
        r, c, h = rows[i], cols[i], smooth[rows[i], cols[i]]
        radius = max(3.0, 0.3 * h)
        if all((r - kr) ** 2 + (c - kc) ** 2 > radius ** 2 for kr, kc, _ in kept):
            kept.append((r, c, h))
    return kept


def segment(smooth, tops, min_height=2.0):
    """Marker watershed on the inverted CHM; crowns limited to a plausible radius."""
    canopy_mask = smooth >= min_height
    scaled = np.clip(1000 - smooth * 30, 0, 1000).astype(np.uint16)
    markers = np.zeros(smooth.shape, np.int32)
    markers[~canopy_mask] = 1
    for i, (r, c, _) in enumerate(tops):
        markers[r, c] = i + 2
    labels = ndimage.watershed_ift(scaled, markers)
    labels[~canopy_mask] = 0
    labels[labels == 1] = 0
    for i, (r, c, h) in enumerate(tops):
        limit = max(3.0, 0.4 * h + 2.0)
        rr, cc = np.nonzero(labels == i + 2)
        far = (rr - r) ** 2 + (cc - c) ** 2 > limit ** 2
        labels[rr[far], cc[far]] = 0
    return labels


def crown_metrics(labels, tops, chm, lr, points_hag):
    """Per-crown stats from the raster and the raw returns inside the crown."""
    n = len(tops)
    area = ndimage.sum_labels(np.ones_like(chm), labels, index=np.arange(2, n + 2))
    row, col, ok, hag = lr['point_row'], lr['point_col'], lr['point_ok'], points_hag
    cls = lr['_cls']
    sel = ok & np.isin(cls, (1, 3, 4, 5)) & (hag > 0.5) & (hag < 32)
    lab = labels[row[sel], col[sel]]
    keep = lab >= 2
    lab, h = lab[keep] - 2, hag[sel][keep]
    order = np.argsort(lab, kind='stable')
    lab, h = lab[order], h[order]
    bounds = np.searchsorted(lab, np.arange(n + 1))
    out = []
    for i, (r, c, top) in enumerate(tops):
        hs = h[bounds[i]:bounds[i + 1]]
        if len(hs) < 8:
            out.append(None)
            continue
        cells = labels == i + 2
        raw_top = float(chm[cells].max()) if cells.any() else float(top)
        high = float((chm[cells] >= 0.8 * raw_top).sum()) / max(1.0, float(area[i]))
        out.append({'area': float(area[i]), 'radius': math.sqrt(area[i] / math.pi), 'height': raw_top,
                    'points': int(len(hs)), 'p10': float(np.percentile(hs, 10)), 'p50': float(np.median(hs)),
                    'std': float(hs.std()), 'top_fraction': high})
    return out


def classify(m):
    """Leaf block from crown shape (a visual proxy, never a species claim)."""
    slender = m['height'] / max(1.0, 2 * m['radius'])
    if m['top_fraction'] < 0.12 and slender > 1.4:
        return 'spruce'
    if m['radius'] < 2.3 or slender > 1.6:
        return 'birch'
    return 'oak'


def admit(m, top_cell, near_building, min_area=16.0, min_height=5.0):
    """Reasons a crown is rejected (empty list = admitted)."""
    reasons = []
    if m is None:
        return ['too_few_returns']
    if m['area'] < min_area:
        reasons.append('crown_too_small')
    # Small, low 'crowns' downtown are mostly poles, signs and vehicles in the
    # unclassified returns; real street trees here are >= 5 m with >= 16 m2 crowns.
    if m['height'] < min_height:
        reasons.append('too_low')
    if m['std'] < 0.6 or (m['height'] - m['p10']) < 1.5:
        reasons.append('planar_not_volumetric')
    if near_building[top_cell]:
        reasons.append('top_near_building')
    return reasons


def place_trunk(r, c, labels, label, blocked, max_shift=3):
    """Top cell, or the nearest admissible crown cell within max_shift metres."""
    if not blocked[r, c]:
        return r, c, 0.0
    rr, cc = np.nonzero(labels == label)
    d2 = (rr - r) ** 2 + (cc - c) ** 2
    ok = ~blocked[rr, cc] & (d2 <= max_shift ** 2)
    if not ok.any():
        return None
    i = np.argmin(np.where(ok, d2, 1 << 30))
    return rr[i], cc[i], math.sqrt(float(d2[i]))


def tree_voxels(tree, labels, chm, ground_y, x0, z0):
    """Return dict cell->(state, role) for one admitted tree in world block coordinates."""
    r, c = tree['trunk']
    gy = int(ground_y[r, c])
    kind = tree['shape']
    height = max(4, int(round(tree['height'])))
    base = int(round(min(max(tree['crown_base'], 1.5), 0.7 * height)))
    top_y = gy + height
    base_y = gy + base
    centre_y = (top_y + base_y) / 2
    cells = {}
    rr, cc = np.nonzero(labels == tree['label'])
    tid = tree['id']
    for rr_, cc_ in zip(rr, cc):
        local_top = gy + int(round(chm[rr_, cc_])) if chm[rr_, cc_] > 0 else None
        if local_top is None or local_top <= base_y:
            continue
        if kind == 'spruce':
            bottom = base_y + 1
        else:
            bottom = int(math.floor(max(base_y + 1, 2 * centre_y - local_top)))
            bottom = min(bottom, local_top - 1)
        x, z = x0 + cc_, z0 + rr_
        for y in range(bottom, local_top + 1):
            surface = y == local_top
            cells[(x, y, z)] = (LEAVES[kind], 'canopy_observed' if surface else 'crown_estimated')
    # Gentle, deterministic thinning of exposed crown edges so it does not read as a cube.
    occupied = set(cells)
    for (x, y, z) in list(cells):
        exposed = sum((x + dx, y + dy, z + dz) not in occupied
                      for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0)))
        if exposed >= 2 and unit_hash(tid, x, y, z) < 0.18:
            del cells[(x, y, z)]
    tx, tz = x0 + c, z0 + r
    trunk_top = max(base_y, int(round(base_y + (top_y - base_y) * (0.35 if kind != 'spruce' else 0.8))))
    trunk_top = min(trunk_top, top_y - 1)
    for y in range(gy + 1, trunk_top + 1):
        cells[(tx, y, tz)] = (LOGS[kind] + '[axis=y]', 'trunk_estimated')
    if kind == 'oak' and tree['radius'] >= 3.0:
        branches = 2 + int(unit_hash(tid, 'n') * 3)
        for b in range(branches):
            angle = 2 * math.pi * (b / branches + unit_hash(tid, 'a', b) * 0.3)
            dx, dz = math.cos(angle), math.sin(angle)
            length = 1 + int(min(3, tree['radius'] / 2))
            by = trunk_top - int(unit_hash(tid, 'y', b) * 2)
            for step in range(1, length + 1):
                bx, bz = int(round(tx + dx * step)), int(round(tz + dz * step))
                axis = 'x' if abs(dx) >= abs(dz) else 'z'
                cells[(bx, by + step // 2, bz)] = (LOGS[kind] + f'[axis={axis}]', 'branch_estimated')
    # Leaf distance (1..7) from the nearest log, as the game computes it.
    logs = [k for k, v in cells.items() if 'log' in v[0]]
    dist = {k: 0 for k in logs}
    frontier = list(logs)
    for d in range(1, 8):
        nxt = []
        for (x, y, z) in frontier:
            for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                k = (x + dx, y + dy, z + dz)
                if k in cells and k not in dist:
                    dist[k] = d
                    nxt.append(k)
        frontier = nxt
    out = {}
    for k, (state, role) in cells.items():
        if 'leaves' in state:
            state = f'{state}[distance={min(7, dist.get(k, 7))},persistent=true,waterlogged=false]'
        out[k] = (state, role)
    return out, gy


def build_trees(lr, world, footprint_mask, road_mask, water_mask, tile, accept_bounds):
    """Detect and admit trees on the context grid. Returns (trees, rejected counts, labels, chm)."""
    built = world['built']
    building_cells = ndimage.binary_dilation(footprint_mask | (lr['building_count'] > 0), iterations=1)
    near_building = ndimage.binary_dilation(building_cells, iterations=2)
    chm, smooth = canopy(lr, building_cells | built)
    tops = detect_tops(smooth)
    labels = segment(smooth, tops)
    metrics = crown_metrics(labels, tops, chm, lr, lr['hag'])
    surface = world['surface_key']
    natural_or_pavement = np.vectorize(lambda k: k.split('[')[0] not in ('minecraft:water',))(surface)
    blocked = road_mask | footprint_mask | built | water_mask | ~natural_or_pavement
    trees, rejected = [], {}
    ax0, az0, ax1, az1 = accept_bounds
    for i, ((r, c, h), m) in enumerate(zip(tops, metrics)):
        reasons = admit(m, (r, c), near_building)
        spot = None if reasons else place_trunk(r, c, labels, i + 2, blocked)
        if not reasons and spot is None:
            reasons = ['no_admissible_trunk_cell']
        if not reasons:
            x, z = tile.x0 + spot[1], tile.z0 + spot[0]
            if not (ax0 <= x < ax1 and az0 <= z < az1):
                reasons = ['outside_tile']
        if reasons:
            for reason in reasons:
                rejected[reason] = rejected.get(reason, 0) + 1
            continue
        tree = {'id': f'tree-{tile.x0 + c}-{tile.z0 + r}', 'label': i + 2, 'top': (int(r), int(c)),
                'trunk': (int(spot[0]), int(spot[1])), 'trunk_shift_m': round(spot[2], 2),
                'height': round(m['height'], 2), 'radius': round(m['radius'], 2), 'area_m2': m['area'],
                'crown_base': round(max(1.5, m['p10']), 2), 'points': m['points'], 'std': round(m['std'], 2),
                'top_fraction': round(m['top_fraction'], 3)}
        tree['shape'] = classify(m)
        trees.append(tree)
    return trees, rejected, labels, chm
