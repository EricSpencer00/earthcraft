"""Public geometry of the installed save's tile declarations, without world bytes.

Use the save's inherited metre frame. Split larger declarations on that frame's
256 m grid and union overlaps; a tile receipt does not prove every current chunk
is filled, scanned, or photo-colored.
"""
import hashlib
import json
import re
from pathlib import Path

from pyproj import Transformer

CELL = 256
STATUS = 'geometry_verified_existing_chunks_preserved'


def read_world_coverage(root):
    root = Path(root)
    save = root / 'runtime/traversal/saves/Earthcraft'
    manifest_bytes = (save / 'city-coverage.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    binding = json.loads((root / 'runtime/traversal/config/earthcraft-live.json').read_text())
    frame = binding['coordinate_frame']
    if manifest['frame'] != frame:
        raise ValueError('Save and binding frames differ; do not recenter the world')
    tiles = manifest['tiles']
    if not isinstance(tiles, dict) or len(tiles) > 100_000:
        raise ValueError('Unbounded or invalid save declarations')
    inverse = Transformer.from_crs(frame['crs'], 'EPSG:4326', always_xy=True)
    try:
        quality_bytes = (save / 'regional-quality.json').read_bytes()
        quality = json.loads(quality_bytes)
    except FileNotFoundError:
        quality_bytes = None
        quality = {'frame': frame, 'tiles': {}}
    if quality['frame'] != frame:
        raise ValueError('Quality receipts use a different frame')
    native_scan_tiles = sum(
        r.get('quality') == 'scan-points-and-roof' and r.get('upgraded_chunks', 0) > 0
        for r in quality['tiles'].values())
    cells = {}
    omitted = 0
    for tile in tiles.values():
        size, offset = tile['size_m'], tile['world_offset_xz']
        if (type(size) is not int or not 0 < size <= 4096 or
                not isinstance(offset, list) or len(offset) != 2 or
                any(type(v) is not int or v % 16 for v in offset)):
            raise ValueError('Invalid tile dimensions')
        # Legacy small declarations cannot be enlarged into a whole grid cell.
        if tile.get('status') != STATUS or size % CELL or any(v % CELL for v in offset):
            omitted += 1
            continue
        x, z = offset
        for dz in range(0, size, CELL):
            for dx in range(0, size, CELL):
                key = ((x + dx) // CELL, (z + dz) // CELL)
                cells.setdefault(key, False)
        if len(cells) > 500_000:
            raise ValueError('Unbounded save footprint')
    # An upgrade can replace existing chunks without adding a new 512 m
    # declaration. Join its footprint to the existing cells independently.
    for identity, receipt in quality['tiles'].items():
        if receipt.get('quality') != 'scan-points-and-roof' or receipt.get('upgraded_chunks', 0) <= 0:
            continue
        match = re.fullmatch(r'(\d+):(-?\d+)_(-?\d+)', identity)
        if not match:
            raise ValueError('Invalid scan receipt address')
        size, tx, tz = map(int, match.groups())
        if size % CELL or not CELL <= size <= 4096:
            raise ValueError('Invalid scan receipt dimensions')
        for dz in range(size // CELL):
            for dx in range(size // CELL):
                key = (tx * size // CELL + dx, tz * size // CELL + dz)
                if key in cells:
                    cells[key] = True
    rows = []
    for (x, z), scan in sorted(cells.items(), key=lambda item: (item[0][1], item[0][0])):
        east, north = frame['west'] + x * CELL, frame['north'] - z * CELL
        corners = [list(inverse.transform(east + dx, north - dz))
                   for dx, dz in ((0, 0), (CELL, 0), (CELL, CELL), (0, CELL))]
        lon, lat = inverse.transform(east + CELL / 2, north - CELL / 2)
        tile_id = f'{x}_{z}'
        rows.append({
            'id': f'world/{tile_id}', 'region_id': 'world', 'tile_id': tile_id,
            'size_m': CELL, 'state': 'generated', 'geometry_state': 'complete',
            'appearance_state': 'pending', 'game_verify_state': 'pending',
            'longitude': lon, 'latitude': lat, 'corners_lonlat': corners,
            'width_deg': max(p[0] for p in corners) - min(p[0] for p in corners),
            'height_deg': max(p[1] for p in corners) - min(p[1] for p in corners),
            'scan_upgrade_recorded_in_parent_tile': scan,
        })
    # Metadata is atomic per file, but two independently updated files can race.
    if (save / 'city-coverage.json').read_bytes() != manifest_bytes:
        raise ValueError('Save manifest changed during export; retry later')
    if quality_bytes is not None and (save / 'regional-quality.json').read_bytes() != quality_bytes:
        raise ValueError('Quality receipts changed during export; retry later')
    return {
        'schema_version': 1, 'basis': 'installed_save_tile_manifest',
        'native_tiles': len(tiles), 'omitted_declarations': omitted,
        'cell_size_m': CELL, 'unique_cells': len(rows),
        'unique_area_km2': len(rows) * CELL ** 2 / 1e6,
        'scan_upgrade_parent_tiles': native_scan_tiles,
        'photo_colored_cells': 0, 'current_block_fill_verified': False,
        'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'frame_sha256': hashlib.sha256(json.dumps(frame, sort_keys=True).encode()).hexdigest(),
        'cells': rows,
    }
