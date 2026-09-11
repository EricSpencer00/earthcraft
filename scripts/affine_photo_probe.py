"""Bounded affine-view RootSIFT diagnostic on frozen photos; no scene mutation.

Uses the affine sampling idea illustrated by OpenCV's asift.py, with only seven
views per photo. It is not full ASIFT or independent geographic registration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageOps
from facade_photos import validate_titles

ROOT=Path(__file__).resolve().parents[1]
VIEWS=[(1.,0.)]+[(tilt,angle) for tilt in (np.sqrt(2),2.) for angle in (0.,60.,120.)]


def affine_view(gray,mask,tilt,angle):
    if not 1<=tilt<=2 or gray.ndim!=2 or mask.shape!=gray.shape:
        raise ValueError('Bounded grayscale affine view required')
    radians=np.deg2rad(angle)
    rotation=np.array([[np.cos(radians),-np.sin(radians)],
                       [np.sin(radians),np.cos(radians)]])
    linear=np.diag([1/tilt,1.])@rotation
    h,w=gray.shape
    corners=np.array([[0,0],[w-1,0],[0,h-1],[w-1,h-1]])@linear.T
    lower=np.floor(corners.min(0));upper=np.ceil(corners.max(0))
    matrix=np.column_stack((linear,-lower))
    size=tuple(map(int,upper-lower+1))
    # Bounded smoothing is an anti-aliasing approximation, not super-resolution.
    image=cv2.GaussianBlur(gray,(3,3),.5) if tilt>1 else gray
    warped=cv2.warpAffine(image,matrix,size,flags=cv2.INTER_LINEAR)
    valid=cv2.warpAffine(mask,matrix,size,flags=cv2.INTER_NEAREST)
    valid=cv2.erode(valid,np.ones((5,5),np.uint8))
    return warped,valid,cv2.invertAffineTransform(matrix)


def rootsift(descriptors):
    descriptors=np.asarray(descriptors,np.float32)
    return np.sqrt(descriptors/np.maximum(descriptors.sum(1,keepdims=True),1e-12))


def spatial_keys(keys,shape,budget=1200):
    """Select within the valid ROI, round-robin over a fixed 4x4 image grid."""
    if not 1<=budget<=1200:raise ValueError('Feature budget must be 1..1200')
    h,w=shape
    buckets={}
    for key in sorted(keys,key=lambda k:(-k.response,k.pt,k.size,k.angle)):
        cell=(min(3,int(4*key.pt[0]/w)),min(3,int(4*key.pt[1]/h)))
        buckets.setdefault(cell,[]).append(key)
    selected=[];depth=0
    while len(selected)<budget:
        layer=[buckets[cell][depth] for cell in sorted(buckets) if len(buckets[cell])>depth]
        if not layer:break
        selected.extend(layer[:budget-len(selected)]);depth+=1
    return selected


def features(gray,mask,views,selection='legacy'):
    if selection not in ('legacy','roi-spatial'):raise ValueError('Unknown feature selection')
    # OpenCV retains nfeatures *before* applying the pixel mask. Detect without
    # that cap, then allocate the descriptor budget only among valid ROI points.
    detector=cv2.SIFT_create(nfeatures=1200 if selection=='legacy' else 0)
    points=[];descriptors=[];counts=[]
    for tilt,angle in views:
        image,valid,inverse=affine_view(gray,mask,tilt,angle)
        if selection=='legacy':
            keys,values=detector.detectAndCompute(image,valid)
        else:
            keys=spatial_keys(detector.detect(image,valid),image.shape)
            keys,values=detector.compute(image,keys)
        counts.append(len(keys))
        if values is None:continue
        xy=np.array([key.pt for key in keys])
        points.append(np.c_[xy,np.ones(len(xy))]@inverse.T)
        descriptors.append(rootsift(values))
    return (np.concatenate(points).astype(np.float32) if points else np.empty((0,2),np.float32),
            np.concatenate(descriptors) if descriptors else np.empty((0,128),np.float32),counts)


def distinct_matches(a,b,ratio=.7):
    pa,da=a[:2];pb,db=b[:2]
    if len(da)<2 or len(db)<2:return np.empty((0,2)),np.empty((0,2))
    cv2.setRNGSeed(0)
    matcher=cv2.FlannBasedMatcher({'algorithm':1,'trees':4},{'checks':64})
    candidates=[]
    for neighbors in matcher.knnMatch(da,db,k=min(8,len(db))):
        first=neighbors[0]
        second=next((m for m in neighbors[1:] if np.linalg.norm(pb[m.trainIdx]-pb[first.trainIdx])>3),None)
        if second is not None and first.distance<ratio*second.distance:
            candidates.append(first)
    source_cells=set();target_cells=set();matches=[]
    for match in sorted(candidates,key=lambda m:(m.distance,m.queryIdx,m.trainIdx)):
        source=tuple(np.floor(pa[match.queryIdx]/3).astype(int))
        target=tuple(np.floor(pb[match.trainIdx]/3).astype(int))
        if source in source_cells or target in target_cells:continue
        source_cells.add(source);target_cells.add(target);matches.append(match)
    return (np.array([pa[m.queryIdx] for m in matches],np.float32).reshape(-1,2),
            np.array([pb[m.trainIdx] for m in matches],np.float32).reshape(-1,2))


def fit(a,b,sizes):
    pa,pb=distinct_matches(a,b)
    cv2.setRNGSeed(0)
    matrix,mask=cv2.findHomography(pa,pb,cv2.RANSAC,3,maxIters=5000,confidence=.995) if len(pa)>=4 else (None,None)
    keep=np.zeros(len(pa),bool) if mask is None else mask.ravel().astype(bool)
    count=int(keep.sum());fractions=[];median=None
    if count>=4:
        predicted=cv2.perspectiveTransform(pa[keep,None,:],matrix).reshape(-1,2)
        median=float(np.median(np.linalg.norm(predicted-pb[keep],axis=1)))
        for points,(width,height) in zip((pa[keep],pb[keep]),sizes):
            fractions.append(float(cv2.contourArea(cv2.convexHull(points)))/(width*height))
    else:fractions=[0.,0.]
    candidate=count>=30 and min(fractions)>=.02 and median is not None and median<=2
    return {'distinct_ratio_matches':len(pa),'homography_inliers':count,
            'inlier_hull_image_fractions':fractions,'median_inlier_reprojection_px':median,
            'matching_candidate_gate':candidate,'independent_geographic_validation':False},pa,pb,keep


def run(output,selection='legacy',additional_photos=None):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    cv2.setNumThreads(4);start=time.monotonic()
    frozen=json.loads((ROOT/'runs/water-tower-facade-evidence-001/report.json').read_text())['photo_sha256']
    paths={name:ROOT/'runs/water-tower-facade-photos'/f'{name}.jpg' for name in ('west','south-a','south-b')}
    paths['west-check']=ROOT/'runs/water-tower-facade-validation-photo/west-check.jpg'
    pairs=[('west','west-check'),('west','south-a'),('south-a','south-b')]
    if additional_photos is not None:
        additional_photos=Path(additional_photos)
        manifest=json.loads((additional_photos/'manifest.json').read_text())
        if manifest['status']!='originals_verified_registration_pending' or manifest['llm_used']:
            raise ValueError('Only checksum-verified original photo batches accepted')
        assets=manifest['assets']
        validate_titles([(a['id'],a['title']) for a in assets])
        for asset in assets:
            name=asset['id']
            if name in paths or asset['file']!=f'{name}.jpg':raise ValueError('Duplicate or unsafe photo path')
            paths[name]=additional_photos/asset['file'];frozen[name]=asset['sha256']
            pairs.extend([('west',name),('south-a',name)])
    images={};all_features={};sizes={};counts={}
    for name,path in paths.items():
        if hashlib.sha256(path.read_bytes()).hexdigest()!=frozen[name]:raise ValueError('Frozen photograph changed')
        image=ImageOps.exif_transpose(Image.open(path)).convert('RGB');image.thumbnail((1200,1200))
        images[name]=np.asarray(image);sizes[name]=image.size
        gray=cv2.cvtColor(images[name],cv2.COLOR_RGB2GRAY);mask=np.full(gray.shape,255,np.uint8)
        if name=='west':
            original=np.asarray(Image.open(ROOT/'runs/water-tower-registration-west-002/colour-mask.png'))
            mask=cv2.resize(original,image.size,interpolation=cv2.INTER_NEAREST)
        counts[name]={}
        for mode,views in [('identity',VIEWS[:1]),('affine',VIEWS)]:
            if time.monotonic()-start>240:raise TimeoutError('Affine feature probe exceeded four minutes')
            all_features[name,mode]=features(gray,mask,views,selection)
            counts[name][mode]=all_features[name,mode][2]
        print(f'Features complete: {name}',flush=True)
    results=[]
    for first,second in pairs:
        for mode in ('identity','affine'):
            if time.monotonic()-start>300:raise TimeoutError('Affine matching probe exceeded five minutes')
            metrics,pa,pb,keep=fit(all_features[first,mode],all_features[second,mode],[sizes[first],sizes[second]])
            results.append(dict(pair=[first,second],mode=mode,**metrics))
            np.savez_compressed(output/f'{first}-{second}-{mode}.npz',source_xy=pa,target_xy=pb,inliers=keep)
            # A correspondence diagnostic preserves source pixels; it is not a
            # reconstructed facade or evidence of metre-scale registration.
            height=max(images[first].shape[0],images[second].shape[0]);width=sum(sizes[n][0] for n in (first,second))
            canvas=np.zeros((height,width,3),np.uint8)
            canvas[:images[first].shape[0],:sizes[first][0]]=images[first]
            canvas[:images[second].shape[0],sizes[first][0]:]=images[second]
            for a,b in list(zip(pa[keep],pb[keep]))[:100]:
                cv2.line(canvas,tuple(np.rint(a).astype(int)),tuple(np.rint(b+[sizes[first][0],0]).astype(int)),(255,40,40),1)
            Image.fromarray(canvas).save(output/f'{first}-{second}-{mode}.jpg')
            print(json.dumps(results[-1]),flush=True)
    report={'status':'matching_diagnostic_not_world_admission','photo_sha256':frozen,
            'opencv_version':cv2.__version__,'feature_selection':selection,
            'additional_photo_manifest':str(additional_photos/'manifest.json') if additional_photos else None,
            'descriptor_budget_per_view':1200,'views':VIEWS,'feature_counts':counts,'results':results,
            'seconds':time.monotonic()-start,'llm_used':False,'world_modified':False,
            'fixed_candidate_gate':{'distinct_inliers':30,'minimum_hull_fraction_both_images':.02,'median_reprojection_px':2},
            'limitations':['Seven affine views and RootSIFT with approximate nearest neighbours; not full ASIFT.',
                'All warped views share the same source photo, not independent observations.',
                'Existing unvalidated west foreground mask is reused; other images are not semantically masked.',
                'Near-duplicate south photos are a positive matching control, not independent coverage.',
                'A dominant homography may describe a wrong plane/background or repeating ornaments.',
                'Passing the matching gate does not validate camera poses, metre geometry or facade colour.']}
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--selection',choices=['legacy','roi-spatial'],default='legacy')
    p.add_argument('--additional-photos',type=Path,help='One verified batch from facade_photos.py')
    args=p.parse_args();run(args.output,args.selection,args.additional_photos)
