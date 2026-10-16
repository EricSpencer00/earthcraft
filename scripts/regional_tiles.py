"""tiles/v2: one directory per generated 512 m tile, raw Anvil plus portable receipts.

Layout, relative to the dataset root:

  tiles/v2/<rx>_<rz>/tile.json                  entry point (schema earthcraft.generated-tile.v2)
  tiles/v2/<rx>_<rz>/region/r.<rx>.<rz>.mca      delivered region, raw Anvil (Range-readable)
  tiles/v2/<rx>_<rz>/earthcraft.json            delivered world manifest
  tiles/v2/<rx>_<rz>/lq/region/r.<rx>.<rz>.mca   LQ geometry from the same pass (scan tiles only):
  tiles/v2/<rx>_<rz>/lq/earthcraft.json         the immutable baseline a later install upgrades
  tiles/v2/<rx>_<rz>/receipts/<stage>.json      sources, geometry and appearance receipts

Why not the v1 tar shards: a raw .mca at a stable path can be read one chunk
at a time with HTTP Range (8 KiB header, then one sector run), by the browser
viewer and by the installer alike; one tile can be republished without
rewriting a shard shared with other tiles; and every file has its own Hub
SHA-256. Commit volume stays low because the generator still puts several
tiles in one commit.

Receipts are portable. Before upload, every local path is rewritten: published
files become their dataset path, per-tile scratch inputs 'gen:<tile>/...',
source-cache entries 'cache:...' (their URL and SHA-256 sit beside them), run
control files 'control:...', repository files 'repo:...'. A final gate refuses
any JSON that still holds an absolute local path. A receipt's
world_manifest_sha256 names the published earthcraft.json;
generated_world_manifest_sha256 keeps the hash of the file as generated.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil

PREFIX = 'tiles/v2'
TILE_SCHEMA = 'earthcraft.generated-tile.v2'
TILE_ID = re.compile(r'(-?\d+)_(-?\d+)')
STAGES = ('sources', 'geometry', 'appearance')
QUALITIES = ('LQ', 'scan-roof', 'scan-points-and-roof', 'classified-scan-points')
CHUNK_KEYS = {'Status', 'InhabitedTime', 'DataVersion', 'zPos', 'isLightOn', 'Heightmaps', 'structures', 'yPos',
              'xPos', 'entities', 'block_entities', 'PostProcessing', 'LastUpdate', 'sections', 'block_ticks',
              'fluid_ticks'}
LOCAL_PATH = re.compile(r'(?<![\w:./-])/(?:Users|Volumes|private|var|tmp|home|opt|Library|System|mnt|srv)/')
PATH_CONVENTIONS = {
    'tiles/v2/...': 'a file published in this dataset (see files[])',
    'gen:<tile>/...': 'per-tile scratch input or intermediate; deleted after upload, not published',
    'cache:<namespace>/...': 'shared source-cache entry; its URL and SHA-256 are recorded beside it',
    'control:...': 'run control file on the generating machine (plan, catalogs, journal)',
    'repo:...': 'file in the Earthcraft source repository at generator.revision',
    'sources/indexes/...': 'derived source index published in this dataset'}
ATTRIBUTION = [
    {'source': 'OpenStreetMap contributors', 'license': 'ODbL-1.0', 'url': 'https://www.openstreetmap.org/copyright'},
    {'source': 'USGS 3D Elevation Program lidar (EPT, usgs-lidar-public)', 'license': 'U.S. Government public domain',
     'url': 'https://registry.opendata.aws/usgs-lidar/'},
    {'source': 'Illinois Height Modernization / county lidar (ISGS clearinghouse)',
     'license': 'Public; attribution as recorded in receipts', 'url': 'https://clearinghouse.isgs.illinois.edu/'},
    {'source': 'Mapzen Terrain Tiles on AWS', 'license': 'Per upstream terrain source',
     'url': 'https://github.com/tilezen/joerd/blob/master/docs/attribution.md'},
    {'source': 'Overture Maps Foundation building footprints', 'license': 'ODbL-1.0 (buildings theme)',
     'url': 'https://docs.overturemaps.org/attribution/'},
    {'source': 'USDA NAIP aerial imagery (roof colour only)', 'license': 'U.S. Government public domain',
     'url': 'https://naip-usdaonline.hub.arcgis.com/'}]


def tile_coords(tile):
    match = TILE_ID.fullmatch(str(tile))
    if not match:
        raise ValueError(f'Invalid tile id: {tile!r}')
    return int(match[1]), int(match[2])


def tile_prefix(tile):
    tile_coords(tile)
    return f'{PREFIX}/{tile}'


def region_name(tile):
    rx, rz = tile_coords(tile)
    return f'r.{rx}.{rz}.mca'


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    return (json.dumps(value, indent=1, sort_keys=True) + '\n').encode()


# --------------------------------------------------------------------------
# Privacy and structure gate for published regions


def bounded_decompress(raw, mode, bound):
    import gzip
    import zlib
    if mode == 2:
        decoder = zlib.decompressobj()
        result = decoder.decompress(raw, bound + 1)
        if len(result) > bound or not decoder.eof or decoder.unused_data:
            raise ValueError('Compressed generated chunk exceeds its bound or is incomplete')
        return result
    if mode == 1:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            result = stream.read(bound + 1)
    else:
        result = raw
    if len(result) > bound:
        raise ValueError('Generated chunk exceeds its decompression bound')
    return result


def audit_region(raw, rx, rz):
    """Generator-only content: 1024 chunks, known fields, no entities, players or block entities."""
    import nbtlib
    from region_expansion import records
    chunks = records(raw)
    if len(chunks) != 1024:
        raise ValueError('A complete 1024-chunk 512 m generated region is required')
    for slot, (record, _) in chunks.items():
        tag = nbtlib.File.parse(io.BytesIO(bounded_decompress(record[5:], record[4], 4 * 2**20)))
        if set(tag) - CHUNK_KEYS:
            raise ValueError('Unexpected generated chunk fields; refusing to publish')
        if (any(tag.get(k) for k in ('entities', 'block_entities', 'block_ticks', 'fluid_ticks')) or
                any(tag.get('structures', {}).values())):
            raise ValueError('Entity or non-generator content; refusing to publish')
        if (int(tag['xPos']), int(tag['zPos'])) != (rx * 32 + slot % 32, rz * 32 + slot // 32):
            raise ValueError('Generated chunk coordinates differ from the tile region')
        for section in tag.get('sections', []):
            if set(section) - {'Y', 'block_states', 'biomes', 'BlockLight', 'SkyLight'}:
                raise ValueError('Unexpected section data')
            for item in section.get('block_states', {}).get('palette', []):
                if set(item) - {'Name', 'Properties'} or not re.fullmatch(r'minecraft:[a-z0-9_]+', str(item['Name'])):
                    raise ValueError('Unexpected block palette')
    return len(chunks)


# --------------------------------------------------------------------------
# Portable receipts


class PathMap:
    """Rewrite local paths in receipts: exact published files first, then prefixes."""

    def __init__(self, exact=None, prefixes=None):
        self.exact = {}
        for path, target in (exact or {}).items():
            for form in self._forms(path):
                self.exact[form] = target
        pairs = []
        for path, target in (prefixes or {}).items():
            for form in self._forms(path):
                pairs.append((form, target))
        pairs.sort(key=lambda item: -len(item[0]))
        self.prefixes = dict(pairs)
        alternatives = '|'.join(re.escape(form) for form, _ in pairs)
        self.pattern = re.compile('(' + alternatives + r')(?:(/)|(?=$|[^\w.-]))') if pairs else None

    def _replace(self, match):
        target = self.prefixes[match.group(1)]
        if match.group(2):  # 'cache:' + 'terrain/x' ; 'gen:0_0' + '/sources'
            return target if target.endswith(':') else target + '/'
        return target

    @staticmethod
    def _forms(path):
        path = Path(path)
        forms = {str(path.absolute())}
        try:
            forms.add(str(path.resolve()))
        except OSError:
            pass
        return forms

    def text(self, value):
        if value in self.exact:
            return self.exact[value]
        if self.pattern is None:
            return value
        return self.pattern.sub(self._replace, value)

    def rewrite(self, value):
        if isinstance(value, dict):
            return {self.text(k) if isinstance(k, str) else k: self.rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.rewrite(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def assert_portable(value, name, home=None):
    """Refuse JSON that still carries an absolute local path (or the home directory)."""
    home = home or str(Path.home())

    def walk(item, where):
        if isinstance(item, dict):
            for key, child in item.items():
                walk(key, where)
                walk(child, f'{where}.{key}')
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f'{where}[{index}]')
        elif isinstance(item, str) and (LOCAL_PATH.search(item) or (len(home) > 1 and home in item)):
            raise ValueError(f'Local path left in {name} at {where}: {item[:120]!r}')
    walk(value, name)
    return value


# --------------------------------------------------------------------------
# Packaging


class Package:
    def __init__(self, tile, directory, items, manifest, receipts):
        self.tile, self.directory, self.items, self.manifest, self.receipts = tile, directory, items, manifest, receipts

    @property
    def bytes(self):
        return sum(item['bytes'] for item in self.items)


def _place(source, target):
    """Hard-link (same volume, no second copy) or copy one generated file into the package."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def package_tile(tile, root, *, receipts, delivered=None, lq=None, plan_sha256, frame_sha256, generator,
                 prefixes=None, baselines=(), notes=None):
    """Write tiles/v2/<tile>/ under root/package and return the Package (nothing uploaded yet).

    receipts: {stage: receipt dict as generated}. delivered: world directory
    whose region is installed (None when the pass changes nothing installable).
    lq: the LQ world of the same pass, published as the upgrade baseline when
    the delivered quality is a scan quality.
    """
    tile_id = tile['id']
    rx, rz = tile_coords(tile_id)
    name = region_name(tile_id)
    base = tile_prefix(tile_id)
    package = Path(root) / 'package'
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    worlds = [('', Path(delivered).resolve())] if delivered else []
    quality = 'LQ'
    appearance = receipts.get('appearance') or {}
    if delivered and appearance.get('world') and Path(appearance['world']).resolve() == worlds[0][1]:
        quality = appearance['quality']
    if quality not in QUALITIES:
        raise ValueError(f'Unknown delivered quality {quality!r}')
    if lq and quality != 'LQ':
        worlds.append(('lq/', Path(lq).resolve()))
    exact = {}
    for sub, world in worlds:
        exact[world] = base + ('/' + sub.rstrip('/') if sub else '')
        exact[world / 'region' / name] = f'{base}/{sub}region/{name}'
        exact[world / 'earthcraft.json'] = f'{base}/{sub}earthcraft.json'
    mapping = PathMap(exact, {Path(root): f'gen:{tile_id}', **(prefixes or {})})
    items, manifests, files = [], {}, []

    def add(relative, local, role):
        digest = sha_file(local)
        size = Path(local).stat().st_size
        path = f'{base}/{relative}'
        items.append({'path': path, 'local': str(local), 'sha256': digest, 'bytes': size, 'role': role})
        files.append({'path': path, 'bytes': size, 'sha256': digest, 'role': role})
        return digest

    for sub, world in worlds:
        source = world / 'region' / name
        others = sorted(p.name for p in (world / 'region').glob('r.*.*.mca') if p.name != name)
        if not source.is_file() or others:
            raise ValueError(f'{world.name}: exactly one region {name} is required')
        raw = source.read_bytes()
        audit_region(raw, rx, rz)
        target = package / sub / 'region' / name
        _place(source, target)
        if sha_file(target) != sha_bytes(raw):
            raise ValueError('Packaged region differs from the verified generated region')
        add(f'{sub}region/{name}', target, 'lq-region' if sub else 'region')
        generated = (world / 'earthcraft.json').read_bytes()
        published = canonical(assert_portable(mapping.rewrite(json.loads(generated)), f'{sub}earthcraft.json'))
        manifest_path = package / sub / 'earthcraft.json'
        manifest_path.write_bytes(published)
        manifests[world] = {'generated': sha_bytes(generated), 'published': add(f'{sub}earthcraft.json', manifest_path,
                                                                             'lq-world-manifest' if sub else 'world-manifest')}
    stage_records = {}
    for stage in STAGES:
        receipt = receipts.get(stage)
        if receipt is None:
            continue
        receipt = copy.deepcopy(receipt)
        world = Path(receipt['world']).resolve() if receipt.get('world') else None
        if world is not None and world in manifests:
            receipt['generated_world_manifest_sha256'] = receipt.get('world_manifest_sha256')
            receipt['world_manifest_sha256'] = manifests[world]['published']
        receipt = assert_portable(mapping.rewrite(receipt), f'receipts/{stage}.json')
        raw = canonical(receipt)
        path = package / 'receipts' / f'{stage}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        digest = add(f'receipts/{stage}.json', path, 'receipt')
        stage_records[stage] = {'receipt': f'{base}/receipts/{stage}.json', 'sha256': digest,
                                'result': receipt.get('result'), 'quality': receipt.get('quality')}
    delivered_receipt = None
    if delivered:
        delivered_receipt = 'appearance' if quality != 'LQ' else 'geometry'
    manifest = {
        'schema': TILE_SCHEMA, 'tile': tile_id, 'region': {'x': rx, 'z': rz, 'file': name},
        'world_offset_xz': [rx * 512, rz * 512], 'size_m': tile.get('size', 512), 'metres_per_block': 1,
        'quality': quality if delivered else None, 'installable': bool(delivered),
        'delivered_receipt': delivered_receipt, 'stages': stage_records, 'files': files,
        'baselines': list(baselines) + ([{'kind': 'tiles/v2-lq', 'region': f'{base}/lq/region/{name}',
                                           'manifest': f'{base}/lq/earthcraft.json'}]
                                         if any(sub for sub, _ in worlds) else []),
        'plan_sha256': plan_sha256, 'frame_sha256': frame_sha256, 'generator': generator,
        'attribution': ATTRIBUTION, 'path_conventions': PATH_CONVENTIONS,
        'privacy': 'Generator-only regions: no players, entities, block entities or scheduled ticks (audited).',
        'notes': notes or []}
    raw = canonical(assert_portable(mapping.rewrite(manifest), 'tile.json'))
    (package / 'tile.json').write_bytes(raw)
    digest = sha_bytes(raw)
    items.append({'path': f'{base}/tile.json', 'local': str(package / 'tile.json'), 'sha256': digest,
                  'bytes': len(raw), 'role': 'tile'})
    return Package(tile_id, package, items, manifest, stage_records)


def receipt_bytes(package, stage):
    return (package.directory / 'receipts' / f'{stage}.json').read_bytes()
