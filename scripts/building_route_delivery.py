"""Durably advance proof-bound building delivery along an ordered route.

This worker never opens a player save.  Every tile is audited against the live
CAS baseline, split when necessary, proved in an isolated Fabric fixture,
cross-checked by the closed writer, and only then submitted to the existing
publisher-owned delivery lane.  A checkpoint makes an overnight run resumable
without reordering a route or bypassing any of those gates.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import time

from audit_live_building_baseline import audit
from building_delivery import make_request
from check_building_delta import check as native_check
from check_closed_building_stage import check as closed_check
from filter_building_stage import create_filtered_stage
from live_city import atomic, sha


MIN_FREE_BYTES = int(20.25 * 2 ** 30)


def _read(path):
    return json.loads(Path(path).read_text())


def route_tiles(route, first_tile=None):
    """Return the immutable ordered suffix beginning with ``first_tile``."""
    document = _read(route)
    tiles = document.get('ordered_tiles', document.get('tiles'))
    if not isinstance(tiles, list) or not all(isinstance(tile, str) for tile in tiles):
        raise ValueError('Route has no ordered tile list')
    if first_tile is None:
        return tiles
    try:
        return tiles[tiles.index(first_tile):]
    except ValueError as exc:
        raise ValueError(f'Route does not contain requested first tile: {first_tile}') from exc


def _stage_manifest(stage):
    stage = Path(stage)
    manifest = _read(stage / 'manifest.json')
    if manifest.get('schema') != 'building-delta-stage-v1' or manifest.get('llm_used') is not False:
        raise ValueError(f'Not an admitted no-LLM building stage: {stage}')
    return manifest


def _clean_stage(base_stage):
    return Path(base_stage).with_name(Path(base_stage).name + '-clean-001')


def select_stage(base_stage, exchange):
    """Choose the exact base stage or make/reuse its audited safe subset."""
    base_stage, exchange = map(Path, (base_stage, exchange))
    baseline = audit(base_stage, exchange)
    if baseline.get('ready_for_building_delta'):
        return base_stage, baseline
    if baseline.get('encoding_exact') is not True:
        raise ValueError(f'Live encoding mismatch for {base_stage.name}')
    partial = _clean_stage(base_stage)
    if partial.exists():
        manifest = _stage_manifest(partial)
        if manifest.get('parent_stage_manifest_sha256') != sha(base_stage / 'manifest.json'):
            raise ValueError(f'Existing partial stage does not bind {base_stage.name}')
        return partial, audit(partial, exchange)
    created = create_filtered_stage(base_stage, exchange, partial)
    if not created['accepted_patches']:
        raise ValueError(f'No safe patches in {base_stage.name}')
    return partial, audit(partial, exchange)


def _native_ok(document, stage_hash):
    return (document.get('passed') is True and
            document.get('staged_manifest_sha256') == stage_hash and
            document.get('two_load_save_cycles_verified') is True and
            document.get('player_edit_preserved') is True and
            document.get('block_entity_preserved') is True and
            document.get('unowned_chunk_preserved') is True)


def _closed_ok(document, stage_hash):
    return (document.get('passed') is True and
            document.get('stage_manifest_sha256') == stage_hash and
            document.get('two_native_save_cycles_previously_verified') is True and
            document.get('closed_writer_matches_native_blocks_and_block_entities') is True and
            document.get('llm_used') is False)


def proof_paths(report_root, route_label, stage):
    stem = f'{route_label}-{Path(stage).name}'
    root = Path(report_root)
    return root / f'{stem}-native.json', root / f'{stem}-closed.json'


def _archive_receipt(source, destination):
    """Write a proof once, or require the existing immutable proof to match."""
    source, destination = map(Path, (source, destination))
    raw = source.read_bytes()
    if destination.exists():
        if destination.read_bytes() != raw:
            raise ValueError(f'Existing proof differs: {destination}')
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic(destination, raw)
    if sha(destination) != sha(source):
        raise ValueError(f'Archived proof changed: {destination}')


def _fixture(prefix):
    return Path('/private/tmp') / f'{prefix}-{os.getpid()}-{time.time_ns()}'


def ensure_proofs(stage, route_label, report_root):
    """Return immutable native/closed receipts, recreating only fixtures."""
    stage = Path(stage)
    manifest_hash = sha(stage / 'manifest.json')
    native_path, closed_path = proof_paths(report_root, route_label, stage)
    native = _read(native_path) if native_path.exists() else None
    closed = _read(closed_path) if closed_path.exists() else None
    if native is not None and closed is not None:
        if not _native_ok(native, manifest_hash) or not _closed_ok(closed, manifest_hash):
            raise ValueError(f'Existing proof does not admit {stage.name}')
        return native_path, closed_path
    if native is not None or closed is not None:
        raise ValueError(f'Incomplete proof archive for {stage.name}')
    native_work, closed_work = _fixture('earthcraft-route-native'), _fixture('earthcraft-route-closed')
    try:
        result = native_check(native_work, stage)
        if not _native_ok(result, manifest_hash):
            raise ValueError(f'Native proof failed for {stage.name}')
        closed = closed_check(stage, native_work, closed_work)
        if not _closed_ok(closed, manifest_hash):
            raise ValueError(f'Closed proof failed for {stage.name}')
        _archive_receipt(native_work / 'verification.json', native_path)
        _archive_receipt(closed_work / 'verification.json', closed_path)
    finally:
        for fixture in (native_work, closed_work):
            if fixture.exists():
                shutil.rmtree(fixture)
    return native_path, closed_path


def _request_for_stage(stage, exchange, native, closed):
    stage, exchange = map(Path, (stage, exchange))
    manifest_hash = sha(stage / 'manifest.json')
    request = exchange / 'building-requests' / f'{manifest_hash}.json'
    if request.exists():
        existing = _read(request)
        if existing.get('stage_manifest_sha256') != manifest_hash:
            raise ValueError(f'Existing request is not bound to {stage.name}')
        return request
    made, _ = make_request(stage, exchange, native, closed)
    return made


def await_publication(exchange, stage, seconds=300):
    """Wait for a zero-conflict publisher result; never touch a player save."""
    exchange, stage = map(Path, (exchange, stage))
    manifest_hash = sha(stage / 'manifest.json')
    record = exchange / 'building-publications' / f'{manifest_hash}.json'
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if record.exists():
            result = _read(record)
            state = result.get('state')
            if state == 'complete' and result.get('conflicts_preserved', 0) == 0:
                return result
            if state in {'complete_with_conflicts', 'receipt_requires_attention', 'rejected'}:
                raise ValueError(f'Publisher stopped safely for {stage.name}: {state}')
        time.sleep(2)
    raise TimeoutError(f'Publisher did not complete {stage.name} within {seconds}s')


def _write_checkpoint(path, value):
    value = {**value, 'updated_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
             'llm_used': False}
    atomic(path, json.dumps(value, indent=2, sort_keys=True).encode())


def wait_for_headroom(checkpoint):
    """Pause safely when the live publisher reserve would be threatened."""
    while shutil.disk_usage('/Users/eric/earthcraft').free < MIN_FREE_BYTES:
        _write_checkpoint(checkpoint, {'state': 'waiting_for_disk_headroom',
                                       'minimum_free_bytes': MIN_FREE_BYTES})
        time.sleep(30)


def run(route, stage_root, exchange, report_root, checkpoint, route_label, first_tile=None, limit=None):
    route, stage_root, exchange, report_root, checkpoint = map(Path, (
        route, stage_root, exchange, report_root, checkpoint))
    tiles = route_tiles(route, first_tile)
    if limit is not None:
        tiles = tiles[:limit]
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    completed = []
    for tile in tiles:
        try:
            wait_for_headroom(checkpoint)
            _write_checkpoint(checkpoint, {'state': 'auditing', 'tile': tile, 'completed': completed})
            base_stage = stage_root / tile
            stage, baseline = select_stage(base_stage, exchange)
            if baseline.get('ready_for_building_delta') is not True:
                raise ValueError(f'Filtered stage did not become live-base-ready: {stage}')
            _write_checkpoint(checkpoint, {'state': 'proving', 'tile': tile, 'stage': str(stage),
                                           'completed': completed})
            native, closed = ensure_proofs(stage, route_label, report_root)
            _write_checkpoint(checkpoint, {'state': 'registering', 'tile': tile, 'stage': str(stage),
                                           'native_proof': str(native), 'closed_proof': str(closed),
                                           'completed': completed})
            _request_for_stage(stage, exchange, native, closed)
            result = await_publication(exchange, stage)
            completed.append({'tile': tile, 'stage': str(stage), 'patches': result['total'],
                              'written': result['written'], 'conflicts_preserved': result['conflicts_preserved']})
            _write_checkpoint(checkpoint, {'state': 'delivered', 'tile': tile, 'completed': completed})
        except BaseException as exc:
            _write_checkpoint(checkpoint, {'state': 'requires_attention', 'tile': tile,
                                           'completed': completed, 'error': str(exc)})
            raise
    final = {'state': 'complete', 'completed': completed}
    _write_checkpoint(checkpoint, final)
    return final


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--route', type=Path, required=True)
    parser.add_argument('--stage-root', type=Path, required=True)
    parser.add_argument('--exchange', type=Path, required=True)
    parser.add_argument('--report-root', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--route-label', required=True)
    parser.add_argument('--first-tile')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    lock_path = args.checkpoint.with_suffix(args.checkpoint.suffix + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f'Another route delivery worker owns {lock_path}') from exc
        print(json.dumps(run(args.route, args.stage_root, args.exchange, args.report_root,
                             args.checkpoint, args.route_label, args.first_tile, args.limit), indent=2))
