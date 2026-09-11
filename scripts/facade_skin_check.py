"""Read exported skin assets back; render a labelled diagnostic, not gameplay."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np
from PIL import Image,ImageDraw

from facade_registration import project
from facade_skin import NORMALS, to_enu


def corners(cell,face,epsilon=0):
    if face=='west': p=[[-epsilon,1,0],[-epsilon,1,1],[-epsilon,0,1],[-epsilon,0,0]]
    elif face=='east':p=[[1+epsilon,1,1],[1+epsilon,1,0],[1+epsilon,0,0],[1+epsilon,0,1]]
    elif face=='north':p=[[1,1,-epsilon],[0,1,-epsilon],[0,0,-epsilon],[1,0,-epsilon]]
    elif face=='south':p=[[0,1,1+epsilon],[1,1,1+epsilon],[1,0,1+epsilon],[0,0,1+epsilon]]
    else:raise ValueError(face)
    return np.array(p)+cell


def check(world):
    world=Path(world)
    report=json.loads((world/'photo-skin.json').read_text())
    source=json.loads((world/'earthcraft.json').read_text())
    meta=source['source'];offset=source['vertical_offset_m']
    registration=json.loads(Path(report['registration']).read_text())
    ground=registration['ground_elevation_m']
    cells=np.load(world/'point-voxels.npy');occupied=set(map(tuple,cells))
    textures={};texel_count=0;placement_errors=[]
    with zipfile.ZipFile(world/'resources.zip') as archive:
        records=json.loads(archive.read('earthcraft-skin-manifest.json'))
        for r in records:
            data=archive.read(f'assets/earthcraft_skin/textures/block/face_{r["id"]}.png')
            assert hashlib.sha256(data).hexdigest()==r['texture_sha256']
            texture=np.asarray(Image.open(io.BytesIO(data)))
            assert texture.shape==(16,16,4)
            assert np.isin(texture[:,:,3],[0,255]).all()
            texel_count+=int((texture[:,:,3]>0).sum())
            assert tuple(r['cell']) in occupied
            assert tuple(np.array(r['cell'])+NORMALS[r['face']]) not in occupied
            model=json.loads(archive.read(f'assets/earthcraft_skin/models/face_{r["id"]}.json'))
            element=model['elements'][0]
            center=(np.array(element['from'])+element['to'])/32-.5
            # Built-in half-turn and explicit 180-degree entity yaw cancel.
            actual=np.array(r['cell'])+.5+center
            expected=np.array(r['cell'])+.5+np.array(NORMALS[r['face']])*.502
            placement_errors.append(float(np.linalg.norm(actual-expected)))
            textures[(tuple(r['cell']),r['face'])]=texture
    assert max(placement_errors)<1e-10 and texel_count==report['observed_texels']
    baseline=Path(report['baseline'])
    for p in (world/'region').glob('r.*.*.mca'):
        assert p.read_bytes()==(baseline/'region'/p.name).read_bytes()
    # Independent camera deliberately differs from the texture-fitting image.
    camera=np.array([-16.,-8.,34.])
    target=np.array([.9,-3.8,34.])
    direction=target-camera
    params=np.r_[camera,np.arctan2(direction[0],direction[1]),0.,0.,np.log(540.)]
    size=(460,760)
    polygons=[]
    for cell in cells:
        if not 23<cell[1]-offset-ground<46:
            continue
        for face,normal in NORMALS.items():
            if tuple(cell+normal) in occupied:
                continue
            enu=to_enu(corners(cell,face),meta,offset,ground)
            n=np.array([normal[0],-normal[2],normal[1]])
            if n@(camera-enu.mean(0))<=0:
                continue
            uv,depth=project(enu,params,size)
            if depth.min()<=0 or (uv[:,0].max()<0 or uv[:,0].min()>size[0] or uv[:,1].max()<0 or uv[:,1].min()>size[1]):
                continue
            polygons.append((depth.mean(),uv,cell,face))
    neutral=np.full((16,16,3),[139,140,136],np.uint8)
    # Use the installed game's stone-brick texture, not generated decorative detail.
    jar=Path.home()/'Library/Application Support/minecraft/versions/1.21.10/1.21.10.jar'
    with zipfile.ZipFile(jar) as archive:
        neutral=np.asarray(Image.open(io.BytesIO(archive.read('assets/minecraft/textures/block/stone_bricks.png'))).convert('RGB'))
    images=[]
    for skin in (False,True):
        canvas=np.full((size[1],size[0],3),[225,230,233],np.uint8)
        for _,uv,cell,face in sorted(polygons,key=lambda x:-x[0]):
            tex=np.dstack((neutral,np.full((16,16),255,np.uint8)))
            if skin and (tuple(cell),face) in textures:
                t=textures[(tuple(cell),face)]; tex[t[:,:,3]>0]=t[t[:,:,3]>0]
            matrix=cv2.getPerspectiveTransform(np.float32([[0,0],[16,0],[16,16],[0,16]]),uv.astype('float32'))
            warped=cv2.warpPerspective(tex,matrix,size,flags=cv2.INTER_NEAREST)
            valid=warped[:,:,3]>0;canvas[valid]=warped[valid,:3]
        images.append(Image.fromarray(canvas))
    result=world.parents[1]/'runs'/world.name
    result.mkdir(exist_ok=True)
    combined=Image.new('RGB',(940,835),'#f4f1eb');d=ImageDraw.Draw(combined)
    d.text((15,12),'WATER TOWER | SAME BLOCK GEOMETRY, PHOTO APPEARANCE EXPERIMENT',fill='#202020')
    d.text((15,33),'Original stone-brick faces',fill='#202020');d.text((480,33),'Registered photograph on supported faces',fill='#202020')
    combined.paste(images[0],(10,55));combined.paste(images[1],(475,55))
    d.text((15,819),'Software diagnostic, NOT a Minecraft screenshot. Dough4872 photo / CC BY-SA 4.0. Alignment unverified.',fill='#202020')
    combined.save(result/'before-after-diagnostic.png')
    audit={'status':'asset_and_geometry_checks_pass','skin_faces':len(records),'observed_texels':texel_count,
        'max_display_center_error_m':max(placement_errors),'regions_identical':True,
        'new_camera':params.tolist(),'client_render_verified':False,
        'diagnostic_renderer':'Pinhole projection / depth-sorted block faces; not Minecraft lighting or a fidelity benchmark.'}
    (result/'asset-check.json').write_text(json.dumps(audit,indent=2))
    print(json.dumps(audit,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('world',type=Path)
    check(p.parse_args().world)
