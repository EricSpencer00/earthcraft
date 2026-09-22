"""Apply source-backed water surfaces without replacing existing save chunks.

The continuous Chicago save already owns the region files around the lake and
river.  This overlay changes only an empty-air-above, mapped ground surface to
water; structures, player blocks, and unsupported floating water positions are
retained and counted as skipped.
"""
import argparse
import io
import json
from pathlib import Path
import zlib

import nbtlib as n
import numpy as np

from metric_world import packed
from verify_metric_world import unpack


GROUND_SURFACES={'minecraft:stone','minecraft:dirt','minecraft:grass_block',
                 'minecraft:sand','minecraft:gravel','minecraft:clay'}


def _state(tag,x,y,z):
    for section in tag.get('sections',[]):
        if int(section['Y'])*16<=y<int(section['Y'])*16+16 and 'block_states' in section:
            state=section['block_states'];palette=state['palette']
            values=(np.zeros(4096,dtype=int) if len(palette)==1 else
                    unpack(state['data'],max(4,(len(palette)-1).bit_length()),4096))
            slot=(y%16)*256+(z%16)*16+(x%16)
            return str(palette[int(values[slot])]['Name']),section,state,values,slot
    return 'minecraft:air',None,None,None,None


def _encode_chunk(tag):
    stream=io.BytesIO();tag.write(stream);compressed=zlib.compress(stream.getvalue())
    sector=(len(compressed)+1).to_bytes(4,'big')+b'\x02'+compressed
    return sector+b'\0'*(-len(sector)%4096)


def _surface_top(tag):
    tops=np.full((16,16),-64,dtype=int)
    for section in tag.get('sections',[]):
        if 'block_states' not in section:continue
        state=section['block_states'];palette=state['palette']
        values=(np.zeros(4096,dtype=int) if len(palette)==1 else
                unpack(state['data'],max(4,(len(palette)-1).bit_length()),4096))
        names=np.array([str(p['Name']) for p in palette],dtype=object)
        solid=names[values].reshape(16,16,16)!='minecraft:air'
        y0=int(section['Y'])*16
        for local_y in range(16):
            tops=np.where(solid[local_y],y0+local_y,tops)
    return tops


def overlay(world, water_source, world_offset_xz, region_name=None, align_to_current_surface=False):
    world=Path(world);water_source=Path(water_source)
    data=np.load(water_source)
    mask=np.asarray(data['mask'],dtype=bool);tops=np.asarray(data['top_y'],dtype=int)
    if mask.shape!=tops.shape or mask.ndim!=2:
        raise ValueError('Water source mask/top_y shape mismatch')
    ox,oz=map(int,world_offset_xz)
    region_dir=world/'region'
    region_name=region_name or f'r.{(ox//16)//32}.{(oz//16)//32}.mca'
    path=region_dir/region_name
    raw=path.read_bytes()
    if len(raw)<8192 or len(raw)%4096:raise ValueError('Invalid Anvil region')
    cells_by_chunk={}
    for local_z,local_x in np.argwhere(mask):
        wx,wz=ox+int(local_x),oz+int(local_z);y=int(tops[local_z,local_x])
        cells_by_chunk.setdefault((wx//16,wz//16),[]).append((wx,wz,y))
    changed={};counts={'water_cells':int(mask.sum()),'changed_cells':0,
                        'already_water':0,'protected_or_unsupported':0,
                        'changed_chunks':0}
    modified_keys=set()
    for index in range(1024):
        offset=int.from_bytes(raw[index*4:index*4+3],'big')*4096
        if not offset:continue
        count=raw[index*4+3]
        length=int.from_bytes(raw[offset:offset+4],'big')
        if raw[offset+4]!=2:raise ValueError('Expected zlib-compressed Anvil chunk')
        tag=n.File.parse(io.BytesIO(zlib.decompress(raw[offset+5:offset+4+length])))
        key=(int(tag['xPos']),int(tag['zPos']))
        if key not in cells_by_chunk:continue
        current_tops=_surface_top(tag) if align_to_current_surface else None
        pending_states={}
        for wx,wz,y in cells_by_chunk[key]:
            if current_tops is not None:y=int(current_tops[wz%16,wx%16])
            state_key=(key,y//16)
            current,_,_,_,_= _state(tag,wx,y,wz)
            if current=='minecraft:water':
                counts['already_water']+=1
                continue
            above,_,_,_,_= _state(tag,wx,y+1,wz)
            if current not in GROUND_SURFACES or above!='minecraft:air':
                counts['protected_or_unsupported']+=1
                continue
            if state_key not in pending_states:
                current,section,state,values,slot=_state(tag,wx,y,wz)
                if section is None:
                    counts['protected_or_unsupported']+=1
                    continue
                pending_states[state_key]=(state,values)
            state,values=pending_states[state_key]
            section=next(section for section in tag['sections']
                         if int(section['Y'])==y//16 and 'block_states' in section)
            palette=state['palette'];slot=(y%16)*256+(wz%16)*16+(wx%16)
            current=str(palette[int(values[slot])]['Name'])
            if current=='minecraft:water':
                counts['already_water']+=1
                continue
            index_water=next((i for i,p in enumerate(palette)
                              if str(p['Name'])=='minecraft:water'),None)
            if index_water is None:
                index_water=len(palette);palette.append(n.Compound({'Name':n.String('minecraft:water')}))
            values[slot]=index_water;counts['changed_cells']+=1;modified_keys.add(key)
        for state,values in pending_states.values():
            if len(state['palette'])>1:
                state['data']=packed(values,max(4,(len(state['palette'])-1).bit_length()))
        if key in modified_keys:changed[key]=tag
    counts['changed_chunks']=len(modified_keys)
    header=bytearray(raw[:8192]);body=bytearray();existing_chunks=0
    for index in range(1024):
        offset=int.from_bytes(raw[index*4:index*4+3],'big')*4096
        if not offset:continue
        count=raw[index*4+3];existing_chunks+=1
        if index in (key[0]%32+(key[1]%32)*32 for key in changed):
            key=next(key for key in changed if key[0]%32+key[1]%32*32==index)
            sector=_encode_chunk(changed[key])
        else:
            sector=raw[offset:offset+count*4096]
        new_offset=2+len(body)//4096
        header[index*4:index*4+3]=new_offset.to_bytes(3,'big');header[index*4+3]=len(sector)//4096
        body.extend(sector)
    temporary=path.with_suffix('.mca.partial');temporary.write_bytes(header+body);temporary.replace(path)
    result={'schema':'earthcraft-water-overlay-v1','world':str(world.resolve()),
            'water_source':str(water_source.resolve()),'world_offset_xz':[ox,oz],
            'region':region_name,**counts,'existing_chunks_retained':existing_chunks,
            'geometry_outside_water_mask_changed':False,
            'align_to_current_surface':align_to_current_surface}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world',type=Path,required=True)
    parser.add_argument('--water-source',type=Path,required=True)
    parser.add_argument('--world-x',type=int,required=True)
    parser.add_argument('--world-z',type=int,required=True)
    parser.add_argument('--region')
    parser.add_argument('--align-to-current-surface',action='store_true')
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    report=overlay(args.world,args.water_source,(args.world_x,args.world_z),args.region,
                   args.align_to_current_surface)
    if args.receipt:args.receipt.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
