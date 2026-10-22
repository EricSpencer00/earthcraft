"""Shared offline fixtures: a fake Hub over HTTP (for DatasetReader) and synthetic tiles."""
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import zlib

import nbtlib as n

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hf_store as hs  # noqa: E402
from region_expansion import encode  # noqa: E402
from test_hf_store import FakeHub, REPO, make_ctx  # noqa: E402,F401

FRAME = {'crs': 'fixture-crs', 'west': 0, 'north': 0, 'vertical_offset_m': 0, 'dimension_min_y': -64,
         'dimension_height': 1024}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeOpener:
    """urlopen() stand-in serving the public Hub API and resolve URLs from a FakeHub."""

    def __init__(self, hub, repo=REPO):
        self.hub, self.repo, self.requests = hub, repo, []

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        self.requests.append(request.full_url)
        files = self.hub.repos[self.repo]['files']
        api = f'/api/datasets/{self.repo}/'
        if url.path.startswith(api + 'revision/'):
            info = {'sha': self.hub.repos[self.repo]['sha']}
            if ('expand[]', 'sha') not in urllib.parse.parse_qsl(url.query):
                # Like the Hub: the full answer lists every file, megabytes for this dataset.
                info['siblings'] = [{'rfilename': f'tiles/v2/{i}_0/tile.json'} for i in range(120000)]
            return _Response(json.dumps(info).encode())
        if url.path.startswith(api + 'paths-info/'):
            form = urllib.parse.parse_qs(request.data.decode())
            items = []
            for path in form.get('paths', []):
                if path in files:
                    remote = self.hub.remote(path, files[path])
                    item = {'type': 'file', 'oid': remote.blob_id, 'size': remote.size, 'path': path}
                    if remote.sha256:
                        item['lfs'] = {'oid': remote.sha256, 'size': remote.size}
                    items.append(item)
            return _Response(json.dumps(items).encode())
        prefix = f'/datasets/{self.repo}/resolve/'
        if url.path.startswith(prefix):
            path = urllib.parse.unquote(url.path[len(prefix):].split('/', 1)[1])
            if path in files:
                return _Response(files[path])
        raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', {}, None)


def reader_for(hub):
    return hs.DatasetReader(REPO, opener=FakeOpener(hub), token='', retry=hs.Retry(attempts=2, sleep=lambda _s: None))


def chunk_record(cx, cz, block='minecraft:stone', stamp=0):
    tag = n.File({'DataVersion': n.Int(4556), 'xPos': n.Int(cx), 'zPos': n.Int(cz), 'yPos': n.Int(-4),
                  'Status': n.String('minecraft:full'),
                  'sections': n.List[n.Compound]([n.Compound({'Y': n.Byte(0), 'block_states': n.Compound({
                      'palette': n.List[n.Compound]([n.Compound({'Name': n.String(block)})])})})])})
    stream = io.BytesIO()
    tag.write(stream)
    raw = zlib.compress(stream.getvalue())
    return ((len(raw) + 1).to_bytes(4, 'big') + bytes([2]) + raw, stamp.to_bytes(4, 'big'))


def region(rx, rz, block='minecraft:stone'):
    return encode({slot: chunk_record(rx * 32 + slot % 32, rz * 32 + slot // 32, block) for slot in range(1024)})


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_world(directory, tile, block, *, cache_root=None, extrusion=True):
    rx, rz = map(int, tile.split('_'))
    directory = Path(directory)
    (directory / 'region').mkdir(parents=True)
    (directory / 'region' / f'r.{rx}.{rz}.mca').write_bytes(region(rx, rz, block))
    source = {'size': 512, 'west': rx * 512, 'north': -rz * 512, 'crs': FRAME['crs']}
    if extrusion:
        source['building_source_kind'] = 'osm-presentation'
    if cache_root is not None:
        source['elevation_assets'] = [{'url': 'https://example.invalid/15/1/2.png', 'sha256': '0' * 64,
                                       'cache_path': str(Path(cache_root) / 'terrain' / '15-1-2.png')}]
    meta = {'world_frame': FRAME, 'world_offset_xz': [rx * 512, rz * 512], 'source': source,
            'dem_path': str((directory.parent / 'sources' / 'elevation.tif').absolute())}
    if not extrusion:
        meta['lidar_surface_source'] = {'verified': True}
    (directory / 'earthcraft.json').write_text(json.dumps(meta, indent=2))
    return directory


def world_receipt(tile, stage, world, quality='LQ'):
    rx, rz = map(int, tile.split('_'))
    name = f'r.{rx}.{rz}.mca'
    return {'tile': tile, 'stage': stage, 'result': 'pass', 'world': str(Path(world).resolve()), 'quality': quality,
            'installed': False, 'world_manifest_sha256': sha(Path(world) / 'earthcraft.json'),
            'regions': {name: sha(Path(world) / 'region' / name)}, 'checks': {'chunks': 1024}}


def fake_tile_pass(tile, root, cache_root, *, scan=True):
    """What one pass leaves in root: sources, the LQ world and (optionally) a scan world."""
    root = Path(root)
    (root / 'sources').mkdir(parents=True)
    (root / 'sources' / 'sources.json').write_text('{}')
    receipts = {'sources': {'tile': tile, 'stage': 'sources', 'result': 'pass', 'installed': False,
                            'sources_sha256': sha(root / 'sources' / 'sources.json'),
                            'inputs': str(root / 'sources')}}
    lq = write_world(root / 'world', tile, 'minecraft:stone', cache_root=cache_root)
    receipts['geometry'] = world_receipt(tile, 'geometry', lq)
    delivered = lq
    if scan:
        scanned = write_world(root / 'world.scanned', tile, 'minecraft:bricks', cache_root=cache_root, extrusion=False)
        receipts['appearance'] = dict(world_receipt(tile, 'appearance', scanned, 'scan-roof'),
                                      scan_source={'county': 'Lake', 'dtm': {'cache': str(Path(cache_root) / 'arcgrid' / 'Lake' / 'dtm')}})
        delivered = scanned
    return receipts, delivered, lq


def plan_for(tiles):
    rows = []
    for index, tile in enumerate(tiles):
        rx, rz = map(int, tile.split('_'))
        rows.append({'id': tile, 'tx': rx, 'tz': rz, 'west': rx * 512, 'north': -rz * 512, 'size': 512,
                     'priority': float(index)})
    return {'schema_version': 1, 'frame': FRAME, 'tiles': rows, 'city_area_m2': 1.0}


def write_templates(directory):
    """Placeholder world templates (the real ones hold a player compound and stay local)."""
    chunk = Path(directory) / 'chicago-water-tower-64' / 'Arnis World 1'
    (chunk / 'region').mkdir(parents=True)
    (chunk / 'level.dat').write_bytes(b'template')
    (chunk / 'region' / 'r.0.0.mca').write_bytes(bytes(8192))
    height = Path(directory) / 'Earthcraft-Chicago-Photo-Atlas-v2' / 'datapacks' / 'earthcraft_height'
    (height / 'data' / 'minecraft' / 'dimension_type').mkdir(parents=True)
    (height / 'pack.mcmeta').write_text('{}')
    (height / 'data' / 'minecraft' / 'dimension_type' / 'overworld.json').write_text('{}')
    return Path(directory)
