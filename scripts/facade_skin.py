"""Bake a bounded experimental photo skin onto existing metre-block faces.

This does not complete or move building geometry. Transparent texels retain the
original Minecraft material wherever source support/visibility is insufficient.
Each display model is attached to an existing block; collision and regions stay
byte-identical. Camera registration is an explicitly unverified hypothesis.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import zipfile

import cv2
import nbtlib as n
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

from facade_registration import load_lidar, photo, project
from facade_surface import visible_samples

ROOT = Path(__file__).resolve().parents[1]
NORMALS = {'west':(-1,0,0),'east':(1,0,0),'north':(0,0,-1),'south':(0,0,1)}


def face_samples(cell, face, resolution=16, epsilon=.002):
    """Minecraft UV order: top-to-bottom V, face-local left-to-right U."""
    u,v = np.meshgrid((np.arange(resolution)+.5)/resolution,
                      (np.arange(resolution)+.5)/resolution)
    u=u.ravel();v=v.ravel();one=np.ones_like(u)
    if face=='west':local=np.c_[-epsilon*one,1-v,u]
    elif face=='east':local=np.c_[(1+epsilon)*one,1-v,1-u]
    elif face=='north':local=np.c_[1-u,1-v,-epsilon*one]
    elif face=='south':local=np.c_[u,1-v,(1+epsilon)*one]
    else:raise ValueError('Only vertical exterior faces supported')
    return np.asarray(cell)+local


def to_enu(world, meta, offset, ground):
    return np.c_[world[:,0]+meta['west'],meta['north']-world[:,2],world[:,1]-offset-ground]


def run(registration, name):
    if Path(name).name!=name or name in ('.','..'):
        raise ValueError('Simple new world name required')
    baseline = ROOT/'worlds/Earthcraft-Water-Tower-3D'
    world = ROOT/'worlds'/name
    result = ROOT/'runs'/name
    if world.exists() or result.exists():
        raise FileExistsError('Never overwrite a world or experiment')
    if shutil.disk_usage(ROOT).free<21*2**30:
        raise ValueError('Keep 20 GiB free plus working allowance')
    camera = json.loads(Path(registration).read_text())
    if camera['asset']['id']!='west':
        raise ValueError('This experiment is the west photo only')
    source_report = json.loads((baseline/'earthcraft.json').read_text())
    meta=source_report['source'];offset=source_report['vertical_offset_m']
    xyz,_,_,ground,manifest = load_lidar()
    cells=np.load(baseline/'point-voxels.npy')
    cell_set=set(map(tuple,cells))
    photo_path=ROOT/'runs/water-tower-facade-photos'/camera['asset']['file']
    if hashlib.sha256(photo_path.read_bytes()).hexdigest()!=camera['asset']['sha256']:
        raise ValueError('Photo checksum changed')
    rgb,_,_=photo(photo_path,2400)
    h,w=rgb.shape[:2]
    params=np.asarray(camera['best']['parameters'],float)
    params[6]+=np.log(h/camera['image_size'][1])
    mask=np.asarray(Image.open(Path(registration).parent/'colour-mask.png'))
    mask=cv2.resize(mask,(w,h),interpolation=cv2.INTER_NEAREST)
    mask=cv2.erode(mask,np.ones((13,13),np.uint8))
    tree=cKDTree(xyz)
    faces=[]
    rejected={'faces_not_exposed_or_facing':0,'texels_without_lidar_support_or_photo_visibility':0}
    for cell in cells:
        height=cell[1]-offset-ground
        if not 25<=height<44:
            continue
        for face,normal in NORMALS.items():
            if tuple(cell+normal) in cell_set:
                continue
            samples=face_samples(cell,face)
            enu=to_enu(samples,meta,offset,ground)
            norm=np.array([normal[0],-normal[2],normal[1]])
            view=params[:3]-enu.mean(0);view/=np.linalg.norm(view)
            if norm@view<.15:
                rejected['faces_not_exposed_or_facing']+=1
                continue
            uv,visible=visible_samples(enu,xyz,params,(w,h),radius=4,tolerance=.8)
            pixels=np.rint(uv).astype(int)
            xx=np.clip(pixels[:,0],0,w-1);yy=np.clip(pixels[:,1],0,h-1)
            distances,_=tree.query(enu)
            # Existing metre faces can sit up to a voxel away from the source
            # wall. This is presentation error, not a measured surface offset.
            keep=visible&(distances<=1.0)&(mask[yy,xx]>0)
            # Every allowed face uses the one source photo. No inferred window
            # patterns, palette randomization, rear-side copying or AI inpaint.
            rejected['texels_without_lidar_support_or_photo_visibility']+=int((~keep).sum())
            if keep.sum()<16:
                continue
            tex=np.zeros((256,4),np.uint8)
            tex[keep,:3]=rgb[yy[keep],xx[keep]];tex[keep,3]=255
            faces.append({'cell':cell.copy(),'face':face,'texture':tex.reshape(16,16,4),
                          'source_uv':uv,'valid':keep,'source_distance':distances})
    if not faces or len(faces)>512:
        raise ValueError('No supported texture or display budget exceeded')
    world.mkdir();result.mkdir()
    shutil.copytree(baseline,world,dirs_exist_ok=True)
    # Sidecar verification inherits geometry only, never the old appearance verdict.
    records=[]; commands=[]
    pack=world/'datapacks/earthcraft_photo_skin'
    functions=pack/'data/earthcraft_skin/function';functions.mkdir(parents=True)
    tags=pack/'data/minecraft/tags/function';tags.mkdir(parents=True)
    (pack/'pack.mcmeta').write_text(json.dumps({'pack':{'pack_format':88,'min_format':[88,0],
        'max_format':[88,0],'description':'Water Tower measured-photo skin EXPERIMENT; no geometry completion'}}))
    (tags/'tick.json').write_text(json.dumps({'values':['earthcraft_skin:tick']}))
    (functions/'tick.mcfunction').write_text('execute if entity @a unless data storage earthcraft_skin:state installed run function earthcraft_skin:build\n')
    author='Dough4872'
    attribution={'title':camera['asset']['title'],'author':author,'source':camera['asset']['description_url'],
                 'license':'CC BY-SA 4.0','license_url':camera['asset']['license_url'],
                 'changes':'EXIF orientation, resizing, camera projection, visibility mask, 16px/metre face textures.',
                 'capture_date':'2025-08-19','geometry_source':'Cook County 2022 LiDAR; separate source lineage.'}
    with zipfile.ZipFile(world/'resources.zip','w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('pack.mcmeta',json.dumps({'pack':{'pack_format':69,'min_format':[69,0],
            'max_format':[69,0],'description':'Water Tower photo skin — Dough4872 / CC BY-SA 4.0; experimental alignment'}}))
        archive.writestr('attribution.json',json.dumps(attribution,indent=2))
        for index,record in enumerate(faces):
            buffer=io.BytesIO();Image.fromarray(record['texture']).save(buffer,format='PNG')
            archive.writestr(f'assets/earthcraft_skin/textures/block/face_{index}.png',buffer.getvalue())
            normal=np.array(NORMALS[record['face']]);lo=np.zeros(3);hi=np.full(3,16.)
            axis=int(np.flatnonzero(normal)[0]);value=-.032 if normal[axis]<0 else 16.032
            lo[axis]=hi[axis]=value
            model={'ambientocclusion':False,'textures':{'skin':f'earthcraft_skin:block/face_{index}',
                'particle':f'earthcraft_skin:block/face_{index}'},'elements':[{'from':lo.tolist(),
                'to':hi.tolist(),'shade':False,'faces':{record['face']:{'texture':'#skin','uv':[0,0,16,16]}}}]}
            archive.writestr(f'assets/earthcraft_skin/models/face_{index}.json',json.dumps(model))
            archive.writestr(f'assets/earthcraft_skin/items/face_{index}.json',json.dumps({
                'model':{'type':'minecraft:model','model':f'earthcraft_skin:face_{index}'}}))
            position=record['cell']+.5
            commands.append('summon minecraft:item_display '+ ' '.join(map(str,position))+
                ' {Tags:["earthcraft_photo_skin"],item:{id:"minecraft:stone",count:1,components:{'+
                f'"minecraft:item_model":"earthcraft_skin:face_{index}"'+
                '}},item_display:"none",Rotation:[180f,0f],brightness:{block:15,sky:15},view_range:4f,width:2f,height:2f}')
            records.append({'id':index,'cell':record['cell'].tolist(),'face':record['face'],
                'observed_texels':int(record['valid'].sum()),'texture_sha256':hashlib.sha256(buffer.getvalue()).hexdigest()})
        archive.writestr('earthcraft-skin-manifest.json',json.dumps(records,indent=2))
    commands.append('data modify storage earthcraft_skin:state installed set value 1b')
    (functions/'build.mcfunction').write_text('\n'.join(commands)+'\n')
    # Force-load only the handful of target chunks during automatic creation;
    # perform summons on the next tick after chunks have loaded, then release.
    (functions/'tick.mcfunction').write_text(
        'execute if entity @a unless data storage earthcraft_skin:state {installed:1b} unless data storage earthcraft_skin:state pending run function earthcraft_skin:prepare\n')
    (tags/'load.json').write_text(json.dumps({'values':['earthcraft_skin:load']}))
    (functions/'load.mcfunction').write_text('data remove storage earthcraft_skin:state pending\n')
    mincell=cells.min(0);maxcell=cells.max(0)
    (functions/'prepare.mcfunction').write_text(
        f'forceload add {mincell[0]} {mincell[2]} {maxcell[0]} {maxcell[2]}\n'+
        'schedule function earthcraft_skin:finish 10t replace\n'+
        'data modify storage earthcraft_skin:state pending set value 1b\n')
    (functions/'finish.mcfunction').write_text('function earthcraft_skin:build\n'+
        f'forceload remove {mincell[0]} {mincell[2]} {maxcell[0]} {maxcell[2]}\n'+
        'data remove storage earthcraft_skin:state pending\n')
    # Start in creative flight opposite the test patch, without user commands.
    # The safe ground spawn stays intact; this changes only the initial camera.
    preview_eye=np.array([-16.,-8.,34.])
    preview_target=np.array([.9,-3.8,34.])
    player_position=[preview_eye[0]-meta['west'],preview_eye[2]+ground+offset-1.62,
                     meta['north']-preview_eye[1]]
    preview_yaw=np.degrees(np.arctan2(*(preview_target-preview_eye)[:2]))-180
    level=n.load(world/'level.dat');level['Data']['LevelName']=n.String(name)
    level['Data']['Player']['Pos']=n.List[n.Double](player_position)
    level['Data']['Player']['Rotation']=n.List[n.Float]([preview_yaw,0])
    level['Data']['Player']['abilities']['flying']=n.Byte(1)
    level.save(world/'level.dat')
    region_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (baseline/'region').glob('r.*.*.mca')}
    for file,digest in region_hashes.items():
        assert hashlib.sha256((world/'region'/file).read_bytes()).hexdigest()==digest
    report={'status':'experimental_photo_skin_exported_client_unverified','baseline':str(baseline),
        'world':str(world),'registration':str(registration),'photo':attribution,
        'point_source_sha256':manifest['points_sha256'],'geometry_regions_byte_identical':True,
        'region_sha256':region_hashes,'new_or_removed_blocks':0,'collision_changed':False,
        'surface_offset_m':.002,'texture_pixels_per_block_edge':16,
        'initial_player_position':player_position,'initial_player_yaw':preview_yaw,
        'initial_player_flying':True,
        'display_entities':len(faces),'observed_texels':sum(r['observed_texels'] for r in records),
        'selection_height_above_county_ground_m':[25,44],'rejected':rejected,
        'llm_used':False,'independent_photo_validation':False,'client_visual_verification':False,
        'limitations':['One upper-shaft patch, not a complete textured building.',
            'Photo camera alignment remains experimental; silhouette training overlap is not accuracy.',
            'Metre-block faces are coarser than the real measured wall; texture placement inherits that error.',
            'Sparse LiDAR visibility does not prove absence of image occluders.',
            'Transparent unsupported texels retain original neutral blocks; no autofill was admitted.',
            'Photographed shadows are retained. No actual material reflectance recovery.',
            'No source or generated world is published.']}
    (world/'photo-skin.json').write_text(json.dumps(report,indent=2))
    (result/'export.json').write_text(json.dumps(report,indent=2))
    np.savez_compressed(result/'texture-samples.npz',cells=np.array([r['cell'] for r in faces]),
                        faces=np.array([r['face'] for r in faces]),rgba=np.stack([r['texture'] for r in faces]),
                        uv=np.stack([r['source_uv'] for r in faces]))
    print(json.dumps(report,indent=2))
    return world


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registration',type=Path,required=True)
    p.add_argument('--name',required=True)
    a=p.parse_args();run(a.registration,a.name)
