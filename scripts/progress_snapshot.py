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

from global_projection import ATLAS_SCHEMA, WORLD_PAGE_COUNT, address_for
from pyproj import Transformer


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
        center_lng, center_lat = project(west + size / 2, north - size / 2)
        west_lng, north_lat = project(west, north)
        east_lng, south_lat = project(east, south)
        states = {stage: states_by_tile.get(tile_id, {}).get(stage, 'pending')
                  for stage in STAGES}
        state = _cell_state(states)
        chunks_total = (size // MINECRAFT_CHUNK_SIZE_M) ** 2
        atlas_page_id = (address_for(center_lng, center_lat).id
                         if plan.get('frame', {}).get('crs') else None)
        source_complete = states['sources'] == 'complete'
        geometry_complete = states['geometry'] == 'complete'
        appearance_complete = states['appearance'] == 'complete'
        verified = states['game_verify'] == 'complete'
        cell_rows.append({
            'id': f'{region_id}/{tile_id}',
            'region_id': region_id,
            'tile_id': tile_id,
            'latitude': round(center_lat, 6),
            'longitude': round(center_lng, 6),
            'atlas_page_center_id': atlas_page_id,
            'atlas_page_center_basis': ('exact_metric_tile_center'
                                        if atlas_page_id is not None else 'unavailable'),
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


def _active_save_coverage(root):
    """Report save-manifest declarations without claiming current block fill.

    Reading the live world's Anvil files while Minecraft is open can race its
    writer. The versioned tile manifest is a bounded, read-only source, but it
    describes tile declarations rather than a current physical chunk scan.
    """
    path = Path(root) / 'runtime' / 'traversal' / 'saves' / 'Earthcraft' / 'city-coverage.json'
    try:
        manifest = json.loads(path.read_text())
        tiles = manifest['tiles']
        if not isinstance(tiles, dict):
            raise ValueError('tiles must be an object')
        declarations = 0
        for tile in tiles.values():
            size = tile.get('size_m')
            chunks = tile.get('chunks')
            if (type(size) is not int or size <= 0 or size % MINECRAFT_CHUNK_SIZE_M or
                    type(chunks) is not int or chunks < 0):
                raise ValueError('invalid tile size or chunk declaration')
            if chunks != (size // MINECRAFT_CHUNK_SIZE_M) ** 2:
                raise ValueError('chunk declaration does not match tile dimensions')
            declarations += chunks
        page_counts = None
        binding_path = (Path(root) / 'runtime' / 'traversal' / 'config' /
                        'earthcraft-live.json')
        try:
            binding = json.loads(binding_path.read_text())
            frame = binding['coordinate_frame']
            inverse = Transformer.from_crs(frame['crs'], 'EPSG:4326', always_xy=True)
            page_counts = {}
            for tile in tiles.values():
                offset = tile['world_offset_xz']
                size = tile['size_m']
                if (not isinstance(offset, list) or len(offset) != 2 or
                        any(type(value) is not int or value % MINECRAFT_CHUNK_SIZE_M
                            for value in offset)):
                    raise ValueError('invalid tile coordinate offset')
                east = float(frame['west']) + offset[0] + size / 2
                north = float(frame['north']) - offset[1] - size / 2
                longitude, latitude = inverse.transform(east, north)
                page_id = address_for(longitude, latitude).id
                page_counts[page_id] = page_counts.get(page_id, 0) + 1
            page_counts = dict(sorted(page_counts.items()))
        except (OSError, KeyError, TypeError, ValueError):
            page_counts = None
        return {
            'state': 'manifest_listed',
            'manifest_tiles': len(tiles),
            'manifest_chunk_declarations': declarations,
            'manifest_tile_center_page_counts': page_counts,
            'atlas_page_address_schema': ATLAS_SCHEMA if page_counts is not None else None,
            'manifest_page_address_basis': ('tile center transformed through the active save frame'
                                             if page_counts is not None else None),
            'current_block_fill_verified': False,
            'physical_chunk_scan': 'not performed; live-save writes may be in progress',
            'unlisted_chunks': None,
            'scope_note': 'Counts describe tile declarations in the active save manifest, not all pipeline artifacts or every chunk in the save.',
        }
    except FileNotFoundError:
        return {
            'state': 'unavailable', 'manifest_tiles': None,
            'manifest_chunk_declarations': None,
            'current_block_fill_verified': False,
            'physical_chunk_scan': 'not performed', 'unlisted_chunks': None,
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {
            'state': 'invalid_manifest', 'manifest_tiles': None,
            'manifest_chunk_declarations': None,
            'current_block_fill_verified': False,
            'physical_chunk_scan': 'not performed', 'unlisted_chunks': None,
        }


def build_snapshot(root, now=None):
    root = Path(root)
    active_save_coverage = _active_save_coverage(root)
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
        grid = previous.setdefault('cell_grid', {})
        grid.setdefault('global_page_address_schema', ATLAS_SCHEMA)
        grid.setdefault('global_page_count', WORLD_PAGE_COUNT)
        grid.setdefault('page_address_basis', 'geographic center of each listed cell')
        for cell in previous.get('cells', []):
            try:
                cell['atlas_page_center_id'] = address_for(
                    float(cell['longitude']), float(cell['latitude'])).id
                cell['atlas_page_center_basis'] = 'published_cell_center'
            except (KeyError, TypeError, ValueError):
                continue
        previous['active_save_coverage'] = active_save_coverage
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
            'addressing': 'region/tile_id with global atlas page at each cell center',
            'global_page_address_schema': ATLAS_SCHEMA,
            'global_page_count': WORLD_PAGE_COUNT,
            'page_address_basis': 'geographic center of each listed cell',
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
        'active_save_coverage': active_save_coverage,
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
            'active_save_fill_verified': active_save_coverage['current_block_fill_verified'],
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
