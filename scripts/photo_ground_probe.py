"""Fit a bounded ground-plane hypothesis from photo-inferred points only."""
import json,time
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree,ConvexHull
from local_paths import imagery_root
start=time.monotonic();out=Path(__file__).resolve().parents[1]/'runs/imagery-proof'
r=json.loads((out/'detail-export-v5.json').read_text());data=np.load(imagery_root()/'courtyard-run/courtyard-inferred.npz');p=data['xyz'];crop=r['crop'];p=p[((p>=crop['min'])&(p<=crop['max'])).all(1)]
# Hypothesis only: dataset Z roughly vertical. Search low surfaces rather than tables.
low=p[p[:,2]<np.percentile(p[:,2],20)];rng=np.random.default_rng(173)
q=low[rng.choice(len(low),min(15000,len(low)),replace=False)]
parity=np.floor(q[:,:2]*4).astype(int).sum(1)%2;train=q[parity==0];test=q[parity==1]
best=None;count=0
for _ in range(300):
 a,b,c=train[rng.choice(len(train),3,replace=False)];normal=np.cross(b-a,c-a);norm=np.linalg.norm(normal)
 if norm<1e-8:continue
 normal/=norm
 if abs(normal[2])<.98:continue
 normal*=np.sign(normal[2]);d=-normal@a;inside=np.abs(train@normal+d)<.035
 if inside.sum()>count:best=(normal,d,inside);count=inside.sum()
if best is None:raise RuntimeError('No near-horizontal plane supported')
inliers=train[best[2]];center=inliers.mean(0);_,_,v=np.linalg.svd(inliers-center,full_matrices=False);normal=v[-1]*np.sign(v[-1,2]);d=-normal@center
support=low[np.abs(low@normal+d)<.035]
# Bounded interpolation: inside observed hull AND within25cm of a supporting point.
hull=ConvexHull(support[:,:2]);lo=support[:,:2].min(0);hi=support[:,:2].max(0)
x,y=np.meshgrid(np.arange(lo[0],hi[0],.0625),np.arange(lo[1],hi[1],.0625));xy=np.c_[x.ravel(),y.ravel()]
inside=(xy@hull.equations[:,:2].T+hull.equations[:,2]<=1e-9).all(1);dist,_=cKDTree(support[:,:2]).query(xy,distance_upper_bound=.25,workers=4);xy=xy[inside&np.isfinite(dist)]
z=-(xy@normal[:2]+d)/normal[2];candidate=np.c_[xy,z]
np.savez_compressed(out/'ground-plane-candidate.npz',xyz=candidate)
res=np.abs(test@normal+d)
report={'status':'hypothesis_not_exported','normal':normal.tolist(),'offset':float(d),'tilt_from_dataset_z_degrees':float(np.degrees(np.arccos(normal[2]))),'low_height_percentile':20,'low_height_max':float(low[:,2].max()),'training_points':len(train),'training_inliers':int(count),'heldout_spatial_points':len(test),'heldout_fraction_within_035':float(np.mean(res<.035)),'heldout_residual_median':float(np.median(res)),'support_points':len(support),'candidate_points':len(candidate),'candidate_area_approx_units2':len(candidate)*.0625**2,'maximum_support_distance':.25,'elapsed_seconds':time.monotonic()-start,'caveats':['Spatial split is not independent image evidence','Ground label and roughly vertical Z are hypotheses','No scan used to fit or choose extent','No plane outside observed hull or farther than25cm from support','Appearance, occlusion and independent scan validation pending']}
(out/'ground-plane-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
