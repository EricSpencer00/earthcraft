"""Freeze twenty source-identified buildings before tuning facade appearance.

Footprints establish associations only, never measured walls or source-photo
visibility. Nearby cached photo positions are discovery candidates, not usable
facade coverage. Every selected building stays in the evaluation denominator.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from pyproj import Transformer
from shapely import from_wkb
from shapely.geometry import box, mapping

from playable_acceptance_snapshot import ANCHORS


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def select(index, frame, count=10):
    if count!=10:
        raise ValueError('The approved pilot is ten buildings per area')
    index=Path(index)
    signature=(index.stat().st_size,index.stat().st_mtime_ns)
    db=sqlite3.connect('file:'+str(index.resolve())+'?mode=ro',uri=True)
    records=[]
    try:
        source=json.loads(db.execute("SELECT value FROM meta WHERE key='source'").fetchone()[0])
        if source['crs']!=frame['crs']:
            raise ValueError('Footprint source uses another inherited frame')
        project=Transformer.from_crs(4326,frame['crs'],always_xy=True)
        inverse=Transformer.from_crs(frame['crs'],4326,always_xy=True)
        for area, lonlat in ANCHORS.items():
            east,north=project.transform(*lonlat)
            extent=box(east-192,north-192,east+192,north+192)
            left,bottom,right,top=extent.bounds
            rows=db.execute('''SELECT b.id,b.geometry,b.source FROM buildings b JOIN bounds r ON b.rowid=r.rowid
                WHERE r.minx<=? AND r.maxx>=? AND r.miny<=? AND r.maxy>=?''',(right,left,top,bottom))
            candidates=[]
            for identifier,wkb,provenance in rows:
                geometry=from_wkb(wkb)
                if not geometry.is_valid or geometry.is_empty or geometry.area<20 or not extent.covers(geometry.centroid):
                    continue
                distance=math.hypot(geometry.centroid.x-east,geometry.centroid.y-north)
                candidates.append((distance,identifier,geometry,json.loads(provenance)))
            candidates.sort(key=lambda row:(row[0],row[1]))
            if len(candidates)<count:
                raise ValueError(f'{area}: fewer than ten source footprints in fixed context')
            for ordinal,(distance,identifier,geometry,provenance) in enumerate(candidates[:count],1):
                left,bottom,right,top=geometry.bounds
                records.append({'pilot_id':f'{area}-{ordinal:02d}','area':area,'source_building_id':identifier,
                    'footprint_geometry':mapping(geometry),'footprint_sha256':hashlib.sha256(geometry.wkb).hexdigest(),
                    'source_provenance':provenance,'area_m2':geometry.area,'anchor_distance_m':distance,
                    'centroid_lonlat':list(inverse.transform(geometry.centroid.x,geometry.centroid.y)),
                    'world_bounds_xz':[left-frame['west'],frame['north']-top,right-frame['west'],frame['north']-bottom],
                    'footprint_is_measured_lidar':False,'world_wall_anchor_verified':False,
                    'registration':'pending','usable_photo_wall_area_m2':None,'admitted_face_colors':0})
    finally:db.close()
    if signature!=(index.stat().st_size,index.stat().st_mtime_ns):
        raise ValueError('Footprint index changed during frozen selection')
    result={'schema':'earthcraft.facade-pilot.v1','selection':'nearest existing footprints; no photo-coverage ranking',
            'frame':frame,'frame_sha256':canonical_sha(frame),'anchors':ANCHORS,
            'core_size_m':256,'context_halo_m':64,'building_count':20,
            'source':source,'buildings':records,'new_face_colors_applied':False,
            'client_render_tested':False,'usable_facade_coverage_measured':False}
    result['selection_sha256']=canonical_sha(records)
    return result


def attach_cached_photo_candidates(pilot, directory):
    if canonical_sha(pilot['buildings'])!=pilot['selection_sha256']:
        raise ValueError('Frozen building selection changed')
    project=Transformer.from_crs(4326,pilot['frame']['crs'],always_xy=True)
    photos={}
    for path in sorted(Path(directory).glob('*-nearby.json')):
        doc=json.loads(path.read_text())
        for item in doc.get('data',[]):
            geometry=item.get('computed_geometry') or item.get('geometry')
            if not geometry or geometry.get('type')!='Point':continue
            photos[str(item['id'])]={k:item.get(k) for k in ('id','captured_at','camera_type','is_pano')}
            photos[str(item['id'])]['metric_xy']=project.transform(*geometry['coordinates'])
    candidates={}
    for building in pilot['buildings']:
        from shapely.geometry import shape, Point
        geometry=shape(building['footprint_geometry'])
        nearby=[]
        for photo in photos.values():
            distance=geometry.distance(Point(photo['metric_xy']))
            if distance<=75:
                nearby.append({k:v for k,v in photo.items() if k!='metric_xy'}|{'distance_to_footprint_m':distance})
        candidates[building['pilot_id']]={'nearby_metadata_candidates':sorted(nearby,key=lambda p:(p['distance_to_footprint_m'],str(p['id']))),
            'visible_wall_coverage_verified':False,'admitted_face_colors':0}
    pilot['photo_discovery']={'cached_unique_images':len(photos),'per_building':candidates,
        'position_radius_m':75,'usable_facade_coverage_measured':False}
    return pilot


def audit_saved_walls(pilot, world):
    """Locate a bounded set of existing exposed block anchors near footprints.

    A footprint proximity match is not measured LiDAR identity or registration.
    Reads at most 720 columns per area over Y=64..200; never creates geometry.
    """
    from facade_world import BlockReader, MATERIALS, EMPTY
    from shapely.geometry import shape
    if canonical_sha(pilot['buildings'])!=pilot['selection_sha256']:
        raise ValueError('Frozen pilot selection changed')
    reader=BlockReader(world); frame=pilot['frame']; result={}
    normals={'west':(-1,0,0),'east':(1,0,0),'north':(0,0,-1),'south':(0,0,1)}
    for building in pilot['buildings']:
        geometry=shape(building['footprint_geometry'])
        columns=set()
        for ordinal in range(8):
            p=geometry.boundary.interpolate(geometry.boundary.length*ordinal/8)
            x,z=math.floor(p.x-frame['west']),math.floor(frame['north']-p.y)
            columns.update((x+dx,z+dz) for dx in (-1,0,1) for dz in (-1,0,1))
        anchors=[];missing=0
        for x,z in sorted(columns):
            if reader.chunk(x//16,z//16) is None:missing+=1;continue
            # Keep one exposed facade-like block per sampled column, above support.
            support=reader.support(x,z)
            if support is None:continue
            for y in range(min(200,support[1]-1),64,-1):
                state=reader.state((x,y,z))
                if str(state['Name']) not in MATERIALS or state.get('Properties') or reader.has_entity((x,y,z)):continue
                faces=[face for face,normal in normals.items() if reader.name((x+normal[0],y,z+normal[2])) in EMPTY]
                if faces:
                    anchors.append({'cell':[x,y,z],'faces':faces,'expected_block':str(state['Name'])});break
        result[building['pilot_id']]={'sampled_columns':len(columns),'missing_native_columns':missing,
            'existing_exposed_block_anchors':anchors,'building_identity_verified':False,
            'photo_registration_verified':False,'new_face_colors_applied':0}
    return {'schema':'earthcraft.pilot-wall-audit.v1','pilot_sha256':pilot['selection_sha256'],
        'frame':frame,'vertical_search_y':[65,200],'per_building':result,
        'source_world_modified':False,'client_render_tested':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path)
    parser.add_argument('--frame',type=Path)
    parser.add_argument('--pilot',type=Path)
    parser.add_argument('--photo-cache',type=Path)
    parser.add_argument('--audit-world',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Keep pilot selections immutable')
    result=json.loads(args.pilot.read_text()) if args.pilot else select(args.index,json.loads(args.frame.read_text()))
    if args.photo_cache:result=attach_cached_photo_candidates(result,args.photo_cache)
    if args.audit_world:
        report=audit_saved_walls(result,args.audit_world)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({key:len(value['existing_exposed_block_anchors']) for key,value in report['per_building'].items()}))
        raise SystemExit(0)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'buildings':result['building_count'],'selection_sha256':result['selection_sha256'],
                      'new_face_colors_applied':False,'usable_facade_coverage_measured':False}))
