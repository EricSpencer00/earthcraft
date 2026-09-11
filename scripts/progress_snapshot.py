"""Create a privacy-safe whole-Earth progress snapshot.

The snapshot is deliberately stage-based. Earthcraft has no defensible global
area denominator yet, so it never turns a small regional run into a fake
planet-wide percentage. Local journals contribute counts without exporting
machine paths, coordinates from private profiles, or raw source metadata.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys


STAGES = ('sources', 'geometry', 'appearance', 'game_verify')
EARTH_SURFACE_M2 = 510_064_471 * 1_000_000
CELL_SIZE_M = 256
MINECRAFT_CHUNK_SIZE_M = 16


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
    if snapshot.get('scope', {}).get('id') != 'earth':
        return None
    if snapshot.get('local', {}).get('private_paths_included') is not False:
        return None
    if snapshot.get('claims', {}).get('private_data_in_snapshot') is not False:
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
