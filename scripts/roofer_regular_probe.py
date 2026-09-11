"""One transparent ordinary-building Roofer probe from the frozen district crop."""
import argparse
import json
from pathlib import Path
import subprocess
import time

import laspy
import numpy as np
from shapely import contains_xy
from shapely.geometry import box, mapping

from building_audit import county_objects
from roofer_probe import LAS_SCALE, canonical_cityjson, sha


ROOT=Path(__file__).resolve().parents[1]


def select(source,meta,xyz,withheld):
    extent=box(meta['west'],meta['north']-meta['size'],meta['west']+meta['size'],meta['north'])
    candidates=[]
    for obj in county_objects(source,meta):
        geometry=obj['geometry'];rectangle=geometry.minimum_rotated_rectangle
        rectangularity=geometry.area/rectangle.area
        if not (obj['source_id']!=833197 and 3<=obj['height_m']<=30 and 75<=geometry.area<=800 and rectangularity>=.9 and extent.contains(geometry)):
            continue
        observed=contains_xy(geometry.buffer(2),xyz[:,0],xyz[:,1]) & ~withheld
        if observed.sum()>=100:candidates.append((obj,rectangularity,observed))
    if not candidates:raise ValueError('No ordinary-building candidate satisfies the declared source-only rule')
    return min(candidates,key=lambda item:item[0]['source_id'])


def run(output,points=ROOT/'runs/city-crop-fast-001',source=ROOT/'runs/chicago-west-metric-256',binary=ROOT/'vendor/roofer-v1.0.0/bin/roofer'):
    output,points,source,binary=map(Path,(output,points,source,binary))
    if output.exists():raise FileExistsError(output)
    manifest=json.loads((points/'manifest.json').read_text());meta=manifest['grid']
    with np.load(points/'points.npz') as data:
        xyz=np.asarray(data['xyz']);classification=np.asarray(data['classification']);withheld=np.asarray(data['withheld']).astype(bool)
        intensity=np.asarray(data['intensity']);returns=np.asarray(data['return_number'])
    if sha(points/'points.npz')!=manifest['points_sha256']:raise ValueError('Frozen district points changed')
    obj,rectangularity,selected=select(source,meta,xyz,withheld)
    output.mkdir(parents=True);input_dir=output/'input';input_dir.mkdir()
    # Scope the LAS solely to the selected footprint association envelope; values
    # and classifications are copied, never inferred or relabeled.
    indices=np.flatnonzero(selected);coords=xyz[indices]
    header=laspy.LasHeader(point_format=3,version='1.2');header.scales=np.repeat(LAS_SCALE,3);header.offsets=coords.min(0)
    las=laspy.LasData(header);las.x,las.y,las.z=coords.T;las.classification=classification[indices]
    las.withheld=withheld[indices];las.intensity=intensity[indices];las.return_number=returns[indices]
    las_path=input_dir/'ordinary-building.las';las.write(las_path)
    reread=laspy.read(las_path);error=float(np.abs(np.column_stack((reread.x,reread.y,reread.z))-coords).max())
    footprint=input_dir/'ordinary-building.geojson';footprint.write_text(json.dumps({'type':'FeatureCollection','crs':{'type':'name','properties':{'name':meta['crs']}},'features':[{'type':'Feature','id':str(obj['source_id']),'properties':{'id':str(obj['source_id']),'ground_m':obj['ground_m']},'geometry':mapping(obj['geometry'])}]}))
    cls,cnt=np.unique(classification[indices],return_counts=True);class6=coords[classification[indices]==6,2]
    report={'selection_rule':'lowest OBJECTID satisfying: not 833197; height 3..30 m; area 75..800 m²; minimum-rotated-rectangle ratio >=0.9; fully inside frozen 256 m crop; >=100 non-withheld points within footprint +2 m',
        'objectid':obj['source_id'],'footprint_area_m2':obj['geometry'].area,'rectangularity':rectangularity,'provider_height_m':obj['height_m'],'provider_ground_m':obj['ground_m'],
        'point_count':int(len(indices)),'class_counts':dict(zip(map(str,cls),map(int,cnt))),'class6_height_quantiles_m':np.quantile(class6-obj['ground_m'],[.5,.95,.98,1]).tolist() if len(class6) else None,
        'crs':meta['crs'],'vertical_datum':manifest['vertical_datum'],'las_max_error_m':error,
        'input_hashes':{str(points/'points.npz'):sha(points/'points.npz'),str(points/'manifest.json'):sha(points/'manifest.json'),str(source/'cook-buildings-2022.json'):sha(source/'cook-buildings-2022.json')},
        'generated_hashes':{str(las_path):sha(las_path),str(footprint):sha(footprint)}}
    runs=[]
    for name in ('first','replay'):
        directory=output/name;directory.mkdir();command=[str(binary.resolve()),'--jobs','1','--bld-class','6','--grnd-class','2','--id-attribute','id','--h-terrain-attribute','ground_m','--h-terrain-strategy','user',str(las_path),str(footprint),str(directory)]
        start=time.monotonic();result=subprocess.run(command,text=True,capture_output=True);elapsed=time.monotonic()-start
        (directory/'stdout.log').write_text(result.stdout);(directory/'stderr.log').write_text(result.stderr)
        city=next(directory.glob('*.city.jsonl'),None);attrs={}
        if city:attrs=json.loads(city.read_text().splitlines()[1])['CityObjects'][str(obj['source_id'])]['attributes']
        runs.append({'name':name,'returncode':result.returncode,'elapsed_seconds':elapsed,'geometry_sha256':sha_city(city) if city else None,'attributes':attrs})
    report['roofer']={'version':subprocess.run([str(binary),'--version'],text=True,capture_output=True,check=True).stdout.strip(),'bld_class':6,'runs':runs,'replay_equal':runs[0]['geometry_sha256']==runs[1]['geometry_sha256'],'roof_height_residual_m':runs[0]['attributes'].get('rf_h_roof_max',0)-obj['ground_m']-obj['height_m']}
    (output/'result.json').write_text(json.dumps(report,indent=2));return report


def sha_city(path):
    import hashlib
    return hashlib.sha256(canonical_cityjson(path).encode()).hexdigest()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(run(a.output),indent=2))
