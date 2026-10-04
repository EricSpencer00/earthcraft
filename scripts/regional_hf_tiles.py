"""Archive verified generator regions for HF without personal save contents.

Runs on the existing generation host. Reads completed jobs only. Explicitly
excludes player state, entities, block entities, resource packs and local paths.
An archive is an immutable generated input, never a replacement for user edits.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import tarfile
import zlib

import nbtlib
from region_expansion import records, sha, atomic
from regional_store import region_bytes

SCHEMA = 'earthcraft.generated-tile-shard.v1'
CHUNK_KEYS = {'Status', 'InhabitedTime', 'DataVersion', 'zPos', 'isLightOn',
              'Heightmaps', 'structures', 'yPos', 'xPos', 'entities',
              'block_entities', 'PostProcessing', 'LastUpdate', 'sections',
              'block_ticks', 'fluid_ticks'}
ATTRIBUTION = [
    {'source': 'OpenStreetMap contributors', 'license': 'ODbL-1.0',
     'url': 'https://www.openstreetmap.org/copyright'},
    {'source': 'USGS 3DEP', 'license': 'US Government Public Domain',
     'url': 'https://registry.opendata.aws/usgs-lidar/'},
    {'source': 'Illinois Height Modernization / county surveys',
     'url': 'https://clearinghouse.isgs.illinois.edu/node/495',
     'license': 'Source metadata retained locally; attribution required'},
    {'source': 'Terrain Tiles',
     'url': 'https://github.com/tilezen/joerd/blob/master/docs/attribution.md',
     'license': 'Per upstream terrain source'},
    {'source': 'USDA NAIP', 'license': 'US Government Public Domain',
     'url': 'https://www.fsa.usda.gov/resources/programs/naip-imagery'}]


def audit_region(raw, rx, rz):
    chunks = records(raw)
    for slot, (record, _) in chunks.items():
        payload = record[5:]
        decoded = (zlib.decompress(payload) if record[4] == 2 else
                   gzip.decompress(payload) if record[4] == 1 else payload)
        tag = nbtlib.File.parse(io.BytesIO(decoded))
        if set(tag) - CHUNK_KEYS:
            raise ValueError('Unexpected generated chunk fields; no export')
        if any(tag.get(k) for k in ('entities', 'block_entities', 'structures', 'block_ticks', 'fluid_ticks')):
            # Empty structures dictionaries with empty Starts/References are normal.
            if (any(tag.get(k) for k in ('entities', 'block_entities', 'block_ticks', 'fluid_ticks')) or
                    any(tag.get('structures', {}).values())):
                raise ValueError('Entity or non-generator content; no export')
        if (int(tag['xPos']), int(tag['zPos'])) != (rx * 32 + slot % 32, rz * 32 + slot // 32):
            raise ValueError('Generated region coordinates differ')
        for section in tag.get('sections', []):
            if set(section) - {'Y', 'block_states', 'biomes', 'BlockLight', 'SkyLight'}:
                raise ValueError('Unexpected section data')
            for item in section.get('block_states', {}).get('palette', []):
                if set(item) - {'Name', 'Properties'} or not re.fullmatch(r'minecraft:[a-z0-9_]+', str(item['Name'])):
                    raise ValueError('Unexpected block palette')
    return len(chunks)


def public_tile(tile, evidence, bulk, frame):
    if not re.fullmatch(r'-?\d+_-?\d+', tile):
        raise ValueError('Invalid tile identity')
    rx, rz = map(int, tile.split('_'))
    proof = json.loads(evidence.read_text())
    root = (bulk / 'tiles' / tile).resolve()
    world = Path(proof['world'])
    try:
        relative = world.resolve().relative_to(root)
    except ValueError:
        relative = Path('outside')
    cloud = (len(relative.parts) == 3 and relative.parts[0] == 'cloud' and
             re.fullmatch(r'aws-scan-\d+', relative.parts[1]) and relative.parts[2] == 'world')
    if world.is_symlink() or not (world.resolve().parent == root or cloud):
        raise ValueError('Export only task-generated tiles, never a personal save')
    if any(p.is_symlink() for p in [world, *world.parents]):
        raise ValueError('Generator path symlink forbidden')
    meta = json.loads((world / 'earthcraft.json').read_text())
    if (proof.get('tile') != tile or proof.get('result') != 'pass' or
            proof.get('quality') not in ('LQ', 'scan-roof', 'scan-points-and-roof') or
            proof.get('world_manifest_sha256') != sha(world / 'earthcraft.json') or
            meta.get('world_frame') != frame or meta.get('world_offset_xz') != [rx*512, rz*512] or
            meta.get('source', {}).get('size') != 512):
        raise ValueError('Verified fixed-frame generated tile required')
    name = f'r.{rx}.{rz}.mca'
    if set(proof['regions']) != {name}:
        raise ValueError('One exact 512 metre region per tile required')
    path = world / 'region' / name
    if path.is_symlink() or path.with_suffix('.mca.gz').is_symlink():
        raise ValueError('Region symlink forbidden')
    raw = region_bytes(path)
    checksum = hashlib.sha256(raw).hexdigest()
    if proof['regions'][name] != checksum:
        raise ValueError('Generated region hash changed')
    chunks = audit_region(raw, rx, rz)
    source = meta['source']
    scan = proof.get('scan_source') or {}
    # Only primitive geographic facts are selected from local source receipts.
    record = {'tile': tile, 'quality': proof['quality'], 'world_offset_xz': [rx*512, rz*512],
              'size_m': 512, 'metres_per_block': 1, 'receipt_sha256': sha(evidence),
              'region_sha256': checksum, 'region_file': name, 'chunks': chunks,
              'source_grid': {k: source[k] for k in ('west', 'north', 'size')},
              'scan': {k: scan[k] for k in ('county', 'year') if k in scan},
              'roof_source_cells': int(proof.get('roof_source_cells', 0)),
              'real_facade_photo_colors_applied': 0, 'client_visual_verified': False,
              'privacy_audit': 'Generator-only region; no entities, block entities, players or local metadata'}
    return record, gzip.compress(raw, compresslevel=1, mtime=0)


def export(control, bulk, output, known, limit=8):
    if not 1 <= limit <= 32:
        raise ValueError('Bounded export batch required')
    control, bulk, output = map(Path, (control, bulk, output))
    plan_path = control / 'plan.json'
    plan = json.loads(plan_path.read_text())
    frame = plan['frame']
    with sqlite3.connect('file:'+str(control/'jobs.sqlite')+'?mode=ro', uri=True) as db:
        rows = db.execute("SELECT tile,stage,evidence,evidence_sha256 FROM jobs WHERE state='complete' AND stage IN (1,2) ORDER BY priority,tile,stage DESC").fetchall()
    selected, seen = [], set()
    for tile, stage, path, checksum in rows:
        if tile in seen:
            continue
        # The immutable remote shard already proves this receipt identity.
        # Avoid re-reading every previously archived receipt on each batch;
        # a new scan upgrade has a different identity and is fully audited.
        if known.get(tile) == checksum:
            seen.add(tile)
            continue
        evidence = Path(path)
        if sha(evidence) != checksum:
            raise ValueError('Completed receipt changed')
        proof = json.loads(evidence.read_text())
        if stage == 2 and proof.get('quality') == 'LQ':
            continue
        seen.add(tile)
        selected.append((tile, evidence))
        if len(selected) == limit:
            break
    if not selected:
        return {'state': 'caught_up', 'tiles': 0}
    entries, payloads = [], []
    for tile, evidence in selected:
        entry, data = public_tile(tile, evidence, bulk, frame)
        entries.append(entry); payloads.append((tile+'/'+entry['region_file']+'.gz', data))
        if sum(len(body) for _, body in payloads) > 256*2**20:
            raise ValueError('Public shard exceeds bounded transfer budget')
    manifest = {'schema': SCHEMA, 'frame': frame, 'plan_sha256': sha(plan_path),
                'entries': entries, 'attribution': ATTRIBUTION,
                'excludes': ['player state', 'personal save edits', 'entity regions', 'resource packs', 'source photos', 'credentials'],
                'note': 'Generated tiles only; incomplete coverage and incomplete facades. Restore separately and preserve existing edits.'}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        for name, data in [('manifest.json', json.dumps(manifest, sort_keys=True).encode()), *payloads]:
            item = tarfile.TarInfo(name); item.size = len(data); item.mode = 0o644
            tar.addfile(item, io.BytesIO(data))
    raw = out.getvalue(); checksum = hashlib.sha256(raw).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (checksum+'.tar')
    if archive.exists() and sha(archive) != checksum:
        raise ValueError('Immutable shard differs')
    if not archive.exists(): atomic(archive, raw)
    record = {'schema': SCHEMA, 'state': 'ready', 'archive': str(archive), 'sha256': checksum,
              'bytes': len(raw), 'tiles': len(entries), 'entries': entries,
              'plan_sha256': manifest['plan_sha256']}
    atomic(archive.with_suffix('.local.json'), json.dumps(record).encode())
    return record


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for k in ('control', 'bulk', 'output'): p.add_argument('--'+k, type=Path, required=True)
    p.add_argument('--limit', type=int, default=8)
    a = p.parse_args()
    import sys
    print(json.dumps(export(a.control, a.bulk, a.output, json.loads(sys.stdin.readline()), a.limit)))
