"""Apply one or more street-detail layers to COPIES of region files.

    PYTHONPATH=scripts python scripts/detail_apply.py \
        --regions COPY/region --layer detail/trees --layer detail/street \
        --out STAGED/region --max-bytes 300000000

Never writes to the input directory or to any directory of a Minecraft save
(a directory whose parent holds ``level.dat`` or ``session.lock``). Only
regions with edits are written, as new files; untouched chunks keep their
original compressed bytes. Each edit's precondition (cell is air, or holds the
block the layer was computed from) is checked against the copy; failed
preconditions are skipped and counted, so player edits made after the layer
was computed survive. Layers apply in the order given. A receipt with input
and output hashes and per-kind counts is written next to the output, then the
output is re-read and verified (``verify_region``).
"""
import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import nbtlib as n

from detail_anvil import (AIR_LIKE, ChunkVolume, block_bits, block_name, encode_region,
                          read_region, unpack)
from detail_guard import WriteGuard
from detail_layer import load_manifest, read_region_edits


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def refuse_save(path):
    path = Path(path).resolve()
    for parent in (path, *path.parents):
        if (parent / 'session.lock').exists() or (parent / 'level.dat').exists():
            raise PermissionError(f'{path} is inside a Minecraft save ({parent}); write to a copy elsewhere')


def apply(regions, layers, out, guard):
    regions, out = Path(regions).resolve(), Path(out).resolve()
    if regions == out or regions in out.parents:
        raise PermissionError('Output must be a new directory outside the input region directory')
    refuse_save(out)
    manifests = [(Path(layer), load_manifest(layer)) for layer in layers]
    names = sorted({name for _, m in manifests for name in m['regions']})
    receipt = {'schema': 'earthcraft.detail-apply.v1', 'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'layers': [{'path': str(p), 'layer': m['layer'], 'manifest_sha256': sha(p / 'manifest.json')}
                          for p, m in manifests], 'regions': {}}
    for name in names:
        source = regions / f'{name}.mca'
        if not source.exists():
            receipt['regions'][name] = {'skipped': 'input region absent'}
            continue
        before_hash = sha(source)
        chunks = read_region(source)
        stats = {'applied': 0, 'unchanged': 0, 'precondition_failed': 0, 'missing_chunk': 0, 'by_kind': {}}
        volumes = {}
        for path, manifest in manifests:
            entry = manifest['regions'].get(name)
            if entry is None:
                continue
            sources = manifest['sources']
            for _, (cx, cz), edits in read_region_edits(path / entry['path']):
                record = chunks.get((cx, cz))
                if record is None:
                    stats['missing_chunk'] += len(edits)
                    continue
                volume = volumes.setdefault((cx, cz), ChunkVolume(record['tag']))
                for lx, y, lz, state, expect, src in edits:
                    current = volume.get(lx, y, lz)
                    ok = (block_name(current) in AIR_LIKE) if expect is None else (block_name(current) == block_name(expect))
                    if current == state:
                        stats['unchanged'] += 1
                        continue
                    if not ok:
                        stats['precondition_failed'] += 1
                        continue
                    volume.set(lx, y, lz, state)
                    stats['applied'] += 1
                    kind = sources[src]['kind']
                    stats['by_kind'][kind] = stats['by_kind'].get(kind, 0) + 1
        changed = 0
        for key, volume in volumes.items():
            if volume.flush():
                chunks[key]['changed'] = True
                changed += 1
        if sha(source) != before_hash:
            raise RuntimeError(f'{source} changed while it was being read; use a frozen copy')
        target = out / f'{name}.mca'
        guard.write_bytes(target, encode_region(chunks))
        problems = verify_region(target, expected_chunks=set(chunks))
        if problems:
            raise ValueError(f'{target} failed verification: {problems[:5]}')
        stats.update({'chunks_changed': changed, 'input_sha256': before_hash, 'output_sha256': sha(target),
                      'output_bytes': target.stat().st_size, 'verified': True})
        receipt['regions'][name] = stats
    receipt['finished'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    receipt['totals'] = {k: sum(r.get(k, 0) for r in receipt['regions'].values())
                         for k in ('applied', 'unchanged', 'precondition_failed', 'missing_chunk', 'chunks_changed')}
    guard.write_text(out / 'detail-apply-receipt.json', json.dumps(receipt, indent=1))
    return receipt


def verify_region(path, expected_chunks=None, data_version=None):
    """Structural 1.21.x checks: NBT parses, palettes/packed longs/heightmaps are consistent."""
    problems = []
    chunks = read_region(path)
    if expected_chunks is not None and set(chunks) != set(expected_chunks):
        problems.append('chunk set differs from input')
    for (cx, cz), record in chunks.items():
        tag = record['tag']
        where = f'{Path(path).name}:{cx},{cz}'
        for key in ('DataVersion', 'xPos', 'zPos', 'yPos', 'Status', 'sections', 'Heightmaps'):
            if key not in tag:
                problems.append(f'{where} missing {key}')
        if data_version is not None and int(tag['DataVersion']) != data_version:
            problems.append(f'{where} DataVersion {int(tag["DataVersion"])}')
        ys = [int(s['Y']) for s in tag['sections']]
        if len(ys) != len(set(ys)):
            problems.append(f'{where} duplicate section Y')
        height = 16 * len([s for s in tag['sections'] if 'block_states' in s])
        for section in tag['sections']:
            if 'block_states' not in section:
                continue
            states = section['block_states']
            palette = states['palette']
            if not len(palette):
                problems.append(f'{where} empty palette'); continue
            for entry in palette:
                if not isinstance(entry.get('Name'), n.String) or ':' not in str(entry['Name']):
                    problems.append(f'{where} bad palette Name')
                props = entry.get('Properties')
                if props is not None and not all(isinstance(v, n.String) for v in props.values()):
                    problems.append(f'{where} non-string property')
            if len(palette) == 1:
                if 'data' in states:
                    problems.append(f'{where} single palette with data')
                continue
            bits = block_bits(len(palette))
            words = states.get('data')
            if not isinstance(words, n.LongArray):
                problems.append(f'{where} data is not a LongArray'); continue
            if len(words) != math.ceil(4096 / (64 // bits)):
                problems.append(f'{where} packed length {len(words)} for {bits} bits'); continue
            values = unpack(words, bits, 4096)
            if values.max() >= len(palette):
                problems.append(f'{where} index outside palette')
            light = [section.get(k) for k in ('BlockLight', 'SkyLight')]
            for array in light:
                if array is not None and len(array) != 2048:
                    problems.append(f'{where} light array length')
        hbits = height.bit_length()
        for name, words in tag['Heightmaps'].items():
            if len(words) != math.ceil(256 / (64 // hbits)):
                problems.append(f'{where} heightmap {name} length {len(words)}')
            elif unpack(words, hbits, 256).max() > height:
                problems.append(f'{where} heightmap {name} above dimension')
    return problems


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--regions', type=Path, required=True, help='Directory of COPIED r.X.Z.mca files (read only)')
    p.add_argument('--layer', type=Path, action='append', required=True)
    p.add_argument('--out', type=Path, required=True, help='New directory for edited region copies')
    p.add_argument('--max-bytes', type=int, default=300 * 10**6)
    p.add_argument('--free-floor', type=int, default=15 * 2**30)
    a = p.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        p.error('Output directory must be new or empty')
    guard = WriteGuard(a.out, a.max_bytes, a.free_floor)
    receipt = apply(a.regions, a.layer, a.out, guard)
    print(json.dumps(receipt['totals'], indent=1))


if __name__ == '__main__':
    main()
