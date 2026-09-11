"""Independently read exported model cells and Minecraft transforms back to world space."""
import json,re,zipfile
from pathlib import Path
import numpy as np
from local_paths import imagery_root
root=Path(__file__).resolve().parents[1];out=root/'runs/imagery-proof'
data=np.load(out/'dense-multiview.npz');coords=data['xyz'][:,[1,2,0]]*[1,1,-1];coords-=np.floor(coords.min(axis=0))
expected=np.unique(np.floor(coords*16).astype(int),axis=0)/16+1/32+[0,80,0]
reports=[]
for version in (2,3,5):
 if version==5:
  record=json.loads((out/"detail-export-v5.json").read_text());cloud=np.load(imagery_root()/"courtyard-run/courtyard-inferred.npz")["xyz"]
  crop=record["crop"];cloud=cloud[((cloud>=crop["min"])&(cloud<=crop["max"])).all(axis=1)]
  coords=cloud@np.array(record["world_axis_matrix"]).T;coords-=np.floor(coords.min(axis=0))
  expected=np.unique(np.floor(coords*16).astype(int),axis=0)/16+1/32+[0,80,0]
 world=root/f'worlds/Earthcraft-Photo-Detail-v{version}';actual=[]
 with zipfile.ZipFile(world/'resources.zip') as z:
  for line in (world/'datapacks/photo/data/earthcraft/function/build.mcfunction').read_text().splitlines():
   if not line.startswith('summon'):continue
   pos=np.array(line.split()[2:5],float)
   model=re.search(r'earthcraft:surface_(\d+)',line).group(1)
   rotation=re.search(r'Rotation:\[([\d.]+)f,0f\]',line)
   # Documented built-in Y half-turn occurs before entity yaw rotation.
   yaw=np.deg2rad(float(rotation.group(1)) if rotation else 0)
   matrix=np.array([[np.cos(yaw),0,-np.sin(yaw)],[0,1,0],[np.sin(yaw),0,np.cos(yaw)]])
   for element in json.loads(z.read(f'assets/earthcraft/models/surface_{model}.json'))['elements']:
    local=(np.array(element['from'])+element['to'])/32-.5
    actual.append(pos+matrix@(local*[-1,1,-1]))
 actual=np.array(actual)
 def sort(a):return a[np.lexsort(a.T[::-1])]
 # Integer grid comparison avoids floating-point sorting instability after rotation.
 a=sort(np.rint(actual*32).astype(int));b=sort(np.rint(expected*32).astype(int))
 exact=bool(np.array_equal(a,b));reports.append({'version':version,'cells':len(actual),'exact_cell_centres_match_source_quantization':exact})
 assert exact==(version!=2)
report={'status':'passed','scope':'model cell centres and documented built-in/entity transforms; not source geometry accuracy','variants':reports,'rotation_source':'https://www.minecraft.net/en-us/article/minecraft-snapshot-23w16a'}
(out/'display-coordinate-audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
