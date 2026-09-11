"""Cross-photo colour/visibility check for an independently fitted plane hypothesis."""
import json,runpy
from pathlib import Path
import numpy as np,cv2
from local_paths import imagery_root
root=Path(__file__).resolve().parents[1];out=root/'runs/imagery-proof';s=runpy.run_path(str(root/'scripts/photo_pair_probe.py'))
ground=np.load(out/'ground-plane-candidate.npz')['xyz'];cloud=np.load(imagery_root()/'courtyard-run/courtyard-inferred.npz')['xyz'];samples=[];valids=[]
for index in (0,1,3):
 name,R,t,K=s['poses'][index];im=cv2.imread(str(s['ROOT']/'images'/name));scale=1600/im.shape[1];im=cv2.resize(im,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA);K=K.copy();K[:2]*=scale;h,w=im.shape[:2]
 def project(p):
  c=p@R.T+t;uv=c@K.T;uv=np.rint(uv[:,:2]/uv[:,2:]).astype(int);inside=(c[:,2]>0)&(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h);return c,uv,inside
 c,uv,inside=project(cloud);zbuf=np.full(h*w,np.inf);np.minimum.at(zbuf,uv[inside,1]*w+uv[inside,0],c[inside,2]);zbuf=zbuf.reshape(h,w).astype('float32')
 # Conservative nearby foreground occlusion. Does not manufacture ground depth.
 zbuf=cv2.erode(zbuf,np.ones((7,7),np.uint8))
 c,uv,inside=project(ground);x=np.clip(uv[:,0],0,w-1);y=np.clip(uv[:,1],0,h-1)
 visible=inside&(c[:,2]<=zbuf[y,x]+.06);samples.append(im[y,x][:,::-1].astype(float));valids.append(visible)
 if index==0:
  preview=im.copy()
  for point in uv[visible]:cv2.circle(preview,tuple(point),2,(30,220,40),-1)
  cv2.imwrite(str(out/'ground-plane-visibility.jpg'),preview)
samples=np.array(samples);valids=np.array(valids);accepted=np.zeros(len(ground),bool);colors=np.zeros((len(ground),3));pair_count=np.zeros(len(ground),int)
for a,b in ((0,1),(0,2),(1,2)):
 agreement=valids[a]&valids[b]&(np.abs(samples[a]-samples[b]).mean(axis=1)<15)
 colors[agreement]+=(samples[a,agreement]+samples[b,agreement])/2;pair_count+=agreement
accepted=pair_count>0;colors[accepted]/=pair_count[accepted,None]
np.savez_compressed(out/'ground-plane-visible.npz',xyz=ground[accepted],rgb=np.rint(colors[accepted]).astype('uint8'))
report={'status':'candidate_not_exported','candidate_points':len(ground),'accepted_points':int(accepted.sum()),'source_indices':[0,1,3],'minimum_agreeing_views':2,'max_pair_mean_rgb_error_255':15,'visible_per_view':valids.sum(axis=1).tolist(),'caveats':['Visibility depends on incomplete inferred cloud','Cross-view colour agreement does not prove ground or metric depth','No held-out or scan inputs used']};(out/'ground-plane-visibility.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
