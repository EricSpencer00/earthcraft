"""Fingerprint frozen inputs and canonical Minecraft block states, without inference."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform

import numpy as np

from inspect_world import chunks
from verify_metric_world import unpack
from world_templates import height_template


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def files_snapshot(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'Missing frozen input directory: {root}')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Frozen input must not be a symlink: {path}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_hash(path)
    if not result:
        raise ValueError(f'Empty frozen input directory: {root}')
    return result


def input_snapshot(root, region, source, points, photo_world=None):
    """Include hidden template/texture dependencies as well as acquired data.

    Hashes identify bytes, not source accuracy. Input paths are retained in the
    config for replay; generated save metadata is deliberately not an input.
    """
    template = root / 'worlds/chicago-water-tower-64/Arnis World 1'
    height = height_template(root)
    client = Path.home() / 'Library/Application Support/minecraft/versions/1.21.10/1.21.10.jar'
    return {
        'schema_version': 1,
        'region': region,
        'source': files_snapshot(source),
        'points': files_snapshot(points) if points else None,
        'photo_layer': files_snapshot(photo_world) if photo_world else None,
        'templates': {name: file_hash(template / name) for name in
                      ('level.dat', 'region/r.0.0.mca')},
        'height_pack': files_snapshot(height),
        'client_jar_sha256': file_hash(client),
        'pipeline_code': {p.name: file_hash(p) for p in sorted((root / 'scripts').glob('*.py'))},
        'runtime': {'python': platform.python_version(), 'machine': platform.machine(),
                    'packages': {name: version(name) for name in
                                 ('numpy', 'nbtlib', 'rasterio', 'pyproj', 'shapely', 'Pillow')}},
        'llm_inference_used': False,
    }


def canonical_section(section):
    """Normalize palette order and unused entries while retaining state properties."""
    states = section['block_states']
    labels = [json.dumps({'Name': str(p['Name']),
                          'Properties': {str(k): str(v) for k, v in p.get('Properties', {}).items()}},
                         sort_keys=True, separators=(',', ':')) for p in states['palette']]
    if not labels:
        raise ValueError('Empty block palette')
    indices = (np.zeros(4096, dtype=int) if len(labels) == 1 else
               unpack(states['data'], max(4, (len(labels) - 1).bit_length()), 4096))
    if indices.max() >= len(labels):
        raise ValueError('Block palette index outside palette')
    used = sorted({labels[i] for i in np.unique(indices)})
    mapping = {label: i for i, label in enumerate(used)}
    values = np.array([mapping.get(label, 0) for label in labels], dtype='<u4')[indices]
    block_hash = hashlib.sha256(json.dumps(used, separators=(',', ':')).encode())
    block_hash.update(values.tobytes())
    return block_hash.hexdigest()


def world_payload(world):
    """Geographic block identity; not entity, biome, lighting or gameplay equality."""
    records = {}
    for path in sorted((world / 'region').glob('r.*.*.mca')):
        for _, tag, _ in chunks(path):
            key = f"{int(tag['xPos'])},{int(tag['zPos'])}"
            if key in records:
                raise ValueError(f'Duplicate chunk {key}')
            sections = {}
            for section in tag['sections']:
                sy = str(int(section['Y']))
                if sy in sections:
                    raise ValueError(f'Duplicate section {key}/{sy}')
                sections[sy] = canonical_section(section)
            records[key] = sections
    if not records:
        raise ValueError('World has no populated chunks')
    report = json.loads((world / 'earthcraft.json').read_text())
    chart = {k: report['source'][k] for k in ('crs', 'west', 'north', 'size')}
    transform = {k: report[k] for k in ('vertical_offset_m', 'dimension_min_y', 'dimension_height')}
    transform['world_offset_xz']=report.get('world_offset_xz',[0,0])
    result = {'chart': chart, 'transform': transform, 'chunks': records}
    return {'sha256': digest(result), 'chunk_count': len(records),
            'scope': 'Decoded block names/properties and local metric chart; excludes save timestamps, lighting, entities and biomes',
            'payload': result}


def compare_worlds(first, second):
    left, right = world_payload(first), world_payload(second)
    changed = sorted(k for k in left['payload']['chunks'].keys() | right['payload']['chunks'].keys()
                     if left['payload']['chunks'].get(k) != right['payload']['chunks'].get(k))
    result = {'equal': left['sha256'] == right['sha256'],
              'first_sha256': left['sha256'], 'second_sha256': right['sha256'],
              'chunk_count': left['chunk_count'], 'changed_chunks': changed,
              'scope': left['scope']}
    return result
