"""Build deterministic building-only candidates for an explicit tile manifest.

The city worker owns immutable source and base-world receipts.  This utility
never edits those roots or the live journal.  It selects only completed,
unstyled geometry tiles, records their exact hashes, and writes styled
candidates to a new output tree.  A later building-delta stage can compare the
candidate against the recorded baseline without rewriting an open save.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import time

from building_layer import compile_layer
from verify_metric_world import verify


SCHEMA = 'building-rerun-manifest-v1'
DEFAULT_SOURCE_ROOT = Path('/Volumes/LaCie/Earthcraft/chicago/city-tiles-001')


def sha(path):
    path = Path(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_sha(path):
    return sha(path)


def code_revision(root):
    try:
        return subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def source_hashes(root):
    files = ('sources.json', 'rasters.npz', 'cook-buildings-2022.json', 'osm-ways.json')
    return {name: sha(Path(root) / name) for name in files}


def region_hashes(world):
    return {path.name: sha(path) for path in sorted((Path(world) / 'region').glob('r.*.*.mca'))}


def tile_record(tile, row, source_root, output_root):
    evidence = Path(row['evidence']).resolve()
    root = evidence.parent
    if not root.is_relative_to(source_root):
        raise ValueError(f'{tile}: source root escaped the LaCie tile root: {root}')
    world = root / 'world'
    sources = root / 'sources'
    if not (world / 'earthcraft.json').is_file() or not (sources / 'sources.json').is_file():
        raise ValueError(f'{tile}: incomplete source or base-world receipt')
    if (root / 'world.observed').exists() or (world / 'building-layer.json').exists():
        return None, 'already_styled'
    report = json.loads((world / 'earthcraft.json').read_text())
    if report.get('llm_used') is True or report.get('inference_used') is True:
        raise ValueError(f'{tile}: AI/inference marker is enabled in the base report')
    buildings = report.get('buildings') or []
    if not buildings:
        return None, 'no_buildings'
    source_manifest = json.loads((sources / 'sources.json').read_text())
    if source_manifest.get('inference_used') is True:
        raise ValueError(f'{tile}: source manifest is marked as inferred')
    receipt = json.loads(evidence.read_text())
    if receipt.get('result') != 'pass':
        raise ValueError(f'{tile}: geometry receipt is not a pass')
    return {
        'tile': tile,
        'tx': int(tile.split('_')[0]),
        'tz': int(tile.split('_')[1]),
        'source_root': str(root),
        'base_world': str(world),
        'sources': str(sources),
        'source_manifest_sha256': json_sha(sources / 'sources.json'),
        'source_file_sha256': source_hashes(sources),
        'base_world_manifest_sha256': json_sha(world / 'earthcraft.json'),
        'base_region_sha256': region_hashes(world),
        'geometry_receipt_sha256': json_sha(evidence),
        'building_count': len(buildings),
        'llm_used': False,
        'candidate_world': str(output_root / tile / 'world'),
    }, 'rerun'


def select(args):
    plan = json.loads((args.plan / 'plan.json').read_text())
    by_id = {tile['id']: tile for tile in plan['tiles']}
    import sqlite3
    with sqlite3.connect(args.journal) as db:
        db.row_factory = sqlite3.Row
        rows = {
            row['tile']: row for row in db.execute(
                "SELECT tile,state,evidence FROM jobs WHERE stage=1"
            ).fetchall()
        }
    source_root = args.source_root.resolve()
    output_root = args.output.resolve()
    records = []
    skipped = {}
    candidates = []
    for tile in sorted(by_id.values(), key=lambda item: (item['tz'], item['tx'], item['id'])):
        if not (args.min_tx <= tile['tx'] <= args.max_tx and
                args.min_tz <= tile['tz'] <= args.max_tz):
            continue
        row = rows.get(tile['id'])
        if not row or row['state'] != 'complete':
            skipped['not_complete'] = skipped.get('not_complete', 0) + 1
            continue
        evidence = Path(row['evidence']).resolve()
        if not evidence.parent.parent.is_relative_to(source_root):
            skipped['not_lacie'] = skipped.get('not_lacie', 0) + 1
            continue
        candidates.append((tile['id'], row))
    def inspect(candidate):
        return tile_record(candidate[0], candidate[1], source_root, output_root)
    with ThreadPoolExecutor(max_workers=8) as pool:
        inspected = pool.map(inspect, candidates)
        for record, reason in inspected:
            if record is None:
                skipped[reason] = skipped.get(reason, 0) + 1
            else:
                records.append(record)
    return {
        'schema': SCHEMA,
        'created_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'code_revision': code_revision(args.root),
        'code_files_sha256': {
            name: sha(args.root / 'scripts' / name)
            for name in ('building_rerun.py', 'building_layer.py',
                         'verify_metric_world.py', 'metric_world.py')
        },
        'plan': str(args.plan.resolve()),
        'plan_sha256': sha(args.plan / 'plan.json'),
        'journal': str(args.journal.resolve()),
        'source_root': str(source_root),
        'output_root': str(output_root),
        'bounds': {
            'tx': [args.min_tx, args.max_tx],
            'tz': [args.min_tz, args.max_tz],
            'meaning': 'Explicit tile lattice window; existing styled tiles are excluded.',
        },
        'tiles': records,
        'skipped': skipped,
        'llm_used': False,
        'installed': False,
    }


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def load_and_check(manifest, root):
    if manifest.get('schema') != SCHEMA or manifest.get('llm_used') is not False:
        raise ValueError('Not a deterministic building rerun manifest')
    expected = {
        name: sha(root / 'scripts' / name)
        for name in ('building_rerun.py', 'building_layer.py',
                     'verify_metric_world.py', 'metric_world.py')
    }
    if expected != manifest.get('code_files_sha256'):
        raise ValueError('Building generator code changed since manifest creation')
    if sha(Path(manifest['plan']) / 'plan.json') != manifest['plan_sha256']:
        raise ValueError('City plan changed since manifest creation')


def build(manifest, root):
    load_and_check(manifest, root)
    output_root = Path(manifest['output_root'])
    receipts = []
    for index, record in enumerate(manifest['tiles'], 1):
        source_root = Path(record['source_root'])
        base_world = Path(record['base_world'])
        sources = Path(record['sources'])
        if sha(base_world / 'earthcraft.json') != record['base_world_manifest_sha256']:
            raise ValueError(f"{record['tile']}: base world changed after selection")
        if sha(sources / 'sources.json') != record['source_manifest_sha256']:
            raise ValueError(f"{record['tile']}: source manifest changed after selection")
        if source_hashes(sources) != record['source_file_sha256']:
            raise ValueError(f"{record['tile']}: source files changed after selection")
        if region_hashes(base_world) != record['base_region_sha256']:
            raise ValueError(f"{record['tile']}: base regions changed after selection")
        receipt_path = source_root / 'geometry-receipt.json'
        if sha(receipt_path) != record['geometry_receipt_sha256']:
            raise ValueError(f"{record['tile']}: geometry receipt changed after selection")
        destination = Path(record['candidate_world'])
        if destination.exists():
            raise FileExistsError(f'{record["tile"]}: refusing to overwrite {destination}')
        started = time.monotonic()
        compile_layer(base_world, sources, destination)
        checks = verify(destination)
        layer = json.loads((destination / 'building-layer.json').read_text())
        if layer.get('llm_used') is not False or layer.get('source_world') != str(base_world):
            raise ValueError(f"{record['tile']}: candidate provenance is invalid")
        receipt = {
            'schema': 'building-rerun-receipt-v1',
            'tile': record['tile'],
            'result': 'pass',
            'code_revision': manifest['code_revision'],
            'code_files_sha256': manifest['code_files_sha256'],
            'baseline': record,
            'candidate_world': str(destination),
            'candidate_manifest_sha256': sha(destination / 'building-layer.json'),
            'candidate_region_sha256': region_hashes(destination),
            'checks': checks,
            'building_count': len(layer.get('buildings', [])),
            'derived_cells': checks.get('derived_building_cells', 0),
            'appearance_cells': layer.get('appearance_cells', 0),
            'llm_used': False,
            'installed': False,
            'seconds': time.monotonic() - started,
        }
        write_json(destination.parent / 'building-rerun-receipt.json', receipt)
        receipts.append(receipt)
        print(f"BUILDING {index}/{len(manifest['tiles'])} {record['tile']} "
              f"buildings={receipt['building_count']} derived={receipt['derived_cells']}", flush=True)
    result = dict(manifest)
    result['state'] = 'complete'
    result['completed_tiles'] = len(receipts)
    result['completed_buildings'] = sum(item['building_count'] for item in receipts)
    result['derived_cells'] = sum(item['derived_cells'] for item in receipts)
    result['completed_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    write_json(output_root / 'manifest.completed.json', result)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--journal', type=Path, required=True)
    p.add_argument('--source-root', type=Path, default=DEFAULT_SOURCE_ROOT)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--manifest', type=Path)
    p.add_argument('--min-tx', type=int, required=True)
    p.add_argument('--max-tx', type=int, required=True)
    p.add_argument('--min-tz', type=int, required=True)
    p.add_argument('--max-tz', type=int, required=True)
    p.add_argument('--select', action='store_true', help='Write a fresh immutable tile manifest')
    p.add_argument('--build', action='store_true', help='Build candidates from an existing manifest')
    return p


def main():
    args = parser().parse_args()
    if args.select == args.build:
        raise SystemExit('Choose exactly one of --select or --build')
    if args.min_tx > args.max_tx or args.min_tz > args.max_tz:
        raise SystemExit('Tile bounds must be ordered')
    manifest_path = args.manifest or args.output / 'manifest.json'
    if args.select:
        if Path(manifest_path).exists():
            raise SystemExit(f'Refusing to overwrite existing manifest: {manifest_path}')
        result = select(args)
        if not result['tiles']:
            raise SystemExit('No completed unstyled building tiles matched the explicit bounds')
        write_json(manifest_path, result)
        print(json.dumps({
            'manifest': str(Path(manifest_path).resolve()),
            'tiles': len(result['tiles']),
            'buildings': sum(item['building_count'] for item in result['tiles']),
            'skipped': result['skipped'],
            'bounds': result['bounds'],
        }, indent=2))
    else:
        if not Path(manifest_path).is_file():
            raise SystemExit(f'Manifest not found: {manifest_path}')
        result = build(json.loads(Path(manifest_path).read_text()), args.root.resolve())
        print(json.dumps({
            'state': result['state'], 'output': result['output_root'],
            'tiles': result['completed_tiles'], 'buildings': result['completed_buildings'],
            'derived_cells': result['derived_cells'],
        }, indent=2))


if __name__ == '__main__':
    main()
