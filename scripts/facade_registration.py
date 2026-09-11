"""Local, no-LLM camera-registration diagnostic against frozen LiDAR.

GPS and EXIF initialize a pinhole camera, never geographic truth. A deliberately
simple central-subject colour mask tests silhouette registration. This is NOT a
general building segmenter, and training silhouette fit alone cannot admit paint.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageOps
from pyproj import Transformer
from scipy.optimize import minimize
from shapely.geometry import Polygon
from shapely.ops import transform
from shapely import contains_xy

ROOT = Path(__file__).resolve().parents[1]


def load_lidar(include_groups=False):
    source = ROOT/'runs/water-tower-focus-64'
    meta = json.loads((source/'sources.json').read_text())
    path = ROOT/'runs/water-tower-points-2022/points.npz'
    manifest = json.loads(path.with_name('manifest.json').read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['points_sha256']:
        raise ValueError('Measured source changed')
    feature = next(f for f in json.loads((source/'cook-buildings-2022.json').read_text())['features']
                   if f['attributes']['OBJECTID'] == 833197)
    if len(feature['geometry']['rings']) != 1:
        raise ValueError('Review changed footprint')
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    polygon = transform(project.transform, Polygon(feature['geometry']['rings'][0]))
    ground = feature['attributes']['Ground_Z'] * 1200/3937
    with np.load(path) as data:
        p = data['xyz']
        selected = contains_xy(polygon.buffer(1), p[:,0], p[:,1]) & (data['withheld']==0)
        selected &= np.isin(data['classification'], [1,6,15,19]) & (p[:,2]>ground+1)
        xyz = p[selected].copy()
        intensity = data['intensity'][selected].copy()
        groups = data['point_source_id'][selected].copy()
    xyz[:,2] -= ground
    result = (xyz, intensity, meta, ground, manifest)
    return (*result, groups) if include_groups else result


def rotation(yaw, pitch, roll):
    # Right/down/forward camera axes in east/north/up coordinates.
    forward = np.array([np.cos(pitch)*np.sin(yaw), np.cos(pitch)*np.cos(yaw), np.sin(pitch)])
    right = np.array([np.cos(yaw), -np.sin(yaw), 0.])
    down = np.cross(forward, right)
    return np.stack((np.cos(roll)*right+np.sin(roll)*down,
                     -np.sin(roll)*right+np.cos(roll)*down, forward))


def project(xyz, parameters, size):
    cx, cy, cz, yaw, pitch, roll, logf = parameters
    r = rotation(yaw, pitch, roll)
    camera = (xyz-np.array([cx,cy,cz])) @ r.T
    uv = camera[:,:2] / np.maximum(camera[:,2:3], 1e-6) * np.exp(logf)
    uv += np.array(size)/2
    return uv, camera[:,2]


def photo(path, longest=1000):
    original = Image.open(path)
    exif = original.getexif().get_ifd(34665)
    im = ImageOps.exif_transpose(original).convert('RGB')
    scale = longest / max(im.size)
    size = tuple(round(x*scale) for x in im.size)
    # EXIF focal plane resolution takes precedence over 35mm-equivalent estimate.
    if exif.get(41486) and exif.get(41488)==2 and exif.get(37386):
        f = float(exif[37386]) * float(exif[41486]) / 25.4 * scale
        reason = 'EXIF physical focal length and focal-plane pixels per inch'
    elif exif.get(41989):
        f = max(size)*float(exif[41989])/36
        reason = 'EXIF 35mm-equivalent focal length; long-edge approximation'
    else:
        raise ValueError('No focal-length initialization available')
    return np.asarray(im.resize(size, Image.Resampling.LANCZOS)), f, reason


def central_stone_mask(rgb):
    # A bounded hypothesis for this buff-stone landmark, not semantic inference.
    # No hand-clicked image points, outlines, or generated building dimensions.
    x = rgb.astype(float)
    mask = ((x[:,:,0]-x[:,:,2]>8) & (x[:,:,1]-x[:,:,2]>6) &
            (np.abs(x[:,:,0]-x[:,:,1])<20) & (x.mean(2)>35) &
            ((x.max(2)-x.min(2)) / np.maximum(x.max(2),1)<.4)).astype('uint8')
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5,5),np.uint8))
    h,w = mask.shape
    # Only the upper central subject: avoid cars and connecting street foreground.
    mask[int(h*.66):] = 0
    mask[:,:int(w*.25)] = 0
    mask[:,int(w*.75):] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n<2:
        raise ValueError('No central colour component')
    candidates = []
    for i in range(1,n):
        yy,xx = np.nonzero(labels==i)
        central = np.sum((np.abs(xx-w/2)<w*.12) & (yy<h*.55))
        candidates.append(central)
    mask = (labels == (np.argmax(candidates)+1)).astype('uint8')
    contours,_ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(mask, contours, -1, 1, -1)
    # Seed classical GrabCut from that colour component so a neutral/dark roof
    # is not automatically discarded merely because it is not buff-coloured.
    yy,xx = np.nonzero(mask)
    top = int(yy.min())
    upper = (yy<top+max(10,int(h*.08)))
    left,right = int(xx[upper].min()),int(xx[upper].max())
    gc = np.full((h,w),cv2.GC_BGD,np.uint8)
    gc[:int(h*.66),int(w*.25):int(w*.75)] = cv2.GC_PR_BGD
    gc[mask.astype(bool)] = cv2.GC_PR_FGD
    gc[max(0,top-int(h*.08)):top,left:right+1] = cv2.GC_PR_FGD
    gc[cv2.erode(mask,np.ones((7,7),np.uint8)).astype(bool)] = cv2.GC_FGD
    cv2.setRNGSeed(0)
    cv2.grabCut(rgb.copy(),gc,None,np.zeros((1,65)),np.zeros((1,65)),5,cv2.GC_INIT_WITH_MASK)
    mask = np.isin(gc,[cv2.GC_FGD,cv2.GC_PR_FGD]).astype('uint8')
    contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(mask,contours,-1,1,-1)
    return mask


def silhouette(xyz, parameters, size):
    uv, depth = project(xyz, parameters, size)
    w,h = size
    indices = np.rint(uv).astype(int)
    keep = (depth>1) & (indices[:,0]>=0) & (indices[:,0]<w) & (indices[:,1]>=0) & (indices[:,1]<h)
    mask = np.zeros((h,w),np.uint8)
    mask[indices[keep,1],indices[keep,0]] = 1
    mask = cv2.dilate(mask,np.ones((3,3),np.uint8))
    mask = cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(mask,contours,-1,1,-1)
    return mask


def run(photo_id, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    start = time.monotonic()
    cv2.setNumThreads(4)
    photos = ROOT/'runs/water-tower-facade-photos'
    assets = json.loads((photos/'manifest.json').read_text())['assets']
    asset = next(a for a in assets if a['id']==photo_id)
    path = photos/asset['file']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=asset['sha256']:
        raise ValueError('Photo checksum changed')
    rgb,f,reason = photo(path,800)
    xyz,intensity,meta,ground,manifest = load_lidar()
    # 20 cm deduplication only for silhouette speed; original remains unchanged.
    _,unique = np.unique(np.floor(xyz/.2).astype(int),axis=0,return_index=True)
    sampled = xyz[unique]
    project_geo = Transformer.from_crs(4326,meta['crs'],always_xy=True)
    cx,cy = project_geo.transform(float(asset['source_camera_longitude']),float(asset['source_camera_latitude']))
    target = np.median(xyz[xyz[:,2]>20],axis=0)
    yaw = np.arctan2(target[0]-cx,target[1]-cy)
    pitch = np.arctan2(22-1.7,np.linalg.norm(target[:2]-[cx,cy]))
    initial = np.array([cx,cy,1.7,yaw,pitch,0,np.log(f)])
    mask = central_stone_mask(rgb)
    size = (rgb.shape[1],rgb.shape[0])
    # Freeze an upper-image fitting domain. Lower facade and mast are excluded
    # from this initial silhouette hypothesis, not silently treated as validated.
    valid = np.zeros(mask.shape,bool)
    valid[int(size[1]*.02):int(size[1]*.50),int(size[0]*.25):int(size[0]*.75)] = True
    bounds = [(cx-15,cx+15),(cy-15,cy+15),(1,3),(yaw-.45,yaw+.45),
              (max(0,pitch-.3),min(1.4,pitch+.3)),(-.12,.12),(np.log(f*.75),np.log(f*1.25))]
    def cost(p):
        rendered = silhouette(sampled,p,size).astype(bool)
        union = ((rendered|mask.astype(bool)) & valid).sum()
        iou = ((rendered & mask.astype(bool)) & valid).sum()/max(1,union)
        return 1-iou
    results=[]
    for multiplier in (1.,.85,1.15):
        seed = initial.copy(); seed[6] += np.log(multiplier)
        fit = minimize(cost,seed,method='Powell',bounds=bounds,
                       options={'maxiter':35,'maxfev':2200,'xtol':1e-4,'ftol':1e-5})
        results.append({'parameters':fit.x.tolist(),'loss':float(fit.fun),'evaluations':int(fit.nfev)})
        print(photo_id, 'candidate',len(results), 'IoU',1-fit.fun,flush=True)
        if time.monotonic()-start>180:
            break
    best = min(results,key=lambda r:r['loss'])
    for label,p in [('initial',initial),('candidate',best['parameters'])]:
        projected = silhouette(sampled,p,size)
        overlay = rgb.copy()
        edge = projected-cv2.erode(projected,np.ones((3,3),np.uint8))
        overlay[edge.astype(bool)] = [255,50,40]
        Image.fromarray(overlay).save(output/f'{label}-overlay.jpg')
    Image.fromarray(mask*255).save(output/'colour-mask.png')
    report = {'status':'candidate_not_admitted', 'asset':asset, 'focal_initialization':reason,
        'image_size':size, 'initial_parameters':initial.tolist(), 'candidates':results,
        'best':best, 'parameter_order':['east','north','height_above_county_ground','yaw','pitch','roll','log_focal_pixels'],
        'ground_elevation_m':ground,'point_source_sha256':manifest['points_sha256'],
        'training_domain_fractional_xy':[.25,.02,.75,.50],
        'llm_used':False,'geometry_changed':False,'world_modified':False,
        'manual_correspondences':False,'elapsed_seconds':time.monotonic()-start,
        'limitations':['Colour mask assumes central buff-stone subject; not a generic building detector.',
            'GPS, focal length, principal point, lens distortion and camera height remain uncertain.',
            'Silhouette overlap is a training score, not texture alignment or geographic accuracy.',
            'Symmetry can produce an incorrect face assignment; independent controls required.',
            '2013/2025 photographs differ in date from 2022 LiDAR.']}
    (output/'registration.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'output':str(output),'best_iou':1-best['loss'],'elapsed_seconds':report['elapsed_seconds']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--photo', choices=['west','south-a','south-b'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.photo,args.output)
