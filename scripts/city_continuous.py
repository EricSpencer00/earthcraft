"""Append verified city tiles to one closed, continuously assembled Minecraft world.

One region at a time keeps memory independent of city size. Never writes the
installed save; player-preserving publication is a separate closed-save step.
"""
import json
from pathlib import Path
import shutil

import nbtlib as n

from city_assemble import chunk_payload
from cook_city_cache import save_json,sha
from inspect_world import chunks
from metric_world import region_write
from chicago_tiles import digest
from world_border import bounds_for_plan, update_world_border


def initialize(destination,seed,plan):
    destination=Path(destination)
    if (destination/'city-coverage.json').exists():
        state=json.loads((destination/'city-coverage.json').read_text())
        if state['world_plan_sha256']!=digest(plan):raise ValueError('Continuous world uses another frozen plan')
        return
    if destination.exists():raise FileExistsError('Uninitialized continuous world preserved')
    shutil.copytree(seed,destination)
    report=json.loads((destination/'earthcraft.json').read_text())
    if report['world_frame']!=plan['frame']:raise ValueError('Seed uses a different city frame')
    report['status']='incremental_city_assembly'
    report['source_scope']='Seed chart only; authoritative growing coverage is in city-coverage.json'
    report['point_sidecar_scope']='Seed geometry only; additional observations are retained per city tile'
    save_json(destination/'earthcraft.json',report)
    # The seed's server result does not verify subsequently appended city tiles.
    prior=destination/'seed-verification';prior.mkdir()
    for name in ('server-verification.json','city-assembly.json'):
        path=destination/name
        if path.exists():path.rename(prior/name)
    planned_border = bounds_for_plan(plan)
    state={'world_plan_sha256':digest(plan),'frame':plan['frame'],'tiles':{},'region_sha256':{},
        'planned_tiles':len(plan['tiles']),'full_chicago_geometry_complete':False,
        'appearance_complete':False,'installed':False,'block_scale_m':1,
        'world_border':planned_border,
        'seed_world':str(seed.resolve()),'seed_world_manifest_sha256':sha(seed/'earthcraft.json')}
    update_world_border(destination, planned_border)
    save_json(destination/'city-coverage.json',state)


def append_tile(destination,world,tile,plan):
    destination=Path(destination);world=Path(world)
    if (destination/'session.lock').exists():raise ValueError('Refusing to mutate a world that has been opened in Minecraft')
    state=json.loads((destination/'city-coverage.json').read_text())
    if state['world_plan_sha256']!=digest(plan):raise ValueError('City plan changed')
    report=json.loads((world/'earthcraft.json').read_text())
    if report['world_frame']!=plan['frame'] or report['world_offset_xz']!=tile['world_offset_xz']:
        raise ValueError('Tile world uses another coordinate frame')
    source_hash=sha(world/'earthcraft.json')
    if tile['id'] in state['tiles']:
        if state['tiles'][tile['id']]['source_world_manifest_sha256']!=source_hash:raise ValueError('Already assembled source tile changed')
        return
    incoming={};paths=list((world/'region').glob('r.*.*.mca'))
    if len(paths)!=1:raise ValueError('A 256 m aligned city tile must fit one region')
    for _,tag,_ in chunks(paths[0]):incoming[(int(tag['xPos']),int(tag['zPos']))]=tag
    tx,tz=tile['world_offset_xz'];s=tile['size']//16
    wanted={(x,z) for x in range(tx//16,tx//16+s) for z in range(tz//16,tz//16+s)}
    if set(incoming)!=wanted:raise ValueError('Source tile is not exactly the declared chunk extent')
    target=destination/'region'/paths[0].name;existing={}
    pending=destination/'pending-region.json';intent=json.loads(pending.read_text()) if pending.exists() else None
    if intent and intent['tile'] in state['tiles'] and state['region_sha256'].get(intent['region'])==intent['after_sha256']:
        if sha(destination/'region'/intent['region'])!=intent['after_sha256']:raise ValueError('Previously committed region changed')
        pending.unlink();intent=None
    recovering=bool(intent and intent['tile']==tile['id'] and intent['source_world_manifest_sha256']==source_hash
        and intent['prior_state_sha256']==sha(destination/'city-coverage.json') and target.exists()
        and sha(target)==intent['after_sha256'])
    if target.exists():
        expected=state['region_sha256'].get(target.name)
        # An interrupted append can have published a region but not its receipt;
        # identical incoming chunks below permit that idempotent recovery only.
        for _,tag,_ in chunks(target):existing[(int(tag['xPos']),int(tag['zPos']))]=tag
        overlap=set(incoming)&set(existing)
        if any(chunk_payload(incoming[k])!=chunk_payload(existing[k]) for k in overlap):
            raise ValueError('An appended tile would overwrite different geographic blocks')
        if expected and sha(target)!=expected and not recovering:
            raise ValueError('Previously assembled region changed outside an idempotent append')
    merged={**existing,**incoming}
    temporary=target.with_suffix('.mca.partial')
    if temporary.exists():
        candidate={(int(t['xPos']),int(t['zPos'])):chunk_payload(t) for _,t,_ in chunks(temporary)}
        if candidate!={k:chunk_payload(t) for k,t in merged.items()}:raise ValueError('Interrupted region differs; preserve it')
    else:region_write(temporary,[(k[0],k[1],t) for k,t in sorted(merged.items())])
    actual={(int(t['xPos']),int(t['zPos'])):chunk_payload(t) for _,t,_ in chunks(temporary)}
    if actual!={k:chunk_payload(t) for k,t in merged.items()}:raise ValueError('Region merge changed geographic blocks')
    if intent and not recovering:raise ValueError('Unresolved prior region transaction; preserve it')
    save_json(pending,{'tile':tile['id'],'region':target.name,'source_world_manifest_sha256':source_hash,
        'prior_state_sha256':sha(destination/'city-coverage.json'),'after_sha256':sha(temporary)})
    temporary.replace(target)
    state['tiles'][tile['id']]={'source_world':str(world.resolve()),'source_world_manifest_sha256':source_hash,
        'chunks':len(incoming),'world_offset_xz':[tx,tz],'size_m':tile['size']}
    state['region_sha256'][target.name]=sha(target)
    state['generated_tiles']=len(state['tiles']);state['generated_tile_area_m2']=sum(v['size_m']**2 for v in state['tiles'].values())
    state['full_chicago_geometry_complete']=len(state['tiles'])==len(plan['tiles'])
    metadata=json.loads((destination/'earthcraft.json').read_text())
    metadata['chunks']=sum(t['chunks'] for t in state['tiles'].values())
    metadata['city_coverage']={'verified_tiles':state['generated_tiles'],'tile_area_m2':state['generated_tile_area_m2'],
        'coverage_manifest':'city-coverage.json','appearance_complete':False,'full_chicago_complete':False}
    metadata['buildings_source_scope']='Seed building records only; each city tile retains its own source features and observation report'
    save_json(destination/'earthcraft.json',metadata)
    # The border describes the frozen Chicago plan. It must not hug the small
    # materialized cluster and move every time another tile is appended.
    state['world_border'] = bounds_for_plan(plan)
    update_world_border(destination, state['world_border'])
    save_json(destination/'city-coverage.json',state)
    pending.unlink()
    print(f"ASSEMBLED {tile['id']}: {state['generated_tiles']}/{state['planned_tiles']} tiles in one closed city world",flush=True)
