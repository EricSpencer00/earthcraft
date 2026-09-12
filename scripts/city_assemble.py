"""Join independently built, non-overlapping city tiles without moving blocks."""
import copy
import json
from pathlib import Path
import shutil

import nbtlib as n
import numpy as np

from inspect_world import chunks
from metric_world import region_write
from metric_frame import tile_layout
from world_replay import canonical_section,file_hash
from verify_metric_world import verify
from world_border import bounds_for_tiles, update_world_border


def chunk_payload(tag):
    air=canonical_section({'block_states':{'palette':[{'Name':'minecraft:air'}]}})
    result={}
    for section in tag['sections']:
        # Saved Minecraft chunks can contain lighting-only sections, and added
        # empty sections must not masquerade as player edits or geometry changes.
        if 'block_states' not in section:continue
        value=canonical_section(section)
        if value!=air:result[int(section['Y'])]=value
    return result


def assemble(worlds,source,destination,spawn_world=None):
    destination=Path(destination)
    if destination.exists():raise FileExistsError(destination)
    reports=[json.loads((w/'earthcraft.json').read_text()) for w in worlds]
    frame=reports[0]['world_frame']
    if frame is None or any(r['world_frame']!=frame for r in reports):raise ValueError('One common frame required')
    meta=json.loads((source/'sources.json').read_text());size=meta['size']
    if not 16<=size<=1024:raise ValueError('Bounded rectangular assembly required')
    # tile_layout deliberately bounds individual workers at 512 m.
    ox=int(meta['west']-frame['west']);oz=int(frame['north']-meta['north'])
    if meta['crs']!=frame['crs'] or ox%16 or oz%16:raise ValueError('Assembly grid differs from city frame')
    selected=worlds.index(spawn_world) if spawn_world else 0
    blocks={};expected={};points=[];roads=np.zeros((size,size),bool);owners=np.zeros((size,size),np.uint8)
    buildings={}
    for world,report in zip(worlds,reports):
        tx,tz=report['world_offset_xz'];x,z=tx-ox,tz-oz;s=report['source']['size']
        if min(x,z)<0 or max(x+s,z+s)>size:raise ValueError('Tile outside assembly grid')
        owners[z:z+s,x:x+s]+=1
        for path in (world/'region').glob('r.*.*.mca'):
            for _,tag,_ in chunks(path):
                key=(int(tag['xPos']),int(tag['zPos']))
                if key in expected:raise ValueError('Overlapping generated chunks')
                expected[key]=chunk_payload(tag)
                blocks.setdefault((key[0]//32,key[1]//32),[]).append((*key,tag))
        points.append(np.load(world/'point-voxels.npy')+[x,0,z])
        if (world/'classified-road-mask.npy').exists():roads[z:z+s,x:x+s]=np.load(world/'classified-road-mask.npy')
        for b in report['buildings']:buildings[b['county_objectid']]=b
    if not (owners==1).all():raise ValueError('Missing or overlapping metre cells')
    destination.mkdir(parents=True);(destination/'region').mkdir()
    for key,records in blocks.items():region_write(destination/'region'/f'r.{key[0]}.{key[1]}.mca',records)
    shutil.copytree(worlds[selected]/'datapacks',destination/'datapacks')
    level=n.load(worlds[selected]/'level.dat');data=level['Data']
    data['LevelName']=n.String('Earthcraft')
    level.save(destination/'level.dat')
    update_world_border(destination, bounds_for_tiles([{'world_offset_xz':[ox,oz],'size_m':size}]))
    report=dict(reports[selected],source=meta,world_offset_xz=[ox,oz],chunks=len(expected),
        dem_path=str((source/meta.get('elevation_raster','usgs-elevation.tif')).resolve()),buildings=list(buildings.values()),
        assembly={'tiles':[str(w.resolve()) for w in worlds],'generated_area_m2':size**2,
                  'full_chicago_complete':False,'all_chunk_coordinates_unchanged':True})
    # Tile-local geometry provenance lives with each tile, not only the spawn tile.
    report.pop('point_geometry_source',None)
    report['point_geometry_tiles']=[str(w.resolve()/'point-geometry.json') for w in worlds]
    (destination/'earthcraft.json').write_text(json.dumps(report,indent=2))
    np.save(destination/'point-voxels.npy',np.concatenate(points))
    np.save(destination/'classified-road-mask.npy',roads)
    actual={}
    for path in (destination/'region').glob('r.*.*.mca'):
        for _,tag,_ in chunks(path):actual[(int(tag['xPos']),int(tag['zPos']))]=chunk_payload(tag)
    if actual!=expected:raise ValueError('Joined chunks differ from independently built tiles')
    checked=verify(destination)
    (destination/'city-assembly.json').write_text(json.dumps({'tiles':[str(w.resolve()) for w in worlds],
        'tile_manifests':{str(w.resolve()):file_hash(w/'earthcraft.json') for w in worlds},
        'chunks':len(actual),'blocks_equal_source_tiles':True,'checks':checked,
        'source_area_m2':size**2,'full_chicago_complete':False},indent=2))
    return report
