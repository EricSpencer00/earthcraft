"""Public footprint of the Hugging Face dataset, as 256 m cells on the save frame.

The map must match the dataset, not whatever save is installed on one machine.
A cell is listed when the dataset holds a region for it: a chunk in
`world/index/regions.json`, or a published `tiles/v2/<X>_<Z>/` folder (the id is
the 512 m region coordinate). Neither proves current block fill.
"""
import base64
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from pyproj import CRS

REPO = 'EricSpencer00/earthcraft'
HUB = 'https://huggingface.co'
CELL = 256
REGION = 512
TILE = re.compile(r'^-?\d+_-?\d+$')
MAX_BYTES = 32 * 2**20
MAX_PAGES = 200  # tiles/v2 listing pages of 1,000; the plan has 95,399 tiles


def _get(url, limit=MAX_BYTES):
    request = urllib.request.Request(url, headers={'User-Agent': 'earthcraft-progress'})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(limit + 1)
        headers = response.headers
    if len(body) > limit:
        raise ValueError('Dataset response exceeds the byte cap')
    return body, headers


def lake_tiles(commit):
    """Lake water tile ids from lake/v1/index.json at this commit (none before it is published)."""
    try:
        body, _ = _get(f'{HUB}/datasets/{REPO}/resolve/{commit}/lake/v1/index.json', 8 * 2**20)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return set()
        raise
    index = json.loads(body)
    if index.get('schema') != 'earthcraft.lake-tiles.v1':
        raise ValueError('Unexpected lake index schema')
    return {t for t in index['tiles'] if TILE.match(t)}


def fetch_dataset(revision='main'):
    """Return (commit, region index bytes, published v2 tile ids, lake tile ids) from one revision."""
    index, headers = _get(f'{HUB}/datasets/{REPO}/resolve/{revision}/world/index/regions.json')
    commit = headers.get('X-Repo-Commit', '')
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Dataset did not report a commit')
    tiles, url = set(), f'{HUB}/api/datasets/{REPO}/tree/{commit}/tiles/v2?limit=1000'
    for _ in range(MAX_PAGES):
        body, headers = _get(url, 2**20)
        for entry in json.loads(body):
            tile = entry['path'].rsplit('/', 1)[-1]
            if entry['type'] == 'directory' and TILE.match(tile):
                tiles.add(tile)
        match = re.search(r'<([^>]+)>;\s*rel="next"', headers.get('Link', ''))
        if not match:
            return commit, index, tiles, lake_tiles(commit)
        url = match.group(1)
    raise ValueError('Dataset tile listing exceeds the page cap')


def _present_cells(region):
    """256 m cells of one region that hold at least one chunk."""
    bits = base64.b64decode(region.get('chunk_bitmap') or '')
    if len(bits) != 128:
        raise ValueError('Region chunk bitmap must be 128 bytes')
    out = set()
    for i in range(1024):
        if bits[i >> 3] >> (i & 7) & 1:
            out.add((i % 32 // 16, i // 32 // 16))
    return out


def dataset_cells(index_bytes, v2_tiles, lake=()):
    index = json.loads(index_bytes)
    if index.get('schema') != 'earthcraft.world-regions.v1':
        raise ValueError('Unexpected region index schema')
    per = REGION // CELL
    cells = set()
    for region in index['regions']:
        x, z = region['x'], region['z']
        if type(x) is not int or type(z) is not int:
            raise ValueError('Invalid region coordinates')
        for cx, cz in _present_cells(region):
            cells.add((x * per + cx, z * per + cz))
    for tile in (*v2_tiles, *lake):
        x, z = map(int, tile.split('_'))
        cells.update((x * per + dx, z * per + dz) for dz in range(per) for dx in range(per))
    return cells


def projection(frame):
    """Transverse Mercator parameters of the frame, for the site's block -> WGS84 inverse."""
    crs = CRS(frame['crs'])
    params = {p.name: p.value for p in crs.coordinate_operation.params}
    if (crs.coordinate_operation.method_name != 'Transverse Mercator' or crs.ellipsoid.name != 'WGS 84'
            or params['False easting'] or params['False northing']):
        raise ValueError('The site inverse supports only a WGS 84 Transverse Mercator frame without false origin')
    return {'method': 'transverse_mercator_wgs84', 'lat_0': params['Latitude of natural origin'],
            'lon_0': params['Longitude of natural origin'], 'k': params['Scale factor at natural origin'],
            'west': frame['west'], 'north': frame['north']}


def read_dataset_coverage(root, revision='main', fetch=fetch_dataset):
    frame = json.loads((Path(root) / 'configs/world-frame.json').read_text())
    commit, index_bytes, v2_tiles, lake = fetch(revision)
    keys = dataset_cells(index_bytes, v2_tiles, lake)
    # Cells carry only their id; the site places them with dashboard/frame.mjs from
    # 'projection'. Per-cell coordinates would push the Pages artifact past its cap.
    rows = [{'tile_id': f'{x}_{z}'} for x, z in sorted(keys, key=lambda k: (k[1], k[0]))]
    return {
        'schema_version': 1, 'basis': 'dataset_region_index',
        'dataset_revision': commit, 'native_tiles': len(v2_tiles),
        # Published 512 m tiles (region X_Z); the site reads their blocks from tiles/v2 at this revision.
        'v2_tiles': sorted(v2_tiles, key=lambda t: tuple(map(int, t.split('_')))),
        # Lake Michigan water tiles (lake/v1): built from bathymetry, not stored as regions.
        'lake_tiles': sorted(lake, key=lambda t: tuple(map(int, t.split('_')))),
        'omitted_declarations': 0, 'cell_size_m': CELL, 'unique_cells': len(rows),
        'unique_area_km2': len(rows) * CELL ** 2 / 1e6,
        'scan_upgrade_parent_tiles': 0, 'photo_colored_cells': 0,
        'current_block_fill_verified': False,
        'manifest_sha256': hashlib.sha256(index_bytes).hexdigest(),
        'frame_sha256': hashlib.sha256(json.dumps(
            {k: frame[k] for k in ('crs', 'west', 'north')}, sort_keys=True).encode()).hexdigest(),
        # Fields every dataset cell shares; stored once to keep the Pages artifact small.
        'projection': projection(frame),
        'cell_defaults': {
            'region_id': 'world', 'size_m': CELL, 'state': 'generated',
            'geometry_state': 'complete', 'appearance_state': 'pending',
            'game_verify_state': 'pending',
        },
        'cells': rows,
    }
