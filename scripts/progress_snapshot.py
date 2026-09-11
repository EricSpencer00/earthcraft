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


def _journal_counts(root):
    candidates = sorted((root / 'runs').glob('**/jobs.sqlite'),
                        key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        try:
            with sqlite3.connect(path) as db:
                rows = db.execute(
                    'SELECT stage,state,count(*) FROM jobs GROUP BY stage,state'
                ).fetchall()
        except (OSError, sqlite3.Error):
            continue
        counts = {stage: {} for stage in STAGES}
        for stage, state, count in rows:
            if isinstance(stage, int) and 0 <= stage < len(STAGES):
                counts[STAGES[stage]][str(state)] = int(count)
        return counts
    return {stage: {} for stage in STAGES}


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
    return {
        'schema_version': 1,
        'updated_utc': (now or datetime.now(timezone.utc)).isoformat(),
        'scope': {
            'id': 'earth',
            'label': 'Entire Earth',
            'progress_model': 'stage evidence; no fabricated global percentage',
            'coverage_percent': None,
        },
        'rollup': {
            'state': 'regional-foundation',
            'generated_tiles': geometry_complete,
            'planned_tiles': None,
            'known_area_km2': None,
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
             'note': 'Current working example; CI republishes the last privacy-safe aggregate when no local journal is present.'},
        ],
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
