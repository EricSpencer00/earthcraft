"""Prepare and replay one Roofer reconstruction from frozen Water Tower inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import laspy
import numpy as np
from shapely.geometry import mapping

from building_audit import county_objects


ROOT=Path(__file__).resolve().parents[1]
OBJECTID=833197
LAS_SCALE=1e-6  # metre coordinates; sub-micrometre export quantisation


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_cityjson(path):
    """Compare physical CityJSON geometry, excluding metadata and run statistics."""
    transform=None;records=[]
    for line in Path(path).read_text().splitlines():
        item=json.loads(line)
        if item.get('type')=='CityJSON':
            transform=item.get('transform');continue
        if item.get('type')!='CityJSONFeature':
            records.append(item)
            continue
        vertices=item['vertices']
        if transform:
            scale,translate=transform['scale'],transform['translate']
            vertices=[[v[i]*scale[i]+translate[i] for i in range(3)] for v in vertices]
        def ring(value):
            coordinates=tuple(tuple(vertices[v]) for v in value)
            rotations=lambda a:[a[i:]+a[:i] for i in range(len(a))]
            return min(rotations(coordinates)+rotations(coordinates[::-1]))
        def boundaries(value):
            if value and all(isinstance(v,int) for v in value):return ring(value)
            # Sorting sibling groups ignores serializer ordering, but preserves
            # every ring's adjacency and its enclosing surface/shell grouping.
            return tuple(sorted(boundaries(v) for v in value))
        objects=[]
        for identifier,obj in sorted(item['CityObjects'].items()):
            geometries=[]
            for geometry in obj.get('geometry',[]):
                geometries.append({'type':geometry['type'],'lod':geometry.get('lod'),
                    'boundaries':boundaries(geometry['boundaries'])})
            objects.append({'id':identifier,'type':obj['type'],'geometry':geometries})
        records.append(objects)
    return json.dumps(records,sort_keys=True,separators=(',',':'))


def prepare(points,source,output):
    points,source,output=map(Path,(points,source,output))
    manifest=json.loads((points/'manifest.json').read_text())
    meta=json.loads((source/'sources.json').read_text())
    if sha(points/'points.npz')!=manifest['points_sha256']:raise ValueError('Frozen point checksum changed')
    if manifest['output_horizontal_crs']!=meta['crs'] or (meta['size'],meta['west'],meta['north'])!=(64,-32,33):
        raise ValueError('Water Tower source frame mismatch')
    tower=[o for o in county_objects(source,meta) if o['source_id']==OBJECTID]
    if len(tower)!=1:raise ValueError('Expected exactly one County OBJECTID 833197 footprint')
    output.mkdir(parents=True,exist_ok=True)
    with np.load(points/'points.npz') as data:
        xyz=np.asarray(data['xyz']);classification=np.asarray(data['classification'])
        withheld=np.asarray(data['withheld']);intensity=np.asarray(data['intensity'])
        returns=np.asarray(data['return_number'])
    header=laspy.LasHeader(point_format=3,version='1.2')
    header.scales=np.array([LAS_SCALE,LAS_SCALE,LAS_SCALE])
    header.offsets=xyz.min(axis=0)
    las=laspy.LasData(header);las.x,las.y,las.z=xyz.T
    las.classification=classification;las.withheld=withheld.astype(bool)
    las.intensity=intensity;las.return_number=returns
    las_path=output/'water-tower-2022.las';las.write(las_path)
    with laspy.open(las_path) as reader:
        exported=reader.read()
    error=float(np.abs(np.column_stack((exported.x,exported.y,exported.z))-xyz).max())
    if error>LAS_SCALE/2+1e-12:raise ValueError('LAS coordinate quantisation exceeded declared bound')
    footprint=output/'water-tower-833197.geojson'
    footprint.write_text(json.dumps({'type':'FeatureCollection','name':'water_tower_833197','crs':
        {'type':'name','properties':{'name':meta['crs']}},'features':[{'type':'Feature','id':'833197',
        'properties':{'id':'833197','objectid':OBJECTID,'ground_m':tower[0]['ground_m']},
        'geometry':mapping(tower[0]['geometry'])}]}))
    classes,counts=np.unique(classification,return_counts=True)
    report={'objectid':OBJECTID,'point_count':int(len(xyz)),'class_counts':dict(zip(map(str,classes),map(int,counts))),
        'withheld_count':int(withheld.sum()),'horizontal_crs_wkt':meta['crs'],
        'vertical_datum':manifest['vertical_datum'],'vertical_units':'metres NAVD88, Geoid18; source feet normalized using 1200/3937',
        'las_scale_m':LAS_SCALE,'max_coordinate_export_error_m':error,
        'footprint_crs_wkt':meta['crs'],'input_hashes':{str(points/'points.npz'):sha(points/'points.npz'),
        str(points/'manifest.json'):sha(points/'manifest.json'),str(source/'sources.json'):sha(source/'sources.json'),
        str(source/'cook-buildings-2022.json'):sha(source/'cook-buildings-2022.json')},
        'generated_hashes':{str(las_path):sha(las_path),str(footprint):sha(footprint)},
        'classification_policy':'Original LAS classifications copied unchanged; the Roofer command records its explicit building-class association.'}
    (output/'input-report.json').write_text(json.dumps(report,indent=2))
    return las_path,footprint,report


def run(binary,points,source,output,bld_class=1):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    las,footprint,report=prepare(points,source,output/'input')
    commands=[];runs=[]
    for name in ('first','replay'):
        destination=output/name;destination.mkdir()
        command=[str(Path(binary).resolve()),'--jobs','1','--bld-class',str(bld_class),'--grnd-class','2',
                 '--id-attribute','id','--h-terrain-attribute','ground_m','--h-terrain-strategy','user',
                 str(las),str(footprint),str(destination)]
        started=time.monotonic();result=subprocess.run(command,text=True,capture_output=True)
        elapsed=time.monotonic()-started
        (destination/'stdout.log').write_text(result.stdout);(destination/'stderr.log').write_text(result.stderr)
        cityjson=sorted(destination.glob('*.city.jsonl'))
        runs.append({'name':name,'returncode':result.returncode,'elapsed_seconds':elapsed,
                     'cityjsonseq':[p.name for p in cityjson],
                     'geometry_sha256':hashlib.sha256(canonical_cityjson(cityjson[0]).encode()).hexdigest() if cityjson else None})
        commands.append(command)
    report.update(roofer={'binary':str(Path(binary).resolve()),'binary_sha256':sha(binary),'bld_class':bld_class,
        'classification_policy':f'Uses original class {bld_class} as Roofer building points; no classifications were relabeled.',
        'version':subprocess.run([str(binary),'--version'],text=True,capture_output=True,check=True).stdout.strip(),
        'commands':commands,'runs':runs,'canonical_geometry_replay_equal':runs[0]['geometry_sha256']==runs[1]['geometry_sha256']})
    (output/'result.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--points',type=Path,default=ROOT/'runs/water-tower-points-2022')
    p.add_argument('--source',type=Path,default=ROOT/'runs/water-tower-focus-64')
    p.add_argument('--binary',type=Path,default=ROOT/'vendor/roofer-v1.0.0/bin/roofer')
    p.add_argument('--bld-class',type=int,default=1)
    a=p.parse_args();print(json.dumps(run(a.binary,a.points,a.source,a.output,a.bld_class),indent=2))


if __name__=='__main__':main()
