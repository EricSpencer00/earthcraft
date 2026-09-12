"""Create an immutable building stage containing only live-base-safe patches.

The live importer keeps any nonmatching block instead of overwriting it.  A
large staged building pass therefore cannot be published as a whole when one
or more base chunks have legitimate conflicts.  This tool takes a fresh,
read-only baseline audit and creates a new stage containing exactly the
patches whose encoded base was admitted.  It never alters the parent stage,
the live exchange, or a player save.
"""
import argparse
import copy
import json
from pathlib import Path
import os
import tempfile

from audit_live_building_baseline import audit
from live_city import atomic, sha


SAFE_BASE_DELIVERIES = frozenset({
    'applied',
    'historic_compatible',
    'protected_bootstrap_applied',
    'protected_bootstrap_already_target',
})


def select_safe_patches(manifest, baseline):
    """Return exact parent patch records admitted by a baseline audit.

    The audit must prove that conversion encoding is exact.  Delivery state is
    evaluated per source chunk, so an unsafe chunk is excluded without
    weakening the compare-and-set condition for any accepted chunk.
    """
    if baseline.get('schema') != 'live-building-baseline-audit-v1':
        raise ValueError('Unexpected baseline audit schema')
    if baseline.get('encoding_exact') is not True or baseline.get('baseline_mismatches') != 0:
        raise ValueError('Baseline encoding is not exact')
    records = {}
    for record in baseline.get('records', []):
        key = tuple(record.get('chunk', ()))
        if len(key) != 2 or key in records:
            raise ValueError('Baseline audit chunk records are ambiguous')
        records[key] = record
    accepted, excluded = [], []
    for patch in manifest.get('patches', []):
        key = tuple(patch.get('chunk', ()))
        record = records.get(key)
        if record is None or record.get('cells') != patch.get('cells'):
            raise ValueError(f'Baseline audit lacks exact patch {key}')
        detail = {
            'patch': patch.get('patch'),
            'chunk': list(key),
            'cells': patch.get('cells'),
            'base_delivery': record.get('base_delivery'),
            'base_patch': record.get('base_patch'),
            'bootstrap_base_patch': record.get('bootstrap_base_patch'),
        }
        if record.get('base_delivery') in SAFE_BASE_DELIVERIES:
            accepted.append(copy.deepcopy(patch))
        else:
            excluded.append(detail)
    if not accepted:
        raise ValueError('Baseline audit admits no building patches')
    return accepted, excluded


def _receipt_summary(exchange, exclusion):
    """Keep only the relevant public importer outcome for excluded evidence."""
    identities = (exclusion['bootstrap_base_patch'], exclusion['base_patch'])
    for identity in identities:
        path = Path(exchange) / 'receipts' / f'{identity}.json'
        if not path.is_file():
            continue
        receipt = json.loads(path.read_text())
        return {key: receipt.get(key) for key in (
            'patch', 'chunk', 'mode', 'result', 'written', 'already_target', 'conflicts', 'time')}
    return None


def create_filtered_stage(stage, exchange, output):
    """Audit *stage* and atomically write a new partial stage at *output*."""
    stage, exchange, output = map(lambda value: Path(value).resolve(), (stage, exchange, output))
    if output.exists():
        raise FileExistsError(f'Refusing to replace existing stage: {output}')
    manifest_path = stage / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 'building-delta-stage-v1' or manifest.get('llm_used') is not False:
        raise ValueError('Parent is not an admitted no-LLM building stage')
    baseline = audit(stage, exchange)
    if baseline.get('stage_manifest_sha256') != sha(manifest_path):
        raise ValueError('Baseline audit does not bind the parent manifest')
    accepted, excluded = select_safe_patches(manifest, baseline)
    exclusions = []
    for record in excluded:
        summary = _receipt_summary(exchange, record)
        if summary is not None:
            record['observed_base_receipt'] = summary
        exclusions.append(record)
    filtered = copy.deepcopy(manifest)
    filtered.update({
        'patches': accepted,
        'changed_cells': sum(record['cells'] for record in accepted),
        'installed': False,
        'llm_used': False,
        'partial_delivery': True,
        'parent_stage_manifest_sha256': sha(manifest_path),
        'baseline_audit_sha256': None,
        'baseline_filter_allowed_base_deliveries': sorted(SAFE_BASE_DELIVERIES),
        'excluded_patches': exclusions,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f'.{output.name}.', dir=output.parent))
    try:
        inbox = temporary / 'inbox'
        inbox.mkdir()
        for record in accepted:
            source = stage / 'inbox' / f"{record['patch']}.json.gz"
            if sha(source) != record['patch']:
                raise ValueError(f'Parent patch changed: {record["patch"]}')
            destination = inbox / source.name
            atomic(destination, source.read_bytes())
            if sha(destination) != record['patch']:
                raise ValueError(f'Filtered patch copy changed: {record["patch"]}')
        audit_path = temporary / 'baseline-audit.json'
        atomic(audit_path, json.dumps(baseline, indent=2, sort_keys=True).encode())
        filtered['baseline_audit_sha256'] = sha(audit_path)
        atomic(temporary / 'manifest.json', json.dumps(filtered, indent=2, sort_keys=True).encode())
        os.rename(temporary, output)
    except BaseException:
        if temporary.exists():
            for child in sorted(temporary.rglob('*'), reverse=True):
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            temporary.rmdir()
        raise
    return {
        'stage': str(output),
        'stage_manifest_sha256': sha(output / 'manifest.json'),
        'parent_stage_manifest_sha256': sha(manifest_path),
        'accepted_patches': len(accepted),
        'accepted_changed_cells': filtered['changed_cells'],
        'excluded_patches': len(exclusions),
        'excluded': exclusions,
        'llm_used': False,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--exchange', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_filtered_stage(args.stage, args.exchange, args.output), indent=2))
