import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import zlib

import nbtlib as n
import numpy as np
from pyproj import Transformer

ROOT = Path('/Users/ericspencer/earthcraft-city-run-20260929')
STAGE = Path('/Volumes/LaCie/Earthcraft/runs/chicago-elmhurst-20260929/staged-world')
sys.path.insert(0, str(ROOT/'scripts'))
from region_expansion import records, sha, atomic, empty_unowned_record
from verify_metric_world import unpack
from world_replay import files_snapshot


def top_block(world, x, z):
    region = world/'region'/f'r.{x//512}.{z//512}.mca'
    table = records(region.read_bytes())
    record = table[(z//16 % 32)*32+x//16 % 32][0]
    mode, payload = record[4], record[5:]
    raw = zlib.decompress(payload) if mode == 2 else gzip.decompress(payload) if mode == 1 else payload
    tag = n.File.parse(io.BytesIO(raw))
    candidates = []
    for section in tag['sections']:
        state = section.get('block_states')
        if state is None:
            continue
        palette = state['palette']
        values = np.zeros(4096, dtype=int) if len(palette) == 1 else unpack(state['data'], max(4, (len(palette)-1).bit_length()), 4096)
        for local_y in range(16):
            index = int(values[local_y*256+(z%16)*16+x%16])
            block = str(palette[index]['Name'])
            if block not in ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'):
                candidates.append((int(section['Y'])*16+local_y, block))
    return max(candidates)


def audit_and_prepare():
    baseline = ROOT/'baseline-world'
    original = json.loads((ROOT/'original-snapshot.json').read_text())
    changed_allowed = {'level.dat', 'city-coverage.json', 'city-update-receipt.json', 'data/world_border.dat',
        'datapacks/earthcraft_travel/data/earthcraft/function/travel/city_2.mcfunction',
        'datapacks/earthcraft_travel/data/earthcraft/dialog/cities.json'}
    preserved_chunks = 0
    materialized_voids = 0
    region_hashes = {}
    void_report = json.loads((STAGE/'void-materialization.json').read_text())
    allowed_voids = {tuple(chunk) for chunks in void_report['regions'].values() for chunk in chunks}
    protected = set(json.loads((ROOT/'frozen-chunk-ownership.json').read_text())['chunks'])
    for number, name in enumerate(sorted(original), 1):
        path = baseline/name
        destination = STAGE/name
        if name.startswith('region/') and name.endswith('.mca'):
            raw = destination.read_bytes()
            before, after = records(path.read_bytes()), records(raw)
            region_hashes[name] = hashlib.sha256(raw).hexdigest()
            rx, rz = map(int, path.name.split('.')[1:3])
            for slot, value in before.items():
                cx, cz = rx*32+slot%32, rz*32+slot//32
                if after.get(slot) != value:
                    if ((cx, cz) not in allowed_voids or f'{cx},{cz}' in protected or
                            not empty_unowned_record(value)):
                        raise ValueError(f'Original generated or player chunk bytes changed: {name}/{slot}')
                    materialized_voids += 1
                else:
                    preserved_chunks += 1
        elif name not in changed_allowed and sha(destination) != original[name]:
            raise ValueError(f'Original non-region file changed: {name}')
        if number % 500 == 0:
            print('Preservation audit files', number, flush=True)
    before_player = n.load(baseline/'level.dat')['Data']['Player']
    after_player = n.load(STAGE/'level.dat')['Data']['Player']
    if before_player != after_player:
        raise ValueError('Original singleplayer player NBT changed')
    frame = json.loads((ROOT/'runs/chicago-adaptation-city-001/frame.json').read_text())
    project = Transformer.from_crs(4326, frame['crs'], always_xy=True)
    locations = [('water_tower', -87.62443, 41.8972), ('chicago_south', -87.6386, 41.6948),
                 ('chicago_west', -87.7565, 41.8820), ('chicago_north', -87.6689, 41.9800),
                 ('elmhurst_centre', -87.9403418, 41.8994745), ('elmhurst_north', -87.9415, 41.9230)]
    probes = []
    for label, lon, lat in locations:
        east, north = project.transform(lon, lat)
        x, z = math.floor(east-frame['west']), math.floor(frame['north']-north)
        y, block = top_block(STAGE, x, z)
        probes.append({'label': label, 'wgs84': [lon, lat], 'x': x, 'y': y, 'z': z, 'block': block})
    # Preserve the existing travel UI and controls; update only this destination.
    elmhurst = next(p for p in probes if p['label'] == 'elmhurst_centre')
    east, north = project.transform(-87.9403418, 41.8994745)
    travel = STAGE/'datapacks/earthcraft_travel/data/earthcraft'
    (travel/'function/travel/city_2.mcfunction').write_text(
        f'tp @s {east-frame["west"]} {elmhurst["y"]+2} {frame["north"]-north}\n'
        'tellraw @s {"text":"Arrived at Elmhurst, IL."}\n')
    cities_path = travel/'dialog/cities.json'
    cities = json.loads(cities_path.read_text())
    cities['body'] = [{'type': 'minecraft:plain_message', 'width': 280,
                       'contents': 'Travel to Chicago or Elmhurst.'}]
    cities_path.write_text(json.dumps(cities, indent=2))
    receipt = {'schema_version': 1, 'original_snapshot': original,
        'preserved_original_chunks': preserved_chunks, 'player_data_preserved': True,
        'previously_unowned_void_chunks_materialized': materialized_voids,
        'policy': 'Preserve generated and nonempty saved chunks and player data; fill unowned void chunks and add all verified Chicago and Elmhurst base tiles',
        'physical_accuracy_verified': False, 'complete_facade_appearance_verified': False}
    atomic(STAGE/'city-update-receipt.json', json.dumps(receipt, indent=2).encode())
    # Editing an existing exFAT file can recreate its AppleDouble wrapper.
    # Do this after the final metadata writes, preserving original archives.
    for path in STAGE.rglob('._*'):
        if path.relative_to(STAGE).as_posix() not in original:
            path.unlink()
    if list((STAGE/'datapacks').rglob('._*')):
        raise ValueError('New AppleDouble wrappers remain inside active datapacks')
    proof = ROOT/'runs/full-world-proof'
    if proof.exists():
        # Reuse the failed datapack-load input only after removing task-created
        # metadata wrappers. All files are independently compared below.
        for name in changed_allowed | {'city-update-receipt.json'}:
            (proof/name).write_bytes((STAGE/name).read_bytes())
        print('Reusing complete APFS proof after metadata-only repair', flush=True)
    else:
        required = sum(p.stat().st_size for p in STAGE.rglob('*') if p.is_file())
        if shutil.disk_usage(ROOT).free < required+20*2**30:
            raise ValueError('Preserve 20 GiB internal reserve during exact full-world proof copy')
        print('Copying complete staged save to APFS for exact Minecraft proof', required, flush=True)
        shutil.copytree(STAGE, proof)
    wanted = {f'r.{p["x"]//512}.{p["z"]//512}.mca' for p in probes}
    # Include the original spawn and the photo panels around the Water Tower.
    wanted.update({'r.-1.-1.mca', 'r.-1.0.mca', 'r.0.-1.mca', 'r.0.0.mca'})
    result = {'preserved_original_chunks': preserved_chunks, 'original_player_nbt_preserved': True,
              'previously_unowned_void_chunks_materialized': materialized_voids,
              'original_nonregion_files_preserved_except_border_coverage_and_elmhurst_travel': True,
              'probes': probes, 'sample_regions': sorted(wanted)}
    atomic(ROOT/'runs/preservation-and-probes.json', json.dumps(result, indent=2).encode())
    for progress in ('chicago-expansion.json', 'elmhurst-expansion.json'):
        state = json.loads((ROOT/'runs'/progress).read_text())
        for name, record in state['regions'].items():
            expected = record['sha256']
            if 'region/'+name in region_hashes and region_hashes['region/'+name] != expected:
                raise ValueError('Audited region differs from sealed assembly checksum')
            region_hashes['region/'+name] = expected
    return proof, probes, result, region_hashes


def server_check(proof, probes):
    work = ROOT/'runs/game-verification'
    work.mkdir(exist_ok=False)
    subprocess.run(['/bin/cp', '-cR', str(proof), str(work/'world')], check=True)
    if files_snapshot(work/'world') != files_snapshot(proof):
        raise ValueError('Minecraft verification clone differs from sealed proof input')
    (work/'eula.txt').write_text('eula=true\n')
    (work/'server.properties').write_text(
        'server-ip=127.0.0.1\nserver-port=25585\nlevel-name=world\nonline-mode=true\n'
        'gamemode=creative\ndifficulty=peaceful\nview-distance=2\nsimulation-distance=2\n'
        'max-players=1\nenable-rcon=false\nenable-query=false\nspawn-protection=0\n')
    java = '/opt/homebrew/opt/openjdk/bin/java'
    runs = []
    for cycle in (1, 2):
        events = queue.Queue()
        process = subprocess.Popen([java, '-Xms256M', '-Xmx1536M', '-jar',
            str(ROOT/'vendor/minecraft-server-1.21.10.jar'), 'nogui'], cwd=work,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        def reader():
            for line in process.stdout:
                events.put(line)
        threading.Thread(target=reader, daemon=True).start()
        start, ready_at, requested = time.monotonic(), None, False
        passed, errors = set(), []
        try:
            with (work/f'run-{cycle}.log').open('w') as log:
                while process.poll() is None or not events.empty():
                    if time.monotonic()-start > 180:
                        raise TimeoutError('Representative Minecraft check exceeded 180 seconds')
                    if ready_at and not requested and time.monotonic()-ready_at > 4:
                        lines = [f'execute if block {p["x"]} {p["y"]} {p["z"]} {p["block"]} run say EARTHCRAFT_PROBE_{p["label"]}' for p in probes]
                        process.stdin.write('\n'.join(lines+['save-all flush', 'stop'])+'\n')
                        process.stdin.flush()
                        requested = True
                    try:
                        line = events.get(timeout=.25)
                    except queue.Empty:
                        continue
                    log.write(line)
                    log.flush()
                    if 'ERROR' in line or 'Failed' in line:
                        errors.append(line.strip())
                    for p in probes:
                        if f'EARTHCRAFT_PROBE_{p["label"]}' in line and '[Server]' in line:
                            passed.add(p['label'])
                    if 'Done (' in line and ready_at is None:
                        ready_at = time.monotonic()
                        process.stdin.write('\n'.join(f'forceload add {p["x"]} {p["z"]}' for p in probes)+'\n')
                        process.stdin.flush()
                process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        report = {'cycle': cycle, 'ready': ready_at is not None, 'returncode': process.returncode,
                  'passed_probes': sorted(passed), 'errors': errors, 'seconds': time.monotonic()-start}
        runs.append(report)
        print(json.dumps(report), flush=True)
        if process.returncode != 0 or errors or passed != {p['label'] for p in probes}:
            raise ValueError('Minecraft load, block probe, or save/reload verification failed')
    return runs


proof, probes, result, region_hashes = audit_and_prepare()
result['minecraft_1_21_10'] = server_check(proof, probes)
result['full_save_load_save_reload_with_representative_block_probes_verified'] = True
result['whole_city_visual_accuracy_verified'] = False
atomic(ROOT/'runs/game-and-preservation-verification.json', json.dumps(result, indent=2).encode())
# Assembly already read back and hashed each immutable output region, and the
# independent preservation audit hashed every region holding original chunks.
# Reuse those sealed hashes; hash all other files directly. The APFS proof and receiving Mac
# independently reads and verifies every transferred file before publication.
manifest = {}
for path in sorted(STAGE.rglob('*')):
    if path.is_symlink():
        raise ValueError('Unexpected staging symlink')
    if path.is_file():
        name = path.relative_to(STAGE).as_posix()
        manifest[name] = region_hashes[name] if name in region_hashes else sha(path)
expected = {name: value for name, value in manifest.items() if name != 'server-verification.json'}
proof_hashes = {name: value for name, value in files_snapshot(proof).items()
                if name != 'server-verification.json'}
if proof_hashes != expected:
    raise ValueError('Exact full-world proof input differs from staged save')
checked = {'server_load_save_reload_verified': True, 'minecraft': '1.21.10',
    'verified_input_snapshot': expected, 'block_probes': result['probes'],
    'runs': result['minecraft_1_21_10'], 'physical_accuracy_verified': False,
    'client_visual_inspection_verified': False}
atomic(STAGE/'server-verification.json', json.dumps(checked, indent=2).encode())
manifest['server-verification.json'] = sha(STAGE/'server-verification.json')
atomic(ROOT/'runs/staged-files.json', json.dumps(manifest, indent=2).encode())
print('Completed verified staging manifest', len(manifest), flush=True)
