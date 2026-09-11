"""Route dated, georeferenced image colors onto existing observed pavement only.

Finite vanilla palette: no per-block textures, models, LLMs, new geometry or
fabricated facade details. The image is an observation, not measured reflectance.
"""
import copy
import json
from pathlib import Path
import shutil

import nbtlib as n
import numpy as np

from cook_city_cache import sha
from inspect_world import chunks
from material_router import texture_colors
from metric_world import packed,region_write
from verify_metric_world import unpack,verify
from photo_layer import verify_layer


def nearest_colors(rgb,palette):
    rgb=np.asarray(rgb,dtype=float);palette=np.asarray(palette,dtype=float)
    if rgb.shape[-1]!=3 or palette.ndim!=2 or palette.shape[1]!=3 or not np.isfinite(rgb).all() or not np.isfinite(palette).all():
        raise ValueError('Finite RGB triplets required')
    def linear(values):
        values=values/255
        return np.where(values<=.04045,values/12.92,((values+.055)/1.055)**2.4)
    distances=np.sum((linear(rgb)[...,None,:]-linear(palette))**2*np.array([.2126,.7152,.0722]),axis=-1)
    return distances.argmin(axis=-1)


def apply(source_world,imagery,destination):
    source_world,imagery,destination=map(Path,(source_world,imagery,destination))
    if destination.exists():raise FileExistsError(destination)
    image=json.loads((imagery/'imagery.json').read_text());report=json.loads((source_world/'earthcraft.json').read_text())
    grid=image['source_grid'];meta=report['source'];size=meta['size']
    if grid['crs']!=meta['crs']:raise ValueError('Image and world chart differ')
    col=grid['west']-meta['west'];row=meta['north']-grid['north'];s=grid['size']
    if col!=int(col) or row!=int(row) or min(col,row)<0 or max(col+s,row+s)>size:raise ValueError('Image grid is not contained and aligned')
    col,row=int(col),int(row)
    for asset in image['assets']:
        if Path(asset['file']).name!=asset['file'] or sha(imagery/asset['file'])!=asset['sha256']:raise ValueError('Image source asset changed')
    if sha(imagery/'metric-imagery.npz')!=image['normalized_sha256']:raise ValueError('Normalized image changed')
    with np.load(imagery/'metric-imagery.npz') as data:
        rgb=data['bands'][:3].transpose(1,2,0)
        valid=data['valid']&~data['shadow_candidate']&~data['vegetation_candidate']
    road=np.load(source_world/'classified-road-mask.npy');ground=np.load(source_world/'top-heights.npy')
    if road.shape!=(size,size) or ground.shape!=road.shape:raise ValueError('World evidence grid mismatch')
    colors=texture_colors();names=sorted(name for name in colors if name.endswith('_concrete'))
    choices=nearest_colors(rgb,np.array([colors[name] for name in names]))
    selected=np.zeros((size,size),bool);selected[row:row+s,col:col+s]=valid&road[row:row+s,col:col+s]
    expected=np.zeros((size,size),np.uint8);table=['gray_concrete']+names
    expected[row:row+s,col:col+s]=np.where(selected[row:row+s,col:col+s],choices+1,0)
    ox,oz=report.get('world_offset_xz',[0,0]);changed=0;retained=0
    shutil.copytree(source_world,destination)
    prior=destination/'pre-color-evidence';prior.mkdir()
    for name in ('server-verification.json','city-assembly.json','photo-layer-verification.json'):
        path=destination/name
        if path.exists():path.rename(prior/name)
    for path in (destination/'region').glob('r.*.*.mca'):
        records=[];region_changed=False
        for _,tag,_ in chunks(path):
            cx,cz=int(tag['xPos']),int(tag['zPos']);x0=cx*16-ox;z0=cz*16-oz
            local=selected[z0:z0+16,x0:x0+16]
            if local.any():
                zz,xx=np.nonzero(local);ys=ground[z0+zz,x0+xx]
                for section in tag['sections']:
                    sy=int(section['Y']);take=np.flatnonzero(ys//16==sy)
                    if not len(take):continue
                    state=section['block_states'];palette=state['palette']
                    values=np.zeros(4096,int) if len(palette)==1 else unpack(state['data'],max(4,(len(palette)-1).bit_length()),4096)
                    for index in take:
                        slot=(int(ys[index])%16)*256+int(zz[index])*16+int(xx[index]);x=x0+xx[index];z=z0+zz[index]
                        before=palette[int(values[slot])]
                        # Existing user material changes, holes and buildings win.
                        if str(before['Name'])!='minecraft:gray_concrete':
                            expected[z,x]=0;selected[z,x]=False;retained+=1;continue
                        block='minecraft:'+table[int(expected[z,x])]
                        if block==str(before['Name']):continue
                        identifier=next((i for i,p in enumerate(palette) if str(p['Name'])==block),None)
                        if identifier is None:identifier=len(palette);palette.append(n.Compound({'Name':n.String(block)}))
                        values[slot]=identifier;changed+=1;region_changed=True
                    if len(palette)>1:state['data']=packed(values,max(4,(len(palette)-1).bit_length()))
            records.append((cx,cz,tag))
        if region_changed:
            temporary=path.with_suffix('.mca.colors');region_write(temporary,records);temporary.replace(path)
    result={'method':'Source-georeferenced 2022 orthophoto RGB to finite vanilla concrete palette, onto existing classified pavement only',
        'imagery':str(imagery.resolve()),'imagery_manifest_sha256':sha(imagery/'imagery.json'),
        'capture_interval':image['capture_interval'],'capture_precision':image['capture_precision'],
        'attribution':image['attribution'],'rights_scope':image['rights_scope'],
        'palette':table,'palette_rgb':{name:colors[name].tolist() for name in names},
        'selected_cells':int(selected.sum()),'recolored_cells':changed,'nonpavement_or_edited_cells_retained':retained,
        'geometry_changed':False,'llm_used':False,'registration':'Publisher georeference; no fitted shift or warp applied',
        'independent_accuracy_verified':False,'facades_painted':False,
        'limitations':['Image color includes illumination and potentially transient objects; it is not calibrated material reflectance.',
            'Source acquisition is known only to year precision; year match does not prove simultaneous capture.',
            'Spectral vegetation and darkness masks are conservative diagnostic candidates, not complete occlusion masks.',
            'Finite block colors are an artistic adaptation of image pixels, not a 100% accurate material survey.']}
    np.save(destination/'ground-appearance-blocks.npy',expected)
    (destination/'ground-appearance.json').write_text(json.dumps(result,indent=2))
    verify(destination)
    if (destination/'photo-skin.json').exists():
        skin=json.loads((destination/'photo-skin.json').read_text())
        skin['pre_ground_color_region_sha256']=skin['region_sha256']
        skin['region_sha256']={p.name:sha(p) for p in (destination/'region').glob('r.*.*.mca')}
        skin['subsequent_material_only_revision']='Ground colors changed; building occupancy and photo anchors unchanged'
        (destination/'photo-skin.json').write_text(json.dumps(skin,indent=2))
        verify_layer(destination,Path(skin['source_world']))
    return result
