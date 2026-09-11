"""Evaluation only: supplied scans never enter reconstruction/export inputs."""
import argparse,json,time,xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from local_paths import imagery_root
from scipy.spatial import cKDTree
root=imagery_root()/'evaluation/reference/courtyard/dslr_scan_eval'
out=Path(__file__).resolve().parents[1]/'runs/imagery-proof'
parser=argparse.ArgumentParser();parser.add_argument('--candidate',choices=['dense-pair','dense-filtered','dense-multiview','courtyard','ground-plane-candidate'],default='dense-pair');args=parser.parse_args()
started=time.monotonic();points=np.load(imagery_root()/'courtyard-run/courtyard-inferred.npz' if args.candidate=='courtyard' else out/(args.candidate+'.npz'))['xyz']
# All candidate points stay in the evaluation denominator, including outliers.
lo,hi=points.min(axis=0)-1,points.max(axis=0)+1
refs=[];counts=[]
for mesh in ET.parse(root/'scan_alignment.mlp').findall('.//MLMesh'):
 transform=np.array(mesh.find('MLMatrix44').text.split(),float).reshape(4,4)
 path=root/mesh.attrib['filename']
 with path.open('rb') as f:
  header=[]
  while True:
   line=f.readline().decode().strip();header.append(line)
   if line=='end_header':break
  offset=f.tell()
 n=int(next(line.split()[2] for line in header if line.startswith('element vertex ')))
 assert header[1]=='format binary_little_endian 1.0'
 vi=header.index('element vertex '+str(n));assert header[vi+1:vi+4]==['property float x','property float y','property float z']
 raw=np.memmap(path,offset=offset,dtype='<f4',mode='r',shape=(n,3))
 kept=0
 for begin in range(0,n,500000):
  chunk=raw[begin:begin+500000].astype(float)@transform[:3,:3].T+transform[:3,3]
  mask=np.isfinite(chunk).all(axis=1)&(chunk>=lo).all(axis=1)&(chunk<=hi).all(axis=1)
  refs.append(chunk[mask].astype(np.float32));kept+=int(mask.sum())
 counts.append({'file':path.name,'vertices':n,'evaluation_bbox_vertices':kept})
reference=np.concatenate(refs);tree=cKDTree(reference);dist,_=tree.query(points,workers=4,distance_upper_bound=1.0)
report={'status':'evaluation_only','candidate_points':len(points),'reference_counts':counts,'distance_units':'dataset coordinates; metre provenance still to verify','distance_search_limit':1.0,'fraction_at_or_beyond_limit':float(np.mean(~np.isfinite(dist))),'candidate_to_scan_distance_quantiles':{name:(float(np.sort(dist)[min(len(dist)-1,int(q*len(dist)))]) if np.isfinite(np.sort(dist)[min(len(dist)-1,int(q*len(dist)))]) else '>=1.0') for name,q in [('p50',.5),('p90',.9),('p95',.95),('p99',.99)]},'fraction_within':{str(t):float(np.mean(dist<t)) for t in [.02,.05,.1,.25,.5,1]},'elapsed_seconds':time.monotonic()-started,'alignment':'provided scan_alignment.mlp; no fitted ICP or rescaling','caveats':['One-sided nearest-surface error does not measure completeness','Camera calibration is supplied and registered to the scan; not fully independent calibration','No reference geometry enters reconstruction','Full candidate denominator retained; reference bbox has one-unit margin']}
(out/(args.candidate+'-scan-evaluation.json')).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
