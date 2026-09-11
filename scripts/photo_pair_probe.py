"""Photo-only correspondences using supplied calibration; NOT a dense reconstruction."""
import json,time,hashlib,os
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from local_paths import imagery_root
ROOT=imagery_root()/'sources/courtyard/courtyard'
OUT=Path(os.environ.get('PHOTO_OUT',str(Path(__file__).resolve().parents[1]/'runs/imagery-proof')))
cv2.setNumThreads(4)
started=time.monotonic()
cameras={}
for line in (ROOT/'dslr_calibration_undistorted/cameras.txt').read_text().splitlines():
 if not line or line.startswith('#'):continue
 v=line.split();assert v[1]=='PINHOLE'
 fx,fy,cx,cy=map(float,v[4:]);cameras[int(v[0])]=np.array([[fx,0,cx],[0,fy,cy],[0,0,1]])
# Ignore every supplied observation line and never open points3D.txt.
poses=[]
with (ROOT/'dslr_calibration_undistorted/images.txt').open() as f:
 for line in f:
  if not line.strip() or line.startswith('#'):continue
  v=line.split();q=np.array(v[1:5],float);r=Rotation.from_quat(q[[1,2,3,0]]).as_matrix()
  poses.append((v[9],r,np.array(v[5:8],float),cameras[int(v[8])]))
  next(f,None)
poses.sort(key=lambda p:p[0]);indices=[int(v) for v in os.environ.get('PHOTO_PAIR','0,1').split(',')];assert len(indices)==2 and len(set(indices))==2;selected=[poses[i] for i in indices]
images=[];features=[];projections=[];sift=cv2.SIFT_create(nfeatures=12000)
for name,r,t,k in selected:
 im=cv2.imread(str(ROOT/'images'/name));assert im is not None
 scale=1600/im.shape[1];im=cv2.resize(im,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
 k=k.copy();k[:2]*=scale
 images.append(im);features.append(sift.detectAndCompute(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),None));projections.append(k@np.column_stack([r,t]))
pairs=cv2.BFMatcher().knnMatch(features[0][1],features[1][1],k=2)
matches=[a for a,b in pairs if a.distance<.7*b.distance]
p1=np.float64([features[0][0][m.queryIdx].pt for m in matches]);p2=np.float64([features[1][0][m.trainIdx].pt for m in matches])
X=cv2.triangulatePoints(*projections,p1.T,p2.T);X=(X[:3]/X[3]).T
errors=[];depth=[]
for pose,P,points in zip(selected,projections,[p1,p2]):
 projected=(P@np.column_stack([X,np.ones(len(X))]).T).T
 errors.append(np.linalg.norm(projected[:,:2]/projected[:,2:]-points,axis=1));depth.append((pose[1]@X.T+pose[2][:,None])[2])
error=np.maximum(*errors);good=np.isfinite(X).all(axis=1)&(error<1.5)&(depth[0]>0)&(depth[1]>0)
centers=[-r.T@t for _,r,t,_ in selected]
rays=[X-c for c in centers];cos=np.sum(rays[0]*rays[1],axis=1)/(np.linalg.norm(rays[0],axis=1)*np.linalg.norm(rays[1],axis=1));angle=np.degrees(np.arccos(np.clip(cos,-1,1)));good &= angle>1
OUT.mkdir(exist_ok=True,parents=True)
np.savez_compressed(OUT/'pair-points.npz',xyz=X[good],image_points=p1[good])
report={'status':'diagnostic_only','sources':[p[0] for p in selected],'source_sha256':[hashlib.file_digest((ROOT/'images'/p[0]).open('rb'),'sha256').hexdigest() for p in selected], 'features':[len(f[0]) for f in features], 'ratio_matches':len(matches),'accepted_points':int(good.sum()), 'median_reprojection_pixels':float(np.median(error[good])) if good.any() else None,'median_ray_angle_degrees':float(np.median(angle[good])) if good.any() else None,'camera_baseline_dataset_units':float(np.linalg.norm(centers[0]-centers[1])),'elapsed_seconds':time.monotonic()-started,'local_cpu_threads':4,'provided_camera_calibration':True,'provided_points_or_depth_used':False,'metric_scale_verified':False,'dense_geometry':False,'held_out_accuracy_measured':False}
(OUT/'pair-report.json').write_text(json.dumps(report,indent=2))
cv2.imwrite(str(OUT/'source-first.jpg'),images[0]);cv2.imwrite(str(OUT/'pair-matches.jpg'),cv2.drawMatches(images[0],features[0][0],images[1],features[1][0],[m for m,ok in zip(matches,good) if ok][:80],None,flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS))
print(json.dumps(report,indent=2))
