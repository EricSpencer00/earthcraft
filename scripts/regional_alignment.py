"""Audit exact inherited metre/chunk placement and independent GPS round trips."""
import argparse
import json
from pathlib import Path
import re

from pyproj import CRS,Transformer
from inspect_world import chunks
from metric_frame import validate_frame
from regional_store import materialized
from region_expansion import atomic,sha


def compare_frame(actual,expected):
    if not CRS.from_user_input(actual['crs']).equals(CRS.from_user_input(expected['crs'])):
        raise ValueError('Existing world CRS differs')
    if any(actual[key]!=expected[key] for key in ('west','north','vertical_offset_m','dimension_min_y','dimension_height')):
        raise ValueError('Existing world origin/height translation differs')


def audit(frame,worlds,existing=None):
    validate_frame(frame);reports=[]
    if existing:
        for metadata in existing:
            doc=json.loads(Path(metadata).read_text());compare_frame(doc.get('frame',doc.get('world_frame')),frame)
    for world in worlds:
        world=Path(world);meta=json.loads((world/'earthcraft.json').read_text());source=meta['source']
        compare_frame(meta['world_frame'],frame)
        if (not CRS.from_user_input(source['crs']).equals(CRS.from_user_input(frame['crs'])) or
                meta['vertical_offset_m']!=frame['vertical_offset_m']):
            raise ValueError('Tile source CRS or vertical translation differs')
        offsets=[source['west']-frame['west'],frame['north']-source['north']]
        if offsets!=meta['world_offset_xz'] or any(value%16 for value in offsets):
            raise ValueError('Tile chunk placement differs from projected metre coordinates')
        expected=list(map(int,offsets))
        seen=set();size=source['size'];ox,oz=expected
        with materialized(world):
            for region in (world/'region').glob('r.*.*.mca'):
                rx,rz=map(int,re.fullmatch(r'r\.(-?\d+)\.(-?\d+)\.mca',region.name).groups())
                for slot,tag,_ in chunks(region):
                    x,z=int(tag['xPos']),int(tag['zPos'])
                    if (x//32,z//32)!=(rx,rz) or slot!=(x%32)+(z%32)*32:
                        raise ValueError('Anvil slot/region coordinate mismatch')
                    if not ox//16<=x<(ox+size)//16 or not oz//16<=z<(oz+size)//16 or (x,z) in seen:
                        raise ValueError('Chunk outside declared tile or duplicate')
                    seen.add((x,z))
        if len(seen)!=(size//16)**2:raise ValueError('Incomplete positioned tile')
        reports.append({'world':str(world.resolve()),'offset_xz':expected,'chunks':len(seen),'manifest_sha256':sha(world/'earthcraft.json')})
    project=Transformer.from_crs(4326,frame['crs'],always_xy=True);inverse=Transformer.from_crs(frame['crs'],4326,always_xy=True)
    anchors=[]
    for name,lon,lat in [('Water Tower frame origin',-87.62443,41.8972),('Elmhurst planning anchor',-87.9403,41.8995)]:
        east,north=project.transform(lon,lat);again=inverse.transform(east,north)
        if max(abs(again[0]-lon),abs(again[1]-lat))>1e-9:raise ValueError('Geographic round trip failed')
        anchors.append({'name':name,'longitude':lon,'latitude':lat,'world_x':east-frame['west'],'world_z':frame['north']-north})
    if abs(anchors[0]['world_x']-32)>1e-6 or abs(anchors[0]['world_z']-33)>1e-6:
        raise ValueError('Inherited Water Tower origin moved')
    return {'result':'pass','frame':frame,'worlds':reports,'anchors':anchors,
            'metres_per_block':1,'world_y':'metre NAVD88 elevation - 116; each survey needs its own datum check',
            'independent_building_accuracy_verified':False,'client_visual_verified':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frame',type=Path,required=True)
    p.add_argument('--world',type=Path,action='append',default=[]);p.add_argument('--existing',type=Path,action='append')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=audit(json.loads(a.frame.read_text()),a.world,a.existing);atomic(a.output,json.dumps(result,indent=2).encode());print(json.dumps(result),flush=True)
