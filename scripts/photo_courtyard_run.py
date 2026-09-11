"""Bounded same-scene reconstruction from nearby calibrated photo pairs, no scan inputs."""
import os,sys,time,json,subprocess,runpy,resource
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from local_paths import imagery_root
HERE=Path(__file__).resolve().parent
OUT=imagery_root()/'courtyard-run'
OUT.mkdir(parents=True,exist_ok=True)
s=runpy.run_path(str(HERE/'photo_pair_probe.py'));poses=s['poses']
heldout={2,7,12,17,22,27,32,37};train=[i for i in range(len(poses)) if i not in heldout]
centers=np.array([-r.T@t for _,r,t,k in poses]);directions=np.array([r.T@np.array([0,0,1]) for _,r,t,k in poses])
pairs=set()
for i in train:
 nearby=sorted((np.linalg.norm(centers[i]-centers[j]),j) for j in train if j!=i and directions[i]@directions[j]>.8)
 for distance,j in nearby[:2]:
  if .2<distance<5:pairs.add(tuple(sorted((i,j))))
pairs=sorted(pairs);assert len(pairs)<=60
report={'status':'running','started':time.time(),'heldout_indices':sorted(heldout),'pairs':pairs,'results':[],'reference_inputs':False}
def save():
 (OUT/'status.json').write_text(json.dumps(report,indent=2))
save();clouds=[];colors=[];labels=[]
for a,b in pairs:
 directory=OUT/f'pair-{a}-{b}';directory.mkdir(exist_ok=True)
 env=os.environ.copy();env.update(PHOTO_PAIR=f'{a},{b}',PHOTO_OUT=str(directory))
 try:
  if not (directory/'dense-filtered.npz').exists():
   with (directory/'run.log').open('w') as log:
    process=subprocess.run([sys.executable,str(HERE/'photo_dense_probe.py'),'--uncertainty-filter','--adaptive-disparity'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
   if process.returncode:raise RuntimeError('Pair failed; inspect run.log')
  data=np.load(directory/'dense-filtered.npz');p=data['xyz'];rgb=data['rgb']
  if not len(p):raise RuntimeError('No valid stereo points; pair excluded from fusion')
  # Deduplicate within pair at 2cm sampling without moving retained points.
  _,index=np.unique(np.floor(p/.02).astype(np.int64),axis=0,return_index=True)
  p=p[index];rgb=rgb[index];clouds.append(p);colors.append(rgb);labels.append([a,b])
  report['results'].append({'pair':[a,b],'status':'inferred','sampled_points':len(p)})
 except (RuntimeError,subprocess.TimeoutExpired) as e:
  report['results'].append({'pair':[a,b],'status':'failed','reason':str(e)})
 save()
 if time.time()-report['started']>600:raise SystemExit('10 minute experiment budget')
trees=[cKDTree(p) for p in clouds];retained=[];paint=[]
for i,p in enumerate(clouds):
 supported=np.zeros(len(p),bool)
 # All inferred pairs participate. Agreement is correlated when photos overlap.
 for j,tree in enumerate(trees):
  if i==j:continue
  if np.any(clouds[j].max(axis=0)<p.min(axis=0)-.1) or np.any(clouds[j].min(axis=0)>p.max(axis=0)+.1):continue
  ids=np.flatnonzero(~supported)
  if not len(ids):break
  d,_=tree.query(p[ids],distance_upper_bound=.1,workers=4);supported[ids]|=np.isfinite(d)
 retained.append(p[supported]);paint.append(colors[i][supported])
 report['fusion_pair']=i;save()
xyz=np.concatenate(retained);rgb=np.concatenate(paint)
np.savez_compressed(OUT/'courtyard-inferred.npz',xyz=xyz,rgb=rgb)
report.update(status='candidate_unverified',points=len(xyz),extent=np.ptp(xyz,axis=0).tolist(),elapsed_seconds=time.time()-report['started'],peak_main_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
save();print(json.dumps(report,indent=2))
