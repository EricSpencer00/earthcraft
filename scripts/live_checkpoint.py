"""One-time deployment checkpoint; preserve and verify every delivered chunk."""
import argparse
import fcntl
import gzip
import json
from pathlib import Path
import shutil
import time
import nbtlib
import numpy as np
from live_city import ROOT,atomic,sha
from city_save_update import read_region
from check_live_import import block_volume


def hold(exchange):
    with (exchange/'publisher.lock').open('a+') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        held=exchange/'held-for-upgrade';held.mkdir(exist_ok=False)
        for p in (exchange/'inbox').glob('*.gz'):p.rename(held/p.name)
        # The old importer may already have loaded one file; let it finish.
        for _ in range(40):
            time.sleep(.25)
            status=json.loads((exchange/'status.json').read_text())
            if status['state']=='ready':
                time.sleep(2)
                if json.loads((exchange/'status.json').read_text())['state']=='ready':return
        raise TimeoutError('Importer still active; do not close game')


def checkpoint(exchange):
    world=ROOT/'runtime/traversal/saves/Earthcraft'
    mod=ROOT/'vendor/live/earthcraft-live-0.1.0.jar'
    tested=ROOT/'runs/live-import-server-007-fast'
    if not json.loads((tested/'verification.json').read_text())['passed'] or sha(mod)!=sha(tested/'mods'/mod.name):raise ValueError('Exact binary verification required')
    with (world/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipts=[json.loads(p.read_text()) for p in (exchange/'receipts').glob('*.json')]
        groups={};scan_cells=0;chunks=0;painted=0
        for r in receipts:
            if r['result']!='applied_in_memory':raise ValueError(r)
            patch=None
            for folder in ('archive','held-for-upgrade','inbox'):
                p=exchange/folder/(r['patch']+'.json.gz')
                if p.exists():
                    if sha(p)!=r['patch']:raise ValueError('Patch changed')
                    patch=json.loads(gzip.decompress(p.read_bytes()));break
            if patch is None:raise FileNotFoundError(r['patch'])
            key=(patch['cx']//32,patch['cz']//32)
            groups.setdefault(key,[]).append((r,patch))
        for (rx,rz),items in groups.items():
            region=read_region(world/'region'/f'r.{rx}.{rz}.mca')
            before_region=None
            if any(p['mode']=='pavement' for _,p in items):
                before_world=ROOT/'archives/Earthcraft-before-live-updates-20260910'
                before_region=read_region(before_world/'region'/f'r.{rx}.{rz}.mca')
            for r,p in items:
                actual=block_volume(region[(p['cx'],p['cz'])])
                if p['mode']=='new_chunk':
                    expected=np.full(262144,'minecraft:air',object)
                    for start,n,v in p['runs']:expected[start:start+n]=p['palette'][v]
                    np.testing.assert_array_equal(actual,expected)
                    chunks+=1;scan_cells+=p['cells']
                    # Migrate old-version ownership without changing game files.
                    claim=exchange/f"claimed-{p['cx']},{p['cz']}.json"
                    if not claim.exists():atomic(claim,json.dumps({'chunk':r['chunk'],'patch':r['patch'],'verified_saved':True}).encode())
                else:
                    before=block_volume(before_region[(p['cx'],p['cz'])])
                    for start,n,v in p['runs']:
                        for i in range(start,start+n):
                            target=p['palette'][v] if before[i]=='minecraft:gray_concrete' else before[i]
                            if actual[i]!=target:raise ValueError('Pavement/player edit mismatch')
                    painted+=r['written']
        prior=nbtlib.load(ROOT/'archives/Earthcraft-before-live-updates-20260910/level.dat')['Data']['Player']
        current=nbtlib.load(world/'level.dat')['Data']['Player']
        for key in ('Pos','Rotation','Inventory','abilities'):
            if current[key]!=prior[key]:raise ValueError('Player state changed: '+key)
        if sha(world/'resources.zip')!=sha(ROOT/'archives/Earthcraft-before-live-updates-20260910/resources.zip'):raise ValueError('Photo resources changed')
        installed=ROOT/'runtime/traversal/mods'/mod.name
        shutil.copy2(installed,exchange/'initial-tested-mod.jar')
        shutil.copy2(mod,installed)
        if sha(installed)!=sha(mod):raise ValueError('Installed mod checksum failed')
        for p in (exchange/'held-for-upgrade').glob('*.gz'):
            dest=exchange/('archive' if (exchange/'receipts'/(p.name[:64]+'.json')).exists() else 'inbox')/p.name
            if dest.exists():raise FileExistsError(dest)
            p.rename(dest)
        result={'scan_chunks_saved_verified':chunks,'scan_cells_saved_verified':scan_cells,'pavement_recolors_verified':painted,
                'player_position_rotation_inventory_abilities_preserved':True,'photo_resource_pack_unchanged':True,
                'mod_sha256':sha(mod),'checks':'Every cell in each live new chunk; every attempted pavement delta',
                'physical_accuracy_verified':False,'world_files_written_by_checkpoint':False}
        atomic(exchange/'saved-checkpoint.json',json.dumps(result,indent=2).encode());print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['hold','checkpoint']);a=p.parse_args()
    {'hold':hold,'checkpoint':checkpoint}[a.action](ROOT/'runs/chicago-live-001')
