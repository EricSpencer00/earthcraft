"""Stage a single-world expansion, preserving all existing nonempty save chunks.

Never mutate an open save. Keep the accepted original tile wholesale (including
air/gaps/player edits), keep nonempty player chunks outside it, and add acquired
city chunks elsewhere. Publication uses a verified same-volume backup.
"""
import copy
import fcntl
import json
from pathlib import Path
import re
import shutil

import nbtlib as n
import numpy as np

from city_assemble import chunk_payload
from inspect_world import chunks
from metric_world import packed,region_write
from verify_metric_world import unpack
from world_replay import files_snapshot,file_hash


def read_region(path):
    if not path.exists():return {}
    match=re.fullmatch(r'r\.(-?\d+)\.(-?\d+)\.mca',path.name)
    if not match:raise ValueError('Invalid native region name')
    region=tuple(map(int,match.groups()));result={}
    for _,tag,_ in chunks(path):
        key=(int(tag['xPos']),int(tag['zPos']))
        if (key[0]//32,key[1]//32)!=region or key in result:raise ValueError('Misplaced or duplicate native chunk')
        result[key]=tag
    return result


def read_chunks(world):
    result={}
    for path in (world/'region').glob('r.*.*.mca'):
        for key,tag in read_region(path).items():
            if key in result:raise ValueError('Duplicate source chunk')
            result[key]=tag
    return result


def extend_height(tag,old_height,new_height):
    result=copy.deepcopy(tag)
    before=chunk_payload(result)
    if old_height!=new_height:
        for key,values in result.get('Heightmaps',{}).items():
            result['Heightmaps'][key]=packed(unpack(values,old_height.bit_length(),256),new_height.bit_length())
        # Discard only stale lighting; Minecraft computes light for the joined city.
        for section in result['sections']:
            for name in ('BlockLight','SkyLight'):section.pop(name,None)
        result['isLightOn']=n.Byte(0)
    if chunk_payload(result)!=before:raise ValueError('Height extension changed geographic blocks')
    return result


def pavement_overlay(records,painted_world,region=None):
    """Apply only explicit source-backed pavement colors, never overwrite edits."""
    path=painted_world/'ground-appearance.json'
    if not path.exists():return {'applied_cells':0,'source':None}
    appearance=json.loads(path.read_text());report=json.loads((painted_world/'earthcraft.json').read_text())
    if appearance['geometry_changed'] or appearance['llm_used']:raise ValueError('Material-only deterministic overlay required')
    table=appearance['palette'];codes=np.load(painted_world/'ground-appearance-blocks.npy')
    tops=np.load(painted_world/'top-heights.npy');ox,oz=report.get('world_offset_xz',[0,0])
    if codes.shape!=tops.shape or codes.max()>=len(table):raise ValueError('Invalid pavement overlay grid')
    changes=[];skipped=0;groups={}
    for z,x in np.argwhere(codes>0):
        block=table[int(codes[z,x])]
        if block=='gray_concrete':continue
        if not block.endswith('_concrete'):raise ValueError('Only opaque full-cube concrete colors may replace pavement')
        wx,wz=int(x+ox),int(z+oz);y=int(tops[z,x]);key=(wx//16,wz//16)
        if region is not None and (wx//512,wz//512)!=region:continue
        groups.setdefault((key,y//16),[]).append((wx,y,wz,block))
    for (key,sy),cells in groups.items():
        tag=records.get(key)
        section=next((s for s in tag['sections'] if int(s['Y'])==sy and 'block_states' in s),None) if tag else None
        if section is None:skipped+=len(cells);continue
        state=section['block_states'];palette=state['palette']
        values=np.zeros(4096,int) if len(palette)==1 else unpack(state['data'],max(4,(len(palette)-1).bit_length()),4096)
        for x,y,z,block in cells:
            slot=(y%16)*256+(z%16)*16+x%16
            if str(palette[int(values[slot])]['Name'])!='minecraft:gray_concrete':skipped+=1;continue
            label='minecraft:'+block;index=next((i for i,p in enumerate(palette) if str(p['Name'])==label),None)
            if index is None:index=len(palette);palette.append(n.Compound({'Name':n.String(label)}))
            values[slot]=index;changes.append([x,y,z,block])
        if len(palette)>1:state['data']=packed(values,max(4,(len(palette)-1).bit_length()))
    return {'applied_cells':len(changes),'changed_cells':changes,'edited_or_nonpavement_cells_retained':skipped,
        'source':str(path.resolve()),'source_manifest_sha256':file_hash(path),
        'geometry_changed':False,'facades_painted':False}


def generated_chunk_predicate(current,report):
    """An entirely cleared previously generated chunk is still a player edit."""
    path=current/'city-coverage.json'
    if path.exists():
        tiles=json.loads(path.read_text())['tiles'].values();addresses=set()
        for tile in tiles:
            x,z=tile['world_offset_xz']
            if tile['size_m']!=256 or x%256 or z%256:raise ValueError('Unsupported saved city coverage grid')
            addresses.add((x//256,z//256))
        return lambda key:(key[0]//16,key[1]//16) in addresses
    x,z=report.get('world_offset_xz',[0,0]);size=report['source']['size']
    return lambda key:x<=key[0]*16<x+size and z<=key[1]*16<z+size


def stage(current,generated,baseline,destination,material_source=None):
    if destination.exists():raise FileExistsError(destination)
    with (current/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        snapshot=files_snapshot(current)
        generated_snapshot=files_snapshot(generated)
        original=json.loads((current/'earthcraft.json').read_text())
        expanded=json.loads((generated/'earthcraft.json').read_text())
        if original['source']['crs']!=expanded['source']['crs'] or original['vertical_offset_m']!=expanded['vertical_offset_m']:
            raise ValueError('Installed and generated coordinate frames differ')
        accepted=set(read_chunks(baseline));missing_accepted=set(accepted)
        was_generated=generated_chunk_predicate(current,original)
        painted=material_source or generated
        appearance=painted/'ground-appearance.json';paint_regions=set();material_snapshot={}
        if appearance.exists():
            for name in ('ground-appearance.json','earthcraft.json','ground-appearance-blocks.npy','top-heights.npy'):
                material_snapshot[name]=file_hash(painted/name)
            p=json.loads((painted/'earthcraft.json').read_text());ox,oz=p.get('world_offset_xz',[0,0])
            codes=np.load(painted/'ground-appearance-blocks.npy')
            for z,x in np.argwhere(codes>0):paint_regions.add(((int(x)+ox)//512,(int(z)+oz)//512))
        # Copy player resources and other dimensions, but stream overworld
        # regions instead of retaining every city's inflated chunk NBT in RAM.
        def ignore_root(folder,names):
            return {'session.lock','region'} if Path(folder).resolve()==current.resolve() else set()
        shutil.copytree(current,destination,ignore=ignore_root)
        shutil.copytree(current/'region',destination/'region',ignore=shutil.ignore_patterns('*.mca'))
        region_names={p.name for world in (current,generated) for p in (world/'region').glob('r.*.*.mca')}
        preserved=set();new_count=retained_count=verified_chunks=peak_chunks=0;region_checks=[]
        material_overlay={'applied_cells':0,'source':None}
        for name in sorted(region_names):
            old=read_region(current/'region'/name);fresh=read_region(generated/'region'/name)
            missing_accepted.difference_update(old)
            keep={key for key,tag in old.items() if key in accepted or was_generated(key) or chunk_payload(tag) or tag.get('block_entities')}
            preserved.update(keep);new_count+=len(set(fresh)-keep);retained_count+=len(set(old)-set(fresh))
            combined={**old,**fresh}
            for key in keep:combined[key]=old[key]
            for key in (set(combined)-set(fresh))|keep:
                combined[key]=extend_height(combined[key],original['dimension_height'],expanded['dimension_height'])
            region=tuple(map(int,name.split('.')[1:3]))
            if region in paint_regions:
                update=pavement_overlay(combined,painted,region)
                if material_overlay['source'] is None:material_overlay=update
                else:
                    material_overlay['applied_cells']+=update['applied_cells']
                    material_overlay['edited_or_nonpavement_cells_retained']+=update['edited_or_nonpavement_cells_retained']
                    material_overlay['changed_cells'].extend(update['changed_cells'])
            expected={key:chunk_payload(tag) for key,tag in combined.items()}
            if combined:region_write(destination/'region'/name,[(x,z,tag) for (x,z),tag in combined.items()])
            actual={key:chunk_payload(tag) for key,tag in read_region(destination/'region'/name).items()}
            if actual!=expected:raise ValueError('Expanded save changed a source chunk')
            verified_chunks+=len(combined);peak_chunks=max(peak_chunks,len(combined))
            region_checks.append({'region':name,'verified_chunks':len(combined),'preserved_chunks':len(keep)})
        if missing_accepted:raise ValueError('Accepted source chunks missing from installed save')
        # exFAT represents Mac metadata as AppleDouble files. Minecraft treats
        # those as malformed namespaces/JSON if they enter an unpacked datapack.
        shutil.copytree(generated/'datapacks/earthcraft_height',destination/'datapacks/earthcraft_height',
            dirs_exist_ok=True,ignore=shutil.ignore_patterns('._*'))
        level=n.load(destination/'level.dat');player=copy.deepcopy(level['Data']['Player'])
        generated_level=n.load(generated/'level.dat')['Data']
        for field in ('BorderCenterX','BorderCenterZ','BorderSize','BorderSizeLerpTarget','BorderSizeLerpTime'):
            level['Data'][field]=generated_level[field]
        level['Data']['LevelName']=n.String('Earthcraft');level.save(destination/'level.dat')
        if level['Data']['Player']!=player:raise ValueError('Player data changed')
        expanded['spawn']=original['spawn'];expanded['spawn_rotation']=original.get('spawn_rotation',[0,0])
        expanded['installed_overlay']={'preserved_chunks':[list(key) for key in sorted(preserved)],
            'policy':'Keep accepted and previously generated chunks, including cleared player edits, and nonempty save geometry; add new city chunks and explicit pavement-color updates only where saved gray pavement remains',
            'original_report_sha256':file_hash(current/'earthcraft.json'),'player_data_preserved':True}
        expanded['installed_overlay']['pavement_material_update']=material_overlay
        (destination/'earthcraft.json').write_text(json.dumps(expanded,indent=2))
        # These are measurements of the generated base before the saved overlay.
        for name in ('city-assembly.json','city-coverage.json','block-overview.png'):
            if (generated/name).exists():shutil.copyfile(generated/name,destination/name)
        shutil.copyfile(generated/'block-verification.json',destination/'city-base-block-verification.json')
        # Do not leave the previous 64m sidecars looking like full-city evidence.
        legacy=destination/'pre-expansion-evidence'/file_hash(current/'earthcraft.json')[:16];legacy.mkdir(parents=True)
        for name in ('point-voxels.npy','classified-road-mask.npy','top-heights.npy','water-observations.npz',
                     'block-verification.json','server-verification.json','point-geometry.json'):
            path=destination/name
            if path.exists():path.rename(legacy/name)
        if files_snapshot(current)!=snapshot:raise ValueError('Installed save changed while staging')
        if files_snapshot(generated)!=generated_snapshot:raise ValueError('Generated city changed while staging; finish its current tile before taking a snapshot')
        if any(file_hash(painted/name)!=checksum for name,checksum in material_snapshot.items()):
            raise ValueError('Pavement source changed while staging')
        report={'original':str(current.resolve()),'generated':str(generated.resolve()),'baseline':str(baseline.resolve()),
            'original_snapshot':snapshot,'preserved_nonempty_or_accepted_chunks':len(preserved),
            'new_city_chunks':new_count,'retained_explored_chunks':retained_count,
            'streamed_region_checks':region_checks,'verified_chunks':verified_chunks,'peak_merged_region_chunks':peak_chunks,
            'all_retained_and_added_block_payloads_verified':True,'player_nbt_unchanged':True,
            'installed_world_changed':False,'full_chicago_complete':False,'pavement_material_update':material_overlay}
        (destination/'city-update-receipt.json').write_text(json.dumps(report,indent=2))
        return report


def publish(current,staged,backup):
    if backup.exists():raise FileExistsError(backup)
    if current.is_symlink() or staged.is_symlink():raise ValueError('Physical save and stage required')
    if current.stat().st_dev!=staged.stat().st_dev or current.stat().st_dev!=backup.parent.stat().st_dev:
        raise ValueError('Publication requires one filesystem; no unverified cross-volume move')
    receipt=json.loads((staged/'city-update-receipt.json').read_text())
    checked=json.loads((staged/'server-verification.json').read_text())
    if not checked['server_load_save_reload_verified']:raise ValueError('Verify expanded save in Minecraft before publication')
    actual={name:value for name,value in files_snapshot(staged).items() if name!='server-verification.json'}
    if checked.get('verified_input_snapshot')!=actual:raise ValueError('Staging differs from the exact world verified in Minecraft; recheck before publication')
    with (current/'session.lock').open('r+b') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if files_snapshot(current)!=receipt['original_snapshot']:raise ValueError('Player save changed; restage before publication')
        before=files_snapshot(staged)
        current.rename(backup)
        try:
            if files_snapshot(backup)!=receipt['original_snapshot']:raise ValueError('Backup verification failed')
            staged.rename(current)
        except Exception:
            if not current.exists():backup.rename(current)
            raise
        if files_snapshot(current)!=before:raise ValueError('Published save differs from verified staging')
    return {'installed':str(current),'backup':str(backup),'snapshot_verified':True,'only_one_installed_world':True}
