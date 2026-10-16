"""Resume a deterministic building rerun without overwriting candidates."""
import argparse
import json
from pathlib import Path
import time

from building_layer import verify
from building_rerun import (
    build,
    load_and_check,
    region_hashes,
    sha,
    write_json,
)


def finalize_existing(record, manifest):
    candidate = Path(record['candidate_world'])
    receipt_path = candidate.parent / 'building-rerun-receipt.json'
    if receipt_path.exists():
        return json.loads(receipt_path.read_text())
    if not (candidate / 'earthcraft.json').is_file():
        raise ValueError(f"{record['tile']}: partial candidate has no earthcraft.json")
    started = time.monotonic()
    checks = verify(candidate)
    layer = json.loads((candidate / 'building-layer.json').read_text())
    if layer.get('llm_used') is not False or layer.get('source_world') != record['base_world']:
        raise ValueError(f"{record['tile']}: candidate provenance is invalid")
    receipt = {
        'schema': 'building-rerun-receipt-v1',
        'tile': record['tile'],
        'result': 'pass',
        'code_revision': manifest['code_revision'],
        'code_files_sha256': manifest['code_files_sha256'],
        'baseline': record,
        'candidate_world': str(candidate),
        'candidate_manifest_sha256': sha(candidate / 'building-layer.json'),
        'candidate_region_sha256': region_hashes(candidate),
        'checks': checks,
        'building_count': len(layer.get('buildings', [])),
        'derived_cells': checks.get('derived_building_cells', 0),
        'appearance_cells': layer.get('appearance_cells', 0),
        'llm_used': False,
        'installed': False,
        'seconds': time.monotonic() - started,
    }
    write_json(receipt_path, receipt)
    print(f"RESUMED {record['tile']} buildings={receipt['building_count']} "
          f"derived={receipt['derived_cells']}", flush=True)
    return receipt


def resume(manifest_path, root):
    manifest = json.loads(Path(manifest_path).read_text())
    load_and_check(manifest, root)
    completed = []
    remaining = []
    for record in manifest['tiles']:
        candidate = Path(record['candidate_world'])
        if (candidate.parent / 'building-rerun-receipt.json').exists():
            completed.append(json.loads((candidate.parent / 'building-rerun-receipt.json').read_text()))
        elif candidate.exists():
            completed.append(finalize_existing(record, manifest))
        else:
            remaining.append(record)
    if remaining:
        partial = dict(manifest, tiles=remaining)
        load_and_check(partial, root)
        build(partial, root)
        completed.extend(
            json.loads((Path(record['candidate_world']).parent / 'building-rerun-receipt.json').read_text())
            for record in remaining
        )
    result = dict(manifest)
    result.update(
        state='complete', completed_tiles=len(completed),
        completed_buildings=sum(item['building_count'] for item in completed),
        derived_cells=sum(item['derived_cells'] for item in completed),
        completed_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    )
    write_json(Path(manifest['output_root']) / 'manifest.completed.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    value = resume(args.manifest, args.root.resolve())
    print(json.dumps({
        'state': value['state'],
        'tiles': value['completed_tiles'],
        'buildings': value['completed_buildings'],
        'derived_cells': value['derived_cells'],
    }, indent=2))
