"""Apply the same additive CAS patches to a locked, closed save with backups."""
import argparse
import copy
import fcntl
import gzip
import json
from pathlib import Path
import shutil

import nbtlib as n
import numpy as np

from city_assemble import chunk_payload
from city_save_update import read_region
from live_city import atomic, sha, validate
from metric_world import packed, region_write
from verify_metric_world import unpack
from world_replay import files_snapshot


def mutate_chunk(original,patch):
    validate(patch)
    if patch['mode']!='building_delta':raise ValueError('Building delta required')
    if (int(original['xPos']),int(original['zPos']))!=(patch['cx'],patch['cz']):raise ValueError('Wrong chunk')
    result=copy.deepcopy(original);sections={int(s['Y']):s for s in result['sections']}
    arrays={};written=conflicts=already=0;raised=np.zeros(256,int)
    entities={(int(e['x']),int(e['y']),int(e['z'])) for e in original.get('block_entities',[])}
    for start,count,expected_code,target_code in patch['runs']:
        expected,target=patch['palette'][expected_code],patch['palette'][target_code]
        if target=='minecraft:water':raise ValueError('Closed building delta requires a solid target')
        for index in range(start,start+count):
            sy=index//4096-4;slot=index%4096
            if sy not in sections or 'block_states' not in sections[sy]:raise ValueError('Missing source block section')
            state=sections[sy]['block_states'];palette=state['palette']
            if sy not in arrays:
                arrays[sy]=np.zeros(4096,int) if len(palette)==1 else unpack(state['data'],max(4,(len(palette)-1).bit_length()),4096)
            values=arrays[sy];old=palette[int(values[slot])]
            current=str(old['Name'])
            default=not old.get('Properties')
            pos=(patch['cx']*16+(index&15),(index>>8)-64,patch['cz']*16+((index>>4)&15))
            if default and current==target:already+=1;continue
            if pos in entities or not default or current!=expected:conflicts+=1;continue
            target_index=next((i for i,p in enumerate(palette) if str(p['Name'])==target and not p.get('Properties')),None)
            if target_index is None:
                target_index=len(palette);palette.append(n.Compound({'Name':n.String(target)}))
            values[slot]=target_index;written+=1
            raised[index%256]=max(raised[index%256],index//256+1)
    if written:
        for sy,values in arrays.items():
            state=sections[sy]['block_states']
            if len(state['palette'])>1:state['data']=packed(values,max(4,(len(state['palette'])-1).bit_length()))
        # All changes are additions/recolors to full solid cubes. Every existing
        # heightmap can only rise to the highest newly written solid block.
        for key,value in result.get('Heightmaps',{}).items():
            result['Heightmaps'][key]=packed(np.maximum(unpack(value,11,256),raised),11)
        result['isLightOn']=n.Byte(0)
        for section in result['sections']:
            section.pop('BlockLight',None);section.pop('SkyLight',None)
    return result,{'written':written,'conflicts':conflicts,'already_target':already}


def apply(stage,exchange,output,closed_proof,world_override=None):
    stage,exchange,output=map(Path,(stage,exchange,output))
    manifest=json.loads((stage/'manifest.json').read_text());binding=json.loads((exchange/'binding.json').read_text())
    world=Path(world_override) if world_override else Path(binding['world'])
    proof=json.loads(Path(closed_proof).read_text())
    if (not proof.get('passed') or proof.get('stage_manifest_sha256')!=sha(stage/'manifest.json') or
        proof.get('closed_writer_matches_native_blocks_and_block_entities') is not True or
        proof.get('two_native_save_cycles_previously_verified') is not True):
        raise ValueError('Matching closed/native proof required before save publication')
    if manifest['frame']!=binding['frame'] or manifest['llm_used'] is not False:raise ValueError('Wrong frame or source')
    if sha(Path(manifest['candidate_world'])/'building-layer.json')!=manifest['candidate_manifest_sha256']:raise ValueError('Candidate changed')
    if output.exists():raise FileExistsError(output)
    groups={};protected=set(binding['protected_chunks'])
    for record in manifest['patches']:
        file=stage/'inbox'/(record['patch']+'.json.gz')
        if sha(file)!=record['patch']:raise ValueError('Changed staged patch')
        patch=validate(json.loads(gzip.decompress(file.read_bytes())))
        if patch['mode']!='building_delta' or patch['frame']!=binding['frame']:raise ValueError('Wrong patch')
        key=f"{patch['cx']},{patch['cz']}"
        if key not in protected and not (exchange/f'claimed-{key}.json').exists():raise ValueError('Unowned chunk: '+key)
        photo=patch['provenance'].get('protected_photo_resource_sha256')
        if photo and sha(world/'resources.zip')!=photo:raise ValueError('Accepted photo changed')
        name=f"r.{patch['cx']//32}.{patch['cz']//32}.mca"
        groups.setdefault(name,[]).append((record['patch'],patch))
    with (world/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=files_snapshot(world)
        needed=sum((world/'region'/name).stat().st_size for name in groups)*3
        if shutil.disk_usage(world).free-needed<20*2**30:raise ValueError('Preserve internal 20 GiB reserve')
        output.mkdir(parents=True);(output/'backup').mkdir();(output/'prepared').mkdir()
        report={'state':'preparing','stage_manifest_sha256':sha(stage/'manifest.json'),'world':str(world.resolve()),
                'world_snapshot_before':before,'patches':[],'regions':{},'committed_regions':[],
                'saved_and_reloaded_verified':False,'world_metadata_changed':False,'llm_used':False}
        def record():atomic(output/'verification.json',json.dumps(report,indent=2).encode())
        record()
        for name,items in sorted(groups.items()):
            source=world/'region'/name;shutil.copy2(source,output/'backup'/name)
            if sha(source)!=sha(output/'backup'/name):raise ValueError('Region backup failed')
            chunks=read_region(source);expected={key:chunk_payload(tag) for key,tag in chunks.items()}
            for identity,patch in items:
                key=(patch['cx'],patch['cz'])
                if key not in chunks:raise ValueError('Missing owned chunk')
                entities=copy.deepcopy(chunks[key].get('block_entities'))
                chunks[key],counts=mutate_chunk(chunks[key],patch)
                if chunks[key].get('block_entities')!=entities:raise ValueError('Block entities changed')
                expected[key]=chunk_payload(chunks[key]);report['patches'].append({'patch':identity,'chunk':list(key),**counts})
            prepared=output/'prepared'/name
            region_write(prepared,[(x,z,tag) for (x,z),tag in chunks.items()])
            actual={key:chunk_payload(tag) for key,tag in read_region(prepared).items()}
            if actual!=expected:raise ValueError('Prepared block readback mismatch')
            report['regions'][name]={'before':sha(source),'after':sha(prepared),'chunks_verified':len(actual)};record()
        if files_snapshot(world)!=before:raise ValueError('Closed world changed during preparation')
        report['state']='prepared';record()
        for name,hashes in report['regions'].items():
            target=world/'region'/name
            if sha(target)!=hashes['before']:raise ValueError('Region changed before publication')
            atomic(target,(output/'prepared'/name).read_bytes())
            if sha(target)!=hashes['after']:raise ValueError('Published region mismatch')
            report['committed_regions'].append(name);record()
        after=files_snapshot(world)
        for key,value in before.items():
            expected=report['regions'][Path(key).name]['after'] if key.startswith('region/') and Path(key).name in report['regions'] else value
            if after.get(key)!=expected:raise ValueError('Unexpected world file change: '+key)
        if after.keys()!=before.keys():raise ValueError('World file set changed')
        report.update(state='published',written=sum(p['written'] for p in report['patches']),
            conflicts_preserved=sum(p['conflicts'] for p in report['patches']),
            already_target=sum(p['already_target'] for p in report['patches']),
            player_photo_entities_and_other_files_identical=True)
        record();return {k:v for k,v in report.items() if k not in ('world_snapshot_before','patches')}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--exchange',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--closed-proof',type=Path,required=True)
    a=p.parse_args();print(json.dumps(apply(a.stage,a.exchange,a.output,a.closed_proof),indent=2))
