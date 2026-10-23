"""LiDAR trees for one regional tile, planted into the world the tile delivers.

The newest classified USGS 3DEP EPT survey over the tile is cropped at full
density (detail_points), its canopy height model is segmented into crowns
(detail_trees) on the tile's own block grid, and the admitted trees are applied
to the delivered region (detail_apply), each edit only where the world still
holds air. Observed: position, height, crown footprint, upper canopy surface.
Estimated: trunk, branches, crown underside and leaf block (from crown shape,
never a species claim). Trunks stand only on natural ground; crowns never enter
built columns. The base world's checks stay in its receipt; this pass records
the region hash before and after.
"""
import json
import os
from pathlib import Path

import numpy as np

from detail_context import NATURAL_GROUND, Tile, lidar_rasters
from detail_guard import WriteGuard
from detail_layer import LayerBuilder
import detail_points
import detail_trees as tr

LICENSE = 'Public domain (USGS 3DEP)'
TREE_CAP = 900 * 2**20  # points crop + layer + one rewritten region
TREE_FLOOR = 4 * 2**30


def tile_grid(tile, frame):
    x0, z0 = round(tile['west'] - frame['west']), round(frame['north'] - tile['north'])
    if x0 % 512 or z0 % 512 or tile['size'] != 512:
        raise ValueError('Tile is not one region on the frame grid')
    return Tile(frame, x0, z0, tile['size'])


def tile_surface(world, grid, headroom=430):
    """Ground, occupancy and surface block of a generated tile world.

    Regional worlds stand on a thin terrain slab with air beneath, so ground is
    the world's own DEM row (as verify_metric_world reads it), not the first air
    above some fixed floor. A column is built when any block stands above ground.
    """
    import rasterio
    from detail_render import load_window
    report = json.loads((Path(world) / 'earthcraft.json').read_text())
    with rasterio.open(report['dem_path']) as dem:
        ground = np.floor(dem.read(1) + report['vertical_offset_m']).astype(np.int32)
    if ground.shape != (grid.size, grid.size):
        raise ValueError('World DEM does not match the tile grid')
    ylo = int(ground.min()) - 1
    yhi = min(ylo + headroom, report['dimension_min_y'] + report['dimension_height'])
    vol, names = load_window(Path(world) / 'region', (grid.x0, grid.z0, grid.x0 + grid.size, grid.z0 + grid.size), ylo, yhi)
    solid = np.array([name.split('[', 1)[0] not in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air')
                      for name in names])[vol]
    top = np.where(solid.any(axis=2), vol.shape[2] - 1 - np.argmax(solid[:, :, ::-1], axis=2) + ylo, ylo)
    zz, xx = np.indices(ground.shape)
    surface = vol[zz, xx, np.clip(ground - ylo, 0, vol.shape[2] - 1)]
    return {'ground_y': ground, 'top_y': top.astype(np.int32), 'built': top > ground,
            'surface_key': np.asarray(names, dtype=object)[surface]}


def layer_for(trees, labels, chm, surface, grid, frame, project):
    """detail_trees output -> one 'trees' layer with observed/estimated sources per tree."""
    layer = LayerBuilder('trees', frame, f'LiDAR canopy trees (USGS 3DEP {project})')
    blocked = surface['built']
    for tree in trees:
        voxels, _ = tr.tree_voxels(tree, labels, chm, surface['ground_y'], grid.x0, grid.z0)
        common = {'feature': tree['id'], 'height_m': tree['height'], 'crown_radius_m': tree['radius'],
                  'returns': tree['points'], 'shape': tree['shape']}
        observed = layer.source('tree_canopy', f'USGS 3DEP {project}', LICENSE, 'observed',
                                note='position, height, crown footprint and upper canopy surface', **common)
        estimated = layer.source('tree_structure', f'USGS 3DEP {project}', LICENSE, 'estimated',
                                 note='trunk, branches, crown underside and leaf block chosen from crown shape', **common)
        for (x, y, z), (state, role) in sorted(voxels.items()):
            r, c = z - grid.z0, x - grid.x0
            if not (0 <= r < grid.size and 0 <= c < grid.size) or blocked[r, c] or y <= surface['ground_y'][r, c]:
                continue
            layer.add(x, y, z, state, observed if role == 'canopy_observed' else estimated,
                      priority=3 if role == 'trunk_estimated' else 2 if 'log' in state else 1)
    return layer


def plant(tile, frame, world, work, catalog, *, max_download=600 * 2**20, cache=None, points=None):
    """Plant LiDAR trees into world/region in place. Returns the tree receipt.

    points: an existing crop of the same survey (the scan surfaces'), reused as is;
    cache: the regional EPT node cache, so a fresh crop downloads nothing twice.
    """
    from detail_apply import apply
    from regional_point_catalog import candidates
    world, work = Path(world), Path(work)
    surveys = candidates(tile, frame, catalog) if catalog else []
    if not surveys:
        return {'result': 'no classified survey over the tile', 'trees_admitted': 0}
    project = surveys[0]['project']
    guard = WriteGuard(work, TREE_CAP, TREE_FLOOR)
    shared = points is not None and (Path(points) / 'manifest.json').exists() and \
        json.loads((Path(points) / 'manifest.json').read_text()).get('project') == project
    if shared:
        crop_dir = Path(points)
        points = json.loads((crop_dir / 'manifest.json').read_text())
    else:
        crop_dir = work / 'points'
        points = detail_points.crop(project, frame, tile['west'], tile['north'], tile['size'], 0, guard,
                                    crop_dir, max_download=max_download, cache=cache)
    xyz, cls, _, _, intensity = detail_points.load(crop_dir)
    grid = tile_grid(tile, frame)
    surface = tile_surface(world, grid)
    lr = lidar_rasters((xyz, cls, np.zeros(len(cls), np.uint16) if intensity is None else intensity), grid)
    del xyz
    base = np.vectorize(lambda key: key.split('[', 1)[0])(surface['surface_key'])
    water = base == 'minecraft:water'
    paved = ~np.isin(base, list(NATURAL_GROUND)) & ~water  # trunks stand on lawn and soil only
    accept = (grid.x0, grid.z0, grid.x0 + grid.size, grid.z0 + grid.size)
    trees, rejected, labels, chm = tr.build_trees(lr, surface, surface['built'], paved, water, grid, accept)
    report = {'result': 'pass', 'survey': project, 'survey_year': surveys[0].get('year_from_project_name'),
              'points': points['cropped_points'], 'points_sha256': points['points_sha256'],
              'downloaded_bytes': 0 if shared else points['downloaded_bytes'], 'crop_shared_with_scan': shared,
              'trees_admitted': len(trees),
              'candidate_tops': len(trees) + sum(rejected.values()), 'rejected': rejected,
              'shapes': {shape: sum(t['shape'] == shape for t in trees) for shape in sorted({t['shape'] for t in trees})},
              'height_m_percentiles': np.percentile([t['height'] for t in trees], [10, 50, 90]).round(1).tolist()
              if trees else None, 'license': LICENSE}
    if not trees:
        return report
    layer = layer_for(trees, labels, chm, surface, grid, frame, project)
    layer.write(work / 'layer', guard, {'tile': tile['id']})
    manifest = json.loads((world / 'earthcraft.json').read_text())
    receipt = apply(world / 'region', [work / 'layer'], work / 'region', guard,
                    height=manifest['dimension_height'], grow=True)
    name = f'r.{grid.x0 >> 9}.{grid.z0 >> 9}'
    applied = receipt['regions'].get(name, {})
    if applied.get('verified'):
        os.replace(work / 'region' / f'{name}.mca', world / 'region' / f'{name}.mca')
    report.update(blocks_applied=applied.get('applied', 0), precondition_failed=applied.get('precondition_failed', 0),
                  region_before_sha256=applied.get('input_sha256'), region_after_sha256=applied.get('output_sha256'),
                  edits_by_kind=layer.counts_by_kind())
    return report


def record(world, report):
    """Add the tree receipt to the world manifest (earthcraft.json)."""
    path = Path(world) / 'earthcraft.json'
    manifest = json.loads(path.read_text())
    manifest['trees'] = report
    if report.get('trees_admitted'):
        manifest.setdefault('limitations', []).append(
            'Trees: position, height and crown footprint from LiDAR; trunk, branches and crown underside estimated.')
    path.write_text(json.dumps(manifest, indent=2))
