"""Location -> available measured sources -> verified installed Minecraft world.

Google Earth KML/KMZ supplies a location only, not Google's imagery or 3D mesh.
Cached scans take precedence. Else bounded public AWS terrain and mapped ways
are acquired. Unknown heights remain absent; no LLM or generated facade detail.
"""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from pyproj import Transformer
from earthcraft import ROOT,region_path
from public_map_sources import prepare


def location(lon,lat):
    if not all(math.isfinite(v) for v in (lon,lat)) or not -180<=lon<180 or not -85<lat<85:
        raise ValueError('Finite longitude [-180,180) and latitude (-85,85) required')
    return float(lon),float(lat)


def kml_location(path):
    path=Path(path)
    if path.stat().st_size>4*2**20:raise ValueError('Location export exceeds 4 MiB')
    if path.suffix.lower()=='.kmz':
        with zipfile.ZipFile(path) as z:
            entries=[i for i in z.infolist() if i.filename.lower().endswith('.kml')]
            if len(entries)!=1 or entries[0].file_size>2**20:
                raise ValueError('KMZ requires one KML document no larger than 1 MiB')
            raw=z.read(entries[0])
    else:raw=path.read_bytes()
    if len(raw)>2**20 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Bounded KML without XML entities required')
    root=ET.fromstring(raw)
    points=[p for p in root.iter() if p.tag.rsplit('}',1)[-1]=='Point']
    if len(points)!=1:raise ValueError('Export exactly one location placemark (Point), not building geometry')
    coordinates=[p for p in points[0].iter() if p.tag.rsplit('}',1)[-1]=='coordinates']
    if len(coordinates)!=1:raise ValueError('One Point coordinate required')
    tokens=(coordinates[0].text or '').split()
    if len(tokens)!=1:raise ValueError('One longitude,latitude coordinate required')
    fields=tokens[0].split(',')
    if len(fields) not in (2,3):raise ValueError('Invalid Point coordinate')
    lon,lat=location(float(fields[0]),float(fields[1]))
    return lon,lat,{'path':str(path.resolve()),'kml_sha256':hashlib.sha256(raw).hexdigest(),
                   'role':'Location selection only; no Google imagery or mesh consumed'}


def select_cached(lon,lat,catalog,resolve=region_path):
    location(lon,lat);candidates=[];unavailable=[]
    for key,region in catalog['regions'].items():
        try:
            source=resolve(region['source'])
            meta=json.loads((source/'sources.json').read_text())
            x,y=Transformer.from_crs(4326,meta['crs'],always_xy=True).transform(lon,lat)
            if not (meta['west']<=x<meta['west']+meta['size'] and
                    meta['north']-meta['size']<y<=meta['north']):continue
            if region.get('points'):resolve(region['points'])
            if region.get('photo_layer'):resolve(region['photo_layer']['source_world'])
            rank=(bool(region.get('photo_layer')),bool(region.get('points')),-meta['size'],key)
            candidates.append((rank,key,region,meta))
        except (ValueError,OSError,KeyError) as error:
            unavailable.append({'region':key,'reason':str(error)})
    if not candidates:return None,unavailable
    _,key,region,meta=max(candidates,key=lambda p:p[0])
    return {'region':key,'configuration':region,'actual_grid':{k:meta[k] for k in ('west','north','size','crs')},
            'extent_policy':'Whole cached chart containing the requested point; not a recentered or cropped chart'},unavailable


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kml',type=Path);p.add_argument('--lon',type=float);p.add_argument('--lat',type=float)
    p.add_argument('--name',default='Earthcraft-Location-'+datetime.now().strftime('%Y%m%d-%H%M%S'));p.add_argument('--size',type=int,default=256,
        help='Uncached terrain extent only; cached scans retain their declared grid')
    p.add_argument('--plan-only',action='store_true')
    p.add_argument('--public-data',action='store_true',help='Acquire fresh bounded public sources instead of selecting cached scans')
    p.add_argument('--resume-acquisition',action='store_true',help='Resume this named public source acquisition before any world has been written')
    args=p.parse_args()
    if Path(args.name).name!=args.name or args.name in ('','.','..'):p.error('Simple new world name required')
    if args.kml:
        if args.lon is not None or args.lat is not None:p.error('Use a KML point or coordinates, not both')
        lon,lat,origin=kml_location(args.kml)
    else:
        if args.lon is None or args.lat is None:p.error('Provide --kml or both --lon and --lat')
        lon,lat=location(args.lon,args.lat);origin={'role':'Explicit WGS84 coordinates'}
    if args.size<16 or args.size>512 or args.size%16:p.error('Terrain size must be 16..512, a multiple of 16')
    catalog=json.loads((ROOT/'configs/atlas-regions.json').read_text())
    selected,unavailable=select_cached(lon,lat,catalog)
    if args.public_data:selected=None
    plan={'location_wgs84':[lon,lat],'location_input':origin,'selected':selected,'requested_size_m':args.size,
          'unavailable_sources':unavailable,'llm_used':False,'google_imagery_used':False,
          'uncached_policy':'Acquire public AWS terrain and OSM ways; explicit-height building shells, missing heights/facades reported'}
    if args.plan_only:print(json.dumps(plan,indent=2));return
    run=ROOT/'runs'/f'{args.name}-location'
    if (ROOT/'worlds'/args.name).exists():raise FileExistsError('World already exists; never overwrite it')
    if args.resume_acquisition:
        previous=json.loads((run/'location.json').read_text())
        if not args.public_data or previous['location_wgs84']!=[lon,lat] or previous['selected'] is not None or previous['requested_size_m']!=args.size:
            raise ValueError('Resume requires the same public-data location and world name')
    else:
        run.mkdir();(run/'location.json').write_text(json.dumps(plan,indent=2))
    if selected is None:
        source=prepare(lon,lat,args.size,run/'terrain',resume=args.resume_acquisition)
        region={'source':'project:'+str(source.relative_to(ROOT)),'points':None,
                'scope':'Automatically acquired AWS terrain and OSM ways; partial explicit-height buildings, not scanned facades'}
    else:region=selected['configuration']
    generated={'default_region':'location','regions':{'location':region}}
    config=run/'catalog.json';config.write_text(json.dumps(generated,indent=2))
    subprocess.run([sys.executable,str(ROOT/'scripts/earthcraft.py'),'--catalog',str(config),
                    '--replay-check','--name',args.name],check=True,cwd=ROOT)


if __name__=='__main__':main()
