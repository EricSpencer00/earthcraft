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
import urllib.request
from pathlib import Path

from pyproj import Transformer

REPO = 'EricSpencer00/earthcraft'
HUB = 'https://huggingface.co'
CELL = 256
REGION = 512
TILE = re.compile(r'^-?\d+_-?\d+$')
MAX_BYTES = 32 * 2**20
MAX_PAGES = 50


def _get(url, limit=MAX_BYTES):
    request = urllib.request.Request(url, headers={'User-Agent': 'earthcraft-progress'})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(limit + 1)
        headers = response.headers
    if len(body) > limit:
        raise ValueError('Dataset response exceeds the byte cap')
    return body, headers


def fetch_dataset(revision='main'):
    """Return (commit, region index bytes, published v2 tile ids) from one revision."""
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
            return commit, index, tiles
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


def dataset_cells(index_bytes, v2_tiles):
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
    for tile in v2_tiles:
        x, z = map(int, tile.split('_'))
        cells.update((x * per + dx, z * per + dz) for dz in range(per) for dx in range(per))
    return cells


def read_dataset_coverage(root, revision='main', fetch=fetch_dataset):
    frame = json.loads((Path(root) / 'configs/world-frame.json').read_text())
    commit, index_bytes, v2_tiles = fetch(revision)
    keys = dataset_cells(index_bytes, v2_tiles)
    inverse = Transformer.from_crs(frame['crs'], 'EPSG:4326', always_xy=True)
    rows = []
    for x, z in sorted(keys, key=lambda k: (k[1], k[0])):
        # Cell centres only: the app sizes each cell from latitude, and per-cell
        # corners would push the Pages artifact past its cap at this many cells.
        east, north = frame['west'] + (x + 0.5) * CELL, frame['north'] - (z + 0.5) * CELL
        lon, lat = (round(v, 6) for v in inverse.transform(east, north))
        tile_id = f'{x}_{z}'
        rows.append({
            'id': f'world/{tile_id}', 'region_id': 'world', 'tile_id': tile_id,
            'size_m': CELL, 'state': 'generated', 'geometry_state': 'complete',
            'appearance_state': 'pending', 'game_verify_state': 'pending',
            'longitude': lon, 'latitude': lat,
        })
    return {
        'schema_version': 1, 'basis': 'dataset_region_index',
        'dataset_revision': commit, 'native_tiles': len(v2_tiles),
        'omitted_declarations': 0, 'cell_size_m': CELL, 'unique_cells': len(rows),
        'unique_area_km2': len(rows) * CELL ** 2 / 1e6,
        'scan_upgrade_parent_tiles': 0, 'photo_colored_cells': 0,
        'current_block_fill_verified': False,
        'manifest_sha256': hashlib.sha256(index_bytes).hexdigest(),
        'frame_sha256': hashlib.sha256(json.dumps(
            {k: frame[k] for k in ('crs', 'west', 'north')}, sort_keys=True).encode()).hexdigest(),
        'cells': rows,
    }
