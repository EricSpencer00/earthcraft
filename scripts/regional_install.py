"""Install verified regional chunks into the closed existing Earthcraft save.

Existing chunks win by default. A scan upgrade needs the exact immutable LQ
record (including its timestamp); any mismatch keeps the entire player chunk.
Each touched region has a verified private backup and an atomic replacement.
"""
import argparse
import fcntl
import json
import gzip
import hashlib
import io
from pathlib import Path
import shutil
import time
import zlib
from functools import lru_cache

import nbtlib
import numpy as np

from region_expansion import atomic, records, encode, sha
from regional_store import region_bytes


def baseline_is_extrusion(prior):
    source=prior.get('source',{})
    observed=prior.get('lidar_surface_source') or prior.get('point_geometry_source')
    mapped=source.get('building_source_kind')=='osm-presentation'
    county=source.get('county_source') and source.get('county_sha256')
    return bool(not observed and (mapped or county))


@lru_cache(maxsize=2048)
def geometry_signature(record):
    """Ignore game maintenance fields, retaining every occupied block/property.

    Light, data version and palette packing change when vanilla saves a chunk.
    A resaved generated chunk is eligible only if its entire block volume is
    unchanged and it has no entities, block entities or scheduled block ticks.
    """
    raw = record[0]
    payload = zlib.decompress(raw[5:]) if raw[4] == 2 else gzip.decompress(raw[5:]) if raw[4] == 1 else raw[5:]
    tag = nbtlib.File.parse(io.BytesIO(payload))
    if any(tag.get(key) for key in ('block_entities','TileEntities','entities','block_ticks','fluid_ticks')):
        return None
    sections = [];biomes=[]
    from verify_metric_world import unpack
    air = {'minecraft:air','minecraft:cave_air','minecraft:void_air'}
    for section in tag.get('sections', []):
        if 'biomes' in section:
            state=section['biomes'];palette=state['palette']
            if any(str(name)!='minecraft:plains' for name in palette):
                values=np.zeros(64,dtype=int) if len(palette)==1 else unpack(state['data'],max(1,(len(palette)-1).bit_length()),64)
                names=[str(palette[int(index)]) for index in values]
                if any(name!='minecraft:plains' for name in names):biomes.append((int(section['Y']),names))
        if 'block_states' not in section: continue
        state = section['block_states']; palette = state['palette']
        if all(str(item['Name']) in air for item in palette):continue
        labels = []
        for item in palette:
            normalized = item.unpack()
            defaults = {'minecraft:grass_block':{'snowy':'false'}, 'minecraft:water':{'level':'0'}}
            properties = dict(defaults.get(str(item['Name']), {}))
            properties.update(normalized.get('Properties', {}))
            if properties:normalized['Properties'] = properties
            labels.append(json.dumps(normalized,sort_keys=True,separators=(',',':')))
        values = np.zeros(4096,dtype=int) if len(palette)==1 else unpack(state['data'],max(4,(len(palette)-1).bit_length()),4096)
        occupied = np.array([str(item['Name']) not in air for item in palette])[values]
        if not occupied.any(): continue
        # Canonical palette numbering makes vanilla palette reordering harmless.
        ordered = sorted(set(labels[index] for index in np.unique(values)))
        mapping = {label:i for i,label in enumerate(ordered)}
        canonical = np.array([mapping.get(label,0) for label in labels],dtype='<u2')[values]
        sections.append((int(section['Y']),ordered,hashlib.sha256(canonical.tobytes()).hexdigest()))
    return hashlib.sha256(json.dumps([sorted(sections),sorted(biomes)],separators=(',',':')).encode()).hexdigest()


def unchanged_generated_record(current, baseline):
    if current == baseline:return True
    actual = geometry_signature(current)
    return actual is not None and actual == geometry_signature(baseline)


def quality_merge(current, incoming, expected=None, compare_geometry=False):
    merged = dict(current)
    added = upgraded = conflicts = 0
    for slot, record in incoming.items():
        if slot not in current:
            merged[slot] = record; added += 1
        elif expected is not None and slot in expected and any(current[slot] == baseline or
                (compare_geometry and unchanged_generated_record(current[slot], baseline))
                for baseline in (expected[slot] if isinstance(expected[slot],list) else [expected[slot]])):
            merged[slot] = record; upgraded += 1
        else:
            conflicts += 1
    return merged, {'added_chunks': added, 'upgraded_chunks': upgraded,
                    'existing_or_edited_chunks_preserved': conflicts}


def install(world, candidate, receipt_path, backups, expected=None):
    world, candidate, backups = map(Path, (world, candidate, backups))
    receipt_path = Path(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('result') != 'pass' or not receipt.get('checks'):
        raise ValueError('Verified geometry receipt required')
    if sha(candidate/'earthcraft.json') != receipt['world_manifest_sha256']:
        raise ValueError('Verified tile manifest changed')
    actual = json.loads((candidate/'earthcraft.json').read_text())
    installed = json.loads((world/'city-coverage.json').read_text())
    if actual['world_frame'] != installed['frame']:
        raise ValueError('Tile uses another coordinate frame')
    baselines=[] if expected is None else [Path(expected)] if isinstance(expected,(str,Path)) else list(map(Path,expected))
    expected_regions={}
    if baselines:
        for baseline in baselines:
            baseline_receipt = json.loads((baseline.parent/'geometry-receipt.json').read_text())
            if sha(baseline/'earthcraft.json') != baseline_receipt['world_manifest_sha256']:
                raise ValueError('Immutable LQ baseline differs from its verified generation receipt')
            prior = json.loads((baseline/'earthcraft.json').read_text())
            if prior['world_frame'] != actual['world_frame']:
                raise ValueError('Upgrade baseline uses another frame')
            if not baseline_is_extrusion(prior):
                raise ValueError('Only immutable mapped height extrusions may be upgraded')
            for name,checksum in baseline_receipt['regions'].items():
                raw=region_bytes(baseline/'region'/name)
                if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Immutable baseline region changed')
                merged=expected_regions.setdefault(name,{})
                for slot,value in records(raw).items():
                    choices=merged.setdefault(slot,[])
                    if value not in choices:choices.append(value)
        if receipt.get('quality') not in ('scan-roof', 'scan-points-and-roof', 'classified-scan-points'):
            raise ValueError('An LQ candidate cannot overwrite existing geometry')
    backup = backups/(str(time.time_ns())+'-'+receipt['tile'])
    backup.mkdir(parents=True)
    report = {'tile': receipt['tile'], 'quality': receipt['quality'], 'source_receipt_sha256': sha(receipt_path),
              'added_chunks': 0, 'upgraded_chunks': 0, 'existing_or_edited_chunks_preserved': 0,
              'regions': {}, 'backup': str(backup.resolve()), 'installed': False}
    with (world/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, checksum in receipt['regions'].items():
            source = candidate/'region'/name
            incoming_bytes=region_bytes(source)
            if hashlib.sha256(incoming_bytes).hexdigest() != checksum:
                raise ValueError('Verified source region changed')
            target = world/'region'/name
            original_bytes = target.read_bytes() if target.exists() else b''
            original = records(original_bytes)
            expected_records = expected_regions.get(name)
            merged, counts = quality_merge(original, records(incoming_bytes), expected_records, compare_geometry=True)
            if counts['added_chunks'] or counts['upgraded_chunks']:
                if original_bytes:
                    atomic(backup/name, original_bytes)
                    if (backup/name).read_bytes() != original_bytes:
                        raise ValueError('Pre-update region backup differs')
                if (target.read_bytes() if target.exists() else b'') != original_bytes:
                    raise ValueError('Player region changed inside the session fence')
                atomic(target, encode(merged))
                if records(target.read_bytes()) != merged:
                    raise ValueError('Installed region differs from merged records')
            report['regions'][name] = counts
            for key in counts: report[key] += counts[key]
        report['installed'] = True
        # Record actual delivered quality separately from the frozen generator
        # plan. A partly protected tile never claims all its chunks were upgraded.
        quality_path=world/'regional-quality.json'
        quality=json.loads(quality_path.read_text()) if quality_path.exists() else {'frame':actual['world_frame'],'tiles':{}}
        identity=str(actual['source']['size'])+':'+receipt['tile']
        quality['tiles'][identity]={key:report[key] for key in ('tile','quality','added_chunks','upgraded_chunks','existing_or_edited_chunks_preserved','source_receipt_sha256')}
        atomic(quality_path,json.dumps(quality,indent=2).encode())
        if report['added_chunks']:
            installed=json.loads((world/'city-coverage.json').read_text())
            installed['tiles']['regional-'+identity]={'id':receipt['tile'],
                'world_offset_xz':actual['world_offset_xz'],'size_m':actual['source']['size'],
                'status':'geometry_verified_existing_chunks_preserved'}
            atomic(world/'city-coverage.json',json.dumps(installed,indent=2).encode())
            from world_border import update_world_border,bounds_for_tiles
            for path in (world/'level.dat',world/'data/world_border.dat'):
                if path.exists():atomic(backup/path.name,path.read_bytes())
            update_world_border(world,bounds_for_tiles(installed['tiles'].values()))
        atomic(backup/'installation.json', json.dumps(report, indent=2).encode())
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--backups', type=Path, required=True)
    parser.add_argument('--expected', type=Path,action='append')
    args = parser.parse_args()
    print(json.dumps(install(args.world,args.candidate,args.receipt,args.backups,args.expected),indent=2))
