"""Verify real building deltas, conflict preservation and disk replay in Fabric."""
import argparse
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np

from check_live_import import block_volume
from city_save_update import read_region
from live_city import ROOT, publish, sha


def await_condition(process,predicate,seconds,label):
    deadline=time.monotonic()+seconds
    while not predicate():
        if process.poll() is not None:raise RuntimeError('Server exited during '+label)
        if time.monotonic()>deadline:raise TimeoutError(label)
        time.sleep(.1)


def check(work):
    work=Path(work).resolve();stage=ROOT/'runs/building-delta-district-001'
    manifest=json.loads((stage/'manifest.json').read_text());records=manifest['patches']
    source=Path(manifest['source_world']);decoded={}
    for record in records:
        file=stage/'inbox'/(record['patch']+'.json.gz')
        if sha(file)!=record['patch']:raise ValueError('Changed staged patch')
        p=json.loads(gzip.decompress(file.read_bytes()))
        decoded[(p['cx'],p['cz'])]=p
    if work.exists():raise FileExistsError(work)
    work.mkdir(parents=True);shutil.copytree(source,work/'world')
    for folder in ('mods','config','exchange/inbox','exchange/receipts','exchange/archive'):
        (work/folder).mkdir(parents=True)
    mod=ROOT/'vendor/live/earthcraft-live-0.1.0.jar'
    shutil.copy2(mod,work/'mods'/mod.name)
    shutil.copy2(ROOT/'runtime/traversal/mods/fabric-api-0.138.4+1.21.10.jar',work/'mods')
    shutil.copy2(ROOT/'vendor/minecraft-server-1.21.10.jar',work/'server.jar')
    (work/'eula.txt').write_text('eula=true\n')
    (work/'server.properties').write_text('server-ip=127.0.0.1\nserver-port=25586\nlevel-name=world\nonline-mode=true\nmax-players=1\nview-distance=2\nsimulation-distance=2\n')
    protected=sorted({'0,0',*(f'{x},{z}' for x,z in decoded)})
    (work/'config/earthcraft-live.json').write_text(json.dumps({'world':str(work/'world'),
        'exchange':str(work/'exchange'),'frame':manifest['frame'],'protected_chunks':protected}))
    (work/'verification.json').write_text(json.dumps({'passed':False,'reason':'Native verification in progress'}))
    frame=manifest['frame'];index=(900+64)*256+2*16+2
    probe=dict(version=1,frame=frame,cx=0,cz=0,mode='building_delta',
        palette=['minecraft:gray_concrete','minecraft:bricks','minecraft:air','minecraft:stone'],
        runs=[[index,2,0,1],[index+2,2,2,3]],cells=4)
    unowned=dict(version=1,frame=frame,cx=100,cz=100,mode='building_delta',
                 palette=['minecraft:air','minecraft:stone'],runs=[[index,1,0,1]],cells=1)
    source_regions={p.name:sha(p) for p in (source/'region').glob('r.*.*.mca')}
    runs=[];all_results=[];compared=0
    for cycle in (1,2):
        start=time.monotonic();logpath=work/f'run-{cycle}.log';log=logpath.open('w')
        process=subprocess.Popen(['/opt/homebrew/opt/openjdk@21/bin/java','-Xms512M','-Xmx2G','-jar',
            str(ROOT/'vendor/live/fabric-server-launch.jar'),'nogui'],cwd=work,stdin=subprocess.PIPE,
            stdout=log,stderr=subprocess.STDOUT,text=True)
        try:
            await_condition(process,lambda:'Done (' in logpath.read_text() and '[Earthcraft live] attached' in logpath.read_text(),90,'startup')
            status=json.loads((work/'exchange/status.json').read_text())
            if 'building_delta' not in status.get('modes',[]):raise ValueError('Missing runtime capability')
            def command(value):process.stdin.write(value+'\n');process.stdin.flush()
            if cycle==1:
                command('gamerule randomTickSpeed 0\nforceload add 0 0 15 15\nsetblock 2 900 2 minecraft:gray_concrete\nsetblock 3 900 2 minecraft:diamond_block\nsetblock 4 900 2 minecraft:air\nsetblock 5 900 2 minecraft:chest{LootTable:"minecraft:chests/simple_dungeon"}\nsay EARTHCRAFT_PROBE_SETUP')
                await_condition(process,lambda:'EARTHCRAFT_PROBE_SETUP' in logpath.read_text(),15,'probe setup')
            probe_id=publish(work/'exchange',{**probe,'provenance':{'test_cycle':cycle}})
            unowned_id=publish(work/'exchange',{**unowned,'provenance':{'test_cycle':cycle}})
            ids=[probe_id,unowned_id]
            await_condition(process,lambda:all((work/'exchange/receipts'/f'{i}.json').exists() for i in ids),30,'CAS probes')
            results=[json.loads((work/'exchange/receipts'/f'{i}.json').read_text()) for i in ids]
            wanted=(2,0,2) if cycle==1 else (0,2,2)
            actual=tuple(results[0][k] for k in ('written','already_target','conflicts'))
            if actual!=wanted or results[1]['result']!='unowned_chunk_preserved':raise ValueError(results)
            for identity in ids:
                (work/'exchange/inbox'/f'{identity}.json.gz').rename(work/'exchange/archive'/f'{identity}.json.gz')
            if cycle==1:
                for offset in range(0,len(records),128):
                    batch=records[offset:offset+128]
                    for record in batch:
                        shutil.copy2(stage/'inbox'/(record['patch']+'.json.gz'),work/'exchange/inbox')
                    await_condition(process,lambda:all((work/'exchange/receipts'/(r['patch']+'.json')).exists() for r in batch),180,'district batch')
                    received=[json.loads((work/'exchange/receipts'/(r['patch']+'.json')).read_text()) for r in batch]
                    for r,receipt in zip(batch,received):
                        if receipt['result']!='applied_in_memory' or receipt['written']!=r['cells'] or receipt['conflicts']:
                            raise ValueError(receipt)
                        (work/'exchange/inbox'/(r['patch']+'.json.gz')).rename(work/'exchange/archive'/(r['patch']+'.json.gz'))
                    all_results.extend(received)
                    print(f'Cycle {cycle}: applied {len(all_results)}/{len(records)} real district patches',flush=True)
            command('save-all flush\nstop');process.wait(timeout=60)
            if process.returncode:raise RuntimeError('Server shutdown failed')
        finally:
            if process.poll() is None:
                try:command('stop');process.wait(timeout=20)
                except (BrokenPipeError,subprocess.TimeoutExpired):process.terminate();process.wait(timeout=10)
            log.close()
        cycle_cells=0
        for name,original_hash in source_regions.items():
            original=read_region(source/'region'/name);saved=read_region(work/'world/region'/name)
            for key,tag in original.items():
                expected=block_volume(tag);p=decoded.get(key)
                if p:
                    for begin,count,old,target in p['runs']:
                        if not (expected[begin:begin+count]==p['palette'][old]).all():raise ValueError('Patch expectation differs from original')
                        expected[begin:begin+count]=p['palette'][target]
                if key==(0,0):
                    expected[index:index+4]=['minecraft:bricks','minecraft:diamond_block','minecraft:stone','minecraft:chest']
                    entities=[b for b in saved[key]['block_entities'] if (int(b['x']),int(b['y']),int(b['z']))==(5,900,2)]
                    if len(entities)!=1 or str(entities[0]['LootTable'])!='minecraft:chests/simple_dungeon':raise ValueError('Chest data changed')
                np.testing.assert_array_equal(block_volume(saved[key]),expected,err_msg=f'cycle {cycle}, chunk {key}')
                cycle_cells+=len(expected)
            if sha(source/'region'/name)!=original_hash:raise ValueError('Immutable source changed')
        compared+=cycle_cells
        runs.append({'cycle':cycle,'seconds':time.monotonic()-start,'saved_cells_compared':cycle_cells,
                     'probe_receipts':results,'source_unchanged':True})
        print(f'Cycle {cycle}: {cycle_cells:,} saved block cells match, including protected edits and chest data',flush=True)
    if sha(mod)!=sha(work/'mods'/mod.name):raise ValueError('Mod changed during verification')
    result={'passed':True,'mod_sha256':sha(mod),'two_load_save_cycles_verified':True,
        'saved_block_cells_compared':compared,'player_edit_preserved':True,'block_entity_preserved':True,
        'unowned_chunk_preserved':True,'replay_already_target_verified':True,'player_near_verified':False,
        'district_patches':len(all_results),'district_written_cells':sum(r['written'] for r in all_results),
        'queue_cap':128,'staged_manifest_sha256':sha(stage/'manifest.json'),'runs':runs}
    (work/'verification.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('work',type=Path);a=p.parse_args()
    print(json.dumps(check(a.work),indent=2))
