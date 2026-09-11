"""Calibrated two-photo CPU stereo diagnostic. No supplied depth/point cloud inputs."""
import runpy,time,json,argparse
from pathlib import Path
import cv2,numpy as np
from stereo_match import match_pair
parser=argparse.ArgumentParser();parser.add_argument('--uncertainty-filter',action='store_true');parser.add_argument('--adaptive-disparity',action='store_true');args=parser.parse_args()
s=runpy.run_path(str(Path(__file__).with_name('photo_pair_probe.py')))
started=time.monotonic(); images=s['images'];poses=s['selected'];P=s['projections'];out=s['OUT']
# Recover resized intrinsics from P=K[R|t].
k=[P[i][:,:3]@poses[i][1].T for i in range(2)]
r=poses[1][1]@poses[0][1].T;t=poses[1][2]-r@poses[0][2]
size=(images[0].shape[1],images[0].shape[0])
R1,R2,P1,P2,Q,roi1,roi2=cv2.stereoRectify(k[0].copy(),np.zeros(5),k[1].copy(),np.zeros(5),size,r.copy(),t.reshape(3,1).copy(),flags=cv2.CALIB_ZERO_DISPARITY,alpha=0)
axis=0 if abs(P2[0,3]) > abs(P2[1,3]) else 1
rect=[]
for im,K,R,proj in zip(images,k,[R1,R2],[P1,P2]):
 maps=cv2.initUndistortRectifyMap(K.copy(),np.zeros(5),R,proj,size,cv2.CV_32FC1)
 rect.append(cv2.remap(im,*maps,cv2.INTER_LINEAR))
g=[cv2.cvtColor(im,cv2.COLOR_BGR2GRAY) for im in rect]
# A symmetric disparity search supports either camera ordering, without assumed scene depth.
minimum=-128;count=256
if args.adaptive_disparity:
 sparse=s['X'][s['good']];camera=(poses[0][1]@sparse.T+poses[0][2][:,None]);rectxyz=R1@camera
 homogeneous=np.vstack([rectxyz,np.ones(rectxyz.shape[1])]);left=P1@homogeneous;right=P2@homogeneous
 disparities=left[axis]/left[2]-right[axis]/right[2]
 low,high=np.percentile(disparities,[1,99]);minimum=int(np.floor((low-16)/16)*16);count=int(np.ceil((high+16-minimum)/16)*16)
 assert 16<=count<=1024, 'Disparity search exceeds bounded CPU budget'
reverse_min=-minimum-count+1
params=dict(numDisparities=count,blockSize=5,P1=8*25,P2=32*25,uniquenessRatio=12,speckleWindowSize=100,speckleRange=2,disp12MaxDiff=1,mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
d,reverse=match_pair(g,axis,minimum,count,{k:v for k,v in params.items() if k!='numDisparities'})
y,x=np.indices(d.shape);xr=np.rint(x-d).astype(int) if axis==0 else x;yr=np.rint(y-d).astype(int) if axis==1 else y
inside=(xr>=0)&(xr<d.shape[1])&(yr>=0)&(yr<d.shape[0]);xr=np.clip(xr,0,d.shape[1]-1);yr=np.clip(yr,0,d.shape[0]-1)
xyz=cv2.reprojectImageTo3D(d,Q)
valid=inside&(d>minimum-1)&(reverse[yr,xr]>reverse_min-1)&(np.abs(d+reverse[yr,xr])<1)&np.isfinite(xyz).all(axis=2)&(xyz[:,:,2]>0)
# Keep only pixels within both rectified valid-image regions and away from black undistortion borders.
valid &= (g[0]>3)&(g[1][yr,xr]>3)
before_filter=int(valid.sum())
if args.uncertainty_filter:
 sensitivity=xyz[:,:,2]**2 * 0.5 / abs(P2[axis,3])
 valid &= sensitivity < 0.25
 # Require meaningful parallax, independently of any reference geometry.
 angle=np.arctan2(np.linalg.norm(t),xyz[:,:,2])
 valid &= angle > np.deg2rad(1)
 for roi,xx,yy in [(roi1,x,y),(roi2,xr,yr)]:
  a,b,w,h=roi;valid &= (xx>=a)&(xx<a+w)&(yy>=b)&(yy<b+h)
name='dense-filtered' if args.uncertainty_filter else 'dense-pair'
world=(poses[0][1].T@(R1.T@xyz[valid].T-poses[0][2][:,None])).T
np.savez_compressed(out/(name+'.npz'),xyz=world,rgb=rect[0][valid][:,::-1],mask=valid)
preview=np.zeros((*d.shape,3),np.uint8)
if valid.any():
 lo,hi=np.percentile(xyz[:,:,2][valid],[5,95]);norm=np.uint8(np.clip((xyz[:,:,2]-lo)/max(hi-lo,1e-6),0,1)*255)
 preview=cv2.applyColorMap(norm,cv2.COLORMAP_VIRIDIS);preview[~valid]=0
cv2.imwrite(str(out/(name+'-depth.png')),preview)
cv2.imwrite(str(out/'rectified-left.jpg'),rect[0])
photo=np.abs(g[0].astype(float)-g[1][yr,xr].astype(float))[valid]
report={'status':'diagnostic_only','disparity_search':[minimum,count],'adaptive_disparity':args.adaptive_disparity,'uncertainty_filter':args.uncertainty_filter,'pre_filter_points':before_filter,'assumed_disparity_error_pixels':0.5,'maximum_depth_sensitivity_dataset_units':0.25,'accepted_pixels':int(valid.sum()),'image_pixels':int(valid.size),'accepted_fraction':float(valid.mean()),'median_matched_grayscale_difference':float(np.median(photo)) if len(photo) else None,'elapsed_seconds':time.monotonic()-started,'metric_scale_verified':False,'held_out_accuracy_measured':False,'supplied_depth_used':False,'supplied_camera_calibration':True,'limitations':['Two-view stereo only; repetitive textures may produce consistent wrong matches','No held-out scan accuracy yet','Unknown areas remain absent','Units and gravity not verified']}
(out/(name+'-report.json')).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
