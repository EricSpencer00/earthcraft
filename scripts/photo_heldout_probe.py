"""Project frozen two-view geometry into a third photograph not used for inference."""
import argparse,json,runpy
from pathlib import Path
import numpy as np,cv2
from local_paths import imagery_root
parser=argparse.ArgumentParser();parser.add_argument('--candidate',choices=['dense-pair','dense-multiview','courtyard'],default='dense-pair');args=parser.parse_args()
s=runpy.run_path(str(Path(__file__).with_name('photo_pair_probe.py')))
out=s['OUT'];sample=np.load(imagery_root()/'courtyard-run/courtyard-inferred.npz' if args.candidate=='courtyard' else out/(args.candidate+'.npz'));xyz=sample['xyz'];colors=sample['rgb']
source_indices=[0,1,3] if args.candidate=='dense-multiview' else [0,1]
if args.candidate=='courtyard':
 manifest=json.loads((imagery_root()/'courtyard-run/status.json').read_text());source_indices=sorted({i for result in manifest['results'] if result['status']=='inferred' for i in result['pair']});assert 2 not in source_indices
name,R,t,K=s['poses'][2]
im=cv2.imread(str(s['ROOT']/'images'/name));scale=1600/im.shape[1];im=cv2.resize(im,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA);K=K.copy();K[:2]*=scale
cam=(R@xyz.T+t[:,None]).T;uv=cam@K.T;uv=uv[:,:2]/uv[:,2:];finite=np.isfinite(uv).all(axis=1)&(cam[:,2]>0);uv=np.rint(np.nan_to_num(uv)).astype(int)
inside=finite&(uv[:,0]>=0)&(uv[:,0]<im.shape[1])&(uv[:,1]>=0)&(uv[:,1]<im.shape[0])
ids=np.flatnonzero(inside);pixels=uv[ids,1]*im.shape[1]+uv[ids,0];order=np.argsort(cam[ids,2]);_,first=np.unique(pixels[order],return_index=True);visible=ids[order[first]]
render=np.zeros_like(im);render[uv[visible,1],uv[visible,0]]=colors[visible][:,::-1]
errors=np.abs(colors[visible].astype(float)-im[uv[visible,1],uv[visible,0]][:,::-1]).mean(axis=1)
report={'status':'held_out_photometric_diagnostic','heldout_image':name,'source_images':[s['poses'][i][0] for i in source_indices],'rendered_pixels':len(visible),'image_pixels':im.shape[0]*im.shape[1],'coverage':len(visible)/(im.shape[0]*im.shape[1]),'median_rgb_absolute_error_255':float(np.median(errors)),'p90_rgb_absolute_error_255':float(np.percentile(errors,90)),'limitations':['Sparse point splatting leaves sampling holes; coverage is not surface completeness','Z-buffer covers reconstructed geometry only; unseen occluders can invalidate comparisons','Exposure and reflection affect color error; no color fitting applied','No filtering or geometry repair based on held-out result']}
(out/(args.candidate+'-heldout-report.json')).write_text(json.dumps(report,indent=2));cv2.imwrite(str(out/'heldout-source.jpg'),im);cv2.imwrite(str(out/(args.candidate+'-heldout-projection.png')),render);print(json.dumps(report,indent=2))
