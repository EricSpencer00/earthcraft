"""Admit and deliver proof-bound building deltas from the live publisher.

The player save is never opened here.  A request names an immutable staged
delta and two independent verification receipts.  The long-running base
publisher consumes requests through the same bounded inbox it already owns,
so detail cannot race terrain publication or bypass the importer CAS rules.
"""
import gzip
import json
from pathlib import Path
import time

from audit_live_building_baseline import audit as audit_live_baseline
from live_city import atomic, publish, sha, validate


REQUEST_SCHEMA = 'earthcraft.building-delivery-request-v1'
TERMINAL_RESULTS = {'applied_in_memory'}
TERMINAL_PUBLICATIONS = {'complete', 'complete_with_conflicts', 'receipt_requires_attention', 'rejected'}


def _read(path):
    return json.loads(Path(path).read_text())


def _proof(proof, manifest_sha, kind):
    if not proof.get('passed'):
        raise ValueError(f'{kind} proof did not pass')
    # ``check_building_delta`` published ``staged_manifest_sha256`` before
    # the closed writer standardized the shorter field.  Both bind the same
    # immutable manifest; retain compatibility without accepting ambiguity.
    proof_manifest = proof.get('stage_manifest_sha256', proof.get('staged_manifest_sha256'))
    if proof_manifest != manifest_sha:
        raise ValueError(f'{kind} proof belongs to another stage')
    # Older native harness receipts predate the explicit field.  The immutable
    # stage manifest and the closed receipt both carry the no-LLM declaration;
    # reject only an affirmative or malformed proof declaration.
    if proof.get('llm_used', False) is not False:
        raise ValueError(f'{kind} proof does not rule out LLM use')


def stage_patches(stage, binding):
    """Validate and decode a static staged update without publishing it."""
    stage = Path(stage).resolve()
    manifest_path = stage / 'manifest.json'
    manifest = _read(manifest_path)
    if (manifest.get('schema') != 'building-delta-stage-v1' or
            manifest.get('frame') != binding.get('frame') or
            manifest.get('llm_used') is not False or manifest.get('installed') is not False):
        raise ValueError('Wrong staged update or coordinate frame')
    candidate = Path(manifest['candidate_world']) / 'building-layer.json'
    if sha(candidate) != manifest.get('candidate_manifest_sha256'):
        raise ValueError('Staged building candidate changed')
    patches = {}
    for record in manifest.get('patches', []):
        identity = record.get('patch')
        file = stage / 'inbox' / f'{identity}.json.gz'
        if not isinstance(identity, str) or sha(file) != identity:
            raise ValueError('Changed staged patch')
        patch = validate(json.loads(gzip.decompress(file.read_bytes())))
        if (patch.get('mode') != 'building_delta' or patch.get('frame') != binding['frame'] or
                patch.get('cells') != record.get('cells') or identity in patches):
            raise ValueError('Staged patch metadata mismatch')
        if patch.get('provenance', {}).get('llm_used', False) is not False:
            raise ValueError('Staged patch declares LLM use')
        if list(record.get('chunk', ())) != [patch['cx'], patch['cz']]:
            raise ValueError('Staged patch chunk mismatch')
        photo = patch.get('provenance', {}).get('protected_photo_resource_sha256')
        if photo and sha(Path(binding['world']) / 'resources.zip') != photo:
            raise ValueError('Accepted photo resources changed')
        patches[identity] = patch
    if not patches or sum(patch['cells'] for patch in patches.values()) != manifest.get('changed_cells'):
        raise ValueError('Staged patch totals differ')
    return manifest, patches


def make_request(stage, exchange, native_path, closed_path):
    """Create an immutable request only after proof and baseline admission."""
    stage, exchange = Path(stage).resolve(), Path(exchange).resolve()
    binding = _read(exchange / 'binding.json')
    manifest, patches = stage_patches(stage, binding)
    manifest_sha = sha(stage / 'manifest.json')
    baseline = audit_live_baseline(stage, exchange)
    if not baseline.get('ready_for_building_delta'):
        raise ValueError('Exact live base is not fully applied for this building stage')
    native_path, closed_path = Path(native_path).resolve(), Path(closed_path).resolve()
    native, closed = _read(native_path), _read(closed_path)
    _proof(native, manifest_sha, 'native')
    _proof(closed, manifest_sha, 'closed')
    if (native.get('two_load_save_cycles_verified') is not True or
            native.get('player_edit_preserved') is not True or
            native.get('block_entity_preserved') is not True or
            native.get('unowned_chunk_preserved') is not True or
            native.get('district_patches') != len(patches) or
            native.get('district_written_cells') != manifest['changed_cells']):
        raise ValueError('Native proof is incomplete')
    if (closed.get('closed_writer_matches_native_blocks_and_block_entities') is not True or
            closed.get('two_native_save_cycles_previously_verified') is not True or
            closed.get('patches') != len(patches) or closed.get('changed_cells') != manifest['changed_cells']):
        raise ValueError('Closed proof is incomplete')
    requests = exchange / 'building-requests'
    requests.mkdir(exist_ok=True)
    request = {
        'schema': REQUEST_SCHEMA,
        'stage': str(stage),
        'stage_manifest_sha256': manifest_sha,
        'frame': binding['frame'],
        'native_verification': str(native_path),
        'native_verification_sha256': sha(native_path),
        'closed_verification': str(closed_path),
        'closed_verification_sha256': sha(closed_path),
        'baseline_audit': baseline,
        'patches': len(patches),
        'changed_cells': manifest['changed_cells'],
        'llm_used': False,
        'created_at': time.time(),
    }
    target = requests / f'{manifest_sha}.json'
    raw = json.dumps(request, indent=2, sort_keys=True).encode()
    if target.exists():
        if target.read_bytes() != raw:
            raise FileExistsError('A different request already claims this immutable stage')
    else:
        atomic(target, raw)
    return target, request


def admit_request(path, binding):
    """Revalidate immutable request bindings and return its exact patches."""
    path = Path(path)
    request = _read(path)
    if (request.get('schema') != REQUEST_SCHEMA or request.get('frame') != binding.get('frame') or
            request.get('llm_used') is not False):
        raise ValueError('Invalid building delivery request')
    stage = Path(request['stage']).resolve()
    if sha(stage / 'manifest.json') != request.get('stage_manifest_sha256'):
        raise ValueError('Stage manifest changed after admission')
    manifest, patches = stage_patches(stage, binding)
    if len(patches) != request.get('patches') or manifest['changed_cells'] != request.get('changed_cells'):
        raise ValueError('Request totals changed')
    baseline = request.get('baseline_audit', {})
    if (baseline.get('schema') != 'live-building-baseline-audit-v1' or
            baseline.get('stage_manifest_sha256') != request['stage_manifest_sha256'] or
            baseline.get('ready_for_building_delta') is not True or baseline.get('llm_used') is not False):
        raise ValueError('Request lacks a passing baseline audit')
    native_path, closed_path = Path(request['native_verification']), Path(request['closed_verification'])
    if sha(native_path) != request.get('native_verification_sha256') or sha(closed_path) != request.get('closed_verification_sha256'):
        raise ValueError('Verification receipt changed after admission')
    native, closed = _read(native_path), _read(closed_path)
    _proof(native, request['stage_manifest_sha256'], 'native')
    _proof(closed, request['stage_manifest_sha256'], 'closed')
    if (native.get('two_load_save_cycles_verified') is not True or native.get('player_edit_preserved') is not True or
            native.get('block_entity_preserved') is not True or native.get('unowned_chunk_preserved') is not True or
            native.get('district_patches') != len(patches) or native.get('district_written_cells') != manifest['changed_cells'] or
            closed.get('closed_writer_matches_native_blocks_and_block_entities') is not True or
            closed.get('two_native_save_cycles_previously_verified') is not True or closed.get('patches') != len(patches) or
            closed.get('changed_cells') != manifest['changed_cells']):
        raise ValueError('Verification receipt is incomplete')
    return request, patches


def _record_path(exchange, request_path):
    return exchange / 'building-publications' / request_path.name


def has_pending_delivery(exchange):
    """Whether a proof-bound stage still needs a bounded inbox opportunity."""
    exchange = Path(exchange)
    for request_path in (exchange / 'building-requests').glob('*.json'):
        record_path = _record_path(exchange, request_path)
        if not record_path.exists() or _read(record_path).get('state') not in TERMINAL_PUBLICATIONS:
            return True
    return False


def _save_record(path, request_path, request, patches, completed, queued, status):
    receipts = {identity: value for identity, value in completed.items()}
    result = {
        'schema': 'earthcraft.building-delivery-publication-v1',
        'request': str(request_path.resolve()),
        'request_sha256': sha(request_path),
        'stage_manifest_sha256': request['stage_manifest_sha256'],
        'state': status,
        'total': len(patches),
        'completed': len(completed),
        'queued': sorted(queued),
        'written': sum(value.get('written', 0) for value in completed.values()),
        'conflicts_preserved': sum(value.get('conflicts', 0) for value in completed.values()),
        'already_target': sum(value.get('already_target', 0) for value in completed.values()),
        'saved_and_reloaded_verified': False,
        'llm_used': False,
        'receipts': receipts,
        'updated_at': time.time(),
    }
    atomic(path, json.dumps(result, indent=2, sort_keys=True).encode())
    return result


def service_pending_requests(exchange, binding, room, cache):
    """Publish up to 32 proof-bound detail patches, preserving terrain room.

    The caller owns ``publisher.lock``.  Invalid request files become explicit
    rejected records rather than stopping the ordinary terrain publisher.
    """
    exchange = Path(exchange)
    request_dir, publication_dir = exchange / 'building-requests', exchange / 'building-publications'
    request_dir.mkdir(exist_ok=True); publication_dir.mkdir(exist_ok=True)
    if room <= 0:
        return 0
    status_path = exchange / 'status.json'
    if not status_path.exists() or 'building_delta' not in _read(status_path).get('modes', []):
        return 0
    quota = min(room, 32); published = 0
    for request_path in sorted(request_dir.glob('*.json')):
        stamp = (request_path.stat().st_size, request_path.stat().st_mtime_ns)
        entry = cache.get(str(request_path))
        record_path = _record_path(exchange, request_path)
        if entry is None or entry['stamp'] != stamp:
            try:
                request, patches = admit_request(request_path, binding)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                atomic(record_path, json.dumps({'schema': 'earthcraft.building-delivery-publication-v1',
                    'request': str(request_path.resolve()), 'state': 'rejected', 'reason': str(exc),
                    'llm_used': False, 'updated_at': time.time()}, indent=2, sort_keys=True).encode())
                cache[str(request_path)] = {'stamp': stamp, 'rejected': True}
                continue
            entry = cache[str(request_path)] = {'stamp': stamp, 'request': request, 'patches': patches, 'rejected': False}
        if entry.get('rejected'):
            continue
        request, patches = entry['request'], entry['patches']
        completed, queued, terminal_error = {}, set(), None
        for identity in patches:
            receipt_path = exchange / 'receipts' / f'{identity}.json'
            if receipt_path.exists():
                receipt = _read(receipt_path)
                if receipt.get('result') in TERMINAL_RESULTS:
                    completed[identity] = receipt
                else:
                    terminal_error = {'patch': identity, 'receipt': receipt}
                    break
            elif (exchange / 'inbox' / f'{identity}.json.gz').exists():
                queued.add(identity)
        if terminal_error is not None:
            atomic(record_path, json.dumps({'schema': 'earthcraft.building-delivery-publication-v1',
                'request': str(request_path.resolve()), 'stage_manifest_sha256': request['stage_manifest_sha256'],
                'state': 'receipt_requires_attention', 'detail': terminal_error, 'llm_used': False,
                'updated_at': time.time()}, indent=2, sort_keys=True).encode())
            continue
        for identity, patch in patches.items():
            if quota <= 0:
                break
            if identity not in completed and identity not in queued:
                if publish(exchange, patch) != identity:
                    raise ValueError('Canonical building patch identity changed')
                queued.add(identity); quota -= 1; published += 1
        if len(completed) == len(patches):
            state = ('complete_with_conflicts' if any(value.get('conflicts', 0) for value in completed.values())
                     else 'complete')
        else:
            state = 'awaiting_importer'
        _save_record(record_path, request_path, request, patches, completed, queued, state)
        # One stage at a time makes later source-relative detail stages
        # deterministic and prevents two independently-derived styles racing
        # over the same baseline cells.
        if not state.startswith('complete') or quota <= 0:
            break
    return published
