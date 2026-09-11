"""Require support from two calibrated stereo pairs; reference geometry never read."""
import os,subprocess,sys,json,time
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
out=Path(__file__).resolve().parents[1]/'runs/imagery-proof';started=time.monotonic()
pairs=[(0,1),(0,3),(1,3)] # image index 2 remains held out
clouds=[];colors=[];reports=[]
for a,b in pairs:
 directory=out/f'pair-{a}-{b}';directory.mkdir(exist_ok=True)
 env=os.environ.copy();env.update(PHOTO_PAIR=f'{a},{b}',PHOTO_OUT=str(directory))
 with (directory/'run.log').open('w') as log:
  subprocess.run([sys.executable,str(Path(__file__).with_name('photo_dense_probe.py')),'--uncertainty-filter','--adaptive-disparity'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600,check=True)
 data=np.load(directory/'dense-filtered.npz');clouds.append(data['xyz']);colors.append(data['rgb']);reports.append(json.loads((directory/'dense-filtered-report.json').read_text()))
trees=[cKDTree(p) for p in clouds];retained=[];rgb=[];counts=[]
for i,points in enumerate(clouds):
 support=np.zeros(len(points),bool)
 for j,tree in enumerate(trees):
  if i==j:continue
  distance,_=tree.query(points,workers=4,distance_upper_bound=.1)
  support |= np.isfinite(distance)
 retained.append(points[support]);rgb.append(colors[i][support]);counts.append(int(support.sum()))
np.savez_compressed(out/'dense-multiview.npz',xyz=np.concatenate(retained),rgb=np.concatenate(rgb))
report={'status':'unvalidated_multiview_candidate','pairs':pairs,'heldout_index':2,'pair_points':[len(p) for p in clouds],'supported_points':counts,'support_radius_dataset_units':.1,'support_rule':'another stereo pair within radius; pairs share photos so not independent observations','elapsed_seconds':time.monotonic()-started,'reference_geometry_used':False}
(out/'multiview-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
