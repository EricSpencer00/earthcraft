"""Advance verified Cook-2022 roof colour for completed Lake Shore tiles.

The worker claims only the journal's appearance stage for an explicit frozen
road corridor.  It does not alter the geometry worker, continuous assembly, or
any installed Minecraft save.  Each result remains a separate staged world.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from chicago_tiles import Journal, digest
from cook_city_cache import sha
from cook_ortho import acquire as acquire_cook_ortho
from roof_color import apply as paint_roofs
from verify_metric_world import verify


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2))


def route_tile_ids(route_path, plan):
    route=json.loads(Path(route_path).read_text())
    if route.get('schema') != 'named-road-priority-v1' or route.get('plan_sha256') != digest(plan):
        raise ValueError('Road route does not match the frozen city plan')
    tiles=tuple(route.get('tiles', ()))
    if not tiles or len(set(tiles)) != len(tiles):
        raise ValueError('Road route must contain unique tile identifiers')
    known={tile['id'] for tile in plan['tiles']}
    if set(tiles)-known:
        raise ValueError('Road route contains a tile outside the frozen city plan')
    return tiles


def styled_root(journal, tile):
    row=journal.db.execute('SELECT state,evidence FROM jobs WHERE tile=? AND stage=1',(tile,)).fetchone()
    if row is None or row['state'] != 'complete':
        return None
    root=Path(row['evidence']).parent
    return root if (root/'world'/'building-layer.json').is_file() else None


def roofable_root(journal, tile):
    """Return a completed shell only when it contains building-owned cells.

    Terrain-only city tiles have an intentionally empty building-layer sidecar.
    They remain valid geometry results, but fetching imagery for their appearance
    stage cannot improve the city and would make a route look more complete than
    it is.  This test uses only the shell compiler's own local metadata.
    """
    root=styled_root(journal,tile)
    if root is None:
        return None
    layer=json.loads((root/'world'/'building-layer.json').read_text())
    buildings=layer.get('buildings')
    if not isinstance(buildings,list):
        raise ValueError('Styled shell has an invalid building-layer manifest')
    return root if any(int(building.get('appearance_cells',0)) > 0
                       for building in buildings if isinstance(building,dict)) else None


def existing_roof_result(world, roof):
    result=json.loads((roof/'roof-colour.json').read_text())
    if result.get('schema') != 'earthcraft.roof-colour-v1' or result.get('source_world') != str(world.resolve()):
        raise ValueError('Existing roof stage does not belong to this styled shell')
    if not Path(result['imagery']).is_dir():
        raise ValueError('Existing roof stage imagery is unavailable')
    verify(roof)
    return result


def run(plan_dir, route_path, limit, owner='lake-shore-roof-worker', acquire=acquire_cook_ortho,
        paint=paint_roofs, verify_existing=existing_roof_result):
    plan_dir=Path(plan_dir); plan=json.loads((plan_dir/'plan.json').read_text())
    route=route_tile_ids(route_path, plan)
    if not 1 <= limit <= len(route):
        raise ValueError('Limit must be within the selected road corridor')
    lock=(plan_dir/'roof-worker.lock').open('a+')
    fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.seek(0);lock.truncate();lock.write(str(os.getpid()));lock.flush()
    journal=Journal(plan_dir/'jobs.sqlite',plan)
    processed=failed=0
    try:
        while processed < limit:
            candidate=next((tile for tile in route if roofable_root(journal,tile) is not None and
                            journal.db.execute('SELECT state FROM jobs WHERE tile=? AND stage=2',(tile,)).fetchone()['state'] == 'pending'),
                           None)
            if candidate is None:
                break
            job=journal.claim_exact('appearance',candidate,owner,lease_seconds=3600)
            if job is None:
                continue
            processed+=1;root=roofable_root(journal,candidate);world=root/'world';roof=root/'world.roof'
            started=time.monotonic()
            try:
                if roof.is_dir():
                    result=verify_existing(world,roof)
                else:
                    imagery=root/'cook-ortho-2022'
                    if not imagery.exists():
                        acquire(root/'sources',imagery)
                    result=paint(world,imagery,roof)
                receipt=root/f'appearance-receipt-{job["token"]}.json'
                save(receipt,{**job,'result':'pass','seconds':time.monotonic()-started,
                              'roof_world':str(roof.resolve()),'roof_colour_sha256':sha(roof/'roof-colour.json'),
                              'imagery_manifest_sha256':result['imagery_manifest_sha256'],
                              'selected_roof_cells':result['selected_roof_cells'],
                              'recolored_roof_cells':result['recolored_roof_cells'],
                              'geometry_changed':False,'walls_painted':False,'llm_used':False,
                              'installed':False})
                journal.finish(job,receipt)
                print(f"ROOF {candidate}: {result['recolored_roof_cells']}/{result['selected_roof_cells']} cells",flush=True)
            except Exception as error:
                receipt=root/f'appearance-failure-{job["token"]}.json'
                save(receipt,{**job,'result':'failed','error_type':type(error).__name__,'error':str(error),
                              'fallback_used':False,'geometry_changed':False,'installed':False})
                journal.fail(job,receipt);failed+=1
                print(f'FAILED ROOF {candidate}: {error}',flush=True)
    finally:
        journal.close();lock.close()
    return {'route':str(Path(route_path).resolve()),'processed':processed,'failed':failed}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=Path(__file__).resolve().parents[1]/'runs/chicago-adaptation-city-001')
    parser.add_argument('--route',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=1)
    args=parser.parse_args()
    print(json.dumps(run(args.plan,args.route,args.limit),indent=2))
