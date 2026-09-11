"""Supported vertical LiDAR plane patches and measured photo colour, no LLM.

This bounded Water Tower diagnostic does not infer a complete building. It fits
planes in a frozen shaft interval, leaves unsupported holes empty, and preserves
source observations. Camera registration remains an explicit experimental input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import Delaunay, cKDTree

from facade_registration import load_lidar, photo, project

ROOT = Path(__file__).resolve().parents[1]


def vertical_planes(xyz, tolerance=.12, minimum=70):
    """Deterministic RANSAC lines in XY, refitted with SVD; no axis snapping."""
    points = np.asarray(xyz,float)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():
        raise ValueError('Finite Nx3 points required')
    remaining = np.arange(len(points))
    rng = np.random.default_rng(2409)
    planes = []
    while len(remaining)>=minimum and len(planes)<12:
        p = points[remaining]
        best = np.zeros(len(p),bool)
        for _ in range(500):
            a,b = p[rng.choice(len(p),2,replace=False),:2]
            delta = b-a
            if np.linalg.norm(delta)<.3:
                continue
            normal = np.array([-delta[1],delta[0]])/np.linalg.norm(delta)
            inside = np.abs((p[:,:2]-a)@normal)<tolerance
            if inside.sum()>best.sum():
                best = inside
        if best.sum()<minimum:
            break
        center = p[best].mean(0)
        _,_,v = np.linalg.svd(p[best,:2]-center[:2],full_matrices=False)
        normal = np.r_[v[-1],0.]
        inside = np.abs((p-center)@normal)<tolerance
        support = p[inside]
        tangent = np.array([-normal[1],normal[0],0.])
        local = np.column_stack(((support-center)@tangent,support[:,2]-center[2]))
        if len(support)>=minimum and np.ptp(local[:,0])>.5 and np.ptp(local[:,1])>2:
            planes.append({'center':center,'normal':normal,'tangent':tangent,
                           'support':support,'local':local})
        remaining = remaining[~inside]
    return planes


def supported_patch(plane, step=.0625, max_edge=1.25, max_support=.5):
    """No hull-wide filling: each sample needs a short supporting triangle."""
    local = np.unique(plane['local'],axis=0)
    mesh = Delaunay(local)
    triangles = local[mesh.simplices]
    edges = np.stack([np.linalg.norm(triangles[:,i]-triangles[:,j],axis=1)
                      for i,j in [(0,1),(1,2),(2,0)]],axis=1)
    usable = edges.max(axis=1)<=max_edge
    lo,hi = local.min(0),local.max(0)
    nx,ny = np.ceil((hi-lo)/step).astype(int)
    if nx*ny>500_000:
        raise ValueError('Plane exceeds bounded sampling budget')
    xx,yy = np.meshgrid(lo[0]+(np.arange(nx)+.5)*step,lo[1]+(np.arange(ny)+.5)*step)
    samples = np.c_[xx.ravel(),yy.ravel()]
    simplex = mesh.find_simplex(samples)
    distances,_ = cKDTree(local).query(samples)
    keep = (simplex>=0)&usable[np.maximum(simplex,0)]&(distances<=max_support)
    selected = samples[keep]
    points = plane['center']+selected[:,0,None]*plane['tangent']+np.c_[
        np.zeros(len(selected)),np.zeros(len(selected)),selected[:,1]]
    return points, distances[keep], {'candidate_samples':len(samples),'supported_samples':len(points),
        'max_triangle_edge_m':max_edge,'max_nearest_support_m':max_support,
        'sampling_step_m':step,'usable_triangles':int(usable.sum()),'triangles':len(usable)}


def visible_samples(samples, cloud, params, size, radius=2, tolerance=.25):
    """Conservative point-cloud z-buffer; absent occluders remain a limitation."""
    w,h = size
    uv,depth = project(cloud,params,size)
    pixels = np.rint(uv).astype(int)
    valid = (depth>0)&(pixels[:,0]>=0)&(pixels[:,0]<w)&(pixels[:,1]>=0)&(pixels[:,1]<h)
    z = np.full(h*w,np.inf,dtype='float32')
    np.minimum.at(z,pixels[valid,1]*w+pixels[valid,0],depth[valid])
    z = cv2.erode(z.reshape(h,w),np.ones((2*radius+1,2*radius+1),np.uint8))
    uv,depth = project(samples,params,size)
    pixels = np.rint(uv).astype(int)
    valid = (depth>0)&(pixels[:,0]>=0)&(pixels[:,0]<w)&(pixels[:,1]>=0)&(pixels[:,1]<h)
    xx = np.clip(pixels[:,0],0,w-1); yy = np.clip(pixels[:,1],0,h-1)
    visible = valid & (depth<=z[yy,xx]+tolerance)
    return uv,visible


def run(registration, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    start = time.monotonic()
    registration = Path(registration)
    camera = json.loads(registration.read_text())
    xyz,intensity,meta,ground,source = load_lidar()
    # Frozen region of this experiment, not a generated wall dimension.
    shaft = xyz[(xyz[:,2]>=25)&(xyz[:,2]<=44)]
    planes = vertical_planes(shaft)
    parameters = np.array(camera['best']['parameters'])
    scored = []
    for p in planes:
        view = parameters[:3]-p['center'];view/=np.linalg.norm(view)
        outward = p['center']-shaft.mean(0); outward[2]=0
        if p['normal']@outward<0:
            p['normal'] *= -1
        facing = float(p['normal']@view)
        scored.append((facing,p))
    facing,plane = max(scored,key=lambda pair:pair[0])
    if facing<.4:
        raise ValueError('No well-facing supported vertical plane')
    candidates,distances,support_report = supported_patch(plane)
    original_path = ROOT/'runs/water-tower-facade-photos'/camera['asset']['file']
    if hashlib.sha256(original_path.read_bytes()).hexdigest()!=camera['asset']['sha256']:
        raise ValueError('Original photo changed')
    rgb,_,_ = photo(original_path,2400)
    h,w = rgb.shape[:2]
    ratio = h/camera['image_size'][1]
    params = parameters.copy();params[6] += np.log(ratio)
    uv,visible = visible_samples(candidates,xyz,params,(w,h),radius=3)
    # Restrict appearance to the interior of the fitted training silhouette;
    # this rejects some sky leakage but cannot certify real-world visibility.
    mask = np.asarray(Image.open(registration.parent/'colour-mask.png'))
    mask = cv2.resize(mask,(w,h),interpolation=cv2.INTER_NEAREST)
    mask = cv2.erode(mask,np.ones((7,7),np.uint8))
    pixels = np.rint(uv).astype(int)
    xx=np.clip(pixels[:,0],0,w-1); yy=np.clip(pixels[:,1],0,h-1)
    visible &= mask[yy,xx]>0
    accepted = candidates[visible]
    colours = rgb[yy[visible],xx[visible]]
    np.savez_compressed(output/'surface-candidate.npz',xyz=accepted,rgb=colours,
                        normal=plane['normal'],support=plane['support'])
    # A rectified measured-photo diagnostic, not a game rendering or a new source.
    local = np.c_[(accepted-plane['center'])@plane['tangent'],accepted[:,2]]
    lower=local.min(0); cells=np.rint((local-lower)/.0625).astype(int)
    panel=np.full((cells[:,1].max()+1,cells[:,0].max()+1,3),[235,80,180],np.uint8)
    panel[panel.shape[0]-1-cells[:,1],cells[:,0]]=colours
    Image.fromarray(panel).resize((panel.shape[1]*2,panel.shape[0]*2),Image.Resampling.NEAREST).save(output/'rectified-candidate.png')
    overlay=rgb.copy()
    for x,y in pixels[visible][::max(1,len(accepted)//1500)]:
        cv2.circle(overlay,(int(x),int(y)),2,(0,235,110),-1)
    Image.fromarray(overlay).resize((600,800)).save(output/'photo-support-overlay.jpg')
    report={'status':'photo_surface_candidate_not_accuracy_verified',
        'registration':str(registration),'registration_sha256':hashlib.sha256(registration.read_bytes()).hexdigest(),
        'photo':camera['asset'],'point_source_sha256':source['points_sha256'],
        'shaft_selection_height_m':[25,44],'planes_found':len(planes),
        'selected_plane':{k:plane[k].tolist() for k in ['center','normal','tangent']},
        'selected_plane_support_points':len(plane['support']),
        'plane_residual_p95_m':float(np.percentile(np.abs((plane['support']-plane['center'])@plane['normal']),95)),
        'facing_cosine':facing,'support':support_report,'coloured_samples':len(accepted),
        'surface_file_sha256':hashlib.sha256((output/'surface-candidate.npz').read_bytes()).hexdigest(),
        'ground_elevation_m':ground,'crs':meta['crs'],'llm_used':False,
        'geometry_evidence':'Locally fitted vertical plane and short-triangle interpolation, not new measured points.',
        'colour_evidence':'Direct registered photograph samples, including its lighting and shadows.',
        'world_modified':False,'independent_photo_validation':False,'elapsed_seconds':time.monotonic()-start,
        'limitations':['One shaft plane only; not complete facade or building.',
            'Camera fit uses silhouette, not surveyed image control points.',
            'Sparse airborne returns cannot rule out unseen openings or occluders.',
            'No colour is copied to unseen rear faces; unsupported areas stay absent.',
            'Photographic colour is not calibrated material reflectance.']}
    (output/'surface-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registration',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.registration,a.output)
