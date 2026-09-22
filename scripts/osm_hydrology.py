"""Extract source-backed OSM water polygons from a local PBF.

The Chicago extract contains Lake Michigan as a large multipolygon relation;
the ordinary way-only importer cannot represent it.  This adapter resolves
water relations from their member ways, polygonizes their outer rings, and
emits the same bounded feature format consumed by ``metric_world``.
"""
import argparse
import json
import math
from pathlib import Path

from shapely.geometry import LineString, box, shape
from shapely.ops import polygonize, unary_union


def is_water(tags):
    return (tags.get('natural') in ('water','wetland') or
            tags.get('water') in ('yes','pond','lake','reservoir','river','canal',
                                  'basin','harbour','lagoon','wastewater','stormwater') or
            tags.get('waterway') in ('river','stream','canal','drain','ditch','riverbank',
                                     'basin','dock','harbour','boatyard','lock','lock_gate'))


def _valid_points(nodes):
    points=[]
    for node in nodes:
        if not node.location.valid():
            return None
        lon,lat=float(node.lon),float(node.lat)
        if not math.isfinite(lon+lat) or not -180<=lon<=180 or not -90<=lat<=90:
            raise ValueError('Invalid WGS84 OSM node')
        points.append((lon,lat))
    return points if len(points)>=2 else None


def extract(pbf, bbox=None, relation_names=None, relation_ids=None):
    """Return ``metric_world`` water features and extraction evidence.

    ``bbox`` is ``(west, south, east, north)`` in WGS84.  Relation member
    ways are retained outside that box while resolving a selected relation so
    a clipped tile still gets a complete polygon.
    """
    import osmium

    pbf=Path(pbf)
    selected_names=set(relation_names or ())
    selected_ids={int(identifier) for identifier in (relation_ids or ())}
    relations={}

    class RelationReader(osmium.SimpleHandler):
        def relation(self, relation):
            tags=dict(relation.tags)
            if not is_water(tags):
                return
            name=tags.get('name')
            if (selected_names or selected_ids) and name not in selected_names and relation.id not in selected_ids:
                return
            relations[relation.id]={'tags':tags,
                                    'members':[member.ref for member in relation.members
                                               if member.type=='w' and member.role=='outer']}

    RelationReader().apply_file(str(pbf),locations=False)
    member_ids={member for relation in relations.values() for member in relation['members']}
    ways={}

    class WayReader(osmium.SimpleHandler):
        def way(self, way):
            tags=dict(way.tags)
            if way.id not in member_ids and (selected_names or selected_ids or not is_water(tags)):
                return
            points=_valid_points(way.nodes)
            if points is None:
                return
            geometry=LineString(points)
            if bbox is not None and way.id not in member_ids and not geometry.intersects(box(*bbox)):
                return
            ways[way.id]={'id':way.id,'tags':tags,'coordinates':[[x,y] for x,y in points],
                          'closed':points[0]==points[-1]}

    WayReader().apply_file(str(pbf),locations=True)
    features=[]
    relation_count=0
    relation_polygon_count=0
    for relation_id,relation in relations.items():
        lines=[LineString(ways[member]['coordinates']) for member in relation['members']
               if member in ways and len(ways[member]['coordinates'])>1]
        polygons=list(polygonize(unary_union(lines))) if lines else []
        if bbox is not None:
            polygons=[polygon for polygon in polygons if polygon.intersects(box(*bbox))]
        for index,polygon in enumerate(polygons):
            coordinates=[[float(x),float(y)] for x,y in polygon.exterior.coords]
            tags=dict(relation['tags']);tags['source_relation_id']=relation_id
            features.append({'id':-(int(relation_id)*1000+index+1),'tags':tags,
                             'coordinates':coordinates,'closed':True})
            relation_polygon_count+=1
        if polygons:
            relation_count+=1
    member_set=set(member_ids)
    for way_id,way in ways.items():
        if way_id in member_set:
            continue
        if bbox is not None and not shape({'type':'LineString','coordinates':way['coordinates']}).intersects(box(*bbox)):
            continue
        features.append(way)
    features.sort(key=lambda feature:(str(feature['id'])))
    return features, {'pbf':str(pbf.resolve()),'relation_count':relation_count,
                      'relation_polygon_count':relation_polygon_count,
                      'water_feature_count':len(features),
                      'selected_relation_names':sorted(selected_names),
                      'selected_relation_ids':sorted(selected_ids),
                      'bbox_wgs84':list(bbox) if bbox else None,
                      'coverage_is_complete':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--bbox',type=float,nargs=4,metavar=('WEST','SOUTH','EAST','NORTH'))
    parser.add_argument('--relation-name',action='append',default=[])
    parser.add_argument('--relation-id',action='append',type=int,default=[])
    args=parser.parse_args()
    features,report=extract(args.pbf,tuple(args.bbox) if args.bbox else None,
                            args.relation_name,args.relation_id)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(features,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
