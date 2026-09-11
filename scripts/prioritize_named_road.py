"""Prioritize a directional OSM road corridor without changing the city plan.

The frozen metric OSM index supplies road geometry.  The resulting tile list
is recorded with source hashes so rerunning it chooses the same pending work.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from pyproj import CRS, Transformer
from shapely.geometry import LineString, box
from shapely.ops import transform, unary_union

from chicago_tiles import Journal, digest
from metric_way_index import sha


def route_tiles(plan,index_path,name,north_from_y,corridor_m):
    """Return northward city tiles intersecting an observed named-road buffer."""
    plan=Path(plan);index_path=Path(index_path)
    document=json.loads((plan/'plan.json').read_text());frame=document['frame']
    if corridor_m<=0 or not isinstance(name,str) or not name.strip():raise ValueError('Road name and positive corridor required')
    db=sqlite3.connect(index_path.as_uri()+'?mode=ro',uri=True)
    try:
        metadata=json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])
        if metadata.get('crs')!=CRS.from_user_input(frame['crs']).to_wkt():raise ValueError('Road index frame differs from city plan')
        rows=db.execute('SELECT payload FROM ways WHERE payload LIKE ? ORDER BY seq',('%'+name+'%',)).fetchall()
    finally:db.close()
    project=Transformer.from_crs(4326,frame['crs'],always_xy=True)
    lines=[];observed=[]
    for (raw,) in rows:
        way=json.loads(raw);label=way.get('tags',{}).get('name','')
        if name.casefold() not in label.casefold():continue
        coords=way.get('coordinates',[])
        if len(coords)<2:continue
        line=transform(project.transform,LineString(coords))
        # Direction is geographic north in the common metric frame, beginning
        # at the explicit starting ordinate instead of an inferred landmark.
        north=line.intersection(box(-10_000_000,north_from_y,10_000_000,10_000_000))
        if not north.is_empty:
            lines.append(north);observed.append({'id':way['id'],'name':label})
    if not lines:raise ValueError('No northward observed road geometry matched')
    road=unary_union(lines);corridor=road.buffer(corridor_m,cap_style=2,join_style=2)
    selected=[]
    for tile in document['tiles']:
        extent=box(tile['west'],tile['north']-tile['size'],tile['west']+tile['size'],tile['north'])
        if extent.intersects(corridor):
            # North-progress first; distance only resolves side-by-side tiles.
            center=extent.centroid
            selected.append((float(center.y),float(center.distance(road)),tile['id']))
    selected.sort()
    if not selected:raise ValueError('Road corridor did not meet Chicago plan tiles')
    result={'schema':'named-road-priority-v1','plan_sha256':digest(document),'frame':frame,
            'road_name':name,'north_from_y':north_from_y,'corridor_m':corridor_m,
            'index':str(index_path.resolve()),'index_sha256':sha(index_path),
            'index_metadata':metadata,'matched_ways':observed,
            'tiles':[tile for _,_,tile in selected],'llm_used':False}
    result['sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def apply(plan,index_path,name,north_from_y,corridor_m,label,output):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    result=route_tiles(plan,index_path,name,north_from_y,corridor_m)
    journal=Journal(Path(plan)/'jobs.sqlite',json.loads((Path(plan)/'plan.json').read_text()))
    try:
        result.update(label=label,scheduled_pending_jobs=journal.prioritize_tiles(result['tiles'],label))
        result['stage_counts_after']=journal.summary()
    finally:journal.close()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--index',type=Path,required=True)
    p.add_argument('--name',default='Lake Shore Drive');p.add_argument('--north-from-y',type=float,default=0.0)
    p.add_argument('--corridor-m',type=float,default=384.0);p.add_argument('--label',default='lake-shore-drive-north-001')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    value=apply(a.plan,a.index,a.name,a.north_from_y,a.corridor_m,a.label,a.output)
    print(json.dumps({'tiles':len(value['tiles']),'scheduled_pending_jobs':value['scheduled_pending_jobs'],
                      'label':value['label'],'output':str(a.output)},indent=2))
