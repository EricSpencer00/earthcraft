"""Create a privacy-safe whole-Earth progress snapshot.

The snapshot is deliberately stage-based. Earthcraft has no defensible global
area denominator yet, so it never turns a small regional run into a fake
planet-wide percentage. Local journals contribute counts without exporting
machine paths, coordinates from private profiles, or raw source metadata.
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import sys


STAGES = ('sources', 'geometry', 'appearance', 'game_verify')
EARTH_SURFACE_M2 = 510_064_471 * 1_000_000
CELL_SIZE_M = 256
MINECRAFT_CHUNK_SIZE_M = 16
CELL_STATES = frozenset(('queued', 'sourced', 'running', 'leased', 'generated', 'styled', 'verified', 'failed'))
STAGE_STATES = frozenset(('pending', 'running', 'leased', 'complete', 'failed'))
CELL_ID = re.compile(r'^-?\d+_-?\d+$')
REGION_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
PRIVATE_BOOLEAN_FIELDS = frozenset(('private_paths_included', 'private_data_in_snapshot'))
PRIVATE_KEY_MARKERS = ('path', 'secret', 'token', 'password', 'credential', 'private')
PRIVATE_VALUE_MARKERS = (
    '/Users/', '/home/', '\\Users\\', '/Volumes/', '/private/', '/tmp/',
    'file://', 'sqlite://',
)


def _latest_journal(root):
    candidates = sorted((root / 'runs').glob('**/jobs.sqlite'),
                        key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        try:
            with sqlite3.connect(path) as db:
                db.execute('SELECT 1 FROM jobs LIMIT 1').fetchone()
        except (OSError, sqlite3.Error):
            continue
        return path
    return None


def _is_integer(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _privacy_safe_json(value):
    """Reject private-looking fields and non-finite values in public data."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                return False
            normalized = key.casefold()
            if any(marker in normalized for marker in PRIVATE_KEY_MARKERS):
                if key not in PRIVATE_BOOLEAN_FIELDS or nested is not False:
                    return False
            if not _privacy_safe_json(nested):
                return False
        return True
    if isinstance(value, list):
        return all(_privacy_safe_json(item) for item in value)
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return not any(marker in value for marker in PRIVATE_VALUE_MARKERS)
    return True


def _valid_timestamp(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return True


def _valid_cell(cell):
    if not isinstance(cell, dict):
        return False
    region_id = cell.get('region_id')
    tile_id = cell.get('tile_id')
    if (not isinstance(region_id, str) or not REGION_ID.fullmatch(region_id)
            or not isinstance(tile_id, str) or not CELL_ID.fullmatch(tile_id)
            or cell.get('id') != f'{region_id}/{tile_id}'
            or cell.get('state') not in CELL_STATES):
        return False

    for field in ('source_state', 'geometry_state', 'appearance_state', 'game_verify_state'):
        if field in cell and cell[field] not in STAGE_STATES:
            return False
    size = cell.get('size_m')
    if not _is_integer(size, 1) or size % MINECRAFT_CHUNK_SIZE_M:
        return False
    total = cell.get('chunks_total')
    if total is not None:
        if not _is_integer(total, 1) or total != (size // MINECRAFT_CHUNK_SIZE_M) ** 2:
            return False
    for field in ('chunks_sourced', 'chunks_generated', 'chunks_styled', 'chunks_verified'):
        if field in cell:
            value = cell[field]
            if not _is_integer(value) or total is not None and value > total:
                return False
    for field, low, high in (
            ('latitude', -90, 90), ('longitude', -180, 180)):
        if field in cell and (not _is_number(cell[field]) or not low <= cell[field] <= high):
            return False
    for field in ('width_deg', 'height_deg'):
        if field in cell and (not _is_number(cell[field]) or cell[field] <= 0):
            return False
    return True


def _valid_cell_grid(grid, cell_count=None):
    if not isinstance(grid, dict):
        return False
    cell_size = grid.get('cell_size_m')
    chunk_size = grid.get('minecraft_chunk_size_m')
    chunks_per_cell = grid.get('chunks_per_cell')
    if (not _is_integer(cell_size, 1) or not _is_integer(chunk_size, 1)
            or cell_size % chunk_size or not _is_integer(chunks_per_cell, 1)
            or chunks_per_cell != (cell_size // chunk_size) ** 2):
        return False
    if grid.get('addressing', 'region/tile_id') != 'region/tile_id':
        return False
    for field in ('materialized_cells_are_observed_or_queued', 'unmeasured_cells_omitted'):
        if field in grid and grid[field] is not True:
            return False
    materialized = grid.get('materialized_cells')
    if materialized is not None:
        if not _is_integer(materialized) or cell_count is not None and materialized != cell_count:
            return False
    for field in ('global_cell_count_estimate', 'global_chunk_count_estimate'):
        if field in grid and not _is_integer(grid[field], 1):
            return False
    return True


def _valid_rollup(rollup, cell_count=None):
    if not isinstance(rollup, dict):
        return False
    for field in ('generated_tiles', 'materialized_cells', 'materialized_chunks', 'planned_tiles'):
        if field in rollup and rollup[field] is not None and not _is_integer(rollup[field]):
            return False
    if (cell_count is not None and rollup.get('materialized_cells') is not None
            and rollup['materialized_cells'] != cell_count):
        return False
    states = rollup.get('cell_states')
    if states is not None:
        if not isinstance(states, dict):
            return False
        if any(state not in CELL_STATES or not _is_integer(count)
               for state, count in states.items()):
            return False
    known_area = rollup.get('known_area_km2')
    if known_area is not None and (not _is_number(known_area) or known_area < 0):
        return False
    return True


def _valid_public_snapshot(snapshot):
    """Return whether a prior snapshot is safe to republish unchanged."""
    if not isinstance(snapshot, dict) or not _privacy_safe_json(snapshot):
        return False
    if 'schema_version' in snapshot and snapshot['schema_version'] != 1:
        return False
    if not _valid_timestamp(snapshot.get('updated_utc')):
        return False
    scope = snapshot.get('scope')
    if not isinstance(scope, dict) or scope.get('id') != 'earth':
        return False
    coverage = scope.get('coverage_percent')
    if coverage is not None and (not _is_number(coverage) or not 0 <= coverage <= 100):
        return False
    local = snapshot.get('local')
    claims = snapshot.get('claims')
    if (not isinstance(local, dict) or local.get('private_paths_included') is not False
            or not isinstance(claims, dict) or claims.get('private_data_in_snapshot') is not False):
        return False

    cells = snapshot.get('cells')
    if cells is not None:
        if not isinstance(cells, list) or not all(_valid_cell(cell) for cell in cells):
            return False
        identities = [(cell['region_id'], cell['tile_id']) for cell in cells]
        if len(identities) != len(set(identities)):
            return False
    cell_grid = snapshot.get('cell_grid')
    if cell_grid is not None and not _valid_cell_grid(cell_grid, len(cells) if cells is not None else None):
        return False
    if not _valid_rollup(snapshot.get('rollup', {}), len(cells) if cells is not None else None):
        return False
    return True


def _journal_counts(root):
    path = _latest_journal(root)
    if path:
        try:
            with sqlite3.connect(path) as db:
                rows = db.execute(
                    'SELECT stage,state,count(*) FROM jobs GROUP BY stage,state'
                ).fetchall()
        except (OSError, sqlite3.Error):
            rows = []
        counts = {stage: {} for stage in STAGES}
        for stage, state, count in rows:
            if isinstance(stage, int) and 0 <= stage < len(STAGES):
                counts[STAGES[stage]][str(state)] = int(count)
        return counts
    return {stage: {} for stage in STAGES}


def _projector(plan):
    """Return a metric-to-WGS84 function without making pyproj a hard dependency."""
    frame = plan.get('frame', {})
    crs = frame.get('crs')
    if crs:
        try:
            from pyproj import Transformer
            transformer = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
            return transformer.transform
        except (ImportError, RuntimeError, ValueError):
            pass

    origin_lat = float(frame.get('latitude_of_natural_origin', frame.get('lat0', 0)))
    origin_lng = float(frame.get('longitude_of_natural_origin', frame.get('lon0', 0)))
    import math
    cos_lat = max(0.1, math.cos(math.radians(origin_lat)))

    def approximate(x, y):
        return (origin_lng + x / (111_320 * cos_lat), origin_lat + y / 111_320)
    return approximate


def _cell_state(states):
    if any(states.get(stage) == 'failed' for stage in STAGES):
        return 'failed'
    if any(states.get(stage) in {'running', 'leased'} for stage in STAGES):
        return 'running'
    if states.get('game_verify') == 'complete':
        return 'verified'
    if states.get('appearance') == 'complete':
        return 'styled'
    if states.get('geometry') == 'complete':
        return 'generated'
    if states.get('sources') == 'complete':
        return 'sourced'
    return 'queued'


def _cell_rows(journal_path, region_id='chicago'):
    """Publish one privacy-safe row per planned 256m cell.

    Rows are intentionally sparse: they describe cells that are queued or
    observed by a run, not every possible cell on Earth.
    """
    if not journal_path:
        return []
    plan_path = journal_path.with_name('plan.json')
    try:
        plan = json.loads(plan_path.read_text())
        with sqlite3.connect(journal_path) as db:
            rows = db.execute('SELECT tile,stage,state FROM jobs').fetchall()
    except (OSError, ValueError, sqlite3.Error):
        return []
    states_by_tile = {}
    for tile, stage, state in rows:
        if isinstance(stage, int) and 0 <= stage < len(STAGES):
            states_by_tile.setdefault(str(tile), {})[STAGES[stage]] = str(state)
    project = _projector(plan)
    cell_rows = []
    for tile in plan.get('tiles', []):
        tile_id = str(tile.get('id', ''))
        size = int(tile.get('size', tile.get('size_m', CELL_SIZE_M)))
        west = float(tile.get('west', 0))
        north = float(tile.get('north', 0))
        east = west + size
        south = north - size
        west_lng, north_lat = project(west, north)
        east_lng, south_lat = project(east, south)
        states = {stage: states_by_tile.get(tile_id, {}).get(stage, 'pending')
                  for stage in STAGES}
        state = _cell_state(states)
        chunks_total = (size // MINECRAFT_CHUNK_SIZE_M) ** 2
        source_complete = states['sources'] == 'complete'
        geometry_complete = states['geometry'] == 'complete'
        appearance_complete = states['appearance'] == 'complete'
        verified = states['game_verify'] == 'complete'
        cell_rows.append({
            'id': f'{region_id}/{tile_id}',
            'region_id': region_id,
            'tile_id': tile_id,
            'latitude': round((north_lat + south_lat) / 2, 6),
            'longitude': round((west_lng + east_lng) / 2, 6),
            'width_deg': round(abs(east_lng - west_lng), 6),
            'height_deg': round(abs(north_lat - south_lat), 6),
            'size_m': size,
            'chunks_total': chunks_total,
            'chunks_sourced': chunks_total if source_complete else 0,
            'chunks_generated': chunks_total if geometry_complete else 0,
            'chunks_styled': chunks_total if appearance_complete else 0,
            'chunks_verified': chunks_total if verified else 0,
            'source_state': states['sources'],
            'geometry_state': states['geometry'],
            'appearance_state': states['appearance'],
            'game_verify_state': states['game_verify'],
            'state': state,
        })
    return cell_rows


def _cell_summary(cells):
    summary = {}
    for cell in cells:
        state = cell['state']
        summary[state] = summary.get(state, 0) + 1
    return summary


def _status(root):
    for path in sorted((root / 'runs' / 'chicago').glob('*/status.json')):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if value.get('status') == 'running':
            return 'running'
    return 'idle'


def _previous_public_snapshot(root):
    """Read the last committed aggregate when CI has no local run journal."""
    path = root / 'progress' / 'earth.json'
    try:
        snapshot = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not _valid_public_snapshot(snapshot):
        return None
    return snapshot


def build_snapshot(root, now=None):
    root = Path(root)
    journal_path = _latest_journal(root)
    counts = _journal_counts(root)
    source_complete = counts['sources'].get('complete', 0)
    geometry_complete = counts['geometry'].get('complete', 0)
    total = sum(counts['sources'].values())
    has_local_journal = bool(total)
    previous = _previous_public_snapshot(root) if not has_local_journal else None
    if previous:
        previous['updated_utc'] = (now or datetime.now(timezone.utc)).isoformat()
        previous.setdefault('local', {})['state'] = 'public_snapshot'
        return previous
    local_state = _status(root) if has_local_journal else 'no_local_run'
    cells = _cell_rows(journal_path) if has_local_journal else []
    cell_summary = _cell_summary(cells)
    materialized_chunks = sum(cell['chunks_total'] for cell in cells)
    return {
        'schema_version': 1,
        'updated_utc': (now or datetime.now(timezone.utc)).isoformat(),
        'scope': {
            'id': 'earth',
            'label': 'Entire Earth',
            'progress_model': 'stage evidence; no fabricated global percentage',
            'coverage_percent': None,
        },
        'cell_grid': {
            'schema_version': 1,
            'addressing': 'region/tile_id',
            'cell_size_m': CELL_SIZE_M,
            'minecraft_chunk_size_m': MINECRAFT_CHUNK_SIZE_M,
            'chunks_per_cell': (CELL_SIZE_M // MINECRAFT_CHUNK_SIZE_M) ** 2,
            'coverage_model': 'global sparse cell address space',
            'global_cell_count_estimate': round(EARTH_SURFACE_M2 / CELL_SIZE_M ** 2),
            'global_chunk_count_estimate': round(EARTH_SURFACE_M2 / MINECRAFT_CHUNK_SIZE_M ** 2),
            'estimate_basis': 'mean Earth surface area divided by square cell area; not a completion denominator',
            'materialized_cells': len(cells),
            'materialized_cells_are_observed_or_queued': True,
            'unmeasured_cells_omitted': True,
        },
        'rollup': {
            'state': 'regional-foundation',
            'generated_tiles': geometry_complete,
            'planned_tiles': None,
            'known_area_km2': None,
            'materialized_cells': len(cells),
            'materialized_chunks': materialized_chunks,
            'cell_states': cell_summary,
            'note': 'A global source catalog and coverage denominator are not complete.',
        },
        'workstreams': [
            {'id': 'catalog', 'label': 'Global source catalog', 'state': 'planned',
             'note': 'Build immutable spatial indexes before planetary batch work.'},
            {'id': 'terrain', 'label': 'Terrain surface', 'state': 'prototype',
             'note': 'Precise observed surface; no invented fill for missing observations.'},
            {'id': 'subsurface', 'label': 'Repeated substrate', 'state': 'implemented',
             'note': 'Uniform blocks below the admitted surface; air sections above chunk tops are omitted.'},
            {'id': 'urban', 'label': 'Urban structures', 'state': 'regional',
             'note': 'Chicago source-backed pilot; ordinary buildings and landmarks remain separate classes.'},
            {'id': 'delivery', 'label': 'On-demand delivery', 'state': 'local-only',
             'note': 'Local Minecraft delivery is measured; planetary streaming is not claimed.'},
        ],
        'regions': [
            {'id': 'chicago', 'label': 'Chicago pilot', 'latitude': 41.8781,
             'longitude': -87.6298, 'state': 'active',
             'source_tiles_complete': source_complete,
             'geometry_tiles_complete': geometry_complete,
             'source_tiles_total': total or None,
             'cell_count': len(cells),
             'chunks_total': materialized_chunks,
             'note': 'Current working example; CI republishes the last privacy-safe aggregate when no local journal is present.'},
        ],
        'cells': cells,
        'local': {
            'state': local_state,
            'source_tiles_complete': source_complete,
            'source_tiles_total': total or None,
            'geometry_tiles_complete': geometry_complete,
            'stages': counts,
            'private_paths_included': False,
        },
        'performance': {
            'parallel_tile_workers': 'independent processes over SQLite leases',
            'source_cache_coordination': 'per-source file locks',
            'surface_strategy': 'precise surface plus repeated substrate',
            'global_generation': 'not started',
        },
        'claims': {
            'global_complete': False,
            'accuracy_verified': False,
            'private_data_in_snapshot': False,
        },
    }


def write_snapshot(root, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    temporary.write_text(json.dumps(build_snapshot(root), indent=2) + '\n')
    temporary.replace(output)
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'progress/earth.json')
    args = parser.parse_args()
    print(write_snapshot(args.root, args.output))
