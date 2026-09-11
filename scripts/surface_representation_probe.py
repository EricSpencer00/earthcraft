"""Measure whether a continuous photo layer would be visible outside frozen blocks.

No world edits, geometry completion or camera fitting. Samples are the existing
LiDAR-supported plane interpolation, not independent surveyed surface truth.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

from facade_skin import face_samples

ROOT=Path(__file__).resolve().parents[1]


def world_coordinates(xyz,meta,offset,ground):
    p=np.asarray(xyz,float)
    return np.c_[p[:,0]-meta['west'],p[:,2]+ground+offset,meta['north']-p[:,1]]


def block_occlusion(origin,targets,cells):
    """Exact ray/AABB entry before each target; unit cubes, no texture alpha.

    Returns metres of blockage before the target, zero when no solid block
    intervenes. Batched to bound memory; retains all targets in the denominator.
    """
    origin=np.asarray(origin,float);targets=np.asarray(targets,float)
    cells=np.asarray(cells,float)
    if (origin.shape!=(3,) or targets.ndim!=2 or targets.shape[1]!=3 or
        cells.ndim!=2 or cells.shape[1]!=3 or len(targets)>10000 or len(cells)>10000 or
        not all(np.isfinite(p).all() for p in (origin,targets,cells))):
        raise ValueError('Finite bounded origin, targets and unit block cells required')
    if not np.equal(cells,np.floor(cells)).all():raise ValueError('Integral block cells required')
    results=[]
    for start in range(0,len(targets),64):
        delta=targets[start:start+64]-origin
        distance=np.linalg.norm(delta,axis=1)
        if (distance<1e-9).any():raise ValueError('Camera coincides with target')
        direction=delta/distance[:,None]
        near=np.full((len(delta),len(cells)),-np.inf)
        far=np.full_like(near,np.inf)
        for axis in range(3):
            d=direction[:,axis,None]
            parallel=np.abs(d)<1e-12
            safe=np.where(parallel,1,d)
            a=(cells[None,:,axis]-origin[axis])/safe
            b=(cells[None,:,axis]+1-origin[axis])/safe
            inside=(origin[axis]>=cells[None,:,axis])&(origin[axis]<=cells[None,:,axis]+1)
            lower=np.where(parallel,np.where(inside,-np.inf,np.inf),np.minimum(a,b))
            upper=np.where(parallel,np.where(inside,np.inf,-np.inf),np.maximum(a,b))
            near=np.maximum(near,lower);far=np.minimum(far,upper)
        entry=np.maximum(near,0)
        valid=(far>=entry)&(entry<distance[:,None]-.002)
        first=np.min(np.where(valid,entry,np.inf),axis=1) if len(cells) else np.full(len(delta),np.inf)
        results.extend(np.maximum(0,distance-first))
    return np.asarray(results)


def summary(values):
    values=np.asarray(values,float)
    return dict(zip(['min','p50','p95','max'],map(float,np.percentile(values,[0,50,95,100]))))


def run(output):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    surface=ROOT/'runs/water-tower-facade-surface-001'
    world=ROOT/'worlds/Earthcraft-Water-Tower-Photo-Skin-v3'
    report=json.loads((surface/'surface-report.json').read_text())
    surface_path=surface/'surface-candidate.npz'
    assert hashlib.sha256(surface_path.read_bytes()).hexdigest()==report['surface_file_sha256']
    meta=json.loads((world/'earthcraft.json').read_text())
    skin=json.loads((world/'photo-skin.json').read_text())
    with np.load(surface_path) as data:xyz=data['xyz'].copy()
    cells=np.load(world/'point-voxels.npy')
    occupied=set(map(tuple,cells))
    targets=world_coordinates(xyz,meta['source'],meta['vertical_offset_m'],report['ground_elevation_m'])
    inside=np.array([tuple(p) in occupied for p in np.floor(targets).astype(int)])
    # Read actual exported opaque texels, not a freshly generated approximation.
    texels=[]
    with zipfile.ZipFile(world/'resources.zip') as archive:
        records=json.loads(archive.read('earthcraft-skin-manifest.json'))
        for record in records:
            raw=archive.read(f'assets/earthcraft_skin/textures/block/face_{record["id"]}.png')
            assert hashlib.sha256(raw).hexdigest()==record['texture_sha256']
            rgba=np.asarray(Image.open(io.BytesIO(raw)))
            texels.append(face_samples(record['cell'],record['face'])[rgba[:,:,3].ravel()>0])
    texels=np.concatenate(texels)
    nearest,_=cKDTree(texels).query(targets)
    camera=json.loads(Path(skin['registration']).read_text())
    cameras={'photo_fit':np.asarray(camera['best']['parameters'][:3]),
             'existing_preview':np.array([-16.,-8.,34.])}
    results={};arrays={'surface_world':targets,'inside_solid_block':inside,'nearest_opaque_texel_m':nearest}
    for name,enu in cameras.items():
        origin=world_coordinates(enu[None,:],meta['source'],meta['vertical_offset_m'],report['ground_elevation_m'])[0]
        blocked=block_occlusion(origin,targets,cells)
        arrays[f'{name}_blockage_m']=blocked
        results[name]={'origin_world':origin.tolist(),'occluded_samples':int((blocked>0).sum()),
                       'unoccluded_samples':int((blocked==0).sum()),
                       'occluded_fraction':float((blocked>0).mean()),'blockage_m':summary(blocked)}
    output.mkdir(parents=True)
    np.savez_compressed(output/'measurements.npz',**arrays)
    result={'status':'representation_diagnostic_not_export','samples':len(targets),
            'surface_source_sha256':report['surface_file_sha256'],
            'block_cells_sha256':hashlib.sha256((world/'point-voxels.npy').read_bytes()).hexdigest(),
            'photo_pack_sha256':hashlib.sha256((world/'resources.zip').read_bytes()).hexdigest(),
            'inside_solid_block_samples':int(inside.sum()),'inside_fraction':float(inside.mean()),
            'nearest_existing_opaque_texel_centre_m':summary(nearest),'cameras':results,
            'world_modified':False,'llm_used':False,'independent_accuracy_validation':False,
            'decision':'Do not export a finer plane layer while most samples are hidden inside unchanged solid blocks.'
                       if results['existing_preview']['occluded_fraction']>.5 else
                       'Visibility permits a bounded prototype, but support and camera validation remain required.',
            'limitations':['Interpolated plane samples are not new measured points or independent truth.',
                'Nearest texel centre distance is representation displacement, not geographic or colour accuracy.',
                'Occlusion tests the frozen building unit cubes only; additional terrain/scene occluders are excluded.',
                'Exactly tangent cube rays are conservatively counted as intersections.',
                'No shifted surface, transparent collision block, hull-wide fill or geographic edit is exported.']}
    (output/'report.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
