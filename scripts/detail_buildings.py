"""Building regularization: keep the scan's placement, emit clean closed volumes.

The canonical save places buildings well (footprint position, roof heights)
but many downtown shells are sparse LiDAR voxels: walls with holes, thin
slabs, interior clutter and floating fragments. This pass rebuilds each mapped
footprint as a closed volume whose *heights come from the save itself*:

* roof height per column = highest existing solid voxel in that column,
  de-spiked with a 3x3 then 5x5 median (tiers and setbacks survive, single
  stray voxels do not); unobserved columns take the nearest observed height;
* walls are solid from ground to roof on the footprint outline and on every
  step down to a lower tier; the roof is a one-block plate; interiors are air;
* interior clutter below the roof is removed; nothing above the roof is
  removed (antennas, spires and penthouses stay as scanned);
* floating fragments outside every footprint (components that never touch
  the ground and are small) are removed;
* new wall/roof blocks reuse the building's own dominant existing material,
  so appearance is unchanged until the facade layer recolours it.

Every edit carries the block it replaces (layer precondition), so the pass is
reversible. Footprints with too few observed columns are left untouched.
Geometry changes are a separate layer with their own receipt, never mixed into
the appearance layers.
"""
import numpy as np
from scipy import ndimage

AIR = 'minecraft:air'


def _nan_median(values, size):
    filled = np.where(np.isnan(values), -1e9, values)
    out = ndimage.median_filter(filled, size=size, mode='nearest')
    valid = ndimage.uniform_filter((~np.isnan(values)).astype(float), size=size, mode='nearest') > 0.5
    return np.where(valid & (out > -1e8), out, np.nan)


def roof_heights(column_top, footprint, min_coverage=0.3):
    """Despiked per-column roof heights inside one footprint, or None if poorly observed."""
    h = np.where(footprint, column_top, np.nan).astype(float)
    observed = footprint & ~np.isnan(h)
    if observed.sum() < max(4, min_coverage * footprint.sum()):
        return None, float(observed.sum()) / max(1, footprint.sum())
    h = _nan_median(h, 3)
    h = _nan_median(h, 5)
    known = footprint & ~np.isnan(h)
    if not known.any():
        return None, 0.0
    idx = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    filled = np.where(footprint, np.round(h[tuple(idx)]), np.nan)
    return filled, float(observed.sum()) / footprint.sum()


def closed_volume(top, footprint, ground):
    """Boolean occupancy [rows, cols, y] (as a dict of column -> (lo, hi) spans) for a closed shell."""
    rows, cols = footprint.shape
    spans = {}
    padded = np.pad(np.where(footprint, top, -1), 1, constant_values=-1)
    for r, c in zip(*np.nonzero(footprint)):
        t = int(top[r, c])
        g = int(ground[r, c])
        neighbours = [padded[r, c + 1], padded[r + 2, c + 1], padded[r + 1, c], padded[r + 1, c + 2]]
        lowest = min(neighbours)
        col = [(t, t)]                      # roof plate
        if lowest < 0:
            col.append((g + 1, t))          # outline wall
        elif lowest < t - 1:
            col.append((int(lowest) + 1, t))  # step wall down to a lower tier
        spans[(r, c)] = col
    return spans


def regularize(world, footprints, tile, accept, protect, min_cells=8, fragment_max=2000, band_cells=2):
    """Return (edits, report). edits: list of (x, y, z, new_state, expected_state, footprint_id)."""
    vol, names, ylo = world['vol'], np.asarray(world['names'], dtype=object), world['ylo']
    base = np.array([k.split('[', 1)[0] for k in names], dtype=object)
    air_ids = np.isin(base, ['minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'])
    solid = ~air_ids[vol]
    ground = world['ground_y']
    H = vol.shape[2]
    yy = np.arange(H) + ylo
    above_ground = yy[None, None, :] > ground[:, :, None] + 0
    top_any = np.where((solid & above_ground).any(axis=2),
                       H - 1 - np.argmax((solid & above_ground)[:, :, ::-1], axis=2) + ylo, np.nan)
    owner = np.zeros(ground.shape, np.int32)
    edits, report = [], {'footprints': 0, 'regularized': 0, 'skipped_low_coverage': 0, 'skipped_small': 0,
                         'added_cells': 0, 'removed_interior_cells': 0, 'removed_fragment_cells': 0,
                         'coverage_median': None}
    coverages = []
    ordered = sorted(footprints, key=lambda f: -f['geom'].area)
    for index, f in enumerate(ordered, start=1):
        mask = tile.raster([(f['geom'], 1)]).astype(bool) & (owner == 0) & ~protect
        report['footprints'] += 1
        if mask.sum() < min_cells:
            report['skipped_small'] += 1
            continue
        top, coverage = roof_heights(top_any, mask)
        coverages.append(coverage)
        if top is None:
            report['skipped_low_coverage'] += 1
            continue
        owner[mask] = index
        rr, cc = np.nonzero(mask)
        ids = vol[rr, cc][solid[rr, cc] & above_ground[rr, cc]]
        opaque = ids[~np.char.endswith(base[ids].astype(str), '_glass')] if len(ids) else ids
        material = names[np.bincount(opaque).argmax()] if len(opaque) else 'minecraft:light_gray_concrete'
        top_ids = [vol[r, c, int(top[r, c]) - ylo] for r, c in zip(rr, cc)
                   if 0 <= int(top[r, c]) - ylo < H and solid[r, c, int(top[r, c]) - ylo]]
        roof_material = names[np.bincount(top_ids).argmax()] if top_ids else material
        spans = closed_volume(top, mask, ground)
        for (r, c), col in spans.items():
            x, z = tile.x0 + c, tile.z0 + r
            if not (accept[0] <= x < accept[2] and accept[1] <= z < accept[3]):
                continue
            want = np.zeros(H, bool)
            for lo, hi in col:
                want[max(0, lo - ylo):min(H, hi - ylo + 1)] = True
            t = int(top[r, c])
            g = int(ground[r, c])
            column_solid = solid[r, c]
            for iy in np.flatnonzero(want & ~column_solid):
                edits.append((x, int(iy + ylo), z, roof_material if iy + ylo == t else material, None, f['id']))
                report['added_cells'] += 1
            # Walls take one material per building (scan voxels carry no observed wall colour);
            # existing roof cells keep their (orthophoto-routed) colour.
            wall = want.copy()
            if 0 <= t - ylo < H:
                wall[t - ylo] = False
            for iy in np.flatnonzero(wall & column_solid & (vol[r, c] != np.flatnonzero(names == material)[0])):
                edits.append((x, int(iy + ylo), z, material, names[vol[r, c, iy]], f['id']))
                report['recoloured_wall_cells'] = report.get('recoloured_wall_cells', 0) + 1
            interior = column_solid & ~want & (yy > g) & (yy < t)
            for iy in np.flatnonzero(interior):
                edits.append((x, int(iy + ylo), z, AIR, names[vol[r, c, iy]], f['id']))
                report['removed_interior_cells'] += 1
        report['regularized'] += 1
    # Scan wall returns just outside a closed outline duplicate the new wall: clear a 2-cell band
    # above street level (awnings/canopies at <= ground+2 stay).
    band = ndimage.binary_dilation(owner > 0, iterations=band_cells) & (owner == 0) & ~protect
    for r, c in zip(*np.nonzero(band)):
        x, z = tile.x0 + c, tile.z0 + r
        if not (accept[0] <= x < accept[2] and accept[1] <= z < accept[3]):
            continue
        g = int(ground[r, c])
        for iy in np.flatnonzero(solid[r, c] & (yy > g + 2)):
            edits.append((x, int(iy + ylo), z, AIR, names[vol[r, c, iy]], 'outline_band'))
            report['removed_band_cells'] = report.get('removed_band_cells', 0) + 1
        solid[r, c, yy > g + 2] = False
    # Floating fragments outside all footprints: small 26-connected components not touching ground.
    outside = (owner == 0) & ~protect
    candidate = solid & above_ground & outside[:, :, None]
    candidate &= ~((yy[None, None, :] <= ground[:, :, None] + 1))
    labels, count = ndimage.label(candidate, structure=np.ones((3, 3, 3)))
    if count:
        sizes = ndimage.sum_labels(np.ones_like(labels), labels, index=np.arange(1, count + 1))
        grounded = np.zeros(count + 1, bool)
        touch = solid & (yy[None, None, :] <= ground[:, :, None] + 1)
        # A component is grounded if any of its voxels sits directly on a ground-touching solid voxel.
        below = np.zeros_like(candidate)
        below[:, :, 1:] = touch[:, :, :-1]
        grounded[np.unique(labels[candidate & below])] = True
        # Components attached to a regularised building (awnings, canopies, offset walls) stay.
        owned = solid & (owner > 0)[:, :, None] & above_ground
        attached = ndimage.binary_dilation(owned, structure=np.ones((3, 3, 3)))
        grounded[np.unique(labels[candidate & attached])] = True
        grounded[0] = True
        drop = (~grounded) & np.r_[False, sizes <= fragment_max]
        rz, rx, ry = np.nonzero(drop[labels])
        for r, c, iy in zip(rz, rx, ry):
            x, z = tile.x0 + c, tile.z0 + r
            if accept[0] <= x < accept[2] and accept[1] <= z < accept[3]:
                edits.append((x, int(iy + ylo), z, AIR, names[vol[r, c, iy]], 'fragment'))
                report['removed_fragment_cells'] += 1
        report['fragment_components_removed'] = int(drop.sum())
    report['coverage_median'] = float(np.median(coverages)) if coverages else None
    return edits, report
