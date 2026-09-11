"""Load/save/reload an isolated copy in the pinned Mojang server, then stop."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import nbtlib
import zipfile

from photo_layer import layer_bounds, verify_saved_entities
from world_replay import files_snapshot

ROOT=Path(__file__).resolve().parents[1]


def find_java(explicit=None):
    """Find the Java runtime without depending on one developer's home path."""
    if explicit:
        return Path(explicit).expanduser()
    configured=os.environ.get('EARTHCRAFT_JAVA')
    if configured:
        return Path(configured).expanduser()
    candidates=(
        Path.home()/'Library/Application Support/minecraft/runtime/java-runtime-delta/mac-os-arm64/java-runtime-delta/jre.bundle/Contents/Home/bin/java',
        Path.home()/'Library/Application Support/minecraft/runtime/java-runtime-gamma/mac-os-arm64/java-runtime-gamma/jre.bundle/Contents/Home/bin/java',
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path('java')


def verified_snapshot(world):
    return {name:value for name,value in files_snapshot(world).items() if name!='server-verification.json'}


def check(world,work,travel_probe=False,photo_probe=False,java=None):
    before=verified_snapshot(world)
    jar=ROOT/'vendor/minecraft-server-1.21.10.jar'
    if hashlib.sha1(jar.read_bytes()).hexdigest()!='95495a7f485eedd84ce928cef5e223b757d2f764':
        raise ValueError('Mojang server hash mismatch')
    work.mkdir(parents=True,exist_ok=False)
    shutil.copytree(world,work/'world')
    if photo_probe:
        with zipfile.ZipFile(world/'resources.zip') as archive:
            photo_records=json.loads(archive.read('earthcraft-skin-manifest.json'))
        photo_rectangle=' '.join(map(str,layer_bounds(photo_records)))
        probe=work/'world/datapacks/earthcraft_photo_skin/data/earthcraft_skin/function/probe.mcfunction'
        probe.write_text('execute store result storage earthcraft:verification photo_count int 1 run execute if entity @e[type=minecraft:item_display,tag=earthcraft_photo_skin]\n'+
                         'data get storage earthcraft:verification photo_count\n')
        # The layer intentionally releases its chunks after creation. Reload
        # them for the runtime count instead of mistaking unloaded entities for
        # absent entities. Keep production layer commands unchanged.
        (probe.parent/'probe_load.mcfunction').write_text(
            f'forceload add {photo_rectangle}\n'+
            'schedule function earthcraft_skin:probe 10t replace\n')
    if travel_probe:
        probe=work/'world/datapacks/earthcraft_travel/data/earthcraft/function/travel/probe.mcfunction'
        probe.write_text('\n'.join([
            'function earthcraft:travel/apply_scale {scale:10}',
            'execute store result storage earthcraft:verification scale int 1 run attribute @s minecraft:scale base get 100',
            'function earthcraft:travel/apply_speed {speed:2}',
            'execute store result storage earthcraft:verification speed int 1 run attribute @s minecraft:movement_speed base get 100',
            'kill @s'])+'\n')
    (work/'eula.txt').write_text('eula=true\n')
    (work/'server.properties').write_text('server-ip=127.0.0.1\nserver-port=25585\nlevel-name=world\nonline-mode=true\ngamemode=creative\ndifficulty=peaceful\nview-distance=3\nsimulation-distance=3\nmax-players=1\nenable-rcon=false\nenable-query=false\nspawn-protection=0\n')
    runs=[]
    java=find_java(java)
    for run in (1,2):
        start=time.monotonic(); lines=queue.Queue()
        process=subprocess.Popen([str(java),'-Xms512M','-Xmx2G','-jar',str(jar),'nogui'],cwd=work,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        def reader():
            for line in process.stdout:lines.put(line)
        thread=threading.Thread(target=reader,daemon=True);thread.start()
        ready=False;error_lines=[];ready_at=None;save_requested=False
        try:
            with (work/f'run-{run}.log').open('w') as log:
                while process.poll() is None or not lines.empty():
                    if time.monotonic()-start>180:
                        raise TimeoutError('Server load/save exceeded 180 seconds')
                    if photo_probe and ready_at is not None and not save_requested and time.monotonic()-ready_at>=5:
                        # Function feedback can be disabled by the source world's
                        # gamerules. Read persisted NBT, not a console-message cue.
                        process.stdin.write(f'forceload remove {photo_rectangle}\nsave-all flush\nstop\n')
                        process.stdin.flush();save_requested=True
                    try:line=lines.get(timeout=.25)
                    except queue.Empty:continue
                    log.write(line);log.flush()
                    if 'ERROR' in line or 'Failed' in line:error_lines.append(line.strip())
                    if 'Done (' in line and not ready:
                        ready=True
                        ready_at=time.monotonic()
                        if travel_probe:
                            process.stdin.write('execute positioned 0 80 0 summon minecraft:armor_stand run function earthcraft:travel/probe\n')
                        if photo_probe:
                            process.stdin.write(f'forceload add {photo_rectangle}\n'+
                                'execute unless data storage earthcraft_skin:state {installed:1b} run function earthcraft_skin:prepare\n'+
                                'schedule function earthcraft_skin:probe_load 40t replace\n')
                        else:
                            process.stdin.write('save-all flush\nstop\n')
                        process.stdin.flush()
                        print(f'Pass {run}: world loaded; verifying and saving',flush=True)
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        run_result={'run':run,'ready':ready,'returncode':process.returncode,'seconds':time.monotonic()-start,'errors':error_lines}
        if photo_probe and ready and process.returncode==0:
            stored=nbtlib.load(work/'world/data/command_storage_earthcraft.dat')
            count=int(stored['data']['contents']['verification']['photo_count'])
            expected=json.loads((world/'photo-skin.json').read_text())['display_entities']
            run_result['photo_display_count']=count
            if count!=expected:
                error_lines.append(f'Photo display count {count} != expected {expected}')
            try:
                run_result['saved_photo_entities']=verify_saved_entities(work/'world',photo_records)
            except ValueError as error:
                error_lines.append(str(error))
        runs.append(run_result)
        (work/'verification.json').write_text(json.dumps({'runs':runs},indent=2))
        if not ready or process.returncode or error_lines:
            raise RuntimeError(f'Server check failed; inspect {work}/run-{run}.log')
    if verified_snapshot(world)!=before:raise ValueError('Source world changed during isolated game verification')
    result={'minecraft_version':'1.21.10','server_load_save_reload_verified':True,
            'verified_input_snapshot':before,
            'client_visual_verification':False,'runs':runs}
    if travel_probe:
        stored=nbtlib.load(work/'world/data/command_storage_earthcraft.dat')
        values=stored['data']['contents']['verification']
        if int(values['scale'])!=1000 or int(values['speed'])!=200:
            raise ValueError('Travel attribute runtime probe failed')
        result['travel_attribute_runtime_probe']={'scale':10,'movement_speed':2,
            'entity':'isolated test armor stand; not a player gameplay test'}
    if photo_probe:
        stored=nbtlib.load(work/'world/data/command_storage_earthcraft.dat')
        count=int(stored['data']['contents']['verification']['photo_count'])
        expected=json.loads((world/'photo-skin.json').read_text())['display_entities']
        if count!=expected:
            raise ValueError(f'Photo display runtime count {count} != expected {expected}')
        result['photo_display_runtime_probe']={'count':count,'expected':expected,
            'scope':'Server creation and persistence only; resource pack rendering requires client verification.'}
    (world/'server-verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='verified_input_snapshot'},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--travel-probe',action='store_true')
    parser.add_argument('--photo-probe',action='store_true')
    parser.add_argument('--java',help='Java executable; otherwise EARTHCRAFT_JAVA or the local Minecraft runtime is used')
    args=parser.parse_args();check(args.world,args.work.resolve(),args.travel_probe,args.photo_probe,args.java)
