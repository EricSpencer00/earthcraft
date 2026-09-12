"""Isolated real Fabric server check: deliver source chunks AFTER server startup."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import sys
import numpy as np

from live_city import ROOT,encode_chunk,publish,sha,atomic
from city_save_update import read_region
from verify_metric_world import unpack
from local_paths import bulk_path


def block_volume(tag):
    v=np.full(262144,'minecraft:air',dtype=object)
    for s in tag['sections']:
        if 'block_states' not in s:continue
        b=s['block_states'];p=np.array([str(x['Name']) for x in b['palette']],object)
        ids=np.zeros(4096,int) if len(p)==1 else unpack(b['data'],max(4,(len(p)-1).bit_length()),4096)
        start=(int(s['Y'])+4)*4096
        if 0<=start<=len(v)-4096:v[start:start+4096]=p[ids]
    return v


def check(work):
    work.mkdir(parents=True,exist_ok=False)
    # Closed fixture only; the installed save is never copied while active.
    shutil.copytree(ROOT/'worlds/Earthcraft-Chicago-Ground-Color-002',work/'world')
    for name in ('mods','config','exchange/inbox','exchange/receipts','exchange/archive'):(work/name).mkdir(parents=True)
    shutil.copy2(ROOT/'vendor/live/earthcraft-live-0.1.0.jar',work/'mods')
    shutil.copy2(ROOT/'runtime/traversal/mods/fabric-api-0.138.4+1.21.10.jar',work/'mods')
    shutil.copy2(ROOT/'vendor/minecraft-server-1.21.10.jar',work/'server.jar')
    (work/'eula.txt').write_text('eula=true\n')
    (work/'server.properties').write_text('server-ip=127.0.0.1\nserver-port=25585\nlevel-name=world\nonline-mode=true\ngamemode=creative\ndifficulty=peaceful\nview-distance=2\nsimulation-distance=2\nmax-players=1\nenable-rcon=false\n')
    binding={'world':str(work/'world'),'exchange':str(work/'exchange'),'frame':'live-test',
             'budget_ms':8,'protected_chunks':['0,0']}
    (work/'config/earthcraft-live.json').write_text(json.dumps(binding))
    # A real source outside the fixture, at unchanged geospatial coordinates.
    source=bulk_path('chicago','city-tiles-001','-1_4','world')
    record=json.loads((source.parent/'geometry-receipt.json').read_text())
    region=next((source/'region').glob('r.*.*.mca'))
    if sha(region)!=record['regions'][region.name]:raise ValueError('Changed source region')
    originals=read_region(region);key=sorted(originals)[0]
    patch=encode_chunk(originals[key],'live-test',{'source':str(source),'region_sha256':sha(region)})
    java='/opt/homebrew/opt/openjdk@21/bin/java';runs=[]
    for cycle in (1,2):
        log=(work/f'run-{cycle}.log').open('w');start=time.monotonic()
        process=subprocess.Popen([java,'-Xms512M','-Xmx2G','-jar',str(ROOT/'vendor/live/fabric-server-launch.jar'),'nogui'],
                                 cwd=work,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,text=True)
        try:
            while time.monotonic()-start<180:
                text=(work/f'run-{cycle}.log').read_text()
                if process.poll() is not None:raise RuntimeError('Server exited: '+text[-2500:])
                if '[Earthcraft live] attached' in text and 'Done (' in text:break
                time.sleep(.5)
            else:raise TimeoutError('Fabric server startup')
            loaded=time.monotonic()
            if cycle==1:
                identity=publish(work/'exchange',patch)
                # A known protected chunk and a nonempty chunk must stay unchanged.
                for cx in (0,1):
                    p=dict(version=1,frame='live-test',cx=cx,cz=0,mode='new_chunk',palette=['minecraft:stone'],runs=[[262143,1,0]],cells=1)
                    publish(work/'exchange',p)
                # A material-only CAS update must preserve a diamond player edit.
                process.stdin.write('forceload add 0 0 31 15\n');process.stdin.flush()
                time.sleep(1)
                process.stdin.write('setblock 2 200 2 minecraft:gray_concrete\nsetblock 3 200 2 minecraft:diamond_block\n');process.stdin.flush()
                time.sleep(.5)
                p=dict(version=1,frame='live-test',cx=0,cz=0,mode='pavement',palette=['minecraft:white_concrete'],
                       runs=[[(200+64)*256+2*16+2,2,0]],cells=2)
                paint_id=publish(work/'exchange',p)
                while time.monotonic()-loaded<120:
                    receipts=list((work/'exchange/receipts').glob('*.json'))
                    if len(receipts)==4:break
                    time.sleep(.2)
                else:raise TimeoutError('Live import did not finish')
                receipt=json.loads((work/'exchange/receipts'/f'{identity}.json').read_text())
                if receipt['result']!='applied_in_memory' or receipt['written']!=patch['cells']:raise ValueError(receipt)
                paint_result=json.loads((work/'exchange/receipts'/f'{paint_id}.json').read_text())
                if paint_result['written']!=1 or paint_result['skipped']!=1:raise ValueError(paint_result)
            else:time.sleep(2)
            if cycle==1:
                # Quit during a newly started real chunk: the lifecycle callback
                # must finish it before Minecraft performs its normal save.
                second_key=sorted(originals)[1]
                second_patch=encode_chunk(originals[second_key],'live-test',{'source':str(source),'quit_probe':True})
                second_id=publish(work/'exchange',second_patch)
                deadline=time.monotonic()+10
                # New chunks use their durable ownership claim as the single
                # interruption fence; other mutation modes retain patch markers.
                claim=work/'exchange'/f'claimed-{second_key[0]},{second_key[1]}.json'
                while not claim.exists():
                    if time.monotonic()>deadline:raise TimeoutError('Quit probe never began')
                    time.sleep(.005)
                atomic(work/'exchange/pause',b'operator pause')
            process.stdin.write('save-all flush\nstop\n');process.stdin.flush();process.wait(timeout=60)
            if process.returncode:raise RuntimeError('Server failed at shutdown')
        finally:
            if process.poll() is None:
                process.stdin.write('stop\n');process.stdin.flush()
                try:process.wait(timeout=20)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
            log.close()
        actual=read_region(work/'world/region'/f'r.{key[0]//32}.{key[1]//32}.mca')[key]
        np.testing.assert_array_equal(block_volume(actual),block_volume(originals[key]))
        second=read_region(work/'world/region'/f'r.{second_key[0]//32}.{second_key[1]//32}.mca')[second_key]
        np.testing.assert_array_equal(block_volume(second),block_volume(originals[second_key]))
        region0=read_region(work/'world/region/r.0.0.mca')
        cells=block_volume(region0[(0,0)])
        assert cells[(200+64)*256+34]=='minecraft:white_concrete'
        assert cells[(200+64)*256+35]=='minecraft:diamond_block'
        assert cells[-1]=='minecraft:air' and block_volume(region0[(1,0)])[-1]=='minecraft:air'
        runs.append({'cycle':cycle,'seconds':time.monotonic()-start,'live_phase_seconds':time.monotonic()-loaded,
                     'native_chunk_cells_compared':524288,'protected_chunk':True,'player_edit_preserved':True,'quit_during_import_preserved':True})
        print(json.dumps(runs[-1]),flush=True)
    result={'passed':True,'runs':runs,'source_chunk':key,'source_cells':patch['cells'],
            'receipt':receipt,'physical_accuracy_verified':False,'client_visual_verified':False}
    (work/'verification.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('work',type=Path);a=p.parse_args();print(json.dumps(check(a.work.resolve()),indent=2))
