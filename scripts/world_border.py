"""Keep Minecraft's two overworld border representations in sync."""
from pathlib import Path

import nbtlib as n


BORDER_MARGIN_M = 2048
DEFAULT_BORDER = {
    'damage_per_block': 0.2,
    'safe_zone': 5.0,
    'warning_blocks': 5,
    'warning_time': 15,
}


def bounds_for_tiles(tiles, margin=BORDER_MARGIN_M):
    """Return a square traversal envelope with ``margin`` on every side."""
    rectangles = []
    for tile in tiles:
        offset = tile.get('world_offset_xz') or tile.get('offset_xz')
        size = tile.get('size_m', tile.get('size'))
        if not offset or size is None:
            raise ValueError('Tile is missing world_offset_xz or size_m')
        x, z = (int(offset[0]), int(offset[1]))
        size = int(size)
        if size <= 0 or x % 16 or z % 16 or size % 16:
            raise ValueError('World-border tile must be positive and chunk aligned')
        rectangles.append((x, z, size))
    if not rectangles:
        raise ValueError('Cannot derive a world border without tiles')
    x0 = min(x for x, _, _ in rectangles)
    z0 = min(z for _, z, _ in rectangles)
    x1 = max(x + size for x, _, size in rectangles)
    z1 = max(z + size for _, z, size in rectangles)
    return {
        'center_x': (x0 + x1) / 2,
        'center_z': (z0 + z1) / 2,
        'size': max(x1 - x0, z1 - z0) + 2 * float(margin),
        'min_x': x0,
        'min_z': z0,
        'max_x': x1,
        'max_z': z1,
    }


def bounds_for_plan(plan, margin=BORDER_MARGIN_M):
    """Use the frozen plan, never the currently materialized subset."""
    tiles = plan.get('tiles', [])
    if not tiles:
        raise ValueError('City plan has no tiles for its world border')
    return bounds_for_tiles(tiles, margin=margin)


def update_world_border(world, bounds):
    """Write level.dat and data/world_border.dat for a closed world."""
    world = Path(world)
    level_path = world / 'level.dat'
    level = n.load(level_path)
    data = level['Data']
    data['BorderCenterX'] = n.Double(bounds['center_x'])
    data['BorderCenterZ'] = n.Double(bounds['center_z'])
    data['BorderSize'] = n.Double(bounds['size'])
    data['BorderSizeLerpTarget'] = n.Double(bounds['size'])
    data['BorderSizeLerpTime'] = n.Long(0)
    level.save(level_path)

    border_path = world / 'data' / 'world_border.dat'
    border_path.parent.mkdir(parents=True, exist_ok=True)
    if border_path.exists():
        border_file = n.load(border_path)
    else:
        border_file = n.File({'DataVersion': n.Int(4556), 'data': n.Compound({})}, gzipped=True)
    border = border_file['data']
    values = {
        'center_x': bounds['center_x'],
        'center_z': bounds['center_z'],
        'size': bounds['size'],
        'lerp_target': bounds['size'],
        'lerp_time': 0,
        **DEFAULT_BORDER,
    }
    numeric = {
        'center_x': n.Double,
        'center_z': n.Double,
        'size': n.Double,
        'lerp_target': n.Double,
        'lerp_time': n.Long,
        'damage_per_block': n.Double,
        'safe_zone': n.Double,
        'warning_blocks': n.Int,
        'warning_time': n.Int,
    }
    for key, value in values.items():
        border[key] = numeric[key](value)
    border_file.save(border_path)
    return bounds
