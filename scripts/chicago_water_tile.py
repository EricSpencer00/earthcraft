"""Build one shared-frame Chicago tile with source-backed lake and rivers."""
import argparse
import json
from pathlib import Path

from pyproj import Transformer

from aws_terrain import prepare as terrain
from metric_chart import chart_in_frame
from metric_world import build
from osm_hydrology import extract
from verify_metric_world import verify


ROOT=Path(__file__).resolve().parents[1]


def tile_source(frame, pbf, tile_x, tile_z, destination, size=256, features_path=None):
    """Acquire terrain and OSM water for a frame-aligned tile."""
    inverse=Transformer.from_crs(frame['crs'],4326,always_xy=True)
    easting=frame['west']+tile_x+size/2
    northing=frame['north']-tile_z-size/2
    lon,lat=inverse.transform(easting,northing)
    meta=chart_in_frame(lon,lat,size,frame)
    source=terrain(lon,lat,size,destination,chart_meta=meta)
    inverse=Transformer.from_crs(meta['crs'],4326,always_xy=True)
    corners=[inverse.transform(x,y) for x in
             (meta['west']-16,meta['west']+size+16)
             for y in (meta['north']+16,meta['north']-size-16)]
    bbox=(min(p[0] for p in corners),min(p[1] for p in corners),
          max(p[0] for p in corners),max(p[1] for p in corners))
    if features_path:
        features=json.loads(Path(features_path).read_text())
        hydrology={'source_features':str(Path(features_path).resolve()),
                   'water_feature_count':len(features),'bbox_wgs84':list(bbox),
                   'coverage_is_complete':False}
    else:
        features,hydrology=extract(pbf,bbox)
    (source/'osm-ways.json').write_text(json.dumps(features,indent=2))
    receipt={'provider':'OpenStreetMap contributors via frozen Chicago PBF',
             'source_pbf':str(Path(pbf).resolve()) if pbf else None,
             'source_features':str(Path(features_path).resolve()) if features_path else None,
             'coordinates':'WGS84 longitude, latitude',
             'bbox_wgs84':list(bbox),'inventory':hydrology,
             'water_policy':'Lake and river polygons are source-backed; linear waterways require explicit mapped width.',
             'coverage_is_complete':False,'llm_used':False}
    (source/'osm-acquisition.json').write_text(json.dumps(receipt,indent=2))
    meta=json.loads((source/'sources.json').read_text())
    meta.update(buildings_available=False,building_source_kind='none',osm_source=receipt,
                missing_layers=['building observations','ground cover','facade imagery','bathymetry'],
                purpose='Shared-frame terrain tile with source-backed Lake Michigan and river surfaces')
    (source/'sources.json').write_text(json.dumps(meta,indent=2))
    return source,hydrology


def run(frame_path,pbf,source_out,world_out,tile_x,tile_z,features_path=None):
    frame=json.loads(Path(frame_path).read_text())
    source,hydrology=tile_source(frame,pbf,tile_x,tile_z,Path(source_out),features_path=features_path)
    build(source,Path(world_out),world_frame=frame)
    report=json.loads((Path(world_out)/'earthcraft.json').read_text())
    report['city_tile_id']=f'{tile_x//256}_{tile_z//256}'
    report['hydrology']=hydrology
    (Path(world_out)/'earthcraft.json').write_text(json.dumps(report,indent=2))
    checks=verify(Path(world_out))
    return {'source':str(source),'world':str(world_out),'tile':[tile_x,tile_z],
            'hydrology':hydrology,'checks':checks}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frame',type=Path,required=True)
    parser.add_argument('--pbf',type=Path)
    parser.add_argument('--features',type=Path)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--world',type=Path,required=True)
    parser.add_argument('--tile-x',type=int,required=True)
    parser.add_argument('--tile-z',type=int,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.frame,args.pbf,args.source,args.world,args.tile_x,args.tile_z,args.features),indent=2))
