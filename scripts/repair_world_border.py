"""Repair one closed overworld from its authoritative coverage envelope."""
import argparse
import fcntl
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chicago_tiles import digest
from world_border import bounds_for_plan, bounds_for_tiles, update_world_border


REPO = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = REPO / 'runs/chicago-adaptation-city-001/plan.json'


def coverage_tiles(world):
    world = Path(world)
    coverage = world / 'city-coverage.json'
    if coverage.exists():
        tiles = json.loads(coverage.read_text()).get('tiles', {})
        return list(tiles.values()) if isinstance(tiles, dict) else tiles
    report = json.loads((world / 'earthcraft.json').read_text())
    return [{'world_offset_xz': report.get('world_offset_xz', [0, 0]),
             'size_m': report['source']['size']}]


def _repair_locked(world, plan_path):
    """Verify the installed plan binding and update both border records."""
    world = Path(world)
    plan_path = Path(plan_path) if plan_path else None
    if plan_path and plan_path.exists():
        plan = json.loads(plan_path.read_text())
        coverage = json.loads((world / 'city-coverage.json').read_text())
        if coverage.get('world_plan_sha256') != digest(plan):
            raise ValueError('Installed world and border plan do not match')
        bounds = bounds_for_plan(plan)
    else:
        bounds = bounds_for_tiles(coverage_tiles(world))
    update_world_border(world, bounds)
    return bounds


def repair(world, plan_path=DEFAULT_PLAN, wait=False):
    """Repair a world while holding its Minecraft session lock.

    ``wait`` blocks on the operating-system lock, then performs the verified
    repair before another process can reopen the save.
    """
    world = Path(world)
    lock_path = world / 'session.lock'
    if not lock_path.exists():
        return _repair_locked(world, plan_path)
    with lock_path.open('r+b') as lock:
        try:
            operation = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.lockf(lock, operation)
        except BlockingIOError as error:
            raise ValueError('Refusing to repair a world that is open in Minecraft') from error
        return _repair_locked(world, plan_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=DEFAULT_PLAN)
    parser.add_argument('--wait', action='store_true',
                        help='Wait for Minecraft to close, then repair while holding its save lock')
    args = parser.parse_args()
    try:
        print(json.dumps(repair(args.world, args.plan, wait=args.wait), indent=2))
    except ValueError as error:
        parser.error(str(error))
