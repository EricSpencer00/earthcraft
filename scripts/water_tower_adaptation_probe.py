"""Frozen local Water Tower diagnostic for the reusable appearance adapter.

Runs no network calls and changes no world. Historical silhouette calibration is
an experimental input only. This does not claim new facade registration evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image

from appearance_adapter import paint_surface, rank_capture, temporal_relation
from facade_registration import load_lidar, photo, project, rotation
from facade_photos import normalized_capture

ROOT=Path(__file__).resolve().parents[1]


def run(output):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    xyz,_,meta,ground,manifest=load_lidar()
    target=manifest['capture_interval']
    assets=[]
    for folder in ['water-tower-facade-photos','water-tower-facade-validation-photo',
                   'water-tower-photo-overlap-001']:
        path=ROOT/'runs'/folder
        for asset in json.loads((path/'manifest.json').read_text())['assets']:
            if Path(asset['file']).name!=asset['file']:raise ValueError('Invalid photo path')
            if hashlib.sha256((path/asset['file']).read_bytes()).hexdigest()!=asset['sha256']:
                raise ValueError('Changed cached photo')
            row={key:asset[key] for key in ['id','capture_date_source','sha256','license','description_url']}
            row['capture_interval_iso']=normalized_capture(asset['capture_date_source'])
            try:
                row['temporal_relation']=temporal_relation(row['capture_interval_iso'],target)
                row['rank']=list(rank_capture(row['capture_interval_iso'],target))
            except ValueError:
                row['temporal_relation']=None;row['rank']=[1_000_000,1_000_000]
            row['camera_registration_verified']=False
            assets.append(row)
    assets.sort(key=lambda a:(a['rank'],a['id']))
    world=json.loads((ROOT/'worlds/Earthcraft-Water-Tower-Explorer-v4/earthcraft.json').read_text())
    # Freeze the actual Minecraft chart, not a fresh grid origin at each LOD.
    mapped=np.c_[xyz[:,0]-meta['west'],xyz[:,2]+ground+world['vertical_offset_m'],meta['north']-xyz[:,1]]
    precision=[]
    for step in [1.,.5,.25,.125]:
        centers=(np.floor(mapped/step)+.5)*step
        error=np.linalg.norm(mapped-centers,axis=1)
        precision.append({'cell_size_m':step,'occupied_cells':len(np.unique(centers,axis=0)),
            'point_to_quantized_center_p50_m':float(np.median(error)),
            'point_to_quantized_center_p95_m':float(np.percentile(error,95)),
            'max_possible_center_displacement_m':float(np.sqrt(3)*step/2)})
    registration=ROOT/'runs/water-tower-registration-west-002/registration.json'
    fitted=json.loads(registration.read_text())
    surface=ROOT/'runs/water-tower-facade-surface-001'
    support_report=json.loads((surface/'surface-report.json').read_text())
    if hashlib.sha256(registration.read_bytes()).hexdigest()!=support_report['registration_sha256']:
        raise ValueError('Changed experimental registration')
    raw=surface/'surface-candidate.npz'
    if hashlib.sha256(raw.read_bytes()).hexdigest()!=support_report['surface_file_sha256']:
        raise ValueError('Changed candidate surface')
    with np.load(raw) as data:
        samples=data['xyz'].copy();normals=np.tile(data['normal'],(len(samples),1))
    rgb,_,_=photo(ROOT/'runs/water-tower-facade-photos'/fitted['asset']['file'],2400)
    h,w=rgb.shape[:2];parameters=np.array(fitted['best']['parameters'])
    parameters[6]+=np.log(h/fitted['image_size'][1])
    uv,z=project(xyz,parameters,(w,h));pixel=np.rint(uv).astype(int)
    valid=(z>0)&(pixel[:,0]>=0)&(pixel[:,0]<w)&(pixel[:,1]>=0)&(pixel[:,1]<h)
    depth=np.full(h*w,np.inf,dtype=np.float32)
    np.minimum.at(depth,pixel[valid,1]*w+pixel[valid,0],z[valid])
    depth=cv2.erode(depth.reshape(h,w),np.ones((7,7),np.uint8))
    mask=cv2.resize(np.asarray(Image.open(registration.parent/'colour-mask.png')),(w,h),interpolation=cv2.INTER_NEAREST)
    mask=cv2.erode(mask,np.ones((7,7),np.uint8))>0
    r=rotation(*parameters[3:6]);f=np.exp(parameters[6])
    camera={'K':[[f,0,w/2],[0,f,h/2],[0,0,1]],'R':r,'t':-r@parameters[:3]}
    start=time.monotonic()
    result=paint_surface(samples,normals,rgb,depth,camera,mask,depth_tolerance_m=.25)
    elapsed=time.monotonic()-start
    report={'status':'adapter_exercised_on_real_inputs_not_a_new_world',
        'building_id':'Cook County 2022 OBJECTID 833197','building_name':'Chicago Water Tower',
        'target_capture_interval':target,'point_source_sha256':manifest['points_sha256'],
        'source_points':len(mapped),'precision_probe':precision,'dated_photo_candidates':assets,
        'painting_probe':{'surface_samples':len(samples),'painted_samples':int(result['visible'].sum()),
            'sampler_seconds':elapsed,'source':fitted['asset']['id'],
            'temporal_relation':temporal_relation(fitted['asset']['capture_date_source'],target),
            'registration_verified':False,'admitted_to_world':False},
        'llm_used':False,'network_requests':0,'world_modified':False,
        'limitations':['Quantization error is measured against input points, not independent surveyed surfaces.',
            'Finer cells reveal missing samples; they do not create new measured detail.',
            'Painting uses the historical experimental silhouette pose and a sparse point-depth buffer.',
            'Point-depth splats and colour mask cannot rule out all real occluders.',
            'Closest-date photo still needs camera registration; date proximity is not accuracy.',
            'Global address lookup, mesh/collision LOD and automated facade pose recovery are not implemented by this probe.']}
    output.mkdir(parents=True)
    np.savez_compressed(output/'paint-candidate.npz',xyz=samples,**result)
    (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
