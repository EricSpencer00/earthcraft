"""Two bounded Fabric load/save cycles on a frozen excerpt, never the owner save.

Uses the installed importer binary and existing cached Minecraft dependencies.
Synthetic CAS controls are distinct from photo colors and from a player test.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

import nbtlib

from facade_world import BlockReader, EMPTY
from playable_acceptance_snapshot import sha
from live_city import publish
from inspect_world import chunks
from apply_building_stage_closed import mutate_chunk
from check_live_import import assert_same_blocks


SERVER_SHA1 = '95495a7f485eedd84ce928cef5e223b757d2f764'


def verify_snapshot(excerpt):
    proof = json.loads((excerpt/'snapshot.json').read_text())
    for name, record in proof['files'].items():
        path = excerpt/'world'/name
        if path.stat().st_size != record['bytes'] or sha(path) != record['sha256']:
            raise ValueError('Frozen save changed: '+name)
    return proof


def wait_for(process, predicate, seconds, label):
    deadline = time.monotonic()+seconds
    while not predicate():
        if process.poll() is not None:
            raise RuntimeError('Minecraft exited during '+label)
        if time.monotonic() > deadline:
            raise TimeoutError(label)
        time.sleep(.1)


def photo_entity_count(world):
    return sum('earthcraft_photo_skin' in entity.get('Tags', [])
               for path in (world/'entities').glob('*.mca') for _, tag, _ in chunks(path)
               for entity in tag.get('Entities', []))


def check(excerpt, work, assets, java, stage=None):
    excerpt, work, assets = map(lambda p: Path(p).resolve(), (excerpt, work, assets))
    proof = verify_snapshot(excerpt)
    staged=[]
    stage_sha=None
    if stage:
        stage=Path(stage)
        manifest=json.loads((stage/'manifest.json').read_text())
        if manifest.get('synthetic_fixture') is not True or manifest['snapshot_sha256']!=sha(excerpt/'snapshot.json') or manifest['frame']!=proof['binding_frame']:
            raise ValueError('Only a matching synthetic material fixture is admitted to this bounded checker')
        stage_sha=sha(stage/'manifest.json')
        for record in manifest['patches']:
            file=stage/'exchange/inbox'/(record['patch']+'.json.gz')
            if sha(file)!=record['patch']:raise ValueError('Staged fixture changed')
            packet=json.loads(gzip.decompress(file.read_bytes()))
            if packet['mode']!='building_delta' or packet['frame']!=proof['binding_frame']:raise ValueError('Staged fixture frame differs')
            staged.append(packet)
        if len(staged)>20 or sum(p['cells'] for p in staged)>50000:raise ValueError('Synthetic stage exceeds pilot bounds')
    jar = assets/'vendor/minecraft-server-1.21.10.jar'
    if hashlib.sha1(jar.read_bytes()).hexdigest() != SERVER_SHA1:
        raise ValueError('Pinned server checksum differs')
    if work.exists():
        raise FileExistsError('Keep native acceptance attempts separate')
    work.mkdir(parents=True)
    shutil.copytree(excerpt/'world', work/'world', copy_function=shutil.copyfile)
    for name in ('libraries', 'versions', '.fabric'):
        source = assets/'runs/building-delta-server-006'/name
        shutil.copytree(source, work/name, copy_function=shutil.copyfile)
    for folder in ('mods', 'config', 'exchange/inbox', 'exchange/receipts', 'exchange/archive'):
        (work/folder).mkdir(parents=True)
    mod = assets/'vendor/live/earthcraft-live-0.1.0.jar'
    shutil.copyfile(mod, work/'mods'/mod.name)
    api = assets/'runtime/traversal/mods/fabric-api-0.138.4+1.21.10.jar'
    shutil.copyfile(api, work/'mods'/api.name)
    shutil.copyfile(jar, work/'server.jar')
    (work/'eula.txt').write_text('eula=true\n')
    (work/'server.properties').write_text(
        'server-ip=127.0.0.1\nserver-port=25587\nlevel-name=world\nonline-mode=true\n'
        'max-players=1\nview-distance=2\nsimulation-distance=2\nenable-rcon=false\n'
        'enable-query=false\ngamemode=creative\ndifficulty=peaceful\nspawn-protection=0\n')
    original = BlockReader(excerpt/'world')
    support = {}
    for name, anchor in proof['anchors'].items():
        x, z = map(lambda v: int(v//1), anchor['world_xz'])
        support[name] = [original.support(x+dx, z+dz) for dx, dz in ((0,0), (16,0), (-16,0), (0,16), (0,-16))]
    # These probes report support, including failures; they cannot prove a walkable street.
    safe = [p for positions in support.values() for p in positions if p is not None]
    if not safe:
        raise ValueError('No dry supported checkpoint in frozen excerpts')
    x, _, z = safe[0]
    cx, cz = x//16, z//16
    x, z = cx*16+2, cz*16+2
    probe_cells = [(x+i, 900, z) for i in range(5)]
    if any(original.name(cell) not in EMPTY for cell in probe_cells):
        raise ValueError('Synthetic controls must be above empty source geometry')
    index = (900+64)*256+(z % 16)*16+x % 16
    patch = dict(version=1, frame=proof['binding_frame'], cx=cx, cz=cz, mode='building_delta',
                 palette=['minecraft:gray_concrete', 'minecraft:bricks', 'minecraft:air', 'minecraft:stone'],
                 runs=[[index,2,0,1], [index+2,3,2,3]], cells=5)
    unowned = dict(version=1, frame=proof['binding_frame'], cx=cx+400, cz=cz+400,
                   mode='building_delta', palette=['minecraft:air','minecraft:stone'],
                   runs=[[index,1,0,1]], cells=1)
    (work/'config/earthcraft-live.json').write_text(json.dumps({
        'world':str(work/'world'), 'exchange':str(work/'exchange'), 'frame':proof['binding_frame'],
        'protected_chunks':sorted({f'{cx},{cz}',*(f"{p['cx']},{p['cz']}" for p in staged)}), 'budget_ms':3}))
    with zipfile.ZipFile(excerpt/'world/resources.zip') as archive:
        photo_records = json.loads(archive.read('earthcraft-skin-manifest.json'))
    source_photo_count = photo_entity_count(excerpt/'world')
    photo_bounds = [min(r['cell'][0] for r in photo_records), min(r['cell'][2] for r in photo_records),
                    max(r['cell'][0] for r in photo_records), max(r['cell'][2] for r in photo_records)]
    functions = work/'world/datapacks/earthcraft_travel/data/earthcraft/function'
    (functions/'acceptance.mcfunction').write_text(
        'execute store result storage earthcraft:acceptance photo_count int 1 run execute if entity @e[type=minecraft:item_display,tag=earthcraft_photo_skin]\n'
        'execute positioned 0 900 0 summon minecraft:armor_stand run function earthcraft:acceptance_attributes\n')
    (functions/'acceptance_attributes.mcfunction').write_text(
        'function earthcraft:travel/apply_scale {scale:10}\n'
        'function earthcraft:travel/apply_speed {speed:2}\n'
        'execute store result storage earthcraft:acceptance scale int 1 run attribute @s minecraft:scale base get 100\n'
        'execute store result storage earthcraft:acceptance speed int 1 run attribute @s minecraft:movement_speed base get 100\nkill @s\n')
    runs = []
    (work/'verification.json').write_text(json.dumps({'passed':False, 'scope':'frozen excerpt; in progress'}))
    for cycle in (1,2):
        start = time.monotonic()
        logpath = work/f'run-{cycle}.log'
        with logpath.open('w') as log:
            process = subprocess.Popen([str(java), '-Xms256M', '-Xmx1536M', '-jar',
                str(assets/'vendor/live/fabric-server-launch.jar'), 'nogui'], cwd=work,
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)
            def command(value):
                process.stdin.write(value+'\n'); process.stdin.flush()
            try:
                wait_for(process, lambda:'Done (' in logpath.read_text() and '[Earthcraft live] attached' in logpath.read_text(), 120, 'startup')
                command('gamerule randomTickSpeed 0\ngamerule doMobSpawning false\ngamerule doWeatherCycle false')
                for positions in support.values():
                    for cell in positions:
                        if cell:
                            command(f'forceload add {cell[0]} {cell[2]}')
                command('forceload add '+' '.join(map(str, photo_bounds)))
                command(f'forceload add {x} {z}')
                if cycle == 1:
                    command(f'setblock {x} 900 {z} gray_concrete\nsetblock {x+1} 900 {z} diamond_block\n'
                            f'setblock {x+2} 900 {z} air\nsetblock {x+3} 900 {z} chest{{LootTable:"minecraft:chests/simple_dungeon"}}\n'
                            f'setblock {x+4} 900 {z} oak_log[axis=x]\nsay EARTHCRAFT_CONTROLS_READY')
                    wait_for(process, lambda:'EARTHCRAFT_CONTROLS_READY' in logpath.read_text(), 20, 'control setup')
                ids = [publish(work/'exchange', dict(p, provenance={'synthetic_control_cycle':cycle})) for p in (patch, unowned)]
                wait_for(process, lambda:all((work/'exchange/receipts'/f'{i}.json').exists() for i in ids), 30, 'current importer controls')
                receipts = [json.loads((work/'exchange/receipts'/f'{i}.json').read_text()) for i in ids]
                expected = (2,0,3) if cycle == 1 else (0,2,3)
                if tuple(receipts[0][k] for k in ('written','already_target','conflicts')) != expected or receipts[1]['result'] != 'unowned_chunk_preserved':
                    raise ValueError('Importer control receipts differ: '+str(receipts))
                for identity in ids:
                    (work/'exchange/inbox'/f'{identity}.json.gz').rename(work/'exchange/archive'/f'{identity}.json.gz')
                stage_receipts=[]
                for packet in staged:
                    identity=publish(work/'exchange',dict(packet,provenance=dict(packet['provenance'],native_fixture_cycle=cycle)))
                    wait_for(process,lambda:(work/'exchange/receipts'/f'{identity}.json').exists(),30,'compiled material packet')
                    receipt=json.loads((work/'exchange/receipts'/f'{identity}.json').read_text())
                    expected=(packet['cells'],0,0) if cycle==1 else (0,packet['cells'],0)
                    if tuple(receipt[k] for k in ('written','already_target','conflicts'))!=expected:
                        raise ValueError('Compiled material packet differs: '+str(receipt))
                    (work/'exchange/inbox'/f'{identity}.json.gz').rename(work/'exchange/archive'/f'{identity}.json.gz')
                    stage_receipts.append(receipt)
                # Wait for loaded entities, then store the actual runtime values in NBT.
                command('schedule function earthcraft:acceptance 40t replace')
                time.sleep(4)
                command('save-all flush\nstop')
                process.wait(timeout=45)
                if process.returncode:
                    raise RuntimeError('Minecraft did not stop cleanly')
            finally:
                if process.poll() is None:
                    try:
                        command('stop'); process.wait(timeout=15)
                    except (BrokenPipeError, subprocess.TimeoutExpired):
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()
        saved = BlockReader(work/'world')
        names = [saved.name(cell) for cell in probe_cells]
        if names != ['minecraft:bricks','minecraft:diamond_block','minecraft:stone','minecraft:chest','minecraft:oak_log']:
            raise ValueError('Saved importer control blocks differ')
        chest = [b for b in saved.chunk(cx,cz)['block_entities'] if tuple(int(b[k]) for k in ('x','y','z')) == probe_cells[3]]
        if len(chest) != 1 or str(chest[0]['LootTable']) != 'minecraft:chests/simple_dungeon' or str(saved.state(probe_cells[4])['Properties']['axis']) != 'x':
            raise ValueError('Saved block entity or properties were not preserved')
        values = nbtlib.load(work/'world/data/command_storage_earthcraft.dat')['data']['contents']['acceptance']
        if (int(values['scale']), int(values['speed'])) != (1000,200):
            raise ValueError('Travel functions failed in Minecraft')
        photo_count = int(values['photo_count'])
        if photo_count != source_photo_count or photo_entity_count(work/'world') != source_photo_count:
            raise ValueError('Existing photo displays were not preserved')
        unchanged = all(saved.state(tuple(cell)) == original.state(tuple(cell)) and
                        saved.name((cell[0],cell[1]+1,cell[2])) in EMPTY and
                        saved.name((cell[0],cell[1]+2,cell[2])) in EMPTY for cell in safe)
        if not unchanged:
            raise ValueError('Loaded support checkpoint changed')
        player_files = {name:sha(work/'world'/name) == record['sha256'] for name,record in proof['files'].items() if name.startswith('playerdata/')}
        if not all(player_files.values()):
            raise ValueError('Stored player data changed without a player session')
        stage_compared=0
        for packet in staged:
            key=(packet['cx'],packet['cz'])
            if key==(cx,cz):raise ValueError('Material fixture must avoid synthetic CAS control chunk')
            expected,counts=mutate_chunk(original.chunk(*key),packet)
            if counts['written']!=packet['cells']:raise ValueError('Closed writer rejected the compiled material packet')
            stage_compared+=assert_same_blocks(expected,saved.chunk(*key),'compiled facade fixture')
            if expected.get('block_entities',[])!=saved.chunk(*key).get('block_entities',[]):raise ValueError('Material fixture changed block entities')
        runs.append({'cycle':cycle, 'seconds':time.monotonic()-start, 'returncode':0,
                     'importer_receipts':receipts, 'existing_photo_display_entities':photo_count,
                     'compiled_material_receipts':stage_receipts,'closed_native_block_cells_compared':stage_compared,
                     'checkpoint_support_preserved':True, 'stored_player_files_unchanged':len(player_files),
                     'travel_attributes':{'scale':10,'speed':2,'subject':'test armor stand'}})
        print(f'Cycle {cycle}: current importer CAS, saved states, photo entities and travel functions verified', flush=True)
    verify_snapshot(excerpt)
    result = {'schema':'earthcraft.playable-acceptance.v1','passed':True,
              'passed_scope':'bounded server tests only','playable_world_acceptance_complete':False,
              'minecraft_version':'1.21.10','server_sha1':SERVER_SHA1,'mod_sha256':sha(mod),
              'snapshot_sha256':sha(excerpt/'snapshot.json'),'frame':proof['frame'],'binding_frame':proof['binding_frame'],
              'scope':'six native regions in a frozen Elmhurst/Water Tower excerpt',
              'source_save_modified':False,'frozen_excerpt_unchanged':True,'two_load_save_cycles_verified':True,
              'support_checkpoints':support,'dry_supported_checkpoints':len(safe),
              'synthetic_cas_fixture_only':True,'player_conflict_and_entity_and_properties_preserved':True,
              'unowned_chunk_preserved':True,'replay_verified':True,
              'new_photo_face_colors_applied':0,'client_render_tested':False,
              'photo_layer_declared_entities':len(photo_records),'photo_layer_saved_entities':source_photo_count,
              'photo_layer_declared_count_matches':source_photo_count == len(photo_records),
              'appearance_acceptance_blockers':([] if source_photo_count == len(photo_records) else
                  ['Historical photo display count differs from declared layer; preserved, not repaired in owner save']),
              'player_walkthrough_tested':False,'full_world_acceptance':False,'runs':runs}
    if stage_sha:
        result.update(synthetic_material_stage_sha256=stage_sha,
                      compiled_material_packets_verified=len(staged),compiled_material_cells=sum(p['cells'] for p in staged),
                      closed_writer_matches_native_saved_blocks=True)
    (work/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('excerpt','work','assets','java'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--stage',type=Path)
    a=p.parse_args(); result=check(a.excerpt,a.work,a.assets,a.java,a.stage)
    print(json.dumps({k:v for k,v in result.items() if k not in ('runs','frame')},indent=2))
