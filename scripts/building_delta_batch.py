"""Stage a receipt-bound batch of deterministic building deltas.

Candidate worlds are immutable, separate from the live world, and already
verified individually.  This adapter binds each candidate back to its exact
rerun receipt before producing a per-tile compare-and-set stage.  It never
opens a Minecraft save or submits anything to the game inbox.
"""
import argparse
import hashlib
import json
from pathlib import Path

from building_delta import stage
from live_city import atomic, sha


SCHEMA = 'building-delta-batch-v1'


def region_hashes(world):
    result = {}
    for path in sorted((Path(world) / 'region').glob('r.*.*.mca')):
        with path.open('rb') as stream:
            result[path.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def check_candidate(record):
    """Return the receipt-bound source and candidate paths for one tile."""
    candidate = Path(record['candidate_world']).resolve()
    receipt_path = candidate.parent / 'building-rerun-receipt.json'
    if not receipt_path.is_file():
        raise ValueError(f"{record['tile']}: missing building candidate receipt")
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('schema') != 'building-rerun-receipt-v1' or
            receipt.get('result') != 'pass' or receipt.get('llm_used') is not False or
            receipt.get('installed') is not False):
        raise ValueError(f"{record['tile']}: candidate receipt is not an uninstalled deterministic pass")
    if receipt.get('baseline') != record or Path(receipt.get('candidate_world', '')).resolve() != candidate:
        raise ValueError(f"{record['tile']}: candidate receipt baseline differs from batch manifest")
    layer = candidate / 'building-layer.json'
    if not layer.is_file() or sha(layer) != receipt.get('candidate_manifest_sha256'):
        raise ValueError(f"{record['tile']}: candidate building manifest changed")
    if region_hashes(candidate) != receipt.get('candidate_region_sha256'):
        raise ValueError(f"{record['tile']}: candidate regions changed")
    return Path(record['base_world']).resolve(), candidate, receipt_path.resolve(), receipt


def stage_batch(manifest_path, binding_path, output, stage_one=stage):
    rerun_manifest_path, binding_path, output = map(Path, (manifest_path, binding_path, output))
    manifest = json.loads(rerun_manifest_path.read_text())
    if (manifest.get('schema') != 'building-rerun-manifest-v1' or
            manifest.get('state') != 'complete' or manifest.get('llm_used') is not False or
            manifest.get('installed') is not False):
        raise ValueError('Completed deterministic building rerun manifest required')
    records = manifest.get('tiles')
    if not isinstance(records, list) or manifest.get('completed_tiles') != len(records):
        raise ValueError('Building rerun manifest has incomplete tile coverage')
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    staged = []
    for index, record in enumerate(records, 1):
        original, candidate, receipt_path, receipt = check_candidate(record)
        tile_output = output / record['tile']
        value = stage_one(original, candidate, binding_path, tile_output)
        stage_manifest_path = tile_output / 'manifest.json'
        if not stage_manifest_path.is_file() or sha(stage_manifest_path) == '':
            raise ValueError(f"{record['tile']}: stage did not create a manifest")
        staged.append({
            'tile': record['tile'],
            'candidate_receipt': str(receipt_path),
            'candidate_receipt_sha256': sha(receipt_path),
            'candidate_manifest_sha256': receipt['candidate_manifest_sha256'],
            'stage': str(tile_output.resolve()),
            'stage_manifest_sha256': sha(stage_manifest_path),
            'patches': len(value['patches']),
            'changed_cells': value['changed_cells'],
        })
        progress = {
            'schema': SCHEMA, 'state': 'staging', 'source_manifest': str(rerun_manifest_path.resolve()),
            'source_manifest_sha256': sha(rerun_manifest_path),
            'binding': str(binding_path.resolve()), 'binding_sha256': sha(binding_path),
            'tiles_total': len(records), 'tiles_staged': len(staged), 'records': staged,
            'installed': False, 'llm_used': False,
        }
        atomic(output / 'progress.json', json.dumps(progress, indent=2).encode())
        print(f"STAGE {index}/{len(records)} {record['tile']} patches={len(value['patches'])}", flush=True)
    result = dict(progress, state='complete')
    atomic(output / 'manifest.json', json.dumps(result, indent=2).encode())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True,
                        help='Completed building-rerun manifest')
    parser.add_argument('--binding', type=Path, required=True,
                        help='Existing live-world binding')
    parser.add_argument('--output', type=Path, required=True,
                        help='Fresh immutable LaCie stage directory')
    args = parser.parse_args()
    result = stage_batch(args.manifest, args.binding, args.output)
    print(json.dumps({'state': result['state'], 'tiles': result['tiles_staged'],
                      'patches': sum(record['patches'] for record in result['records']),
                      'changed_cells': sum(record['changed_cells'] for record in result['records']),
                      'installed': False}, indent=2))
