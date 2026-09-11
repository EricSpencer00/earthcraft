"""Independent-source limitations and frozen strip test for facade experiments."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image,ImageOps

from facade_registration import load_lidar
from facade_surface import vertical_planes

ROOT=Path(__file__).resolve().parents[1]


def run():
    output=ROOT/'runs/water-tower-facade-evidence-001'
    if output.exists():raise FileExistsError(output)
    output.mkdir()
    xyz,_,_,_,manifest,groups=load_lidar(include_groups=True)
    region=(xyz[:,2]>=25)&(xyz[:,2]<=44)
    train=xyz[region&(groups<200)];heldout=xyz[region&(groups>=200)]
    planes=vertical_planes(train,tolerance=.18,minimum=35)
    errors=np.stack([np.abs((heldout-p['center'])@p['normal']) for p in planes]).min(0)
    plane_report={'training_points':len(train),'heldout_points':len(heldout),'fitted_planes':len(planes),
        'training_group_ids':np.unique(groups[region&(groups<200)]).tolist(),
        'heldout_group_ids':np.unique(groups[region&(groups>=200)]).tolist(),
        'heldout_distance_to_nearest_infinite_plane_m':dict(zip(['p50','p95','max'],
            map(float,np.percentile(errors,[50,95,100])))),
        'fraction_within_035m':float((errors<=.35).mean()),
        'scope':'Plane-normal consistency only. Infinite planes do not prove observed support, openings, coverage, or facade ownership.',
        'point_source_sha256':manifest['points_sha256'],'autofill_admitted':False}
    cv2.setNumThreads(4);sift=cv2.SIFT_create(nfeatures=8000)
    features={};hashes={}
    paths=[* (ROOT/'runs/water-tower-facade-photos').glob('*.jpg'),
            *(ROOT/'runs/water-tower-facade-validation-photo').glob('*.jpg')]
    for path in paths:
        im=ImageOps.exif_transpose(Image.open(path)).convert('RGB');im.thumbnail((1600,1600))
        features[path.stem]=sift.detectAndCompute(cv2.cvtColor(np.asarray(im),cv2.COLOR_RGB2GRAY),None)
        hashes[path.stem]=hashlib.sha256(path.read_bytes()).hexdigest()
    matching=[]
    for a,b in [('west','west-check'),('west','south-a'),('south-a','south-b')]:
        ka,da=features[a];kb,db=features[b]
        pairs=cv2.BFMatcher().knnMatch(da,db,k=2)
        good=[x for x,y in pairs if x.distance<.7*y.distance]
        pa=np.float32([ka[m.queryIdx].pt for m in good]);pb=np.float32([kb[m.trainIdx].pt for m in good])
        cv2.setRNGSeed(0)
        h,inliers=cv2.findHomography(pa,pb,cv2.RANSAC,3) if len(good)>=4 else (None,None)
        matching.append({'pair':[a,b],'ratio_matches':len(good),
            'dominant_homography_inliers':int(inliers.sum()) if inliers is not None else 0,
            'accepted_as_independent_texture_validation':False})
    report={'status':'useful_candidate_but_independent_facade_validation_not_passed',
        'strip_plane_check':plane_report,'photo_matches':matching,'photo_sha256':hashes,
        'decision':'Keep the world photo skin experimental. Do not admit complete wall filling or advertise automatic general camera registration.',
        'evidence':['West validation image lacks sufficient robust cross-image support; geographic metadata alone cannot assign a facade.',
            'The south pair is near-duplicate and can support only a nearby-view consistency check, not independent wall coverage.',
            'The conservative full-support plane patch covered only a small fraction of its rectangular extent; no hull-wide fill exported.'],
        'llm_used':False,'world_modified':False}
    (output/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':run()
