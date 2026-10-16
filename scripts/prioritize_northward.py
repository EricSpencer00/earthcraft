"""Route pending Chicago generation northward without changing the frozen plan."""
import argparse
import json
from pathlib import Path

from chicago_tiles import Journal, digest


def apply(plan_dir, label, output):
    plan_dir = Path(plan_dir)
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    plan = json.loads((plan_dir / 'plan.json').read_text())
    # In this metric frame smaller tile tz means farther north.  Tie-break
    # west-to-east so every dispatch order is deterministic and auditable.
    tiles = sorted(plan['tiles'], key=lambda tile: (int(tile['tz']), int(tile['tx']), tile['id']))
    ordered_ids = [tile['id'] for tile in tiles]
    journal = Journal(plan_dir / 'jobs.sqlite', plan)
    try:
        scheduled = journal.prioritize_tiles(ordered_ids, label, stages=('sources', 'geometry'))
        counts = journal.summary()
    finally:
        journal.close()
    result = {
        'schema': 'northward-priority-v1',
        'label': label,
        'plan_sha256': digest(plan),
        'direction': 'north-to-south',
        'ordering': 'tile tz ascending, then tx ascending, then id',
        'tiles': ordered_ids,
        'scheduled_pending_jobs': scheduled,
        'stage_counts_after': counts,
        'llm_used': False,
    }
    result['sha256'] = digest(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--label', default='northward-001')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = apply(args.plan, args.label, args.output)
    print(json.dumps({
        'label': value['label'],
        'direction': value['direction'],
        'tiles': len(value['tiles']),
        'scheduled_pending_jobs': value['scheduled_pending_jobs'],
        'output': str(args.output.resolve()),
    }, indent=2))
